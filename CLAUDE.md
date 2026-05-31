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

## Guidance Extraction System

**Location:** `src/guidance_manager.py`, `src/ir_scrapers.py`, `scripts/discover_presentations.py`

### Overview

The guidance system allows you to extract **official FCF and EPS forecasts** from company investor relations presentations (PDF earnings calls, earnings announcements) and automatically use them to override calculated forecasts.

### How It Works

1. **Extract PDF Text** - `extract_fcf_from_pdf(pdf_url)` downloads and extracts text from earnings presentations
2. **Find Guidance Numbers** - Searches for keywords like "FCF guidance", "forecast", "2026", etc.
3. **Store Guidance** - `guidance_manager.py` stores official numbers in `data/guidance_forecasts.json`
4. **Auto-Apply** - When `fetch_fcf_yield_forecast_2026()` is called, it checks for stored guidance first

### Usage

**Option 1: Discover and store automatically**
```bash
python scripts/discover_presentations.py --discover
```

This finds known presentations (currently OTIS), extracts guidance, and stores it.

**Option 2: Add presentations manually**
```bash
python scripts/discover_presentations.py --add-manual "https://s203.q4cdn.com/.../1Q26-Otis-Earnings-Webcast.pdf"
```

The script will:
- Download the PDF
- Extract FCF-related text
- Show you the extracted sections
- Prompt you to enter the guidance amount (e.g., `1.65` for $1.65B)
- Store it automatically

**Option 3: Programmatic**
```python
from src.guidance_manager import add_forecast

# Store official guidance
add_forecast(
    ticker="OTIS",
    year=2026,
    fcf_billions=1.625,  # Midpoint of $1.6B - $1.65B guidance
    source="Q1 2026 Earnings Webcast (2026-05-02)",
    pdf_url="https://..."
)

# Forecast will now use this guidance automatically
from src.stock_fetcher import fetch_fcf_yield_forecast_2026
forecast = fetch_fcf_yield_forecast_2026("OTIS")
# Returns: {"confidence": "Official", "source": "Company guidance", ...}
```

### Finding Presentations

1. Navigate to company investor relations site (e.g., https://www.otisinvestors.com/)
2. Look for "Events & Presentations" or "Past Events"
3. Many sites use JavaScript to load content dynamically — you may need to:
   - Inspect Network tab (F12 → Network) to find PDF download URLs
   - Or manually right-click → Save presentation as
4. Copy the direct PDF URL and use `--add-manual`

### Storage Format

Guidance is stored in `data/guidance_forecasts.json`:
```json
{
  "OTIS": {
    "2026": {
      "fcf_billions": 1.625,
      "eps_dollars": null,
      "source": "Q1 2026 Earnings Webcast (2026-05-02)",
      "pdf_url": "https://..."
    }
  }
}
```

### Priority Order

When calling `fetch_fcf_yield_forecast_2026(ticker)`:
1. If `fcf_guidance_billions` parameter provided → use it
2. If stored guidance exists → use it (confidence: "Official")
3. Otherwise → calculate from historical data (confidence: "High"/"Medium")

### Limitations

- PDF extraction is text-only; complex tables may not extract cleanly
- Guidance is manual — requires you to identify and enter the numbers
- Works best with plain-English guidance like "$1.6B to 1.65B FCF guidance"

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
