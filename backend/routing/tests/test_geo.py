"""Geodesic helper and simplification tests.

Distances are checked against values that can be derived independently (a degree
of latitude, a known city pair) rather than against this module's own output.
"""

import unittest

from routing.domain.geo import (
    destination, haversine_m, initial_bearing, max_deviation_m, midpoint, resample, simplify,
)

CHICAGO = [-87.6298, 41.8781]
DENVER = [-104.9903, 39.7392]


class Distance(unittest.TestCase):
    def test_one_degree_of_latitude_is_about_111_km(self):
        self.assertAlmostEqual(haversine_m([0, 0], [0, 1]) / 1000, 111.19, places=1)

    def test_zero_for_the_same_point(self):
        self.assertEqual(haversine_m(CHICAGO, CHICAGO), 0.0)

    def test_chicago_to_denver_is_about_1480_km(self):
        """Published great-circle distance is ~1,475 km; well inside 1%."""
        km = haversine_m(CHICAGO, DENVER) / 1000
        self.assertTrue(1460 < km < 1495, km)

    def test_symmetric(self):
        self.assertAlmostEqual(haversine_m(CHICAGO, DENVER), haversine_m(DENVER, CHICAGO), places=6)


class Bearing(unittest.TestCase):
    def test_due_north_and_east(self):
        self.assertAlmostEqual(initial_bearing([0, 0], [0, 1]), 0.0, places=6)
        self.assertAlmostEqual(initial_bearing([0, 0], [1, 0]), 90.0, places=6)

    def test_due_south_and_west(self):
        self.assertAlmostEqual(initial_bearing([0, 1], [0, 0]), 180.0, places=6)
        self.assertAlmostEqual(initial_bearing([1, 0], [0, 0]), 270.0, places=6)

    def test_chicago_to_denver_heads_west_south_west(self):
        self.assertTrue(250 < initial_bearing(CHICAGO, DENVER) < 275)


class Destination(unittest.TestCase):
    def test_round_trips_through_haversine(self):
        for bearing in (0, 45, 90, 180, 270, 315):
            with self.subTest(bearing=bearing):
                target = destination(CHICAGO, bearing, 80_000)
                self.assertAlmostEqual(haversine_m(CHICAGO, target) / 1000, 80.0, places=1)

    def test_bearing_is_preserved(self):
        target = destination(CHICAGO, 90.0, 50_000)
        self.assertAlmostEqual(initial_bearing(CHICAGO, target), 90.0, places=2)

    def test_midpoint_is_equidistant(self):
        mid = midpoint(CHICAGO, DENVER)
        self.assertAlmostEqual(haversine_m(CHICAGO, mid), haversine_m(mid, DENVER), delta=50)


class Resample(unittest.TestCase):
    def test_keeps_both_ends(self):
        line = [[i * 0.1, 0.0] for i in range(100)]
        out = resample(line, 10)
        self.assertEqual(len(out), 10)
        self.assertEqual(out[0], line[0])
        self.assertEqual(out[-1], line[-1])

    def test_short_input_is_returned_as_endpoints(self):
        self.assertEqual(resample([[0, 0], [1, 1]], 10), [[0, 0], [1, 1]])


class Simplify(unittest.TestCase):
    def test_a_straight_line_collapses_to_its_endpoints(self):
        line = [[i * 0.01, 0.0] for i in range(500)]
        self.assertEqual(simplify(line, 50.0), [line[0], line[-1]])

    def test_a_detour_larger_than_the_tolerance_is_kept(self):
        line = [[0.0, 0.0], [0.5, 0.2], [1.0, 0.0]]
        self.assertEqual(len(simplify(line, 50.0)), 3)

    def test_a_wobble_smaller_than_the_tolerance_is_dropped(self):
        """A 0.00005 degree jog is about 5 m, far under a 100 m tolerance."""
        line = [[0.0, 0.0], [0.5, 0.00005], [1.0, 0.0]]
        self.assertEqual(simplify(line, 100.0), [[0.0, 0.0], [1.0, 0.0]])

    def test_endpoints_always_survive(self):
        line = [[i * 0.01, (i % 3) * 0.0001] for i in range(300)]
        out = simplify(line, 200.0)
        self.assertEqual(out[0], line[0])
        self.assertEqual(out[-1], line[-1])

    def test_never_returns_more_points_than_it_was_given(self):
        line = [[i * 0.01, (i % 7) * 0.001] for i in range(400)]
        self.assertLessEqual(len(simplify(line, 100.0)), len(line))


class Deviation(unittest.TestCase):
    def test_identical_routes_barely_deviate(self):
        line = [[i * 0.05, 40.0] for i in range(200)]
        self.assertLess(max_deviation_m(line, line), 1.0)

    def test_a_parallel_route_deviates_by_its_offset(self):
        """Two lines a quarter degree of latitude apart sit about 27 km apart."""
        a = [[i * 0.05, 40.0] for i in range(200)]
        b = [[i * 0.05, 40.25] for i in range(200)]
        self.assertTrue(26_000 < max_deviation_m(a, b) < 29_000)

    def test_empty_input_is_infinitely_far(self):
        self.assertEqual(max_deviation_m([], [[0, 0]]), float("inf"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
