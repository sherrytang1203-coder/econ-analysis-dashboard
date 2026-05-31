"""
IR Presentations Configuration Manager

Manages the ir_presentations.csv file that stores investor relations presentation URLs
for each tracked company. Allows batch processing of all presentations to extract guidance.
"""

import csv
from pathlib import Path
from datetime import datetime
from typing import Optional
from src.stock_fetcher import extract_fcf_from_pdf
from src.guidance_manager import add_forecast, append_to_csv
import re


IR_CONFIG_FILE = Path(__file__).parent.parent / "data" / "ir_presentations.csv"


def _ensure_ir_config_exists() -> bool:
    """Create ir_presentations.csv with headers if it doesn't exist."""
    try:
        IR_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

        if not IR_CONFIG_FILE.exists():
            with open(IR_CONFIG_FILE, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=['company', 'url', 'description'])
                writer.writeheader()
        return True
    except Exception:
        return False


def load_ir_presentations() -> list[dict]:
    """Load all IR presentations from config file."""
    _ensure_ir_config_exists()

    try:
        presentations = []
        with open(IR_CONFIG_FILE, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row and row.get('company') and row.get('url'):
                    presentations.append(row)
        return presentations
    except Exception:
        return []


def add_ir_presentation(company: str, url: str, description: str = "") -> bool:
    """
    Add a new IR presentation URL to the config.

    Args:
        company: Stock ticker (e.g., 'OTIS')
        url: Direct URL to earnings presentation PDF
        description: Optional description (e.g., 'Q1 2026 Earnings Webcast')

    Returns:
        True if added successfully
    """
    try:
        _ensure_ir_config_exists()

        with open(IR_CONFIG_FILE, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=['company', 'url', 'description'])
            writer.writerow({
                'company': company,
                'url': url,
                'description': description
            })
        return True
    except Exception:
        return False


def remove_ir_presentation(company: str, url: str) -> bool:
    """Remove a presentation from config."""
    try:
        presentations = load_ir_presentations()
        presentations = [
            p for p in presentations
            if not (p.get('company') == company and p.get('url') == url)
        ]

        with open(IR_CONFIG_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=['company', 'url', 'description'])
            writer.writeheader()
            writer.writerows(presentations)
        return True
    except Exception:
        return False


def _extract_fcf_guidance_from_text(text: str) -> Optional[tuple[float, float]]:
    """
    Extract FCF guidance range from PDF text.

    Looks for patterns like:
    - "$1.6B to $1.65B"
    - "$1.6 to 1.65 billion"
    - "1.6 billion to 1.65 billion"

    Returns:
        (min_fcf, max_fcf) or None if not found
    """
    # Pattern: $X.XB to $Y.YB or similar
    patterns = [
        r'\$?([\d.]+)\s*[Bb](?:illion)?\s+to\s+\$?([\d.]+)\s*[Bb](?:illion)?',
        r'([\d.]+)\s+billion\s+to\s+([\d.]+)\s+billion',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            try:
                min_val = float(matches[0][0])
                max_val = float(matches[0][1])
                return (min_val, max_val)
            except (ValueError, IndexError):
                continue

    return None


def _extract_eps_guidance_from_text(text: str) -> Optional[tuple[float, float]]:
    """
    Extract EPS guidance range from PDF text.

    Looks for patterns like:
    - "$4.20 to $4.24"
    - "4.20 to 4.24 per share"
    """
    patterns = [
        r'\$([\d.]+)\s+to\s+\$([\d.]+)',
        r'([\d.]+)\s+to\s+([\d.]+)\s+(?:per share|eps)',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            try:
                min_val = float(matches[0][0])
                max_val = float(matches[0][1])
                return (min_val, max_val)
            except (ValueError, IndexError):
                continue

    return None


def process_ir_presentation(company: str, url: str, forecast_year: int = 2026) -> dict:
    """
    Process a single IR presentation and extract guidance.

    Args:
        company: Stock ticker
        url: PDF URL
        forecast_year: What year the forecast is for (default 2026)

    Returns:
        Results dict with extracted guidance
    """
    result = {
        "company": company,
        "url": url,
        "status": "processing",
        "fcf_min": None,
        "fcf_max": None,
        "fcf_midpoint": None,
        "eps_min": None,
        "eps_max": None,
        "eps_midpoint": None,
        "stored": False,
        "error": None
    }

    try:
        # Extract PDF
        pdf_data = extract_fcf_from_pdf(url)

        if not pdf_data or "error" in pdf_data:
            result["status"] = "error"
            result["error"] = pdf_data.get("error", "Unknown PDF error")
            return result

        # Combine text sections for analysis
        full_text = (pdf_data.get("fcf_sections", "") + "\n" +
                     pdf_data.get("raw_text", ""))

        # Extract FCF guidance
        fcf_range = _extract_fcf_guidance_from_text(full_text)
        if fcf_range:
            result["fcf_min"] = fcf_range[0]
            result["fcf_max"] = fcf_range[1]
            result["fcf_midpoint"] = (fcf_range[0] + fcf_range[1]) / 2

        # Extract EPS guidance
        eps_range = _extract_eps_guidance_from_text(full_text)
        if eps_range:
            result["eps_min"] = eps_range[0]
            result["eps_max"] = eps_range[1]
            result["eps_midpoint"] = (eps_range[0] + eps_range[1]) / 2

        # Store in guidance system if FCF found
        if result["fcf_midpoint"]:
            success = add_forecast(
                ticker=company,
                year=forecast_year,
                fcf_billions=result["fcf_midpoint"],
                eps_dollars=result["eps_midpoint"],
                source=f"IR Presentation ({url.split('/')[-1]})",
                pdf_url=url
            )
            result["stored"] = success
            result["status"] = "success"
        else:
            result["status"] = "no_guidance"

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)

    return result


def batch_process_all_presentations(forecast_year: int = 2026) -> list[dict]:
    """
    Process all IR presentations from the config file.

    Returns:
        List of results for each presentation
    """
    presentations = load_ir_presentations()
    results = []

    for pres in presentations:
        company = pres.get('company')
        url = pres.get('url')

        if not company or not url:
            continue

        print(f"Processing {company}: {pres.get('description', 'N/A')}")
        result = process_ir_presentation(company, url, forecast_year)
        results.append(result)

        # Print status
        if result["status"] == "success":
            print(f"  [OK] FCF: ${result['fcf_midpoint']}B, EPS: ${result['eps_midpoint']}")
        elif result["status"] == "no_guidance":
            print(f"  [WARN] No guidance found in PDF")
        else:
            print(f"  [ERROR] {result.get('error', 'Unknown error')}")

    return results


def get_ir_config_summary() -> str:
    """Get human-readable summary of IR presentations config."""
    presentations = load_ir_presentations()

    if not presentations:
        return "No IR presentations configured. Use add_ir_presentation() to add one."

    lines = ["\n[IR PRESENTATIONS CONFIG]"]
    lines.append("=" * 80)

    for pres in presentations:
        company = pres.get('company', 'Unknown')
        url = pres.get('url', '')
        description = pres.get('description', '')
        lines.append(f"\n{company}:")
        lines.append(f"  Description: {description}")
        lines.append(f"  URL: {url}")

    lines.append("\n" + "=" * 80)
    return "\n".join(lines)
