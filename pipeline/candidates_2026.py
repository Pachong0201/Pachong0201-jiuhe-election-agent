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


def _header_index(row: list) -> dict:
    """Map roster header labels to column indexes; {} if not a header row."""
    idx: dict[str, int] = {}
    for i, c in enumerate(row):
        c = re.sub(r"\s+", "", c or "")
        if "姓名" in c:
            idx.setdefault("name", i)
        if "政黨" in c:
            idx.setdefault("party", i)
        if "選舉區" in c or c in COUNTY_ALIASES:
            idx.setdefault("county", i)
    return idx if "name" in idx else {}


def _norm_party(raw: str) -> str:
    p = re.sub(r"\s+", "", raw or "")
    return {"": "", "無": "無黨籍", "无": "無黨籍"}.get(p, p)


def _extract_row(row: list, cols: dict, county_pat: str):
    """Return (county, name, party) or None. Uses header columns when known,
    else falls back to CJK-name heuristics."""
    cells = [(c or "").strip() for c in row]
    if cols:
        name = cells[cols["name"]] if cols["name"] < len(cells) else ""
        if not re.fullmatch(r"[一-鿿]{2,5}", name):
            return None
        party = _norm_party(
            cells[cols["party"]] if cols.get("party", 9**9) < len(cells) else "")
        joined = " ".join(c for c in cells if c)
        m = re.search(county_pat, joined)
        if not m:
            return None
        return COUNTY_ALIASES.get(m.group(0), m.group(0)), name, party
    filled = [c for c in cells if c]
    if len(filled) < 2:
        return None
    joined = " ".join(filled)
    m = re.search(county_pat, joined)
    if not m:
        return None
    names = [c for c in filled
             if re.fullmatch(r"[一-鿿]{2,4}", c)
             and c not in COUNTY_ALIASES
             and not re.search(county_pat, c)]
    if not names:
        return None
    return COUNTY_ALIASES.get(m.group(0), m.group(0)), names[0], ""


def parse_pdf(content: bytes, source: str, store: Store,
              cycle: str, office: str) -> tuple[int, int]:
    """Best-effort extraction of (county, name, party) triples from a PDF."""
    import pdfplumber

    kept, dropped = 0, 0
    county_pat = "|".join(sorted(COUNTIES, key=len, reverse=True))
    cols: dict = {}
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            table = page.extract_table() or []
            for row in table:
                hdr = _header_index(row)
                if hdr:
                    cols = hdr
                    continue
                got = _extract_row(row, cols, county_pat)
                if not got:
                    if row and any((c or "").strip() for c in row):
                        dropped += 1
                    continue
                county, name, party = got
                store.upsert_race_candidate(
                    cycle=cycle, county=county, name=name, office=office,
                    party=party, status="registered", source=source,
                )
                kept += 1
    return kept, dropped


def parse_xlsx(content: bytes, source: str, store: Store,
               cycle: str, office: str) -> tuple[int, int]:
    import openpyxl

    kept, dropped = 0, 0
    county_pat = "|".join(sorted(COUNTIES, key=len, reverse=True))
    cols: dict = {}
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            row = [str(c) if c is not None else "" for c in row]
            hdr = _header_index(row)
            if hdr:
                cols = hdr
                continue
            got = _extract_row(row, cols, county_pat)
            if not got:
                if any(c.strip() for c in row):
                    dropped += 1
                continue
            county, name, party = got
            store.upsert_race_candidate(
                cycle=cycle, county=county, name=name, office=office,
                party=party, status="registered", source=source,
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
