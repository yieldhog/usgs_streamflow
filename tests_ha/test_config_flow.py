"""Config-flow coverage on the Home Assistant harness (Bronze: config-flow-*).

NOTE: written to HA conventions but not run in the authoring sandbox (no
homeassistant installed there). Validate with `pytest` after
`pip install -r requirements_test.txt`.
"""
from __future__ import annotations

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.usgs_streamflow.client import MODERN_BASE_URL
from custom_components.usgs_streamflow.const import (
    CONF_API_KEY,
    CONF_SITE_ID,
    DOMAIN,
    USGS_SITE_URL,
)

from .helpers import SITE_ID, SITE_NAME, SITE_RDB, modern_tsm_json

TSM_URL = f"{MODERN_BASE_URL}/collections/time-series-metadata/items"


async def _start_user(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )


async def test_user_flow_by_site_number_creates_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(USGS_SITE_URL, text=SITE_RDB)

    result = await _start_user(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    # A bare site number needs no state code.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"search_term": SITE_ID}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "select_site"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"site_id": SITE_ID}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["result"].data[CONF_SITE_ID] == SITE_ID
    assert result["result"].title == SITE_NAME


async def test_user_flow_no_results(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(USGS_SITE_URL, text="# nothing\n")

    result = await _start_user(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"search_term": SITE_ID}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "no_sites_found"


async def test_user_flow_cannot_connect(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(USGS_SITE_URL, exc=Exception("boom"))

    result = await _start_user(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"search_term": SITE_ID}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "cannot_connect"


async def test_name_search_requires_state(hass: HomeAssistant) -> None:
    result = await _start_user(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"search_term": "Potomac River"}
    )
    assert result["type"] == FlowResultType.FORM
    assert "state_code" in result["errors"]


async def test_duplicate_site_aborts(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, legacy_entry
) -> None:
    legacy_entry.add_to_hass(hass)
    aioclient_mock.get(USGS_SITE_URL, text=SITE_RDB)

    result = await _start_user(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"search_term": SITE_ID}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"site_id": SITE_ID}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_invalid_then_valid_key(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, modern_entry
) -> None:
    modern_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": modern_entry.entry_id},
        data=modern_entry.data,
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    # A rejected key -> invalid_auth.
    aioclient_mock.get(TSM_URL, status=403)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "BAD"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_auth"

    # A good key validates and the flow aborts as reauth_successful. Mock the
    # endpoints the subsequent entry reload will touch so the poll is
    # deterministic (empty collections = station simply reporting nothing yet).
    empty_fc = {"type": "FeatureCollection", "features": [], "links": []}
    aioclient_mock.clear_requests()
    aioclient_mock.get(TSM_URL, json=modern_tsm_json("00060"))
    aioclient_mock.get(
        f"{MODERN_BASE_URL}/collections/latest-continuous/items", json=empty_fc
    )
    aioclient_mock.get(
        f"{MODERN_BASE_URL}/collections/continuous/items", json=empty_fc
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "GOOD"}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert modern_entry.options[CONF_API_KEY] == "GOOD"


async def test_options_flow_saves(hass: HomeAssistant, legacy_entry) -> None:
    legacy_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(legacy_entry.entry_id)
    assert result["type"] == FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"scan_interval_minutes": 30, "enabled_parameters": ["00060"]},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert legacy_entry.options["scan_interval_minutes"] == 30
