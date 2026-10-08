"""Disposable rendered Streamlit workflows for Patch 5.2.1 (not discovery)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from datetime import time, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def button(app, text):
    return next(item for item in app.button if text in item.label)


def values(app, kind, label):
    return [item for item in getattr(app, kind) if item.label == label]


if "--child" not in sys.argv:
    with tempfile.TemporaryDirectory() as tmp:
        child_db = Path(tmp) / "patch521_ui.db"
        result = subprocess.run(
            [sys.executable, __file__, "--child", str(child_db)],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        raise SystemExit(result.returncode)

if len(sys.argv) != 3:
    raise SystemExit("Child verification requires one disposable database path.")

db_path = Path(sys.argv[2])
os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"

from database import Database
from timezone_utils import user_today
from planning_request import (
    PlanningInputMethod,
    PlanningScope,
    PlanningTaskRequest,
    StructuredPlanningRequest,
)

db = Database(os.environ["DATABASE_URL"])
db.execute(
    "INSERT INTO users (user_id,email,display_name,timezone) VALUES (?,?,?,?)",
    ("a", "a@test.local", "A", "Africa/Cairo"),
)
db.connection.commit()
planning_day = user_today(db, "a") + timedelta(days=1)
db.close()

from streamlit.testing.v1 import AppTest
import services

page = next((ROOT / "pages").glob("3_*Future_Planning.py"))
app = AppTest.from_file(str(page), default_timeout=20)
app.session_state["user_id"] = "a"
app.session_state["user_display_name"] = "A"
app.run()
assert not app.exception, app.exception

# A. Specific Day, zero existing tasks, draft -> Preview -> Apply -> Undo.
button(app, "Specific Day").click().run()
button(app, "Manual").click().run()
assert any("no existing tasks" in item.value.lower() for item in app.caption)
button(app, "Add new task").click().run()
values(app, "text_input", "Task name")[0].set_value("Machine Learning").run()
check = Database(os.environ["DATABASE_URL"])
assert check.fetch_one("SELECT COUNT(*) AS n FROM tasks")["n"] == 0
check.close()
button(app, "Preview plan").click().run()
assert not app.exception, app.exception
assert any("NEW" in item.value and "Machine Learning" in item.value for item in app.success)
check = Database(os.environ["DATABASE_URL"])
assert check.fetch_one("SELECT COUNT(*) AS n FROM tasks")["n"] == 0
check.close()
button(app, "Apply this preview").click().run()
assert not app.exception, app.exception
check = Database(os.environ["DATABASE_URL"])
assert check.fetch_one("SELECT COUNT(*) AS n FROM tasks")["n"] == 1
assert check.fetch_one("SELECT COUNT(*) AS n FROM task_segments")["n"] >= 1
check.close()
button(app, "Undo last future plan").click().run()
check = Database(os.environ["DATABASE_URL"])
assert check.fetch_one("SELECT COUNT(*) AS n FROM tasks")["n"] == 0
check.close()

# B. Date Range, zero existing tasks, two drafts -> Preview -> Apply -> Undo.
button(app, "Change scope").click().run()
button(app, "Date Range").click().run()
button(app, "Manual").click().run()
button(app, "Add new task").click().run()
values(app, "text_input", "Task name")[0].set_value("Deep Learning").run()
button(app, "Add new task").click().run()
draft_titles = [item.value for item in values(app, "text_input", "Task name")]
assert draft_titles == ["Deep Learning", ""], draft_titles
values(app, "text_input", "Task name")[1].set_value("Database").run()
assert [item.value for item in values(app, "text_input", "Task name")] == ["Deep Learning", "Database"]
button(app, "Preview plan").click().run()
assert not app.exception, app.exception
new_messages = [item.value for item in app.success if "NEW" in item.value]
assert len(new_messages) == 2, [item.value for item in app.success]
check = Database(os.environ["DATABASE_URL"])
assert check.fetch_one("SELECT COUNT(*) AS n FROM tasks")["n"] == 0
check.close()
button(app, "Apply this preview").click().run()
check = Database(os.environ["DATABASE_URL"])
assert check.fetch_one("SELECT COUNT(*) AS n FROM tasks")["n"] == 2
check.close()
button(app, "Undo last future plan").click().run()
check = Database(os.environ["DATABASE_URL"])
assert check.fetch_one("SELECT COUNT(*) AS n FROM tasks")["n"] == 0
check.close()

# E/F. Scope and input changes discard incompatible manual-draft state.
button(app, "Add new task").click().run()
assert len(values(app, "text_input", "Task name")) == 1
button(app, "Change input").click().run()
assert "future_manual_draft_ids" not in app.session_state
button(app, "Write").click().run()
assert len(values(app, "text_input", "Task name")) == 0

# C. Write mode can produce a new draft and Preview remains pure.
written_request = StructuredPlanningRequest(
    scope=PlanningScope.DATE_RANGE,
    input_method=PlanningInputMethod.WRITE,
    planning_start=planning_day,
    planning_end=planning_day + timedelta(days=6),
    work_day_start=time(8),
    tasks=[PlanningTaskRequest(
        temporary_id="draft:written", title="Written Draft",
        remaining_minutes=75, priority=2,
    )],
)
values(app, "text_area", "Describe the tasks and constraints")[0].set_value(
    "Plan a new 75 minute Written Draft task."
).run()
with patch.object(services, "understand_future_planning_text", return_value=written_request):
    button(app, "Understand request").click().run()
button(app, "Preview plan").click().run()
assert any("NEW" in item.value and "Written Draft" in item.value for item in app.success)
check = Database(os.environ["DATABASE_URL"])
assert check.fetch_one("SELECT COUNT(*) AS n FROM tasks")["n"] == 0
check.close()

# D. Existing and new draft are shown together in one manual Preview.
button(app, "Change input").click().run()
check = Database(os.environ["DATABASE_URL"])
existing_plan = check.get_plan_by_date("a", planning_day)
plan_id = int(existing_plan["plan_id"]) if existing_plan else check.create_plan(
    "a", planning_day, "mixed UI"
)
existing_id = check.add_task(plan_id, "a", "Existing SQL", estimated_minutes=60)
check.close()
button(app, "Manual").click().run()
app.multiselect[0].set_value([existing_id]).run()
button(app, "Add new task").click().run()
values(app, "text_input", "Task name")[0].set_value("New Operating Systems").run()
button(app, "Preview plan").click().run()
messages = [item.value for item in app.success]
assert any("EXISTING" in value and "Existing SQL" in value for value in messages)
assert any("NEW" in value and "New Operating Systems" in value for value in messages)

print("PATCH521_STREAMLIT_WORKFLOW_PASS")
