import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from database import Database, TimerConflictError
from rate_limiter import RateLimiter, RateLimitExceeded
from timezone_utils import local_hour_from_utc_string, safe_zoneinfo, utc_naive_to_local
from google_calendar import GoogleCalendarClient, GoogleCalendarError
from datetime import date, time


class MiscRegressionTests(unittest.TestCase):
    def test_calendar_delete_treats_gone_tombstone_as_absent(self):
        class GoneError(Exception):
            resp = type("Response", (), {"status": 410})()

        class DeleteRequest:
            @staticmethod
            def execute(*, num_retries=0):
                self.assertEqual(3, num_retries)
                raise GoneError("deleted tombstone")

        class Events:
            @staticmethod
            def delete(**_kwargs):
                return DeleteRequest()

        class Service:
            @staticmethod
            def events():
                return Events()

        client = GoogleCalendarClient("token", "", datetime.now(timezone.utc))
        client._service = Service()
        self.assertIsNone(client.delete_event("primary", "already-deleted"))

    def test_rate_limit_is_per_user(self):
        limiter = RateLimiter(2, 60)
        limiter.check("u1")
        limiter.check("u1")
        with self.assertRaises(RateLimitExceeded):
            limiter.check("u1")
        limiter.check("u2")
        with self.assertRaises(ValueError):
            limiter.check(None)

    def test_timezone_conversion_uses_iana_rules(self):
        self.assertEqual(14, local_hour_from_utc_string("2026-01-01 12:00:00", "Africa/Cairo"))
        winter = utc_naive_to_local(datetime(2026, 1, 1, 12), "America/New_York")
        summer = utc_naive_to_local(datetime(2026, 7, 1, 12), "America/New_York")
        self.assertNotEqual(winter.utcoffset(), summer.utcoffset())
        self.assertEqual("UTC", safe_zoneinfo("Not/AZone").key)

    def test_dst_gap_is_rejected_but_normal_wall_time_is_valid(self):
        new_york = safe_zoneinfo("America/New_York")
        with self.assertRaises(GoogleCalendarError):
            GoogleCalendarClient._validated_local_datetime(
                date(2026, 3, 8), time(2, 30), new_york,
            )
        valid = GoogleCalendarClient._validated_local_datetime(
            date(2026, 3, 8), time(3, 30), new_york,
        )
        self.assertEqual((3, 30), (valid.hour, valid.minute))

    def test_timer_identity_remains_logical_task_with_optimistic_concurrency(self):
        tempdir = tempfile.TemporaryDirectory()
        try:
            path = Path(tempdir.name) / "test.db"
            db = Database(f"sqlite:///{path.as_posix()}")
            db.execute("INSERT INTO users (user_id,email,display_name) VALUES ('u','u@x.com','U')")
            db.execute("INSERT INTO plans (user_id,plan_date,raw_input) VALUES ('u','2026-09-30','x')")
            db.connection.commit()
            plan = db.fetch_one("SELECT plan_id FROM plans")["plan_id"]
            task = db.add_task(plan, "u", "Split", estimated_minutes=60)
            revision = db.get_plan_by_id(plan, "u")["schedule_revision"]
            db.replace_plan_segments(plan, "u", {task: [
                {"segment_index": 0, "scheduled_start": "09:00:00", "scheduled_end": "09:30:00"},
                {"segment_index": 1, "scheduled_start": "10:00:00", "scheduled_end": "10:30:00"},
            ]}, revision)
            version = db.set_task_timer_state(task, "u", 10, None, None, 0, expected_version=0)
            self.assertEqual(1, version)
            self.assertEqual(2, len(db.get_task_segments(task, "u")))
            self.assertEqual(10, db.get_task(task, "u")["timer_accumulated_seconds"])
            with self.assertRaises(TimerConflictError):
                db.set_task_timer_state(task, "u", 20, None, None, 0, expected_version=0)
            db.close()
        finally:
            tempdir.cleanup()


if __name__ == "__main__":
    unittest.main()
