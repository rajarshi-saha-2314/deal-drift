"""
DealDrift Plotly chart builders.

Color choices follow the validated reference palette (categorical slot 1 =
blue for "actual/historical", slot 2 = orange for "forecast/projected" --
fixed roles, never cycled or reassigned by filter state). Forecast lines
are additionally dashed so the actual-vs-projected distinction never rests
on color alone. Status red is reserved for flagged outliers only.
"""

import pandas as pd
import plotly.graph_objects as go

# Fixed semantic roles -- see palette reference. Never reassign these by
# filter/selection state; a categorical hue always means the same series.
COLOR_ACTUAL = "#2a78d6"      # categorical slot 1 (blue) -- observed price
COLOR_FORECAST = "#eb6834"    # categorical slot 2 (orange) -- projected price
COLOR_LIST_PRICE = "#898781"  # muted ink -- reference line, not a data series
COLOR_OUTLIER = "#d03b3b"     # status critical -- reserved for flagged points
GRIDLINE = "#e1e0d9"
AXIS_LINE = "#c3c2b7"
TEXT_MUTED = "#52514e"


def _base_layout(title: str, y_title: str) -> dict:
    return dict(
        title=title,
        xaxis=dict(title=None, showgrid=False, linecolor=AXIS_LINE, tickfont=dict(color=TEXT_MUTED)),
        yaxis=dict(title=y_title, showgrid=True, gridcolor=GRIDLINE, zeroline=False, tickfont=dict(color=TEXT_MUTED)),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(l=10, r=10, t=50, b=10),
    )


def build_price_history_chart(history_df: pd.DataFrame, currency: str = "INR") -> go.Figure:
    """Line chart of observed price over time, with list_price as a muted
    reference line and outlier points called out explicitly."""
    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=history_df["scraped_at"], y=history_df["price"],
        mode="lines+markers", name="Price",
        line=dict(color=COLOR_ACTUAL, width=2),
        marker=dict(size=8, color=COLOR_ACTUAL),
        hovertemplate=f"%{{x|%Y-%m-%d %H:%M}}<br>Price: {currency} %{{y:.2f}}<extra></extra>",
    ))

    if history_df["list_price"].notna().any():
        fig.add_trace(go.Scatter(
            x=history_df["scraped_at"], y=history_df["list_price"],
            mode="lines", name="List price",
            line=dict(color=COLOR_LIST_PRICE, width=1.5, dash="dot"),
            hovertemplate=f"List price: {currency} %{{y:.2f}}<extra></extra>",
        ))

    outliers = history_df[history_df["is_outlier"] == True]  # noqa: E712
    if not outliers.empty:
        fig.add_trace(go.Scatter(
            x=outliers["scraped_at"], y=outliers["price"],
            mode="markers", name="Flagged outlier",
            marker=dict(size=11, color=COLOR_OUTLIER, symbol="circle-open", line=dict(width=2)),
            hovertemplate=f"Outlier: {currency} %{{y:.2f}}<extra></extra>",
        ))

    fig.update_layout(**_base_layout("Price History", f"Price ({currency})"))
    return fig


def build_forecast_chart(
    history_df: pd.DataFrame, forecast_df: pd.DataFrame | None, currency: str = "INR"
) -> go.Figure | None:
    """Historical price + N-day forecast with confidence band. Returns None
    if there's no forecast to show -- caller should render a message
    instead rather than an empty/misleading chart."""
    if forecast_df is None or forecast_df.empty:
        return None

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=history_df["scraped_at"], y=history_df["price"],
        mode="lines+markers", name="Actual",
        line=dict(color=COLOR_ACTUAL, width=2),
        marker=dict(size=8, color=COLOR_ACTUAL),
        hovertemplate=f"%{{x|%Y-%m-%d}}<br>Actual: {currency} %{{y:.2f}}<extra></extra>",
    ))

    # Confidence band first (so the forecast line draws on top of it).
    fig.add_trace(go.Scatter(
        x=pd.concat([forecast_df["date"], forecast_df["date"][::-1]]),
        y=pd.concat([forecast_df["upper_ci"], forecast_df["lower_ci"][::-1]]),
        fill="toself", fillcolor="rgba(235, 104, 52, 0.15)",
        line=dict(color="rgba(0,0,0,0)"),
        name="Confidence interval", hoverinfo="skip", showlegend=True,
    ))

    fig.add_trace(go.Scatter(
        x=forecast_df["date"], y=forecast_df["forecast"],
        mode="lines+markers", name="Forecast",
        line=dict(color=COLOR_FORECAST, width=2, dash="dash"),  # dashed: identity never rests on color alone
        marker=dict(size=8, color=COLOR_FORECAST),
        hovertemplate=f"%{{x|%Y-%m-%d}}<br>Forecast: {currency} %{{y:.2f}}<extra></extra>",
    ))

    fig.update_layout(**_base_layout("Price History + Forecast", f"Price ({currency})"))
    return fig


def build_discount_bar_chart(snapshot_df: pd.DataFrame, top_n: int = 15) -> go.Figure:
    """Horizontal bar chart of current discount % across products -- single
    hue (magnitude across one dimension, not a multi-series identity)."""
    top = snapshot_df.nlargest(top_n, "discount_pct").sort_values("discount_pct")
    labels = top["title"].str.slice(0, 50)

    fig = go.Figure(go.Bar(
        x=top["discount_pct"], y=labels, orientation="h",
        marker=dict(color=COLOR_ACTUAL),
        hovertemplate="%{y}<br>Discount: %{x:.1f}%<extra></extra>",
    ))
    fig.update_layout(**_base_layout("Top Current Discounts", "Discount %"))
    fig.update_layout(yaxis=dict(showgrid=False, tickfont=dict(color=TEXT_MUTED)), height=max(300, 28 * len(top)))
    return fig


def build_seller_chart(seller_df: pd.DataFrame) -> go.Figure | None:
    """Bar chart of price-point counts per seller. Returns None if no
    seller data has been captured yet (sample detail-page enrichment is
    opt-in per scrape, so this can legitimately be empty)."""
    if seller_df.empty:
        return None

    fig = go.Figure(go.Bar(
        x=seller_df["seller_name"], y=seller_df["n_price_points"],
        marker=dict(color=COLOR_ACTUAL),
        hovertemplate="%{x}<br>Price points: %{y}<extra></extra>",
    ))
    fig.update_layout(**_base_layout("Seller Coverage (Sampled)", "Price points captured"))
    return fig
