"""USGS API client abstraction.

Every USGS network call the integration makes is routed through a client that
implements the :class:`UsgsClient` interface, so the rest of the integration
(coordinator, config flow, sensors, data model) never learns which backend
answered.  This is the seam that lets us migrate from the legacy WaterServices
API to the modern OGC API one file at a time, and run the two side by side.

Phase A ships only :class:`LegacyClient`, which wraps today's WaterServices
calls unchanged.  :class:`ModernClient` (OGC API) arrives in a later phase.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .const import (
    SITE_NUMBER_RE,
    USGS_IV_URL,
    USGS_SITE_URL,
    normalize_site_number,
)

# Network timeout for every USGS request, in seconds.
_REQUEST_TIMEOUT = 30


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class UsgsClientError(Exception):
    """Base class for every error a USGS client raises.

    Backends raise these instead of Home Assistant's ``UpdateFailed`` so the
    client stays decoupled from HA; the coordinator translates them.
    """


class UsgsHttpStatusError(UsgsClientError):
    """The API responded with a non-success HTTP status."""

    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(f"HTTP {status}")


class UsgsResponseFormatError(UsgsClientError):
    """The API response could not be parsed into the expected structure."""


class UsgsCommunicationError(UsgsClientError):
    """A transport-level failure (timeout, connection reset, bad JSON, ...)."""


# --------------------------------------------------------------------------- #
# Data transfer objects
# --------------------------------------------------------------------------- #
@dataclass
class SiteHit:
    """One monitoring location returned by a site search.

    ``state`` and ``site_type`` are optional and may be ``None`` depending on
    what the backend can provide; the legacy backend supplies ``state`` (for the
    selector label) but not ``site_type``.
    """

    site_id: str
    site_name: str
    state: str | None = None
    site_type: str | None = None


@dataclass
class Reading:
    """The latest observed value for a single parameter at a site.

    ``approval_status`` and ``qualifier`` are carried for forward compatibility
    with the modern backend (which exposes them); the legacy backend leaves them
    ``None`` and the current data model does not surface them.
    """

    value: float | None
    reading_time: datetime | None
    approval_status: str | None = None
    qualifier: str | None = None


@dataclass
class LatestResult:
    """Result of a latest-values poll for one site.

    ``readings`` is keyed by parameter code and contains an entry only for the
    parameters the station actually reports (i.e. the station has that sensor).

    ``station_reporting`` is ``False`` only when the station returned no time
    series at all — the signal the legacy API uses for a seasonally discontinued
    gauge.  It is distinct from ``readings`` being empty: a station can return
    series headers with no data for every requested parameter, which leaves
    ``readings`` empty while ``station_reporting`` stays ``True``.  The
    coordinator relies on this distinction to reproduce offline detection.
    """

    readings: dict[str, Reading] = field(default_factory=dict)
    station_reporting: bool = True


# --------------------------------------------------------------------------- #
# Interface
# --------------------------------------------------------------------------- #
class UsgsClient(Protocol):
    """The single interface every backend implements.

    The coordinator and config flow depend only on this; backend selection
    happens upstream.
    """

    async def search_sites(
        self, term: str, *, state: str | None = None
    ) -> list[SiteHit]:
        """Search monitoring locations by name or by exact site number."""
        ...

    async def get_site_parameters(self, site_id: str) -> set[str]:
        """Return the parameter codes the site reports as continuous values.

        Used by the modern backend for capability gating.  The legacy backend
        derives the reported parameters from the poll response itself, so it
        does not implement this.
        """
        ...

    async def get_latest_values(
        self, site_id: str, params: list[str]
    ) -> LatestResult:
        """Fetch the latest value for each requested parameter at ``site_id``."""
        ...


# --------------------------------------------------------------------------- #
# Legacy backend (WaterServices) — wraps today's calls unchanged
# --------------------------------------------------------------------------- #
# USGS RDB responses return numeric FIPS state codes (e.g. "08"), not two-letter
# abbreviations.  This mapping converts them for display.
_FIPS_TO_STATE: dict[str, str] = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA",
    "08": "CO", "09": "CT", "10": "DE", "11": "DC", "12": "FL",
    "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN",
    "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME",
    "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS",
    "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND",
    "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI",
    "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT",
    "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI",
    "56": "WY", "60": "AS", "66": "GU", "69": "MP", "72": "PR",
    "78": "VI",
}

# USGS uses -999999 as a sentinel for missing/suppressed data.
_MISSING_VALUE_SENTINEL = -999999.0


class LegacyClient:
    """USGS client backed by the legacy WaterServices API.

    Wraps the exact requests and parsing the integration used before the client
    abstraction existed, so behavior is unchanged.
    """

    def __init__(self, hass: HomeAssistant, api_key: str | None = None) -> None:
        self._hass = hass
        # WaterServices is unauthenticated, so the api.data.gov key is accepted
        # for a uniform constructor signature but deliberately unused here — it
        # is plumbed through only for the modern backend.  Keeping it off the
        # legacy request path guarantees legacy polling is unchanged.
        self._api_key = api_key

    # -- site search ------------------------------------------------------- #
    async def search_sites(
        self, term: str, *, state: str | None = None
    ) -> list[SiteHit]:
        """Query the NWIS site service by name or direct site number."""
        session = async_get_clientsession(self._hass)
        params: dict[str, str] = {
            "format": "rdb",
            "siteStatus": "all",    # include seasonal — user should see all options
            "hasDataTypeCd": "iv",  # only sites with instantaneous values (what we poll)
        }

        # Detect if the user pasted a site number directly (optionally prefixed).
        candidate = normalize_site_number(term)
        if SITE_NUMBER_RE.match(candidate):
            params["sites"] = candidate
        else:
            params["siteName"] = term.strip()

        if state and state.strip():
            params["stateCd"] = state.strip().upper()

        timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)
        async with session.get(USGS_SITE_URL, params=params, timeout=timeout) as resp:
            # The NWIS site service returns 404 when *no sites match* the query —
            # documented behavior, not a transport error.  Treat it as an empty
            # result set so the flow shows "no sites found" rather than a
            # spurious connection error.
            if resp.status == 404:
                return []
            if resp.status != 200:
                raise UsgsHttpStatusError(resp.status)
            text = await resp.text()

        return self._parse_rdb_sites(text)

    @staticmethod
    def _parse_rdb_sites(rdb_text: str) -> list[SiteHit]:
        """Parse a USGS RDB (tab-delimited) site response into SiteHits."""
        sites: list[SiteHit] = []
        lines = [line for line in rdb_text.splitlines() if not line.startswith("#")]
        if len(lines) < 3:
            return sites

        headers = lines[0].split("\t")
        # lines[1] is the column type descriptor row — skip it
        for line in lines[2:]:
            cols = line.split("\t")
            if len(cols) < len(headers):
                continue
            row = dict(zip(headers, cols))
            site_no = row.get("site_no", "").strip()
            station_nm = row.get("station_nm", "").strip()
            # USGS RDB returns a numeric FIPS code in state_cd (e.g. "08"),
            # not a two-letter abbreviation.  Convert it for a readable label.
            fips_cd = row.get("state_cd", "").strip()
            state_abbrev = _FIPS_TO_STATE.get(fips_cd, fips_cd)  # fallback: raw value

            if not site_no or not station_nm:
                continue

            sites.append(
                SiteHit(
                    site_id=site_no,
                    site_name=station_nm,
                    state=state_abbrev or None,
                )
            )

        return sites[:50]  # cap to keep selector manageable

    # -- capability gating ------------------------------------------------- #
    async def get_site_parameters(self, site_id: str) -> set[str]:
        """Not used on the legacy backend.

        The legacy poll response itself tells us which parameters the station
        reports (a parameter with a non-empty value list = a real sensor), so
        the coordinator derives capabilities from ``get_latest_values`` and
        never calls this.
        """
        raise NotImplementedError(
            "LegacyClient derives site parameters from the poll response"
        )

    # -- latest values poll ------------------------------------------------ #
    async def get_latest_values(
        self, site_id: str, params: list[str]
    ) -> LatestResult:
        """Fetch the latest instantaneous values for ``params`` at ``site_id``."""
        session = async_get_clientsession(self._hass)
        request_params = {
            "sites": site_id,
            "parameterCd": ",".join(params),
            "format": "json",
        }

        timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)
        try:
            async with session.get(
                USGS_IV_URL, params=request_params, timeout=timeout
            ) as resp:
                if resp.status != 200:
                    raise UsgsHttpStatusError(resp.status)
                data = await resp.json(content_type=None)
        except UsgsClientError:
            raise
        except Exception as err:  # noqa: BLE001 - mirror legacy catch-all
            raise UsgsCommunicationError(str(err)) from err

        return self._parse_latest(data)

    @staticmethod
    def _parse_latest(data) -> LatestResult:
        """Parse USGS NWIS instantaneous-values JSON into a LatestResult."""
        try:
            time_series_list = data["value"]["timeSeries"]
        except (KeyError, TypeError) as err:
            raise UsgsResponseFormatError(str(err)) from err

        if not time_series_list:
            # Station exists but reports no time series at all — this happens
            # when a gauge is seasonally discontinued.
            return LatestResult(readings={}, station_reporting=False)

        readings: dict[str, Reading] = {}

        for series in time_series_list:
            try:
                param_cd = series["variable"]["variableCode"][0]["value"]
                value_list = series["values"][0]["value"]
            except (KeyError, IndexError):
                continue

            if not value_list:
                # USGS returned the series header but no data — this is how the
                # API signals "this parameter was requested but does not exist
                # at this station."  Skip entirely so no phantom sensor is
                # created for it.
                continue

            last_entry = value_list[-1]
            reading_dt = LegacyClient._parse_reading_time(last_entry.get("dateTime"))
            value = LegacyClient._parse_value(last_entry.get("value"))

            readings[param_cd] = Reading(
                value=value,
                reading_time=reading_dt,
                approval_status=None,
                qualifier=None,
            )

        return LatestResult(readings=readings, station_reporting=True)

    @staticmethod
    def _parse_reading_time(dt_str) -> datetime | None:
        """Parse a USGS ISO-8601 timestamp into a tz-aware UTC-comparable dt."""
        if not dt_str:
            return None
        try:
            # USGS timestamps are ISO-8601 with a UTC offset, e.g.
            # "2024-06-01T14:15:00.000-06:00".  fromisoformat produces a
            # tz-aware datetime, comparable directly against dt_util.utcnow().
            reading_dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
        if reading_dt.tzinfo is None:
            # Defensive: treat naive timestamps as UTC
            reading_dt = reading_dt.replace(tzinfo=dt_util.UTC)
        return reading_dt

    @staticmethod
    def _parse_value(raw) -> float | None:
        """Convert a raw USGS value to float, mapping the sentinel to None."""
        try:
            value = float(raw)
        except (ValueError, TypeError):
            return None
        return None if value == _MISSING_VALUE_SENTINEL else value
