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
from src.stock_fetcher import fetch_stock_info, fetch_stock_price, fetch_revenue, fetch_pe_history, fetch_loss_years, fetch_fcf_yield, fetch_fcf_history, fetch_rsi
from src.news_fetcher import deduplicate_by_similarity
from src.news_analyzer import get_groq_client, groq_key_configured
from src.news_pipeline import run_pipeline, needs_run

DB_PATH           = os.path.join(os.path.dirname(__file__), "..", "data", "econ_data.db")
CUSTOM_STOCKS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "custom_stocks.json")


def _load_custom_stocks() -> dict:
    if os.path.exists(CUSTOM_STOCKS_PATH):
        try:
            with open(CUSTOM_STOCKS_PATH) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_custom_stocks(stocks: dict) -> None:
    with open(CUSTOM_STOCKS_PATH, "w") as f:
        json.dump(stocks, f, indent=2)

st.set_page_config(
    page_title="Economic Dashboard",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Session state ─────────────────────────────────────────────────────────────

if "store" not in st.session_state:
    st.session_state.store = Store()
if "update_results" not in st.session_state:
    st.session_state.update_results = None

store = st.session_state.store

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
    return fetch_stock_info(ticker)

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
def _stock_fcf_yield(ticker):
    return fetch_fcf_yield(ticker)

@st.cache_data(ttl=3600)
def _stock_fcf_history(ticker):
    return fetch_fcf_history(ticker)

@st.cache_data(ttl=3600)
def _stock_rsi(ticker, weekly=False):
    return fetch_rsi(ticker, weekly=weekly)

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
    fred = get_fred_client()
    msg = ("Loading available data from BLS & Yahoo Finance (no FRED key)..."
           if fred is None else "First run — loading all macro series from FRED (~60 sec)...")
    st.info(msg)
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


def _range_buttons():
    return dict(
        rangeselector=dict(buttons=[
            dict(count=1,  label="1Y",  step="year",  stepmode="backward"),
            dict(count=5,  label="5Y",  step="year",  stepmode="backward"),
            dict(count=10, label="10Y", step="year",  stepmode="backward"),
            dict(step="all", label="All"),
        ]),
        rangeslider=dict(visible=False),
    )


_PRO_CSS = """
<style>
.modebar-container { opacity: 0.25 !important; transition: opacity .2s; }
.modebar-container:hover { opacity: 1 !important; }
.sec-label {
    font-size: 11px; font-weight: 700; color: #9ca3af;
    text-transform: uppercase; letter-spacing: 1.2px;
    padding-bottom: 8px; border-bottom: 1px solid #f3f4f6;
    margin: 14px 0 4px 0;
}
</style>
"""


def _pro_layout(title: str, y_title: str, height: int = 300) -> dict:
    return dict(
        title=dict(text=f"<b>{title}</b>",
                   font=dict(size=13, color="#111827"), y=0.97, yanchor="top"),
        yaxis=dict(
            title=dict(text=y_title, font=dict(size=11, color="#9ca3af")),
            gridcolor="#f3f4f6", gridwidth=1,
            tickfont=dict(size=11, color="#9ca3af"),
            zeroline=False, showline=False,
        ),
        xaxis=dict(
            gridcolor="#f3f4f6",
            tickfont=dict(size=11, color="#9ca3af"),
            showline=False,
        ),
        plot_bgcolor="#ffffff",
        paper_bgcolor="rgba(0,0,0,0)",
        height=height,
        margin=dict(l=58, r=24, t=50, b=40),
        hovermode="x unified",
        showlegend=False,
        hoverlabel=dict(bgcolor="white", bordercolor="#e5e7eb",
                        font=dict(size=12, color="#111827")),
    )


def _rsi_chart(daily_df: pd.DataFrame, weekly_df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if not daily_df.empty:
        fig.add_trace(go.Scatter(
            x=daily_df["date"], y=daily_df["rsi"],
            mode="lines", name="Daily",
            line=dict(width=1.5, color="#8b5cf6"),
            hovertemplate="%{x|%b %d, %Y}: %{y:.1f}<extra>Daily</extra>",
        ))
    if not weekly_df.empty:
        fig.add_trace(go.Scatter(
            x=weekly_df["date"], y=weekly_df["rsi"],
            mode="lines", name="Weekly",
            line=dict(width=2.5, color="#f59e0b"),
            hovertemplate="%{x|%b %d, %Y}: %{y:.1f}<extra>Weekly</extra>",
        ))
    fig.add_hrect(y0=70, y1=100, fillcolor="rgba(239,68,68,0.04)", line_width=0)
    fig.add_hrect(y0=0,  y1=30,  fillcolor="rgba(16,185,129,0.04)", line_width=0)
    fig.add_hline(y=70, line_dash="dot", line_color="#ef4444", line_width=1,
                  annotation_text="Overbought 70",
                  annotation_font=dict(size=10, color="#ef4444"),
                  annotation_position="top right")
    fig.add_hline(y=30, line_dash="dot", line_color="#10b981", line_width=1,
                  annotation_text="Oversold 30",
                  annotation_font=dict(size=10, color="#10b981"),
                  annotation_position="bottom right")
    fig.update_layout(**_pro_layout("RSI — Daily vs Weekly (14-period)", "", height=260))
    fig.update_layout(
        yaxis=dict(range=[0, 100], gridcolor="#f3f4f6",
                   tickfont=dict(size=11, color="#9ca3af"), zeroline=False, showline=False),
        xaxis=dict(**_range_buttons(), gridcolor="#f3f4f6",
                   tickfont=dict(size=11, color="#9ca3af"), showline=False),
        legend=dict(orientation="h", x=0, y=1.08,
                    font=dict(size=11, color="#6b7280"),
                    bgcolor="rgba(0,0,0,0)"),
        showlegend=True,
    )
    return fig


def _line_chart(df: pd.DataFrame, title: str, yaxis: str,
                x_col="date", y_col="value", color="#1f77b4",
                zero_line=False) -> go.Figure:
    fig = go.Figure()
    if not df.empty:
        fig.add_trace(go.Scatter(
            x=df[x_col], y=df[y_col],
            mode="lines", line=dict(width=2, color=color),
            hovertemplate="%{x|%Y-%m-%d}: %{y:,.4g}<extra></extra>",
        ))
    if zero_line:
        fig.add_hline(y=0, line_dash="dash", line_color="gray", line_width=1)
    fig.update_layout(
        title=dict(text=title, font=dict(size=13), y=0.97, yanchor="top"),
        yaxis_title=yaxis,
        height=320,
        margin=dict(l=50, r=20, t=60, b=40),
        hovermode="x unified",
        showlegend=False,
        xaxis=_range_buttons(),
    )
    return fig


# ── Section 1: Capital Markets ────────────────────────────────────────────────

def render_capital_markets():
    # ── Major indices ─────────────────────────────────────────────────────────
    st.subheader("Major Indices")
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
        colors = ["#2ecc71" if x >= 0 else "#e74c3c" for x in sec_df["pct_change"]]
        fig = go.Figure(go.Bar(
            x=sec_df["pct_change"],
            y=sec_df["name"],
            orientation="h",
            marker_color=colors,
            text=[f"{x:+.2f}%" for x in sec_df["pct_change"]],
            textposition="outside",
        ))
        fig.update_layout(
            height=420,
            margin=dict(l=140, r=80, t=20, b=40),
            xaxis=dict(title=f"% Change ({period_labels[selected_period]})",
                       zeroline=True, zerolinecolor="gray"),
            yaxis=dict(autorange="reversed"),
            showlegend=False,
            dragmode=False,
        )
        # on_select captures bar clicks; key resets selection when period changes
        event = st.plotly_chart(
            fig,
            use_container_width=True,
            on_select="rerun",
            key=f"sector_chart_{selected_period}",
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
                    x_col="date", y_col="close", color="#5C6BC0",
                )
                st.plotly_chart(fig5, use_container_width=True)
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
        if not comm_df.empty:
            c_cols = st.columns(len(comm_df))
            for col, row in zip(c_cols, comm_df.itertuples()):
                col.metric(row.name, f"{row.current:,.2f}", f"{row.pct_change:+.2f}%")
    with col_b:
        st.subheader("Rates & Volatility")
        if not rate_df.empty:
            r_cols = st.columns(len(rate_df))
            for col, row in zip(r_cols, rate_df.itertuples()):
                inv = row.ticker in INVERSE_DELTA_SERIES
                col.metric(
                    row.name,
                    f"{row.current:.2f}%",
                    f"{row.pct_change:+.2f}%",
                    delta_color="inverse" if inv else "normal",
                )



# ── Section 2: Macro Economy ──────────────────────────────────────────────────

def render_market_leading_charts():
    """S&P 500, VIX, yield curve, and spread — shown in the Leading Indicators tab."""
    st.subheader("Market-Based Leading Indicators")
    c1, c2 = st.columns(2)
    with c1:
        sp_df = _historical("^GSPC")
        st.plotly_chart(
            _line_chart(sp_df, "S&P 500", "Price", x_col="date", y_col="close", color="#2196F3"),
            use_container_width=True,
        )
    with c2:
        vix_df = _historical("^VIX")
        st.plotly_chart(
            _line_chart(vix_df, "VIX (Volatility)", "Index", x_col="date", y_col="close", color="#FF5722"),
            use_container_width=True,
        )

    c3, c4 = st.columns(2)
    df10 = store.get_observations("DGS10")
    df2  = store.get_observations("DGS2")
    with c3:
        fig_yc = go.Figure()
        if not df10.empty:
            fig_yc.add_trace(go.Scatter(
                x=df10["date"], y=df10["value"],
                mode="lines", name="10-Year", line=dict(color="#1565C0", width=2),
                hovertemplate="%{x|%Y-%m-%d}: %{y:.2f}%<extra>10Y</extra>",
            ))
        if not df2.empty:
            fig_yc.add_trace(go.Scatter(
                x=df2["date"], y=df2["value"],
                mode="lines", name="2-Year", line=dict(color="#EF6C00", width=2),
                hovertemplate="%{x|%Y-%m-%d}: %{y:.2f}%<extra>2Y</extra>",
            ))
        fig_yc.update_layout(
            title=dict(text="Treasury Yield Curve (10Y vs 2Y)", font=dict(size=13), y=0.97, yanchor="top"),
            yaxis_title="Yield (%)",
            height=320,
            margin=dict(l=50, r=20, t=60, b=40),
            hovermode="x unified",
            legend=dict(x=0.01, y=0.92),
            xaxis=_range_buttons(),
        )
        st.plotly_chart(fig_yc, use_container_width=True)

    with c4:
        if not df10.empty and not df2.empty:
            d10 = df10.set_index("date")["value"]
            d2  = df2.set_index("date")["value"]
            spread = (d10 - d2).dropna().reset_index()
            spread.columns = ["date", "value"]
            fig_sp = go.Figure(go.Bar(
                x=spread["date"], y=spread["value"],
                marker_color=["#2ecc71" if v >= 0 else "#e74c3c" for v in spread["value"]],
                hovertemplate="%{x|%Y-%m-%d}: %{y:.2f}%<extra></extra>",
            ))
            fig_sp.add_hline(y=0, line_dash="dash", line_color="gray", line_width=1)
            fig_sp.update_layout(
                title=dict(text="Yield Spread (10Y − 2Y)", font=dict(size=13), y=0.97, yanchor="top"),
                yaxis_title="Spread (%)",
                height=320,
                margin=dict(l=50, r=20, t=60, b=40),
                showlegend=False,
                xaxis=_range_buttons(),
            )
            st.plotly_chart(fig_sp, use_container_width=True)
        else:
            st.info("Sync FRED data to see the yield spread chart.")

def render_indicator_group(group_key: str, series_dict: dict) -> None:
    series_ids = list(series_dict.keys())

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
        last_upd = row["last_updated"].iloc[0] if not row.empty else "Unknown"
        inv    = sid in INVERSE_DELTA_SERIES

        col.metric(
            label=info["name"],
            value=_format_value(latest, info["unit"]),
            delta=delta,
            delta_color="inverse" if inv else "normal",
            help=f"{info['unit']}  ·  {info['frequency']}  ·  Updated: {last_upd}",
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
                extra_col.metric(
                    "Yield Spread (10Y−2Y)",
                    f"{latest_spread:.2f}%",
                    delta_spread,
                    delta_color="normal",
                    help="10-Year minus 2-Year Treasury yield. Negative = inverted curve.",
                )

    st.divider()

    # Charts (2 per row)
    pairs = [series_ids[i:i + 2] for i in range(0, len(series_ids), 2)]
    for pair in pairs:
        chart_cols = st.columns(2)
        for col, sid in zip(chart_cols, pair):
            info = series_dict[sid]
            df = store.get_observations(sid)
            col.plotly_chart(
                _line_chart(df, info["name"], info["unit"]),
                use_container_width=True,
            )


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
.badge-sector   { background: rgba(33,150,243,0.12); color: #1565C0; }
.badge-industry { background: rgba(156,39,176,0.10); color: #6a1b9a; }
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
.pos { color: #2ecc71; } .neg { color: #e74c3c; }
.ob  { color: #e74c3c; } .os  { color: #2ecc71; }
</style>
"""

def _stock_metric_cards(info: dict, fcf_yield, curr_rsi_d, curr_rsi_w) -> None:
    pe  = info.get("pe_ratio")
    fpe = info.get("forward_pe")
    eps = info.get("eps")
    mc  = info.get("market_cap")
    hi  = info.get("52w_high")
    lo  = info.get("52w_low")

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

    badge_sec = f'<span class="badge badge-sector">{sector}</span>' if sector else ""
    badge_ind = f'<span class="badge badge-industry">{industry}</span>' if industry else ""

    html = f"""{_CARD_CSS}
<div class="stock-header">
  <div class="stock-name">{name}</div>
  <div>{badge_sec}{badge_ind}</div>
</div>
<div class="metric-grid">

  <div class="mgroup" style="--gc:#2196F3">
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

  <div class="mgroup" style="--gc:#4CAF50">
    <div class="mgroup-title">Earnings &amp; Cash Flow</div>
    <div class="mpair">
      <div class="mitem">
        <div class="mlabel">EPS (TTM)</div>
        <div class="mval">{v(eps,'.2f','$') if eps else 'N/A'}</div>
      </div>
      <div class="mitem">
        <div class="mlabel">FCF Yield</div>
        <div class="mval {fcf_cls}">{v(fcf_yield,'.1f',suf='%') if fcf_yield is not None else 'N/A'}</div>
      </div>
    </div>
  </div>

  <div class="mgroup" style="--gc:#FF9800">
    <div class="mgroup-title">Size &amp; Price</div>
    <div class="mpair">
      <div class="mitem">
        <div class="mlabel">Market Cap</div>
        <div class="mval">{f'${mc/1e9:.1f}B' if mc else 'N/A'}</div>
      </div>
      <div class="mitem">
        <div class="mlabel">52-Week Range</div>
        <div class="mval-sm">${lo:.0f}&nbsp;–&nbsp;${hi:.0f}</div>
      </div>
    </div>
  </div>

  <div class="mgroup" style="--gc:#9C27B0">
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
    info       = _stock_info(ticker)
    fcf_yield  = _stock_fcf_yield(ticker)
    rsi_daily  = _stock_rsi(ticker, weekly=False)
    rsi_weekly = _stock_rsi(ticker, weekly=True)
    curr_rsi_d = float(rsi_daily["rsi"].iloc[-1])  if not rsi_daily.empty  else None
    curr_rsi_w = float(rsi_weekly["rsi"].iloc[-1]) if not rsi_weekly.empty else None

    _stock_metric_cards(info, fcf_yield, curr_rsi_d, curr_rsi_w)

    st.divider()

    pe = info.get("pe_ratio")

    st.markdown(_PRO_CSS, unsafe_allow_html=True)

    # ── Price & Technical Analysis ────────────────────────────────────────────
    st.markdown('<div class="sec-label">Price &amp; Technical Analysis</div>',
                unsafe_allow_html=True)
    with st.container(border=True):
        price_df = _stock_price(ticker, "5y")
        fig_price = go.Figure()
        if not price_df.empty:
            fig_price.add_trace(go.Scatter(
                x=price_df["date"], y=price_df["close"],
                mode="lines",
                line=dict(color="#2563eb", width=2),
                fill="tozeroy", fillcolor="rgba(37,99,235,0.06)",
                hovertemplate="%{x|%b %d, %Y} — $%{y:,.2f}<extra></extra>",
            ))
        fig_price.update_layout(**_pro_layout("Stock Price (5Y)", "USD ($)", height=300))
        fig_price.update_layout(
            xaxis=dict(**_range_buttons(), gridcolor="#f3f4f6",
                       tickfont=dict(size=11, color="#9ca3af"), showline=False),
        )
        st.plotly_chart(fig_price, use_container_width=True)
        st.plotly_chart(_rsi_chart(rsi_daily, rsi_weekly), use_container_width=True)

    # ── Fundamentals ─────────────────────────────────────────────────────────
    st.markdown('<div class="sec-label">Fundamentals</div>', unsafe_allow_html=True)
    with st.container(border=True):
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
                        line=dict(color="#f59e0b", width=2),
                        marker=dict(size=7, color="#f59e0b",
                                    line=dict(width=1.5, color="white")),
                        hovertemplate="%{x}: %{y:.1f}×<extra></extra>",
                    ))
                if loss_years:
                    fig_pe.add_trace(go.Scatter(
                        x=[str(y) for y in loss_years], y=[0] * len(loss_years),
                        mode="markers+text",
                        marker=dict(symbol="x-thin", size=13, color="#ef4444",
                                    line=dict(width=2)),
                        text=["Loss"] * len(loss_years), textposition="top center",
                        hovertemplate="%{x}: Net Loss<extra></extra>",
                        showlegend=False,
                    ))
                if pe is not None:
                    fig_pe.add_hline(y=pe, line_dash="dot", line_color="#d1d5db",
                                     line_width=1,
                                     annotation_text=f"Now: {pe:.1f}×",
                                     annotation_font=dict(size=10, color="#9ca3af"),
                                     annotation_position="top right")
                fig_pe.update_layout(**_pro_layout("P/E Ratio — Annual", "P/E (×)"))
                fig_pe.update_layout(
                    yaxis=dict(rangemode="tozero", gridcolor="#f3f4f6",
                               tickfont=dict(size=11, color="#9ca3af"),
                               zeroline=False, showline=False),
                    xaxis=dict(type="category", categoryorder="array",
                               categoryarray=all_year_strs, gridcolor="#f3f4f6",
                               tickfont=dict(size=11, color="#9ca3af"), showline=False),
                )
                st.plotly_chart(fig_pe, use_container_width=True)
                if loss_years:
                    st.caption(f"✕ Loss year(s): {', '.join(str(y) for y in sorted(loss_years))}")
            else:
                st.info("P/E history unavailable.")

        with fb:
            rev_df = _stock_revenue(ticker)
            if not rev_df.empty:
                fig_rev = go.Figure(go.Bar(
                    x=rev_df["year"].astype(str), y=rev_df["revenue"],
                    marker=dict(color="#10b981", opacity=0.85, line=dict(width=0)),
                    customdata=rev_df["revenue"],
                    hovertemplate="%{x}: $%{customdata:.2f}B<extra></extra>",
                ))
                fig_rev.update_layout(**_pro_layout("Annual Revenue", "USD (B)"))
                fig_rev.update_layout(
                    xaxis=dict(type="category", gridcolor="#f3f4f6",
                               tickfont=dict(size=11, color="#9ca3af"), showline=False),
                    yaxis=dict(gridcolor="#f3f4f6",
                               tickfont=dict(size=11, color="#9ca3af"),
                               zeroline=False, showline=False),
                )
                st.plotly_chart(fig_rev, use_container_width=True)
            else:
                st.info("Revenue data unavailable.")

        with fc:
            fcf_hist = _stock_fcf_history(ticker)
            if not fcf_hist.empty and fcf_hist["fcf_yield"].notna().any():
                valid = fcf_hist.dropna(subset=["fcf_yield"])
                fig_fcf = go.Figure(go.Scatter(
                    x=valid["year"].astype(str), y=valid["fcf_yield"],
                    mode="lines+markers",
                    line=dict(color="#8b5cf6", width=2),
                    marker=dict(
                        size=8,
                        color=["#10b981" if v >= 0 else "#ef4444"
                               for v in valid["fcf_yield"]],
                        line=dict(width=1.5, color="white"),
                    ),
                    customdata=valid["fcf_b"],
                    hovertemplate="%{x} — Yield: %{y:.1f}%<br>FCF: $%{customdata:.2f}B<extra></extra>",
                ))
                fig_fcf.add_hline(y=0, line_dash="dot", line_color="#e5e7eb", line_width=1)
                fig_fcf.update_layout(**_pro_layout("FCF Yield — Annual", "%"))
                fig_fcf.update_layout(
                    xaxis=dict(type="category", gridcolor="#f3f4f6",
                               tickfont=dict(size=11, color="#9ca3af"), showline=False),
                    yaxis=dict(gridcolor="#f3f4f6",
                               tickfont=dict(size=11, color="#9ca3af"),
                               zeroline=False, showline=False),
                )
                st.plotly_chart(fig_fcf, use_container_width=True)
            else:
                st.info("FCF Yield data unavailable.")


def render_stock_tracing() -> None:
    custom   = _load_custom_stocks()
    all_stocks = {**TRACKED_STOCKS, **custom}
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
                                     label_visibility="hidden")
    with col_btn:
        st.write("")
        search_clicked = st.button("Search", type="primary", use_container_width=True)

    # ── Search logic ──────────────────────────────────────────────────────────
    if search_clicked and search_input:
        query = search_input.strip().upper()
        if query in all_stocks:
            st.info(f"**{query}** is already in your watchlist.")
        else:
            with st.spinner(f"Looking up {query}…"):
                result_info = fetch_stock_info(query)
            if result_info.get("sector") or result_info.get("market_cap"):
                st.session_state["search_result"] = {"ticker": query, "info": result_info}
            else:
                st.warning(f"Could not find **{query}**. Check the symbol and try again.")

    if "search_result" in st.session_state:
        res  = st.session_state["search_result"]
        t, info = res["ticker"], res["info"]
        mc   = info.get("market_cap")
        mc_str = f"${mc/1e9:.1f}B" if mc else "N/A"
        st.info(
            f"**{info.get('name', t)}** ({t})  ·  {info.get('sector', '')}  ·  "
            f"{info.get('industry', '')}  ·  Market Cap: {mc_str}"
        )
        add_col, dismiss_col, _ = st.columns([1, 1, 4])
        with add_col:
            if st.button(f"Add {t} to watchlist", type="primary"):
                custom[t] = info.get("name", t)
                _save_custom_stocks(custom)
                del st.session_state["search_result"]
                st.session_state["pending_select"] = t
                st.rerun()
        with dismiss_col:
            if st.button("Dismiss"):
                del st.session_state["search_result"]
                st.rerun()

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
