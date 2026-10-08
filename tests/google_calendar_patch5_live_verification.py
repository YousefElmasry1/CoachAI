"""Opt-in Patch 5 live Google Calendar verification.

This module is intentionally excluded from ``unittest discover``.  It must be
run against a disposable copy of the CoachAI database that contains a real
OAuth session.  Every remote event it creates has a unique exact ID and is
deleted by exact ID in ``finally``; unrelated events are read-only.
"""

from __future__ import annotations

import base64
import hashlib
import os
import sys
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path


def require_disposable_database() -> None:
    url = os.environ.get("DATABASE_URL", "")
    normalized = url.replace("\\", "/").lower()
    if "coachai_patch51_calendar_verify.db" not in normalized:
        raise SystemExit("Refusing live verification without the approved disposable database copy.")


require_disposable_database()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import services  # noqa: E402
from google_calendar import GoogleCalendarClient  # noqa: E402
from scheduler import SchedulingPreferences  # noqa: E402


USER_ID = "1"
CALENDAR_ID = "primary"


def deterministic_segment_event_id(user_id: str, segment_id: int) -> str:
    digest = hashlib.sha256(f"{user_id}:{segment_id}".encode("utf-8")).digest()
    return "coachai" + base64.b32hexencode(digest).decode("ascii").lower().rstrip("=")[:40]


def main() -> None:
    db = services.get_database()
    client = services._get_google_client(USER_ID)
    if client is None:
        raise AssertionError("The disposable database copy has no Google OAuth session.")
    calendars = client.list_calendars()
    if not any(calendar.get("is_primary") for calendar in calendars):
        raise AssertionError("The authenticated account has no primary calendar.")

    user = db.get_user(USER_ID)
    timezone_name = str(user["timezone"] or "UTC")
    today = date.today()
    run_id = uuid.uuid4().hex[:12]
    title_prefix = f"[CoachAI Patch5.1 {run_id}]"
    created_remote_ids: set[str] = set()
    service = client._build_service()

    def remote_get(event_id: str):
        try:
            event = service.events().get(calendarId=CALENDAR_ID, eventId=event_id).execute()
            return None if event.get("status") == "cancelled" else event
        except Exception as exc:
            if getattr(getattr(exc, "resp", None), "status", None) in {404, 410}:
                return None
            raise

    def create_remote(event_id: str, title: str, day: date, start: str, end: str) -> dict:
        created_remote_ids.add(event_id)
        returned = client.create_event(
            CALENDAR_ID, title, start, end, day.isoformat(), timezone_name, event_id,
        )
        if returned != event_id:
            raise AssertionError("Google did not preserve the supplied deterministic event identity.")
        event = remote_get(event_id)
        if event is None:
            raise AssertionError(f"Created event {event_id} is not readable.")
        return event

    def find_unused_days(offset: int) -> list[date]:
        for delta in range(offset, offset + 42):
            days = [today + timedelta(days=delta + index) for index in range(3)]
            if all(
                db.fetch_one(
                    "SELECT 1 FROM plans WHERE user_id=? AND plan_date=?",
                    (USER_ID, day.isoformat()),
                ) is None
                for day in days
            ):
                return days
        raise AssertionError("Could not find three disposable plan dates in the database copy.")

    try:
        days = find_unused_days(28)
        plans = {day: db.create_plan(USER_ID, day, f"{title_prefix} plan") for day in days}

        external_id = "p51e" + uuid.uuid4().hex
        imported_id = "p51i" + uuid.uuid4().hex
        legacy_id = "p51l" + uuid.uuid4().hex
        external_before = create_remote(
            external_id, f"{title_prefix} external occupancy", days[0], "08:00", "09:00"
        )
        imported_before = create_remote(
            imported_id, f"{title_prefix} imported protected", days[1], "20:00", "21:00"
        )
        legacy_before = create_remote(
            legacy_id, f"{title_prefix} legacy protected", days[2], "20:00", "21:00"
        )
        db.upsert_google_calendar_event(
            USER_ID, external_id, external_before["summary"], "08:00", "09:00",
            days[0].isoformat(), CALENDAR_ID,
        )
        imported_task = db.add_task(
            plans[days[1]], USER_ID, imported_before["summary"], estimated_minutes=60,
            is_fixed_time=True, scheduled_start="20:00", scheduled_end="21:00",
            google_event_id=imported_id, google_calendar_id=CALENDAR_ID,
            calendar_provenance="imported",
        )
        legacy_task = db.add_task(
            plans[days[2]], USER_ID, legacy_before["summary"], estimated_minutes=60,
            is_fixed_time=True, scheduled_start="20:00", scheduled_end="21:00",
            google_event_id=legacy_id, google_calendar_id=CALENDAR_ID,
            calendar_provenance="legacy_unknown",
        )
        long_task = db.add_task(
            plans[days[0]], USER_ID, f"{title_prefix} multi-day", estimated_minutes=600,
            priority=1, earliest_start=datetime.combine(days[0], time(8)),
            deadline=datetime.combine(days[-1], time(18)),
        )
        planner = services.get_future_planning_service()
        preferences = SchedulingPreferences(work_day_start=time(8))
        preview = planner.preview(USER_ID, days[0], days[-1], preferences)
        outcome = next(item for item in preview.outcomes if item.task_id == long_task)
        if not outcome.placed or len(outcome.segments) != 3:
            raise AssertionError("Controlled multi-day task was not placed in exactly three segments.")
        if outcome.segments[0].scheduled_start < time(9):
            raise AssertionError("The real external Calendar occupancy was not treated as a hard anchor.")
        operation = planner.apply(preview)

        if not services.export_task_to_google_calendar(long_task, USER_ID, client=client):
            raise AssertionError("Initial live multi-day export failed.")
        original_segments = [dict(row) for row in db.get_task_segments(long_task, USER_ID)]
        if len(original_segments) != 3:
            raise AssertionError("Persisted multi-day segment count differs from preview.")
        original_windows = {
            int(row["segment_index"]): (
                str(row["effective_date"]), str(row["scheduled_start"]), str(row["scheduled_end"])
            )
            for row in original_segments
        }
        original_ids = {str(row["google_event_id"]) for row in original_segments}
        created_remote_ids.update(original_ids)
        for row in original_segments:
            expected_id = deterministic_segment_event_id(USER_ID, int(row["segment_id"]))
            if row["google_event_id"] != expected_id or remote_get(expected_id) is None:
                raise AssertionError("A physical segment lacks its deterministic live event.")
        if not services.export_task_to_google_calendar(long_task, USER_ID, client=client):
            raise AssertionError("Idempotent repeat export failed.")
        if sum(remote_get(event_id) is not None for event_id in original_ids) != 3:
            raise AssertionError("Repeat export duplicated or lost a segment event.")

        if not services.export_task_to_google_calendar(imported_task, USER_ID, client=client):
            raise AssertionError("Imported-event protection call failed.")
        if not services.export_task_to_google_calendar(legacy_task, USER_ID, client=client):
            raise AssertionError("Legacy-event protection call failed.")
        if remote_get(imported_id) != imported_before or remote_get(legacy_id) != legacy_before:
            raise AssertionError("Imported or legacy_unknown live event was modified.")

        first = original_segments[0]
        blocker_id = "p51b" + uuid.uuid4().hex
        create_remote(
            blocker_id, f"{title_prefix} move blocker",
            date.fromisoformat(str(first["effective_date"])),
            str(first["scheduled_start"]), str(first["scheduled_end"]),
        )
        db.upsert_google_calendar_event(
            USER_ID, blocker_id, f"{title_prefix} move blocker",
            str(first["scheduled_start"]), str(first["scheduled_end"]),
            str(first["effective_date"]), CALENDAR_ID,
        )
        moved_preview = planner.preview(USER_ID, days[0], days[-1], preferences)
        moved_operation = planner.apply(moved_preview)
        moved_before_export = [dict(row) for row in db.get_task_segments(long_task, USER_ID)]
        if all(
            original_windows[int(row["segment_index"])] == (
                str(row["effective_date"]), str(row["scheduled_start"]), str(row["scheduled_end"])
            )
            for row in moved_before_export
        ):
            raise AssertionError("The external blocker did not cause a controlled move.")

        moved_reconciliation = services.reconcile_schedule_calendar_operation(
            moved_operation, USER_ID, client=client,
        )
        if moved_reconciliation["errors"] or moved_reconciliation["deleted"]:
            raise AssertionError("A moved owned segment was deleted instead of updated.")
        if not services.export_task_to_google_calendar(long_task, USER_ID, client=client):
            raise AssertionError("Moved-segment live export failed.")
        moved_segments = [dict(row) for row in db.get_task_segments(long_task, USER_ID)]
        moved_ids = {str(row["google_event_id"]) for row in moved_segments}
        created_remote_ids.update(moved_ids)
        if moved_ids != original_ids:
            raise AssertionError("A moved segment did not preserve its stable Calendar identity.")
        if any(remote_get(event_id) is None for event_id in moved_ids):
            raise AssertionError("Moved schedule is missing a live segment event.")

        if not services.undo_smart_schedule(moved_operation, USER_ID):
            raise AssertionError("Future Planning move Undo failed.")
        restored = [dict(row) for row in db.get_task_segments(long_task, USER_ID)]
        restored_windows = {
            int(row["segment_index"]): (
                str(row["effective_date"]), str(row["scheduled_start"]), str(row["scheduled_end"])
            )
            for row in restored
        }
        if restored_windows != original_windows:
            raise AssertionError("Future Planning Undo did not restore exact multi-day windows.")
        if any(remote_get(str(row["google_event_id"])) is None for row in restored):
            raise AssertionError("Future Planning Undo did not restore every live segment event.")

        # Force a real orphan-cleanup operation without changing task data:
        # disposable external occupancy makes the task unplaceable, then Undo
        # restores the exact multi-day state and recovers tombstoned IDs.
        for index, day in enumerate(days):
            capacity_blocker_id = "p51c" + uuid.uuid4().hex
            create_remote(
                capacity_blocker_id, f"{title_prefix} capacity blocker {index}",
                day, "08:00", "19:00",
            )
            db.upsert_google_calendar_event(
                USER_ID, capacity_blocker_id,
                f"{title_prefix} capacity blocker {index}", "08:00", "19:00",
                day.isoformat(), CALENDAR_ID,
            )
        blocked_preview = planner.preview(USER_ID, days[0], days[-1], preferences)
        blocked_outcome = next(item for item in blocked_preview.outcomes if item.task_id == long_task)
        if blocked_outcome.placed:
            raise AssertionError("Controlled external occupancy did not produce an unplaced result.")
        blocked_operation = planner.apply(blocked_preview)
        if db.get_task_segments(long_task, USER_ID):
            raise AssertionError("Unplaced Apply retained physical segments.")

        bad_client = GoogleCalendarClient(
            "intentionally-invalid-patch51-token", "",
            datetime.now(timezone.utc) + timedelta(hours=1),
        )
        failed = services.reconcile_schedule_calendar_operation(
            blocked_operation, USER_ID, client=bad_client,
        )
        if not failed["errors"]:
            raise AssertionError("A real Calendar API failure was not surfaced.")
        failed_row = db.get_schedule_operation(blocked_operation, USER_ID)
        if not failed_row["calendar_reconciliation_error"]:
            raise AssertionError("Calendar reconciliation failure was not durably recorded.")

        retried = services.reconcile_schedule_calendar_operation(
            blocked_operation, USER_ID, client=client,
        )
        if retried["errors"] or retried["deleted"] != 3:
            raise AssertionError(f"Live reconciliation retry failed: {retried}")
        retried_row = db.get_schedule_operation(blocked_operation, USER_ID)
        if retried_row["calendar_reconciliation_error"]:
            raise AssertionError("Successful retry did not clear the durable error.")
        if any(remote_get(event_id) is not None for event_id in original_ids):
            raise AssertionError("Orphaned segment events remained active after reconciliation.")

        if not services.undo_smart_schedule(blocked_operation, USER_ID):
            raise AssertionError("Multi-day orphan-cleanup Undo failed.")
        recovered = [dict(row) for row in db.get_task_segments(long_task, USER_ID)]
        recovered_windows = {
            int(row["segment_index"]): (
                str(row["effective_date"]), str(row["scheduled_start"]), str(row["scheduled_end"])
            )
            for row in recovered
        }
        if recovered_windows != original_windows:
            raise AssertionError("Multi-day Undo did not restore exact physical windows.")
        recovered_ids = {str(row["google_event_id"]) for row in recovered}
        created_remote_ids.update(recovered_ids)
        if len(recovered_ids) != 3 or any(remote_get(event_id) is None for event_id in recovered_ids):
            raise AssertionError("Multi-day Undo failed to recover tombstoned segment events.")

        undo_days = find_unused_days(70)
        undo_plans = {
            day: db.create_plan(USER_ID, day, f"{title_prefix} undo plan") for day in undo_days
        }
        undo_task = db.add_task(
            undo_plans[undo_days[0]], USER_ID, f"{title_prefix} apply undo", estimated_minutes=600,
            priority=2, deadline=datetime.combine(undo_days[-1], time(18)),
        )
        fresh_preview = planner.preview(USER_ID, undo_days[0], undo_days[-1], preferences)
        fresh_operation = planner.apply(fresh_preview)
        if not services.export_task_to_google_calendar(undo_task, USER_ID, client=client):
            raise AssertionError("Fresh multi-day Apply export failed.")
        fresh_ids = {
            str(row["google_event_id"]) for row in db.get_task_segments(undo_task, USER_ID)
        }
        created_remote_ids.update(fresh_ids)
        if len(fresh_ids) != 3:
            raise AssertionError("Fresh Apply did not create one event per physical segment.")
        if not services.undo_smart_schedule(fresh_operation, USER_ID):
            raise AssertionError("Fresh multi-day Apply Undo failed.")
        if db.get_task_segments(undo_task, USER_ID):
            raise AssertionError("Fresh Apply Undo left local segments behind.")
        if any(remote_get(event_id) is not None for event_id in fresh_ids):
            raise AssertionError("Fresh Apply Undo left live segment events behind.")

        if remote_get(external_id) != external_before:
            raise AssertionError("External Calendar event changed during planning operations.")
        if remote_get(imported_id) != imported_before or remote_get(legacy_id) != legacy_before:
            raise AssertionError("Protected Calendar events changed during Apply/Undo.")

        print("LIVE_GOOGLE_CALENDAR_PATCH5=PASS")
        print("MULTI_DAY_SEGMENTS=3")
        print("ONE_EVENT_PER_SEGMENT=PASS")
        print("DETERMINISTIC_IDENTITY=PASS")
        print("MOVED_RECONCILIATION=PASS")
        print("ORPHAN_CLEANUP=PASS")
        print("PROVENANCE_PROTECTION=PASS")
        print("REAL_API_FAILURE_DURABLE_RETRY=PASS")
        print("FUTURE_APPLY_UNDO=PASS")
        print("MULTI_DAY_UNDO=PASS")
        print(f"CALENDAR_COUNT={len(calendars)}")
    finally:
        cleanup_errors = []
        for event_id in sorted(created_remote_ids):
            try:
                client.delete_event(CALENDAR_ID, event_id)
            except Exception as exc:
                cleanup_errors.append(f"{event_id}: {exc}")
        remaining = [event_id for event_id in created_remote_ids if remote_get(event_id) is not None]
        if cleanup_errors or remaining:
            print(f"CLEANUP_ERRORS={cleanup_errors}", file=sys.stderr)
            print(f"CLEANUP_REMAINING_COUNT={len(remaining)}", file=sys.stderr)
            if sys.exc_info()[0] is None:
                raise AssertionError("Live verification event cleanup was incomplete.")
        else:
            print(f"REMOTE_TEST_EVENTS_CLEANED={len(created_remote_ids)}")


if __name__ == "__main__":
    main()
