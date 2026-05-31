import re
from datetime import datetime
from difflib import SequenceMatcher

import feedparser


def _normalise(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace for comparison."""
    t = title.lower()
    t = re.sub(r"[^\w\s]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalise(a), _normalise(b)).ratio()


def deduplicate_by_similarity(articles: list[dict], threshold: float = 0.75) -> list[dict]:
    """
    Remove articles whose title is highly similar to an already-kept article.
    When two articles are near-duplicates, keep the one published earlier
    (original source) or the first encountered if dates are equal.
    Articles are assumed to be pre-sorted newest-first, so we reverse,
    deduplicate (keeping the oldest/original), then re-reverse.
    """
    chronological = list(reversed(articles))   # oldest first → keep originals
    kept: list[dict] = []
    for candidate in chronological:
        if not any(_similarity(candidate["title"], k["title"]) >= threshold
                   for k in kept):
            kept.append(candidate)
    return list(reversed(kept))                # back to newest-first


def _strip_html(text: str) -> str:
    if not text:
        return ""
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def _parse_date(entry) -> datetime:
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime(*t[:6])
            except Exception:
                pass
    return datetime.min


def fetch_news(feeds: list[dict], max_per_feed: int = 10) -> list[dict]:
    """Fetch articles from RSS feeds, deduplicate by URL, return sorted newest-first."""
    articles = []

    for feed_info in feeds:
        try:
            feed = feedparser.parse(feed_info["url"])
            for entry in feed.entries[:max_per_feed]:
                title = _strip_html(getattr(entry, "title", ""))
                summary = _strip_html(
                    getattr(entry, "summary", "") or getattr(entry, "description", "")
                )
                url = getattr(entry, "link", "")

                if not title or not url:
                    continue

                if len(summary) > 350:
                    summary = summary[:347] + "..."

                articles.append({
                    "source": feed_info["name"],
                    "title": title,
                    "summary": summary,
                    "url": url,
                    "published": _parse_date(entry),
                })
        except Exception:
            continue

    seen: set[str] = set()
    unique = []
    for a in sorted(articles, key=lambda x: x["published"], reverse=True):
        if a["url"] not in seen:
            seen.add(a["url"])
            unique.append(a)

    return unique
