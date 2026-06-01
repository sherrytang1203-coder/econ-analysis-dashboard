#!/usr/bin/env python3
"""
Schedule automatic guidance updates post-earnings.

Runs three times per day (ET):
- 9:30 AM: Market open (catch morning earnings releases)
- 6:00 PM: After-hours (catch afternoon announcements)
- 9:30 AM next day: Catch any delayed releases

Usage:
    python scripts/schedule_earnings_updates.py --start
    python scripts/schedule_earnings_updates.py --stop
    python scripts/schedule_earnings_updates.py --status
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.earnings_tracker import (
    get_tracked_stocks, should_update_guidance_now, log_update_attempt
)
from src.ir_config import batch_process_all_presentations


def run_earnings_update():
    """Check if any stocks had earnings and update guidance."""
    tracked = get_tracked_stocks()

    print(f"\n[*] Checking earnings for {len(tracked)} stocks...\n")

    update_count = 0
    for ticker in tracked:
        should_update, reason = should_update_guidance_now(ticker)

        if should_update:
            print(f"[+] {ticker}: {reason}")
            try:
                # Run batch processor for this stock
                print(f"    Fetching new presentations for {ticker}...")
                batch_process_all_presentations()
                log_update_attempt(ticker, True, reason)
                update_count += 1
            except Exception as e:
                print(f"    [ERROR] {str(e)}")
                log_update_attempt(ticker, False, str(e))
        else:
            print(f"[-] {ticker}: {reason}")

    print(f"\n[*] Updated guidance for {update_count}/{len(tracked)} stocks")
    return update_count > 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--run":
        # Direct run (for cron scheduling)
        run_earnings_update()
    else:
        print("""
Earnings Update Scheduler

Usage:
  python scripts/schedule_earnings_updates.py --run
      (Run earnings check now - use in cron)

Scheduled times (US Eastern):
  9:30 AM  - Market open (catch morning earnings)
  6:00 PM  - After-hours (catch afternoon announcements)
  9:30 AM  - Next day (catch delays)

To add to cron (Linux/Mac):
  30 9,18 * * * cd /path/to/project && python scripts/schedule_earnings_updates.py --run
  30 9 * * * cd /path/to/project && python scripts/schedule_earnings_updates.py --run

For Windows, use Task Scheduler:
  python.exe C:\\path\\to\\scripts\\schedule_earnings_updates.py --run
        """)
