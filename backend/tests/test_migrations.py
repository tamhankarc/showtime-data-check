"""Additive upgrades use legacy SQLite fixtures and a mocked PostgreSQL lock."""
from contextlib import contextmanager
from types import SimpleNamespace
import re

from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.schema import CreateTable

from app.config import Settings
from app.db import make_engine
from app.main import create_app
from app.migrations import SCHEMA_LOCK, initialize_schema
from app.models import Base, Connection, ProviderMovie


def legacy_engine(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "legacy.sqlite").as_posix())
    legacy = str(CreateTable(ProviderMovie.__table__).compile(engine))
    legacy = re.sub(r"\n\s*url_key VARCHAR\(500\),", "", legacy)
    assert "url_key" not in legacy
    legacy_connection = str(CreateTable(Connection.__table__).compile(engine))
    legacy_connection = re.sub(r"\n\s*catalog_window_days INTEGER,", "", legacy_connection)
    assert "catalog_window_days" not in legacy_connection
    with engine.begin() as connection:
        connection.exec_driver_sql(legacy)
        connection.exec_driver_sql(legacy_connection)
        Base.metadata.create_all(connection)
        connection.execute(text("INSERT INTO countries (code,name,timezone,enabled) VALUES ('BR','Brazil','America/Sao_Paulo',true)"))
        connection.execute(text("INSERT INTO connections (id,country_code,provider_code,provider_country_code,timezone,enabled,last_status,movie_count) VALUES (1,'BR','INGRESSO','BR','America/Sao_Paulo',true,'seeded',1)"))
        connection.execute(text("INSERT INTO movie_groups (id,country_code,name,active) VALUES (7,'BR','Existing movie',true)"))
        connection.execute(text("INSERT INTO provider_movies (id,connection_id,external_id,name,group_id,active,manually_grouped) VALUES (42,1,'12345','Existing movie',7,true,true)"))
    return engine


def test_legacy_upgrade_is_additive_idempotent_and_preserves_identifiers(tmp_path):
    engine = legacy_engine(tmp_path)
    assert "url_key" not in {column["name"] for column in inspect(engine).get_columns("provider_movies")}
    assert "catalog_window_days" not in {column["name"] for column in inspect(engine).get_columns("connections")}
    initialize_schema(engine)
    initialize_schema(engine)
    columns = inspect(engine).get_columns("provider_movies")
    assert sum(column["name"] == "url_key" for column in columns) == 1
    assert next(column for column in columns if column["name"] == "url_key")["nullable"] is True
    assert sum(column["name"] == "catalog_window_days" for column in inspect(engine).get_columns("connections")) == 1
    with engine.begin() as connection:
        assert connection.execute(text("SELECT id,external_id,group_id,manually_grouped,url_key FROM provider_movies")).one() == (42, "12345", 7, True, None)
        assert connection.execute(text("SELECT catalog_window_days FROM connections WHERE id=1")).scalar() is None
        connection.execute(text("UPDATE provider_movies SET url_key='existing-movie' WHERE id=42"))
    initialize_schema(engine)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT url_key FROM provider_movies WHERE id=42")).scalar() == "existing-movie"
    engine.dispose()


def test_app_startup_upgrades_legacy_database_before_any_movie_orm_queries(tmp_path):
    engine = legacy_engine(tmp_path)
    settings = Settings(environment="development", proxy_secret="test-secret", app_origin="http://localhost:3001")
    app = create_app(settings=settings, provider_service=object(), engine=engine)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        with app.state.sessions() as db:
            movie = db.get(ProviderMovie, 42)
            assert movie.external_id == "12345" and movie.group_id == 7 and movie.url_key is None
    engine.dispose()


def test_postgres_transaction_lock_precedes_schema_inspection(monkeypatch):
    from app import migrations
    operations = []
    connection = SimpleNamespace(execute=lambda statement, parameters: operations.append((str(statement), parameters)))
    class MockEngine:
        dialect = SimpleNamespace(name="postgresql")
        @contextmanager
        def begin(self):
            operations.append("begin")
            yield connection
            operations.append("commit")
    monkeypatch.setattr(migrations, "_initialize", lambda supplied: operations.append("initialize") if supplied is connection else None)
    initialize_schema(MockEngine())
    assert operations == ["begin", ("SELECT pg_advisory_xact_lock(:lock)", {"lock": SCHEMA_LOCK}), "initialize", "commit"]
