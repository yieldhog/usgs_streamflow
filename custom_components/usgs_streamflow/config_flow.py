"""Config flow for USGS Streamflow integration."""
from __future__ import annotations

import logging
import re
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_ENABLED_PARAMETERS,
    CONF_SCAN_INTERVAL,
    CONF_SITE_ID,
    CONF_SITE_NAME,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    MAX_SCAN_INTERVAL_MINUTES,
    MIN_SCAN_INTERVAL_MINUTES,
    SUPPORTED_PARAMETERS,
    USGS_SITE_URL,
)

_LOGGER = logging.getLogger(__name__)

# USGS site numbers are 6–15 digit strings (zero-padded, varies by region)
_SITE_NUMBER_RE = re.compile(r"^\d{6,15}$")
_AGENCY_PREFIX_RE = re.compile(r"^[A-Za-z]+-")


def _normalize_site_number(raw: str) -> str:
    """Strip whitespace and an optional agency prefix (e.g. 'USGS-') from input.

    USGS site numbers are 6-15 digits: surface-water sites are typically 8,
    while groundwater, combined-sewer, and other lat/long-based IDs run to 15.
    WDFN monitoring-location pages display these IDs with a 'USGS-' prefix, so
    we strip it before deciding whether the input is a site number.
    """
    return _AGENCY_PREFIX_RE.sub("", raw.strip())

USGS_WATER_DATA_URL = "https://waterdata.usgs.gov/nwis/rt"

# USGS RDB responses return numeric FIPS state codes (e.g. "08"), not
# two-letter abbreviations.  This mapping converts them for display.
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


async def _search_usgs_sites(hass, search_term: str, state_code: str) -> list[dict]:
    """Query USGS NWIS site service. Supports name search or direct site number lookup."""
    session = async_get_clientsession(hass)
    params: dict[str, str] = {
        "format": "rdb",
        "siteStatus": "all",    # include seasonal — user should see all options
        "hasDataTypeCd": "iv",  # only sites with instantaneous values (the data we poll)
    }

    # Detect if user pasted a site number directly (optionally "USGS-" prefixed)
    candidate = _normalize_site_number(search_term)
    if _SITE_NUMBER_RE.match(candidate):
        params["sites"] = candidate
    else:
        params["siteName"] = search_term.strip()

    if state_code.strip():
        params["stateCd"] = state_code.strip().upper()

    timeout = aiohttp.ClientTimeout(total=30)
    async with session.get(USGS_SITE_URL, params=params, timeout=timeout) as resp:
        # The NWIS site service returns 404 when *no sites match* the query —
        # this is documented behavior, not a transport error. Treat it as an
        # empty result set so the flow shows "no sites found" rather than a
        # spurious connection error.
        if resp.status == 404:
            return []
        if resp.status != 200:
            raise ConnectionError(f"USGS site search returned HTTP {resp.status}")
        text = await resp.text()

    return _parse_rdb_sites(text)


def _parse_rdb_sites(rdb_text: str) -> list[dict]:
    """Parse USGS RDB (tab-delimited) site response into a list of dicts."""
    sites: list[dict] = []
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
        # USGS RDB returns a numeric FIPS code in state_cd (e.g. "08"), not
        # a two-letter abbreviation.  Convert it for a readable label.
        fips_cd = row.get("state_cd", "").strip()
        state_abbrev = _FIPS_TO_STATE.get(fips_cd, fips_cd)  # fallback: raw value

        if not site_no or not station_nm:
            continue

        label = f"{station_nm} (#{site_no})"
        if state_abbrev:
            label += f"  [{state_abbrev}]"

        sites.append(
            {
                "site_id": site_no,
                "name": station_nm,
                "state": state_abbrev,
                "label": label,
            }
        )

    return sites[:50]  # cap to keep selector manageable


class USGSStreamflowConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for USGS Streamflow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "USGSStreamflowOptionsFlow":
        """Return the options flow handler."""
        return USGSStreamflowOptionsFlow()

    def __init__(self) -> None:
        self._sites: list[dict] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 1 — Search for a gauge by name or site number."""
        errors: dict[str, str] = {}

        if user_input is not None:
            search_term = user_input.get("search_term", "").strip()
            state_code = user_input.get("state_code", "").strip()

            if not search_term:
                errors["search_term"] = "search_required"
            elif not _SITE_NUMBER_RE.match(
                _normalize_site_number(search_term)
            ) and not state_code:
                # Name searches without a state code return thousands of results
                # and can cause the API response to time out or be unparseable.
                errors["state_code"] = "state_required_for_name_search"
            else:
                try:
                    sites = await _search_usgs_sites(self.hass, search_term, state_code)
                except Exception as err:
                    _LOGGER.exception("Error contacting USGS site search API: %s", err)
                    errors["base"] = "cannot_connect"
                else:
                    if not sites:
                        errors["base"] = "no_sites_found"
                    else:
                        self._sites = sites
                        return await self.async_step_select_site()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("search_term"): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                    vol.Optional("state_code", default=""): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                }
            ),
            errors=errors,
            description_placeholders={"usgs_url": USGS_WATER_DATA_URL},
        )

    async def async_step_select_site(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 2 — Pick a station from the results list."""
        errors: dict[str, str] = {}

        if user_input is not None:
            site_id = user_input["site_id"]
            site = next((s for s in self._sites if s["site_id"] == site_id), None)
            if site:
                await self.async_set_unique_id(f"usgs_{site_id}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=site["name"],
                    data={
                        CONF_SITE_ID: site_id,
                        CONF_SITE_NAME: site["name"],
                    },
                )
            errors["base"] = "unknown"

        options = [{"value": s["site_id"], "label": s["label"]} for s in self._sites]

        return self.async_show_form(
            step_id="select_site",
            data_schema=vol.Schema(
                {
                    vol.Required("site_id"): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
            errors=errors,
            description_placeholders={"count": str(len(self._sites))},
        )


class USGSStreamflowOptionsFlow(config_entries.OptionsFlow):
    """Options: poll interval and which parameters create sensors."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Manage the integration options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=options.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL_MINUTES,
                        max=MAX_SCAN_INTERVAL_MINUTES,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="min",
                    )
                ),
                vol.Optional(
                    CONF_ENABLED_PARAMETERS,
                    default=options.get(
                        CONF_ENABLED_PARAMETERS, list(SUPPORTED_PARAMETERS)
                    ),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=code, label=label)
                            for code, label in SUPPORTED_PARAMETERS.items()
                        ],
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
