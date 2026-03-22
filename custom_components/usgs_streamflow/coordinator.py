"""DataUpdateCoordinator for USGS Streamflow."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    SCAN_INTERVAL_MINUTES,
    USGS_IV_URL,
    PARAM_DISCHARGE,
    PARAM_GAUGE_HEIGHT,
    PARAM_WATER_TEMP,
)

_LOGGER = logging.getLogger(__name__)

FETCH_PARAMS = ",".join([PARAM_GAUGE_HEIGHT, PARAM_DISCHARGE, PARAM_WATER_TEMP])

# If the most recent USGS reading is older than this, the station is likely
# seasonally shut down or decommissioned.
STALE_READING_HOURS = 48


class CoordinatorData:
    """Holds the parsed coordinator payload for a single poll."""

    def __init__(
        self,
        values: dict[str, float | None],
        reading_times: dict[str, datetime | None],
        station_offline: bool,
        offline_reason: str | None,
    ) -> None:
        self.values = values                    # param_cd -> float | None
        self.reading_times = reading_times      # param_cd -> datetime of last value
        self.station_offline = station_offline  # True when station appears shut down
        self.offline_reason = offline_reason    # Human-readable reason string


class USGSStreamflowCoordinator(DataUpdateCoordinator[CoordinatorData]):
    """Coordinator that polls the USGS NWIS Instantaneous Values API."""

    def __init__(self, hass: HomeAssistant, site_id: str, site_name: str) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"USGS {site_name}",
            update_interval=timedelta(minutes=SCAN_INTERVAL_MINUTES),
        )
        self.site_id = site_id
        self.site_name = site_name
        # Tracks which parameter codes this station actually has, based on
        # what appeared in the USGS timeSeries response during any successful
        # online fetch.  Populated from values.keys() — NOT from which params
        # returned non-None values — because a param can legitimately exist at
        # a station while temporarily returning -999999 (USGS suppressed/missing
        # sentinel).  Used by the sensor platform to show only sensors the
        # station actually supports, while still registering all sensors
        # unconditionally at setup to survive offline-at-startup restarts.
        self.known_params: set[str] = set()

    async def _async_update_data(self) -> CoordinatorData:
        """Fetch latest readings from USGS NWIS."""
        session = async_get_clientsession(self.hass)
        params = {
            "sites": self.site_id,
            "parameterCd": FETCH_PARAMS,
            "format": "json",
        }

        timeout = aiohttp.ClientTimeout(total=30)
        try:
            async with session.get(USGS_IV_URL, params=params, timeout=timeout) as resp:
                if resp.status != 200:
                    raise UpdateFailed(
                        f"USGS API returned HTTP {resp.status} for site {self.site_id}"
                    )
                data = await resp.json(content_type=None)
        except UpdateFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"Error communicating with USGS API: {err}") from err

        result = self._parse_response(data)

        # When the station is online, values.keys() is exactly the set of
        # parameter codes the station has configured in USGS NWIS — regardless
        # of whether the current reading is a real float or None (-999999
        # sentinel).  Record these so sensors for absent params (e.g., no
        # thermistor) can be surfaced as unavailable rather than hidden.
        # We only update on non-offline responses; a seasonal/stale response
        # with an empty values dict would otherwise clear our knowledge.
        if not result.station_offline and result.values:
            self.known_params.update(result.values.keys())

        return result

    def _parse_response(self, data: Any) -> CoordinatorData:
        """Parse USGS NWIS JSON into a CoordinatorData object."""
        values: dict[str, float | None] = {}
        reading_times: dict[str, datetime | None] = {}

        try:
            time_series_list = data["value"]["timeSeries"]
        except (KeyError, TypeError) as err:
            raise UpdateFailed(f"Unexpected USGS response structure: {err}") from err

        if not time_series_list:
            # Station exists but reports no time series at all —
            # this happens when a gauge is seasonally discontinued.
            return CoordinatorData(
                values={},
                reading_times={},
                station_offline=True,
                offline_reason="Station is not currently reporting data (seasonal or discontinued)",
            )

        # Use HA's dt_util so we get a timezone-aware UTC datetime.
        # datetime.utcnow() is deprecated in Python 3.12+ and returns a naive
        # datetime that cannot be safely compared against tz-aware values.
        now = dt_util.utcnow()
        any_recent = False

        for series in time_series_list:
            try:
                param_cd = series["variable"]["variableCode"][0]["value"]
                value_list = series["values"][0]["value"]
            except (KeyError, IndexError):
                continue

            if not value_list:
                values[param_cd] = None
                reading_times[param_cd] = None
                continue

            last_entry = value_list[-1]
            raw = last_entry.get("value")
            dt_str = last_entry.get("dateTime")

            # Parse reading timestamp
            reading_dt: datetime | None = None
            if dt_str:
                try:
                    # USGS timestamps are ISO-8601 with a UTC offset, e.g.
                    # "2024-06-01T14:15:00.000-06:00".  fromisoformat produces a
                    # tz-aware datetime, which we can compare directly against
                    # dt_util.utcnow() without stripping tzinfo.
                    reading_dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                    if reading_dt.tzinfo is None:
                        # Defensive: treat naive timestamps as UTC
                        reading_dt = reading_dt.replace(tzinfo=dt_util.UTC)
                    age_hours = (now - reading_dt).total_seconds() / 3600
                    if age_hours < STALE_READING_HOURS:
                        any_recent = True
                except (ValueError, TypeError):
                    pass

            reading_times[param_cd] = reading_dt

            try:
                value = float(raw)
                # USGS uses -999999 as a sentinel for missing/suppressed data
                values[param_cd] = None if value == -999999.0 else value
            except (ValueError, TypeError):
                values[param_cd] = None

        # Determine offline status
        station_offline = False
        offline_reason: str | None = None

        if values and not any_recent:
            station_offline = True
            latest_times = [t for t in reading_times.values() if t is not None]
            if latest_times:
                last_ts = max(latest_times)
                offline_reason = (
                    f"Station data is stale — last reading {last_ts.strftime('%Y-%m-%d')}. "
                    "This gauge may be seasonally decommissioned."
                )
            else:
                offline_reason = (
                    "Station is not reporting current data. "
                    "This gauge may be seasonally decommissioned."
                )

        return CoordinatorData(
            values=values,
            reading_times=reading_times,
            station_offline=station_offline,
            offline_reason=offline_reason,
        )
