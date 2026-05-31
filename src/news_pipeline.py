from datetime import date

from src.config import NEWS_FEEDS
from src.news_fetcher import fetch_news, deduplicate_by_similarity
from src.news_store import (
    init_articles_table,
    insert_new_articles,
    get_unanalyzed,
    save_analysis,
    assign_ranks,
    get_top10,
    last_pipeline_date,
)
from src.news_analyzer import analyze_batch


def needs_run(conn) -> bool:
    return last_pipeline_date(conn) != date.today().isoformat()


def run_pipeline(conn, groq_client) -> dict:
    today = date.today().isoformat()
    init_articles_table(conn)

    # 1. Fetch RSS articles and deduplicate by URL then by title similarity
    articles = fetch_news(NEWS_FEEDS, max_per_feed=15)
    articles = deduplicate_by_similarity(articles, threshold=0.75)

    # 2. Store new articles (INSERT OR IGNORE skips duplicates)
    new_count = insert_new_articles(conn, articles, today)

    # 3. Analyze unanalyzed articles for today
    to_analyze = get_unanalyzed(conn, today)
    analyzed_count = 0

    if to_analyze:
        try:
            results = analyze_batch(groq_client, to_analyze)
            for res in results:
                idx = res.get("article_index", 0) - 1
                if 0 <= idx < len(to_analyze):
                    save_analysis(conn, to_analyze[idx]["id"], res)
                    analyzed_count += 1
        except Exception as e:
            return {
                "error": str(e),
                "fetched": len(articles),
                "new": new_count,
                "analyzed": 0,
                "top10": [],
            }

    # 4. Score and assign ranks 1–10
    assign_ranks(conn, today)

    return {
        "fetched": len(articles),
        "new": new_count,
        "analyzed": analyzed_count,
        "top10": get_top10(conn, today),
    }
