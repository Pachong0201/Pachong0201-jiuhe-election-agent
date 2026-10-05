"""Import the CEC 2026 local-elections registration roster into race_candidates.

Source page: https://web.cec.gov.tw/central/article/64733
The page links to the official roster files (PDF/XLSX) per office type; we pick
the 直轄市長/縣市長 roster, download it and parse candidate rows.

The roster format changes between publications, so parsing is best-effort:
any row containing a known county name and a personal name is captured with
status='registered'. Rows we cannot parse are counted and logged, never guessed.

Usage:
    python -m pipeline.candidates_2026 [--db PATH] [--page URL]
"""

from __future__ import annotations

import argparse
import io
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

DEFAULT_PAGE = "https://web.cec.gov.tw/central/article/64733"
CACHE = Path(__file__).resolve().parents[1] / "data" / "cache"

COUNTIES = sorted(set(COUNTY_ALIASES.values()))


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str]] = []  # (href, text)
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


def office_from_label(label: str) -> str:
    """Classify a roster file's office type from its link text / URL."""
    if "直轄市長" in label or "縣市長" in label or "縣市市長" in label:
        return "mayor"
    if "議員" in label:
        return "councilor"
    if "鄉鎮市長" in label:
        return "township_mayor"
    if "代表" in label:
        return "township_rep"
    if "里長" in label or "村長" in label:
        return "village_chief"
    return "unknown"


def find_roster_links(page_url: str) -> list[tuple[str, str]]:
    """(url, link_text) pairs for roster files linked on the CEC page."""
    html = _get(page_url).decode("utf-8", errors="replace")
    parser = _LinkParser()
    parser.feed(html)
    out = []
    for href, text in parser.links:
        if href.lower().endswith((".pdf", ".xls", ".xlsx", ".ods")):
            out.append((urllib.parse.urljoin(page_url, href), text))
    return out


def parse_pdf(content: bytes, source: str, store: Store,
              cycle: str, office: str) -> tuple[int, int]:
    """Best-effort extraction of (county, name, party) triples from a PDF."""
    import pdfplumber

    kept, dropped = 0, 0
    county_pat = "|".join(sorted(COUNTIES, key=len, reverse=True))
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            table = page.extract_table() or []
            for row in table:
                cells = [(c or "").strip() for c in row if (c or "").strip()]
                if len(cells) < 2:
                    continue
                joined = " ".join(cells)
                m = re.search(county_pat, joined)
                if not m:
                    continue
                county = COUNTY_ALIASES.get(m.group(0), m.group(0))
                # name: the shortest CJK-only cell that isn't the county
                names = [
                    c for c in cells
                    if re.fullmatch(r"[一-鿿]{2,4}", c)
                    and c not in COUNTY_ALIASES
                    and not re.search(county_pat, c)
                ]
                if not names:
                    dropped += 1
                    continue
                store.upsert_race_candidate(
                    cycle=cycle, county=county, name=names[0], office=office,
                    status="registered", source=source,
                )
                kept += 1
    return kept, dropped


def parse_xlsx(content: bytes, source: str, store: Store,
               cycle: str, office: str) -> tuple[int, int]:
    import openpyxl

    kept, dropped = 0, 0
    county_pat = "|".join(sorted(COUNTIES, key=len, reverse=True))
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            cells = [str(c).strip() for c in row if c and str(c).strip()]
            if len(cells) < 2:
                continue
            joined = " ".join(cells)
            m = re.search(county_pat, joined)
            if not m:
                continue
            county = COUNTY_ALIASES.get(m.group(0), m.group(0))
            names = [
                c for c in cells
                if re.fullmatch(r"[一-鿿]{2,4}", c)
                and c not in COUNTY_ALIASES
                and not re.search(county_pat, c)
            ]
            if not names:
                dropped += 1
                continue
            store.upsert_race_candidate(
                cycle=cycle, county=county, name=names[0], office=office,
                status="registered", source=source,
            )
            kept += 1
    return kept, dropped


def run(page_url: str, db_path: Path, cycle: str) -> None:
    store = Store(db_path)
    links = find_roster_links(page_url)
    log.info("found %d roster link(s)", len(links))
    total_kept = total_dropped = 0
    for url, text in links:
        office = office_from_label(text + " " + url)
        log.info("fetching %s (office=%s, label=%s)", url, office, text[:40])
        try:
            content = _get(url)
        except Exception as e:  # noqa: BLE001
            log.warning("download failed: %s", e)
            continue
        local = CACHE / ("roster_" + url.rsplit("/", 1)[-1])
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(content)
        try:
            if url.lower().endswith(".pdf"):
                kept, dropped = parse_pdf(content, url, store, cycle, office)
            elif url.lower().endswith((".xls", ".xlsx", ".ods")):
                kept, dropped = parse_xlsx(content, url, store, cycle, office)
            else:
                continue
        except Exception as e:  # noqa: BLE001
            log.warning("parse failed for %s: %s", url, e)
            continue
        total_kept += kept
        total_dropped += dropped
        log.info("%s: %d kept, %d unparseable", url, kept, dropped)
    log.info("done: %d candidates upserted, %d rows skipped",
             total_kept, total_dropped)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", default=DEFAULT_PAGE)
    ap.add_argument("--db", type=Path,
                    default=Path(__file__).resolve().parents[1] / "data" / "kb.sqlite3")
    ap.add_argument("--cycle", default="2026")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run(args.page, args.db, args.cycle)


if __name__ == "__main__":
    main()
