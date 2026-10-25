-- ===========================================================================
-- ANALYSIS QUERIES
-- ===========================================================================
-- Every query below is a standalone statement with a `-- name:` header and a
-- `-- description:` line. `src/run_queries.py` parses those headers, runs each
-- statement against the database and renders the results into
-- `reports/sql_results.md`. `src/analytics.py` exposes the same statements to
-- the dashboard, and `tests/test_analysis.py` asserts that the dashboard KPIs
-- equal the values these queries produce.
--
-- FILTER PARAMETERS
--   :start_date  TEXT  inclusive lower bound on order_date (ISO-8601)
--   :end_date    TEXT  inclusive upper bound on order_date (ISO-8601)
--   :category    TEXT  product category, or NULL for all categories
--   :segment     TEXT  customer segment name, or NULL for all segments
--
-- FILTER SEMANTICS (identical in every query)
--   * The date filter is applied at ORDER level.
--   * The category filter is applied at LINE level. When a category is
--     selected, "orders" means orders containing at least one line in that
--     category, and revenue/units count only those lines. AOV under a category
--     filter is therefore "revenue per order that contained the category".
--   * The segment filter is applied at CUSTOMER level. Segments are computed
--     once on the full eligible population (see sql/03_marts.sql, rule R7) and
--     are NOT recomputed when a filter changes.
--   * `filtered_lines` is the shared CTE that implements all of the above.
--
-- REVENUE DEFINITION (see sql/03_marts.sql, rule R6)
--   net line revenue = quantity x unit_price x (1 - discount_pct)
--   net revenue      = net line revenue - allocated refund
--   Shipping and tax are NOT revenue and never appear in a revenue column.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- name: kpi_summary
-- description: The five headline KPIs plus supporting counts, in one row.
-- ---------------------------------------------------------------------------
WITH filtered_lines AS (
    SELECT
        f.customer_id, f.order_id, f.order_date, f.order_month,
        f.product_id, f.product_name, f.category, f.quantity,
        f.gross_line_amount, f.discount_amount, f.net_line_amount,
        f.refund_allocated, f.net_revenue,
        d.segment
    FROM fact_order_lines f
    JOIN dim_customers d ON d.customer_id = f.customer_id
    WHERE f.order_date BETWEEN :start_date AND :end_date
      AND (:category IS NULL OR f.category = :category)
      AND (:segment IS NULL OR d.segment = :segment)
),
cust AS (
    SELECT customer_id, COUNT(DISTINCT order_id) AS orders
    FROM filtered_lines GROUP BY customer_id
)
SELECT
    ROUND(SUM(net_revenue), 2)                                       AS total_net_revenue,
    COUNT(DISTINCT order_id)                                         AS eligible_orders,
    SUM(quantity)                                                    AS units_sold,
    COUNT(DISTINCT customer_id)                                      AS unique_customers,
    ROUND(SUM(net_revenue) / NULLIF(COUNT(DISTINCT order_id), 0), 2) AS avg_order_value,
    (SELECT COUNT(*) FROM cust)                                      AS customers_with_orders,
    (SELECT COUNT(*) FROM cust WHERE orders >= 2)                    AS repeat_customers,
    ROUND(100.0 * (SELECT COUNT(*) FROM cust WHERE orders >= 2)
          / NULLIF((SELECT COUNT(*) FROM cust), 0), 2)                AS repeat_purchase_rate_pct,
    ROUND((SELECT SUM(orders) FROM cust) * 1.0
          / NULLIF((SELECT COUNT(*) FROM cust), 0), 3)                AS avg_orders_per_customer,
    ROUND(SUM(gross_line_amount), 2)                                 AS gross_item_revenue,
    ROUND(SUM(discount_amount), 2)                                   AS discounts_given,
    ROUND(100.0 * SUM(discount_amount)
          / NULLIF(SUM(gross_line_amount), 0), 2)                    AS effective_discount_pct,
    ROUND(SUM(refund_allocated), 2)                                  AS refunds_given,
    ROUND(100.0 * SUM(refund_allocated)
          / NULLIF(SUM(gross_line_amount), 0), 2)                    AS effective_refund_pct
FROM filtered_lines;

-- ---------------------------------------------------------------------------
-- name: revenue_bridge
-- description: From gross item revenue to net revenue, one row per step.
-- ---------------------------------------------------------------------------
WITH filtered_lines AS (
    SELECT
        f.order_id, f.customer_id, f.gross_line_amount, f.discount_amount,
        f.net_line_amount, f.refund_allocated, f.net_revenue,
        d.segment
    FROM fact_order_lines f
    JOIN dim_customers d ON d.customer_id = f.customer_id
    WHERE f.order_date BETWEEN :start_date AND :end_date
      AND (:category IS NULL OR f.category = :category)
      AND (:segment IS NULL OR d.segment = :segment)
)
SELECT step_order, step_label AS step, ROUND(amount, 2) AS amount FROM (
    SELECT 1 AS step_order, '1. Gross item revenue (qty x list price)' AS step_label,
           SUM(gross_line_amount) AS amount FROM filtered_lines
    UNION ALL
    SELECT 2, '2. Less line discounts', -SUM(discount_amount) FROM filtered_lines
    UNION ALL
    SELECT 3, '3. Net item revenue (after discounts)', SUM(net_line_amount) FROM filtered_lines
    UNION ALL
    SELECT 4, '4. Less refunds allocated', -SUM(refund_allocated) FROM filtered_lines
    UNION ALL
    SELECT 5, '5. NET REVENUE', SUM(net_revenue) FROM filtered_lines
)
ORDER BY step_order;

-- ---------------------------------------------------------------------------
-- name: monthly_revenue_trend
-- description: Monthly net revenue, orders, units, customers and AOV.
-- ---------------------------------------------------------------------------
WITH filtered_lines AS (
    SELECT
        f.customer_id, f.order_id, f.order_month, f.quantity, f.net_revenue,
        d.segment
    FROM fact_order_lines f
    JOIN dim_customers d ON d.customer_id = f.customer_id
    WHERE f.order_date BETWEEN :start_date AND :end_date
      AND (:category IS NULL OR f.category = :category)
      AND (:segment IS NULL OR d.segment = :segment)
)
SELECT
    order_month,
    COUNT(DISTINCT order_id)                                         AS orders,
    COUNT(DISTINCT customer_id)                                      AS customers,
    SUM(quantity)                                                    AS units_sold,
    ROUND(SUM(net_revenue), 2)                                       AS net_revenue,
    ROUND(SUM(net_revenue) / NULLIF(COUNT(DISTINCT order_id), 0), 2) AS avg_order_value,
    ROUND(AVG(net_revenue), 2)                                       AS revenue_per_line_item
FROM filtered_lines
GROUP BY order_month
ORDER BY order_month;

-- ---------------------------------------------------------------------------
-- name: monthly_new_vs_repeat_revenue
-- description: Revenue split between first-time and repeat orders per month.
-- ---------------------------------------------------------------------------
WITH filtered_lines AS (
    SELECT f.customer_id, f.order_id, f.order_date, f.order_month, f.net_revenue,
           d.segment
    FROM fact_order_lines f
    JOIN dim_customers d ON d.customer_id = f.customer_id
    WHERE f.order_date BETWEEN :start_date AND :end_date
      AND (:category IS NULL OR f.category = :category)
      AND (:segment IS NULL OR d.segment = :segment)
),
orders AS (
    SELECT
        order_id, customer_id, order_date, order_month,
        SUM(net_revenue) AS order_revenue,
        ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY order_date, order_id) AS order_seq
    FROM filtered_lines
    GROUP BY order_id, customer_id, order_date, order_month
)
SELECT
    order_month,
    COUNT(*)                                                        AS orders,
    SUM(CASE WHEN order_seq = 1 THEN 1 ELSE 0 END)                   AS first_orders,
    SUM(CASE WHEN order_seq > 1 THEN 1 ELSE 0 END)                   AS repeat_orders,
    ROUND(SUM(CASE WHEN order_seq = 1 THEN order_revenue ELSE 0 END), 2) AS first_order_revenue,
    ROUND(SUM(CASE WHEN order_seq > 1 THEN order_revenue ELSE 0 END), 2) AS repeat_order_revenue,
    ROUND(100.0 * SUM(CASE WHEN order_seq > 1 THEN order_revenue ELSE 0 END)
          / NULLIF(SUM(order_revenue), 0), 2)                        AS repeat_revenue_pct
FROM orders
GROUP BY order_month
ORDER BY order_month;

-- ---------------------------------------------------------------------------
-- name: revenue_by_category
-- description: Net revenue, units and orders per product category.
-- ---------------------------------------------------------------------------
WITH filtered_lines AS (
    SELECT f.customer_id, f.order_id, f.category, f.quantity, f.net_revenue,
           f.gross_line_amount, d.segment
    FROM fact_order_lines f
    JOIN dim_customers d ON d.customer_id = f.customer_id
    WHERE f.order_date BETWEEN :start_date AND :end_date
      AND (:category IS NULL OR f.category = :category)
      AND (:segment IS NULL OR d.segment = :segment)
)
SELECT
    category,
    COUNT(DISTINCT order_id)                                         AS orders,
    COUNT(DISTINCT customer_id)                                      AS customers,
    SUM(quantity)                                                    AS units_sold,
    ROUND(SUM(net_revenue), 2)                                       AS net_revenue,
    ROUND(SUM(net_revenue) / NULLIF(COUNT(DISTINCT order_id), 0), 2) AS avg_order_value,
    ROUND(SUM(net_revenue) / NULLIF(SUM(quantity), 0), 2)            AS revenue_per_unit,
    ROUND(100.0 * SUM(net_revenue)
          / NULLIF(SUM(SUM(net_revenue)) OVER (), 0), 2)             AS pct_of_revenue
FROM filtered_lines
GROUP BY category
ORDER BY net_revenue DESC;

-- ---------------------------------------------------------------------------
-- name: category_revenue_share
-- description: Revenue share vs unit share per category (price-mix effect).
-- ---------------------------------------------------------------------------
WITH filtered_lines AS (
    SELECT f.customer_id, f.order_id, f.category, f.quantity, f.net_revenue,
           d.segment
    FROM fact_order_lines f
    JOIN dim_customers d ON d.customer_id = f.customer_id
    WHERE f.order_date BETWEEN :start_date AND :end_date
      AND (:category IS NULL OR f.category = :category)
      AND (:segment IS NULL OR d.segment = :segment)
)
SELECT
    category,
    ROUND(SUM(net_revenue), 2)                                       AS net_revenue,
    ROUND(100.0 * SUM(net_revenue)
          / NULLIF(SUM(SUM(net_revenue)) OVER (), 0), 2)             AS pct_of_revenue,
    SUM(quantity)                                                    AS units_sold,
    ROUND(100.0 * SUM(quantity)
          / NULLIF(SUM(SUM(quantity)) OVER (), 0), 2)                AS pct_of_units,
    ROUND(100.0 * (SUM(net_revenue)
          / NULLIF(SUM(SUM(net_revenue)) OVER (), 0))
          - (100.0 * SUM(quantity)
          / NULLIF(SUM(SUM(quantity)) OVER (), 0)), 2)               AS revenue_minus_unit_share_pts,
    ROUND(SUM(net_revenue) / NULLIF(SUM(quantity), 0), 2)            AS revenue_per_unit
FROM filtered_lines
GROUP BY category
ORDER BY net_revenue DESC;

-- ---------------------------------------------------------------------------
-- name: top_products_by_revenue
-- description: The 10 products with the highest net revenue.
-- ---------------------------------------------------------------------------
WITH filtered_lines AS (
    SELECT f.customer_id, f.order_id, f.product_id, f.product_name, f.category,
           f.quantity, f.net_revenue, d.segment
    FROM fact_order_lines f
    JOIN dim_customers d ON d.customer_id = f.customer_id
    WHERE f.order_date BETWEEN :start_date AND :end_date
      AND (:category IS NULL OR f.category = :category)
      AND (:segment IS NULL OR d.segment = :segment)
)
SELECT
    product_id, product_name, category,
    ROUND(SUM(net_revenue), 2)                                       AS net_revenue,
    SUM(quantity)                                                    AS units_sold,
    COUNT(DISTINCT order_id)                                         AS orders,
    COUNT(DISTINCT customer_id)                                      AS customers,
    ROUND(SUM(net_revenue) / NULLIF(SUM(quantity), 0), 2)            AS revenue_per_unit,
    ROUND(100.0 * SUM(net_revenue)
          / NULLIF(SUM(SUM(net_revenue)) OVER (), 0), 2)             AS pct_of_revenue
FROM filtered_lines
GROUP BY product_id, product_name, category
ORDER BY net_revenue DESC
LIMIT 10;

-- ---------------------------------------------------------------------------
-- name: top_products_by_units
-- description: The 10 products with the highest unit volume.
-- ---------------------------------------------------------------------------
WITH filtered_lines AS (
    SELECT f.customer_id, f.order_id, f.product_id, f.product_name, f.category,
           f.quantity, f.net_revenue, d.segment
    FROM fact_order_lines f
    JOIN dim_customers d ON d.customer_id = f.customer_id
    WHERE f.order_date BETWEEN :start_date AND :end_date
      AND (:category IS NULL OR f.category = :category)
      AND (:segment IS NULL OR d.segment = :segment)
)
SELECT
    product_id, product_name, category,
    SUM(quantity)                                                    AS units_sold,
    ROUND(SUM(net_revenue), 2)                                       AS net_revenue,
    COUNT(DISTINCT order_id)                                         AS orders,
    COUNT(DISTINCT customer_id)                                      AS customers,
    ROUND(SUM(net_revenue) / NULLIF(SUM(quantity), 0), 2)            AS revenue_per_unit,
    ROUND(100.0 * SUM(net_revenue)
          / NULLIF(SUM(SUM(net_revenue)) OVER (), 0), 2)             AS pct_of_revenue
FROM filtered_lines
GROUP BY product_id, product_name, category
ORDER BY units_sold DESC
LIMIT 10;

-- ---------------------------------------------------------------------------
-- name: high_volume_low_revenue_products
-- description: Products in the top quartile of units but the bottom half of
-- description: revenue per unit - they sell a lot but dilute the average order.
-- ---------------------------------------------------------------------------
WITH filtered_lines AS (
    SELECT f.customer_id, f.order_id, f.product_id, f.product_name, f.category,
           f.quantity, f.net_revenue, d.segment
    FROM fact_order_lines f
    JOIN dim_customers d ON d.customer_id = f.customer_id
    WHERE f.order_date BETWEEN :start_date AND :end_date
      AND (:category IS NULL OR f.category = :category)
      AND (:segment IS NULL OR d.segment = :segment)
),
product_totals AS (
    SELECT
        product_id, product_name, category,
        SUM(quantity)    AS units_sold,
        SUM(net_revenue) AS net_revenue,
        COUNT(DISTINCT order_id) AS orders
    FROM filtered_lines
    GROUP BY product_id, product_name, category
),
scored AS (
    SELECT
        p.*,
        p.net_revenue / NULLIF(p.units_sold, 0) AS revenue_per_unit,
        100.0 * p.units_sold / NULLIF(SUM(p.units_sold) OVER (), 0) AS pct_of_units,
        100.0 * p.net_revenue / NULLIF(SUM(p.net_revenue) OVER (), 0) AS pct_of_revenue,
        PERCENT_RANK() OVER (ORDER BY p.units_sold) AS units_percentile,
        PERCENT_RANK() OVER (
            ORDER BY p.net_revenue / NULLIF(p.units_sold, 0)
        ) AS value_percentile
    FROM product_totals p
)
SELECT
    product_id, product_name, category,
    units_sold,
    ROUND(net_revenue, 2)     AS net_revenue,
    ROUND(revenue_per_unit, 2) AS revenue_per_unit,
    ROUND(pct_of_units, 2)    AS pct_of_units,
    ROUND(pct_of_revenue, 2)  AS pct_of_revenue,
    ROUND(pct_of_revenue - pct_of_units, 2) AS revenue_minus_unit_share_pts,
    orders
FROM scored
WHERE units_percentile >= 0.75
  AND value_percentile < 0.50
ORDER BY units_sold DESC;

-- ---------------------------------------------------------------------------
-- name: aov_by_segment
-- description: Average order value compared across the three customer segments.
-- ---------------------------------------------------------------------------
WITH filtered_lines AS (
    SELECT f.customer_id, f.order_id, f.quantity, f.net_revenue, d.segment
    FROM fact_order_lines f
    JOIN dim_customers d ON d.customer_id = f.customer_id
    WHERE f.order_date BETWEEN :start_date AND :end_date
      AND (:category IS NULL OR f.category = :category)
      AND (:segment IS NULL OR d.segment = :segment)
)
SELECT
    segment,
    COUNT(DISTINCT order_id)                                         AS orders,
    COUNT(DISTINCT customer_id)                                      AS customers,
    ROUND(SUM(net_revenue), 2)                                       AS net_revenue,
    ROUND(SUM(net_revenue) / NULLIF(COUNT(DISTINCT order_id), 0), 2) AS avg_order_value,
    ROUND(SUM(net_revenue) / NULLIF(COUNT(DISTINCT customer_id), 0), 2) AS revenue_per_customer,
    ROUND(SUM(quantity) * 1.0 / NULLIF(COUNT(DISTINCT order_id), 0), 2) AS units_per_order,
    ROUND(100.0 * SUM(net_revenue)
          / NULLIF(SUM(SUM(net_revenue)) OVER (), 0), 2)             AS pct_of_revenue
FROM filtered_lines
GROUP BY segment
ORDER BY avg_order_value DESC;

-- ---------------------------------------------------------------------------
-- name: segment_distribution
-- description: How the eligible customer base splits across the three segments.
-- ---------------------------------------------------------------------------
WITH filtered_lines AS (
    SELECT f.customer_id, f.order_id, f.net_revenue, d.segment
    FROM fact_order_lines f
    JOIN dim_customers d ON d.customer_id = f.customer_id
    WHERE f.order_date BETWEEN :start_date AND :end_date
      AND (:category IS NULL OR f.category = :category)
      AND (:segment IS NULL OR d.segment = :segment)
),
cust AS (
    SELECT customer_id, segment, SUM(net_revenue) AS cust_revenue
    FROM filtered_lines
    GROUP BY customer_id, segment
)
SELECT
    segment,
    COUNT(*)                                                          AS customers,
    ROUND(100.0 * COUNT(*) / NULLIF(SUM(COUNT(*)) OVER (), 0), 2)    AS pct_of_customers,
    ROUND(SUM(cust_revenue), 2)                                       AS net_revenue,
    ROUND(100.0 * SUM(cust_revenue)
          / NULLIF(SUM(SUM(cust_revenue)) OVER (), 0), 2)             AS pct_of_revenue,
    ROUND(SUM(cust_revenue) / NULLIF(COUNT(*), 0), 2)                 AS revenue_per_customer
FROM cust
GROUP BY segment
ORDER BY segment;

-- ---------------------------------------------------------------------------
-- name: segment_profile
-- description: Behavioural scorecard for each segment.
-- ---------------------------------------------------------------------------
WITH filtered_lines AS (
    SELECT f.customer_id, f.order_id, f.category, f.quantity, f.net_revenue,
           d.segment, d.is_repeat_customer, d.recency_days
    FROM fact_order_lines f
    JOIN dim_customers d ON d.customer_id = f.customer_id
    WHERE f.order_date BETWEEN :start_date AND :end_date
      AND (:category IS NULL OR f.category = :category)
      AND (:segment IS NULL OR d.segment = :segment)
),
cust AS (
    SELECT
        customer_id, segment, is_repeat_customer,
        COUNT(DISTINCT order_id)   AS orders,
        SUM(quantity)              AS units,
        SUM(net_revenue)           AS revenue,
        COUNT(DISTINCT category)   AS categories
    FROM filtered_lines
    GROUP BY customer_id, segment, is_repeat_customer
)
SELECT
    segment,
    COUNT(*)                                                          AS customers,
    ROUND(100.0 * COUNT(*) / NULLIF(SUM(COUNT(*)) OVER (), 0), 2)    AS pct_of_customers,
    ROUND(SUM(revenue), 2)                                            AS net_revenue,
    ROUND(100.0 * SUM(revenue)
          / NULLIF(SUM(SUM(revenue)) OVER (), 0), 2)                 AS pct_of_revenue,
    ROUND(AVG(orders), 2)                                             AS avg_orders_per_customer,
    ROUND(AVG(revenue), 2)                                            AS avg_revenue_per_customer,
    ROUND(SUM(revenue) / NULLIF(SUM(orders), 0), 2)                   AS avg_order_value,
    ROUND(AVG(units), 2)                                              AS avg_units_per_customer,
    ROUND(AVG(categories), 2)                                         AS avg_categories,
    ROUND(100.0 * SUM(is_repeat_customer) / NULLIF(COUNT(*), 0), 2)   AS repeat_customer_pct
FROM cust
GROUP BY segment
ORDER BY avg_revenue_per_customer DESC;

-- ---------------------------------------------------------------------------
-- name: segment_thresholds
-- description: The actual cut points the segmentation produced (audit trail).
-- ---------------------------------------------------------------------------
SELECT
    segment,
    segment_tertile,
    COUNT(*)                              AS customers,
    ROUND(MIN(value_score), 4)            AS score_min,
    ROUND(MAX(value_score), 4)            AS score_max,
    ROUND(MIN(net_revenue), 2)            AS min_net_revenue,
    ROUND(MAX(net_revenue), 2)            AS max_net_revenue,
    MIN(order_count)                      AS min_orders,
    MAX(order_count)                      AS max_orders,
    ROUND(MIN(spend_percentile), 4)       AS spend_pct_min,
    ROUND(MAX(spend_percentile), 4)       AS spend_pct_max
FROM dim_customers
GROUP BY segment, segment_tertile
ORDER BY segment_tertile;

-- ---------------------------------------------------------------------------
-- name: repeat_purchase_summary
-- description: Repeat customer counts, repeat purchase rate and orders per customer.
-- ---------------------------------------------------------------------------
WITH filtered_lines AS (
    SELECT f.customer_id, f.order_id, f.net_revenue, d.segment
    FROM fact_order_lines f
    JOIN dim_customers d ON d.customer_id = f.customer_id
    WHERE f.order_date BETWEEN :start_date AND :end_date
      AND (:category IS NULL OR f.category = :category)
      AND (:segment IS NULL OR d.segment = :segment)
),
cust AS (
    SELECT
        customer_id,
        COUNT(DISTINCT order_id) AS orders,
        SUM(net_revenue)         AS revenue
    FROM filtered_lines
    GROUP BY customer_id
)
SELECT
    COUNT(*)                                                          AS unique_customers,
    SUM(CASE WHEN orders >= 2 THEN 1 ELSE 0 END)                      AS repeat_customers,
    SUM(CASE WHEN orders = 1 THEN 1 ELSE 0 END)                       AS one_time_customers,
    ROUND(100.0 * SUM(CASE WHEN orders >= 2 THEN 1 ELSE 0 END)
          / NULLIF(COUNT(*), 0), 2)                                   AS repeat_purchase_rate_pct,
    ROUND(SUM(orders) * 1.0 / NULLIF(COUNT(*), 0), 3)                 AS avg_orders_per_customer,
    ROUND(MAX(orders), 0)                                             AS max_orders_one_customer,
    ROUND(SUM(revenue) / NULLIF(COUNT(*), 0), 2)                      AS avg_revenue_per_customer,
    ROUND(SUM(revenue) / NULLIF(SUM(orders), 0), 2)                   AS avg_order_value
FROM cust;

-- ---------------------------------------------------------------------------
-- name: order_count_distribution
-- description: How many customers placed exactly N orders.
-- ---------------------------------------------------------------------------
WITH filtered_lines AS (
    SELECT f.customer_id, f.order_id, d.segment
    FROM fact_order_lines f
    JOIN dim_customers d ON d.customer_id = f.customer_id
    WHERE f.order_date BETWEEN :start_date AND :end_date
      AND (:category IS NULL OR f.category = :category)
      AND (:segment IS NULL OR d.segment = :segment)
),
cust AS (
    SELECT customer_id, COUNT(DISTINCT order_id) AS orders
    FROM filtered_lines GROUP BY customer_id
)
SELECT
    orders,
    COUNT(*)                                                          AS customers,
    ROUND(100.0 * COUNT(*) / NULLIF(SUM(COUNT(*)) OVER (), 0), 2)    AS pct_of_customers
FROM cust
GROUP BY orders
ORDER BY orders;

-- ---------------------------------------------------------------------------
-- name: interpurchase_interval
-- description: Time between consecutive purchases (repeat customers only).
-- ---------------------------------------------------------------------------
WITH filtered_lines AS (
    SELECT f.customer_id, f.order_id, f.order_date, d.segment
    FROM fact_order_lines f
    JOIN dim_customers d ON d.customer_id = f.customer_id
    WHERE f.order_date BETWEEN :start_date AND :end_date
      AND (:category IS NULL OR f.category = :category)
      AND (:segment IS NULL OR d.segment = :segment)
),
orders AS (
    SELECT DISTINCT customer_id, order_id, order_date FROM filtered_lines
),
gaps AS (
    SELECT
        customer_id,
        julianday(order_date) - julianday(
            LAG(order_date) OVER (PARTITION BY customer_id ORDER BY order_date, order_id)
        ) AS gap_days
    FROM orders
),
clean AS (
    SELECT customer_id, gap_days FROM gaps WHERE gap_days IS NOT NULL AND gap_days > 0
),
ranked AS (
    SELECT
        customer_id,
        gap_days,
        ROW_NUMBER() OVER (ORDER BY gap_days) AS rn,
        COUNT(*)     OVER ()                   AS n
    FROM clean
)
SELECT
    COUNT(*)                                                        AS intervals_measured,
    COUNT(DISTINCT customer_id)                                     AS customers_with_a_repeat,
    ROUND(AVG(gap_days), 1)                                        AS avg_gap_days,
    ROUND(AVG(CASE WHEN rn IN ((n + 1) / 2, (n + 2) / 2) THEN gap_days END), 1) AS median_gap_days,
    ROUND(MIN(gap_days), 1)                                        AS min_gap_days,
    ROUND(MAX(gap_days), 1)                                        AS max_gap_days,
    ROUND(100.0 * SUM(CASE WHEN gap_days <= 30 THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_repeat_within_30_days,
    ROUND(100.0 * SUM(CASE WHEN gap_days <= 90 THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_repeat_within_90_days
FROM ranked;

-- ---------------------------------------------------------------------------
-- name: repeat_vs_single_customer_profile
-- description: Value comparison between repeat customers and one-time buyers.
-- ---------------------------------------------------------------------------
WITH filtered_lines AS (
    SELECT f.customer_id, f.order_id, f.category, f.quantity, f.net_revenue,
           d.segment
    FROM fact_order_lines f
    JOIN dim_customers d ON d.customer_id = f.customer_id
    WHERE f.order_date BETWEEN :start_date AND :end_date
      AND (:category IS NULL OR f.category = :category)
      AND (:segment IS NULL OR d.segment = :segment)
),
cust AS (
    SELECT
        customer_id, segment,
        COUNT(DISTINCT order_id) AS orders,
        SUM(net_revenue)         AS revenue,
        SUM(quantity)            AS units,
        COUNT(DISTINCT category) AS categories
    FROM filtered_lines GROUP BY customer_id, segment
)
SELECT
    CASE WHEN orders >= 2 THEN 'Repeat customer' ELSE 'One-time customer' END AS customer_type,
    COUNT(*)                                                          AS customers,
    ROUND(AVG(orders), 3)                                             AS avg_orders,
    ROUND(AVG(revenue), 2)                                            AS avg_revenue_per_customer,
    ROUND(AVG(revenue / orders), 2)                                   AS avg_order_value,
    ROUND(AVG(units), 2)                                              AS avg_units,
    ROUND(AVG(categories), 2)                                         AS avg_categories
FROM cust
GROUP BY customer_type
ORDER BY avg_revenue_per_customer DESC;

-- ---------------------------------------------------------------------------
-- name: revenue_by_channel
-- description: Net revenue and AOV by sales channel.
-- ---------------------------------------------------------------------------
WITH filtered_lines AS (
    SELECT f.customer_id, f.order_id, f.order_channel, f.quantity, f.net_revenue,
           d.segment
    FROM fact_order_lines f
    JOIN dim_customers d ON d.customer_id = f.customer_id
    WHERE f.order_date BETWEEN :start_date AND :end_date
      AND (:category IS NULL OR f.category = :category)
      AND (:segment IS NULL OR d.segment = :segment)
)
SELECT
    order_channel,
    COUNT(DISTINCT order_id)                                         AS orders,
    COUNT(DISTINCT customer_id)                                      AS customers,
    SUM(quantity)                                                    AS units_sold,
    ROUND(SUM(net_revenue), 2)                                       AS net_revenue,
    ROUND(SUM(net_revenue) / NULLIF(COUNT(DISTINCT order_id), 0), 2) AS avg_order_value,
    ROUND(100.0 * SUM(net_revenue)
          / NULLIF(SUM(SUM(net_revenue)) OVER (), 0), 2)             AS pct_of_revenue
FROM filtered_lines
GROUP BY order_channel
ORDER BY net_revenue DESC;

-- ---------------------------------------------------------------------------
-- name: discount_analysis
-- description: Revenue and order count by discount band actually paid.
-- ---------------------------------------------------------------------------
WITH filtered_lines AS (
    SELECT f.customer_id, f.order_id, f.discount_pct, f.quantity,
           f.gross_line_amount, f.discount_amount, f.net_revenue, d.segment
    FROM fact_order_lines f
    JOIN dim_customers d ON d.customer_id = f.customer_id
    WHERE f.order_date BETWEEN :start_date AND :end_date
      AND (:category IS NULL OR f.category = :category)
      AND (:segment IS NULL OR d.segment = :segment)
)
SELECT
    CASE
        WHEN discount_pct = 0     THEN 'No discount'
        WHEN discount_pct <= 0.10 THEN '1-10%'
        WHEN discount_pct <= 0.20 THEN '11-20%'
        ELSE '21-25%'
    END                                                               AS discount_band,
    COUNT(DISTINCT order_id)                                         AS orders_touching_band,
    SUM(quantity)                                                    AS units_sold,
    ROUND(SUM(gross_line_amount), 2)                                 AS gross_line_revenue,
    ROUND(SUM(discount_amount), 2)                                   AS discount_value,
    ROUND(SUM(net_revenue), 2)                                       AS net_revenue,
    ROUND(100.0 * SUM(discount_amount) / NULLIF(SUM(gross_line_amount), 0), 2) AS effective_discount_pct,
    ROUND(SUM(net_revenue) / NULLIF(COUNT(DISTINCT order_id), 0), 2) AS revenue_per_order_touching_band
FROM filtered_lines
GROUP BY discount_band
ORDER BY gross_line_revenue DESC;

-- ---------------------------------------------------------------------------
-- name: category_penetration
-- description: How many customers bought each category and what they are worth.
-- ---------------------------------------------------------------------------
WITH filtered_lines AS (
    SELECT f.customer_id, f.order_id, f.category, f.net_revenue, d.segment
    FROM fact_order_lines f
    JOIN dim_customers d ON d.customer_id = f.customer_id
    WHERE f.order_date BETWEEN :start_date AND :end_date
      AND (:category IS NULL OR f.category = :category)
      AND (:segment IS NULL OR d.segment = :segment)
),
totals AS (
    SELECT COUNT(DISTINCT customer_id) AS customers FROM filtered_lines
)
SELECT
    f.category,
    COUNT(DISTINCT f.customer_id)                                    AS buyers,
    ROUND(100.0 * COUNT(DISTINCT f.customer_id) / NULLIF(t.customers, 0), 2) AS penetration_pct,
    COUNT(DISTINCT f.order_id)                                       AS orders,
    ROUND(SUM(f.net_revenue), 2)                                     AS net_revenue,
    ROUND(SUM(f.net_revenue) / NULLIF(COUNT(DISTINCT f.customer_id), 0), 2) AS revenue_per_buyer
FROM filtered_lines f
CROSS JOIN totals t
GROUP BY f.category, t.customers
ORDER BY net_revenue DESC;

-- ---------------------------------------------------------------------------
-- name: acquisition_channel_performance
-- description: Segment mix and value of each customer acquisition channel.
-- ---------------------------------------------------------------------------
WITH filtered_lines AS (
    SELECT f.customer_id, f.order_id, f.net_revenue, d.segment,
           d.acquisition_channel, d.is_repeat_customer
    FROM fact_order_lines f
    JOIN dim_customers d ON d.customer_id = f.customer_id
    WHERE f.order_date BETWEEN :start_date AND :end_date
      AND (:category IS NULL OR f.category = :category)
      AND (:segment IS NULL OR d.segment = :segment)
)
SELECT
    acquisition_channel,
    COUNT(DISTINCT customer_id)                                      AS customers,
    COUNT(DISTINCT order_id)                                         AS orders,
    ROUND(SUM(net_revenue), 2)                                       AS net_revenue,
    ROUND(SUM(net_revenue) / NULLIF(COUNT(DISTINCT order_id), 0), 2) AS avg_order_value,
    ROUND(100.0 * COUNT(DISTINCT CASE WHEN is_repeat_customer = 1 THEN customer_id END)
          / NULLIF(COUNT(DISTINCT customer_id), 0), 2)               AS repeat_customer_pct,
    ROUND(100.0 * COUNT(DISTINCT CASE WHEN segment = 'High Value' THEN customer_id END)
          / NULLIF(COUNT(DISTINCT customer_id), 0), 2)               AS high_value_pct
FROM filtered_lines
GROUP BY acquisition_channel
ORDER BY net_revenue DESC;

-- ---------------------------------------------------------------------------
-- name: top_category_pairs
-- description: The most common category combinations inside a single basket.
-- ---------------------------------------------------------------------------
WITH filtered_lines AS (
    SELECT DISTINCT f.customer_id, f.order_id, f.category, d.segment
    FROM fact_order_lines f
    JOIN dim_customers d ON d.customer_id = f.customer_id
    WHERE f.order_date BETWEEN :start_date AND :end_date
      AND (:category IS NULL OR f.category = :category)
      AND (:segment IS NULL OR d.segment = :segment)
),
pairs AS (
    SELECT a.order_id, a.category AS category_a, b.category AS category_b
    FROM filtered_lines a
    JOIN filtered_lines b
      ON b.order_id = a.order_id
     AND b.category > a.category
)
SELECT
    category_a,
    category_b,
    COUNT(DISTINCT order_id) AS orders,
    ROUND(100.0 * COUNT(DISTINCT order_id)
          / NULLIF(SUM(COUNT(DISTINCT order_id)) OVER (), 0), 2) AS pct_of_multi_category_baskets
FROM pairs
GROUP BY category_a, category_b
ORDER BY orders DESC
LIMIT 10;

-- ---------------------------------------------------------------------------
-- name: revenue_concentration
-- description: How concentrated is customer revenue? Pareto shares and Gini.
-- ---------------------------------------------------------------------------
WITH customer_revenue AS (
    SELECT d.customer_id, SUM(f.net_revenue) AS revenue
    FROM fact_order_lines f
    JOIN dim_customers d ON d.customer_id = f.customer_id
    WHERE f.order_date BETWEEN :start_date AND :end_date
      AND (:category IS NULL OR f.category = :category)
      AND (:segment IS NULL OR d.segment = :segment)
    GROUP BY d.customer_id
),
ranked AS (
    SELECT
        revenue,
        ROW_NUMBER() OVER (ORDER BY revenue DESC) AS rank_desc,
        ROW_NUMBER() OVER (ORDER BY revenue)     AS rank_asc,
        COUNT(*)     OVER ()                      AS customers
    FROM customer_revenue
),
totals AS (
    SELECT
        SUM(revenue) AS total_revenue,
        SUM(CASE WHEN rank_desc <= customers * 0.01 THEN revenue ELSE 0 END) AS top_1,
        SUM(CASE WHEN rank_desc <= customers * 0.05 THEN revenue ELSE 0 END) AS top_5,
        SUM(CASE WHEN rank_desc <= customers * 0.10 THEN revenue ELSE 0 END) AS top_10,
        SUM(CASE WHEN rank_desc <= customers * 0.20 THEN revenue ELSE 0 END) AS top_20
    FROM ranked
)
SELECT
    (SELECT customers FROM ranked LIMIT 1)                       AS customers,
    ROUND(total_revenue, 2)                                     AS total_revenue,
    ROUND(top_1  * 100.0 / total_revenue, 2)                    AS top_1pct_customers_revenue_pct,
    ROUND(top_5  * 100.0 / total_revenue, 2)                    AS top_5pct_customers_revenue_pct,
    ROUND(top_10 * 100.0 / total_revenue, 2)                    AS top_10pct_customers_revenue_pct,
    ROUND(top_20 * 100.0 / total_revenue, 2)                    AS top_20pct_customers_revenue_pct,
    -- Gini coefficient: 0 = perfectly even revenue, 1 = one customer holds all.
    ROUND((2.0 * SUM(rank_asc * revenue) / (customers * total_revenue))
          - (customers + 1.0) / customers, 4)                   AS gini_coefficient
FROM ranked, totals;

-- ---------------------------------------------------------------------------
-- name: revenue_concentration_curve
-- description: Cumulative revenue share as customers are added, best first.
-- ---------------------------------------------------------------------------
WITH customer_revenue AS (
    SELECT d.customer_id, SUM(f.net_revenue) AS revenue
    FROM fact_order_lines f
    JOIN dim_customers d ON d.customer_id = f.customer_id
    WHERE f.order_date BETWEEN :start_date AND :end_date
      AND (:category IS NULL OR f.category = :category)
      AND (:segment IS NULL OR d.segment = :segment)
    GROUP BY d.customer_id
),
ranked AS (
    SELECT revenue, ROW_NUMBER() OVER (ORDER BY revenue DESC) AS rn,
           COUNT(*) OVER () AS customers
    FROM customer_revenue
),
bucketed AS (
    SELECT
        MIN(20, 1 + CAST((rn - 1) * 20.0 / customers AS INTEGER)) AS bucket,
        SUM(revenue) AS revenue
    FROM ranked
    GROUP BY bucket
)
SELECT
    bucket * 5                                    AS cumulative_customer_pct,
    ROUND(100.0 * SUM(revenue) OVER (ORDER BY bucket)
          / (SELECT SUM(revenue) FROM customer_revenue), 2) AS cumulative_revenue_pct
FROM bucketed
ORDER BY bucket;

-- ---------------------------------------------------------------------------
-- name: repeat_propensity_by_first_order
-- description: Does the size of a customer's FIRST order predict whether they repeat?
-- ---------------------------------------------------------------------------
WITH filtered_lines AS (
    SELECT f.customer_id, f.order_id, f.order_date, f.quantity, f.net_revenue, d.segment
    FROM fact_order_lines f
    JOIN dim_customers d ON d.customer_id = f.customer_id
    WHERE f.order_date BETWEEN :start_date AND :end_date
      AND (:category IS NULL OR f.category = :category)
      AND (:segment IS NULL OR d.segment = :segment)
),
orders AS (
    SELECT customer_id, order_id, order_date,
           SUM(quantity) AS units, SUM(net_revenue) AS revenue
    FROM filtered_lines
    GROUP BY customer_id, order_id, order_date
),
sequenced AS (
    SELECT orders.*,
           ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY order_date, order_id) AS order_seq
    FROM orders
),
customers AS (
    SELECT customer_id,
           COUNT(*) AS orders,
           SUM(revenue) AS lifetime_revenue,
           MAX(CASE WHEN order_seq = 1 THEN units END) AS first_order_units
    FROM sequenced
    GROUP BY customer_id
),
banded AS (
    SELECT customers.*,
           CASE
               WHEN first_order_units = 1 THEN '1 unit'
               WHEN first_order_units = 2 THEN '2 units'
               WHEN first_order_units <= 4 THEN '3-4 units'
               ELSE '5+ units'
           END AS first_order_basket,
           CASE
               WHEN first_order_units = 1 THEN 1
               WHEN first_order_units = 2 THEN 2
               WHEN first_order_units <= 4 THEN 3
               ELSE 4
           END AS basket_order
    FROM customers
)
SELECT
    first_order_basket,
    COUNT(*)                                                          AS customers,
    SUM(CASE WHEN orders >= 2 THEN 1 ELSE 0 END)                      AS repeat_customers,
    SUM(CASE WHEN orders = 1 THEN 1 ELSE 0 END)                       AS one_time_customers,
    ROUND(100.0 * SUM(CASE WHEN orders >= 2 THEN 1 ELSE 0 END)
          / NULLIF(COUNT(*), 0), 2)                                   AS repeat_rate_pct,
    ROUND(AVG(orders), 3)                                             AS avg_orders_per_customer,
    ROUND(AVG(lifetime_revenue), 2)                                   AS avg_lifetime_revenue
FROM banded
GROUP BY first_order_basket, basket_order
ORDER BY basket_order;

-- ---------------------------------------------------------------------------
-- name: one_time_buyer_profile
-- description: The retention target population: what one-time buyers bought.
-- ---------------------------------------------------------------------------
WITH filtered_lines AS (
    SELECT f.customer_id, f.order_id, f.order_date, f.category, f.quantity,
           f.net_revenue, d.segment
    FROM fact_order_lines f
    JOIN dim_customers d ON d.customer_id = f.customer_id
    WHERE f.order_date BETWEEN :start_date AND :end_date
      AND (:category IS NULL OR f.category = :category)
      AND (:segment IS NULL OR d.segment = :segment)
),
orders AS (
    SELECT customer_id, order_id, order_date, SUM(quantity) AS units
    FROM filtered_lines
    GROUP BY customer_id, order_id, order_date
),
customer_order_counts AS (
    SELECT customer_id, COUNT(*) AS orders FROM orders GROUP BY customer_id
),
one_time_orders AS (
    SELECT orders.*
    FROM orders
    JOIN customer_order_counts USING (customer_id)
    WHERE customer_order_counts.orders = 1
)
SELECT
    f.category,
    COUNT(DISTINCT f.order_id)                                         AS one_time_orders,
    COUNT(DISTINCT f.customer_id)                                      AS one_time_customers,
    ROUND(SUM(f.net_revenue), 2)                                       AS one_time_revenue,
    ROUND(SUM(f.net_revenue) / NULLIF(COUNT(DISTINCT f.order_id), 0), 2) AS one_time_aov,
    ROUND(SUM(f.quantity) * 1.0 / NULLIF(COUNT(DISTINCT f.order_id), 0), 2) AS units_per_order
FROM filtered_lines f
JOIN one_time_orders o ON o.order_id = f.order_id
GROUP BY f.category
ORDER BY one_time_revenue DESC;

-- ---------------------------------------------------------------------------
-- name: first_repeat_gap
-- description: The second-purchase clock: days from a first order to a second.
-- ---------------------------------------------------------------------------
WITH filtered_lines AS (
    SELECT f.customer_id, f.order_id, f.order_date, d.segment
    FROM fact_order_lines f
    JOIN dim_customers d ON d.customer_id = f.customer_id
    WHERE f.order_date BETWEEN :start_date AND :end_date
      AND (:category IS NULL OR f.category = :category)
      AND (:segment IS NULL OR d.segment = :segment)
),
orders AS (
    SELECT DISTINCT customer_id, order_id, order_date FROM filtered_lines
),
sequenced AS (
    SELECT orders.*,
           ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY order_date, order_id) AS order_seq,
           LAG(order_date) OVER (PARTITION BY customer_id ORDER BY order_date, order_id) AS previous_date
    FROM orders
),
gaps AS (
    SELECT julianday(order_date) - julianday(previous_date) AS gap_days
    FROM sequenced
    WHERE order_seq = 2 AND previous_date IS NOT NULL
),
ranked AS (
    SELECT gap_days, ROW_NUMBER() OVER (ORDER BY gap_days) AS rn,
           COUNT(*) OVER () AS n
    FROM gaps
)
SELECT
    COUNT(*)                                                        AS first_to_second_intervals,
    ROUND(AVG(gap_days), 1)                                        AS avg_gap_days,
    ROUND(AVG(CASE WHEN rn IN ((n + 1) / 2, (n + 2) / 2) THEN gap_days END), 1) AS median_gap_days,
    ROUND(100.0 * SUM(CASE WHEN gap_days <= 30 THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_second_order_within_30_days,
    ROUND(100.0 * SUM(CASE WHEN gap_days <= 45 THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_second_order_within_45_days,
    ROUND(100.0 * SUM(CASE WHEN gap_days <= 60 THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_second_order_within_60_days,
    ROUND(100.0 * SUM(CASE WHEN gap_days <= 90 THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_second_order_within_90_days
FROM ranked;

-- ---------------------------------------------------------------------------
-- name: revenue_growth_decomposition
-- description: What explains the revenue change: customers, frequency or AOV?
-- ---------------------------------------------------------------------------
WITH yearly AS (
    SELECT
        strftime('%Y', f.order_date) AS year,
        COUNT(DISTINCT f.customer_id) AS customers,
        COUNT(DISTINCT f.order_id)    AS orders,
        SUM(f.net_revenue)            AS revenue
    FROM fact_order_lines f
    JOIN dim_customers d ON d.customer_id = f.customer_id
    WHERE f.order_date BETWEEN :start_date AND :end_date
      AND (:category IS NULL OR f.category = :category)
      AND (:segment IS NULL OR d.segment = :segment)
    GROUP BY strftime('%Y', f.order_date)
),
with_growth AS (
    SELECT
        year, customers, orders,
        orders * 1.0 / customers  AS orders_per_customer,
        revenue / orders          AS avg_order_value,
        revenue,
        customers * 1.0 / LAG(customers) OVER (ORDER BY year)       AS customer_ratio,
        (orders * 1.0 / customers)
            / LAG(orders * 1.0 / customers) OVER (ORDER BY year)       AS frequency_ratio,
        (revenue / orders)
            / LAG(revenue / orders) OVER (ORDER BY year)               AS aov_ratio,
        revenue / LAG(revenue) OVER (ORDER BY year)                    AS revenue_ratio
    FROM yearly
)
SELECT
    year,
    customers,
    orders,
    ROUND(orders_per_customer, 3)                                    AS orders_per_customer,
    ROUND(avg_order_value, 2)                                        AS avg_order_value,
    ROUND(revenue, 2)                                                 AS net_revenue,
    ROUND(100.0 * (customer_ratio  - 1), 1)                           AS customers_yoy_pct,
    ROUND(100.0 * (frequency_ratio  - 1), 1)                          AS frequency_yoy_pct,
    ROUND(100.0 * (aov_ratio       - 1), 1)                           AS aov_yoy_pct,
    ROUND(100.0 * (revenue_ratio   - 1), 1)                           AS revenue_yoy_pct,
    -- Revenue is customers x frequency x AOV, so the three factors must
    -- reproduce the total change. This column is the arithmetic proof.
    ROUND(100.0 * (customer_ratio * frequency_ratio * aov_ratio - 1), 1) AS implied_revenue_yoy_pct
FROM with_growth
ORDER BY year;

-- ---------------------------------------------------------------------------
-- name: orders_by_customer_tenure
-- description: Separates orders from newly-acquired customers from orders from
-- description: customers carried over from a previous year. A fall in blended
-- description: orders-per-customer can be a cohort-mix effect, not a drop in
-- description: the behaviour of the customers the business already had.
-- ---------------------------------------------------------------------------
WITH customer_orders AS (
    SELECT customer_id, order_id, order_date
    FROM fact_order_lines
    GROUP BY customer_id, order_id, order_date
),
tenure AS (
    SELECT customer_orders.*,
           strftime('%Y', MIN(order_date) OVER (PARTITION BY customer_id)) AS acquisition_year
    FROM customer_orders
)
SELECT
    strftime('%Y', order_date)                                         AS order_year,
    CASE WHEN strftime('%Y', order_date) = acquisition_year
         THEN 'Acquisition year (first purchase)'
         ELSE 'Existing customer (repeat year)'
    END                                                               AS order_type,
    COUNT(DISTINCT customer_id)                                       AS customers,
    COUNT(DISTINCT order_id)                                          AS orders,
    ROUND(COUNT(DISTINCT order_id) * 1.0 / COUNT(DISTINCT customer_id), 3) AS orders_per_customer
FROM tenure
WHERE order_date BETWEEN :start_date AND :end_date
  AND (:segment IS NULL OR customer_id IN (
        SELECT customer_id FROM dim_customers WHERE segment = :segment))
GROUP BY order_year, order_type
ORDER BY order_year, order_type;

