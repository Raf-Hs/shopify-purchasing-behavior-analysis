# Methodology

The definitions behind every number in `reports/business_analysis.md`,
`reports/data_quality.md` and the dashboard. If a figure appears anywhere in
this project, it was produced by a rule stated here.

---

## 1. Terminology

| Term | Definition | Where it lives |
|---|---|---|
| **Order** | One checkout event by one customer. Carries a status, a date, a total, a channel and a payment method. | `orders` / `stg_orders` |
| **Transaction** | The event of money actually changing hands. In this project a transaction is an **eligible order**: a `completed` order that resolves to a known customer and contains at least one valid line item. A cancelled or refunded order was a checkout, not a transaction. | `fact_orders` |
| **Order line item** | One product, at one price, at one quantity, inside one order. The unit of sale and the grain at which revenue is computed. | `order_items` / `stg_order_items` / `fact_order_lines` |
| **Eligible order** | See §4. | `fact_orders` |
| **Eligible customer** | A customer with at least one eligible order. | `dim_customers` |
| **Repeat customer** | An eligible customer with **≥ 2 eligible orders** inside the observation window. | `dim_customers.is_repeat_customer` |
| **Line of revenue** | Net revenue attributed to one order line, after the order's refund has been allocated to it. | `fact_order_lines.net_revenue` |

The distinction matters because the three grains answer different questions.
5,061 orders is not "5,061 products sold" — the store sold 21,602 units across
14,729 line items.

---

## 2. Money handling

**All money is computed in integer cents.** Both the generator (`src/generate_data.py`)
and the SQL (`sql/02_staging.sql`) convert to cents first and use integer
arithmetic from there:

```sql
gross_cents    = quantity * CAST(ROUND(unit_price * 100.0) AS INTEGER)
discount_cents = (gross_cents * pct_cents + 50) / 100    -- integer division
net_cents      = gross_cents - discount_cents
```

Why this matters: Python's `round()` is round-half-to-even, SQLite's `ROUND()`
is printf-style, and the two disagree on exact ties. An earlier build of this
project computed money in floating point in both languages and produced
one-cent gaps between an order's stored subtotal and the sum of its recomputed
lines on 37 orders — an artefact of the tooling, not a data defect. Integer
cents removed the class of problem entirely and made every reconciliation an
exact comparison instead of a tolerance.

One related footnote: Python's `//` floors toward negative infinity while
SQLite's `/` truncates toward zero, so the two disagree on negative operands.
Every line that can carry revenue has a positive quantity and price, so this
never touches the money model — but it does affect how a validation query
behaves on already-invalid negative-quantity rows, which is why the discount
consistency check (DQ13) is scoped to positive-quantity lines.

---

## 3. Revenue

### 3.1 The definition

```
gross line revenue = quantity × unit_price
net line revenue   = gross line revenue × (1 − discount_pct)
order net revenue  = Σ net line revenue − order refund
```

**Shipping and tax are not revenue.** They are collected on behalf of
carriers and tax authorities, and they are reported separately. Including them
would overstate the store's revenue by the amount of third-party money it
handled.

### 3.2 The bridge

| Step | Amount | Query |
|---|---:|---|
| 1. Gross item revenue | $1,759,900.43 | `revenue_bridge` |
| 2. Less line discounts | −$45,116.84 | |
| 3. Net item revenue | $1,714,783.59 | |
| 4. Less refunds allocated | −$32,964.24 | |
| 5. **Net revenue** | **$1,681,819.35** | |

The bridge closes exactly; `tests/test_analysis.py` asserts it.

### 3.3 Discounts

`discount_pct` is authoritative. The stored `discount_amount` column is
**advisory**: it is recomputed from the percentage and any disagreement with
the stored value is reported as a data-quality finding rather than trusted.

### 3.4 Refunds

Refunds are recorded at order level (`orders.refund_amount`) but revenue is
reported at line level, so the refund must be allocated.

**Allocation rule (R5).** Each line receives a pro-rata share of the order
refund, proportional to its net line value:

```
provisional_share = order_refund_cents × net_line_cents / order_net_cents
```

The share is rounded to whole cents, and the **last line of the order
receives the rounding remainder**. That guarantees the allocations sum back to
the order refund exactly — asserted in `src/build_db.py` at build time and in
`tests/test_analysis.py` (`test_refund_allocation_sums_back_to_the_order_refund`).

An alternative rule (allocate to the largest line, or spread the remainder
pro-rata) would produce different line-level product revenue while the same
order-level total. The rule is therefore stated explicitly rather than left
implicit, because product-level revenue depends on it.

---

## 4. Eligibility

**An eligible order** (`fact_orders`) is a row that satisfies **all** of:

1. `order_status = 'completed'`;
2. `order_date` lies inside the observation window (2024-01-01 → 2025-12-31);
3. `customer_id` resolves to a row in the customer table;
4. it contains at least one valid order line item.

Statuses that are **excluded**, and why:

| Status | Treatment | Reason |
|---|---|---|
| `cancelled` | Excluded | The order never completed; no money moved. |
| `refunded` | Excluded | The money was returned; the order is not a net revenue event. |
| `pending` | Excluded | Payment was never confirmed. |
| `completed` | **Included**, subject to rules 2–4. | |

A `completed` order with a partial refund is **included**, and the refund is
deducted from its revenue. A `completed` order with no valid lines is
**excluded** — there is nothing to sell.

The observation window is defined once, in `src/config.py`, and injected into
the SQL at build time through the `{{PERIOD_START}}` / `{{PERIOD_END}}`
tokens, so the SQL and the Python can never drift apart.

---

## 5. Cleaning rules

The raw tables are a dirty landing zone. The rules are applied in this order.

| Rule | Action | Rationale |
|---|---|---|
| **R0** | De-duplicate `orders` on `order_id`, keep the earliest. | A repeated order record is an export artefact, not two orders. |
| **R1** | De-duplicate `order_items` on `(order_id, product_id, quantity, unit_price, discount_pct)`, keep the lowest `line_item_id`. | Shopify merges identical product lines inside one cart, so a repeated key is a repeated export row. |
| **R2** | Drop line items whose order or product cannot be resolved. | An unattributable sale has no customer and no category. Nothing is imputed. |
| **R3** | Drop line items with a missing or non-positive quantity or unit price, or a `discount_pct` outside `[0, 1)`. | Revenue is uncomputable without them. A negative quantity is a returns artefact, not a sale. |
| **R4** | Recompute every money column from `quantity × unit_price × (1 − pct)`. | The stored discount column is advisory. |
| **R5** | Apply the eligibility rules in §4. | Cancelled, refunded and pending orders are retained in the raw and staging layers for auditability but excluded from every metric. |

**No imputation, no back-filling, no silent correction.** Every dropped row is
either unresolvable or unpriceable.

### 5.1 Why the model is tables, not views

The first build implemented the staging and mart layers as SQLite **views**.
SQLite inlines a view into its consumer, so the `DATE()` and `TRIM()`
expressions of a dimension were re-evaluated once per fact row: a 0.02 s
reference took 7 s, and a 5,000-row join ran for minutes. The project uses the
standard warehouse pattern instead — indexed staging and mart **tables**
(`stg_*`, `fact_*`, `dim_*`) with thin convenience views on top.

---

## 6. Segmentation

### 6.1 The method

```
value_score = 0.65 × spend_percentile + 0.35 × order_percentile

spend_percentile  = PERCENT_RANK() of net_revenue among eligible customers
order_percentile  = PERCENT_RANK() of order_count among eligible customers

segment = NTILE(3) OVER (ORDER BY value_score, customer_id)
           1 -> Low Engagement
           2 -> Regular
           3 -> High Value
```

**Why this method.** Percentiles make the method scale-free — the same rules
produce sensible segments for a store with 200 customers or 2 million.
`NTILE(3)` guarantees the three segments are mutually exclusive and jointly
cover every eligible customer, with no customer left unassigned. The
`customer_id` tiebreak makes the banding deterministic for tied scores, so
re-running the pipeline produces byte-identical assignments (asserted in
`test_segmentation_is_reproducible`).

**Why these weights.** Monetary value is weighted 65% and engagement 35%
because revenue is the outcome the business manages. The weights are stated
rather than tuned to a preferred story; they are a documented modelling choice,
and the segment cut points they produce are published in the
`segment_thresholds` query so a reader can see exactly what each band means.

**Why recency is excluded.** In a right-censored window a customer who
registered six weeks ago has had less time to buy than one who registered two
years ago. Scoring recency would therefore measure *tenure* and label new
customers as inactive. Recency is still computed and reported per customer
(`dim_customers.recency_days`), but it is not a scoring input.

### 6.2 The resulting bands

| Segment | Score range | Net revenue range | Orders | Customers |
|---|---|---:|---|---:|
| Low Engagement | 0.0000 – 0.2251 | $12.67 – $264.40 | 1 | 670 |
| Regular | 0.2254 – 0.6198 | $107.49 – $1,548.51 | 1 – 4 | 669 |
| High Value | 0.6204 – 0.9976 | $481.17 – $10,318.23 | 2 – 22 | 669 |

### 6.3 Segment stability under filtering

Segments are computed **once**, on the full eligible population. Filtering the
dashboard by date, category or segment **selects** customers; it never
re-segments them. Re-segmenting under a filter would make a customer's segment
depend on which slice of the data the analyst happened to be looking at.

---

## 7. Filter semantics

| Filter | Level | Effect |
|---|---|---|
| Date range | Order | Restricts eligible orders by `order_date`. |
| Category | Line | Revenue and units count only the selected category's lines. "Orders" means orders **containing** at least one line in that category, so category AOV is "revenue per order that contained the category". |
| Segment | Customer | Restricts to orders placed by customers in that segment. |

Under any filter, the repeat purchase rate is recomputed **within the
filtered set**: a customer counts as a repeat if they placed two or more
qualifying orders inside the current selection. This is the only definition
that stays meaningful when the population changes.

---

## 8. Observation-window limitation

The repeat purchase rate is a **floor**, not a lifetime rate. The window is
finite and right-censored:

- A customer who bought once in January 2024 and would have bought again in
  February 2026 is recorded as a one-time customer.
- Customers who registered in the final months of the window have had
  mechanically less time to repeat.

Any statement about lifetime value, retention or churn is therefore bounded by
"within this 24-month window", and the report says so wherever it matters.

---

## 9. Query catalogue

33 named queries across two files. `src/run_queries.py` executes all of them
and renders `reports/sql_results.md`.

**`sql/04_analysis_queries.sql` (22 queries)**

| Query | Answers |
|---|---|
| `kpi_summary` | The five headline KPIs in one row |
| `revenue_bridge` | Gross → net revenue reconciliation |
| `monthly_revenue_trend` | Revenue, orders, units and AOV per month |
| `monthly_new_vs_repeat_revenue` | First-order vs repeat-order revenue per month |
| `revenue_by_category` | Category performance |
| `category_revenue_share` | Revenue share vs unit share (price mix) |
| `top_products_by_revenue` | Top 10 products by revenue |
| `top_products_by_units` | Top 10 products by volume |
| `high_volume_low_revenue_products` | Volume-rich, value-poor products |
| `aov_by_segment` | AOV across the three segments |
| `segment_distribution` | Customer and revenue mix by segment |
| `segment_profile` | Behavioural scorecard per segment |
| `segment_thresholds` | The cut points the segmentation produced |
| `repeat_purchase_summary` | Repeat counts, rate, orders per customer |
| `order_count_distribution` | How many customers placed exactly N orders |
| `interpurchase_interval` | Time between consecutive purchases |
| `repeat_vs_single_customer_profile` | Value comparison, repeat vs one-time |
| `revenue_by_channel` | Performance by sales channel |
| `discount_analysis` | Revenue and order count by discount band |
| `category_penetration` | Buyers and revenue per buyer per category |
| `acquisition_channel_performance` | Value of each acquisition channel |
| `top_category_pairs` | Most common category combinations in a basket |

**`sql/05_data_quality.sql` (11 queries)**

| Query | Answers |
|---|---|
| `dq_summary` | All 23 validation checks with counts and handling rules |
| `dq_row_reconciliation` | Row counts at every layer of the pipeline |
| `dq_duplicate_line_items` | The exact duplicate lines found |
| `dq_orphan_references` | Every dangling foreign key |
| `dq_invalid_line_values` | Lines with missing or out-of-range values |
| `dq_order_total_reconciliation` | Orders whose stored total disagrees with components |
| `dq_line_vs_order_subtotal` | Orders whose subtotal disagrees with its lines |
| `dq_reconciliation_explained` | Proves every variance is explained by a logged defect |
| `dq_defect_log_summary` | The audit trail of injected defects |
| `dq_status_distribution` | Statuses and their treatment |
| `dq_catalog_price_drift` | How often lines sold below list price |

---

## 10. Reproducibility

| Guarantee | How |
|---|---|
| Identical data on every machine | A single `random.Random(20261025)` stream; every draw is ordered and no unordered set or dict feeds a random choice. |
| Identical SQL on every machine | The observation window is injected from `src/config.py`; no date is hard-coded twice. |
| Identical segmentation | `PERCENT_RANK` + `NTILE(3)` with a `customer_id` tiebreak. |
| Identical money | Integer cents everywhere. |
| One source of truth for the KPIs | `sql/04_analysis_queries.sql` is read by the report renderer, the dashboard and the tests through `src/analytics.py`. |
| Defects are accounted for | `src/generate_data.py` writes `data/raw/defects.csv`; the build asserts that every reconciliation variance is in that log. |

Run the whole thing with `python src/run_all.py` and the test suite with
`pytest -q`.
