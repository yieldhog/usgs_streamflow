"""Repair issue: DEMO_KEY on the Modern backend raises a warning issue.

NOTE: HA-harness test, unverified in the authoring sandbox.
"""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.usgs_streamflow.client import MODERN_BASE_URL
from custom_components.usgs_streamflow.const import (
    CONF_API_KEY,
    CONF_BACKEND,
    CONF_SITE_ID,
    CONF_SITE_NAME,
    DOMAIN,
    USGS_IV_URL,
)

from .helpers import SITE_ID, SITE_NAME, iv_json, modern_latest_json

LATEST_URL = f"{MODERN_BASE_URL}/collections/latest-continuous/items"
CONTINUOUS_URL = f"{MODERN_BASE_URL}/collections/continuous/items"
EMPTY_FC = {"type": "FeatureCollection", "features": [], "links": []}


def _issue_id(entry: MockConfigEntry) -> str:
    return f"demo_key_{entry.entry_id}"


async def test_demo_key_issue_raised_on_modern_without_key(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(LATEST_URL, json=modern_latest_json("00060"))
    aioclient_mock.get(CONTINUOUS_URL, json=EMPTY_FC)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"usgs_{SITE_ID}",
        title=SITE_NAME,
        data={CONF_SITE_ID: SITE_ID, CONF_SITE_NAME: SITE_NAME},
        options={CONF_BACKEND: "modern"},  # no API key -> DEMO_KEY
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, _issue_id(entry)) is not None


async def test_no_issue_on_legacy_backend(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, legacy_entry
) -> None:
    aioclient_mock.get(USGS_IV_URL, json=iv_json("00060"))
    legacy_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(legacy_entry.entry_id)
    await hass.async_block_till_done()

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, _issue_id(legacy_entry)) is None
