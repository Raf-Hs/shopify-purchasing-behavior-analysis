# Data dictionary

Column-by-column description of the four raw tables in `data/raw/`. Types are
as stored in SQLite. Every table carries a `source` column; in this project it
is always `'synthetic'`.

---

## `customers.csv` — one row per customer

| Column | Type | Description |
|---|---|---|
| `customer_id` | TEXT | Primary key, format `CUST-00001`. Unique. |
| `registration_date` | TEXT | ISO-8601 date the customer registered. Between 2023-01-01 and 2025-12-31. Can precede the first order. |
| `country` | TEXT | Market of the customer: US, GB, CA, DE, FR, AU, ES, JP, NL, MX. |
| `region` | TEXT | Region or state within the country. |
| `acquisition_channel` | TEXT | How the customer was first acquired: `organic_search`, `paid_social`, `email`, `direct`, `referral`, `affiliate`, `social_organic`. |
| `device` | TEXT | `mobile`, `desktop`, `tablet`. |
| `is_subscribed` | INTEGER | 1 if subscribed to marketing email, else 0. |
| `source` | TEXT | Always `synthetic`. |

---

## `products.csv` — one row per product

| Column | Type | Description |
|---|---|---|
| `product_id` | TEXT | Primary key, format `PROD-0001`. Unique. |
| `product_name` | TEXT | Display name, e.g. `Essential Two Person Tent`. |
| `category` | TEXT | Top-level category: Apparel, Footwear, Home & Living, Beauty, Electronics, Sports & Outdoors, Accessories, Kids & Baby. |
| `subcategory` | TEXT | Finer grouping, e.g. `Camping`, `Sneakers`, `Skincare`. |
| `vendor` | TEXT | Brand. |
| `unit_price` | REAL | List price in USD, 2 decimals. Always positive in the clean catalogue. |
| `launched_date` | TEXT | ISO-8601 date the product was added to the catalogue. |
| `source` | TEXT | Always `synthetic`. |

---

## `orders.csv` — one row per order (a checkout event)

| Column | Type | Description |
|---|---|---|
| `order_id` | TEXT | Primary key, format `ORD-000001`. Unique in the clean layer; the raw layer may contain repeats (see DQ02). |
| `customer_id` | TEXT | The buying customer. FK → `customers.customer_id`. **Can be missing or dangling** in the raw layer. |
| `order_date` | TEXT | ISO-8601 date the order was placed. |
| `order_status` | TEXT | `completed` (eligible), `cancelled`, `refunded`, `pending` (all excluded from revenue). |
| `order_channel` | TEXT | `online`, `mobile_app`, `pos`, `marketplace`. |
| `payment_method` | TEXT | `credit_card`, `paypal`, `apple_pay`, `google_pay`, `shop_pay`, `klarna`, `gift_card`. |
| `currency` | TEXT | `USD`. |
| `items_count` | INTEGER | Number of line items the order was created with. |
| `subtotal` | REAL | Order value after line discounts, **excluding** shipping and tax. Computed in cents. |
| `discount_total` | REAL | Total discount value on the order. |
| `shipping_total` | REAL | Shipping charged. Zero above the free-shipping threshold. |
| `tax_total` | REAL | Tax charged, as a simple rate of the country. |
| `refund_amount` | REAL | Money refunded against the order. Mostly 0; a partial refund on a minority of completed orders. |
| `total` | REAL | `subtotal + shipping_total + tax_total`. **Can disagree with those components** in the raw layer (DQ14). |
| `shipping_country` | TEXT | Destination country. |
| `shipping_region` | TEXT | Destination region. |
| `shipping_city` | TEXT | Destination city label. |
| `is_first_order` | INTEGER | 1 if this was the customer's first order. Informational — the analysis derives first-order status itself. |
| `cancelled_at` | TEXT | Timestamp of cancellation. Empty unless the order was cancelled. |
| `source` | TEXT | Always `synthetic`. |

---

## `order_items.csv` — one row per order line item (a unit of sale)

| Column | Type | Description |
|---|---|---|
| `line_item_id` | INTEGER | Surrogate key. Unique in the clean layer. |
| `order_id` | TEXT | The parent order. FK → `orders.order_id`. **Can be missing or dangling** in the raw layer. |
| `product_id` | TEXT | The sold product. FK → `products.product_id`. **Can be missing or dangling** in the raw layer. |
| `quantity` | INTEGER | Units sold. Always ≥ 1 in the clean layer. |
| `unit_price` | REAL | Price actually charged per unit in USD. May be below the catalogue list price. |
| `discount_pct` | REAL | Discount rate applied to the line, as a fraction (0.05 = 5%). **Authoritative** for the discount. |
| `discount_amount` | REAL | Discount value in USD. **Advisory** — recomputed from `discount_pct` (DQ13). |
| `tax_amount` | REAL | Tax attributable to the line. |
| `source` | TEXT | Always `synthetic`. |

---

## `defects.csv` — the injected-defect audit trail

Not a store table. Metadata about the generator, loaded into the database as
`defect_log` so the build can assert that every reconciliation variance is
explained.

| Column | Type | Description |
|---|---|---|
| `defect_type` | TEXT | Which defect was injected, e.g. `null_quantity`, `orphan_order_reference`. |
| `entity` | TEXT | `order` or `order_item`. |
| `entity_id` | TEXT | The identifier of the affected record. |
| `affected_order_id` | TEXT | The order the defect touched. Used to prove a reconciliation variance is explained. |
| `detail` | TEXT | Human-readable description of what was changed. |

---

## Derived columns added by the cleaning layer

These are not in the CSVs. `sql/02_staging.sql` adds them to `stg_order_items`,
`sql/03_marts.sql` to the fact and dimension tables.

| Column | Table | Description |
|---|---|---|
| `gross_cents` | `stg_order_items` | `quantity × unit_price` in whole cents. |
| `discount_cents` | `stg_order_items` | `gross_cents × pct_cents`, rounded to whole cents. |
| `net_cents` | `stg_order_items` | `gross_cents − discount_cents`. |
| `gross_line_amount` / `discount_amount` / `net_line_amount` | `stg_order_items` | The same three values in dollars. |
| `refund_cents` | `stg_orders` | The order refund in whole cents. |
| `net_revenue_cents` | `fact_order_lines`, `fact_orders`, `dim_customers` | Line / order / customer net revenue in whole cents. |
| `refund_cents` | `fact_order_lines` | The order's refund allocated to this line. |
| `refund_allocated` | `fact_order_lines` | The same allocation in dollars. |
| `value_score` | `dim_customers` | `0.65 × spend percentile + 0.35 × order percentile`. |
| `segment_tertile` | `dim_customers` | 1, 2 or 3 from `NTILE(3)`. |
| `segment` | `dim_customers` | `Low Engagement`, `Regular` or `High Value`. |
| `recency_days` | `dim_customers` | Days from the customer's last eligible order to the last order in the dataset. |
| `is_repeat_customer` | `dim_customers` | 1 if the customer has ≥ 2 eligible orders. |
