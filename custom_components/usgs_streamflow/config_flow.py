"""Config flow for USGS Streamflow integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
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

from .client import (
    LegacyClient,
    ModernClient,
    SiteHit,
    UsgsClientError,
    UsgsHttpStatusError,
)
from .const import (
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
    DOMAIN,
    MAX_SCAN_INTERVAL_MINUTES,
    MIN_SCAN_INTERVAL_MINUTES,
    SITE_NUMBER_RE,
    SUPPORTED_PARAMETERS,
    normalize_site_number,
)

_LOGGER = logging.getLogger(__name__)

USGS_WATER_DATA_URL = "https://waterdata.usgs.gov/nwis/rt"
# Where users obtain an api.data.gov key.  Passed as a description placeholder
# (hassfest forbids literal URLs in translation strings).
API_SIGNUP_URL = "https://api.data.gov/signup/"


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

    def _existing_api_key(self) -> str:
        """Return an api.data.gov key from any existing entry, for pre-fill.

        Lets a user who already configured one gauge avoid re-typing the key
        for each additional gauge.
        """
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            key = entry.options.get(CONF_API_KEY) or entry.data.get(CONF_API_KEY)
            if key:
                return key
        return ""

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
            api_key = user_input.get(CONF_API_KEY, "").strip()
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
                    # Key lives in options so the options flow can edit it later.
                    options={CONF_API_KEY: api_key},
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
                    ),
                    vol.Optional(
                        CONF_API_KEY, default=self._existing_api_key()
                    ): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "count": str(len(self._sites)),
                "signup_url": API_SIGNUP_URL,
            },
        )

    # -- reauth (Modern backend api.data.gov key) -------------------------- #
    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.FlowResult:
        """Start reauth — the Modern backend rejected the api.data.gov key."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Collect and validate a new api.data.gov key, then reload the entry."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input.get(CONF_API_KEY, "").strip()
            client = ModernClient(self.hass, api_key=api_key)
            try:
                # A light authenticated call: rejected keys raise 401/403.
                await client.get_site_parameters(entry.data[CONF_SITE_ID])
            except UsgsHttpStatusError as err:
                errors["base"] = (
                    "invalid_auth" if err.status in (401, 403) else "cannot_connect"
                )
            except UsgsClientError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    options={**entry.options, CONF_API_KEY: api_key},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
            description_placeholders={"signup_url": API_SIGNUP_URL},
        )


class USGSStreamflowOptionsFlow(config_entries.OptionsFlow):
    """Options: poll interval and which parameters create sensors."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Manage the integration options."""
        if user_input is not None:
            # Merge over existing options so a field not shown in this form
            # (e.g. the advanced backend selector when Advanced Mode is off) is
            # preserved rather than dropped.
            new_options = {**self.config_entry.options, **user_input}
            return self.async_create_entry(title="", data=new_options)

        options = self.config_entry.options
        fields: dict[Any, Any] = {
            vol.Optional(
                CONF_API_KEY,
                default=options.get(CONF_API_KEY, ""),
            ): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
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
            vol.Optional(
                CONF_ENABLE_STATS,
                default=options.get(CONF_ENABLE_STATS, False),
            ): BooleanSelector(),
        }

        # API backend is an advanced choice (the modern API is still alpha), so
        # only expose it when Home Assistant Advanced Mode is enabled.
        if self.show_advanced_options:
            fields[
                vol.Optional(
                    CONF_BACKEND,
                    default=options.get(CONF_BACKEND, BACKEND_LEGACY),
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(
                            value=BACKEND_LEGACY,
                            label="Legacy — WaterServices (current, stable)",
                        ),
                        SelectOptionDict(
                            value=BACKEND_MODERN,
                            label="Modern — Water Data OGC API (beta)",
                        ),
                    ],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(fields),
            description_placeholders={"signup_url": API_SIGNUP_URL},
        )
