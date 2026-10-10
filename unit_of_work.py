"""Explicit request/worker database acquisition and transaction lifetime."""

from __future__ import annotations

from threading import Lock
from typing import Callable

from database import Database


class DatabaseFactory:
    """Own one process-level pool while issuing short-lived connections.

    A future web request and each worker iteration should create a
    :class:`UnitOfWork` from this factory. Closing a unit releases its checked
    out connection; closing the factory disposes the process pool.
    """

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.engine = Database.create_pool(database_url)
        self._schema_validated = False
        self._lock = Lock()
        self._closed = False

    def __call__(self) -> Database:
        if self._closed:
            raise RuntimeError("DatabaseFactory is closed.")
        with self._lock:
            check_schema = not self._schema_validated
            db = Database(
                self.database_url,
                engine=self.engine,
                dispose_engine_on_close=False,
                check_schema=check_schema,
            )
            if check_schema:
                self._schema_validated = True
            return db

    def pool_metrics(self) -> dict[str, int | str | None]:
        pool = self.engine.pool

        def value(name: str):
            candidate = getattr(pool, name, None)
            if candidate is None:
                return None
            try:
                return int(candidate() if callable(candidate) else candidate)
            except (TypeError, ValueError, NotImplementedError):
                return None

        return {
            "status": pool.status(),
            "size": value("size"),
            "checked_in": value("checkedin"),
            "checked_out": value("checkedout"),
            "overflow": value("overflow"),
        }

    def close(self) -> None:
        if not self._closed:
            self.engine.dispose()
            self._closed = True

    def __enter__(self) -> "DatabaseFactory":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


class UnitOfWork:
    """One checked-out connection with deterministic rollback and release."""

    def __init__(self, database_factory: Callable[[], Database] = Database) -> None:
        self._factory = database_factory
        self.db: Database | None = None

    def __enter__(self) -> "UnitOfWork":
        if self.db is not None:
            raise RuntimeError("UnitOfWork cannot be entered twice.")
        self.db = self._factory()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.db is None:
            return
        try:
            if self.db.connection is not None:
                # A correctly scoped application operation has already
                # committed its explicit transaction. A leftover implicit
                # SQLAlchemy transaction is never allowed to leak into the
                # next pool checkout.
                self.db.connection.rollback()
        finally:
            self.db.close()
            self.db = None

    def transaction(self):
        if self.db is None:
            raise RuntimeError("UnitOfWork must be entered before use.")
        return self.db.transaction()
