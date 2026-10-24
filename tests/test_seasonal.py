"""Seasonal shutdown: USGS's explicit seasonal qualifier drives offline status.

Payload shapes below are trimmed copies of what USGS returned for
06711570 (Harvard Gulch at Colorado Blvd., Denver) in October 2026, after the
gauge shut down for the season on 2026-10-01.
"""
import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from tests import _ha
from custom_components.usgs_streamflow import client as client_mod
from custom_components.usgs_streamflow import sensor as sensor_mod
from custom_components.usgs_streamflow.client import LegacyClient, ModernClient
from custom_components.usgs_streamflow.coordinator import USGSStreamflowCoordinator

SHUTDOWN = "2026-10-01T07:05:00+00:00"
SHUTDOWN_DT = datetime(2026, 10, 1, 7, 5, tzinfo=timezone.utc)


def run(coro):
    return asyncio.run(coro)


def legacy_series(code, value, qualifiers, when="2026-10-01T01:05:00.000-06:00"):
    return {
        "variable": {"variableCode": [{"value": code}], "noDataValue": -999999},
        "values": [{"value": [
            {"dateTime": when, "qualifiers": qualifiers, "value": value}
        ]}],
    }


# Legacy IV: -999999 placeholder flagged "P" + "Ssn" (monitored seasonally).
LEGACY_SEASONAL_IV = {"value": {"timeSeries": [
    legacy_series("00060", "-999999", ["P", "Ssn"]),
    legacy_series("00065", "-999999", ["P", "Ssn"]),
]}}


def modern_feat(code, value, qualifier, time=SHUTDOWN):
    return {"type": "Feature", "properties": {
        "parameter_code": code, "time": time, "value": value,
        "qualifier": qualifier, "statistic_id": "00011",
    }}


# Modern latest-continuous: null value with qualifier ["SEASONAL"].
MODERN_SEASONAL_FC = {"type": "FeatureCollection", "links": [], "features": [
    modern_feat("00065", None, ["SEASONAL"]),
    modern_feat("00060", None, ["SEASONAL"]),
    modern_feat("63160", None, ["SEASONAL"]),
]}


def build(latest):
    coord = USGSStreamflowCoordinator.__new__(USGSStreamflowCoordinator)
    return coord._build_coordinator_data(latest)


def reading(value, when=SHUTDOWN, seasonal=False):
    return client_mod.Reading(
        value=value, reading_time=client_mod._parse_iso_datetime(when),
        seasonal=seasonal,
    )


class TestHasQualifier(unittest.TestCase):
    def test_shapes(self):
        f = client_mod._has_qualifier
        self.assertTrue(f(["P", "Ssn"], "Ssn"))
        self.assertTrue(f(("SEASONAL",), "SEASONAL"))
        self.assertTrue(f("SEASONAL", "SEASONAL"))
        self.assertFalse(f(["P"], "Ssn"))
        self.assertFalse(f("Ssn,P", "Ssn"))   # no substring matching
        self.assertFalse(f(None, "Ssn"))
        self.assertFalse(f(42, "Ssn"))


class TestClientsFlagSeasonal(unittest.TestCase):
    def test_legacy_real_payload(self):
        res = LegacyClient._parse_latest(LEGACY_SEASONAL_IV)
        for code in ("00060", "00065"):
            self.assertIsNone(res.readings[code].value)   # -999999 sentinel
            self.assertTrue(res.readings[code].seasonal)
        # Legacy still leaves the qualifier attribute unset (unchanged).
        self.assertIsNone(res.readings["00060"].qualifier)

    def test_legacy_without_ssn_not_seasonal(self):
        data = {"value": {"timeSeries": [legacy_series("00060", "12.0", ["P"])]}}
        self.assertFalse(LegacyClient._parse_latest(data).readings["00060"].seasonal)
        data = {"value": {"timeSeries": [{
            "variable": {"variableCode": [{"value": "00060"}]},
            "values": [{"value": [{"dateTime": SHUTDOWN, "value": "12.0"}]}],
        }]}}   # no "qualifiers" key at all
        self.assertFalse(LegacyClient._parse_latest(data).readings["00060"].seasonal)

    def test_modern_real_payload(self):
        _ha.set_session(_ha.FakeSession([_ha.FakeResp(json_data=MODERN_SEASONAL_FC)]))
        client = ModernClient(object(), api_key="K")
        res = run(client.get_latest_values("06711570", ["00060", "00065"]))
        self.assertEqual(set(res.readings), {"00060", "00065"})
        for code in ("00060", "00065"):
            self.assertIsNone(res.readings[code].value)
            self.assertTrue(res.readings[code].seasonal)

    def test_modern_null_qualifier_not_seasonal(self):
        fc = {"type": "FeatureCollection", "links": [], "features": [
            modern_feat("00060", "1.5", None, time="2026-06-11T03:00:00Z")]}
        _ha.set_session(_ha.FakeSession([_ha.FakeResp(json_data=fc)]))
        res = run(ModernClient(object(), api_key="K").get_latest_values("X", ["00060"]))
        self.assertFalse(res.readings["00060"].seasonal)


class TestCoordinatorSeasonal(unittest.TestCase):
    def test_flags_shutdown_immediately(self):
        # One hour after shutdown the placeholder is "recent", so the 48h
        # staleness check alone would still say Active.
        _ha.set_now(SHUTDOWN_DT + timedelta(hours=1))
        cd = build(client_mod.LatestResult(readings={
            "00060": reading(None, seasonal=True),
            "00065": reading(None, seasonal=True),
        }))
        self.assertTrue(cd.station_offline)
        self.assertTrue(cd.seasonal)
        self.assertIn("Seasonal shutdown", cd.offline_reason)
        self.assertIn("since 2026-10-01", cd.offline_reason)
        # Still reports which sensors the station has (no phantom entities).
        self.assertEqual(cd.reported_params, {"00060", "00065"})

    def test_weeks_later_uses_seasonal_reason_not_stale(self):
        _ha.set_now(SHUTDOWN_DT + timedelta(days=23))
        cd = build(client_mod.LatestResult(readings={
            "00060": reading(None, seasonal=True),
        }))
        self.assertTrue(cd.station_offline)
        self.assertTrue(cd.seasonal)
        self.assertNotIn("stale", cd.offline_reason.lower())

    def test_end_to_end_from_legacy_payload(self):
        _ha.set_now(SHUTDOWN_DT + timedelta(days=23))
        cd = build(LegacyClient._parse_latest(LEGACY_SEASONAL_IV))
        self.assertTrue(cd.station_offline)
        self.assertTrue(cd.seasonal)

    def test_any_real_value_means_not_seasonal(self):
        # One parameter still producing data -> station is not shut down.
        _ha.set_now(SHUTDOWN_DT + timedelta(hours=1))
        cd = build(client_mod.LatestResult(readings={
            "00060": reading(None, seasonal=True),
            "00065": reading(2.4),
        }))
        self.assertFalse(cd.station_offline)
        self.assertFalse(cd.seasonal)
        self.assertIsNone(cd.offline_reason)

    def test_seasonal_flag_on_real_value_is_not_shutdown(self):
        _ha.set_now(SHUTDOWN_DT + timedelta(hours=1))
        cd = build(client_mod.LatestResult(readings={
            "00060": reading(5.0, seasonal=True),
        }))
        self.assertFalse(cd.station_offline)
        self.assertFalse(cd.seasonal)

    def test_missing_values_without_flag_keep_old_behavior(self):
        # No seasonal qualifier: fall back to timestamp staleness as before.
        _ha.set_now(SHUTDOWN_DT + timedelta(hours=1))
        recent = build(client_mod.LatestResult(readings={"00060": reading(None)}))
        self.assertFalse(recent.station_offline)
        self.assertFalse(recent.seasonal)
        _ha.set_now(SHUTDOWN_DT + timedelta(days=5))
        stale = build(client_mod.LatestResult(readings={"00060": reading(None)}))
        self.assertTrue(stale.station_offline)
        self.assertFalse(stale.seasonal)
        self.assertIn("stale", stale.offline_reason.lower())

    def test_seasonal_without_timestamp(self):
        _ha.set_now(SHUTDOWN_DT)
        cd = build(client_mod.LatestResult(readings={
            "00060": client_mod.Reading(value=None, reading_time=None, seasonal=True),
        }))
        self.assertTrue(cd.seasonal)
        self.assertNotIn("since", cd.offline_reason)

    def test_not_reporting_branch_unchanged(self):
        cd = build(client_mod.LatestResult(readings={}, station_reporting=False))
        self.assertTrue(cd.station_offline)
        self.assertFalse(cd.seasonal)


class TestStatusSensorAttributes(unittest.TestCase):
    def _sensor(self, data):
        s = sensor_mod.USGSStationStatusSensor.__new__(
            sensor_mod.USGSStationStatusSensor
        )
        s.coordinator = SimpleNamespace(site_id="06711570", data=data)
        return s

    def test_seasonal_offline(self):
        s = self._sensor(SimpleNamespace(
            station_offline=True, seasonal=True,
            offline_reason="Seasonal shutdown — x",
        ))
        self.assertEqual(s.native_value, "Offline")   # state values unchanged
        attrs = s.extra_state_attributes
        self.assertIs(attrs["seasonal"], True)
        self.assertEqual(attrs["offline_reason"], "Seasonal shutdown — x")

    def test_active(self):
        s = self._sensor(SimpleNamespace(
            station_offline=False, seasonal=False, offline_reason=None,
        ))
        self.assertEqual(s.native_value, "Active")
        attrs = s.extra_state_attributes
        self.assertIs(attrs["seasonal"], False)
        self.assertNotIn("offline_reason", attrs)

    def test_no_data_yet(self):
        s = self._sensor(None)
        self.assertEqual(s.native_value, "Unknown")
        self.assertNotIn("seasonal", s.extra_state_attributes)


if __name__ == "__main__":
    unittest.main()
