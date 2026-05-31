import hashlib
import sqlite3
from datetime import date, datetime


_CREATE_ARTICLES = """
CREATE TABLE IF NOT EXISTS articles (
    id               TEXT PRIMARY KEY,
    title            TEXT NOT NULL,
    url              TEXT UNIQUE NOT NULL,
    source           TEXT,
    published        TEXT,
    raw_summary      TEXT,
    fetched_date     TEXT NOT NULL,

    econ_relevance   REAL,
    impact_direction TEXT,
    impact_magnitude REAL,
    ai_summary       TEXT,
    reasoning        TEXT,
    rank             INTEGER,
    analyzed_at      TEXT
);
"""


def article_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def init_articles_table(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE_ARTICLES)
    conn.commit()


def insert_new_articles(conn: sqlite3.Connection,
                        articles: list[dict], fetched_date: str) -> int:
    inserted = 0
    for a in articles:
        pub = a["published"]
        pub_str = pub.isoformat() if isinstance(pub, datetime) else str(pub)
        try:
            conn.execute(
                """INSERT OR IGNORE INTO articles
                   (id, title, url, source, published, raw_summary, fetched_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (article_id(a["url"]), a["title"], a["url"], a.get("source", ""),
                 pub_str, a.get("summary", ""), fetched_date),
            )
            if conn.execute("SELECT changes()").fetchone()[0]:
                inserted += 1
        except Exception:
            continue
    conn.commit()
    return inserted


def get_unanalyzed(conn: sqlite3.Connection, fetched_date: str) -> list[dict]:
    rows = conn.execute(
        """SELECT id, title, url, source, published, raw_summary
           FROM articles
           WHERE fetched_date = ? AND analyzed_at IS NULL
           ORDER BY published DESC""",
        (fetched_date,),
    ).fetchall()
    cols = ("id", "title", "url", "source", "published", "raw_summary")
    return [dict(zip(cols, row)) for row in rows]


def save_analysis(conn: sqlite3.Connection, art_id: str, result: dict) -> None:
    conn.execute(
        """UPDATE articles SET
               econ_relevance   = ?,
               impact_direction = ?,
               impact_magnitude = ?,
               ai_summary       = ?,
               reasoning        = ?,
               analyzed_at      = ?
           WHERE id = ?""",
        (
            result.get("econ_relevance"),
            result.get("impact_direction"),
            result.get("impact_magnitude"),
            result.get("ai_summary"),
            result.get("reasoning"),
            datetime.utcnow().isoformat(),
            art_id,
        ),
    )
    conn.commit()


def assign_ranks(conn: sqlite3.Connection, fetched_date: str) -> None:
    conn.execute(
        "UPDATE articles SET rank = NULL WHERE fetched_date = ?", (fetched_date,)
    )
    rows = conn.execute(
        """SELECT id, econ_relevance, impact_magnitude
           FROM articles
           WHERE fetched_date = ? AND analyzed_at IS NOT NULL""",
        (fetched_date,),
    ).fetchall()

    scored = sorted(
        rows,
        key=lambda r: (r[1] or 0) * (1 + (r[2] or 0)),
        reverse=True,
    )
    for rank, (art_id, _, __) in enumerate(scored[:10], start=1):
        conn.execute("UPDATE articles SET rank = ? WHERE id = ?", (rank, art_id))
    conn.commit()


def get_top10(conn: sqlite3.Connection, fetched_date: str) -> list[dict]:
    rows = conn.execute(
        """SELECT id, title, url, source, published,
                  econ_relevance, impact_direction, impact_magnitude,
                  ai_summary, reasoning, rank
           FROM articles
           WHERE fetched_date = ? AND rank IS NOT NULL
           ORDER BY rank ASC""",
        (fetched_date,),
    ).fetchall()
    cols = ("id", "title", "url", "source", "published",
            "econ_relevance", "impact_direction", "impact_magnitude",
            "ai_summary", "reasoning", "rank")
    return [dict(zip(cols, row)) for row in rows]


def last_pipeline_date(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT MAX(fetched_date) FROM articles WHERE rank IS NOT NULL"
    ).fetchone()
    return row[0] if row else None
