import unittest

from app.tools import calc


class TestCalc(unittest.TestCase):
    def test_vote_share(self):
        self.assertEqual(calc.vote_share(575590, 1361430), 42.28)
        self.assertEqual(calc.vote_share(0, 0), 0.0)

    def test_swing(self):
        self.assertEqual(calc.swing_pp(42.29, 40.81), 1.48)
        self.assertIsNone(calc.swing_pp(None, 40.0))

    def test_residual(self):
        self.assertEqual(calc.residual(45.0, 38.0), 7.0)
        self.assertIsNone(calc.residual(45.0, None))

    def test_competitiveness(self):
        rows = [
            {"name": "A", "party": "X", "share": 42.29},
            {"name": "B", "party": "Y", "share": 31.93},
            {"name": "C", "party": "Z", "share": 25.14},
        ]
        v = calc.competitiveness(rows)
        self.assertEqual(v["class"], "safe")
        self.assertAlmostEqual(v["margin_pp"], 10.36, places=2)
        self.assertEqual(v["leader"]["name"], "A")
        self.assertEqual(v["runner_up"]["name"], "B")

        toss = calc.competitiveness([
            {"name": "A", "share": 41.06}, {"name": "B", "share": 40.81}])
        self.assertEqual(toss["class"], "tossup")

        empty = calc.competitiveness([])
        self.assertEqual(empty["class"], "no_data")


if __name__ == "__main__":
    unittest.main()
