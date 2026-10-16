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
_DISTRICT = {"type": "string", "description": "立委選區全名，如「臺北市第03選區」，非立委留空"}
_KINDS = ["county_mayor", "president", "regional_legislator", "party_list",
          "lowland_indigenous", "highland_indigenous"]


def build_tools(store: Store, config: Config) -> tuple[list[dict], dict[str, Callable]]:
    """Return (OpenAI tool schemas, dispatch table)."""

    dispatch: dict[str, Callable[..., Any]] = {
        "list_elections": lambda **_: kb.elections_list_tool(store),
        "county_results": lambda county, year, kind="county_mayor":
            kb.county_results_tool(store, county, year, kind),
        "township_results": lambda county, year, kind="county_mayor":
            kb.township_results_tool(store, county, year, kind),
        "race_candidates": lambda county="", office="mayor", cycle="", district="":
            kb.race_candidates_tool(
                store, county, "" if office == "all" else office,
                cycle or config.race_cycle, district),
        "get_polls": lambda county="", days=180, race="", district="":
            kb.polls_tool(store, county, days, race, district),
        "county_districts": lambda county, year=2024:
            kb.county_districts_tool(store, county, year),
        "district_results": lambda district, year=2024:
            kb.district_results_tool(store, district, year),
        "district_margins": lambda year=2024:
            kb.district_margins_tool(store, year),
        "legislator_seats": lambda year=2024:
            kb.legislator_seats_tool(store, year),
        "record_candidate": lambda **kw: kb.add_candidate_tool(store, **kw),
        "record_poll": lambda **kw: kb.add_poll_tool(store, **kw),
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
             "kind": {"type": "string", "enum": _KINDS,
                      "description": "選舉類型，預設 county_mayor（縣市長/直轄市長）；"
                                     "county='全國' 可查總統/不分區政黨票全國結果"}},
            ["county", "year"]),
        _fn("township_results", "查詢某縣市某次選舉的鄉鎮市區級候選人得票（看票倉分佈）",
            {"county": _COUNTY, "year": _YEAR, "kind": {"type": "string"}},
            ["county", "year"]),
        _fn("race_candidates", "查詢參選名單：2026九合一為中選會登記名冊；2028總統/立委為從新聞整理的"
            "擬參選人（status: prospective 傳聞/點名, shortlisted 黨內評估, nominated 已提名, registered 已登記, withdrawn 退出）",
            {"county": {**_COUNTY, "description": "留空則回傳全部縣市；總統用「全國」"},
             "office": {"type": "string",
                        "enum": ["mayor", "councilor", "township_mayor", "township_rep", "village_chief",
                                 "president", "vice_president", "legislator", "indigenous_legislator", "all"],
                        "description": "公職類型，預設 mayor（縣市長）"},
             "cycle": {"type": "string", "description": "選舉年度，預設 2026；總統/立委用 2028"},
             "district": _DISTRICT}, []),
        _fn("get_polls", "查詢民調庫中的近期民調（保留機構/方法/時間元數據）",
            {"county": {**_COUNTY, "description": "留空查全部；全國民調用「全國」"},
             "days": {"type": "integer", "description": "最近N天，預設180；無資料時回傳庫中最新幾份"},
             "race": {"type": "string", "enum": ["county_mayor", "president", "legislator", "party_list"],
                      "description": "民調類型，留空不限"},
             "district": _DISTRICT}, []),
        _fn("county_districts", "列出某縣市的區域立委選區名稱（如 臺北市第01選區…）",
            {"county": _COUNTY, "year": {**_YEAR, "description": "選區劃分所依據的選舉年，預設 2024"}},
            ["county"]),
        _fn("district_results", "查詢某立委選區的歷屆結果：區域立委得票＋同選區總統票與不分區政黨票（選區藍綠白基本盤）",
            {"district": _DISTRICT, "year": {**_YEAR, "description": "2016/2020/2024，預設 2024"}},
            ["district"]),
        _fn("district_margins", "列出某屆全部區域立委選區的勝負差距與競爭度分類（膠著/競爭/傾向/安全），找翻轉風險選區用",
            {"year": {**_YEAR, "description": "2016/2020/2024，預設 2024"}}, []),
        _fn("legislator_seats", "查詢某屆立委全國席次結構：各黨區域/原住民/不分區席次與全國政黨票",
            {"year": {**_YEAR, "description": "2016/2020/2024，預設 2024"}}, []),
        _fn("record_candidate", "把新聞中查證的 2028 擬參選人/提名人寫入名單庫（必須附來源）",
            {"cycle": {"type": "string", "description": "如 2028"},
             "office": {"type": "string", "enum": ["president", "vice_president", "legislator",
                                                    "indigenous_legislator"]},
             "name": {"type": "string"},
             "party": {"type": "string", "description": "政黨全名，如 民主進步黨、中國國民黨、台灣民眾黨"},
             "status": {"type": "string",
                        "enum": ["prospective", "shortlisted", "declared", "nominated", "registered", "withdrawn"]},
             "county": {**_COUNTY, "description": "總統用「全國」"},
             "district": _DISTRICT,
             "source_url": {"type": "string"},
             "date": {"type": "string", "description": "報導日期 YYYY-MM-DD"}},
            ["cycle", "office", "name", "status", "source_url"]),
        _fn("record_poll", "把新聞/機構公布的民調寫入民調庫（同機構多波次才能看趨勢；必須有機構與調查時間）",
            {"race": {"type": "string", "enum": ["county_mayor", "president", "legislator", "party_list"]},
             "org": {"type": "string", "description": "民調機構"},
             "results": {"type": "object", "description": "{\"人選或政黨\": 百分比, \"未表態\": 百分比}"},
             "fieldwork_end": {"type": "string", "description": "調查結束日 YYYY-MM-DD"},
             "publish_date": {"type": "string"},
             "method": {"type": "string", "description": "如 市話+手機 CATI；對比組合（如 賴vs盧vs黃）也寫在這"},
             "sample_n": {"type": "integer"},
             "county": {**_COUNTY, "description": "全國民調用「全國」"},
             "district": _DISTRICT,
             "source_url": {"type": "string"}},
            ["race", "org", "results", "fieldwork_end", "source_url"]),
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
