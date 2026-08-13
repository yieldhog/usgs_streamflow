"""DataUpdateCoordinator for USGS Streamflow."""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .client import (
    LatestResult,
    LegacyClient,
    UsgsClient,
    UsgsCommunicationError,
    UsgsHttpStatusError,
    UsgsResponseFormatError,
)
from .const import (
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DERIVED_PARAM_CODES,
    DOMAIN,
    HISTORY_RETENTION_MINUTES,
    SUPPORTED_PARAMETERS,
)

_LOGGER = logging.getLogger(__name__)

# Every supported parameter is requested each poll; the station's response tells
# us which it actually has.
FETCH_PARAM_LIST = list(SUPPORTED_PARAMETERS)

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
        reported_params: set[str],
        reading_attrs: dict[str, dict[str, str | None]] | None = None,
    ) -> None:
        self.values = values                    # param_cd -> float | None
        self.reading_times = reading_times      # param_cd -> datetime of last value
        self.station_offline = station_offline  # True when station appears shut down
        self.offline_reason = offline_reason    # Human-readable reason string
        # Params that had a non-empty value_list in this fetch — meaning the
        # station actually has a sensor for this parameter.  Distinct from
        # values.keys(): when we request a param the station doesn't have,
        # USGS returns the timeSeries header with an empty value_list rather
        # than omitting the entry entirely.  An empty value_list = no sensor.
        self.reported_params = reported_params
        # Per-parameter metadata the sensors expose as attributes (approval
        # status, qualifier, statistic / time-series id).  Populated by the
        # modern backend; the legacy backend leaves the values None, so nothing
        # extra appears on legacy entities.
        self.reading_attrs = reading_attrs or {}


class USGSStreamflowCoordinator(DataUpdateCoordinator[CoordinatorData]):
    """Coordinator that polls the USGS NWIS Instantaneous Values API."""

    def __init__(
        self,
        hass: HomeAssistant,
        site_id: str,
        site_name: str,
        update_interval_minutes: int = DEFAULT_SCAN_INTERVAL_MINUTES,
        client: UsgsClient | None = None,
        api_key: str | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"USGS {site_name}",
            update_interval=timedelta(minutes=update_interval_minutes),
        )
        self.site_id = site_id
        self.site_name = site_name
        # All USGS network access goes through this client.  Defaults to the
        # legacy WaterServices backend; a later phase injects the modern one.
        # The API key is passed to the default client but ignored by the legacy
        # backend (it only matters for the modern one).
        self._client: UsgsClient = client or LegacyClient(hass, api_key=api_key)
        # Accumulates which parameter codes this station genuinely has sensors
        # for, across all successful online fetches.  Populated from
        # result.reported_params (params with a non-empty value_list), NOT from
        # result.values.keys() — because USGS returns an empty timeSeries entry
        # for params we requested but the station doesn't have, and treating
        # those as real sensors creates phantom entities.
        self.known_params: set[str] = set()
        # Rolling buffer of recent observations per parameter code, used by
        # the derived rate-of-change and trend sensors.  Keyed by reading
        # timestamp so repeated transmissions of the same observation are
        # not double-counted.  In memory only: cleared on restart/reload, but
        # warm-started from recent history on the first poll (see _seed_history)
        # so the derived sensors report immediately rather than over a few polls.
        self._history: dict[str, deque[tuple[datetime, float]]] = defaultdict(
            deque
        )
        # One-shot guard so the rate/trend buffer is seeded only on the first poll.
        self._history_seeded = False

    async def _async_update_data(self) -> CoordinatorData:
        """Fetch latest readings from USGS via the client and assemble data."""
        try:
            latest = await self._client.get_latest_values(
                self.site_id, FETCH_PARAM_LIST
            )
        except UsgsHttpStatusError as err:
            # On an authenticated backend, 401/403 means the api.data.gov key is
            # missing/invalid — trigger reauth instead of retrying forever.
            if err.status in (401, 403) and getattr(
                self._client, "uses_auth", False
            ):
                raise ConfigEntryAuthFailed(
                    "USGS API rejected the api.data.gov key"
                ) from err
            raise UpdateFailed(
                f"USGS API returned HTTP {err.status} for site {self.site_id}"
            ) from err
        except UsgsResponseFormatError as err:
            raise UpdateFailed(f"Unexpected USGS response structure: {err}") from err
        except UsgsCommunicationError as err:
            raise UpdateFailed(f"Error communicating with USGS API: {err}") from err

        result = self._build_coordinator_data(latest)

        # Always update known_params when reported_params is non-empty.
        # Offline/seasonal status means the values are stale, but the station
        # still tells us exactly which parameters it has — a seasonal gauge
        # with -999999 readings still has value_lists for its real sensors.
        # Gating this on "not offline" caused the fallback in sensor.py to
        # create all three sensors (including phantom temp) for every station
        # that was offline at startup, even those that never had a temp sensor.
        if result.reported_params:
            self.known_params.update(result.reported_params)

        # Warm-start the rate/trend buffer once, before this poll's point is
        # appended, so those sensors report immediately after a restart/reload.
        if not self._history_seeded:
            self._history_seeded = True
            await self._seed_history()

        self._append_history(result)

        return result

    def _append_history(self, result: CoordinatorData) -> None:
        """Record new (reading_time, value) points for derived sensors.

        Only genuinely new observations are stored: a point is appended just
        when its reading timestamp is newer than the last buffered one, so a
        station that re-transmits the same reading across several polls does
        not inflate the buffer or flatten the computed rate.
        """
        for param_cd, value in result.values.items():
            if value is None:
                continue
            reading_dt = result.reading_times.get(param_cd)
            if reading_dt is None:
                continue
            buf = self._history[param_cd]
            if buf and reading_dt <= buf[-1][0]:
                continue
            buf.append((reading_dt, value))
            cutoff = reading_dt - timedelta(minutes=HISTORY_RETENTION_MINUTES)
            while buf and buf[0][0] < cutoff:
                buf.popleft()

    async def _seed_history(self) -> None:
        """Warm-start the rate/trend buffer from recent instantaneous history.

        Fetches the trailing ``HISTORY_RETENTION_MINUTES`` of real observations
        for each reported rate/trend parameter and loads them into the buffer, so
        those sensors compute a rate on the very first poll instead of waiting to
        accumulate points.  Best-effort: any failure (or a backend without the
        recent-values endpoint) is logged at debug and skipped — the buffer then
        simply fills the old way over subsequent polls.
        """
        for param_cd in DERIVED_PARAM_CODES:
            if self.known_params and param_cd not in self.known_params:
                continue
            try:
                points = await self._client.get_recent_values(
                    self.site_id, param_cd, HISTORY_RETENTION_MINUTES
                )
                buf = self._history[param_cd]
                for reading_dt, value in sorted(points):
                    if value is None or reading_dt is None:
                        continue
                    if buf and reading_dt <= buf[-1][0]:
                        continue
                    buf.append((reading_dt, value))
                if buf:
                    cutoff = buf[-1][0] - timedelta(
                        minutes=HISTORY_RETENTION_MINUTES
                    )
                    while buf and buf[0][0] < cutoff:
                        buf.popleft()
            except Exception as err:  # noqa: BLE001
                # Seeding is strictly best-effort: it runs outside the poll's
                # own error handling, so it must never raise.  On any failure the
                # buffer simply fills the old way over subsequent polls.
                _LOGGER.debug(
                    "Skipping rate/trend seed for %s param %s: %s",
                    self.site_id, param_cd, err,
                )

    def recent_points(
        self, param_cd: str, window_minutes: int
    ) -> list[tuple[datetime, float]]:
        """Return buffered (time, value) points within ``window_minutes`` of
        the most recent observation, oldest first."""
        buf = self._history.get(param_cd)
        if not buf:
            return []
        newest = buf[-1][0]
        cutoff = newest - timedelta(minutes=window_minutes)
        return [(t, v) for (t, v) in buf if t >= cutoff]

    def _build_coordinator_data(self, latest: LatestResult) -> CoordinatorData:
        """Assemble a CoordinatorData from the client's latest-values result.

        The client owns fetching and per-parameter parsing; offline detection
        lives here so it stays backend-independent (the same time-age logic
        applies whichever API answered).
        """
        # The station reports a parameter only when the client returned a
        # reading for it (a non-empty value list), so the reading keys are the
        # reported parameters.
        reported_params: set[str] = set(latest.readings)
        values: dict[str, float | None] = {
            param_cd: reading.value for param_cd, reading in latest.readings.items()
        }
        reading_times: dict[str, datetime | None] = {
            param_cd: reading.reading_time
            for param_cd, reading in latest.readings.items()
        }
        # Optional per-parameter metadata for sensor attributes; all-None on the
        # legacy backend (so nothing extra surfaces there).
        reading_attrs: dict[str, dict[str, str | None]] = {
            param_cd: {
                "approval_status": reading.approval_status,
                "qualifier": reading.qualifier,
                "statistic_id": reading.statistic_id,
                "time_series_id": reading.time_series_id,
            }
            for param_cd, reading in latest.readings.items()
        }

        if not latest.station_reporting:
            # Station exists but reports no time series at all —
            # this happens when a gauge is seasonally discontinued.
            return CoordinatorData(
                values={},
                reading_times={},
                station_offline=True,
                offline_reason="Station is not currently reporting data (seasonal or discontinued)",
                reported_params=set(),
            )

        # Use HA's dt_util so we get a timezone-aware UTC datetime.
        # datetime.utcnow() is deprecated in Python 3.12+ and returns a naive
        # datetime that cannot be safely compared against tz-aware values.
        now = dt_util.utcnow()
        any_recent = any(
            reading_dt is not None
            and (now - reading_dt).total_seconds() / 3600 < STALE_READING_HOURS
            for reading_dt in reading_times.values()
        )

        # Determine offline status
        station_offline = False
        offline_reason: str | None = None

        if reported_params and not any_recent:
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
            reported_params=reported_params,
            reading_attrs=reading_attrs,
        )
