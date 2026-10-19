"""Risk classification tests.

Every band boundary gets its own assertion. Off-by-one at a boundary is the
failure mode that matters here: it silently turns a "no travel" checkpoint into
a merely "severe" one, which is the one mistake the app must never make.
"""

import unittest

from routing.domain.risk import (
    Risk, classify, load_floor, rain_risk, severity, snow_risk, wind_risk,
)


class WindBands(unittest.TestCase):
    def test_bands(self):
        for mph, expected in [
            (0, Risk.LOW), (10, Risk.LOW), (24.9, Risk.LOW),
            (25.0, Risk.MODERATE), (30, Risk.MODERATE), (34.9, Risk.MODERATE),
            (35.0, Risk.HIGH), (40, Risk.HIGH), (44.9, Risk.HIGH),
            (45.0, Risk.SEVERE), (50, Risk.SEVERE), (54.9, Risk.SEVERE),
            (55.0, Risk.NO_TRAVEL), (80, Risk.NO_TRAVEL),
        ]:
            with self.subTest(mph=mph):
                self.assertIs(wind_risk(mph), expected)

    def test_no_travel_bound_is_inclusive(self):
        """The table says ">= 55 mph", so 55.0 exactly is already No Travel."""
        self.assertIs(wind_risk(54.999), Risk.SEVERE)
        self.assertIs(wind_risk(55.0), Risk.NO_TRAVEL)


class RainBands(unittest.TestCase):
    def test_bands(self):
        for iph, expected in [
            (0, Risk.LOW), (0.099, Risk.LOW),
            (0.10, Risk.MODERATE), (0.249, Risk.MODERATE),
            (0.25, Risk.HIGH), (0.499, Risk.HIGH),
            (0.50, Risk.SEVERE), (0.99, Risk.SEVERE),
            (1.01, Risk.NO_TRAVEL), (3.0, Risk.NO_TRAVEL),
        ]:
            with self.subTest(iph=iph):
                self.assertIs(rain_risk(iph), expected)

    def test_no_travel_bound_is_exclusive(self):
        """The table says "> 1.00", so 1.00 exactly is still Severe, not No Travel."""
        self.assertIs(rain_risk(1.00), Risk.SEVERE)
        self.assertIs(rain_risk(1.0001), Risk.NO_TRAVEL)


class SnowBands(unittest.TestCase):
    def test_bands(self):
        for iph, expected in [
            (0, Risk.LOW), (0.49, Risk.LOW),
            (0.5, Risk.MODERATE), (0.99, Risk.MODERATE),
            (1.0, Risk.HIGH), (1.99, Risk.HIGH),
            (2.0, Risk.SEVERE), (2.99, Risk.SEVERE),
            (3.01, Risk.NO_TRAVEL), (6.0, Risk.NO_TRAVEL),
        ]:
            with self.subTest(iph=iph):
                self.assertIs(snow_risk(iph), expected)

    def test_no_travel_bound_is_exclusive(self):
        """The table says "> 3.0", so 3.0 exactly is still Severe."""
        self.assertIs(snow_risk(3.0), Risk.SEVERE)
        self.assertIs(snow_risk(3.0001), Risk.NO_TRAVEL)


class LoadRules(unittest.TestCase):
    def test_55_plus_is_no_travel_for_any_load(self):
        for load in (0, 1_000, 80_000):
            with self.subTest(load=load):
                self.assertIs(load_floor(55.0, load)[0], Risk.NO_TRAVEL)
                self.assertIs(classify(55.0, 0, 0, load)["overall"], Risk.NO_TRAVEL)

    def test_45_to_54_escalates_only_above_30000(self):
        """Same wind, same weather - the load decides whether the truck rolls."""
        self.assertIs(classify(50.0, 0, 0, 25_000)["overall"], Risk.SEVERE)
        self.assertIs(classify(50.0, 0, 0, 30_000)["overall"], Risk.SEVERE)   # not strictly above
        self.assertIs(classify(50.0, 0, 0, 30_001)["overall"], Risk.NO_TRAVEL)
        self.assertIs(classify(50.0, 0, 0, 45_000)["overall"], Risk.NO_TRAVEL)

    def test_35_to_44_escalates_only_above_40000(self):
        self.assertIs(classify(40.0, 0, 0, 35_000)["overall"], Risk.HIGH)
        self.assertIs(classify(40.0, 0, 0, 40_000)["overall"], Risk.HIGH)     # not strictly above
        self.assertIs(classify(40.0, 0, 0, 40_001)["overall"], Risk.SEVERE)

    def test_below_35_has_no_load_rule(self):
        """A heavy load does not raise risk when the wind is below 35 mph."""
        self.assertIs(classify(30.0, 0, 0, 80_000)["overall"], Risk.MODERATE)
        self.assertIsNone(load_floor(30.0, 80_000)[1])

    def test_load_rules_never_lower_risk(self):
        """A load rule is a floor: heavy rain still wins if it is worse."""
        result = classify(40.0, 1.5, 0, 80_000)
        self.assertIs(result["overall"], Risk.NO_TRAVEL)   # rain, not the load floor


class OverallRisk(unittest.TestCase):
    def test_worst_condition_wins(self):
        result = classify(10.0, 0.60, 0.0, 10_000)
        self.assertIs(result["overall"], Risk.SEVERE)
        self.assertEqual(result["driver"], "rain")
        self.assertIs(result["bands"]["wind"], Risk.LOW)

    def test_driver_is_load_when_load_escalates(self):
        result = classify(50.0, 0, 0, 35_000)
        self.assertIs(result["overall"], Risk.NO_TRAVEL)
        self.assertEqual(result["driver"], "load")
        self.assertTrue(result["load_escalated"])
        self.assertIn("30,000 lb", result["reason"])

    def test_clear_conditions(self):
        result = classify(5.0, 0.0, 0.0, 20_000)
        self.assertIs(result["overall"], Risk.LOW)
        self.assertFalse(result["load_escalated"])

    def test_missing_and_negative_readings_are_treated_as_calm(self):
        """The weather provider can return null for a variable; that must not crash."""
        self.assertIs(classify(None, None, None, 50_000)["overall"], Risk.LOW)
        self.assertIs(wind_risk(-3.0), Risk.LOW)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class Severity(unittest.TestCase):
    """The continuous scale behind the heatmap.

    It is anchored to the same No Travel thresholds as the bands, so these
    expectations are derived from the risk table, not from the implementation.
    """

    def test_calm_is_zero(self):
        self.assertEqual(severity(0, 0, 0), 0.0)

    def test_each_no_travel_threshold_is_exactly_one(self):
        self.assertAlmostEqual(severity(55, 0, 0), 1.0, places=6)
        self.assertAlmostEqual(severity(0, 1.00, 0), 1.0, places=6)
        self.assertAlmostEqual(severity(0, 0, 3.0), 1.0, places=6)

    def test_half_of_a_threshold_is_a_half(self):
        self.assertAlmostEqual(severity(27.5, 0, 0), 0.5, places=6)
        self.assertAlmostEqual(severity(0, 0.50, 0), 0.5, places=6)
        self.assertAlmostEqual(severity(0, 0, 1.5), 0.5, places=6)

    def test_clamped_at_one(self):
        self.assertEqual(severity(150, 9, 20), 1.0)

    def test_worst_reading_wins(self):
        self.assertAlmostEqual(severity(5, 0.50, 0), 0.5, places=6)

    def test_light_weather_still_registers(self):
        """This is the whole point: a band-based heatmap would show nothing here."""
        self.assertIs(wind_risk(5), Risk.LOW)
        self.assertGreater(severity(5, 0, 0), 0.0)

    def test_missing_readings_are_calm(self):
        self.assertEqual(severity(None, None, None), 0.0)

    def test_negative_readings_are_calm(self):
        self.assertEqual(severity(-10, -1, -1), 0.0)
