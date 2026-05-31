"""
IR Website Scrapers: Automatically pull guidance data from investor relations websites.

Since many IR sites use JavaScript for dynamic content loading, this module
provides company-specific scrapers that can:
1. Detect available presentation links
2. Download and parse PDFs
3. Extract guidance numbers
4. Store in guidance_manager
"""

import requests
from bs4 import BeautifulSoup
import re
from typing import list, dict
from src.stock_fetcher import extract_fcf_from_pdf
from src.guidance_manager import add_forecast, extract_and_store_guidance


def find_otis_presentations() -> list:
    """
    Find recent OTIS earnings presentations from investor relations website.

    Note: OTIS website uses JavaScript to load events dynamically.
    This function provides manual guidance on how to find presentations.

    Returns:
        List of presentation info dicts with title, date, pdf_url
    """
    presentations = []

    # These are known OTIS presentation URLs (manually found from their IR site)
    # Format: https://s203.q4cdn.com/227649559/files/doc_financials/YYYY/qX/
    known_presentations = [
        {
            "title": "Q1 2026 Earnings Webcast",
            "date": "2026-05-02",
            "pdf_url": "https://s203.q4cdn.com/227649559/files/doc_financials/2026/q1/1Q26-Otis-Earnings-Webcast.pdf",
            "event_type": "Earnings"
        },
        # Add more as discovered
    ]

    return known_presentations


def scrape_otis_guidance(auto_store: bool = True) -> dict:
    """
    Scrape OTIS presentations and extract guidance.

    Args:
        auto_store: If True, automatically store guidance in guidance_manager

    Returns:
        Summary of extracted guidance with sources
    """
    presentations = find_otis_presentations()
    results = {
        "ticker": "OTIS",
        "presentations_found": len(presentations),
        "guidance_extracted": [],
        "errors": []
    }

    for pres in presentations:
        try:
            print(f"Processing: {pres['title']} ({pres['date']})")

            # Extract PDF text
            pdf_data = extract_fcf_from_pdf(pres["pdf_url"])

            if not pdf_data or "error" in pdf_data:
                results["errors"].append({
                    "presentation": pres["title"],
                    "error": pdf_data.get("error", "Unknown error")
                })
                continue

            # Look for guidance numbers in extracted text
            text = pdf_data.get("fcf_sections", "") + "\n" + pdf_data.get("raw_text", "")

            # Pattern for FCF guidance (e.g., "$1.6B to 1.65B")
            fcf_pattern = r'\$?([\d.]+)\s*[Bb] to \$?([\d.]+)\s*[Bb]'
            fcf_matches = re.findall(fcf_pattern, text)

            if fcf_matches:
                # Use midpoint of range
                min_fcf = float(fcf_matches[0][0])
                max_fcf = float(fcf_matches[0][1])
                midpoint = (min_fcf + max_fcf) / 2

                guidance_info = {
                    "presentation": pres["title"],
                    "date": pres["date"],
                    "fcf_guidance_range": f"${min_fcf}B - ${max_fcf}B",
                    "fcf_guidance_midpoint": round(midpoint, 2),
                    "pdf_url": pres["pdf_url"]
                }

                results["guidance_extracted"].append(guidance_info)

                # Store in guidance manager
                if auto_store:
                    add_forecast(
                        ticker="OTIS",
                        year=2026,
                        fcf_billions=midpoint,
                        source=f"{pres['title']} ({pres['date']})",
                        pdf_url=pres["pdf_url"]
                    )

        except Exception as e:
            results["errors"].append({
                "presentation": pres["title"],
                "error": str(e)
            })

    return results


def get_presentation_pdf_url(ir_domain: str, ticker: str, year: int, quarter: int) -> str:
    """
    Construct OTIS-style presentation URL.

    OTIS uses pattern: https://s203.q4cdn.com/227649559/files/doc_financials/YYYY/qX/

    Args:
        ir_domain: Base Q4 CDN domain (e.g., 's203.q4cdn.com/227649559')
        ticker: Stock ticker
        year: Year (e.g., 2026)
        quarter: Quarter (1-4)

    Returns:
        URL to presentation PDF (if it exists)
    """
    return f"https://{ir_domain}/files/doc_financials/{year}/q{quarter}/{quarter}Q{year}-{ticker}-Earnings-Webcast.pdf"


# Example usage instructions:
"""
# To use this module:

from src.ir_scrapers import scrape_otis_guidance

# Automatically extract and store guidance from Otis presentations
results = scrape_otis_guidance(auto_store=True)

# Then use in forecasting:
from src.stock_fetcher import fetch_fcf_yield_forecast_2026
from src.guidance_manager import get_forecast

# Get stored guidance
otis_guidance = get_forecast("OTIS", 2026)
if otis_guidance:
    forecast = fetch_fcf_yield_forecast_2026(
        ticker="OTIS",
        fcf_guidance_billions=otis_guidance["fcf_billions"]
    )
    print(f"2026 FCF Yield (official): {forecast['fcf_yield_2026']}%")
"""
