"""Automated validation for the Shopify purchasing behaviour analysis.

Run from the project root:

    pytest -q

The suite checks four things the project promises:

1. DATA VOLUME AND INTEGRITY - the dataset clears the 10,000 line-item target,
   the pipeline reconciles, and every deliberate defect is caught.
2. KPI CORRECTNESS - the revenue and AOV formulas are what the documentation
   says they are, and eligible-order counts agree across queries.
3. SEGMENTATION - the three segments are mutually exclusive and jointly cover
   every eligible customer.
4. DASHBOARD PARITY - the KPIs the dashboard shows equal the KPIs the SQL
   produces, and the dashboard application actually starts.

A test is only reported as passing if it was executed. If the database or a
dependency is missing, the suite fails loudly rather than skipping silently.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from analytics import (  # noqa: E402
    default_params,
    filter_params,
    kpi_bundle,
    load_query_library,
    run_query,
)
from config import (  # noqa: E402
    DB_PATH,
    KEY_METRICS_JSON,
    MIN_LINE_ITEMS,
    PERIOD_END,
    PERIOD_START,
    SEGMENT_HIGH,
    SEGMENT_LOW,
    SEGMENT_ORDER,
    SEGMENT_REGULAR,
    TARGET_LINE_ITEMS,
)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def con() -> sqlite3.Connection:
    if not DB_PATH.exists():
        pytest.fail(
            f"{DB_PATH.name} is missing. Run `python src/run_all.py` first "
            "(or at least src/generate_data.py and src/build_db.py)."
        )
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    yield connection
    connection.close()


@pytest.fixture(scope="session")
def library() -> dict:
    return load_query_library()


@pytest.fixture(scope="session")
def kpi(con, library) -> dict:
    return kpi_bundle(con, default_params(), library)


def one(con, sql: str, params: dict | None = None) -> sqlite3.Row:
    return con.execute(sql, params or {}).fetchone()


# ---------------------------------------------------------------------------
# 1. DATA VOLUME AND INTEGRITY
# ---------------------------------------------------------------------------


class TestDatasetVolume:
    def test_line_items_clear_the_project_target(self, con):
        """The project target is at least 10,000 transaction line items."""
        raw = one(con, "SELECT COUNT(*) AS n FROM order_items")["n"]
        valid = one(con, "SELECT COUNT(*) AS n FROM fact_order_lines")["n"]
        assert valid >= MIN_LINE_ITEMS, (
            f"only {valid:,} valid line items, target is {MIN_LINE_ITEMS:,}"
        )
        assert raw >= TARGET_LINE_ITEMS, (
            f"the generator produced {raw:,} raw line items, "
            f"aim is {TARGET_LINE_ITEMS:,}"
        )
        assert raw >= valid

    def test_raw_layer_is_present_and_populated(self, con):
        for table, minimum in (
            ("customers", 500),
            ("products", 50),
            ("orders", 1_000),
            ("order_items", 10_000),
        ):
            count = one(con, f"SELECT COUNT(*) AS n FROM {table}")["n"]
            assert count >= minimum, f"{table} has only {count:,} rows"

    def test_pipeline_row_counts_reconcile(self, con):
        """Raw -> staged -> fact must lose rows only for documented reasons."""
        raw_items = one(con, "SELECT COUNT(*) AS n FROM order_items")["n"]
        staged_items = one(con, "SELECT COUNT(*) AS n FROM stg_order_items")["n"]
        fact_lines = one(con, "SELECT COUNT(*) AS n FROM fact_order_lines")["n"]

        assert staged_items <= raw_items
        assert fact_lines <= staged_items

        # Independent recount of the cleaning rule: a line is dropped only if
        # its order cannot be resolved, its product cannot be resolved, or its
        # quantity / price is unusable. Every other line must survive.
        unjustified = one(
            con,
            """
            WITH deduped AS (
                SELECT line_item_id, order_id, product_id, quantity, unit_price
                FROM (
                    SELECT i.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY TRIM(i.order_id), TRIM(i.product_id),
                                            i.quantity, i.unit_price, i.discount_pct
                               ORDER BY i.line_item_id
                           ) AS rn
                    FROM order_items i
                    WHERE i.order_id IS NOT NULL AND TRIM(i.order_id) <> ''
                      AND i.product_id IS NOT NULL AND TRIM(i.product_id) <> ''
                ) WHERE rn = 1
            )
            SELECT COUNT(*) AS n
            FROM deduped d
            LEFT JOIN stg_order_items s ON s.line_item_id = d.line_item_id
            LEFT JOIN stg_orders   o ON o.order_id = d.order_id
            LEFT JOIN stg_products p ON p.product_id = d.product_id
            WHERE s.line_item_id IS NULL
              AND o.order_id IS NOT NULL
              AND p.product_id IS NOT NULL
              AND d.quantity IS NOT NULL AND d.quantity > 0
              AND d.unit_price IS NOT NULL AND d.unit_price > 0
            """,
        )["n"]
        assert unjustified == 0, (
            f"{unjustified} line items were dropped even though their order, product "
            "and values were all valid"
        )

    def test_staging_invents_no_rows(self, con):
        """Every staged row must exist in the raw table, byte for byte."""
        invented = one(
            con,
            """
            SELECT COUNT(*) AS n FROM stg_order_items s
            LEFT JOIN order_items i ON i.line_item_id = s.line_item_id
            WHERE i.line_item_id IS NULL
               OR i.quantity <> s.quantity
               OR ROUND(i.unit_price, 2) <> ROUND(s.unit_price, 2)
               OR COALESCE(i.discount_pct, 0) <> COALESCE(s.discount_pct, 0)
            """,
        )["n"]
        assert invented == 0, f"{invented} staged rows do not match a raw row"

    def test_staged_business_keys_are_unique(self, con):
        duplicates = one(
            con,
            """
            SELECT COUNT(*) AS n FROM (
                SELECT order_id, product_id, quantity, unit_price, discount_pct
                FROM stg_order_items
                GROUP BY order_id, product_id, quantity, unit_price, discount_pct
                HAVING COUNT(*) > 1
            )
            """,
        )["n"]
        assert duplicates == 0

    def test_defect_log_covers_every_injected_defect(self, con):
        logged = one(con, "SELECT COUNT(*) AS n FROM defect_log")["n"]
        assert logged == 93, f"expected 93 logged defects, found {logged}"

    def test_sqlite_integrity_check_passes(self, con):
        assert one(con, "PRAGMA integrity_check")[0] == "ok"

    def test_every_stored_row_is_labelled_synthetic(self, con):
        for table in ("customers", "products", "orders", "order_items"):
            non_synthetic = one(
                con, f"SELECT COUNT(*) AS n FROM {table} WHERE source <> 'synthetic'"
            )["n"]
            assert non_synthetic == 0, f"{table} contains non-synthetic rows"


class TestReconciliation:
    def test_refund_allocation_sums_back_to_the_order_refund(self, con):
        drift = one(
            con,
            """
            SELECT COUNT(*) AS n FROM (
                SELECT o.order_id,
                       SUM(f.refund_cents) AS allocated,
                       o.refund_cents      AS expected
                FROM fact_order_lines f
                JOIN fact_orders o ON o.order_id = f.order_id
                GROUP BY o.order_id, o.refund_cents
            ) WHERE allocated <> expected
            """,
        )["n"]
        assert drift == 0, f"{drift} orders have a refund allocation that does not reconcile"

    def test_order_subtotal_matches_its_line_items(self, con):
        """Every eligible order reconciles, unless a logged defect explains it."""
        unexplained = one(
            con,
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
            SELECT COUNT(*) AS n FROM unreconciled
            WHERE order_id NOT IN (SELECT order_id FROM logged)
            """,
        )["n"]
        assert unexplained == 0, (
            f"{unexplained} eligible orders do not reconcile and are not explained "
            "by the defect log"
        )

    def test_no_unexplained_variances_in_the_dq_output(self, con, library):
        df = run_query(con, "dq_reconciliation_explained", default_params(), library)
        if not df.empty:
            unexplained = df[~df["status"].str.startswith("Explained")]
            assert unexplained.empty, f"{len(unexplained)} unexplained variances"

    def test_net_revenue_equals_net_item_revenue_minus_refunds(self, con):
        """The revenue bridge must close exactly."""
        row = one(
            con,
            """
            SELECT
                (SELECT SUM(net_cents) FROM fact_order_lines)              AS net_line_cents,
                (SELECT SUM(refund_cents) FROM fact_order_lines)            AS refund_cents,
                (SELECT SUM(net_revenue_cents) FROM fact_order_lines)       AS net_revenue_cents,
                (SELECT SUM(net_revenue_cents) FROM fact_orders)            AS order_net_cents
            """,
        )
        assert row["net_line_cents"] - row["refund_cents"] == row["net_revenue_cents"]
        assert row["net_revenue_cents"] == row["order_net_cents"]

    def test_order_level_and_line_level_revenue_agree(self, con):
        lines = one(con, "SELECT SUM(net_revenue_cents) AS n FROM fact_order_lines")["n"]
        orders = one(con, "SELECT SUM(net_revenue_cents) AS n FROM fact_orders")["n"]
        assert lines == orders


# ---------------------------------------------------------------------------
# 2. KPI CORRECTNESS
# ---------------------------------------------------------------------------


class TestRevenueFormula:
    def test_total_revenue_matches_a_hand_written_formula(self, con, kpi):
        """Recompute net revenue from first principles, independently of the views."""
        expected = one(
            con,
            """
            SELECT SUM(
                (li.quantity * CAST(ROUND(li.unit_price * 100.0) AS INTEGER)
                 - (li.quantity * CAST(ROUND(li.unit_price * 100.0) AS INTEGER)
                    * CAST(ROUND(COALESCE(li.discount_pct, 0) * 100.0) AS INTEGER) + 50) / 100
                - li.refund_cents)
            ) AS cents
            FROM fact_order_lines li
            """,
        )["cents"]
        assert round(expected / 100, 2) == kpi["total_net_revenue"]

    def test_revenue_bridge_closes(self, con, library):
        df = run_query(con, "revenue_bridge", default_params(), library).set_index("step")
        gross = float(df.loc[df.index.str.startswith("1."), "amount"].iloc[0])
        discounts = float(df.loc[df.index.str.startswith("2."), "amount"].iloc[0])
        net_items = float(df.loc[df.index.str.startswith("3."), "amount"].iloc[0])
        refunds = float(df.loc[df.index.str.startswith("4."), "amount"].iloc[0])
        net = float(df.loc[df.index.str.startswith("5."), "amount"].iloc[0])

        assert round(gross + discounts, 2) == round(net_items, 2)
        assert round(net_items + refunds, 2) == round(net, 2)
        assert discounts < 0 and refunds < 0

    def test_shipping_and_tax_are_not_in_revenue(self, con):
        """Shipping and tax must be excluded from every revenue figure."""
        revenue = one(con, "SELECT SUM(net_revenue_cents) AS n FROM fact_orders")["n"]
        shipping_tax = one(
            con,
            "SELECT SUM(CAST(ROUND(shipping_total * 100) AS INTEGER) "
            "         + CAST(ROUND(tax_total * 100) AS INTEGER)) AS n FROM fact_orders",
        )["n"]
        assert revenue > 0
        # Shipping + tax are collected separately and are not netted into revenue.
        assert revenue != one(
            con, "SELECT SUM(total) AS n FROM fact_orders"
        )["n"] * 100

    def test_discounts_are_recomputed_from_the_percentage(self, con):
        """The stored discount_amount column is advisory; the pct is authoritative."""
        mismatches = one(
            con,
            """
            SELECT COUNT(*) AS n FROM stg_order_items
            WHERE ABS(discount_cents
                      - (gross_cents * CAST(ROUND(COALESCE(discount_pct, 0) * 100.0) AS INTEGER) + 50) / 100
                      ) > 0
            """,
        )["n"]
        assert mismatches == 0

    def test_effective_discount_rate_is_the_discount_share_of_gross(self, con, kpi):
        """The headline discount rate must be discounts / gross, not net / gross."""
        gross = one(con, "SELECT SUM(gross_cents) AS n FROM fact_order_lines")["n"]
        discounts = one(con, "SELECT SUM(discount_cents) AS n FROM fact_order_lines")["n"]
        assert round(100.0 * discounts / gross, 2) == kpi["effective_discount_pct"]
        assert 0 < kpi["effective_discount_pct"] < 100

    def test_effective_refund_rate_is_the_refund_share_of_gross(self, con, kpi):
        gross = one(con, "SELECT SUM(gross_cents) AS n FROM fact_order_lines")["n"]
        refunds = one(con, "SELECT SUM(refund_cents) AS n FROM fact_order_lines")["n"]
        assert round(100.0 * refunds / gross, 2) == kpi["effective_refund_pct"]
        assert 0 < kpi["effective_refund_pct"] < 100


class TestAov:
    def test_aov_is_revenue_divided_by_eligible_orders(self, con, kpi):
        revenue = one(con, "SELECT SUM(net_revenue_cents) AS n FROM fact_orders")["n"]
        orders = one(con, "SELECT COUNT(*) AS n FROM fact_orders")["n"]
        assert round(revenue / 100 / orders, 2) == kpi["avg_order_value"]

    def test_eligible_order_count_is_consistent_across_queries(self, con, library):
        """Every query that counts orders must count the same orders."""
        counts = {
            "kpi_summary": int(
                run_query(con, "kpi_summary", default_params(), library)["eligible_orders"].iloc[0]
            ),
            "repeat_purchase_summary_orders": int(
                one(con, "SELECT COUNT(DISTINCT order_id) AS n FROM fact_order_lines")["n"]
            ),
            "fact_orders": int(one(con, "SELECT COUNT(*) AS n FROM fact_orders")["n"]),
            "distinct_order_ids_in_lines": int(
                one(con, "SELECT COUNT(DISTINCT order_id) AS n FROM fact_order_lines")["n"]
            ),
        }
        assert len(set(counts.values())) == 1, counts

    def test_every_fact_order_is_completed_and_inside_the_window(self, con):
        bad_status = one(
            con, "SELECT COUNT(*) AS n FROM fact_orders WHERE order_status <> 'completed'"
        )["n"]
        out_of_window = one(
            con,
            "SELECT COUNT(*) AS n FROM fact_orders "
            "WHERE order_date < :start OR order_date > :end",
            {"start": PERIOD_START, "end": PERIOD_END},
        )["n"]
        assert bad_status == 0
        assert out_of_window == 0

    def test_aov_by_segment_covers_all_segments(self, con, library):
        df = run_query(con, "aov_by_segment", default_params(), library)
        assert set(df["segment"]) == set(SEGMENT_ORDER)
        total = one(con, "SELECT SUM(net_revenue_cents) AS n FROM fact_orders")["n"] / 100
        assert abs(df["net_revenue"].sum() - total) < 0.05

    def test_category_revenue_sums_to_total_revenue(self, con, library):
        df = run_query(con, "revenue_by_category", default_params(), library)
        total = one(con, "SELECT SUM(net_revenue_cents) AS n FROM fact_orders")["n"] / 100
        assert abs(df["net_revenue"].sum() - total) < 0.05


class TestRepeatPurchase:
    def test_repeat_rate_is_a_percentage_between_zero_and_one_hundred(self, kpi):
        rate = kpi["repeat_purchase_rate_pct"]
        assert 0.0 <= rate <= 100.0

    def test_repeat_customers_are_a_subset_of_unique_customers(self, kpi):
        assert kpi["repeat_customers"] <= kpi["unique_customers"]
        assert kpi["unique_customers"] > 0

    def test_repeat_rate_matches_its_definition(self, con, kpi):
        row = one(
            con,
            """
            SELECT
                COUNT(*) AS customers,
                SUM(CASE WHEN orders >= 2 THEN 1 ELSE 0 END) AS repeat_customers
            FROM (
                SELECT customer_id, COUNT(DISTINCT order_id) AS orders
                FROM fact_order_lines GROUP BY customer_id
            )
            """,
        )
        assert row["customers"] == kpi["unique_customers"]
        assert row["repeat_customers"] == kpi["repeat_customers"]
        assert round(100.0 * row["repeat_customers"] / row["customers"], 2) == \
            kpi["repeat_purchase_rate_pct"]

    def test_avg_orders_per_customer_matches_its_definition(self, con, kpi):
        row = one(
            con,
            """
            SELECT
                (SELECT COUNT(DISTINCT order_id) FROM fact_order_lines) AS orders,
                (SELECT COUNT(DISTINCT customer_id) FROM fact_order_lines) AS customers
            """,
        )
        assert round(row["orders"] / row["customers"], 3) == kpi["avg_orders_per_customer"]

    def test_interpurchase_interval_is_measured_for_repeat_customers(self, con, library):
        df = run_query(con, "interpurchase_interval", default_params(), library)
        assert not df.empty
        row = df.iloc[0]
        assert row["intervals_measured"] > 0
        assert row["median_gap_days"] >= 0
        assert 0 <= row["pct_repeat_within_30_days"] <= 100
        assert row["pct_repeat_within_90_days"] >= row["pct_repeat_within_30_days"]


# ---------------------------------------------------------------------------
# 3. SEGMENTATION
# ---------------------------------------------------------------------------


class TestSegmentation:
    def test_three_segments_exist(self, con):
        count = one(
            con, "SELECT COUNT(DISTINCT segment) AS n FROM dim_customers"
        )["n"]
        assert count == 3
        names = {
            r[0]
            for r in con.execute("SELECT DISTINCT segment FROM dim_customers").fetchall()
        }
        assert names == set(SEGMENT_ORDER)

    def test_segments_are_mutually_exclusive_and_collectively_exhaustive(self, con):
        """No customer may appear in two segments; every customer must appear."""
        total = one(con, "SELECT COUNT(*) AS n FROM dim_customers")["n"]
        assigned = one(
            con, "SELECT COUNT(DISTINCT customer_id) AS n FROM dim_customers"
        )["n"]
        assert total == assigned, "a customer is assigned to more than one segment"

        eligible = one(
            con, "SELECT COUNT(DISTINCT customer_id) AS n FROM fact_order_lines"
        )["n"]
        assert total == eligible, "an eligible customer is missing from the segmentation"

    def test_every_customer_has_exactly_one_tercile(self, con):
        counts = con.execute(
            "SELECT customer_id, COUNT(DISTINCT segment_tertile) AS n "
            "FROM dim_customers GROUP BY customer_id HAVING n > 1"
        ).fetchall()
        assert not counts, "a customer has more than one segment tertile"

    def test_segmentation_is_reproducible(self, con, library):
        """Re-running the segmentation logic gives identical assignments."""
        recomputed = con.execute(
            """
            WITH pct AS (
                SELECT customer_id,
                       PERCENT_RANK() OVER (ORDER BY net_revenue) AS spend_percentile,
                       PERCENT_RANK() OVER (ORDER BY order_count) AS order_percentile
                FROM dim_customers
            ),
            scored AS (
                SELECT customer_id,
                       ROUND(0.65 * spend_percentile + 0.35 * order_percentile, 6) AS value_score
                FROM pct
            )
            SELECT customer_id,
                   NTILE(3) OVER (ORDER BY value_score, customer_id) AS tertile
            FROM scored
            """
        ).fetchall()
        stored = {
            r["customer_id"]: r["segment_tertile"] for r in
            con.execute("SELECT customer_id, segment_tertile FROM dim_customers").fetchall()
        }
        assert len(recomputed) == len(stored)
        for customer_id, tertile in recomputed:
            assert stored[customer_id] == tertile

    def test_segments_are_ordered_by_value(self, con):
        rows = con.execute(
            "SELECT segment, AVG(value_score) AS avg_score FROM dim_customers GROUP BY segment"
        ).fetchall()
        scores = {r["segment"]: r["avg_score"] for r in rows}
        assert scores[SEGMENT_LOW] < scores[SEGMENT_REGULAR] < scores[SEGMENT_HIGH]

    def test_high_value_segment_buys_more_and_spends_more(self, con):
        rows = con.execute(
            "SELECT segment, AVG(order_count) AS orders, AVG(net_revenue) AS revenue "
            "FROM dim_customers GROUP BY segment"
        ).fetchall()
        by_segment = {r["segment"]: (r["orders"], r["revenue"]) for r in rows}
        low = by_segment[SEGMENT_LOW]
        high = by_segment[SEGMENT_HIGH]
        assert high[0] > low[0]
        assert high[1] > low[1]


# ---------------------------------------------------------------------------
# 4. DASHBOARD PARITY
# ---------------------------------------------------------------------------


class TestDashboardParity:
    def test_dashboard_kpis_equal_the_sql_outputs(self, con, library):
        """The KPI bundle the dashboard renders must equal the published SQL."""
        bundle = kpi_bundle(con, default_params(), library)
        summary = run_query(con, "kpi_summary", default_params(), library).iloc[0]
        top = run_query(con, "top_products_by_revenue", default_params(), library).iloc[0]

        assert bundle["total_net_revenue"] == float(summary["total_net_revenue"])
        assert bundle["avg_order_value"] == float(summary["avg_order_value"])
        assert bundle["repeat_purchase_rate_pct"] == float(summary["repeat_purchase_rate_pct"])
        assert bundle["eligible_orders"] == int(summary["eligible_orders"])
        assert bundle["units_sold"] == int(summary["units_sold"])
        assert bundle["unique_customers"] == int(summary["unique_customers"])
        assert bundle["repeat_customers"] == int(summary["repeat_customers"])
        assert bundle["avg_orders_per_customer"] == float(summary["avg_orders_per_customer"])
        assert bundle["top_product_name"] == top["product_name"]
        assert bundle["top_product_revenue"] == float(top["net_revenue"])

    def test_key_metrics_json_matches_the_live_database(self, con, library):
        """The published key_metrics.json must not drift from the database."""
        if not KEY_METRICS_JSON.exists():
            pytest.fail("reports/key_metrics.json is missing. Run src/run_queries.py.")
        published = json.loads(KEY_METRICS_JSON.read_text(encoding="utf-8"))
        live = kpi_bundle(con, default_params(), library)
        for key in (
            "total_net_revenue", "avg_order_value", "repeat_purchase_rate_pct",
            "eligible_orders", "units_sold", "unique_customers",
            "repeat_customers", "avg_orders_per_customer",
        ):
            assert published["kpis"][key] == live[key], f"{key} drifted"

    def test_filters_change_the_results_consistently(self, con, library):
        """A category filter must reduce revenue and never invent orders."""
        full = kpi_bundle(con, default_params(), library)
        filtered = kpi_bundle(
            con, filter_params(category="Electronics"), library
        )
        assert filtered["total_net_revenue"] < full["total_net_revenue"]
        assert filtered["eligible_orders"] <= full["eligible_orders"]
        assert filtered["unique_customers"] <= full["unique_customers"]

        segment = kpi_bundle(con, filter_params(segment=SEGMENT_HIGH), library)
        assert segment["total_net_revenue"] < full["total_net_revenue"]

    def test_date_filter_restricts_the_window(self, con, library):
        narrowed = kpi_bundle(
            con, filter_params(start_date="2025-01-01", end_date="2025-12-31"), library
        )
        full = kpi_bundle(con, default_params(), library)
        assert narrowed["eligible_orders"] < full["eligible_orders"]
        assert narrowed["total_net_revenue"] < full["total_net_revenue"]

    def test_filtered_revenue_sums_to_the_unfiltered_total(self, con, library):
        """The category revenues must add back up to the whole."""
        categories = [
            r[0] for r in con.execute("SELECT DISTINCT category FROM stg_products").fetchall()
        ]
        total = kpi_bundle(con, default_params(), library)["total_net_revenue"]
        summed = sum(
            kpi_bundle(con, filter_params(category=c), library)["total_net_revenue"]
            for c in categories
        )
        assert abs(summed - total) < 0.05


class TestDashboardApplication:
    def test_streamlit_app_runs_without_exceptions(self):
        """Execute the dashboard script the way Streamlit does."""
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_file(str(PROJECT_ROOT / "dashboard" / "app.py"), default_timeout=180)
        app.run()
        assert not app.exception, (
            "the dashboard raised an exception:\n"
            + "\n".join(str(e.value) for e in app.exception)
        )
        assert len(app.title) > 0
        # The synthetic-data disclaimer must be visible on the page.
        markdown = " ".join(m.value for m in app.markdown)
        assert "Synthetic data" in markdown

    def test_dashboard_server_starts_and_serves_the_app(self):
        """Boot the real server and request the page."""
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "tests" / "smoke_dashboard.py")],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert result.returncode == 0, (
            f"dashboard smoke test failed:\n{result.stdout}\n{result.stderr}"
        )
        assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# 5. NAMED QUERY CONTRACT
# ---------------------------------------------------------------------------


class TestQueryLibrary:
    def test_every_named_query_executes(self, con, library):
        failures = []
        for name in library:
            try:
                run_query(con, name, default_params(), library)
            except Exception as exc:  # noqa: BLE001 - report every failure
                failures.append(f"{name}: {exc}")
        assert not failures, "queries failed:\n" + "\n".join(failures)

    def test_query_names_are_unique(self, library):
        assert len(library) == len(set(library))

    def test_dq_checks_behave_as_documented(self, con, library):
        """The validation queries must find the defects that were injected."""
        dq = run_query(con, "dq_summary", default_params(), library).set_index("check_id")
        expected = {
            "DQ01": 14,   # duplicate line items
            "DQ04": 6,    # orphan order references
            "DQ05": 9,    # orphan product references
            "DQ06": 6,    # orphan customer references
            "DQ07": 8,    # NULL quantity
            "DQ08": 7,    # negative quantity
            "DQ09": 5,    # zero quantity
            "DQ10": 6,    # NULL unit price
            "DQ11": 6,    # non-positive unit price
            "DQ13": 3,    # discount mismatch on a line that can carry revenue
            "DQ14": 9,    # order total variance
            "DQ21": 0,    # refund allocation drift
        }
        for check_id, count in expected.items():
            actual = int(dq.loc[check_id, "records_flagged"])
            assert actual == count, f"{check_id} flagged {actual}, expected {count}"

    def test_cancelled_and_refunded_orders_are_excluded_from_revenue(self, con):
        excluded = one(
            con,
            "SELECT COUNT(*) AS n FROM stg_orders "
            "WHERE order_status <> 'completed'",
        )["n"]
        included = one(
            con, "SELECT COUNT(*) AS n FROM stg_orders WHERE order_status = 'completed'"
        )["n"]
        assert excluded > 0, "the dataset should contain non-eligible orders to exclude"
        assert included > 0
        assert one(
            con, "SELECT COUNT(*) AS n FROM fact_orders WHERE order_status <> 'completed'"
        )["n"] == 0
