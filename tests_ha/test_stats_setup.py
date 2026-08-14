"""Stats-enabled setup: envelope build, stats sensors, diagnostics stats branch.

NOTE: HA-harness test, unverified in the authoring sandbox.
"""
from __future__ import annotations

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
from custom_components.usgs_streamflow.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .helpers import SITE_ID, SITE_NAME, dv_json_today, iv_json


async def test_stats_enabled_end_to_end(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    # Legacy backend keeps the mocked surface small: IV for the live poll + the
    # rate/trend seed, DV for the long-term percentile envelope.
    aioclient_mock.get(USGS_IV_URL, json=iv_json("00060", "00065"))
    aioclient_mock.get(USGS_DV_URL, json=dv_json_today())

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"usgs_{SITE_ID}",
        title=SITE_NAME,
        data={CONF_SITE_ID: SITE_ID, CONF_SITE_NAME: SITE_NAME},
        options={CONF_ENABLE_STATS: True},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    ent_reg = er.async_get(hass)
    entries = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    by_uid = {e.unique_id: e for e in entries}

    # Discharge gets all three stats sensors; gauge height gets no % of Normal.
    assert f"usgs_{SITE_ID}_00060_condition" in by_uid
    assert f"usgs_{SITE_ID}_00060_percentile" in by_uid
    assert f"usgs_{SITE_ID}_00060_pct_of_normal" in by_uid
    assert f"usgs_{SITE_ID}_00065_condition" in by_uid
    assert f"usgs_{SITE_ID}_00065_pct_of_normal" not in by_uid

    # Reading a stats sensor exercises native_value + extra_state_attributes.
    condition = hass.states.get(by_uid[f"usgs_{SITE_ID}_00060_condition"].entity_id)
    assert condition is not None
    assert "percentile" in condition.attributes
    assert "record_years" in condition.attributes

    # Diagnostics includes the stats branch (envelopes + results), key redacted.
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["stats"] is not None
    assert "00060" in diag["stats"]["envelopes"]
    assert diag["stats"]["envelopes"]["00060"]["day_count"] >= 1

    # Clean removal deletes the persisted stats cache without error.
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
