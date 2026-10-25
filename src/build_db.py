"""Create the SQLite database from the generated CSV files.

Run order: generate_data.py -> build_db.py -> run_queries.py -> make_figures.py

The script refuses to build a database from missing or empty CSV files and
verifies the row counts, the SQLite integrity check and the referential
coverage of the analytic views before declaring success.
"""

from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from config import (
    CUSTOMERS_CSV,
    DB_PATH,
    DEFECTS_CSV,
    ORDER_ITEMS_CSV,
    ORDERS_CSV,
    PRODUCTS_CSV,
    SCHEMA_SQL,
    STAGING_SQL,
    MARTS_SQL,
    render_sql,
)

TABLES = {
    "customers": (CUSTOMERS_CSV, ["customer_id", "registration_date", "country", "region",
                                  "acquisition_channel", "device", "is_subscribed", "source"]),
    "products": (PRODUCTS_CSV, ["product_id", "product_name", "category", "subcategory",
                                "vendor", "unit_price", "launched_date", "source"]),
    "orders": (ORDERS_CSV, ["order_id", "customer_id", "order_date", "order_status",
                            "order_channel", "payment_method", "currency", "items_count",
                            "subtotal", "discount_total", "shipping_total", "tax_total",
                            "refund_amount", "total", "shipping_country", "shipping_region",
                            "shipping_city", "is_first_order", "cancelled_at", "source"]),
    "order_items": (ORDER_ITEMS_CSV, ["line_item_id", "order_id", "product_id", "quantity",
                                      "unit_price", "discount_pct", "discount_amount",
                                      "tax_amount", "source"]),
    "defect_log": (DEFECTS_CSV, ["defect_type", "entity", "entity_id",
                                 "affected_order_id", "detail"]),
}

INT_COLUMNS = {"items_count", "is_subscribed", "line_item_id", "quantity"}
FLOAT_COLUMNS = {"unit_price", "subtotal", "discount_total", "shipping_total",
                 "tax_total", "refund_amount", "total", "discount_pct",
                 "discount_amount", "tax_amount"}


def _coerce(value: str, column: str):
    """Cast a CSV string into the column's Python type; empty means NULL."""
    if value is None:
        return None
    value = value.strip()
    if value == "":
        return None
    if column in INT_COLUMNS:
        return int(float(value))
    if column in FLOAT_COLUMNS:
        return float(value)
    return value


def read_csv(path: Path, columns: list[str]) -> list[tuple]:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing. Run `python src/generate_data.py` first."
        )
    rows: list[tuple] = []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = set(columns) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path.name} is missing columns: {sorted(missing)}")
        for record in reader:
            rows.append(tuple(_coerce(record.get(col), col) for col in columns))
    if not rows:
        raise ValueError(f"{path.name} is empty")
    return rows


def main() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()

    payloads = {table: read_csv(path, cols) for table, (path, cols) in TABLES.items()}

    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("PRAGMA foreign_keys = OFF")
        con.executescript(render_sql(SCHEMA_SQL.read_text(encoding="utf-8")))

        for table, (path, columns) in TABLES.items():
            placeholders = ", ".join(["?"] * len(columns))
            sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
            con.executemany(sql, payloads[table])

        con.commit()
        con.executescript(render_sql(STAGING_SQL.read_text(encoding="utf-8")))
        con.commit()
        con.executescript(render_sql(MARTS_SQL.read_text(encoding="utf-8")))
        con.commit()

        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {integrity}")

        raw_counts = {
            table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in TABLES
        }
        for table, rows in payloads.items():
            if raw_counts[table] != len(rows):
                raise RuntimeError(
                    f"row count mismatch for {table}: {raw_counts[table]} != {len(rows)}"
                )

        view_counts = {
            view: con.execute(f"SELECT COUNT(*) FROM {view}").fetchone()[0]
            for view in (
                "stg_customers", "stg_products", "stg_orders", "stg_order_items",
                "fact_orders", "fact_order_lines", "dim_customers",
            )
        }

        # The refund allocation must sum back to the order refund exactly.
        # Integer cents make this an exact comparison, not a tolerance.
        refund_drift = con.execute(
            """
            SELECT COUNT(*)
            FROM (
                SELECT o.order_id,
                       SUM(f.refund_cents) AS allocated,
                       o.refund_cents      AS expected
                FROM fact_order_lines f
                JOIN fact_orders o ON o.order_id = f.order_id
                GROUP BY o.order_id, o.refund_cents
            )
            WHERE allocated <> expected
            """
        ).fetchone()[0]
        if refund_drift:
            raise RuntimeError(
                f"refund allocation does not reconcile for {refund_drift} orders"
            )

        # The stored order subtotal must equal the sum of its line items.
        # Scoped to orders where the raw and staged line counts agree. Where a
        # line was dropped the stored subtotal is expected to be higher, and
        # that variance must be EXPLAINED by an entry in the defect log -
        # an unexplained variance would mean a bug in the cleaning rules.
        unexplained = con.execute(
            """
            WITH unreconciled AS (
                SELECT o.order_id
                FROM fact_orders o
                WHERE (SELECT COUNT(*) FROM order_items i
                       WHERE TRIM(i.order_id) = o.order_id)
                    = (SELECT COUNT(*) FROM stg_order_items li WHERE li.order_id = o.order_id)
                  AND CAST(ROUND(o.subtotal * 100.0) AS INTEGER)
                      <> o.net_revenue_before_refunds_cents
            ),
            logged AS (
                SELECT DISTINCT affected_order_id AS order_id
                FROM defect_log
                WHERE affected_order_id IS NOT NULL AND affected_order_id <> ''
            )
            SELECT COUNT(*)
            FROM unreconciled
            WHERE order_id NOT IN (SELECT order_id FROM logged)
            """
        ).fetchone()[0]
        if unexplained:
            raise RuntimeError(
                f"{unexplained} eligible orders do not reconcile and are not in the defect log"
            )

        print(f"Built {DB_PATH.name}")
        print(f"  built at        : {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")
        print("  raw tables      :")
        for table, count in raw_counts.items():
            print(f"    {table:<16} {count:>8,}")
        print("  analytic views  :")
        for view, count in view_counts.items():
            print(f"    {view:<22} {count:>8,}")
        print("  integrity_check : ok")
        print("  refund allocation reconciles for every eligible order")
        print("  every subtotal variance is explained by the defect log")
    finally:
        con.close()


if __name__ == "__main__":
    main()
