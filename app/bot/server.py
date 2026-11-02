"""Feishu bot server — lark-oapi WebSocket long-connection mode.

No public endpoint needed: the bot dials out to open.feishu.cn and receives
im.message.receive_v1 events over the socket. Replies go through the REST API.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import logging
import threading
import time
import re
from dataclasses import dataclass
from typing import Optional

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateFileRequest,
    CreateFileRequestBody,
    P2ImMessageReceiveV1,
    PatchMessageRequest,
    PatchMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

from ..agent.core import ElectionAgent
from ..config import Config
from ..db.store import normalize_county, normalize_district
from .cards import (clean_report, markdown_card, report_messages, report_summary,
                    text_message)
from .docx_report import markdown_to_docx
from .health import Health
from .progress import Progress
from .session import SessionRouter

log = logging.getLogger(__name__)

REPORT_RE = re.compile(r"^/report\s*(.*)$", re.I)
RESET_RE = re.compile(r"^/reset\s*$", re.I)
HELP_RE = re.compile(r"^/(help|幫助|帮助)\s*$", re.I)
STATUS_RE = re.compile(r"^/(status|狀態|状态)\s*$", re.I)
# free-text questions that ask for a race situation report (繁/简 both)
ANALYSIS_RE = re.compile(
    r"選情|选情|選戰|选战|選舉|选举|大選|大选|態勢|态势|情勢|情势|研判|分析|報告|报告|"
    r"預測|预测|席次|如何|怎麼樣|怎么样")
# simplified spellings -> canonical traditional county names
_SIMPLIFIED = {
    "桃园": "桃園", "嘉义": "嘉義", "宜兰": "宜蘭", "云林": "雲林", "屏东": "屏東",
    "花莲": "花蓮", "台东": "臺東", "金门": "金門", "连江": "連江", "马祖": "連江",
}
COUNTY_RE = re.compile(
    r"(台北|臺北|新北|桃園|台中|臺中|台南|臺南|高雄|基隆|新竹|嘉義|宜蘭|彰化|南投|"
    r"雲林|苗栗|屏東|花蓮|臺東|台東|澎湖|金門|連江|馬祖|" + "|".join(_SIMPLIFIED) + r")"
    r"(市|縣|县)?")
PRESIDENT_RE = re.compile(r"總統|总统|大選|大选")
LEGISLATOR_RE = re.compile(
    r"立委|立法委員|立法委员|國會|国会|席次|不分區|不分区|政黨票|政党票|選區|选区")
DISTRICT_RE = re.compile(r"第?\s*([0-9０-９]{1,2}|[一二三四五六七八九十]{1,3})\s*(?:選區|选区)")


@dataclass(frozen=True)
class Target:
    """What a report request is about."""
    race: str = "county_mayor"   # county_mayor | president | legislator | legislator_national
    county: str = ""
    district: str = ""

    @property
    def title(self) -> str:
        if self.race == "president":
            return "2028總統選情研判"
        if self.race == "legislator_national":
            return "2028立委席次態勢研判"
        if self.race == "legislator":
            return f"{self.district or self.county}立委選情研判"
        return f"{self.county}選情研判" if self.county else "選情研判"


def _parse_target(text: str, explicit: bool) -> Optional[Target]:
    for simp, trad in _SIMPLIFIED.items():
        text = text.replace(simp, trad)
    c = COUNTY_RE.search(text)
    # keep the 市/縣 suffix: 嘉義市 and 嘉義縣 are different races
    county = normalize_county(c.group(1) + (c.group(2) or "").replace("县", "縣")) if c else ""
    d = DISTRICT_RE.search(text)
    is_legislator = bool(LEGISLATOR_RE.search(text))
    # "台北第3选区立委" is unambiguous even without an analysis keyword
    if not explicit and not ANALYSIS_RE.search(text) and not (is_legislator and c and d):
        return None
    if is_legislator:
        if c and d:
            return Target("legislator", county, normalize_district(county, d.group(1)))
        if c:
            return Target("legislator", county)
        return Target("legislator_national")
    if PRESIDENT_RE.search(text):
        return Target("president")
    if c:
        return Target("county_mayor", county)
    if explicit:
        return Target("county_mayor", text.strip())
    return None


def route(text: str) -> tuple[str, Optional[Target]]:
    """Classify a message: ("report", Target) for situation reports, else ("chat", None)."""
    m = REPORT_RE.match(text)
    if m is not None:
        return "report", _parse_target(m.group(1).strip(), explicit=True)
    target = _parse_target(text, explicit=False)
    return ("report", target) if target else ("chat", None)


_NEWS = ("觀點是對多則新聞的歸納判斷，不要逐條羅列新聞。新聞中查證到的擬參選人用 record_candidate 入庫"
         "（標註 prospective/shortlisted/nominated，不得把傳聞人選寫成已提名），"
         "有機構、時間的民調用 record_poll 入庫。")


def report_prompt(target: Optional[Target]) -> str:
    if target is None or (target.race == "county_mayor" and not target.county):
        return "使用者要求完整報告但未指定縣市，請詢問要分析哪個縣市。"
    if target.race == "president":
        return (
            "請產出2028總統大選選情研判報告，嚴格遵循「總統選情報告結構」："
            "一、全國選情總體態勢（以近期總統/政黨民調為主軸，結合2024總統票與區域板塊）；"
            "二、三黨可能人選動向（民進黨、國民黨、民眾黨各提煉2–4個觀點）。"
            "先查 race_candidates(cycle=2028, office=president, county=全國)、get_polls(race=president)、"
            "county_results(county=全國, year=2024, kind=president) 及重點縣市 county_results(kind=president)，"
            "再分別檢索三黨可能人選近30天新聞。" + _NEWS)
    if target.race == "legislator_national":
        return (
            "請產出2028立委選舉全國席次態勢研判報告，嚴格遵循「立委全國席次報告結構」。"
            "先查 legislator_seats(2024) 與 legislator_seats(2020)、get_polls(race=party_list)，"
            "再檢索各黨立委提名進度與政黨支持度新聞。" + _NEWS)
    if target.race == "legislator" and target.district:
        return (
            f"請產出「{target.district}」2028區域立委選情研判報告，嚴格遵循「立委選區報告結構」。"
            f"先查 district_results(district={target.district}) 的 2024、2020 兩屆結果"
            "（含同選區總統票與政黨票），再查 race_candidates(cycle=2028, office=legislator, "
            f"district={target.district})、get_polls(race=legislator)，然後檢索該選區各黨可能人選近30天新聞。"
            "若查無此選區，先用 county_districts 確認選區名稱。" + _NEWS)
    if target.race == "legislator":
        return (
            f"請產出「{target.county}」2028區域立委選情研判報告。先用 county_districts 取得選區清單："
            "只有一個選區就按「立委選區報告結構」；多個選區時第一部分總覽各選區基本盤與競爭度"
            "（用 district_results 逐區查 2024 結果），第二部分聚焦 2–4 個關鍵選區的人選動向。" + _NEWS)
    return (
        f"請產出「{target.county}」2026九合一縣市長選情研判報告，嚴格遵循分析報告結構的兩大部分："
        "一、選情總體態勢（以近期民調為主軸研判格局、走勢、勝負傾向與置信度）；"
        "二、兩黨候選人動向（民進黨、國民黨候選人各提煉2–4個觀點，觀點是對多則新聞的歸納判斷，"
        "不要逐條羅列新聞）。先查 race_candidates 與 get_polls，再分別檢索兩黨候選人近30天新聞。"
    )


HELP_TEXT = (
    "**九合一選情分析智能體**\n"
    "直接發消息即可對話，例如：\n"
    "・「分析台北市選情」\n"
    "・「最近一週台中選戰有什麼變化」\n"
    "・「高雄歷屆市長得票對比」\n"
    "・「2028總統選情」「台北第3選區立委」「立委席次態勢」\n\n"
    "指令：\n"
    "・`/report <縣市>` — 產出該縣市完整分析報告\n"
    "・`/report 總統`、`/report 臺北市第3選區`、`/report 立委` — 2028 總統/立委報告\n"
    "・`/reset` — 清空本對話上下文\n"
    "・`/status` — 機器人運行狀態\n"
    "・`/help` — 本說明"
)


def _extract_text(message) -> str:
    if message.message_type != "text":
        return ""
    try:
        data = json.loads(message.content or "{}")
    except json.JSONDecodeError:
        return ""
    text = (data.get("text") or "").strip()
    # strip @_user_N mention placeholders left by group @-mentions
    return re.sub(r"@_user_\d+", "", text).strip()


class FeishuBot:
    def __init__(self, config: Config, agent: ElectionAgent,
                 health: Optional[Health] = None):
        self.config = config
        self.health = health or Health()
        self.agent = agent
        self.sessions = SessionRouter(config.session_history_turns)

        self.client = (
            lark.Client.builder()
            .app_id(config.feishu_app_id)
            .app_secret(config.feishu_app_secret)
            .domain(lark.FEISHU_DOMAIN)
            .build()
        )

        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_message)
            .build()
        )
        self.ws = lark.ws.Client(
            config.feishu_app_id,
            config.feishu_app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.INFO,
            domain=lark.FEISHU_DOMAIN,
        )

    # ------------------------------------------------------------------ api

    def start(self) -> None:
        log.info("starting Feishu WS client (long connection)")
        self.ws.start()

    # ------------------------------------------------------------- handlers

    def _reply(self, message_id: str, payload: dict) -> str:
        req = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .msg_type(payload["msg_type"])
                .content(payload["content"])
                .build()
            )
            .build()
        )
        resp = self.client.im.v1.message.reply(req)
        if not resp.success():
            log.error("reply failed: %s %s", resp.code, resp.msg)
            return ""
        return resp.data.message_id if resp.data else ""

    def _patch(self, message_id: str, payload: dict) -> None:
        req = (
            PatchMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                PatchMessageRequestBody.builder()
                .content(payload["content"])
                .build()
            )
            .build()
        )
        resp = self.client.im.v1.message.patch(req)
        if not resp.success():
            log.warning("patch failed: %s %s", resp.code, resp.msg)

    def _upload_file(self, file_name: str, data: bytes) -> str:
        req = (
            CreateFileRequest.builder()
            .request_body(
                CreateFileRequestBody.builder()
                .file_type("doc")
                .file_name(file_name)
                .file(io.BytesIO(data))
                .build()
            )
            .build()
        )
        resp = self.client.im.v1.file.create(req)
        if not resp.success():
            raise RuntimeError(f"file upload failed: {resp.code} {resp.msg}")
        return resp.data.file_key

    def _allowed(self, chat_id: str) -> bool:
        return not self.config.feishu_allow or chat_id in self.config.feishu_allow

    def _on_message(self, event: P2ImMessageReceiveV1) -> None:
        msg = event.event.message
        chat_id = msg.chat_id
        text = _extract_text(msg)

        if not self._allowed(chat_id):
            log.info("ignored message from non-whitelisted chat %s", chat_id)
            return
        if not text:
            if msg.message_type != "text":
                self._reply(msg.message_id,
                            text_message("目前只支援文字消息（報告會以卡片回覆）。"))
            return

        self.health.bump("received")
        if STATUS_RE.match(text):
            self._reply(msg.message_id, text_message(
                self.health.status_text(self.sessions.busy_count())))
            return
        if RESET_RE.match(text):
            self.sessions.reset(chat_id)
            self._reply(msg.message_id, text_message("上下文已清空。"))
            return
        if HELP_RE.match(text):
            self._reply(msg.message_id, text_message(HELP_TEXT))
            return

        kind, target = route(text)
        log.info("message route=%s target=%s text=%r", kind, target, text[:60])
        if kind == "report":
            threading.Thread(
                target=self._handle_query,
                args=(msg.message_id, chat_id, report_prompt(target), target),
                daemon=True,
            ).start()
            return

        threading.Thread(
            target=self._handle_query,
            args=(msg.message_id, chat_id, text, None),
            daemon=True,
        ).start()

    def _handle_query(self, message_id: str, chat_id: str,
                      text: str, target: Optional[Target] = None) -> None:
        session = self.sessions.acquire(chat_id)
        if session is None:
            self._reply(message_id,
                        text_message("上一個問題還在分析中，請稍候再問。"))
            return
        try:
            if target is not None:
                self._handle_report(message_id, session, text, target)
                return
            answer = self.agent.answer(list(session.history), text)
            self.sessions.record_turn(session, text, answer)
            for payload in report_messages("回覆", answer):
                self._reply(message_id, payload)
        except Exception as e:  # noqa: BLE001
            log.exception("query failed")
            self.health.error(f"{type(e).__name__}: {e}")
            self._reply(message_id, text_message(f"分析時出錯：{e}"[:300]))
        finally:
            self.sessions.release(chat_id)

    def _handle_report(self, message_id: str, session, text: str,
                       target: Target) -> None:
        title = target.title
        progress = Progress(title, "" if target.race == "county_mayor"
                            else "檢索各黨人選與提名動態新聞")
        progress_id = self._reply(message_id, progress.card())
        last_patch = [0.0]

        def update(force: bool = False) -> None:
            if not progress_id:
                return
            now = time.monotonic()
            if force or now - last_patch[0] >= 1.5:
                last_patch[0] = now
                self._patch(progress_id, progress.card())

        def on_tools(names: list[str]) -> None:
            progress.on_tools(names)
            update()

        try:
            answer = self.agent.answer(list(session.history), text,
                                       on_progress=on_tools)
        except Exception as e:  # noqa: BLE001
            progress.finish(error=str(e)[:120])
            update(force=True)
            raise
        answer = clean_report(answer)
        self.sessions.record_turn(session, text, answer)

        progress.advance("doc")
        update(force=True)
        file_key = ""
        try:
            name = f"{title}_{dt.date.today().isoformat()}.docx"
            file_key = self._upload_file(name, markdown_to_docx(answer))
        except Exception:  # noqa: BLE001
            log.exception("docx delivery failed; falling back to cards")

        progress.finish()
        update(force=True)
        self.health.bump("reports_ok")
        log.info("report done: %s chars=%d docx=%s", title, len(answer),
                 "uploaded" if file_key else "failed, using cards")
        if file_key:
            self._reply(message_id, markdown_card(
                f"{title}（摘要）",
                report_summary(answer) + "\n\n完整報告見下方 Word 附件。"))
            self._reply(message_id, {"msg_type": "file",
                                     "content": json.dumps({"file_key": file_key})})
        else:
            for payload in report_messages(title, answer):
                self._reply(message_id, payload)
