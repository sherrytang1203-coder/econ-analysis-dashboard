import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "econ_data.db")


def _get_secret(key: str) -> str:
    val = os.environ.get(key, "")
    if not val:
        try:
            import streamlit as st
            val = st.secrets.get(key, "")
        except Exception:
            pass
    return val


class Store:
    """Unified data store — uses Supabase when SUPABASE_URL + SUPABASE_KEY are
    set, otherwise falls back to local SQLite."""

    def __init__(self):
        url = _get_secret("SUPABASE_URL")
        key = _get_secret("SUPABASE_KEY")
        if url and key:
            from supabase import create_client
            self._supa = create_client(url, key)
            self._conn = None
        else:
            self._supa = None
            os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
            self._conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._init_sqlite_tables()

    def _init_sqlite_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS series_metadata (
                series_id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL,
                unit TEXT, frequency TEXT, last_updated TEXT, last_fetched TEXT
            );
            CREATE TABLE IF NOT EXISTS observations (
                series_id TEXT NOT NULL, date TEXT NOT NULL, value REAL,
                PRIMARY KEY (series_id, date)
            );
            CREATE TABLE IF NOT EXISTS articles (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT UNIQUE NOT NULL,
                source TEXT, published TEXT, raw_summary TEXT, fetched_date TEXT NOT NULL,
                econ_relevance REAL, impact_direction TEXT, impact_magnitude REAL,
                ai_summary TEXT, reasoning TEXT, rank INTEGER, analyzed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS watchlist (
                ticker   TEXT PRIMARY KEY,
                name     TEXT NOT NULL,
                added_at TEXT
            );
        """)
        self._conn.commit()

    @property
    def is_supabase(self) -> bool:
        return self._supa is not None

    # ── FRED metadata ──────────────────────────────────────────────────────────

    def get_all_metadata(self) -> pd.DataFrame:
        if self.is_supabase:
            r = self._supa.table("series_metadata").select("*").execute()
            return pd.DataFrame(r.data) if r.data else pd.DataFrame()
        return pd.read_sql_query("SELECT * FROM series_metadata", self._conn)

    def get_stored_last_updated(self, series_id: str) -> str | None:
        if self.is_supabase:
            r = (self._supa.table("series_metadata")
                 .select("last_updated").eq("series_id", series_id).execute())
            return r.data[0]["last_updated"] if r.data else None
        row = self._conn.execute(
            "SELECT last_updated FROM series_metadata WHERE series_id = ?",
            (series_id,)
        ).fetchone()
        return row[0] if row else None

    def upsert_metadata(self, series_id, name, category, unit,
                        frequency, last_updated, last_fetched) -> None:
        if self.is_supabase:
            self._supa.table("series_metadata").upsert({
                "series_id": series_id, "name": name, "category": category,
                "unit": unit, "frequency": frequency,
                "last_updated": last_updated, "last_fetched": last_fetched,
            }).execute()
        else:
            self._conn.execute(
                """INSERT OR REPLACE INTO series_metadata
                   (series_id, name, category, unit, frequency, last_updated, last_fetched)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (series_id, name, category, unit, frequency, last_updated, last_fetched),
            )
            self._conn.commit()

    # ── FRED observations ──────────────────────────────────────────────────────

    def get_observations(self, series_id: str) -> pd.DataFrame:
        if self.is_supabase:
            r = (self._supa.table("observations")
                 .select("date,value")
                 .eq("series_id", series_id)
                 .order("date")
                 .execute())
            df = pd.DataFrame(r.data) if r.data else pd.DataFrame(columns=["date", "value"])
        else:
            df = pd.read_sql_query(
                "SELECT date, value FROM observations WHERE series_id = ? ORDER BY date ASC",
                self._conn, params=(series_id,),
            )
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
        return df

    def get_last_stored_date(self, series_id: str) -> str | None:
        if self.is_supabase:
            r = (self._supa.table("observations")
                 .select("date")
                 .eq("series_id", series_id)
                 .order("date", desc=True)
                 .limit(1)
                 .execute())
            return r.data[0]["date"] if r.data else None
        row = self._conn.execute(
            "SELECT MAX(date) FROM observations WHERE series_id = ?", (series_id,)
        ).fetchone()
        return row[0] if row and row[0] else None

    def bulk_upsert_observations(self, series_id: str,
                                  records: list[tuple]) -> None:
        if not records:
            return
        if self.is_supabase:
            data = [{"series_id": series_id, "date": d, "value": v}
                    for d, v in records]
            for i in range(0, len(data), 500):
                self._supa.table("observations").upsert(data[i:i + 500]).execute()
        else:
            self._conn.execute("BEGIN")
            self._conn.executemany(
                "INSERT OR REPLACE INTO observations (series_id, date, value) VALUES (?,?,?)",
                [(series_id, d, v) for d, v in records],
            )
            self._conn.commit()

    # ── News articles ──────────────────────────────────────────────────────────

    def insert_new_articles(self, articles: list[dict],
                            fetched_date: str) -> int:
        inserted = 0
        for a in articles:
            pub = a["published"]
            pub_str = pub.isoformat() if hasattr(pub, "isoformat") else str(pub)
            aid = hashlib.sha256(a["url"].encode()).hexdigest()[:16]
            row = {
                "id": aid, "title": a["title"], "url": a["url"],
                "source": a.get("source", ""), "published": pub_str,
                "raw_summary": a.get("summary", ""), "fetched_date": fetched_date,
            }
            if self.is_supabase:
                try:
                    self._supa.table("articles").upsert(
                        row, on_conflict="url", ignore_duplicates=True
                    ).execute()
                    inserted += 1
                except Exception:
                    continue
            else:
                try:
                    self._conn.execute(
                        """INSERT OR IGNORE INTO articles
                           (id, title, url, source, published, raw_summary, fetched_date)
                           VALUES (?,?,?,?,?,?,?)""",
                        (aid, a["title"], a["url"], a.get("source", ""),
                         pub_str, a.get("summary", ""), fetched_date),
                    )
                    if self._conn.execute("SELECT changes()").fetchone()[0]:
                        inserted += 1
                except Exception:
                    continue
        if not self.is_supabase:
            self._conn.commit()
        return inserted

    def get_unanalyzed(self, fetched_date: str) -> list[dict]:
        cols = ("id", "title", "url", "source", "published", "raw_summary")
        if self.is_supabase:
            r = (self._supa.table("articles")
                 .select(",".join(cols))
                 .eq("fetched_date", fetched_date)
                 .is_("analyzed_at", "null")
                 .order("published", desc=True)
                 .execute())
            return r.data or []
        rows = self._conn.execute(
            """SELECT id, title, url, source, published, raw_summary
               FROM articles WHERE fetched_date = ? AND analyzed_at IS NULL
               ORDER BY published DESC""",
            (fetched_date,),
        ).fetchall()
        return [dict(zip(cols, row)) for row in rows]

    def save_analysis(self, art_id: str, result: dict) -> None:
        data = {
            "econ_relevance":   result.get("econ_relevance"),
            "impact_direction": result.get("impact_direction"),
            "impact_magnitude": result.get("impact_magnitude"),
            "ai_summary":       result.get("ai_summary"),
            "reasoning":        result.get("reasoning"),
            "analyzed_at":      datetime.now(timezone.utc).isoformat(),
        }
        if self.is_supabase:
            self._supa.table("articles").update(data).eq("id", art_id).execute()
        else:
            self._conn.execute(
                """UPDATE articles SET
                   econ_relevance=?, impact_direction=?, impact_magnitude=?,
                   ai_summary=?, reasoning=?, analyzed_at=? WHERE id=?""",
                (*data.values(), art_id),
            )
            self._conn.commit()

    def assign_ranks(self, fetched_date: str) -> None:
        if self.is_supabase:
            self._supa.table("articles").update({"rank": None}).eq(
                "fetched_date", fetched_date
            ).execute()
            r = (self._supa.table("articles")
                 .select("id,econ_relevance,impact_magnitude")
                 .eq("fetched_date", fetched_date)
                 .not_.is_("analyzed_at", "null")
                 .execute())
            rows = [(a["id"], a.get("econ_relevance"), a.get("impact_magnitude"))
                    for a in (r.data or [])]
        else:
            self._conn.execute(
                "UPDATE articles SET rank = NULL WHERE fetched_date = ?",
                (fetched_date,),
            )
            rows = self._conn.execute(
                """SELECT id, econ_relevance, impact_magnitude FROM articles
                   WHERE fetched_date = ? AND analyzed_at IS NOT NULL""",
                (fetched_date,),
            ).fetchall()

        scored = sorted(rows, key=lambda x: (x[1] or 0) * (1 + (x[2] or 0)),
                        reverse=True)
        for rank, (art_id, _, __) in enumerate(scored[:10], start=1):
            if self.is_supabase:
                self._supa.table("articles").update({"rank": rank}).eq(
                    "id", art_id
                ).execute()
            else:
                self._conn.execute(
                    "UPDATE articles SET rank = ? WHERE id = ?", (rank, art_id)
                )
        if not self.is_supabase:
            self._conn.commit()

    def get_top10(self, fetched_date: str) -> list[dict]:
        cols = ("id", "title", "url", "source", "published",
                "econ_relevance", "impact_direction", "impact_magnitude",
                "ai_summary", "reasoning", "rank")
        if self.is_supabase:
            r = (self._supa.table("articles")
                 .select(",".join(cols))
                 .eq("fetched_date", fetched_date)
                 .not_.is_("rank", "null")
                 .order("rank")
                 .execute())
            return r.data or []
        rows = self._conn.execute(
            """SELECT id,title,url,source,published,econ_relevance,impact_direction,
                      impact_magnitude,ai_summary,reasoning,rank
               FROM articles WHERE fetched_date = ? AND rank IS NOT NULL
               ORDER BY rank ASC""",
            (fetched_date,),
        ).fetchall()
        return [dict(zip(cols, row)) for row in rows]

    def last_pipeline_date(self) -> str | None:
        if self.is_supabase:
            r = (self._supa.table("articles")
                 .select("fetched_date")
                 .not_.is_("rank", "null")
                 .order("fetched_date", desc=True)
                 .limit(1)
                 .execute())
            return r.data[0]["fetched_date"] if r.data else None
        row = self._conn.execute(
            "SELECT MAX(fetched_date) FROM articles WHERE rank IS NOT NULL"
        ).fetchone()
        return row[0] if row else None

    # ── Watchlist ──────────────────────────────────────────────────────────────

    def get_watchlist(self) -> dict:
        """Return {ticker: name} for all user-added stocks."""
        if self.is_supabase:
            r = self._supa.table("watchlist").select("ticker,name").execute()
            return {row["ticker"]: row["name"] for row in (r.data or [])}
        rows = self._conn.execute(
            "SELECT ticker, name FROM watchlist ORDER BY added_at"
        ).fetchall()
        return {ticker: name for ticker, name in rows}

    def add_to_watchlist(self, ticker: str, name: str) -> None:
        added_at = datetime.now(timezone.utc).isoformat()
        if self.is_supabase:
            self._supa.table("watchlist").upsert(
                {"ticker": ticker, "name": name, "added_at": added_at}
            ).execute()
        else:
            self._conn.execute(
                "INSERT OR REPLACE INTO watchlist (ticker, name, added_at) VALUES (?,?,?)",
                (ticker, name, added_at),
            )
            self._conn.commit()

    def remove_from_watchlist(self, ticker: str) -> None:
        if self.is_supabase:
            self._supa.table("watchlist").delete().eq("ticker", ticker).execute()
        else:
            self._conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker,))
            self._conn.commit()
