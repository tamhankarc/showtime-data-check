"""Small additive, idempotent startup upgrades for existing reference databases."""
from sqlalchemy import inspect, text

from .models import Base


SCHEMA_LOCK = 732964103


def _initialize(connection):
    Base.metadata.create_all(bind=connection)
    for table, column, sql_type in (("provider_movies", "url_key", "VARCHAR(500)"),
                                    ("connections", "catalog_window_days", "INTEGER")):
        columns = {item["name"] for item in inspect(connection).get_columns(table)}
        if column not in columns:
            # Fixed identifiers, never user-supplied SQL; existing data is untouched.
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))


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
