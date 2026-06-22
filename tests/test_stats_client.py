"""get_daily_means: daily-history fetch + parsing for both backends."""
import asyncio
import unittest
from datetime import date

from tests import _ha
from custom_components.usgs_streamflow.client import LegacyClient, ModernClient


def run(coro):
    return asyncio.run(coro)


def fc(features, links=None):
    return {"type": "FeatureCollection", "features": features, "links": links or []}


def feat(props):
    return {"type": "Feature", "properties": props}


class TestModernDailyMeans(unittest.TestCase):
    def test_parses_and_builds_request(self):
        session = _ha.FakeSession([_ha.FakeResp(json_data=fc([
            feat({"parameter_code": "00060", "statistic_id": "00003",
                  "time": "1995-06-19", "value": "16400"}),
            feat({"parameter_code": "00060", "statistic_id": "00003",
                  "time": "1995-06-20", "value": "15500"}),
        ]))])
        _ha.set_session(session)
        out = run(ModernClient(object(), api_key="K").get_daily_means(
            "14211720", "00060", "00003", "1995-01-01", "2025-12-31"))
        self.assertEqual(out, [(date(1995, 6, 19), 16400.0), (date(1995, 6, 20), 15500.0)])
        call = session.calls[-1]
        self.assertTrue(call["url"].endswith("/collections/daily/items"))
        self.assertEqual(call["params"]["monitoring_location_id"], "USGS-14211720")
        self.assertEqual(call["params"]["statistic_id"], "00003")
        self.assertEqual(call["params"]["datetime"], "1995-01-01/2025-12-31")

    def test_skips_sentinel_and_bad_dates(self):
        _ha.set_session(_ha.FakeSession([_ha.FakeResp(json_data=fc([
            feat({"time": "2000-01-01", "value": "-999999"}),
            feat({"time": "", "value": "5"}),
            feat({"time": "2000-01-03", "value": "ICE"}),
            feat({"time": "2000-01-04", "value": "12"}),
        ]))]))
        out = run(ModernClient(object(), api_key="K").get_daily_means(
            "X", "00060", "00003", "2000-01-01", "2000-12-31"))
        self.assertEqual(out, [(date(2000, 1, 4), 12.0)])

    def test_follows_pagination(self):
        session = _ha.FakeSession([
            _ha.FakeResp(json_data=fc(
                [feat({"time": "2000-01-01", "value": "1"})],
                links=[{"rel": "next", "href": "https://api.x/p2"}])),
            _ha.FakeResp(json_data=fc([feat({"time": "2000-01-02", "value": "2"})])),
        ])
        _ha.set_session(session)
        out = run(ModernClient(object(), api_key="K").get_daily_means(
            "X", "00060", "00003", "2000-01-01", "2000-12-31"))
        self.assertEqual(len(out), 2)
        self.assertEqual(session.calls[1]["url"], "https://api.x/p2")


class TestLegacyDailyMeans(unittest.TestCase):
    def _dv(self, values):
        return {"value": {"timeSeries": [
            {"values": [{"value": [
                {"value": str(v), "dateTime": d} for d, v in values
            ]}]}
        ]}}

    def test_parses_dv_response(self):
        session = _ha.FakeSession([_ha.FakeResp(json_data=self._dv([
            ("1995-06-19", 16400), ("1995-06-20", 15500),
        ]))])
        _ha.set_session(session)
        out = run(LegacyClient(object()).get_daily_means(
            "14211720", "00060", "00003", "1995-01-01", "2025-12-31"))
        self.assertEqual(out, [(date(1995, 6, 19), 16400.0), (date(1995, 6, 20), 15500.0)])
        call = session.calls[-1]
        self.assertEqual(call["params"]["statCd"], "00003")
        self.assertEqual(call["params"]["startDT"], "1995-01-01")

    def test_404_is_empty(self):
        _ha.set_session(_ha.FakeSession([_ha.FakeResp(status=404)]))
        out = run(LegacyClient(object()).get_daily_means(
            "X", "00060", "00003", "1995-01-01", "2025-12-31"))
        self.assertEqual(out, [])

    def test_skips_sentinel(self):
        _ha.set_session(_ha.FakeSession([_ha.FakeResp(json_data=self._dv([
            ("2000-01-01", -999999), ("2000-01-02", 12),
        ]))]))
        out = run(LegacyClient(object()).get_daily_means(
            "X", "00060", "00003", "2000-01-01", "2000-12-31"))
        self.assertEqual(out, [(date(2000, 1, 2), 12.0)])


if __name__ == "__main__":
    unittest.main()
