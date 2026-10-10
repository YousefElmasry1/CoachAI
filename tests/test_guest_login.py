import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import services
from database import Database


def _database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _replace_user_tables_with_historical_schema(path: Path) -> None:
    """Model the protected live DB's original integer-identity schema."""
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        profile_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='user_profiles'"
        ).fetchone()[0]
        profile_sql = profile_sql.replace(
            "CREATE TABLE user_profiles", "CREATE TABLE user_profiles_legacy", 1
        ).replace("user_id TEXT", "user_id INTEGER", 1)

        connection.execute(profile_sql)
        connection.execute("DROP TABLE user_profiles")
        connection.execute("ALTER TABLE user_profiles_legacy RENAME TO user_profiles")
        connection.execute("DROP TABLE users")
        connection.execute(
            """
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                display_name TEXT NOT NULL,
                timezone TEXT NOT NULL DEFAULT 'UTC',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.commit()
    finally:
        connection.close()


class HistoricalGuestLoginTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "historical_guest.db"
        initial = Database(_database_url(self.db_path))
        initial.close()
        _replace_user_tables_with_historical_schema(self.db_path)
        self.db = Database(_database_url(self.db_path))

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    def test_guest_creation_succeeds_and_initializes_profile(self):
        with patch("services.get_database", return_value=self.db):
            user_id = services.create_guest_user("mohamed")

        self.assertIsInstance(user_id, int)
        user = self.db.get_user(user_id)
        profile = self.db.get_profile(user_id)
        self.assertEqual(user["display_name"], "mohamed")
        self.assertTrue(user["email"].endswith("@guest.local"))
        self.assertEqual(user["password_hash"], "!")
        self.assertIsNotNone(profile)
        self.assertEqual(profile["user_id"], user_id)

    def test_second_guest_gets_a_distinct_isolated_identity(self):
        with patch("services.get_database", return_value=self.db):
            first = services.create_guest_user("mohamed")
            second = services.create_guest_user("mohamed")

        self.assertNotEqual(first, second)
        self.assertEqual(
            self.db.fetch_one("SELECT COUNT(*) AS count FROM users")["count"], 2
        )
        self.assertEqual(
            self.db.fetch_one("SELECT COUNT(*) AS count FROM user_profiles")["count"], 2
        )

    def test_profile_initialization_failure_rolls_back_guest_atomically(self):
        original_execute = self.db.execute

        def fail_profile_insert(sql, parameters=()):
            if "INSERT INTO user_profiles" in sql:
                raise RuntimeError("simulated profile initialization failure")
            return original_execute(sql, parameters)

        with (
            patch("services.get_database", return_value=self.db),
            patch.object(self.db, "execute", side_effect=fail_profile_insert),
            self.assertLogs("services", level="WARNING") as logs,
        ):
            user_id = services.create_guest_user("mohamed")

        self.assertIsNone(user_id)
        self.assertIn("guest_session_creation_failed", "\n".join(logs.output))
        self.assertEqual(
            self.db.fetch_one("SELECT COUNT(*) AS count FROM users")["count"], 0
        )
        self.assertEqual(
            self.db.fetch_one("SELECT COUNT(*) AS count FROM user_profiles")["count"], 0
        )

    def test_registered_signup_and_login_still_work(self):
        with patch("services.get_database", return_value=self.db):
            user_id, error = services.sign_up(
                "registered@example.com", "correct-horse", "Registered User"
            )
            logged_in_id, display_name = services.log_in(
                "registered@example.com", "correct-horse"
            )

        self.assertIsNone(error)
        self.assertEqual(logged_in_id, user_id)
        self.assertEqual(display_name, "Registered User")
        self.assertIsNotNone(self.db.get_profile(user_id))


class FreshGuestLoginTests(unittest.TestCase):
    def test_fresh_schema_keeps_text_identity_and_creates_profile(self):
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "fresh_guest.db"
            db = Database(_database_url(db_path))
            try:
                with patch("services.get_database", return_value=db):
                    user_id = services.create_guest_user("mohamed")

                self.assertIsInstance(user_id, str)
                self.assertTrue(user_id.startswith("guest_"))
                self.assertIsNotNone(db.get_user(user_id))
                self.assertIsNotNone(db.get_profile(user_id))
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
