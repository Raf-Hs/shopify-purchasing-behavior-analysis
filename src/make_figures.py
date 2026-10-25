"""Render the report figures.

Run order: build_db.py -> run_queries.py -> make_figures.py

Every figure is drawn from a named query in `sql/04_analysis_queries.sql`, so
a figure can never drift from the published analysis.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from analytics import default_params, load_query_library, run_query  # noqa: E402
from config import DB_PATH, FIGURES_DIR, PALETTE, SEGMENT_COLORS  # noqa: E402

plt.rcParams.update({
    "figure.dpi": 140,
    "savefig.dpi": 140,
    "font.size": 9.5,
    "axes.titlesize": 12,
    "axes.titleweight": "bold",
    "axes.labelsize": 9.5,
    "axes.edgecolor": "#d4d4d8",
    "axes.grid": True,
    "axes.axisbelow": True,
    "grid.color": "#ececee",
    "grid.linewidth": 0.8,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "legend.frameon": False,
    "axes.spines.top": False,
    "axes.spines.right": False,
})


def _money(value: float) -> str:
    return f"${value:,.0f}"


def _save(fig, name: str) -> None:
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    path = FIGURES_DIR / name
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  wrote {path.name}")


def fig_revenue_trend(con, library) -> None:
    df = run_query(con, "monthly_revenue_trend", default_params(), library)
    df["label"] = pd.to_datetime(df["order_month"]).dt.strftime("%b %y")

    fig, ax = plt.subplots(figsize=(11, 4.4))
    ax.bar(df["label"], df["net_revenue"], color=PALETTE["teal"], width=0.72, label="Net revenue")
    ax.set_ylabel("Net revenue (USD)")
    ax.set_title("Monthly net revenue and order volume, 2024-2025")
    ax.set_ylim(0, df["net_revenue"].max() * 1.25)
    ax.tick_params(axis="x", rotation=90)

    ax2 = ax.twinx()
    ax2.plot(df["label"], df["orders"], color=PALETTE["navy"], marker="o",
             markersize=3.5, linewidth=1.6, label="Orders")
    ax2.set_ylabel("Eligible orders")
    ax2.grid(False)
    ax2.spines["top"].set_visible(False)

    for i, (rev, orders) in enumerate(zip(df["net_revenue"], df["orders"])):
        if rev == df["net_revenue"].max() or rev == df["net_revenue"].iloc[0]:
            ax.annotate(_money(rev), (i, rev), textcoords="offset points",
                        xytext=(0, 4), ha="center", fontsize=8, color=PALETTE["dark"])

    handles = [plt.Rectangle((0, 0), 1, 1, color=PALETTE["teal"]),
               plt.Line2D([0], [0], color=PALETTE["navy"], marker="o", markersize=4)]
    ax.legend(handles, ["Net revenue (USD)", "Eligible orders"],
              loc="upper left", ncol=2, fontsize=9)
    fig.tight_layout()
    _save(fig, "01_revenue_trend.png")


def fig_category_revenue(con, library) -> None:
    df = run_query(con, "revenue_by_category", default_params(), library).sort_values("net_revenue")

    fig, ax = plt.subplots(figsize=(9, 4.4))
    bars = ax.barh(df["category"], df["net_revenue"], color=PALETTE["navy"], height=0.62)
    ax.set_xlabel("Net revenue (USD)")
    ax.set_title("Net revenue by product category")
    ax.set_xlim(0, df["net_revenue"].max() * 1.22)

    for bar, rev, pct in zip(bars, df["net_revenue"], df["pct_of_revenue"]):
        ax.annotate(f"{_money(rev)}  ({pct:.1f}%)",
                    (bar.get_width(), bar.get_y() + bar.get_height() / 2),
                    xytext=(6, 0), textcoords="offset points",
                    va="center", fontsize=8.5, color=PALETTE["dark"])
    fig.tight_layout()
    _save(fig, "02_revenue_by_category.png")


def fig_top_products(con, library) -> None:
    df = run_query(con, "top_products_by_revenue", default_params(), library).sort_values("net_revenue")

    fig, ax = plt.subplots(figsize=(10, 5))
    bars = ax.barh(df["product_name"], df["net_revenue"], color=PALETTE["teal"], height=0.62)
    ax.set_xlabel("Net revenue (USD)")
    ax.set_title("Top 10 products by net revenue")
    ax.set_xlim(0, df["net_revenue"].max() * 1.24)
    for bar, rev, units in zip(bars, df["net_revenue"], df["units_sold"]):
        ax.annotate(f"{_money(rev)} · {units:,} u",
                    (bar.get_width(), bar.get_y() + bar.get_height() / 2),
                    xytext=(6, 0), textcoords="offset points",
                    va="center", fontsize=8.5, color=PALETTE["dark"])
    fig.tight_layout()
    _save(fig, "03_top_products.png")


def fig_aov_by_segment(con, library) -> None:
    df = run_query(con, "aov_by_segment", default_params(), library).sort_values("avg_order_value")
    colours = [SEGMENT_COLORS.get(s, PALETTE["grey"]) for s in df["segment"]]

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.1))

    bars = axes[0].barh(df["segment"], df["avg_order_value"], color=colours, height=0.55)
    axes[0].set_xlabel("Average order value (USD)")
    axes[0].set_title("Average order value by segment")
    axes[0].set_xlim(0, df["avg_order_value"].max() * 1.2)
    for bar, value in zip(bars, df["avg_order_value"]):
        axes[0].annotate(f"${value:,.2f}", (bar.get_width(), bar.get_y() + bar.get_height() / 2),
                         xytext=(6, 0), textcoords="offset points",
                         va="center", fontsize=9, color=PALETTE["dark"])

    bars = axes[1].barh(df["segment"], df["revenue_per_customer"], color=colours, height=0.55)
    axes[1].set_xlabel("Revenue per customer (USD)")
    axes[1].set_title("Revenue per customer by segment")
    axes[1].set_xlim(0, df["revenue_per_customer"].max() * 1.2)
    for bar, value in zip(bars, df["revenue_per_customer"]):
        axes[1].annotate(_money(value), (bar.get_width(), bar.get_y() + bar.get_height() / 2),
                         xytext=(6, 0), textcoords="offset points",
                         va="center", fontsize=9, color=PALETTE["dark"])

    fig.suptitle("Segment value: basket size vs lifetime value",
                 fontsize=12, fontweight="bold", y=1.02)
    fig.tight_layout()
    _save(fig, "04_aov_by_segment.png")


def fig_segment_distribution(con, library) -> None:
    df = run_query(con, "segment_distribution", default_params(), library)

    fig, ax = plt.subplots(figsize=(10, 4.2))
    x = range(len(df))
    width = 0.38

    ax.bar([i - width / 2 for i in x], df["pct_of_customers"], width,
           label="% of customers", color=PALETTE["sand"])
    ax.bar([i + width / 2 for i in x], df["pct_of_revenue"], width,
           label="% of revenue", color=PALETTE["navy"])

    for i, (cust, rev) in enumerate(zip(df["pct_of_customers"], df["pct_of_revenue"])):
        ax.annotate(f"{cust:.1f}%", (i - width / 2, cust), xytext=(0, 4),
                    textcoords="offset points", ha="center", fontsize=9)
        ax.annotate(f"{rev:.1f}%", (i + width / 2, rev), xytext=(0, 4),
                    textcoords="offset points", ha="center", fontsize=9)

    ax.set_xticks(list(x))
    ax.set_xticklabels(df["segment"])
    ax.set_ylabel("Share (%)")
    ax.set_ylim(0, max(df["pct_of_revenue"].max(), df["pct_of_customers"].max()) * 1.25)
    ax.set_title("A third of the customers produce three quarters of the revenue")
    ax.legend(loc="upper right")
    fig.tight_layout()
    _save(fig, "05_segment_distribution.png")


def fig_repeat_behaviour(con, library) -> None:
    dist = run_query(con, "order_count_distribution", default_params(), library)
    profile = run_query(con, "repeat_vs_single_customer_profile", default_params(), library)

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.1))

    labels = [f"{v}" if v < 10 else "10+" for v in dist["orders"]]
    axes[0].bar(labels, dist["customers"], color=PALETTE["teal"])
    axes[0].set_xlabel("Orders placed in the window")
    axes[0].set_ylabel("Customers")
    axes[0].set_title("How many orders customers placed")
    for i, (c, pct) in enumerate(zip(dist["customers"], dist["pct_of_customers"])):
        axes[0].annotate(f"{c:,}", (i, c), xytext=(0, 3),
                         textcoords="offset points", ha="center", fontsize=8)

    colours = [PALETTE["grey"] if "One-time" in t else PALETTE["teal"]
               for t in profile["customer_type"]]
    bars = axes[1].bar(profile["customer_type"], profile["avg_revenue_per_customer"],
                       color=colours, width=0.5)
    axes[1].set_ylabel("Average revenue per customer (USD)")
    axes[1].set_title("Repeat buyers are worth far more")
    for bar, value, orders in zip(bars, profile["avg_revenue_per_customer"], profile["avg_orders"]):
        axes[1].annotate(f"{_money(value)}\n{orders:.2f} orders each",
                         (bar.get_x() + bar.get_width() / 2, value),
                         xytext=(0, 5), textcoords="offset points",
                         ha="center", fontsize=9, color=PALETTE["dark"])
    axes[1].set_ylim(0, profile["avg_revenue_per_customer"].max() * 1.3)

    fig.tight_layout()
    _save(fig, "06_repeat_behaviour.png")


def fig_revenue_bridge(con, library) -> None:
    df = run_query(con, "revenue_bridge", default_params(), library)

    labels = ["Gross item\nrevenue", "Line\ndiscounts", "Net item\nrevenue",
              "Refunds", "NET\nREVENUE"]
    values = df["amount"].tolist()
    gross, discounts, net_items, refunds, net = values

    fig, ax = plt.subplots(figsize=(9.5, 4.5))
    width = 0.58

    # Totals sit on the baseline; the two deductions float between the totals.
    ax.bar(0, gross, width, color=PALETTE["grey"])
    ax.bar(1, abs(discounts), width, bottom=gross + discounts, color=PALETTE["red"])
    ax.bar(2, net_items, width, color=PALETTE["grey"])
    ax.bar(3, abs(refunds), width, bottom=net_items + refunds, color=PALETTE["red"])
    ax.bar(4, net, width, color=PALETTE["navy"])

    # Connectors between consecutive steps.
    ax.plot([0 + width / 2, 1 - width / 2], [gross, gross], color=PALETTE["grey"], linewidth=0.9)
    ax.plot([1 + width / 2, 2 - width / 2], [net_items, net_items],
            color=PALETTE["grey"], linewidth=0.9)
    ax.plot([2 + width / 2, 3 - width / 2], [net_items, net_items],
            color=PALETTE["grey"], linewidth=0.9)
    ax.plot([3 + width / 2, 4 - width / 2], [net, net], color=PALETTE["grey"], linewidth=0.9)

    annotations = [
        (0, 0, gross, f"${gross:,.0f}"),
        (1, gross + discounts, abs(discounts), f"-${abs(discounts):,.0f}"),
        (2, 0, net_items, f"${net_items:,.0f}"),
        (3, net_items + refunds, abs(refunds), f"-${abs(refunds):,.0f}"),
        (4, 0, net, f"${net:,.0f}"),
    ]
    for x, bottom, height, text in annotations:
        ax.annotate(text, (x, bottom + height), xytext=(0, 5),
                    textcoords="offset points", ha="center", fontsize=9.5,
                    fontweight="bold", color=PALETTE["dark"])

    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels)
    ax.set_ylabel("USD")
    ax.set_title("From gross item revenue to net revenue")
    ax.set_ylim(0, gross * 1.16)
    ax.yaxis.set_major_formatter(
        matplotlib.ticker.FuncFormatter(lambda v, _: f"${v/1000:,.0f}k")
    )
    fig.tight_layout()
    _save(fig, "07_revenue_bridge.png")


def fig_interpurchase(con, library) -> None:
    gap = run_query(con, "interpurchase_interval", default_params(), library).iloc[0]

    con2 = sqlite3.connect(DB_PATH)
    try:
        gaps = con2.execute(
            """
            WITH orders AS (
                SELECT DISTINCT customer_id, order_id, order_date FROM fact_order_lines
            ),
            g AS (
                SELECT julianday(order_date) - julianday(
                    LAG(order_date) OVER (PARTITION BY customer_id ORDER BY order_date, order_id)
                ) AS gap_days
                FROM orders
            )
            SELECT gap_days FROM g WHERE gap_days IS NOT NULL AND gap_days > 0
            """
        ).fetchall()
    finally:
        con2.close()
    days = pd.Series([r[0] for r in gaps])

    fig, ax = plt.subplots(figsize=(10, 4.2))
    ax.hist(days, bins=60, color=PALETTE["teal"], edgecolor="white", linewidth=0.4)
    ax.axvline(gap["median_gap_days"], color=PALETTE["red"], linewidth=1.8,
               label=f"Median {gap['median_gap_days']:.0f} days")
    ax.axvline(gap["avg_gap_days"], color=PALETTE["navy"], linewidth=1.8, linestyle="--",
               label=f"Mean {gap['avg_gap_days']:.0f} days")
    ax.set_xlabel("Days between consecutive orders")
    ax.set_ylabel("Repeat intervals")
    ax.set_title("Time between purchases - the tail is long, the median is not")
    ax.legend(loc="upper right")
    ax.set_xlim(0, min(400, days.max() * 1.02))
    fig.tight_layout()
    _save(fig, "08_interpurchase_interval.png")


def fig_share_vs_units(con, library) -> None:
    df = run_query(con, "category_revenue_share", default_params(), library).sort_values("net_revenue")
    y = range(len(df))

    fig, ax = plt.subplots(figsize=(10, 4.6))
    ax.hlines(y, df["pct_of_units"], df["pct_of_revenue"], color=PALETTE["grey"], linewidth=2)
    ax.scatter(df["pct_of_units"], y, s=55, color=PALETTE["sand"], label="% of units", zorder=3)
    ax.scatter(df["pct_of_revenue"], y, s=55, color=PALETTE["navy"], label="% of revenue", zorder=3)

    for i, row in enumerate(df.itertuples()):
        gap = row.pct_of_revenue - row.pct_of_units
        ax.annotate(f"{gap:+.1f} pts", (max(row.pct_of_units, row.pct_of_revenue), i),
                    xytext=(10, 0), textcoords="offset points", va="center",
                    fontsize=8.5,
                    color=PALETTE["red"] if gap < 0 else PALETTE["teal"])

    ax.set_yticks(list(y))
    ax.set_yticklabels(df["category"])
    ax.set_xlabel("Share of total (%)")
    ax.set_title("Revenue share vs unit share by category")
    ax.legend(loc="lower right")
    ax.set_xlim(0, df[["pct_of_units", "pct_of_revenue"]].to_numpy().max() * 1.35)
    fig.tight_layout()
    _save(fig, "09_share_vs_units.png")


def fig_dilutive_products(con, library) -> None:
    df = run_query(con, "high_volume_low_revenue_products", default_params(), library).head(10)
    if df.empty:
        return
    df = df.sort_values("pct_of_units")

    fig, ax = plt.subplots(figsize=(10, 4.6))
    bars = ax.barh(df["product_name"], df["pct_of_units"], color=PALETTE["sand"], height=0.6)
    ax.set_xlabel("% of all units sold")
    ax.set_title("High volume, low revenue: products that dilute the basket")
    ax.set_xlim(0, df["pct_of_units"].max() * 1.45)
    for bar, rev_pct, unit_pct in zip(bars, df["pct_of_revenue"], df["pct_of_units"]):
        ax.annotate(f"{unit_pct:.2f}% of units · {rev_pct:.2f}% of revenue",
                    (bar.get_width(), bar.get_y() + bar.get_height() / 2),
                    xytext=(6, 0), textcoords="offset points",
                    va="center", fontsize=8.5, color=PALETTE["dark"])
    fig.tight_layout()
    _save(fig, "10_high_volume_low_revenue.png")


def main() -> None:
    library = load_query_library()
    con = sqlite3.connect(DB_PATH)
    print(f"Rendering figures to {FIGURES_DIR}")
    print(f"  built {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")
    try:
        fig_revenue_trend(con, library)
        fig_category_revenue(con, library)
        fig_top_products(con, library)
        fig_aov_by_segment(con, library)
        fig_segment_distribution(con, library)
        fig_repeat_behaviour(con, library)
        fig_revenue_bridge(con, library)
        fig_interpurchase(con, library)
        fig_share_vs_units(con, library)
        fig_dilutive_products(con, library)
    finally:
        con.close()
    print(f"done - {len(list(FIGURES_DIR.glob('*.png')))} figures")


if __name__ == "__main__":
    main()
