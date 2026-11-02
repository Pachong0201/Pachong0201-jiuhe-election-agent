import asyncio
import logging
import threading
import time
import unittest

from app.bot.health import Health, LarkConnWatcher, start_watchdog
from app.bot.session import SessionRouter
from app.doctor import scan_log


class TestHealth(unittest.TestCase):
    def test_watchdog_exits_when_loop_is_stuck(self):
        loop = asyncio.new_event_loop()  # never run: callbacks are never processed
        fired = threading.Event()
        start_watchdog(loop, interval=0.01, limit=0.05, exit_fn=lambda code: fired.set())
        self.assertTrue(fired.wait(2))
        loop.close()

    def test_watchdog_quiet_when_loop_runs(self):
        loop = asyncio.new_event_loop()
        threading.Thread(target=loop.run_forever, daemon=True).start()
        fired = threading.Event()
        start_watchdog(loop, interval=0.01, limit=1, exit_fn=lambda code: fired.set())
        self.assertFalse(fired.wait(0.3))
        loop.call_soon_threadsafe(loop.stop)

    def test_conn_watcher(self):
        h = Health()
        w = LarkConnWatcher(h)
        rec = lambda m: logging.LogRecord("Lark", 20, "", 0, m, None, None)  # noqa: E731
        w.emit(rec("connected to wss://msg-frontier.feishu.cn/ws/v2 [conn_id=1]"))
        self.assertTrue(h.connected)
        w.emit(rec("disconnected to wss://msg-frontier.feishu.cn/ws/v2"))
        self.assertFalse(h.connected)
        self.assertIn("斷線", h.status_text(0))

    def test_stale_busy_session_is_taken_over(self):
        r = SessionRouter(stale_after=0.05)
        self.assertIsNotNone(r.acquire("c"))
        self.assertIsNone(r.acquire("c"))
        self.assertEqual(r.busy_count(), 1)
        time.sleep(0.08)
        self.assertIsNotNone(r.acquire("c"))

    def test_doctor_scan(self):
        lines = [
            "2026-11-01 10:00:00,1 INFO app.bot.server: starting Feishu WS client (long connection)",
            "2026-11-01 10:00:01,1 INFO Lark: connected to wss://x [conn_id=1]",
            "2026-11-01 10:05:00,1 INFO Lark: disconnected to wss://x",
            "2026-11-01 10:06:00,1 ERROR app.bot.server: reply failed: 230002 bot not in chat",
        ]
        r = scan_log(lines)
        self.assertLess(r["last"]["connect"][0], r["last"]["disconnect"][0])
        self.assertEqual(len(r["errors"]), 1)
        self.assertNotIn("message", r["last"])
