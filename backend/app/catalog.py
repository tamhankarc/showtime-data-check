"""Upsert provider catalogs without losing manual movie/theater assignments."""
from datetime import date
import re
import unicodedata
from sqlalchemy import exists, select, update
from .models import Connection, MovieGroup, ProviderMovie, ProviderTheater, Theater, utcnow


def connection_dict(connection, db=None):
    values = {column.name: getattr(connection, column.name) for column in connection.__table__.columns}
    if db is not None:
        rows = db.execute(select(ProviderTheater.external_id, Theater.timezone).join(Theater, ProviderTheater.theater_id == Theater.id)
                          .where(ProviderTheater.connection_id == connection.id)).all()
        values["theater_timezones"] = dict(rows)
    return values


def generic_initial(name):
    value = unicodedata.normalize("NFKD", name or "")
    return next((c.lower() for c in value if re.fullmatch(r"[A-Za-z0-9]", c)), "t")


def parse_day(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def upsert_movies(db, connection, movies, complete=True):
    records = {row.external_id: row for row in db.scalars(select(ProviderMovie).where(ProviderMovie.connection_id == connection.id))}
    incoming = {}
    for item in movies:
        external_id, name = str(item.get("id") or "").strip(), str(item.get("name") or "").strip()
        if external_id and name:
            incoming[external_id] = {**item, "id": external_id, "name": name,
                                     "parent_id": str(item.get("parent_id") or "").strip() or None}
    observed = set(incoming)

    # Existing decisions are immutable during catalog refresh, including manual
    # splits of provider-native parent/child families. Resolve new families in RAM
    # before inserting anything, rather than flushing one new group per movie.
    targets = {external_id: ("existing", row.group_id) for external_id, row in records.items()}
    for external_id in incoming:
        if external_id in targets:
            continue
        trail, positions, cursor = [], {}, external_id
        while cursor not in targets:
            if cursor in positions:
                # A malformed native parent cycle is not evidence that distinct
                # IDs should be combined. Keep its members individually reviewable.
                for member in trail[positions[cursor]:]:
                    targets[member] = ("new", member)
                break
            positions[cursor] = len(trail)
            trail.append(cursor)
            parent = incoming[cursor]["parent_id"]
            if not parent or parent == cursor or parent not in incoming and parent not in records:
                targets[cursor] = ("new", cursor)
                break
            cursor = parent
        for member in reversed(trail):
            if member not in targets:
                targets[member] = targets.get(incoming[member]["parent_id"], ("new", member))

    roots = dict.fromkeys(targets[external_id][1] for external_id in incoming
                         if targets[external_id][0] == "new")
    new_groups = {external_id: MovieGroup(country_code=connection.country_code,
                    name=incoming[external_id]["name"][:500], active=True) for external_id in roots}
    if new_groups:
        db.add_all(list(new_groups.values()))
        db.flush()

    new_movies = []
    for external_id, item in incoming.items():
        row = records.get(external_id)
        if row is None:
            kind, target = targets[external_id]
            group_id = target if kind == "existing" else new_groups[target].id
            row = ProviderMovie(connection_id=connection.id, external_id=external_id,
                                name=item["name"][:500], group_id=group_id)
            new_movies.append(row)
            records[external_id] = row
        row.name, row.active = item["name"][:500], True
        row.original_name = str(item.get("original_name") or "")[:500] or None
        if connection.provider_code == "INGRESSO" and "url_key" in item:
            row.url_key = str(item.get("url_key") or "").strip()[:500] or None
        parsed_release = parse_day(item.get("release"))
        if parsed_release is not None:
            row.release_date = parsed_release
        row.parent_external_id = str(item.get("parent_id") or "")[:160] or None
    db.add_all(new_movies)
    if complete:
        for external_id, row in records.items():
            if external_id not in observed:
                row.active = False
    db.flush()

    # Group availability depends on all providers, not only this refreshed feed.
    # A correlated EXISTS update replaces two database queries per catalog movie.
    # Chunks bound bind-parameter counts without limiting the number of records.
    group_ids = sorted({row.group_id for row in records.values()})
    active_member = exists(select(ProviderMovie.id).where(
        ProviderMovie.group_id == MovieGroup.id, ProviderMovie.active.is_(True)))
    for start in range(0, len(group_ids), 1000):
        db.execute(update(MovieGroup).where(MovieGroup.id.in_(group_ids[start:start + 1000]))
                   .values(active=active_member).execution_options(synchronize_session="fetch"))
    connection.movie_count = len(observed) if complete else sum(row.active for row in records.values())
    return len(observed)


def upsert_theaters(db, connection, theaters):
    existing = {row.external_id: row for row in db.scalars(select(ProviderTheater).where(ProviderTheater.connection_id == connection.id))}
    for item in theaters:
        external_id = str(item.get("id") or "").strip()
        if not external_id:
            continue
        row = existing.get(external_id)
        if row is None:
            name = str(item.get("name") or f"{connection.provider_code} theater {external_id}")[:500]
            canonical = Theater(country_code=connection.country_code, name=name, address=item.get("address"), city=item.get("city"),
                                region=item.get("region"), postal_code=item.get("postal"), latitude=item.get("lat"), longitude=item.get("lon"),
                                website=item.get("website"), chain=item.get("chain_name"), timezone=item.get("timezone") or connection.timezone)
            db.add(canonical)
            db.flush()
            row = ProviderTheater(connection_id=connection.id, external_id=external_id, name=name, theater_id=canonical.id, review_status="needs_review")
            db.add(row)
            existing[external_id] = row
        # Update observed provider details only; canonical edits and assignment decisions remain authoritative.
        for source, destination in (("name", "name"), ("address", "address"), ("city", "city"), ("region", "region"),
                                    ("postal", "postal_code"), ("lat", "latitude"), ("lon", "longitude"),
                                    ("website", "website"), ("chain_name", "chain")):
            if item.get(source) is not None:
                setattr(row, destination, item[source])
    db.flush()
