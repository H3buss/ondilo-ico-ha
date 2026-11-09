"""The capture buttons of an ICO probe: dip the probe, press the button.

A calibration point is a raw reading taken while the probe sits in a solution whose
value is known. The probe already sends the raw reading, the user knows the value,
and only the two need to be met - which is what these buttons do, so that calibrating
does not mean editing a number by hand.
"""

from __future__ import annotations

import logging
from typing import override

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .calibration import NERNST_SLOPE_25C, STANDARD_TEMP_C, ProbeCalibration
from .coordinator import IcoDevice, OndiloIcoConfigEntry, OndiloIcoCoordinator
from .entity import OndiloIcoEntity, async_new_devices

PARALLEL_UPDATES = 0

LOGGER = logging.getLogger(__package__)

# The three keys the buttons show up under, and therefore the three names that have
# to be translated. The gate reads them here.
KEY_CAPTURE_PH = "calibrate_ph"
KEY_CAPTURE_TDS = "calibrate_tds"
KEY_RESET = "reset_calibration"
BUTTON_KEYS: tuple[str, ...] = (KEY_CAPTURE_PH, KEY_CAPTURE_TDS, KEY_RESET)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OndiloIcoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the capture buttons of the ICO probes this entry knows."""
    coordinator = entry.runtime_data
    seen: set[str] = set()
    classes = (
        IcoCapturePhButton,
        IcoCaptureTdsButton,
        IcoResetCalibrationButton,
    )

    @callback
    def async_add_new_devices() -> None:
        """Add the buttons of the probes that appeared since the last poll."""
        new_devices = async_new_devices(coordinator, seen)
        if not new_devices:
            return
        async_add_entities(
            cls(coordinator, device)
            for device in new_devices
            for cls in classes
        )

    async_add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(async_add_new_devices))


class OndiloIcoCalibrationButton(OndiloIcoEntity, ButtonEntity):
    """Base of the buttons that write to the calibration of one probe."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: OndiloIcoCoordinator, device: IcoDevice, key: str
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator, device)
        self._attr_translation_key = key
        self._attr_unique_id = f"{device.uuid}-{key}"

    @property
    def calibration(self) -> ProbeCalibration:
        """Return the calibration of this probe."""
        return self.coordinator.calibrations.for_probe(self._uuid)

    def _reading(self, key: str, what: str) -> float:
        """Return the last raw reading of this key, or explain why there is none."""
        device = self.ico_device
        value = device.value(key) if device is not None else None
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise HomeAssistantError(
                f"The probe has not reported a {what} recently, so there is "
                "nothing to calibrate on. Press the button on the probe, or wait "
                "for its next report."
            )
        return float(value)

    @property
    def _water_temp(self) -> float:
        """Return the temperature to compute the slope at, 25 C if unknown."""
        device = self.ico_device
        value = device.value("temperature") if device is not None else None
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return STANDARD_TEMP_C
        return float(value)

    async def _async_store(self, message: str) -> None:
        """Record what was just captured, say it, and show it everywhere."""
        await self.coordinator.calibrations.async_save()
        self.calibration.notify()
        LOGGER.info("ondilo_ico_local: %s, %s", self._uuid, message)


class IcoCapturePhButton(OndiloIcoCalibrationButton):
    """Record the potential of the buffer the probe is sitting in."""

    def __init__(
        self, coordinator: OndiloIcoCoordinator, device: IcoDevice
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator, device, KEY_CAPTURE_PH)

    @override
    async def async_press(self) -> None:
        """Turn the reading in hand into the offset of this electrode."""
        mv = self._reading("ph", "pH electrode potential")
        await self._async_store(self.calibration.capture_ph(mv, self._water_temp))


class IcoCaptureTdsButton(OndiloIcoCalibrationButton):
    """Record the cell reading of the standard the probe is sitting in."""

    def __init__(
        self, coordinator: OndiloIcoCoordinator, device: IcoDevice
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator, device, KEY_CAPTURE_TDS)

    @override
    async def async_press(self) -> None:
        """Turn the reading in hand into the ppm factor of this cell."""
        counts = self._reading("conductivity", "conductivity reading")
        try:
            message = self.calibration.capture_tds(counts)
        except ValueError as err:
            raise HomeAssistantError(
                f"Cannot calibrate the conductivity: {err}. Set the known ppm of "
                "the standard first."
            ) from err
        await self._async_store(message)


class IcoResetCalibrationButton(OndiloIcoCalibrationButton):
    """Forget the calibration, which leaves the raw readings on their own."""

    def __init__(
        self, coordinator: OndiloIcoCoordinator, device: IcoDevice
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator, device, KEY_RESET)

    @override
    async def async_press(self) -> None:
        """Drop what the captures wrote, and start over."""
        calibration = self.calibration
        calibration.offset_mv = None
        calibration.ppm_per_count = None
        calibration.slope_25 = NERNST_SLOPE_25C
        calibration.point_ph = None
        calibration.point_mv = None
        calibration.point_temp = None
        await self._async_store("calibration cleared")
