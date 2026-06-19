"""Coordinator: backend-independent offline detection and attribute mapping."""
import unittest
from datetime import datetime, timezone

from tests import _ha
from custom_components.usgs_streamflow import client as client_mod
from custom_components.usgs_streamflow.coordinator import USGSStreamflowCoordinator

NOW = datetime(2026, 6, 11, 4, 0, 0, tzinfo=timezone.utc)
RECENT = "2026-06-11T03:30:00Z"   # 30 min before NOW
STALE = "2026-06-07T00:00:00Z"    # ~4 days before NOW (> 48h)


def reading(value, when, **meta):
    return client_mod.Reading(
        value=value, reading_time=client_mod._parse_iso_datetime(when), **meta
    )


def build(latest):
    coord = USGSStreamflowCoordinator.__new__(USGSStreamflowCoordinator)
    return coord._build_coordinator_data(latest)


class TestOfflineDetection(unittest.TestCase):
    def setUp(self):
        _ha.set_now(NOW)

    def test_not_reporting_is_offline_seasonal(self):
        cd = build(client_mod.LatestResult(readings={}, station_reporting=False))
        self.assertTrue(cd.station_offline)
        self.assertIn("seasonal", cd.offline_reason.lower())
        self.assertEqual(cd.reported_params, set())
        self.assertEqual(cd.values, {})

    def test_recent_reading_is_online(self):
        cd = build(client_mod.LatestResult(
            readings={"00065": reading(3.5, RECENT)}, station_reporting=True))
        self.assertFalse(cd.station_offline)
        self.assertIsNone(cd.offline_reason)
        self.assertEqual(cd.values["00065"], 3.5)
        self.assertEqual(cd.reported_params, {"00065"})

    def test_stale_reading_is_offline_with_date(self):
        cd = build(client_mod.LatestResult(
            readings={"00065": reading(3.5, STALE)}, station_reporting=True))
        self.assertTrue(cd.station_offline)
        self.assertIn("stale", cd.offline_reason.lower())
        self.assertIn("2026-06-07", cd.offline_reason)

    def test_reporting_but_no_readings_is_online(self):
        # series present yet empty readings -> not offline (distinct from seasonal)
        cd = build(client_mod.LatestResult(readings={}, station_reporting=True))
        self.assertFalse(cd.station_offline)
        self.assertIsNone(cd.offline_reason)


class TestReadingAttributes(unittest.TestCase):
    def setUp(self):
        _ha.set_now(NOW)

    def test_modern_attrs_populated(self):
        cd = build(client_mod.LatestResult(readings={
            "00065": reading(-1.02, RECENT, approval_status="Provisional",
                             qualifier=None, statistic_id="00011",
                             time_series_id="abc123"),
        }, station_reporting=True))
        attrs = cd.reading_attrs["00065"]
        self.assertEqual(attrs["approval_status"], "Provisional")
        self.assertEqual(attrs["statistic_id"], "00011")
        self.assertEqual(attrs["time_series_id"], "abc123")
        self.assertIsNone(attrs["qualifier"])

    def test_legacy_attrs_all_none(self):
        cd = build(client_mod.LatestResult(
            readings={"00065": reading(2.0, RECENT)}, station_reporting=True))
        self.assertTrue(all(v is None for v in cd.reading_attrs["00065"].values()))


if __name__ == "__main__":
    unittest.main()
