from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from unit_of_work import DatabaseFactory, UnitOfWork


class UnitOfWorkTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        path = Path(self.tempdir.name) / "uow.db"
        self.factory = DatabaseFactory(f"sqlite:///{path.as_posix()}")

    def tearDown(self):
        self.factory.close()
        self.tempdir.cleanup()

    def test_connections_are_short_lived_but_pool_is_reused(self):
        engine = self.factory.engine
        with UnitOfWork(self.factory) as first:
            self.assertIs(engine, first.db.engine)
            first.db.execute(
                "INSERT INTO users(user_id,email,display_name) VALUES (?,?,?)",
                ("u1", "u1@example.com", "u1"),
            )
            first.db.commit()
        with UnitOfWork(self.factory) as second:
            self.assertIs(engine, second.db.engine)
            self.assertIsNotNone(second.db.get_user("u1"))
        self.assertIn("status", self.factory.pool_metrics())

    def test_uncommitted_work_is_rolled_back_on_release(self):
        with UnitOfWork(self.factory) as uow:
            uow.db.execute(
                "INSERT INTO users(user_id,email,display_name) VALUES (?,?,?)",
                ("rolled-back", "rollback@example.com", "Rollback"),
            )
        with UnitOfWork(self.factory) as verify:
            self.assertIsNone(verify.db.get_user("rolled-back"))

    def test_transaction_rolls_back_on_failure(self):
        with self.assertRaises(RuntimeError):
            with UnitOfWork(self.factory) as uow:
                with uow.transaction():
                    uow.db.execute(
                        "INSERT INTO users(user_id,email,display_name) VALUES (?,?,?)",
                        ("u2", "u2@example.com", "u2"),
                    )
                    raise RuntimeError("injected")
        with UnitOfWork(self.factory) as verify:
            self.assertIsNone(verify.db.get_user("u2"))


if __name__ == "__main__":
    unittest.main()
