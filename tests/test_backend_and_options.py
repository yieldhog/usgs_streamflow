"""Backend selection factory and the options flow (Phase D)."""
import asyncio
import unittest

from custom_components.usgs_streamflow.client import (
    LegacyClient,
    ModernClient,
    build_client,
)
from custom_components.usgs_streamflow.config_flow import USGSStreamflowOptionsFlow
from custom_components.usgs_streamflow.const import BACKEND_LEGACY


class _Entry:
    def __init__(self, options):
        self.options = options


class TestBuildClient(unittest.TestCase):
    def test_modern(self):
        self.assertIsInstance(build_client(object(), backend="modern"), ModernClient)

    def test_legacy(self):
        self.assertIsInstance(build_client(object(), backend="legacy"), LegacyClient)

    def test_unknown_falls_back_to_legacy(self):
        self.assertIsInstance(build_client(object(), backend="bogus"), LegacyClient)
        self.assertIsInstance(build_client(object(), backend=""), LegacyClient)

    def test_const_default_is_legacy(self):
        self.assertEqual(BACKEND_LEGACY, "legacy")

    def test_api_key_forwarded(self):
        self.assertEqual(build_client(object(), backend="modern", api_key="K")._api_key, "K")
        self.assertEqual(build_client(object(), backend="legacy", api_key="K")._api_key, "K")


class TestOptionsFlow(unittest.TestCase):
    def _form_keys(self, advanced):
        flow = USGSStreamflowOptionsFlow()
        flow.config_entry = _Entry({})
        flow.show_advanced_options = advanced
        result = asyncio.run(flow.async_step_init(None))
        return {marker.key for marker in result["schema"].fields}

    def test_backend_only_shown_in_advanced(self):
        self.assertIn("backend", self._form_keys(True))
        self.assertNotIn("backend", self._form_keys(False))

    def test_core_fields_always_shown(self):
        keys = self._form_keys(False)
        self.assertTrue(
            {"api_key", "scan_interval_minutes", "enabled_parameters"} <= keys
        )

    def test_save_merges_and_preserves_hidden_backend(self):
        flow = USGSStreamflowOptionsFlow()
        flow.config_entry = _Entry(
            {"backend": "modern", "api_key": "OLD", "scan_interval_minutes": 15}
        )
        flow.show_advanced_options = False
        result = asyncio.run(flow.async_step_init(
            {"api_key": "NEW", "scan_interval_minutes": 30, "enabled_parameters": ["00060"]}
        ))
        data = result["data"]
        self.assertEqual(data["backend"], "modern")   # preserved though hidden
        self.assertEqual(data["api_key"], "NEW")
        self.assertEqual(data["scan_interval_minutes"], 30)

    def test_switch_to_modern_saved(self):
        flow = USGSStreamflowOptionsFlow()
        flow.config_entry = _Entry({"backend": "legacy"})
        flow.show_advanced_options = True
        result = asyncio.run(flow.async_step_init(
            {"api_key": "", "scan_interval_minutes": 15,
             "enabled_parameters": [], "backend": "modern"}
        ))
        self.assertEqual(result["data"]["backend"], "modern")


if __name__ == "__main__":
    unittest.main()
