import sqlite3
import pandas as pd
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "econ_data.db")

_CREATE_METADATA = """
CREATE TABLE IF NOT EXISTS series_metadata (
    series_id    TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    category     TEXT NOT NULL,
    unit         TEXT,
    frequency    TEXT,
    last_updated TEXT,
    last_fetched TEXT
);
"""

_CREATE_OBSERVATIONS = """
CREATE TABLE IF NOT EXISTS observations (
    series_id  TEXT NOT NULL,
    date       TEXT NOT NULL,
    value      REAL,
    PRIMARY KEY (series_id, date),
    FOREIGN KEY (series_id) REFERENCES series_metadata(series_id)
);
"""


def init_db(db_path: str = DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(_CREATE_METADATA)
    conn.execute(_CREATE_OBSERVATIONS)
    conn.commit()
    return conn


def upsert_metadata(conn: sqlite3.Connection, series_id: str, name: str,
                    category: str, unit: str, frequency: str,
                    last_updated: str, last_fetched: str) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO series_metadata
           (series_id, name, category, unit, frequency, last_updated, last_fetched)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (series_id, name, category, unit, frequency, last_updated, last_fetched),
    )
    conn.commit()


def get_stored_last_updated(conn: sqlite3.Connection, series_id: str) -> str | None:
    row = conn.execute(
        "SELECT last_updated FROM series_metadata WHERE series_id = ?", (series_id,)
    ).fetchone()
    return row[0] if row else None


def bulk_upsert_observations(conn: sqlite3.Connection, series_id: str,
                              records: list[tuple[str, float | None]]) -> None:
    if not records:
        return
    conn.execute("BEGIN")
    conn.executemany(
        "INSERT OR REPLACE INTO observations (series_id, date, value) VALUES (?, ?, ?)",
        [(series_id, date, value) for date, value in records],
    )
    conn.commit()


def get_observations(conn: sqlite3.Connection, series_id: str) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT date, value FROM observations WHERE series_id = ? ORDER BY date ASC",
        conn,
        params=(series_id,),
    )
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def get_last_stored_date(conn: sqlite3.Connection, series_id: str) -> str | None:
    row = conn.execute(
        "SELECT MAX(date) FROM observations WHERE series_id = ?", (series_id,)
    ).fetchone()
    return row[0] if row and row[0] else None


def get_all_metadata(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM series_metadata", conn)
