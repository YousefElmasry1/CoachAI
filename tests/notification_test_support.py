from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest

from database import Database
from notifications import NotificationRepository, NotificationService


class NotificationTestCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "notifications.db"
        self.db = Database(f"sqlite:///{self.db_path.as_posix()}")
        for user_id, timezone_name in (("u1", "Africa/Cairo"), ("u2", "UTC")):
            self.db.execute(
                "INSERT INTO users(user_id,email,display_name,timezone) VALUES (?,?,?,?)",
                (user_id, f"{user_id}@example.com", user_id, timezone_name),
            )
            self.db.execute(
                "INSERT INTO plans(user_id,plan_date,raw_input) VALUES (?,?,?)",
                (user_id, "2026-10-08", "notification test"),
            )
        self.db.connection.commit()
        self.plan1 = int(self.db.fetch_one(
            "SELECT plan_id FROM plans WHERE user_id='u1'"
        )["plan_id"])
        self.plan2 = int(self.db.fetch_one(
            "SELECT plan_id FROM plans WHERE user_id='u2'"
        )["plan_id"])
        self.repo = NotificationRepository(self.db)
        self.service = NotificationService(self.db)

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    def direct_task(self, user_id="u1", title="Task", minutes=60):
        plan_id = self.plan1 if user_id == "u1" else self.plan2
        task_id = int(self.db._insert_and_get_id(
            """INSERT INTO tasks(plan_id,title,estimated_minutes,priority)
               VALUES (?,?,?,3)""",
            (plan_id, title, minutes),
            pk_column="task_id",
        ))
        self.db.connection.commit()
        return task_id

    def revision(self, plan_id=None, user_id="u1"):
        return int(self.db.get_plan_by_id(plan_id or self.plan1, user_id)["schedule_revision"])

    @staticmethod
    def segment(start="09:00:00", end="10:00:00", index=0):
        return [{
            "segment_index": index,
            "scheduled_start": start,
            "scheduled_end": end,
            "scheduled_date": date(2026, 10, 8).isoformat(),
        }]
