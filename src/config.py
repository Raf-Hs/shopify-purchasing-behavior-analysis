"""Central configuration for the Shopify purchasing behaviour analysis.

Every path is resolved relative to the project root so the pipeline runs from
any working directory.

This module is the *single source of truth* for the rules that the SQL, the
dashboard, the report and the tests must agree on. If a rule changes here it
changes everywhere; no magic numbers are duplicated in the analysis code.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
RAW_DIR = PROJECT_ROOT / "data" / "raw"
SQL_DIR = PROJECT_ROOT / "sql"
REPORTS_DIR = PROJECT_ROOT / "reports"
FIGURES_DIR = REPORTS_DIR / "figures"

DB_PATH = PROJECT_ROOT / "shopify_analysis.db"

CUSTOMERS_CSV = RAW_DIR / "customers.csv"
PRODUCTS_CSV = RAW_DIR / "products.csv"
ORDERS_CSV = RAW_DIR / "orders.csv"
ORDER_ITEMS_CSV = RAW_DIR / "order_items.csv"
DEFECTS_CSV = RAW_DIR / "defects.csv"

QUALITY_REPORT_JSON = REPORTS_DIR / "data_quality.json"
KEY_METRICS_JSON = REPORTS_DIR / "key_metrics.json"
SQL_RESULTS_MD = REPORTS_DIR / "sql_results.md"
QUALITY_REPORT_MD = REPORTS_DIR / "data_quality.md"

SCHEMA_SQL = SQL_DIR / "01_schema.sql"
STAGING_SQL = SQL_DIR / "02_staging.sql"
MARTS_SQL = SQL_DIR / "03_marts.sql"
ANALYSIS_SQL = SQL_DIR / "04_analysis_queries.sql"
DQ_SQL = SQL_DIR / "05_data_quality.sql"

# ---------------------------------------------------------------------------
# Synthetic data generation
# ---------------------------------------------------------------------------
# Fixed seed -> byte-identical CSV output on every machine and every run.
SEED = 20261025

# Observation window for the synthetic store. Two full calendar years so that
# monthly seasonality (Q4 peak) and year-over-year growth are both visible.
PERIOD_START = "2024-01-01"
PERIOD_END = "2025-12-31"

# Customers may register before the first order window (early adopters).
REGISTRATION_START = "2023-01-01"

CURRENCY = "USD"

# Target volumes.
# MIN_LINE_ITEMS is the project requirement: at least 10,000 valid transaction
# line items. TARGET_* are the generator's aims, which are set higher so the
# requirement still holds after the documented cleaning rules remove rows.
MIN_LINE_ITEMS = 10_000
TARGET_CUSTOMERS = 2_100
TARGET_LINE_ITEMS = 15_000

# ---------------------------------------------------------------------------
# Business rules
# ---------------------------------------------------------------------------
# These values are injected into the SQL at build and run time through the
# {{TOKEN}} mechanism, so the SQL and this file cannot drift apart.

# Only `completed` orders count as eligible revenue events.
ELIGIBLE_ORDER_STATUS = "completed"

# Segment labels.
SEGMENT_LOW = "Low Engagement"
SEGMENT_REGULAR = "Regular"
SEGMENT_HIGH = "High Value"
SEGMENT_ORDER = (SEGMENT_LOW, SEGMENT_REGULAR, SEGMENT_HIGH)

# Composite segmentation weights (monetary is weighted higher than frequency).
SEGMENT_WEIGHT_SPEND = 0.65
SEGMENT_WEIGHT_ORDERS = 0.35

# A customer is a repeat customer when they placed at least this many
# eligible orders inside the observation window.
REPEAT_ORDER_THRESHOLD = 2

# Free-shipping threshold (USD) used by the synthetic generator.
FREE_SHIPPING_THRESHOLD = 75.0

# ---------------------------------------------------------------------------
# Plot styling shared by every figure (business-report palette).
# ---------------------------------------------------------------------------
PALETTE = {
    "navy": "#1f3b57",
    "teal": "#2a9d8f",
    "sand": "#e9c46a",
    "orange": "#f4a261",
    "red": "#e76f51",
    "grey": "#8d99ae",
    "light": "#f4f1ec",
    "dark": "#2b2d42",
}

SEGMENT_COLORS = {
    SEGMENT_LOW: "#8d99ae",
    SEGMENT_REGULAR: "#2a9d8f",
    SEGMENT_HIGH: "#1f3b57",
}

MONTH_LABELS_EN = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
}


def render_sql(text: str) -> str:
    """Substitute the {{TOKEN}} placeholders in a SQL file with config values.

    The business rules live here once; the SQL references them as tokens, so
    the two cannot drift apart.
    """
    replacements = {
        "PERIOD_START": PERIOD_START,
        "PERIOD_END": PERIOD_END,
        "ELIGIBLE_ORDER_STATUS": ELIGIBLE_ORDER_STATUS,
        "SEGMENT_WEIGHT_SPEND": SEGMENT_WEIGHT_SPEND,
        "SEGMENT_WEIGHT_ORDERS": SEGMENT_WEIGHT_ORDERS,
        "REPEAT_ORDER_THRESHOLD": REPEAT_ORDER_THRESHOLD,
    }
    for token, value in replacements.items():
        text = text.replace(f"{{{{{token}}}}}", str(value))
    return text
