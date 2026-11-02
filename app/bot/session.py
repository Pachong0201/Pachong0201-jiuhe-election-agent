"""Per-chat conversation state: history window + in-flight serialization."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ChatSession:
    chat_id: str
    history: deque = field(default_factory=deque)
    busy: bool = False
    busy_since: float = 0.0
    last_active: float = field(default_factory=time.time)


class SessionRouter:
    """Keeps one ChatSession per chat_id and serializes work per chat."""

    def __init__(self, history_turns: int = 20, stale_after: float = 1800):
        self._turns = history_turns
        self._stale_after = stale_after
        self._sessions: dict[str, ChatSession] = {}
        self._lock = threading.Lock()

    def get(self, chat_id: str) -> ChatSession:
        with self._lock:
            s = self._sessions.get(chat_id)
            if s is None:
                s = ChatSession(chat_id=chat_id,
                                history=deque(maxlen=self._turns * 2))
                self._sessions[chat_id] = s
            s.last_active = time.time()
            return s

    def acquire(self, chat_id: str) -> Optional[ChatSession]:
        """Mark the chat busy; returns None if another request is in flight."""
        s = self.get(chat_id)
        with self._lock:
            if s.busy and time.time() - s.busy_since < self._stale_after:
                return None
            s.busy, s.busy_since = True, time.time()
            return s

    def release(self, chat_id: str) -> None:
        s = self.get(chat_id)
        with self._lock:
            s.busy = False

    def busy_count(self) -> int:
        with self._lock:
            return sum(1 for s in self._sessions.values() if s.busy)

    def reset(self, chat_id: str) -> None:
        with self._lock:
            self._sessions.pop(chat_id, None)

    def record_turn(self, session: ChatSession, user: str, assistant: str) -> None:
        session.history.append({"role": "user", "content": user})
        session.history.append({"role": "assistant", "content": assistant})
