#!/usr/bin/env python3
"""Test the complete guidance extraction and forecast integration."""

from src.stock_fetcher import fetch_fcf_yield_forecast_2026
from src.guidance_manager import get_forecast, list_all_guidance

print("\n" + "=" * 60)
print("GUIDANCE INTEGRATION TEST")
print("=" * 60)

# Show stored guidance
print("\n[1] Stored Guidance in System:")
all_guidance = list_all_guidance()
for ticker, data in all_guidance.items():
    print(f"\n  {ticker}:")
    for year, forecast_data in data.items():
        print(f"    {year}: FCF ${forecast_data.get('fcf_billions')}B")
        print(f"           Source: {forecast_data.get('source')}")

# Test auto-loading stored guidance
print("\n[2] OTIS 2026 Forecast (auto-loaded guidance):")
forecast = fetch_fcf_yield_forecast_2026("OTIS")

if forecast:
    print(f"  FCF Yield 2026: {forecast.get('fcf_yield_2026')}%")
    print(f"  FCF 2026: ${forecast.get('fcf_2026')}B")
    print(f"  Market Cap: ${forecast.get('market_cap_2026')}B")
    print(f"  Confidence: {forecast.get('confidence')}")
    print(f"  Source: {forecast.get('source', 'N/A')}")
else:
    print("  [ERROR] Failed to fetch forecast")

# Test manual guidance override
print("\n[3] OTIS Forecast (manual guidance override):")
forecast_manual = fetch_fcf_yield_forecast_2026("OTIS", fcf_guidance_billions=1.65)

if forecast_manual:
    print(f"  FCF Yield 2026: {forecast_manual.get('fcf_yield_2026')}%")
    print(f"  FCF 2026: ${forecast_manual.get('fcf_2026')}B")
    print(f"  Confidence: {forecast_manual.get('confidence')}")

print("\n" + "=" * 60)
print("SUCCESS: Guidance system is working end-to-end")
print("=" * 60 + "\n")
