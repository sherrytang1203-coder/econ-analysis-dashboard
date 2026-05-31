"""
Guidance Manager: Store and retrieve official FCF/EPS forecasts from company presentations.

Since many investor relations sites use JavaScript to load content dynamically,
this module provides a practical system to:
1. Manually input official guidance from presentations
2. Extract guidance from PDF URLs
3. Override calculated forecasts with official numbers
"""

import json
from pathlib import Path
from typing import Optional
from src.stock_fetcher import extract_fcf_from_pdf


GUIDANCE_FILE = Path(__file__).parent.parent / "data" / "guidance_forecasts.json"


def load_guidance() -> dict[str, any]:
    """Load all stored guidance forecasts."""
    if not GUIDANCE_FILE.exists():
        return {}
    try:
        with open(GUIDANCE_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def save_guidance(guidance_dict: dict[str, any]) -> bool:
    """Save guidance forecasts to file."""
    try:
        GUIDANCE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(GUIDANCE_FILE, 'w') as f:
            json.dump(guidance_dict, f, indent=2)
        return True
    except Exception:
        return False


def add_forecast(ticker: str, year: int, fcf_billions: float, eps_dollars: float = None,
                 source: str = "", pdf_url: str = "") -> bool:
    """
    Add or update official guidance for a company.

    Args:
        ticker: Stock ticker (e.g., 'OTIS')
        year: Forecast year (e.g., 2026)
        fcf_billions: Free Cash Flow forecast in billions (e.g., 1.65)
        eps_dollars: EPS forecast (optional)
        source: Source description (e.g., "Q1 2026 Earnings Presentation")
        pdf_url: URL to the presentation PDF

    Returns:
        True if saved successfully
    """
    guidance = load_guidance()

    if ticker not in guidance:
        guidance[ticker] = {}

    guidance[ticker][str(year)] = {
        "fcf_billions": fcf_billions,
        "eps_dollars": eps_dollars,
        "source": source,
        "pdf_url": pdf_url,
    }

    return save_guidance(guidance)


def get_forecast(ticker: str, year: int = 2026) -> dict | None:
    """Get official guidance for a company and year."""
    guidance = load_guidance()
    if ticker in guidance and str(year) in guidance[ticker]:
        return guidance[ticker][str(year)]
    return None


def extract_and_store_guidance(ticker: str, pdf_url: str, year: int = 2026,
                               fcf_billions: float = None) -> dict:
    """
    Extract guidance from a PDF and store it.

    Args:
        ticker: Stock ticker
        pdf_url: URL to earnings presentation PDF
        year: Forecast year
        fcf_billions: If known, provide the FCF guidance directly
                      Otherwise it will be extracted from PDF text

    Returns:
        Extracted guidance dict or error dict
    """
    # Extract text from PDF
    pdf_data = extract_fcf_from_pdf(pdf_url)

    if not pdf_data or "error" in pdf_data:
        return {"error": f"Failed to extract PDF: {pdf_data.get('error', 'Unknown')}"}

    guidance_dict = {
        "ticker": ticker,
        "year": year,
        "pdf_url": pdf_url,
        "fcf_billions": fcf_billions,
        "extracted_text": pdf_data.get("fcf_sections", "")[:500],  # First 500 chars
        "amounts_found": pdf_data.get("dollar_amounts", []),
    }

    # Try to store if we have FCF guidance
    if fcf_billions:
        if add_forecast(ticker, year, fcf_billions, source=f"PDF: {pdf_url}"):
            guidance_dict["stored"] = True
        else:
            guidance_dict["error"] = "Failed to store guidance"

    return guidance_dict


def list_all_guidance() -> dict[str, any]:
    """List all stored guidance forecasts."""
    return load_guidance()


def get_guidance_summary(ticker: str) -> str:
    """Get human-readable summary of stored guidance."""
    guidance = load_guidance()
    if ticker not in guidance:
        return f"No guidance data found for {ticker}"

    lines = [f"\n{ticker} Guidance Forecasts:"]
    lines.append("-" * 40)
    for year, data in guidance[ticker].items():
        fcf = data.get("fcf_billions", "N/A")
        eps = data.get("eps_dollars", "N/A")
        source = data.get("source", "Unknown")
        lines.append(f"  {year}: FCF ${fcf}B, EPS ${eps} ({source})")
    return "\n".join(lines)
