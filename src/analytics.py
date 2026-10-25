"""Shared query layer.

This module is the bridge between the SQL files and everything that consumes
them: the report renderer, the Streamlit dashboard and the test suite. It
parses the `-- name:` blocks out of the SQL files, binds the filter
parameters and returns pandas DataFrames.

Because the dashboard, the report and the tests all go through this module,
they cannot drift apart: there is exactly one copy of every KPI definition,
and it lives in `sql/04_analysis_queries.sql`.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from config import (
    ANALYSIS_SQL,
    DQ_SQL,
    PERIOD_END,
    PERIOD_START,
    SEGMENT_HIGH,
    SEGMENT_LOW,
    SEGMENT_ORDER,
    SEGMENT_REGULAR,
)

HEADER = re.compile(r"^-- name:\s*(\S+)\s*$", re.MULTILINE)
DESC = re.compile(r"^-- description:\s*(.+)$", re.MULTILINE)


@dataclass(frozen=True)
class Query:
    name: str
    description: str
    sql: str
    source: Path


def render_sql(text: str) -> str:
    """Substitute the observation-window tokens from config.

    The window is defined once in `src/config.py` and injected into the SQL at
    build and run time, so the SQL and the Python code cannot drift apart.
    """
    return (
        text.replace("{{PERIOD_START}}", PERIOD_START)
            .replace("{{PERIOD_END}}", PERIOD_END)
    )


def parse_named_queries(text: str, source: Path) -> list[Query]:
    """Split a SQL file into its `-- name:` blocks."""
    matches = list(HEADER.finditer(text))
    queries: list[Query] = []
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[start:end]
        desc_match = DESC.search(block)
        sql = DESC.sub("", block)
        sql = re.sub(r"^--.*$", "", sql, flags=re.MULTILINE).strip()
        sql = sql.rstrip(";").strip()
        if not sql:
            raise ValueError(f"query '{match.group(1)}' in {source.name} is empty")
        queries.append(
            Query(
                name=match.group(1),
                description=desc_match.group(1).strip() if desc_match else "",
                sql=sql,
                source=source,
            )
        )
    return queries


def load_query_library() -> dict[str, Query]:
    """Load every named query from the analysis and data-quality SQL files."""
    library: dict[str, Query] = {}
    for path in (ANALYSIS_SQL, DQ_SQL):
        if not path.exists():
            raise FileNotFoundError(f"{path} is missing")
        for query in parse_named_queries(render_sql(path.read_text(encoding="utf-8")), path):
            if query.name in library:
                raise ValueError(f"duplicate query name '{query.name}'")
            library[query.name] = query
    return library


def default_params() -> dict:
    """Parameters that select the whole observation window and no filters."""
    return {
        "start_date": PERIOD_START,
        "end_date": PERIOD_END,
        "category": None,
        "segment": None,
    }


def filter_params(
    start_date: str | None = None,
    end_date: str | None = None,
    category: str | None = None,
    segment: str | None = None,
) -> dict:
    """Build the parameter set for a filtered analysis."""
    return {
        "start_date": start_date or PERIOD_START,
        "end_date": end_date or PERIOD_END,
        "category": category,
        "segment": segment,
    }


def run_query(
    con: sqlite3.Connection,
    name: str,
    params: dict | None = None,
    library: dict[str, Query] | None = None,
) -> pd.DataFrame:
    """Execute a named query and return the result as a DataFrame."""
    library = library or load_query_library()
    if name not in library:
        raise KeyError(f"unknown query '{name}'")
    return pd.read_sql_query(library[name].sql, con, params=params or default_params())


def connect(db_path) -> sqlite3.Connection:
    """Open the project database with row access by name."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def kpi_bundle(
    con: sqlite3.Connection,
    params: dict | None = None,
    library: dict[str, Query] | None = None,
) -> dict:
    """The five dashboard KPIs plus their supporting counts.

    Every value comes from the canonical `kpi_summary` query, so the dashboard,
    the report and the tests read identical numbers.
    """
    library = library or load_query_library()
    params = params or default_params()

    kpi = run_query(con, "kpi_summary", params, library).iloc[0].to_dict()

    top = run_query(con, "top_products_by_revenue", params, library)
    top_row = top.iloc[0].to_dict() if not top.empty else {}

    return {
        "total_net_revenue": float(kpi["total_net_revenue"]),
        "avg_order_value": float(kpi["avg_order_value"]),
        "repeat_purchase_rate_pct": float(kpi["repeat_purchase_rate_pct"]),
        "eligible_orders": int(kpi["eligible_orders"]),
        "units_sold": int(kpi["units_sold"]),
        "unique_customers": int(kpi["unique_customers"]),
        "repeat_customers": int(kpi["repeat_customers"]),
        "avg_orders_per_customer": float(kpi["avg_orders_per_customer"]),
        "gross_item_revenue": float(kpi["gross_item_revenue"]),
        "discounts_given": float(kpi["discounts_given"]),
        "effective_discount_pct": float(kpi["effective_discount_pct"]),
        "refunds_given": float(kpi["refunds_given"]),
        "effective_refund_pct": float(kpi["effective_refund_pct"]),
        "top_product_name": top_row.get("product_name"),
        "top_product_revenue": float(top_row.get("net_revenue", 0.0)),
        "top_product_units": int(top_row.get("units_sold", 0)),
        "params": params,
    }


def available_filters(con: sqlite3.Connection) -> dict:
    """The filter options the dashboard offers, taken from the data itself."""
    categories = [
        r[0] for r in con.execute(
            "SELECT DISTINCT category FROM stg_products ORDER BY category"
        ).fetchall()
    ]
    return {
        "categories": categories,
        "segments": list(SEGMENT_ORDER),
        "segment_labels": {
            SEGMENT_LOW: "Low Engagement - fewest orders and lowest spend",
            SEGMENT_REGULAR: "Regular - steady, mid-value purchasers",
            SEGMENT_HIGH: "High Value - top third by spend and frequency",
        },
    }
