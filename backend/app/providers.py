"""Authorized, read-only provider adapters, independent of the Admin application.

Provider responses live only in the caller's memory. Catalog refreshes return movie
and theater identities, never session bodies. Credentials are server-side settings;
errors deliberately contain no upstream URLs, response bodies, or credentials.
"""
from __future__ import annotations

import gzip
import hashlib
import hmac
import json
import re
import secrets
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from threading import Event
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


MAX_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 500_000
MAX_PAGES = 500
MAX_THEATERS = 5_000
CINEMARK_FEED = "https://cdnim.prd.cineticket.com.br/showtime/showtime.json"
IS_BASE = "https://api.internationalshowtimes.com/v5"
INGRESSO_BASE = "https://api-content.ingresso.com"
VELOX_BASE = "http://vws.veloxtickets.com:82/wscinema.ws"


class ProviderError(RuntimeError):
    """Public-safe provider failure; never include an upstream error verbatim."""


class ProviderCancelled(ProviderError):
    pass


@dataclass
class ProviderResult:
    movies: list[dict] = field(default_factory=list)
    theaters: list[dict] = field(default_factory=list)
    showtimes: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _url(value: Any) -> str | None:
    value = _text(value)
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme.lower() not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        # Reject control characters and non-public link schemes; URLs are never fetched here.
        if any(ord(char) < 32 for char in value):
            return None
        return value
    except ValueError:
        return None


def _date(value: Any) -> str | None:
    if isinstance(value, dict):
        try:
            return date(int(value["year"]), int(value["month"]), int(value["day"])).isoformat()
        except (ValueError, TypeError, KeyError):
            return None
    value = _text(value)
    for pattern, source in (("%Y-%m-%d", value[:10]), ("%Y%m%d", value), ("%d/%m/%Y", value)):
        try:
            return datetime.strptime(source, pattern).date().isoformat()
        except ValueError:
            continue
    return None


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if result == result and abs(result) != float("inf") else None
    except (ValueError, TypeError):
        return None


def _list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("items", "results", "data"):
            if isinstance(value.get(key), list):
                return value[key]
    raise ProviderError("The provider returned an invalid list response.")


def _codes(value: Any) -> list[str]:
    if value is None:
        return []
    output = []
    for entry in value if isinstance(value, (list, tuple)) else [value]:
        for part in _text(entry).split(","):
            code = part.strip().upper()
            if re.fullmatch(r"[A-Z]{2,3}(?:-[A-Z0-9]{2,8})*", code) and code not in output:
                output.append(code)
    return output


def _movie(external_id: Any, name: Any, release: Any = None, original: Any = None,
           parent_id: Any = None, **metadata: Any) -> dict | None:
    external_id, name = _text(external_id), _text(name)
    if not external_id or not name:
        return None
    return {"id": external_id, "name": name, "original_name": _text(original) or None,
            "release": _date(release), "parent_id": _text(parent_id) or None, **metadata}


def _theater(external_id: Any, name: Any = None, **values: Any) -> dict:
    return {"id": _text(external_id), "name": _text(name) or None,
            **{key: values.get(key) for key in (
                "address", "city", "region", "postal", "lat", "lon", "website",
                "chain_id", "chain_name", "timezone")}}


def _vip(value: Any) -> bool:
    value = re.sub(r"[^A-Z0-9]+", " ", _text(value).upper())
    return bool(re.search(r"(?:^| )VIP(?: |$)", value)) or any(
        part in value for part in ("DELUX", "DE LUXE", "LUXURY"))


# Preserve the current Admin's verified feed-name to actual Cinemark cinema-ID
# fallback. Provider-supplied IDs always take precedence over this legacy catalog.
_CINEMARK_IDS = {
    "ARICANDUVA": "716", "ATRIUM SHOPPING": "2115", "BARRA SHOPPING SUL": "704",
    "BH SHOPPING": "768", "BOTAFOGO": "728", "BOULEVARD LONDRINA": "2104",
    "BOULEVARD SHOPPING MOGI GUA U": "2140", "BOULEVARD TATUAPE": "690",
    "BOURBON IPIRANGA": "695", "BOURBON NOVO HAMBURGO": "2134", "BOURBON WALLIG": "2135",
    "BRAGAN A SHOPPING": "2128", "CAMA ARI": "2130", "CAMPINAS IGUATEMI": "725",
    "CAMPINAS VIP": "725", "CANOAS SHOPPING": "693", "CAPIM DOURADO": "661",
    "CARIOCA SHOPPING": "692", "CENTER NORTE": "699", "CENTER SHOPPING RIO JACAREPAGUA": "2118",
    "CENTER VALE": "696", "CENTRAL PLAZA": "705", "CIDADE JARDIM VIP": "682",
    "CIDADE SAO PAULO": "2119", "CINEMARK INTERNACIONAL SHOPPING": "759", "CINEMARK OSASCO": "757",
    "COLINAS SHOPPING": "712", "DIAMOND MALL": "767", "DOWNTOWN": "719", "ELDORADO": "715",
    "EXTRA ANCHIETA": "724", "FLAMBOYANT": "2133", "FLORIPA SHOPPING": "703", "FOZ DO IGUA U": "2129",
    "GOIABEIRAS SHOPPING": "2108", "GOLDEN SQUARE": "2109", "GRAND PLAZA SHOPPING": "713",
    "IGUATEMI BRASILIA": "769", "INTERLAGOS": "714", "JACAREI SHOPPING": "689",
    "JEQUITIB ITABUNA": "2137", "JUA GARDEN SHOPPING": "2127", "LAGES GARDEN SHOPPING": "2121",
    "LAR CENTER": "2114", "MARKET PLACE": "688", "METRO SANTA CRUZ": "684", "METRO TATUAPE": "711",
    "METRO TUCURUVI": "758", "METROPOLITANO BARRA": "2112", "MIDWAY MALL NATAL": "681",
    "MOGI DAS CRUZES": "2120", "MOGI URUPEMA": "2139", "MOOCA PLAZA SHOPPING": "2103",
    "NOVO SHOPPING": "726", "PARK SHOPPING SAO CAETANO": "2100", "PARKSHOPPINGBARIGUI": "700",
    "PARTAGE SHOPPING BETIM": "2111", "PASSEIO DAS GUAS": "2113", "PATIO HIGIENOPOLIS": "721",
    "PATIO PAULISTA": "723", "PATIO SAVASSI": "697", "PIER 21": "720", "PLAZA SHOPPING NITEROI": "691",
    "PRAIAMAR SHOPPING": "709", "RAPOSO SHOPPING": "662", "RIOMAR ARACAJU": "755", "RIOMAR RECIFE": "2107",
    "RORAIMA": "2123", "SALVADOR SHOPPING": "785", "S O GON ALO": "2136", "SHOPPING CAMPO GRANDE": "694",
    "SHOPPING D": "687", "SHOPPING GRANJA VIANNA": "663", "SHOPPING IGUATEMI SP": "2132",
    "SHOPPING JARDINS": "706", "SHOPPING MUELLER": "698", "SHOPPING SAO JOSE": "894", "SHOPPING TAMBORE": "717",
    "SHOPPING VILA VELHA": "2117", "SHOPPING VITORIA": "702", "SP MARKET": "710", "STUDIO 5 SHOPPING": "708",
    "TAGUATINGA SHOPPING": "718", "TIETE PLAZA SHOPPING": "2110", "UBERL NDIA SHOPPING": "2101",
    "VIA CAFE": "2122", "VIA VALE TAUBATE": "2105", "VILLA LOBOS": "727", "VILLAGE MALL": "2106", "WEST PLAZA": "2102",
}


def cinemark_theater_identity(item: dict) -> str:
    for key in ("TheaterId", "TheaterID", "theaterId", "theater_id", "TheatreId", "CinemaId", "CinemaID",
                "cinemaId", "TheaterCode", "theaterCode", "CinemaCode", "cinemaCode", "Code", "code", "Id", "id"):
        if _text(item.get(key)):
            return _text(item[key])
    name = _text(item.get("Name") or item.get("name"))
    normalized = re.sub(r"[^A-Z0-9]+", " ", name.upper()).strip()
    if normalized in _CINEMARK_IDS:
        return _CINEMARK_IDS[normalized]
    prefixed = re.match(r"^KT\s*([0-9]+)(?:\s|$)", normalized)
    normalized_name = re.sub(r"\s+", " ", name.upper()).strip() or "2D"
    return prefixed.group(1) if prefixed else "name-" + hashlib.sha256(normalized_name.encode()).hexdigest()[:24]


class ProviderService:
    def __init__(self, settings: Any):
        self.settings = settings
        self.timeout = min(240.0, max(5.0, float(getattr(settings, "request_timeout", 120))))
        # This is per-theater Velox fan-out, not the global provider-job limit.
        self.concurrency = min(6, max(1, int(getattr(settings, "velox_concurrency", 6))))

    @staticmethod
    def _check(cancel: Event) -> None:
        if cancel.is_set():
            raise ProviderCancelled("The provider request was cancelled.")

    def _fetch_json(self, url: str, cancel: Event, headers: dict | None = None,
                    *, body: bytes | None = None, gzip_response: bool = False) -> Any:
        """Bounded HTTP transport. Test fixtures replace this method, never providers."""
        opener = urllib.request.build_opener(_NoRedirects())
        for attempt in range(3):
            self._check(cancel)
            # A previous attempt may have reached the signed relay before its
            # response failed. Use a fresh nonce so its replay guard accepts retry.
            request_headers = dict(headers or {})
            if attempt and "X-Relay-Signature" in request_headers:
                request_headers.update(self._relay_headers(body or b""))
            request = urllib.request.Request(url, data=body, headers={
                "Accept": "application/json", "User-Agent": "showtime-data-check/1", **request_headers})
            deadline, delay = time.monotonic() + self.timeout, 2 ** attempt
            try:
                with opener.open(request, timeout=self.timeout) as response:
                    if response.headers.get("Content-Length"):
                        try:
                            if int(response.headers["Content-Length"]) > MAX_RESPONSE_BYTES:
                                raise ProviderError("The provider response exceeds the supported size limit.")
                        except ValueError:
                            raise ProviderError("The provider returned an invalid response length.") from None
                    stream = response
                    if gzip_response:
                        if response.headers.get("Content-Encoding", "").lower() != "gzip":
                            raise ProviderError("The Cinemark relay returned an unsupported response encoding.")
                        stream = gzip.GzipFile(fileobj=response)
                    chunks, total = [], 0
                    while True:
                        self._check(cancel)
                        if time.monotonic() > deadline:
                            raise ProviderError("The provider download exceeded its time limit.")
                        chunk = stream.read(min(64 * 1024, MAX_RESPONSE_BYTES + 1 - total))
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_RESPONSE_BYTES:
                            raise ProviderError("The provider response exceeds the supported size limit.")
                        chunks.append(chunk)
                    data = b"".join(chunks)
                self._check(cancel)
                return json.loads(data) if data.strip() else []
            except urllib.error.HTTPError as error:
                status = error.code
                retry_after = error.headers.get("Retry-After") if error.headers else None
                error.close()
                if status not in {408, 429, 500, 502, 503, 504} or attempt == 2:
                    raise ProviderError(f"The provider request failed (HTTP {status}).") from None
                if retry_after:
                    try:
                        delay = min(30, max(delay, float(retry_after)))
                    except (TypeError, ValueError):
                        pass
            except (TimeoutError, socket.timeout, urllib.error.URLError, OSError):
                if attempt == 2:
                    raise ProviderError("The provider could not be reached. Please retry.") from None
            except (ValueError, UnicodeError, EOFError):
                raise ProviderError("The provider returned invalid JSON or an incomplete response.") from None
            if cancel.wait(delay):
                self._check(cancel)
        raise ProviderError("The provider request did not complete.")

    def catalog(self, connection: dict, cancel: Event) -> ProviderResult:
        return self._load(connection, None, cancel)

    def showtimes(self, connection: dict, external_ids: set[str], cancel: Event) -> ProviderResult:
        self._check(cancel)
        ids = {_text(value) for value in external_ids if _text(value)}
        if not ids:
            return ProviderResult()
        return self._load(connection, ids, cancel)

    def _load(self, connection: dict, selected: set[str] | None, cancel: Event) -> ProviderResult:
        self._check(cancel)
        code = _text(connection.get("provider_code") or connection.get("code")).upper()
        operation = {"IS": self._is, "INGRESSO": self._ingresso, "CINEMARK": self._cinemark,
                     "VELOX": self._velox}.get(code)
        if operation is None:
            raise ProviderError("This data provider is not supported.")
        try:
            result = operation(connection, selected, cancel)
        except ProviderError:
            raise
        except Exception:
            # Parsing/library exceptions may include upstream values or credential URLs.
            raise ProviderError(f"The {code} response could not be processed.") from None
        self._check(cancel)
        if len(result.movies) > MAX_RECORDS or len(result.theaters) > MAX_THEATERS:
            raise ProviderError("The provider catalog exceeds the supported record limit.")
        if selected is None:
            result.showtimes.clear()
        return result

    @staticmethod
    def _timezone(connection: dict, theater_id: str = "", supplied: Any = None) -> ZoneInfo:
        for value in (supplied, (connection.get("theater_timezones") or {}).get(theater_id),
                      connection.get("timezone"), "UTC"):
            try:
                if value:
                    return ZoneInfo(str(value))
            except (ZoneInfoNotFoundError, ValueError):
                continue
        return ZoneInfo("UTC")

    def _session(self, connection: dict, theater: dict, title_id: str, external_id: Any,
                 day: Any, clock: Any, raw: str, links: list[dict], *, starts: Any = None,
                 audio: Any = None, subtitle: Any = None, attributes: list | None = None,
                 screen: Any = None, vip: bool = False, **metadata: Any) -> dict | None:
        day = _date(day)
        clock = _text(clock)[:5]
        tz = self._timezone(connection, theater["id"], theater.get("timezone"))
        if not day or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", clock):
            return None
        if day < datetime.now(tz).date().isoformat():
            return None
        cleaned, seen = [], set()
        for link in links:
            url = _url(link.get("url"))
            if url and url not in seen:
                cleaned.append({"name": _text(link.get("name")) or "Direct", "url": url,
                                "kind": "movie" if link.get("kind") == "movie" else "booking"})
                seen.add(url)
        instant = starts or datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=tz).astimezone(timezone.utc).isoformat()
        booking = next((link["url"] for link in cleaned if link["kind"] == "booking"), None)
        movie_url = next((link["url"] for link in cleaned if link["kind"] == "movie"), None)
        return {"id": _text(external_id), "title": title_id, "theater": theater["id"],
                "date": day, "time": clock, "starts": instant, "format": raw or "2D",
                "audio": _codes(audio), "subtitle": _codes(subtitle), "attributes": attributes or [],
                "booking_url": booking, "movie_url": movie_url, "links": cleaned,
                "screen": _text(screen) or None, "vip": bool(vip), **metadata}

    @staticmethod
    def _add_session(result: ProviderResult, session: dict | None) -> None:
        if session:
            if len(result.showtimes) >= MAX_RECORDS:
                raise ProviderError("The selected data exceeds the supported record limit.")
            result.showtimes.append(session)

    def _is_items(self, path: str, parameters: dict, cancel: Event) -> list[dict]:
        api_key = _text(getattr(self.settings, "is_api_key", ""))
        if not api_key:
            raise ProviderError("The IS API credential has not been configured.")
        parameters = {**parameters, "page_size": 100}
        url = IS_BASE + path + "?" + urllib.parse.urlencode(parameters)
        output, visited = [], set()
        for _ in range(MAX_PAGES):
            self._check(cancel)
            if url in visited:
                raise ProviderError("The IS API repeated a pagination page.")
            visited.add(url)
            payload = self._fetch_json(url, cancel, {"X-Api-Key": api_key})
            if isinstance(payload, list):
                values = payload
            elif isinstance(payload, dict):
                values = payload[path.strip("/")] if path.strip("/") in payload else _list(payload)
            else:
                raise ProviderError("The IS API returned an invalid list response.")
            if not isinstance(values, list):
                raise ProviderError("The IS API returned an invalid list response.")
            output.extend(value for value in values if isinstance(value, dict))
            if len(output) > MAX_RECORDS:
                raise ProviderError("The selected IS data exceeds the supported record limit.")
            meta = payload.get("meta_info", payload.get("pagination", {})) if isinstance(payload, dict) else {}
            meta = meta if isinstance(meta, dict) else {}
            next_url, token = meta.get("next_page_url") or meta.get("next"), meta.get("next_page_token")
            if next_url:
                candidate = urllib.parse.urljoin(url, str(next_url))
                parsed = urllib.parse.urlsplit(candidate)
                if (parsed.scheme != "https" or parsed.hostname != "api.internationalshowtimes.com"
                        or parsed.port not in {None, 443} or parsed.username or parsed.password
                        or parsed.fragment or parsed.path != "/v5" + path):
                    raise ProviderError("The IS API returned an unsafe pagination destination.")
                url = candidate
            elif token:
                parameters["page_token"] = str(token)
                url = IS_BASE + path + "?" + urllib.parse.urlencode(parameters)
            else:
                return output
        raise ProviderError("The IS API exceeded the supported pagination limit.")

    def _is(self, connection: dict, selected: set[str] | None, cancel: Event) -> ProviderResult:
        country = _text(connection.get("provider_country_code") or connection.get("country_code"))
        result = ProviderResult()
        if selected is None:
            for item in self._is_items("/movies", {"countries": country, "fields": "id,title,release_dates"}, cancel):
                releases = item.get("release_dates") or {}
                release = item.get("release_date") or (releases.get(country) if isinstance(releases, dict) else None)
                movie = _movie(item.get("id"), item.get("title") or item.get("name"), release,
                               item.get("original_title"))
                if movie:
                    result.movies.append(movie)
            return result
        chains = {}
        try:
            chains = {_text(item.get("id")): item.get("name")
                      for item in self._is_items("/chains", {"countries": country}, cancel)}
        except ProviderError as error:
            if "HTTP 404" not in str(error):
                raise
            result.warnings.append("IS chain details are not available for this country.")
        theaters = {}
        for item in self._is_items("/cinemas", {"countries": country}, cancel):
            location = item.get("location") or {}
            location = location if isinstance(location, dict) else {}
            address = location.get("address") or item.get("address") or {}
            address = address if isinstance(address, dict) else {"display_text": _text(address)}
            external_id, chain = _text(item.get("id")), _text(item.get("chain_id"))
            if not external_id:
                continue
            theater = _theater(external_id, item.get("name"),
                address=address.get("display_text") or address.get("street"), city=address.get("city") or location.get("city"),
                region=address.get("state") or address.get("region"), postal=address.get("zipcode") or address.get("postal_code"),
                lat=_number(location.get("lat", item.get("latitude"))), lon=_number(location.get("lon", item.get("longitude"))),
                chain_id=chain or None, chain_name=chains.get(chain), website=_url(item.get("website")),
                timezone=str(self._timezone(connection, external_id, item.get("timezone") or item.get("time_zone") or location.get("timezone"))))
            theaters[external_id] = theater
        # Fetch from the earliest current midnight in the country, so cinemas in a
        # different zone are not dropped before their local-day filtering occurs.
        zones = [self._timezone(connection)] + [self._timezone(connection, value["id"], value.get("timezone")) for value in theaters.values()]
        earliest = min(datetime.combine(datetime.now(tz).date(), datetime.min.time(), tz).astimezone(timezone.utc) for tz in zones)
        for title_id in sorted(selected):
            for item in self._is_items("/showtimes", {"countries": country, "movie_id": title_id, "time_from": earliest.isoformat()}, cancel):
                self._check(cancel)
                actual_title = _text(item.get("movie_id"))
                if actual_title not in selected:
                    continue
                theater_id = _text(item.get("cinema_id"))
                if not theater_id:
                    continue
                theater = theaters.setdefault(theater_id, _theater(theater_id, timezone=str(self._timezone(connection, theater_id))))
                try:
                    instant = datetime.fromisoformat(_text(item.get("start_at")).replace("Z", "+00:00"))
                    if instant.tzinfo is None:
                        continue
                except ValueError:
                    continue
                local = instant.astimezone(self._timezone(connection, theater_id, theater.get("timezone")))
                attributes = item.get("attribute_codes") or []
                attributes = attributes if isinstance(attributes, list) else [_text(attributes)]
                links = [{"name": "Direct", "url": item.get("booking_link"), "kind": "booking"},
                         {"name": "Movie page", "url": item.get("movie_page_link"), "kind": "movie"}]
                self._add_session(result, self._session(connection, theater, actual_title, item.get("id"),
                    local.date().isoformat(), local.strftime("%H:%M"), ";".join(_text(value) for value in attributes) or "2D", links,
                    starts=instant.astimezone(timezone.utc).isoformat(), audio=item.get("audio_language") or item.get("language"),
                    subtitle=item.get("subtitle_language"), attributes=attributes,
                    screen=item.get("screen_name") or item.get("screen_id"), vip=_vip(" ".join(map(str, attributes)))))
        result.theaters = list(theaters.values())
        return result

    def _partnerships(self) -> list[str]:
        configured = getattr(self.settings, "ingresso_partnerships", None) or ["warner", "sony"]
        if isinstance(configured, str):
            configured = configured.split(",")
        values = sorted({_text(item) for item in configured if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", _text(item))})
        if not values:
            raise ProviderError("No Ingresso partnerships have been configured.")
        return values

    @staticmethod
    def _ingresso_theater(item: dict, connection: dict) -> dict:
        geo = item.get("geolocation") or {}
        geo = geo if isinstance(geo, dict) else {}
        external_id = _text(item.get("id") or item.get("boxOfficeId"))
        return _theater(external_id, item.get("name"),
            address=", ".join(_text(item.get(key)) for key in ("address", "number") if _text(item.get(key))) or None,
            city=item.get("cityName"), region=item.get("uf") or item.get("state"), postal=item.get("zipCode") or item.get("postalCode"),
            lat=_number(geo.get("latitude", geo.get("lat"))), lon=_number(geo.get("longitude", geo.get("lng", geo.get("lon")))),
            chain_id=_text(item.get("corporationId")) or None, chain_name=item.get("corporation"),
            website=_url(item.get("siteURL") or item.get("nationalSiteURL")),
            timezone=str(ProviderService._timezone(connection, external_id, item.get("timezone"))))

    def _ingresso(self, connection: dict, selected: set[str] | None, cancel: Event) -> ProviderResult:
        result, movies, theaters, available = ProviderResult(), {}, {}, {}
        partnerships = self._partnerships()
        for partnership in partnerships:
            suffix = urllib.parse.quote(partnership, safe="")
            for item in _list(self._fetch_json(f"{INGRESSO_BASE}/v0/events/partnership/{suffix}", cancel)):
                movie = _movie(item.get("id"), item.get("title") or item.get("originalTitle"),
                               item.get("premiereDate"), item.get("originalTitle"), partnerships=[partnership])
                if movie:
                    known = movies.setdefault(movie["id"], movie)
                    known["partnerships"] = sorted(set(known["partnerships"]) | {partnership})
                    available.setdefault(movie["id"], set()).add(partnership)
            for item in _list(self._fetch_json(f"{INGRESSO_BASE}/v0/theaters/partnership/{suffix}", cancel)):
                theater = self._ingresso_theater(item, connection)
                if theater["id"]:
                    theaters[theater["id"]] = theater
        result.movies = list(movies.values())
        if selected is not None:
            seen = set()
            for title_id in sorted(selected):
                for partnership in sorted(available.get(title_id, set(partnerships))):
                    url = f"{INGRESSO_BASE}/v0/sessions/event/{urllib.parse.quote(title_id, safe='')}/partnership/{urllib.parse.quote(partnership, safe='')}"
                    try:
                        groups = _list(self._fetch_json(url, cancel))
                    except ProviderError as error:
                        if "HTTP 404" in str(error):
                            continue
                        raise
                    for group in groups:
                        for item in group.get("theaters") or []:
                            self._check(cancel)
                            theater_id = _text(item.get("id"))
                            if not theater_id:
                                continue
                            theater = theaters.setdefault(theater_id, self._ingresso_theater(item, connection))
                            for room in item.get("rooms") or []:
                                for session in room.get("sessions") or []:
                                    when = session.get("date") or {}
                                    when = when if isinstance(when, dict) else {"localDate": when}
                                    types = session.get("type") or []
                                    types = list(types) if isinstance(types, list) else [types]
                                    types += [entry.get("name") or entry.get("alias") for entry in session.get("types") or [] if isinstance(entry, dict)]
                                    parts = [_text(value) for value in types if _text(value)]
                                    screen = room.get("name") or session.get("roomName") or session.get("screenName")
                                    room_values = [room.get("name"), room.get("type"), room.get("alias"), screen]
                                    for field_name in ("room", "screen"):
                                        value = session.get(field_name)
                                        room_values.extend(value.get(key) for key in ("name", "type", "alias")) if isinstance(value, dict) else room_values.append(value)
                                    vip = _vip(" ".join(_text(value) for value in room_values + parts))
                                    if vip and not _vip(" ".join(parts)):
                                        parts.append("Vip")
                                    showing = self._session(connection, theater, title_id, session.get("id"),
                                        when.get("localDate") or group.get("date"), when.get("hour") or session.get("time"),
                                        " | ".join(parts) or "2D", [{"name": "Ingresso", "url": session.get("siteURL") or session.get("nationalSiteURL"), "kind": "booking"}],
                                        audio=session.get("audioLanguage") or session.get("audio_language"),
                                        subtitle=session.get("subtitleLanguage") or session.get("subtitle_language"),
                                        attributes=parts, screen=screen, vip=vip)
                                    # The same partnership session may be returned twice;
                                    # preserve distinct screens, formats and booking URLs.
                                    key = tuple(showing.get(key) for key in ("id", "title", "theater", "date", "time", "format", "booking_url", "screen")) if showing else None
                                    if key is not None and key not in seen:
                                        seen.add(key)
                                        self._add_session(result, showing)
        result.theaters = list(theaters.values())
        return result

    def _relay_headers(self, body: bytes) -> dict:
        secret = _text(getattr(self.settings, "cinemark_relay_secret", ""))
        stamp, nonce = str(int(time.time())), secrets.token_hex(16)
        canonical = "\n".join(("v1", "POST", "/cinemark", stamp, nonce, hashlib.sha256(body).hexdigest()))
        return {"X-Relay-Timestamp": stamp, "X-Relay-Nonce": nonce,
                "X-Relay-Signature": hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()}

    def _cinemark_payload(self, cancel: Event) -> list:
        relay_url = _text(getattr(self.settings, "cinemark_relay_url", ""))
        if not relay_url:
            payload = self._fetch_json(CINEMARK_FEED, cancel)
        else:
            parsed = urllib.parse.urlsplit(relay_url)
            secret = _text(getattr(self.settings, "cinemark_relay_secret", ""))
            if (parsed.scheme != "https" or not re.fullmatch(r"[a-z0-9]+\.lambda-url\.us-east-1\.on\.aws", parsed.hostname or "")
                    or parsed.path != "/cinemark" or parsed.query or parsed.fragment or parsed.username or parsed.password
                    or parsed.port not in {None, 443} or len(secret) < 40):
                raise ProviderError("The Cinemark relay configuration is invalid.")
            body = b'{"version":1}'
            payload = self._fetch_json(relay_url, cancel, {"Content-Type": "application/json", "Accept-Encoding": "gzip",
                **self._relay_headers(body)}, body=body, gzip_response=True)
        if not isinstance(payload, list):
            raise ProviderError("Cinemark returned an invalid theater feed.")
        return payload

    def _cinemark(self, connection: dict, selected: set[str] | None, cancel: Event) -> ProviderResult:
        result, movies, theaters = ProviderResult(), {}, {}
        for item in self._cinemark_payload(cancel):
            self._check(cancel)
            theater_id = cinemark_theater_identity(item)
            theater = _theater(theater_id, item.get("Name") or item.get("name"),
                address=item.get("Address") or item.get("address"), city=item.get("City") or item.get("city"),
                region=item.get("State") or item.get("state"), postal=item.get("ZipCode") or item.get("PostalCode"),
                lat=_number(item.get("Latitude")), lon=_number(item.get("Longitude")), chain_id="CINEMARK-BR", chain_name="Cinemark",
                website=_url(item.get("Website") or item.get("website") or item.get("SiteURL") or item.get("siteURL")),
                timezone=str(self._timezone(connection, theater_id, item.get("Timezone") or item.get("timezone"))))
            theaters[theater_id] = theater
            for entry in item.get("Movies") or []:
                movie = _movie(entry.get("Id"), entry.get("Name"), entry.get("ReleaseDate"), entry.get("OriginalName"))
                if not movie:
                    continue
                movies[movie["id"]] = movie
                if selected is None or movie["id"] not in selected:
                    continue
                for day in entry.get("Dates") or []:
                    for index, session in enumerate(day.get("ShowTimes") or []):
                        self._check(cancel)
                        raw = " | ".join(_text(value) for value in (session.get("Format"), session.get("Language")) if _text(value)) or "2D"
                        screen = session.get("Auditorium") or session.get("Screen") or session.get("Room")
                        self._add_session(result, self._session(connection, theater, movie["id"],
                            session.get("Id") or f"{movie['id']}:{day.get('Date')}:{index}-{screen or ''}",
                            day.get("Date"), session.get("Time"), raw,
                            [{"name": "Cinemark", "url": session.get("URL"), "kind": "booking"}],
                            audio=session.get("AudioLanguage"), subtitle=session.get("SubtitleLanguage"),
                            screen=screen, vip=_vip(f"{raw} {screen or ''}")))
        result.movies, result.theaters = list(movies.values()), list(theaters.values())
        return result

    def _velox_request(self, operation: str, parameters: dict, cancel: Event) -> Any:
        username, password = _text(getattr(self.settings, "velox_username", "")), _text(getattr(self.settings, "velox_password", ""))
        if not username or not password:
            raise ProviderError("The Velox credential has not been configured.")
        auth = json.dumps({"USUARIO": username, "SENHA": password}, separators=(",", ":"))
        query = urllib.parse.urlencode({"AUTENTICACAO": auth, **parameters})
        payload = self._fetch_json(f"{VELOX_BASE}/{operation}?{query}", cancel)
        envelope = payload.get("ReturnOf" + operation) if isinstance(payload, dict) else None
        expected = {"Get_Cinema_Filmes": "FILMES", "Get_Cinema_Pracas": "PRACAS", "Get_Cinema_Programacao": "PROGS"}[operation]
        if not isinstance(envelope, dict) or expected not in envelope or envelope[expected] is not None and not isinstance(envelope[expected], list):
            raise ProviderError("Velox returned an invalid catalog or schedule response.")
        return payload

    @staticmethod
    def _velox_movies(item: dict) -> tuple[list[dict], str, str]:
        parent, child = _text(item.get("IDFILMEPAI")), _text(item.get("IDFILME"))
        parent_name, child_name = item.get("IDFILMEPAI_DESC"), item.get("IDFILME_DESC") or item.get("TITULO")
        output = []
        parent_movie = _movie(parent, parent_name or (child_name if child == parent else None), item.get("DT_ESTREIA"))
        child_movie = _movie(child, child_name or parent_name, item.get("DT_ESTREIA"), parent_id=parent if parent != child else None)
        if parent_movie:
            output.append(parent_movie)
        if child_movie and child != parent:
            output.append(child_movie)
        return output, parent, child

    def _velox(self, connection: dict, selected: set[str] | None, cancel: Event) -> ProviderResult:
        result, movies, theaters = ProviderResult(), {}, {}
        today = datetime.now(self._timezone(connection)).date()
        payload = self._velox_request("Get_Cinema_Filmes", {"GETFILMES": json.dumps({
            "ORDEM": "ESTREIA", "DT_ESTREIAINI": (today - timedelta(days=15)).isoformat(),
            "DT_ESTREIAFIN": (today + timedelta(days=45)).isoformat()}, separators=(",", ":"))}, cancel)
        rows = ((payload or {}).get("ReturnOfGet_Cinema_Filmes") or {}).get("FILMES") or []
        for item in rows:
            movie = _movie(item.get("IDFILME"), item.get("TITULO"), item.get("DT_ESTREIA"))
            if movie:
                movies[movie["id"]] = movie
        payload = self._velox_request("Get_Cinema_Pracas", {}, cancel)
        locations = ((payload or {}).get("ReturnOfGet_Cinema_Pracas") or {}).get("PRACAS") or []
        if len(locations) > MAX_THEATERS:
            raise ProviderError("The Velox theater catalog exceeds the supported limit.")
        for item in locations:
            external_id = _text(item.get("CODPRACA"))
            if external_id:
                theaters[external_id] = _theater(external_id, item.get("DESCRICAO"), address=item.get("ENDERECO"),
                    city=item.get("CIDADE"), region=item.get("ESTADO"), postal=item.get("CEP"),
                    lat=_number(item.get("LATITUDE")), lon=_number(item.get("LONGITUDE")),
                    chain_id=_text(item.get("CODREDE") or item.get("IDREDE")) or None, chain_name=item.get("REDE"),
                    website=_url(item.get("URL") or item.get("SITE") or item.get("WEBSITE")),
                    timezone=str(self._timezone(connection, external_id, item.get("TIMEZONE"))))

        def schedule(external_id: str) -> tuple[str, list]:
            self._check(cancel)
            values = {"CODPRACA": external_id, "DATAINI": today.isoformat(), "DATAFIN": (today + timedelta(days=120)).isoformat()}
            payload = self._velox_request("Get_Cinema_Programacao", {"GETPROG": json.dumps(values, separators=(",", ":"))}, cancel)
            return external_id, ((payload or {}).get("ReturnOfGet_Cinema_Programacao") or {}).get("PROGS") or []

        # Submit at most N requests, consume and discard each completed response,
        # then refill. Catalog scans therefore never retain nationwide session data.
        executor = ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="velox")
        outstanding, iterator = set(), iter(theaters)
        try:
            for _ in range(self.concurrency):
                external_id = next(iterator, None)
                if external_id is not None:
                    outstanding.add(executor.submit(schedule, external_id))
            while outstanding:
                self._check(cancel)
                done, outstanding = wait(outstanding, timeout=0.2, return_when=FIRST_COMPLETED)
                for future in done:
                    external_id, rows = future.result()
                    theater = theaters[external_id]
                    for index, item in enumerate(rows):
                        self._check(cancel)
                        records, parent, child = self._velox_movies(item)
                        for movie in records:
                            # A schedule entry supplies parent identity even when the
                            # release-window catalog did not expose that relationship.
                            known = movies.get(movie["id"], {})
                            movies[movie["id"]] = {**known, **{key: value for key, value in movie.items() if value is not None or key not in known}}
                        if selected is None:
                            continue
                        title_id = child if child in selected else parent if parent in selected else None
                        if not title_id:
                            continue
                        technologies = item.get("TECNOLOGIAS") or []
                        technologies = technologies if isinstance(technologies, list) else [technologies]
                        parts = [_text(value.get("DESCRICAO") if isinstance(value, dict) else value) for value in technologies]
                        raw = " | ".join(value for value in parts if value) or "2D"
                        instant = _text(item.get("DATASESSAO"))
                        screen = item.get("SALA") or item.get("NOMESALA") or item.get("IDSALA")
                        self._add_session(result, self._session(connection, theater, title_id,
                            item.get("IDPROGRAMACAO") or f"{external_id}:{child}:{instant}:{index}",
                            instant[:10], instant[11:16], raw,
                            [{"name": "Velox", "url": item.get("URL"), "kind": "booking"}],
                            audio=item.get("IDIOMA_AUDIO") or item.get("AUDIO_LANGUAGE"),
                            subtitle=item.get("IDIOMA_LEGENDA") or item.get("SUBTITLE_LANGUAGE"),
                            attributes=parts, screen=screen, vip=_vip(f"{raw} {screen or ''}"),
                            provider_child_id=child or None, provider_parent_id=parent or None))
                    external_id = next(iterator, None)
                    if external_id is not None:
                        outstanding.add(executor.submit(schedule, external_id))
        finally:
            for future in outstanding:
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
        result.movies, result.theaters = list(movies.values()), list(theaters.values())
        if selected is None:
            result.notes.append("Velox movie availability includes the release catalog and theater schedules for the next 120 days.")
        return result
