"""Stats coordinator: envelope build/refresh gating, caching, and computation."""
import asyncio
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from tests import _ha
from custom_components.usgs_streamflow import streamflow_stats as st
from custom_components.usgs_streamflow.const import StatsParamConfig
from custom_components.usgs_streamflow.stats_coordinator import (
    USGSStatsCoordinator,
    _is_stale,
)

NOW = datetime(2026, 6, 11, 4, 0, 0, tzinfo=timezone.utc)


def run(coro):
    return asyncio.run(coro)


class FakeClient:
    def __init__(self, records):
        self._records = records
        self.calls = []

    async def get_daily_means(self, site, param, stat, start, end):
        self.calls.append((site, param, stat, start, end))
        return list(self._records)


def fake_source(values, times):
    return SimpleNamespace(
        site_id="X", site_name="Test Gauge",
        data=SimpleNamespace(values=values, reading_times=times),
    )


def thirty_years(month=6, day=11, base=100.0, step=100.0):
    return [
        (date(y, month, day), base + (y - 1991) * step)
        for y in range(1991, 2022)
    ]


class TestStaleness(unittest.TestCase):
    def test_missing_built_is_stale(self):
        env = st.Envelope("00060", "00003", 0, 30, None, None, None, {})
        self.assertTrue(_is_stale(env, NOW))

    def test_recent_is_fresh(self):
        env = st.Envelope("00060", "00003", 0, 30, None, None,
                          (NOW - timedelta(days=2)).isoformat(), {})
        self.assertFalse(_is_stale(env, NOW))

    def test_old_is_stale(self):
        env = st.Envelope("00060", "00003", 0, 30, None, None,
                          (NOW - timedelta(days=99)).isoformat(), {})
        self.assertTrue(_is_stale(env, NOW))

    def test_unparseable_built_is_stale(self):
        env = st.Envelope("00060", "00003", 0, 30, None, None, "garbage", {})
        self.assertTrue(_is_stale(env, NOW))


class TestEnsureAndCompute(unittest.TestCase):
    def setUp(self):
        _ha.set_now(NOW)

    def _coord(self, client, source, params):
        return USGSStatsCoordinator(object(), source, client, params)

    def test_builds_then_computes_discharge(self):
        client = FakeClient(thirty_years())
        source = fake_source(
            {"00060": 1600.0},  # the historical median
            {"00060": st_dt("2026-06-11")},
        )
        coord = self._coord(client, source, {"00060": StatsParamConfig(invert=False)})
        run(coord._ensure_envelopes())
        self.assertEqual(len(client.calls), 1)
        res = coord._compute()
        self.assertIn("00060", res)
        self.assertEqual(res["00060"].condition, st.CONDITION_NORMAL)

    def test_inverted_depth_below_normal(self):
        # Depth records 100..3100; a deep (high) reading -> below normal.
        client = FakeClient(thirty_years())
        source = fake_source(
            {"72019": 3050.0},
            {"72019": st_dt("2026-06-11")},
        )
        coord = self._coord(client, source, {"72019": StatsParamConfig(invert=True)})
        run(coord._ensure_envelopes())
        res = coord._compute()
        self.assertIn("72019", res)
        self.assertEqual(res["72019"].condition, st.CONDITION_MUCH_BELOW)
        self.assertTrue(res["72019"].inverted)

    def test_fresh_envelope_not_refetched(self):
        client = FakeClient(thirty_years())
        source = fake_source({"00060": 1600.0}, {"00060": st_dt("2026-06-11")})
        coord = self._coord(client, source, {"00060": StatsParamConfig(invert=False)})
        run(coord._ensure_envelopes())
        run(coord._ensure_envelopes())  # second pass: envelope still fresh
        self.assertEqual(len(client.calls), 1)

    def test_cache_round_trips_via_store(self):
        client = FakeClient(thirty_years())
        source = fake_source({"00060": 1600.0}, {"00060": st_dt("2026-06-11")})
        coord = self._coord(client, source, {"00060": StatsParamConfig(invert=False)})
        run(coord._ensure_envelopes())
        # A new coordinator sharing the same backing store reuses the cache and
        # does not hit the client at all.
        coord2 = self._coord(FakeClient([]), source, {"00060": StatsParamConfig(invert=False)})
        coord2._store = coord._store
        run(coord2._ensure_envelopes())
        self.assertEqual(len(coord2._client.calls), 0)
        self.assertIn("00060", coord2.envelopes)

    def test_no_value_yields_no_result(self):
        client = FakeClient(thirty_years())
        source = fake_source({"00060": None}, {"00060": st_dt("2026-06-11")})
        coord = self._coord(client, source, {"00060": StatsParamConfig(invert=False)})
        run(coord._ensure_envelopes())
        self.assertEqual(coord._compute(), {})

    def test_load_cache_ignores_corrupt_entry(self):
        source = fake_source({"00060": 1600.0}, {"00060": st_dt("2026-06-11")})
        coord = self._coord(FakeClient([]), source,
                            {"00060": StatsParamConfig(invert=False)})

        class Store:
            async def async_load(self):
                return {"envelopes": {"00060": {"bad": "shape"}}}

        coord._store = Store()
        run(coord._load_cache())  # from_dict raises -> entry dropped, no crash
        self.assertNotIn("00060", coord.envelopes)

    def test_load_cache_no_data(self):
        source = fake_source({}, {})
        coord = self._coord(FakeClient([]), source,
                            {"00060": StatsParamConfig(invert=False)})

        class Store:
            async def async_load(self):
                return None

        coord._store = Store()
        run(coord._load_cache())
        self.assertEqual(coord.envelopes, {})

    def test_save_cache_failure_is_non_fatal(self):
        client = FakeClient(thirty_years())
        source = fake_source({"00060": 1600.0}, {"00060": st_dt("2026-06-11")})
        coord = self._coord(client, source, {"00060": StatsParamConfig(invert=False)})

        class BoomStore:
            async def async_load(self):
                return None
            async def async_save(self, data):
                raise OSError("disk full")
        coord._store = BoomStore()
        run(coord._ensure_envelopes())  # must not raise despite save failure
        # Envelope is still available in memory, so stats still compute.
        self.assertIn("00060", coord.envelopes)
        self.assertIn("00060", coord._compute())

    def test_fetch_failure_leaves_no_envelope(self):
        class Boom:
            calls = []
            async def get_daily_means(self, *a):
                from custom_components.usgs_streamflow.client import (
                    UsgsCommunicationError,
                )
                raise UsgsCommunicationError("down")
        source = fake_source({"00060": 1600.0}, {"00060": st_dt("2026-06-11")})
        coord = self._coord(Boom(), source, {"00060": StatsParamConfig(invert=False)})
        run(coord._ensure_envelopes())  # must not raise
        self.assertEqual(coord._compute(), {})

    def test_persistent_failure_warns_once(self):
        from custom_components.usgs_streamflow.client import UsgsCommunicationError

        class Boom:
            def __init__(self):
                self.calls = 0
            async def get_daily_means(self, *a):
                self.calls += 1
                raise UsgsCommunicationError("down")

        source = fake_source({"00060": 1600.0}, {"00060": st_dt("2026-06-11")})
        coord = self._coord(Boom(), source, {"00060": StatsParamConfig(invert=False)})
        logger = "custom_components.usgs_streamflow.stats_coordinator"
        with self.assertLogs(logger, level="WARNING") as first:
            run(coord._rebuild("00060"))
        self.assertEqual(len(first.records), 1)
        self.assertIn("00060", coord._warned)
        # A second failing rebuild does not re-warn (assertLogs would fail if no
        # record is emitted, so assert on the guard + a fresh logger capture).
        import logging
        with self.assertLogs(logger, level="DEBUG") as second:
            logging.getLogger(logger).debug("probe")  # keep the context non-empty
            run(coord._rebuild("00060"))
        self.assertEqual(
            [r for r in second.records if r.levelno >= logging.WARNING], []
        )

    def test_success_after_failure_rewarns(self):
        from custom_components.usgs_streamflow.client import UsgsCommunicationError

        class Flaky:
            def __init__(self, records):
                self._records = records
                self.fail = True
            async def get_daily_means(self, *a):
                if self.fail:
                    raise UsgsCommunicationError("down")
                return list(self._records)

        client = Flaky(thirty_years())
        source = fake_source({"00060": 1600.0}, {"00060": st_dt("2026-06-11")})
        coord = self._coord(client, source, {"00060": StatsParamConfig(invert=False)})
        run(coord._rebuild("00060"))              # fails -> warned
        self.assertIn("00060", coord._warned)
        client.fail = False
        run(coord._rebuild("00060"))              # succeeds -> guard cleared
        self.assertNotIn("00060", coord._warned)
        self.assertIn("00060", coord.envelopes)


class TestNoDailyRecord(unittest.TestCase):
    """A station with no daily-mean series for a param (e.g. USGS 06711570 has
    instantaneous gauge height but no 00065 daily record): the empty answer is
    remembered instead of being refetched and re-warned every check."""

    LOGGER = "custom_components.usgs_streamflow.stats_coordinator"

    def setUp(self):
        _ha.set_now(NOW)

    def _coord(self, client, params=("00065",)):
        source = fake_source({p: 3.2 for p in params},
                             {p: st_dt("2026-06-11") for p in params})
        cfg = {p: StatsParamConfig(invert=False) for p in params}
        return USGSStatsCoordinator(object(), source, client, cfg)

    def test_empty_record_marked_logged_at_info_not_warning(self):
        import logging
        client = FakeClient([])
        coord = self._coord(client)
        with self.assertLogs(self.LOGGER, level="INFO") as logs:
            run(coord._ensure_envelopes())
        self.assertEqual(len(client.calls), 1)
        self.assertIn("00065", coord.no_record)
        self.assertNotIn("00065", coord.envelopes)
        self.assertNotIn("00065", coord._warned)
        levels = [r.levelno for r in logs.records]
        self.assertIn(logging.INFO, levels)
        self.assertNotIn(logging.WARNING, levels)

    def test_marked_param_not_refetched_within_window(self):
        client = FakeClient([])
        coord = self._coord(client)
        run(coord._ensure_envelopes())
        _ha.set_now(NOW + timedelta(days=29))
        run(coord._ensure_envelopes())   # 12-hour checks inside the window
        run(coord._ensure_envelopes())
        self.assertEqual(len(client.calls), 1)

    def test_marked_param_rechecked_after_window(self):
        client = FakeClient([])
        coord = self._coord(client)
        run(coord._ensure_envelopes())
        first_mark = coord.no_record["00065"]
        _ha.set_now(NOW + timedelta(days=31))
        import logging
        with self.assertLogs(self.LOGGER, level="DEBUG") as logs:
            logging.getLogger(self.LOGGER).debug("probe")
            run(coord._ensure_envelopes())
        self.assertEqual(len(client.calls), 2)
        # Re-confirmed: mark refreshed, but the info line is not repeated.
        self.assertNotEqual(coord.no_record["00065"], first_mark)
        self.assertEqual(
            [r for r in logs.records if r.levelno >= logging.INFO], []
        )

    def test_mark_cleared_when_records_appear(self):
        client = FakeClient([])
        coord = self._coord(client)
        run(coord._ensure_envelopes())
        self.assertIn("00065", coord.no_record)
        client._records = thirty_years()
        _ha.set_now(NOW + timedelta(days=31))
        run(coord._ensure_envelopes())
        self.assertNotIn("00065", coord.no_record)
        self.assertIn("00065", coord.envelopes)

    def test_mark_survives_restart_via_store(self):
        coord = self._coord(FakeClient([]))
        run(coord._ensure_envelopes())
        # Fresh coordinator (an HA restart) sharing the persisted store.
        client2 = FakeClient([])
        coord2 = self._coord(client2)
        coord2._store = coord._store
        run(coord2._ensure_envelopes())
        self.assertEqual(client2.calls, [])
        self.assertIn("00065", coord2.no_record)

    def test_only_unmarked_params_fetched(self):
        # Discharge has a daily record, gauge height does not (Harvard Gulch).
        class PerParam:
            def __init__(self):
                self.calls = []
            async def get_daily_means(self, site, param, *a):
                self.calls.append(param)
                return thirty_years() if param == "00060" else []

        client = PerParam()
        coord = self._coord(client, params=("00060", "00065"))
        run(coord._ensure_envelopes())
        self.assertIn("00060", coord.envelopes)
        self.assertEqual(set(coord.no_record), {"00065"})
        _ha.set_now(NOW + timedelta(days=1))
        run(coord._ensure_envelopes())   # both fresh/marked -> no traffic
        self.assertEqual(sorted(client.calls), ["00060", "00065"])

    def test_fetch_error_is_not_a_no_record(self):
        from custom_components.usgs_streamflow.client import UsgsCommunicationError

        class Boom:
            async def get_daily_means(self, *a):
                raise UsgsCommunicationError("down")

        coord = self._coord(Boom())
        with self.assertLogs(self.LOGGER, level="WARNING"):
            run(coord._ensure_envelopes())
        self.assertEqual(coord.no_record, {})
        self.assertIn("00065", coord._warned)

    def test_thin_record_still_warns(self):
        # Records exist but too few per day for an envelope: unchanged behavior.
        client = FakeClient([(date(2020, 6, 11), 1.0)])
        coord = self._coord(client)
        with self.assertLogs(self.LOGGER, level="WARNING") as logs:
            run(coord._ensure_envelopes())
        self.assertIn("No usable daily history", logs.output[0])
        self.assertEqual(coord.no_record, {})

    def test_load_cache_tolerates_bad_no_record_shapes(self):
        coord = self._coord(FakeClient([]), params=("00060", "00065"))

        class Store:
            def __init__(self, data):
                self.data = data
            async def async_load(self):
                return self.data

        coord._store = Store({"envelopes": {}, "no_record": ["not", "a", "dict"]})
        run(coord._load_cache())
        self.assertEqual(coord.no_record, {})
        coord._store = Store({"no_record": {"00065": 12, "99999": "x",
                                            "00060": "2026-06-01T00:00:00+00:00"}})
        run(coord._load_cache())
        self.assertEqual(coord.no_record, {"00060": "2026-06-01T00:00:00+00:00"})

    def test_unparseable_mark_is_rechecked(self):
        client = FakeClient([])
        coord = self._coord(client)
        coord._cache_loaded = True
        coord.no_record["00065"] = "garbage"
        run(coord._ensure_envelopes())
        self.assertEqual(len(client.calls), 1)


def st_dt(d):
    """A tz-aware datetime at midnight for an ISO date (for reading_times)."""
    return datetime.fromisoformat(d).replace(tzinfo=timezone.utc)


if __name__ == "__main__":
    unittest.main()
