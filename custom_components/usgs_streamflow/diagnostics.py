"""Diagnostics support for USGS Streamflow."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import UsgsConfigEntry
from .const import CONF_API_KEY
from .coordinator import CoordinatorData, USGSStreamflowCoordinator
from .stats_coordinator import USGSStatsCoordinator

# The api.data.gov key is the only secret an entry carries.
TO_REDACT = {CONF_API_KEY}


def _coordinator_diagnostics(coordinator: USGSStreamflowCoordinator) -> dict[str, Any]:
    data: CoordinatorData | None = coordinator.data
    diag: dict[str, Any] = {
        "site_id": coordinator.site_id,
        "known_params": sorted(coordinator.known_params),
        "last_update_success": coordinator.last_update_success,
    }
    if data is not None:
        diag["latest"] = {
            "values": data.values,
            "reading_times": {
                param: (dt.isoformat() if dt else None)
                for param, dt in data.reading_times.items()
            },
            "station_offline": data.station_offline,
            "offline_reason": data.offline_reason,
            "reported_params": sorted(data.reported_params),
        }
    return diag


def _stats_diagnostics(stats: USGSStatsCoordinator | None) -> dict[str, Any] | None:
    if stats is None:
        return None
    envelopes = {
        param: {
            "years": env.years,
            "record_start": env.record_start,
            "record_end": env.record_end,
            "built": env.built,
            "day_count": len(env.days),
        }
        for param, env in stats.envelopes.items()
    }
    results = {
        param: {
            "condition": res.condition,
            "percentile": res.percentile,
            "percent_of_normal": res.percent_of_normal,
            "sample_count": res.sample_count,
            "inverted": res.inverted,
        }
        for param, res in (stats.data or {}).items()
    }
    return {
        "params": list(stats.params),
        "envelopes": envelopes,
        "results": results,
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: UsgsConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry (with the API key redacted)."""
    runtime = entry.runtime_data
    return {
        "entry": {
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "coordinator": _coordinator_diagnostics(runtime.coordinator),
        "stats": _stats_diagnostics(runtime.stats),
    }
