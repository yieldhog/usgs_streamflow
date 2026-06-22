"""Test support: minimal Home Assistant / aiohttp / voluptuous stubs.

The integration imports a small, stable slice of Home Assistant. Rather than
install the full ``homeassistant`` package (and a matching HA version) just to
exercise pure logic — request building, response parsing, offline detection,
backend selection, sensor coverage — we register lightweight stand-ins in
``sys.modules`` before the integration is imported.

``install_stubs()`` is idempotent and is invoked on import (and again from
``tests/__init__.py``), so any test module that imports the integration package
gets a working environment. It also puts the repo root on ``sys.path`` so
``custom_components.usgs_streamflow`` is importable as a namespace package.

These are deliberately *logic* tests. Full Home Assistant integration tests
(entity lifecycle, config-entry setup) would use
``pytest-homeassistant-custom-component`` and are a future addition.
"""
from __future__ import annotations

import os
import sys
import types
from dataclasses import dataclass
from datetime import datetime, timezone

# Mutable state the stubs read, so tests can control "now" and the HTTP session.
_state: dict = {
    "now": datetime(2026, 6, 11, 4, 0, 0, tzinfo=timezone.utc),
    "session": None,
}


def set_now(dt: datetime) -> None:
    """Set the value returned by the stubbed ``dt_util.utcnow()``."""
    _state["now"] = dt


def set_session(session) -> None:
    """Set the session returned by the stubbed ``async_get_clientsession()``."""
    _state["session"] = session


# --------------------------------------------------------------------------- #
# Fakes for HTTP
# --------------------------------------------------------------------------- #
class FakeResp:
    """An aiohttp-like response usable as an async context manager."""

    def __init__(self, status: int = 200, json_data=None, headers=None) -> None:
        self.status = status
        self._json = json_data
        self.headers = headers or {}

    async def json(self, content_type=None):
        return self._json

    async def text(self):
        return self._json if isinstance(self._json, str) else ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Records calls and returns queued responses for .request/.get."""

    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def request(self, method, url, params=None, data=None, headers=None, timeout=None):
        self.calls.append(
            {"method": method, "url": url, "params": params, "data": data, "headers": headers}
        )
        return self.responses.pop(0)

    def get(self, url, params=None, timeout=None):
        self.calls.append({"method": "GET", "url": url, "params": params})
        return self.responses.pop(0)


def patch_no_sleep(client_module):
    """Replace ``asyncio`` in the client module with a non-sleeping fake.

    Returns a list that records every delay passed to ``asyncio.sleep`` so 429
    backoff can be asserted without real waiting.
    """
    slept: list[float] = []

    async def _fast_sleep(delay):
        slept.append(delay)

    client_module.asyncio = types.SimpleNamespace(sleep=_fast_sleep)
    return slept


# --------------------------------------------------------------------------- #
# Stub installation
# --------------------------------------------------------------------------- #
class _AttrMeta(type):
    """Metaclass so ``Klass.ANYTHING`` returns the attribute name as a string."""

    def __getattr__(cls, name):
        return name


class _Attr(metaclass=_AttrMeta):
    """Enum-like stand-in; also instantiable as a selector/config object."""

    def __init__(self, *args, **kwargs) -> None:
        pass


class _VMarker:
    """Stand-in for vol.Optional/vol.Required that remembers its key."""

    def __init__(self, key, default=None, **kwargs) -> None:
        self.key = key
        self.default = default


class _VSchema:
    """Stand-in for vol.Schema that exposes the field mapping for inspection."""

    def __init__(self, fields=None) -> None:
        self.fields = fields or {}


class _ConfigFlow:
    def __init_subclass__(cls, **kwargs):  # accepts domain=...
        super().__init_subclass__()


class _OptionsFlow:
    def async_create_entry(self, *, title, data):
        return {"type": "create", "title": title, "data": data}

    def async_show_form(self, *, step_id, data_schema=None, **kwargs):
        return {"type": "form", "step_id": step_id, "schema": data_schema, **kwargs}


@dataclass(frozen=True, kw_only=True)
class _SensorEntityDescription:
    key: str = ""
    name: str | None = None
    native_unit_of_measurement: object = None
    icon: str | None = None
    device_class: object = None
    state_class: object = None
    suggested_display_precision: int | None = None


class _Coordinatorish:
    data = None

    def __init__(self, *args, **kwargs) -> None:
        self.hass = args[0] if args else kwargs.get("hass")

    def __class_getitem__(cls, item):
        return cls

    def async_add_listener(self, update_callback, context=None):
        return lambda: None

    def async_set_updated_data(self, data):
        self.data = data

    async def async_refresh(self):
        return None


class _Store:
    """Minimal homeassistant.helpers.storage.Store stand-in (in-memory)."""

    def __init__(self, *args, **kwargs) -> None:
        self._data = None

    async def async_load(self):
        return self._data

    async def async_save(self, data) -> None:
        self._data = data


_INSTALLED = False


def install_stubs() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    def mod(name, **attrs):
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        sys.modules[name] = m
        return m

    # aiohttp
    mod("aiohttp", ClientTimeout=type("ClientTimeout", (), {"__init__": lambda s, *a, **k: None}))

    # homeassistant.*
    ha = mod("homeassistant")
    mod("homeassistant.core", HomeAssistant=type("HomeAssistant", (), {}), callback=lambda f: f)

    def _parse_datetime(value):
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None

    util = mod("homeassistant.util")
    util.dt = mod(
        "homeassistant.util.dt",
        utcnow=lambda: _state["now"],
        UTC=timezone.utc,
        parse_datetime=_parse_datetime,
    )

    helpers = mod("homeassistant.helpers")
    helpers.aiohttp_client = mod(
        "homeassistant.helpers.aiohttp_client",
        async_get_clientsession=lambda hass: _state["session"],
    )
    helpers.update_coordinator = mod(
        "homeassistant.helpers.update_coordinator",
        DataUpdateCoordinator=_Coordinatorish,
        CoordinatorEntity=_Coordinatorish,
        UpdateFailed=type("UpdateFailed", (Exception,), {}),
    )
    helpers.device_registry = mod(
        "homeassistant.helpers.device_registry",
        DeviceEntryType=_Attr,
        DeviceInfo=lambda *a, **k: dict(**k),
    )
    helpers.entity_platform = mod(
        "homeassistant.helpers.entity_platform", AddEntitiesCallback=object
    )
    helpers.storage = mod("homeassistant.helpers.storage", Store=_Store)
    selector_names = [
        "BooleanSelector",
        "NumberSelector", "NumberSelectorConfig", "NumberSelectorMode",
        "SelectOptionDict", "SelectSelector", "SelectSelectorConfig",
        "SelectSelectorMode", "TextSelector", "TextSelectorConfig", "TextSelectorType",
    ]
    helpers.selector = mod(
        "homeassistant.helpers.selector", **{n: _Attr for n in selector_names}
    )

    ha.config_entries = mod(
        "homeassistant.config_entries",
        ConfigFlow=_ConfigFlow,
        OptionsFlow=_OptionsFlow,
        ConfigEntry=type("ConfigEntry", (), {}),
        FlowResult=dict,
    )

    mod(
        "homeassistant.const",
        DEGREE="°",
        PERCENTAGE="%",
        UnitOfLength=_Attr,
        UnitOfTemperature=_Attr,
        UnitOfSpeed=_Attr,
    )
    mod(
        "homeassistant.components.sensor",
        SensorDeviceClass=_Attr,
        SensorEntity=object,
        SensorEntityDescription=_SensorEntityDescription,
        SensorStateClass=_Attr,
    )

    # voluptuous
    mod("voluptuous", Schema=_VSchema, Optional=_VMarker,
        Required=lambda key, **kw: _VMarker(key, **kw))

    # Quiet the integration's own warnings (e.g. DEMO_KEY fallback) in test output.
    import logging
    logging.getLogger("custom_components.usgs_streamflow").setLevel(logging.CRITICAL)

    _INSTALLED = True


install_stubs()
