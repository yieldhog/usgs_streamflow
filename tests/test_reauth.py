"""Auth-failure handling: 401/403 on an authenticated backend triggers reauth."""
import asyncio
import unittest

from tests import _ha
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from custom_components.usgs_streamflow.client import UsgsHttpStatusError
from custom_components.usgs_streamflow.coordinator import USGSStreamflowCoordinator


def run(coro):
    return asyncio.run(coro)


class _FailClient:
    def __init__(self, status, uses_auth):
        self._status = status
        self.uses_auth = uses_auth

    async def get_latest_values(self, site_id, params):
        raise UsgsHttpStatusError(self._status)


def _coord(client):
    coord = USGSStreamflowCoordinator.__new__(USGSStreamflowCoordinator)
    coord.site_id = "X"
    coord._client = client
    return coord


class TestAuthFailureMapping(unittest.TestCase):
    def test_modern_403_raises_auth_failed(self):
        coord = _coord(_FailClient(403, uses_auth=True))
        with self.assertRaises(ConfigEntryAuthFailed):
            run(coord._async_update_data())

    def test_modern_401_raises_auth_failed(self):
        coord = _coord(_FailClient(401, uses_auth=True))
        with self.assertRaises(ConfigEntryAuthFailed):
            run(coord._async_update_data())

    def test_legacy_403_is_update_failed_not_auth(self):
        # Unauthenticated backend: a 403 is transient, not an auth problem.
        coord = _coord(_FailClient(403, uses_auth=False))
        with self.assertRaises(UpdateFailed):
            run(coord._async_update_data())

    def test_modern_500_is_update_failed_not_auth(self):
        # Non-auth status on an authenticated backend stays a retryable failure.
        coord = _coord(_FailClient(500, uses_auth=True))
        with self.assertRaises(UpdateFailed):
            run(coord._async_update_data())


if __name__ == "__main__":
    unittest.main()
