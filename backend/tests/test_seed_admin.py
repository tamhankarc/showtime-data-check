from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import seed_admin as seed
from app.models import AppSetting, Base, Connection, Country, FormatMapping, MovieFormatOverride, MovieGroup, ProviderMovie, ProviderTheater, Theater


@pytest.fixture
def reference_rows():
    def row(section, **values):
        return {**dict.fromkeys(seed.FIELDS[section]), **values}

    countries = [row("countries", code=code, name=name, timezone=zone, enabled=True)
                 for code, name, zone in [("BR", "Brazil", "America/Sao_Paulo"), ("DE", "Germany", "Europe/Berlin")]]
    connections = [row("connections", id=id_, country_code=country, provider_code=provider,
                       provider_country_code=country, timezone="UTC", enabled=True)
                   for id_, country, provider in [(10, "BR", "INGRESSO"), (20, "BR", "CINEMARK"),
                                                   (30, "BR", "VELOX"), (40, "DE", "IS")]]
    movies = []
    for id_, connection, external, title, active in [(1, 10, "ing-1", 800, True), (2, 20, "cin-1", 800, True),
                                                     (3, 30, "vel-child", 800, True), (4, 30, "unlinked-1", None, False),
                                                     (5, 30, "unlinked-2", None, True), (6, 40, "de-1", 800, True)]:
        movie = row("provider_movies", id=id_, connection_id=connection, external_id=external,
                    name="Identical provider name", release_date=date(2026, 10, 24), active=active)
        movie.pop("group_id")
        movie.pop("manually_grouped")
        movie.update(canonical_title_id=title, canonical_title_name="Confirmed canonical movie" if title else None)
        movies.append(movie)
    return {
        "countries": countries, "connections": connections, "provider_movies": movies,
        "theaters": [row("theaters", id=701, country_code="BR", name="Manual canonical theater",
                         address="Preserved address", chain="Preserved chain", timezone="America/Sao_Paulo", active=True)],
        "provider_theaters": [row("provider_theaters", id=901, connection_id=20, external_id="cin-theater",
                                  name="Provider theater", theater_id=701, review_status="confirmed"),
                              row("provider_theaters", id=902, connection_id=30, external_id="new-theater",
                                  name="Unreviewed theater", theater_id=701, review_status="needs_review")],
        "format_mappings": [row("format_mappings", id=201, country_code="BR", provider_code="CINEMARK",
                                raw_format="IMAX LEG VIP", published_format="IMAX", audio_language="EN",
                                subtitle_language="PT", is_dubbed=False, is_subtitled=True, is_luxury=True)],
        "movie_format_overrides": [{"id": 301, "country_code": "BR", "canonical_title_id": 800,
                                    "format_mapping_id": 201, "published_format": "IMAX 3D", "active": True}],
    }


@pytest.fixture
def bundle(reference_rows):
    return seed.build_bundle(reference_rows)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_confirmed_ids_group_across_br_providers_without_name_merges(bundle):
    movies = bundle["data"]["provider_movies"]
    assert len(movies) == 6  # Includes unavailable and unlinked provider movies.
    assert len({movie["group_id"] for movie in movies[:3]}) == 1
    assert movies[3]["group_id"] != movies[4]["group_id"]
    assert movies[0]["group_id"] != movies[5]["group_id"]  # Same canonical ID, different country.
    assert all(movie["manually_grouped"] for movie in movies[:3])
    assert movies[3]["active"] is False
    assert movies[3]["manually_grouped"] is False
    groups = {row["id"]: row for row in bundle["data"]["movie_groups"]}
    assert groups[movies[0]["group_id"]]["name"] == "Confirmed canonical movie"
    assert groups[movies[3]["group_id"]]["name"] == "Identical provider name"
    assert movies[0]["release_date"] == "2026-10-24"


def test_source_extra_credentials_and_logos_never_reach_bundle(reference_rows):
    reference_rows["connections"][0]["credential_secret_arn"] = "DO-NOT-COPY-SECRET"
    reference_rows["theaters"][0]["logo_asset_id"] = "DO-NOT-COPY-LOGO"
    reference_rows["users"] = [{"password": "DO-NOT-COPY-PASSWORD"}]
    serialized = json.dumps(seed.build_bundle(reference_rows))
    assert "DO-NOT-COPY" not in serialized
    assert "credential" not in serialized and "logo_asset" not in serialized and '"users"' not in serialized


def test_title_overrides_only_seed_existing_base_mapping_and_same_country_confirmed_group(reference_rows):
    reference_rows["movie_format_overrides"].extend([
        {"id": 302, "country_code": "BR", "canonical_title_id": 999, "format_mapping_id": 201,
         "published_format": "Unlinked movie", "active": True},
        {"id": 303, "country_code": "BR", "canonical_title_id": 800, "format_mapping_id": 999,
         "published_format": "Unseeded base mapping", "active": True},
        {"id": 304, "country_code": "DE", "canonical_title_id": 800, "format_mapping_id": 201,
         "published_format": "Wrong country", "active": True},
    ])
    data = seed.build_bundle(reference_rows)["data"]
    assert len(data["movie_format_overrides"]) == 1
    override = data["movie_format_overrides"][0]
    assert override["published_format"] == "IMAX 3D"
    assert override["group_id"] == data["provider_movies"][0]["group_id"]


def test_hash_manifest_detects_tampering_and_unknown_fields(bundle):
    changed = deepcopy(bundle)
    changed["data"]["theaters"][0]["name"] = "Changed after export"
    with pytest.raises(seed.SeedError, match="manifest"):
        seed.validate_bundle(changed)
    changed = deepcopy(bundle)
    changed["data"]["connections"][0]["password"] = "never accepted"
    with pytest.raises(seed.SeedError, match="Unexpected fields"):
        seed.validate_bundle(changed)


def test_bo_and_cross_country_assignment_are_rejected(bundle):
    changed = deepcopy(bundle)
    changed["data"]["connections"][0]["provider_code"] = "BO"
    changed["manifest"] = seed._manifest(changed["data"])
    with pytest.raises(seed.SeedError, match="approved seed scope"):
        seed.validate_bundle(changed)
    changed = deepcopy(bundle)
    changed["data"]["theaters"][0]["country_code"] = "DE"
    changed["manifest"] = seed._manifest(changed["data"])
    with pytest.raises(seed.SeedError, match="assignment crosses"):
        seed.validate_bundle(changed)


def test_import_preserves_reference_decisions_and_is_one_time(bundle, session):
    manifest = seed.import_bundle(bundle, session, confirm_one_time=True)
    assert manifest["counts"]["provider_movies"] == 6
    assert session.get(Theater, 701).address == "Preserved address"
    assert session.get(Theater, 701).chain == "Preserved chain"
    assert session.get(ProviderTheater, 901).theater_id == 701
    assert session.get(ProviderTheater, 901).review_status == "confirmed"
    assert session.get(ProviderTheater, 902).review_status == "needs_review"
    assert session.get(ProviderTheater, 902).theater_id is None
    mapping = session.get(FormatMapping, 201)
    assert (mapping.audio_language, mapping.subtitle_language) == ("EN", "PT")
    assert mapping.is_luxury and mapping.is_subtitled and not mapping.is_dubbed
    override = session.get(MovieFormatOverride, 301)
    assert override.published_format == "IMAX 3D" and override.active
    assert override.group_id == session.get(ProviderMovie, 1).group_id
    assert override.format_mapping_id == mapping.id
    assert session.get(Connection, 30).movie_count == 2
    assert session.get(Connection, 30).last_status == "seeded_not_refreshed"
    assert session.get(Connection, 30).last_success_at is None
    assert session.get(ProviderMovie, 1).release_date == date(2026, 10, 24)
    assert session.get(ProviderMovie, 1).manually_grouped
    assert session.get(AppSetting, "admin_reference_seed") is not None
    session.rollback()  # End read-only inspection transaction before invoking importer again.
    with pytest.raises(seed.SeedError, match="already received"):
        seed.import_bundle(bundle, session, confirm_one_time=True)


def test_confirmation_and_empty_target_are_required(bundle, session):
    with pytest.raises(seed.SeedError, match="confirm-one-time"):
        seed.import_bundle(bundle, session)
    session.add(Country(code="XX", name="Existing decision", timezone="UTC"))
    session.commit()
    with pytest.raises(seed.SeedError, match="must be empty"):
        seed.import_bundle(bundle, session, confirm_one_time=True)
    assert session.get(Country, "XX").name == "Existing decision"
    assert session.get(Country, "BR") is None


def test_import_failure_rolls_back_every_section_and_marker(bundle, session):
    changed = deepcopy(bundle)
    duplicate = deepcopy(changed["data"]["provider_movies"][0])
    duplicate["id"] = 100
    changed["data"]["provider_movies"].append(duplicate)
    changed["manifest"] = seed._manifest(changed["data"])
    with pytest.raises(IntegrityError):
        seed.import_bundle(changed, session, confirm_one_time=True)
    assert session.scalar(select(func.count()).select_from(Country)) == 0
    assert session.scalar(select(func.count()).select_from(MovieGroup)) == 0
    assert session.get(AppSetting, "admin_reference_seed") is None


def test_psycopg_export_is_explicitly_read_only_and_rolls_back(monkeypatch, reference_rows):
    calls = []

    class Source:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, sql):
            calls.append(sql)
            key = next((key for key, query in seed.EXPORT_QUERIES.items() if query == sql), None)
            return SimpleNamespace(fetchall=lambda: reference_rows[key])

    options = {}

    def connect(dsn, **kwargs):
        options.update(kwargs)
        return Source()

    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=connect))
    monkeypatch.setitem(sys.modules, "psycopg.rows", SimpleNamespace(dict_row=object()))
    assert seed.read_admin("postgresql://source-user:secret@localhost/admin") == reference_rows
    assert "default_transaction_read_only=on" in options["options"]
    assert "REPEATABLE READ READ ONLY" in calls[0]
    assert calls[-1] == "ROLLBACK"
    assert all(query.lstrip().upper().startswith("SELECT") for query in seed.EXPORT_QUERIES.values())
    forbidden = ["credential_secret", "app_user", "showtime ", "logo_asset", "client", "analytics"]
    assert not any(term in " ".join(seed.EXPORT_QUERIES.values()).lower() for term in forbidden)


def test_source_connection_errors_never_expose_dsn(monkeypatch):
    def connect(*args, **kwargs):
        raise RuntimeError("postgresql://source-user:SUPERSECRET@host/database")

    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=connect))
    monkeypatch.setitem(sys.modules, "psycopg.rows", SimpleNamespace(dict_row=object()))
    with pytest.raises(seed.SeedError) as failure:
        seed.read_admin("postgresql://source-user:SUPERSECRET@host/database")
    assert "SUPERSECRET" not in str(failure.value)


def test_docker_fallback_uses_read_only_psql_without_port_publication(tmp_path, monkeypatch, reference_rows):
    compose = tmp_path / "compose.yaml"
    compose.touch()
    captured = {}

    def run(command, **kwargs):
        captured.update(command=command, **kwargs)
        return SimpleNamespace(returncode=0, stdout=json.dumps(reference_rows, default=seed._json_default))

    monkeypatch.setattr(subprocess, "run", run)
    rows = seed.read_admin_docker(compose)
    assert rows["countries"] == reference_rows["countries"]
    assert captured["command"][:2] == ["docker", "compose"]
    assert "exec" in captured["command"] and "psql" in captured["command"]
    assert "-X" in captured["command"] and "ON_ERROR_STOP=1" in captured["command"]
    assert captured["encoding"] == "utf-8"  # Provider/country names must not use Windows' legacy codec.
    assert "REPEATABLE READ READ ONLY" in captured["input"]
    assert captured["input"].endswith("ROLLBACK;\n")
    assert "--publish" not in captured["command"]


def test_private_export_does_not_overwrite_and_has_only_reference_manifest(bundle, tmp_path):
    output = tmp_path / "admin-reference.json"
    seed.write_bundle(bundle, output)
    assert seed.validate_bundle(json.loads(output.read_text(encoding="utf-8"))) == bundle["data"]
    with pytest.raises(seed.SeedError, match="new export file"):
        seed.write_bundle(bundle, output)


def test_git_export_requires_private_ignored_untracked_location(tmp_path, monkeypatch):
    repository = tmp_path / "repository"
    (repository / ".git").mkdir(parents=True)
    private = repository / "private"
    private.mkdir()
    with pytest.raises(seed.SeedError, match="ignored private"):
        seed.validate_export_path(repository / "public-reference.json")

    def run(command, **kwargs):
        return SimpleNamespace(returncode=0 if "check-ignore" in command else 1)

    monkeypatch.setattr(subprocess, "run", run)
    assert seed.validate_export_path(private / "reference.json") == (private / "reference.json").resolve()
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    with pytest.raises(seed.SeedError, match="ignored and untracked"):
        seed.validate_export_path(private / "tracked.json")


def test_cli_refuses_unconfirmed_import_before_target_configuration(capsys):
    assert seed.main(["import", "--file", "does-not-exist.json"]) == 1
    assert "--confirm-one-time" in capsys.readouterr().err
