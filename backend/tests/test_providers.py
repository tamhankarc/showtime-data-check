"""Provider fixtures are intentionally offline and contain no live credentials."""
import gzip
import hashlib
import hmac
import io
import json
import threading
import urllib.error
import urllib.parse
from types import SimpleNamespace

import pytest

from app import providers
from app.providers import ProviderCancelled, ProviderError, ProviderService, cinemark_theater_identity


CONNECTION = {"country_code": "BR", "provider_country_code": "BR", "timezone": "America/Sao_Paulo"}


def settings(**overrides):
    values = dict(is_api_key="fixture-is-key", velox_username="fixture-user",
        velox_password="fixture-password", ingresso_partnerships=["warner", "sony"],
        cinemark_relay_url="", cinemark_relay_secret="", request_timeout=20, provider_concurrency=2, velox_concurrency=6)
    return SimpleNamespace(**(values | overrides))


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    class Forbidden:
        def open(self, *args, **kwargs):
            raise AssertionError("Offline tests must never contact an external endpoint.")
    monkeypatch.setattr(providers.urllib.request, "build_opener", lambda *args: Forbidden())


class FixtureService(ProviderService):
    def __init__(self, route, **values):
        super().__init__(settings(**values))
        self.route = route
        self.calls = []

    def _fetch_json(self, url, cancel, headers=None, **kwargs):
        self._check(cancel)
        self.calls.append((url, headers or {}, kwargs))
        return json.loads(json.dumps(self.route(url, headers or {}, kwargs)))


def test_unsupported_provider_and_empty_selection_do_not_fetch():
    service = FixtureService(lambda *_: pytest.fail("No transport expected"))
    assert service.showtimes({"provider_code": "IS"}, set(), threading.Event()).showtimes == []
    with pytest.raises(ProviderError, match="not supported"):
        service.catalog({"provider_code": "BO"}, threading.Event())


def test_cancel_before_request_does_not_fetch():
    service = FixtureService(lambda *_: pytest.fail("No transport expected"))
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ProviderCancelled):
        service.catalog({"provider_code": "IS"}, cancel)


def test_is_catalog_pages_contain_only_movie_identities():
    def route(url, headers, _):
        assert headers["X-Api-Key"] == "fixture-is-key"
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        assert query["countries"] == ["AU"]
        if "page_token" not in query:
            return {"movies": [{"id": "a", "title": "Alpha", "original_title": "Original Alpha",
                    "release_dates": {"AU": "2099-11-01"}}], "meta_info": {"next_page_token": "next"}}
        return {"movies": [{"id": "b", "title": "Beta"}]}
    service = FixtureService(route)
    result = service.catalog({"provider_code": "IS", "country_code": "AU"}, threading.Event())
    assert [entry["id"] for entry in result.movies] == ["a", "b"]
    assert result.movies[0]["release"] == "2099-11-01"
    assert result.movies[0]["original_name"] == "Original Alpha"
    assert result.showtimes == [] and result.theaters == []
    assert len(service.calls) == 2


@pytest.mark.parametrize("next_page", [
    "https://attacker.invalid/v5/movies", "http://api.internationalshowtimes.com/v5/movies",
    "https://api.internationalshowtimes.com:444/v5/movies",
    "https://credentials@api.internationalshowtimes.com/v5/movies",
    "https://api.internationalshowtimes.com/v5/showtimes",
    "https://api.internationalshowtimes.com/v5/movies#fragment",
])
def test_is_rejects_cross_origin_and_wrong_path_pagination(next_page):
    service = FixtureService(lambda *_: {"movies": [], "meta_info": {"next_page_url": next_page}})
    with pytest.raises(ProviderError, match="unsafe pagination"):
        service.catalog({"provider_code": "IS", "country_code": "AU"}, threading.Event())
    assert len(service.calls) == 1


def test_is_stops_repeated_pagination_tokens():
    service = FixtureService(lambda *_: {"movies": [], "meta_info": {"next_page_token": "repeat"}})
    with pytest.raises(ProviderError, match="repeated"):
        service.catalog({"provider_code": "IS", "country_code": "AU"}, threading.Event())
    assert len(service.calls) == 2


def test_is_selected_movie_preserves_local_time_languages_and_fallback_links():
    def route(url, *_):
        path = urllib.parse.urlsplit(url).path
        if path.endswith("/chains"):
            return {"chains": [{"id": "chain-1", "name": "Cinema Chain"}]}
        if path.endswith("/cinemas"):
            return {"cinemas": [{"id": "cinema-1", "name": "Central", "chain_id": "chain-1",
                "timezone": "America/Los_Angeles", "website": "https://theater.example/",
                "location": {"address": {"display_text": "123 Main St", "city": "City"}, "lat": 0, "lon": 0}}]}
        assert path.endswith("/showtimes")
        return {"showtimes": [
            {"id": "s1", "movie_id": "movie-1", "cinema_id": "cinema-1", "start_at": "2099-11-04T01:30:00Z",
             "attribute_codes": ["IMAX", "dubbed"], "audio_language": "ES", "subtitle_language": ["EN", "FR"],
             "booking_link": "", "movie_page_link": "https://movie.example/film", "screen_name": "Screen 2"},
            {"id": "other", "movie_id": "not-selected", "cinema_id": "cinema-1", "start_at": "2099-11-04T01:30:00Z"},
            {"id": "old", "movie_id": "movie-1", "cinema_id": "cinema-1", "start_at": "2000-01-01T00:00:00Z"},
        ]}
    service = FixtureService(route)
    result = service.showtimes({**CONNECTION, "provider_code": "IS"}, {"movie-1"}, threading.Event())
    assert len(result.showtimes) == 1
    session = result.showtimes[0]
    assert session["date"] == "2099-11-03" and session["time"] == "17:30"
    assert session["audio"] == ["ES"] and session["subtitle"] == ["EN", "FR"]
    assert session["format"] == "IMAX;dubbed"
    assert session["booking_url"] is None and session["movie_url"] == "https://movie.example/film"
    assert session["links"][0]["kind"] == "movie"
    assert result.theaters[0]["lat"] == 0 and result.theaters[0]["chain_name"] == "Cinema Chain"


def test_is_uses_seed_theater_timezone_when_provider_omits_it():
    def route(url, *_):
        if "/chains?" in url:
            return []
        if "/cinemas?" in url:
            return [{"id": "west", "name": "West"}]
        return [{"id": "show", "movie_id": "m", "cinema_id": "west", "start_at": "2099-08-01T02:00:00Z"}]
    result = FixtureService(route).showtimes({**CONNECTION, "provider_code": "IS", "theater_timezones": {"west": "America/Los_Angeles"}}, {"m"}, threading.Event())
    assert result.showtimes[0]["date"] == "2099-07-31"
    assert result.showtimes[0]["time"] == "19:00"


def test_is_missing_credential_is_public_safe():
    service = ProviderService(settings(is_api_key=""))
    with pytest.raises(ProviderError, match="credential"):
        service.catalog({"provider_code": "IS", "country_code": "MX"}, threading.Event())


def ingresso_route(url, *_):
    if "/events/" in url:
        return [{"id": "m", "title": "Movie", "originalTitle": "Original", "premiereDate": {"year": 2099, "month": 1, "day": 1}}]
    if "/theaters/" in url:
        return [{"id": "t", "name": "Cinema", "corporationId": "chain", "corporation": "Chain",
                 "address": "Avenue", "number": "2", "cityName": "City", "geolocation": {"latitude": 0, "longitude": -42}}]
    assert "/sessions/event/m/" in url
    return [{"date": "2099-01-02", "theaters": [{"id": "t", "rooms": [
        {"name": "Sala 1", "sessions": [{"id": "regular", "type": ["2D", "Legendado"],
            "date": {"localDate": "2099-01-02", "hour": "20:00"}, "siteURL": "https://booking.example/regular"}]},
        {"name": "Sala VIP", "sessions": [{"id": "vip", "type": ["2D", "Legendado"],
            "date": {"localDate": "2099-01-02", "hour": "20:00"}, "siteURL": "https://booking.example/vip"}]},
    ]}]}]


def test_ingresso_catalog_has_partnership_metadata_and_no_sessions():
    service = FixtureService(ingresso_route)
    result = service.catalog({**CONNECTION, "provider_code": "INGRESSO"}, threading.Event())
    assert len(result.movies) == 1
    assert result.movies[0]["partnerships"] == ["sony", "warner"]
    assert result.theaters[0]["address"] == "Avenue, 2"
    assert result.theaters[0]["lat"] == 0
    assert result.showtimes == []
    assert not any("/sessions/" in call[0] for call in service.calls)


def test_ingresso_preserves_vip_and_deduplicates_partnership_copies():
    result = FixtureService(ingresso_route).showtimes({**CONNECTION, "provider_code": "INGRESSO"}, {"m"}, threading.Event())
    assert len(result.showtimes) == 2
    regular, vip = result.showtimes
    assert regular["vip"] is False
    assert vip["vip"] is True and vip["format"] == "2D | Legendado | Vip"
    assert vip["screen"] == "Sala VIP"
    assert regular["booking_url"] != vip["booking_url"]


CINEMARK = [{"TheaterId": "2140", "Name": "Boulevard Shopping Mogi Guaçu", "Movies": [
    {"Id": "m1", "Name": "Selected", "Dates": [{"Date": "2099-01-02", "ShowTimes": [
        {"Time": "14:30", "Format": "IMAX 3D", "Language": "Legendado", "URL": "https://booking.example/imax", "Auditorium": "1"},
        {"Time": "25:00", "Format": "2D", "Language": "Dublado"},
    ]}]},
    {"Id": "m2", "Name": "Other", "Dates": [{"Date": "2099-01-02", "ShowTimes": [{"Time": "18:00"}]}]},
]}]


def test_cinemark_catalog_discards_sessions_and_preserves_movie_ids():
    service = FixtureService(lambda *_: CINEMARK)
    result = service.catalog({**CONNECTION, "provider_code": "CINEMARK"}, threading.Event())
    assert [movie["id"] for movie in result.movies] == ["m1", "m2"]
    assert result.theaters[0]["id"] == "2140" and result.showtimes == []


def test_cinemark_selected_movie_and_provider_format():
    result = FixtureService(lambda *_: CINEMARK).showtimes({**CONNECTION, "provider_code": "CINEMARK"}, {"m1"}, threading.Event())
    assert len(result.showtimes) == 1
    assert result.showtimes[0]["title"] == "m1"
    assert result.showtimes[0]["format"] == "IMAX 3D | Legendado"
    assert result.showtimes[0]["theater"] == "2140"


def test_cinemark_identity_prefers_provider_id_then_verified_fallback():
    assert cinemark_theater_identity({"Name": "Boulevard Shopping Mogi Guaçu", "CinemaId": 999}) == "999"
    assert cinemark_theater_identity({"Name": "Boulevard Shopping Mogi Guaçu"}) == "2140"
    assert cinemark_theater_identity({"Name": "KT 123 New Cinema"}) == "123"
    assert cinemark_theater_identity({"Name": "Unknown Cinema"}).startswith("name-")


def test_cinemark_relay_signature_fixed_route_body_and_no_secret_headers():
    secret = "x" * 48
    def route(url, headers, kwargs):
        assert url == "https://fixture123.lambda-url.us-east-1.on.aws/cinemark"
        assert kwargs["body"] == b'{"version":1}' and kwargs["gzip_response"] is True
        assert kwargs["timeout_budget"] == 200
        canonical = "\n".join(("v1", "POST", "/cinemark", headers["X-Relay-Timestamp"],
            headers["X-Relay-Nonce"], hashlib.sha256(kwargs["body"]).hexdigest()))
        assert headers["X-Relay-Signature"] == hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
        assert secret not in json.dumps(headers)
        return CINEMARK
    service = FixtureService(route, cinemark_relay_url="https://fixture123.lambda-url.us-east-1.on.aws/cinemark", cinemark_relay_secret=secret)
    assert service.catalog({**CONNECTION, "provider_code": "CINEMARK"}, threading.Event()).movies


@pytest.mark.parametrize("url", ["https://example.com/cinemark", "http://fixture.lambda-url.us-east-1.on.aws/cinemark",
    "https://fixture.lambda-url.us-east-1.on.aws/not-cinemark", "https://fixture.lambda-url.us-east-1.on.aws/cinemark?url=another"])
def test_cinemark_relay_rejects_arbitrary_destination(url):
    service = FixtureService(lambda *_: pytest.fail("Must reject before transport"), cinemark_relay_url=url, cinemark_relay_secret="x" * 48)
    with pytest.raises(ProviderError, match="configuration"):
        service.catalog({**CONNECTION, "provider_code": "CINEMARK"}, threading.Event())


def velox_route(url, *_):
    path = urllib.parse.urlsplit(url).path
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert json.loads(query["AUTENTICACAO"][0]) == {"USUARIO": "fixture-user", "SENHA": "fixture-password"}
    if path.endswith("Get_Cinema_Filmes"):
        return {"ReturnOfGet_Cinema_Filmes": {"FILMES": [{"IDFILME": "recent", "TITULO": "Recent Movie", "DT_ESTREIA": "2099-01-01"}]}}
    if path.endswith("Get_Cinema_Pracas"):
        return {"ReturnOfGet_Cinema_Pracas": {"PRACAS": [{"CODPRACA": "t1", "DESCRICAO": "Cinema", "CIDADE": "City", "ENDERECO": "Main Street"}]}}
    assert path.endswith("Get_Cinema_Programacao")
    assert json.loads(query["GETPROG"][0])["CODPRACA"] == "t1"
    return {"ReturnOfGet_Cinema_Programacao": {"PROGS": [
        {"IDFILMEPAI": "parent", "IDFILMEPAI_DESC": "Older Movie", "IDFILME": "child-dub", "IDFILME_DESC": "Older Movie - Dublado",
         "DATASESSAO": "2099-01-02T14:00:00", "TECNOLOGIAS": [{"DESCRICAO": "2D"}, {"DESCRICAO": "Dublado"}], "URL": "https://booking.example/dub", "IDPROGRAMACAO": "s1"},
        {"IDFILMEPAI": "parent", "IDFILMEPAI_DESC": "Older Movie", "IDFILME": "child-sub", "IDFILME_DESC": "Older Movie - Legendado",
         "DATASESSAO": "2099-01-02T16:00:00", "TECNOLOGIAS": ["IMAX", "Legendado"], "URL": "https://booking.example/sub", "IDPROGRAMACAO": "s2"},
    ]}}


def test_velox_catalog_scans_schedules_discovers_older_parent_child_ids_and_discards_sessions():
    result = FixtureService(velox_route).catalog({**CONNECTION, "provider_code": "VELOX"}, threading.Event())
    movies = {movie["id"]: movie for movie in result.movies}
    assert set(movies) == {"recent", "parent", "child-dub", "child-sub"}
    assert movies["child-dub"]["parent_id"] == "parent"
    assert result.showtimes == [] and result.warnings == []
    assert result.notes and "120 days" in result.notes[0]


def test_velox_parent_selection_loads_both_variants_once():
    result = FixtureService(velox_route).showtimes({**CONNECTION, "provider_code": "VELOX"}, {"parent"}, threading.Event())
    assert len(result.showtimes) == 2
    assert {show["title"] for show in result.showtimes} == {"parent"}
    assert {show["provider_child_id"] for show in result.showtimes} == {"child-dub", "child-sub"}
    assert result.showtimes[0]["format"] == "2D | Dublado"


def test_velox_child_selection_does_not_load_sibling_and_precedes_parent_selection():
    service = FixtureService(velox_route)
    result = service.showtimes({**CONNECTION, "provider_code": "VELOX"}, {"child-sub"}, threading.Event())
    assert len(result.showtimes) == 1 and result.showtimes[0]["title"] == "child-sub"
    result = service.showtimes({**CONNECTION, "provider_code": "VELOX"}, {"parent", "child-sub"}, threading.Event())
    assert len(result.showtimes) == 2
    assert [show["title"] for show in result.showtimes] == ["parent", "child-sub"]


def test_provider_parse_errors_never_expose_raw_exception_urls_or_credentials():
    def route(*_):
        raise RuntimeError("http://upstream.invalid/?password=fixture-password&api_key=secret")
    with pytest.raises(ProviderError) as captured:
        FixtureService(route).catalog({**CONNECTION, "provider_code": "VELOX"}, threading.Event())
    assert str(captured.value) == "The VELOX response could not be processed."
    assert "password" not in str(captured.value)


class ImmediateEvent(threading.Event):
    def __init__(self):
        super().__init__()
        self.waits = []
    def wait(self, timeout=None):
        self.waits.append(timeout)
        return self.is_set()


class Response(io.BytesIO):
    def __init__(self, body, headers=None):
        super().__init__(body)
        self.headers = headers or {}


def transport(monkeypatch, operation):
    class Opener:
        def open(self, request, timeout):
            return operation(request, timeout)
    monkeypatch.setattr(providers.urllib.request, "build_opener", lambda *_: Opener())
    return ProviderService(settings())


def test_transport_redacts_http_error_url_and_body(monkeypatch):
    def fail(*_):
        raise urllib.error.HTTPError("http://upstream/?secret=private", 403, "password=private", {}, io.BytesIO(b"secret=private"))
    service = transport(monkeypatch, fail)
    with pytest.raises(ProviderError) as captured:
        service._fetch_json("https://fixture.invalid/", threading.Event())
    assert str(captured.value) == "The provider request failed (HTTP 403)."


def test_transport_retries_transient_error_then_succeeds(monkeypatch):
    calls = []
    def response(*_):
        calls.append(1)
        if len(calls) < 3:
            raise urllib.error.HTTPError("https://fixture.invalid/", 503, "temporary", {}, io.BytesIO())
        return Response(b'{"items":[]}')
    cancel = ImmediateEvent()
    assert transport(monkeypatch, response)._fetch_json("https://fixture.invalid/", cancel) == {"items": []}
    assert len(calls) == 3 and cancel.waits == [1, 2]


def test_transport_bounds_declared_and_actual_response_bytes(monkeypatch):
    monkeypatch.setattr(providers, "MAX_RESPONSE_BYTES", 8)
    service = transport(monkeypatch, lambda *_: Response(b'{}', {"Content-Length": "9"}))
    with pytest.raises(ProviderError, match="size limit"):
        service._fetch_json("https://fixture.invalid/", threading.Event())
    service = transport(monkeypatch, lambda *_: Response(b'0123456789'))
    with pytest.raises(ProviderError, match="size limit"):
        service._fetch_json("https://fixture.invalid/", threading.Event())


def test_transport_bounds_uncompressed_relay_body(monkeypatch):
    monkeypatch.setattr(providers, "MAX_RESPONSE_BYTES", 16)
    service = transport(monkeypatch, lambda *_: Response(gzip.compress(b'"' + b'a' * 100 + b'"'), {"Content-Encoding": "gzip"}))
    with pytest.raises(ProviderError, match="size limit"):
        service._fetch_json("https://fixture.invalid/", threading.Event(), gzip_response=True)


def test_transport_rejects_invalid_json_and_unsafe_link_scheme(monkeypatch):
    service = transport(monkeypatch, lambda *_: Response(b'not json password=secret'))
    with pytest.raises(ProviderError, match="invalid JSON"):
        service._fetch_json("https://fixture.invalid/", threading.Event())
    assert providers._url("javascript:alert(1)") is None
    assert providers._url("https://user:password@website.example/") is None
    assert providers._url("https://valid.example/") == "https://valid.example/"


def test_velox_catalog_concurrency_is_bounded_and_scan_is_complete():
    lock, active, peak = threading.Lock(), 0, 0
    def route(url, *_):
        nonlocal active, peak
        path = urllib.parse.urlsplit(url).path
        if path.endswith("Get_Cinema_Filmes"):
            return {"ReturnOfGet_Cinema_Filmes": {"FILMES": []}}
        if path.endswith("Get_Cinema_Pracas"):
            return {"ReturnOfGet_Cinema_Pracas": {"PRACAS": [{"CODPRACA": str(i), "DESCRICAO": f"Cinema {i}"} for i in range(8)]}}
        with lock:
            active += 1
            peak = max(peak, active)
        theater_id = json.loads(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["GETPROG"][0])["CODPRACA"]
        value = {"ReturnOfGet_Cinema_Programacao": {"PROGS": [{"IDFILMEPAI": "p" + theater_id, "IDFILMEPAI_DESC": "Film " + theater_id}]}}
        with lock:
            active -= 1
        return value
    result = FixtureService(route, provider_concurrency=1, velox_concurrency=2).catalog({**CONNECTION, "provider_code": "VELOX"}, threading.Event())
    assert peak <= 2 and len(result.movies) == 8 and result.showtimes == []


def test_velox_fanout_limit_is_separate_from_global_provider_limit_and_never_exceeds_six():
    assert ProviderService(settings(provider_concurrency=2)).concurrency == 6
    assert ProviderService(settings(provider_concurrency=1, velox_concurrency=3)).concurrency == 3
    assert ProviderService(settings(velox_concurrency=100)).concurrency == 6
    assert ProviderService(settings(velox_concurrency=0)).concurrency == 1


@pytest.mark.parametrize("code", ["IS", "INGRESSO", "VELOX"])
def test_structured_provider_errors_are_not_mistaken_for_empty_successful_catalogs(code):
    service = FixtureService(lambda *_: {"error": "fixture invalid credential"})
    with pytest.raises(ProviderError) as captured:
        service.catalog({**CONNECTION, "provider_code": code}, threading.Event())
    assert "fixture invalid credential" not in str(captured.value)


def test_velox_same_parent_child_id_without_parent_name_still_discovers_movie():
    movies, parent, child = ProviderService._velox_movies({"IDFILMEPAI": "m", "IDFILME": "m", "IDFILME_DESC": "One Movie"})
    assert parent == child == "m"
    assert len(movies) == 1 and movies[0]["name"] == "One Movie"


def test_cinemark_legacy_unknown_name_identity_is_whitespace_stable():
    assert cinemark_theater_identity({"Name": "An Unknown  Cinema"}) == cinemark_theater_identity({"Name": "An Unknown Cinema"})


def test_relay_transient_retry_uses_fresh_signed_nonce(monkeypatch):
    requests = []
    def response(request, _):
        requests.append(request)
        if len(requests) == 1:
            raise urllib.error.HTTPError(request.full_url, 503, "temporary", {}, io.BytesIO())
        return Response(gzip.compress(b"[]"), {"Content-Encoding": "gzip"})
    service = transport(monkeypatch, response)
    service.settings.cinemark_relay_secret = "x" * 48
    body = b'{"version":1}'
    headers = service._relay_headers(body)
    assert service._fetch_json("https://fixture.lambda-url.us-east-1.on.aws/cinemark", ImmediateEvent(), headers,
                               body=body, gzip_response=True) == []
    assert requests[0].get_header("X-relay-nonce") != requests[1].get_header("X-relay-nonce")
    assert requests[0].get_header("X-relay-signature") != requests[1].get_header("X-relay-signature")


def test_transport_respects_bounded_retry_after(monkeypatch):
    attempts = []
    def response(*_):
        attempts.append(1)
        if len(attempts) == 1:
            raise urllib.error.HTTPError("https://fixture.invalid/", 429, "rate limit", {"Retry-After": "600"}, io.BytesIO())
        return Response(b"[]")
    cancel = ImmediateEvent()
    assert transport(monkeypatch, response)._fetch_json("https://fixture.invalid/", cancel) == []
    assert cancel.waits == [30]


def test_transport_cancel_between_chunks_does_not_return_partial_data(monkeypatch):
    cancel = threading.Event()
    class CancellingResponse(Response):
        def read(self, size=-1):
            value = super().read(size)
            cancel.set()
            return value
    service = transport(monkeypatch, lambda *_: CancellingResponse(b"[]"))
    with pytest.raises(ProviderCancelled):
        service._fetch_json("https://fixture.invalid/", cancel)


def test_ingresso_vip_detection_does_not_mutate_source_type_list():
    source = ingresso_route("https://fixture/sessions/event/m/")
    original = json.loads(json.dumps(source))
    def route(url, *_):
        return source if "/sessions/" in url else ingresso_route(url)
    service = FixtureService(route, ingresso_partnerships=["warner"])
    assert service.showtimes({**CONNECTION, "provider_code": "INGRESSO"}, {"m"}, threading.Event()).showtimes
    assert source == original


@pytest.mark.parametrize("budget,expected", [(None, 20), (2, 5), (500, 240), (180, 180)])
def test_transport_override_timeout_budget_is_bounded_without_changing_default(monkeypatch, budget, expected):
    received = []
    def response(_, timeout):
        received.append(timeout)
        return Response(b"[]")
    service = transport(monkeypatch, response)
    assert service._fetch_json("https://fixture.invalid/", threading.Event(), timeout_budget=budget) == []
    assert received == [expected] and service.timeout == 20


def test_is_transport_keeps_default_timeout_instead_of_relay_budget(monkeypatch):
    received = []
    def response(_, timeout):
        received.append(timeout)
        return Response(b'{"movies":[]}')
    service = transport(monkeypatch, response)
    result = service.catalog({"provider_code": "IS", "country_code": "AU"}, threading.Event())
    assert result.movies == [] and received == [20]


def test_signed_relay_uses_at_least_200_seconds_but_respects_larger_configured_budget():
    for configured, expected in ((20, 200), (120, 200), (240, 240)):
        def route(_, __, kwargs):
            assert kwargs["timeout_budget"] == expected
            return CINEMARK
        service = FixtureService(route, request_timeout=configured,
            cinemark_relay_url="https://fixture123.lambda-url.us-east-1.on.aws/cinemark", cinemark_relay_secret="x" * 48)
        assert service.catalog({**CONNECTION, "provider_code": "CINEMARK"}, threading.Event()).movies


@pytest.mark.parametrize("body", [b"not-gzip private-provider-body", gzip.compress(b"[]")[:-4]])
def test_corrupt_or_truncated_gzip_is_not_misreported_as_network_failure(monkeypatch, body):
    calls = []
    def response(*_):
        calls.append(1)
        return Response(body, {"Content-Encoding": "gzip"})
    service = transport(monkeypatch, response)
    with pytest.raises(ProviderError) as captured:
        service._fetch_json("https://fixture.invalid/", ImmediateEvent(), gzip_response=True)
    assert str(captured.value) == "The provider returned an invalid or incomplete compressed response."
    assert len(calls) == 1 and "private-provider-body" not in str(captured.value)


@pytest.mark.parametrize("failure", [
    TimeoutError("private-timeout-details"),
    urllib.error.URLError(TimeoutError("private-timeout-details")),
])
def test_actual_timeout_retries_then_reports_safe_timeout_category(monkeypatch, failure):
    attempts = []
    def response(*_):
        attempts.append(1)
        raise failure
    service = transport(monkeypatch, response)
    with pytest.raises(ProviderError) as captured:
        service._fetch_json("https://fixture.invalid/?secret=private", ImmediateEvent())
    assert str(captured.value) == "The provider request timed out. Please retry."
    assert len(attempts) == 3 and "private" not in str(captured.value)


def test_non_timeout_url_error_remains_sanitized_network_failure(monkeypatch):
    def response(*_):
        raise urllib.error.URLError(OSError("private-dns-or-tls-details"))
    with pytest.raises(ProviderError) as captured:
        transport(monkeypatch, response)._fetch_json("https://fixture.invalid/?secret=private", ImmediateEvent())
    assert str(captured.value) == "The provider could not be reached. Please retry."
