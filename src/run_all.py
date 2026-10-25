"""Run the full analysis pipeline in the correct order.

Usage (from the project root):

    python src/run_all.py

Each step is a separate script so it can also be run on its own. This runner
stops on the first failure and prints which stage failed, because a downstream
step on stale inputs is worse than no output at all.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _named_query_count() -> int:
    """Count the named queries across the analysis and validation SQL files.

    The step labels below are derived rather than hard-coded, so they cannot
    drift away from what the project actually contains.
    """
    total = 0
    for name in ("sql/04_analysis_queries.sql", "sql/05_data_quality.sql"):
        path = PROJECT_ROOT / name
        if path.exists():
            total += len(re.findall(r"^-- name:", path.read_text(encoding="utf-8"), re.M))
    return total


def _figure_count() -> int:
    """Count the figures `src/make_figures.py` renders.

    Counted from the script's own `_save(...)` calls, so the label is correct
    on a fresh clone where `reports/figures/` does not exist yet.
    """
    path = PROJECT_ROOT / "src" / "make_figures.py"
    if not path.exists():
        return 0
    return len(re.findall(r"_save\(\s*fig", path.read_text(encoding="utf-8")))


STEPS = [
    ("generate the synthetic dataset", "src/generate_data.py"),
    ("build the SQLite database", "src/build_db.py"),
    (
        f"run the {_named_query_count()} SQL analysis and validation queries",
        "src/run_queries.py",
    ),
    ("render the data-quality report", "src/make_quality_report.py"),
    (f"render the {_figure_count()} report figures", "src/make_figures.py"),
    ("audit the dashboard and document contracts", "src/audit_contracts.py"),
    ("run the automated test suite", None),  # pytest, handled specially
]


def main() -> int:
    print(f"Project root: {PROJECT_ROOT}\n")
    started = time.perf_counter()

    for i, (label, script) in enumerate(STEPS, start=1):
        if script is None:
            print(f"[{i}/{len(STEPS)}] {label}")
            command = [sys.executable, "-m", "pytest", "-q"]
        else:
            path = PROJECT_ROOT / script
            if not path.exists():
                print(f"[{i}/{len(STEPS)}] MISSING {script}")
                return 1
            print(f"[{i}/{len(STEPS)}] {label}")
            command = [sys.executable, str(path)]

        step_start = time.perf_counter()
        result = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
        elapsed = time.perf_counter() - step_start

        if result.returncode != 0:
            print(f"    FAILED after {elapsed:.1f}s — stopping.")
            return result.returncode

        print(f"    ok in {elapsed:.1f}s\n")

    total = time.perf_counter() - started
    print(f"Pipeline complete in {total:.1f}s.")
    print("Outputs: data/raw/, shopify_analysis.db, reports/, dashboard/ is ready to run:")
    print("    streamlit run dashboard/app.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
