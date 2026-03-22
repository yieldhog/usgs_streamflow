"""Sensor platform for USGS Streamflow."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfLength, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_SITE_ID,
    CONF_SITE_NAME,
    DOMAIN,
    PARAM_DISCHARGE,
    PARAM_GAUGE_HEIGHT,
    PARAM_WATER_TEMP,
)
from .coordinator import USGSStreamflowCoordinator

# CFS (cubic feet per second) is not yet a named HA unit constant; use the
# canonical string directly.  HA will store/display it correctly; unit
# conversion to metric is not available for this unit.
_UNIT_CFS = "ft³/s"


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
)


def _make_device_info(site_id: str, site_name: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, site_id)},
        name=site_name,
        manufacturer="USGS",
        model="NWIS Stream Gauge",
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
        # Station Status is always present so users see online/offline state
        # even when the gauge is seasonally decommissioned.
        USGSStationStatusSensor(coordinator, entry),
        # All three measurement sensors are always registered.  Each one
        # reports itself as unavailable when (a) the station is offline or
        # (b) the station has never reported that parameter.  Registering
        # them unconditionally is required so they appear correctly after
        # an HA restart when the station happens to be offline at startup —
        # previously the sensors would silently vanish until the next restart
        # after the gauge came back online.
        *(USGSStreamSensor(coordinator, entry, desc) for desc in SENSOR_DESCRIPTIONS),
    ]

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

        Three distinct unavailability cases:
        1. Coordinator has not yet received any data (HA just started).
        2. Station is seasonally decommissioned — shows "Unavailable" with a
           grey chip rather than "Unknown", which is more accurate than a
           station that simply hasn't been polled yet.
        3. The station has successfully reported data before (seen_params is
           non-empty) but has never included this parameter — e.g., a gauge
           with no water temperature sensor.  In this case we surface the
           sensor as permanently unavailable rather than hiding it, so the
           user can see it in the entity list and understand why it has no
           value.
        """
        if not super().available:
            return False
        if self.coordinator.data is None:
            return False
        if self.coordinator.data.station_offline:
            return False
        # If we have seen at least one successful fetch but this param has
        # never appeared, the station simply doesn't support it.
        if (
            self.coordinator.seen_params
            and self.entity_description.param_cd not in self.coordinator.seen_params
        ):
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
