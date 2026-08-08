"""
DealDrift data cleaning & validation.

Pulls raw price_history (+ products) rows via raw SQL into a pandas
DataFrame, applies dedup / missing-value handling / type & currency
normalization / outlier flagging, and writes the outlier flags back to
price_history.is_outlier via raw SQL UPDATE. No ORM.

This is a processing step, not a scrape step -- it never touches Amazon,
only the database.
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd

from config.db_config import get_connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

VALID_CURRENCIES = {"INR", "USD"}  # extend if you start tracking other locales
MIN_POINTS_FOR_OUTLIER_CHECK = 4  # need a few observations before IQR is meaningful


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

_LOAD_SQL = """
    SELECT
        ph.price_history_id, ph.asin, ph.seller_id, ph.scraped_at, ph.price,
        ph.list_price, ph.discount_pct, ph.currency, ph.rating, ph.review_count,
        ph.availability, ph.is_prime, ph.is_outlier,
        p.title, p.category
    FROM price_history ph
    JOIN products p ON p.asin = ph.asin
    {where_clause}
    ORDER BY ph.asin, ph.scraped_at
"""


def load_price_history_df(asin: Optional[str] = None) -> pd.DataFrame:
    """Load price_history joined with product title/category into a DataFrame."""
    where_clause = "WHERE ph.asin = %(asin)s" if asin else ""
    sql = _LOAD_SQL.format(where_clause=where_clause)
    params = {"asin": asin} if asin else None

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
    finally:
        conn.close()

    df = pd.DataFrame(rows)
    if df.empty:
        logger.warning("No price_history rows found (asin filter=%s)", asin)
    return df


# ---------------------------------------------------------------------------
# Cleaning steps
# ---------------------------------------------------------------------------

def deduplicate(df: pd.DataFrame) -> pd.DataFrame:
    """Defensive dedup on (asin, scraped_at) -- the DB's UNIQUE constraint
    should already prevent this, but this guards against, e.g., loading
    from a CSV export or a future code path that bypasses the loader."""
    before = len(df)
    df = df.drop_duplicates(subset=["asin", "scraped_at"], keep="last")
    dropped = before - len(df)
    if dropped:
        logger.info("Dropped %d exact-duplicate (asin, scraped_at) rows", dropped)
    return df


def drop_missing_price(df: pd.DataFrame) -> pd.DataFrame:
    """price is the one field everything downstream depends on -- rows
    missing it are unusable and dropped (defensive; price is NOT NULL in
    the schema, but scraper.py warnings show this can happen upstream)."""
    before = len(df)
    df = df[df["price"].notna()].copy()
    dropped = before - len(df)
    if dropped:
        logger.info("Dropped %d rows with missing price", dropped)
    return df


def normalize_types_and_ranges(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce columns to consistent numeric/datetime dtypes and null out
    values outside a sane range rather than silently trusting bad data."""
    df = df.copy()

    numeric_cols = ["price", "list_price", "discount_pct", "rating", "review_count"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["scraped_at"] = pd.to_datetime(df["scraped_at"])

    # Range checks -- out-of-bounds values become NaN (flagged, not dropped;
    # the row's price is still usable even if e.g. rating looks bad).
    invalid_rating = df["rating"].notna() & ~df["rating"].between(0, 5)
    if invalid_rating.any():
        logger.warning("Nulling %d rating value(s) outside [0, 5]", invalid_rating.sum())
        df.loc[invalid_rating, "rating"] = np.nan

    invalid_review_count = df["review_count"].notna() & (df["review_count"] < 0)
    if invalid_review_count.any():
        logger.warning("Nulling %d negative review_count value(s)", invalid_review_count.sum())
        df.loc[invalid_review_count, "review_count"] = np.nan

    invalid_price = df["price"].notna() & (df["price"] <= 0)
    if invalid_price.any():
        logger.warning("Dropping %d row(s) with non-positive price", invalid_price.sum())
        df = df[~invalid_price]

    # discount_pct should be derivable from price/list_price; recompute
    # rather than trusting whatever was stored, so it's always consistent.
    has_list_price = df["list_price"].notna() & (df["list_price"] > 0) & (df["price"] <= df["list_price"])
    df["discount_pct"] = np.where(
        has_list_price, (df["list_price"] - df["price"]) / df["list_price"] * 100, np.nan
    )

    return df


def flag_unexpected_currency(df: pd.DataFrame) -> pd.DataFrame:
    """Flag (log, don't drop) rows with a currency outside VALID_CURRENCIES
    -- this project doesn't do FX conversion (no forex source in the
    approved library list), so mixed-currency analysis would be misleading
    if it ever happened silently."""
    df = df.copy()
    unexpected = ~df["currency"].isin(VALID_CURRENCIES)
    if unexpected.any():
        logger.warning(
            "Found %d row(s) with unexpected currency: %s",
            unexpected.sum(), df.loc[unexpected, "currency"].unique().tolist(),
        )
    return df


def flag_outliers(df: pd.DataFrame) -> pd.DataFrame:
    """Per-ASIN IQR-based outlier flagging on price. ASINs with fewer than
    MIN_POINTS_FOR_OUTLIER_CHECK observations are left un-flagged (False) --
    not enough history yet to say what's "normal" for that product."""
    df = df.copy()
    df["is_outlier"] = False

    for asin, group in df.groupby("asin"):
        if len(group) < MIN_POINTS_FOR_OUTLIER_CHECK:
            continue
        q1, q3 = group["price"].quantile([0.25, 0.75])
        iqr = q3 - q1
        if iqr == 0:
            continue  # every price identical -- nothing to flag
        lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        outlier_mask = (group["price"] < lower) | (group["price"] > upper)
        if outlier_mask.any():
            logger.info("ASIN %s: flagged %d/%d price(s) as outliers", asin, outlier_mask.sum(), len(group))
        df.loc[group.index[outlier_mask], "is_outlier"] = True

    return df


# ---------------------------------------------------------------------------
# Write back
# ---------------------------------------------------------------------------

_UPDATE_OUTLIER_SQL = "UPDATE price_history SET is_outlier = %(is_outlier)s WHERE price_history_id = %(id)s"


def write_outlier_flags(df: pd.DataFrame) -> int:
    """Persist the is_outlier column back to price_history. Returns the
    number of rows updated."""
    conn = get_connection()
    updated = 0
    try:
        with conn.cursor() as cursor:
            for row in df.itertuples():
                cursor.execute(
                    _UPDATE_OUTLIER_SQL,
                    {"is_outlier": bool(row.is_outlier), "id": row.price_history_id},
                )
                updated += 1
        conn.commit()
    finally:
        conn.close()
    logger.info("Wrote is_outlier back for %d row(s)", updated)
    return updated


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def clean_and_validate(asin: Optional[str] = None, persist_outlier_flags: bool = True) -> pd.DataFrame:
    """Run the full cleaning pipeline and return the cleaned DataFrame.
    If persist_outlier_flags, also writes is_outlier back to the DB."""
    df = load_price_history_df(asin=asin)
    if df.empty:
        return df

    logger.info("Loaded %d raw price_history rows (%d distinct ASINs)", len(df), df["asin"].nunique())

    df = deduplicate(df)
    df = drop_missing_price(df)
    df = normalize_types_and_ranges(df)
    df = flag_unexpected_currency(df)
    df = flag_outliers(df)

    logger.info("Cleaned to %d rows; %d flagged as outliers", len(df), int(df["is_outlier"].sum()))

    if persist_outlier_flags:
        write_outlier_flags(df)

    return df


if __name__ == "__main__":
    cleaned = clean_and_validate()
    if not cleaned.empty:
        print(f"\nCleaned {len(cleaned)} rows across {cleaned['asin'].nunique()} products.")
        print(f"Outliers flagged: {int(cleaned['is_outlier'].sum())}")
        print("\nSample:")
        print(
            cleaned[["asin", "title", "scraped_at", "price", "discount_pct", "is_outlier"]]
            .head(10)
            .to_string(index=False)
        )
