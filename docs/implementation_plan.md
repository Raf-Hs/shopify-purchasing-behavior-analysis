# Implementation plan

Two plans live here: the original one (written before any code) and the
recovery plan written after the interrupted session was restarted.

---

## Part 1 — Original plan (written before any code, kept for auditability)

## Constraints discovered during repository inspection

- Workspace root `C:\Users\Rafae\Downloads\net` is **not** a git repo; it contains sibling
  portfolio projects (`airbnb-queretaro-analysis`, `league-of-legends-match-analysis`) that
  establish the house pattern to follow.
- A shared `.venv` exists at the workspace root (Python 3.12.10, SQLite 3.49.1) with
  pandas / numpy / matplotlib / seaborn / SQLAlchemy. `streamlit`, `plotly` and `pytest`
  were missing and were installed into it.
- **No e-commerce or Shopify dataset exists anywhere in the workspace.** The project
  therefore ships a *reproducible synthetic* dataset and labels every finding accordingly.

## Steps

| # | Step | Deliverable |
|---|---|---|
| 1 | Lock the revenue and KPI definitions in code | `src/config.py` |
| 2 | Generate seeded synthetic Shopify-like data (customers / products / orders / order items) with deliberate quality defects | `src/generate_data.py` → `data/raw/*.csv` |
| 3 | Model the raw landing tables | `sql/01_schema.sql` |
| 4 | Build the documented cleaning + analytic layer | `sql/02_views.sql` |
| 5 | Write the analysis queries (revenue, AOV, segmentation, repeat, product, funnel) | `sql/03_analysis_queries.sql` |
| 6 | Write the data-quality checks | `sql/04_data_quality.sql` |
| 7 | Load CSV → SQLite, verify integrity | `src/build_db.py` |
| 8 | One shared query layer so dashboard and tests use the same SQL | `src/analytics.py` |
| 9 | Render query results to a markdown appendix | `src/run_queries.py` |
| 10 | Render report figures | `src/make_figures.py` |
| 11 | Build the Streamlit dashboard with filters and 5 KPIs | `dashboard/app.py` |
| 12 | Automated tests (data volume, KPI formulas, segment coverage, dashboard↔SQL parity) | `tests/test_analysis.py` |
| 13 | Business report, data-quality report, methodology, README | `reports/`, `docs/`, `README.md` |
| 14 | Orchestrate everything in one command | `src/run_all.py` |

## Key modelling decisions

1. **Revenue** = `Σ quantity × unit_price × (1 − discount_pct)` over *eligible* order lines,
   minus order-level refunds allocated pro-rata by net line value. Shipping and tax are
   excluded from revenue and reported separately. The full bridge is published.
2. **Eligible order** = `order_status = 'completed'`, customer resolvable, and at least one
   valid line item. Cancelled / refunded / pending orders are retained in the raw tables and
   excluded from every revenue and order-count metric.
3. **Segmentation** = composite `value_score = 0.65 × spend percentile + 0.35 × order-count
   percentile`, banded by `NTILE(3)` with a `customer_id` tiebreak → three mutually exclusive,
   collectively exhaustive segments. Recency is deliberately excluded (it confounds tenure with
   engagement in a right-censored window).
4. **Repeat customer** = customer with ≥ 2 eligible orders inside the observation window.
5. **One source of truth**: `sql/04_analysis_queries.sql` holds the canonical KPI SQL; the
   dashboard, the report and the tests all read the same named queries.

---

## Part 2 — Deviations from the original plan (recorded after the build)

The plan above was written before any code. Four things changed during the
build, and each change was forced by a measurement rather than a preference.

| Planned | Built | Why |
|---|---|---|
| `sql/02_views.sql` (one file of views) | `sql/02_staging.sql` + `sql/03_marts.sql` (staging and mart **tables**) | SQLite inlines a view into its consumer, so the dimension expressions were re-evaluated once per fact row: a 0.02 s reference took 7 s and a 5,000-row join ran for minutes. The standard warehouse pattern (indexed staging and mart tables) fixed it. |
| `sql/03_analysis_queries.sql`, `sql/04_data_quality.sql` | `sql/04_analysis_queries.sql`, `sql/05_data_quality.sql` | Two SQL files were added to separate the raw schema from the staging layer, so the numbering follows the pipeline. |
| Money computed in floating point | Money computed in **integer cents** | Python's `round()` (half-to-even) and SQLite's `ROUND()` (printf-style) disagree on exact ties. In a float build this produced one-cent gaps between 37 orders' stored subtotals and their recomputed lines — an artefact of the tooling that would have polluted the reconciliation check. |
| A single `reports/data_quality.md` written by hand | `src/make_quality_report.py` generates it from the validation queries | A hand-written quality report drifts from the queries. Generating it means the numbers in the report are the numbers the queries return. |

Two additions that the plan did not anticipate:

* **`data/raw/defects.csv`** — an audit log of every injected defect. It lets
  `src/build_db.py` assert that *every* order-level reconciliation variance in
  the cleaned model is explained (`unexplained = 0`), so a gap can never hide
  behind a plausible-looking number.
* **`tests/smoke_dashboard.py`** — boots the real Streamlit server and requests
  the page over HTTP, because a passing unit test on the KPI functions says
  nothing about whether the application starts.

---

## Part 3 — Recovery plan (second session)

The first session was interrupted while adding a *Retention opportunity* view of
the data. It left the workspace in a mid-edit state: a concurrent process was
still writing files while the tests were being run, so the same test failed
with two different errors on consecutive runs.

### What was found on restart

| Finding | Evidence |
|---|---|
| A second, orphaned `opencode` session was still running and editing the repo | `dashboard/app.py` mtime moved between two test runs; the failure changed from `KeyError: 'basket_order'` to `KeyError: 'dark'` |
| The orphan had been killed mid-write | `PALETTE["dark"]` referenced a colour that was never added to the palette |
| The new *Retention opportunity* tab **replaced** the *Customers* tab rather than joining it | `tabs` went from 5 to 5 entries; segment distribution, orders-per-customer and time-between-purchases were dropped |
| `reports/sql_results.md` was stale | Written at 12:45, before the seven retention queries landed at 13:22 |
| The README query count was already wrong at the previous commit | Claimed "33 named SQL queries" / "22 analysis queries"; the files hold 29 analysis + 11 validation = **40** |
| A broken documentation link | The dashboard's Methodology tab pointed at `reports/decision_report.md`, which does not exist |

### Recovery steps

| # | Step | Done |
|---|---|---|
| 1 | Terminate the orphaned session and its in-flight child processes so the tree stops moving | ✔ |
| 2 | Re-establish a stable baseline: run the full suite and record the real failures | ✔ |
| 3 | Fix the palette bug that crashed the dashboard on the Revenue tab | ✔ |
| 4 | Restore the *Customers* tab **and** keep the new *Retention opportunity* tab (6 tabs), so nothing required by the brief is lost | ✔ |
| 5 | Fix the broken report link in the Methodology tab | ✔ |
| 6 | Audit every query/column the dashboard depends on against the SQL library | ✔ |
| 7 | Regenerate every derived artefact: `sql_results.md`, `key_metrics.json`, `data_quality.md`, figures | ✔ |
| 8 | Correct the README's query, tab, figure and test counts | ✔ |
| 9 | Extend the report and README with the retention findings | ✔ |
| 10 | Run the full suite and the live server smoke test to green | ✔ |

### Final state

```
58 passed
```

Run with `pytest -q` from the project root.
