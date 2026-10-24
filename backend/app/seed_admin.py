"""One-time, allowlisted Admin reference copy; never reads provider credentials or showtimes.

Export and import are deliberately separate commands. Export has no target-DB dependency;
import never connects to Admin and refuses to overwrite existing reference data.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any
from urllib.parse import unquote, urlsplit


class SeedError(ValueError):
    """A safe, operator-facing seed failure, containing no connection credentials."""


SCOPE_SQL = "(p.code = 'IS' OR (c.code = 'BR' AND p.code IN ('INGRESSO','CINEMARK','VELOX')))"
CONNECTION_JOIN = """JOIN title_admin.country_provider cp ON cp.country_id = c.id
    JOIN title_admin.data_provider p ON p.id = cp.provider_id"""

# Explicit columns are intentional. Do not replace these with SELECT * or database dumps.
EXPORT_QUERIES = {
    "countries": f"""SELECT c.code, c.name, coalesce(c.timezone,'UTC') AS timezone,
        c.is_active AS enabled FROM title_admin.country c
        WHERE EXISTS (SELECT 1 FROM title_admin.country_provider cp
          JOIN title_admin.data_provider p ON p.id=cp.provider_id
          WHERE cp.country_id=c.id AND {SCOPE_SQL}) ORDER BY c.code""",
    "connections": f"""SELECT cp.id,c.code AS country_code,p.code AS provider_code,
        cp.provider_country_code,coalesce(c.timezone,'UTC') AS timezone,
        (cp.is_active AND p.is_active) AS enabled
        FROM title_admin.country c {CONNECTION_JOIN} WHERE {SCOPE_SQL} ORDER BY cp.id""",
    "provider_movies": f"""SELECT pt.id,cp.id AS connection_id,
        pt.provider_title_id AS external_id,pt.provider_name AS name,
        NULL::text AS original_name,pt.provider_release_date AS release_date,
        NULL::text AS parent_external_id,pt.title_id AS canonical_title_id,
        title.name AS canonical_title_name,(pt.availability_status='AVAILABLE') AS active
        FROM title_admin.provider_title pt JOIN title_admin.country c ON c.id=pt.country_id
        {CONNECTION_JOIN} LEFT JOIN title_admin.title title ON title.id=pt.title_id
        WHERE pt.provider_id=p.id AND {SCOPE_SQL} ORDER BY pt.id""",
    "theaters": f"""SELECT t.id,c.code AS country_code,coalesce(t.name,'') AS name,
        nullif(concat_ws(', ',nullif(t.address_line1,''),nullif(t.address_line2,'')),'') AS address,
        t.city,t.region,t.postal_code,t.latitude,t.longitude,t.website_url AS website,
        chain.name AS chain,coalesce(t.timezone,c.timezone,'UTC') AS timezone,t.is_active AS active
        FROM title_admin.theater t JOIN title_admin.country c ON c.id=t.country_id
        LEFT JOIN title_admin.theater_chain chain ON chain.id=t.chain_id
        WHERE EXISTS (SELECT 1 FROM title_admin.country_provider cp
          JOIN title_admin.data_provider p ON p.id=cp.provider_id
          WHERE cp.country_id=c.id AND {SCOPE_SQL}) ORDER BY t.id""",
    "provider_theaters": f"""SELECT pt.id,cp.id AS connection_id,
        pt.provider_theater_id AS external_id,coalesce(pt.provider_name,t.name,'') AS name,
        pt.provider_address AS address,pt.provider_city AS city,pt.provider_region AS region,
        pt.provider_postal_code AS postal_code,pt.provider_latitude AS latitude,
        pt.provider_longitude AS longitude,pt.provider_website AS website,
        CASE WHEN pt.match_status='CONFIRMED' THEN chain.name ELSE NULL END AS chain,
        CASE WHEN pt.match_status='CONFIRMED' THEN pt.theater_id ELSE NULL END AS theater_id,
        CASE WHEN pt.match_status='CONFIRMED' THEN 'confirmed'
          ELSE 'needs_review' END AS review_status
        FROM title_admin.provider_theater pt JOIN title_admin.country c ON c.id=pt.country_id
        {CONNECTION_JOIN} JOIN title_admin.theater t ON t.id=pt.theater_id
        LEFT JOIN title_admin.theater_chain chain ON chain.id=t.chain_id
        WHERE pt.provider_id=p.id AND {SCOPE_SQL} ORDER BY pt.id""",
    "format_mappings": f"""SELECT fm.id,c.code AS country_code,p.code AS provider_code,
        fm.raw_value AS raw_format,pf.label AS published_format,audio.code AS audio_language,
        subtitle.code AS subtitle_language,fm.is_dubbed,fm.is_subtitled,fm.is_luxury
        FROM title_admin.format_mapping fm JOIN title_admin.country c ON c.id=fm.country_id
        {CONNECTION_JOIN} JOIN title_admin.presentation_format pf ON pf.id=fm.presentation_format_id
        LEFT JOIN title_admin.language audio ON audio.id=fm.audio_language_id
        LEFT JOIN title_admin.language subtitle ON subtitle.id=fm.subtitle_language_id
        WHERE fm.provider_id=p.id AND {SCOPE_SQL} ORDER BY fm.id""",
    "movie_format_overrides": f"""SELECT tfo.id,c.code AS country_code,
        tfo.format_mapping_id,tfo.title_id AS canonical_title_id,
        pf.label AS published_format,tfo.is_active AS active
        FROM title_admin.title_format_override tfo
        JOIN title_admin.format_mapping fm ON fm.id=tfo.format_mapping_id
        JOIN title_admin.country c ON c.id=fm.country_id {CONNECTION_JOIN}
        JOIN title_admin.presentation_format pf ON pf.id=tfo.presentation_format_id
        WHERE fm.provider_id=p.id AND {SCOPE_SQL} ORDER BY tfo.id""",
}

FIELDS = {
    "countries": "code name timezone enabled".split(),
    "connections": "id country_code provider_code provider_country_code timezone enabled".split(),
    "movie_groups": "id country_code name active".split(),
    "provider_movies": "id connection_id external_id name original_name release_date parent_external_id group_id active manually_grouped".split(),
    "theaters": "id country_code name address city region postal_code latitude longitude website chain timezone active".split(),
    "provider_theaters": "id connection_id external_id name address city region postal_code latitude longitude website chain theater_id review_status".split(),
    "format_mappings": "id country_code provider_code raw_format published_format audio_language subtitle_language is_dubbed is_subtitled is_luxury".split(),
    "movie_format_overrides": "id group_id format_mapping_id published_format active".split(),
}


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError("Unsupported reference value")


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=_json_default, allow_nan=False).encode("utf-8")


def _hash(value) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _manifest(data) -> dict:
    return {
        "counts": {key: len(data[key]) for key in FIELDS},
        "section_sha256": {key: _hash(data[key]) for key in FIELDS},
        "data_sha256": _hash(data),
    }


def _database_hash(url: str) -> str:
    parsed = urlsplit(url.replace("postgresql+psycopg:", "postgresql:"))
    host = parsed.hostname or ""
    host = "127.0.0.1" if host == "localhost" else host
    return _hash([host, parsed.port or 5432, unquote(parsed.path.lstrip("/"))])


def build_bundle(source_rows: dict, source_database_sha256: str | None = None) -> dict:
    """Normalize only allowlisted fields; group by confirmed IDs, never similar names."""
    data = {key: [] for key in FIELDS}
    for key in EXPORT_QUERIES:
        if key not in {"provider_movies", "movie_format_overrides"}:
            data[key] = [{field: row.get(field) for field in FIELDS[key]} for row in source_rows[key]]
    # Admin's pending matches may have been guessed from name/city. Only confirmed assignments are authoritative.
    for theater in data["provider_theaters"]:
        if theater["review_status"] != "confirmed":
            theater["theater_id"] = None
            theater["chain"] = None
    connections = {row["id"]: row for row in data["connections"]}
    groups = {}
    for source in sorted(source_rows["provider_movies"], key=lambda row: row["id"]):
        connection = connections[source["connection_id"]]
        country = connection["country_code"]
        canonical_id = source.get("canonical_title_id")
        key = (country, "title", canonical_id) if canonical_id is not None else (
            country, "provider", source["connection_id"], source["external_id"])
        if key not in groups:
            group = {"id": len(groups) + 1, "country_code": country,
                     "name": source.get("canonical_title_name") or source["name"], "active": False}
            groups[key] = group
            data["movie_groups"].append(group)
        group = groups[key]
        group["active"] = group["active"] or bool(source["active"])
        movie = {field: source.get(field) for field in FIELDS["provider_movies"]}
        movie.update(group_id=group["id"], manually_grouped=canonical_id is not None)
        data["provider_movies"].append(movie)
    mappings = {row["id"]: row for row in data["format_mappings"]}
    for source in source_rows["movie_format_overrides"]:
        mapping = mappings.get(source["format_mapping_id"])
        group = groups.get((source["country_code"], "title", source["canonical_title_id"]))
        if group is None or mapping is None or mapping["country_code"] != source["country_code"]:
            continue
        override = {field: source.get(field) for field in FIELDS["movie_format_overrides"]}
        override["group_id"] = group["id"]
        data["movie_format_overrides"].append(override)
    # Dates/decimals from psycopg and JSON from Docker must produce the same bundle shape.
    data = json.loads(_canonical(data))
    bundle = {"kind": "showtime-data-check-admin-reference", "version": 1,
              "created_at": datetime.now(timezone.utc).isoformat(), "data": data,
              "manifest": _manifest(data)}
    if source_database_sha256:
        bundle["source_database_sha256"] = source_database_sha256
    validate_bundle(bundle)
    return bundle


def validate_bundle(bundle: dict) -> dict:
    """Reject unexpected fields, unsupported providers, broken links, and hash changes."""
    if not isinstance(bundle, dict) or bundle.get("kind") != "showtime-data-check-admin-reference" or bundle.get("version") != 1:
        raise SeedError("Unsupported reference bundle")
    allowed = {"kind", "version", "created_at", "data", "manifest", "source_database_sha256"}
    if set(bundle) - allowed:
        raise SeedError("Unexpected bundle fields")
    data = bundle.get("data")
    if not isinstance(data, dict) or set(data) != set(FIELDS):
        raise SeedError("Reference sections do not match the allowlist")
    for key, fields in FIELDS.items():
        rows = data[key]
        if not isinstance(rows, list) or any(not isinstance(row, dict) or set(row) != set(fields) for row in rows):
            raise SeedError(f"Unexpected fields in {key}")
        identifier = "code" if key == "countries" else "id"
        identifiers = [row[identifier] for row in rows]
        if len(set(identifiers)) != len(identifiers):
            raise SeedError(f"Duplicate identity in {key}")
        if key != "countries" and any(type(item) is not int or item <= 0 for item in identifiers):
            raise SeedError(f"Invalid identity in {key}")
    if not data["countries"] or not data["connections"]:
        raise SeedError("An empty country/provider reference seed is not permitted")
    if bundle.get("manifest") != _manifest(data):
        raise SeedError("Reference bundle hash/count manifest does not match")
    countries = {row["code"] for row in data["countries"]}
    connections = {row["id"]: row for row in data["connections"]}
    groups = {row["id"]: row for row in data["movie_groups"]}
    theaters = {row["id"]: row for row in data["theaters"]}
    mappings = {row["id"]: row for row in data["format_mappings"]}
    for row in data["connections"] + data["format_mappings"]:
        country, provider = row["country_code"], row["provider_code"]
        if country not in countries or not (provider == "IS" or country == "BR" and provider in {"INGRESSO", "CINEMARK", "VELOX"}):
            raise SeedError("Country/provider outside the approved seed scope")
    referenced_countries = {row["country_code"] for row in connections.values()}
    if countries != referenced_countries:
        raise SeedError("Countries must have an approved provider connection")
    for row in data["movie_groups"] + data["theaters"]:
        if row["country_code"] not in countries:
            raise SeedError("Reference country does not exist")
    for row in data["provider_movies"] + data["provider_theaters"]:
        connection = connections.get(row["connection_id"])
        if connection is None or not isinstance(row["external_id"], str) or not row["external_id"].strip():
            raise SeedError("Provider reference identity is invalid")
        if "group_id" in row:
            group = groups.get(row["group_id"])
            if group is None or group["country_code"] != connection["country_code"]:
                raise SeedError("Movie group crosses a country or does not exist")
            if row["release_date"] is not None:
                try:
                    date.fromisoformat(row["release_date"])
                except (TypeError, ValueError) as exc:
                    raise SeedError("Invalid movie release date") from exc
        else:
            theater = theaters.get(row["theater_id"])
            if row["review_status"] not in {"confirmed", "needs_review"}:
                raise SeedError("Invalid theater review status")
            if row["theater_id"] is None and row["review_status"] == "needs_review":
                continue
            if theater is None or theater["country_code"] != connection["country_code"]:
                raise SeedError("Theater assignment crosses a country or does not exist")
    for row in data["movie_format_overrides"]:
        group, mapping = groups.get(row["group_id"]), mappings.get(row["format_mapping_id"])
        if group is None or mapping is None or group["country_code"] != mapping["country_code"]:
            raise SeedError("Movie format override references an absent or different-country group/mapping")
    return data


def read_admin(dsn: str) -> dict:
    if not dsn or not dsn.startswith(("postgres://", "postgresql://")):
        raise SeedError("Set ADMIN_DATABASE_URL to the source PostgreSQL DSN")
    import psycopg
    from psycopg.rows import dict_row
    try:
        with psycopg.connect(dsn, autocommit=True, row_factory=dict_row,
                             options="-c default_transaction_read_only=on", connect_timeout=10) as source:
            source.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            try:
                source.execute("SET LOCAL statement_timeout = '120s'")
                return {key: list(source.execute(sql).fetchall()) for key, sql in EXPORT_QUERIES.items()}
            finally:
                source.execute("ROLLBACK")
    except Exception as exc:
        raise SeedError(f"Admin read-only export failed ({type(exc).__name__}); connection details suppressed") from None


def read_admin_docker(compose_file: Path, *, user="title_local", database="title_ticketing") -> dict:
    if not compose_file.is_file():
        raise SeedError("Docker Compose file does not exist")
    members = [f"'{key}',(SELECT coalesce(json_agg(row_to_json(ref)), '[]'::json) FROM ({sql}) ref)"
               for key, sql in EXPORT_QUERIES.items()]
    sql = "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;\nSET LOCAL statement_timeout = '120s';\n"
    sql += "SELECT json_build_object(" + ",".join(members) + ");\nROLLBACK;\n"
    command = ["docker", "compose", "-f", str(compose_file.resolve()), "exec", "-T", "postgres",
               "psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-A", "-t", "-U", user, "-d", database]
    try:
        result = subprocess.run(command, input=sql, text=True, encoding="utf-8", capture_output=True, timeout=180, check=False)
        if result.returncode:
            raise SeedError("Admin Docker read-only export failed; source output suppressed")
        rows = json.loads(result.stdout)
        if set(rows) != set(EXPORT_QUERIES):
            raise SeedError("Unexpected Admin Docker export sections")
        return rows
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise SeedError(f"Admin Docker read-only export failed ({type(exc).__name__}); source output suppressed") from None


def validate_export_path(path: Path) -> Path:
    path = path.expanduser().resolve()
    if path.exists() or not path.parent.is_dir():
        raise SeedError("Choose a new export file in an existing private directory")
    project = Path(__file__).resolve().parents[2]
    if path.is_relative_to(project) and path.relative_to(project).parts[0] != "private":
        raise SeedError("Exports inside the app must be under its ignored private/ directory")
    git_root = next((parent for parent in path.parents if (parent / ".git").exists()), None)
    if git_root is not None:
        relative = path.relative_to(git_root)
        app_private = path.is_relative_to(project / "private")
        if relative.parts[0] != "private" and not app_private:
            raise SeedError("Exports inside a Git repository must be under ignored private/")
        ignored = subprocess.run(["git", "-C", str(git_root), "check-ignore", "--quiet", "--no-index", "--", str(relative)], capture_output=True)
        tracked = subprocess.run(["git", "-C", str(git_root), "ls-files", "--error-unmatch", "--", str(relative)], capture_output=True)
        if ignored.returncode != 0 or tracked.returncode == 0:
            raise SeedError("Export destination must be ignored and untracked")
    return path


def write_bundle(bundle: dict, output: Path) -> None:
    validate_bundle(bundle)
    output = validate_export_path(output)
    # O_EXCL prevents overwrite; mode 0600 applies where supported. Use a private ACL directory on Windows.
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(bundle, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def import_bundle(bundle: dict, session, *, confirm_one_time: bool = False) -> dict:
    if not confirm_one_time:
        raise SeedError("Import requires --confirm-one-time")
    data = validate_bundle(bundle)
    from sqlalchemy import func, select, text
    from .models import AppSetting, Connection, Country, FormatMapping, MovieFormatOverride, MovieGroup, ProviderMovie, ProviderTheater, Theater
    models = dict(zip(FIELDS, [Country, Connection, MovieGroup, ProviderMovie, Theater, ProviderTheater, FormatMapping, MovieFormatOverride]))
    with session.begin():
        bind = session.get_bind()
        if bind.dialect.name == "postgresql":
            session.execute(text("SELECT pg_advisory_xact_lock(732964102)"))
            if bundle.get("source_database_sha256") == _database_hash(bind.url.render_as_string(hide_password=False)):
                raise SeedError("Source Admin database cannot be the import target")
        if session.get(AppSetting, "admin_reference_seed") is not None:
            raise SeedError("This database has already received its one-time Admin seed")
        if any(session.scalar(select(func.count()).select_from(model)) for model in models.values()):
            raise SeedError("Target reference tables must be empty; existing decisions will not be overwritten")
        for key, model in models.items():
            columns = set(model.__table__.columns.keys())
            if any(set(row) - columns for row in data[key]):
                raise SeedError(f"Target {key} model lacks required reference fields")
            for source_row in data[key]:
                row = dict(source_row)
                if key == "provider_movies" and row["release_date"]:
                    row["release_date"] = date.fromisoformat(row["release_date"])
                if key == "connections":
                    row["movie_count"] = sum(movie["connection_id"] == row["id"] and movie["active"] for movie in data["provider_movies"])
                    row["last_status"] = "seeded_not_refreshed"
                session.add(model(**row))
            session.flush()
        # Explicit source IDs preserve theater references. Advance PostgreSQL sequences afterward.
        if bind.dialect.name == "postgresql":
            for model in models.values():
                if "id" not in model.__table__.columns:
                    continue
                table = model.__table__
                sequence = session.scalar(text("SELECT pg_get_serial_sequence(:table_name, 'id')"), {"table_name": table.fullname})
                if sequence:
                    next_id = session.scalar(select(func.coalesce(func.max(table.c.id), 0))) + 1
                    session.execute(text("SELECT setval(CAST(:sequence AS regclass), :next_id, false)"), {"sequence": sequence, "next_id": next_id})
        session.add(AppSetting(key="admin_reference_seed", value=json.dumps({
            "imported_at": datetime.now(timezone.utc).isoformat(), **bundle["manifest"]}, sort_keys=True)))
    return bundle["manifest"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    exporter = commands.add_parser("export", help="Read allowed Admin references into a private JSON bundle")
    exporter.add_argument("--out", type=Path, required=True)
    exporter.add_argument("--docker-compose", type=Path, help="Use local postgres service without publishing its port")
    exporter.add_argument("--docker-user", default="title_local")
    exporter.add_argument("--docker-database", default="title_ticketing")
    importer = commands.add_parser("import", help="Seed the new app once from a reviewed reference bundle")
    importer.add_argument("--file", type=Path, required=True)
    importer.add_argument("--confirm-one-time", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            validate_export_path(args.out)  # Refuse unsafe output before connecting to Admin.
            if args.docker_compose:
                bundle = build_bundle(read_admin_docker(args.docker_compose, user=args.docker_user, database=args.docker_database))
            else:
                dsn = os.environ.get("ADMIN_DATABASE_URL", "")
                bundle = build_bundle(read_admin(dsn), _database_hash(dsn))
            write_bundle(bundle, args.out)
            manifest = bundle["manifest"]
        else:
            if not args.confirm_one_time:
                raise SeedError("Import requires --confirm-one-time")
            bundle = json.loads(args.file.read_text(encoding="utf-8"))
            validate_bundle(bundle)  # Do not even import target DB configuration for an invalid file.
            from .db import SessionLocal
            with SessionLocal() as session:
                manifest = import_bundle(bundle, session, confirm_one_time=True)
        print(json.dumps(manifest, sort_keys=True))
        return 0
    except SeedError as exc:
        print(str(exc), file=sys.stderr)
    except Exception as exc:
        print(f"Reference {args.command} failed ({type(exc).__name__}); details suppressed to protect private data", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
