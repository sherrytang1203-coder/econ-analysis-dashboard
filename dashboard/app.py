import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, date as date_type

import streamlit as st
import plotly.graph_objects as go
import pandas as pd

from src.config import (
    MACRO_INDICATORS, MACRO_GROUP_LABELS, ALL_SERIES_IDS, INVERSE_DELTA_SERIES,
    SECTOR_ETFS, INDEX_TICKERS, COMMODITY_TICKERS, RATE_TICKERS, TRACKED_STOCKS,
)
from src.store import Store
from src.fetcher import get_fred_client, fred_key_configured
from src.updater import run_update
from src.market_fetcher import fetch_market_snapshot, fetch_historical, fetch_sector_performance
from src.stock_fetcher import (fetch_stock_info, fetch_stock_price, fetch_stock_ohlcv,
                               fetch_revenue, fetch_pe_history, fetch_loss_years,
                               fetch_fcf_yield, fetch_fcf_history, fetch_rsi,
                               fetch_fcf_yield_forecast_2026, fetch_dividend_yield_history,
                               fetch_premarket_price)
from src.news_fetcher import deduplicate_by_similarity
from src.news_analyzer import get_groq_client, groq_key_configured
from src.news_pipeline import run_pipeline, needs_run
from src.earnings_tracker import get_earnings_calendar, format_earnings_for_display
from dashboard import theme

DB_PATH           = os.path.join(os.path.dirname(__file__), "..", "data", "econ_data.db")
CUSTOM_STOCKS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "custom_stocks.json")

st.set_page_config(
    page_title="Economic Dashboard",
    layout="wide",
    initial_sidebar_state="collapsed",
)

theme.register_template()
st.markdown(theme.BASE_CSS + theme.MOBILE_CSS, unsafe_allow_html=True)

# ── Store ─────────────────────────────────────────────────────────────────────

store = Store()

# ── Supabase connection diagnostic ────────────────────────────────────────────
if store.is_supabase:
    try:
        test_write = store._supa.table("series_metadata").upsert({
            "series_id": "__test__", "name": "test", "category": "test",
            "unit": "", "frequency": "", "last_updated": "", "last_fetched": "",
        }).execute()
        test_read = store._supa.table("series_metadata").select("series_id").eq("series_id", "__test__").execute()
        if test_read.data:
            store._supa.table("series_metadata").delete().eq("series_id", "__test__").execute()
        else:
            st.error("Supabase write succeeded but read returned empty — check RLS policies: run `ALTER TABLE series_metadata DISABLE ROW LEVEL SECURITY;` in SQL Editor.")
            st.stop()
    except Exception as e:
        st.error(f"Supabase connection failed: {e}. Check SUPABASE_URL and SUPABASE_KEY in Streamlit Secrets.")
        st.stop()

# ── Session state ─────────────────────────────────────────────────────────────

if "update_results" not in st.session_state:
    st.session_state.update_results = None


def _load_custom_stocks() -> dict:
    # Load from JSON file first (git-tracked for sync across deployments)
    if os.path.exists(CUSTOM_STOCKS_PATH):
        try:
            with open(CUSTOM_STOCKS_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_custom_stock(ticker: str, name: str) -> None:
    """Save custom stock to JSON file (git-tracked for sync)."""
    try:
        stocks = _load_custom_stocks()
        stocks[ticker] = name
        os.makedirs(os.path.dirname(CUSTOM_STOCKS_PATH), exist_ok=True)
        with open(CUSTOM_STOCKS_PATH, "w") as f:
            json.dump(stocks, f, indent=2)
    except Exception as e:
        st.session_state["watchlist_error"] = str(e)


def _remove_custom_stock(ticker: str) -> None:
    """Remove custom stock from JSON file (git-tracked for sync)."""
    try:
        stocks = _load_custom_stocks()
        if ticker in stocks:
            del stocks[ticker]
            os.makedirs(os.path.dirname(CUSTOM_STOCKS_PATH), exist_ok=True)
            with open(CUSTOM_STOCKS_PATH, "w") as f:
                json.dump(stocks, f, indent=2)
    except Exception as e:
        st.session_state["watchlist_error"] = str(e)

# ── Cached market data fetchers ───────────────────────────────────────────────

@st.cache_data(ttl=3600)
def _market_snapshot(tickers_tuple):
    return fetch_market_snapshot(dict(tickers_tuple))

@st.cache_data(ttl=3600)
def _sector_performance(tickers_tuple, period):
    return fetch_sector_performance(dict(tickers_tuple), period)

@st.cache_data(ttl=3600)
def _historical(ticker, period="2y"):
    return fetch_historical(ticker, period)

@st.cache_data(ttl=3600)
def _stock_info(ticker):
    info = fetch_stock_info(ticker)
    if not info.get("ok"):
        # Yahoo rate-limited / returned empty. Raise so st.cache_data does NOT
        # cache this failure for an hour; the caller handles the fallback.
        raise RuntimeError(f"info fetch failed for {ticker}")
    # Persist last-known-good fundamentals (runs only on a real cache miss).
    try:
        store.upsert_stock_fundamentals(ticker, info)
    except Exception:
        pass
    return info

def _stock_info_safe(ticker):
    """Info fetch with two layers of caching:

    1. In-memory (`_stock_info`, 1h) so we don't hit Yahoo every page load.
    2. Durable DB cache: every successful live fetch is written to the
       `stock_fundamentals` table, and if a live fetch fails (Yahoo rate-limit /
       empty), we serve the last-known-good row instead of showing N/A.
    """
    try:
        return _stock_info(ticker)
    except Exception:
        cached = None
        try:
            cached = store.get_stock_fundamentals(ticker)
        except Exception:
            pass
        if cached:
            cached["stale"] = True
            return cached
        return fetch_stock_info(ticker)

@st.cache_data(ttl=900)
def _stock_current_price(ticker):
    """Current price + daily change.

    Sourced from `fast_info` / the chart endpoint, NOT `t.info` — the
    quoteSummary endpoint behind `.info` rate-limits intermittently, whereas
    these endpoints are the reliable ones (same source the RSI/price chart use).
    """
    import yfinance as yf

    # Primary: fast_info (lightweight, reliable endpoint).
    # Use attribute access — yfinance normalizes it, while .get() keys are
    # camelCase ("lastPrice") and vary across versions.
    try:
        fi = yf.Ticker(ticker).fast_info
        price = fi.last_price
        prev = fi.previous_close
        if price is not None:
            change = (price - prev) if prev else 0
            change_pct = (change / prev * 100) if prev else 0
            return {"price": price, "change": change, "change_pct": change_pct}
    except Exception:
        pass

    # Fallback: derive from recent close history (chart endpoint)
    try:
        hist = fetch_stock_ohlcv(ticker, period="5d")
        if not hist.empty:
            closes = hist["close"].tolist()
            price = closes[-1]
            prev = closes[-2] if len(closes) >= 2 else price
            change = price - prev
            change_pct = (change / prev * 100) if prev else 0
            return {"price": price, "change": change, "change_pct": change_pct}
    except Exception:
        pass

    return {"price": None, "change": 0, "change_pct": 0}

@st.cache_data(ttl=300)
def _stock_premarket_price(ticker):
    """Get pre-market stock price and change (5-minute cache for frequent updates)."""
    return fetch_premarket_price(ticker)

@st.cache_data(ttl=3600)
def _stock_price(ticker, period="5y"):
    return fetch_stock_price(ticker, period)

@st.cache_data(ttl=3600)
def _stock_revenue(ticker):
    return fetch_revenue(ticker)

@st.cache_data(ttl=3600)
def _stock_pe(ticker):
    return fetch_pe_history(ticker)

@st.cache_data(ttl=3600)
def _stock_pe_all(ticker):
    return fetch_loss_years(ticker)

@st.cache_data(ttl=3600)
def _stock_ohlcv(ticker, period="5y"):
    return fetch_stock_ohlcv(ticker, period)

@st.cache_data(ttl=3600)
def _stock_fcf_yield(ticker):
    return fetch_fcf_yield(ticker)

@st.cache_data(ttl=3600)
def _stock_fcf_history(ticker):
    return fetch_fcf_history(ticker)

@st.cache_data(ttl=3600)
def _stock_dividend_yield_history(ticker):
    return fetch_dividend_yield_history(ticker)

@st.cache_data(ttl=3600)
def _stock_rsi(ticker, weekly=False):
    return fetch_rsi(ticker, weekly=weekly)

@st.cache_data(ttl=3600)
def _stock_fcf_forecast_2026(ticker):
    return fetch_fcf_yield_forecast_2026(ticker)

@st.cache_data(ttl=3600)
def _earnings_calendar(tickers: tuple) -> dict:
    return get_earnings_calendar(list(tickers))

# ── Header ────────────────────────────────────────────────────────────────────

col_title, col_btn = st.columns([5, 1])
with col_title:
    st.title("Economic Analytics Dashboard")
with col_btn:
    st.write("")
    check_clicked = st.button("Sync FRED Data", type="primary", use_container_width=True)

if check_clicked:
    fred = get_fred_client()
    with st.spinner("Syncing macro data from FRED..."):
        results = run_update(store, fred)
        st.session_state.update_results = results
    st.rerun()

if st.session_state.update_results:
    updated  = [r for r in st.session_state.update_results if r.get("synced")]
    fallback = [r for r in st.session_state.update_results if r.get("fallback")]
    errors   = [r for r in st.session_state.update_results if "error" in r]
    if fallback:
        st.warning(f"FRED unavailable — {len(fallback)} series fetched from BLS/Yahoo Finance.")
    if updated:
        st.success(f"Synced {len(updated)} series.")
    if errors:
        st.warning(f"Errors on {len(errors)} series: " +
                   ", ".join(r["series_id"] for r in errors))

# ── Auto initial load ─────────────────────────────────────────────────────────

meta_df = store.get_all_metadata()
if meta_df.empty:
    st.session_state.update_results = None  # clear stale results from previous session

    # Detect infinite loop: if we already ran a load this session, stop
    if st.session_state.get("initial_load_attempted"):
        st.error(
            "Data load completed but the database still appears empty. "
            "This usually means the **Supabase key is incorrect**. "
            "Check that `SUPABASE_KEY` in Streamlit Secrets starts with `eyJ` (JWT format), "
            "not `sb_publishable_...`. Then reboot the app."
        )
        st.stop()

    fred = get_fred_client()
    if fred is None:
        st.warning(
            "No FRED API key found — add `FRED_API_KEY` to your Streamlit secrets, "
            "then click **Sync FRED Data** to load all indicators. "
            "Free key at https://fred.stlouisfed.org/docs/api/api_key.html"
        )
        st.stop()
    st.session_state.initial_load_attempted = True
    st.info("First run — fetching all macro series from FRED and saving to database. "
            "This takes ~1 min locally or ~3-5 min on Streamlit Cloud. Please wait and do not refresh.")
    progress_bar = st.progress(0, text="Loading...")
    results = []
    for i, sid in enumerate(ALL_SERIES_IDS):
        progress_bar.progress((i + 1) / len(ALL_SERIES_IDS), text=f"Loading {sid}...")
        partial = run_update(store, fred, series_ids=[sid])
        results.extend(partial)
    progress_bar.empty()
    st.session_state.update_results = results
    meta_df = store.get_all_metadata()
    st.rerun()

# ── Shared helpers ────────────────────────────────────────────────────────────

def _format_value(val: float, unit: str) -> str:
    if pd.isna(val):
        return "N/A"
    if "Percent" in unit:
        return f"{val:.2f}%"
    if "Billions" in unit:
        return f"{val:,.1f}B"
    if "Thousands" in unit or "Millions" in unit:
        return f"{val:,.0f}"
    if "Hours" in unit or "Weeks" in unit:
        return f"{val:.1f}"
    return f"{val:,.2f}"


def _delta_str(latest: float, prev: float) -> str | None:
    if prev is None or pd.isna(prev) or prev == 0:
        return None
    pct = (latest - prev) / abs(prev) * 100
    return f"{pct:+.2f}%"


def _apply_range_buttons(fig: go.Figure, df: pd.DataFrame,
                         x_col: str = "date", y_col: str = "value",
                         fixed_y: list | None = None,
                         timeframes: list[tuple] | None = None,
                         perf_colors: bool = False) -> go.Figure:
    """Apple Stocks-style segmented timeframe control below the chart.

    Buttons rescale both axes client-side (no rerun). With perf_colors=True
    the line and its gradient recolor green/red to match the selected
    period's performance — requires a single scatter trace.
    """
    if df.empty:
        return fig
    today = pd.Timestamp.today().normalize()
    if timeframes is None:
        timeframes = [
            ("1Y",  today - pd.DateOffset(years=1)),
            ("5Y",  today - pd.DateOffset(years=5)),
            ("10Y", today - pd.DateOffset(years=10)),
            ("All", None),
        ]
    cutoffs = timeframes

    def _sub(start):
        sub = df if start is None else df[df[x_col] >= start]
        return sub[y_col].dropna()

    def _y(start):
        if fixed_y:
            return fixed_y
        sub = _sub(start)
        if sub.empty:
            return [0, 1]
        lo, hi = float(sub.min()), float(sub.max())
        pad = max((hi - lo) * 0.08, abs(hi) * 0.01, 1e-6)
        return [lo - pad, hi + pad]

    def _perf(start):
        sub = _sub(start)
        if len(sub) < 2:
            return theme.PALETTE["primary"]
        return (theme.PALETTE["up"] if float(sub.iloc[-1]) >= float(sub.iloc[0])
                else theme.PALETTE["down"])

    buttons, default_x0 = [], None
    for i, (label, start) in enumerate(cutoffs):
        x0 = (start.isoformat() if start is not None
               else (df[x_col].min().isoformat() if not df.empty else "2000-01-01"))
        if i == 0:
            default_x0 = x0
        layout_args = {"xaxis.range": [x0, today.isoformat()],
                       "yaxis.range": _y(start)}
        if perf_colors:
            c = _perf(start)
            buttons.append(dict(
                label=label, method="update",
                args=[{"line.color": [c], "fillgradient": [theme.gradient(c)]},
                      layout_args],
            ))
        else:
            buttons.append(dict(label=label, method="relayout", args=[layout_args]))

    range_menu = dict(
        type="buttons", direction="right", showactive=True, active=0,
        x=0.5, xanchor="center", y=-0.16, yanchor="top",
        pad={"l": 4, "r": 4, "t": 8, "b": 8},
        buttons=buttons,
        bgcolor="rgba(243,244,246,0.9)",
        bordercolor="rgba(0,0,0,0)", borderwidth=0,
        font=dict(size=12, color=theme.PALETTE["muted"]),
    )
    existing = list(fig.layout.updatemenus or [])
    existing.append(range_menu)
    fig.update_layout(
        updatemenus=existing,
        # the segmented control sits in the bottom margin; grow both so the
        # plot area keeps its original height
        margin_b=84,
        height=(fig.layout.height or theme.CHART_HEIGHT["md"]) + 48,
        xaxis=dict(range=[default_x0, today.isoformat()]),
        yaxis=dict(range=_y(cutoffs[0][1])),
    )
    if perf_colors and fig.data:
        c0 = _perf(cutoffs[0][1])
        fig.update_traces(line_color=c0, fillgradient=theme.gradient(c0),
                          selector=dict(type="scatter"))
    return fig


def _show_chart(fig: go.Figure, **kwargs):
    """Render a figure with the econ_light template and the shared config.

    theme=None is required — Streamlit's default theme="streamlit" would
    override the Plotly template.
    """
    return st.plotly_chart(fig, width="stretch", theme=None,
                           config=theme.PLOTLY_CONFIG, **kwargs)


def _rsi_chart(daily_df: pd.DataFrame, weekly_df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if not daily_df.empty:
        fig.add_trace(go.Scatter(
            x=daily_df["date"], y=daily_df["rsi"],
            mode="lines", name="Daily",
            line=dict(width=1.5, color=theme.PALETTE["violet"]),
            hovertemplate="%{x|%b %d, %Y}: %{y:.1f}<extra>Daily</extra>",
        ))
    if not weekly_df.empty:
        fig.add_trace(go.Scatter(
            x=weekly_df["date"], y=weekly_df["rsi"],
            mode="lines", name="Weekly",
            line=dict(width=2.5, color=theme.PALETTE["amber"]),
            hovertemplate="%{x|%b %d, %Y}: %{y:.1f}<extra>Weekly</extra>",
        ))
    fig.add_hrect(y0=70, y1=100, fillcolor=theme.rgba(theme.PALETTE["down"], 0.04), line_width=0)
    fig.add_hrect(y0=0,  y1=30,  fillcolor=theme.rgba(theme.PALETTE["up"], 0.04), line_width=0)
    fig.add_hline(y=70, line_dash="dot", line_color=theme.PALETTE["down"], line_width=1,
                  annotation_text="Overbought 70",
                  annotation_font=dict(size=10, color=theme.PALETTE["down"]),
                  annotation_position="top right")
    fig.add_hline(y=30, line_dash="dot", line_color=theme.PALETTE["up"], line_width=1,
                  annotation_text="Oversold 30",
                  annotation_font=dict(size=10, color=theme.PALETTE["up"]),
                  annotation_position="bottom right")
    fig.update_layout(**theme.pro_layout("RSI (14)", "",
                                         height=theme.CHART_HEIGHT["sm"], edge=True))
    fig.update_layout(yaxis=dict(range=[0, 100]), showlegend=True)
    ref_df = daily_df if not daily_df.empty else weekly_df
    _apply_range_buttons(fig, ref_df, x_col="date", y_col="rsi", fixed_y=[0, 100])
    return fig


def _line_chart(df: pd.DataFrame, title: str, yaxis: str,
                x_col="date", y_col="value", color=None,
                zero_line=False, fill_area=True, perf_colors=False) -> go.Figure:
    color = color or theme.PALETTE["primary"]
    fig = go.Figure()
    if not df.empty:
        fig.add_trace(go.Scatter(
            x=df[x_col], y=df[y_col],
            mode="lines", line=dict(width=2, color=color),
            fill="tozeroy" if fill_area else None,
            fillgradient=theme.gradient(color) if fill_area else None,
            hovertemplate="%{x|%Y-%m-%d}: %{y:,.4g}<extra></extra>",
        ))
    if zero_line:
        fig.add_hline(y=0, line_dash="dash", line_color=theme.PALETTE["faint"], line_width=1)
    fig.update_layout(**theme.pro_layout(title, yaxis, height=theme.CHART_HEIGHT["lg"],
                                         edge=True))
    if df.empty:
        fig.add_annotation(text="No data — sync FRED data to populate",
                           xref="paper", yref="paper", x=0.5, y=0.5,
                           xanchor="center", yanchor="middle", showarrow=False,
                           font=dict(size=12, color=theme.PALETTE["faint"]))
        fig.update_layout(xaxis=dict(visible=False), yaxis=dict(visible=False))
    _apply_range_buttons(fig, df, x_col, y_col, perf_colors=perf_colors)
    return fig


# ── Section 1: Capital Markets ────────────────────────────────────────────────

def render_capital_markets():
    # ── Major indices ─────────────────────────────────────────────────────────
    st.subheader("Major Indices")
    st.caption("📊 [Source: Yahoo Finance](https://finance.yahoo.com) • Real-time (during market hours)")
    idx_df = _market_snapshot(tuple(INDEX_TICKERS.items()))
    if not idx_df.empty:
        cols = st.columns(len(idx_df))
        for col, row in zip(cols, idx_df.itertuples()):
            inv = row.ticker in INVERSE_DELTA_SERIES
            col.metric(
                row.name,
                f"{row.current:,.2f}",
                f"{row.pct_change:+.2f}%",
                delta_color="inverse" if inv else "normal",
                help=f"Ticker: {row.ticker}",
            )
    else:
        st.warning("Could not load index data.")

    st.divider()

    # ── Sector heatmap ────────────────────────────────────────────────────────
    # Plotly's .nsewdrag overlay sits on top of all bars and owns the cursor.
    # dragmode=False (set on the figure below) stops Plotly forcing a move cursor;
    # this JS then sets pointer on that overlay element.
    import streamlit.components.v1 as components
    components.html("""
    <script>
    (function() {
        function applyPointer() {
            var doc = window.parent.document;
            // .nsewdrag is the transparent rect Plotly places over the whole plot area
            doc.querySelectorAll('.js-plotly-plot .nsewdrag').forEach(function(el) {
                el.style.cursor = 'pointer';
            });
        }
        applyPointer();
        setTimeout(applyPointer, 400);
        setTimeout(applyPointer, 1000);
        new MutationObserver(applyPointer).observe(
            window.parent.document.body, { childList: true, subtree: true }
        );
    })();
    </script>
    """, height=0)
    period_options = ["1D", "1M", "6M", "1Y", "2Y"]
    period_labels  = {"1D": "1 Day", "1M": "1 Month", "6M": "6 Months",
                      "1Y": "1 Year", "2Y": "2 Years"}

    hdr_col, sel_col = st.columns([3, 2])
    with hdr_col:
        st.subheader("S&P 500 Sector Performance")
    with sel_col:
        selected_period = st.radio(
            "Period",
            options=period_options,
            format_func=lambda x: period_labels[x],
            horizontal=True,
            index=0,
            label_visibility="collapsed",
        )

    sec_df = _sector_performance(tuple(SECTOR_ETFS.items()), selected_period)
    if not sec_df.empty:
        colors = [theme.PALETTE["up"] if x >= 0 else theme.PALETTE["down"]
                  for x in sec_df["pct_change"]]
        fig = go.Figure(go.Bar(
            x=sec_df["pct_change"],
            y=sec_df["name"],
            orientation="h",
            marker_color=colors,
            text=[f"{x:+.2f}%" for x in sec_df["pct_change"]],
            textposition="outside",
            hovertemplate="%{y}: %{x:+.2f}%<extra></extra>",
        ))
        fig.update_layout(
            height=theme.CHART_HEIGHT["heatmap"],
            margin=dict(l=140, r=80, t=20, b=40),
            xaxis=dict(title=f"% Change ({period_labels[selected_period]})",
                       zeroline=True, zerolinecolor=theme.PALETTE["border"]),
            yaxis=dict(autorange="reversed"),
            hovermode="y",
        )
        # on_select captures bar clicks; key resets selection when period changes
        event = st.plotly_chart(
            fig,
            width="stretch",
            theme=None,
            on_select="rerun",
            key=f"sector_chart_{selected_period}",
            config=theme.PLOTLY_CONFIG,
        )

        # ── 5-year drill-down ─────────────────────────────────────────────────
        name_to_ticker = {v: k for k, v in SECTOR_ETFS.items()}
        clicked_ticker = None
        if (event and hasattr(event, "selection")
                and event.selection and event.selection.points):
            clicked_name = event.selection.points[0].get("y", "")
            clicked_ticker = name_to_ticker.get(clicked_name)

        if clicked_ticker:
            sector_name = SECTOR_ETFS[clicked_ticker]
            st.markdown(f"#### {sector_name} — 5 Year Price Trend")
            hist_df = _historical(clicked_ticker, period="5y")
            if not hist_df.empty:
                fig5 = _line_chart(
                    hist_df, f"{sector_name} ({clicked_ticker})", "Price (USD)",
                    x_col="date", y_col="close", perf_colors=True,
                )
                _show_chart(fig5)
            else:
                st.warning(f"Could not load 5-year data for {sector_name}.")
        else:
            st.caption("Click any sector bar to see its 5-year price trend.")

    else:
        st.warning("Could not load sector data.")

    st.divider()

    # ── Commodities & Rates ───────────────────────────────────────────────────
    comm_df = _market_snapshot(tuple(COMMODITY_TICKERS.items()))
    rate_df = _market_snapshot(tuple(RATE_TICKERS.items()))

    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("Commodities")
        st.caption("📊 [Source: Yahoo Finance](https://finance.yahoo.com) • Real-time (during market hours)")
        if not comm_df.empty:
            c_cols = st.columns(len(comm_df))
            for col, row in zip(c_cols, comm_df.itertuples()):
                col.metric(
                    row.name,
                    f"{row.current:,.2f}",
                    f"{row.pct_change:+.2f}%",
                    help=f"Ticker: {row.ticker}",
                )
    with col_b:
        st.subheader("Rates & Volatility")
        st.caption("📊 [Source: Yahoo Finance](https://finance.yahoo.com) • Real-time (during market hours)")
        if not rate_df.empty:
            r_cols = st.columns(len(rate_df))
            for col, row in zip(r_cols, rate_df.itertuples()):
                inv = row.ticker in INVERSE_DELTA_SERIES
                col.metric(
                    row.name,
                    f"{row.current:.2f}%",
                    f"{row.pct_change:+.2f}%",
                    delta_color="inverse" if inv else "normal",
                    help=f"Ticker: {row.ticker}",
                )



# ── Section 2: Macro Economy ──────────────────────────────────────────────────

def render_market_leading_charts():
    """S&P 500, VIX, yield curve, and spread — shown in the Leading Indicators tab."""
    st.subheader("Market-Based Leading Indicators")
    c1, c2 = st.columns(2)
    with c1:
        sp_df = _historical("^GSPC")
        _show_chart(_line_chart(sp_df, "S&P 500", "Price",
                                x_col="date", y_col="close", perf_colors=True))
    with c2:
        vix_df = _historical("^VIX")
        _show_chart(_line_chart(vix_df, "VIX (Volatility)", "Index",
                                x_col="date", y_col="close",
                                color=theme.PALETTE["amber"]))

    c3, c4 = st.columns(2)
    df10 = store.get_observations("DGS10")
    df2  = store.get_observations("DGS2")
    with c3:
        fig_yc = go.Figure()
        if not df10.empty:
            fig_yc.add_trace(go.Scatter(
                x=df10["date"], y=df10["value"],
                mode="lines", name="10Y",
                line=dict(color=theme.PALETTE["primary"], width=1.8),
                hovertemplate="%{x|%Y-%m-%d}: %{y:.2f}%<extra>10Y</extra>",
            ))
        if not df2.empty:
            fig_yc.add_trace(go.Scatter(
                x=df2["date"], y=df2["value"],
                mode="lines", name="2Y",
                line=dict(color=theme.PALETTE["amber"], width=1.8),
                hovertemplate="%{x|%Y-%m-%d}: %{y:.2f}%<extra>2Y</extra>",
            ))
        fig_yc.update_layout(**theme.pro_layout("Treasury Yields",
                                                "Yield (%)",
                                                height=theme.CHART_HEIGHT["lg"],
                                                edge=True))
        fig_yc.update_layout(showlegend=True)
        combined_yc = pd.concat([df10, df2]).dropna(subset=["value"])
        _apply_range_buttons(fig_yc, combined_yc, "date", "value")
        _show_chart(fig_yc)

    with c4:
        if not df10.empty and not df2.empty:
            d10 = df10.set_index("date")["value"]
            d2  = df2.set_index("date")["value"]
            spread = (d10 - d2).dropna().reset_index()
            spread.columns = ["date", "value"]
            fig_sp = go.Figure(go.Bar(
                x=spread["date"], y=spread["value"],
                marker_color=[theme.PALETTE["up"] if v >= 0 else theme.PALETTE["down"]
                              for v in spread["value"]],
                hovertemplate="%{x|%Y-%m-%d}: %{y:.2f}%<extra></extra>",
            ))
            fig_sp.add_hline(y=0, line_dash="dash", line_color=theme.PALETTE["faint"], line_width=1)
            fig_sp.update_layout(**theme.pro_layout("Yield Spread (10Y − 2Y)",
                                                    "Spread (%)",
                                                    height=theme.CHART_HEIGHT["lg"]))
            _apply_range_buttons(fig_sp, spread, "date", "value")
            _show_chart(fig_sp)
        else:
            st.info("Sync FRED data to see the yield spread chart.")

def render_indicator_group(group_key: str, series_dict: dict) -> None:
    series_ids = list(series_dict.keys())

    # Get latest fetch date for header
    latest_fetch_date = "Unknown"
    if not meta_df.empty:
        fetch_dates = meta_df["last_fetched"].dropna()
        if not fetch_dates.empty:
            try:
                latest_fetch = pd.to_datetime(fetch_dates).max()
                latest_fetch_date = latest_fetch.strftime('%Y-%m-%d %H:%M')
            except:
                pass

    # Add source header for FRED data with fetch date
    st.caption(f"📊 [Source: FRED](https://fred.stlouisfed.org) • Last fetched: {latest_fetch_date}")

    # Metric cards
    cols = st.columns(min(len(series_ids), 4))
    for i, sid in enumerate(series_ids):
        col = cols[i % 4]
        info = series_dict[sid]
        df = store.get_observations(sid)

        if df.empty or df["value"].dropna().empty:
            col.metric(info["name"], "No data", help="Sync FRED data to populate.")
            continue

        clean  = df["value"].dropna()
        latest = clean.iloc[-1]
        prev   = clean.iloc[-2] if len(clean) >= 2 else None
        delta  = _delta_str(latest, prev)
        row    = meta_df[meta_df["series_id"] == sid]
        last_upd = row["last_updated"].iloc[0] if not row.empty else None
        last_fetched = row["last_fetched"].iloc[0] if not row.empty else None
        inv    = sid in INVERSE_DELTA_SERIES

        # Format date for label (yyyy-mm-dd) - try metadata first, then data date
        date_label = ""
        if last_upd:
            try:
                # Handle "fallback:YYYY-MM-DD" format
                date_str = last_upd.replace("fallback:", "") if isinstance(last_upd, str) else last_upd
                date_obj = pd.to_datetime(date_str)
                date_label = f" ({date_obj.strftime('%Y-%m-%d')})"
            except Exception as e:
                # If metadata date fails, try data date
                if not df.empty:
                    try:
                        latest_date = pd.to_datetime(df["date"].iloc[-1])
                        date_label = f" ({latest_date.strftime('%Y-%m-%d')})"
                    except:
                        pass
        elif not df.empty:
            # Fallback: use latest data date if metadata unavailable
            try:
                latest_date = pd.to_datetime(df["date"].iloc[-1])
                date_label = f" ({latest_date.strftime('%Y-%m-%d')})"
            except:
                pass

        # Build help text
        help_text = (
            f"**Unit:** {info['unit']}\n"
            f"**Frequency:** {info['frequency']}"
        )

        col.metric(
            label=f"{info['name']}{date_label}",
            value=_format_value(latest, info["unit"]),
            delta=delta,
            delta_color="inverse" if inv else "normal",
            help=help_text,
        )

    # Yield spread bonus card in Leading tab
    if group_key == "leading":
        df10 = store.get_observations("DGS10")
        df2  = store.get_observations("DGS2")
        if not df10.empty and not df2.empty:
            d10 = df10.set_index("date")["value"]
            d2  = df2.set_index("date")["value"]
            spread = (d10 - d2).dropna()
            if not spread.empty:
                latest_spread = spread.iloc[-1]
                prev_spread   = spread.iloc[-2] if len(spread) >= 2 else None
                delta_spread  = _delta_str(latest_spread, prev_spread)
                extra_col = cols[len(series_ids) % 4] if len(series_ids) < 4 * len(cols) else st.columns(1)[0]

                # Get last updated info for yield spreads
                row_10 = meta_df[meta_df["series_id"] == "DGS10"]
                last_upd_spread = row_10["last_updated"].iloc[0] if not row_10.empty else None

                # Format date for label (yyyy-mm-dd) - try metadata first, then data date
                date_label = ""
                if last_upd_spread:
                    try:
                        # Handle "fallback:YYYY-MM-DD" format
                        date_str = last_upd_spread.replace("fallback:", "") if isinstance(last_upd_spread, str) else last_upd_spread
                        date_obj = pd.to_datetime(date_str)
                        date_label = f" ({date_obj.strftime('%Y-%m-%d')})"
                    except Exception as e:
                        # If metadata date fails, try data date
                        try:
                            latest_date = pd.to_datetime(df10["date"].iloc[-1])
                            date_label = f" ({latest_date.strftime('%Y-%m-%d')})"
                        except:
                            pass
                else:
                    # Fallback: use latest data date if metadata unavailable
                    try:
                        latest_date = pd.to_datetime(df10["date"].iloc[-1])
                        date_label = f" ({latest_date.strftime('%Y-%m-%d')})"
                    except:
                        pass

                help_text = (
                    f"10-Year minus 2-Year Treasury yield spread\n"
                    f"Negative = inverted curve (recession indicator)"
                )
                extra_col.metric(
                    f"Yield Spread (10Y−2Y){date_label}",
                    f"{latest_spread:.2f}%",
                    delta_spread,
                    delta_color="normal",
                    help=help_text,
                )

    st.divider()

    # Charts (2 per row)
    pairs = [series_ids[i:i + 2] for i in range(0, len(series_ids), 2)]
    for pair in pairs:
        chart_cols = st.columns(2)
        for col, sid in zip(chart_cols, pair):
            info = series_dict[sid]
            df = store.get_observations(sid)
            with col:
                if df.empty:
                    st.info(f"**{info['name']}** — no data yet. Sync FRED data to populate.")
                else:
                    _show_chart(_line_chart(df, info["name"], info["unit"]))


def render_macro():
    lead_tab, coin_tab, lag_tab = st.tabs([
        "Leading Indicators",
        "Coincident Indicators",
        "Lagging Indicators",
    ])
    for tab, key in zip([lead_tab, coin_tab, lag_tab],
                        ["leading", "coincident", "lagging"]):
        with tab:
            render_indicator_group(key, MACRO_INDICATORS[key])
            if key == "leading":
                st.divider()
                render_market_leading_charts()


# ── Section 3: News Feed ──────────────────────────────────────────────────────

_DIRECTION_ICON = {"positive": "🟢", "negative": "🔴", "mixed": "🟡", "neutral": "⚪"}


def render_news():
    today = date_type.today().isoformat()
    top10 = deduplicate_by_similarity(store.get_top10(today), threshold=0.75)
    last_run = store.last_pipeline_date()
    already_ran_today = last_run == today

    col_hdr, col_btn = st.columns([4, 1])
    with col_hdr:
        st.subheader("Top 10 Economic News")
        st.caption(
            "Ranked by Llama 3.3 70B — relevance × impact magnitude.  "
            "🟢 positive  🔴 negative  🟡 mixed  ⚪ neutral"
        )
    with col_btn:
        st.write("")
        btn_label = "Re-run Analysis" if already_ran_today else "Run Today's Analysis"
        run_clicked = st.button(btn_label, type="primary", use_container_width=True)

    if run_clicked:
        if not groq_key_configured():
            st.error("GROQ_API_KEY not set in `.env`. Free key at https://console.groq.com")
        else:
            try:
                groq = get_groq_client()
                with st.spinner("Analyzing today's news with Llama 3.3 70B..."):
                    result = run_pipeline(store, groq)
                if "error" in result:
                    st.session_state["news_status"] = ("error", result["error"])
                else:
                    st.session_state["news_status"] = (
                        "success",
                        f"Done — fetched {result['fetched']} articles, "
                        f"analyzed {result['analyzed']} new, ranked top 10.",
                    )
                st.rerun()
            except Exception as e:
                st.error(f"Analysis failed: {type(e).__name__}: {e}")

    if "news_status" in st.session_state:
        level, msg = st.session_state.pop("news_status")
        (st.error if level == "error" else st.success)(msg)

    if not already_ran_today:
        st.info(
            "Today's analysis hasn't run yet. Click **Run Today's Analysis** above."
            + ("\n\nAdd `GROQ_API_KEY` to `.env` first — free at https://console.groq.com"
               if not groq_key_configured() else "")
        )
        return

    if not top10:
        st.warning("Pipeline ran but no articles were ranked. Try re-running.")
        return

    for article in top10:
        direction = article.get("impact_direction") or "neutral"
        icon = _DIRECTION_ICON.get(direction, "⚪")
        pub  = (article.get("published") or "")[:10]
        rel  = article.get("econ_relevance") or 0
        mag  = article.get("impact_magnitude") or 0

        st.markdown(f"**#{article['rank']} {icon} [{article['title']}]({article['url']})**")
        st.caption(
            f"{article['source']}  ·  {pub}  ·  "
            f"Relevance {rel:.0%}  ·  Magnitude {mag:.0%}  ·  {direction.capitalize()}"
        )
        if article.get("ai_summary"):
            st.markdown(article["ai_summary"])
        st.divider()


# ── Section 4: Stock Tracing ──────────────────────────────────────────────────

_CARD_CSS = """
<style>
.stock-header { margin-bottom: 12px; }
.stock-name   { font-size: 26px; font-weight: 700; margin: 0 0 6px 0; }
.badge        { display: inline-block; padding: 3px 10px; border-radius: 20px;
                font-size: 11px; font-weight: 600; margin-right: 6px; }
.badge-sector   { background: rgba(37,99,235,0.10); color: #2563eb; }
.badge-industry { background: rgba(124,58,237,0.10); color: #7c3aed; }
.metric-grid  { display: flex; gap: 10px; margin: 14px 0; }
.mgroup       { flex: 1; border-radius: 10px; padding: 13px 15px;
                border-top: 3px solid var(--gc); background: rgba(128,128,128,0.06); }
.mgroup-title { font-size: 10px; font-weight: 700; text-transform: uppercase;
                letter-spacing: 1px; color: var(--gc); margin-bottom: 10px; }
.mpair        { display: flex; gap: 6px; }
.mitem        { flex: 1; min-width: 0; }
.mlabel       { font-size: 11px; color: #888; margin-bottom: 3px; white-space: nowrap; }
.mval         { font-size: 19px; font-weight: 600; line-height: 1.15; }
.mval-sm      { font-size: 14px; font-weight: 600; line-height: 1.3; }
.pos { color: #059669; } .neg { color: #dc2626; }
.ob  { color: #dc2626; } .os  { color: #059669; }
.price-item { display: inline; }
.price-separator { display: inline; }

@media (max-width: 1024px) {
  .metric-grid { gap: 8px; margin: 0; }
  .mgroup { padding: 8px 6px; border-radius: 6px; }
  .mgroup-title { font-size: 9px; margin-bottom: 6px; }
  .mpair { gap: 4px; }
  .mlabel { font-size: 10px; margin-bottom: 2px; }
  .mval { font-size: 16px; }
  .price-item { display: block; margin: 4px 0; }
  .price-separator { display: none; }
}
</style>
"""

def _get_forecast_html_block(fcf_forecast: dict) -> str:
    """Generate forecast metrics HTML block (to be embedded in parent f-string)."""
    if not fcf_forecast:
        return ""

    try:
        pdf_url = fcf_forecast.get("pdf_url", "")
        pdf_name = ""

        if pdf_url:
            # Extract filename from URL
            parts = pdf_url.rstrip("/").split("/")
            pdf_name = parts[-1] if parts[-1] else parts[-2] if len(parts) > 1 else "Source"
            # If just a domain, use domain name
            if "." in pdf_name and len(pdf_name) < 10 and not pdf_name.endswith(".pdf"):
                pdf_name = parts[-2] if len(parts) > 2 else "Source"

        fcf_yield = fcf_forecast.get("fcf_yield_2026", 0)
        fcf_2026 = fcf_forecast.get("fcf_2026", 0)
        eps_2026 = fcf_forecast.get("eps_2026", None)
        confidence = fcf_forecast.get("confidence", "Unknown")
        eps_growth = fcf_forecast.get("eps_growth_rate", 0)

        # Source attribution - clickable link with tooltip
        source_link = ""
        if pdf_url:
            source_label = pdf_name if pdf_name and pdf_name != "Source" else "Company Source"
            source_link = f'<a href="{pdf_url}" target="_blank" title="{source_label}" style="font-size:0.65em; opacity:0.7; color:#0066cc; text-decoration:underline; display:block; margin-top:3px; cursor:help;">📋 Source</a>'

        # FCF source - show if official guidance (fcf_2026 > 0 indicates official)
        fcf_source = ""
        if fcf_2026 > 0 and confidence == "Official" and source_link:
            fcf_source = source_link

        # EPS source - show if EPS exists
        eps_source = ""
        if eps_2026 and source_link:
            eps_source = source_link

        # Build forecast metrics with better layout
        forecast_html = '<div style="margin-top:8px; padding-top:8px; border-top:1px solid rgba(255,255,255,0.1);">'

        # Row 1: FCF and EPS forecasts
        forecast_html += '<div class="mpair">'
        if fcf_2026 > 0:
            forecast_html += f'<div class="mitem"><div class="mlabel">FCF 2026F</div><div class="mval">${fcf_2026:.2f}B</div>{fcf_source}</div>'
        if eps_2026:
            forecast_html += f'<div class="mitem"><div class="mlabel">EPS 2026F</div><div class="mval">${eps_2026:.2f}</div>{eps_source}</div>'
        forecast_html += f'<div class="mitem"><div class="mlabel">FCF Yield 2026F</div><div class="mval">{fcf_yield:.1f}%</div></div>'
        forecast_html += '</div>'

        # Row 2: Confidence and Growth
        forecast_html += f'<div class="mpair" style="margin-top:6px;">'
        forecast_html += f'<div class="mitem"><div class="mlabel">Confidence</div><div class="mval" style="font-size:15px;">{confidence}</div></div>'
        forecast_html += f'<div class="mitem"><div class="mlabel">EPS Growth</div><div class="mval" style="font-size:15px;">{eps_growth:.1f}%</div></div>'
        forecast_html += '</div>'
        forecast_html += '</div>'

        return forecast_html
    except Exception as e:
        return f"<div style='color:red;'>Error rendering forecast: {str(e)}</div>"


def _stock_metric_cards(info: dict, fcf_yield, curr_rsi_d, curr_rsi_w, fcf_forecast=None, ticker: str = "", price_data: dict = None, premarket_data: dict = None) -> None:
    pe  = info.get("pe_ratio")
    fpe = info.get("forward_pe")
    eps = info.get("eps")
    mc  = info.get("market_cap")
    hi  = info.get("52w_high")
    lo  = info.get("52w_low")
    div_yield = info.get("dividend_yield")
    div_annual = info.get("annual_dividend")

    def v(val, fmt, pre="", suf=""):
        return f"{pre}{val:{fmt}}{suf}" if val is not None else "N/A"

    fcf_cls  = "pos" if fcf_yield and fcf_yield > 0 else ("neg" if fcf_yield else "")
    rsi_d_cls = ("ob" if curr_rsi_d and curr_rsi_d >= 70
                 else "os" if curr_rsi_d and curr_rsi_d <= 30 else "")
    rsi_w_cls = ("ob" if curr_rsi_w and curr_rsi_w >= 70
                 else "os" if curr_rsi_w and curr_rsi_w <= 30 else "")
    rsi_d_tag = " ↑OB" if rsi_d_cls == "ob" else (" ↓OS" if rsi_d_cls == "os" else "")
    rsi_w_tag = " ↑OB" if rsi_w_cls == "ob" else (" ↓OS" if rsi_w_cls == "os" else "")

    name    = info.get("name", "")
    sector  = info.get("sector", "")
    industry = info.get("industry", "")

    # Add ticker and price to name if provided
    ticker_suffix = f" ({ticker})" if ticker else ""
    price_suffix = ""

    # Display both pre-market and closing prices when available
    has_valid_premarket = premarket_data and premarket_data.get("price") is not None
    has_closing_price = price_data and price_data.get("price") is not None

    if has_valid_premarket or has_closing_price:
        price_parts = []

        # Add pre-market price if available
        if has_valid_premarket:
            pm_price = premarket_data["price"]
            pm_change = premarket_data.get("change", 0)
            pm_change_pct = premarket_data.get("change_pct", 0)
            pm_color = theme.PALETTE["up"] if pm_change >= 0 else theme.PALETTE["down"]
            pm_arrow = "▲" if pm_change >= 0 else "▼"
            price_parts.append(f'<div class="price-item"><span style="font-size:14px; color:#9ca3af;">Pre-Market:</span> <span style="font-size:18px; color:{pm_color};">${pm_price:.2f} {pm_arrow} {abs(pm_change_pct):.2f}%</span></div>')

        # Add closing/regular price if available
        if has_closing_price:
            close_price = price_data["price"]
            close_change = price_data.get("change", 0)
            close_change_pct = price_data.get("change_pct", 0)
            close_color = theme.PALETTE["up"] if close_change >= 0 else theme.PALETTE["down"]
            close_arrow = "▲" if close_change >= 0 else "▼"
            price_parts.append(f'<div class="price-item"><span style="font-size:14px; color:#9ca3af;">Close:</span> <span style="font-size:18px; color:{close_color};">${close_price:.2f} {close_arrow} {abs(close_change_pct):.2f}%</span></div>')

        # Combine both prices with responsive separator
        price_suffix = '<span class="price-separator"> | </span>'.join(price_parts)

    badge_sec = f'<span class="badge badge-sector">{sector}</span>' if sector else ""
    badge_ind = f'<span class="badge badge-industry">{industry}</span>' if industry else ""

    # Build price div separately to avoid nested f-string issues
    price_div = f'<div style="margin-top: 8px; margin-bottom: 12px;">{price_suffix}</div>' if price_suffix else ''

    html = f"""{_CARD_CSS}
<div class="stock-header">
  <div class="stock-name">{name}{ticker_suffix}</div>
  {price_div}
  <div>{badge_sec}{badge_ind}</div>
</div>
<div class="metric-grid">

  <div class="mgroup" style="--gc:#2563eb">
    <div class="mgroup-title">Valuation</div>
    <div class="mpair">
      <div class="mitem">
        <div class="mlabel">Trailing P/E</div>
        <div class="mval">{v(pe,'.1f') + 'x' if pe else 'N/A'}</div>
      </div>
      <div class="mitem">
        <div class="mlabel">Forward P/E</div>
        <div class="mval">{v(fpe,'.1f') + 'x' if fpe else 'N/A'}</div>
      </div>
    </div>
  </div>

  <div class="mgroup" style="--gc:#059669">
    <div class="mgroup-title">Earnings &amp; Cash Flow</div>
    <div class="mpair">
      <div class="mitem">
        <div class="mlabel">EPS (TTM)</div>
        <div class="mval">{v(eps,'.2f','$') if eps else 'N/A'}</div>
      </div>
      <div class="mitem">
        <div class="mlabel">FCF Yield (TTM)</div>
        <div class="mval {fcf_cls}">{v(fcf_yield,'.1f',suf='%') if fcf_yield is not None else 'N/A'}</div>
      </div>
    </div>
    {_get_forecast_html_block(fcf_forecast) if fcf_forecast else ''}
  </div>

  <div class="mgroup" style="--gc:#d97706">
    <div class="mgroup-title">Size &amp; Price</div>
    <div class="mpair">
      <div class="mitem">
        <div class="mlabel">Market Cap</div>
        <div class="mval">{f'${mc/1e9:.1f}B' if mc else 'N/A'}</div>
      </div>
      <div class="mitem">
        <div class="mlabel">52-Week Range</div>
        <div class="mval-sm">{f'${lo:.0f}&nbsp;–&nbsp;${hi:.0f}' if lo and hi else 'N/A'}</div>
      </div>
    </div>
  </div>

  <div class="mgroup" style="--gc:#db2777">
    <div class="mgroup-title">Dividend Income</div>
    <div class="mpair">
      <div class="mitem">
        <div class="mlabel">Dividend Yield</div>
        <div class="mval">{v(div_yield,'.2f',suf='%') if div_yield else 'N/A'}</div>
      </div>
      <div class="mitem">
        <div class="mlabel">Annual Dividend</div>
        <div class="mval">{v(div_annual,'.2f','$') if div_annual else 'N/A'}</div>
      </div>
    </div>
  </div>

  <div class="mgroup" style="--gc:#7c3aed">
    <div class="mgroup-title">Momentum (RSI 14)</div>
    <div class="mpair">
      <div class="mitem">
        <div class="mlabel">Daily</div>
        <div class="mval {rsi_d_cls}">{f'{curr_rsi_d:.1f}{rsi_d_tag}' if curr_rsi_d else 'N/A'}</div>
      </div>
      <div class="mitem">
        <div class="mlabel">Weekly</div>
        <div class="mval {rsi_w_cls}">{f'{curr_rsi_w:.1f}{rsi_w_tag}' if curr_rsi_w else 'N/A'}</div>
      </div>
    </div>
  </div>

</div>
"""
    st.markdown(html, unsafe_allow_html=True)


def render_single_stock(ticker: str) -> None:
    # CSS for red remove button
    st.markdown("""
    <style>
    [data-testid="stButton"] button[key="remove_button"] {
        background-color: #dc2626 !important;
        color: white !important;
    }
    </style>
    """, unsafe_allow_html=True)

    with st.spinner("Loading stock data..."):
        info         = _stock_info_safe(ticker)
        current_price = _stock_current_price(ticker)
        premarket_price = _stock_premarket_price(ticker)
        fcf_yield    = _stock_fcf_yield(ticker)
        fcf_forecast = _stock_fcf_forecast_2026(ticker)
        rsi_daily    = _stock_rsi(ticker, weekly=False)
        rsi_weekly   = _stock_rsi(ticker, weekly=True)
        curr_rsi_d   = float(rsi_daily["rsi"].iloc[-1])  if not rsi_daily.empty  else None
        curr_rsi_w   = float(rsi_weekly["rsi"].iloc[-1]) if not rsi_weekly.empty else None

    # Handle remove button setup
    custom_stocks = _load_custom_stocks()
    is_custom = ticker in custom_stocks

    # Display metrics and remove button on same "row" using columns
    col_metrics, col_btn = st.columns([18, 2], gap="large")

    with col_metrics:
        # Load metrics (includes stock title with sector/industry badges)
        _stock_metric_cards(info, fcf_yield, curr_rsi_d, curr_rsi_w, fcf_forecast, ticker, current_price, premarket_price)

    with col_btn:
        if st.button("Remove", key=f"remove_{ticker}", help="Remove from watchlist"):
            if is_custom:
                _remove_custom_stock(ticker)
            else:
                st.session_state.hidden_stocks.add(ticker)

            st.success(f"Removed {ticker}")
            st.session_state["pending_select"] = None
            st.rerun()

    st.divider()

    pe = info.get("pe_ratio")

    # ── Price & Technical Analysis ────────────────────────────────────────────
    st.markdown('<div class="sec-label">Price &amp; Technical Analysis</div>',
                unsafe_allow_html=True)

    price_df = _stock_price(ticker, "5y")
    ohlcv_df = _stock_ohlcv(ticker, "5y")

    # Chart type toggle (Line/Candle)
    col1, col2 = st.columns([5, 1])
    with col2:
        chart_type = st.radio(
            "", ["Line", "Candle"],
            index=1, horizontal=True,
            label_visibility="collapsed",
            key=f"ct_{ticker}",
        )

    fig_price = go.Figure()
    if chart_type == "Line" and not price_df.empty:
        fig_price.add_trace(go.Scatter(
            x=price_df["date"], y=price_df["close"],
            mode="lines",
            line=dict(color=theme.PALETTE["primary"], width=2),
            fill="tozeroy", fillgradient=theme.gradient(theme.PALETTE["primary"]),
            hovertemplate="%{x|%b %d, %Y} — $%{y:,.2f}<extra></extra>",
        ))
    elif chart_type == "Candle" and not ohlcv_df.empty:
        fig_price.add_trace(go.Candlestick(
            x=ohlcv_df["date"],
            open=ohlcv_df["open"], high=ohlcv_df["high"],
            low=ohlcv_df["low"],   close=ohlcv_df["close"],
            increasing_line_color=theme.PALETTE["up"],
            increasing_fillcolor=theme.PALETTE["up"],
            decreasing_line_color=theme.PALETTE["down"],
            decreasing_fillcolor=theme.PALETTE["down"],
        ))

    fig_price.update_layout(**theme.pro_layout("Stock Price (5Y)", "USD ($)", edge=True))
    fig_price.update_layout(
        xaxis=dict(
            rangebreaks=[
                dict(bounds=["sat", "mon"]),  # Hide weekends (Saturday to Monday)
                # Market holidays (US market closed)
                dict(values=["2024-01-01", "2024-01-15", "2024-02-19", "2024-03-29",
                             "2024-05-27", "2024-06-19", "2024-07-04", "2024-09-02",
                             "2024-11-28", "2024-12-25",
                             "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18",
                             "2025-05-26", "2025-06-19", "2025-07-04", "2025-09-01",
                             "2025-11-27", "2025-12-25",
                             "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-10",
                             "2026-05-25", "2026-06-19", "2026-07-04", "2026-09-07",
                             "2026-11-26", "2026-12-25"]),
            ]
        )
    )

    # Timeframe buttons (1M/6M/1Y/5Y/All) inside the chart — fast, no rerun
    today = pd.Timestamp.today().normalize()
    timeframes = [
        ("1M", today - pd.DateOffset(months=1)),
        ("6M", today - pd.DateOffset(months=6)),
        ("1Y", today - pd.DateOffset(years=1)),
        ("5Y", today - pd.DateOffset(years=5)),
        ("All", None),
    ]
    _apply_range_buttons(fig_price, price_df, "date", "close", timeframes=timeframes,
                         perf_colors=(chart_type == "Line"))
    _show_chart(fig_price)

    with st.container(border=False):
        _show_chart(_rsi_chart(rsi_daily, rsi_weekly))

    # ── Fundamentals ─────────────────────────────────────────────────────────
    st.markdown('<div class="sec-label">Fundamentals</div>', unsafe_allow_html=True)
    with st.container(border=False):
        fa, fb, fc = st.columns(3)

        with fa:
            pe_df      = _stock_pe(ticker)
            loss_years = set(_stock_pe_all(ticker))
            all_years  = sorted(
                set(pe_df["year"].tolist() if not pe_df.empty else []) | loss_years
            )
            if all_years:
                pe_by_year    = dict(zip(pe_df["year"], pe_df["pe"])) if not pe_df.empty else {}
                valid_years   = [y for y in all_years if y in pe_by_year]
                valid_pe      = [pe_by_year[y] for y in valid_years]
                all_year_strs = [str(y) for y in all_years]
                fig_pe = go.Figure()
                if valid_years:
                    fig_pe.add_trace(go.Scatter(
                        x=[str(y) for y in valid_years], y=valid_pe,
                        mode="lines+markers",
                        line=dict(color=theme.PALETTE["amber"], width=1.8),
                        marker=dict(size=7, color=theme.PALETTE["amber"],
                                    line=dict(width=1.5, color="white")),
                        hovertemplate="%{x}: %{y:.1f}×<extra></extra>",
                    ))
                if loss_years:
                    fig_pe.add_trace(go.Scatter(
                        x=[str(y) for y in loss_years], y=[0] * len(loss_years),
                        mode="markers+text",
                        marker=dict(symbol="x-thin", size=13, color=theme.PALETTE["down"],
                                    line=dict(width=2)),
                        text=["Loss"] * len(loss_years), textposition="top center",
                        hovertemplate="%{x}: Net Loss<extra></extra>",
                        showlegend=False,
                    ))
                if pe is not None:
                    fig_pe.add_hline(y=pe, line_dash="dot",
                                     line_color=theme.PALETTE["border"],
                                     line_width=1,
                                     annotation_text=f"Now: {pe:.1f}×",
                                     annotation_font=dict(size=10, color=theme.PALETTE["faint"]),
                                     annotation_position="top right")
                fig_pe.update_layout(**theme.pro_layout("P/E Ratio — Annual", "P/E (×)"))
                fig_pe.update_layout(
                    yaxis=dict(rangemode="tozero"),
                    xaxis=dict(type="category", categoryorder="array",
                               categoryarray=all_year_strs),
                )
                _show_chart(fig_pe)
                if loss_years:
                    st.caption(f"✕ Loss year(s): {', '.join(str(y) for y in sorted(loss_years))}")
            else:
                st.info("P/E history unavailable.")

        with fb:
            rev_df = _stock_revenue(ticker)
            if not rev_df.empty:
                fig_rev = go.Figure(go.Bar(
                    x=rev_df["year"].astype(str), y=rev_df["revenue"],
                    marker=dict(color=theme.PALETTE["up"], opacity=0.85, line=dict(width=0)),
                    customdata=rev_df["revenue"],
                    hovertemplate="%{x}: $%{customdata:.2f}B<extra></extra>",
                ))
                fig_rev.update_layout(**theme.pro_layout("Annual Revenue", "USD (B)"))
                fig_rev.update_layout(xaxis=dict(type="category"))
                _show_chart(fig_rev)
            else:
                st.info("Revenue data unavailable.")

        with fc:
            fcf_hist = _stock_fcf_history(ticker)
            fcf_forecast = _stock_fcf_forecast_2026(ticker)
            if not fcf_hist.empty and fcf_hist["fcf_yield"].notna().any():
                valid = fcf_hist.dropna(subset=["fcf_yield"])
                fig_fcf = go.Figure(go.Scatter(
                    x=valid["year"].astype(str), y=valid["fcf_yield"],
                    mode="lines+markers", name="Historical",
                    line=dict(color=theme.PALETTE["violet"], width=1.8),
                    marker=dict(
                        size=8,
                        color=[theme.PALETTE["up"] if v >= 0 else theme.PALETTE["down"]
                               for v in valid["fcf_yield"]],
                        line=dict(width=1.5, color="white"),
                    ),
                    customdata=valid["fcf_b"],
                    hovertemplate="%{x} — Yield: %{y:.1f}%<br>FCF: $%{customdata:.2f}B<extra></extra>",
                ))

                # Add 2026 forecast point
                if fcf_forecast:
                    # Add dotted line connecting last historical year to 2026 forecast
                    last_year = valid["year"].iloc[-1]
                    last_yield = valid["fcf_yield"].iloc[-1]
                    fig_fcf.add_trace(go.Scatter(
                        x=[str(last_year), "2026"],
                        y=[last_yield, fcf_forecast["fcf_yield_2026"]],
                        mode="lines", name="Projection",
                        line=dict(color=theme.PALETTE["amber"], width=1.8, dash="dot"),
                        hovertemplate="%{x} — Yield: %{y:.1f}%<extra></extra>",
                    ))

                    fig_fcf.add_trace(go.Scatter(
                        x=["2026"], y=[fcf_forecast["fcf_yield_2026"]],
                        mode="markers", name="2026 Forecast",
                        marker=dict(size=12, color=theme.PALETTE["amber"],
                                   symbol="diamond",
                                   line=dict(width=2, color="white")),
                        hovertemplate="2026 Forecast — Yield: %{y:.1f}%<extra></extra>",
                    ))

                fig_fcf.add_hline(y=0, line_dash="dot",
                                  line_color=theme.PALETTE["border"], line_width=1)
                fig_fcf.update_layout(**theme.pro_layout("FCF Yield — Annual", "%"))
                fig_fcf.update_layout(xaxis=dict(type="category"))
                _show_chart(fig_fcf)
            else:
                st.info("FCF Yield data unavailable.")

    # ── Dividend Trend ───────────────────────────────────────────────────────
    div_hist = _stock_dividend_yield_history(ticker)
    if not div_hist.empty:
        st.markdown('<div class="sec-label">Dividend Trend</div>', unsafe_allow_html=True)
        with st.container(border=False):
            fig_div = go.Figure(go.Scatter(
                x=div_hist["date"], y=div_hist["dividend_yield"],
                mode="lines+markers", name="Dividend Yield",
                line=dict(color=theme.PALETTE["pink"], width=1.8),
                marker=dict(size=6, color=theme.PALETTE["pink"],
                           line=dict(width=1.5, color="white")),
                hovertemplate="%{x|%b %Y} — Yield: %{y:.2f}%<extra></extra>",
            ))
            fig_div.update_layout(**theme.pro_layout("Dividend Yield — Historical", "%",
                                                     edge=True))
            _show_chart(fig_div)


def render_stock_tracing() -> None:
    if "watchlist_error" in st.session_state:
        st.error(f"Watchlist save failed: {st.session_state.pop('watchlist_error')}")

    # Initialize hidden stocks list (for removed defaults)
    if "hidden_stocks" not in st.session_state:
        st.session_state.hidden_stocks = set()

    custom   = _load_custom_stocks()
    all_stocks = {**TRACKED_STOCKS, **custom}

    # Filter out hidden stocks (removed defaults)
    all_stocks = {k: v for k, v in all_stocks.items() if k not in st.session_state.hidden_stocks}
    tickers  = list(all_stocks.keys())

    # ── Controls row ──────────────────────────────────────────────────────────
    col_sel, col_search, col_btn = st.columns([3, 2, 1])
    with col_sel:
        # Restore selection after adding a new stock
        default_idx = 0
        if "pending_select" in st.session_state:
            pending = st.session_state.pop("pending_select")
            if pending in tickers:
                default_idx = tickers.index(pending)

        selected_ticker = st.selectbox(
            "Watchlist",
            options=tickers,
            index=default_idx,
            format_func=lambda t: f"{all_stocks[t]}  ({t})",
        )

    with col_search:
        search_input = st.text_input("Search ticker", placeholder="e.g. AAPL",
                                     label_visibility="hidden", key="stock_search_input")
    with col_btn:
        st.write("")
        search_clicked = st.button("Search", type="primary", use_container_width=True)

    # ── Earnings Calendar ─────────────────────────────────────────────────────
    with st.expander("📅 Earnings Calendar", expanded=False):
        earnings_cal = _earnings_calendar(tuple(tickers))
        calendar_text = format_earnings_for_display(earnings_cal)
        st.markdown(calendar_text)
        st.caption("*Auto-updates after earnings releases at 9:30 AM & 6:00 PM ET*")

    # ── Search logic ──────────────────────────────────────────────────────────
    # Trigger search on button click OR when Enter is pressed in search box (text_input rerun)
    should_search = False
    search_query = ""

    if search_clicked and search_input:
        should_search = True
        search_query = search_input
    elif search_input and search_input != st.session_state.get("last_search_input", ""):
        # Detect Enter key - when search_input changes and is not empty
        # This happens when user presses Enter in the text field
        should_search = True
        search_query = search_input

    # Store current input for next comparison
    st.session_state["last_search_input"] = search_input

    if should_search and search_query:
        query = search_query.strip().upper()
        if query in all_stocks:
            st.info(f"**{query}** is already in your watchlist.")
            st.session_state.pop("search_result", None)
        else:
            with st.spinner(f"Looking up {query}…"):
                result_info = fetch_stock_info(query)
            if result_info.get("sector") or result_info.get("market_cap"):
                st.session_state["search_result"] = {"ticker": query, "info": result_info}
            else:
                st.warning(f"Could not find **{query}**. Check the symbol and try again.")
                st.session_state.pop("search_result", None)

    if "search_result" in st.session_state:
        res  = st.session_state["search_result"]
        t, info = res["ticker"], res["info"]
        mc   = info.get("market_cap")
        mc_str = f"${mc/1e9:.1f}B" if mc else "N/A"

        # Show search result info
        st.info(
            f"**{info.get('name', t)}** ({t})  ·  {info.get('sector', '')}  ·  "
            f"{info.get('industry', '')}  ·  Market Cap: {mc_str}"
        )

        # Action buttons
        add_col, dismiss_col = st.columns([1, 1])
        with add_col:
            if st.button(f"Add {t} to watchlist", type="primary", use_container_width=True):
                _save_custom_stock(t, info.get("name", t))
                del st.session_state["search_result"]
                st.session_state["pending_select"] = t
                st.rerun()
        with dismiss_col:
            if st.button("Dismiss", use_container_width=True):
                del st.session_state["search_result"]
                st.rerun()

        # Show stock details immediately after search
        st.divider()
        st.markdown(f"### {info.get('name', t)} ({t})")
        render_single_stock(t)
    else:
        st.divider()
        render_single_stock(selected_ticker)


# ── Main tabs ─────────────────────────────────────────────────────────────────

cap_tab, macro_tab, stock_tab, news_tab = st.tabs([
    "Capital Markets", "Macro Economy", "Stock Tracing", "News Feed"
])

with cap_tab:
    render_capital_markets()

with macro_tab:
    render_macro()

with stock_tab:
    render_stock_tracing()

with news_tab:
    render_news()

# ── Footer ────────────────────────────────────────────────────────────────────

st.markdown("---")
source = "FRED, BLS, Yahoo Finance" if not fred_key_configured() else "FRED, Yahoo Finance"
st.caption(f"Data sourced from {source}. Market data refreshes hourly. Click **Sync FRED Data** for latest macro releases.")
