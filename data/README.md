# `data/` — the dataset

## ⚠️ This data is synthetic

`data/raw/` contains **generated** e-commerce data. It is not a Shopify
export, not a scrape, and not derived from any real merchant or customer.
Every row carries `source = 'synthetic'`.

Shopify publishes no public transaction dataset, and the workspace this project
was built in contained no e-commerce data of any kind. Rather than quietly
substituting an unrelated public dataset, this project generates its own store
and labels every downstream finding as a demonstration.

---

## What is here

| File | Rows | Grain |
|---|---:|---|
| `customers.csv` | 2,100 | One row per customer |
| `products.csv` | 114 | One row per product |
| `orders.csv` | 5,360 | One row per order (a checkout event) |
| `order_items.csv` | 15,636 | One row per order line item (a unit of sale) |
| `defects.csv` | 93 | The audit trail of every injected data defect |

These files are **committed** rather than git-ignored, so the analysis can be
reproduced without running the generator first. They are deterministic: the
same seed always produces the same bytes.

---

## How it is generated

```bash
python src/generate_data.py
```

* **Seed:** `20261025`, set in `src/config.py`.
* **RNG:** a single `random.Random(seed)` stream drives every draw, so the
  output is identical on any machine and any Python 3.10+ runtime.
* **Window:** 2024-01-01 → 2025-12-31 (two full years, so seasonality and
  year-over-year growth are both visible).
* **Money:** computed in **integer cents** throughout, so the CSV, the SQL and
  the stored order totals agree to the cent.

### The model

**Customers (2,100)** — drawn from four latent loyalty tiers that set their
order counts, which is what creates the realistic long tail of one-time
buyers, a solid repeat base and a small group of very loyal customers:

| Tier | Share | Orders |
|---|---:|---|
| `one_time` | 60% | 1 |
| `occasional` | 28% | 2 – 4 |
| `regular` | 9% | 5 – 9 |
| `loyal` | 3% | 10 – 22 |

Each customer also gets: a registration date (biased so loyal customers
registered earlier), a country (10 markets), a region, an acquisition channel
(7 channels), a device, a newsletter flag, and a **category affinity** — two
or three favoured categories that drive their basket composition.

**Products (114)** — 8 categories, each with its own subcategories, price band
and product types (see `PRODUCT_CATALOGUE` in `src/generate_data.py`). Prices
are log-normal around a category base price. **Popularity is heavy-tailed**: a
handful of products dominate unit volume while most sell rarely, which is what
makes the "high volume / low revenue" analysis meaningful.

**Orders (5,360)** — monthly seasonality with a Q4 peak (November/December
carry the most weight, February the least), year-over-year growth of ~18% in
2025, and a status mix of ~95% completed, 2.8% cancelled, 1.7% refunded and
0.8% pending. About 4.5% of completed orders carry a partial refund. Orders
under $75 pay shipping unless they randomly qualify for free shipping; tax is
a simple country rate.

**Order line items (15,636)** — 1–8 lines per order, 2.92 on average.
**Shopify merges identical product lines inside one cart**, so the generator
merges same-product lines within an order too — which makes "duplicate line
item" a meaningful defect to detect rather than an accident.

---

## The deliberate defects

A perfectly clean synthetic dataset would prove nothing about the cleaning
layer. 93 defects are injected on purpose, on **disjoint** pools of rows so
that no defect can mask another:

| Defect | Count | How the analysis handles it |
|---|---:|---|
| Duplicate line item | 14 | De-duplicated on the Shopify business key |
| Line item → missing product | 9 | Line dropped |
| Line item → missing order | 6 | Line dropped |
| Order → missing customer | 6 | Order dropped |
| NULL quantity | 8 | Line dropped |
| Negative quantity | 7 | Line dropped (a returns artefact, not a sale) |
| Zero quantity | 5 | Line dropped |
| NULL unit price | 6 | Line dropped |
| Non-positive unit price | 6 | Line dropped |
| Stored discount ≠ pct × gross | 12 | Recomputed from `discount_pct` |
| Order total ≠ components | 9 | Components are authoritative |
| Completed order with no items | 5 | Excluded from the eligible-order set |

`defects.csv` records every one of them — the defect type, the affected
record and the order it touched. The build asserts that **every** order-level
reconciliation variance in the cleaned model is matched by an entry in that
log (`unexplained = 0`), so a reconciliation gap can never hide.

---

## Column dictionary

See [`dictionary.md`](dictionary.md) for a column-by-column description of all
four tables.

---

## Using a real dataset instead

Everything except `src/generate_data.py` is store-agnostic. To point this
project at a real Shopify export:

1. Drop your four CSVs into `data/raw/` with the column names in
   `dictionary.md` (renaming as needed).
2. Adjust the column mapping in `src/build_db.py` and the cleaning rules in
   `sql/02_staging.sql`.
3. Delete `data/raw/defects.csv` — it only describes synthetic defects — and
   remove the defect-log assertion in `src/build_db.py`.
4. Set `PERIOD_START` / `PERIOD_END` in `src/config.py` to your window. It is
   injected into the SQL automatically.
5. Run `python src/run_all.py`.

**Read the revenue definition carefully first.** A real Shopify order export
carries `total_price`, `total_discount`, `total_tax`, `total_shipping` and
`total_line_items` with specific semantics (for example `total_discount` is
sometimes a negative number and sometimes a positive one, and `total_tax` is
inclusive of some jurisdiction taxes). Blindly summing them is the single
most common way an e-commerce revenue number ends up wrong.
