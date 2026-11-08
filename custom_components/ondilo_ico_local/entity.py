"""Shared entity behaviour and descriptions for the Ondilo ICO integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntityDescription
from homeassistant.components.sensor import SensorEntityDescription
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ICO_MODEL, MANUFACTURER, SHORT_ID_LENGTH
from .coordinator import IcoDevice, OndiloIcoCoordinator


@dataclass(frozen=True, kw_only=True)
class OndiloIcoSensorEntityDescription(SensorEntityDescription):
    """Describes an ICO sensor, which reads a state key unless told otherwise."""

    value_fn: Callable[[IcoDevice], Any] | None = None


@dataclass(frozen=True, kw_only=True)
class OndiloIcoBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes an ICO binary sensor."""

    is_on_fn: Callable[[IcoDevice], bool | None]


@callback
def async_new_devices(
    coordinator: OndiloIcoCoordinator, seen: set[str]
) -> list[IcoDevice]:
    """Return the probes the bridge has not reported before, and remember them."""
    if coordinator.data is None:
        return []
    new_devices = [
        device
        for uuid, device in coordinator.data.items()
        if uuid not in seen
    ]
    seen.update(device.uuid for device in new_devices)
    return new_devices


class OndiloIcoEntity(CoordinatorEntity[OndiloIcoCoordinator]):
    """Base class for the entities of one ICO probe."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: OndiloIcoCoordinator, device: IcoDevice) -> None:
        """Initialize the entity and the device it belongs to."""
        super().__init__(coordinator)
        self._uuid = device.uuid
        self._attr_device_info = DeviceInfo(
            configuration_url=coordinator.source.configuration_url,
            identifiers={(DOMAIN, device.uuid)},
            manufacturer=MANUFACTURER,
            model=ICO_MODEL,
            serial_number=device.uuid,
            sw_version=device.firmware,
            translation_key="ico",
            translation_placeholders={"short_id": device.uuid[-SHORT_ID_LENGTH:]},
            via_device_id=coordinator.source_device_id,
        )

    @property
    def ico_device(self) -> IcoDevice | None:
        """Return the freshest report of this probe, if the bridge still has one."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._uuid)

    @property
    def available(self) -> bool:
        """Return whether the bridge still reports this probe."""
        return super().available and self.ico_device is not None
