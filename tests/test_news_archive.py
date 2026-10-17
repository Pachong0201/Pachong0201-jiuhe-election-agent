import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from app.tools import web
from app.tools.news_archive import search_archive

# schema copied from tw-news-monitor-simple app/database.py
SCHEMA = """CREATE TABLE articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL,
    source_name TEXT NOT NULL, category TEXT NOT NULL, title TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE, published_at TEXT, fetched_at TEXT NOT NULL,
    position INTEGER NOT NULL, summary TEXT, summary_source TEXT,
    summary_attempted_at TEXT, section TEXT, language TEXT, access_level TEXT)"""


def _day(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat() + "T09:00:00+08:00"


class NewsArchiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "news.db"
        conn = sqlite3.connect(self.db)
        conn.execute(SCHEMA)
        rows = [
            ("cna", "中央社", "柯志恩高雄造勢 喊拚翻轉", "https://a/1", _day(1), "國民黨高雄市長參選人柯志恩…"),
            ("ltn", "自由時報", "賴瑞隆拜票 鞏固基層", "https://a/2", _day(2), "民進黨高雄市長參選人…"),
            ("udn", "聯合報", "柯志恩舊聞", "https://a/3", _day(200), None),
            ("tvbs", "TVBS", "高雄市長民調 柯志恩賴瑞隆差距縮小", "https://a/4", None, None),
        ]
        for i, (sid, name, title, url, pub, summ) in enumerate(rows):
            conn.execute(
                "INSERT INTO articles(source_id, source_name, category, title, url,"
                " published_at, fetched_at, position, summary) VALUES (?,?,?,?,?,?,?,?,?)",
                (sid, name, "politics", title, url, pub, _day(0), i, summ))
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_and_match_newest_first_within_window(self):
        got = search_archive(self.db, "柯志恩")
        self.assertEqual([r["url"] for r in got], ["https://a/4", "https://a/1"])
        self.assertEqual(got[1]["source"], "中央社")
        self.assertEqual(got[0]["provider"], "news_monitor")

    def test_matches_summary_and_drops_trailing_terms(self):
        self.assertEqual([r["url"] for r in search_archive(self.db, "民進黨 高雄")], ["https://a/2"])
        got = search_archive(self.db, "柯志恩 不存在的詞")
        self.assertEqual(len(got), 2)

    def test_missing_db_is_silent(self):
        self.assertEqual(search_archive(Path(self.tmp.name) / "none.db", "柯志恩"), [])
        self.assertEqual(search_archive("", "柯志恩"), [])

    def test_web_search_merges_archive_first_and_dedupes(self):
        live = {"query": "q", "provider": "google_news", "results": [
            {"title": "柯志恩高雄造勢 喊拚翻轉 - 中央社 CNA", "url": "https://news.google.com/x"},
            {"title": "其他媒體報導", "url": "https://b/9"}]}
        with patch.object(web, "_live_search", return_value=live):
            out = web.web_search("柯志恩", archive_path=str(self.db))
        self.assertEqual(out["provider"], "news_monitor+google_news")
        self.assertEqual([r["url"] for r in out["results"]],
                         ["https://a/4", "https://a/1", "https://b/9"])
        with patch.object(web, "_live_search", return_value=live):
            self.assertIs(web.web_search("柯志恩"), live)
