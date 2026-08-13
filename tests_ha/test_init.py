"""Setup / unload / reauth lifecycle on the Home Assistant harness.

NOTE: written to HA conventions but not run in the authoring sandbox. Validate
with `pytest` after `pip install -r requirements_test.txt`.
"""
from __future__ import annotations

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.usgs_streamflow.client import MODERN_BASE_URL
from custom_components.usgs_streamflow.const import USGS_IV_URL

from .helpers import SITE_ID, iv_json

LATEST_URL = f"{MODERN_BASE_URL}/collections/latest-continuous/items"


async def test_setup_creates_entities_and_unloads(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, legacy_entry
) -> None:
    aioclient_mock.get(USGS_IV_URL, json=iv_json("00060", "00065"))
    legacy_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(legacy_entry.entry_id)
    await hass.async_block_till_done()
    assert legacy_entry.state is ConfigEntryState.LOADED

    ent_reg = er.async_get(hass)
    entries = er.async_entries_for_config_entry(ent_reg, legacy_entry.entry_id)
    unique_ids = {e.unique_id for e in entries}
    assert f"usgs_{SITE_ID}_00060" in unique_ids  # discharge
    assert f"usgs_{SITE_ID}_status" in unique_ids  # station status

    assert await hass.config_entries.async_unload(legacy_entry.entry_id)
    await hass.async_block_till_done()
    assert legacy_entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_retry_on_api_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, legacy_entry
) -> None:
    aioclient_mock.get(USGS_IV_URL, status=500)
    legacy_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(legacy_entry.entry_id)
    await hass.async_block_till_done()
    assert legacy_entry.state is ConfigEntryState.SETUP_RETRY


async def test_modern_auth_failure_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, modern_entry
) -> None:
    aioclient_mock.get(LATEST_URL, status=403)
    modern_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(modern_entry.entry_id)
    await hass.async_block_till_done()

    assert modern_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == SOURCE_REAUTH for f in flows)


async def test_remove_entry_runs_cleanly(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, legacy_entry
) -> None:
    aioclient_mock.get(USGS_IV_URL, json=iv_json("00060"))
    legacy_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(legacy_entry.entry_id)
    await hass.async_block_till_done()

    # async_remove_entry deletes the persisted stats cache (no-op if absent).
    assert await hass.config_entries.async_remove(legacy_entry.entry_id)
    await hass.async_block_till_done()
    assert not hass.config_entries.async_entries(legacy_entry.domain)
