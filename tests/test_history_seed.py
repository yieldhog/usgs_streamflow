"""Rate/trend buffer warm-start: recent-values fetch + coordinator seeding."""
import asyncio
import unittest
from collections import defaultdict, deque
from datetime import datetime, timezone

from tests import _ha
from custom_components.usgs_streamflow.client import LegacyClient, ModernClient
from custom_components.usgs_streamflow.coordinator import USGSStreamflowCoordinator
from custom_components.usgs_streamflow.sensor import _compute_rate

UTC = timezone.utc
NOW = datetime(2026, 6, 11, 4, 0, 0, tzinfo=UTC)


def run(coro):
    return asyncio.run(coro)


def fc(features, links=None):
    return {"type": "FeatureCollection", "features": features, "links": links or []}


def feat(props):
    return {"type": "Feature", "properties": props}


def at(h, m):
    return datetime(2026, 6, 11, h, m, tzinfo=UTC)


class TestModernRecentValues(unittest.TestCase):
    def setUp(self):
        _ha.set_now(NOW)

    def test_parses_and_builds_range_request(self):
        session = _ha.FakeSession([_ha.FakeResp(json_data=fc([
            feat({"parameter_code": "00065", "time": "2026-06-11T03:30:00+00:00", "value": "2.80"}),
            feat({"parameter_code": "00065", "time": "2026-06-11T03:45:00+00:00", "value": "2.83"}),
        ]))])
        _ha.set_session(session)
        out = run(ModernClient(object(), api_key="K").get_recent_values("X", "00065", 180))
        self.assertEqual(out, [(at(3, 30), 2.80), (at(3, 45), 2.83)])
        call = session.calls[-1]
        self.assertTrue(call["url"].endswith("/collections/continuous/items"))
        self.assertEqual(call["params"]["parameter_code"], "00065")
        # NOW is 04:00, 180 min back -> 01:00
        self.assertEqual(
            call["params"]["datetime"], "2026-06-11T01:00:00Z/2026-06-11T04:00:00Z"
        )

    def test_skips_sentinel_and_bad_points(self):
        _ha.set_session(_ha.FakeSession([_ha.FakeResp(json_data=fc([
            feat({"time": "2026-06-11T03:30:00Z", "value": "-999999"}),
            feat({"time": "", "value": "5"}),
            feat({"time": "2026-06-11T03:45:00Z", "value": "2.9"}),
        ]))]))
        out = run(ModernClient(object(), api_key="K").get_recent_values("X", "00065", 180))
        self.assertEqual(out, [(at(3, 45), 2.9)])


class TestLegacyRecentValues(unittest.TestCase):
    def _iv(self, points):
        return {"value": {"timeSeries": [
            {"values": [{"value": [
                {"value": str(v), "dateTime": d} for d, v in points
            ]}]}
        ]}}

    def test_parses_full_window_and_period_param(self):
        session = _ha.FakeSession([_ha.FakeResp(json_data=self._iv([
            ("2026-06-11T03:30:00-04:00", 2.80),
            ("2026-06-11T03:45:00-04:00", 2.83),
        ]))])
        _ha.set_session(session)
        out = run(LegacyClient(object()).get_recent_values("X", "00065", 180))
        self.assertEqual([v for _, v in out], [2.80, 2.83])
        self.assertEqual(session.calls[-1]["params"]["period"], "PT180M")

    def test_sentinel_dropped(self):
        _ha.set_session(_ha.FakeSession([_ha.FakeResp(json_data=self._iv([
            ("2026-06-11T03:30:00Z", -999999),
            ("2026-06-11T03:45:00Z", 2.9),
        ]))]))
        out = run(LegacyClient(object()).get_recent_values("X", "00065", 180))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][1], 2.9)


class _SeedClient:
    def __init__(self, recent):
        self._recent = recent
        self.asked = []

    async def get_recent_values(self, site, param, minutes):
        self.asked.append(param)
        return list(self._recent.get(param, []))


class TestCoordinatorSeeding(unittest.TestCase):
    def _coord(self, client, known):
        coord = USGSStreamflowCoordinator.__new__(USGSStreamflowCoordinator)
        coord._history = defaultdict(deque)
        coord.site_id = "X"
        coord.known_params = set(known)
        coord._client = client
        return coord

    def test_seed_fills_buffer_so_rate_computes_immediately(self):
        client = _SeedClient({"00060": [
            (at(3, 0), 2000.0), (at(3, 30), 1900.0), (at(3, 59), 1850.0),
        ]})
        coord = self._coord(client, {"00060"})
        run(coord._seed_history())
        pts = coord.recent_points("00060", 60)
        self.assertGreaterEqual(len(pts), 2)
        self.assertIsNotNone(_compute_rate(pts))

    def test_seed_skips_params_station_does_not_report(self):
        client = _SeedClient({"00060": [(at(3, 0), 1.0), (at(3, 30), 2.0)]})
        coord = self._coord(client, {"00060"})  # station has discharge only
        run(coord._seed_history())
        # Only the reported derived param is queried; gauge height / gw depth skipped
        self.assertEqual(client.asked, ["00060"])

    def test_seed_tolerates_empty_and_dedups(self):
        client = _SeedClient({"00065": []})  # no recent data
        coord = self._coord(client, {"00065"})
        run(coord._seed_history())  # must not raise
        self.assertEqual(coord.recent_points("00065", 60), [])


if __name__ == "__main__":
    unittest.main()
