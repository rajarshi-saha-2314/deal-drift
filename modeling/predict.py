"""
DealDrift forecasting.

N-day forward price forecasts per ASIN, using the full available history
(unlike evaluate.py, which holds out a test set -- this is for genuine
forward-looking predictions, so it trains on everything there is).

Same data caveat as train_model.py/evaluate.py: forecasts produced today
are a pipeline demonstration only, not real predictions, until
data_extraction/scheduler.py has accumulated weeks of daily history.
"""

import logging
import warnings

import pandas as pd

from modeling.train_model import MIN_OBSERVATIONS, fit_prophet, fit_sarima, get_daily_price_series

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def forecast_sarima(series: pd.Series, days: int = 7, order: tuple = (1, 1, 0)) -> pd.DataFrame:
    """Forecast `days` steps ahead. Returns a DataFrame with columns
    [date, forecast, lower_ci, upper_ci]."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fitted = fit_sarima(series, order=order)
        pred = fitted.get_forecast(steps=days)
        mean = pred.predicted_mean
        ci = pred.conf_int(alpha=0.20)  # 80% interval -- narrower/more legible than 95% on thin data

    future_dates = pd.date_range(series.index.max() + pd.Timedelta(days=1), periods=days, freq="D")
    return pd.DataFrame({
        "date": future_dates,
        "forecast": mean.values,
        "lower_ci": ci.iloc[:, 0].values,
        "upper_ci": ci.iloc[:, 1].values,
        "model": "sarima",
    })


def forecast_prophet(series: pd.Series, days: int = 7) -> pd.DataFrame | None:
    """Forecast `days` steps ahead with Prophet. Returns None if Prophet
    isn't available."""
    model = fit_prophet(series)
    if model is None:
        return None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        future = model.make_future_dataframe(periods=days, freq="D")
        forecast = model.predict(future)

    tail = forecast.tail(days)
    return pd.DataFrame({
        "date": tail["ds"].values,
        "forecast": tail["yhat"].values,
        "lower_ci": tail["yhat_lower"].values,
        "upper_ci": tail["yhat_upper"].values,
        "model": "prophet",
    })


def forecast_price(asin: str, days: int = 7, model_type: str = "sarima") -> pd.DataFrame | None:
    """Forecast forward prices for one ASIN. model_type is 'sarima' or
    'prophet'. Returns None if there's not enough history to fit."""
    series = get_daily_price_series(asin)
    if len(series) < MIN_OBSERVATIONS:
        logger.warning(
            "ASIN %s: only %d daily observation(s), below MIN_OBSERVATIONS=%d -- cannot forecast yet",
            asin, len(series), MIN_OBSERVATIONS,
        )
        return None

    try:
        if model_type == "sarima":
            forecast_df = forecast_sarima(series, days=days)
        elif model_type == "prophet":
            forecast_df = forecast_prophet(series, days=days)
            if forecast_df is None:
                logger.warning("Prophet not available -- falling back to SARIMA for ASIN %s", asin)
                forecast_df = forecast_sarima(series, days=days)
        else:
            raise ValueError(f"Unknown model_type: {model_type!r} (expected 'sarima' or 'prophet')")
    except Exception:
        logger.exception("ASIN %s: forecast failed", asin)
        return None

    forecast_df.insert(0, "asin", asin)
    return forecast_df


if __name__ == "__main__":
    from config.db_config import get_connection

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT DISTINCT asin FROM price_history;")
            asins = [row["asin"] for row in cursor.fetchall()]
    finally:
        conn.close()

    print(f"Forecasting {len(asins)} ASINs 7 days forward (need >= {MIN_OBSERVATIONS} daily points)...\n")

    any_forecast = False
    for asin in asins:
        forecast_df = forecast_price(asin, days=7, model_type="sarima")
        if forecast_df is not None:
            any_forecast = True
            print(f"--- {asin} ---")
            print(forecast_df.to_string(index=False))
            print()

    if not any_forecast:
        print("No ASIN currently has enough daily history to forecast. "
              "Expected today -- re-run once the scheduler has accumulated more history.")
