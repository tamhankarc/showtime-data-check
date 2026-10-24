"""Small single-process worker with metadata in SQL and results ONLY in RAM.

Run one Uvicorn worker/one Railway replica. Restarting intentionally discards all
showtimes; previously running checks become failed and require explicit reload.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import timedelta
import threading
import time
import uuid
from sqlalchemy import delete, select, update
from fastapi import HTTPException
from .catalog import connection_dict, upsert_movies, upsert_theaters
from .models import AuthSession, Connection, Job, utcnow
from .results import consolidate, normalize_rows


@dataclass
class Slice:
    rows: list
    fetched_at: str
    expires_at: str
    deadline: float


@dataclass
class Snapshot:
    owner: str
    selection: dict
    cancel: threading.Event = field(default_factory=threading.Event)
    slices: dict = field(default_factory=dict)
    outcomes: dict = field(default_factory=dict)
    invalid: str = ""
    created: float = field(default_factory=time.monotonic)


class JobManager:
    def __init__(self, session_factory, settings, provider_service):
        self.sessions, self.settings, self.provider = session_factory, settings, provider_service
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="check")
        self.provider_limit = threading.BoundedSemaphore(settings.provider_concurrency)
        self.lock = threading.RLock()
        self.snapshots = {}
        self.stopping = threading.Event()
        with self.sessions() as db:
            db.execute(update(Job).where(Job.status.in_(["queued", "processing"])).values(status="failed", message="Service restarted. Please reload explicitly.", completed_at=utcnow()))
            db.commit()
        self.cleaner = threading.Thread(target=self._cleanup_loop, daemon=True, name="snapshot-expiry")
        self.cleaner.start()

    def close(self):
        self.stopping.set()
        with self.lock:
            for snapshot in self.snapshots.values():
                snapshot.cancel.set()
                snapshot.slices.clear()
            self.snapshots.clear()
        self.executor.shutdown(wait=False, cancel_futures=True)

    def _cleanup_loop(self):
        while not self.stopping.wait(5):
            self.purge()

    def purge(self):
        now = time.monotonic()
        with self.lock:
            for identifier, snapshot in list(self.snapshots.items()):
                expired = [code for code, value in snapshot.slices.items() if value.deadline <= now]
                for code in expired:
                    del snapshot.slices[code]
                    snapshot.outcomes[code] = {**snapshot.outcomes.get(code, {}), "status": "expired", "message": "Temporary data expired. Reload required."}
                if expired:
                    snapshot.invalid = "expired"
                    snapshot.slices.clear()
                    snapshot.cancel.set()
                if now - snapshot.created > 7200:
                    snapshot.cancel.set()
                    del self.snapshots[identifier]

    def invalidate(self, country_code=None):
        with self.lock:
            for snapshot in self.snapshots.values():
                if country_code is None or snapshot.selection.get("country_code") == country_code:
                    snapshot.invalid = "stale"
                    snapshot.cancel.set()
                    snapshot.slices.clear()

    def submit(self, kind, owner, parameters):
        identifier = uuid.uuid4().hex
        with self.sessions() as db:
            active = db.scalars(select(Job.id).where(Job.status.in_(["queued", "processing"]))).all()
            if len(active) >= 12:
                raise HTTPException(429, "The request queue is full. Please wait for a current job to finish.")
            db.add(Job(id=identifier, kind=kind, owner=owner, parameters=parameters))
            db.commit()
        if kind == "check":
            self.purge()
            with self.lock:
                retained = sum(bool(value.slices) or (not value.outcomes and not value.invalid) for value in self.snapshots.values())
                if retained >= self.settings.max_snapshots:
                    self._update(identifier, status="failed", message="Too many retained checks. Cancel or wait for an existing result to expire.")
                    raise HTTPException(429, "Too many checks are open. Cancel an earlier check or wait for expiry.")
                self.snapshots[identifier] = Snapshot(owner, parameters)
        self.executor.submit(self._run, identifier)
        return identifier

    def _update(self, identifier, **values):
        with self.sessions() as db:
            db.execute(update(Job).where(Job.id == identifier).values(**values))
            db.commit()

    def _run(self, identifier, retry_codes=None):
        with self.sessions() as db:
            job = db.get(Job, identifier)
            if not job:
                return
            parameters, kind = job.parameters, job.kind
            connections = list(db.scalars(select(Connection).where(Connection.id.in_(parameters["connection_ids"]))))
            connections = [connection_dict(connection, db) for connection in connections]
        self._update(identifier, status="processing", progress=0, message="Downloading provider data")
        with self.lock:
            snapshot = self.snapshots.get(identifier)
            cancel = snapshot.cancel if snapshot else threading.Event()
        outcomes = dict(snapshot.outcomes) if snapshot else {}
        chosen = [connection for connection in connections if retry_codes is None or connection["provider_code"] in retry_codes]
        def execute(connection):
            code = connection["provider_code"]
            while not self.provider_limit.acquire(timeout=0.2):
                if cancel.is_set() or self.stopping.is_set():
                    raise RuntimeError("Cancelled")
            try:
                if cancel.is_set():
                    raise RuntimeError("Cancelled")
                if kind == "catalog":
                    result = self.provider.catalog(connection, cancel)
                else:
                    ids = set(parameters["external_ids"].get(code, []))
                    result = self.provider.showtimes(connection, ids, cancel)
                # Expiry starts at the provider's completion, never at a later consumer turn.
                return connection, result, utcnow(), time.monotonic() + self.settings.snapshot_seconds
            finally:
                self.provider_limit.release()
        futures = {}
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="provider") as pool:
            for connection in chosen:
                futures[pool.submit(execute, connection)] = connection
            index = 0
            for future in as_completed(futures):
                index += 1
                connection_data = futures.pop(future)
                code = connection_data["provider_code"]
                result, rows = None, None
                try:
                    _, result, fetched_at, deadline = future.result()
                    if cancel.is_set():
                        result.showtimes.clear()
                        result.movies.clear()
                        result.theaters.clear()
                        continue
                    with self.sessions() as db:
                        connection = db.get(Connection, connection_data["id"])
                        if kind == "catalog":
                            if result.warnings:
                                # Catalog completeness cannot silently retire valid IDs.
                                upsert_movies(db, connection, result.movies, complete=False)
                            else:
                                upsert_movies(db, connection, result.movies, complete=True)
                        else:
                            upsert_movies(db, connection, result.movies, complete=False)
                        upsert_theaters(db, connection, result.theaters)
                        if kind == "catalog":
                            connection.last_success_at, connection.last_status = fetched_at, "partial" if result.warnings else "done"
                            connection.last_error = "; ".join(result.warnings)[:500] or None
                        rows = normalize_rows(db, connection, result.showtimes) if kind == "check" else []
                        db.commit()
                    # Clear payloads on the shared ProviderResult too; Future also owns it.
                    result.showtimes.clear()
                    result.movies.clear()
                    result.theaters.clear()
                    if len(rows) > self.settings.max_snapshot_rows:
                        raise RuntimeError("This result exceeds the safe temporary memory limit. Select fewer movie IDs.")
                    outcome = {"provider_code": code, "country_code": connection_data["country_code"], "connection_id": connection_data["id"],
                               "status": "partial" if result.warnings else "done", "message": "; ".join(result.warnings)[:500] or "Complete",
                               "fetched_at": fetched_at.isoformat(), "expires_at": (fetched_at + timedelta(seconds=self.settings.snapshot_seconds)).isoformat() if kind == "check" else None}
                    if snapshot:
                        with self.lock:
                            if not snapshot.invalid and not cancel.is_set():
                                snapshot.slices[code] = Slice(rows, outcome["fetched_at"], outcome["expires_at"], deadline)
                                snapshot.outcomes[code] = outcome
                    outcomes[code if kind == "check" else str(connection_data["id"])] = outcome
                except Exception as error:
                    # Adapters expose safe errors. Never log exception URLs/credentials/response bodies.
                    from .providers import ProviderError
                    message = str(error)[:500] if isinstance(error, ProviderError) else "The provider request could not complete. Please retry."
                    outcome = {"provider_code": code, "country_code": connection_data["country_code"], "connection_id": connection_data["id"], "status": "cancelled" if cancel.is_set() else "failed", "message": message}
                    outcomes[code if kind == "check" else str(connection_data["id"])] = outcome
                    if snapshot:
                        with self.lock:
                            snapshot.outcomes[code] = outcome
                    else:
                        with self.sessions() as db:
                            connection = db.get(Connection, connection_data["id"])
                            connection.last_status, connection.last_error = "failed", message
                            db.commit()
                finally:
                    if result is not None:
                        result.showtimes.clear()
                        result.movies.clear()
                        result.theaters.clear()
                    # A slow second provider must not retain the first slice through
                    # a worker-local alias after the shared cache has expired.
                    result, rows = None, None
                    # Failed futures own exception tracebacks, which may own a
                    # provider parser's raw payload even before a result exists.
                    del future
                self._update(identifier, progress=int(index / max(1, len(chosen)) * 100), provider_outcomes=list(outcomes.values()))
        if snapshot and snapshot.cancel.is_set():
            status = "cancelled" if not snapshot.invalid else snapshot.invalid
        else:
            values = list(outcomes.values())
            successes = sum(value["status"] == "done" for value in values)
            status = "done" if successes == len(values) else "partial" if any(value["status"] in {"done", "partial"} for value in values) else "failed"
        self._update(identifier, status=status, progress=100, message={"done": "Complete", "partial": "Incomplete: one or more provider results are unavailable", "failed": "Provider requests failed", "cancelled": "Cancelled", "stale": "Reference data changed. Reload required.", "expired": "Result expired. Reload required."}.get(status, status), completed_at=utcnow(), provider_outcomes=list(outcomes.values()))

    def get_job(self, identifier, user):
        with self.sessions() as db:
            job = db.get(Job, identifier)
            if not job or (job.owner != user["username"] and user["role"] != "admin"):
                raise HTTPException(404, "Check not found.")
            return {"id": job.id, "kind": job.kind, "status": job.status, "progress": job.progress, "message": job.message,
                    "provider_outcomes": job.provider_outcomes, "created_at": job.created_at, "completed_at": job.completed_at}

    def result(self, identifier, user):
        self.purge()
        job = self.get_job(identifier, user)
        with self.lock:
            snapshot = self.snapshots.get(identifier)
            if not snapshot:
                raise HTTPException(410, "This temporary check is no longer available. Reload fresh data.")
            if snapshot.invalid:
                raise HTTPException(410, "Reference data changed; reload required." if snapshot.invalid == "stale" else "Temporary result expired. Reload fresh data.")
            rows = consolidate([row for value in snapshot.slices.values() for row in value.rows])
            expires = min((value.expires_at for value in snapshot.slices.values()), default=None)
            return rows, {"status": job["status"], "selection": snapshot.selection, "provider_outcomes": list(snapshot.outcomes.values()), "expires_at": expires}

    def cancel(self, identifier, user):
        job = self.get_job(identifier, user)
        if job["kind"] != "check":
            raise HTTPException(400, "Only a showtime check can be cancelled here.")
        with self.lock:
            snapshot = self.snapshots.get(identifier)
            if snapshot:
                snapshot.cancel.set()
                snapshot.slices.clear()
                snapshot.invalid = "cancelled"
        self._update(identifier, status="cancelled", message="Cancelled", completed_at=utcnow())

    def retry(self, identifier, user, provider_codes):
        self.result(identifier, user)
        job = self.get_job(identifier, user)
        if job["status"] in {"queued", "processing"}:
            raise HTTPException(409, "A request is already processing for this check.")
        with self.lock:
            snapshot = self.snapshots[identifier]
            allowed = [code for code, outcome in snapshot.outcomes.items() if outcome["status"] in {"failed", "partial"}]
            codes = [code for code in provider_codes if code in allowed] if provider_codes else allowed
            if not codes:
                raise HTTPException(400, "There are no failed providers to retry.")
        self._update(identifier, status="queued", message="Provider retry queued", progress=0)
        self.executor.submit(self._run, identifier, codes)
