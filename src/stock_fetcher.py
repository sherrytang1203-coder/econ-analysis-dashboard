import pandas as pd
import yfinance as yf
import requests
import re


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


def fetch_fcf_yield_forecast_2026(ticker: str) -> dict | None:
    """
    Forecast 2026 Free Cash Flow Yield using fundamental hybrid approach.

    Methodology:
    1. Get analyst consensus EPS growth rate for 2026
    2. Calculate historical FCF margin (FCF / Revenue) over 3 years
    3. Calculate historical CapEx as % of revenue over 3 years
    4. Project 2026 FCF = 2026 Revenue × FCF Margin
    5. Project 2026 Market Cap = Current Market Cap × (1 + EPS growth)
    6. Calculate 2026 FCF Yield = 2026 FCF / 2026 Market Cap

    Returns:
        {
            "fcf_yield_2026": float (%),
            "fcf_2026": float (dollars),
            "market_cap_2026": float (dollars),
            "eps_growth_rate": float (%),
            "fcf_margin_hist": float (% of revenue),
            "capex_margin_hist": float (% of revenue),
            "confidence": str ("High" / "Medium" / "Low")
        }
    """
    try:
        t = yf.Ticker(ticker)
        info = t.info

        # Current metrics
        current_mc = info.get("marketCap")
        trailing_eps = info.get("trailingEps")
        forward_eps = info.get("forwardEps")
        shares = info.get("sharesOutstanding")

        if not all([current_mc, trailing_eps, forward_eps, shares]):
            return None

        # Calculate EPS growth rate (forward vs trailing)
        eps_growth = (forward_eps - trailing_eps) / abs(trailing_eps) if trailing_eps != 0 else 0

        # Get historical financials for margin calculations (3-year average)
        try:
            income_stmt = t.income_stmt
            cash_flow = t.cash_flow

            if income_stmt.empty or cash_flow.empty:
                return None

            # Extract revenue (most recent 3 years)
            revenue_col = None
            for col_name in ["Total Revenue", "TotalRevenue", "Revenue"]:
                if col_name in income_stmt.index:
                    revenue_col = col_name
                    break

            if not revenue_col:
                return None

            revenues = income_stmt.loc[revenue_col].dropna().head(3)
            if revenues.empty:
                return None

            avg_revenue = float(revenues.mean())

            # Extract FCF or calculate from OCF - CapEx
            fcf_col = None
            for col_name in ["Free Cash Flow", "FreeCashFlow"]:
                if col_name in cash_flow.index:
                    fcf_col = col_name
                    break

            if fcf_col:
                fcfs = cash_flow.loc[fcf_col].dropna().head(3)
                avg_fcf = float(fcfs.mean()) if not fcfs.empty else None
            else:
                # Calculate FCF from OCF - CapEx
                ocf_col = None
                for col_name in ["Operating Cash Flow", "Total Cash From Operating Activities"]:
                    if col_name in cash_flow.index:
                        ocf_col = col_name
                        break

                capex_col = None
                for col_name in ["Capital Expenditure", "Capital Expenditures"]:
                    if col_name in cash_flow.index:
                        capex_col = col_name
                        break

                if ocf_col and capex_col:
                    ocfs = cash_flow.loc[ocf_col].dropna().head(3)
                    capexs = cash_flow.loc[capex_col].dropna().head(3)
                    if not ocfs.empty and not capexs.empty:
                        avg_fcf = float((ocfs + capexs).mean())  # CapEx is negative
                    else:
                        avg_fcf = None
                else:
                    avg_fcf = None

            if not avg_fcf or avg_fcf <= 0:
                return None

            # Calculate FCF margin and CapEx margin
            fcf_margin = avg_fcf / avg_revenue if avg_revenue > 0 else 0

            # CapEx as % of revenue
            capex_col = None
            for col_name in ["Capital Expenditure", "Capital Expenditures"]:
                if col_name in cash_flow.index:
                    capex_col = col_name
                    break

            if capex_col:
                capexs = cash_flow.loc[capex_col].dropna().head(3)
                avg_capex = float(abs(capexs.mean())) if not capexs.empty else 0
                capex_margin = avg_capex / avg_revenue if avg_revenue > 0 else 0
            else:
                capex_margin = 0

            # Project 2026 metrics
            # Revenue growth ≈ EPS growth (simplified assumption)
            revenue_growth = eps_growth
            fcf_2026 = avg_revenue * (1 + revenue_growth) * fcf_margin

            # Use current market cap (no growth assumption)
            mc_2026 = current_mc

            # Calculate 2026 FCF Yield
            fcf_yield_2026 = (fcf_2026 / mc_2026 * 100) if mc_2026 > 0 else None

            if not fcf_yield_2026:
                return None

            # Confidence level based on data completeness
            confidence = "High" if all([fcf_col, avg_fcf > 0]) else "Medium"

            return {
                "fcf_yield_2026": round(fcf_yield_2026, 2),
                "fcf_2026": round(fcf_2026, 0),
                "market_cap_2026": round(mc_2026, 0),
                "eps_growth_rate": round(eps_growth * 100, 2),
                "fcf_margin_hist": round(fcf_margin * 100, 2),
                "capex_margin_hist": round(capex_margin * 100, 2),
                "confidence": confidence,
            }

        except Exception:
            return None

    except Exception:
        return None


def extract_fcf_from_pdf(pdf_url: str) -> dict | None:
    """
    Extract Free Cash Flow information from earnings presentation PDF.

    Searches for FCF-specific keywords and extracts:
    - FCF amounts (historical and guidance)
    - FCF margins and trends
    - Capital expenditure information
    - Operating cash flow

    Returns:
        {
            "fcf_current": str (current/latest FCF amount),
            "fcf_guidance": str (forward FCF guidance),
            "fcf_margin": str (FCF as % of revenue),
            "fcf_sections": str (relevant text snippets),
            "numbers_found": list (all extracted $ amounts and %),
            "raw_text": str (full text for manual review)
        }
    """
    try:
        import pdfplumber
        import io

        # Download PDF
        resp = requests.get(pdf_url, timeout=10)
        if resp.status_code != 200:
            return {"error": "Failed to download PDF"}

        # Extract text from PDF
        pdf_bytes = resp.content
        text = ""

        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages[:15]:  # First 15 pages
                text += page.extract_text() or ""

        if not text:
            return {"error": "Could not extract text from PDF"}

        # Search for FCF-specific sections
        fcf_keywords = [
            "free cash flow", "fcf", "operating cash flow", "capital expenditure",
            "capex", "cash from operations", "fcf guidance", "fcf outlook",
            "fcf conversion", "fcf margin", "cash generation"
        ]

        fcf_sections = ""
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if any(kw.lower() in line.lower() for kw in fcf_keywords):
                # Capture context
                start = max(0, i - 3)
                end = min(len(lines), i + 12)
                fcf_sections += "\n".join(lines[start:end]) + "\n\n"

        # Extract numbers: currency amounts, percentages
        dollar_amounts = re.findall(r'\$[\d,.]+\s*[BM]?', text)
        percentages = re.findall(r'\d+[.]\d+%|\d+%', text)

        return {
            "fcf_current": "See fcf_sections",
            "fcf_guidance": "See fcf_sections",
            "fcf_margin": "See fcf_sections",
            "fcf_sections": fcf_sections[:3000],  # Relevant FCF text
            "dollar_amounts": list(set(dollar_amounts))[:20],  # Top amounts found
            "percentages": list(set(percentages))[:10],  # Percentages found
            "raw_text": text,  # Full text for review
        }

    except ImportError:
        return {"error": "pdfplumber not installed. Run: pip install pdfplumber"}
    except Exception as e:
        return {"error": str(e)}


def extract_guidance_from_pdf(pdf_url: str) -> dict | None:
    """
    Extract forward guidance (earnings, FCF) from earnings presentation PDF.

    Searches for keywords like "guidance", "outlook", "forecast", "2026", "2027"
    and extracts associated numbers.

    Returns:
        {
            "earnings_guidance": str (e.g., "5-10% growth"),
            "fcf_guidance": str (e.g., "$2.5B"),
            "revenue_guidance": str,
            "raw_text": str (first 5000 chars of PDF for manual review)
        }
    """
    try:
        import pdfplumber
        import io

        # Download PDF
        resp = requests.get(pdf_url, timeout=10)
        if resp.status_code != 200:
            return None

        # Extract text from PDF
        pdf_bytes = resp.content
        text = ""

        # Use pdfplumber to extract text
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages[:10]:  # First 10 pages only
                text += page.extract_text() or ""

        if not text:
            return None

        # Search for guidance sections
        guidance_section = ""
        keywords = ["guidance", "outlook", "forecast", "2026", "2027", "full year"]

        lines = text.split("\n")
        for i, line in enumerate(lines):
            if any(kw.lower() in line.lower() for kw in keywords):
                # Capture surrounding context (5 lines before and after)
                start = max(0, i - 5)
                end = min(len(lines), i + 15)
                guidance_section += "\n".join(lines[start:end]) + "\n---\n"

        # Try to extract numbers (revenue, earnings, FCF)
        numbers = re.findall(r'\$[\d,.]+[BM]?|\d+[.]\d+%', guidance_section)

        return {
            "earnings_guidance": "See raw_text",
            "fcf_guidance": "See raw_text",
            "numbers_found": numbers,
            "guidance_section": guidance_section[:2000],  # First 2000 chars
            "raw_text": text[:3000],  # First 3000 chars for review
        }

    except ImportError:
        return {"error": "pdfplumber not installed. Run: pip install pdfplumber"}
    except Exception as e:
        return {"error": str(e)}


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
