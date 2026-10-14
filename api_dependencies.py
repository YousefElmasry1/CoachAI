"""Request-scoped FastAPI dependencies over the existing backend core."""

from __future__ import annotations

from threading import Lock
from typing import Any, Callable, Iterator

from sqlalchemy.engine import make_url

from backend_config import BackendSettings


class RequestUnitOfWorkProvider:
    """Yield one existing ``UnitOfWork`` per request and always release it."""

    def __init__(self, database_factory: Callable[[], Any] | None = None) -> None:
        self._database_factory = database_factory
        self._owned_factory: Any | None = None
        self._lock = Lock()

    def _database(self):
        if self._database_factory is not None:
            return self._database_factory()
        with self._lock:
            if self._owned_factory is None:
                settings = BackendSettings.from_env()
                database_url = make_url(settings.database_url)
                if database_url.get_backend_name() != "postgresql":
                    raise ValueError("The API runtime requires PostgreSQL.")
                database_url = database_url.update_query_dict(
                    {"connect_timeout": str(settings.external_timeout_seconds)}
                )
                from unit_of_work import DatabaseFactory

                self._owned_factory = DatabaseFactory(database_url)
        return self._owned_factory()

    def __call__(self) -> Iterator[Any]:
        from unit_of_work import UnitOfWork

        with UnitOfWork(self._database) as unit_of_work:
            yield unit_of_work

    def close(self) -> None:
        with self._lock:
            if self._owned_factory is not None:
                self._owned_factory.close()
                self._owned_factory = None
