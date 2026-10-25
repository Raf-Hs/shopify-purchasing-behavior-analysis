-- ===========================================================================
-- STAGING LAYER - raw landing tables -> clean, joinable staging tables
-- ===========================================================================
-- The raw tables in `sql/01_schema.sql` model a dirty landing zone. This file
-- turns them into clean, indexed staging tables. Every rule below is stated
-- once here and referenced from the README, the report and the tests.
--
-- WHY TABLES AND NOT VIEWS?
-- SQLite inlines a view into its consumer, which means the `DATE(...)` and
-- `TRIM(...)` expressions of a dimension get re-evaluated once per row of the
-- fact table. Measured on this dataset that turned a 0.02 s reference into
-- 7 s, and a 5,000-row join into a multi-minute query. Materialising the
-- staging and mart layers as indexed tables is the standard warehouse pattern
-- (dbt-style staging -> marts) and keeps every downstream query fast.
--
-- THE RULES, IN ORDER OF APPLICATION
-- -----------------------------------
-- R0  De-duplicate orders on `order_id`; keep the earliest record.
-- R1  De-duplicate line items. Shopify merges identical product lines inside
--     one cart, so a (order_id, product_id, quantity, unit_price, discount_pct)
--     tuple appearing twice is a duplicate export row, not a second sale.
--     The row with the lowest line_item_id is kept.
-- R2  Drop rows that cannot be resolved or valued:
--       - order whose customer_id is not in the customer table
--       - line item whose order_id is not in the order table
--       - line item whose product_id is not in the product table
--       - line item with a missing or non-positive quantity
--       - line item with a missing or non-positive unit price
--       - line item with a discount_pct outside [0, 1)
--     Nothing is imputed: an unattributable row contributes no money.
-- R3  Recompute every money column from quantity x unit_price x (1 - pct).
--     The stored `discount_amount` column is advisory only.
-- ===========================================================================

PRAGMA foreign_keys = OFF;

-- ---------------------------------------------------------------------------
-- Staging dimensions
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS stg_customers;
CREATE TABLE stg_customers AS
SELECT
    TRIM(customer_id)       AS customer_id,
    DATE(registration_date) AS registration_date,
    country,
    region,
    acquisition_channel,
    device,
    is_subscribed
FROM customers
WHERE customer_id IS NOT NULL
  AND TRIM(customer_id) <> ''
  AND DATE(registration_date) IS NOT NULL;

CREATE UNIQUE INDEX idx_stg_customers_id ON stg_customers (customer_id);
CREATE INDEX idx_stg_customers_reg  ON stg_customers (registration_date);

DROP TABLE IF EXISTS stg_products;
CREATE TABLE stg_products AS
SELECT
    TRIM(product_id) AS product_id,
    product_name,
    category,
    subcategory,
    vendor,
    unit_price,
    launched_date
FROM products
WHERE product_id IS NOT NULL
  AND TRIM(product_id) <> ''
  AND unit_price IS NOT NULL
  AND unit_price > 0;

CREATE UNIQUE INDEX idx_stg_products_id ON stg_products (product_id);
CREATE INDEX idx_stg_products_cat    ON stg_products (category);

-- ---------------------------------------------------------------------------
-- Staging orders (R0 + R2)
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS stg_orders;
CREATE TABLE stg_orders AS
WITH ranked AS (
    SELECT
        TRIM(o.order_id)          AS order_id,
        TRIM(o.customer_id)       AS customer_id,
        DATE(o.order_date)        AS order_date,
        strftime('%Y-%m', DATE(o.order_date)) AS order_month,
        o.order_status,
        o.order_channel,
        o.payment_method,
        COALESCE(o.currency, 'USD')       AS currency,
        o.items_count,
        COALESCE(o.subtotal, 0)      AS subtotal,
        COALESCE(o.discount_total, 0) AS discount_total,
        COALESCE(o.shipping_total, 0) AS shipping_total,
        COALESCE(o.tax_total, 0)      AS tax_total,
        COALESCE(o.refund_amount, 0)  AS refund_amount,
        CAST(ROUND(COALESCE(o.refund_amount, 0) * 100.0) AS INTEGER) AS refund_cents,
        COALESCE(o.total, 0)          AS total,
        o.is_first_order,
        o.cancelled_at,
        ROW_NUMBER() OVER (PARTITION BY TRIM(o.order_id) ORDER BY o.order_date) AS rn
    FROM orders o
    WHERE o.order_id IS NOT NULL
      AND TRIM(o.order_id) <> ''
      AND DATE(o.order_date) IS NOT NULL
      AND o.order_status IN ('{{ELIGIBLE_ORDER_STATUS}}', 'cancelled', 'refunded', 'pending')
)
SELECT
    r.order_id, r.customer_id, r.order_date, r.order_month, r.order_status,
    r.order_channel, r.payment_method, r.currency, r.items_count, r.subtotal,
    r.discount_total, r.shipping_total, r.tax_total, r.refund_amount,
    r.refund_cents, r.total, r.is_first_order, r.cancelled_at
FROM ranked r
WHERE r.rn = 1
  AND EXISTS (SELECT 1 FROM stg_customers c WHERE c.customer_id = r.customer_id);

CREATE UNIQUE INDEX idx_stg_orders_id   ON stg_orders (order_id);
CREATE INDEX idx_stg_orders_customer    ON stg_orders (customer_id);
CREATE INDEX idx_stg_orders_date        ON stg_orders (order_date);
CREATE INDEX idx_stg_orders_status      ON stg_orders (order_status);

-- ---------------------------------------------------------------------------
-- Staging line items (R1 + R2 + R3)
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS stg_order_items;
CREATE TABLE stg_order_items AS
WITH deduped AS (
    SELECT
        line_item_id,
        order_id,
        product_id,
        quantity,
        unit_price,
        discount_pct,
        discount_amount,
        tax_amount
    FROM (
        SELECT
            i.line_item_id,
            TRIM(i.order_id)   AS order_id,
            TRIM(i.product_id) AS product_id,
            i.quantity,
            i.unit_price,
            i.discount_pct,
            i.discount_amount,
            i.tax_amount,
            ROW_NUMBER() OVER (
                PARTITION BY TRIM(i.order_id), TRIM(i.product_id), i.quantity,
                             i.unit_price, i.discount_pct
                ORDER BY i.line_item_id
            ) AS dup_rank
        FROM order_items i
        WHERE i.order_id IS NOT NULL AND TRIM(i.order_id) <> ''
          AND i.product_id IS NOT NULL AND TRIM(i.product_id) <> ''
    )
    WHERE dup_rank = 1
),
priced AS (
    SELECT
        d.line_item_id,
        d.order_id,
        d.product_id,
        p.product_name,
        p.category,
        p.subcategory,
        d.quantity,
        d.unit_price,
        d.discount_pct,
        -- Money is computed in WHOLE CENTS (integer arithmetic) so that the
        -- generator, the stored order totals and this recomputation agree to
        -- the cent. ROUND() in SQLite and Python's round() disagree on exact
        -- ties, and a one-cent gap between an order subtotal and the sum of
        -- its lines would be a reconciliation artefact, not a finding.
        d.quantity * CAST(ROUND(d.unit_price * 100.0) AS INTEGER) AS gross_cents,
        CAST(ROUND(COALESCE(d.discount_pct, 0) * 100.0) AS INTEGER)  AS pct_cents
    FROM deduped d
    JOIN stg_orders   o ON o.order_id = d.order_id
    JOIN stg_products p ON p.product_id = d.product_id
    WHERE d.quantity   IS NOT NULL AND d.quantity > 0
      AND d.unit_price IS NOT NULL AND d.unit_price > 0
      AND COALESCE(d.discount_pct, 0) >= 0
      AND COALESCE(d.discount_pct, 0) < 1
)
SELECT
    line_item_id,
    order_id,
    product_id,
    product_name,
    category,
    subcategory,
    quantity,
    unit_price,
    discount_pct,
    gross_cents,
    (gross_cents * pct_cents + 50) / 100                          AS discount_cents,
    gross_cents - (gross_cents * pct_cents + 50) / 100          AS net_cents,
    gross_cents / 100.0                                         AS gross_line_amount,
    (gross_cents * pct_cents + 50) / 100 / 100.0                 AS discount_amount,
    (gross_cents - (gross_cents * pct_cents + 50) / 100) / 100.0 AS net_line_amount
FROM priced;

CREATE UNIQUE INDEX idx_stg_items_id      ON stg_order_items (line_item_id);
CREATE INDEX idx_stg_items_order           ON stg_order_items (order_id);
CREATE INDEX idx_stg_items_product         ON stg_order_items (product_id);
CREATE INDEX idx_stg_items_category        ON stg_order_items (category);
