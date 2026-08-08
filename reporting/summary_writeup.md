# DealDrift — Price Intelligence Summary

> **Template.** This is a skeleton for a short, client-style write-up —
> replace every `[bracketed]` placeholder with real findings once there's
> enough accumulated price history (see caveat below). Keep it to
> roughly one page: this is meant to read like something you'd actually
> hand to a stakeholder, not a data dump.

**Report period:** `[start date]` – `[end date]`
**Category tracked:** `[e.g. "lighting room decor" on amazon.in]`
**Products tracked:** `[N]`

---

## Executive Summary

`[2-3 sentences: what did prices do over the period, and what should the
reader do about it? e.g. "Average prices across tracked lighting products
fell 8% over the past 3 weeks, with the steepest declines in [category].
Products X, Y, and Z are currently at their lowest observed price and are
good near-term buy candidates."]`

## Key Findings

1. **Price trend:** `[Did prices trend up, down, or stay flat overall?
   Any notable single-product swings worth calling out?]`
2. **Discount patterns:** `[Are discounts concentrated in certain sellers,
   price bands, or days of week? Reference the discount_bucket /
   is_weekend features from processing/feature_engineering.py.]`
3. **Volatility / outliers:** `[Any products flagged by
   processing/clean_validate.py's is_outlier logic? Worth a sentence on
   what caused it (e.g. a flash sale, a data glitch, a genuine repricing).]`
4. **Forecast highlights:** `[Which products does the model expect to
   drop/rise over the next 7 days, and by how much? Only fill this in once
   modeling/predict.py has real forecasts — see caveat.]`

## Recommendations

- `[e.g. "Wait N days before purchasing X — forecast suggests a further
  Y% drop."]`
- `[e.g. "Product Z's price has been stable for N weeks with no discount
  activity — unlikely to drop further soon."]`

## Methodology (brief)

- Prices scraped from amazon.in search results (`[frequency, e.g. "daily"]`)
  via Selenium + BeautifulSoup, stored in a Railway-hosted MySQL database.
- Forecasts: SARIMA (statsmodels) as the primary model, Prophet as a
  comparison model, evaluated via time-based train/test split (RMSE/MAE).
- Full schema and column definitions: `database/data_dictionary.md`.

---

### ⚠ Data caveat (remove once resolved)

As of **2026-08-08**, price history only spans same-day test scrapes —
not enough for SARIMA/Prophet to produce a meaningful forecast (see
`modeling/train_model.py`'s `MIN_OBSERVATIONS` gate). Do not publish this
write-up with real conclusions until `data_extraction/scheduler.py` has
been running long enough (several weeks recommended) to produce genuine
day-over-day price movement. Run `reporting/excel_export.py` to check
whether the Forecasts sheet has populated yet.
