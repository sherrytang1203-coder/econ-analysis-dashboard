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
from src.guidance_manager import add_forecast, get_guidance_summary, list_all_guidance, get_csv_summary, read_guidance_csv
from src.stock_fetcher import extract_fcf_from_pdf
from src.ir_config import (
    load_ir_presentations, add_ir_presentation, batch_process_all_presentations,
    get_ir_config_summary, process_ir_presentation
)
from src.ir_discovery import (
    discover_and_add_pdfs, load_ir_registry, get_ir_registry_summary, manual_add_pdf
)


def discover_otis():
    """Discover and extract guidance from known OTIS presentations."""
    print("\n[*] Discovering OTIS presentations...\n")
    results = scrape_otis_guidance(auto_store=True)

    print(f"Found {results['presentations_found']} presentations")
    print(f"Extracted {len(results['guidance_extracted'])} guidance records\n")

    if results['guidance_extracted']:
        print("[+] Guidance Extracted:")
        for g in results['guidance_extracted']:
            print(f"  * {g['presentation']} ({g['date']})")
            print(f"    FCF: {g['fcf_guidance_range']} -> ${g['fcf_guidance_midpoint']}B")

    if results['errors']:
        print("\n[-] Errors:")
        for e in results['errors']:
            print(f"  * {e['presentation']}: {e['error']}")

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
    print(f"\n[PDF] Adding presentation: {pdf_url}\n")

    # Extract from PDF
    print("Extracting FCF data from PDF...")
    pdf_data = extract_fcf_from_pdf(pdf_url)

    if not pdf_data or "error" in pdf_data:
        print(f"[ERROR] {pdf_data.get('error', 'Unknown')}")
        return False

    print("\n[INFO] Extracted Information:")
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
        print("[ERROR] Invalid input")
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
        print(f"\n[OK] Stored: {ticker} {year} FCF = ${fcf_billions}B")
        return True
    else:
        print("[ERROR] Failed to store guidance")
        return False


def show_summary():
    """Show summary of all stored guidance (JSON + CSV)."""
    print("\n[SUMMARY] Current Guidance (from JSON):\n")
    all_guidance = list_all_guidance()

    if not all_guidance:
        print("No guidance data found. Run discovery first!")
    else:
        for ticker, years in all_guidance.items():
            print(f"\n{ticker}:")
            for year, data in years.items():
                fcf = data.get("fcf_billions", "N/A")
                eps = data.get("eps_dollars", "N/A")
                source = data.get("source", "Unknown")
                print(f"  {year}: FCF ${fcf}B, EPS ${eps} | {source}")

    # Show CSV history
    print(get_csv_summary())


def batch_process():
    """Process all presentations from ir_presentations.csv config."""
    print("\n[*] Batch processing all presentations from config...\n")
    results = batch_process_all_presentations()

    print("\n" + "=" * 70)
    print("[BATCH RESULTS]")
    print("=" * 70)

    success_count = sum(1 for r in results if r["status"] == "success")
    error_count = sum(1 for r in results if r["status"] == "error")
    no_guidance = sum(1 for r in results if r["status"] == "no_guidance")

    print(f"\nProcessed: {len(results)} presentations")
    print(f"  [OK] {success_count} successfully extracted")
    print(f"  [WARN] {no_guidance} no guidance found")
    print(f"  [ERROR] {error_count} errors")

    for result in results:
        if result["status"] == "success":
            print(f"\n[+] {result['company']}:")
            print(f"    FCF: ${result['fcf_min']}B - ${result['fcf_max']}B (${result['fcf_midpoint']}B midpoint)")
            if result["eps_midpoint"]:
                print(f"    EPS: ${result['eps_min']} - ${result['eps_max']} (${result['eps_midpoint']} midpoint)")


def show_ir_config():
    """Show current IR presentations configuration."""
    print(get_ir_config_summary())


def add_to_config():
    """Interactively add a presentation to the config."""
    print("\n[+] Add new IR presentation to config\n")

    company = input("Company ticker (e.g., OTIS): ").strip().upper()
    url = input("Presentation PDF URL: ").strip()
    description = input("Description (optional, e.g., 'Q1 2026 Earnings'): ").strip()

    if not company or not url:
        print("[ERROR] Company and URL are required")
        return

    success = add_ir_presentation(company, url, description)

    if success:
        print(f"[OK] Added {company} to ir_presentations.csv")
    else:
        print("[ERROR] Failed to add presentation")


def discover_from_ir_website():
    """Discover PDFs from investor relations website."""
    print("\n[*] Discover PDFs from IR website\n")

    company = input("Company ticker (e.g., OTIS): ").strip().upper()
    ir_url = input("IR website URL (e.g., https://www.otisinvestors.com/...): ").strip()

    if not company or not ir_url:
        print("[ERROR] Company and URL required")
        return

    result = discover_and_add_pdfs(company, ir_url)

    if result["status"] == "success":
        print(f"\n[OK] Discovered {len(result['pdfs_found'])} PDF(s)")
        print("Updated ir_presentations.csv")
    else:
        print(f"\n[!] Status: {result['status']}")
        if result["error"]:
            print(f"Error: {result['error']}")
        print("\nTry manually adding PDFs:")
        print(f"  python scripts/discover_presentations.py --add-pdf-manual")


def add_pdf_manually():
    """Manually add a PDF URL when auto-discovery fails."""
    print("\n[+] Manually add PDF URL\n")

    company = input("Company ticker (e.g., OTIS): ").strip().upper()
    ir_url = input("IR website URL: ").strip()
    pdf_url = input("PDF URL (copy from browser): ").strip()

    if not all([company, ir_url, pdf_url]):
        print("[ERROR] All fields required")
        return

    success = manual_add_pdf(company, ir_url, pdf_url)

    if success:
        print(f"[OK] Added PDF for {company}")
    else:
        print("[ERROR] Failed to add PDF")


def show_ir_registry():
    """Show IR website registry."""
    print(get_ir_registry_summary())


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Discover and extract guidance from IR presentations")

    # IR Discovery (new)
    parser.add_argument("--discover-ir", action="store_true", help="Discover PDFs from IR website URL")
    parser.add_argument("--ir-registry", action="store_true", help="Show IR website registry")
    parser.add_argument("--add-pdf-manual", action="store_true", help="Manually add PDF URL")

    # Legacy/Original discovery
    parser.add_argument("--discover", action="store_true", help="Discover presentations from OTIS (legacy)")
    parser.add_argument("--add-manual", type=str, help="Add presentation PDF URL directly")

    # Batch processing
    parser.add_argument("--batch", action="store_true", help="Batch process all PDFs")
    parser.add_argument("--config", action="store_true", help="Show batch config")
    parser.add_argument("--add-config", action="store_true", help="Add to batch config")

    # Summary
    parser.add_argument("--summary", action="store_true", help="Show all guidance")
    parser.add_argument("--ticker", type=str, default="OTIS", help="Stock ticker")
    parser.add_argument("--year", type=int, default=2026, help="Forecast year")

    args = parser.parse_args()

    # Determine action
    if args.discover_ir:
        discover_from_ir_website()
    elif args.ir_registry:
        show_ir_registry()
    elif args.add_pdf_manual:
        add_pdf_manually()
    elif args.discover:
        discover_otis()
    elif args.add_manual:
        add_manual_presentation(args.add_manual, ticker=args.ticker, year=args.year)
    elif args.batch:
        batch_process()
    elif args.config:
        show_ir_config()
    elif args.add_config:
        add_to_config()
    elif args.summary or not any(vars(args).values()):
        show_summary()


if __name__ == "__main__":
    main()
