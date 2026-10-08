"""Disposable Streamlit checks for optional Future Planning timing controls."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def button(app, text):
    return next(item for item in app.button if text in item.label)


def values(app, kind, label):
    return [item for item in getattr(app, kind) if item.label == label]


if "--child" not in sys.argv:
    with tempfile.TemporaryDirectory() as tmp:
        child_db = Path(tmp) / "future_timing_ui.db"
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
from streamlit.testing.v1 import AppTest


user_id = "future-timing-ui"
today = datetime.now(ZoneInfo("Africa/Cairo")).date()
selected_day = today + timedelta(days=1)
page = next((ROOT / "pages").glob("3_*Future_Planning.py"))

db = Database(os.environ["DATABASE_URL"])
db.execute(
    "INSERT INTO users (user_id,email,display_name,timezone) VALUES (?,?,?,?)",
    (user_id, "future-timing@test.local", "Future Timing", "Africa/Cairo"),
)
plan_id = db.create_plan(user_id, selected_day, "Stored constraint verification")
stored_deadline = datetime.combine(selected_day + timedelta(days=1), time(16, 30))
existing_task_id = db.add_task(
    plan_id,
    user_id,
    "Existing deadline task",
    estimated_minutes=60,
    deadline=stored_deadline,
)
db.close()


def open_manual(scope_label):
    app = AppTest.from_file(str(page), default_timeout=20)
    app.session_state["user_id"] = user_id
    app.session_state["user_display_name"] = "Future Timing"
    app.run()
    button(app, scope_label).click().run()
    button(app, "Manual").click().run()
    assert not app.exception, app.exception
    return app


# Date Range: no task deadline means no deadline widgets and deadline=None.
app = open_manual("Date Range")
button(app, "Add new task").click().run()
values(app, "text_input", "Task name")[0].set_value("No deadline draft").run()
assert not values(app, "date_input", "Deadline date")
assert not values(app, "time_input", "Deadline time")
button(app, "Preview plan").click().run()
request = app.session_state["future_planning_request"]
assert len(request.tasks) == 1
assert request.tasks[0].deadline is None


# Date Range: two drafts retain independent explicit deadlines.
app = open_manual("Date Range")
planning_start = values(app, "date_input", "Planning starts")[0].value
planning_end = planning_start + timedelta(days=4)
values(app, "date_input", "Planning ends")[0].set_value(planning_end).run()
button(app, "Add new task").click().run()
values(app, "text_input", "Task name")[0].set_value("Earlier deadline").run()
button(app, "Add new task").click().run()
values(app, "text_input", "Task name")[1].set_value("Later deadline").run()

# Date Range keeps its explicit earliest-start date and time controls.
values(app, "checkbox", "Set earliest start")[0].set_value(True).run()
assert len(values(app, "date_input", "Earliest date")) == 1
assert len(values(app, "time_input", "Earliest time")) == 1
values(app, "checkbox", "Set earliest start")[0].set_value(False).run()

deadline_toggles = values(app, "checkbox", "Add task-specific deadline")
assert len(deadline_toggles) == 2
deadline_toggles[0].set_value(True).run()
values(app, "checkbox", "Add task-specific deadline")[1].set_value(True).run()
assert [item.value for item in values(app, "date_input", "Deadline date")] == [None, None]
assert [item.value for item in values(app, "time_input", "Deadline time")] == [None, None]

first_deadline = datetime.combine(planning_start + timedelta(days=1), time(11, 0))
second_deadline = datetime.combine(planning_start + timedelta(days=3), time(15, 30))
values(app, "date_input", "Deadline date")[0].set_value(first_deadline.date()).run()
values(app, "time_input", "Deadline time")[0].set_value(first_deadline.time()).run()
values(app, "date_input", "Deadline date")[1].set_value(second_deadline.date()).run()
values(app, "time_input", "Deadline time")[1].set_value(second_deadline.time()).run()
button(app, "Preview plan").click().run()
request = app.session_state["future_planning_request"]
deadlines = {task.title: task.deadline for task in request.tasks}
assert deadlines == {
    "Earlier deadline": first_deadline,
    "Later deadline": second_deadline,
}


# Specific Day: optional constraints use times only and combine with the scope day.
app = open_manual("Specific Day")
scope_day = values(app, "date_input", "Select day")[0].value
button(app, "Add new task").click().run()
values(app, "text_input", "Task name")[0].set_value("Same-day constraints").run()
assert [item.label for item in app.date_input] == ["Select day"]
assert not values(app, "time_input", "Finish by")
values(app, "checkbox", "Set earliest start")[0].set_value(True).run()
values(app, "checkbox", "Add task-specific deadline")[0].set_value(True).run()
assert [item.label for item in app.date_input] == ["Select day"]
assert values(app, "time_input", "Start no earlier than")[0].value is None
assert values(app, "time_input", "Finish by")[0].value is None
values(app, "time_input", "Start no earlier than")[0].set_value(time(10, 15)).run()
values(app, "time_input", "Finish by")[0].set_value(time(17, 45)).run()
button(app, "Preview plan").click().run()
request = app.session_state["future_planning_request"]
assert request.tasks[0].earliest_start == datetime.combine(scope_day, time(10, 15))
assert request.tasks[0].deadline == datetime.combine(scope_day, time(17, 45))


# Specific Day: separate drafts do not share either optional timing state.
app = open_manual("Specific Day")
scope_day = values(app, "date_input", "Select day")[0].value
button(app, "Add new task").click().run()
values(app, "text_input", "Task name")[0].set_value("Independent earliest").run()
button(app, "Add new task").click().run()
values(app, "text_input", "Task name")[1].set_value("Independent deadline").run()
values(app, "checkbox", "Set earliest start")[0].set_value(True).run()
values(app, "checkbox", "Add task-specific deadline")[1].set_value(True).run()
assert [item.label for item in app.date_input] == ["Select day"]
assert len(values(app, "time_input", "Start no earlier than")) == 1
assert len(values(app, "time_input", "Finish by")) == 1
values(app, "time_input", "Start no earlier than")[0].set_value(time(9, 30)).run()
values(app, "time_input", "Finish by")[0].set_value(time(18, 0)).run()
button(app, "Preview plan").click().run()
request = app.session_state["future_planning_request"]
constraints = {
    task.title: (task.earliest_start, task.deadline)
    for task in request.tasks
}
assert constraints == {
    "Independent earliest": (datetime.combine(scope_day, time(9, 30)), None),
    "Independent deadline": (None, datetime.combine(scope_day, time(18, 0))),
}


# Existing stored deadlines remain populated and are not rewritten by rendering.
app = open_manual("Specific Day")
app.multiselect[0].set_value([existing_task_id]).run()
deadline_toggle = values(app, "checkbox", "Add task-specific deadline")[0]
assert deadline_toggle.value is True
assert values(app, "time_input", "Finish by")[0].value == stored_deadline.time()
assert any(stored_deadline.strftime("%b %d, %Y") in item.value for item in app.caption)
button(app, "Preview plan").click().run()
request = app.session_state["future_planning_request"]
assert request.tasks[0].deadline == stored_deadline

print("FUTURE_PLANNING_TIMING_STREAMLIT_PASS")
