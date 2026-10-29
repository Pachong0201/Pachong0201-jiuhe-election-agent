"""Agent core: runs the LLM tool-calling loop over a conversation history."""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any, Callable, Optional

from ..config import Config
from ..db.store import Store
from .llm import LLM
from ..tools import build_tools, run_tool

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


def _load_prompt(name: str) -> str:
    p = PROMPTS_DIR / name
    return p.read_text(encoding="utf-8") if p.exists() else ""


def system_prompt(config: Config) -> str:
    today = dt.date.today().isoformat()
    return (
        _load_prompt("system.md")
        .replace("{{TODAY}}", today)
        .replace("{{RACE_CYCLE}}", config.race_cycle)
        + "\n\n"
        + _load_prompt("analysis.md")
        + "\n\n"
        + _load_prompt("analysis_2028.md")
    )


_FINALIZE = ("檢索輪數已用完，不能再呼叫工具。請立即依據上面已取得的工具結果，"
             "按原定格式寫出完整回覆；查不到的部分在資料缺口中如實標註，不要虛構。")


class ElectionAgent:
    def __init__(self, llm: LLM, store: Store, config: Config):
        self.llm = llm
        self.store = store
        self.config = config
        self.schemas, self.dispatch = build_tools(store, config)
        self._system = system_prompt(config)

    def answer(self, history: list[dict], user_text: str,
               on_progress: Optional[Callable[[list[str]], None]] = None,
               ) -> str:
        """Run the tool loop and return the final assistant message text.

        ``on_progress`` receives the tool names of each round before they run.
        """
        messages: list[dict] = [{"role": "system", "content": self._system}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_text})

        for round_i in range(self.config.max_tool_rounds):
            resp = self.llm.chat(messages, tools=self.schemas)
            msg = resp.choices[0].message

            tool_calls = getattr(msg, "tool_calls", None) or []
            if not tool_calls:
                return (msg.content or "").strip() or "（無法產生回覆）"

            # Append assistant turn with tool calls, then each tool result.
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name,
                                     "arguments": tc.function.arguments},
                    }
                    for tc in tool_calls
                ],
            })
            if on_progress is not None:
                try:
                    on_progress([tc.function.name for tc in tool_calls])
                except Exception:  # noqa: BLE001
                    log.exception("progress callback failed")
            for tc in tool_calls:
                log.info("tool %s(%s)", tc.function.name,
                         (tc.function.arguments or "")[:120])
                result = run_tool(self.dispatch, tc.function.name,
                                  tc.function.arguments)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result[:20000],
                })

        # Out of tool rounds: make the model write up what it already gathered
        # instead of discarding a full round of evidence.
        log.warning("tool rounds exhausted (%d); forcing final answer",
                    self.config.max_tool_rounds)
        messages.append({"role": "user", "content": _FINALIZE})
        resp = self.llm.chat(messages)
        return (resp.choices[0].message.content or "").strip() or "（無法產生回覆）"
