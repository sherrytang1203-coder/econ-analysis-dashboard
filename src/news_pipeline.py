from datetime import date

from src.config import NEWS_FEEDS
from src.news_fetcher import fetch_news, deduplicate_by_similarity
from src.news_analyzer import analyze_batch


def needs_run(store) -> bool:
    return store.last_pipeline_date() != date.today().isoformat()


def run_pipeline(store, groq_client) -> dict:
    today = date.today().isoformat()

    articles = fetch_news(NEWS_FEEDS, max_per_feed=15)
    articles = deduplicate_by_similarity(articles, threshold=0.75)

    new_count = store.insert_new_articles(articles, today)

    to_analyze = store.get_unanalyzed(today)
    analyzed_count = 0

    if to_analyze:
        try:
            results = analyze_batch(groq_client, to_analyze)
            for res in results:
                idx = res.get("article_index", 0) - 1
                if 0 <= idx < len(to_analyze):
                    store.save_analysis(to_analyze[idx]["id"], res)
                    analyzed_count += 1
        except Exception as e:
            return {
                "error": str(e),
                "fetched": len(articles),
                "new": new_count,
                "analyzed": 0,
                "top10": [],
            }

    store.assign_ranks(today)

    return {
        "fetched": len(articles),
        "new": new_count,
        "analyzed": analyzed_count,
        "top10": store.get_top10(today),
    }
