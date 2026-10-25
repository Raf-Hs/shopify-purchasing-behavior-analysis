"""Streamlit dashboard for the Shopify purchasing behaviour analysis.

Run from the project root:

    streamlit run dashboard/app.py

Every number and every chart on this page comes from the named queries in
`sql/04_analysis_queries.sql` and `sql/05_data_quality.sql`, executed against
the SQLite database built by `src/build_db.py`. The dashboard shares those
queries with `src/run_queries.py` and the test suite through
`src/analytics.py`, so the KPIs here cannot drift from the published
analysis.

THE DATA IS SYNTHETIC. Nothing on this page describes a real Shopify store.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from analytics import (  # noqa: E402
    available_filters,
    connect,
    kpi_bundle,
    load_query_library,
    run_query,
)
from config import (  # noqa: E402
    DB_PATH,
    PALETTE,
    PERIOD_END,
    PERIOD_START,
    SEGMENT_COLORS,
)

# ---------------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Shopify Purchasing Behaviour Analysis",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="expanded",
)

CATEGORY_SEQUENCE = [
    PALETTE["navy"], PALETTE["teal"], PALETTE["sand"], PALETTE["orange"],
    PALETTE["red"], PALETTE["grey"], "#577590", "#43aa8b",
]
st.markdown(
    """
    <style>
      .block-container { padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1500px; }
      .synthetic-banner {
          background: #fff4e5; border-left: 4px solid #f4a261; border-radius: 4px;
          padding: 10px 14px; margin-bottom: 14px; font-size: 0.88rem; color: #5a4632;
      }
      .kpi-label { font-size: 0.78rem; letter-spacing: 0.06em; text-transform: uppercase;
                   color: #6b7280; margin-bottom: 2px; }
      .kpi-value { font-size: 1.65rem; font-weight: 700; color: #1f3b57; line-height: 1.15; }
      .kpi-sub   { font-size: 0.80rem; color: #6b7280; margin-top: 2px; }
      .answer-box {
          background: #eef5f4; border-left: 4px solid #2a9d8f; border-radius: 4px;
          padding: 12px 16px; margin-bottom: 14px; font-size: 0.95rem; color: #1f3b57;
      }
      div[data-testid="stMetricValue"] { font-size: 1.55rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------


@st.cache_resource(show_spinner=False)
def get_connection():
    """One cached SQLite connection for the session.

    It is deliberately never closed: it is a cached resource that Streamlit
    owns for the lifetime of the app.
    """
    return connect(DB_PATH)


@st.cache_data(show_spinner=False)
def query(name: str, params_key: tuple):
    """Run a named query. `params_key` makes the cache depend on the filters."""
    library = load_query_library()
    params = dict(zip(("start_date", "end_date", "category", "segment"), params_key))
    return run_query(get_connection(), name, params, library)


@st.cache_data(show_spinner=False)
def bundle(params_key: tuple):
    library = load_query_library()
    params = dict(zip(("start_date", "end_date", "category", "segment"), params_key))
    return kpi_bundle(get_connection(), params, library)


@st.cache_data(show_spinner=False)
def filters():
    return available_filters(get_connection())


def money(value: float, decimals: int = 0) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"${value:,.{decimals}f}"


def kpi_card(label: str, value: str, sub: str = "") -> str:
    sub_html = f'<div class="kpi-sub">{sub}</div>' if sub else ""
    return (
        f'<div class="kpi-label">{label}</div>'
        f'<div class="kpi-value">{value}</div>{sub_html}'
    )


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title("Shopify Purchasing Behaviour Analysis")
st.caption(
    "Revenue, average order value, customer segmentation and repeat-purchase "
    "behaviour for a two-year Shopify-like transaction history."
)
st.markdown(
    '<div class="synthetic-banner"><b>Synthetic data.</b> This store is generated '
    "by <code>src/generate_data.py</code> with a fixed seed. Every figure below "
    "demonstrates the analysis method; none of them is a measurement of a real "
    "Shopify merchant, customer or order.</div>",
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Sidebar filters
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Filters")

    date_range = st.date_input(
        "Order date range",
        value=(
            pd.Timestamp(PERIOD_START).date(),
            pd.Timestamp(PERIOD_END).date(),
        ),
        min_value=pd.Timestamp(PERIOD_START).date(),
        max_value=pd.Timestamp(PERIOD_END).date(),
        format="YYYY-MM-DD",
        help="Filters on the order date of eligible orders.",
    )
    if isinstance(date_range, (tuple, list)) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date = end_date = date_range

    options = filters()
    category = st.selectbox(
        "Product category",
        ["All categories"] + options["categories"],
        help="Applies at line level: orders are counted when they contain the category.",
    )
    segment = st.selectbox(
        "Customer segment",
        ["All segments"] + options["segments"],
        help="Applies at customer level. Segments are fixed for the full window.",
    )

    if st.button("Reset filters", width="stretch"):
        st.rerun()

    st.divider()
    st.markdown(
        f"**Data window** {PERIOD_START} to {PERIOD_END}  \n"
        "**Currency** USD  \n"
        "**Revenue** net of discounts and refunds, excl. shipping and tax",
    )

params_key = (
    str(start_date),
    str(end_date),
    None if category == "All categories" else category,
    None if segment == "All segments" else segment,
)

# ---------------------------------------------------------------------------
# KPI band
# ---------------------------------------------------------------------------
k = bundle(params_key)

cols = st.columns(5)
with cols[0]:
    st.markdown(
        kpi_card(
            "1 · Total net revenue",
            money(k["total_net_revenue"]),
            f"{money(k['gross_item_revenue'])} gross · {k['effective_discount_pct']}% effective discount",
        ),
        unsafe_allow_html=True,
    )
with cols[1]:
    st.markdown(
        kpi_card(
            "2 · Average order value",
            money(k["avg_order_value"], 2),
            f"{k['eligible_orders']:,} eligible orders",
        ),
        unsafe_allow_html=True,
    )
with cols[2]:
    st.markdown(
        kpi_card(
            "3 · Repeat purchase rate",
            f"{k['repeat_purchase_rate_pct']:.1f}%",
            f"{k['repeat_customers']:,} of {k['unique_customers']:,} customers",
        ),
        unsafe_allow_html=True,
    )
with cols[3]:
    st.markdown(
        kpi_card(
            "4 · Product performance",
            money(k["top_product_revenue"]),
            f"{k['top_product_name']} · {k['top_product_units']:,} units",
        ),
        unsafe_allow_html=True,
    )
with cols[4]:
    st.markdown(
        kpi_card(
            "5 · Customer purchasing behaviour",
            f"{k['avg_orders_per_customer']:.2f}",
            f"orders per customer · {k['units_sold']:,} units sold",
        ),
        unsafe_allow_html=True,
    )

st.divider()

tabs = st.tabs(
    [
        "Retention opportunity",
        "Revenue",
        "Products",
        "Customers",
        "Data quality",
        "Methodology",
    ]
)

# ---------------------------------------------------------------------------
# TAB 1 - RETENTION OPPORTUNITY
# ---------------------------------------------------------------------------
with tabs[0]:
    propensity = query("repeat_propensity_by_first_order", params_key)
    concentration = query("revenue_concentration", params_key)
    curve = query("revenue_concentration_curve", params_key)
    repeat_profile = query("repeat_vs_single_customer_profile", params_key)
    aov = query("aov_by_segment", params_key)
    profile = query("segment_profile", params_key)
    one_time = query("one_time_buyer_profile", params_key)
    gap = query("first_repeat_gap", params_key)

    if propensity.empty or repeat_profile.empty:
        st.info("No eligible orders in the selected range.")
    else:
        one_time_row = repeat_profile[
            repeat_profile["customer_type"] == "One-time customer"
        ].iloc[0]
        repeat_row = repeat_profile[
            repeat_profile["customer_type"] == "Repeat customer"
        ].iloc[0]
        value_multiple = (
            repeat_row["avg_revenue_per_customer"] / one_time_row["avg_revenue_per_customer"]
        )

        st.markdown(
            f"""
<div class="answer-box">
<b>The opportunity:</b> {int(one_time_row['customers']):,} of {k['unique_customers']:,} customers
({100 * one_time_row['customers'] / k['unique_customers']:.0f}%) bought once and never came back.
They hold {100 * one_time_row['customers'] / k['unique_customers']:.0f}% of the customers but only a
fifth of the revenue, while a customer who does repeat is worth
<b>{value_multiple:.1f}x</b> as much ({money(repeat_row['avg_revenue_per_customer'])} vs
{money(one_time_row['avg_revenue_per_customer'])}).
The store does not have a traffic problem - it has a
<b>second-purchase problem</b>.
</div>
""",
            unsafe_allow_html=True,
        )

        gap_row = gap.iloc[0] if not gap.empty else None
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("One-time buyers", f"{int(one_time_row['customers']):,}",
                  f"{100 * one_time_row['customers'] / k['unique_customers']:.0f}% of all customers")
        m2.metric("Value of a repeat buyer", money(repeat_row["avg_revenue_per_customer"]),
                  f"vs {money(one_time_row['avg_revenue_per_customer'])} for a one-time buyer")
        m3.metric(
            "Revenue concentration (Gini)",
            f"{float(concentration.iloc[0]['gini_coefficient']):.2f}"
            if not concentration.empty else "-",
            "0 = perfectly even, 1 = one customer holds everything",
        )
        m4.metric("Median time to a second order",
                  f"{gap_row['median_gap_days']:.0f} days" if gap_row is not None else "-",
                  "the campaign clock, not the 56-day all-repeat average")

        left, right = st.columns(2)

        with left:
            st.subheader("The size of a customer's first order predicts whether they return")
            d = propensity.sort_values("basket_order")
            fig = px.bar(
                d,
                x="first_order_basket",
                y="repeat_rate_pct",
                color="repeat_rate_pct",
                color_continuous_scale=[PALETTE["red"], PALETTE["sand"], PALETTE["teal"], PALETTE["navy"]],
                labels={
                    "first_order_basket": "Size of the first order",
                    "repeat_rate_pct": "Customers who placed a second order (%)",
                },
                custom_data=["customers", "one_time_customers", "avg_lifetime_revenue",
                             "avg_orders_per_customer"],
            )
            fig.update_traces(
                text=[f"{v:.1f}%" for v in d["repeat_rate_pct"]],
                textposition="outside",
                hovertemplate=(
                    "<b>%{x} in the first order</b><br>Repeat rate: %{y:.1f}%"
                    "<br>%{customdata[0]:,} customers (%{customdata[1]:,} never repeated)"
                    "<br>$%{customdata[2]:,.0f} lifetime revenue"
                    "<br>%.2f orders each<extra></extra>"
                ),
            )
            fig.update_layout(
                margin={"l": 10, "r": 10, "t": 10, "b": 10},
                height=320,
                coloraxis_colorbar_title="Repeat rate",
                xaxis_title="Size of the customer's first order",
                yaxis_title="Customers who placed a second order (%)",
            )
            st.plotly_chart(fig, width="stretch")

        with right:
            st.subheader("Revenue is concentrated in a small cohort")
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=[0, 100], y=[0, 100], mode="lines", name="Perfectly even revenue",
                line={"color": PALETTE["grey"], "dash": "dash"},
            ))
            fig.add_trace(go.Scatter(
                x=curve["cumulative_customer_pct"], y=curve["cumulative_revenue_pct"],
                mode="lines+markers", name="Actual concentration",
                line={"color": PALETTE["navy"], "width": 2.5},
                marker={"size": 6},
                fill="tozeroy", fillcolor="rgba(233,196,106,0.22)",
                hovertemplate="Top %{x:.0f}% of customers hold %{y:.1f}% of revenue<extra></extra>",
            ))
            fig.update_layout(
                margin={"l": 10, "r": 10, "t": 10, "b": 10},
                height=320,
                xaxis_title="Customers, ranked by revenue (cumulative %)",
                yaxis_title="Revenue (cumulative %)",
                legend={"orientation": "h", "y": -0.22},
            )
            st.plotly_chart(fig, width="stretch")
            if not concentration.empty:
                conc = concentration.iloc[0]
                st.caption(
                    f"The top 10% of customers hold "
                    f"{conc['top_10pct_customers_revenue_pct']:.1f}% of revenue; "
                    f"the top 20% hold {conc['top_20pct_customers_revenue_pct']:.1f}%. "
                    "Losing a small cohort would cost a large share of revenue."
                )

        left, right = st.columns(2)

        with left:
            st.subheader("What a repeat buyer is worth")
            d = repeat_profile.sort_values("avg_revenue_per_customer")
            fig = px.bar(
                d,
                x="customer_type",
                y="avg_revenue_per_customer",
                color="customer_type",
                color_discrete_sequence=[PALETTE["grey"], PALETTE["teal"]],
                labels={
                    "customer_type": "",
                    "avg_revenue_per_customer": "Average revenue per customer (USD)",
                },
                custom_data=["customers", "avg_orders", "avg_order_value", "avg_categories"],
            )
            fig.update_traces(
                text=[money(v) for v in d["avg_revenue_per_customer"]],
                textposition="outside",
                hovertemplate=(
                    "<b>%{x}</b><br>Avg revenue/customer: $%{y:,.0f}"
                    "<br>%{customdata[0]:,} customers · %{customdata[1]:.2f} orders each"
                    "<br>$%{customdata[2]:,.0f} AOV · %{customdata[3]:.2f} categories<extra></extra>"
                ),
            )
            fig.update_layout(
                margin={"l": 10, "r": 10, "t": 10, "b": 10},
                height=300,
                showlegend=False,
                yaxis_title="Average revenue per customer (USD)",
                xaxis_title=None,
            )
            st.plotly_chart(fig, width="stretch")

        with right:
            st.subheader("Which segments to target")
            if aov.empty:
                st.info("No eligible orders in the selected range.")
            else:
                d = aov.sort_values("avg_order_value")
                fig = px.bar(
                    d,
                    x="avg_order_value",
                    y="segment",
                    orientation="h",
                    color="segment",
                    color_discrete_map=SEGMENT_COLORS,
                    labels={"avg_order_value": "Average order value (USD)", "segment": ""},
                    custom_data=["orders", "customers", "revenue_per_customer",
                                 "pct_of_revenue", "units_per_order"],
                )
                fig.update_traces(
                    text=[f"${v:,.0f}" for v in d["avg_order_value"]],
                    textposition="outside",
                    hovertemplate=(
                        "<b>%{y}</b><br>AOV: $%{x:,.2f}"
                        "<br>%{customdata[0]:,} orders · %{customdata[1]:,} customers"
                        "<br>$%{customdata[2]:,.0f} per customer"
                        "<br>%{customdata[3]:.2f}% of revenue"
                        "<br>%.2f units per order<extra></extra>"
                    ),
                )
                fig.update_layout(
                    margin={"l": 10, "r": 40, "t": 10, "b": 10},
                    height=300,
                    showlegend=False,
                    xaxis_title="Average order value (USD)",
                    yaxis_title=None,
                )
                st.plotly_chart(fig, width="stretch")

        st.subheader("What one-time buyers bought - the categories to offer them again")
        if one_time.empty:
            st.info("No eligible orders in the selected range.")
        else:
            st.dataframe(
                one_time[
                    ["category", "one_time_customers", "one_time_revenue",
                     "one_time_aov", "units_per_order"]
                ],
                hide_index=True,
                width="stretch",
                column_config={
                    "one_time_revenue": st.column_config.NumberColumn("Net revenue", format="$%.2f"),
                    "one_time_aov": st.column_config.NumberColumn("AOV", format="$%.2f"),
                    "units_per_order": st.column_config.NumberColumn("Units / order", format="%.2f"),
                    "one_time_customers": st.column_config.NumberColumn("Customers", format="%d"),
                },
            )

        st.subheader("Segment scorecard")
        if not profile.empty:
            st.dataframe(
                profile[
                    [
                        "segment", "customers", "pct_of_customers", "net_revenue",
                        "pct_of_revenue", "avg_orders_per_customer",
                        "avg_revenue_per_customer", "avg_order_value",
                        "avg_units_per_customer", "avg_categories", "repeat_customer_pct",
                    ]
                ],
                hide_index=True,
                width="stretch",
                column_config={
                    "net_revenue": st.column_config.NumberColumn("Net revenue", format="$%.2f"),
                    "pct_of_revenue": st.column_config.NumberColumn("% of revenue", format="%.2f%%"),
                    "pct_of_customers": st.column_config.NumberColumn("% of customers", format="%.2f%%"),
                    "avg_orders_per_customer": st.column_config.NumberColumn("Avg orders", format="%.2f"),
                    "avg_revenue_per_customer": st.column_config.NumberColumn("Avg revenue / customer", format="$%.2f"),
                    "avg_order_value": st.column_config.NumberColumn("AOV", format="$%.2f"),
                    "avg_units_per_customer": st.column_config.NumberColumn("Avg units", format="%.2f"),
                    "avg_categories": st.column_config.NumberColumn("Avg categories", format="%.2f"),
                    "repeat_customer_pct": st.column_config.NumberColumn("Repeat customers", format="%.2f%%"),
                    "customers": st.column_config.NumberColumn("Customers", format="%d"),
                },
            )

# ---------------------------------------------------------------------------
# TAB 2 - Revenue
# ---------------------------------------------------------------------------
with tabs[1]:
    monthly = query("monthly_revenue_trend", params_key)
    by_category = query("revenue_by_category", params_key)
    bridge = query("revenue_bridge", params_key)
    growth = query("revenue_growth_decomposition", params_key)
    tenure = query("orders_by_customer_tenure", params_key)

    left, right = st.columns([2, 1])

    with left:
        st.subheader("Revenue over time")
        if monthly.empty:
            st.info("No eligible orders in the selected range.")
        else:
            fig = px.area(
                monthly,
                x="order_month",
                y="net_revenue",
                color_discrete_sequence=[PALETTE["teal"]],
                labels={"order_month": "Month", "net_revenue": "Net revenue (USD)"},
            )
            fig.update_traces(
                line={"width": 2.5},
                hovertemplate="<b>%{x}</b><br>Net revenue: $%{y:,.0f}<extra></extra>",
            )
            fig.update_layout(
                margin={"l": 10, "r": 10, "t": 10, "b": 10},
                height=380,
                showlegend=False,
                xaxis_title=None,
                yaxis_title="Net revenue (USD)",
                hovermode="x unified",
            )
            st.plotly_chart(fig, width="stretch")

    with right:
        st.subheader("Revenue bridge")
        fig = go.Figure(
            go.Waterfall(
                orientation="v",
                measure=["relative", "relative", "total", "relative", "total"],
                x=bridge["step"],
                y=bridge["amount"],
                connector={"line": {"color": PALETTE["grey"]}},
                increasing={"marker": {"color": PALETTE["teal"]}},
                decreasing={"marker": {"color": PALETTE["red"]}},
                totals={"marker": {"color": PALETTE["navy"]}},
                text=[money(v) for v in bridge["amount"]],
                textposition="outside",
                hovertemplate="%{x}<br>%{y:,.2f}<extra></extra>",
            )
        )
        fig.update_layout(
            margin={"l": 10, "r": 10, "t": 10, "b": 10},
            height=380,
            showlegend=False,
            yaxis_title="USD",
        )
        st.plotly_chart(fig, width="stretch")

    st.subheader("What explains the change in revenue")
    if growth.empty or growth.iloc[-1]["customers_yoy_pct"] is None:
        st.info("A year-on-year comparison needs at least two years of data in the selection.")
    else:
        latest = growth.iloc[-1]
        factors = [
            ("Customers", latest["customers_yoy_pct"], PALETTE["teal"]),
            ("Orders per customer", latest["frequency_yoy_pct"], PALETTE["red"]),
            ("Average order value", latest["aov_yoy_pct"], PALETTE["red"]),
        ]
        fig = px.bar(
            [f[0] for f in factors], [f[1] for f in factors],
            labels={"x": "", "y": "Change year on year (%)"},
            color=[f[2] for f in factors],
        )
        fig.update_traces(
            text=[f"{v:+.1f}%" for _, v, _ in factors], textposition="outside",
            hovertemplate="<b>%{x}</b><br>%{y:+.1f}% year on year<extra></extra>",
        )
        fig.add_hline(y=0, line_color=PALETTE["dark"], line_width=1)
        fig.update_layout(
            margin={"l": 10, "r": 10, "t": 10, "b": 10},
            height=300,
            showlegend=False,
        )
        st.plotly_chart(fig, width="stretch")
        st.caption(
            f"Revenue grew {latest['revenue_yoy_pct']:.1f}%. That is customers "
            f"{latest['customers_yoy_pct']:+.1f}%, orders per customer "
            f"{latest['frequency_yoy_pct']:+.1f}% and average order value "
            f"{latest['aov_yoy_pct']:+.1f}% - and the three factors reproduce the "
            f"total to {latest['implied_revenue_yoy_pct']:.1f}%."
        )

        if not tenure.empty:
            d = tenure.copy()
            d["label"] = d["order_year"] + " - " + d["order_type"].map(
                lambda s: "acquisition year" if s.startswith("Acquisition") else "existing customer"
            )
            fig = px.bar(
                d, x="label", y="orders_per_customer",
                color="order_type", color_discrete_sequence=[PALETTE["sand"], PALETTE["navy"]],
                labels={
                    "label": "", "orders_per_customer": "Orders per customer",
                    "order_type": "",
                },
                custom_data=["customers", "orders"],
            )
            fig.update_traces(
                text=[f"{v:.2f}" for v in d["orders_per_customer"]],
                textposition="outside",
                hovertemplate=(
                    "<b>%{x}</b><br>%{y:.2f} orders per customer"
                    "<br>%{customdata[0]:,} customers · %{customdata[1]:,} orders<extra></extra>"
                ),
            )
            fig.update_layout(
                margin={"l": 10, "r": 10, "t": 10, "b": 10},
                height=320,
                legend={"orientation": "h", "y": -0.2},
                yaxis_title="Orders per customer",
            )
            st.plotly_chart(fig, width="stretch")
            st.caption(
                "A fall in the blended average is not the same as worse customers: "
                "the cohort carried over from the previous year bought more, while a "
                "much larger new cohort of mostly one-time buyers dilutes the average."
            )

    st.subheader("Revenue by product category")
    if by_category.empty:
        st.info("No eligible orders in the selected range.")
    else:
        cat = by_category.sort_values("net_revenue")
        fig = px.bar(
            cat,
            x="net_revenue",
            y="category",
            orientation="h",
            color="pct_of_revenue",
            color_continuous_scale=[PALETTE["light"], PALETTE["teal"], PALETTE["navy"]],
            labels={
                "net_revenue": "Net revenue (USD)",
                "category": "",
                "pct_of_revenue": "% of revenue",
            },
            custom_data=["orders", "units_sold", "avg_order_value", "revenue_per_unit"],
        )
        fig.update_traces(
            hovertemplate=(
                "<b>%{y}</b><br>Net revenue: $%{x:,.0f}"
                "<br>%{customdata[2]:.0f} avg order value"
                "<br>%{customdata[0]:,} orders · %{customdata[1]:,} units<extra></extra>"
            )
        )
        fig.update_layout(
            margin={"l": 10, "r": 10, "t": 10, "b": 10},
            height=340,
            coloraxis_colorbar_title="% of revenue",
            yaxis_title=None,
        )
        st.plotly_chart(fig, width="stretch")

        st.dataframe(
            by_category[
                [
                    "category", "orders", "customers", "units_sold", "net_revenue",
                    "avg_order_value", "revenue_per_unit", "pct_of_revenue",
                ]
            ],
            hide_index=True,
            width="stretch",
            column_config={
                "net_revenue": st.column_config.NumberColumn("Net revenue", format="$%.2f"),
                "avg_order_value": st.column_config.NumberColumn("AOV", format="$%.2f"),
                "revenue_per_unit": st.column_config.NumberColumn("Revenue / unit", format="$%.2f"),
                "pct_of_revenue": st.column_config.NumberColumn("% of revenue", format="%.2f%%"),
                "orders": st.column_config.NumberColumn("Orders", format="%d"),
                "customers": st.column_config.NumberColumn("Customers", format="%d"),
                "units_sold": st.column_config.NumberColumn("Units", format="%d"),
            },
        )


# ---------------------------------------------------------------------------
# TAB 3 - Products
# ---------------------------------------------------------------------------
with tabs[2]:
    top_rev = query("top_products_by_revenue", params_key)
    top_units = query("top_products_by_units", params_key)
    dilutive = query("high_volume_low_revenue_products", params_key)

    left, right = st.columns(2)

    with left:
        st.subheader("Top 10 products by revenue")
        if top_rev.empty:
            st.info("No eligible orders in the selected range.")
        else:
            d = top_rev.sort_values("net_revenue")
            fig = px.bar(
                d,
                x="net_revenue",
                y="product_name",
                orientation="h",
                color="category",
                color_discrete_sequence=CATEGORY_SEQUENCE,
                labels={"net_revenue": "Net revenue (USD)", "product_name": ""},
                custom_data=["units_sold", "orders", "revenue_per_unit", "pct_of_revenue"],
            )
            fig.update_traces(
                hovertemplate=(
                    "<b>%{y}</b><br>Net revenue: $%{x:,.0f}"
                    "<br>%{customdata[0]:,} units · %{customdata[1]:,} orders"
                    "<br>$%{customdata[2]:.2f} per unit · %{customdata[3]:.2f}% of revenue"
                    "<extra></extra>"
                )
            )
            fig.update_layout(
                margin={"l": 10, "r": 10, "t": 10, "b": 10},
                height=430,
                legend_title_text="",
                xaxis_title="Net revenue (USD)",
                yaxis_title=None,
            )
            st.plotly_chart(fig, width="stretch")

    with right:
        st.subheader("Top 10 products by units sold")
        if top_units.empty:
            st.info("No eligible orders in the selected range.")
        else:
            d = top_units.sort_values("units_sold")
            fig = px.bar(
                d,
                x="units_sold",
                y="product_name",
                orientation="h",
                color="net_revenue",
                color_continuous_scale=[PALETTE["light"], PALETTE["sand"], PALETTE["orange"]],
                labels={"units_sold": "Units sold", "product_name": ""},
                custom_data=["net_revenue", "revenue_per_unit", "pct_of_revenue"],
            )
            fig.update_traces(
                hovertemplate=(
                    "<b>%{y}</b><br>Units sold: %{x:,}"
                    "<br>Net revenue: $%{customdata[0]:,.0f}"
                    "<br>$%{customdata[1]:.2f} per unit<extra></extra>"
                )
            )
            fig.update_layout(
                margin={"l": 10, "r": 10, "t": 10, "b": 10},
                height=430,
                coloraxis_colorbar_title="Net revenue",
                xaxis_title="Units sold",
                yaxis_title=None,
            )
            st.plotly_chart(fig, width="stretch")

    st.subheader("High-volume, low-revenue products")
    st.caption(
        "Products in the top quartile by units sold but the bottom half by revenue "
        "per unit. They add volume to the basket while diluting the average order."
    )
    if dilutive.empty:
        st.info("No products match the high-volume / low-revenue definition in this selection.")
    else:
        st.dataframe(
            dilutive[
                [
                    "product_id", "product_name", "category", "units_sold",
                    "net_revenue", "revenue_per_unit", "pct_of_units",
                    "pct_of_revenue", "revenue_minus_unit_share_pts",
                ]
            ],
            hide_index=True,
            width="stretch",
            column_config={
                "net_revenue": st.column_config.NumberColumn("Net revenue", format="$%.2f"),
                "revenue_per_unit": st.column_config.NumberColumn("Revenue / unit", format="$%.2f"),
                "pct_of_units": st.column_config.NumberColumn("% of units", format="%.2f%%"),
                "pct_of_revenue": st.column_config.NumberColumn("% of revenue", format="%.2f%%"),
                "revenue_minus_unit_share_pts": st.column_config.NumberColumn(
                    "Revenue share - unit share (pts)", format="%.2f"
                ),
                "units_sold": st.column_config.NumberColumn("Units", format="%d"),
                "orders": st.column_config.NumberColumn("Orders", format="%d"),
            },
        )


# ---------------------------------------------------------------------------
# TAB 4 - Customers
# ---------------------------------------------------------------------------
with tabs[3]:
    distribution = query("segment_distribution", params_key)
    orders_dist = query("order_count_distribution", params_key)
    gaps = query("interpurchase_interval", params_key)

    left, right = st.columns(2)

    with left:
        st.subheader("Customer segment distribution")
        st.caption(
            "Customers and revenue by segment. Hover the revenue legend entry to "
            "switch the rings from customers to revenue."
        )
        if distribution.empty:
            st.info("No eligible orders in the selected range.")
        else:
            fig = go.Figure()
            fig.add_trace(
                go.Pie(
                    labels=distribution["segment"],
                    values=distribution["customers"],
                    name="Customers",
                    hole=0.55,
                    marker={"colors": [SEGMENT_COLORS.get(s, PALETTE["grey"]) for s in distribution["segment"]],
                            "line": {"color": "white", "width": 2}},
                    hovertemplate="<b>%{label}</b><br>%{value:,} customers<br>%{percent:.1%}<extra></extra>",
                )
            )
            fig.add_trace(
                go.Pie(
                    labels=distribution["segment"],
                    values=distribution["net_revenue"],
                    name="Revenue",
                    hole=0.55,
                    visible="legendonly",
                    marker={"colors": [SEGMENT_COLORS.get(s, PALETTE["grey"]) for s in distribution["segment"]],
                            "line": {"color": "white", "width": 2}},
                    hovertemplate="<b>%{label}</b><br>$%{value:,.0f}<extra></extra>",
                )
            )
            fig.update_layout(
                margin={"l": 10, "r": 10, "t": 10, "b": 10},
                height=320,
                showlegend=True,
                legend_title_text="",
            )
            st.plotly_chart(fig, width="stretch")
            top = distribution.sort_values("net_revenue", ascending=False).iloc[0]
            st.caption(
                f"{top['segment']} holds {top['pct_of_customers']:.1f}% of the "
                f"customers but {top['pct_of_revenue']:.1f}% of the revenue - "
                f"${top['revenue_per_customer']:,.0f} per customer."
            )

    with right:
        st.subheader("Orders per customer")
        if orders_dist.empty:
            st.info("No eligible orders in the selected range.")
        else:
            d = orders_dist.copy()
            d["label"] = d["orders"].astype(str) + (
                d["orders"].map(lambda v: "+" if v >= 10 else "")
            )
            fig = px.bar(
                d,
                x="label",
                y="customers",
                color="pct_of_customers",
                color_continuous_scale=[PALETTE["light"], PALETTE["teal"], PALETTE["navy"]],
                labels={
                    "label": "Orders placed",
                    "customers": "Customers",
                    "pct_of_customers": "% of customers",
                },
            )
            fig.update_traces(
                hovertemplate="<b>%{x} orders</b><br>%{y:,} customers (%{customdata[0]:.2f}%)<extra></extra>",
                customdata=d[["pct_of_customers"]].to_numpy(),
            )
            fig.update_layout(
                margin={"l": 10, "r": 10, "t": 10, "b": 10},
                height=320,
                coloraxis_colorbar_title="% of customers",
                xaxis_title="Orders placed",
                yaxis_title="Customers",
            )
            st.plotly_chart(fig, width="stretch")

    st.subheader("Time between purchases")
    st.caption(
        "Measured between consecutive eligible orders of the same customer, "
        "inside the observation window only."
    )
    if gaps.empty:
        st.info("No repeat orders in the selected range.")
    else:
        g = gaps.iloc[0]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Median gap between orders", f"{g['median_gap_days']:.0f} days")
        c2.metric("Mean gap between orders", f"{g['avg_gap_days']:.0f} days")
        c3.metric("Repeats within 30 days", f"{g['pct_repeat_within_30_days']:.1f}%")
        c4.metric("Repeats within 90 days", f"{g['pct_repeat_within_90_days']:.1f}%")
        st.caption(
            "The mean sits far above the median because of a long dormant tail - "
            "customers who come back after six months or more."
        )


# ---------------------------------------------------------------------------
# TAB 5 - Data quality
# ---------------------------------------------------------------------------
with tabs[4]:
    dq = query("dq_summary", (PERIOD_START, PERIOD_END, None, None))
    recon = query("dq_row_reconciliation", (PERIOD_START, PERIOD_END, None, None))
    status = query("dq_reconciliation_explained", (PERIOD_START, PERIOD_END, None, None))

    st.subheader("Validation checks on the raw landing tables")
    st.caption(
        "The raw tables intentionally contain defects so the cleaning rules have "
        "something real to catch. Each check states what it found and how the "
        "record is handled."
    )
    st.dataframe(
        dq,
        hide_index=True,
        width="stretch",
        column_config={
            "check_id": st.column_config.TextColumn("ID", width="small"),
            "check_name": st.column_config.TextColumn("Check", width="medium"),
            "severity": st.column_config.TextColumn("Severity", width="small"),
            "records_flagged": st.column_config.NumberColumn("Records flagged", format="%d"),
            "resolution": st.column_config.TextColumn("Handling", width="large"),
        },
    )

    left, right = st.columns(2)
    with left:
        st.subheader("Row counts through the pipeline")
        st.dataframe(recon, hide_index=True, width="stretch")
    with right:
        st.subheader("Reconciliation variance")
        if status.empty:
            st.success("Every eligible order reconciles with its line items.")
        else:
            unexplained = int((status["status"] != "Explained by a logged defect").sum())
            if unexplained:
                st.error(f"{unexplained} unexplained variances — investigate.")
            else:
                st.success(
                    f"All {len(status):,} variances are explained by a logged defect."
                )
            st.dataframe(status, hide_index=True, width="stretch")

# ---------------------------------------------------------------------------
# TAB 6 - Methodology
# ---------------------------------------------------------------------------
with tabs[5]:
    st.subheader("How these numbers are produced")
    st.markdown(
        """
**Revenue** — `quantity x unit_price x (1 - discount_pct)` per line, minus the
order's refund allocated pro-rata by net line value. Shipping and tax are
collected for third parties and are **not** revenue; they are reported
separately. All money is computed in integer cents so the generator, the SQL
and the stored order totals agree to the cent.

**Eligible orders** — a `completed` order inside the observation window that
resolves to a known customer and contains at least one valid line item.
Cancelled, refunded and pending orders stay in the raw tables and are excluded
from every revenue and order-count metric.

**Segmentation** — `value_score = 0.65 x spend percentile + 0.35 x order-count
percentile`, banded with `NTILE(3)` over `(value_score, customer_id)`. The
percentiles make the method scale-free and the `customer_id` tiebreak makes the
banding deterministic. Recency is deliberately excluded: in a right-censored
window a recent registrant has simply had less time to buy.

**Repeat customer** — at least two eligible orders inside the observation
window. The window is finite, so a customer who bought once before it opened is
indistinguishable from a one-time buyer here.

**Filters** — the date filter applies at order level, the category filter at
line level, and the segment filter at customer level. Segments are computed
once on the full population and are not recomputed when a filter changes. The
repeat purchase rate is recomputed inside the filtered selection, so it can
differ from the unfiltered headline.
        """
    )
    st.caption(
        "Full definitions: `docs/methodology.md`. Query-by-query output: "
        "`reports/sql_results.md`. Decision report: `reports/business_analysis.md`."
    )
