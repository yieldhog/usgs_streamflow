"""API key plumbing (Phase B): key reaches the client, ignored by legacy."""
import unittest

from custom_components.usgs_streamflow.client import LegacyClient
from custom_components.usgs_streamflow.config_flow import USGSStreamflowConfigFlow
from custom_components.usgs_streamflow.coordinator import USGSStreamflowCoordinator


class _Entry:
    def __init__(self, options=None, data=None):
        self.options = options or {}
        self.data = data or {}


class _Hass:
    def __init__(self, entries):
        self.config_entries = type(
            "CE", (), {"async_entries": lambda self, domain: entries}
        )()


class TestCoordinatorKeyForwarding(unittest.TestCase):
    def test_key_reaches_default_legacy_client(self):
        coord = USGSStreamflowCoordinator(
            object(), site_id="1", site_name="X", api_key="KEY"
        )
        self.assertIsInstance(coord._client, LegacyClient)
        self.assertEqual(coord._client._api_key, "KEY")

    def test_no_key_is_none(self):
        coord = USGSStreamflowCoordinator(object(), site_id="1", site_name="X")
        self.assertIsNone(coord._client._api_key)

    def test_injected_client_used_as_is(self):
        sentinel = object()
        coord = USGSStreamflowCoordinator(
            object(), site_id="1", site_name="X", client=sentinel, api_key="IGNORED"
        )
        self.assertIs(coord._client, sentinel)


class TestPrefillLookup(unittest.TestCase):
    def _prefill(self, entries):
        flow = USGSStreamflowConfigFlow()
        flow.hass = _Hass(entries)
        return flow._existing_api_key()

    def test_prefill_from_options(self):
        self.assertEqual(self._prefill([_Entry(options={"api_key": "FROM_OPTS"})]), "FROM_OPTS")

    def test_prefill_from_data(self):
        self.assertEqual(self._prefill([_Entry(data={"api_key": "FROM_DATA"})]), "FROM_DATA")

    def test_prefill_skips_empty_entries(self):
        self.assertEqual(
            self._prefill([_Entry(), _Entry(options={"api_key": "SECOND"})]), "SECOND"
        )

    def test_prefill_none_when_no_key(self):
        self.assertEqual(self._prefill([]), "")


if __name__ == "__main__":
    unittest.main()
