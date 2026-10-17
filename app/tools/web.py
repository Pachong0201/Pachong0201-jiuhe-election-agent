"""Real-time retrieval tools: web search + page fetch.

Search providers:
- auto       (default) Google News RSS first (zh-TW), duckduckgo as fallback
- gnews      Google News RSS only
- duckduckgo (no key needed; flaky for CJK bursts)
- tavily     (needs TAVILY_API_KEY)

fetch_url extracts main text heuristically and truncates for LLM context.
"""

from __future__ import annotations

import re
import urllib.parse
from html.parser import HTMLParser
from typing import Optional
from xml.etree import ElementTree as ET

import httpx

from .news_archive import search_archive

_UA = {"User-Agent": "Mozilla/5.0 (jiuhe-election-agent)"}
_MAX_CHARS = 6000


def web_search(query: str, max_results: int = 6, provider: str = "auto",
               tavily_key: str = "", archive_path: str = "") -> dict:
    """Team news-monitor archive first, then the live provider fills in."""
    local = search_archive(archive_path, query, max_results) if archive_path else []
    live = _live_search(query, max_results, provider, tavily_key)
    if not local:
        return live
    seen = {_title_key(r["title"]) for r in local} | {r["url"] for r in local}
    extra = [r for r in live.get("results", [])
             if r.get("url") not in seen and _title_key(r.get("title", "")) not in seen]
    return {"query": query, "provider": f"news_monitor+{live.get('provider', '')}",
            "results": local + extra}


def _title_key(title: str) -> str:
    """Google News titles carry a ' - 媒體名' suffix; compare on the headline only."""
    return re.sub(r"\s+-\s+[^-]+$", "", title).replace(" ", "")


def _live_search(query: str, max_results: int, provider: str, tavily_key: str) -> dict:
    if provider == "tavily":
        return _tavily(query, max_results, tavily_key)
    if provider in ("auto", "gnews"):
        try:
            out = _gnews(query, max_results)
            if out["results"] or provider == "gnews":
                return out
        except Exception:  # noqa: BLE001 — fall through to ddg
            pass
    return _ddg(query, max_results)


def _gnews(query: str, max_results: int) -> dict:
    """Google News RSS search (zh-TW): reliable, current, includes pubDate."""
    q = urllib.parse.quote(query)
    url = ("https://news.google.com/rss/search"
           f"?q={q}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant")
    resp = httpx.get(url, headers=_UA, timeout=20)
    resp.raise_for_status()
    out = []
    for it in list(ET.fromstring(resp.content).iter("item"))[:max_results]:
        snippet = re.sub(r"<[^>]+>", "", it.findtext("description", ""))
        out.append({
            "title": it.findtext("title", ""),
            "url": it.findtext("link", ""),
            "snippet": snippet[:300],
            "published": it.findtext("pubDate", ""),
        })
    return {"query": query, "provider": "google_news", "results": out}


def _ddg(query: str, max_results: int) -> dict:
    try:
        from ddgs import DDGS  # package renamed from duckduckgo_search
    except ImportError:
        from duckduckgo_search import DDGS  # type: ignore
    out = []
    with DDGS() as ddgs:
        # region="tw-tzh" returns empty/garbage for CJK queries via the bing
        # backend; wt-wt (worldwide) works for both zh and en.
        for r in ddgs.text(query, region="wt-wt", max_results=max_results):
            out.append({
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
            })
    return {"query": query, "provider": "duckduckgo", "results": out}


def _tavily(query: str, max_results: int, api_key: str) -> dict:
    resp = httpx.post(
        "https://api.tavily.com/search",
        json={"query": query, "max_results": max_results,
              "api_key": api_key, "search_depth": "basic"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "query": query,
        "provider": "tavily",
        "results": [
            {"title": r.get("title", ""), "url": r.get("url", ""),
             "snippet": r.get("content", "")}
            for r in data.get("results", [])
        ],
    }


class _TextExtractor(HTMLParser):
    """Crude main-text extractor: collects <p>/<h*> text, skips script/style."""

    def __init__(self):
        super().__init__()
        self._skip = 0
        self._tag = ""
        self.chunks: list[str] = []

    def handle_starttag(self, tag, attrs):
        self._tag = tag
        if tag in ("script", "style", "noscript", "header", "footer", "nav"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "header", "footer", "nav") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip:
            return
        if self._tag in ("p", "h1", "h2", "h3", "li", "td", "title"):
            text = re.sub(r"\s+", " ", data).strip()
            if text:
                self.chunks.append(text)


def fetch_url(url: str, max_chars: int = _MAX_CHARS) -> dict:
    try:
        resp = httpx.get(url, headers=_UA, timeout=30, follow_redirects=True)
        resp.raise_for_status()
    except Exception as e:  # noqa: BLE001 — surface failure to the agent
        return {"url": url, "ok": False, "error": str(e)[:200]}

    ctype = resp.headers.get("content-type", "")
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        return _fetch_pdf(resp.content, url, max_chars)

    parser = _TextExtractor()
    try:
        parser.feed(resp.text)
    except Exception:  # noqa: BLE001
        pass
    text = "\n".join(parser.chunks)[:max_chars]
    return {"url": url, "ok": True, "content_type": ctype, "text": text}


def _fetch_pdf(content: bytes, url: str, max_chars: int) -> dict:
    import io
    try:
        import pdfplumber
    except ImportError:
        return {"url": url, "ok": False, "error": "pdfplumber not installed"}
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            text = "\n".join(
                (p.extract_text() or "") for p in pdf.pages[:10]
            )[:max_chars]
    except Exception as e:  # noqa: BLE001
        return {"url": url, "ok": False, "error": f"pdf parse failed: {e}"[:200]}
    return {"url": url, "ok": True, "content_type": "application/pdf", "text": text}
