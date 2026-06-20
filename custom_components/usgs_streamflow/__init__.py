"""USGS Streamflow integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .client import build_client
from .const import (
    BACKEND_LEGACY,
    CONF_API_KEY,
    CONF_BACKEND,
    CONF_ENABLE_STATS,
    CONF_ENABLED_PARAMETERS,
    CONF_SCAN_INTERVAL,
    CONF_SITE_ID,
    CONF_SITE_NAME,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    STATS_PARAMS,
    SUPPORTED_PARAMETERS,
)
from .coordinator import USGSStreamflowCoordinator
from .stats_coordinator import USGSStatsCoordinator

PLATFORMS = ["sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
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
            param: invert
            for param, invert in STATS_PARAMS.items()
            if param in enabled and (not reported or param in reported)
        }
        if stats_params:
            stats_coordinator = USGSStatsCoordinator(
                hass, coordinator, client, stats_params
            )
            await stats_coordinator.async_refresh()
            entry.async_on_unload(stats_coordinator.attach_source())

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "stats": stats_coordinator,
    }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload the entry when its options change (scan interval / parameter set).
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
