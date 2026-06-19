"""Config flow for USGS Streamflow integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
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

from .client import LegacyClient, SiteHit
from .const import (
    CONF_ENABLED_PARAMETERS,
    CONF_SCAN_INTERVAL,
    CONF_SITE_ID,
    CONF_SITE_NAME,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    MAX_SCAN_INTERVAL_MINUTES,
    MIN_SCAN_INTERVAL_MINUTES,
    SITE_NUMBER_RE,
    SUPPORTED_PARAMETERS,
    normalize_site_number,
)

_LOGGER = logging.getLogger(__name__)

USGS_WATER_DATA_URL = "https://waterdata.usgs.gov/nwis/rt"


def _format_site_label(site: SiteHit) -> str:
    """Build the selector label for a search hit (name, number, optional state)."""
    label = f"{site.site_name} (#{site.site_id})"
    if site.state:
        label += f"  [{site.state}]"
    return label


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
        self._sites: list[SiteHit] = []

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
            elif not SITE_NUMBER_RE.match(
                normalize_site_number(search_term)
            ) and not state_code:
                # Name searches without a state code return thousands of results
                # and can cause the API response to time out or be unparseable.
                errors["state_code"] = "state_required_for_name_search"
            else:
                try:
                    client = LegacyClient(self.hass)
                    sites = await client.search_sites(search_term, state=state_code)
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
            site = next((s for s in self._sites if s.site_id == site_id), None)
            if site:
                await self.async_set_unique_id(f"usgs_{site_id}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=site.site_name,
                    data={
                        CONF_SITE_ID: site_id,
                        CONF_SITE_NAME: site.site_name,
                    },
                )
            errors["base"] = "unknown"

        options = [
            {"value": s.site_id, "label": _format_site_label(s)} for s in self._sites
        ]

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
