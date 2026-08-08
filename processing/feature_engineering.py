"""
DealDrift feature engineering.

Builds on processing/clean_validate.py's cleaned DataFrame and adds
modeling-ready features: rolling price averages, discount features, and
seasonality flags. These are computed on-demand from price_history rather
than persisted as extra columns/tables (see database/data_dictionary.md) --
they're fully derivable from raw data every time this runs.
"""

import logging

import numpy as np
import pandas as pd

from processing.clean_validate import clean_and_validate

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Time-based rolling windows (pandas offset strings) applied per ASIN.
ROLLING_WINDOWS = ("3D", "7D", "14D")

DISCOUNT_BUCKET_BINS = [-0.01, 0, 25, 50, 75, 100]
DISCOUNT_BUCKET_LABELS = ["0%", "0-25%", "25-50%", "50-75%", "75-100%"]


def add_rolling_features(df: pd.DataFrame, windows=ROLLING_WINDOWS) -> pd.DataFrame:
    """Per-ASIN rolling mean/std of price over time-based windows (not
    row-count windows, since scrapes aren't perfectly evenly spaced).
    min_periods=1 so early history still gets a value instead of NaN --
    with only a few observations per ASIN so far, a strict min_periods
    would leave most of the dataset empty.
    """
    df = df.sort_values(["asin", "scraped_at"]).reset_index(drop=True)

    for window in windows:
        mean_col = f"price_roll_mean_{window}"
        std_col = f"price_roll_std_{window}"
        means = pd.Series(index=df.index, dtype=float)
        stds = pd.Series(index=df.index, dtype=float)

        for _, group in df.groupby("asin"):
            rolling = group.set_index("scraped_at")["price"].rolling(window, min_periods=1)
            means.loc[group.index] = rolling.mean().values
            stds.loc[group.index] = rolling.std().values

        df[mean_col] = means
        df[std_col] = stds

    return df


def add_discount_features(df: pd.DataFrame) -> pd.DataFrame:
    """discount_pct is already computed in clean_validate.py; this adds a
    simple boolean flag and a bucketed category for easier grouping/plots."""
    df = df.copy()
    df["is_discounted"] = df["discount_pct"].notna() & (df["discount_pct"] > 0)
    df["discount_bucket"] = pd.cut(
        df["discount_pct"].fillna(0), bins=DISCOUNT_BUCKET_BINS, labels=DISCOUNT_BUCKET_LABELS
    )
    return df


def add_seasonality_flags(df: pd.DataFrame) -> pd.DataFrame:
    """Calendar-derived seasonality features from scraped_at. No external
    holiday-calendar library is in the approved dependency list, so this
    sticks to plain calendar fields rather than e.g. Diwali/Christmas flags."""
    df = df.copy()
    df["day_of_week"] = df["scraped_at"].dt.dayofweek  # Monday=0 .. Sunday=6
    df["is_weekend"] = df["day_of_week"].isin([5, 6])
    df["month"] = df["scraped_at"].dt.month
    df["day_of_month"] = df["scraped_at"].dt.day
    df["is_month_start"] = df["scraped_at"].dt.is_month_start
    df["is_month_end"] = df["scraped_at"].dt.is_month_end
    return df


def build_feature_set(asin: str | None = None, persist_outlier_flags: bool = True) -> pd.DataFrame:
    """End-to-end: load + clean (via clean_validate) + engineer features.
    Returns a DataFrame ready to feed into modeling/train_model.py."""
    df = clean_and_validate(asin=asin, persist_outlier_flags=persist_outlier_flags)
    if df.empty:
        return df

    df = add_rolling_features(df)
    df = add_discount_features(df)
    df = add_seasonality_flags(df)

    logger.info("Built feature set: %d rows, %d columns", len(df), len(df.columns))
    return df


if __name__ == "__main__":
    features = build_feature_set()
    if not features.empty:
        print(f"\nFeature set: {len(features)} rows, {len(features.columns)} columns")
        print("\nColumns:", list(features.columns))
        cols_to_show = [
            "asin", "scraped_at", "price", "price_roll_mean_7D",
            "discount_pct", "discount_bucket", "is_weekend",
        ]
        print("\nSample:")
        print(features[cols_to_show].head(10).to_string(index=False))
