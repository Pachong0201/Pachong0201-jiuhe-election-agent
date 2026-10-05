"""Tool registry: OpenAI function-calling schemas + dispatch to Python."""

from __future__ import annotations

import json
from typing import Any, Callable

from ..config import Config
from ..db.store import Store
from . import kb, web


def _fn(name: str, description: str, properties: dict, required: list) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


_COUNTY = {"type": "string", "description": "縣市名稱，可含或不含「市/縣」後綴，如「台北市」「臺中市」"}
_YEAR = {"type": "integer", "description": "選舉年份（民國換算為西元），如 2022"}


def build_tools(store: Store, config: Config) -> tuple[list[dict], dict[str, Callable]]:
    """Return (OpenAI tool schemas, dispatch table)."""

    dispatch: dict[str, Callable[..., Any]] = {
        "list_elections": lambda **_: kb.elections_list_tool(store),
        "county_results": lambda county, year, kind="county_mayor":
            kb.county_results_tool(store, county, year, kind),
        "township_results": lambda county, year, kind="county_mayor":
            kb.township_results_tool(store, county, year, kind),
        "race_candidates": lambda county="", office="mayor":
            kb.race_candidates_tool(
                store, county, "" if office == "all" else office,
                config.race_cycle),
        "get_polls": lambda county="", days=90:
            kb.polls_tool(store, county, days),
        "recent_events": lambda county="", days=30:
            kb.events_tool(store, county, days),
        "kb_search": lambda query, county="":
            kb.kb_search_tool(store, query, county),
        "local_knowledge": lambda county, topic="":
            kb.knowledge_tool(store, county, topic),
        "record_event": lambda county, date, kind, summary, source_url="":
            kb.add_event_tool(store, county, date, kind, summary, source_url),
        "web_search": lambda query, max_results=6:
            web.web_search(query, max_results, config.search_provider,
                           config.tavily_api_key),
        "fetch_url": lambda url: web.fetch_url(url),
    }

    schemas = [
        _fn("list_elections", "列出知識庫中已入庫的歷史選舉（類型、年份、日期）", {}, []),
        _fn("county_results", "查詢某縣市某次選舉的縣市級候選人得票結果（含勝負判定）",
            {"county": _COUNTY, "year": _YEAR,
             "kind": {"type": "string", "enum": ["county_mayor", "president", "regional_legislator"],
                      "description": "選舉類型，預設 county_mayor（縣市長/直轄市長）"}},
            ["county", "year"]),
        _fn("township_results", "查詢某縣市某次選舉的鄉鎮市區級候選人得票（看票倉分佈）",
            {"county": _COUNTY, "year": _YEAR, "kind": {"type": "string"}},
            ["county", "year"]),
        _fn("race_candidates", "查詢本屆（2026九合一）各縣市已表態/登記候選人名單，預設只看縣市長",
            {"county": {**_COUNTY, "description": "留空則回傳全部縣市"},
             "office": {"type": "string",
                        "enum": ["mayor", "councilor", "township_mayor", "township_rep", "village_chief", "all"],
                        "description": "公職類型，預設 mayor（縣市長）"}}, []),
        _fn("get_polls", "查詢民調庫中某縣市近期民調（保留機構/方法/時間元數據）",
            {"county": {**_COUNTY, "description": "留空查全部"},
             "days": {"type": "integer", "description": "最近N天，預設90"}}, []),
        _fn("recent_events", "查詢已入庫的近期選戰事件（造勢、民調發布、表態、爭議等）",
            {"county": {**_COUNTY, "description": "留空查全部"},
             "days": {"type": "integer", "description": "最近N天，預設30"}}, []),
        _fn("kb_search", "對地方知識與事件庫做全文檢索（派系、組織、議題、歷史事件）",
            {"query": {"type": "string", "description": "檢索詞"},
             "county": {**_COUNTY, "description": "限定縣市，留空不限"}}, ["query"]),
        _fn("local_knowledge", "讀取某縣市的地方知識條目（派系/組織/議題，帶時間範圍與來源）",
            {"county": _COUNTY,
             "topic": {"type": "string", "description": "faction|organization|issue|candidate_profile，留空全部"}},
            ["county"]),
        _fn("record_event", "把即時檢索到的、已核實的選戰事件寫入事件庫（供後續查詢）",
            {"county": _COUNTY, "date": {"type": "string", "description": "YYYY-MM-DD"},
             "kind": {"type": "string", "description": "rally|poll_release|scandal|endorsement|registration|other"},
             "summary": {"type": "string"},
             "source_url": {"type": "string"}}, ["county", "date", "kind", "summary"]),
        _fn("web_search", "即時網路檢索最新選情、民調、新聞（台灣地區中文結果優先）",
            {"query": {"type": "string"},
             "max_results": {"type": "integer", "description": "預設6"}}, ["query"]),
        _fn("fetch_url", "抓取指定網頁/PDF正文（用於讀取檢索到的具體來源）",
            {"url": {"type": "string"}}, ["url"]),
    ]
    return schemas, dispatch


def run_tool(dispatch: dict, name: str, args_json: str) -> str:
    """Execute one tool call; always returns a JSON string (errors included)."""
    fn = dispatch.get(name)
    if fn is None:
        return json.dumps({"error": f"unknown tool {name}"}, ensure_ascii=False)
    try:
        args = json.loads(args_json or "{}")
    except json.JSONDecodeError:
        return json.dumps({"error": f"bad tool args: {args_json[:200]}"},
                          ensure_ascii=False)
    try:
        result = fn(**args)
    except TypeError as e:
        return json.dumps({"error": f"bad args for {name}: {e}"}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001 — tool failure goes back to the model
        return json.dumps({"error": f"{name} failed: {e}"[:500]}, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False, default=str)
