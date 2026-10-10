import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import chatbot_service
import services
from analytics import TaskSnapshot, build_intermediate_stats
from database import Database
from recommendation_service import RecommendationService


class DownstreamSegmentContractTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        path = Path(self.tempdir.name) / "contracts.db"
        self.db = Database(f"sqlite:///{path.as_posix()}")
        self.db.execute("INSERT INTO users (user_id,email,display_name) VALUES ('u','u@x.com','U')")
        self.db.execute("INSERT INTO plans (user_id,plan_date,raw_input) VALUES ('u','2026-09-30','x')")
        self.db.connection.commit()
        self.plan = self.db.fetch_one("SELECT plan_id FROM plans")["plan_id"]
        self.task = self.db.add_task(self.plan, "u", "Split task", estimated_minutes=60)
        revision = self.db.get_plan_by_id(self.plan, "u")["schedule_revision"]
        self.db.replace_plan_segments(self.plan, "u", {self.task: [
            {"segment_index": 0, "scheduled_date": "2026-09-30",
             "scheduled_start": "09:00:00", "scheduled_end": "09:30:00"},
            {"segment_index": 1, "scheduled_date": "2026-10-01",
             "scheduled_start": "10:00:00", "scheduled_end": "10:30:00"},
        ]}, revision)

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    def test_history_shape_keeps_one_task_with_all_windows(self):
        with patch.object(services, "get_database", return_value=self.db), patch.object(
            services, "user_today", return_value=date(2026, 9, 30)
        ):
            plans = services.load_recent_plans("u", days=1)
        self.assertEqual(1, len(plans))
        self.assertEqual(1, len(plans[0]["tasks"]))
        task = plans[0]["tasks"][0]
        self.assertEqual(2, len(task["segments"]))
        self.assertEqual("09:00:00", str(task["scheduled_start"]))
        self.assertEqual("10:30:00", str(task["scheduled_end"]))

        summary = services.summarize_plan_history(plans[0])
        self.assertEqual(60, summary["logical_estimated_minutes"])
        self.assertEqual(30, summary["anchor_scheduled_minutes"])
        self.assertEqual(
            {"2026-09-30": 30, "2026-10-01": 30},
            summary["scheduled_minutes_by_date"],
        )
        self.assertEqual(
            ["2026-09-30", "2026-10-01"],
            [str(segment["effective_date"]) for segment in task["segments"]],
        )

    def test_history_physical_occupancy_merges_overlapping_intervals(self):
        plan = {
            "plan_date": "2026-09-30",
            "tasks": [
                {"estimated_minutes": 60, "segments": [
                    {"effective_date": "2026-09-30", "scheduled_start": "09:00", "scheduled_end": "10:00"},
                ]},
                {"estimated_minutes": 60, "segments": [
                    {"effective_date": "2026-09-30", "scheduled_start": "09:30", "scheduled_end": "10:30"},
                ]},
            ],
        }
        summary = services.summarize_plan_history(plan)
        self.assertEqual(120, summary["logical_estimated_minutes"])
        self.assertEqual(90, summary["anchor_scheduled_minutes"])

    def test_chatbot_shape_keeps_one_logical_task_and_total_once(self):
        task = self.db.get_tasks_with_segments(self.plan, "u")[0]
        with patch.object(services, "load_today_plan", return_value={"plan_date": "2026-09-30"}), patch.object(
            services, "load_today_tasks", return_value=[task]
        ), patch.object(services, "load_categories", return_value=[]):
            result = chatbot_service._impl_get_today_tasks("u")
        self.assertEqual(1, len(result["tasks"]))
        self.assertEqual(2, len(result["tasks"][0]["segments"]))
        self.assertEqual(60, result["total_planned_minutes"])

    def test_chatbot_core_tool_uses_explicit_database_without_streamlit_services(self):
        with patch.object(
            services, "get_database", side_effect=AssertionError("legacy DB used")
        ), patch.object(
            self.db,
            "get_today_plan",
            return_value={"plan_id": self.plan, "plan_date": "2026-09-30"},
        ):
            result = chatbot_service._impl_get_today_tasks("u", self.db)
        self.assertEqual(1, len(result["tasks"]))
        self.assertEqual(2, len(result["tasks"][0]["segments"]))

    def test_recommendation_prompt_has_one_line_and_all_windows(self):
        task = self.db.get_tasks_with_segments(self.plan, "u")[0]
        text = RecommendationService(self.db)._format_schedule_text([task])
        self.assertEqual(1, len(text.splitlines()))
        self.assertIn("09:00:00–09:30:00", text)
        self.assertIn("10:00:00–10:30:00", text)
        self.assertEqual(1, text.count("60 min total"))

    def test_best_hour_groups_split_task_once(self):
        snapshot = TaskSnapshot(
            task_id=self.task, plan_date="2026-09-30", title="Split task",
            category_name="Study", priority=3, estimated_minutes=60,
            actual_minutes=55, scheduled_start="09:00:00", scheduled_end="10:30:00",
            status="completed", failure_reason=None, completed_at=None, created_at="",
            weekday=2, hour=9,
            segments=(("09:00:00", "09:30:00"), ("10:00:00", "10:30:00")),
        )
        stats = build_intermediate_stats([snapshot], today=date(2026, 9, 30))
        self.assertEqual([snapshot], stats.tasks_by_hour[9])
        self.assertNotIn(10, stats.tasks_by_hour)


if __name__ == "__main__":
    unittest.main()
