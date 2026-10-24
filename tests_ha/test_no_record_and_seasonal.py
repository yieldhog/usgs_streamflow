"""No-daily-record caching and seasonal status, end to end through Home Assistant.

Modeled on USGS 06711570 (Harvard Gulch, Denver): discharge has a daily-mean
record, gauge height has none, and the gauge shuts down seasonally.
"""
from __future__ import annotations

import logging

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.usgs_streamflow.const import (
    CONF_ENABLE_STATS,
    CONF_SITE_ID,
    CONF_SITE_NAME,
    DOMAIN,
    USGS_DV_URL,
    USGS_IV_URL,
)

from .helpers import SITE_ID, SITE_NAME, dv_json_today, iv_json, recent_iso

EMPTY_DV = {"value": {"timeSeries": []}}


def _entry(stats: bool = True) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"usgs_{SITE_ID}",
        title=SITE_NAME,
        data={CONF_SITE_ID: SITE_ID, CONF_SITE_NAME: SITE_NAME},
        options={CONF_ENABLE_STATS: stats},
    )


def _dv_calls(aioclient_mock: AiohttpClientMocker, param: str) -> int:
    return sum(
        1
        for _method, url, _data, _headers in aioclient_mock.mock_calls
        if str(url).startswith(USGS_DV_URL) and url.query.get("parameterCd") == param
    )


async def test_gauge_height_without_daily_record(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    aioclient_mock.get(USGS_IV_URL, json=iv_json("00060", "00065"))
    aioclient_mock.get(USGS_DV_URL, params={"parameterCd": "00060"}, json=dv_json_today())
    aioclient_mock.get(USGS_DV_URL, params={"parameterCd": "00065"}, json=EMPTY_DV)

    entry = _entry()
    entry.add_to_hass(hass)
    caplog.set_level(logging.INFO, logger="custom_components.usgs_streamflow")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    by_uid = {
        e.unique_id
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    }
    # Discharge stats sensors exist; gauge height gets none (no envelope).
    assert f"usgs_{SITE_ID}_00060_condition" in by_uid
    assert f"usgs_{SITE_ID}_00065_condition" not in by_uid

    stats = entry.runtime_data.stats
    assert stats is not None
    assert set(stats.no_record) == {"00065"}
    assert "No usable daily history" not in caplog.text
    assert "publishes no daily-mean record" in caplog.text
    assert not [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and "usgs_streamflow" in r.name
    ]
    assert _dv_calls(aioclient_mock, "00065") == 1

    # Reload (as a restart would): the persisted marker means gauge-height
    # history is not requested again, and the info line is not repeated.
    caplog.clear()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _dv_calls(aioclient_mock, "00065") == 1
    assert entry.runtime_data.stats is not None
    assert set(entry.runtime_data.stats.no_record) == {"00065"}
    assert "publishes no daily-mean record" not in caplog.text


async def test_seasonal_shutdown_status(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    # Legacy placeholder published at shutdown: -999999 flagged "Ssn",
    # timestamped just now (inside the 48h staleness window).
    when = recent_iso()
    aioclient_mock.get(
        USGS_IV_URL,
        json={"value": {"timeSeries": [
            {
                "variable": {"variableCode": [{"value": code}]},
                "values": [{"value": [
                    {"value": "-999999", "dateTime": when, "qualifiers": ["P", "Ssn"]}
                ]}],
            }
            for code in ("00060", "00065")
        ]}},
    )

    entry = _entry(stats=False)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    ent_reg = er.async_get(hass)
    status_id = ent_reg.async_get_entity_id("sensor", DOMAIN, f"usgs_{SITE_ID}_status")
    assert status_id is not None
    state = hass.states.get(status_id)
    assert state is not None
    assert state.state == "Offline"
    assert state.attributes["seasonal"] is True
    assert state.attributes["offline_reason"].startswith("Seasonal shutdown")

    # The measurement sensors exist (the station has them) but are unavailable.
    discharge_id = ent_reg.async_get_entity_id("sensor", DOMAIN, f"usgs_{SITE_ID}_00060")
    assert discharge_id is not None
    discharge = hass.states.get(discharge_id)
    assert discharge is not None
    assert discharge.state == "unavailable"
