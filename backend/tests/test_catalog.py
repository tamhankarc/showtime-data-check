"""Catalog scalability and assignment-preservation tests, with SQLite only."""
from sqlalchemy import event, func, select
from sqlalchemy.orm import sessionmaker
import pytest

from app.catalog import upsert_movies
from app.db import make_engine
from app.models import Base, Connection, Country, MovieGroup, ProviderMovie


@pytest.fixture
def db(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "catalog.sqlite").as_posix())
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add(Country(code="BR", name="Brazil", timezone="America/Sao_Paulo"))
        session.flush()
        session.add_all([Connection(id=1, country_code="BR", provider_code="VELOX", timezone="America/Sao_Paulo"),
                         Connection(id=2, country_code="BR", provider_code="INGRESSO", timezone="America/Sao_Paulo")])
        session.commit()
        yield session
        session.rollback()
    engine.dispose()


def add_movie(db, identifier, external_id, group_id, *, connection_id=1, active=True, manual=False):
    db.add(ProviderMovie(id=identifier, connection_id=connection_id, external_id=external_id,
                        name="Movie " + external_id, group_id=group_id, active=active, manually_grouped=manual))


def group_state(db):
    return dict(db.execute(select(MovieGroup.id, MovieGroup.active)).all())


def test_existing_catalog_refresh_has_constant_selects_and_chunked_group_update(db):
    count = 500
    db.add_all([MovieGroup(id=index, country_code="BR", name=f"Movie {index}") for index in range(1, count + 1)])
    db.flush()
    for index in range(1, count + 1):
        add_movie(db, index, str(index), index)
    db.commit()
    connection = db.get(Connection, 1)
    statements = []
    def record(_, __, statement, ___, ____, _____):
        statements.append(statement.strip().lower())
    event.listen(db.bind, "before_cursor_execute", record)
    try:
        observed = upsert_movies(db, connection, [{"id": str(index), "name": f"Updated movie {index}"} for index in range(1, count + 1)])
    finally:
        event.remove(db.bind, "before_cursor_execute", record)
    assert observed == count and connection.movie_count == count
    assert sum(statement.startswith("select") for statement in statements) <= 2
    assert sum(statement.startswith("update movie_groups") for statement in statements) == 1
    assert all(group_state(db).values())


def test_new_catalog_uses_batch_flushes_not_one_explicit_flush_per_movie(db):
    connection = db.get(Connection, 1)
    flushes = []
    def before_flush(*_):
        flushes.append(1)
    event.listen(db, "before_flush", before_flush)
    try:
        observed = upsert_movies(db, connection, [{"id": str(index), "name": f"New movie {index}"} for index in range(500)])
    finally:
        event.remove(db, "before_flush", before_flush)
    assert observed == 500
    assert len(flushes) <= 3
    assert db.scalar(select(func.count()).select_from(MovieGroup)) == 500
    assert db.scalar(select(func.count()).select_from(ProviderMovie)) == 500


def test_new_native_families_resolve_out_of_order_parent_chains_without_orphan_groups(db):
    connection = db.get(Connection, 1)
    upsert_movies(db, connection, [
        {"id": "grandchild", "name": "Grandchild", "parent_id": "child"},
        {"id": "child", "name": "Child", "parent_id": "parent"},
        {"id": "parent", "name": "Parent"},
    ])
    movies = list(db.scalars(select(ProviderMovie)))
    assert len({row.group_id for row in movies}) == 1
    assert db.scalar(select(func.count()).select_from(MovieGroup)) == 1
    assert db.get(MovieGroup, movies[0].group_id).name == "Parent"
    assert {row.external_id: row.parent_external_id for row in movies} == {
        "parent": None, "child": "parent", "grandchild": "child"}


def test_existing_manual_split_remains_authoritative_and_new_children_follow_it(db):
    db.add_all([MovieGroup(id=1, country_code="BR", name="Native parent"),
                MovieGroup(id=2, country_code="BR", name="Manual split")])
    db.flush()
    add_movie(db, 1, "parent", 1)
    add_movie(db, 2, "manual-child", 2, manual=True)
    db.commit()
    upsert_movies(db, db.get(Connection, 1), [
        {"id": "parent", "name": "Native parent"},
        {"id": "manual-child", "name": "Changed variant", "parent_id": "parent"},
        {"id": "new-child", "name": "New variant", "parent_id": "manual-child"},
    ])
    movies = {row.external_id: row for row in db.scalars(select(ProviderMovie))}
    assert movies["manual-child"].group_id == 2 and movies["manual-child"].manually_grouped is True
    assert movies["parent"].group_id == 1
    assert movies["new-child"].group_id == 2
    assert db.scalar(select(func.count()).select_from(MovieGroup)) == 2


def test_complete_catalog_marks_absent_ids_inactive_but_retains_other_provider_group(db):
    db.add_all([MovieGroup(id=1, country_code="BR", name="Shared"), MovieGroup(id=2, country_code="BR", name="Only this provider")])
    db.flush()
    add_movie(db, 1, "missing-shared", 1)
    add_movie(db, 2, "missing-only", 2)
    add_movie(db, 3, "other-active", 1, connection_id=2)
    db.commit()
    connection = db.get(Connection, 1)
    assert upsert_movies(db, connection, [], complete=True) == 0
    assert connection.movie_count == 0
    assert db.get(ProviderMovie, 1).active is False and db.get(ProviderMovie, 2).active is False
    assert db.get(ProviderMovie, 3).active is True
    assert group_state(db) == {1: True, 2: False}


def test_partial_catalog_keeps_unseen_ids_available_and_counts_all_active(db):
    db.add(MovieGroup(id=1, country_code="BR", name="Existing manual group"))
    db.flush()
    add_movie(db, 1, "old", 1, manual=True)
    db.commit()
    connection = db.get(Connection, 1)
    assert upsert_movies(db, connection, [{"id": "new", "name": "New release"}], complete=False) == 1
    assert db.get(ProviderMovie, 1).active is True and db.get(ProviderMovie, 1).group_id == 1
    assert connection.movie_count == 2
    assert all(group_state(db).values())


def test_duplicate_movie_ids_do_not_create_duplicate_groups_and_invalid_rows_are_ignored(db):
    connection = db.get(Connection, 1)
    assert upsert_movies(db, connection, [
        {"id": "m", "name": "Before"}, {"id": "m", "name": "After"}, {"id": "", "name": "Invalid"}, {"id": "bad", "name": ""},
    ]) == 1
    assert db.scalar(select(func.count()).select_from(MovieGroup)) == 1
    row = db.scalar(select(ProviderMovie))
    assert row.name == "After" and connection.movie_count == 1


def test_parent_cycles_are_not_silently_merged(db):
    upsert_movies(db, db.get(Connection, 1), [
        {"id": "a", "name": "A", "parent_id": "b"},
        {"id": "b", "name": "B", "parent_id": "a"},
        {"id": "child-of-a", "name": "Variant A", "parent_id": "a"},
    ])
    rows = {row.external_id: row for row in db.scalars(select(ProviderMovie))}
    assert rows["a"].group_id != rows["b"].group_id
    assert rows["child-of-a"].group_id == rows["a"].group_id


def test_group_updates_chunk_without_a_global_catalog_limit(db):
    count = 1005
    db.add_all([MovieGroup(id=index, country_code="BR", name=f"Movie {index}") for index in range(1, count + 1)])
    db.flush()
    for index in range(1, count + 1):
        add_movie(db, index, str(index), index)
    db.commit()
    connection = db.get(Connection, 1)
    statements = []
    def record(_, __, statement, ___, ____, _____):
        statements.append(statement.strip().lower())
    event.listen(db.bind, "before_cursor_execute", record)
    try:
        assert upsert_movies(db, connection, [], complete=True) == 0
    finally:
        event.remove(db.bind, "before_cursor_execute", record)
    assert sum(statement.startswith("select") for statement in statements) <= 2
    assert sum(statement.startswith("update movie_groups") for statement in statements) == 2
    assert not any(group_state(db).values())


def test_ingresso_alias_updates_preserve_numeric_identity_manual_group_and_omission(db):
    db.add(MovieGroup(id=1, country_code="BR", name="Manually combined"))
    db.flush()
    add_movie(db, 1, "12345", 1, connection_id=2, manual=True)
    db.commit()
    connection = db.get(Connection, 2)
    upsert_movies(db, connection, [{"id": "12345", "name": "Film", "url_key": "film-url-key"}])
    movie = db.get(ProviderMovie, 1)
    assert (movie.id, movie.external_id, movie.group_id, movie.manually_grouped, movie.url_key) == (
        1, "12345", 1, True, "film-url-key")
    upsert_movies(db, connection, [{"id": "12345", "name": "Updated Film"}])
    assert movie.url_key == "film-url-key"
    upsert_movies(db, connection, [{"id": "12345", "name": "Film", "url_key": "x" * 600}])
    assert len(movie.url_key) == 500
    # An explicitly supplied empty/null alias clears it; omission never does.
    upsert_movies(db, connection, [{"id": "12345", "name": "Film", "url_key": None}])
    assert movie.url_key is None and movie.group_id == 1
    assert db.scalar(select(func.count()).select_from(ProviderMovie)) == 1
    assert db.scalar(select(func.count()).select_from(MovieGroup)) == 1


def test_other_providers_do_not_accept_ingresso_only_url_keys(db):
    connection = db.get(Connection, 1)
    upsert_movies(db, connection, [{"id": "velox-id", "name": "Velox film", "url_key": "not-an-ingresso-key"}])
    assert db.scalar(select(ProviderMovie)).url_key is None


def test_missing_or_invalid_release_metadata_does_not_erase_known_date(db):
    connection = db.get(Connection, 2)
    upsert_movies(db, connection, [{"id": "old", "name": "Film", "release": "2000-01-02"}])
    movie = db.scalar(select(ProviderMovie))
    identity = (movie.id, movie.group_id)
    for item in ({"id": "old", "name": "Film"}, {"id": "old", "name": "Film", "release": None},
                 {"id": "old", "name": "Film", "release": "invalid"}):
        upsert_movies(db, connection, [item])
        assert movie.release_date.isoformat() == "2000-01-02"
        assert (movie.id, movie.group_id) == identity
