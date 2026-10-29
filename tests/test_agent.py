"""Agent loop smoke test with a stubbed LLM (no network)."""

import json
from dataclasses import replace
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.agent.core import ElectionAgent
from app.config import Config
from app.db.store import Store


def _resp(content=None, tool_calls=None):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def _tc(name, args):
    return SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


class _StubLLM:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def chat(self, messages, tools=None, **kw):
        self.calls += 1
        return self.script.pop(0)


class TestAgentLoop(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.sqlite3")
        self.config = Config()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_tool_round_trip(self):
        # seed one election
        eid = self.store.upsert_election("county_mayor", 2022, "2022-11-26")
        self.store.replace_election_rows(
            eid,
            [{"county": "臺北市", "cand_no": 1, "name": "蔣萬安",
              "party": "中國國民黨", "elected": 1}],
            [{"county": "臺北市", "cand_no": 1, "votes": 575590, "share": 42.29}],
        )
        llm = _StubLLM([
            _resp(tool_calls=[_tc("county_results", {"county": "台北市", "year": 2022})]),
            _resp(content="蔣萬安 2022 年以 42.29% 當選台北市長。"),
        ])
        agent = ElectionAgent(llm, self.store, self.config)
        answer = agent.answer([], "2022台北市長誰贏了")
        self.assertIn("蔣萬安", answer)
        self.assertEqual(llm.calls, 2)

    def test_unknown_tool_surfaced(self):
        llm = _StubLLM([
            _resp(tool_calls=[_tc("no_such_tool", {})]),
            _resp(content="工具不存在，改用別的查法"),
        ])
        agent = ElectionAgent(llm, self.store, self.config)
        answer = agent.answer([], "test")
        self.assertTrue(answer)

    def test_rounds_exhausted_forces_final_answer(self):
        llm = _StubLLM([
            _resp(tool_calls=[_tc("list_elections", {})]),
            _resp(tool_calls=[_tc("list_elections", {})]),
            _resp(content="# 臺北市選情研判"),
        ])
        agent = ElectionAgent(llm, self.store, replace(self.config, max_tool_rounds=2))
        self.assertEqual(agent.answer([], "台北選情"), "# 臺北市選情研判")
        self.assertEqual(llm.calls, 3)


if __name__ == "__main__":
    unittest.main()
