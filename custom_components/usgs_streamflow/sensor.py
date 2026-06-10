"""Sensor platform for USGS Streamflow."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfLength, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_ENABLED_PARAMETERS,
    CONF_SITE_ID,
    CONF_SITE_NAME,
    DERIVED_RATE_WINDOW_MINUTES,
    DOMAIN,
    MIN_RATE_SPAN_MINUTES,
    PARAM_DISCHARGE,
    PARAM_DISSOLVED_OXYGEN,
    PARAM_DO_PCT_SAT,
    PARAM_GAUGE_HEIGHT,
    PARAM_GW_DEPTH,
    PARAM_PH,
    PARAM_PRECIPITATION,
    PARAM_SPECIFIC_CONDUCTANCE,
    PARAM_TURBIDITY,
    PARAM_WATER_TEMP,
    SUPPORTED_PARAMETERS,
)
from .coordinator import USGSStreamflowCoordinator

# CFS (cubic feet per second) is not yet a named HA unit constant; use the
# canonical string directly.  HA will store/display it correctly; unit
# conversion to metric is not available for this unit.
_UNIT_CFS = "ft³/s"

# Units for the per-hour rate-of-change sensors.  These are custom (no metric
# conversion); they mirror the base parameter's unit with a "/h" suffix.
_UNIT_FEET_PER_HOUR = "ft/h"
_UNIT_CFS_PER_HOUR = "ft³/s/h"

_TREND_OPTIONS = ["rising", "falling", "steady"]
_TREND_ICONS = {
    "rising": "mdi:trending-up",
    "falling": "mdi:trending-down",
    "steady": "mdi:trending-neutral",
}


@dataclass(frozen=True, kw_only=True)
class DerivedSensorConfig:
    """Configuration for a parameter's rate-of-change and trend sensors.

    trend_abs_deadband / trend_rel_deadband define the dead zone within which a
    rate is reported as "steady".  The effective deadband is
    ``max(trend_abs_deadband, trend_rel_deadband * abs(latest_value))`` per hour,
    so an absolute floor handles near-zero readings while the relative term lets
    a single config scale across very small and very large rivers.
    """

    param_cd: str
    rate_key: str
    rate_name: str
    rate_unit: str
    rate_icon: str
    rate_precision: int
    trend_key: str
    trend_name: str
    trend_abs_deadband: float
    trend_rel_deadband: float


# Rate/trend are created only for level- and flow-type parameters, where
# rate-of-rise and direction are the canonical, broadly useful signal (flood
# rise, flow change, well drawdown).  Adding them to water-quality parameters
# would multiply entity count for little gain.  Creation still respects the
# station-reports gating and the user's enabled-parameter selection, so a site
# that does not serve one of these gets no derived sensors for it.
DERIVED_SENSORS: tuple[DerivedSensorConfig, ...] = (
    DerivedSensorConfig(
        param_cd=PARAM_GAUGE_HEIGHT,
        rate_key="gauge_height_rate",
        rate_name="Gauge Height Rate",
        rate_unit=_UNIT_FEET_PER_HOUR,
        rate_icon="mdi:waves-arrow-up",
        rate_precision=3,
        trend_key="gauge_height_trend",
        trend_name="Gauge Height Trend",
        trend_abs_deadband=0.02,   # ft/h
        trend_rel_deadband=0.0,    # datum-relative; absolute only
    ),
    DerivedSensorConfig(
        param_cd=PARAM_DISCHARGE,
        rate_key="discharge_rate",
        rate_name="Discharge Rate",
        rate_unit=_UNIT_CFS_PER_HOUR,
        rate_icon="mdi:waves-arrow-right",
        rate_precision=2,
        trend_key="discharge_trend",
        trend_name="Discharge Trend",
        trend_abs_deadband=0.5,    # cfs/h floor for tiny streams
        trend_rel_deadband=0.03,   # 3%/h scales to large rivers
    ),
    DerivedSensorConfig(
        param_cd=PARAM_GW_DEPTH,
        rate_key="gw_depth_rate",
        rate_name="Depth to Water Level Rate",
        rate_unit=_UNIT_FEET_PER_HOUR,
        rate_icon="mdi:arrow-expand-vertical",
        rate_precision=3,
        trend_key="gw_depth_trend",
        trend_name="Depth to Water Level Trend",
        trend_abs_deadband=0.02,   # ft/h
        trend_rel_deadband=0.0,
    ),
)


def _compute_rate(points: list[tuple[datetime, float]]) -> float | None:
    """Per-hour rate of change across a window of (time, value) points.

    Returns None when there are too few points or they span less than
    MIN_RATE_SPAN_MINUTES (guarding against huge values from a near-zero dt).
    """
    if len(points) < 2:
        return None
    t0, v0 = points[0]
    t1, v1 = points[-1]
    span_hours = (t1 - t0).total_seconds() / 3600
    if span_hours < (MIN_RATE_SPAN_MINUTES / 60):
        return None
    return (v1 - v0) / span_hours


def _compute_trend(
    points: list[tuple[datetime, float]],
    abs_deadband: float,
    rel_deadband: float,
) -> str | None:
    """Classify the rate as rising/falling/steady, or None if undetermined."""
    rate = _compute_rate(points)
    if rate is None:
        return None
    latest_value = points[-1][1]
    deadband = max(abs_deadband, rel_deadband * abs(latest_value))
    if rate > deadband:
        return "rising"
    if rate < -deadband:
        return "falling"
    return "steady"


def _param_available(
    coordinator: "USGSStreamflowCoordinator", param_cd: str
) -> bool:
    """Shared availability rule for parameter-linked entities (see USGSStreamSensor)."""
    if coordinator.data is None:
        return False
    if coordinator.known_params and param_cd not in coordinator.known_params:
        return False
    if coordinator.data.station_offline:
        return False
    return True



@dataclass(frozen=True, kw_only=True)
class USGSSensorDescription(SensorEntityDescription):
    """Extend SensorEntityDescription with the USGS parameter code."""
    param_cd: str


SENSOR_DESCRIPTIONS: tuple[USGSSensorDescription, ...] = (
    USGSSensorDescription(
        key="gauge_height",
        param_cd=PARAM_GAUGE_HEIGHT,
        name="Gauge Height",
        native_unit_of_measurement=UnitOfLength.FEET,
        icon="mdi:waves",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    USGSSensorDescription(
        key="discharge",
        param_cd=PARAM_DISCHARGE,
        name="Discharge",
        native_unit_of_measurement=_UNIT_CFS,
        icon="mdi:waves-arrow-right",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    USGSSensorDescription(
        key="water_temp",
        param_cd=PARAM_WATER_TEMP,
        name="Water Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    USGSSensorDescription(
        key="specific_conductance",
        param_cd=PARAM_SPECIFIC_CONDUCTANCE,
        name="Specific Conductance",
        native_unit_of_measurement="µS/cm",
        icon="mdi:flash",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    USGSSensorDescription(
        key="dissolved_oxygen",
        param_cd=PARAM_DISSOLVED_OXYGEN,
        name="Dissolved Oxygen",
        native_unit_of_measurement="mg/L",
        icon="mdi:gas-cylinder",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    USGSSensorDescription(
        key="do_pct_saturation",
        param_cd=PARAM_DO_PCT_SAT,
        name="Dissolved Oxygen (% Saturation)",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:percent",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    USGSSensorDescription(
        key="ph",
        param_cd=PARAM_PH,
        name="pH",
        device_class=SensorDeviceClass.PH,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    USGSSensorDescription(
        key="turbidity",
        param_cd=PARAM_TURBIDITY,
        name="Turbidity",
        native_unit_of_measurement="FNU",
        icon="mdi:water-opacity",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    USGSSensorDescription(
        key="precipitation",
        param_cd=PARAM_PRECIPITATION,
        name="Precipitation",
        # USGS 00045 is incremental precip per reporting interval; exposed as
        # a plain measurement in inches, not an accumulating total.
        native_unit_of_measurement="in",
        icon="mdi:weather-rainy",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    USGSSensorDescription(
        key="gw_depth",
        param_cd=PARAM_GW_DEPTH,
        name="Depth to Water Level",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.FEET,
        icon="mdi:water-well",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
)


def _make_device_info(site_id: str, site_name: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, site_id)},
        name=site_name,
        manufacturer="USGS",
        model="NWIS Monitoring Location",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=(
            f"https://waterdata.usgs.gov/monitoring-location/{site_id}/"
        ),
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up USGS Streamflow sensors for a config entry."""
    coordinator: USGSStreamflowCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = [
        # Station Status is always present so users can see online/offline
        # state even when the gauge is seasonally decommissioned.
        USGSStationStatusSensor(coordinator, entry),
    ]

    # Determine which measurement sensors to register.
    #
    # After async_config_entry_first_refresh() (called in __init__.py before
    # we arrive here), coordinator.known_params is populated if the station was
    # online during that first fetch.  We use it to create only the sensors the
    # station actually has.
    #
    # If known_params is still empty the station was offline at startup (e.g.,
    # seasonal shutdown).  In that case we register all three sensors as a
    # fallback so they appear when the station comes back online; the
    # `available` property will correctly mark any unsupported params as
    # Unavailable once the station is reachable and known_params is populated.
    params_to_create = coordinator.known_params or {
        desc.param_cd for desc in SENSOR_DESCRIPTIONS
    }

    # Honor the user's parameter selection from the options flow.  When unset
    # (the default), every supported parameter is eligible; the
    # station-reports gating above still limits creation to params that the
    # site actually serves.
    enabled = set(
        entry.options.get(CONF_ENABLED_PARAMETERS) or SUPPORTED_PARAMETERS.keys()
    )

    for description in SENSOR_DESCRIPTIONS:
        if (
            description.param_cd in params_to_create
            and description.param_cd in enabled
        ):
            entities.append(USGSStreamSensor(coordinator, entry, description))

    # Derived rate-of-change + trend sensors for level/flow parameters,
    # gated by the same station-reports and enabled-parameter rules.
    for cfg in DERIVED_SENSORS:
        if cfg.param_cd in params_to_create and cfg.param_cd in enabled:
            entities.append(USGSRateSensor(coordinator, entry, cfg))
            entities.append(USGSTrendSensor(coordinator, entry, cfg))

    async_add_entities(entities)


class USGSStationStatusSensor(
    CoordinatorEntity[USGSStreamflowCoordinator], SensorEntity
):
    """Reports whether the station is currently active or seasonally offline."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:gauge"
    _attr_name = "Station Status"

    def __init__(
        self,
        coordinator: USGSStreamflowCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        site_id = entry.data[CONF_SITE_ID]
        site_name = entry.data[CONF_SITE_NAME]
        self._attr_unique_id = f"usgs_{site_id}_status"
        self._attr_device_info = _make_device_info(site_id, site_name)

    @property
    def native_value(self) -> str:
        if self.coordinator.data is None:
            return "Unknown"
        return "Offline" if self.coordinator.data.station_offline else "Active"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            "usgs_site_id": self.coordinator.site_id,
            "usgs_waterdata_url": (
                f"https://waterdata.usgs.gov/monitoring-location/"
                f"{self.coordinator.site_id}/"
            ),
        }
        if self.coordinator.data and self.coordinator.data.offline_reason:
            attrs["offline_reason"] = self.coordinator.data.offline_reason
        return attrs


class USGSStreamSensor(CoordinatorEntity[USGSStreamflowCoordinator], SensorEntity):
    """A single USGS stream measurement sensor (gauge height, discharge, or water temp)."""

    entity_description: USGSSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: USGSStreamflowCoordinator,
        entry: ConfigEntry,
        description: USGSSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        site_id = entry.data[CONF_SITE_ID]
        site_name = entry.data[CONF_SITE_NAME]
        self._attr_unique_id = f"usgs_{site_id}_{description.param_cd}"
        self._attr_device_info = _make_device_info(site_id, site_name)

    @property
    def available(self) -> bool:
        """Mark unavailable when station is offline or param is absent.

        Three distinct states:
        1. known_params is empty — station was offline at startup; all sensors
           are in an indeterminate state until the first successful online fetch.
        2. known_params is populated and this param_cd is in it — station
           confirmed it has this sensor; availability follows online/offline state.
        3. known_params is populated and this param_cd is NOT in it — station
           came back online and confirmed it doesn't have this sensor (only
           possible if we fell through the offline-at-startup fallback path and
           created all three sensors).  Mark permanently unavailable so the user
           can see it and disable/remove it from the entity registry.
        """
        if not super().available:
            return False
        if self.coordinator.data is None:
            return False
        # Case 3: station confirmed online but this param never appeared
        if (
            self.coordinator.known_params
            and self.entity_description.param_cd not in self.coordinator.known_params
        ):
            return False
        # Case 2: station is currently offline (seasonal/stale)
        if self.coordinator.data.station_offline:
            return False
        return True

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.values.get(self.entity_description.param_cd)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {"usgs_site_id": self.coordinator.site_id}
        if self.coordinator.data:
            reading_dt = self.coordinator.data.reading_times.get(
                self.entity_description.param_cd
            )
            if reading_dt:
                attrs["last_reading_time"] = reading_dt.isoformat()
        return attrs


class USGSRateSensor(CoordinatorEntity[USGSStreamflowCoordinator], SensorEntity):
    """Per-hour rate of change for a level/flow parameter.

    Reports `unknown` until at least two distinct observations are buffered
    within the window (a short warm-up after startup or an options reload).
    """

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: USGSStreamflowCoordinator,
        entry: ConfigEntry,
        config: DerivedSensorConfig,
    ) -> None:
        super().__init__(coordinator)
        self._config = config
        site_id = entry.data[CONF_SITE_ID]
        site_name = entry.data[CONF_SITE_NAME]
        self._attr_name = config.rate_name
        self._attr_native_unit_of_measurement = config.rate_unit
        self._attr_icon = config.rate_icon
        self._attr_suggested_display_precision = config.rate_precision
        self._attr_unique_id = f"usgs_{site_id}_{config.param_cd}_rate"
        self._attr_device_info = _make_device_info(site_id, site_name)

    @property
    def available(self) -> bool:
        return super().available and _param_available(
            self.coordinator, self._config.param_cd
        )

    @property
    def native_value(self) -> float | None:
        points = self.coordinator.recent_points(
            self._config.param_cd, DERIVED_RATE_WINDOW_MINUTES
        )
        rate = _compute_rate(points)
        return None if rate is None else round(rate, 4)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        points = self.coordinator.recent_points(
            self._config.param_cd, DERIVED_RATE_WINDOW_MINUTES
        )
        return {
            "usgs_site_id": self.coordinator.site_id,
            "window_minutes": DERIVED_RATE_WINDOW_MINUTES,
            "sample_count": len(points),
        }


class USGSTrendSensor(CoordinatorEntity[USGSStreamflowCoordinator], SensorEntity):
    """Rising / falling / steady direction for a level/flow parameter."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = _TREND_OPTIONS

    def __init__(
        self,
        coordinator: USGSStreamflowCoordinator,
        entry: ConfigEntry,
        config: DerivedSensorConfig,
    ) -> None:
        super().__init__(coordinator)
        self._config = config
        site_id = entry.data[CONF_SITE_ID]
        site_name = entry.data[CONF_SITE_NAME]
        self._attr_name = config.trend_name
        self._attr_unique_id = f"usgs_{site_id}_{config.param_cd}_trend"
        self._attr_device_info = _make_device_info(site_id, site_name)

    @property
    def available(self) -> bool:
        return super().available and _param_available(
            self.coordinator, self._config.param_cd
        )

    @property
    def native_value(self) -> str | None:
        points = self.coordinator.recent_points(
            self._config.param_cd, DERIVED_RATE_WINDOW_MINUTES
        )
        return _compute_trend(
            points,
            self._config.trend_abs_deadband,
            self._config.trend_rel_deadband,
        )

    @property
    def icon(self) -> str:
        return _TREND_ICONS.get(self.native_value, "mdi:trending-neutral")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        points = self.coordinator.recent_points(
            self._config.param_cd, DERIVED_RATE_WINDOW_MINUTES
        )
        rate = _compute_rate(points)
        attrs: dict[str, Any] = {
            "usgs_site_id": self.coordinator.site_id,
            "window_minutes": DERIVED_RATE_WINDOW_MINUTES,
            "sample_count": len(points),
        }
        if rate is not None:
            attrs["rate_per_hour"] = round(rate, 4)
        return attrs
