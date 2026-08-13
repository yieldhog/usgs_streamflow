"""USGS Streamflow integration."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .client import build_client
from .const import (
    API_SIGNUP_URL,
    BACKEND_LEGACY,
    BACKEND_MODERN,
    CONF_API_KEY,
    CONF_BACKEND,
    CONF_ENABLE_STATS,
    CONF_ENABLED_PARAMETERS,
    CONF_SCAN_INTERVAL,
    CONF_SITE_ID,
    CONF_SITE_NAME,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DEMO_KEY,
    DOMAIN,
    STATS_PARAMS,
    SUPPORTED_PARAMETERS,
)
from .coordinator import USGSStreamflowCoordinator
from .stats_coordinator import USGSStatsCoordinator, stats_store

PLATFORMS = ["sensor"]


def _demo_key_issue_id(entry: ConfigEntry) -> str:
    return f"demo_key_{entry.entry_id}"


def _update_demo_key_issue(
    hass: HomeAssistant, entry: ConfigEntry, backend: str, api_key: str
) -> None:
    """Raise (or clear) a repair issue when the Modern backend uses DEMO_KEY.

    The shared demo key is heavily rate-limited; a repair issue nudges the user
    to get a free personal key instead of silently hitting 429s.
    """
    issue_id = _demo_key_issue_id(entry)
    if backend == BACKEND_MODERN and (not api_key or api_key == DEMO_KEY):
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="demo_key",
            translation_placeholders={"signup_url": API_SIGNUP_URL},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, issue_id)


@dataclass
class UsgsRuntimeData:
    """Runtime objects stored on the config entry (see ``runtime-data`` rule)."""

    coordinator: USGSStreamflowCoordinator
    stats: USGSStatsCoordinator | None


# The config entry carries its runtime objects on ``entry.runtime_data`` instead
# of a hass.data table keyed by entry id.
UsgsConfigEntry = ConfigEntry[UsgsRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: UsgsConfigEntry) -> bool:
    """Set up USGS Streamflow from a config entry."""
    interval = int(
        entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES)
    )
    # Per-entry api.data.gov key for the modern backend; ignored by legacy.
    api_key = entry.options.get(CONF_API_KEY, "")
    # Which API backend this entry polls (defaults to legacy).  Changing it in
    # the options flow reloads the entry, which rebuilds the client here.
    backend = entry.options.get(CONF_BACKEND, BACKEND_LEGACY)
    client = build_client(hass, backend=backend, api_key=api_key)

    # Surface a repair issue when polling the Modern backend on the shared,
    # rate-limited demo key (cleared when a real key is set or on Legacy).
    _update_demo_key_issue(hass, entry, backend, api_key)

    coordinator = USGSStreamflowCoordinator(
        hass,
        site_id=entry.data[CONF_SITE_ID],
        site_name=entry.data[CONF_SITE_NAME],
        update_interval_minutes=interval,
        client=client,
    )

    await coordinator.async_config_entry_first_refresh()

    # Opt-in percent-of-normal / condition statistics.  Built only for the
    # stats-eligible parameters the station actually serves (known_params, now
    # populated by the first refresh) and that the user left enabled.  The
    # envelope fetch is heavy but cached; a failure is logged inside the stats
    # coordinator and never blocks the rest of setup.
    stats_coordinator: USGSStatsCoordinator | None = None
    if entry.options.get(CONF_ENABLE_STATS):
        enabled = set(
            entry.options.get(CONF_ENABLED_PARAMETERS) or SUPPORTED_PARAMETERS
        )
        reported = coordinator.known_params
        stats_params = {
            param: cfg
            for param, cfg in STATS_PARAMS.items()
            if param in enabled and (not reported or param in reported)
        }
        if stats_params:
            stats_coordinator = USGSStatsCoordinator(
                hass, coordinator, client, stats_params
            )
            await stats_coordinator.async_refresh()
            entry.async_on_unload(stats_coordinator.attach_source())

    entry.runtime_data = UsgsRuntimeData(
        coordinator=coordinator, stats=stats_coordinator
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload the entry when its options change (scan interval / parameter set).
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: UsgsConfigEntry) -> None:
    """Reload the config entry when options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: UsgsConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: UsgsConfigEntry) -> None:
    """Clean up the persisted cache and any repair issue when a gauge is removed."""
    ir.async_delete_issue(hass, DOMAIN, _demo_key_issue_id(entry))
    await stats_store(hass, entry.data[CONF_SITE_ID]).async_remove()
