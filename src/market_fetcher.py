import pandas as pd
import yfinance as yf

# Maps UI period label → yfinance period string
_PERIOD_MAP = {
    "1D": "5d",
    "1M": "1mo",
    "6M": "6mo",
    "1Y": "1y",
    "2Y": "2y",
}


def fetch_sector_performance(tickers: dict[str, str], period: str = "1D") -> pd.DataFrame:
    """
    Return sector % change over the selected period.
    For 1D: last close vs previous close.
    For all others: last close vs first close in the downloaded window.
    """
    symbols = list(tickers.keys())
    yf_period = _PERIOD_MAP.get(period, "5d")
    try:
        raw = yf.download(symbols, period=yf_period, progress=False, auto_adjust=True)
    except Exception:
        return pd.DataFrame(columns=["ticker", "name", "current", "base", "pct_change"])

    if isinstance(raw.columns, pd.MultiIndex):
        close = raw["Close"]
    else:
        close = raw[["Close"]].rename(columns={"Close": symbols[0]})

    rows = []
    for sym, name in tickers.items():
        if sym not in close.columns:
            continue
        series = close[sym].dropna()
        if len(series) < 2:
            continue
        current = float(series.iloc[-1])
        base    = float(series.iloc[-2] if period == "1D" else series.iloc[0])
        pct_change = (current - base) / abs(base) * 100
        rows.append({"ticker": sym, "name": name,
                     "current": current, "base": base, "pct_change": pct_change})

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("pct_change", ascending=False).reset_index(drop=True)
    return df


def fetch_market_snapshot(tickers: dict[str, str]) -> pd.DataFrame:
    """
    Return a DataFrame with columns: ticker, name, current, prev_close, pct_change.
    Uses 5-day window to ensure at least 2 trading days are available.
    """
    symbols = list(tickers.keys())
    try:
        raw = yf.download(symbols, period="5d", progress=False, auto_adjust=True)
    except Exception:
        return pd.DataFrame(columns=["ticker", "name", "current", "prev_close", "pct_change"])

    # yfinance returns MultiIndex columns when >1 ticker, flat when ==1
    if isinstance(raw.columns, pd.MultiIndex):
        close = raw["Close"]
    else:
        close = raw[["Close"]].rename(columns={"Close": symbols[0]})

    rows = []
    for sym, name in tickers.items():
        if sym not in close.columns:
            continue
        series = close[sym].dropna()
        if len(series) < 2:
            continue
        current    = float(series.iloc[-1])
        prev_close = float(series.iloc[-2])
        pct_change = (current - prev_close) / abs(prev_close) * 100
        rows.append({
            "ticker":     sym,
            "name":       name,
            "current":    current,
            "prev_close": prev_close,
            "pct_change": pct_change,
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("pct_change", ascending=False).reset_index(drop=True)
    return df


def fetch_historical(ticker: str, period: str = "2y") -> pd.DataFrame:
    """Return a date-indexed DataFrame with a 'close' column for charting."""
    try:
        df = yf.download(ticker, period=period, progress=False, auto_adjust=True)
        if df.empty:
            return pd.DataFrame(columns=["date", "close"])
        close = df["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        result = close.reset_index()
        result.columns = ["date", "close"]
        result["date"] = pd.to_datetime(result["date"]).dt.normalize()
        return result.dropna()
    except Exception:
        return pd.DataFrame(columns=["date", "close"])
