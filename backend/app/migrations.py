"""Small additive, idempotent startup upgrades for existing reference databases."""
from sqlalchemy import inspect, text

from .models import Base


SCHEMA_LOCK = 732964103


def _initialize(connection):
    Base.metadata.create_all(bind=connection)
    columns = {column["name"] for column in inspect(connection).get_columns("provider_movies")}
    if "url_key" not in columns:
        # Fixed table/column names, never user-supplied SQL. Existing identifiers,
        # assignments, rows and indexes are deliberately left untouched.
        connection.execute(text("ALTER TABLE provider_movies ADD COLUMN url_key VARCHAR(500)"))


def initialize_schema(engine):
    """Serialize inspect/add so overlapping starts cannot race a nullable upgrade."""
    if engine.dialect.name == "sqlite":
        with engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                _initialize(connection)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
    else:
        with engine.begin() as connection:
            if engine.dialect.name == "postgresql":
                connection.execute(text("SELECT pg_advisory_xact_lock(:lock)"), {"lock": SCHEMA_LOCK})
            _initialize(connection)
