"""Scrape the TVBS poll center for county-mayor polls and import into `polls`.

TVBS publishes each poll as a PDF linked from https://www.tvbs.com.tw/poll-center.
Parsing keeps the poll's metadata (org/date/method/sample) and the reported
support numbers; anything unparseable is skipped with the raw PDF kept in cache
for audit.

Usage:
    python -m pipeline.polls_tvbs [--db PATH] [--since YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import re
import sys
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db.store import Store, COUNTY_ALIASES  # noqa: E402

log = logging.getLogger(__name__)

INDEX_URL = "https://www.tvbs.com.tw/poll-center"
CACHE = Path(__file__).resolve().parents[1] / "data" / "cache" / "polls"
COUNTY_PAT = "|".join(sorted(set(COUNTY_ALIASES.values()), key=len, reverse=True))


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href = ""
        self._text = ""

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href", "")
            self._text = ""

    def handle_data(self, data):
        if self._href:
            self._text += data

    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            self.links.append((self._href, self._text.strip()))
            self._href = ""


def _get(url: str) -> bytes:
    req = urllib.request.Request(
        urllib.parse.quote(url, safe=":/?=&%"),
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def find_poll_links(index_url: str = INDEX_URL) -> list[tuple[str, str]]:
    """(title, url) pairs for poll PDFs/pages that mention a mayor race."""
    html = _get(index_url).decode("utf-8", errors="replace")
    parser = _LinkParser()
    parser.feed(html)
    out = []
    for href, text in parser.links:
        if re.search(f"({COUNTY_PAT}).*(市長|縣長|選情|民調)", text):
            out.append((text, urllib.parse.urljoin(index_url, href)))
    return out


# Common CJK variant mismatches between the CEC roster and media text
# (roster has 張啓楷, news writes 張啟楷). Keys normalize to the value char.
_VARIANTS = {"啓": "啟", "栢": "柏", "台": "臺"}


def _norm_name(s: str) -> str:
    return "".join(_VARIANTS.get(ch, ch) for ch in s)


def parse_poll_pdf(content: bytes, names: list[str],
                   source_url: str = "") -> dict:
    """Extract fieldwork date, method, sample, and candidate support from a
    TVBS poll PDF. Candidate numbers are anchored to known roster names via
    explicit 支持度 phrasing — methodology figures (信心水準, 拒訪率) are never
    captured."""
    import pdfplumber

    text_parts = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for p in pdf.pages[:4]:
            text_parts.append(p.extract_text() or "")
    text = "\n".join(text_parts)

    meta: dict = {"results": {}}

    # 訪問時間 | 115 年 4 月 24 日至 30 日 → fieldwork_end (ROC year + 1911)
    m = re.search(
        r"訪問時間[^\n]*?(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*\d{1,2}\s*日"
        r"[^\n]*?至\s*(?:\d{1,2}\s*月\s*)?(\d{1,2})\s*日", text)
    if m:
        meta["fieldwork_end"] = (f"{int(m.group(1)) + 1911}-"
                                 f"{int(m.group(2)):02d}-{int(m.group(3)):02d}")
    # publish_date from URL path /poll_center/2026/20260505/
    m = re.search(r"poll_center/(\d{4})/(\d{4})(\d{2})(\d{2})/", source_url)
    if m:
        meta["publish_date"] = f"{m.group(2)}-{m.group(3)}-{m.group(4)}"
    else:
        m = re.search(r"(20\d{2})\s*年?\s*(\d{1,2})\s*月?\s*(\d{1,2})\s*日", text)
        if m:
            meta["publish_date"] = (f"{m.group(1)}-{int(m.group(2)):02d}-"
                                    f"{int(m.group(3)):02d}")
    m = re.search(r"有效樣本[^\d\n]{0,12}(\d{3,5})", text)
    if m:
        meta["sample_n"] = int(m.group(1))
    m = re.search(r"調查方法[^\n]*", text)
    if m:
        meta["method"] = re.sub(r"^調查方法\s*[|：:]\s*", "", m.group(0))[:80]

    # candidate support, anchored to roster names (variant-normalized)
    text_n = _norm_name(text)
    for name in names:
        pats = [
            re.escape(_norm_name(name)) + r"(?:的)?支持度(?:為|是|：)?\s*[約達]?\s*"
                              r"(\d+(?:\.\d+)?)\s*%",
            r"(\d+(?:\.\d+)?)\s*%[^\d%]{0,6}?" + re.escape(_norm_name(name)),
            re.escape(_norm_name(name)) + r"\s*[（(]\s*(\d+(?:\.\d+)?)\s*%",
        ]
        for pat in pats:
            m = re.search(pat, text_n)
            if m:
                meta["results"][name] = float(m.group(1))
                break
    m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:未決定|尚未決定|未表示)", text)
    if m:
        meta["results"]["未決定"] = float(m.group(1))
    return meta


def run(db_path: Path, index_url: str, since: str = "") -> None:
    store = Store(db_path)
    CACHE.mkdir(parents=True, exist_ok=True)
    links = find_poll_links(index_url)
    log.info("found %d candidate poll link(s)", len(links))

    for title, url in links:
        try:
            content = _get(url)
        except Exception as e:  # noqa: BLE001
            log.warning("fetch failed %s: %s", url, e)
            continue
        fname = re.sub(r"[^\w.-]+", "_", title)[:60] or url.rsplit("/", 1)[-1]
        raw_path = CACHE / (fname + (".pdf" if ".pdf" in url.lower() else ".html"))
        raw_path.write_bytes(content)
        if ".pdf" not in url.lower():
            continue  # index pages we can't parse yet — file kept for audit
        county = ""
        m = re.search(COUNTY_PAT, title)
        if m:
            county = COUNTY_ALIASES.get(m.group(0), m.group(0))
        names = [r["name"] for r in
                 store.race_candidates("2026", county)] if county else []
        try:
            meta = parse_poll_pdf(content, names, url)
        except Exception as e:  # noqa: BLE001
            log.warning("parse failed %s: %s", url, e)
            continue
        if since and meta.get("publish_date", "") < since:
            continue
        store.add_poll(
            org="TVBS",
            publish_date=meta.get("publish_date", ""),
            fieldwork_end=meta.get("fieldwork_end", ""),
            county=county,
            method=meta.get("method", ""),
            sample_n=meta.get("sample_n"),
            results=meta.get("results", {}),
            source_url=url,
            raw_path=str(raw_path),
        )
        log.info("stored poll: %s (%s, n=%s, %d results)",
                 title[:40], meta.get("publish_date"), meta.get("sample_n"),
                 len(meta.get("results", {})))
    store.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path,
                    default=Path(__file__).resolve().parents[1] / "data" / "kb.sqlite3")
    ap.add_argument("--index", default=INDEX_URL)
    ap.add_argument("--since", default="")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run(args.db, args.index, args.since)


if __name__ == "__main__":
    main()
