"""Binary sensors reporting the state of one ICO probe."""

from __future__ import annotations

from typing import override

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import IcoDevice, OndiloIcoConfigEntry, OndiloIcoCoordinator
from .entity import (
    OndiloIcoBinarySensorEntityDescription,
    OndiloIcoEntity,
    async_new_devices,
)

PARALLEL_UPDATES = 0


def _usb_power(device: IcoDevice) -> bool | None:
    """Return whether the probe sees USB power, None when the report stays silent."""
    plugged = device.value("usb_power")
    if plugged is None:
        return None
    return plugged == "plugged"


BINARY_SENSOR_DESCRIPTIONS: tuple[OndiloIcoBinarySensorEntityDescription, ...] = (
    OndiloIcoBinarySensorEntityDescription(
        key="usb_power",
        translation_key="usb_power",
        device_class=BinarySensorDeviceClass.PLUG,
        is_on_fn=_usb_power,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OndiloIcoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors of the ICO probes the bridge knows about."""
    coordinator = entry.runtime_data
    seen: set[str] = set()

    @callback
    def async_add_new_devices() -> None:
        """Add the binary sensors of the probes that appeared since the last poll."""
        new_devices = async_new_devices(coordinator, seen)
        if not new_devices:
            return
        async_add_entities(
            OndiloIcoBinarySensor(coordinator, device, description)
            for device in new_devices
            for description in BINARY_SENSOR_DESCRIPTIONS
        )

    async_add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(async_add_new_devices))


class OndiloIcoBinarySensor(OndiloIcoEntity, BinarySensorEntity):
    """One on/off state of one ICO probe."""

    entity_description: OndiloIcoBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: OndiloIcoCoordinator,
        device: IcoDevice,
        description: OndiloIcoBinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, device)
        self.entity_description = description
        self._attr_unique_id = f"{device.uuid}-{description.key}"

    @property
    @override
    def is_on(self) -> bool | None:
        """Return the state of this key, None meaning 'the bridge is silent'."""
        if (device := self.ico_device) is None:
            return None
        return self.entity_description.is_on_fn(device)
