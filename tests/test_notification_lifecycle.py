from notifications import NotificationService, RiskState

from tests.notification_test_support import NotificationTestCase


class NotificationLifecycleTests(NotificationTestCase):
    def event_types(self):
        return [row["event_type"] for row in self.repo.list_outbox("u1")]

    def test_create_start_complete_and_delete_emit_domain_events(self):
        task_id = self.db.add_task(self.plan1, "u1", "Lifecycle", estimated_minutes=20)
        self.db.update_task_status(task_id, "u1", "in_progress")
        self.db.update_task_status(task_id, "u1", "completed", actual_minutes=18)
        self.db.delete_task(task_id, "u1")
        self.assertEqual(
            ["TASK_CREATED", "TASK_STARTED", "TASK_COMPLETED", "TASK_DELETED"],
            self.event_types(),
        )

    def test_general_task_update_emits_revision_aware_event(self):
        task_id = self.direct_task(title="Before")
        self.db.update_task(task_id, "u1", title="After", priority=1)
        row = self.repo.list_outbox("u1")[0]
        self.assertEqual("TASK_UPDATED", row["event_type"])
        self.assertEqual(self.revision(), row["schedule_revision"])
        self.assertIn('"changed_fields": ["priority", "title"]', row["event_payload"])

    def test_repeating_same_status_does_not_duplicate_event(self):
        task_id = self.direct_task()
        self.db.update_task_status(task_id, "u1", "in_progress")
        self.db.update_task_status(task_id, "u1", "in_progress")
        self.assertEqual(["TASK_STARTED"], self.event_types())

    def test_failed_task_event_preserves_reason(self):
        task_id = self.direct_task(title="Hard task")
        self.db.update_task_status(
            task_id, "u1", "failed", failure_reason="Unexpected event", actual_minutes=7,
        )
        row = self.repo.list_outbox("u1")[0]
        self.assertEqual("TASK_FAILED", row["event_type"])
        self.assertIn('"failure_reason": "Unexpected event"', row["event_payload"])

    def test_defer_is_distinct_and_revision_aware(self):
        task_id = self.direct_task()
        self.db.replace_plan_segments(
            self.plan1, "u1", {task_id: self.segment()}, self.revision(),
        )
        self.db.execute(
            "INSERT INTO plans(user_id,plan_date,raw_input) VALUES ('u1','2026-10-09','next')"
        )
        self.db.connection.commit()
        destination = int(self.db.fetch_one(
            "SELECT plan_id FROM plans WHERE user_id='u1' AND plan_date='2026-10-09'"
        )["plan_id"])
        self.db.move_task_unscheduled(task_id, "u1", destination, 0)
        row = [r for r in self.repo.list_outbox("u1") if r["event_type"] == "TASK_DEFERRED"][0]
        self.assertEqual(task_id, row["task_id"])
        self.assertEqual("2026-10-09", str(row["scheduled_date"]))
        self.assertGreater(int(row["schedule_revision"]), 0)

    def test_deadline_risk_only_emits_on_state_transition(self):
        task_id = self.direct_task(title="Deadline task")
        self.assertTrue(self.service.transition_deadline_risk("u1", task_id, RiskState.AT_RISK))
        self.assertFalse(self.service.transition_deadline_risk("u1", task_id, RiskState.AT_RISK))
        self.assertTrue(self.service.transition_deadline_risk("u1", task_id, RiskState.CRITICAL))
        self.assertEqual(
            ["DEADLINE_RISK_CHANGED", "DEADLINE_RISK_CHANGED"],
            self.event_types(),
        )
        state = self.repo.get_task_notification_state("u1", task_id)
        self.assertEqual("CRITICAL", state["state"])

    def test_risk_can_reenter_a_previous_state_without_id_collision(self):
        task_id = self.direct_task()
        for state in (RiskState.AT_RISK, RiskState.SAFE, RiskState.AT_RISK):
            self.assertTrue(self.service.transition_deadline_risk("u1", task_id, state))
        self.assertEqual(3, len(self.repo.list_outbox("u1")))

    def test_calendar_error_and_reconciliation_emit_state_events(self):
        task_id = self.direct_task()
        self.db.replace_plan_segments(
            self.plan1, "u1", {task_id: self.segment()}, self.revision(),
        )
        segment = self.db.get_task_segments(task_id, "u1")[0]
        self.db.update_segment_calendar_state(
            int(segment["segment_id"]), "u1",
            calendar_sync_status="update_error", calendar_sync_error="network",
        )
        self.db.update_segment_calendar_state(
            int(segment["segment_id"]), "u1",
            calendar_sync_status="synced", calendar_sync_error="",
        )
        types = self.event_types()
        self.assertIn("CALENDAR_ATTENTION_REQUIRED", types)
        self.assertIn("CALENDAR_RECONCILED", types)

    def test_operation_calendar_failure_and_retry_are_durable(self):
        task_id = self.direct_task()
        operation_id = self.db.replace_plan_segments(
            self.plan1, "u1", {task_id: self.segment()}, self.revision(),
        )
        self.db.mark_schedule_calendar_reconciliation(operation_id, "u1", "quota")
        self.db.mark_schedule_calendar_reconciliation(operation_id, "u1", None)
        operation = self.db.get_schedule_operation(operation_id, "u1")
        self.assertIsNone(operation["calendar_reconciliation_error"])
        types = self.event_types()
        self.assertIn("CALENDAR_ATTENTION_REQUIRED", types)
        self.assertIn("CALENDAR_RECONCILED", types)


if __name__ == "__main__":
    import unittest
    unittest.main()
