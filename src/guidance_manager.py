"""
Guidance Manager: Store and retrieve official FCF/EPS forecasts from company presentations.

Since many investor relations sites use JavaScript to load content dynamically,
this module provides a practical system to:
1. Manually input official guidance from presentations
2. Extract guidance from PDF URLs
3. Override calculated forecasts with official numbers

Storage:
- guidance_forecasts.json: Current/latest guidance (for quick lookup)
- guidance_history.csv: Historical log of all guidance extractions (for tracking)
"""

import json
import csv
from pathlib import Path
from datetime import datetime
from typing import Optional
from src.stock_fetcher import extract_fcf_from_pdf


GUIDANCE_FILE = Path(__file__).parent.parent / "data" / "guidance_forecasts.json"
GUIDANCE_CSV = Path(__file__).parent.parent / "data" / "guidance_history.csv"


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
    """Save guidance forecasts to JSON file."""
    try:
        GUIDANCE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(GUIDANCE_FILE, 'w') as f:
            json.dump(guidance_dict, f, indent=2)
        return True
    except Exception:
        return False


def _ensure_csv_exists() -> bool:
    """Create CSV file with headers if it doesn't exist."""
    try:
        GUIDANCE_CSV.parent.mkdir(parents=True, exist_ok=True)

        if not GUIDANCE_CSV.exists():
            with open(GUIDANCE_CSV, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    'company', 'snap_date', 'forecast_year', 'eps', 'fcf', 'pdf_url'
                ])
                writer.writeheader()
        return True
    except Exception:
        return False


def append_to_csv(ticker: str, forecast_year: int, eps: Optional[float] = None,
                  fcf: Optional[float] = None, snap_date: Optional[str] = None,
                  pdf_url: Optional[str] = None) -> bool:
    """
    Append guidance record to CSV history file.

    Args:
        ticker: Stock ticker
        forecast_year: Forecast year (e.g., 2026)
        eps: EPS forecast (optional)
        fcf: FCF forecast in billions (optional)
        snap_date: Date when data was pulled (defaults to today)
        pdf_url: URL to source PDF presentation (optional)

    Returns:
        True if appended successfully
    """
    try:
        if not snap_date:
            snap_date = datetime.now().strftime("%Y-%m-%d")

        _ensure_csv_exists()

        with open(GUIDANCE_CSV, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=[
                'company', 'snap_date', 'forecast_year', 'eps', 'fcf', 'pdf_url'
            ])
            writer.writerow({
                'company': ticker,
                'snap_date': snap_date,
                'forecast_year': forecast_year,
                'eps': eps if eps is not None else '',
                'fcf': fcf if fcf is not None else '',
                'pdf_url': pdf_url if pdf_url else ''
            })
        return True
    except Exception:
        return False


def add_forecast(ticker: str, year: int, fcf_billions: float, eps_dollars: float = None,
                 source: str = "", pdf_url: str = "") -> bool:
    """
    Add or update official guidance for a company.

    This saves to both:
    1. JSON (guidance_forecasts.json) - for quick lookup
    2. CSV (guidance_history.csv) - historical log with snapshot date

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

    # Save to JSON
    json_saved = save_guidance(guidance)

    # Append to CSV history
    csv_saved = append_to_csv(
        ticker=ticker,
        forecast_year=year,
        eps=eps_dollars,
        fcf=fcf_billions,
        snap_date=datetime.now().strftime("%Y-%m-%d"),
        pdf_url=pdf_url
    )

    return json_saved and csv_saved


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


def read_guidance_csv() -> list[dict]:
    """Read all guidance history from CSV file."""
    if not GUIDANCE_CSV.exists():
        return []

    try:
        records = []
        with open(GUIDANCE_CSV, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row:
                    records.append(row)
        return records
    except Exception:
        return []


def get_csv_summary() -> str:
    """Get human-readable summary of guidance history."""
    records = read_guidance_csv()

    if not records:
        return "No guidance history found. Run discovery to populate."

    lines = ["\n[GUIDANCE HISTORY]"]
    lines.append("=" * 70)

    # Group by company
    by_company = {}
    for record in records:
        company = record.get('company', 'Unknown')
        if company not in by_company:
            by_company[company] = []
        by_company[company].append(record)

    for company in sorted(by_company.keys()):
        lines.append(f"\n{company}:")
        lines.append("-" * 70)
        for record in by_company[company]:
            snap_date = record.get('snap_date', 'N/A')
            year = record.get('forecast_year', 'N/A')
            eps = record.get('eps', 'N/A')
            fcf = record.get('fcf', 'N/A')
            pdf_url = record.get('pdf_url', '')

            line = f"  {snap_date} | Year {year} | EPS: ${eps} | FCF: ${fcf}B"
            if pdf_url:
                # Shorten URL for display
                display_url = pdf_url.split('/')[-1] if '/' in pdf_url else pdf_url
                line += f" | PDF: {display_url}"
            lines.append(line)

    return "\n".join(lines)
