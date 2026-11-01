"""Pre-deployment self-check: `python -m app.check`.

Verifies .env, the knowledge base, the news-monitor archive and network reach
(Feishu / LLM / Google News) without starting the bot. Exit code 1 on failure.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import httpx

from .agent.llm import PROVIDERS
from .config import Config
from .tools.news_archive import search_archive

_results: list[tuple[str, bool, str]] = []


def _check(name: str, ok: bool, detail: str, required: bool = True) -> None:
    _results.append((name, ok or not required, detail))
    mark = "OK  " if ok else ("FAIL" if required else "WARN")
    print(f"[{mark}] {name}: {detail}")


def main() -> int:
    cfg = Config()
    missing = cfg.missing()
    _check(".env 必填項", not missing, "齊全" if not missing else "缺少 " + ", ".join(missing))

    if cfg.db_path.is_file():
        conn = sqlite3.connect(cfg.db_path)
        n = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
             for t in ("elections", "candidates", "polls")}
        conn.close()
        _check("知識庫", n["elections"] > 0,
               f"{cfg.db_path}（選舉 {n['elections']} 屆、候選人 {n['candidates']}、民調 {n['polls']}）")
    else:
        _check("知識庫", False, f"{cfg.db_path} 不存在，先跑 python -m pipeline.run_all")

    if cfg.news_db_path:
        try:
            uri = Path(cfg.news_db_path).resolve().as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=5)
            total, latest = conn.execute(
                "SELECT COUNT(*), MAX(COALESCE(published_at, fetched_at)) FROM articles").fetchone()
            conn.close()
            hits = len(search_archive(cfg.news_db_path, "選", 5, days=7))
            _check("新聞監測庫", total > 0, f"{total} 篇，最新 {latest}，近 7 日含「選」{hits} 篇")
        except sqlite3.Error as e:
            _check("新聞監測庫", False, f"{cfg.news_db_path} 讀取失敗：{e}")
    else:
        _check("新聞監測庫", False, "NEWS_DB_PATH 未設定，只會用即時網路檢索", required=False)

    def reach(name: str, fn, required: bool = True) -> None:
        try:
            _check(name, *fn())
        except Exception as e:  # noqa: BLE001
            _check(name, False, f"{type(e).__name__}: {e}"[:160], required)

    def feishu():
        r = httpx.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                       json={"app_id": cfg.feishu_app_id, "app_secret": cfg.feishu_app_secret},
                       timeout=15).json()
        return r.get("code") == 0, "憑證有效" if r.get("code") == 0 else f"code={r.get('code')} {r.get('msg')}"

    def llm():
        base = PROVIDERS[cfg.llm_provider]["base_url"].rstrip("/")
        r = httpx.get(f"{base}/models", timeout=15,
                      headers={"Authorization": f"Bearer {cfg.llm_api_key()}"})
        return r.status_code == 200, f"{cfg.llm_provider} HTTP {r.status_code}"

    def gnews():
        r = httpx.get("https://news.google.com/rss/search?q=%E9%81%B8%E8%88%89"
                      "&hl=zh-TW&gl=TW&ceid=TW:zh-Hant", timeout=10)
        return r.status_code == 200, f"HTTP {r.status_code}"

    reach("飛書開放平台", feishu)
    reach(f"大模型 API（{cfg.llm_provider}）", llm)
    if cfg.search_provider != "none":
        reach("Google 新聞", gnews, required=False)
    if any(not ok for _, ok, _ in _results):
        print("\n有 FAIL 項，修正後再啟動機器人。")
        return 1
    print("\n全部必需項通過。WARN 項不影響啟動。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
