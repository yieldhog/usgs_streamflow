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


def st_dt(d):
    """A tz-aware datetime at midnight for an ISO date (for reading_times)."""
    return datetime.fromisoformat(d).replace(tzinfo=timezone.utc)


if __name__ == "__main__":
    unittest.main()
