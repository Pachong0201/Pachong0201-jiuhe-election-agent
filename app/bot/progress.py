"""Live progress card for report generation (patched in place on Feishu)."""

from __future__ import annotations

import json
import time

STAGES = [
    ("data", "查詢候選人名冊、民調庫與歷史票數"),
    ("search", "檢索兩黨候選人近期新聞"),
    ("read", "閱讀重點報導全文"),
    ("write", "綜合研判、撰寫報告"),
    ("doc", "生成 Word 文件"),
]
_ORDER = [k for k, _ in STAGES]
TOOL_STAGE = {"web_search": "search", "fetch_url": "read",
              "record_event": "write"}


class Progress:
    def __init__(self, title: str, search_label: str = ""):
        self.title = title
        self.labels = dict(STAGES)
        if search_label:
            self.labels["search"] = search_label
        self.started = time.monotonic()
        self.current = "data"
        self.done = False
        self.error = ""
        self.searches = 0
        self.reads = 0

    def on_tools(self, names: list[str]) -> None:
        stages = [TOOL_STAGE.get(n, "data") for n in names]
        self.searches += names.count("web_search")
        self.reads += names.count("fetch_url")
        self.current = max(stages, key=_ORDER.index) if stages else self.current

    def advance(self, stage: str) -> None:
        self.current = stage

    def finish(self, error: str = "") -> None:
        self.done, self.error = True, error

    def elapsed(self) -> int:
        return int(time.monotonic() - self.started)

    def render(self) -> str:
        cur = _ORDER.index(self.current)
        lines = []
        for i, key in enumerate(_ORDER):
            label = self.labels[key]
            if key == "search" and self.searches:
                label += f"（已搜尋 {self.searches} 次）"
            if key == "read" and self.reads:
                label += f"（已讀 {self.reads} 篇）"
            if self.done and not self.error:
                mark = "✅"
            elif i < cur:
                mark = "✅"
            elif i == cur:
                mark = "❌" if self.error else "⏳"
            else:
                mark = "⬜"
            lines.append(f"{mark} {label}")
        if self.error:
            tail = f"分析中斷：{self.error}"
        elif self.done:
            tail = f"已完成，用時 {self.elapsed()} 秒。報告摘要與 Word 附件見下方。"
        else:
            tail = f"已用時 {self.elapsed()} 秒，完整報告約需 1–3 分鐘，請稍候。"
        return "\n".join(lines) + f"\n\n{tail}"

    def card(self) -> dict:
        status = "失敗" if self.error else ("已完成" if self.done else "進行中")
        card = {
            "config": {"wide_screen_mode": True, "update_multi": True},
            "header": {
                "title": {"tag": "plain_text",
                          "content": f"{self.title} · {status}"},
                "template": "red" if self.error else
                            ("green" if self.done else "wathet"),
            },
            "elements": [{"tag": "div",
                          "text": {"tag": "lark_md", "content": self.render()}}],
        }
        return {"msg_type": "interactive", "content": json.dumps(card)}
