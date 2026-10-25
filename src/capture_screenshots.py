"""Capture real screenshots of the running dashboard.

This is an optional step, not part of `run_all.py`, because it needs a
headless browser:

    pip install playwright && python -m playwright install chromium
    python src/capture_screenshots.py

The images are written to `reports/screenshots/` and are what the README
embeds. They are produced by driving the actual Streamlit app in Chromium, so
they cannot be mock-ups.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SHOTS_DIR = PROJECT_ROOT / "reports" / "screenshots"

TABS = [
    ("Retention opportunity", "01_retention_opportunity.png"),
    ("Revenue", "02_revenue.png"),
    ("Products", "03_products.png"),
    ("Customers", "04_customers.png"),
    ("Data quality", "05_data_quality.png"),
    ("Methodology", "06_methodology.png"),
]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed: pip install playwright")
        return 1

    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    server = subprocess.Popen(
        [
            sys.executable, "-m", "streamlit", "run",
            str(PROJECT_ROOT / "dashboard" / "app.py"),
            "--server.port", str(port),
            "--server.headless", "true",
            "--server.address", "127.0.0.1",
            "--browser.gatherUsageStats", "false",
        ],
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        deadline = time.time() + 120
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{base}/_stcore/health", timeout=5):
                    break
            except (urllib.error.URLError, OSError):
                time.sleep(1.5)
        else:
            print("streamlit did not start in time")
            return 1

        SHOTS_DIR.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1600, "height": 1100})
            page.goto(base, wait_until="networkidle", timeout=120_000)
            # The KPI band renders first; wait for the tab strip to exist.
            # Streamlit renders tabs with role="tab"; the older
            # `button[data-baseweb="tab"]` markup is not used in 1.66.
            page.wait_for_selector("[role='tab']", timeout=120_000)
            page.wait_for_timeout(2500)

            # Fail loudly if the app itself raised an exception.
            error = page.locator("[data-testid='stException']")
            if error.count() > 0:
                print("the app raised an exception; screenshotting it instead")
                page.screenshot(path=str(SHOTS_DIR / "error.png"), full_page=True)
                browser.close()
                return 1

            page.screenshot(path=str(SHOTS_DIR / "00_dashboard_header.png"))
            print("  wrote 00_dashboard_header.png")

            for label, filename in TABS:
                tab = page.get_by_role("tab", name=label)
                if tab.count() == 0:
                    print(f"  SKIP {label}: tab not found")
                    continue
                tab.first.click()
                page.wait_for_timeout(2500)

                # Grow the viewport to the page height and take a normal
                # viewport screenshot. A `full_page` screenshot resizes the
                # viewport internally, which makes Plotly re-lay-out and
                # capture blank canvases.
                height = page.evaluate(
                    """
                    () => {
                        const root = document.querySelector(
                            '[data-testid="stAppViewContainer"]'
                        ) || document.querySelector('.main')
                        || document.documentElement;
                        const inner = root ? root.scrollHeight : 0;
                        return Math.max(
                            inner,
                            document.documentElement.scrollHeight,
                            window.innerHeight,
                        );
                    }
                    """
                )
                page.set_viewport_size(
                    {"width": 1600, "height": min(max(int(height), 900), 3600)}
                )
                page.wait_for_timeout(2500)

                page.screenshot(path=str(SHOTS_DIR / filename))
                print(f"  wrote {filename}")

            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=20)
        except subprocess.TimeoutExpired:
            server.kill()

    print(f"\nscreenshots in {SHOTS_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
