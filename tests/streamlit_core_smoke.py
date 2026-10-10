"""Disposable Settings and Today's Schedule Streamlit smoke verification.

This script is intentionally outside unittest discovery. It launches child
AppTest runs against one explicit temporary SQLite database and never reads or
writes the repository's retained ``coach_ai.db`` file.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _button(app, text: str):
    return next(item for item in app.button if text in item.label)


if "--child" not in sys.argv:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "streamlit-core-smoke.db"
        result = subprocess.run(
            [sys.executable, __file__, "--child", str(db_path)],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        raise SystemExit(result.returncode)

if len(sys.argv) != 3:
    raise SystemExit("Child verification requires one disposable database path.")

db_path = Path(sys.argv[2]).resolve()
os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"

from database import Database
from timezone_utils import user_today


db = Database(os.environ["DATABASE_URL"])
db.create_user("streamlit-user", "streamlit@example.test", "Streamlit", "Africa/Cairo")
today = user_today(db, "streamlit-user")
plan_id = db.create_plan("streamlit-user", today, "streamlit smoke")
task_id = db.add_task(
    plan_id,
    "streamlit-user",
    "Schedule smoke task",
    estimated_minutes=45,
    priority=2,
)
db.close()

from streamlit.testing.v1 import AppTest


settings_page = next((ROOT / "pages").glob("5_*Settings.py"))
settings = AppTest.from_file(str(settings_page), default_timeout=30)
settings.session_state["user_id"] = "streamlit-user"
settings.session_state["user_display_name"] = "Streamlit"
settings.run()
assert not settings.exception, settings.exception
assert len(settings.tabs) == 6

name_input = next(item for item in settings.text_input if item.label == "Name")
name_input.set_value("Smoke Category").run()
_button(settings, "Add Category").click().run()
assert not settings.exception, settings.exception
check = Database(os.environ["DATABASE_URL"])
assert any(row["name"] == "Smoke Category" for row in check.get_categories("streamlit-user"))
check.close()

schedule_page = next((ROOT / "pages").glob("2_*Todays_Schedule.py"))
schedule = AppTest.from_file(str(schedule_page), default_timeout=30)
schedule.session_state["user_id"] = "streamlit-user"
schedule.session_state["user_display_name"] = "Streamlit"
schedule.run()
assert not schedule.exception, schedule.exception
_button(schedule, "Run Scheduler").click().run()
assert not schedule.exception, schedule.exception

check = Database(os.environ["DATABASE_URL"])
task = check.get_task(task_id, "streamlit-user")
segments = check.get_task_segments(task_id, "streamlit-user")
assert task is not None
assert segments, "Scheduler did not persist task segments."
assert check.fetch_one(
    "SELECT COUNT(*) AS n FROM calendar_operations WHERE user_id=?",
    ("streamlit-user",),
)["n"] >= 1
check.close()

print("STREAMLIT_SETTINGS_SCHEDULING_PASS")
