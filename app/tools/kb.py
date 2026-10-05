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
                         office: str = "mayor", cycle: str = "2026") -> dict:
    return {
        "cycle": cycle,
        "county": normalize_county(county) if county else "",
        "office": office,
        "candidates": store.race_candidates(
            cycle, county or None, office or None),
    }


def polls_tool(store: Store, county: str = "", days: int = 90) -> dict:
    return {"county": normalize_county(county) if county else "",
            "days": days,
            "polls": store.polls_for(county or None, days=days)}


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
