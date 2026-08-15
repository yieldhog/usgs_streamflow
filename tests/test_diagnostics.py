"""Diagnostics: redaction and the fields support needs to debug a report."""
import asyncio
import unittest
from types import SimpleNamespace

from tests import _ha  # noqa: F401 - installs the HA stubs (incl. async_redact_data)
from custom_components.usgs_streamflow import diagnostics as diag


def run(coro):
    return asyncio.run(coro)


def _coordinator(data=None):
    return SimpleNamespace(
        site_id="01646500",
        site_name="Potomac River",
        known_params={"00060", "00065"},
        last_update_success=True,
        data=data,
    )


def _latest():
    return SimpleNamespace(
        values={"00060": 1200.0, "00065": 3.1},
        reading_times={"00060": None, "00065": None},
        station_offline=False,
        offline_reason=None,
        reported_params={"00060", "00065"},
        reading_attrs={"00060": {"approval_status": "Provisional"}},
    )


class TestCoordinatorDiagnostics(unittest.TestCase):
    def test_includes_site_name_and_known_params(self):
        d = diag._coordinator_diagnostics(_coordinator())
        self.assertEqual(d["site_id"], "01646500")
        self.assertEqual(d["site_name"], "Potomac River")
        self.assertEqual(d["known_params"], ["00060", "00065"])
        self.assertNotIn("latest", d)  # no data yet

    def test_latest_includes_reading_attrs(self):
        d = diag._coordinator_diagnostics(_coordinator(_latest()))
        self.assertFalse(d["latest"]["station_offline"])
        self.assertEqual(
            d["latest"]["reading_attrs"]["00060"]["approval_status"], "Provisional"
        )
        self.assertEqual(d["latest"]["reported_params"], ["00060", "00065"])


class TestStatsDiagnostics(unittest.TestCase):
    def test_none_when_stats_disabled(self):
        self.assertIsNone(diag._stats_diagnostics(None))

    def test_envelope_and_result_metadata(self):
        env = SimpleNamespace(
            years=30, record_start="1994", record_end="2024",
            built="2026-06-11T00:00:00+00:00", days={"06-11": object()},
        )
        res = SimpleNamespace(
            condition="Normal", percentile=52.0, percent_of_normal=104.0,
            sample_count=30, inverted=False,
        )
        stats = SimpleNamespace(
            params={"00060": object()},
            envelopes={"00060": env},
            data={"00060": res},
        )
        d = diag._stats_diagnostics(stats)
        self.assertEqual(d["params"], ["00060"])
        self.assertEqual(d["envelopes"]["00060"]["day_count"], 1)
        self.assertEqual(d["envelopes"]["00060"]["years"], 30)
        self.assertEqual(d["results"]["00060"]["condition"], "Normal")


class TestEntryDiagnostics(unittest.TestCase):
    def test_redacts_key_and_surfaces_backend_flags(self):
        entry = SimpleNamespace(
            title="Potomac River",
            data={"site_id": "01646500", "site_name": "Potomac River"},
            options={
                "backend": "modern",
                "api_key": "secret-key",
                "enabled_parameters": ["00060"],
            },
            runtime_data=SimpleNamespace(coordinator=_coordinator(_latest()), stats=None),
        )
        d = run(diag.async_get_config_entry_diagnostics(None, entry))
        # Secret redacted; backend/flags surfaced for support.
        self.assertEqual(d["entry"]["options"]["api_key"], "**REDACTED**")
        self.assertEqual(d["backend"], "modern")
        self.assertTrue(d["api_key_set"])
        self.assertEqual(d["enabled_params"], ["00060"])
        self.assertEqual(d["coordinator"]["site_name"], "Potomac River")

    def test_legacy_defaults_and_no_key(self):
        entry = SimpleNamespace(
            title="X",
            data={"site_id": "1"},
            options={},
            runtime_data=SimpleNamespace(coordinator=_coordinator(), stats=None),
        )
        d = run(diag.async_get_config_entry_diagnostics(None, entry))
        self.assertEqual(d["backend"], "legacy")
        self.assertFalse(d["api_key_set"])
        self.assertIsNone(d["enabled_params"])


if __name__ == "__main__":
    unittest.main()
