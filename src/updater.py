from datetime import datetime, timezone
import pandas as pd
from fredapi import Fred

from src.config import MACRO_INDICATORS, ALL_SERIES_IDS
from src.fetcher import fetch_series_info, fetch_series_last_updated, fetch_observations


def _find_category(series_id: str) -> str:
    for cat, series in MACRO_INDICATORS.items():
        if series_id in series:
            return cat
    return "unknown"


def check_and_update(store, fred: Fred, series_ids: list[str] = None) -> list[dict]:
    if series_ids is None:
        series_ids = ALL_SERIES_IDS

    results = []
    for series_id in series_ids:
        stored = store.get_stored_last_updated(series_id)
        try:
            live = fetch_series_last_updated(fred, series_id)
        except Exception as e:
            results.append({
                "series_id": series_id,
                "status": "error",
                "error": str(e),
                "synced": False,
            })
            continue

        if stored is None:
            status = "initial_load"
        elif live != stored:
            status = "updated"
        else:
            status = "current"

        results.append({
            "series_id": series_id,
            "status": status,
            "live_last_updated": live,
            "stored_last_updated": stored,
            "synced": False,
            "fallback": live.startswith("fallback:"),
        })

    return results


def sync_series(store, fred: Fred, series_id: str, status: str,
                live_last_updated: str) -> None:
    if status == "current":
        return

    observation_start = None
    if status == "updated":
        observation_start = store.get_last_stored_date(series_id)

    try:
        observations = fetch_observations(fred, series_id, observation_start)
    except Exception:
        observations = pd.Series(dtype=float)

    records = [
        (str(idx.date()), None if pd.isna(val) else float(val))
        for idx, val in observations.items()
    ]
    store.bulk_upsert_observations(series_id, records)

    try:
        info = fetch_series_info(fred, series_id)
        name = info.get("title", series_id)
        unit = info.get("units", "")
        frequency = info.get("frequency", "")
    except Exception:
        cat_series = MACRO_INDICATORS.get(_find_category(series_id), {})
        meta = cat_series.get(series_id, {})
        name = meta.get("name", series_id)
        unit = meta.get("unit", "")
        frequency = meta.get("frequency", "")

    store.upsert_metadata(
        series_id=series_id,
        name=name,
        category=_find_category(series_id),
        unit=unit,
        frequency=frequency,
        last_updated=live_last_updated,
        last_fetched=datetime.now(timezone.utc).isoformat(),
    )


def run_update(store, fred: Fred, series_ids: list[str] = None) -> list[dict]:
    results = check_and_update(store, fred, series_ids)

    for r in results:
        if r["status"] in ("initial_load", "updated"):
            try:
                sync_series(store, fred, r["series_id"], r["status"],
                            r.get("live_last_updated", ""))
                r["synced"] = True
            except Exception as e:
                r["error"] = str(e)

    return results
