"""
DealDrift model evaluation.

Time-based train/test split (never random -- shuffling a price series would
leak future information into training) + RMSE/MAE, for both SARIMA and
Prophet, so they can be compared per-ASIN.

Same data caveat as train_model.py: with only a handful of same-day
observations currently in price_history, these metrics are a pipeline
smoke test, not a real accuracy measurement. They'll become meaningful once
data_extraction/scheduler.py has accumulated weeks of real daily history.
"""

import logging
import warnings

import pandas as pd
from sklearn.metrics import mean_absolute_error, root_mean_squared_error

from modeling.train_model import MIN_OBSERVATIONS, fit_prophet, fit_sarima, get_daily_price_series

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def time_based_split(series: pd.Series, test_size: int = 2) -> tuple[pd.Series, pd.Series]:
    """Split a chronologically-ordered series into (train, test), with the
    last `test_size` points held out. Returns (None, None) if there isn't
    enough data to leave a usable training set (at least 3 points)."""
    if len(series) < test_size + 3:
        logger.warning(
            "Series has %d points, too few for a %d-point test split (need >= %d)",
            len(series), test_size, test_size + 3,
        )
        return None, None
    return series.iloc[:-test_size], series.iloc[-test_size:]


def compute_metrics(y_true: pd.Series, y_pred: pd.Series) -> dict:
    """RMSE and MAE between aligned true/predicted series, via sklearn.metrics
    (root_mean_squared_error / mean_absolute_error -- sklearn 1.4+ dropped
    mean_squared_error's squared= param in favor of a dedicated RMSE func)."""
    rmse = float(root_mean_squared_error(y_true, y_pred))
    mae = float(mean_absolute_error(y_true, y_pred))
    return {"rmse": rmse, "mae": mae}


def evaluate_sarima(series: pd.Series, test_size: int = 2, order: tuple = (1, 1, 0)) -> dict | None:
    train, test = time_based_split(series, test_size=test_size)
    if train is None:
        return None
    try:
        fitted = fit_sarima(train, order=order)
        forecast = fitted.get_forecast(steps=len(test)).predicted_mean
        forecast.index = test.index
        metrics = compute_metrics(test, forecast)
        metrics["model"] = "sarima"
        return metrics
    except Exception:
        logger.exception("SARIMA evaluation failed")
        return None


def evaluate_prophet(series: pd.Series, test_size: int = 2) -> dict | None:
    train, test = time_based_split(series, test_size=test_size)
    if train is None:
        return None
    try:
        model = fit_prophet(train)
        if model is None:
            return None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            future = pd.DataFrame({"ds": test.index})
            forecast = model.predict(future)
        predicted = pd.Series(forecast["yhat"].values, index=test.index)
        metrics = compute_metrics(test, predicted)
        metrics["model"] = "prophet"
        return metrics
    except Exception:
        logger.exception("Prophet evaluation failed")
        return None


def compare_models_for_asin(asin: str, test_size: int = 2) -> pd.DataFrame:
    """Evaluate both models for one ASIN and return a comparison table
    (empty DataFrame if there wasn't enough data to evaluate either)."""
    series = get_daily_price_series(asin)
    if len(series) < MIN_OBSERVATIONS:
        logger.warning("ASIN %s: %d daily point(s), below MIN_OBSERVATIONS=%d", asin, len(series), MIN_OBSERVATIONS)
        return pd.DataFrame()

    rows = []
    sarima_metrics = evaluate_sarima(series, test_size=test_size)
    if sarima_metrics:
        rows.append(sarima_metrics)
    prophet_metrics = evaluate_prophet(series, test_size=test_size)
    if prophet_metrics:
        rows.append(prophet_metrics)

    df = pd.DataFrame(rows)
    if not df.empty:
        df.insert(0, "asin", asin)
    return df


if __name__ == "__main__":
    from config.db_config import get_connection

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT DISTINCT asin FROM price_history;")
            asins = [row["asin"] for row in cursor.fetchall()]
    finally:
        conn.close()

    print(f"Evaluating {len(asins)} ASINs (need >= {MIN_OBSERVATIONS} daily points to attempt)...\n")

    all_results = []
    for asin in asins:
        comparison = compare_models_for_asin(asin)
        if not comparison.empty:
            all_results.append(comparison)

    if all_results:
        combined = pd.concat(all_results, ignore_index=True)
        print(combined.to_string(index=False))
    else:
        print("No ASIN currently has enough daily history to evaluate. "
              "Expected today -- re-run once the scheduler has accumulated more history.")
