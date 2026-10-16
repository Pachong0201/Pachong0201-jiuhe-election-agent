"""SQLite access layer for the election knowledge base.

The DB lives at ``data/kb.sqlite3`` by default (configurable via KB_DB_PATH).
All query helpers return plain dicts so tool results serialize cleanly to JSON.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "kb.sqlite3"

COUNTY_ALIASES = {
    "台北市": "臺北市", "臺北市": "臺北市", "台北": "臺北市",
    "新北市": "新北市", "新北": "新北市",
    "桃园市": "桃園市", "桃園市": "桃園市", "桃園": "桃園市", "桃园": "桃園市",
    "台中市": "臺中市", "臺中市": "臺中市", "台中": "臺中市",
    "台南市": "臺南市", "臺南市": "臺南市", "台南": "臺南市",
    "高雄市": "高雄市", "高雄": "高雄市",
    "宜兰县": "宜蘭縣", "宜蘭縣": "宜蘭縣", "宜蘭": "宜蘭縣",
    "新竹县": "新竹縣", "新竹縣": "新竹縣",
    "苗栗县": "苗栗縣", "苗栗縣": "苗栗縣",
    "彰化县": "彰化縣", "彰化縣": "彰化縣",
    "南投县": "南投縣", "南投縣": "南投縣",
    "云林县": "雲林縣", "雲林縣": "雲林縣",
    "嘉义县": "嘉義縣", "嘉義縣": "嘉義縣",
    "屏东县": "屏東縣", "屏東縣": "屏東縣",
    "台东县": "臺東縣", "臺東縣": "臺東縣",
    "花莲县": "花蓮縣", "花蓮縣": "花蓮縣",
    "澎湖县": "澎湖縣", "澎湖縣": "澎湖縣",
    "基隆市": "基隆市", "基隆": "基隆市",
    "新竹市": "新竹市",
    "嘉义市": "嘉義市", "嘉義市": "嘉義市",
    "金门县": "金門縣", "金門縣": "金門縣", "金門": "金門縣",
    "连江县": "連江縣", "連江縣": "連江縣", "連江": "連江縣", "馬祖": "連江縣",
}


def normalize_county(name: str) -> str:
    """Map a user-supplied county name onto the canonical form used in the DB."""
    n = (name or "").strip()
    if n in COUNTY_ALIASES:
        return COUNTY_ALIASES[n]
    for alias, canon in COUNTY_ALIASES.items():
        if n and (n in canon or n == alias):
            return canon
    return n


_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_number(t: str) -> int:
    """Parse 1-99 written as arabic/fullwidth digits or Chinese numerals (三, 十二)."""
    t = t.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    if t.isdigit():
        return int(t)
    if "十" in t:
        tens, _, ones = t.partition("十")
        return _CN_DIGITS.get(tens, 1) * 10 + _CN_DIGITS.get(ones, 0)
    return _CN_DIGITS.get(t, 0)


def normalize_district(county: str, number: str | int) -> str:
    """('台北', '三') -> '臺北市第03選區' — the 選區 naming used by CEC data."""
    n = number if isinstance(number, int) else _cn_number(str(number))
    return f"{normalize_county(county)}第{n:02d}選區"


class Store:
    def __init__(self, path: str | Path = DEFAULT_DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        # columns added after the first release; CREATE TABLE IF NOT EXISTS skips them
        for table in ("votes", "candidates", "race_candidates", "polls"):
            cols = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if "district" not in cols:
                self.conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN district TEXT NOT NULL DEFAULT ''")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_votes_district"
                          " ON votes(election_id, county, district)")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------ write

    def upsert_election(self, kind: str, year: int, date: str) -> int:
        # cursor.lastrowid is stale when the upsert takes the UPDATE branch
        self.conn.execute(
            "INSERT INTO elections(kind, year, date) VALUES (?,?,?) "
            "ON CONFLICT(kind, year) DO UPDATE SET date=excluded.date",
            (kind, year, date),
        )
        row = self.conn.execute(
            "SELECT id FROM elections WHERE kind=? AND year=?", (kind, year)
        ).fetchone()
        return int(row["id"])

    def replace_election_rows(
        self,
        election_id: int,
        candidates: Iterable[dict],
        votes: Iterable[dict],
    ) -> None:
        self.conn.execute("DELETE FROM candidates WHERE election_id=?", (election_id,))
        self.conn.execute("DELETE FROM votes WHERE election_id=?", (election_id,))
        self.conn.executemany(
            "INSERT INTO candidates(election_id, county, district, cand_no, name, party, elected)"
            " VALUES (?,?,?,?,?,?,?)",
            [
                (
                    election_id,
                    c["county"],
                    c.get("district", ""),
                    c["cand_no"],
                    c["name"],
                    c["party"],
                    int(c.get("elected", 0)),
                )
                for c in candidates
            ],
        )
        self.conn.executemany(
            "INSERT INTO votes(election_id, county, district, township, village, cand_no, votes, share, elected)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (
                    election_id,
                    v["county"],
                    v.get("district", ""),
                    v.get("township", ""),
                    v.get("village", ""),
                    v["cand_no"],
                    v["votes"],
                    v.get("share"),
                    int(v.get("elected", 0)),
                )
                for v in votes
            ],
        )
        self.conn.commit()

    def upsert_race_candidate(
        self,
        cycle: str,
        county: str,
        name: str,
        party: str = "",
        office: str = "",
        status: str = "declared",
        incumbent: bool = False,
        source: str = "",
        updated_at: str = "",
        district: str = "",
    ) -> None:
        self.conn.execute(
            "INSERT INTO race_candidates(cycle, county, district, name, party, office, status,"
            " incumbent, source, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(cycle, county, name, office) DO UPDATE SET"
            " district=excluded.district, party=excluded.party, status=excluded.status,"
            " incumbent=excluded.incumbent, source=excluded.source, updated_at=excluded.updated_at",
            (cycle, normalize_county(county), district, name, party, office, status,
             int(incumbent), source, updated_at),
        )
        self.conn.commit()

    def add_poll(self, **kw: Any) -> int:
        cur = self.conn.execute(
            "INSERT INTO polls(org, publish_date, fieldwork_end, county, district, race, method,"
            " sample_n, results_json, source_url, raw_path)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                kw.get("org", ""),
                kw.get("publish_date", ""),
                kw.get("fieldwork_end", ""),
                normalize_county(kw.get("county", "")),
                kw.get("district", ""),
                kw.get("race", "county_mayor"),
                kw.get("method", ""),
                kw.get("sample_n"),
                json.dumps(kw.get("results", {}), ensure_ascii=False),
                kw.get("source_url", ""),
                kw.get("raw_path", ""),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def add_event(self, county: str, date: str, kind: str, summary: str,
                  source_url: str = "", created_at: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO events(county, date, kind, summary, source_url, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (normalize_county(county), date, kind, summary, source_url, created_at),
        )
        row_id = int(cur.lastrowid)
        self.conn.execute(
            "INSERT INTO kb_fts(text, content_type, row_id) VALUES (?,?,?)",
            (summary, "event", row_id),
        )
        self.conn.commit()
        return row_id

    def add_knowledge(self, county: str, topic: str, claim: str,
                      valid_from: str = "", valid_to: str = "", source: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO knowledge(county, topic, claim, valid_from, valid_to, source)"
            " VALUES (?,?,?,?,?,?)",
            (normalize_county(county), topic, claim, valid_from, valid_to, source),
        )
        row_id = int(cur.lastrowid)
        self.conn.execute(
            "INSERT INTO kb_fts(text, content_type, row_id) VALUES (?,?,?)",
            (claim, "knowledge", row_id),
        )
        self.conn.commit()
        return row_id

    # ------------------------------------------------------------------ read

    def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def list_elections(self) -> list[dict]:
        return self._rows("SELECT * FROM elections ORDER BY year")

    def county_results(self, county: str, year: int,
                       kind: str = "county_mayor") -> list[dict]:
        """County-level candidate results for one election."""
        return self._rows(
            "SELECT v.district, c.name, c.party, v.votes, v.share, c.elected"
            " FROM votes v JOIN candidates c"
            "  ON c.election_id=v.election_id AND c.cand_no=v.cand_no"
            "   AND ((c.county=v.county AND c.district=v.district) OR c.county='全國')"
            " JOIN elections e ON e.id=v.election_id"
            " WHERE e.kind=? AND e.year=? AND v.county=? AND v.township='' AND v.village=''"
            " ORDER BY v.district, v.votes DESC",
            (kind, year, normalize_county(county)),
        )

    def township_results(self, county: str, year: int,
                         kind: str = "county_mayor") -> list[dict]:
        """Township-level candidate results for one election."""
        return self._rows(
            "SELECT v.district, v.township, c.name, c.party, v.votes, v.share, c.elected"
            " FROM votes v JOIN candidates c"
            "  ON c.election_id=v.election_id AND c.cand_no=v.cand_no"
            "   AND ((c.county=v.county AND c.district=v.district) OR c.county='全國')"
            " JOIN elections e ON e.id=v.election_id"
            " WHERE e.kind=? AND e.year=? AND v.county=? AND v.township<>'' AND v.village=''"
            " ORDER BY v.township, v.votes DESC",
            (kind, year, normalize_county(county)),
        )

    def race_candidates(self, cycle: str = "2026",
                        county: Optional[str] = None,
                        office: Optional[str] = "mayor",
                        district: str = "") -> list[dict]:
        """Registered/declared candidates; office=None returns every office."""
        sql = "SELECT * FROM race_candidates WHERE cycle=?"
        params: list = [cycle]
        if county:
            sql += " AND county=?"
            params.append(normalize_county(county))
        if district:
            sql += " AND district=?"
            params.append(district)
        if office:
            sql += " AND office=?"
            params.append(office)
        sql += " ORDER BY county, status, party, name"
        return self._rows(sql, tuple(params))

    def polls_for(self, county: str = "", days: Optional[int] = None,
                  limit: int = 20, race: str = "", district: str = "") -> list[dict]:
        sql = "SELECT * FROM polls"
        cond, params = [], []
        if race:
            cond.append("race=?")
            params.append(race)
        if district:
            cond.append("district=?")
            params.append(district)
        if county:
            cond.append("county=?")
            params.append(normalize_county(county))
        if days:
            cond.append("publish_date>=date('now', ?)")
            params.append(f"-{int(days)} days")
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        sql += " ORDER BY publish_date DESC LIMIT ?"
        params.append(limit)
        out = self._rows(sql, tuple(params))
        for r in out:
            try:
                r["results"] = json.loads(r.pop("results_json") or "{}")
            except json.JSONDecodeError:
                r["results"] = {}
        return out

    def recent_events(self, county: str = "", days: int = 30,
                      limit: int = 30) -> list[dict]:
        sql = "SELECT * FROM events WHERE date>=date('now', ?)"
        params: list = [f"-{int(days)} days"]
        if county:
            sql += " AND county=?"
            params.append(normalize_county(county))
        sql += " ORDER BY date DESC LIMIT ?"
        params.append(limit)
        return self._rows(sql, tuple(params))

    # ----------------------------------------------------- legislator / 2028

    def districts(self, county: str, year: int = 2024) -> list[str]:
        """Legislator 選區 names of a county in a given election year."""
        rows = self.conn.execute(
            "SELECT DISTINCT v.district FROM votes v JOIN elections e ON e.id=v.election_id"
            " WHERE e.kind='regional_legislator' AND e.year=? AND v.county=? AND v.district<>''"
            " ORDER BY v.district", (year, normalize_county(county))).fetchall()
        return [r[0] for r in rows]

    def district_results(self, district: str, year: int) -> list[dict]:
        """District-level regional-legislator results."""
        return self._rows(
            "SELECT c.name, c.party, v.votes, v.share, c.elected"
            " FROM votes v JOIN candidates c ON c.election_id=v.election_id"
            "  AND c.cand_no=v.cand_no AND c.county=v.county AND c.district=v.district"
            " JOIN elections e ON e.id=v.election_id"
            " WHERE e.kind='regional_legislator' AND e.year=? AND v.district=?"
            "  AND v.township='' AND v.village=''"
            " ORDER BY v.votes DESC", (year, district))

    def district_townships(self, district: str, year: int) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT v.township FROM votes v JOIN elections e ON e.id=v.election_id"
            " WHERE e.kind='regional_legislator' AND e.year=? AND v.district=?"
            "  AND v.township<>'' AND v.village=''", (year, district)).fetchall()
        return [r[0] for r in rows]

    def overlay_by_district(self, district: str, year: int,
                            kind: str = "president") -> list[dict]:
        """Re-aggregate a nationwide race (president / party_list) onto one 選區,
        using that year's legislator village -> district mapping."""
        rows = self._rows(
            "WITH map AS ("
            " SELECT DISTINCT v.county, v.township, v.village FROM votes v"
            " JOIN elections e ON e.id=v.election_id"
            " WHERE e.kind='regional_legislator' AND e.year=? AND v.district=? AND v.village<>'')"
            " SELECT c.name, c.party, SUM(p.votes) AS votes"
            " FROM votes p JOIN elections e2 ON e2.id=p.election_id"
            " JOIN map m ON m.county=p.county AND m.township=p.township AND m.village=p.village"
            " JOIN candidates c ON c.election_id=p.election_id AND c.cand_no=p.cand_no"
            "  AND c.county='全國'"
            " WHERE e2.kind=? AND e2.year=?"
            " GROUP BY c.cand_no ORDER BY votes DESC", (year, district, kind, year))
        total = sum(r["votes"] for r in rows) or 1
        for r in rows:
            r["share"] = round(100 * r["votes"] / total, 2)
        return rows

    def legislator_seats(self, year: int, party_list_seats: int = 34,
                         threshold: float = 5.0) -> dict:
        """Seat count by party: regional + indigenous winners from the DB, party-list
        seats re-derived by largest remainder among parties above the threshold."""
        won: dict[str, dict[str, int]] = {}
        for kind in ("regional_legislator", "lowland_indigenous", "highland_indigenous"):
            for r in self.conn.execute(
                    "SELECT c.party, COUNT(*) FROM candidates c JOIN elections e"
                    " ON e.id=c.election_id WHERE e.kind=? AND e.year=? AND c.elected=1"
                    " GROUP BY c.party", (kind, year)):
                won.setdefault(r[0], {})[kind] = r[1]
        party_votes = self.county_results("全國", year, "party_list")
        eligible = [r for r in party_votes if (r["share"] or 0) >= threshold]
        pool = sum(r["votes"] for r in eligible) or 1
        quotas = {r["name"]: party_list_seats * r["votes"] / pool for r in eligible}
        seats = {k: int(q) for k, q in quotas.items()}
        left = party_list_seats - sum(seats.values())
        for k in sorted(quotas, key=lambda k: quotas[k] - seats[k], reverse=True)[:left]:
            seats[k] += 1
        for k, n in seats.items():
            won.setdefault(k, {})["party_list"] = n
        out = [{"party": k, **v, "total": sum(v.values())} for k, v in won.items()]
        out.sort(key=lambda r: r["total"], reverse=True)
        return {"year": year, "seats": out,
                "party_vote": [{"party": r["name"], "votes": r["votes"], "share": r["share"]}
                               for r in party_votes]}

    def search_kb(self, query: str, county: str = "", limit: int = 10) -> list[dict]:
        """Full-text search over events + knowledge, optionally county-scoped.

        FTS5 trigram needs ≥3 chars; shorter queries fall back to LIKE.
        """
        query = (query or "").strip()
        if len(query) >= 3:
            # wrap in quotes so agent-supplied text can't inject FTS syntax
            safe = '"' + query.replace('"', "") + '"'
            hits = self._rows(
                "SELECT content_type, row_id FROM kb_fts WHERE kb_fts MATCH ? LIMIT ?",
                (safe, limit * 4),
            )
        else:
            hits = [
                {"content_type": "knowledge", "row_id": r["id"]}
                for r in self._rows(
                    "SELECT id FROM knowledge WHERE claim LIKE ? LIMIT ?",
                    (f"%{query}%", limit),
                )
            ] + [
                {"content_type": "event", "row_id": r["id"]}
                for r in self._rows(
                    "SELECT id FROM events WHERE summary LIKE ? LIMIT ?",
                    (f"%{query}%", limit),
                )
            ]
        out: list[dict] = []
        for h in hits:
            if h["content_type"] == "knowledge":
                row = self.conn.execute(
                    "SELECT * FROM knowledge WHERE id=?", (h["row_id"],)
                ).fetchone()
            else:
                row = self.conn.execute(
                    "SELECT * FROM events WHERE id=?", (h["row_id"],)
                ).fetchone()
            if row is None:
                continue
            d = dict(row)
            if county and normalize_county(county) not in (d.get("county") or "", ""):
                continue
            d["_type"] = h["content_type"]
            out.append(d)
            if len(out) >= limit:
                break
        return out

    def knowledge_for(self, county: str, topic: str = "", limit: int = 30) -> list[dict]:
        sql = "SELECT * FROM knowledge WHERE county=?"
        params: list = [normalize_county(county)]
        if topic:
            sql += " AND topic=?"
            params.append(topic)
        sql += " ORDER BY valid_from DESC, id DESC LIMIT ?"
        params.append(limit)
        return self._rows(sql, tuple(params))
