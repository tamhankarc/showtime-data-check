from contextlib import asynccontextmanager
from datetime import datetime
import hashlib
import hmac
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import String, case, cast, exists, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from . import models
from .catalog import generic_initial
from .config import Settings
from .db import make_engine
from .jobs import JobManager
from .migrations import initialize_schema
from .results import filtered, metrics, theater_rows, usable_url, xlsx_export
from .security import COOKIE, authenticate, require_admin, session_user


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=1024)


class CheckInput(BaseModel):
    country_code: str
    movie_ids: list[int] = Field(default_factory=list, max_length=100)
    provider_movie_ids: list[int] = Field(default_factory=list, max_length=100)
    provider_codes: list[str] = Field(default_factory=list, max_length=4)


class RefreshInput(BaseModel):
    connection_ids: list[int] = Field(default_factory=list, max_length=100)


class RetryInput(BaseModel):
    provider_codes: list[str] = Field(default_factory=list, max_length=4)


class MoveInput(BaseModel):
    provider_movie_ids: list[int] = Field(min_length=1, max_length=200)
    target_group_id: int | None = None


def row_dict(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def create_app(settings=None, provider_service=None, engine=None):
    settings = settings or Settings()
    settings.validate()
    engine = engine or make_engine(settings.database_url)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(app):
        initialize_schema(engine)
        from .providers import ProviderService
        app.state.jobs = JobManager(sessions, settings, provider_service or ProviderService(settings))
        yield
        app.state.jobs.close()

    app = FastAPI(title="Provider Data Check", version="1.0.0", lifespan=lifespan,
                  docs_url=None if settings.production else "/docs", redoc_url=None)
    app.state.settings, app.state.sessions, app.state.engine = settings, sessions, engine

    @app.middleware("http")
    async def protection(request, call_next):
        if request.url.path != "/health":
            supplied = request.headers.get("x-proxy-secret", "")
            if not settings.proxy_secret or not hmac.compare_digest(supplied, settings.proxy_secret):
                return JSONResponse({"detail": "Access through the application is required."}, status_code=403)
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                origin = request.headers.get("origin", "").rstrip("/")
                if origin != settings.app_origin:
                    return JSONResponse({"detail": "The request origin is not allowed."}, status_code=403)
                if int(request.headers.get("content-length", "0") or "0") > 1024 * 1024:
                    return JSONResponse({"detail": "Request is too large."}, status_code=413)
        response = await call_next(request)
        response.headers.update({"Cache-Control": "no-store, private", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"})
        return response

    @app.exception_handler(IntegrityError)
    async def integrity_error(_, error):
        return JSONResponse({"detail": "This record conflicts with an existing record or assignment."}, status_code=409)

    def db_session():
        with sessions() as db:
            yield db

    def user(request: Request, db=Depends(db_session)):
        return session_user(request, db, settings)

    @app.get("/health")
    def health(db=Depends(db_session)):
        db.execute(text("SELECT 1"))
        return {"status": "ok"}

    @app.post("/api/login")
    def login(payload: LoginInput, request: Request, response: Response, db=Depends(db_session)):
        token, result = authenticate(db, settings, payload.username.strip(), payload.password, request.headers.get("x-client-ip", "unknown")[:200])
        response.set_cookie(COOKIE, token, max_age=settings.session_hours * 3600, httponly=True,
                            secure=settings.production, samesite="strict", path="/")
        return result

    @app.post("/api/logout")
    def logout(request: Request, response: Response, db=Depends(db_session)):
        token = request.cookies.get(COOKIE, "")
        record = db.get(models.AuthSession, hashlib.sha256(token.encode()).hexdigest())
        if record:
            db.delete(record)
            db.commit()
        response.delete_cookie(COOKIE, path="/", httponly=True, secure=settings.production, samesite="strict")
        return {"ok": True}

    @app.get("/api/session")
    def session(current=Depends(user)):
        return current

    @app.get("/api/bootstrap")
    def bootstrap(db=Depends(db_session), current=Depends(user)):
        return {"countries": [row_dict(row) for row in db.scalars(select(models.Country).order_by(models.Country.name))],
                "connections": [row_dict(row) for row in db.scalars(select(models.Connection).order_by(models.Connection.country_code, models.Connection.provider_code))],
                "role": current["role"]}

    def list_search(q, columns, exact=False, prefix=False):
        value = q.strip().lower()
        escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return or_(*(func.lower(cast(column, String)) == value if exact else
                     func.lower(cast(column, String)).like(escaped + "%" if prefix else "%" + escaped + "%", escape="\\")
                     for column in columns))

    def list_order(q, columns):
        if not q.strip():
            return []
        return [case((list_search(q, columns, exact=True), 0),
                     (list_search(q, columns, prefix=True), 1), else_=2)]

    def list_page(db, statement, page, page_size):
        page, page_size = max(1, page), min(500, max(1, page_size))
        total = db.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0
        return statement.offset((page - 1) * page_size).limit(page_size), {
            "total": total, "page": page, "page_size": page_size}

    def movie_groups(db, country=None, provider=None, include_inactive=False, q="", exact=False, page=1, page_size=200):
        statement = select(models.MovieGroup)
        if country:
            statement = statement.where(models.MovieGroup.country_code == country)
        if not include_inactive:
            statement = statement.where(models.MovieGroup.active.is_(True))
        member_conditions = []
        if not include_inactive:
            member_conditions += [models.ProviderMovie.active.is_(True), models.Connection.enabled.is_(True)]
        if country:
            member_conditions.append(models.Connection.country_code == country)
        if provider and provider != "all":
            member_conditions.append(models.Connection.provider_code == provider)
        member_select = select(models.ProviderMovie.id).join(models.Connection).where(
            models.ProviderMovie.group_id == models.MovieGroup.id, *member_conditions)
        statement = statement.where(exists(member_select))
        group_columns = [models.MovieGroup.name, models.MovieGroup.id]
        member_columns = [models.ProviderMovie.name, models.ProviderMovie.original_name,
                          models.ProviderMovie.external_id, models.ProviderMovie.id, models.ProviderMovie.url_key]
        if q.strip():
            statement = statement.where(or_(list_search(q, group_columns, exact),
                exists(member_select.where(list_search(q, member_columns, exact)))))
        statement = statement.order_by(*list_order(q, group_columns), models.MovieGroup.active.desc(),
                                       func.lower(models.MovieGroup.name), models.MovieGroup.id)
        statement, metadata = list_page(db, statement, page, page_size)
        groups = list(db.scalars(statement))
        # Only load members for this page and bucket once; never scan the entire
        # provider catalog for every group (the seeded catalog has ~70k groups).
        buckets = {group.id: [] for group in groups}
        if buckets:
            members = select(models.ProviderMovie, models.Connection).join(models.Connection).where(
                models.ProviderMovie.group_id.in_(buckets), *member_conditions).order_by(
                    models.Connection.provider_code, func.lower(models.ProviderMovie.name), models.ProviderMovie.id)
            for member, connection in db.execute(members):
                buckets[member.group_id].append({"id": member.id, "provider_code": connection.provider_code,
                    "external_id": member.external_id, "name": member.name,
                    "original_name": member.original_name, "url_key": member.url_key, "active": member.active})
        return {"items": [{**row_dict(group), "provider_ids": buckets[group.id]} for group in groups], **metadata}

    @app.get("/api/movies")
    @app.get("/api/movie-groups")
    def movies(country: str | None = None, provider: str | None = None, include_inactive: bool = False,
               q: str = "", exact: bool = False, page: int = 1, page_size: int = 200,
               db=Depends(db_session), current=Depends(user)):
        return movie_groups(db, country, provider, include_inactive, q, exact, page, page_size)

    @app.get("/api/provider-movies")
    def provider_movies(country: str | None = None, provider: str | None = None, q: str = "",
                        exact: bool = False, include_inactive: bool = False, page: int = 1, page_size: int = 200,
                        db=Depends(db_session), current=Depends(user)):
        statement = select(models.ProviderMovie, models.Connection, models.MovieGroup).join(models.Connection, models.ProviderMovie.connection_id == models.Connection.id).join(models.MovieGroup, models.ProviderMovie.group_id == models.MovieGroup.id)
        if country:
            statement = statement.where(models.Connection.country_code == country)
        if provider and provider != "all":
            statement = statement.where(models.Connection.provider_code == provider)
        if not include_inactive:
            statement = statement.where(models.ProviderMovie.active.is_(True), models.Connection.enabled.is_(True))
        search_columns = [models.ProviderMovie.name, models.ProviderMovie.original_name,
                          models.ProviderMovie.external_id, models.ProviderMovie.id, models.ProviderMovie.url_key,
                          models.MovieGroup.name, models.MovieGroup.id]
        if q.strip():
            statement = statement.where(list_search(q, search_columns, exact))
        statement = statement.order_by(*list_order(q, search_columns), func.lower(models.ProviderMovie.name),
                                       models.ProviderMovie.id)
        statement, metadata = list_page(db, statement, page, page_size)
        output = []
        for movie, connection, group in db.execute(statement):
            output.append({**row_dict(movie), "provider_code": connection.provider_code, "country_code": connection.country_code,
                           "group_name": group.name})
        return {"items": output, **metadata}

    @app.post("/api/movie-groups/move")
    def move_movies(payload: MoveInput, db=Depends(db_session), current=Depends(user)):
        require_admin(current)
        members = list(db.scalars(select(models.ProviderMovie).where(models.ProviderMovie.id.in_(payload.provider_movie_ids))))
        if len(members) != len(set(payload.provider_movie_ids)):
            raise HTTPException(400, "One or more selected movie IDs were not found.")
        connections = {row.id: row for row in db.scalars(select(models.Connection).where(models.Connection.id.in_({m.connection_id for m in members})))}
        country_codes = {connections[m.connection_id].country_code for m in members}
        if country_codes != {"BR"}:
            raise HTTPException(400, "Movie grouping is available only within Brazil.")
        target = db.get(models.MovieGroup, payload.target_group_id) if payload.target_group_id else None
        if payload.target_group_id and (not target or target.country_code != "BR"):
            raise HTTPException(400, "Select an imported Brazil movie group.")
        if not target:
            target = models.MovieGroup(country_code="BR", name=members[0].name)
            db.add(target)
            db.flush()
        old_groups = {m.group_id for m in members}
        for member in members:
            member.group_id, member.manually_grouped = target.id, True
        db.flush()
        for group_id in old_groups:
            group = db.get(models.MovieGroup, group_id)
            if group:
                group.active = db.scalar(select(models.ProviderMovie.id).where(models.ProviderMovie.group_id == group_id, models.ProviderMovie.active.is_(True)).limit(1)) is not None
        target.active = True
        db.commit()
        app.state.jobs.invalidate("BR")
        return {"ok": True, "group_id": target.id}

    @app.post("/api/catalogs/refresh")
    def refresh(payload: RefreshInput, db=Depends(db_session), current=Depends(user)):
        statement = select(models.Connection).join(models.Country).where(models.Connection.enabled.is_(True), models.Country.enabled.is_(True))
        if payload.connection_ids:
            statement = statement.where(models.Connection.id.in_(payload.connection_ids))
        connections = list(db.scalars(statement))
        if not connections:
            raise HTTPException(400, "No enabled provider connections were selected.")
        return {"job_id": app.state.jobs.submit("catalog", current["username"], {"connection_ids": [c.id for c in connections]})}

    @app.post("/api/checks")
    def check(payload: CheckInput, db=Depends(db_session), current=Depends(user)):
        country = db.get(models.Country, payload.country_code)
        if not country or not country.enabled:
            raise HTTPException(400, "Select an enabled country.")
        statement = select(models.ProviderMovie, models.Connection).join(models.Connection).where(models.Connection.country_code == country.code, models.Connection.enabled.is_(True), models.ProviderMovie.active.is_(True))
        if payload.provider_movie_ids:
            statement = statement.where(models.ProviderMovie.id.in_(payload.provider_movie_ids))
        elif payload.movie_ids:
            statement = statement.where(models.ProviderMovie.group_id.in_(payload.movie_ids))
        else:
            raise HTTPException(400, "Select at least one available provider movie.")
        if payload.provider_codes and "all" not in payload.provider_codes:
            statement = statement.where(models.Connection.provider_code.in_(payload.provider_codes))
        matches = list(db.execute(statement))
        if not matches:
            raise HTTPException(400, "No available provider IDs match your selection.")
        if country.code != "BR" and ((payload.provider_movie_ids and len(set(payload.provider_movie_ids)) > 1) or (payload.movie_ids and len(set(payload.movie_ids)) > 1)):
            raise HTTPException(400, "Choose one movie outside Brazil.")
        if payload.provider_movie_ids and not payload.provider_codes and {m.id for m, _ in matches} != set(payload.provider_movie_ids):
            raise HTTPException(400, "One or more movie IDs are unavailable or belong to another country.")
        external_ids, connections = {}, {}
        for movie, connection in matches:
            external_ids.setdefault(connection.provider_code, []).append(movie.external_id)
            connections[connection.id] = connection
        parameters = {"country_code": country.code, "provider_movie_ids": [m.id for m, _ in matches],
                      "movie_ids": sorted({m.group_id for m, _ in matches}), "movie_names": sorted({m.name for m, _ in matches}),
                      "connection_ids": list(connections), "external_ids": external_ids, "provider_codes": list(external_ids)}
        return {"job_id": app.state.jobs.submit("check", current["username"], parameters)}

    @app.get("/api/jobs/{identifier}")
    def job(identifier: str, current=Depends(user)):
        app.state.jobs.purge()
        return app.state.jobs.get_job(identifier, current)

    @app.get("/api/checks/{identifier}")
    def result(identifier: str, request: Request, view: str = "showtimes", page: int = 1, page_size: int = 50, current=Depends(user)):
        all_rows, metadata = app.state.jobs.result(identifier, current)
        rows = filtered(all_rows, request.query_params)
        result_rows = theater_rows(rows) if view == "theaters" else rows
        page_size, page = min(max(page_size, 1), 200), max(page, 1)
        options = {"chains": sorted({r["chain"] for r in all_rows if r["chain"]}), "formats": sorted({r["published_format"] for r in all_rows}),
                   "languages": sorted({r[field] for r in all_rows for field in ("audio_language", "subtitle_language") if r[field]})}
        return {**metadata, "rows": result_rows[(page - 1) * page_size:page * page_size], "total": len(result_rows), "page": page, "page_size": page_size,
                "metrics": metrics(rows), "filters": options}

    @app.get("/api/checks/{identifier}/export")
    def export(identifier: str, request: Request, view: str = "showtimes", current=Depends(user)):
        rows, metadata = app.state.jobs.result(identifier, current)
        if metadata["status"] not in {"done", "partial"}:
            raise HTTPException(409, "Wait for the check to complete before exporting.")
        rows = filtered(rows, request.query_params)
        exported = theater_rows(rows) if view == "theaters" else rows
        metadata["filters"] = dict(request.query_params)
        filename = f"{view}-{metadata['selection']['country_code']}-{datetime.now().strftime('%Y%m%d-%H%M')}.xlsx"
        try:
            content = xlsx_export(exported, metadata, view)
        except Exception:
            raise HTTPException(500, "The workbook could not be generated. Please retry with fewer selected movies.") from None
        return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.delete("/api/checks/{identifier}")
    def cancel(identifier: str, current=Depends(user)):
        app.state.jobs.cancel(identifier, current)
        return {"ok": True}

    @app.post("/api/checks/{identifier}/retry")
    def retry(identifier: str, payload: RetryInput, current=Depends(user)):
        app.state.jobs.retry(identifier, current, payload.provider_codes)
        return {"job_id": identifier}

    resources = {"countries": models.Country, "connections": models.Connection, "theaters": models.Theater, "provider-theaters": models.ProviderTheater, "format-mappings": models.FormatMapping, "movie-format-overrides": models.MovieFormatOverride}
    allowed_fields = {
        "countries": {"code", "name", "timezone", "enabled"},
        "connections": {"country_code", "provider_code", "provider_country_code", "timezone", "enabled"},
        "theaters": {"country_code", "name", "address", "city", "region", "postal_code", "latitude", "longitude", "website", "chain", "timezone", "active"},
        "provider-theaters": {"theater_id", "review_status"},
        "format-mappings": {"country_code", "provider_code", "raw_format", "published_format", "audio_language", "subtitle_language", "is_dubbed", "is_subtitled", "is_luxury"},
        "movie-format-overrides": {"group_id", "format_mapping_id", "published_format", "active"},
    }

    def validate(resource, payload, db, existing=None):
        if set(payload) - allowed_fields[resource]:
            raise HTTPException(400, "One or more fields are not editable.")
        for key, value in payload.items():
            if isinstance(value, str) and (len(value) > (8192 if key == "website" else 1000) or any(ord(c) < 32 and c not in "\n\t" for c in value)):
                raise HTTPException(400, f"{key} is too long or contains unsupported characters.")
            if key in {"enabled", "active", "is_dubbed", "is_subtitled", "is_luxury"} and not isinstance(value, bool):
                raise HTTPException(400, f"{key} must be true or false.")
        value = {**(row_dict(existing) if existing else {}), **payload}
        if "timezone" in value:
            try:
                ZoneInfo(value["timezone"])
            except (ZoneInfoNotFoundError, ValueError, TypeError):
                raise HTTPException(400, "Enter a valid IANA timezone.")
        if resource == "countries":
            if not re.fullmatch(r"[A-Z]{2,3}", str(value.get("code") or "")) or not str(value.get("name") or "").strip():
                raise HTTPException(400, "Enter a country name and uppercase country code.")
            if existing and "code" in payload and payload["code"] != existing.code:
                raise HTTPException(400, "Country codes cannot be renamed. Disable this country and add the new code.")
        if resource in {"connections", "theaters"} and not db.get(models.Country, value.get("country_code")):
            raise HTTPException(400, "Select an existing country.")
        if resource == "connections":
            if existing and any(key in payload and payload[key] != getattr(existing, key) for key in ("country_code", "provider_code")):
                raise HTTPException(400, "A connection's country and provider cannot be renamed. Disable it and add a new connection instead.")
            if value.get("provider_code") not in {"IS", "INGRESSO", "CINEMARK", "VELOX"}:
                raise HTTPException(400, "Select a supported provider.")
            if (value.get("country_code") == "BR") != (value.get("provider_code") != "IS"):
                raise HTTPException(400, "Brazil uses Ingresso, Cinemark or Velox; other countries use IS.")
        if resource == "theaters":
            if existing and value.get("country_code") != existing.country_code:
                raise HTTPException(400, "An existing theater's country cannot be changed. Create the correct country-specific theater instead.")
            if not str(value.get("name") or "").strip():
                raise HTTPException(400, "Theater name is required.")
            if value.get("website") and not usable_url(value["website"]):
                raise HTTPException(400, "Theater URL must be a valid HTTP or HTTPS URL.")
            for key, bound in (("latitude", 90), ("longitude", 180)):
                if value.get(key) is not None:
                    try:
                        if not -bound <= float(value[key]) <= bound:
                            raise ValueError()
                        payload[key] = float(value[key])
                    except (ValueError, TypeError):
                        raise HTTPException(400, f"Invalid {key}.")
        if resource == "provider-theaters":
            if not existing:
                raise HTTPException(405, "Provider theaters are discovered by provider requests.")
            if value.get("review_status") not in {"confirmed", "needs_review", "ignored"}:
                raise HTTPException(400, "Select a valid review status.")
            connection = db.get(models.Connection, existing.connection_id)
            theater = db.get(models.Theater, value.get("theater_id")) if value.get("theater_id") else None
            if value.get("theater_id") and (not theater or theater.country_code != connection.country_code):
                raise HTTPException(400, "Assign a canonical theater in the same country.")
            if value.get("review_status") == "confirmed" and not theater:
                raise HTTPException(400, "Choose a theater before confirming.")
        if resource == "format-mappings":
            if value.get("provider_code") not in {"IS", "INGRESSO", "CINEMARK", "VELOX"} or not str(value.get("raw_format") or "").strip() or not str(value.get("published_format") or "").strip():
                raise HTTPException(400, "Provider, raw format and published format are required.")
        if resource == "movie-format-overrides":
            group = db.get(models.MovieGroup, value.get("group_id"))
            mapping = db.get(models.FormatMapping, value.get("format_mapping_id"))
            if not group or not mapping or (mapping.country_code and mapping.country_code != group.country_code) or not str(value.get("published_format") or "").strip():
                raise HTTPException(400, "Select a movie group, a compatible raw-format mapping and a published format.")
        return payload

    @app.get("/api/{resource}")
    def reference_list(resource: str, country: str | None = None, provider: str | None = None,
                       q: str = "", exact: bool = False, page: int = 1, page_size: int = 200,
                       status: str | None = None, include_inactive: bool = False,
                       db=Depends(db_session), current=Depends(user)):
        if resource not in resources:
            raise HTTPException(404, "Page not found.")
        model = resources[resource]
        statement = select(model)
        country_column, provider_column = getattr(model, "country_code", None), getattr(model, "provider_code", None)
        if resource == "provider-theaters":
            statement = select(model, models.Connection, models.Theater.name).join(
                models.Connection, model.connection_id == models.Connection.id).outerjoin(
                models.Theater, model.theater_id == models.Theater.id)
            country_column, provider_column = models.Connection.country_code, models.Connection.provider_code
            search_columns = [model.name, model.external_id, model.id, model.address, model.city, model.region,
                              model.postal_code, model.theater_id, models.Theater.name]
            order_columns = [func.lower(model.name), model.id]
            if status and status != "all":
                statement = statement.where(model.review_status == status)
        elif resource == "movie-format-overrides":
            statement = select(model, models.MovieGroup, models.FormatMapping).join(
                models.MovieGroup, model.group_id == models.MovieGroup.id).join(
                models.FormatMapping, model.format_mapping_id == models.FormatMapping.id)
            country_column, provider_column = models.MovieGroup.country_code, models.FormatMapping.provider_code
            search_columns = [models.MovieGroup.name, models.MovieGroup.id, models.FormatMapping.raw_format,
                              models.FormatMapping.provider_code, model.published_format, model.id, model.format_mapping_id]
            order_columns = [func.lower(models.MovieGroup.name), model.id]
        elif resource == "countries":
            country_column = model.code
            search_columns, order_columns = [model.name, model.code], [func.lower(model.name), model.code]
        elif resource == "connections":
            statement = select(model).join(models.Country)
            search_columns = [model.country_code, model.provider_code, models.Country.name, model.id]
            order_columns = [model.country_code, model.provider_code, model.id]
        elif resource == "format-mappings":
            search_columns = [model.raw_format, model.published_format, model.provider_code, model.id,
                              model.audio_language, model.subtitle_language]
            order_columns = [func.lower(model.raw_format), model.provider_code, model.id]
        else:
            search_columns = [model.name, model.id, model.address, model.city, model.region, model.postal_code, model.chain]
            order_columns = [func.lower(model.name), model.id]
            if not include_inactive:
                statement = statement.where(model.active.is_(True))
        if country and country_column is not None:
            statement = statement.where(or_(country_column == country, country_column == "") if resource == "format-mappings"
                                         else country_column == country)
        if provider and provider != "all":
            if provider_column is not None:
                statement = statement.where(provider_column == provider)
            else:
                # Country/theater filters use a provider association, without
                # joining multiple identities into duplicate canonical rows.
                matching = select(models.Connection.id).where(models.Connection.provider_code == provider)
                if resource == "theaters":
                    matching = matching.join(models.ProviderTheater, models.ProviderTheater.connection_id == models.Connection.id).where(
                        models.ProviderTheater.theater_id == model.id)
                else:
                    matching = matching.where(models.Connection.country_code == model.code)
                statement = statement.where(exists(matching))
        if q.strip():
            search_condition = list_search(q, search_columns, exact)
            if resource == "theaters":
                identity_match = select(models.ProviderTheater.id).join(
                    models.Connection, models.ProviderTheater.connection_id == models.Connection.id).where(
                        models.ProviderTheater.theater_id == model.id,
                        list_search(q, [models.ProviderTheater.external_id, models.Connection.provider_code], exact))
                search_condition = or_(search_condition, exists(identity_match))
            statement = statement.where(search_condition)
        statement = statement.order_by(*list_order(q, search_columns), *order_columns)
        statement, metadata = list_page(db, statement, page, page_size)
        output = []
        page_rows = list(db.execute(statement))
        provider_ids = {result[0].id: [] for result in page_rows} if resource == "theaters" else {}
        if provider_ids:
            assignments = select(models.ProviderTheater.theater_id, models.Connection.provider_code,
                models.ProviderTheater.external_id, models.ProviderTheater.review_status).join(
                    models.Connection, models.ProviderTheater.connection_id == models.Connection.id).where(
                        models.ProviderTheater.theater_id.in_(provider_ids)).order_by(
                            models.ProviderTheater.theater_id, models.Connection.provider_code,
                            models.ProviderTheater.external_id, models.ProviderTheater.id)
            for theater_id, code, external_id, review_status in db.execute(assignments):
                provider_ids[theater_id].append({"provider_code": code, "external_id": external_id,
                                                "review_status": review_status})
        for result in page_rows:
            row = result[0]
            value = row_dict(row)
            if resource == "provider-theaters":
                connection, theater_name = result[1], result[2]
                value.update(country_code=connection.country_code, provider_code=connection.provider_code, theater_name=theater_name)
            elif resource == "movie-format-overrides":
                group, mapping = result[1], result[2]
                value.update(group_name=group.name, country_code=group.country_code,
                             raw_format=mapping.raw_format, provider_code=mapping.provider_code)
            if resource == "theaters":
                value["generic_initial"] = generic_initial(row.name)
                value["provider_ids"] = provider_ids[row.id]
            output.append(value)
        return {"items": output, **metadata}

    @app.post("/api/{resource}")
    def create_reference(resource: str, payload: dict, db=Depends(db_session), current=Depends(user)):
        require_admin(current)
        if resource not in resources:
            raise HTTPException(404, "Page not found.")
        model = resources[resource]
        values = validate(resource, payload, db)
        row = model(**values)
        db.add(row)
        db.commit()
        app.state.jobs.invalidate(values.get("country_code") or None)
        return row_dict(row)

    @app.patch("/api/{resource}/{identifier}")
    def update_reference(resource: str, identifier: str, payload: dict, db=Depends(db_session), current=Depends(user)):
        require_admin(current)
        if resource not in resources:
            raise HTTPException(404, "Page not found.")
        try:
            key = identifier if resource == "countries" else int(identifier)
        except ValueError:
            raise HTTPException(404, "Record not found.")
        row = db.get(resources[resource], key)
        if not row:
            raise HTTPException(404, "Record not found.")
        previous_country = getattr(row, "country_code", None)
        values = validate(resource, payload, db, row)
        for name, value in values.items():
            setattr(row, name, value)
        db.commit()
        next_country = getattr(row, "country_code", None)
        app.state.jobs.invalidate(previous_country or None)
        if next_country != previous_country:
            app.state.jobs.invalidate(next_country or None)
        return row_dict(row)

    @app.delete("/api/format-mappings/{identifier}")
    def remove_mapping(identifier: int, db=Depends(db_session), current=Depends(user)):
        require_admin(current)
        row = db.get(models.FormatMapping, identifier)
        if not row:
            raise HTTPException(404, "Mapping not found.")
        country_code = row.country_code
        for override in db.scalars(select(models.MovieFormatOverride).where(models.MovieFormatOverride.format_mapping_id == identifier)):
            db.delete(override)
        db.delete(row)
        db.commit()
        app.state.jobs.invalidate(country_code or None)
        return {"ok": True}

    return app


app = create_app()
