"""Disposable real Streamlit workflow check for Patch 5.2 (not test discovery)."""

from __future__ import annotations

import os
import sys
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def button(app, label):
    return next(item for item in app.button if item.label == label)


if "--child" not in sys.argv:
    with tempfile.TemporaryDirectory() as tmp:
        child_db = Path(tmp) / "patch52_ui.db"
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

db = Database(os.environ["DATABASE_URL"])
db.execute(
    "INSERT INTO users (user_id,email,display_name,timezone) VALUES (?,?,?,?)",
    ("a", "a@test.local", "A", "Africa/Cairo"),
)
plan = db.create_plan("a", user_today(db, "a") + timedelta(days=1), "UI verification")
task_id = db.add_task(plan, "a", "UI Planning Task", estimated_minutes=60, priority=3)
db.close()

from streamlit.testing.v1 import AppTest

page = ROOT / "pages" / "3_🗓️_Future_Planning.py"
app = AppTest.from_file(str(page), default_timeout=20)
app.session_state["user_id"] = "a"
app.session_state["user_display_name"] = "A"
app.run()

assert not app.exception, app.exception
assert any(item.label == "🗓️ Date Range" for item in app.button)
assert any(item.label == "📅 Specific Day" for item in app.button)
assert len(app.date_input) == 0, "Dates must not appear before scope selection."

button(app, "🗓️ Date Range").click().run()
assert not app.exception, app.exception
assert {item.label for item in app.date_input} == {"Planning starts", "Planning ends"}
assert all(any(item.label == label for item in app.button) for label in ("✍️ Write", "🎙️ Voice", "⚙️ Manual"))

button(app, "⚙️ Manual").click().run()
assert not app.exception, app.exception
app.multiselect[0].set_value([task_id]).run()
assert not app.exception, app.exception
button(app, "Preview plan").click().run()
assert not app.exception, app.exception
assert any("Preview" in item.value for item in app.subheader)
assert len(app.success) >= 1
button(app, "Apply this preview").click().run()
assert not app.exception, app.exception

check = Database(os.environ["DATABASE_URL"])
assert check.get_task_segments(task_id, "a")
check.close()

button(app, "Undo last future plan").click().run()
assert not app.exception, app.exception
check = Database(os.environ["DATABASE_URL"])
assert check.get_task_segments(task_id, "a") == []
check.close()

button(app, "Change scope").click().run()
button(app, "📅 Specific Day").click().run()
assert not app.exception, app.exception
assert [item.label for item in app.date_input] == ["Select day"]
assert all(any(item.label == label for item in app.button) for label in ("✍️ Write", "🎙️ Voice", "⚙️ Manual"))

button(app, "✍️ Write").click().run()
assert not app.exception, app.exception
assert any(item.label == "Describe the tasks and constraints" for item in app.text_area)
assert any(item.label == "Understand request" for item in app.button)

button(app, "Change input").click().run()
button(app, "🎙️ Voice").click().run()
assert not app.exception, app.exception
assert len(app.get("audio_input")) == 1
assert any(item.label == "Review the transcript" for item in app.text_area)
assert any(item.label == "Understand request" for item in app.button)

print("PATCH52_STREAMLIT_WORKFLOW_PASS")
