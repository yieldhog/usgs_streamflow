"""Client edge/error branches: parse helpers + HTTP failure paths (both backends)."""
import asyncio
import unittest
from datetime import timezone

import types

from tests import _ha
from custom_components.usgs_streamflow.client import (
    LegacyClient,
    ModernClient,
    UsgsCommunicationError,
    UsgsHttpStatusError,
    UsgsResponseFormatError,
    _is_newer,
    _parse_daily_values,
    _parse_instantaneous_series,
    _parse_iso_date,
    _parse_iso_datetime,
    _retry_after_seconds,
    _value_to_float,
)


def fc(features, links=None):
    return {"type": "FeatureCollection", "features": features, "links": links or []}


def run(coro):
    return asyncio.run(coro)


class RaisingSession:
    """A session whose calls raise a transport error synchronously."""

    def request(self, *a, **k):
        raise ConnectionError("boom")

    def get(self, *a, **k):
        raise ConnectionError("boom")


class TestParseHelpers(unittest.TestCase):
    def test_iso_datetime_naive_becomes_utc(self):
        parsed = _parse_iso_datetime("2026-06-01T12:00:00")
        self.assertEqual(parsed.tzinfo, timezone.utc)

    def test_iso_datetime_malformed_is_none(self):
        self.assertIsNone(_parse_iso_datetime("not-a-date"))

    def test_iso_datetime_empty_is_none(self):
        self.assertIsNone(_parse_iso_datetime(""))

    def test_iso_date_malformed_is_none(self):
        self.assertIsNone(_parse_iso_date("nope"))

    def test_value_to_float_sentinel_and_bad(self):
        self.assertIsNone(_value_to_float(-999999))
        self.assertIsNone(_value_to_float("ICE"))
        self.assertIsNone(_value_to_float(None))
        self.assertEqual(_value_to_float("3.5"), 3.5)


class TestLegacyEdges(unittest.TestCase):
    def test_rdb_too_few_lines(self):
        self.assertEqual(LegacyClient._parse_rdb_sites("# only comment\n"), [])

    def test_rdb_skips_short_and_missing_rows(self):
        rdb = (
            "agency_cd\tsite_no\tstation_nm\tstate_cd\n"
            "5s\t15s\t30s\t5s\n"
            "USGS\t\tNO NUMBER\t24\n"        # missing site_no -> skipped
            "USGS\t123\t\t24\n"              # missing station_nm -> skipped
            "USGS\t456\tSHORT\n"            # too few columns -> skipped
            "USGS\t06710247\tGOOD CREEK\t08\n"
        )
        hits = LegacyClient._parse_rdb_sites(rdb)
        self.assertEqual([h.site_id for h in hits], ["06710247"])

    def test_search_by_name_with_state_sets_params(self):
        session = _ha.FakeSession([_ha.FakeResp(json_data=(
            "agency_cd\tsite_no\tstation_nm\tstate_cd\n"
            "5s\t15s\t30s\t5s\n"
            "USGS\t06710247\tBEAR CREEK\t08\n"
        ))])
        _ha.set_session(session)
        hits = run(LegacyClient(object()).search_sites("Bear Creek", state="co"))
        self.assertEqual(hits[0].site_id, "06710247")
        self.assertEqual(session.calls[-1]["params"]["siteName"], "Bear Creek")
        self.assertEqual(session.calls[-1]["params"]["stateCd"], "CO")

    def test_search_404_is_empty(self):
        _ha.set_session(_ha.FakeSession([_ha.FakeResp(status=404)]))
        self.assertEqual(run(LegacyClient(object()).search_sites("06710247")), [])

    def test_search_non_200_raises(self):
        _ha.set_session(_ha.FakeSession([_ha.FakeResp(status=500)]))
        with self.assertRaises(UsgsHttpStatusError):
            run(LegacyClient(object()).search_sites("06710247"))

    def test_latest_non_200_raises(self):
        _ha.set_session(_ha.FakeSession([_ha.FakeResp(status=500)]))
        with self.assertRaises(UsgsHttpStatusError):
            run(LegacyClient(object()).get_latest_values("X", ["00060"]))

    def test_latest_transport_error(self):
        _ha.set_session(RaisingSession())
        with self.assertRaises(UsgsCommunicationError):
            run(LegacyClient(object()).get_latest_values("X", ["00060"]))

    def test_latest_bad_structure_raises_format(self):
        _ha.set_session(_ha.FakeSession([_ha.FakeResp(json_data={"nope": 1})]))
        with self.assertRaises(UsgsResponseFormatError):
            run(LegacyClient(object()).get_latest_values("X", ["00060"]))

    def test_daily_non_200_raises(self):
        _ha.set_session(_ha.FakeSession([_ha.FakeResp(status=500)]))
        with self.assertRaises(UsgsHttpStatusError):
            run(LegacyClient(object()).get_daily_means("X", "00060", "00003", "a", "b"))

    def test_recent_404_is_empty(self):
        _ha.set_session(_ha.FakeSession([_ha.FakeResp(status=404)]))
        self.assertEqual(
            run(LegacyClient(object()).get_recent_values("X", "00060", 180)), []
        )

    def test_recent_transport_error(self):
        _ha.set_session(RaisingSession())
        with self.assertRaises(UsgsCommunicationError):
            run(LegacyClient(object()).get_recent_values("X", "00060", 180))

    def test_recent_non_200_raises(self):
        _ha.set_session(_ha.FakeSession([_ha.FakeResp(status=500)]))
        with self.assertRaises(UsgsHttpStatusError):
            run(LegacyClient(object()).get_recent_values("X", "00060", 180))

    def test_daily_transport_error(self):
        _ha.set_session(RaisingSession())
        with self.assertRaises(UsgsCommunicationError):
            run(LegacyClient(object()).get_daily_means("X", "00060", "00003", "a", "b"))

    def test_get_site_parameters_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            run(LegacyClient(object()).get_site_parameters("X"))

    def test_latest_skips_malformed_series(self):
        _ha.set_session(_ha.FakeSession([_ha.FakeResp(json_data={
            "value": {"timeSeries": [{"missing": "keys"}]}
        })]))
        res = run(LegacyClient(object()).get_latest_values("X", ["00060"]))
        self.assertEqual(res.readings, {})
        self.assertTrue(res.station_reporting)


class TestLegacyParseHelpers(unittest.TestCase):
    def test_daily_values_bad_structure(self):
        with self.assertRaises(UsgsResponseFormatError):
            _parse_daily_values({"nope": 1})

    def test_daily_values_skips_series_without_values(self):
        self.assertEqual(
            _parse_daily_values({"value": {"timeSeries": [{"no": "values"}]}}), []
        )

    def test_instantaneous_bad_structure(self):
        with self.assertRaises(UsgsResponseFormatError):
            _parse_instantaneous_series({"nope": 1})

    def test_instantaneous_skips_series_without_values(self):
        self.assertEqual(
            _parse_instantaneous_series({"value": {"timeSeries": [{"no": "v"}]}}), []
        )


class TestClientHelpers(unittest.TestCase):
    def test_retry_after_non_numeric_falls_back(self):
        resp = types.SimpleNamespace(headers={"Retry-After": "soon"})
        # Non-numeric header -> exponential backoff, not a crash.
        self.assertGreater(_retry_after_seconds(resp, 0), 0)

    def test_is_newer_none_handling(self):
        self.assertFalse(_is_newer(None, None))
        from datetime import datetime, timezone
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.assertTrue(_is_newer(dt, None))


class TestModernEdges(unittest.TestCase):
    def _client(self):
        return ModernClient(object(), api_key="K")

    def test_request_non_200_raises(self):
        _ha.set_session(_ha.FakeSession([_ha.FakeResp(status=500)]))
        with self.assertRaises(UsgsHttpStatusError):
            run(self._client().get_latest_values("X", ["00060"]))

    def test_request_transport_error(self):
        _ha.set_session(RaisingSession())
        with self.assertRaises(UsgsCommunicationError):
            run(self._client().get_latest_values("X", ["00060"]))

    def test_search_empty_features(self):
        _ha.set_session(_ha.FakeSession([_ha.FakeResp(json_data={"features": []})]))
        self.assertEqual(run(self._client().search_sites("nothing here")), [])

    def test_search_skips_features_missing_id_or_name(self):
        _ha.set_session(_ha.FakeSession([_ha.FakeResp(json_data=fc([
            {"properties": {}},  # no id / name -> skipped
            {"properties": {"monitoring_location_number": "06710247",
                            "monitoring_location_name": "GOOD"}},
        ]))]))
        hits = run(self._client().search_sites("creek"))
        self.assertEqual([h.site_id for h in hits], ["06710247"])

    def test_search_caps_at_50(self):
        features = [
            {"properties": {"monitoring_location_number": f"{i:08d}",
                            "monitoring_location_name": f"SITE {i}"}}
            for i in range(60)
        ]
        _ha.set_session(_ha.FakeSession([_ha.FakeResp(json_data=fc(features))]))
        hits = run(self._client().search_sites("creek"))
        self.assertEqual(len(hits), 50)

    def test_daily_skips_bad_points(self):
        _ha.set_session(_ha.FakeSession([_ha.FakeResp(json_data={"features": [
            {"properties": {"value": "ICE", "time": "2020-01-01"}},
            {"properties": {"value": "5", "time": "bad-date"}},
            {"properties": {"value": "12", "time": "2020-01-04"}},
        ], "links": []})]))
        out = run(self._client().get_daily_means("X", "00060", "00003", "a", "b"))
        self.assertEqual([v for _, v in out], [12.0])

    def test_recent_skips_bad_points(self):
        _ha.set_session(_ha.FakeSession([_ha.FakeResp(json_data={"features": [
            {"properties": {"value": "-999999", "time": "2026-06-11T03:00:00Z"}},
            {"properties": {"value": "2.9", "time": "2026-06-11T03:15:00Z"}},
        ], "links": []})]))
        out = run(self._client().get_recent_values("X", "00065", 180))
        self.assertEqual([v for _, v in out], [2.9])


if __name__ == "__main__":
    unittest.main()
