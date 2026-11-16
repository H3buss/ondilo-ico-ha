"""Turning the raw fields of an ICO probe into pH and TDS.

The firmware names the pH channel a millivolt measure - "Measure = %d.%d mV" at
0x0159c0 of the 3.1.0 image, with the measure window given as "[-500; 500]" at
0x015996 - and it names the two conductivity electrodes COND_POS (0x01568a) and
COND_NEG (0x0156a1) without naming a unit for either. Whatever Ondilo computes
from those happens in the cloud, and one of the constants it needs, the potential
of this particular electrode at pH 7, is carried by no report and appears in none
of the 25 OTA images.

The shape of the conversion is not a secret though: an electrode is linear, and
its slope is set by the Nernst equation. So the numbers that cannot be read are
offered to the user to measure at home against buffer solutions, and this module
is the arithmetic that turns those points into readings.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

# Slope of an ideal electrode at 25 C, in millivolts per pH unit: R*T*ln(10)/F.
NERNST_SLOPE_25C: float = 59.16

# The pH at which the potential of an electrode does not depend on temperature.
# Every calibration is stored there rather than at the buffer that happened to be
# used, which is what lets a slope measured with 8 C pool water serve a spa at
# 35 C. The probe of a user who measured four points at 8.5 C sits near -23 mV.
NEUTRAL_PH: float = 7.0

KELVIN: float = 273.15
STANDARD_TEMP_C: float = 25.0

# A slope outside this window at 25 C means a bottle was mislabelled, or the probe
# came out of the wrong glass: real electrodes are 50 to 70 mV per pH unit.
MIN_SLOPE_25C: float = 40.0
MAX_SLOPE_25C: float = 75.0

# Two buffers closer than this cannot define a slope; the app rounds pH to 0.1 and
# a single pH 7 bottle is the common case.
MIN_PH_SPAN: float = 0.05

STORAGE_VERSION: int = 1


def nernst_slope(slope_25: float, temperature_c: float) -> float:
    """Return the electrode slope at that water temperature, in mV per pH unit."""
    return slope_25 * (KELVIN + temperature_c) / (KELVIN + STANDARD_TEMP_C)


def ph_from_mv(
    mv: float, offset_mv: float | None, slope_25: float, temperature_c: float
) -> float | None:
    """Return the pH a potential corresponds to, or None if the probe is not set up."""
    if offset_mv is None:
        return None
    slope = nernst_slope(slope_25, temperature_c)
    if slope <= 0:
        return None
    return NEUTRAL_PH + (offset_mv - mv) / slope


def offset_from_point(
    ph: float, mv: float, slope_25: float, temperature_c: float
) -> float:
    """Return the potential this electrode would give at pH 7."""
    return mv + nernst_slope(slope_25, temperature_c) * (ph - NEUTRAL_PH)


def slope_from_points(
    ph_1: float,
    mv_1: float,
    temp_1: float,
    ph_2: float,
    mv_2: float,
    temp_2: float,
) -> float | None:
    """Return the slope of the two points at 25 C, or None if they say nothing."""
    if abs(ph_2 - ph_1) < MIN_PH_SPAN:
        return None
    slope = (mv_1 - mv_2) / (ph_2 - ph_1)
    slope_25 = slope * (KELVIN + STANDARD_TEMP_C) / (KELVIN + (temp_1 + temp_2) / 2)
    if not MIN_SLOPE_25C <= slope_25 <= MAX_SLOPE_25C:
        return None
    return round(slope_25, 2)


@dataclass(slots=True)
class ProbeCalibration:
    """What one probe needs before its raw fields mean anything."""

    # What the user says is in the glass the probe is sitting in right now.
    buffer_ph: float = NEUTRAL_PH
    known_ppm: float | None = None

    # What a capture wrote: the potential at pH 7, and the slope of the electrode.
    offset_mv: float | None = None
    slope_25: float = NERNST_SLOPE_25C
    ppm_per_count: float | None = None

    # The last point captured, so a second buffer can define a slope with it.
    point_ph: float | None = None
    point_mv: float | None = None
    point_temp: float | None = None

    observers: list[Callable[[], None]] = field(default_factory=list, repr=False)

    def add_observer(self, observer: Callable[[], None]) -> Callable[[], None]:
        """Watch this calibration, and return the callable that stops the watch."""

        def stop() -> None:
            if observer in self.observers:
                self.observers.remove(observer)

        self.observers.append(observer)
        return stop

    def notify(self) -> None:
        """Tell the entities that show these numbers that they changed."""
        for observer in list(self.observers):
            observer()

    def capture_ph(self, mv: float, temperature_c: float) -> str:
        """Store the potential of the buffer the probe is sitting in."""
        ph = self.buffer_ph
        previous_ph, previous_mv = self.point_ph, self.point_mv
        previous_temp = (
            self.point_temp if self.point_temp is not None else temperature_c
        )
        self.point_ph, self.point_mv, self.point_temp = ph, mv, temperature_c
        if ph != NEUTRAL_PH and previous_ph is not None and previous_mv is not None:
            slope = slope_from_points(
                previous_ph,
                previous_mv,
                previous_temp,
                ph,
                mv,
                temperature_c,
            )
            if slope is not None:
                self.slope_25 = slope
                self.offset_mv = offset_from_point(ph, mv, slope, temperature_c)
                return (
                    f"slope {slope:.2f} mV/pH and offset "
                    f"{self.offset_mv:.1f} mV fitted on pH {previous_ph} and {ph}"
                )
        self.offset_mv = offset_from_point(ph, mv, self.slope_25, temperature_c)
        return (
            f"offset {self.offset_mv:.1f} mV at pH 7, from {mv:.1f} mV in a "
            f"pH {ph} buffer at {temperature_c:.1f} C, slope {self.slope_25:.2f}"
        )

    def capture_tds(self, counts: float) -> str:
        """Store the factor that turns cell counts into the ppm of the standard."""
        ppm = self.known_ppm
        if ppm is None or ppm <= 0:
            raise ValueError("no known ppm to calibrate against")
        if counts <= 0:
            raise ValueError("the cell reported no counts to calibrate against")
        self.ppm_per_count = ppm / counts
        return (
            f"{ppm:.0f} ppm for {counts:.0f} counts, "
            f"{self.ppm_per_count:.4f} ppm each"
        )

    def estimate_ph(self, mv: float, temperature_c: float | None) -> float | None:
        """Return the pH of a raw electrode potential."""
        return ph_from_mv(
            mv,
            self.offset_mv,
            self.slope_25,
            STANDARD_TEMP_C if temperature_c is None else temperature_c,
        )

    def estimate_tds(self, counts: float | None) -> float | None:
        """Return the TDS of a raw cell reading."""
        if counts is None or self.ppm_per_count is None:
            return None
        return counts * self.ppm_per_count

    def as_dict(self) -> dict[str, Any]:
        """Return what is worth keeping across a restart."""
        return {
            "buffer_ph": self.buffer_ph,
            "known_ppm": self.known_ppm,
            "offset_mv": self.offset_mv,
            "slope_25": self.slope_25,
            "ppm_per_count": self.ppm_per_count,
            "point_ph": self.point_ph,
            "point_mv": self.point_mv,
            "point_temp": self.point_temp,
        }

    @classmethod
    def from_dict(cls, saved: dict[str, Any] | None) -> ProbeCalibration:
        """Return the calibration a restart brought back."""
        if not isinstance(saved, dict):
            return cls()
        known = {key: value for key, value in saved.items() if key in cls.__slots__}
        return cls(**known)  # type: ignore[arg-type]


class CalibrationStore:
    """The calibrations of one config entry, kept across a restart."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the store of one entry."""
        self._store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")
        self._probes: dict[str, ProbeCalibration] = {}

    async def async_load(self) -> None:
        """Read back what a previous run left."""
        saved = await self._store.async_load()
        if not isinstance(saved, dict):
            return
        probes = saved.get("probes")
        if not isinstance(probes, dict):
            return
        for uuid, values in probes.items():
            if isinstance(values, dict):
                self._probes[str(uuid)] = ProbeCalibration.from_dict(values)

    async def async_save(self) -> None:
        """Write the calibrations, with the delay the core applies by default."""
        await self._store.async_save(
            {"probes": {uuid: probe.as_dict() for uuid, probe in self._probes.items()}}
        )

    def for_probe(self, uuid: str) -> ProbeCalibration:
        """Return the calibration of one probe, creating it on first use."""
        if uuid not in self._probes:
            self._probes[uuid] = ProbeCalibration()
        return self._probes[uuid]
