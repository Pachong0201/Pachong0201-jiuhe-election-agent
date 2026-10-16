"""Knowledge-base tools exposed to the agent.

Each function maps 1:1 onto an OpenAI function-calling tool and returns
JSON-serializable data. County names are normalized via Store helpers.
"""

from __future__ import annotations

from typing import Any

from ..db.store import Store, normalize_county
from . import calc


def county_results_tool(store: Store, county: str, year: int,
                        kind: str = "county_mayor") -> dict:
    rows = store.county_results(county, year, kind)
    return {
        "county": normalize_county(county),
        "year": year,
        "kind": kind,
        "results": rows,
        "verdict": calc.competitiveness(rows),
    }


def township_results_tool(store: Store, county: str, year: int,
                          kind: str = "county_mayor") -> dict:
    rows = store.township_results(county, year, kind)
    # group per township, keep top-2 per township for readability
    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(r["township"], []).append(
            {"name": r["name"], "party": r["party"],
             "votes": r["votes"], "share": r["share"], "elected": r["elected"]}
        )
    return {
        "county": normalize_county(county),
        "year": year,
        "townships": {
            t: {"results": rs[:4], "verdict": calc.competitiveness(rs)}
            for t, rs in grouped.items()
        },
    }


def race_candidates_tool(store: Store, county: str = "",
                         office: str = "mayor", cycle: str = "2026",
                         district: str = "") -> dict:
    rows = store.race_candidates(cycle, county or None, office or None, district)
    out = {
        "cycle": cycle,
        "county": normalize_county(county) if county else "",
        "district": district,
        "office": office,
        "candidates": rows,
    }
    if cycle != "2026" and not rows:
        out["note"] = ("名單庫尚無此選舉人選；正式登記前請以新聞檢索整理，"
                       "並用 record_candidate 標註 prospective/shortlisted/nominated 狀態入庫")
    return out


def polls_tool(store: Store, county: str = "", days: int = 180,
               race: str = "", district: str = "") -> dict:
    polls = store.polls_for(county or None, days=days, race=race, district=district)
    note = ""
    if not polls:
        # fall back to the latest polls on file so the agent can still anchor
        polls = store.polls_for(county or None, days=None, limit=5,
                                race=race, district=district)
        if polls:
            note = f"近 {days} 天無民調，以下為庫中最新幾份（請標註時效）"
    return {"county": normalize_county(county) if county else "", "race": race,
            "district": district, "days": days, "note": note, "polls": polls}


def county_districts_tool(store: Store, county: str, year: int = 2024) -> dict:
    return {"county": normalize_county(county), "year": year,
            "districts": store.districts(county, year)}


def district_results_tool(store: Store, district: str, year: int = 2024) -> dict:
    """One 選區: legislator result plus president / party-list votes re-aggregated
    onto the same villages (the district's partisan baseline)."""
    rows = store.district_results(district, year)
    return {
        "district": district,
        "year": year,
        "townships": store.district_townships(district, year),
        "legislator": rows,
        "verdict": calc.competitiveness(rows) if rows else None,
        "president_in_district": store.overlay_by_district(
            district, year, "president"),
        "party_list_in_district": store.overlay_by_district(
            district, year, "party_list")[:6],
        "note": "" if rows else "查無此選區；請先用 county_districts 確認選區名稱（如 臺北市第03選區）",
    }


def legislator_seats_tool(store: Store, year: int = 2024) -> dict:
    out = store.legislator_seats(year)
    out["note"] = ("不分區席次依 5% 門檻與最大餘數法由政黨票推算（34 席），"
                   "區域與原住民席次為實際當選數")
    return out


def add_candidate_tool(store: Store, cycle: str, office: str, name: str,
                       party: str = "", status: str = "prospective",
                       county: str = "全國", district: str = "",
                       source_url: str = "", date: str = "") -> dict:
    """Persist a prospective/nominated candidate surfaced by news retrieval."""
    import datetime as _dt
    store.upsert_race_candidate(
        cycle=cycle, county=county or "全國", name=name, party=party,
        office=office, status=status, source=source_url,
        updated_at=date or _dt.date.today().isoformat(), district=district)
    return {"stored": True, "name": name, "status": status}


def add_poll_tool(store: Store, race: str, org: str, results: dict,
                  fieldwork_end: str = "", publish_date: str = "",
                  method: str = "", sample_n: int | None = None,
                  county: str = "全國", district: str = "",
                  source_url: str = "") -> dict:
    """Persist a poll found in news so later reports can track same-org waves."""
    row_id = store.add_poll(
        org=org, race=race, results=results, fieldwork_end=fieldwork_end,
        publish_date=publish_date or fieldwork_end, method=method,
        sample_n=sample_n, county=county or "全國", district=district,
        source_url=source_url)
    return {"stored": True, "poll_id": row_id}


def events_tool(store: Store, county: str = "", days: int = 30) -> dict:
    return {"county": normalize_county(county) if county else "",
            "days": days,
            "events": store.recent_events(county or None, days=days)}


def kb_search_tool(store: Store, query: str, county: str = "") -> dict:
    return {"query": query, "hits": store.search_kb(query, county or None)}


def knowledge_tool(store: Store, county: str, topic: str = "") -> dict:
    return {"county": normalize_county(county),
            "knowledge": store.knowledge_for(county, topic or None)}


def elections_list_tool(store: Store) -> dict:
    return {"elections": store.list_elections()}


def add_event_tool(store: Store, county: str, date: str, kind: str,
                   summary: str, source_url: str = "") -> dict:
    """Persist a verified campaign event discovered during retrieval."""
    import datetime as _dt
    row_id = store.add_event(
        county=county, date=date, kind=kind, summary=summary,
        source_url=source_url,
        created_at=_dt.date.today().isoformat(),
    )
    return {"stored": True, "event_id": row_id}
