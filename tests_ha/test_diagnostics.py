"""Diagnostics dump redacts the API key and includes coordinator state.

NOTE: HA-harness test, unverified in the authoring sandbox.
"""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.usgs_streamflow.client import MODERN_BASE_URL
from custom_components.usgs_streamflow.const import CONF_API_KEY
from custom_components.usgs_streamflow.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .helpers import modern_latest_json

LATEST_URL = f"{MODERN_BASE_URL}/collections/latest-continuous/items"
CONTINUOUS_URL = f"{MODERN_BASE_URL}/collections/continuous/items"


async def test_diagnostics_redacts_key_and_reports_state(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, modern_entry
) -> None:
    aioclient_mock.get(LATEST_URL, json=modern_latest_json("00060", "00065"))
    aioclient_mock.get(
        CONTINUOUS_URL,
        json={"type": "FeatureCollection", "features": [], "links": []},
    )
    modern_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(modern_entry.entry_id)
    await hass.async_block_till_done()

    diag = await async_get_config_entry_diagnostics(hass, modern_entry)

    # API key is redacted, never leaked.
    assert diag["entry"]["options"][CONF_API_KEY] != "OLD_KEY"
    assert "OLD_KEY" not in str(diag)

    # Coordinator state is present.
    assert diag["coordinator"]["site_id"] == "01646500"
    assert "00060" in diag["coordinator"]["known_params"]
