"""Launch Streamlit on a disposable database and require an HTTP 200 response."""

from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import Database


def _unused_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


with tempfile.TemporaryDirectory() as tmp:
    db_path = Path(tmp) / "streamlit-http-smoke.db"
    db_url = f"sqlite:///{db_path.as_posix()}"
    db = Database(db_url)
    db.create_user("1", "http-smoke@example.test", "HTTP Smoke", "UTC")
    db.close()

    port = _unused_loopback_port()
    environment = os.environ.copy()
    environment["DATABASE_URL"] = db_url
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(ROOT / "app.py"),
            "--server.headless=true",
            f"--server.port={port}",
            "--server.address=127.0.0.1",
            "--browser.gatherUsageStats=false",
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=creation_flags,
    )
    try:
        deadline = time.monotonic() + 45
        status = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                with urlopen(f"http://127.0.0.1:{port}/", timeout=2) as response:
                    status = int(response.status)
                    if status == 200:
                        break
            except (URLError, TimeoutError):
                time.sleep(0.25)
        if status != 200:
            process.terminate()
            output, _ = process.communicate(timeout=10)
            raise AssertionError(
                f"Streamlit did not return HTTP 200 (status={status}).\n{output[-4000:]}"
            )
        print(f"STREAMLIT_HTTP_200_PASS port={port}")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
