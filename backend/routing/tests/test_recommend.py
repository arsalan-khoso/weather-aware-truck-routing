"""Route scoring and recommendation tests.

The ranking is strictly lexicographic, so each test ties every criterion above
the one under test and checks that the criterion below it is ignored. That is
the only way to prove a faster route can never beat a safer one.
"""

import unittest

from routing.domain.recommend import rank, recommend, score
from routing.domain.risk import Risk


def cp(risk, miles):
    return {"risk": risk, "segment_miles": miles}


def route(index, checkpoints, miles=100.0, duration_s=7200.0):
    return score(index, checkpoints, miles, duration_s)


class Scoring(unittest.TestCase):
    def test_miles_are_bucketed_by_risk(self):
        s = route(0, [cp(Risk.LOW, 30), cp(Risk.HIGH, 20), cp(Risk.LOW, 50)])
        self.assertEqual(s.miles_by_risk[Risk.LOW], 80.0)
        self.assertEqual(s.high_miles, 20.0)
        self.assertEqual(s.severe_miles, 0.0)

    def test_average_risk_is_weighted_by_miles(self):
        """90 calm miles and 10 severe ones average 0.30, not the 1.50 a plain mean gives."""
        s = route(0, [cp(Risk.LOW, 90), cp(Risk.SEVERE, 10)])
        self.assertAlmostEqual(s.avg_risk, 0.30, places=4)
        self.assertAlmostEqual(s.mean_risk, 1.50, places=4)

    def test_worst_risk_is_reported(self):
        s = route(0, [cp(Risk.LOW, 50), cp(Risk.SEVERE, 50)])
        self.assertIs(s.worst, Risk.SEVERE)

    def test_route_with_a_no_travel_checkpoint_is_not_viable(self):
        self.assertFalse(route(0, [cp(Risk.NO_TRAVEL, 10), cp(Risk.LOW, 90)]).viable)
        self.assertTrue(route(0, [cp(Risk.SEVERE, 10), cp(Risk.LOW, 90)]).viable)

    def test_empty_checkpoints_do_not_crash(self):
        s = route(0, [])
        self.assertEqual(s.avg_risk, 0.0)
        self.assertIs(s.worst, Risk.LOW)


class PreferenceOrder(unittest.TestCase):
    def test_fewest_severe_miles_wins_even_when_much_slower(self):
        safe_slow = route(0, [cp(Risk.SEVERE, 10), cp(Risk.LOW, 90)], duration_s=20 * 3600)
        risky_fast = route(1, [cp(Risk.SEVERE, 60), cp(Risk.LOW, 40)], duration_s=10 * 3600)
        out = recommend([safe_slow, risky_fast])
        self.assertEqual(out["recommended_index"], 0)
        self.assertEqual(out["decided_by"], "severe_miles")

    def test_high_miles_break_a_severe_tie(self):
        a = route(0, [cp(Risk.SEVERE, 10), cp(Risk.HIGH, 10), cp(Risk.LOW, 80)])
        b = route(1, [cp(Risk.SEVERE, 10), cp(Risk.HIGH, 40), cp(Risk.LOW, 50)])
        out = recommend([a, b])
        self.assertEqual(out["recommended_index"], 0)
        self.assertEqual(out["decided_by"], "high_miles")

    def test_average_risk_breaks_a_severe_and_high_tie(self):
        """Same severe and high miles; the rest of the route decides."""
        a = route(0, [cp(Risk.SEVERE, 10), cp(Risk.HIGH, 10), cp(Risk.LOW, 80)])
        b = route(1, [cp(Risk.SEVERE, 10), cp(Risk.HIGH, 10), cp(Risk.MODERATE, 80)])
        out = recommend([a, b])
        self.assertEqual(out["recommended_index"], 0)
        self.assertEqual(out["decided_by"], "avg_risk")

    def test_travel_time_only_decides_when_risk_is_identical(self):
        a = route(0, [cp(Risk.LOW, 100)], duration_s=12 * 3600)
        b = route(1, [cp(Risk.LOW, 100)], duration_s=9 * 3600)
        out = recommend([a, b])
        self.assertEqual(out["recommended_index"], 1)
        self.assertEqual(out["decided_by"], "duration_s")
        self.assertIn("min", out["reason"])

    def test_no_travel_route_is_ranked_last_despite_fewer_severe_miles(self):
        blocked = route(0, [cp(Risk.NO_TRAVEL, 5), cp(Risk.LOW, 95)])
        severe = route(1, [cp(Risk.SEVERE, 50), cp(Risk.LOW, 50)])
        self.assertEqual([s.route_index for s in rank([blocked, severe])], [1, 0])
        self.assertEqual(recommend([blocked, severe])["recommended_index"], 1)

    def test_ranking_is_a_total_order_over_three_routes(self):
        a = route(0, [cp(Risk.SEVERE, 40), cp(Risk.LOW, 60)])
        b = route(1, [cp(Risk.SEVERE, 10), cp(Risk.LOW, 90)])
        c = route(2, [cp(Risk.NO_TRAVEL, 10), cp(Risk.LOW, 90)])
        self.assertEqual([s.route_index for s in rank([a, b, c])], [1, 0, 2])


class Explanations(unittest.TestCase):
    def test_zero_severe_miles_is_explained_as_the_only_clean_option(self):
        a = route(0, [cp(Risk.LOW, 100)])
        b = route(1, [cp(Risk.SEVERE, 30), cp(Risk.LOW, 70)])
        self.assertIn("no severe miles", recommend([a, b])["reason"])

    def test_all_routes_blocked_says_do_not_drive(self):
        a = route(0, [cp(Risk.NO_TRAVEL, 10), cp(Risk.LOW, 90)])
        b = route(1, [cp(Risk.NO_TRAVEL, 40), cp(Risk.LOW, 60)])
        out = recommend([a, b])
        self.assertEqual(out["recommended_index"], 0)
        self.assertFalse(out["viable"])
        self.assertIn("do not drive", out["reason"].lower())

    def test_single_route_is_handled(self):
        out = recommend([route(0, [cp(Risk.LOW, 100)])])
        self.assertEqual(out["recommended_index"], 0)
        self.assertIn("Only one route", out["reason"])

    def test_no_routes_is_handled(self):
        out = recommend([])
        self.assertIsNone(out["recommended_index"])

    def test_identical_routes_keep_provider_order(self):
        a = route(0, [cp(Risk.LOW, 100)], duration_s=3600)
        b = route(1, [cp(Risk.LOW, 100)], duration_s=3600)
        out = recommend([a, b])
        self.assertEqual(out["recommended_index"], 0)
        self.assertIsNone(out["decided_by"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
