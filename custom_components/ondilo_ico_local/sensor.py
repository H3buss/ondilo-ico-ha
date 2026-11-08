"""Sensors reading the measurement keys of an ICO probe."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfRatio,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .coordinator import IcoDevice, OndiloIcoConfigEntry, OndiloIcoCoordinator
from .entity import (
    OndiloIcoEntity,
    OndiloIcoSensorEntityDescription,
    async_new_devices,
)

PARALLEL_UPDATES = 0


def _arrival_time(value: Any) -> datetime | None:
    """Turn the bridge arrival time into the aware datetime a TIMESTAMP wants."""
    parsed = dt_util.parse_datetime(value) if isinstance(value, str) else None
    if parsed is None:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


SENSOR_DESCRIPTIONS: tuple[OndiloIcoSensorEntityDescription, ...] = (
    OndiloIcoSensorEntityDescription(
        key="temperature",
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    OndiloIcoSensorEntityDescription(
        key="ph",
        translation_key="ph",
        # An electrode potential, not a pH. The firmware says so itself: the report
        # template formats the field as "ph":%d (0x0158aa in fw 3.1.0), and the
        # probe's own console prints "Measure = %d.%d mV" (0x0159c0) under a guard
        # reading "Measure is out of range [-500; 500]" (0x015996). A glass electrode
        # gives roughly 59 mV per pH unit around a zero point near pH 7, which is
        # exactly that window. Turning millivolts into pH needs the offset that the
        # probe's calibration recorded; the report does not carry it and the firmware
        # holds no calibration string at all, so the conversion happens upstream and
        # this entity publishes what the probe actually measured.
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.MILLIVOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    OndiloIcoSensorEntityDescription(
        key="orp",
        translation_key="orp",
        native_unit_of_measurement=UnitOfElectricPotential.MILLIVOLT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    OndiloIcoSensorEntityDescription(
        key="conductivity",
        translation_key="conductivity",
        # No unit, on purpose, until one is earned. The probe reports two electrode
        # readings, cond+ and cond-, each formatted as %d and guarded by "Measure is
        # out of range [50; 65535]" (0x0156d0 in fw 3.1.0); the firmware never names a
        # unit for them and we publish the difference. A field report puts that
        # reading in doubt: 1233 published here against 253 ppm of TDS upstream, and
        # no standard conversion factor explains that pair, so either the scale or the
        # formula is wrong. Microsiemens per centimetre would be a guess with a unit
        # sign on it, which is the one thing a number in Home Assistant must not be.
        state_class=SensorStateClass.MEASUREMENT,
    ),
    OndiloIcoSensorEntityDescription(
        key="battery",
        translation_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=UnitOfRatio.PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    OndiloIcoSensorEntityDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.MILLIVOLT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    OndiloIcoSensorEntityDescription(
        key="battery_temperature",
        translation_key="battery_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    OndiloIcoSensorEntityDescription(
        key="wifi_rssi",
        translation_key="wifi_rssi",
        entity_category=EntityCategory.DIAGNOSTIC,
        # Without a device class and without a unit on purpose: the probe sends a
        # bare vendor number (59 in the report this integration was written
        # against), and nobody has confirmed it is dBm.
    ),
    OndiloIcoSensorEntityDescription(
        key="measure_time",
        translation_key="last_report",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda device: _arrival_time(device.value("measure_time")),
    ),
    OndiloIcoSensorEntityDescription(
        key="mode",
        translation_key="bridge_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    OndiloIcoSensorEntityDescription(
        key="retry",
        translation_key="retry",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    OndiloIcoSensorEntityDescription(
        key="measure_count",
        translation_key="measure_count",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    OndiloIcoSensorEntityDescription(
        key="measure_seq",
        translation_key="measure_seq",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    # device_clock and report_clock stay plain numbers on purpose: they are the
    # counters of the probe itself (470953801 in the captured report), which no
    # longer match the wall clock once the probe has been running a while.
    OndiloIcoSensorEntityDescription(
        key="device_clock",
        translation_key="device_clock",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    OndiloIcoSensorEntityDescription(
        key="report_clock",
        translation_key="report_clock",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    OndiloIcoSensorEntityDescription(
        key="ico_uuid",
        translation_key="ico_uuid",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    OndiloIcoSensorEntityDescription(
        key="fw_version",
        translation_key="firmware_build",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    OndiloIcoSensorEntityDescription(
        key="firmware",
        translation_key="firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OndiloIcoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors of the ICO probes the bridge already knows about."""
    coordinator = entry.runtime_data
    seen: set[str] = set()

    @callback
    def async_add_new_devices() -> None:
        """Add the sensors of the probes that appeared since the last poll."""
        new_devices = async_new_devices(coordinator, seen)
        if not new_devices:
            return
        async_add_entities(
            OndiloIcoSensor(coordinator, device, description)
            for device in new_devices
            for description in SENSOR_DESCRIPTIONS
        )

    async_add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(async_add_new_devices))


class OndiloIcoSensor(OndiloIcoEntity, SensorEntity):
    """One measurement key of one ICO probe."""

    entity_description: OndiloIcoSensorEntityDescription

    def __init__(
        self,
        coordinator: OndiloIcoCoordinator,
        device: IcoDevice,
        description: OndiloIcoSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device)
        self.entity_description = description
        self._attr_unique_id = f"{device.uuid}-{description.key}"

    @property
    @override
    def native_value(self) -> StateType | datetime:
        """Return the last value of this key, None meaning 'the bridge is silent'."""
        if (device := self.ico_device) is None:
            return None
        if (value_fn := self.entity_description.value_fn) is not None:
            return value_fn(device)
        return device.value(self.entity_description.key)
