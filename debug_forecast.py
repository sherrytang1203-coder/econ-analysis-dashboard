import yfinance as yf
from src.guidance_manager import get_forecast

for ticker in ['JRVR', 'MU', 'BN']:
    print(f"\n=== {ticker} ===")

    # Check guidance
    stored = get_forecast(ticker, 2026)
    print(f"Stored guidance: {stored}")

    # Check yfinance data
    t = yf.Ticker(ticker)
    info = t.info

    current_mc = info.get("marketCap")
    trailing_eps = info.get("trailingEps")
    forward_eps = info.get("forwardEps")
    shares = info.get("sharesOutstanding")

    print(f"Market Cap: {current_mc}")
    print(f"Trailing EPS: {trailing_eps}")
    print(f"Forward EPS: {forward_eps}")
    print(f"Shares: {shares}")
    print(f"All required data present: {all([current_mc, trailing_eps, forward_eps, shares])}")

    # Check financials
    income_stmt = t.income_stmt
    cash_flow = t.cash_flow

    print(f"Income Stmt empty: {income_stmt.empty}")
    print(f"Cash Flow empty: {cash_flow.empty}")
