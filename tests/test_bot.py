import json
import unittest

from app.bot.cards import markdown_card, report_messages, split_markdown
from app.bot.session import SessionRouter


class TestCards(unittest.TestCase):
    def test_split_short(self):
        self.assertEqual(split_markdown("short text"), ["short text"])

    def test_split_at_headings(self):
        md = "# A\n" + ("x" * 2000) + "\n# B\n" + ("y" * 2000)
        parts = split_markdown(md, limit=1200)
        self.assertGreaterEqual(len(parts), 2)
        self.assertTrue(all(len(p) <= 1200 or "\n" not in p for p in parts))

    def test_report_messages(self):
        msgs = report_messages("台北選情", "# 標題\n內容")
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["msg_type"], "interactive")
        card = json.loads(msgs[0]["content"])
        self.assertIn("台北", card["header"]["title"]["content"])

    def test_markdown_card(self):
        m = markdown_card("t", "**bold**")
        card = json.loads(m["content"])
        self.assertEqual(card["elements"][0]["text"]["content"], "**bold**")


class TestSessionRouter(unittest.TestCase):
    def test_acquire_serializes(self):
        r = SessionRouter()
        s = r.acquire("chat1")
        self.assertIsNotNone(s)
        self.assertIsNone(r.acquire("chat1"))  # busy
        r.release("chat1")
        self.assertIsNotNone(r.acquire("chat1"))

    def test_history_window(self):
        r = SessionRouter(history_turns=2)
        s = r.get("c")
        for i in range(5):
            r.record_turn(s, f"u{i}", f"a{i}")
        self.assertEqual(len(s.history), 4)  # 2 turns * 2 messages

    def test_reset(self):
        r = SessionRouter()
        r.get("c")
        r.reset("c")
        s = r.get("c")
        self.assertEqual(len(s.history), 0)


if __name__ == "__main__":
    unittest.main()
