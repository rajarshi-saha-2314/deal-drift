# DealDrift

Amazon price-intelligence and forecasting pipeline: scrape → store → clean →
forecast → report/visualize. Built as a portfolio project for a Data Analyst
job application, targeting `amazon.in`.

Every query is raw SQL via `pymysql` (no ORM) so it can be explained
directly; the database is a Railway-hosted MySQL instance, not local MySQL.

## Architecture

```
Amazon (amazon.in)
      |  Selenium renders, BeautifulSoup parses
      v
data_extraction/scraper.py  --(scheduled daily)-->  data_extraction/scheduler.py
      |
      v
database/loader.py  --(raw SQL upsert/insert)-->  Railway MySQL
      |                                             (products, sellers, price_history)
      v
processing/clean_validate.py  -->  processing/feature_engineering.py
      |  (dedup, type/currency normalization, outlier flags -> written back to DB)
      v
modeling/train_model.py  -->  modeling/evaluate.py  -->  modeling/predict.py
      |  (SARIMA primary, Prophet comparison; time-based split; N-day forecasts)
      v
reporting/excel_export.py            dashboard/app.py
   (client-deliverable .xlsx)     (Streamlit + Plotly, live queries)
```

## Project structure

```
dealdrift/
├── config/
│   └── db_config.py           # Railway MySQL connection (env-configured, no localhost default)
├── data_extraction/
│   ├── selectors.py           # centralized CSS selectors (confirmed vs. unverified, see file)
│   ├── scraper.py             # Selenium + BeautifulSoup scraping
│   └── scheduler.py           # APScheduler daily job
├── database/
│   ├── schema.sql             # products, sellers, price_history
│   ├── data_dictionary.md     # column definitions, PK/FK, design rationale
│   └── loader.py              # raw SQL upsert/insert
├── processing/
│   ├── clean_validate.py      # dedup, missing values, normalization, outlier flags
│   └── feature_engineering.py # rolling averages, discount %, seasonality flags
├── modeling/
│   ├── train_model.py         # SARIMA (primary) + Prophet (comparison)
│   ├── evaluate.py            # time-based train/test split, RMSE/MAE
│   └── predict.py             # N-day forward forecasts
├── reporting/
│   ├── excel_export.py        # openpyxl report: summary + price history + forecasts
│   └── summary_writeup.md     # client-style write-up TEMPLATE (fill in once real forecasts exist)
├── dashboard/
│   ├── app.py                 # Streamlit entrypoint
│   ├── db_queries.py          # cached (st.cache_data) raw SQL queries
│   └── charts.py              # Plotly chart builders
├── requirements.txt
├── .env.example
└── .gitignore
```

## Setup

1. **Clone and create the virtual environment** (already done if you're
   reading this in the built project):
   ```
   python -m venv venv
   venv\Scripts\activate            # Windows
   pip install -r requirements.txt
   ```
   Prophet installs cleanly on this project's environment (Python 3.13,
   Windows) via a prebuilt wheel — no SARIMA-only fallback was needed.

2. **Configure Railway MySQL credentials.** Copy `.env.example` to `.env`
   and fill in your **public/proxy** connection details from Railway's
   Connect tab (not the `*.railway.internal` private host — that only
   resolves inside Railway's own network, not from your machine):
   ```
   MYSQL_HOST=your-project.proxy.rlwy.net
   MYSQL_PORT=<the proxy port, NOT 3306>
   MYSQL_USER=root
   MYSQL_PASSWORD=...
   MYSQL_DATABASE=railway
   ```

3. **Create the schema.** Run `database/schema.sql` against your Railway
   database (any MySQL client works — Railway's query console, TablePlus,
   or a short pymysql script that executes the file).

## Running each part

| Step | Command | Notes |
|---|---|---|
| Test DB connection | `python -m config.db_config` | Prints the MySQL server version |
| Scrape + load once | `python -m database.loader` | Search page (1 request) + detail-page enrichment for the first 10 products (seller/availability, +10 requests) |
| Search-only scrape | `python -m data_extraction.scraper` | Lower-volume path, no DB write, prints results |
| Start the daily scheduler | `python -m data_extraction.scheduler` | **Long-running** — must stay running (or be re-triggered daily) to accumulate history. Runs at 03:00 local time by default; see `DAILY_RUN_HOUR`/`DAILY_RUN_MINUTE` in the file. |
| Clean + flag outliers | `python -m processing.clean_validate` | Writes `is_outlier` back to `price_history` |
| Build features | `python -m processing.feature_engineering` | Prints a sample of the engineered feature set |
| Check model feasibility | `python -m modeling.train_model` | Reports how many products have ≥5 daily observations (see caveat below) |
| Evaluate models | `python -m modeling.evaluate` | RMSE/MAE per product, SARIMA vs. Prophet |
| Forecast | `python -m modeling.predict` | 7-day forward forecast per fittable product |
| Export Excel report | `python -m reporting.excel_export` | Writes `reports/dealdrift_report.xlsx` |
| Run the dashboard | `streamlit run dashboard/app.py` | Opens at `localhost:8501` |

## Data caveat: forecasting needs real accumulated history

`modeling/train_model.py` gates every fit behind `MIN_OBSERVATIONS = 5`
**daily** observations per product (same-day scrapes correctly collapse to
one point per calendar day — they don't count as separate days). As of this
project's initial build, price history only spans same-day test scrapes, so
forecasts are a **pipeline smoke test, not real predictions** — verified
separately against a synthetic series to confirm the SARIMA/Prophet fit,
evaluate, and forecast code paths all work correctly.

Both `reporting/excel_export.py` and `dashboard/app.py` are wired to real
data end-to-end and degrade gracefully: with insufficient history they show
an explicit "not enough data yet" state rather than a fabricated chart, and
will start showing real forecasts automatically once
`data_extraction/scheduler.py` has been running long enough (several weeks
of daily runs recommended before treating output as meaningful).

Do not publish `reporting/summary_writeup.md` with real conclusions until
that data exists — see the caveat at the bottom of that file.

## Key design decisions

- **No ORM** — every query in `database/loader.py`, `processing/`,
  `modeling/`, and `dashboard/db_queries.py` is raw SQL via `pymysql`.
- **ASIN as natural primary key** for `products` (see
  `database/data_dictionary.md` for the full rationale).
- **Engineered features are not persisted** — rolling averages, discount
  buckets, and seasonality flags are computed on-demand from `price_history`
  in `processing/feature_engineering.py`, since they're fully derivable from
  raw data. The one exception is `price_history.is_outlier`, which is
  written back since it's a flag on existing rows, not a new derived table.
- **Selectors are marked CONFIRMED / UNVERIFIED** in `data_extraction/selectors.py`
  — CONFIRMED ones came from real pasted HTML or were empirically validated
  against a live scrape; UNVERIFIED ones are best-effort standard Amazon
  selectors flagged for re-checking.
- **Desktop-only User-Agent rotation** — `fake-useragent`'s unfiltered
  `.random` can return a mobile UA, which makes Amazon serve different
  markup server-side and silently breaks every selector; `scraper.py`
  filters to `platforms=["desktop"]`.
- **Sample-based seller enrichment** — the scraper detail-scrapes only the
  first 10 products per run (not all) for `seller_name`/`availability`, to
  bound request volume against Amazon regardless of catalog size.
- **Currency**: prices are stored in INR (`amazon.in`) with an explicit
  `currency` column rather than an assumed-USD default; no FX conversion is
  performed (no forex source in the approved dependency list).

## Known limitations / next steps

- No exploratory-analysis notebook yet — worth adding once there's more
  accumulated data to explore.
- Several `data_extraction/selectors.py` selectors (`DETAIL_REVIEW_COUNT`,
  `DETAIL_LIST_PRICE_FALLBACK`, `DETAIL_IMAGE`) remain UNVERIFIED — they
  weren't exercised by the merge path yet (only used as a fallback when the
  search-page card already had the value).
- Not deployed anywhere — everything above runs locally against Railway
  MySQL, per project scope (no Streamlit Cloud push, no Docker).
