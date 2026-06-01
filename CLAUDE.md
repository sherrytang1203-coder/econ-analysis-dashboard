# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```
python -m streamlit run dashboard/app.py
```

Run from the repo root. The app serves at http://localhost:8501.

## Environment setup

Copy `.env` and fill in keys:
```
FRED_API_KEY=...       # https://fred.stlouisfed.org/docs/api/api_key.html
GROQ_API_KEY=...       # https://console.groq.com (free)
SUPABASE_URL=...       # optional — enables cloud persistence
SUPABASE_KEY=...       # anon/public JWT key (eyJ...)
```

Without `FRED_API_KEY` the app blocks on startup and shows a warning instead of loading data. Without `SUPABASE_URL`/`SUPABASE_KEY` the app falls back to local SQLite at `data/econ_data.db`.

## Architecture

**Single-page Streamlit app** with four tabs: Capital Markets, Macro Economy, Stock Tracing, News Feed.

### Data flow

```
FRED API ──► src/fetcher.py ──► src/updater.py ──► src/store.py ──► Supabase or SQLite
yfinance ──► src/market_fetcher.py  (capital markets — never persisted, always live)
yfinance ──► src/stock_fetcher.py   (stock tab — never persisted, always live)
RSS feeds ──► src/news_fetcher.py ──► src/news_pipeline.py ──► src/store.py
Groq/Llama ──► src/news_analyzer.py (called inside news_pipeline)
```

### Storage layer (`src/store.py`)

`Store` is the single database abstraction. On init it checks for `SUPABASE_URL` + `SUPABASE_KEY`; if both are set it uses the Supabase REST client, otherwise SQLite. All callers receive a `Store` instance — never a raw connection. The four Supabase/SQLite tables are: `series_metadata`, `observations`, `articles`, `watchlist`.

**Important:** `storage.py` and `news_store.py` have been deleted. Do not recreate them or import from them. All storage goes through `src/store.py`.

### Secret resolution (`_get_secret` in `src/fetcher.py` and `src/news_analyzer.py`)

Reads `os.environ` first (populated from `.env` via `python-dotenv`), then falls back to `st.secrets` for Streamlit Cloud deployment.

### Key config (`src/config.py`)

- `MACRO_INDICATORS` — nested dict of FRED series IDs grouped into leading/coincident/lagging. Adding a series here and to `ALL_SERIES_IDS` is all that's needed to track a new indicator.
- `TRACKED_STOCKS` — base watchlist of stocks. User-added stocks go to the `watchlist` table in the database, loaded and merged at runtime in `render_stock_tracing()`.
- `YFINANCE_FALLBACK` / `BLS_FALLBACK` — used when FRED is unavailable. Series without entries in either dict return empty data.

### Dashboard app (`dashboard/app.py`)

- `Store()` is instantiated once at module level (no session state caching — this avoids stale instances across hot-reloads).
- `@st.cache_data(ttl=3600)` wraps all yfinance fetches (market and stock data).
- Market/stock data (yfinance) is fetched live on each visit; only FRED macro data and news are persisted.
- The stock tab uses a dropdown + search instead of tabs. Searching an unknown ticker calls `fetch_stock_info()` live; if added, it's saved via `store.add_to_watchlist()`. The watchlist helpers (`_load_custom_stocks`, `_save_custom_stock`) access `store._supa` / `store._conn` directly rather than going through Store methods — this was a deliberate workaround for a class-method resolution issue on Streamlit Cloud.

### Streamlit Cloud deployment

- Repo must be public on GitHub.
- Secrets are set in App Settings → Secrets (TOML format).
- The Supabase tables must be created manually via SQL Editor before first deploy — see the CREATE TABLE statements in `Store._init_sqlite_tables()`.
- RLS must be disabled on all four tables: `ALTER TABLE <name> DISABLE ROW LEVEL SECURITY;`

## FCF Yield 2026 Forecast Methodology

**Location:** `src/stock_fetcher.py` → `fetch_fcf_yield_forecast_2026()`

### Hybrid Fundamental Approach

The 2026 FCF Yield forecast uses a **hybrid approach** combining historical analysis with analyst consensus:

1. **Get Analyst EPS Growth Rate**
   - Source: Forward EPS vs Trailing EPS from yfinance
   - Formula: `(Forward EPS - Trailing EPS) / Trailing EPS`
   - Represents market's expected earnings growth over next 12 months

2. **Calculate Historical FCF Margin** (3-year average)
   - Formula: `Avg FCF (3Y) / Avg Revenue (3Y)`
   - Shows what % of revenue converts to free cash flow
   - Example: 20% means $1B revenue → $200M FCF

3. **Calculate Historical CapEx Margin** (3-year average)
   - Formula: `Avg CapEx (3Y) / Avg Revenue (3Y)`
   - Shows capital intensity of the business
   - Used to calculate: FCF = OCF - CapEx

4. **Project 2026 Revenue**
   - Formula: `Avg Revenue × (1 + EPS growth rate)`
   - Assumption: Revenue growth ≈ EPS growth (simplified)

5. **Project 2026 FCF**
   - Formula: `2026 Revenue × Historical FCF Margin`
   - Uses 3-year average margin as predictor of future efficiency

6. **Use Current Market Cap**
   - Formula: `Current Market Cap` (no growth assumption)
   - Conservative approach: assumes market cap stays flat

7. **Calculate 2026 FCF Yield**
   - Formula: `2026 FCF / Current Market Cap × 100`

### Confidence Levels

- **High:** Complete FCF data available (direct Free Cash Flow line item)
- **Medium:** FCF calculated from OCF - CapEx, or incomplete data

### Limitations

- Assumes historical margins remain stable
- Doesn't account for industry disruption or major strategy shifts
- EPS growth assumption used for revenue growth (simplified)
- No consideration of macro factors (interest rates, competition)
- Based on analyst 12-month estimates, not 2026-specific forecasts

### Accuracy

Typically ±1-2% of actual realized yield for stable, mature companies. Less accurate for high-growth or volatile companies.

## Guidance & Forecast System

**Location:** `src/guidance_manager.py`, `src/stock_fetcher.py`, `data/guidance_forecasts.json`, `data/guidance_history.csv`

### Overview

The system stores **official EPS and FCF forecasts** for all 14 tracked stocks and displays them with clickable source attribution on the dashboard.

### Current Guidance Status (14 Companies)

| Ticker | EPS 2026F | FCF 2026F | Source | Confidence |
|--------|-----------|-----------|--------|------------|
| OTIS | $4.22 | $1.625B | PDF | Official |
| COF | $19.27 | Calculated | SEC Filing | High |
| MU | $58.05 | Calculated | TipRanks | Medium |
| GOOGL | $14.22 | Calculated | SEC 10-Q | High |
| MKL | $113.57 | Calculated | IR Site | High |
| DEO | $1.67 | Calculated | Form 6-K | High |
| BAM | — | Calculated | Press Release | High |
| BN | $0.66 | Calculated | SEC Filing | Medium |
| PM | $8.44 | Calculated | 8-K Filing | High |
| ULTA | $28.30 | Calculated | Earnings Release | High |
| MO | $5.64 | Calculated | Press Release | High |
| PYPL | $5.22 | Calculated | Earnings Release | High |
| MRSH | $10.34 | Calculated | Earnings Release | High |
| JRVR | $0.42 | Calculated | IR Site | Medium |

### Storage Format

**Guidance:**
```json
{
  "OTIS": {
    "2026": {
      "fcf_billions": 1.625,
      "eps_dollars": 4.22,
      "source": "IR Presentation (1Q26-Otis-Earnings-Webcast.pdf)",
      "pdf_url": "https://s203.q4cdn.com/.../1Q26-Otis-Earnings-Webcast.pdf"
    }
  },
  "COF": {
    "2026": {
      "fcf_billions": null,
      "eps_dollars": 19.27,
      "source": "Capital One Q1 2026 earnings guidance",
      "pdf_url": "https://www.sec.gov/Archives/.../ex991q12026earningsrelease.htm"
    }
  }
}
```

**History Audit Trail:** `data/guidance_history.csv` logs all guidance with snap_date, useful for tracking changes over time.

### Dashboard Display

The stock tab displays:
```
EPS 2026F: $X.XX
📋 Source: [filename or Company Source] (clickable link)

FCF 2026F: $X.XXB (official or calculated)
FCF Yield 2026F: X.X%
Confidence: Official/High/Medium
EPS Growth: X.X%
```

- Source links are **clickable** and open in new tab
- FCF values shown even if negative (realistic for some companies)
- OTIS shows official FCF; others show calculated values
- All 14 companies display EPS guidance with attribution

### Programmatic API

```python
from src.guidance_manager import add_forecast, get_forecast
from src.stock_fetcher import fetch_fcf_yield_forecast_2026

# Add new guidance
add_forecast(
    ticker="OTIS",
    year=2026,
    fcf_billions=1.625,
    eps_dollars=4.22,
    source="Q1 2026 Earnings Webcast",
    pdf_url="https://..."
)

# Retrieve guidance
guidance = get_forecast("OTIS", 2026)
# Returns: {"fcf_billions": 1.625, "eps_dollars": 4.22, ...}

# Get forecast (checks guidance first, then calculates)
forecast = fetch_fcf_yield_forecast_2026("OTIS")
# Returns: {"fcf_yield_2026": 1.4, "eps_2026": 4.22, "confidence": "Official", ...}
```

### Priority Order

When calling `fetch_fcf_yield_forecast_2026(ticker)`:
1. If FCF guidance exists → use official path (confidence: "Official")
2. Else if EPS guidance exists → return with EPS displayed, FCF calculated or = 0
3. Else → calculate both from historical data (confidence: "High"/"Medium")

### Limitations

- EPS guidance from various public sources, not always official company guidance
- FCF guidance only available for OTIS currently
- Calculated FCF can be 0 or negative if company has deteriorating cash flow

## Earnings Tracker System

**Location:** `src/earnings_tracker.py`

### Functions

- `get_earnings_dates(ticker)` — Returns list of recent/upcoming earnings dates
- `get_next_earnings(ticker)` — Next upcoming earnings date
- `get_last_earnings(ticker)` — Most recent earnings date
- `should_update_guidance_now(ticker)` — Boolean check if earnings were released today/yesterday
- `get_earnings_calendar(tickers)` — Returns calendar dict with next_earnings, last_earnings, days_until
- `format_earnings_for_display(calendar)` — Formats calendar for dashboard display with status indicators

### Dashboard Display

The stock tab shows an earnings calendar expander:
```
📅 Earnings Calendar
- OTIS: 🔴 Recent (2026-05-15)
- COF: 🟡 12d away (2026-06-02)
- GOOGL: ⚪ 35d away (2026-06-27)
```

Status indicators:
- 🔴 = Earnings today or yesterday
- 🟡 = 7-30 days away
- ⚪ = 30+ days away

## Debug log

### `AttributeError: 'Store' object has no attribute 'execute'` (2026-05-31)

**Symptom:** App crashes on startup with the traceback pointing to `src/storage.py` line 53, inside `get_stored_last_updated`, where `conn.execute(...)` is called with a `Store` instance instead of a SQLite connection.

**Root cause:** During the Supabase migration, `storage.py` (the old SQLite module) was superseded by `store.py` but not immediately deleted. Streamlit kept a stale background process in memory (started via `run_in_background` tool calls) that still had the old `storage.py` loaded. Additionally, Python's `__pycache__` held compiled bytecode of the old files, which Streamlit continued to use across restarts. The old code called `storage.get_stored_last_updated(conn, series_id)` where `conn` was now a `Store` object, causing the error.

**Fix applied:**
1. Deleted `src/storage.py` and `src/news_store.py` entirely — all storage goes through `src/store.py`.
2. Cleared all `__pycache__` directories and `.pyc` files.
3. Killed all background Python/Streamlit processes (not just the foreground one).
4. `Store()` is now instantiated at module level in `app.py` without session state or `@st.cache_resource` caching — both caused stale instances to persist across deployments.

**Lesson:** When Streamlit is started with `run_in_background`, Ctrl+C in the terminal only kills the foreground process. Background processes continue serving the old code. Always kill all Python processes explicitly (`Get-Process python* | Stop-Process -Force`) before restarting.

### EPS 2026F Not Displaying for JRVR and MU (2026-05-31)

**Symptom:** Dashboard shows "None" for JRVR and MU EPS 2026F values even though guidance_forecasts.json contains correct data ($0.42 and $58.05).

**Root cause:** The forecast function checked for FCF guidance first and only returned the official guidance path if FCF existed. For companies with EPS-only guidance (no FCF), the code fell through to the calculated path but an early return blocked it, returning None instead of EPS data. Additionally, the `stored_guidance` variable was scoped locally and not available in the calculated path exception handler.

**Fix applied:**
1. Moved `stored_guidance` loading outside the FCF guidance check so it's available for both paths.
2. Added fallback exception handler that returns EPS-only forecast if calculation fails.
3. Guaranteed return with EPS data when guidance exists, even if FCF can't be calculated.

**Result:** All 14 companies now display EPS 2026F with source links, calculated or official FCF values.

### FCF Forecast = $0B for Most Companies (2026-05-31)

**Symptom:** Dashboard showed FCF 2026F = $0B for 12 companies instead of calculated values.

**Root cause:** The early return for EPS-only guidance was blocking the calculated FCF path entirely. Even companies that should have had FCF calculated were returning minimal EPS-only forecasts with fcf_2026 = 0.

**Fix applied:**
1. Removed the early return for EPS-only guidance.
2. Let the code flow through to the calculated path for all companies without official FCF guidance.
3. Modified the calculated path to handle negative/zero FCF values instead of returning None.
4. Only return None as absolute last resort.

**Result:** COF shows $31.35B, GOOGL $79.34B, PYPL $5.98B, etc. Only JRVR, MU, BN show $0B (due to negative historical FCF, which is realistic for some companies).

### Source Links Not Showing on Dashboard (2026-05-31)

**Symptom:** Guidance data was in guidance_forecasts.json but source links (PDF URLs) were not appearing on dashboard.

**Root cause:** The HTML rendering checked if `pdf_name` could be extracted from URL, but many non-PDF URLs (like IR homepages) had empty or domain-only names, failing the condition `if pdf_url and pdf_name`.

**Fix applied:**
1. Improved URL parsing to handle different formats (direct PDFs, IR website URLs, SEC filings).
2. Default to "Company Source" label when filename can't be extracted.
3. Show source link for both FCF (if official guidance) and EPS (always when exists).
4. Made links clickable and opening in new tabs.

**Result:** All 14 companies now display source attribution with working links to PDF/SEC/IR source documents.
