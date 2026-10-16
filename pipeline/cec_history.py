"""Import CEC open-data archive (votedata.zip) into the knowledge base.

Source: https://data.cec.gov.tw/選舉資料庫/votedata.zip
Filenames and CSV bodies are Big5/CP950-era encodings; the zip stores names in
cp437, so every member name is re-decoded.

Each election is a 5-CSV family inside the zip:
  elbase.csv  district code/name table       (level rows share 5 code columns)
  elcand.csv  candidates: cand_no/name/party code/.../elected flag
  elctks.csv  votes per candidate per district (col5='0000' = district aggregate)
  elpaty.csv  party code -> party name
  elprof.csv  turnout stats (not imported)

Usage:
    python -m pipeline.cec_history [--zip PATH] [--db PATH] [--kinds county_mayor,president]
"""

from __future__ import annotations

import argparse
import re
import csv
import io
import logging
import sys
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db.store import Store  # noqa: E402

log = logging.getLogger(__name__)

CEC_VOTEDATA_URL = "https://data.cec.gov.tw/選舉資料庫/votedata.zip"
DEFAULT_ZIP = Path(__file__).resolve().parents[1] / "data" / "cache" / "votedata.zip"

# (kind, year): (date, member-dir prefixes relative to voteData/)
ELECTION_DIRS: dict[tuple[str, int], tuple[str, tuple[str, ...]]] = {
    ("county_mayor", 1997): ("1997-12-13", ("1997縣市長",)),
    ("county_mayor", 2001): ("2001-12-01", ("2001縣市長",)),
    ("county_mayor", 2005): ("2005-12-03", ("2005縣市長",)),
    ("county_mayor", 2009): ("2009-12-05",
                             ("20091205-縣市長縣市議員及鄉鎮長/縣市長",)),
    ("county_mayor", 2010): ("2010-11-27",
                             ("20101127-五都市長議員及里長/市長",)),
    ("county_mayor", 2014): ("2014-11-29",
                             ("2014-103年地方公職人員選舉/直轄市市長",
                              "2014-103年地方公職人員選舉/縣市市長")),
    ("county_mayor", 2018): ("2018-11-24",
                             ("2018-107年地方公職人員選舉/直轄市市長",
                              "2018-107年地方公職人員選舉/縣市市長")),
    ("county_mayor", 2022): ("2022-11-26",
                             ("2022-111年地方公職人員選舉/C1/prv",
                              "2022-111年地方公職人員選舉/C1/city")),
    ("president", 2008): ("2008-03-22", ("2008 12任總統",)),
    ("president", 2012): ("2012-01-14", ("20120114-總統及立委/總統",)),
    ("president", 2016): ("2016-01-16", ("2016總統立委/總統",)),
    ("president", 2020): ("2020-01-11", ("2020總統立委/總統",)),
    ("president", 2024): ("2024-01-13", ("2024總統立委/總統",)),
    ("regional_legislator", 2016): ("2016-01-16", ("2016總統立委/區域立委",)),
    ("regional_legislator", 2020): ("2020-01-11", ("2020總統立委/區域立委",)),
    ("regional_legislator", 2024): ("2024-01-13", ("2024總統立委/區域立委",)),
    **{(kind, y): (d, (f"{y}總統立委/{sub}",))
       for y, d in ((2016, "2016-01-16"), (2020, "2020-01-11"), (2024, "2024-01-13"))
       for kind, sub in (("party_list", "不分區政黨"),
                         ("lowland_indigenous", "平地立委"),
                         ("highland_indigenous", "山地立委"))},
}
# indigenous seats are nationwide; village rows add bulk without analytic value.
# party_list keeps villages so party votes can be re-aggregated by 選區.
NO_VILLAGE_KINDS = {"lowland_indigenous", "highland_indigenous"}
DISTRICT_NAME_RE = re.compile(r"第\d+選區$")

COUNTY_FIX = {
    "台北市": "臺北市", "桃園縣": "桃園市", "台中縣": "臺中縣", "台中市": "臺中市",
    "台南縣": "臺南縣", "台南市": "臺南市", "高雄縣": "高雄縣",
    "宜蘭縣": "宜蘭縣", "台北縣": "新北市", "臺北縣": "新北市",
    "台東縣": "臺東縣", "連江縣": "連江縣",
}
# Pre-2010 五都升格/合併 old county names stay as-is (台北縣→新北市 handled above;
# 台中縣/台南縣/高雄縣 keep their historical names since the entities differed).


def decode_zip_name(raw: str) -> str:
    for enc in ("cp950", "big5", "utf-8"):
        try:
            return raw.encode("cp437").decode(enc)
        except Exception:
            continue
    return raw


def decode_csv(content: bytes) -> list[list[str]]:
    for enc in ("utf-8-sig", "cp950", "big5"):
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return []
    rows = []
    for row in csv.reader(io.StringIO(text)):
        row = [(v or "").strip().strip("'\"").strip() for v in row]
        if row and any(row):
            rows.append(row)
    return rows


def download(dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 1_000_000:
        log.info("archive already at %s", dest)
        return dest
    url = urllib.parse.quote(CEC_VOTEDATA_URL, safe=":/?=&%")
    log.info("downloading %s", url)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=300) as r, open(dest, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    log.info("saved %s (%.1f MB)", dest, dest.stat().st_size / 1e6)
    return dest


def _canon_county(name: str) -> str:
    return COUNTY_FIX.get(name.strip(), name.strip())


def import_family(zf: zipfile.ZipFile, name_map: dict[str, str],
                  prefix: str, store: Store, election_id: int,
                  seen_counties: set[str], keep_villages: bool = True) -> int:
    """Import one 5-CSV family. Returns rows written."""
    def find(suffix: str) -> str | None:
        want = f"{prefix}/{suffix}"
        if want in name_map:
            return name_map[want]
        # 2016-era archives suffix filenames, e.g. elctks_P1.csv — match by stem
        stem = suffix.split(".")[0]
        for decoded, raw in name_map.items():
            base = decoded.rsplit("/", 1)[-1]
            if decoded.startswith(prefix + "/") and base.startswith(stem) \
                    and base.endswith(".csv"):
                return raw
        return None

    raw_base, raw_cand = find("elbase.csv"), find("elcand.csv")
    raw_ctks, raw_paty = find("elctks.csv"), find("elpaty.csv")
    if not all((raw_base, raw_cand, raw_ctks, raw_paty)):
        log.warning("incomplete family under %s", prefix)
        return 0

    # party code -> name
    parties = {}
    for r in decode_csv(zf.read(raw_paty)):
        if len(r) >= 2:
            parties[r[0]] = r[1]

    # district code -> canonical name. The county is keyed by (c1, c2):
    # most files use c2, but C1/prv (直轄市) puts the city in c1 and '000' in c2.
    county_name: dict[tuple[str, str], str] = {}
    # legislator 選區 rows share c4='000' with the county row; c3 is the district code
    district_name: dict[tuple[str, str, str], str] = {}
    township_name: dict[tuple[str, str, str], str] = {}
    village_name: dict[tuple[str, str, str, str], str] = {}
    for r in decode_csv(zf.read(raw_base)):
        if len(r) < 6:
            continue
        c1, c2, c3, c4, c5, name = r[:6]
        if c4 == "000" and DISTRICT_NAME_RE.search(name):
            district_name[(c1, c2, c3)] = _canon_county(name[:3]) + name[3:]
        elif c4 == "000":                    # county-level row (or 全國)
            county_name[(c1, c2)] = _canon_county(name)
        elif c5 == "0000":                   # township-level row
            township_name[(c1, c2, c4)] = name
        else:                                # village-level row
            village_name[(c1, c2, c4, c5)] = name

    # candidates: key (c1, c2, c3, cand_no) -> (name, party, elected)
    cands: dict[tuple[str, str, str, str], tuple[str, str, int]] = {}
    for r in decode_csv(zf.read(raw_cand)):
        if len(r) < 14:
            continue
        c1, c2, c3, cand_no, name, party_code = r[0], r[1], r[2], r[5], r[6], r[7]
        # presidential tickets list 正/副 candidates on consecutive rows sharing
        # cand_no, 正 first — keep the first row per (county, district, cand_no)
        if (c1, c2, c3, cand_no) in cands:
            continue
        elected = 1 if "*" in r[13:16] else 0
        cands[(c1, c2, c3, cand_no)] = (
            name,
            parties.get(party_code, "無黨籍" if party_code in ("999", "") else party_code),
            elected,
        )

    n_votes = 0
    cand_rows, vote_rows = [], []
    for r in decode_csv(zf.read(raw_ctks)):
        # col5 is the precinct code; an all-zeros value ('0'/'000'/'0000')
        # marks a district aggregate row (county/township/village totals)
        if len(r) < 9 or r[5].strip("0") != "":
            continue
        c1, c2, c3, c4, c5 = r[0], r[1], r[2], r[3], r[4]
        if c5 != "0000" and not keep_villages:
            continue
        cand_no, votes_s, share_s, flag = r[6], r[7], r[8], r[9] if len(r) > 9 else ""
        county = county_name.get((c1, c2), f"{c1}/{c2}")
        township, village = "", ""
        if c4 != "000" and c5 == "0000":
            township = township_name.get((c1, c2, c4), c4)
        elif c5 != "0000":
            village = village_name.get((c1, c2, c4, c5), c5)
            township = township_name.get((c1, c2, c4), c4)
        if c1 == "00" and c2 == "000":
            county = "全國"
        try:
            votes_i = int(votes_s.replace(",", ""))
        except ValueError:
            continue
        try:
            share_f = float(share_s) if share_s else None
        except ValueError:
            share_f = None
        vote_rows.append({
            "county": county, "district": district_name.get((c1, c2, c3), ""),
            "township": township, "village": village,
            "cand_no": int(cand_no), "votes": votes_i, "share": share_f,
            "elected": 1 if "*" in flag else 0,
        })
        n_votes += 1
        seen_counties.add(county)

    for (c1, c2, c3, cand_no), (name, party, elected) in cands.items():
        county = "全國" if (c1, c2) == ("00", "000") else county_name.get((c1, c2), f"{c1}/{c2}")
        cand_rows.append({
            "county": county, "district": district_name.get((c1, c2, c3), ""),
            "cand_no": int(cand_no),
            "name": name, "party": party, "elected": elected,
        })

    # merge into store (candidates rows are county-scoped; votes keep level)
    store.conn.executemany(
        "INSERT INTO candidates(election_id, county, district, cand_no, name, party, elected)"
        " VALUES (?,?,?,?,?,?,?)",
        [(election_id, c["county"], c["district"], c["cand_no"], c["name"], c["party"],
          c["elected"]) for c in cand_rows],
    )
    store.conn.executemany(
        "INSERT INTO votes(election_id, county, district, township, village, cand_no, votes, share, elected)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        [(election_id, v["county"], v["district"], v["township"], v["village"], v["cand_no"],
          v["votes"], v["share"], v["elected"]) for v in vote_rows],
    )
    store.conn.commit()
    return n_votes


def run(zip_path: Path, db_path: Path, kinds: set[str]) -> None:
    store = Store(db_path)
    with zipfile.ZipFile(zip_path) as zf:
        name_map = {}
        for raw in zf.namelist():
            name_map[decode_zip_name(raw).lstrip("/")] = raw
        # strip leading "votedata/votedata/" (or similar) once
        root_prefix = ""
        for k in name_map:
            if "/voteData/" in k:
                root_prefix = k.split("/voteData/")[0] + "/voteData/"
                break

        for (kind, year), (date, prefixes) in sorted(
                ELECTION_DIRS.items(), key=lambda x: x[0][1]):
            if kind not in kinds:
                continue
            eid = store.upsert_election(kind, year, date)
            store.replace_election_rows(eid, [], [])
            total = 0
            for p in prefixes:
                total += import_family(zf, name_map, root_prefix + p,
                                       store, eid, set(),
                                       keep_villages=kind not in NO_VILLAGE_KINDS)
            log.info("%s %s: %d vote rows", kind, year, total)
    store.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", type=Path, default=DEFAULT_ZIP)
    ap.add_argument("--db", type=Path,
                    default=Path(__file__).resolve().parents[1] / "data" / "kb.sqlite3")
    ap.add_argument("--kinds", default=",".join(sorted({k for k, _ in ELECTION_DIRS})))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    download(args.zip)
    run(args.zip, args.db, set(args.kinds.split(",")))


if __name__ == "__main__":
    main()
