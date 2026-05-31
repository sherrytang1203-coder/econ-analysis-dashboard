import os
import time
import datetime
import requests
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv
from fredapi import Fred

load_dotenv(Path(__file__).parent.parent / ".env")


def get_fred_client() -> Fred | None:
    """Return a Fred client, or None if no API key is configured."""
    api_key = os.environ.get("FRED_API_KEY", "")
    if not api_key or api_key == "your_api_key_here":
        return None
    return Fred(api_key=api_key)


def fred_key_configured() -> bool:
    api_key = os.environ.get("FRED_API_KEY", "")
    return bool(api_key) and api_key != "your_api_key_here"


def fetch_series_info(fred: Fred | None, series_id: str) -> dict:
    if fred is None:
        return {}
    time.sleep(0.1)
    info = fred.get_series_info(series_id)
    return info.to_dict() if hasattr(info, "to_dict") else dict(info)


def fetch_series_last_updated(fred: Fred | None, series_id: str) -> str:
    """Return FRED's last_updated string, or a fallback:{today} marker when unavailable."""
    if fred is None:
        return f"fallback:{datetime.date.today().isoformat()}"
    try:
        info = fetch_series_info(fred, series_id)
        return str(info.get("last_updated", ""))
    except Exception:
        return f"fallback:{datetime.date.today().isoformat()}"


def fetch_observations(fred: Fred | None, series_id: str,
                       observation_start: str = None) -> pd.Series:
    """Fetch from FRED; automatically fall back to BLS or yfinance when unavailable."""
    if fred is not None:
        try:
            time.sleep(0.1)
            kwargs = {}
            if observation_start:
                kwargs["observation_start"] = observation_start
            series = fred.get_series(series_id, **kwargs)
            if series is not None and not series.empty:
                return series
        except Exception:
            pass

    return _fetch_fallback(series_id, observation_start)


def _fetch_fallback(series_id: str, observation_start: str = None) -> pd.Series:
    from src.config import YFINANCE_FALLBACK, BLS_FALLBACK
    if series_id in YFINANCE_FALLBACK:
        return _fetch_yfinance(YFINANCE_FALLBACK[series_id], observation_start)
    if series_id in BLS_FALLBACK:
        return _fetch_bls(BLS_FALLBACK[series_id], observation_start)
    return pd.Series(dtype=float)


def _fetch_bls(bls_series_id: str, observation_start: str = None) -> pd.Series:
    """Pull data from BLS public API v1 (no API key required)."""
    url = f"https://api.bls.gov/publicAPI/v1/timeseries/data/{bls_series_id}"
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("status") != "REQUEST_SUCCEEDED":
            return pd.Series(dtype=float)

        raw = payload["Results"]["series"][0]["data"]
        records = []
        for item in raw:
            period = item.get("period", "")
            try:
                value = float(item["value"])
            except (ValueError, KeyError):
                continue

            if period.startswith("M") and period != "M13":
                month = int(period[1:])
                date = pd.Timestamp(int(item["year"]), month, 1)
            elif period.startswith("Q"):
                month = (int(period[1:]) - 1) * 3 + 1
                date = pd.Timestamp(int(item["year"]), month, 1)
            else:
                continue
            records.append((date, value))

        if not records:
            return pd.Series(dtype=float)

        records.sort(key=lambda x: x[0])

        if observation_start:
            cutoff = pd.Timestamp(observation_start)
            records = [(d, v) for d, v in records if d >= cutoff]

        dates, values = zip(*records)
        return pd.Series(list(values), index=pd.DatetimeIndex(list(dates)))

    except Exception:
        return pd.Series(dtype=float)


def _fetch_yfinance(ticker: str, observation_start: str = None) -> pd.Series:
    try:
        import yfinance as yf
        kwargs = {"period": "max"} if not observation_start else {"start": observation_start}
        df = yf.download(ticker, progress=False, **kwargs)
        if df.empty:
            return pd.Series(dtype=float)
        close = df["Close"]
        if hasattr(close, "squeeze"):
            close = close.squeeze()
        close.index = pd.to_datetime(close.index).normalize()
        return close
    except Exception:
        return pd.Series(dtype=float)
