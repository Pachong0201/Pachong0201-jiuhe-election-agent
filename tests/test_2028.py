import tempfile
import unittest
from pathlib import Path

from app.bot.progress import Progress
from app.bot.server import Target, report_prompt, route
from app.db.store import Store, normalize_district
from app.tools import kb


def _seed(store: Store) -> None:
    """Two-district county + nationwide president / party-list rows."""
    leg = store.upsert_election("regional_legislator", 2024, "2024-01-13")
    store.replace_election_rows(
        leg,
        [
            {"county": "臺北市", "district": "臺北市第01選區", "cand_no": 1,
             "name": "藍甲", "party": "中國國民黨", "elected": 1},
            {"county": "臺北市", "district": "臺北市第01選區", "cand_no": 2,
             "name": "綠甲", "party": "民主進步黨"},
            {"county": "臺北市", "district": "臺北市第02選區", "cand_no": 1,
             "name": "綠乙", "party": "民主進步黨", "elected": 1},
        ],
        [
            {"county": "臺北市", "district": "臺北市第01選區", "cand_no": 1, "votes": 60, "share": 60.0},
            {"county": "臺北市", "district": "臺北市第01選區", "cand_no": 2, "votes": 40, "share": 40.0},
            {"county": "臺北市", "district": "臺北市第02選區", "cand_no": 1, "votes": 70, "share": 100.0},
            {"county": "臺北市", "district": "臺北市第01選區", "township": "北投區",
             "village": "甲里", "cand_no": 1, "votes": 60},
            {"county": "臺北市", "district": "臺北市第02選區", "township": "大安區",
             "village": "乙里", "cand_no": 1, "votes": 70},
        ],
    )
    pres = store.upsert_election("president", 2024, "2024-01-13")
    store.replace_election_rows(
        pres,
        [{"county": "全國", "cand_no": 1, "name": "綠總統", "party": "民主進步黨", "elected": 1},
         {"county": "全國", "cand_no": 2, "name": "藍總統", "party": "中國國民黨"}],
        [{"county": "臺北市", "township": "北投區", "village": "甲里", "cand_no": 1, "votes": 30},
         {"county": "臺北市", "township": "北投區", "village": "甲里", "cand_no": 2, "votes": 70},
         {"county": "臺北市", "township": "大安區", "village": "乙里", "cand_no": 1, "votes": 99}],
    )
    pl = store.upsert_election("party_list", 2024, "2024-01-13")
    store.replace_election_rows(
        pl,
        [{"county": "全國", "cand_no": 1, "name": "民主進步黨", "party": "民主進步黨"},
         {"county": "全國", "cand_no": 2, "name": "中國國民黨", "party": "中國國民黨"},
         {"county": "全國", "cand_no": 3, "name": "小黨", "party": "小黨"}],
        [{"county": "全國", "cand_no": 1, "votes": 500, "share": 50.0},
         {"county": "全國", "cand_no": 2, "votes": 470, "share": 47.0},
         {"county": "全國", "cand_no": 3, "votes": 30, "share": 3.0}],
    )


class Store2028Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "kb.sqlite3")
        _seed(self.store)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_normalize_district(self):
        self.assertEqual(normalize_district("台北", "三"), "臺北市第03選區")
        self.assertEqual(normalize_district("新北市", "十二"), "新北市第12選區")
        self.assertEqual(normalize_district("高雄", "９"), "高雄市第09選區")

    def test_district_results_do_not_mix_districts(self):
        self.assertEqual(self.store.districts("台北"), ["臺北市第01選區", "臺北市第02選區"])
        rows = self.store.district_results("臺北市第01選區", 2024)
        self.assertEqual([r["name"] for r in rows], ["藍甲", "綠甲"])
        self.assertEqual(rows[0]["elected"], 1)

    def test_president_overlay_uses_district_villages(self):
        rows = self.store.overlay_by_district("臺北市第01選區", 2024, "president")
        self.assertEqual([(r["name"], r["votes"], r["share"]) for r in rows],
                         [("藍總統", 70, 70.0), ("綠總統", 30, 30.0)])

    def test_party_list_seats_threshold_and_remainder(self):
        out = self.store.legislator_seats(2024)
        seats = {r["party"]: r for r in out["seats"]}
        # 小黨 3% < 5% threshold; 34 seats split 500:470 -> 17.53 / 16.47
        self.assertNotIn("小黨", seats)
        self.assertEqual(seats["民主進步黨"]["party_list"], 18)
        self.assertEqual(seats["中國國民黨"]["party_list"], 16)
        self.assertEqual(seats["中國國民黨"]["regional_legislator"], 1)

    def test_district_margins_rating(self):
        rows = self.store.district_margins(2024)
        d1 = next(r for r in rows if r["district"] == "臺北市第01選區")
        self.assertEqual((d1["winner"], d1["margin_pp"], d1["rating"]), ("藍甲", 20.0, "安全"))

    def test_clean_report_drops_preamble(self):
        from app.bot.cards import clean_report
        self.assertEqual(clean_report("I have everything.\n\n---\n\n# 標題\n內容"), "# 標題\n內容")
        self.assertEqual(clean_report("# 標題\n內容"), "# 標題\n內容")

    def test_prospective_candidate_and_poll_roundtrip(self):
        kb.add_candidate_tool(self.store, cycle="2028", office="president", name="某甲",
                              party="中國國民黨", status="prospective",
                              source_url="https://example.com/a")
        got = kb.race_candidates_tool(self.store, "全國", "president", "2028")
        self.assertEqual(got["candidates"][0]["status"], "prospective")
        self.assertIn("note", kb.race_candidates_tool(self.store, "全國", "legislator", "2028"))
        kb.add_poll_tool(self.store, race="president", org="某民調", results={"某甲": 30},
                         fieldwork_end="2026-10-01", source_url="https://example.com/p")
        polls = kb.polls_tool(self.store, race="president")["polls"]
        self.assertEqual(polls[0]["county"], "全國")
        self.assertEqual(kb.polls_tool(self.store, race="county_mayor")["polls"], [])

    def test_schema_migration_adds_district(self):
        cols = {r[1] for r in self.store.conn.execute("PRAGMA table_info(polls)")}
        self.assertIn("district", cols)


class Route2028Test(unittest.TestCase):
    def test_routes(self):
        cases = {
            "2028总统选情": Target("president"),
            "/report 總統": Target("president"),
            "台北第3选区立委": Target("legislator", "臺北市", "臺北市第03選區"),
            "臺北市第三選區立委選情": Target("legislator", "臺北市", "臺北市第03選區"),
            "/report 臺北市第3選區": Target("legislator", "臺北市", "臺北市第03選區"),
            "新北立委选情": Target("legislator", "新北市"),
            "全国立委席次态势": Target("legislator_national"),
            "/report 立委": Target("legislator_national"),
            "/report 嘉义市": Target("county_mayor", "嘉義市"),
            "嘉義縣選情": Target("county_mayor", "嘉義縣"),
        }
        for text, want in cases.items():
            self.assertEqual(route(text), ("report", want), text)

    def test_prompts_name_the_right_structure(self):
        self.assertIn("總統選情報告結構", report_prompt(Target("president")))
        self.assertIn("立委全國席次報告結構", report_prompt(Target("legislator_national")))
        p = report_prompt(Target("legislator", "臺北市", "臺北市第03選區"))
        self.assertIn("立委選區報告結構", p)
        self.assertIn("臺北市第03選區", p)
        self.assertIn("九合一", report_prompt(Target("county_mayor", "高雄市")))
        self.assertIn("未指定縣市", report_prompt(Target()))

    def test_progress_search_label(self):
        self.assertIn("檢索兩黨候選人近期新聞", Progress("x").render())
        self.assertIn("各黨人選", Progress("x", "檢索各黨人選與提名動態新聞").render())
