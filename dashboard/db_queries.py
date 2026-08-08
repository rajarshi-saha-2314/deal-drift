"""
DealDrift dashboard data access.

Raw SQL via pymysql (no ORM), wrapped in st.cache_data so Streamlit doesn't
re-hit Railway MySQL on every widget interaction. Each function opens and
closes its own connection -- consistent with the rest of the project
(config/db_config.get_connection() returns a fresh connection per call).
"""

import pandas as pd
import streamlit as st

from config.db_config import get_connection

CACHE_TTL_SECONDS = 300  # 5 minutes -- fresh enough for a once-daily scrape cadence

NUMERIC_COLUMNS = ["price", "list_price", "discount_pct", "rating", "review_count"]


def _coerce_numeric(df: pd.DataFrame) -> pd.DataFrame:
    """pymysql returns DECIMAL columns as Python Decimal objects, which give
    pandas an 'object' dtype -- that breaks numeric ops like .nlargest() and
    Plotly's numeric axis handling. Coerce every present numeric column to
    float right after loading, rather than re-discovering this per caller."""
    for col in NUMERIC_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def get_products() -> pd.DataFrame:
    """All tracked products, for the sidebar selector."""
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT asin, title, category, brand FROM products WHERE is_active = TRUE ORDER BY title;"
            )
            return pd.DataFrame(cursor.fetchall())
    finally:
        conn.close()


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def get_price_history(asin: str) -> pd.DataFrame:
    """Full price_history for one ASIN, chronological."""
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT scraped_at, price, list_price, discount_pct, rating,
                       review_count, availability, is_outlier
                FROM price_history
                WHERE asin = %(asin)s
                ORDER BY scraped_at
                """,
                {"asin": asin},
            )
            df = pd.DataFrame(cursor.fetchall())
    finally:
        conn.close()
    if not df.empty:
        df["scraped_at"] = pd.to_datetime(df["scraped_at"])
        df = _coerce_numeric(df)
    return df


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def get_latest_snapshot() -> pd.DataFrame:
    """Most recent price_history row per product -- the current-state view
    used for KPIs and the top-discounts table."""
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT p.asin, p.title, p.category, ph.price, ph.list_price,
                       ph.discount_pct, ph.rating, ph.review_count, ph.scraped_at
                FROM price_history ph
                JOIN products p ON p.asin = ph.asin
                WHERE ph.price_history_id IN (
                    SELECT MAX(price_history_id) FROM price_history GROUP BY asin
                )
                ORDER BY ph.discount_pct DESC
                """
            )
            df = pd.DataFrame(cursor.fetchall())
    finally:
        conn.close()
    if not df.empty:
        df["scraped_at"] = pd.to_datetime(df["scraped_at"])
        df = _coerce_numeric(df)
    return df


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def get_seller_summary() -> pd.DataFrame:
    """Distinct sellers seen so far (populated by the sample detail-page
    enrichment in the scraper -- may be sparse)."""
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT s.seller_name, s.is_amazon, COUNT(*) AS n_price_points
                FROM price_history ph
                JOIN sellers s ON s.seller_id = ph.seller_id
                GROUP BY s.seller_name, s.is_amazon
                ORDER BY n_price_points DESC
                """
            )
            return pd.DataFrame(cursor.fetchall())
    finally:
        conn.close()
