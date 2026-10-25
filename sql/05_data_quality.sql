-- ===========================================================================
-- DATA QUALITY CHECKS
-- ===========================================================================
-- These queries measure the health of the raw landing tables and of the
-- cleaning layer. They run against the RAW tables on purpose: the point of
-- a validation layer is to inspect what actually arrived, not what survived.
--
-- Every check returns a count. `dq_summary` rolls the counts into one table
-- with the rule that resolves each defect, and `src/run_queries.py` renders
-- both into `reports/sql_results.md`; `src/make_figures.py` and the report
-- use `reports/data_quality.md`.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- name: dq_summary
-- description: One row per validation check: what was flagged and how it is handled.
-- ---------------------------------------------------------------------------
WITH dup_lines AS (
    SELECT COUNT(*) AS keys, COALESCE(SUM(copies - 1), 0) AS surplus
    FROM (
        SELECT COUNT(*) AS copies
        FROM order_items
        GROUP BY TRIM(order_id), TRIM(product_id), quantity, unit_price, discount_pct
        HAVING COUNT(*) > 1
    )
),
dup_orders AS (
    SELECT COUNT(*) AS keys, COALESCE(SUM(copies - 1), 0) AS surplus
    FROM (
        SELECT COUNT(*) AS copies
        FROM orders
        GROUP BY TRIM(order_id)
        HAVING COUNT(*) > 1
    )
)
SELECT * FROM (
    SELECT
        'DQ01' AS check_id,
        'Duplicate line items (same business key)' AS check_name,
        'high' AS severity,
        (SELECT surplus FROM dup_lines) AS records_flagged,
        'De-duplicated on (order_id, product_id, quantity, unit_price, discount_pct); lowest line_item_id kept' AS resolution
    UNION ALL
    SELECT 'DQ02', 'Duplicate order records (same order_id)', 'high',
        (SELECT surplus FROM dup_orders),
        'De-duplicated on order_id; earliest record kept'
    UNION ALL
    SELECT 'DQ03', 'Line items with a missing order_id or product_id', 'high',
        (SELECT COUNT(*) FROM order_items
          WHERE order_id IS NULL OR TRIM(order_id) = ''
             OR product_id IS NULL OR TRIM(product_id) = ''),
        'Line dropped: no key to attribute the sale to'
    UNION ALL
    SELECT 'DQ04', 'Line items pointing at a non-existent order', 'high',
        (SELECT COUNT(*) FROM order_items i
          WHERE i.order_id IS NOT NULL AND TRIM(i.order_id) <> ''
            AND NOT EXISTS (SELECT 1 FROM orders o WHERE TRIM(o.order_id) = TRIM(i.order_id))),
        'Line dropped: the order does not exist'
    UNION ALL
    SELECT 'DQ05', 'Line items pointing at a non-existent product', 'high',
        (SELECT COUNT(*) FROM order_items i
          WHERE i.product_id IS NOT NULL AND TRIM(i.product_id) <> ''
            AND NOT EXISTS (SELECT 1 FROM products p WHERE TRIM(p.product_id) = TRIM(i.product_id))),
        'Line dropped: the product does not exist, so it has no category'
    UNION ALL
    SELECT 'DQ06', 'Orders pointing at a non-existent customer', 'high',
        (SELECT COUNT(*) FROM orders o
          WHERE o.customer_id IS NOT NULL AND TRIM(o.customer_id) <> ''
            AND NOT EXISTS (SELECT 1 FROM customers c WHERE TRIM(c.customer_id) = TRIM(o.customer_id))),
        'Order dropped: it cannot be attributed to a customer'
    UNION ALL
    SELECT 'DQ07', 'Line items with a NULL quantity', 'high',
        (SELECT COUNT(*) FROM order_items WHERE quantity IS NULL),
        'Line dropped: quantity is required to compute revenue'
    UNION ALL
    SELECT 'DQ08', 'Line items with a negative quantity', 'high',
        (SELECT COUNT(*) FROM order_items WHERE quantity < 0),
        'Line dropped: a negative quantity is a returns artefact, not a sale'
    UNION ALL
    SELECT 'DQ09', 'Line items with a zero quantity', 'medium',
        (SELECT COUNT(*) FROM order_items WHERE quantity = 0),
        'Line dropped: a zero-quantity line contributes no revenue'
    UNION ALL
    SELECT 'DQ10', 'Line items with a NULL unit price', 'high',
        (SELECT COUNT(*) FROM order_items WHERE unit_price IS NULL),
        'Line dropped: price is required to compute revenue'
    UNION ALL
    SELECT 'DQ11', 'Line items with a non-positive unit price', 'high',
        (SELECT COUNT(*) FROM order_items WHERE unit_price <= 0),
        'Line dropped: a non-positive price would create negative revenue'
    UNION ALL
    SELECT 'DQ12', 'Line items with a discount_pct outside [0, 1)', 'medium',
        (SELECT COUNT(*) FROM order_items
          WHERE discount_pct IS NOT NULL AND (discount_pct < 0 OR discount_pct >= 1)),
        'Line dropped: the discount rate is not interpretable'
    UNION ALL
    SELECT 'DQ13', 'Stored discount_amount inconsistent with discount_pct', 'medium',
        (SELECT COUNT(*) FROM order_items
          WHERE quantity IS NOT NULL AND unit_price IS NOT NULL
            AND quantity > 0 AND unit_price > 0
            AND ABS(CAST(ROUND(COALESCE(discount_amount, 0) * 100.0) AS INTEGER)
                    - (CAST(ROUND(quantity * unit_price * 100.0) AS INTEGER)
                       * CAST(ROUND(COALESCE(discount_pct, 0) * 100.0) AS INTEGER) + 50) / 100) > 0),
        'Recomputed from discount_pct; the stored column is treated as advisory'
    UNION ALL
    SELECT 'DQ14', 'Order total does not equal subtotal + shipping + tax', 'medium',
        (SELECT COUNT(*) FROM orders
          WHERE ABS(CAST(ROUND(COALESCE(total, 0) * 100.0) AS INTEGER)
                  - (CAST(ROUND(COALESCE(subtotal, 0) * 100.0) AS INTEGER)
                   + CAST(ROUND(COALESCE(shipping_total, 0) * 100.0) AS INTEGER)
                   + CAST(ROUND(COALESCE(tax_total, 0) * 100.0) AS INTEGER))) > 0),
        'Components are authoritative; the order total is reported as a variance only'
    UNION ALL
    SELECT 'DQ15', 'Completed orders with no valid line items', 'medium',
        (SELECT COUNT(*) FROM orders o
          WHERE o.order_status = 'completed'
            AND NOT EXISTS (SELECT 1 FROM stg_order_items li WHERE li.order_id = TRIM(o.order_id))),
        'Excluded from the eligible-order set: nothing to sell'
    UNION ALL
    SELECT 'DQ16', 'Orders with an unrecognised status', 'medium',
        (SELECT COUNT(*) FROM orders
          WHERE order_status IS NULL
             OR order_status NOT IN ('completed', 'cancelled', 'refunded', 'pending')),
        'Order dropped: the status cannot be classified as eligible or excluded'
    UNION ALL
    SELECT 'DQ17', 'Orders with an unparseable order_date', 'high',
        (SELECT COUNT(*) FROM orders
          WHERE order_date IS NULL OR DATE(order_date) IS NULL),
        'Order dropped: it cannot be placed in the observation window'
    UNION ALL
    SELECT 'DQ18', 'Orders outside the observation window', 'low',
        (SELECT COUNT(*) FROM orders
          WHERE DATE(order_date) IS NOT NULL
            AND DATE(order_date) NOT BETWEEN '{{PERIOD_START}}' AND '{{PERIOD_END}}'),
        'Excluded from the eligible-order set by the window rule'
    UNION ALL
    SELECT 'DQ19', 'Orders with a missing customer_id', 'high',
        (SELECT COUNT(*) FROM orders
          WHERE customer_id IS NULL OR TRIM(customer_id) = ''),
        'Order dropped: revenue cannot be attributed to a customer'
    UNION ALL
    SELECT 'DQ20', 'Products with a NULL or non-positive list price', 'medium',
        (SELECT COUNT(*) FROM products WHERE unit_price IS NULL OR unit_price <= 0),
        'Product dropped from the catalogue; it cannot carry revenue'
    UNION ALL
    SELECT 'DQ21', 'Refund allocation does not sum back to the order refund', 'high',
        (SELECT COUNT(*) FROM (
            SELECT o.order_id, SUM(f.refund_allocated) AS allocated, o.refund_amount AS expected
            FROM fact_order_lines f JOIN fact_orders o ON o.order_id = f.order_id
            GROUP BY o.order_id, o.refund_amount
          ) WHERE ABS(allocated - expected) > 0.005),
        'Must be zero: the allocation rule assigns the rounding remainder to the last line'
    UNION ALL
    SELECT 'DQ22', 'Customers with a NULL or unparseable registration_date', 'low',
        (SELECT COUNT(*) FROM customers
          WHERE registration_date IS NULL OR DATE(registration_date) IS NULL),
        'Customer dropped from the customer dimension'
    UNION ALL
    SELECT 'DQ23', 'Stored is_first_order flag disagrees with the derived flag', 'low',
        (SELECT COUNT(*) FROM (
            SELECT o.is_first_order,
                   CASE WHEN ROW_NUMBER() OVER (
                            PARTITION BY o.customer_id ORDER BY o.order_date, o.order_id
                        ) = 1 THEN 1 ELSE 0 END AS derived_flag
            FROM orders o
            WHERE o.customer_id IS NOT NULL AND TRIM(o.customer_id) <> ''
          ) WHERE COALESCE(is_first_order, -1) <> derived_flag),
        'Informational only: the analysis derives first-order status itself'
)
ORDER BY check_id;

-- ---------------------------------------------------------------------------
-- name: dq_row_reconciliation
-- description: Row counts at every layer of the pipeline (the audit trail).
-- ---------------------------------------------------------------------------
SELECT 'raw order_items' AS layer, COUNT(*) AS rows FROM order_items
UNION ALL SELECT 'distinct line business keys', COUNT(*) FROM (
    SELECT 1 FROM order_items
    GROUP BY TRIM(order_id), TRIM(product_id), quantity, unit_price, discount_pct)
UNION ALL SELECT 'staged order_items', COUNT(*) FROM stg_order_items
UNION ALL SELECT 'fact order lines (eligible orders)', COUNT(*) FROM fact_order_lines
UNION ALL SELECT 'raw orders', COUNT(*) FROM orders
UNION ALL SELECT 'staged orders', COUNT(*) FROM stg_orders
UNION ALL SELECT 'fact orders (eligible)', COUNT(*) FROM fact_orders
UNION ALL SELECT 'raw customers', COUNT(*) FROM customers
UNION ALL SELECT 'staged customers', COUNT(*) FROM stg_customers
UNION ALL SELECT 'eligible customers (dimension)', COUNT(*) FROM dim_customers
UNION ALL SELECT 'raw products', COUNT(*) FROM products
UNION ALL SELECT 'staged products', COUNT(*) FROM stg_products;

-- ---------------------------------------------------------------------------
-- name: dq_duplicate_line_items
-- description: The exact duplicate line items found in the raw export.
-- ---------------------------------------------------------------------------
SELECT
    TRIM(order_id)   AS order_id,
    TRIM(product_id) AS product_id,
    quantity,
    unit_price,
    discount_pct,
    COUNT(*)                                      AS copies,
    GROUP_CONCAT(line_item_id)                    AS line_item_ids
FROM order_items
GROUP BY TRIM(order_id), TRIM(product_id), quantity, unit_price, discount_pct
HAVING COUNT(*) > 1
ORDER BY copies DESC, order_id;

-- ---------------------------------------------------------------------------
-- name: dq_orphan_references
-- description: Every dangling foreign key in the raw layer.
-- ---------------------------------------------------------------------------
SELECT 'line_item -> order' AS relationship,
       i.order_id   AS missing_key,
       COUNT(*)     AS affected_rows
FROM order_items i
WHERE i.order_id IS NOT NULL AND TRIM(i.order_id) <> ''
  AND NOT EXISTS (SELECT 1 FROM orders o WHERE TRIM(o.order_id) = TRIM(i.order_id))
GROUP BY i.order_id
UNION ALL
SELECT 'line_item -> product', i.product_id, COUNT(*)
FROM order_items i
WHERE i.product_id IS NOT NULL AND TRIM(i.product_id) <> ''
  AND NOT EXISTS (SELECT 1 FROM products p WHERE TRIM(p.product_id) = TRIM(i.product_id))
GROUP BY i.product_id
UNION ALL
SELECT 'order -> customer', o.customer_id, COUNT(*)
FROM orders o
WHERE o.customer_id IS NOT NULL AND TRIM(o.customer_id) <> ''
  AND NOT EXISTS (SELECT 1 FROM customers c WHERE TRIM(c.customer_id) = TRIM(o.customer_id))
GROUP BY o.customer_id
ORDER BY relationship, missing_key;

-- ---------------------------------------------------------------------------
-- name: dq_invalid_line_values
-- description: Line items with a missing or out-of-range business value.
-- ---------------------------------------------------------------------------
SELECT
    i.line_item_id,
    i.order_id,
    i.product_id,
    i.quantity,
    i.unit_price,
    i.discount_pct,
    CASE
        WHEN i.quantity IS NULL THEN 'NULL quantity'
        WHEN i.quantity < 0     THEN 'Negative quantity'
        WHEN i.quantity = 0     THEN 'Zero quantity'
        WHEN i.unit_price IS NULL THEN 'NULL unit price'
        WHEN i.unit_price <= 0  THEN 'Non-positive unit price'
        WHEN i.discount_pct IS NOT NULL AND (i.discount_pct < 0 OR i.discount_pct >= 1)
            THEN 'Discount pct outside [0,1)'
        ELSE 'Other'
    END AS defect
FROM order_items i
WHERE i.quantity IS NULL OR i.quantity <= 0
   OR i.unit_price IS NULL OR i.unit_price <= 0
   OR (i.discount_pct IS NOT NULL AND (i.discount_pct < 0 OR i.discount_pct >= 1))
ORDER BY i.line_item_id;

-- ---------------------------------------------------------------------------
-- name: dq_order_total_reconciliation
-- description: Orders whose stored total disagrees with their components.
-- ---------------------------------------------------------------------------
SELECT
    o.order_id,
    o.order_status,
    ROUND(COALESCE(o.subtotal, 0), 2)        AS subtotal,
    ROUND(COALESCE(o.shipping_total, 0), 2)  AS shipping_total,
    ROUND(COALESCE(o.tax_total, 0), 2)       AS tax_total,
    ROUND(COALESCE(o.subtotal, 0) + COALESCE(o.shipping_total, 0) + COALESCE(o.tax_total, 0), 2) AS computed_total,
    ROUND(COALESCE(o.total, 0), 2)           AS stored_total,
    ROUND(COALESCE(o.total, 0) - (COALESCE(o.subtotal, 0) + COALESCE(o.shipping_total, 0) + COALESCE(o.tax_total, 0)), 2) AS variance
FROM orders o
WHERE ABS(CAST(ROUND(COALESCE(o.total, 0) * 100.0) AS INTEGER)
          - (CAST(ROUND(COALESCE(o.subtotal, 0) * 100.0) AS INTEGER)
           + CAST(ROUND(COALESCE(o.shipping_total, 0) * 100.0) AS INTEGER)
           + CAST(ROUND(COALESCE(o.tax_total, 0) * 100.0) AS INTEGER))) > 0
ORDER BY ABS(variance) DESC, o.order_id;

-- ---------------------------------------------------------------------------
-- name: dq_line_vs_order_subtotal
-- description: Orders whose stored subtotal disagrees with its valid line items.
-- ---------------------------------------------------------------------------
SELECT
    o.order_id,
    o.order_status,
    ROUND(o.subtotal, 2)                          AS stored_subtotal,
    ROUND(COALESCE(li.line_net, 0), 2)            AS sum_of_valid_lines,
    ROUND(o.subtotal - COALESCE(li.line_net, 0), 2) AS variance,
    COALESCE(li.lines, 0)                         AS valid_lines
FROM stg_orders o
LEFT JOIN (
    SELECT order_id, COUNT(*) AS lines, SUM(net_line_amount) AS line_net
    FROM stg_order_items GROUP BY order_id
) li ON li.order_id = o.order_id
WHERE ABS(CAST(ROUND(o.subtotal * 100.0) AS INTEGER)
          - CAST(ROUND(COALESCE(li.line_net, 0) * 100.0) AS INTEGER)) > 0
ORDER BY ABS(variance) DESC, o.order_id;

-- ---------------------------------------------------------------------------
-- name: dq_reconciliation_explained
-- description: Proves every subtotal variance is explained by a logged defect.
-- ---------------------------------------------------------------------------
WITH unreconciled AS (
    SELECT
        o.order_id,
        o.subtotal,
        COALESCE((
            SELECT SUM(li.net_line_amount)
            FROM stg_order_items li
            WHERE li.order_id = o.order_id
        ), 0) AS line_net
    FROM stg_orders o
),
flagged AS (
    SELECT u.*, (u.subtotal - u.line_net) AS variance
    FROM unreconciled u
    WHERE ABS(CAST(ROUND(u.subtotal * 100.0) AS INTEGER)
              - CAST(ROUND(u.line_net * 100.0) AS INTEGER)) > 0
),
logged AS (
    SELECT DISTINCT affected_order_id AS order_id
    FROM defect_log
    WHERE affected_order_id IS NOT NULL AND affected_order_id <> ''
)
SELECT
    f.order_id,
    ROUND(f.subtotal, 2)   AS stored_subtotal,
    ROUND(f.line_net, 2)   AS sum_of_valid_lines,
    ROUND(f.variance, 2)   AS variance,
    CASE WHEN l.order_id IS NULL
         THEN 'UNEXPLAINED - investigate'
         ELSE 'Explained by a logged defect'
    END AS status
FROM flagged f
LEFT JOIN logged l ON l.order_id = f.order_id
ORDER BY status DESC, ABS(f.variance) DESC, f.order_id;

-- ---------------------------------------------------------------------------
-- name: dq_defect_log_summary
-- description: The deliberate defects the generator injected, for audit.
-- ---------------------------------------------------------------------------
SELECT
    defect_type,
    entity,
    COUNT(*) AS records,
    GROUP_CONCAT(DISTINCT defect_type) AS note
FROM defect_log
GROUP BY defect_type, entity
ORDER BY records DESC, defect_type;

-- ---------------------------------------------------------------------------
-- name: dq_status_distribution
-- description: Order statuses in the raw layer and how each one is treated.
-- ---------------------------------------------------------------------------
SELECT
    o.order_status,
    COUNT(*)                                                            AS orders,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2)                   AS pct_of_orders,
    SUM(CASE WHEN o.order_status = 'completed' THEN 1 ELSE 0 END)         AS treatment_note_count,
    CASE o.order_status
        WHEN 'completed' THEN 'Eligible: counts towards revenue and order counts'
        WHEN 'cancelled' THEN 'Excluded: the order never happened'
        WHEN 'refunded'  THEN 'Excluded: the money was returned'
        WHEN 'pending'   THEN 'Excluded: payment not confirmed'
        ELSE 'Excluded: unknown status'
    END AS treatment
FROM orders o
GROUP BY o.order_status
ORDER BY orders DESC;

-- ---------------------------------------------------------------------------
-- name: dq_catalog_price_drift
-- description: Informational: how often a line was sold off its list price.
-- ---------------------------------------------------------------------------
SELECT
    p.product_id,
    p.product_name,
    p.category,
    ROUND(p.unit_price, 2)                                    AS list_price,
    COUNT(*)                                                   AS lines_sold,
    ROUND(AVG(li.unit_price), 2)                               AS avg_realised_price,
    ROUND(100.0 * SUM(CASE WHEN li.unit_price < p.unit_price THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_below_list
FROM stg_order_items li
JOIN stg_products p ON p.product_id = li.product_id
GROUP BY p.product_id, p.product_name, p.category, p.unit_price
HAVING pct_below_list > 0
ORDER BY lines_sold DESC
LIMIT 10;
