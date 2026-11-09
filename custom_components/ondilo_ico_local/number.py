"""The calibration fields of an ICO probe, as editable numbers.

Ondilo keeps the constants that turn a millivolt into a pH, and a cell count into
a ppm, in its cloud. They cannot be read out of the firmware, so they live here
instead, where they can be measured at home: what the buffer in the glass is, what
a standard is worth, and what the probe then has to be told. They are ordinary
number entities, so they can also be typed in, scripted, or filled from a template.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import override

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfElectricPotential
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .calibration import ProbeCalibration
from .coordinator import IcoDevice, OndiloIcoConfigEntry, OndiloIcoCoordinator
from .entity import (
    OndiloIcoEntity,
    async_new_devices,
)

PARALLEL_UPDATES = 0

UNIT_PARTS_PER_MILLION = "ppm"
UNIT_PPM_PER_COUNT = "ppm/count"
UNIT_MV_PER_PH = "mV/pH"


@dataclass(frozen=True, kw_only=True)
class OndiloIcoNumberEntityDescription(NumberEntityDescription):
    """A calibration field, which is one attribute of one probe's calibration."""

    attribute: str

    # Whether writing this field is worth a restart. The two fields that describe
    # the glass the probe is sitting in right now are not: they are spent once the
    # capture button has been pressed.
    persistent: bool = True


NUMBER_DESCRIPTIONS: tuple[OndiloIcoNumberEntityDescription, ...] = (
    OndiloIcoNumberEntityDescription(
        key="calibration_buffer_ph",
        translation_key="calibration_buffer_ph",
        attribute="buffer_ph",
        persistent=False,
        device_class=NumberDeviceClass.PH,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
        native_min_value=0.0,
        native_max_value=14.0,
        native_step=0.01,
        native_unit_of_measurement=None,
    ),
    OndiloIcoNumberEntityDescription(
        key="calibration_known_ppm",
        translation_key="calibration_known_ppm",
        attribute="known_ppm",
        persistent=False,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
        native_min_value=0.0,
        native_max_value=20000.0,
        native_step=1.0,
        native_unit_of_measurement=UNIT_PARTS_PER_MILLION,
    ),
    OndiloIcoNumberEntityDescription(
        key="calibration_ph_offset",
        translation_key="calibration_ph_offset",
        attribute="offset_mv",
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
        native_min_value=-500.0,
        native_max_value=500.0,
        native_step=0.5,
        native_unit_of_measurement=UnitOfElectricPotential.MILLIVOLT,
    ),
    OndiloIcoNumberEntityDescription(
        key="calibration_ph_slope",
        translation_key="calibration_ph_slope",
        attribute="slope_25",
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
        native_min_value=40.0,
        native_max_value=75.0,
        native_step=0.01,
        native_unit_of_measurement=UNIT_MV_PER_PH,
        native_precision=2,
    ),
    OndiloIcoNumberEntityDescription(
        key="calibration_tds_factor",
        translation_key="calibration_tds_factor",
        attribute="ppm_per_count",
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
        native_min_value=0.0,
        native_max_value=10.0,
        native_step=0.0001,
        native_unit_of_measurement=UNIT_PPM_PER_COUNT,
        native_precision=4,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OndiloIcoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the calibration fields of the ICO probes this entry knows."""
    coordinator = entry.runtime_data
    seen: set[str] = set()

    @callback
    def async_add_new_devices() -> None:
        """Add the fields of the probes that appeared since the last poll."""
        new_devices = async_new_devices(coordinator, seen)
        if not new_devices:
            return
        async_add_entities(
            OndiloIcoNumber(coordinator, device, description)
            for device in new_devices
            for description in NUMBER_DESCRIPTIONS
        )

    async_add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(async_add_new_devices))


class OndiloIcoNumber(OndiloIcoEntity, NumberEntity):
    """One calibration field of one ICO probe."""

    entity_description: OndiloIcoNumberEntityDescription

    def __init__(
        self,
        coordinator: OndiloIcoCoordinator,
        device: IcoDevice,
        description: OndiloIcoNumberEntityDescription,
    ) -> None:
        """Initialize the field."""
        super().__init__(coordinator, device)
        self.entity_description = description
        self._attr_unique_id = f"{device.uuid}-{description.key}"
        self._stop_watching: Callable[[], None] | None = None

    @property
    def calibration(self) -> ProbeCalibration:
        """Return the calibration this field writes to."""
        return self.coordinator.calibrations.for_probe(self._uuid)

    @property
    @override
    def native_value(self) -> float | None:
        """Return what the calibration holds for this field."""
        value = getattr(self.calibration, self.entity_description.attribute, None)
        return None if value is None else float(value)

    @override
    async def async_set_native_value(self, value: float) -> None:
        """Write the field, and tell the entities that read it."""
        setattr(self.calibration, self.entity_description.attribute, float(value))
        if self.entity_description.persistent:
            await self.coordinator.calibrations.async_save()
        self.calibration.notify()

    @callback
    def _async_refresh_from_calibration(self) -> None:
        """Show the calibration again, because something else wrote to it."""
        if self.hass is not None:
            self.async_write_ha_state()

    @override
    async def async_added_to_hass(self) -> None:
        """Follow the calibration, which the capture buttons write to as well."""
        await super().async_added_to_hass()
        self._stop_watching = self.calibration.add_observer(
            self._async_refresh_from_calibration
        )

    @override
    async def async_will_remove_from_hass(self) -> None:
        """Stop following the calibration."""
        if self._stop_watching is not None:
            self._stop_watching()
            self._stop_watching = None
        await super().async_will_remove_from_hass()
