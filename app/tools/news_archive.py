"""Read-only search over the tw-news-monitor-simple article DB (its data/news.db).

The monitor collects ~26 Taiwan outlets every 30 minutes; searching it first gives
the agent sources Google News does not index, and costs no network round trip.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path


def search_archive(db_path: str | Path, query: str, max_results: int = 6,
                   days: int = 60) -> list[dict]:
    """AND-match every query term against title + summary, newest first.

    If nothing matches, trailing terms are dropped one at a time so that a
    narrow query like '高雄市長 柯志恩 辯論' still returns articles on 柯志恩.
    """
    path = Path(db_path) if db_path else None
    terms = query.split()
    if not path or not path.is_file() or not terms:
        return []
    since = (date.today() - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        while terms:
            where = " AND ".join(
                "(title LIKE ? OR COALESCE(summary,'') LIKE ?)" for _ in terms)
            params: list = [p for t in terms for p in (f"%{t}%", f"%{t}%")]
            rows = conn.execute(
                "SELECT title, url, summary, source_name,"
                " COALESCE(published_at, fetched_at) AS ts FROM articles"
                f" WHERE {where} AND COALESCE(published_at, fetched_at) >= ?"
                " ORDER BY ts DESC LIMIT ?",
                (*params, since, max_results)).fetchall()
            if rows or len(terms) == 1:
                break
            terms = terms[:-1]
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return [{"title": r["title"], "url": r["url"],
             "snippet": (r["summary"] or "")[:300], "published": r["ts"] or "",
             "source": r["source_name"], "provider": "news_monitor"} for r in rows]
