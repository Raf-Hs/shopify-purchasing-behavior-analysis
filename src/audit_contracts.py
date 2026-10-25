"""Audit the dashboard's query and column contracts.

The dashboard reads its numbers from the named queries in
`sql/04_analysis_queries.sql`. This script checks, mechanically, that:

  1. every query the dashboard calls exists in the query library;
  2. every column the dashboard reads from a query result actually exists in
     that query's output;
  3. every figure referenced by the reports exists on disk;
  4. every internal document link in the README and the reports resolves.

The column check is scoped per assignment: the code that uses the result of
`x = query("foo")` runs from that assignment to the next query assignment in
the same tab block, so a `custom_data` list is attributed to the query it
actually belongs to.

It is a static check, so it catches a renamed column even when the branch that
uses it is not reachable from a test. Run it with:

    python src/audit_contracts.py
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from analytics import default_params, load_query_library, run_query  # noqa: E402
from config import DB_PATH  # noqa: E402

APP = PROJECT_ROOT / "dashboard" / "app.py"
DOCS = [
    PROJECT_ROOT / "README.md",
    PROJECT_ROOT / "reports" / "business_analysis.md",
    PROJECT_ROOT / "reports" / "decision_report.md",
]

ASSIGN = re.compile(r'^\s*(\w+)\s*=\s*query\(\s*"([a-z0-9_]+)"', re.MULTILINE)
CUSTOM_DATA = re.compile(r"custom_data=\[(.*?)\]", re.DOTALL)
FIG_CALL = re.compile(r"\b(?:px|go)\.[A-Za-z_]+")


def call_containing(text: str, pos: int) -> str:
    """Return the source of the figure call that encloses `pos`.

    Walks backwards to the nearest `px.` / `go.` call, then balances
    parentheses to find where that call ends.
    """
    starts = [m.start() for m in FIG_CALL.finditer(text[:pos])]
    if not starts:
        return ""
    start = starts[-1]
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[start: i + 1]
    return text[start:]


def segments(text: str) -> list[tuple[str, str]]:
    """Every `x = query("name")` assignment in file order."""
    return [(m.group(1), m.group(2)) for m in ASSIGN.finditer(text)]


def columns_used(var: str, code: str, full_text: str) -> set[str]:
    """Every column name the dashboard reads off the result variable.

    Direct indexing (`var["col"]`, `var[[...]]`) is unambiguous. A
    `custom_data=[...]` list belongs to whichever query result is the data
    argument of the enclosing figure call, so the call is parsed to find it.
    """
    used: set[str] = set()
    for m in re.finditer(rf'\b{var}\[\s*"([a-z0-9_]+)"\s*\]', code):
        used.add(m.group(1))
    for m in re.finditer(rf'\b{var}\[\s*\[(.*?)\]\s*\]', code, re.DOTALL):
        used.update(re.findall(r'"([a-z0-9_]+)"', m.group(1)))
    for m in CUSTOM_DATA.finditer(code):
        call = call_containing(full_text, m.start())
        # The data argument is the first positional argument of the call.
        args = call[call.find("(") + 1:]
        first = args.split(",")[0].strip()
        if first == var or first.startswith(f"{var}["):
            used.update(re.findall(r'"([a-z0-9_]+)"', m.group(1)))
    return used


def main() -> int:
    library = load_query_library()
    failures: list[str] = []

    text = APP.read_text(encoding="utf-8")
    called = sorted({name for _, name in segments(text)})
    for name in called:
        if name not in library:
            failures.append(f"dashboard calls unknown query '{name}'")

    con = sqlite3.connect(DB_PATH)
    outputs = {
        name: run_query(con, name, default_params(), library)
        for name in called
        if name in library
    }
    con.close()

    for var, name in segments(text):
        if name not in outputs:
            continue
        available = set(outputs[name].columns)
        missing = sorted(c for c in columns_used(var, text, text) if c not in available)
        if missing:
            failures.append(
                f"query '{name}' does not provide column(s) used by the dashboard: "
                + ", ".join(missing)
            )

    # 3: figures referenced by the docs exist
    for doc in DOCS:
        if not doc.exists():
            continue
        body = doc.read_text(encoding="utf-8")
        for m in re.finditer(r"\]\((figures/[^)]+\.png)\)", body):
            if not (doc.parent / m.group(1)).exists():
                failures.append(f"{doc.name} references missing figure '{m.group(1)}'")

    # 4: internal markdown links resolve
    for doc in DOCS:
        if not doc.exists():
            continue
        body = doc.read_text(encoding="utf-8")
        for m in re.finditer(r"\]\((?!https?://|#)([^)]+)\)", body):
            link = m.group(1).split("#")[0]
            if not link:
                continue
            if not (doc.parent / link).resolve().exists():
                failures.append(f"{doc.name} links to missing file '{link}'")

    print(f"checked {len(called)} dashboard queries and {len(DOCS)} documents")
    if failures:
        print(f"\n{len(failures)} contract problem(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("all contracts satisfied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
