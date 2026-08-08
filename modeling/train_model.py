"""
DealDrift model training.

SARIMA (statsmodels) is the primary model; Prophet is a comparison model,
per project spec. Both are fit per-ASIN on a daily-resampled price series.

*** IMPORTANT DATA CAVEAT (2026-08-08) ***
As of this writing, price_history has at most a handful of same-day
observations per ASIN (the scheduler was only just built and hasn't run
for real yet). SARIMA/Prophet need weeks of daily observations to produce
forecasts that mean anything -- fit against today's data will not be
predictive, just a pipeline smoke test. This module is being built now
so the pipeline exists end-to-end and improves automatically as
data_extraction/scheduler.py accumulates real daily history; it is NOT yet
producing forecasts fit for the job-application deliverable. Re-run
everything in modeling/ once there are several weeks of real data and
discard early results.

Series are resampled to one point per calendar day (last price observed
that day) before fitting -- correct behavior regardless of today's
sparsity, and it's what makes multiple same-day scrapes not get treated as
separate "days" once the daily scheduler is the only thing populating data.
"""

import logging
import warnings

import pandas as pd
from statsmodels.tsa.statespace.sarimax import SARIMAX

from processing.clean_validate import load_price_history_df

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Below this many daily observations, statsmodels/Prophet either error out
# or produce output too unstable to call a "model" -- used across
# train_model.py/evaluate.py/predict.py so the "not enough data" bar is
# consistent everywhere.
MIN_OBSERVATIONS = 5

# Prophet is optional at import time -- it installed fine in this project's
# venv (verified 2026-08-08), but keep the pipeline usable even if a future
# environment lacks it (per original spec: fall back to SARIMA-only rather
# than getting stuck).
try:
    from prophet import Prophet
    PROPHET_AVAILABLE = True
except ImportError:
    PROPHET_AVAILABLE = False
    logger.warning("prophet not importable -- Prophet comparison model will be skipped")


def get_daily_price_series(asin: str) -> pd.Series:
    """Load an ASIN's cleaned price history and resample to one point per
    calendar day (last observed price that day). Returns a Series indexed
    by date, empty if there's no data for this ASIN."""
    df = load_price_history_df(asin=asin)
    if df.empty:
        return pd.Series(dtype=float)

    df["scraped_at"] = pd.to_datetime(df["scraped_at"])
    # pymysql returns DECIMAL columns as Python Decimal -> object dtype,
    # which resample().last() tolerates (no arithmetic) but SARIMAX/Prophet
    # do not. Coerce to float now so the bug doesn't surface later, silently,
    # only once real data finally clears MIN_OBSERVATIONS.
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df = df.sort_values("scraped_at").set_index("scraped_at")
    daily = df["price"].resample("D").last().dropna()
    return daily


def fit_sarima(series: pd.Series, order: tuple = (1, 1, 0)):
    """Fit a non-seasonal SARIMA(order) model. Seasonal terms are
    deliberately left off (seasonal_order defaults to no seasonality) --
    estimating e.g. weekly seasonality needs several full weeks of daily
    data, which doesn't exist yet. Revisit once it does.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # statsmodels is noisy on short series
        model = SARIMAX(series, order=order, enforce_stationarity=False, enforce_invertibility=False)
        fitted = model.fit(disp=False)
    return fitted


def fit_prophet(series: pd.Series):
    """Fit a Prophet model. Returns None if prophet isn't available."""
    if not PROPHET_AVAILABLE:
        return None
    df = pd.DataFrame({"ds": series.index, "y": series.values})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = Prophet(daily_seasonality=False, weekly_seasonality=False, yearly_seasonality=False)
        model.fit(df)
    return model


def train_models_for_asin(asin: str, sarima_order: tuple = (1, 1, 0)) -> dict:
    """Fit both models for one ASIN. Returns a dict with the series and
    fitted models (either may be None if data was insufficient or fitting
    failed -- callers should check before using)."""
    series = get_daily_price_series(asin)
    result = {"asin": asin, "series": series, "sarima": None, "prophet": None}

    if len(series) < MIN_OBSERVATIONS:
        logger.warning(
            "ASIN %s: only %d daily observation(s), below MIN_OBSERVATIONS=%d -- "
            "skipping model fit (pipeline works, but there's nothing to fit yet)",
            asin, len(series), MIN_OBSERVATIONS,
        )
        return result

    try:
        result["sarima"] = fit_sarima(series, order=sarima_order)
    except Exception:
        logger.exception("ASIN %s: SARIMA fit failed", asin)

    try:
        result["prophet"] = fit_prophet(series)
    except Exception:
        logger.exception("ASIN %s: Prophet fit failed", asin)

    return result


if __name__ == "__main__":
    # Smoke test: try every ASIN currently in the DB, report how many have
    # enough data to fit at all.
    from config.db_config import get_connection

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT DISTINCT asin FROM price_history;")
            asins = [row["asin"] for row in cursor.fetchall()]
    finally:
        conn.close()

    print(f"Checking {len(asins)} ASINs for model-fitting feasibility "
          f"(need >= {MIN_OBSERVATIONS} daily observations)...\n")

    fittable = 0
    for asin in asins:
        series = get_daily_price_series(asin)
        status = "FITTABLE" if len(series) >= MIN_OBSERVATIONS else "insufficient data"
        if len(series) >= MIN_OBSERVATIONS:
            fittable += 1
        print(f"  {asin}: {len(series)} daily point(s) -- {status}")

    print(f"\n{fittable}/{len(asins)} ASINs currently have enough history to fit a model.")
    print("Expected to be low/zero today -- this improves as the scheduler runs over time.")
