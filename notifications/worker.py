"""Durable notification worker facade."""

from __future__ import annotations

from datetime import datetime
from typing import Optional, TYPE_CHECKING

from .service import NotificationService

if TYPE_CHECKING:
    from database import Database


class NotificationWorker:
    """Claim and process bounded batches using database-backed leases.

    Retry policy: at most five attempts, beginning at 30 seconds and doubling
    deterministically to a 15-minute cap. Validation/ownership failures are
    permanent; other failures are retried. A five-minute default lease makes
    abandoned PROCESSING rows recoverable by another worker.
    """

    def __init__(self, db: "Database", *, worker_id: Optional[str] = None) -> None:
        self.service = NotificationService(db)
        self.worker_id = worker_id

    def run_once(
        self, *, limit: int = 50, now_utc: Optional[datetime] = None,
        lease_seconds: int = 300, max_attempts: int = 5,
    ) -> dict:
        result = self.service.process_outbox(
            limit=limit, now_utc=now_utc, worker_id=self.worker_id,
            lease_seconds=lease_seconds, max_attempts=max_attempts,
        )
        push = self.service.process_push_deliveries(
            limit=limit, now_utc=now_utc, worker_id=self.worker_id,
            lease_seconds=lease_seconds, max_attempts=max_attempts,
        )
        if push["attempted"] or push["cancelled"]:
            result["push"] = push
        return result

    def run_push_once(
        self, *, limit: int = 100, now_utc: Optional[datetime] = None,
        lease_seconds: int = 300, max_attempts: int = 5,
    ) -> dict:
        return self.service.process_push_deliveries(
            limit=limit, now_utc=now_utc, worker_id=self.worker_id,
            lease_seconds=lease_seconds, max_attempts=max_attempts,
        )
