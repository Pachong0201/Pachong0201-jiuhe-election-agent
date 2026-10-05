import tempfile
import unittest
from pathlib import Path

from app.db.store import Store, normalize_county


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.sqlite3")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _seed(self):
        eid = self.store.upsert_election("county_mayor", 2022, "2022-11-26")
        self.store.replace_election_rows(
            eid,
            [
                {"county": "臺北市", "cand_no": 1, "name": "蔣萬安",
                 "party": "中國國民黨", "elected": 1},
                {"county": "臺北市", "cand_no": 2, "name": "陳時中",
                 "party": "民主進步黨", "elected": 0},
            ],
            [
                {"county": "臺北市", "cand_no": 1, "votes": 575590, "share": 42.29},
                {"county": "臺北市", "cand_no": 2, "votes": 434558, "share": 31.93},
                {"county": "臺北市", "township": "松山區", "cand_no": 1,
                 "votes": 40000, "share": 45.0},
            ],
        )
        return eid

    def test_normalize_county(self):
        self.assertEqual(normalize_county("台北市"), "臺北市")
        self.assertEqual(normalize_county("台中"), "臺中市")
        self.assertEqual(normalize_county("高雄"), "高雄市")

    def test_county_results(self):
        self._seed()
        rows = self.store.county_results("台北市", 2022)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["name"], "蔣萬安")
        self.assertTrue(rows[0]["elected"])

    def test_township_results(self):
        self._seed()
        rows = self.store.township_results("台北", 2022)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["township"], "松山區")

    def test_national_join_fallback(self):
        """President-style candidates registered under 全國 still join."""
        eid = self.store.upsert_election("president", 2024, "2024-01-13")
        self.store.replace_election_rows(
            eid,
            [{"county": "全國", "cand_no": 1, "name": "賴清德",
              "party": "民主進步黨", "elected": 1}],
            [{"county": "臺北市", "cand_no": 1, "votes": 600000, "share": 38.13}],
        )
        rows = self.store.county_results("臺北市", 2024, "president")
        self.assertEqual(rows[0]["name"], "賴清德")

    def test_race_candidates(self):
        self.store.upsert_race_candidate("2026", "台北市", "蔣萬安",
                                         party="中國國民黨", incumbent=True)
        rows = self.store.race_candidates("2026", "臺北市")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["incumbent"], 1)

    def test_fts_search(self):
        self.store.add_knowledge("台中", "faction", "台中紅黑派長期影響地方選舉")
        self.store.add_event("台中", "2026-10-01", "rally", "盧秀燕出席大造勢")
        hits = self.store.search_kb("紅黑派", "臺中市")
        self.assertTrue(any(h["_type"] == "knowledge" for h in hits))
        hits = self.store.search_kb("造勢")
        self.assertTrue(any(h["_type"] == "event" for h in hits))


if __name__ == "__main__":
    unittest.main()
