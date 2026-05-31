#!/usr/bin/env python3
"""
Discover and add presentations from OTIS investor relations website.

Since the OTIS IR site uses JavaScript to load events dynamically,
this script helps you discover presentation URLs and add them to the guidance system.

Usage:
    python scripts/discover_presentations.py
    python scripts/discover_presentations.py --ticker OTIS --year 2026 --q 1
    python scripts/discover_presentations.py --add-manual "https://..."
"""

import sys
import json
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ir_scrapers import scrape_otis_guidance, get_presentation_pdf_url
from src.guidance_manager import add_forecast, get_guidance_summary, list_all_guidance
from src.stock_fetcher import extract_fcf_from_pdf


def discover_otis():
    """Discover and extract guidance from known OTIS presentations."""
    print("\n🔍 Discovering OTIS presentations...\n")
    results = scrape_otis_guidance(auto_store=True)

    print(f"Found {results['presentations_found']} presentations")
    print(f"Extracted {len(results['guidance_extracted'])} guidance records\n")

    if results['guidance_extracted']:
        print("✓ Guidance Extracted:")
        for g in results['guidance_extracted']:
            print(f"  • {g['presentation']} ({g['date']})")
            print(f"    FCF: {g['fcf_guidance_range']} → ${g['fcf_guidance_midpoint']}B")

    if results['errors']:
        print("\n✗ Errors:")
        for e in results['errors']:
            print(f"  • {e['presentation']}: {e['error']}")

    return results


def add_manual_presentation(pdf_url: str, ticker: str = "OTIS", year: int = 2026):
    """
    Add a presentation PDF URL manually.

    This will:
    1. Download and extract text from the PDF
    2. Search for FCF guidance in the text
    3. Prompt you to confirm the guidance amount
    4. Store in guidance_manager
    """
    print(f"\n📄 Adding presentation: {pdf_url}\n")

    # Extract from PDF
    print("Extracting FCF data from PDF...")
    pdf_data = extract_fcf_from_pdf(pdf_url)

    if not pdf_data or "error" in pdf_data:
        print(f"❌ Error: {pdf_data.get('error', 'Unknown')}")
        return False

    print("\n📊 Extracted Information:")
    print("\nFCF Sections Found:")
    print("-" * 50)
    print(pdf_data.get("fcf_sections", "No FCF sections found")[:1000])

    print("\n\nDollar Amounts Found:")
    amounts = pdf_data.get("dollar_amounts", [])
    for amt in amounts[:15]:
        print(f"  {amt}")

    print("\n\nPercentages Found:")
    percs = pdf_data.get("percentages", [])
    for perc in percs[:10]:
        print(f"  {perc}")

    # Prompt for FCF guidance
    print("\n" + "=" * 50)
    fcf_input = input(f"\nEnter FCF guidance for {year} (in billions, e.g., 1.65): ").strip()

    try:
        fcf_billions = float(fcf_input)
    except ValueError:
        print("❌ Invalid input")
        return False

    # Store it
    success = add_forecast(
        ticker=ticker,
        year=year,
        fcf_billions=fcf_billions,
        source=f"Presentation: {pdf_url}",
        pdf_url=pdf_url
    )

    if success:
        print(f"\n✓ Stored: {ticker} {year} FCF = ${fcf_billions}B")
        return True
    else:
        print("❌ Failed to store guidance")
        return False


def show_summary():
    """Show summary of all stored guidance."""
    print("\n📋 Stored Guidance Summary:\n")
    all_guidance = list_all_guidance()

    if not all_guidance:
        print("No guidance data found. Run discovery first!")
        return

    for ticker, years in all_guidance.items():
        print(f"\n{ticker}:")
        for year, data in years.items():
            fcf = data.get("fcf_billions", "N/A")
            eps = data.get("eps_dollars", "N/A")
            source = data.get("source", "Unknown")
            print(f"  {year}: FCF ${fcf}B, EPS ${eps} | {source}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Discover and add presentations from OTIS IR website")
    parser.add_argument("--discover", action="store_true", help="Discover presentations from OTIS")
    parser.add_argument("--add-manual", type=str, help="Add a presentation PDF URL manually")
    parser.add_argument("--summary", action="store_true", help="Show all stored guidance")
    parser.add_argument("--ticker", type=str, default="OTIS", help="Stock ticker")
    parser.add_argument("--year", type=int, default=2026, help="Forecast year")

    args = parser.parse_args()

    if args.summary or not any([args.discover, args.add_manual]):
        show_summary()
    elif args.discover:
        discover_otis()
    elif args.add_manual:
        add_manual_presentation(args.add_manual, ticker=args.ticker, year=args.year)


if __name__ == "__main__":
    main()
