"""Percent-of-normal / condition logic: envelope build, percentile, classify."""
import unittest
from datetime import date, datetime

from custom_components.usgs_streamflow import streamflow_stats as st


def make_records(values_by_year, month=6, day=19):
    """One (date, value) per year for a fixed calendar day."""
    return [(date(y, month, day), v) for y, v in values_by_year.items()]


class TestDayKey(unittest.TestCase):
    def test_zero_padded(self):
        self.assertEqual(st.day_key(date(2020, 6, 9)), "06-09")

    def test_leap_day_folds_to_feb_28(self):
        self.assertEqual(st.day_key(date(2020, 2, 29)), "02-28")


class TestPercentileOf(unittest.TestCase):
    def setUp(self):
        # anchors spanning 0..100 (percentile -> value), monotonic
        self.anchors = {0: 0.0, 5: 10.0, 10: 20.0, 25: 30.0, 50: 50.0,
                        75: 70.0, 90: 90.0, 95: 95.0, 100: 100.0}

    def test_below_min_clamps_to_zero(self):
        self.assertEqual(st.percentile_of(self.anchors, -5), 0.0)

    def test_above_max_clamps_to_hundred(self):
        self.assertEqual(st.percentile_of(self.anchors, 200), 100.0)

    def test_exact_anchor(self):
        self.assertEqual(st.percentile_of(self.anchors, 50.0), 50.0)

    def test_interpolates_between_anchors(self):
        # halfway between value 30 (p25) and 50 (p50) -> p37.5
        self.assertAlmostEqual(st.percentile_of(self.anchors, 40.0), 37.5)

    def test_flat_region_resolves_to_upper(self):
        anchors = {0: 0.0, 10: 0.0, 25: 0.0, 50: 5.0, 75: 8.0, 90: 9.0, 100: 10.0}
        # value 0 sits across a flat run; clamps to record min -> 0
        self.assertEqual(st.percentile_of(anchors, 0.0), 0.0)


class TestClassify(unittest.TestCase):
    def test_streamflow_bands(self):
        self.assertEqual(st.classify(5)[0], st.CONDITION_MUCH_BELOW)
        self.assertEqual(st.classify(15)[0], st.CONDITION_BELOW)
        self.assertEqual(st.classify(50)[0], st.CONDITION_NORMAL)
        self.assertEqual(st.classify(85)[0], st.CONDITION_ABOVE)
        self.assertEqual(st.classify(95)[0], st.CONDITION_MUCH_ABOVE)

    def test_boundaries(self):
        # breaks at 10, 25, 75, 90
        self.assertEqual(st.classify(10)[0], st.CONDITION_BELOW)
        self.assertEqual(st.classify(25)[0], st.CONDITION_NORMAL)
        self.assertEqual(st.classify(75)[0], st.CONDITION_NORMAL)
        self.assertEqual(st.classify(90)[0], st.CONDITION_ABOVE)

    def test_invert_complements_percentile_and_condition(self):
        cond, reported = st.classify(97.6, invert=True)
        self.assertAlmostEqual(reported, 2.4, places=1)
        self.assertEqual(cond, st.CONDITION_MUCH_BELOW)


class TestBuildEnvelope(unittest.TestCase):
    def test_empty_records_returns_none(self):
        self.assertIsNone(
            st.build_envelope([], param_cd="00060", statistic_id="00003")
        )

    def test_drops_days_below_min_samples(self):
        recs = make_records({y: y for y in range(2000, 2005)})  # only 5 samples
        env = st.build_envelope(
            recs, param_cd="00060", statistic_id="00003", min_samples=10
        )
        self.assertIsNone(env)

    def test_builds_day_and_metadata(self):
        vals = {y: float((y - 1990) * 100) for y in range(1991, 2022)}  # 31 yrs
        recs = make_records(vals)
        env = st.build_envelope(
            recs, param_cd="00060", statistic_id="00003",
            min_samples=10, built=datetime(2026, 6, 20),
        )
        self.assertIsNotNone(env)
        self.assertEqual(env.years, 31)
        self.assertIn("06-19", env.days)
        self.assertEqual(env.record_start, "1991-06-19")
        self.assertEqual(env.record_end, "2021-06-19")
        ds = env.days["06-19"]
        self.assertEqual(ds.n, 31)
        # median of 100..3100 step 100 is 1600
        self.assertAlmostEqual(ds.anchors[50], 1600.0)

    def test_window_widens_buckets(self):
        # one value per day across a month, 1 year only -> exact-day buckets are
        # size 1, but a +/-7 window lifts each near-center day above min_samples.
        recs = [(date(2020, 6, d), float(d)) for d in range(1, 29)]
        env = st.build_envelope(
            recs, param_cd="00060", statistic_id="00003",
            window_days=7, min_samples=10,
        )
        self.assertIsNotNone(env)
        self.assertIn("06-15", env.days)
        self.assertGreaterEqual(env.days["06-15"].n, 10)


class TestEvaluate(unittest.TestCase):
    def setUp(self):
        vals = {y: float((y - 1990) * 100) for y in range(1991, 2022)}  # 100..3100
        self.env = st.build_envelope(
            make_records(vals), param_cd="00060", statistic_id="00003",
            min_samples=10, built=datetime(2026, 6, 20),
        )

    def test_none_value(self):
        self.assertIsNone(self.env.evaluate(date(2026, 6, 19), None))

    def test_uncovered_day_returns_none(self):
        self.assertIsNone(self.env.evaluate(date(2026, 1, 1), 1000.0))

    def test_normal_reading(self):
        res = self.env.evaluate(date(2026, 6, 19), 1600.0)  # the median
        self.assertEqual(res.condition, st.CONDITION_NORMAL)
        self.assertAlmostEqual(res.percentile, 50.0, places=0)
        self.assertEqual(res.percent_of_normal, 100.0)
        self.assertEqual(res.median, 1600.0)
        self.assertFalse(res.inverted)

    def test_inverted_deep_reading_is_below_normal(self):
        # A high raw value with invert -> low reported percentile / below normal.
        res = self.env.evaluate(date(2026, 6, 19), 3050.0, invert=True)
        self.assertTrue(res.inverted)
        self.assertLess(res.percentile, 10)
        self.assertEqual(res.condition, st.CONDITION_MUCH_BELOW)
        # % of normal still reported as value / median
        self.assertGreater(res.percent_of_normal, 100)


class TestSerialization(unittest.TestCase):
    def test_round_trip(self):
        vals = {y: float((y - 1990) * 100) for y in range(1991, 2022)}
        env = st.build_envelope(
            make_records(vals), param_cd="00060", statistic_id="00003",
            min_samples=10, built=datetime(2026, 6, 20),
        )
        restored = st.Envelope.from_dict(env.to_dict())
        self.assertEqual(restored.param_cd, env.param_cd)
        self.assertEqual(restored.years, env.years)
        self.assertEqual(restored.built, env.built)
        self.assertEqual(
            restored.days["06-19"].anchors, env.days["06-19"].anchors
        )
        # evaluation matches after a round trip
        a = env.evaluate(date(2026, 6, 19), 1600.0)
        b = restored.evaluate(date(2026, 6, 19), 1600.0)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
