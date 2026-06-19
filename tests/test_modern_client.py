"""ModernClient: OGC API request building, parsing, retry, pagination."""
import asyncio
import json
import unittest

from tests import _ha
from custom_components.usgs_streamflow import client as client_mod
from custom_components.usgs_streamflow.client import ModernClient, UsgsHttpStatusError

# Replace asyncio.sleep in the client so 429 backoff doesn't actually wait.
_SLEPT = _ha.patch_no_sleep(client_mod)


def fc(features, links=None):
    return {"type": "FeatureCollection", "features": features, "links": links or []}


def feat(props):
    return {"type": "Feature", "properties": props}


def run(coro):
    return asyncio.run(coro)


SAMPLE_FEATURE = feat({
    "id": "ac615a6a",
    "time_series_id": "137f32ef352b452f82b8cbbc38cad762",
    "monitoring_location_id": "USGS-413413071270400",
    "parameter_code": "00065",
    "statistic_id": "00011",
    "time": "2026-06-11T03:18:00+00:00",
    "value": "-1.02",
    "unit_of_measure": "ft",
    "approval_status": "Provisional",
    "qualifier": None,
})


class TestGetLatestValues(unittest.TestCase):
    def setUp(self):
        self.client = ModernClient(object(), api_key="KEY123")

    def test_parses_sample_feature(self):
        session = _ha.FakeSession([_ha.FakeResp(json_data=fc([SAMPLE_FEATURE]))])
        _ha.set_session(session)
        res = run(self.client.get_latest_values("413413071270400", ["00065"]))
        r = res.readings["00065"]
        self.assertEqual(r.value, -1.02)
        self.assertEqual(r.reading_time.isoformat(), "2026-06-11T03:18:00+00:00")
        self.assertEqual(r.approval_status, "Provisional")
        self.assertIsNone(r.qualifier)
        self.assertEqual(r.statistic_id, "00011")
        self.assertEqual(r.time_series_id, "137f32ef352b452f82b8cbbc38cad762")
        self.assertTrue(res.station_reporting)
        # auth + endpoint
        self.assertEqual(session.calls[-1]["headers"]["X-Api-Key"], "KEY123")
        self.assertTrue(session.calls[-1]["url"].endswith("/collections/latest-continuous/items"))
        self.assertEqual(
            session.calls[-1]["params"]["monitoring_location_id"], "USGS-413413071270400"
        )

    def test_sentinel_nonnumeric_and_filtering(self):
        _ha.set_session(_ha.FakeSession([_ha.FakeResp(json_data=fc([
            feat({"parameter_code": "00065", "value": "-999999", "time": "2026-06-11T03:00:00Z"}),
            feat({"parameter_code": "00060", "value": "ICE", "time": "2026-06-11T03:00:00Z"}),
            feat({"parameter_code": "99999", "value": "5", "time": "2026-06-11T03:00:00Z"}),
        ]))]))
        res = run(self.client.get_latest_values("X", ["00065", "00060"]))
        self.assertIsNone(res.readings["00065"].value)
        self.assertIsNone(res.readings["00060"].value)
        self.assertNotIn("99999", res.readings)

    def test_most_recent_series_wins(self):
        _ha.set_session(_ha.FakeSession([_ha.FakeResp(json_data=fc([
            feat({"parameter_code": "00065", "value": "1.0", "time": "2026-06-11T01:00:00Z"}),
            feat({"parameter_code": "00065", "value": "2.0", "time": "2026-06-11T05:00:00Z"}),
            feat({"parameter_code": "00065", "value": "1.5", "time": "2026-06-11T03:00:00Z"}),
        ]))]))
        res = run(self.client.get_latest_values("X", ["00065"]))
        self.assertEqual(res.readings["00065"].value, 2.0)

    def test_empty_collection_not_reporting(self):
        _ha.set_session(_ha.FakeSession([_ha.FakeResp(json_data=fc([]))]))
        res = run(self.client.get_latest_values("X", ["00065"]))
        self.assertFalse(res.station_reporting)
        self.assertEqual(res.readings, {})

    def test_pagination_follows_next(self):
        session = _ha.FakeSession([
            _ha.FakeResp(json_data=fc(
                [feat({"parameter_code": "00065", "value": "1", "time": "2026-06-11T01:00:00Z"})],
                links=[{"rel": "next", "href": "https://api.x/next-page"}],
            )),
            _ha.FakeResp(json_data=fc(
                [feat({"parameter_code": "00060", "value": "2", "time": "2026-06-11T01:00:00Z"})]
            )),
        ])
        _ha.set_session(session)
        res = run(self.client.get_latest_values("X", ["00065", "00060"]))
        self.assertEqual(set(res.readings), {"00065", "00060"})
        self.assertEqual(session.calls[1]["url"], "https://api.x/next-page")


class TestGetSiteParameters(unittest.TestCase):
    def test_points_only_intersect_supported(self):
        _ha.set_session(_ha.FakeSession([_ha.FakeResp(json_data=fc([
            feat({"parameter_code": "00060", "computation_period_identifier": "Points"}),
            feat({"parameter_code": "00065", "computation_period_identifier": "Points"}),
            feat({"parameter_code": "00010", "computation_period_identifier": "Daily"}),
            feat({"parameter_code": "99999", "computation_period_identifier": "Points"}),
        ]))]))
        params = run(ModernClient(object(), api_key="K").get_site_parameters("X"))
        self.assertEqual(params, {"00060", "00065"})


class TestSearchSites(unittest.TestCase):
    def test_name_search_builds_cql_like_and_agency(self):
        session = _ha.FakeSession([_ha.FakeResp(json_data=fc([
            feat({"monitoring_location_id": "USGS-01460595",
                  "monitoring_location_name": "DELAWARE CANAL", "site_type": "ST"}),
        ]))])
        _ha.set_session(session)
        hits = run(ModernClient(object(), api_key="K").search_sites("delaware"))
        body = json.loads(session.calls[-1]["data"])
        self.assertEqual(session.calls[-1]["method"], "POST")
        self.assertEqual(body["op"], "and")
        self.assertEqual(body["args"][0]["op"], "like")
        self.assertEqual(body["args"][0]["args"][1], "%DELAWARE%")
        self.assertEqual(body["args"][1]["args"][1], "USGS")
        self.assertEqual(
            session.calls[-1]["headers"]["Content-Type"], "application/query-cql-json"
        )
        self.assertEqual(hits[0].site_id, "01460595")
        self.assertEqual(hits[0].site_type, "ST")

    def test_site_number_uses_equality(self):
        session = _ha.FakeSession([_ha.FakeResp(json_data=fc([
            feat({"monitoring_location_number": "01460595", "monitoring_location_name": "X"}),
        ]))])
        _ha.set_session(session)
        run(ModernClient(object(), api_key="K").search_sites("USGS-01460595"))
        body = json.loads(session.calls[-1]["data"])
        self.assertEqual(body["op"], "=")
        self.assertEqual(body["args"][1], "01460595")


class TestRateLimitBackoff(unittest.TestCase):
    def setUp(self):
        self.client = ModernClient(object(), api_key="K")
        _SLEPT.clear()

    def test_retries_on_429_then_succeeds(self):
        _ha.set_session(_ha.FakeSession([
            _ha.FakeResp(status=429, headers={"Retry-After": "0"}),
            _ha.FakeResp(json_data=fc([])),
        ]))
        res = run(self.client.get_latest_values("X", ["00065"]))
        self.assertFalse(res.station_reporting)
        self.assertEqual(len(_SLEPT), 1)

    def test_exhausts_retries_and_raises(self):
        _ha.set_session(_ha.FakeSession([_ha.FakeResp(status=429) for _ in range(6)]))
        with self.assertRaises(UsgsHttpStatusError) as ctx:
            run(self.client.get_latest_values("X", ["00065"]))
        self.assertEqual(ctx.exception.status, 429)


class TestAuth(unittest.TestCase):
    def test_demo_key_fallback(self):
        session = _ha.FakeSession([_ha.FakeResp(json_data=fc([]))])
        _ha.set_session(session)
        run(ModernClient(object(), api_key=None).get_latest_values("X", ["00065"]))
        self.assertEqual(session.calls[-1]["headers"]["X-Api-Key"], "DEMO_KEY")


if __name__ == "__main__":
    unittest.main()
