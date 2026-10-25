-- ===========================================================================
-- Shopify purchasing behaviour analysis - raw schema (SQLite 3)
-- ===========================================================================
-- Design notes
--   * These four tables model a RAW LANDING ZONE: a near-verbatim export of
--     Shopify order data, exactly as it arrives from the admin API / CSV
--     export. They intentionally carry no NOT NULL / CHECK / FOREIGN KEY
--     constraints, because the raw layer is allowed to be dirty.
--   * Referential integrity and value validity are therefore NOT enforced by
--     the database; they are *measured* by the validation queries in
--     `sql/04_data_quality.sql`, and enforced by the cleaning layer in
--     `sql/02_views.sql`.
--   * Grain:
--       customers   -> one row per customer
--       products    -> one row per product
--       orders      -> one row per order (one checkout event)
--       order_items -> one row per line item inside an order (one sale of one
--                      product at one price). THIS is the transaction row and
--                      the unit of sale.
--   * Money is USD, stored as REAL with 2 decimal places. Currency is a column
--     so a multi-currency store would still be modelled correctly.
--   * Dates are ISO-8601 TEXT (YYYY-MM-DD) so they sort lexicographically and
--     SQLite's date functions work on them directly.
--   * Every row carries `source = 'synthetic'` as a provenance marker. No row
--     in this database originates from a real Shopify merchant.
-- ===========================================================================

PRAGMA foreign_keys = OFF;

DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS customers;

-- ---------------------------------------------------------------------------
-- Customers
-- ---------------------------------------------------------------------------
CREATE TABLE customers (
    customer_id          TEXT    NOT NULL,
    registration_date    TEXT    NOT NULL,
    country              TEXT,
    region               TEXT,
    acquisition_channel  TEXT,
    device               TEXT,
    is_subscribed        INTEGER,
    source               TEXT    NOT NULL DEFAULT 'synthetic'
);

CREATE INDEX idx_customers_registration ON customers (registration_date);

-- ---------------------------------------------------------------------------
-- Products
-- ---------------------------------------------------------------------------
CREATE TABLE products (
    product_id     TEXT    NOT NULL,
    product_name   TEXT,
    category       TEXT,
    subcategory    TEXT,
    vendor         TEXT,
    unit_price     REAL,
    launched_date  TEXT,
    source         TEXT    NOT NULL DEFAULT 'synthetic'
);

CREATE INDEX idx_products_category ON products (category);

-- ---------------------------------------------------------------------------
-- Orders (checkout events)
-- ---------------------------------------------------------------------------
CREATE TABLE orders (
    order_id          TEXT    NOT NULL,
    customer_id       TEXT,
    order_date        TEXT,
    order_status      TEXT,          -- completed | cancelled | refunded | pending
    order_channel     TEXT,          -- online | mobile_app | pos | marketplace
    payment_method    TEXT,
    currency          TEXT    NOT NULL DEFAULT 'USD',
    items_count       INTEGER,
    subtotal          REAL,          -- net of line discounts, excl. shipping & tax
    discount_total    REAL,
    shipping_total    REAL,
    tax_total         REAL,
    refund_amount     REAL    NOT NULL DEFAULT 0,
    total             REAL,          -- subtotal + shipping + tax
    shipping_country  TEXT,
    shipping_region   TEXT,
    shipping_city     TEXT,
    is_first_order    INTEGER,
    cancelled_at      TEXT,
    source            TEXT    NOT NULL DEFAULT 'synthetic'
);

CREATE INDEX idx_orders_customer ON orders (customer_id);
CREATE INDEX idx_orders_date    ON orders (order_date);
CREATE INDEX idx_orders_status  ON orders (order_status);

-- ---------------------------------------------------------------------------
-- Order line items (transaction rows / units of sale)
-- ---------------------------------------------------------------------------
CREATE TABLE order_items (
    line_item_id     INTEGER NOT NULL,
    order_id         TEXT,
    product_id       TEXT,
    quantity         INTEGER,
    unit_price       REAL,
    discount_pct     REAL,
    discount_amount  REAL,           -- ADVISORY: recomputed from discount_pct
    tax_amount       REAL,
    source           TEXT    NOT NULL DEFAULT 'synthetic'
);

CREATE INDEX idx_items_order   ON order_items (order_id);
CREATE INDEX idx_items_product ON order_items (product_id);

-- ---------------------------------------------------------------------------
-- Defect log (audit trail)
-- ---------------------------------------------------------------------------
-- Every deliberate data defect injected by `src/generate_data.py` is recorded
-- here. This table exists so the validation layer can PROVE that each
-- reconciliation variance in the cleaned model is explained by a known
-- defect rather than being an unexplained gap. It is metadata about the
-- generator, not a store record, and it carries no `source` column.
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS defect_log;
CREATE TABLE defect_log (
    defect_type       TEXT    NOT NULL,
    entity            TEXT    NOT NULL,
    entity_id         TEXT    NOT NULL,
    affected_order_id TEXT,
    detail            TEXT
);

CREATE INDEX idx_defect_type ON defect_log (defect_type);
