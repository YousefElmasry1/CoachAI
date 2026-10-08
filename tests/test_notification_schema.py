from pathlib import Path

from database import Database

from tests.notification_test_support import NotificationTestCase


EXPECTED_TABLES = {
    "notification_preferences",
    "notification_type_preferences",
    "notification_devices",
    "notification_outbox",
    "notifications",
    "notification_deliveries",
    "notification_local_schedules",
    "notification_sync_state",
    "task_notification_state",
}


class NotificationSchemaTests(NotificationTestCase):
    def test_fresh_sqlite_contains_all_notification_tables(self):
        found = {
            row["name"] for row in self.db.fetch_all(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertTrue(EXPECTED_TABLES <= found)

    def test_startup_schema_is_idempotent(self):
        self.db.close()
        self.db = Database(f"sqlite:///{self.db_path.as_posix()}")
        self.db.close()
        self.db = Database(f"sqlite:///{self.db_path.as_posix()}")
        count = self.db.fetch_one(
            "SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table' AND name LIKE 'notification%'"
        )["n"]
        self.assertEqual(8, count)

    def test_additive_self_heal_does_not_backfill_history(self):
        task_id = self.direct_task()
        for table in (
            "notification_local_schedules", "notification_deliveries", "notifications", "notification_outbox",
            "task_notification_state", "notification_sync_state", "notification_devices",
            "notification_type_preferences", "notification_preferences",
        ):
            self.db.execute(f"DROP TABLE {table}")
        self.db.connection.commit()
        self.db.close()
        self.db = Database(f"sqlite:///{self.db_path.as_posix()}")
        self.repo.db = self.db
        self.service = self.service.__class__(self.db)
        self.assertIsNotNone(self.db.get_task(task_id, "u1"))
        self.assertEqual(0, self.db.fetch_one("SELECT COUNT(*) AS n FROM notification_outbox")["n"])
        self.assertEqual(0, self.db.fetch_one("SELECT COUNT(*) AS n FROM notifications")["n"])

    def test_patch1_schema_adds_runtime_columns_and_table_idempotently(self):
        self.db.execute("DROP TABLE notification_local_schedules")
        self.db.execute("DROP INDEX idx_notification_outbox_lease")
        self.db.execute("DROP INDEX idx_notifications_sync")
        self.db.execute("DROP INDEX idx_notification_deliveries_push_ready")
        self.db.execute("DROP INDEX idx_notification_deliveries_lease")
        self.db.execute("ALTER TABLE notification_outbox DROP COLUMN locked_by")
        self.db.execute("ALTER TABLE notification_outbox DROP COLUMN expires_at")
        self.db.execute("ALTER TABLE notifications DROP COLUMN sync_revision")
        self.db.execute("ALTER TABLE notification_devices DROP COLUMN app_identifier")
        self.db.execute("ALTER TABLE notification_devices DROP COLUMN provider_metadata")
        for column in (
            "provider", "next_attempt_at", "last_attempt_at", "accepted_at",
            "locked_at", "locked_by", "error_classification",
        ):
            self.db.execute(f"ALTER TABLE notification_deliveries DROP COLUMN {column}")
        self.db.connection.commit()
        self.db.close()
        self.db = Database(f"sqlite:///{self.db_path.as_posix()}")
        self.repo.db = self.db
        self.service = self.service.__class__(self.db)
        tables = {row["name"] for row in self.db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        self.assertIn("notification_local_schedules", tables)
        outbox_columns = {row["name"] for row in self.db.fetch_all(
            "PRAGMA table_info(notification_outbox)"
        )}
        notification_columns = {row["name"] for row in self.db.fetch_all(
            "PRAGMA table_info(notifications)"
        )}
        device_columns = {row["name"] for row in self.db.fetch_all(
            "PRAGMA table_info(notification_devices)"
        )}
        delivery_columns = {row["name"] for row in self.db.fetch_all(
            "PRAGMA table_info(notification_deliveries)"
        )}
        self.assertTrue({"locked_by", "expires_at"} <= outbox_columns)
        self.assertIn("sync_revision", notification_columns)
        self.assertTrue({"app_identifier", "provider_metadata"} <= device_columns)
        self.assertTrue({
            "provider", "next_attempt_at", "last_attempt_at", "accepted_at",
            "locked_at", "locked_by", "error_classification",
        } <= delivery_columns)

    def test_schema_sql_declares_the_same_tables(self):
        text = (Path(__file__).parents[1] / "schema.sql").read_text(encoding="utf-8")
        for table in EXPECTED_TABLES:
            self.assertIn(f"CREATE TABLE {table}", text)


if __name__ == "__main__":
    import unittest
    unittest.main()
