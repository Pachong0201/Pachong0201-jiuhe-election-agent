"""Feishu bot server — lark-oapi WebSocket long-connection mode.

No public endpoint needed: the bot dials out to open.feishu.cn and receives
im.message.receive_v1 events over the socket. Replies go through the REST API.
"""

from __future__ import annotations

import json
import logging
import threading
import re

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    P2ImMessageReceiveV1,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

from ..agent.core import ElectionAgent
from ..config import Config
from .cards import report_messages, text_message
from .session import SessionRouter

log = logging.getLogger(__name__)

REPORT_RE = re.compile(r"^/report\s*(.*)$", re.I)
RESET_RE = re.compile(r"^/reset\s*$", re.I)
HELP_RE = re.compile(r"^/(help|幫助|帮助)\s*$", re.I)
# free-text questions that ask for a race situation report
ANALYSIS_RE = re.compile(r"選情|選戰|態勢|情势|分析報告|分析报告|如何|怎麼樣|怎么样")
COUNTY_RE = re.compile(
    r"台北|臺北|新北|桃園|台中|臺中|台南|臺南|高雄|基隆|新竹|嘉義|宜蘭|彰化|南投|"
    r"雲林|苗栗|屏東|花蓮|臺東|台東|澎湖|金門|連江|馬祖")

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

    def _reply(self, message_id: str, payload: dict) -> None:
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
                f"請產出「{county}」2026九合一縣市長選舉的完整分析報告，"
                "嚴格遵循分析報告結構（態勢速覽→參選格局→近期動態→民調檢視→"
                "結構基礎→地方因素→態勢判斷→資料缺口），"
                "先給態勢判斷再列證據，不要輸出新聞清單。"
                if county else
                "使用者要求完整報告但未指定縣市，請詢問要分析哪個縣市。"
            )

        report_match = REPORT_RE.match(text)
        if report_match is not None:
            county = report_match.group(1).strip()
            threading.Thread(
                target=self._handle_query,
                args=(msg.message_id, chat_id, report_prompt(county), True),
                daemon=True,
            ).start()
            return

        # free-text analysis questions get the same structured report
        if COUNTY_RE.search(text) and ANALYSIS_RE.search(text):
            county = COUNTY_RE.search(text).group(0)  # type: ignore[union-attr]
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
            answer = self.agent.answer(list(session.history), text)
            self.sessions.record_turn(session, text, answer)
            title = None
            if is_report:
                county = text.split("「")[1].split("」")[0] if "「" in text else "選情"
                title = f"{county}選情分析"
            for payload in report_messages(title or "回覆", answer):
                self._reply(message_id, payload)
        except Exception as e:  # noqa: BLE001
            log.exception("query failed")
            self._reply(message_id, text_message(f"分析時出錯：{e}"[:300]))
        finally:
            self.sessions.release(chat_id)
