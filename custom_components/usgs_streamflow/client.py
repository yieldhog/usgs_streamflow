"""USGS API client abstraction.

Every USGS network call the integration makes is routed through a client that
implements the :class:`UsgsClient` interface, so the rest of the integration
(coordinator, config flow, sensors, data model) never learns which backend
answered.  This is the seam that lets us migrate from the legacy WaterServices
API to the modern OGC API one file at a time, and run the two side by side.

:class:`LegacyClient` wraps today's WaterServices calls unchanged.
:class:`ModernClient` talks to the modern OGC API and is the eventual target.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Protocol

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .const import (
    BACKEND_MODERN,
    DEMO_KEY,
    SITE_NUMBER_RE,
    SUPPORTED_PARAMETERS,
    USGS_DV_URL,
    USGS_IV_URL,
    USGS_SITE_URL,
    normalize_site_number,
)

_LOGGER = logging.getLogger(__name__)

# Network timeout for every USGS request, in seconds.
_REQUEST_TIMEOUT = 30

# USGS uses -999999 as a sentinel for missing/suppressed data.  Confirmed for
# the legacy API; applied to the modern API too as a defensive parity measure
# (whether the modern API emits it is unverified — see MIGRATION.md §7.3).
_MISSING_VALUE_SENTINEL = -999999.0


def _parse_iso_datetime(dt_str: Any) -> datetime | None:
    """Parse an ISO-8601 / RFC 3339 timestamp into a tz-aware datetime.

    USGS timestamps carry a UTC offset (e.g. "2024-06-01T14:15:00.000-06:00"
    or "...Z"); the result is comparable directly against ``dt_util.utcnow()``.
    Returns ``None`` for empty or unparseable values.  Shared by both backends.
    """
    if not dt_str:
        return None
    try:
        parsed = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        # Defensive: treat naive timestamps as UTC
        parsed = parsed.replace(tzinfo=dt_util.UTC)
    return parsed


def _parse_iso_date(dt_str: Any) -> date | None:
    """Parse a ``YYYY-MM-DD`` (or longer ISO) string into a ``date``.

    Daily-values timestamps are plain calendar dates; the legacy ``dv`` service
    and the modern ``daily`` collection both emit them this way.  Returns
    ``None`` for empty or unparseable values.  Shared by both backends.
    """
    if not dt_str:
        return None
    try:
        return date.fromisoformat(str(dt_str)[:10])
    except (ValueError, TypeError):
        return None


def _value_to_float(raw: Any) -> float | None:
    """Convert a raw value to float, mapping the missing sentinel to None.

    The modern API transmits values as strings to preserve precision; the legacy
    API as numbers.  ``float()`` handles both.  Shared by both backends.
    """
    try:
        value = float(raw)
    except (ValueError, TypeError):
        return None
    return None if value == _MISSING_VALUE_SENTINEL else value


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

    Beyond ``value`` and ``reading_time``, the modern backend also populates the
    metadata fields (approval status, qualifier, statistic id, time series id),
    which the sensors surface as attributes.  The legacy backend leaves them
    ``None`` (WaterServices does not provide them in the same shape), so they
    simply don't appear on legacy entities.
    """

    value: float | None
    reading_time: datetime | None
    approval_status: str | None = None
    qualifier: str | None = None
    statistic_id: str | None = None
    time_series_id: str | None = None


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

    # Whether this backend authenticates (so the coordinator can treat an HTTP
    # 401/403 as an auth failure that should trigger the reauth flow, rather than
    # a transient error to retry).
    uses_auth: bool

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

    async def get_daily_means(
        self, site_id: str, param: str, statistic_id: str, start: str, end: str
    ) -> list[tuple[date, float]]:
        """Fetch the long-term daily record for ``param`` between two ISO dates.

        Returns ``(date, value)`` pairs (order unspecified) for the requested
        daily statistic — the raw material the percent-of-normal envelope is
        built from.  Used only when the statistics feature is enabled.
        """
        ...

    async def get_recent_values(
        self, site_id: str, param: str, minutes: int
    ) -> list[tuple[datetime, float]]:
        """Fetch the last ``minutes`` of instantaneous values for ``param``.

        Returns ``(reading_time, value)`` pairs used to warm-start the derived
        rate/trend buffer so those sensors report immediately after a restart
        instead of accumulating over several polls.  Best-effort: an empty list
        (no recent data) is a normal result, not an error.
        """
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


class LegacyClient:
    """USGS client backed by the legacy WaterServices API.

    Wraps the exact requests and parsing the integration used before the client
    abstraction existed, so behavior is unchanged.
    """

    # WaterServices is unauthenticated, so a 401/403 is never an auth problem.
    uses_auth = False

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
            readings[param_cd] = Reading(
                value=_value_to_float(last_entry.get("value")),
                reading_time=_parse_iso_datetime(last_entry.get("dateTime")),
                approval_status=None,
                qualifier=None,
            )

        return LatestResult(readings=readings, station_reporting=True)

    # -- daily history ----------------------------------------------------- #
    async def get_daily_means(
        self, site_id: str, param: str, statistic_id: str, start: str, end: str
    ) -> list[tuple[date, float]]:
        """Fetch daily values for ``param`` via the legacy ``dv`` service."""
        session = async_get_clientsession(self._hass)
        request_params = {
            "sites": site_id,
            "parameterCd": param,
            "statCd": statistic_id,
            "startDT": start,
            "endDT": end,
            "format": "json",
        }
        timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)
        try:
            async with session.get(
                USGS_DV_URL, params=request_params, timeout=timeout
            ) as resp:
                if resp.status == 404:
                    return []  # no record for this site/param — empty, not an error
                if resp.status != 200:
                    raise UsgsHttpStatusError(resp.status)
                data = await resp.json(content_type=None)
        except UsgsClientError:
            raise
        except Exception as err:  # noqa: BLE001 - normalize transport errors
            raise UsgsCommunicationError(str(err)) from err

        return _parse_daily_values(data)

    async def get_recent_values(
        self, site_id: str, param: str, minutes: int
    ) -> list[tuple[datetime, float]]:
        """Fetch the trailing ``minutes`` of instantaneous values via the IV service."""
        session = async_get_clientsession(self._hass)
        request_params = {
            "sites": site_id,
            "parameterCd": param,
            "format": "json",
            "period": f"PT{int(minutes)}M",
        }
        timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)
        try:
            async with session.get(
                USGS_IV_URL, params=request_params, timeout=timeout
            ) as resp:
                if resp.status == 404:
                    return []
                if resp.status != 200:
                    raise UsgsHttpStatusError(resp.status)
                data = await resp.json(content_type=None)
        except UsgsClientError:
            raise
        except Exception as err:  # noqa: BLE001 - normalize transport errors
            raise UsgsCommunicationError(str(err)) from err

        return _parse_instantaneous_series(data)


def _parse_daily_values(data) -> list[tuple[date, float]]:
    """Flatten a USGS ``dv`` JSON response into ``(date, value)`` pairs."""
    try:
        time_series_list = data["value"]["timeSeries"]
    except (KeyError, TypeError) as err:
        raise UsgsResponseFormatError(str(err)) from err

    out: list[tuple[date, float]] = []
    for series in time_series_list:
        try:
            value_list = series["values"][0]["value"]
        except (KeyError, IndexError):
            continue
        for entry in value_list:
            value = _value_to_float(entry.get("value"))
            day = _parse_iso_date(entry.get("dateTime"))
            if value is None or day is None:
                continue
            out.append((day, value))
    return out


def _parse_instantaneous_series(data) -> list[tuple[datetime, float]]:
    """Flatten a USGS IV JSON response into ``(reading_time, value)`` pairs.

    Unlike ``_parse_latest`` (which keeps only the newest point per series), this
    returns the full trailing window used to seed the rate/trend buffer.
    """
    try:
        time_series_list = data["value"]["timeSeries"]
    except (KeyError, TypeError) as err:
        raise UsgsResponseFormatError(str(err)) from err

    out: list[tuple[datetime, float]] = []
    for series in time_series_list:
        try:
            value_list = series["values"][0]["value"]
        except (KeyError, IndexError):
            continue
        for entry in value_list:
            value = _value_to_float(entry.get("value"))
            when = _parse_iso_datetime(entry.get("dateTime"))
            if value is None or when is None:
                continue
            out.append((when, value))
    return out


# --------------------------------------------------------------------------- #
# Modern backend (USGS Water Data OGC API)
# --------------------------------------------------------------------------- #
# Base URL for the modern API.  Isolated here so the eventual v0 -> v1 move (or
# any alpha churn) is a one-line edit (MIGRATION.md §4.1, §11).
MODERN_BASE_URL = "https://api.waterdata.usgs.gov/ogcapi/v0"

# Continuous / "IV-equivalent" series are identified in time-series-metadata by
# this computation_period_identifier (MIGRATION.md §3.4).
_CONTINUOUS_PERIOD = "Points"

# CQL2 JSON request content type (MIGRATION.md §3.6).
_CQL_CONTENT_TYPE = "application/query-cql-json"

# Page size for item queries; sites have far fewer series than this, so a poll
# is normally a single page.  We still follow ``next`` links defensively.
_PAGE_LIMIT = 1000
_MAX_PAGES = 50

# 429 backoff: retry on rate-limit responses with exponential delay, honoring a
# Retry-After header when present.  Only HTTP 429 and Retry-After are relied on
# (both standard); the api.data.gov "remaining requests" header names are not
# yet confirmed (MIGRATION.md §3.1), so they are not used for control flow.
_MAX_429_RETRIES = 4
_BACKOFF_BASE_SECONDS = 1.0


def _strip_usgs_prefix(value: str) -> str:
    """Strip a leading agency prefix (e.g. 'USGS-') from a location id."""
    return normalize_site_number(value)


def _next_link(envelope: dict) -> str | None:
    """Return the ``next`` page href from an OGC FeatureCollection, if any."""
    for link in envelope.get("links") or []:
        if link.get("rel") == "next" and link.get("href"):
            return link["href"]
    return None


class ModernClient:
    """USGS client backed by the modern Water Data OGC API.

    Implements the same :class:`UsgsClient` interface as :class:`LegacyClient`
    so it is a drop-in replacement.  Authenticates with an api.data.gov key via
    the ``X-Api-Key`` header and backs off on HTTP 429.
    """

    # Authenticates with an api.data.gov key, so a 401/403 means the key is
    # missing/invalid and the reauth flow should run.
    uses_auth = True

    def __init__(self, hass: HomeAssistant, api_key: str | None = None) -> None:
        self._hass = hass
        # Empty key -> shared demo key (heavily rate-limited); warn once at setup.
        self._api_key = api_key or DEMO_KEY
        if not api_key:
            _LOGGER.warning(
                "No api.data.gov key configured; using the shared DEMO_KEY, "
                "which is strictly rate-limited. Get a free key at "
                "https://api.data.gov/signup/ and set it in the integration "
                "options."
            )

    # -- HTTP plumbing ----------------------------------------------------- #
    def _headers(self, *, cql: bool = False) -> dict[str, str]:
        headers = {"X-Api-Key": self._api_key, "Accept": "application/json"}
        if cql:
            headers["Content-Type"] = _CQL_CONTENT_TYPE
        return headers

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        cql_body: dict | None = None,
    ) -> dict:
        """Perform a request and return parsed JSON, retrying on HTTP 429."""
        session = async_get_clientsession(self._hass)
        timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)
        headers = self._headers(cql=cql_body is not None)
        body = json.dumps(cql_body) if cql_body is not None else None

        attempt = 0
        while True:
            retry_delay: float | None = None
            try:
                async with session.request(
                    method,
                    url,
                    params=params,
                    data=body,
                    headers=headers,
                    timeout=timeout,
                ) as resp:
                    if resp.status == 200:
                        return await resp.json(content_type=None)
                    if resp.status == 429 and attempt < _MAX_429_RETRIES:
                        retry_delay = _retry_after_seconds(resp, attempt)
                    else:
                        raise UsgsHttpStatusError(resp.status)
            except UsgsClientError:
                raise
            except Exception as err:  # noqa: BLE001 - normalize transport errors
                raise UsgsCommunicationError(str(err)) from err

            # Reached only for a retryable 429.
            attempt += 1
            await asyncio.sleep(retry_delay or 0.0)

    async def _collect_features(
        self, url: str, params: dict[str, str]
    ) -> list[dict]:
        """Fetch all features for an item query, following ``next`` links."""
        features: list[dict] = []
        next_url: str | None = url
        next_params: dict[str, str] | None = params
        pages = 0
        while next_url and pages < _MAX_PAGES:
            envelope = await self._request_json("GET", next_url, params=next_params)
            page = envelope.get("features") or []
            features.extend(page)
            # ``next`` is a fully-qualified URL carrying its own query string.
            next_url = _next_link(envelope)
            next_params = None
            pages += 1
            if not page:
                break
        return features

    # -- site search ------------------------------------------------------- #
    async def search_sites(
        self, term: str, *, state: str | None = None
    ) -> list[SiteHit]:
        """Search monitoring locations by exact site number or name substring.

        Name search uses POST CQL2 ``like`` (uppercased — ``LIKE`` is
        case-sensitive) scoped to ``agency_code = 'USGS'`` (MIGRATION.md §7.1).
        The ``state`` argument is accepted but not applied server-side: the
        ``state_code`` units are unconfirmed (§3.5), so we do not filter on it.
        """
        url = f"{MODERN_BASE_URL}/collections/monitoring-locations/items"
        candidate = normalize_site_number(term)
        if SITE_NUMBER_RE.match(candidate):
            cql: dict = {
                "op": "=",
                "args": [{"property": "monitoring_location_number"}, candidate],
            }
        else:
            cql = {
                "op": "and",
                "args": [
                    {
                        "op": "like",
                        "args": [
                            {"property": "monitoring_location_name"},
                            f"%{term.strip().upper()}%",
                        ],
                    },
                    {"op": "=", "args": [{"property": "agency_code"}, "USGS"]},
                ],
            }

        envelope = await self._request_json(
            "POST",
            url,
            params={"f": "json", "limit": str(_PAGE_LIMIT)},
            cql_body=cql,
        )

        hits: list[SiteHit] = []
        for feature in envelope.get("features") or []:
            props = feature.get("properties") or {}
            raw_id = (
                props.get("monitoring_location_id")
                or props.get("monitoring_location_number")
                or ""
            )
            site_id = _strip_usgs_prefix(raw_id)
            name = props.get("monitoring_location_name") or ""
            if not site_id or not name:
                continue
            hits.append(
                SiteHit(
                    site_id=site_id,
                    site_name=name,
                    state=None,
                    site_type=props.get("site_type"),
                )
            )
            if len(hits) >= 50:
                break
        return hits

    # -- capability gating ------------------------------------------------- #
    async def get_site_parameters(self, site_id: str) -> set[str]:
        """Return the supported parameter codes the site reports as continuous.

        Reads time-series-metadata for the location and keeps series whose
        ``computation_period_identifier`` is ``Points`` (MIGRATION.md §7.2),
        intersected with the parameters this integration understands.
        """
        url = f"{MODERN_BASE_URL}/collections/time-series-metadata/items"
        params = {
            "monitoring_location_id": f"USGS-{site_id}",
            "f": "json",
            "limit": str(_PAGE_LIMIT),
        }
        features = await self._collect_features(url, params)

        found: set[str] = set()
        for feature in features:
            props = feature.get("properties") or {}
            if props.get("computation_period_identifier") != _CONTINUOUS_PERIOD:
                continue
            code = props.get("parameter_code")
            if code in SUPPORTED_PARAMETERS:
                found.add(code)
        return found

    # -- latest values poll ------------------------------------------------ #
    async def get_latest_values(
        self, site_id: str, params: list[str]
    ) -> LatestResult:
        """Fetch the latest continuous value for each requested parameter.

        Queries latest-continuous for the location (URL-param equality on
        ``monitoring_location_id`` is verified — §3.3) and keeps the requested
        parameters.  When a parameter has more than one series, the most recent
        observation wins.
        """
        wanted = set(params)
        url = f"{MODERN_BASE_URL}/collections/latest-continuous/items"
        request_params = {
            "monitoring_location_id": f"USGS-{site_id}",
            "f": "json",
            "limit": str(_PAGE_LIMIT),
        }
        features = await self._collect_features(url, request_params)

        readings: dict[str, Reading] = {}
        for feature in features:
            props = feature.get("properties") or {}
            param_cd = props.get("parameter_code")
            if param_cd not in wanted:
                continue
            reading_dt = _parse_iso_datetime(props.get("time"))
            existing = readings.get(param_cd)
            if existing is not None and not _is_newer(
                reading_dt, existing.reading_time
            ):
                continue
            readings[param_cd] = Reading(
                value=_value_to_float(props.get("value")),
                reading_time=reading_dt,
                approval_status=props.get("approval_status"),
                qualifier=props.get("qualifier"),
                statistic_id=props.get("statistic_id"),
                time_series_id=props.get("time_series_id"),
            )

        # Any features at all means the station is reporting; an empty collection
        # mirrors the legacy "no time series" signal for a discontinued gauge.
        return LatestResult(readings=readings, station_reporting=bool(features))

    # -- daily history ----------------------------------------------------- #
    async def get_daily_means(
        self, site_id: str, param: str, statistic_id: str, start: str, end: str
    ) -> list[tuple[date, float]]:
        """Fetch daily values for ``param`` from the modern ``daily`` collection.

        Uses an OGC ``datetime`` interval (``start/end``) and follows cursor
        ``next`` links until the record is exhausted.  Verified live against
        api.waterdata.usgs.gov.
        """
        url = f"{MODERN_BASE_URL}/collections/daily/items"
        request_params = {
            "monitoring_location_id": f"USGS-{site_id}",
            "parameter_code": param,
            "statistic_id": statistic_id,
            "datetime": f"{start}/{end}",
            "f": "json",
            "limit": str(_PAGE_LIMIT),
        }
        features = await self._collect_features(url, request_params)

        out: list[tuple[date, float]] = []
        for feature in features:
            props = feature.get("properties") or {}
            value = _value_to_float(props.get("value"))
            day = _parse_iso_date(props.get("time"))
            if value is None or day is None:
                continue
            out.append((day, value))
        return out

    async def get_recent_values(
        self, site_id: str, param: str, minutes: int
    ) -> list[tuple[datetime, float]]:
        """Fetch the trailing ``minutes`` of a parameter from the ``continuous`` collection."""
        end = dt_util.utcnow()
        start = end - timedelta(minutes=minutes)
        url = f"{MODERN_BASE_URL}/collections/continuous/items"
        request_params = {
            "monitoring_location_id": f"USGS-{site_id}",
            "parameter_code": param,
            "datetime": f"{_iso_instant(start)}/{_iso_instant(end)}",
            "f": "json",
            "limit": str(_PAGE_LIMIT),
        }
        features = await self._collect_features(url, request_params)

        out: list[tuple[datetime, float]] = []
        for feature in features:
            props = feature.get("properties") or {}
            value = _value_to_float(props.get("value"))
            when = _parse_iso_datetime(props.get("time"))
            if value is None or when is None:
                continue
            out.append((when, value))
        return out


def _iso_instant(moment: datetime) -> str:
    """Format a tz-aware datetime as an RFC 3339 instant for an OGC datetime range."""
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _retry_after_seconds(resp, attempt: int) -> float:
    """Delay before retrying a 429: Retry-After if given, else exponential."""
    retry_after = resp.headers.get("Retry-After")
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except (ValueError, TypeError):
            pass
    return _BACKOFF_BASE_SECONDS * (2 ** attempt)


def _is_newer(new_dt: datetime | None, old_dt: datetime | None) -> bool:
    """True if ``new_dt`` is strictly newer than ``old_dt`` (None = oldest)."""
    if new_dt is None:
        return False
    if old_dt is None:
        return True
    return new_dt > old_dt


# --------------------------------------------------------------------------- #
# Backend selection
# --------------------------------------------------------------------------- #
def build_client(
    hass: HomeAssistant, *, backend: str, api_key: str | None = None
) -> UsgsClient:
    """Construct the client for the configured backend.

    Defaults to the legacy backend for any value other than the modern one, so
    an unknown/missing setting can never accidentally route an entry onto the
    alpha API.
    """
    if backend == BACKEND_MODERN:
        return ModernClient(hass, api_key=api_key)
    return LegacyClient(hass, api_key=api_key)
