"""USGS Streamflow integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .client import build_client
from .const import (
    BACKEND_LEGACY,
    CONF_API_KEY,
    CONF_BACKEND,
    CONF_SCAN_INTERVAL,
    CONF_SITE_ID,
    CONF_SITE_NAME,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
)
from .coordinator import USGSStreamflowCoordinator

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

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
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
