"""Headless smoke test for the Streamlit dashboard.

Boots the real Streamlit server, waits for it to answer, requests the app
page and the health endpoint, then shuts it down. This proves the application
*starts*, which a unit test on the KPI functions cannot do.

Usage:  python tests/smoke_dashboard.py
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PORT = 8599


def _free_port(preferred: int) -> int:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


def main() -> int:
    port = _free_port(PORT)
    env = {**os.environ, "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false"}

    process = subprocess.Popen(
        [
            sys.executable, "-m", "streamlit", "run",
            str(PROJECT_ROOT / "dashboard" / "app.py"),
            "--server.port", str(port),
            "--server.headless", "true",
            "--server.address", "127.0.0.1",
            "--browser.gatherUsageStats", "false",
        ],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 120
    healthy = False
    try:
        while time.time() < deadline:
            if process.poll() is not None:
                print("streamlit exited early:")
                print(process.stdout.read() if process.stdout else "")
                return 1
            try:
                with urllib.request.urlopen(f"{base}/_stcore/health", timeout=5) as resp:
                    if resp.status == 200:
                        healthy = True
                        break
            except (urllib.error.URLError, ConnectionError, OSError):
                time.sleep(1.5)
            except Exception:  # noqa: BLE001 - any connection hiccup is retried
                time.sleep(1.5)

        if not healthy:
            print("streamlit did not become healthy within 120s")
            return 1

        with urllib.request.urlopen(base, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            status = resp.status

        print(f"app URL           : {base}")
        print(f"HTTP status       : {status}")
        print(f"response bytes    : {len(body):,}")

        if status != 200:
            print("FAIL: the app did not return HTTP 200")
            return 1
        if "streamlit" not in body.lower():
            print("FAIL: response does not look like a Streamlit page")
            return 1

        print("PASS: dashboard starts and serves the app page")
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
