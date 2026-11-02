"""Liveness bookkeeping, an event-loop watchdog and the /status text."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)
REPO = Path(__file__).resolve().parents[2]


def _fmt(ts: float) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "無"


def repo_version() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                              capture_output=True, text=True, timeout=5).stdout.strip() or "?"
    except Exception:  # noqa: BLE001
        return "?"


class Health:
    def __init__(self) -> None:
        self.started = time.time()
        self.version = repo_version()
        self.connected = False
        self.last_connect = 0.0
        self.received = 0
        self.reports_ok = 0
        self.failures = 0
        self.last_error = ""
        self.last_error_at = 0.0
        self._lock = threading.Lock()

    def bump(self, name: str) -> None:
        with self._lock:
            setattr(self, name, getattr(self, name) + 1)

    def error(self, what: str) -> None:
        with self._lock:
            self.failures += 1
            self.last_error, self.last_error_at = what[:80], time.time()

    def status_text(self, busy_chats: int) -> str:
        up = int(time.time() - self.started)
        return "\n".join([
            f"版本 {self.version}，已運行 {up // 3600} 小時 {up % 3600 // 60} 分",
            f"飛書長連接：{'已連線' if self.connected else '斷線（自動重連中）'}，"
            f"最近連線 {_fmt(self.last_connect)}",
            f"收到消息 {self.received} 條，完成報告 {self.reports_ok} 份，失敗 {self.failures} 次",
            f"最近錯誤：{self.last_error or '無'}（{_fmt(self.last_error_at)}）",
            f"正在分析的對話：{busy_chats} 個",
        ])


class LarkConnWatcher(logging.Handler):
    """Follows lark-oapi's own connect/disconnect log lines."""

    def __init__(self, health: Health) -> None:
        super().__init__(logging.INFO)
        self.health = health

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if ("disconnected to" in msg or "receive message loop exit" in msg
                or "connect failed" in msg):
            self.health.connected = False
        elif "connected to wss" in msg:
            self.health.connected = True
            self.health.last_connect = time.time()


def start_watchdog(loop, interval: float = 60, limit: float = 180,
                   heartbeat_every: int = 30, exit_fn=os._exit) -> threading.Thread:
    """Exit (so deploy/run.ps1 restarts us) if the WS event loop stops responding."""

    def run() -> None:
        n = 0
        while True:
            time.sleep(interval)
            done = threading.Event()
            try:
                loop.call_soon_threadsafe(done.set)
            except RuntimeError:  # loop closed: process is shutting down
                return
            if not done.wait(limit):
                # exit even if the log write itself blocks (e.g. frozen console)
                threading.Timer(5, exit_fn, (3,)).start()
                log.critical("watchdog: event loop unresponsive for %ds, restarting", limit)
                exit_fn(3)
                return
            n += 1
            if n % heartbeat_every == 0:
                log.info("heartbeat")

    t = threading.Thread(target=run, daemon=True, name="watchdog")
    t.start()
    return t
