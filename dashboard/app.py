"""
DealDrift Streamlit dashboard.

Entry point: `streamlit run dashboard/app.py`

Two views: an Overview (current-state KPIs, top discounts, seller
coverage) and a per-Product Detail view (price history + forecast). The
forecast section degrades gracefully to an explicit "not enough history
yet" message rather than a fabricated chart -- see modeling/train_model.py's
MIN_OBSERVATIONS gate. All data is pulled live from Railway MySQL via
dashboard/db_queries.py (raw SQL, cached with st.cache_data).
"""

import os
import sys
from pathlib import Path

# `streamlit run dashboard/app.py` only puts dashboard/ itself on sys.path,
# not the project root -- add it explicitly so `dashboard.*`/`modeling.*`/
# `config.*` package imports resolve regardless of the working directory
# Streamlit was launched from.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

# On Streamlit Community Cloud there is no .env file (it's gitignored, on
# purpose) -- secrets are configured instead via the app's Settings > Secrets
# panel (TOML), surfaced as st.secrets. config/db_config.py only knows how to
# read os.environ (it's shared by non-Streamlit scripts and shouldn't gain a
# Streamlit dependency), so bridge secrets into the environment here, once,
# before anything imports config.db_config. Locally, .env already populates
# os.environ via python-dotenv, so st.secrets is simply empty/absent there.
try:
    for key in ("MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE", "MYSQL_CONNECT_TIMEOUT"):
        if key in st.secrets:
            os.environ[key] = str(st.secrets[key])
except Exception:
    pass  # no secrets.toml locally -- fine, .env covers it instead

from dashboard.charts import build_discount_bar_chart, build_forecast_chart, build_price_history_chart, build_seller_chart
from dashboard.db_queries import get_latest_snapshot, get_price_history, get_products, get_seller_summary
from modeling.train_model import MIN_OBSERVATIONS, get_daily_price_series

st.set_page_config(page_title="DealDrift", page_icon="\U0001F4C9", layout="wide")


@st.cache_data(ttl=300)
def cached_forecast(asin: str, days: int = 7):
    from modeling.predict import forecast_price
    return forecast_price(asin, days=days, model_type="sarima")


def render_overview(snapshot_df, seller_df):
    st.subheader("Overview")

    if snapshot_df.empty:
        st.warning("No price data yet -- run the scraper first (data_extraction/scraper.py or database/loader.py).")
        return

    col1, col2, col3 = st.columns(3)
    col1.metric("Products tracked", len(snapshot_df))
    col2.metric("Average discount", f"{snapshot_df['discount_pct'].mean():.1f}%")
    col3.metric("Top current discount", f"{snapshot_df['discount_pct'].max():.1f}%")

    st.plotly_chart(build_discount_bar_chart(snapshot_df), use_container_width=True)

    with st.expander("All tracked products (latest snapshot)"):
        st.dataframe(
            snapshot_df[["title", "category", "price", "list_price", "discount_pct", "rating", "review_count"]],
            use_container_width=True,
            hide_index=True,
        )

    seller_chart = build_seller_chart(seller_df)
    if seller_chart is not None:
        st.plotly_chart(seller_chart, use_container_width=True)
    else:
        st.caption(
            "No seller data captured yet -- the scraper only enriches a sample of products "
            "with seller info per run (see data_extraction/scraper.py)."
        )


def render_product_detail(products_df):
    st.subheader("Product Detail")

    if products_df.empty:
        st.warning("No products tracked yet.")
        return

    options = dict(zip(products_df["title"], products_df["asin"]))
    selected_title = st.selectbox("Select a product", options.keys())
    asin = options[selected_title]

    history_df = get_price_history(asin)
    if history_df.empty:
        st.warning("No price history for this product yet.")
        return

    st.plotly_chart(build_price_history_chart(history_df), use_container_width=True)

    st.markdown("#### 7-Day Forecast")
    daily_series = get_daily_price_series(asin)
    if len(daily_series) < MIN_OBSERVATIONS:
        st.info(
            f"Not enough price history yet for a forecast -- need at least {MIN_OBSERVATIONS} daily "
            f"observations, currently have {len(daily_series)}. This will populate automatically as "
            f"data_extraction/scheduler.py accumulates more daily history."
        )
    else:
        forecast_df = cached_forecast(asin)
        forecast_chart = build_forecast_chart(history_df, forecast_df)
        if forecast_chart is not None:
            st.plotly_chart(forecast_chart, use_container_width=True)
        else:
            st.info("Forecast could not be generated for this product (model fit failed) -- see logs.")

    with st.expander("Raw price history"):
        st.dataframe(
            history_df[["scraped_at", "price", "list_price", "discount_pct", "rating", "review_count", "is_outlier"]],
            use_container_width=True,
            hide_index=True,
        )


def main():
    st.title("DealDrift")
    st.caption("Amazon price intelligence & forecasting")

    products_df = get_products()
    snapshot_df = get_latest_snapshot()
    seller_df = get_seller_summary()

    tab_overview, tab_detail = st.tabs(["Overview", "Product Detail"])
    with tab_overview:
        render_overview(snapshot_df, seller_df)
    with tab_detail:
        render_product_detail(products_df)


if __name__ == "__main__":
    main()
