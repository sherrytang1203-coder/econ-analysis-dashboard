from src.stock_fetcher import fetch_fcf_yield_forecast_2026
from src.guidance_manager import get_forecast

tickers = ['COF', 'JRVR', 'MU', 'GOOGL', 'MKL', 'DEO', 'BAM', 'BN', 'PM', 'ULTA', 'MO', 'PYPL', 'OTIS', 'MRSH']

print("=== FCF Forecast Check ===\n")
print(f"{'Ticker':<8} {'FCF 2026':<12} {'EPS 2026':<10} {'Confidence':<12} {'Has FCF Guidance':<18}")
print("-" * 75)

for ticker in tickers:
    forecast = fetch_fcf_yield_forecast_2026(ticker)
    guidance = get_forecast(ticker, 2026)

    if forecast:
        fcf = forecast.get('fcf_2026', 0)
        eps = forecast.get('eps_2026', None)
        conf = forecast.get('confidence', 'N/A')
        has_fcf = guidance.get('fcf_billions') if guidance else False

        eps_str = f"${eps:.2f}" if eps else "N/A"
        fcf_str = f"${fcf:.2f}B" if fcf > 0 else "$0B"

        print(f"{ticker:<8} {fcf_str:<12} {eps_str:<10} {conf:<12} {str(bool(has_fcf)):<18}")
    else:
        print(f"{ticker:<8} {'None':<12} {'None':<10} {'None':<12} {'None':<18}")
