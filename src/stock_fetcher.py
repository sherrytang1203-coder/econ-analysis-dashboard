import pandas as pd
import yfinance as yf


def fetch_stock_ohlcv(ticker: str, period: str = "5y") -> pd.DataFrame:
    """Return DataFrame with columns: date, open, high, low, close."""
    try:
        df = yf.download(ticker, period=period, progress=False, auto_adjust=True)
        if df.empty:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close"])
        result = pd.DataFrame({
            "date":  pd.to_datetime(df.index).normalize(),
            "open":  (df["Open"].iloc[:, 0]  if isinstance(df["Open"],  pd.DataFrame) else df["Open"]).values,
            "high":  (df["High"].iloc[:, 0]  if isinstance(df["High"],  pd.DataFrame) else df["High"]).values,
            "low":   (df["Low"].iloc[:, 0]   if isinstance(df["Low"],   pd.DataFrame) else df["Low"]).values,
            "close": (df["Close"].iloc[:, 0] if isinstance(df["Close"], pd.DataFrame) else df["Close"]).values,
        })
        return result.dropna().reset_index(drop=True)
    except Exception:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close"])


def fetch_stock_price(ticker: str, period: str = "5y") -> pd.DataFrame:
    """Return DataFrame with columns: date, close."""
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


def fetch_stock_info(ticker: str) -> dict:
    """Return key current metrics."""
    try:
        info = yf.Ticker(ticker).info
        return {
            "name":        info.get("longName", ticker),
            "sector":      info.get("sector", ""),
            "industry":    info.get("industry", ""),
            "market_cap":  info.get("marketCap"),
            "pe_ratio":    info.get("trailingPE"),
            "forward_pe":  info.get("forwardPE"),
            "eps":         info.get("trailingEps"),
            "52w_high":    info.get("fiftyTwoWeekHigh"),
            "52w_low":     info.get("fiftyTwoWeekLow"),
        }
    except Exception:
        return {"name": ticker}


def fetch_revenue(ticker: str) -> pd.DataFrame:
    """Return annual revenue in billions as DataFrame with columns: year, revenue."""
    t = yf.Ticker(ticker)
    fins = None
    for attr in ("income_stmt", "financials"):
        try:
            fins = getattr(t, attr)
            if fins is not None and not fins.empty:
                break
        except Exception:
            continue

    if fins is None or fins.empty:
        return pd.DataFrame(columns=["year", "revenue"])

    rev_row = None
    for label in ("Total Revenue", "TotalRevenue", "Revenue"):
        if label in fins.index:
            rev_row = label
            break

    if rev_row is None:
        return pd.DataFrame(columns=["year", "revenue"])

    rev = fins.loc[rev_row].dropna()
    df = pd.DataFrame({
        "year":    [pd.Timestamp(d).year for d in rev.index],
        "revenue": [v / 1e9 for v in rev.values],  # convert to billions
    })
    return df.sort_values("year").reset_index(drop=True)


def fetch_pe_history(ticker: str) -> pd.DataFrame:
    """
    Compute annual historical P/E = year-end price / annual EPS.
    Returns DataFrame with columns: year, pe.
    """
    t = yf.Ticker(ticker)

    # --- annual net income ---
    fins = None
    for attr in ("income_stmt", "financials"):
        try:
            fins = getattr(t, attr)
            if fins is not None and not fins.empty:
                break
        except Exception:
            continue

    if fins is None or fins.empty:
        return pd.DataFrame(columns=["year", "pe"])

    ni_row = None
    for label in ("Net Income", "NetIncome", "Net Income Common Stockholders"):
        if label in fins.index:
            ni_row = label
            break

    if ni_row is None:
        return pd.DataFrame(columns=["year", "pe"])

    net_income = fins.loc[ni_row].dropna()

    # --- shares outstanding ---
    shares = None
    try:
        shares = t.info.get("sharesOutstanding")
    except Exception:
        pass
    if not shares:
        try:
            shares = t.fast_info.get("shares")
        except Exception:
            pass
    if not shares or shares == 0:
        return pd.DataFrame(columns=["year", "pe"])

    # --- year-end prices ---
    try:
        price_hist = yf.download(ticker, period="max", progress=False,
                                 auto_adjust=True)["Close"]
        if isinstance(price_hist, pd.DataFrame):
            price_hist = price_hist.iloc[:, 0]
        price_hist.index = pd.to_datetime(price_hist.index).normalize()
    except Exception:
        return pd.DataFrame(columns=["year", "pe"])

    records = []
    for date, ni in net_income.items():
        if ni <= 0:
            continue
        eps = ni / shares
        year = pd.Timestamp(date).year
        # price at year end (Dec 31, or last available trading day)
        year_end = pd.Timestamp(f"{year}-12-31")
        nearby = price_hist[price_hist.index <= year_end]
        if nearby.empty:
            continue
        price = float(nearby.iloc[-1])
        pe = price / eps
        if 0 < pe < 500:
            records.append({"year": year, "pe": round(pe, 1)})

    return (pd.DataFrame(records).sort_values("year").reset_index(drop=True)
            if records else pd.DataFrame(columns=["year", "pe"]))


def fetch_loss_years(ticker: str) -> list[int]:
    """Return list of years where net income was negative (P/E undefined)."""
    t = yf.Ticker(ticker)
    fins = None
    for attr in ("income_stmt", "financials"):
        try:
            fins = getattr(t, attr)
            if fins is not None and not fins.empty:
                break
        except Exception:
            continue
    if fins is None or fins.empty:
        return []
    ni_row = None
    for label in ("Net Income", "NetIncome", "Net Income Common Stockholders"):
        if label in fins.index:
            ni_row = label
            break
    if ni_row is None:
        return []
    return sorted([
        pd.Timestamp(d).year
        for d, v in fins.loc[ni_row].dropna().items()
        if v <= 0
    ])


def fetch_fcf_history(ticker: str) -> pd.DataFrame:
    """Return annual FCF yield history. Columns: year, fcf_b (billions), fcf_yield (%)."""
    try:
        t = yf.Ticker(ticker)

        cf = None
        for attr in ("cashflow", "cash_flow"):
            try:
                cf = getattr(t, attr)
                if cf is not None and not cf.empty:
                    break
            except Exception:
                continue
        if cf is None or cf.empty:
            return pd.DataFrame(columns=["year", "fcf_b", "fcf_yield"])

        fcf_row = None
        for label in ("Free Cash Flow", "FreeCashFlow"):
            if label in cf.index:
                fcf_row = label
                break
        if fcf_row is None:
            return pd.DataFrame(columns=["year", "fcf_b", "fcf_yield"])

        fcf_series = cf.loc[fcf_row].dropna()

        shares = t.info.get("sharesOutstanding")
        if not shares:
            try:
                shares = t.fast_info.get("shares")
            except Exception:
                pass

        try:
            price_hist = yf.download(ticker, period="max", progress=False, auto_adjust=True)["Close"]
            if isinstance(price_hist, pd.DataFrame):
                price_hist = price_hist.iloc[:, 0]
            price_hist.index = pd.to_datetime(price_hist.index).normalize()
        except Exception:
            price_hist = None

        records = []
        for date, fcf_val in fcf_series.items():
            year = pd.Timestamp(date).year
            fcf_yield = None
            if shares and price_hist is not None:
                year_end = pd.Timestamp(f"{year}-12-31")
                nearby = price_hist[price_hist.index <= year_end]
                if not nearby.empty:
                    mc = float(nearby.iloc[-1]) * shares
                    if mc > 0:
                        fcf_yield = round((fcf_val / mc) * 100, 2)
            records.append({"year": year, "fcf_b": round(fcf_val / 1e9, 2), "fcf_yield": fcf_yield})

        return pd.DataFrame(records).sort_values("year").reset_index(drop=True)
    except Exception:
        return pd.DataFrame(columns=["year", "fcf_b", "fcf_yield"])


def fetch_fcf_yield(ticker: str) -> float | None:
    """Return Free Cash Flow Yield as a percentage (FCF / Market Cap * 100)."""
    try:
        t = yf.Ticker(ticker)
        mc = t.info.get("marketCap")
        if not mc:
            return None

        cf = None
        for attr in ("cashflow", "cash_flow"):
            try:
                cf = getattr(t, attr)
                if cf is not None and not cf.empty:
                    break
            except Exception:
                continue
        if cf is None or cf.empty:
            return None

        # Try direct FCF row first
        for label in ("Free Cash Flow", "FreeCashFlow"):
            if label in cf.index:
                vals = cf.loc[label].dropna()
                if not vals.empty:
                    return (float(vals.iloc[0]) / mc) * 100

        # Fall back to Operating Cash Flow - CapEx
        ocf_row = None
        for label in ("Operating Cash Flow", "Total Cash From Operating Activities",
                      "Cash From Operations"):
            if label in cf.index:
                ocf_row = label
                break
        if ocf_row is None:
            return None

        ocf_vals = cf.loc[ocf_row].dropna()
        if ocf_vals.empty:
            return None
        ocf = float(ocf_vals.iloc[0])

        capex = 0.0
        for label in ("Capital Expenditure", "Capital Expenditures",
                      "Purchase Of Property Plant And Equipment"):
            if label in cf.index:
                capex_vals = cf.loc[label].dropna()
                if not capex_vals.empty:
                    capex = float(capex_vals.iloc[0])
                break

        # CapEx is reported as a negative value in yfinance cash flow statements
        return ((ocf + capex) / mc) * 100
    except Exception:
        return None


def fetch_rsi(ticker: str, window: int = 14, weekly: bool = False) -> pd.DataFrame:
    """Return RSI time series with columns: date, rsi (2-year lookback)."""
    try:
        interval = "1wk" if weekly else "1d"
        df = yf.download(ticker, period="2y", interval=interval,
                         progress=False, auto_adjust=True)
        if df.empty:
            return pd.DataFrame(columns=["date", "rsi"])

        close = df["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]

        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)

        # Wilder's smoothing (standard RSI)
        avg_gain = gain.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()

        rs = avg_gain / avg_loss.replace(0, float("nan"))
        rsi = 100 - (100 / (1 + rs))

        rsi_df = pd.DataFrame({"date": rsi.index, "rsi": rsi.values})
        rsi_df["date"] = pd.to_datetime(rsi_df["date"]).dt.normalize()
        return rsi_df.dropna().reset_index(drop=True)
    except Exception:
        return pd.DataFrame(columns=["date", "rsi"])
