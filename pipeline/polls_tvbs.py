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


def parse_poll_pdf(content: bytes) -> dict:
    """Extract publish date + support table from a TVBS poll PDF."""
    import pdfplumber

    text_parts = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for p in pdf.pages[:4]:
            text_parts.append(p.extract_text() or "")
    text = "\n".join(text_parts)

    meta: dict = {"results": {}}
    m = re.search(r"(20\d{2})\s*年?\s*(\d{1,2})\s*月?\s*(\d{1,2})\s*日", text)
    if m:
        meta["publish_date"] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"有效樣本[：: ]*(\d{3,5})", text)
    if m:
        meta["sample_n"] = int(m.group(1))
    m = re.search(r"(市話|手機|網路|CATI|RDD)[^\n]{0,20}", text)
    if m:
        meta["method"] = m.group(0).strip()

    # support rows look like: 候選人名字  23.5  or 名字（政黨）23.5%
    for line in text.split("\n"):
        for name, pct in re.findall(
                r"([一-鿿]{2,4})[^\d]{0,12}(\d{1,2}\.\d)\s*%?", line):
            meta["results"].setdefault(name, float(pct))
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
        try:
            meta = parse_poll_pdf(content)
        except Exception as e:  # noqa: BLE001
            log.warning("parse failed %s: %s", url, e)
            continue
        if since and meta.get("publish_date", "") < since:
            continue
        county = ""
        m = re.search(COUNTY_PAT, title)
        if m:
            county = COUNTY_ALIASES.get(m.group(0), m.group(0))
        store.add_poll(
            org="TVBS",
            publish_date=meta.get("publish_date", ""),
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
