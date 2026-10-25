-- ===========================================================================
-- MART LAYER - analysis-ready facts and dimensions
-- ===========================================================================
-- R4  An ELIGIBLE ORDER is a `completed` order inside the observation window
--     ({{PERIOD_START}} .. {{PERIOD_END}}, substituted from src/config.py at
--     build time) that (a) resolves to a known customer and (b) has at least
--     one valid line item. Cancelled, refunded and pending orders are retained
--     in the raw and staging tables and excluded from every revenue and
--     order-count metric.
--
-- R5  Refunds are recorded at order level. They are allocated to lines pro-rata
--     by net line value, with the rounding remainder assigned to the last
--     line of the order so the allocations sum back to the order refund
--     exactly (asserted in src/build_db.py and tests/test_analysis.py).
--
-- R6  REVENUE DEFINITION
--       gross line revenue = quantity x unit_price
--       net line revenue   = gross line revenue x (1 - discount_pct)
--       order net revenue  = SUM(net line revenue) - order refund
--       SHIPPING AND TAX ARE NOT REVENUE. They are collected on behalf of
--       third parties and are reported separately.
--
-- R7  SEGMENTATION
--       value_score = 0.65 x spend percentile + 0.35 x order-count percentile
--       spend percentile      = PERCENT_RANK() of net_revenue among eligible
--                               customers
--       order-count percentile= PERCENT_RANK() of order_count among eligible
--                               customers
--       segment = NTILE(3) over (value_score, customer_id)
--           1 -> Low Engagement
--           2 -> Regular
--           3 -> High Value
--       Percentiles make the method scale-free; the customer_id tiebreak makes
--       the banding deterministic for tied scores; NTILE(3) guarantees the
--       three segments are mutually exclusive and jointly cover every
--       eligible customer.
--
--       RECENCY IS DELIBERATELY NOT PART OF THE SCORE. In a right-censored
--       window a recent registrant has had less time to buy, so scoring
--       recency would conflate tenure with engagement and mislabel new
--       customers as inactive. Recency is still reported per customer.
-- ===========================================================================

PRAGMA foreign_keys = OFF;

-- ---------------------------------------------------------------------------
-- FACT_ORDERS - one row per eligible order (R4)
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS fact_orders;
CREATE TABLE fact_orders AS
SELECT
    o.order_id,
    o.customer_id,
    o.order_date,
    o.order_month,
    o.order_status,
    o.order_channel,
    o.payment_method,
    o.currency,
    li.valid_line_items,
    li.units,
    li.gross_cents,
    li.discount_cents,
    li.net_cents              AS net_revenue_before_refunds_cents,
    o.refund_cents,
    li.net_cents - o.refund_cents AS net_revenue_cents,
    li.gross_cents / 100.0                       AS gross_revenue,
    li.discount_cents / 100.0                    AS discount_amount,
    li.net_cents / 100.0                         AS net_revenue_before_refunds,
    o.refund_cents / 100.0                       AS refund_amount,
    (li.net_cents - o.refund_cents) / 100.0      AS net_revenue,
    o.shipping_total,
    o.tax_total,
    o.subtotal,
    o.total,
    o.is_first_order
FROM stg_orders o
JOIN (
    SELECT
        order_id,
        COUNT(*)            AS valid_line_items,
        SUM(quantity)       AS units,
        SUM(gross_cents)    AS gross_cents,
        SUM(discount_cents) AS discount_cents,
        SUM(net_cents)      AS net_cents
    FROM stg_order_items
    GROUP BY order_id
) li ON li.order_id = o.order_id
WHERE o.order_status = 'completed'
  AND o.order_date BETWEEN '{{PERIOD_START}}' AND '{{PERIOD_END}}';

CREATE UNIQUE INDEX idx_fact_orders_id       ON fact_orders (order_id);
CREATE INDEX idx_fact_orders_customer        ON fact_orders (customer_id);
CREATE INDEX idx_fact_orders_date            ON fact_orders (order_date);
CREATE INDEX idx_fact_orders_month           ON fact_orders (order_month);
CREATE INDEX idx_fact_orders_channel         ON fact_orders (order_channel);

-- ---------------------------------------------------------------------------
-- FACT_ORDER_LINES - one row per sold line item, refunds allocated (R5)
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS fact_order_lines;
CREATE TABLE fact_order_lines AS
WITH base AS (
    SELECT
        li.order_id,
        o.customer_id,
        o.order_date,
        o.order_month,
        o.order_channel,
        o.payment_method,
        li.line_item_id,
        li.product_id,
        li.product_name,
        li.category,
        li.subcategory,
        li.quantity,
        li.unit_price,
        li.discount_pct,
        li.gross_cents,
        li.discount_cents,
        li.net_cents,
        li.gross_line_amount,
        li.discount_amount,
        li.net_line_amount,
        o.refund_cents AS order_refund_cents,
        o.net_revenue_before_refunds_cents AS order_net_cents,
        -- Pro-rata share of the order refund, rounded to whole cents. The
        -- last line of the order receives the rounding remainder so that the
        -- allocations sum back to the order refund exactly (R5).
        CAST(ROUND(
            o.refund_cents * CASE
                WHEN o.net_revenue_before_refunds_cents > 0
                THEN li.net_cents * 1.0 / o.net_revenue_before_refunds_cents
                ELSE 0 END
        ) AS INTEGER) AS provisional_refund_cents
    FROM stg_order_items li
    JOIN fact_orders o ON o.order_id = li.order_id
),
ranked AS (
    SELECT
        base.*,
        ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY line_item_id) AS rn,
        COUNT(*)     OVER (PARTITION BY order_id)                        AS n_lines,
        SUM(provisional_refund_cents) OVER (
            PARTITION BY order_id ORDER BY line_item_id
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS refund_before_cents
    FROM base
)
SELECT
    order_id,
    customer_id,
    order_date,
    order_month,
    order_channel,
    payment_method,
    line_item_id,
    product_id,
    product_name,
    category,
    subcategory,
    quantity,
    unit_price,
    discount_pct,
    gross_cents,
    discount_cents,
    net_cents,
    gross_line_amount,
    discount_amount,
    net_line_amount,
    CASE WHEN rn = n_lines
         THEN order_refund_cents - COALESCE(refund_before_cents, 0)
         ELSE provisional_refund_cents
    END AS refund_cents,
    net_cents
        - CASE WHEN rn = n_lines
               THEN order_refund_cents - COALESCE(refund_before_cents, 0)
               ELSE provisional_refund_cents END AS net_revenue_cents,
    CASE WHEN rn = n_lines
         THEN (order_refund_cents - COALESCE(refund_before_cents, 0)) / 100.0
         ELSE provisional_refund_cents / 100.0
    END AS refund_allocated,
    (net_cents
        - CASE WHEN rn = n_lines
               THEN order_refund_cents - COALESCE(refund_before_cents, 0)
               ELSE provisional_refund_cents END) / 100.0 AS net_revenue
FROM ranked;

CREATE UNIQUE INDEX idx_fol_line   ON fact_order_lines (line_item_id);
CREATE INDEX idx_fol_order        ON fact_order_lines (order_id);
CREATE INDEX idx_fol_customer     ON fact_order_lines (customer_id);
CREATE INDEX idx_fol_product      ON fact_order_lines (product_id);
CREATE INDEX idx_fol_category     ON fact_order_lines (category);
CREATE INDEX idx_fol_month        ON fact_order_lines (order_month);

-- ---------------------------------------------------------------------------
-- DIM_CUSTOMERS - customer metrics and segment (R7)
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS dim_customers;
CREATE TABLE dim_customers AS
WITH metrics AS (
    SELECT
        f.customer_id,
        COUNT(DISTINCT f.order_id)                AS order_count,
        COUNT(*)                                   AS line_item_count,
        SUM(f.quantity)                            AS units,
        SUM(f.gross_cents)                         AS gross_cents,
        SUM(f.discount_cents)                      AS discount_cents,
        SUM(f.net_cents)                           AS net_revenue_before_refunds_cents,
        SUM(f.refund_cents)                        AS refund_cents,
        SUM(f.net_revenue_cents)                   AS net_revenue_cents,
        SUM(f.gross_cents) / 100.0                 AS gross_revenue,
        SUM(f.discount_cents) / 100.0              AS discount_amount,
        SUM(f.net_cents) / 100.0                   AS net_revenue_before_refunds,
        SUM(f.refund_cents) / 100.0                AS refunds,
        SUM(f.net_revenue_cents) / 100.0           AS net_revenue,
        ROUND(SUM(f.net_revenue_cents) / 100.0
              / COUNT(DISTINCT f.order_id), 2)     AS aov,
        ROUND(AVG(f.net_revenue), 2)               AS revenue_per_line_item,
        MIN(f.order_date)                          AS first_order_date,
        MAX(f.order_date)                          AS last_order_date,
        COUNT(DISTINCT f.category)                 AS category_breadth,
        COUNT(DISTINCT f.product_id)               AS product_breadth,
        CASE WHEN COUNT(DISTINCT f.order_id) >= 2 THEN 1 ELSE 0 END AS is_repeat_customer
    FROM fact_order_lines f
    GROUP BY f.customer_id
),
pct AS (
    SELECT
        m.*,
        PERCENT_RANK() OVER (ORDER BY m.net_revenue) AS spend_percentile,
        PERCENT_RANK() OVER (ORDER BY m.order_count) AS order_percentile
    FROM metrics m
),
scored AS (
    SELECT
        pct.*,
        ROUND(0.65 * spend_percentile + 0.35 * order_percentile, 6) AS value_score
    FROM pct
),
banded AS (
    SELECT
        scored.*,
        NTILE(3) OVER (ORDER BY value_score, customer_id) AS segment_tertile
    FROM scored
),
enriched AS (
    SELECT
        b.*,
        CAST(julianday(b.max_order_date) - julianday(b.last_order_date) AS INTEGER) AS recency_days
    FROM (
        SELECT banded.*, (SELECT MAX(order_date) FROM fact_orders) AS max_order_date
        FROM banded
    ) b
)
SELECT
    e.customer_id,
    e.order_count,
    e.line_item_count,
    e.units,
    e.gross_cents,
    e.discount_cents,
    e.net_revenue_before_refunds_cents,
    e.refund_cents,
    e.net_revenue_cents,
    e.gross_revenue,
    e.discount_amount,
    e.net_revenue_before_refunds,
    e.refunds,
    e.net_revenue,
    e.aov,
    e.revenue_per_line_item,
    e.first_order_date,
    e.last_order_date,
    e.recency_days,
    e.category_breadth,
    e.product_breadth,
    e.is_repeat_customer,
    e.spend_percentile,
    e.order_percentile,
    e.value_score,
    e.segment_tertile,
    CASE e.segment_tertile
        WHEN 1 THEN 'Low Engagement'
        WHEN 2 THEN 'Regular'
        ELSE 'High Value'
    END AS segment,
    c.registration_date,
    c.country,
    c.region,
    c.acquisition_channel,
    c.device,
    c.is_subscribed
FROM enriched e
JOIN stg_customers c ON c.customer_id = e.customer_id;

CREATE UNIQUE INDEX idx_dim_customers_id      ON dim_customers (customer_id);
CREATE INDEX idx_dim_customers_segment        ON dim_customers (segment);
CREATE INDEX idx_dim_customers_repeat         ON dim_customers (is_repeat_customer);

-- ---------------------------------------------------------------------------
-- Convenience views over the marts (thin, safe to inline).
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_order_facts;
CREATE VIEW v_order_facts AS
SELECT * FROM fact_order_lines;

DROP VIEW IF EXISTS v_order_revenue;
CREATE VIEW v_order_revenue AS
SELECT * FROM fact_orders;

DROP VIEW IF EXISTS v_customer_metrics;
CREATE VIEW v_customer_metrics AS
SELECT * FROM dim_customers;

DROP VIEW IF EXISTS v_customer_segments;
CREATE VIEW v_customer_segments AS
SELECT * FROM dim_customers;

DROP VIEW IF EXISTS v_eligible_customers;
CREATE VIEW v_eligible_customers AS
SELECT * FROM dim_customers;
