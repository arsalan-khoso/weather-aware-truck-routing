"""Checkpoint sampling and ETA tests.

The route used here is synthetic on purpose: 99 segments of exactly one mile at
exactly one minute each, so every expected distance and arrival time is known by
hand. A real provider response cannot pin down interpolation this precisely.
"""

import unittest
from datetime import datetime, timedelta, timezone

from routing.domain.checkpoints import METERS_PER_MILE, cumulative, sample

DEPARTURE = datetime(2026, 1, 15, 6, 0, tzinfo=timezone.utc)
SEGMENTS = 99


def straight_route():
    """100 points, 1 mile and 1 minute per segment: 99 miles at a steady 60 mph."""
    geometry = [[-100.0 + i * 0.01, 40.0] for i in range(SEGMENTS + 1)]
    distances = [METERS_PER_MILE] * SEGMENTS
    durations = [60.0] * SEGMENTS
    return geometry, distances, durations


def sample_route(interval_miles, **overrides):
    geometry, distances, durations = straight_route()
    kwargs = dict(
        total_distance_m=SEGMENTS * METERS_PER_MILE,
        total_duration_s=SEGMENTS * 60.0,
        interval_miles=interval_miles,
        departure=DEPARTURE,
    )
    kwargs.update(overrides)
    return sample(geometry, distances, durations, **kwargs)


class Cumulative(unittest.TestCase):
    def test_runs_from_zero_to_total(self):
        cum = cumulative([10.0, 20.0, 30.0], count=4, total=60.0)
        self.assertEqual(cum, [0.0, 10.0, 30.0, 60.0])

    def test_falls_back_to_even_spread_when_annotation_missing(self):
        self.assertEqual(cumulative(None, count=5, total=100.0), [0.0, 25.0, 50.0, 75.0, 100.0])

    def test_falls_back_when_annotation_is_too_short(self):
        self.assertEqual(cumulative([1.0], count=5, total=100.0), [0.0, 25.0, 50.0, 75.0, 100.0])


class Placement(unittest.TestCase):
    def test_lands_on_exact_mile_marks(self):
        points = sample_route(25)
        self.assertEqual([p.miles for p in points], [0.0, 25.0, 50.0, 75.0, 99.0])

    def test_origin_and_destination_are_always_included(self):
        for interval in (10, 25, 50):
            with self.subTest(interval=interval):
                points = sample_route(interval)
                self.assertEqual(points[0].miles, 0.0)
                self.assertAlmostEqual(points[-1].miles, 99.0, places=2)

    def test_interval_10_gives_a_checkpoint_every_10_miles(self):
        points = sample_route(10)
        self.assertEqual([p.miles for p in points][:4], [0.0, 10.0, 20.0, 30.0])
        self.assertEqual(len(points), 11)          # 0,10,...,90 plus the destination

    def test_position_is_interpolated_not_snapped_to_a_vertex(self):
        """Mile 25 of this route is vertex 25, at longitude -99.75."""
        points = sample_route(25)
        self.assertAlmostEqual(points[1].lon, -99.75, places=6)
        self.assertAlmostEqual(points[1].lat, 40.0, places=6)

    def test_route_shorter_than_the_interval_still_yields_both_ends(self):
        points = sample_route(50_000)
        self.assertEqual(len(points), 2)
        self.assertEqual(points[0].miles, 0.0)
        self.assertAlmostEqual(points[1].miles, 99.0, places=2)


class ArrivalTimes(unittest.TestCase):
    def test_eta_uses_the_duration_curve(self):
        """At 60 mph, mile 25 is reached 25 minutes after departure - exactly."""
        points = sample_route(25)
        self.assertEqual(points[0].eta, DEPARTURE)
        self.assertEqual(points[1].eta, DEPARTURE + timedelta(minutes=25))
        self.assertEqual(points[2].eta, DEPARTURE + timedelta(minutes=50))

    def test_final_eta_matches_the_total_route_duration(self):
        points = sample_route(25)
        self.assertEqual(points[-1].eta, DEPARTURE + timedelta(seconds=SEGMENTS * 60))

    def test_etas_increase_along_the_route(self):
        etas = [p.eta for p in sample_route(10)]
        self.assertEqual(etas, sorted(etas))

    def test_slow_first_half_pushes_later_etas_out(self):
        """ETA must follow the real duration curve, not distance times a constant."""
        geometry, distances, _ = straight_route()
        durations = [120.0] * 50 + [60.0] * 49      # 30 mph, then 60 mph
        points = sample(geometry, distances, durations,
                        total_distance_m=SEGMENTS * METERS_PER_MILE,
                        total_duration_s=sum(durations),
                        interval_miles=25, departure=DEPARTURE)
        self.assertEqual(points[1].eta, DEPARTURE + timedelta(minutes=50))    # 25 mi at 30 mph
        self.assertEqual(points[-1].eta, DEPARTURE + timedelta(seconds=sum(durations)))


class MileAttribution(unittest.TestCase):
    def test_owned_stretches_tile_the_whole_route(self):
        """Severe-mile counting is only meaningful if the stretches sum to the route."""
        for interval in (10, 25, 50):
            with self.subTest(interval=interval):
                points = sample_route(interval)
                self.assertAlmostEqual(sum(p.segment_miles for p in points), 99.0, places=2)

    def test_interior_checkpoints_own_one_full_interval(self):
        points = sample_route(25)
        self.assertAlmostEqual(points[1].segment_miles, 25.0, places=3)
        self.assertAlmostEqual(points[2].segment_miles, 25.0, places=3)

    def test_end_checkpoints_own_half_an_interval(self):
        points = sample_route(25)
        self.assertAlmostEqual(points[0].segment_miles, 12.5, places=3)

    def test_no_stretch_is_negative(self):
        self.assertTrue(all(p.segment_miles >= 0 for p in sample_route(10)))


class BadInput(unittest.TestCase):
    def test_single_point_geometry_is_rejected(self):
        with self.assertRaises(ValueError):
            sample([[0.0, 0.0]], [], [], total_distance_m=0, total_duration_s=0,
                   interval_miles=10, departure=DEPARTURE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
