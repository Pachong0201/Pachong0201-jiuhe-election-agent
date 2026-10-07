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
from .cards import markdown_card, report_messages, report_summary, text_message
from .docx_report import markdown_to_docx
from .progress import Progress
from .session import SessionRouter

log = logging.getLogger(__name__)

REPORT_RE = re.compile(r"^/report\s*(.*)$", re.I)
RESET_RE = re.compile(r"^/reset\s*$", re.I)
HELP_RE = re.compile(r"^/(help|幫助|帮助)\s*$", re.I)
# free-text questions that ask for a race situation report (繁/简 both)
ANALYSIS_RE = re.compile(
    r"選情|选情|選戰|选战|選舉|选举|態勢|态势|情勢|情势|研判|分析|報告|报告|"
    r"如何|怎麼樣|怎么样")
# simplified spellings -> canonical traditional county names
_SIMPLIFIED = {
    "桃园": "桃園", "嘉义": "嘉義", "宜兰": "宜蘭", "云林": "雲林", "屏东": "屏東",
    "花莲": "花蓮", "台东": "臺東", "金门": "金門", "连江": "連江", "马祖": "連江",
}
COUNTY_RE = re.compile(
    r"台北|臺北|新北|桃園|台中|臺中|台南|臺南|高雄|基隆|新竹|嘉義|宜蘭|彰化|南投|"
    r"雲林|苗栗|屏東|花蓮|臺東|台東|澎湖|金門|連江|馬祖|" + "|".join(_SIMPLIFIED))


def route(text: str) -> tuple[str, str]:
    """Classify a message: ("report", county) for situation reports, else ("chat", "")."""
    m = REPORT_RE.match(text)
    if m is not None:
        arg = m.group(1).strip()
        for simp, trad in _SIMPLIFIED.items():
            arg = arg.replace(simp, trad)
        return "report", arg
    c = COUNTY_RE.search(text)
    if c and ANALYSIS_RE.search(text):
        return "report", _SIMPLIFIED.get(c.group(0), c.group(0))
    return "chat", ""


HELP_TEXT = (
    "**九合一選情分析智能體**\n"
    "直接發消息即可對話，例如：\n"
    "・「分析台北市選情」\n"
    "・「最近一週台中選戰有什麼變化」\n"
    "・「高雄歷屆市長得票對比」\n\n"
    "指令：\n"
    "・`/report <縣市>` — 產出該縣市完整分析報告\n"
    "・`/reset` — 清空本對話上下文\n"
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
    def __init__(self, config: Config, agent: ElectionAgent):
        self.config = config
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

        if RESET_RE.match(text):
            self.sessions.reset(chat_id)
            self._reply(msg.message_id, text_message("上下文已清空。"))
            return
        if HELP_RE.match(text):
            self._reply(msg.message_id, text_message(HELP_TEXT))
            return

        def report_prompt(county: str) -> str:
            return (
                f"請產出「{county}」2026九合一縣市長選情研判報告，嚴格遵循分析報告結構的兩大部分："
                "一、選情總體態勢（以近期民調為主軸研判格局、走勢、勝負傾向與置信度）；"
                "二、兩黨候選人動向（民進黨、國民黨候選人各提煉2–4個觀點，觀點是對多則新聞的歸納判斷，"
                "不要逐條羅列新聞）。先查 race_candidates 與 get_polls，再分別檢索兩黨候選人近30天新聞。"
                if county else
                "使用者要求完整報告但未指定縣市，請詢問要分析哪個縣市。"
            )

        kind, county = route(text)
        log.info("message route=%s county=%s text=%r", kind, county, text[:60])
        if kind == "report":
            threading.Thread(
                target=self._handle_query,
                args=(msg.message_id, chat_id, report_prompt(county), True),
                daemon=True,
            ).start()
            return

        threading.Thread(
            target=self._handle_query,
            args=(msg.message_id, chat_id, text, False),
            daemon=True,
        ).start()

    def _handle_query(self, message_id: str, chat_id: str,
                      text: str, is_report: bool) -> None:
        session = self.sessions.acquire(chat_id)
        if session is None:
            self._reply(message_id,
                        text_message("上一個問題還在分析中，請稍候再問。"))
            return
        try:
            if is_report:
                self._handle_report(message_id, session, text)
                return
            answer = self.agent.answer(list(session.history), text)
            self.sessions.record_turn(session, text, answer)
            for payload in report_messages("回覆", answer):
                self._reply(message_id, payload)
        except Exception as e:  # noqa: BLE001
            log.exception("query failed")
            self._reply(message_id, text_message(f"分析時出錯：{e}"[:300]))
        finally:
            self.sessions.release(chat_id)

    def _handle_report(self, message_id: str, session, text: str) -> None:
        county = text.split("「")[1].split("」")[0] if "「" in text else "選情"
        title = f"{county}選情研判"
        progress = Progress(title)
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
        if file_key:
            self._reply(message_id, markdown_card(
                f"{title}（摘要）",
                report_summary(answer) + "\n\n完整報告見下方 Word 附件。"))
            self._reply(message_id, {"msg_type": "file",
                                     "content": json.dumps({"file_key": file_key})})
        else:
            for payload in report_messages(title, answer):
                self._reply(message_id, payload)
