"""Optical power meters for Utils > Illumination Power Calibration... (squid/intensity_calibration_run.py).

ThorlabsPowerMeter talks to any Thorlabs meter that speaks the PM SCPI set (PM16, PM100D/USB, PM400) through
pyvisa, and reports its sensor's power and wavelength range so the calibration never drives a sensor past its
rating. SimulatedPowerMeter stands in for one in --simulation and in tests: it asks a callable what light the
calibration is commanding and answers from a source model, with noise and a little ambient light.
"""

import math
import sys
from dataclasses import dataclass
from typing import Callable, Optional, Protocol, Sequence, Tuple

import numpy as np

import squid.logging

_log = squid.logging.get_logger(__name__)

THORLABS_USB_VENDOR_ID = 0x1313
# SCPI instruments answer an overrange or invalid measurement with 9.9e37.
_OVERRANGE_W = 1e37
# Every VISA call gives up after this; it bounds how long a stuck read can hold the light on (and stays below the
# controller's 5 s illumination watchdog, which the calibration feeds before each read).
METER_TIMEOUT_MS = 3000
# Wait after the light comes on before reading, per meter family. Placeholders until the bench measures them
# (spec §12); readings must also converge (squid/intensity_calibration_run.py), so these only set the first wait.
SETTLE_S_BY_MODEL = {"PM16": 0.3, "PM100": 0.15, "PM400": 0.15}
SETTLE_S_UNKNOWN_MODEL = 0.5
# Meter families the calibration has been run with on a bench. Others work, with a warning in the dialog.
VALIDATED_MODELS: Tuple[str, ...] = ()
# Sensor limits of meters with a built-in sensor, for when the meter does not answer the range queries; when it does,
# the lower of the two applies. PM16-121 (the first meter used, 2026-10-08): S121C-type Si photodiode, 400-1100 nm,
# up to 500 mW, inferred from Thorlabs' naming (PM16-120 = S120C, PM16-122 = S122C); confirm on the bench (spec §12).
KNOWN_SENSOR_LIMITS = {"PM16-121": (500.0, (400.0, 1100.0))}
# Making a meter visible to pyvisa (paths relative to software/): the setup steps per OS, and the Linux udev rule that
# lets a user without root open it
SETUP_DOC = "docs/illumination-power-calibration.md"
UDEV_RULE = "drivers and libraries/thorlabs/linux/udev/99-thorlabs-pm16.rules"


def setup_hint() -> str:
    """How to make a meter visible to pyvisa on this OS; every message about a meter Squid cannot reach ends with it."""
    if sys.platform.startswith("linux"):
        steps = (
            f"pip install pyvisa pyvisa-py pyusb, and allow the meter's USB device: sudo cp 'software/{UDEV_RULE}' "
            "/etc/udev/rules.d, then re-plug the meter"
        )
    elif sys.platform == "darwin":
        steps = "brew install libusb, then pip install pyvisa pyvisa-py pyusb"
    elif sys.platform.startswith("win"):
        steps = (
            "bind the WinUSB driver to the meter with Zadig, pip install pyvisa pyvisa-py pyusb, and put "
            "libusb-1.0.dll on PATH (or install NI-VISA with Thorlabs' driver instead)"
        )
    else:
        steps = "pip install pyvisa pyvisa-py pyusb"
    return f"{steps}; see software/{SETUP_DOC}"


class PowerMeterError(RuntimeError):
    """The meter could not be found, opened or read."""


class PowerMeterOverrange(PowerMeterError):
    """The reading is above the sensor's range."""


@dataclass(frozen=True)
class PowerMeterInfo:
    meter: str  # e.g. "Thorlabs PM100USB P2001234"
    sensor: str  # e.g. "S170C 220304512"
    max_power_mw: Optional[float]  # the sensor's top range; None when the meter does not say
    wavelength_range_nm: Optional[Tuple[float, float]]  # the sensor's calibrated range; None when unknown
    settle_s: float
    validated: bool  # the model family has been run on a bench


class PowerMeter(Protocol):
    info: PowerMeterInfo

    def set_wavelength(self, wavelength_nm: float) -> None: ...

    def read_mw(self) -> float: ...

    def close(self) -> None: ...


def find_thorlabs_resource(resources: Sequence[str]) -> str:
    """The first VISA USB resource with Thorlabs' vendor ID, e.g. "USB0::0x1313::0x8072::P2001234::INSTR"."""
    for resource in resources:
        parts = resource.split("::")
        if len(parts) < 2 or not parts[0].upper().startswith("USB"):
            continue
        try:
            vendor = int(parts[1], 0)
        except ValueError:
            continue
        if vendor == THORLABS_USB_VENDOR_ID:
            return resource
    raise PowerMeterError(
        f"No Thorlabs power meter found (USB vendor ID 0x1313). VISA sees: {', '.join(resources) or 'nothing'}. "
        f"Check the USB cable and close Thorlabs' own software; {setup_hint()}"
    )


def _open_resource_manager():
    try:
        import pyvisa
    except ImportError as e:
        raise PowerMeterError(f"pyvisa is not installed: {setup_hint()}") from e
    try:
        return pyvisa.ResourceManager()
    except Exception as e:  # pyvisa raises ValueError/OSError when it finds no VISA backend
        raise PowerMeterError(f"no VISA backend ({e}): {setup_hint()}") from e


def _model_family(model: str) -> Optional[str]:
    return next((family for family in SETTLE_S_BY_MODEL if model.upper().startswith(family)), None)


class ThorlabsPowerMeter:
    """A Thorlabs power meter over VISA (USBTMC). Power is read in W and returned in mW."""

    def __init__(self, resource: Optional[str] = None, averaging: int = 10, resource_manager=None):
        if resource_manager is None:
            resource_manager = _open_resource_manager()
        if resource is None:
            resource = find_thorlabs_resource(resource_manager.list_resources())
        try:
            self._inst = resource_manager.open_resource(resource)
            self._inst.timeout = METER_TIMEOUT_MS
        except Exception as e:  # pyvisa's VisaIOError (busy, no permission) and friends
            raise PowerMeterError(f"could not open the power meter at {resource}: {e}; {setup_hint()}") from e
        idn = [part.strip() for part in self._query("*IDN?").split(",")]
        model = idn[1] if len(idn) > 1 else ""
        self._write("SENS:POW:UNIT W")
        self._write(f"SENS:AVER:COUN {int(averaging)}")
        self._write("SENS:POW:RANG:AUTO ON")
        # A PM16 has its sensor built in and may not answer the sensor queries. The pre-2026-10 tools/PM16.py sent the
        # averaging and auto-range as the short forms SENS:AVER n / SENS:RANGE:AUTO ON; which forms a PM16 takes is
        # checked on the bench (spec §12)
        sensor_idn = self._optional_query("SYST:SENS:IDN?")
        known_max_mw, known_range_nm = KNOWN_SENSOR_LIMITS.get(model, (None, None))
        reported_max_mw = self._optional_float("SENS:POW:RANG:UPP? MAX", scale=1000.0)
        maxima = [value for value in (reported_max_mw, known_max_mw) if value is not None]
        family = _model_family(model)
        self.info = PowerMeterInfo(
            meter=" ".join(idn[:3]),
            sensor=" ".join(part.strip() for part in sensor_idn.split(",")[:2]) if sensor_idn else f"{model} built-in",
            max_power_mw=min(maxima) if maxima else None,
            wavelength_range_nm=self._wavelength_range() or known_range_nm,
            settle_s=SETTLE_S_BY_MODEL[family] if family else SETTLE_S_UNKNOWN_MODEL,
            validated=family in VALIDATED_MODELS,
        )
        _log.info(f"power meter connected: {self.info} ({resource})")

    def _write(self, command: str) -> None:
        try:
            self._inst.write(command)
        except Exception as e:  # pyvisa's VisaIOError (timeout, disconnect) and friends
            raise PowerMeterError(f"power meter did not take '{command}': {e}") from e

    def _query(self, command: str) -> str:
        try:
            return self._inst.query(command)
        except Exception as e:  # pyvisa's VisaIOError (timeout, disconnect) and friends
            raise PowerMeterError(f"power meter did not answer '{command}': {e}") from e

    def _optional_query(self, command: str) -> Optional[str]:
        """A query some meters or sensors do not support: None instead of an error."""
        try:
            return self._query(command).strip() or None
        except PowerMeterError:
            return None

    def _optional_float(self, command: str, scale: float = 1.0) -> Optional[float]:
        answer = self._optional_query(command)
        try:
            value = float(answer) * scale
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) and 0 < value < _OVERRANGE_W else None

    def _wavelength_range(self) -> Optional[Tuple[float, float]]:
        low = self._optional_float("SENS:CORR:WAV? MIN")
        high = self._optional_float("SENS:CORR:WAV? MAX")
        return (low, high) if low is not None and high is not None and low < high else None

    def set_wavelength(self, wavelength_nm: float) -> None:
        self._write(f"SENS:CORR:WAV {float(wavelength_nm):g}")

    def read_mw(self) -> float:
        answer = self._query("MEAS:POW?")
        try:
            watts = float(answer)
        except ValueError as e:
            raise PowerMeterError(f"power meter answered '{answer.strip()}'") from e
        if not math.isfinite(watts) or abs(watts) >= _OVERRANGE_W:
            raise PowerMeterOverrange(f"power meter overrange ({watts:g} W)")
        return watts * 1000.0

    def close(self) -> None:
        try:
            self._inst.close()
        except Exception as e:  # pyvisa's VisaIOError, e.g. the meter was unplugged
            raise PowerMeterError(f"closing the power meter failed: {e}") from e


# (light on, DAC output as a fraction of the DAC's full scale): what SimulatedPowerMeter asks its source for
SourceState = Tuple[bool, float]


def simulated_laser_mw(x):
    """A laser: dark up to x = 0.18 (30 % commanded at factor 0.6), 300 mW at x = 0.6, at most 450 mW."""
    x = np.asarray(x, dtype=float)
    return np.clip(300.0 * (x - 0.18) / 0.42, 0.0, 450.0)


def simulated_led_mw(x):
    """An LED that peaks at 312 mW at x = 0.492 (82 % commanded at factor 0.6) and rolls over to 290 mW at
    x = 0.6; flat beyond."""
    x = np.asarray(x, dtype=float)
    rising = 312.0 * (1.0 - (1.0 - np.minimum(x, 0.492) / 0.492) ** 2)
    falling = 312.0 - 22.0 * ((np.clip(x, 0.492, 0.6) - 0.492) / 0.108) ** 1.5
    return np.where(x <= 0.492, rising, falling)


def simulated_source_mw(wavelength_nm: float, x):
    """Wavelengths from 700 nm up are modelled as the rolling-over LED, shorter ones as the laser."""
    return simulated_led_mw(x) if wavelength_nm >= 700 else simulated_laser_mw(x)


class SimulatedPowerMeter:
    """Answers with the power the commanded light would give, from simulated_source_mw."""

    def __init__(
        self,
        source_state: Callable[[], SourceState],
        noise_fraction: float = 0.003,
        ambient_mw: float = 0.002,
        seed: Optional[int] = None,
    ):
        self.info = PowerMeterInfo(
            meter="Simulated power meter",
            sensor="Simulated sensor",
            max_power_mw=500.0,
            wavelength_range_nm=(350.0, 1100.0),
            settle_s=0.0,
            validated=True,
        )
        self._source_state = source_state
        self._wavelength_nm = 500.0
        self._noise_fraction = noise_fraction
        self._ambient_mw = ambient_mw
        self._rng = np.random.default_rng(seed)

    def set_wavelength(self, wavelength_nm: float) -> None:
        self._wavelength_nm = float(wavelength_nm)

    def read_mw(self) -> float:
        on, x = self._source_state()
        power = float(simulated_source_mw(self._wavelength_nm, x)) if on else 0.0
        noise = self._noise_fraction * self._rng.standard_normal()
        ambient = self._ambient_mw * (1.0 + 0.1 * self._rng.standard_normal())
        return power * (1.0 + noise) + ambient

    def close(self) -> None:
        pass
