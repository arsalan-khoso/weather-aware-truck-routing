"""Forecast parsing and lookup tests.

These cover the pure parts of the weather service - turning an Open-Meteo
response into a series, and reading the right hour out of it - with no network.

They exist because "rain and snow are 0.00" is indistinguishable, from the UI,
between fair weather and a parser that silently drops those fields. These tests
feed non-zero rain and snow through the same code the app uses and assert they
come out, so a zero on screen can be trusted to mean a zero in the forecast.
"""

import os
import unittest
from datetime import datetime, timedelta, timezone

import django
from django.conf import settings

if not settings.configured:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()

from routing.domain.risk import Risk, classify                    # noqa: E402
from routing.services.weather import Series, _as_series, snap     # noqa: E402

HOUR = 3600
START = datetime(2026, 1, 15, 6, 0, tzinfo=timezone.utc)


def block(**columns):
    """An Open-Meteo `hourly` block with the given columns, 6 hours long."""
    hours = 6
    base = int(START.timestamp())
    return {
        "hourly": {
            "time": [base + i * HOUR for i in range(hours)],
            "wind_speed_10m": columns.get("wind", [0.0] * hours),
            "rain": columns.get("rain", [0.0] * hours),
            "snowfall": columns.get("snow", [0.0] * hours),
        }
    }


class Parsing(unittest.TestCase):
    def test_all_three_variables_survive_parsing(self):
        series = _as_series(block(
            wind=[5, 12, 48, 60, 20, 3],
            rain=[0.0, 0.05, 0.30, 0.80, 1.40, 0.0],
            snow=[0.0, 0.2, 1.5, 2.5, 4.0, 0.0],
        ))
        self.assertEqual(series.wind, [5, 12, 48, 60, 20, 3])
        self.assertEqual(series.rain, [0.0, 0.05, 0.30, 0.80, 1.40, 0.0])
        self.assertEqual(series.snow, [0.0, 0.2, 1.5, 2.5, 4.0, 0.0])

    def test_rain_and_snow_are_read_independently_of_wind(self):
        """A calm, wet hour must still report its rain."""
        series = _as_series(block(wind=[0.0] * 6, rain=[0.9] * 6, snow=[0.0] * 6))
        self.assertEqual(max(series.rain), 0.9)
        self.assertEqual(max(series.wind), 0.0)

    def test_nulls_become_zero_rather_than_crashing(self):
        series = _as_series(block(rain=[None, 0.4, None, 0.0, None, 0.1]))
        self.assertEqual(series.rain, [0.0, 0.4, 0.0, 0.0, 0.0, 0.1])

    def test_a_short_column_is_padded_to_the_time_axis(self):
        series = _as_series(block(rain=[0.5, 0.5]))
        self.assertEqual(len(series.rain), len(series.times))

    def test_an_empty_block_does_not_crash(self):
        series = _as_series({})
        self.assertEqual(len(series), 0)


class Lookup(unittest.TestCase):
    def setUp(self):
        self.series = _as_series(block(
            wind=[5, 10, 15, 20, 25, 30],
            rain=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5],
            snow=[0.0, 0.0, 1.0, 2.0, 3.0, 4.0],
        ))

    def test_reads_the_hour_the_truck_arrives(self):
        reading = self.series.at(START + timedelta(hours=3))
        self.assertEqual(reading["wind_mph"], 20)
        self.assertAlmostEqual(reading["rain_in_hr"], 0.3)
        self.assertAlmostEqual(reading["snow_in_hr"], 2.0)

    def test_rounds_to_the_nearest_hour(self):
        """06:50 is nearer 07:00 than 06:00, so it reads hour 1."""
        self.assertEqual(self.series.index_at(START + timedelta(minutes=50)), 1)
        self.assertEqual(self.series.index_at(START + timedelta(minutes=20)), 0)

    def test_times_outside_the_series_clamp_to_its_ends(self):
        self.assertEqual(self.series.index_at(START - timedelta(days=2)), 0)
        self.assertEqual(self.series.index_at(START + timedelta(days=2)), 5)

    def test_window_slices_from_a_start_hour(self):
        window = self.series.window(START + timedelta(hours=2), 3)
        self.assertEqual(window.wind, [15, 20, 25])
        self.assertEqual(window.snow, [1.0, 2.0, 3.0])

    def test_window_does_not_run_past_the_end(self):
        self.assertEqual(len(self.series.window(START + timedelta(hours=4), 10)), 2)


class EndToEndThroughRisk(unittest.TestCase):
    """A wet forecast must reach a risk verdict, not be flattened to Low."""

    def test_rain_drives_the_verdict_with_no_wind_at_all(self):
        series = _as_series(block(wind=[0.0] * 6, rain=[0.0, 0.15, 0.30, 0.60, 1.50, 0.0]))
        verdicts = [
            classify(r["wind_mph"], r["rain_in_hr"], r["snow_in_hr"], 45_000)["overall"]
            for r in (series.at(START + timedelta(hours=i)) for i in range(6))
        ]
        self.assertEqual(verdicts, [
            Risk.LOW, Risk.MODERATE, Risk.HIGH, Risk.SEVERE, Risk.NO_TRAVEL, Risk.LOW,
        ])

    def test_snow_drives_the_verdict_with_no_wind_at_all(self):
        series = _as_series(block(wind=[0.0] * 6, snow=[0.0, 0.7, 1.5, 2.5, 3.5, 0.0]))
        verdicts = [
            classify(r["wind_mph"], r["rain_in_hr"], r["snow_in_hr"], 45_000)["overall"]
            for r in (series.at(START + timedelta(hours=i)) for i in range(6))
        ]
        self.assertEqual(verdicts, [
            Risk.LOW, Risk.MODERATE, Risk.HIGH, Risk.SEVERE, Risk.NO_TRAVEL, Risk.LOW,
        ])

    def test_a_genuinely_calm_forecast_really_is_low(self):
        """The other half of the claim: 0.00 on screen means 0.00 in the data."""
        series = _as_series(block(wind=[4.0] * 6))
        reading = series.at(START)
        self.assertEqual(reading["rain_in_hr"], 0.0)
        self.assertEqual(reading["snow_in_hr"], 0.0)
        self.assertIs(classify(**{
            "wind_mph": reading["wind_mph"],
            "rain_in_hr": reading["rain_in_hr"],
            "snow_in_hr": reading["snow_in_hr"],
            "load_lb": 45_000,
        })["overall"], Risk.LOW)


class Grid(unittest.TestCase):
    def test_nearby_points_share_a_cell(self):
        self.assertEqual(snap(-87.63, 41.88), snap(-87.65, 41.86))

    def test_distant_points_do_not(self):
        self.assertNotEqual(snap(-87.63, 41.88), snap(-87.40, 41.88))

    def test_snapping_is_stable(self):
        self.assertEqual(snap(-104.99, 39.74), snap(-104.99, 39.74))


if __name__ == "__main__":
    unittest.main(verbosity=2)
