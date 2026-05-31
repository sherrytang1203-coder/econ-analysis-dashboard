"""
IR Website PDF Discovery - Find presentation PDFs on investor relations websites.

Handles dynamic content loading (JavaScript) using Playwright headless browser.
Discovers and extracts PDF URLs from:
- Past Events
- Past Presentations
- Earnings Calls
- Annual/Quarterly Reports
"""

import csv
from pathlib import Path
from datetime import datetime
from typing import Optional
import requests
from bs4 import BeautifulSoup
import re


IR_REGISTRY_FILE = Path(__file__).parent.parent / "data" / "ir_presentations.csv"


def discover_pdfs_from_website(ir_url: str) -> list:
    """
    Discover PDF links from an IR website.

    Attempts multiple strategies:
    1. Try Playwright for JavaScript-heavy sites
    2. Fall back to static HTML parsing
    3. Look for common patterns in PDF URLs

    Args:
        ir_url: Main investor relations website URL (e.g., https://www.otisinvestors.com/...)

    Returns:
        List of PDF URLs found
    """
    pdfs = []

    # Strategy 1: Try Playwright for dynamic content
    pdfs.extend(_discover_with_playwright(ir_url))

    # Strategy 2: Try static HTML parsing
    if not pdfs:
        pdfs.extend(_discover_from_html(ir_url))

    # Strategy 3: Look for common Q4 CDN patterns (used by many IR sites)
    pdfs.extend(_discover_from_common_patterns(ir_url))

    return list(set(pdfs))  # Remove duplicates


def _discover_with_playwright(ir_url: str) -> list:
    """
    Use Playwright to load dynamic content and find PDFs.

    Returns empty list if Playwright not available.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []

    pdfs = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            # Load the page
            page.goto(ir_url, wait_until="networkidle", timeout=30000)

            # Wait for content to load
            page.wait_for_timeout(2000)

            # Get all links
            links = page.query_selector_all("a[href*='.pdf']")

            for link in links:
                href = link.get_attribute("href")
                if href and ".pdf" in href.lower():
                    # Convert relative URLs to absolute
                    if href.startswith("http"):
                        pdfs.append(href)
                    else:
                        # Resolve relative URL
                        from urllib.parse import urljoin
                        absolute_url = urljoin(ir_url, href)
                        pdfs.append(absolute_url)

            browser.close()

    except Exception as e:
        print(f"[WARNING] Playwright discovery failed: {str(e)}")
        return []

    return pdfs


def _discover_from_html(ir_url: str) -> list[str]:
    """
    Parse static HTML for PDF links.
    Works for non-JavaScript sites.
    """
    pdfs = []

    try:
        resp = requests.get(ir_url, timeout=10)
        soup = BeautifulSoup(resp.content, 'html.parser')

        # Find all links
        for link in soup.find_all('a', href=True):
            href = link['href']

            if '.pdf' in href.lower():
                # Convert relative to absolute
                if href.startswith('http'):
                    pdfs.append(href)
                else:
                    from urllib.parse import urljoin
                    absolute_url = urljoin(ir_url, href)
                    pdfs.append(absolute_url)

    except Exception as e:
        print(f"[WARNING] HTML parsing failed: {str(e)}")

    return pdfs


def _discover_from_common_patterns(ir_url: str) -> list:
    """
    Look for PDFs in common IR hosting patterns.

    Many companies use:
    - Q4 CDN (s###.q4cdn.com)
    - Direct company domains
    - Investor.com hosting
    """
    pdfs = []

    # Extract domain/company info from IR URL
    # Example: https://www.otisinvestors.com -> "otis"
    match = re.search(r'://(?:www\.)?([a-z]+)investors\.com', ir_url, re.IGNORECASE)
    if not match:
        return []

    company_name = match.group(1).upper()

    # Try common Q4 CDN patterns
    # Q4 CDN typically hosts: s###.q4cdn.com/ID/files/doc_financials/YYYY/qX/
    try:
        # This is more manual - would need company-specific Q4 CDN IDs
        # For OTIS: s203.q4cdn.com/227649559
        pass
    except Exception:
        pass

    return pdfs


def discover_and_add_pdfs(company: str, ir_url: str) -> dict:
    """
    Discover PDFs from IR website and add them to the registry.

    Args:
        company: Stock ticker (e.g., 'OTIS')
        ir_url: Investor relations website URL

    Returns:
        Result dict with found PDFs and status
    """
    result = {
        "company": company,
        "ir_url": ir_url,
        "pdfs_found": [],
        "status": "discovering",
        "error": None
    }

    print(f"\n[*] Discovering PDFs from {company} IR website...")
    print(f"    URL: {ir_url}")

    try:
        pdfs = discover_pdfs_from_website(ir_url)

        if pdfs:
            result["pdfs_found"] = pdfs
            result["status"] = "success"
            print(f"[+] Found {len(pdfs)} PDF(s):")
            for pdf in pdfs:
                print(f"    - {pdf}")

            # Update registry
            _update_ir_registry(company, ir_url, pdfs)

        else:
            result["status"] = "no_pdfs"
            result["error"] = "No PDFs found on website"
            print("[!] No PDFs found. Website may use complex JavaScript loading.")
            print("    Try visiting the site manually and copying PDF URLs.")

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        print(f"[ERROR] {str(e)}")

    return result


def _update_ir_registry(company: str, ir_url: str, pdfs: list[str]) -> bool:
    """Update ir_presentations.csv with discovered PDFs."""
    try:
        # Read existing
        existing = {}
        if IR_REGISTRY_FILE.exists():
            with open(IR_REGISTRY_FILE, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get('company'):
                        existing[row['company']] = row

        # Update with new data
        existing[company] = {
            'company': company,
            'ir_website': ir_url,
            'pdf_urls': ';'.join(pdfs),  # Store multiple PDFs separated by ;
            'last_discovered': datetime.now().strftime("%Y-%m-%d")
        }

        # Write back
        with open(IR_REGISTRY_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=[
                'company', 'ir_website', 'pdf_urls', 'last_discovered'
            ])
            writer.writeheader()
            for row in existing.values():
                writer.writerow(row)

        return True

    except Exception as e:
        print(f"[ERROR] Failed to update registry: {str(e)}")
        return False


def load_ir_registry() -> list:
    """Load all companies from IR registry."""
    if not IR_REGISTRY_FILE.exists():
        return []

    try:
        records = []
        with open(IR_REGISTRY_FILE, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row and row.get('company'):
                    # Split PDF URLs (multiple separated by ;)
                    pdfs = row.get('pdf_urls', '').split(';')
                    pdfs = [p.strip() for p in pdfs if p.strip()]
                    row['pdf_urls'] = pdfs
                    records.append(row)
        return records
    except Exception:
        return []


def get_ir_registry_summary() -> str:
    """Get human-readable summary of IR registry."""
    records = load_ir_registry()

    if not records:
        return "No IR websites registered. Use discover_and_add_pdfs() to add one."

    lines = ["\n[IR WEBSITE REGISTRY]"]
    lines.append("=" * 80)

    for record in records:
        company = record.get('company', 'Unknown')
        ir_url = record.get('ir_website', '')
        pdfs = record.get('pdf_urls', [])
        last_discovered = record.get('last_discovered', '')

        lines.append(f"\n{company}:")
        lines.append(f"  IR Website: {ir_url}")
        lines.append(f"  Last Discovered: {last_discovered}")
        lines.append(f"  PDFs Found: {len(pdfs)}")

        for pdf in pdfs[:5]:  # Show first 5
            # Shorten long URLs
            display_url = pdf if len(pdf) < 60 else pdf[:57] + "..."
            lines.append(f"    - {display_url}")

        if len(pdfs) > 5:
            lines.append(f"    ... and {len(pdfs) - 5} more")

    lines.append("\n" + "=" * 80)
    return "\n".join(lines)


# Manual discovery helper
def manual_add_pdf(company: str, ir_url: str, pdf_url: str) -> bool:
    """
    Manually add a PDF URL to the registry when auto-discovery fails.

    Args:
        company: Stock ticker
        ir_url: Investor relations website URL
        pdf_url: Direct PDF URL

    Returns:
        True if added successfully
    """
    try:
        existing = {}
        if IR_REGISTRY_FILE.exists():
            with open(IR_REGISTRY_FILE, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get('company'):
                        existing[row['company']] = row

        # Update or add
        if company in existing:
            pdfs = existing[company].get('pdf_urls', '').split(';')
            pdfs = [p.strip() for p in pdfs if p.strip()]
            if pdf_url not in pdfs:
                pdfs.append(pdf_url)
            existing[company]['pdf_urls'] = ';'.join(pdfs)
            existing[company]['last_discovered'] = datetime.now().strftime("%Y-%m-%d")
        else:
            existing[company] = {
                'company': company,
                'ir_website': ir_url,
                'pdf_urls': pdf_url,
                'last_discovered': datetime.now().strftime("%Y-%m-%d")
            }

        # Write back
        with open(IR_REGISTRY_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=[
                'company', 'ir_website', 'pdf_urls', 'last_discovered'
            ])
            writer.writeheader()
            for row in existing.values():
                writer.writerow(row)

        return True

    except Exception as e:
        print(f"[ERROR] {str(e)}")
        return False
