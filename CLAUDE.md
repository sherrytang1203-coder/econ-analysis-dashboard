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
