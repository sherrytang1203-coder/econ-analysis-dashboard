"""
Earnings Tracker - Monitor company earnings releases and auto-update guidance.

Tracks earnings dates for tracked stocks and automatically updates guidance
after earnings releases at scheduled times:
- Earnings day: 9:30 AM ET (market open) and 6:00 PM ET (after-hours)
- Next day: 9:30 AM ET (catch any delays)
"""

import yfinance as yf
from datetime import datetime, timedelta
from pathlib import Path
import json
import csv


EARNINGS_CACHE_FILE = Path(__file__).parent.parent / "data" / "earnings_cache.json"


def get_earnings_dates(ticker: str) -> list:
    """
    Get upcoming and recent earnings dates for a stock.

    Returns list of earnings dates (last 4 quarters + next 4 quarters)
    """
    try:
        t = yf.Ticker(ticker)

        # Get quarterly earnings dates from income statement
        # The index contains the earnings dates
        if hasattr(t, 'quarterly_financials') and not t.quarterly_financials.empty:
            earnings_dates = t.quarterly_financials.columns.tolist()
            return sorted(earnings_dates, reverse=True)[:8]  # Last 8 quarters
    except Exception:
        pass

    return []


def get_next_earnings(ticker: str) -> datetime:
    """Get the next upcoming earnings date for a stock."""
    try:
        dates = get_earnings_dates(ticker)
        today = datetime.now().date()

        for date in dates:
            if hasattr(date, 'date'):
                date_obj = date.date()
            else:
                date_obj = date

            if date_obj >= today:
                return date_obj
    except Exception:
        pass

    return None


def get_last_earnings(ticker: str) -> datetime:
    """Get the most recent earnings date."""
    try:
        dates = get_earnings_dates(ticker)
        if dates:
            date = dates[0]
            if hasattr(date, 'date'):
                return date.date()
            return date
    except Exception:
        pass

    return None


def should_update_guidance_now(ticker: str) -> tuple[bool, str]:
    """
    Check if guidance should be updated now.

    Returns: (should_update, reason)

    Update if:
    - Earnings were released today (morning or evening)
    - Earnings were released yesterday (morning catch-up)
    """
    try:
        last_earnings = get_last_earnings(ticker)
        if not last_earnings:
            return False, "No earnings date found"

        today = datetime.now().date()

        # Check if earnings released today
        if last_earnings == today:
            return True, "Earnings released today"

        # Check if earnings released yesterday (catch-up window)
        if last_earnings == today - timedelta(days=1):
            return True, "Earnings released yesterday (catch-up)"

        return False, f"Last earnings: {last_earnings}"

    except Exception as e:
        return False, f"Error checking earnings: {str(e)}"


def get_earnings_calendar(tickers: list) -> dict:
    """
    Get earnings calendar for multiple stocks.

    Returns dict: {ticker: {next_date, last_date, days_until}}
    """
    calendar = {}
    today = datetime.now().date()

    for ticker in tickers:
        try:
            # Fetch the date list once and derive next/last from it —
            # get_next_earnings/get_last_earnings would each re-fetch.
            dates = [d.date() if hasattr(d, "date") else d
                     for d in get_earnings_dates(ticker)]
            future = [d for d in dates if d >= today]
            past   = [d for d in dates if d < today]
            next_earnings = min(future) if future else None
            last_earnings = max(past) if past else None

            if next_earnings:
                days_until = (next_earnings - today).days
            else:
                days_until = None

            calendar[ticker] = {
                "next_earnings": next_earnings.isoformat() if next_earnings else None,
                "last_earnings": last_earnings.isoformat() if last_earnings else None,
                "days_until": days_until,
            }
        except Exception:
            calendar[ticker] = {
                "next_earnings": None,
                "last_earnings": None,
                "days_until": None,
            }

    return calendar


def log_update_attempt(ticker: str, success: bool, reason: str = ""):
    """Log when guidance update was attempted."""
    try:
        log_file = Path(__file__).parent.parent / "data" / "guidance_update_log.csv"

        # Create file with headers if it doesn't exist
        if not log_file.exists():
            with open(log_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    'timestamp', 'ticker', 'status', 'reason'
                ])
                writer.writeheader()

        # Append log entry
        with open(log_file, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=[
                'timestamp', 'ticker', 'status', 'reason'
            ])
            writer.writerow({
                'timestamp': datetime.now().isoformat(),
                'ticker': ticker,
                'status': 'success' if success else 'skipped',
                'reason': reason
            })
    except Exception:
        pass


def get_tracked_stocks() -> list:
    """Get list of stocks being tracked (from config)."""
    from src.config import TRACKED_STOCKS
    return list(TRACKED_STOCKS.keys())


def format_earnings_for_display(calendar: dict) -> str:
    """Format earnings calendar for dashboard display."""
    lines = ["**Earnings Calendar**\n"]

    for ticker in sorted(calendar.keys()):
        data = calendar[ticker]
        next_date = data.get("next_earnings")
        days = data.get("days_until")

        if next_date and days is not None:
            if days <= 0:
                status = "📍 Recent"
            elif days <= 7:
                status = f"🔴 {days}d away"
            elif days <= 30:
                status = f"🟡 {days}d away"
            else:
                status = f"⚪ {days}d away"

            lines.append(f"- **{ticker}**: {status} ({next_date})")
        else:
            lines.append(f"- **{ticker}**: No earnings date")

    return "\n".join(lines)
