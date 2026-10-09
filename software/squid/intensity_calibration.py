"""Illumination power calibration: the fit, the lookup, and the calibration file.

A calibration maps a requested intensity (% of a channel's maximum power) to the DAC command that gives it. This
module is what the runtime needs (control/lighting.py imports it); running a calibration with a power meter lives in
squid/intensity_calibration_run.py. No Qt.

Design: AI-docs Squid/to-do/2026-10-08-power-linearization-gui-design.md (§5.2 the fit, §6 the files).
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

ROLLOVER_FRACTION = 0.01
SMOOTHING_WINDOW = 5
ZERO_LEVEL_SIGMAS = 5.0
ZERO_LEVEL_FRACTION = 1e-3
VERIFY_SETPOINTS = (1, 2, 5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100)
VERIFY_MIN_GATED_PERCENT = 10
VERIFY_REL_TOL = 0.05
MIN_POINTS = 3
FORMAT = "squid-intensity-calibration/1"
CALIBRATIONS_DIR_NAME = "intensity_calibrations"
_CLAMP_EPS = 1e-9


class CalibrationError(ValueError):
    """The measurements cannot make a calibration (too few points, no light)."""


class CalibrationFileError(ValueError):
    """A calibration file exists but cannot be used."""


def isotonic_fit(y: Sequence[float]) -> np.ndarray:
    """Pool-adjacent-violators: the non-decreasing sequence closest to y in least squares (equal weights)."""
    values: List[float] = []
    counts: List[int] = []
    for value in np.asarray(y, dtype=float):
        values.append(float(value))
        counts.append(1)
        while len(values) > 1 and values[-2] > values[-1]:
            n = counts[-2] + counts[-1]
            merged = (values[-2] * counts[-2] + values[-1] * counts[-1]) / n
            values[-2:] = [merged]
            counts[-2:] = [n]
    return np.repeat(values, counts)


def running_median(y: Sequence[float], window: int = SMOOTHING_WINDOW) -> np.ndarray:
    """Median over a window centred on each point, shrunk symmetrically at the ends. It returns a monotone sequence
    unchanged and removes isolated spikes."""
    y = np.asarray(y, dtype=float)
    half = window // 2
    out = np.empty(y.size)
    for i in range(y.size):
        h = min(half, i, y.size - 1 - i)
        out[i] = np.median(y[i - h : i + h + 1])
    return out


def find_rollover_peak(
    power: Sequence[float], sigma_dark: float = 0.0, window: int = SMOOTHING_WINDOW
) -> Optional[int]:
    """Index of the power peak when the output falls at the top (e.g. LED thermal rollover), else None.

    The peak of the running median must lie before the last `window` points, and the median of the last `window`
    raw points must be below it by more than max(ROLLOVER_FRACTION x peak, 3 sigma_dark). A flat top is not
    rollover; neither is a dip in the middle of the curve.
    """
    power = np.asarray(power, dtype=float)
    if power.size <= window:
        return None
    smoothed = running_median(power, window)
    i_peak = int(np.argmax(smoothed))
    if i_peak >= power.size - window:
        return None
    drop = smoothed[i_peak] - float(np.median(power[-window:]))
    return i_peak if drop > max(ROLLOVER_FRACTION * smoothed[i_peak], 3.0 * sigma_dark) else None


def build_anchors(x: Sequence[float], fitted: Sequence[float], zero_level: float) -> Tuple[np.ndarray, np.ndarray]:
    """Inversion anchors (power, x) from a monotone fit: power strictly increasing, x non-decreasing.

    The first anchor is (0, x of the LAST point whose fitted power <= zero_level): the end of the dead zone below a
    lasing threshold, so a small request lands just above threshold instead of inside the dead zone. Each later run
    of equal fitted power contributes (that power, x of its FIRST point): on a plateau, the lowest drive that reaches
    it, so no current is spent for no light.
    """
    x = np.asarray(x, dtype=float)
    fitted = np.asarray(fitted, dtype=float)
    dead = np.nonzero(fitted <= zero_level)[0]
    powers = [0.0]
    xs = [float(x[dead[-1]]) if dead.size else 0.0]
    i = int(dead[-1]) + 1 if dead.size else 0
    while i < x.size:
        j = i
        while j + 1 < x.size and fitted[j + 1] == fitted[i]:
            j += 1
        powers.append(float(fitted[i]))
        xs.append(float(x[i]))
        i = j + 1
    return np.array(powers), np.array(xs)


@dataclass(frozen=True, eq=False)
class CurveFit:
    fitted_mw: np.ndarray  # per point; NaN above the calibrated top
    anchor_power_mw: np.ndarray
    anchor_x: np.ndarray
    p_max_mw: float  # calibrated 100 %
    top_index: int  # the last point of the calibrated range
    zero_level_mw: float
    rollover: Optional[str]  # a sentence for the user, or None


def fit_curve(dac_percent, x, power_mw, sigma_dark: float = 0.0) -> CurveFit:
    """Fit a measured curve (design §5.2): rollover cut, running median, monotone fit, anchors.

    Args:
        dac_percent: commanded DAC % per point, increasing (used in messages).
        x: what the lookup returns, per point, increasing (fraction of the DAC's full scale).
        power_mw: dark-subtracted power per point.
        sigma_dark: the meter's dark noise in mW; 0 when unknown.
    """
    dac_percent = np.asarray(dac_percent, dtype=float)
    x = np.asarray(x, dtype=float)
    power = np.asarray(power_mw, dtype=float)
    if power.size < MIN_POINTS:
        raise CalibrationError(f"only {power.size} points; at least {MIN_POINTS} are needed")
    i_peak = find_rollover_peak(power, sigma_dark)
    top = power.size - 1 if i_peak is None else i_peak
    # Smooth the whole curve once, then cut: smoothing only the kept part would make the peak an end point, where
    # the median window is a single reading and a spike there would set P_max.
    fitted_part = isotonic_fit(running_median(power)[: top + 1])
    p_max = float(fitted_part[-1])
    if not p_max > 0:
        raise CalibrationError("no light measured: the fitted power never rises above zero")
    zero_level = max(ZERO_LEVEL_SIGMAS * sigma_dark, ZERO_LEVEL_FRACTION * p_max)
    anchor_power, anchor_x = build_anchors(x[: top + 1], fitted_part, zero_level)
    if anchor_power.size < 2:
        raise CalibrationError("no light above the dark noise: check the sensor position and the meter's wavelength")
    fitted = np.full(power.size, np.nan)
    fitted[: top + 1] = fitted_part
    rollover = None
    if i_peak is not None:
        end_mw = float(np.median(power[-SMOOTHING_WINDOW:]))
        rollover = (
            f"output peaks at DAC {dac_percent[top]:.1f} % ({p_max:.4g} mW) and falls to {end_mw:.4g} mW at "
            f"DAC {dac_percent[-1]:.1f} %; calibrated range ends at DAC {dac_percent[top]:.1f} %"
        )
    return CurveFit(fitted, anchor_power, anchor_x, p_max, top, zero_level, rollover)


def x_for_power_fraction(anchor_power_mw, anchor_x, p_max_mw: float, fraction: float) -> float:
    """The abscissa that gives `fraction` of p_max_mw: 0 (source off) for fraction <= 0, the top for >= 1."""
    if fraction <= 0:
        return 0.0
    return float(np.interp(min(fraction, 1.0) * p_max_mw, anchor_power_mw, anchor_x))


def calibration_file_name(wavelength_nm: int, controller_port: str) -> str:
    return f"{wavelength_nm}nm_{controller_port}.csv"


def _clamp_to_ceiling(commanded_percent: float, max_output: float) -> Tuple[float, bool]:
    """The DAC never goes past Max Output (design Q4: a hardware ceiling). Returns (command, clamped)."""
    ceiling = max_output * 100.0
    if commanded_percent > ceiling + _CLAMP_EPS:
        return ceiling, True
    return commanded_percent, False


@dataclass(frozen=True, eq=False)
class IntensityCalibration:
    """A calibration this module wrote: intensity % = % of p_max_mw, stored against the DAC's physical full scale
    so the illumination intensity factor drops out (design §6.2)."""

    channel: str
    controller_port: str
    wavelength_nm: int
    calibrated_at: str
    software_commit: str
    meter: str
    sensor: str
    illumination_intensity_factor: float
    max_output: float
    measured_in: str
    dark_mw: Tuple[float, float]
    drift_fraction: float
    p_max_mw: float
    top_dac_percent: float
    zero_level_mw: float
    rollover: Optional[str]
    dac_percent_commanded: np.ndarray
    dac_fraction_of_full_scale: np.ndarray
    power_mw_raw: np.ndarray
    power_mw: np.ndarray
    power_mw_fit: np.ndarray
    anchor_power_mw: np.ndarray
    anchor_x: np.ndarray
    file_name: str
    pulse_on_s: float  # how long the light was on per reading: the operating condition this calibration represents
    verification: Optional[str] = None  # "pass" | "fail" | None before verification
    verification_points: Tuple[Tuple[float, float], ...] = ()  # (requested %, error %)
    hold_s: float = 0.0  # length of the continuous-light check; 0 when not run
    hold_droop_fraction: Optional[float] = None  # (end - start) / start of that check: how live view departs

    @classmethod
    def from_sweep(
        cls,
        *,
        channel: str,
        controller_port: str,
        wavelength_nm: int,
        calibrated_at: str,
        software_commit: str,
        meter: str,
        sensor: str,
        factor: float,
        max_output: float,
        measured_in: str,
        dark_mw: Tuple[float, float],
        drift_fraction: float,
        dac_percent,
        power_raw_mw,
        power_mw,
        sigma_dark: float,
        pulse_on_s: float,
    ) -> "IntensityCalibration":
        dac = np.asarray(dac_percent, dtype=float)
        x = dac / 100.0 * factor
        fit = fit_curve(dac, x, power_mw, sigma_dark)
        return cls(
            channel=channel,
            controller_port=controller_port,
            wavelength_nm=int(wavelength_nm),
            calibrated_at=calibrated_at,
            software_commit=software_commit,
            meter=meter,
            sensor=sensor,
            illumination_intensity_factor=float(factor),
            max_output=float(max_output),
            measured_in=measured_in,
            dark_mw=(float(dark_mw[0]), float(dark_mw[1])),
            drift_fraction=float(drift_fraction),
            p_max_mw=fit.p_max_mw,
            top_dac_percent=float(dac[fit.top_index]),
            zero_level_mw=fit.zero_level_mw,
            rollover=fit.rollover,
            dac_percent_commanded=dac,
            dac_fraction_of_full_scale=x,
            power_mw_raw=np.asarray(power_raw_mw, dtype=float),
            power_mw=np.asarray(power_mw, dtype=float),
            power_mw_fit=fit.fitted_mw,
            anchor_power_mw=fit.anchor_power_mw,
            anchor_x=fit.anchor_x,
            file_name=calibration_file_name(int(wavelength_nm), controller_port),
            pulse_on_s=float(pulse_on_s),
        )

    def commanded_percent(self, intensity_percent: float, factor: float, max_output: float) -> Tuple[float, bool]:
        """The DAC command (commanded %, before the firmware's factor) for a requested % of p_max_mw."""
        x = x_for_power_fraction(self.anchor_power_mw, self.anchor_x, self.p_max_mw, intensity_percent / 100.0)
        if factor <= 0:  # the firmware outputs nothing at factor 0: only "off" is reachable (and no division by 0)
            return (0.0, False) if x == 0 else (max_output * 100.0, True)
        return _clamp_to_ceiling(x / factor * 100.0, max_output)

    def cap_percent(self, max_output: float) -> float:
        """The slider runs the full range: the ceiling is inside the lookup."""
        return 100.0

    def identity_mismatch(self, wavelength_nm: Optional[int], controller_port: Optional[str]) -> Optional[str]:
        """Why this calibration does not belong to a channel with this wavelength and port, or None when it does: a
        lookup measured on one port says nothing about the source on another."""
        if wavelength_nm == self.wavelength_nm and controller_port == self.controller_port:
            return None
        return (
            f"{self.file_name} was measured for {self.wavelength_nm} nm on {self.controller_port}; the channel is now "
            f"{wavelength_nm} nm on {controller_port}: recalibrate"
        )

    def describe(self) -> Dict[str, object]:
        description: Dict[str, object] = {
            "intensity_unit": "power_percent",
            "calibration_format": FORMAT,
            "calibration_file": self.file_name,
            "calibrated_at": self.calibrated_at,
            "max_power_mw": round(float(self.p_max_mw), 3),
            "measured_in": self.measured_in,
            "verification": self.verification or "not run",
        }
        if self.rollover:
            description["rollover"] = self.rollover
        description["pulse_on_s"] = round(float(self.pulse_on_s), 3)
        if self.hold_droop_fraction is not None:
            description["continuous_hold_droop_percent"] = round(100.0 * self.hold_droop_fraction, 2)
        return description

    def notes(self, factor: float, max_output: float) -> List[str]:
        """Why this calibration no longer fits the machine (design §6.2 staleness); empty when it does."""
        notes = []
        if factor < self.illumination_intensity_factor - _CLAMP_EPS:
            notes.append(
                f"Illumination Intensity Factor lowered {self.illumination_intensity_factor:g} -> {factor:g} since "
                "calibration; the top of the range is unreachable: recalibrate"
            )
        if abs(max_output - self.max_output) > _CLAMP_EPS:
            notes.append(f"Max Output changed {self.max_output:g} -> {max_output:g} since calibration: recalibrate")
        return notes

    def status(self, factor: float, max_output: float) -> str:
        date = self.calibrated_at[:10]
        notes = self.notes(factor, max_output)
        if notes:
            return f"stale ({date}): " + "; ".join(notes)
        if self.verification == "fail":
            return f"failed verification ({date})"
        return f"calibrated {date}"

    def verification_summary(self) -> str:
        gated = [(r, e) for r, e in self.verification_points if r >= VERIFY_MIN_GATED_PERCENT]
        if not gated:
            return "not verified"
        r, e = max(gated, key=lambda point: abs(point[1]))
        return f"{self.verification}: worst {e:+.1f} % at {r:g} %"


_COLUMNS = ["dac_percent_commanded", "dac_fraction_of_full_scale", "power_mw_raw", "power_mw", "power_mw_fit"]


def write_calibration(calibration: IntensityCalibration, path: Path) -> None:
    """`# key: value` header lines, then the table (pandas reads it with comment="#"; Excel opens it)."""
    c = calibration
    header = {
        "format": FORMAT,
        "intensity_unit": "power_percent",
        "channel": c.channel,
        "controller_port": c.controller_port,
        "wavelength_nm": str(c.wavelength_nm),
        "calibrated_at": c.calibrated_at,
        "software_commit": c.software_commit,
        "meter": c.meter,
        "sensor": c.sensor,
        "meter_wavelength_nm": str(c.wavelength_nm),
        "illumination_intensity_factor": f"{c.illumination_intensity_factor:g}",
        "max_output": f"{c.max_output:g}",
        "measured_in": c.measured_in,
        "dark_mw": f"{c.dark_mw[0]:.6g} / {c.dark_mw[1]:.6g}",
        "drift_fraction": f"{c.drift_fraction:.6g}",
        "p_max_mw": f"{c.p_max_mw:.10g}",
        "top_dac_percent": f"{c.top_dac_percent:.10g}",
        "zero_level_mw": f"{c.zero_level_mw:.10g}",
        "rollover": c.rollover or "none",
        "verification": c.verification or "not run",
        "verification_points": "; ".join(f"{r:g}:{e:+.4f}" for r, e in c.verification_points),
        "pulse_on_s": f"{c.pulse_on_s:.4g}",
        "hold_s": f"{c.hold_s:g}",
        "hold_droop_fraction": "not run" if c.hold_droop_fraction is None else f"{c.hold_droop_fraction:+.5f}",
    }
    table = pd.DataFrame(
        {
            "dac_percent_commanded": c.dac_percent_commanded,
            "dac_fraction_of_full_scale": c.dac_fraction_of_full_scale,
            "power_mw_raw": c.power_mw_raw,
            "power_mw": c.power_mw,
            "power_mw_fit": c.power_mw_fit,
        }
    )
    with open(path, "w", newline="") as f:
        for key, value in header.items():
            f.write(f"# {key}: {value}\n")
        table.to_csv(f, index=False, float_format="%.10g")


def read_header(path: Path) -> Dict[str, str]:
    """The `# key: value` lines at the top of a file; {} for a legacy file."""
    header: Dict[str, str] = {}
    with open(path) as f:
        for line in f:
            if not line.startswith("#"):
                break
            key, sep, value = line[1:].strip().partition(":")
            if sep:
                header[key.strip()] = value.strip()
    return header


def _parse_points(text: str) -> Tuple[Tuple[float, float], ...]:
    points = []
    for item in text.split(";"):
        if item.strip():
            r, e = item.split(":")
            points.append((float(r), float(e)))
    return tuple(points)


def _optional(value: Optional[str], empty: str) -> Optional[str]:
    return None if value in (None, empty) else value


def _optional_float(value: str) -> Optional[float]:
    return None if value == "not run" else float(value)


def _read_intensity_calibration(path: Path, header: Dict[str, str]) -> IntensityCalibration:
    table = pd.read_csv(path, comment="#")
    missing = [column for column in _COLUMNS if column not in table.columns]
    if missing:
        raise CalibrationFileError(f"{path.name}: missing columns {missing}")
    if len(table) < MIN_POINTS:
        raise CalibrationFileError(f"{path.name}: only {len(table)} rows")
    x = table["dac_fraction_of_full_scale"].to_numpy(dtype=float)
    fitted = table["power_mw_fit"].to_numpy(dtype=float)
    valid = ~np.isnan(fitted)
    anchor_power, anchor_x = build_anchors(x[valid], fitted[valid], float(header["zero_level_mw"]))
    if anchor_power.size < 2 or not anchor_power[-1] > 0:
        raise CalibrationFileError(f"{path.name}: the fitted curve has no light")
    dark_start, dark_end = (float(v) for v in header["dark_mw"].split("/"))
    return IntensityCalibration(
        channel=header["channel"],
        controller_port=header["controller_port"],
        wavelength_nm=int(header["wavelength_nm"]),
        calibrated_at=header["calibrated_at"],
        software_commit=header["software_commit"],
        meter=header["meter"],
        sensor=header["sensor"],
        illumination_intensity_factor=float(header["illumination_intensity_factor"]),
        max_output=float(header["max_output"]),
        measured_in=header["measured_in"],
        dark_mw=(dark_start, dark_end),
        drift_fraction=float(header["drift_fraction"]),
        p_max_mw=float(anchor_power[-1]),
        top_dac_percent=float(header["top_dac_percent"]),
        zero_level_mw=float(header["zero_level_mw"]),
        rollover=_optional(header.get("rollover"), "none"),
        dac_percent_commanded=table["dac_percent_commanded"].to_numpy(dtype=float),
        dac_fraction_of_full_scale=x,
        power_mw_raw=table["power_mw_raw"].to_numpy(dtype=float),
        power_mw=table["power_mw"].to_numpy(dtype=float),
        power_mw_fit=fitted,
        anchor_power_mw=anchor_power,
        anchor_x=anchor_x,
        file_name=path.name,
        pulse_on_s=float(header["pulse_on_s"]),
        verification=_optional(header.get("verification"), "not run"),
        verification_points=_parse_points(header.get("verification_points", "")),
        hold_s=float(header.get("hold_s", "0")),
        hold_droop_fraction=_optional_float(header.get("hold_droop_fraction", "not run")),
    )


def load_calibration(path: Path) -> "Calibration":  # the alias is defined further down
    """Read a calibration file of either kind. Raises CalibrationFileError, naming the file, when it cannot be used."""
    path = Path(path)
    try:
        header = read_header(path)
        if "format" not in header:
            return LegacyCalibration.from_file(path)
        if header["format"] != FORMAT:
            raise CalibrationFileError(f"{path.name}: unknown format '{header['format']}'")
        return _read_intensity_calibration(path, header)
    except CalibrationFileError:
        raise
    except (OSError, ValueError, KeyError, IndexError, UnicodeDecodeError) as e:  # pandas' ParserError is a ValueError
        raise CalibrationFileError(f"{path.name}: {e}") from e


class LegacyCalibration:
    """A file written before 2026-10: "DAC Percent" and "Optical Power (mW)" columns, no header (design §6, §6.3).

    A healthy file keeps the old lookup exactly: normalize to the largest reading, np.interp on the raw curve. A file
    is repaired, in memory only, when the output falls at the top (rollover) or when the old lookup, replayed against
    the file's own measurements, misses a setpoint >= 10 % by more than 5 %: the repair is the fit new calibrations
    use. Either way the command is a DAC percent (these files recorded no factor), the slider cap stays
    max_output x 100 as before, and the DAC never goes past Max Output.
    """

    def __init__(self, file_name: str, dac_percent, power_mw):
        self.file_name = file_name
        power = np.asarray(power_mw, dtype=float)
        self._power_mw = power
        self._max_power = power.max()
        self._power_percent = power / self._max_power * 100  # same expression and order as master
        self._dac_percent = np.clip(np.asarray(dac_percent, dtype=float), 0, 100)
        self.repair_reason = self._find_repair_reason()
        self._fit = (
            fit_curve(self._dac_percent, self._dac_percent / 100.0, power) if self.repair_reason is not None else None
        )

    @classmethod
    def from_file(cls, path: Path) -> "LegacyCalibration":
        data = pd.read_csv(path)
        if "DAC Percent" not in data.columns or "Optical Power (mW)" not in data.columns:
            raise CalibrationFileError(
                f"{path.name}: neither a calibration header nor the legacy 'DAC Percent' / 'Optical Power (mW)' columns"
            )
        # Blank cells would make every command NaN (a crash in set_illumination); a hand-made file may be out of
        # order. Both are no-ops for files the old tool wrote, so those stay bit-identical to master.
        data = data.dropna(subset=["DAC Percent", "Optical Power (mW)"]).sort_values("DAC Percent", kind="stable")
        if len(data) < MIN_POINTS:
            raise CalibrationFileError(f"{path.name}: only {len(data)} rows")
        if not data["Optical Power (mW)"].max() > 0:
            raise CalibrationFileError(f"{path.name}: no light in the file")
        return cls(path.name, data["DAC Percent"].values, data["Optical Power (mW)"].values)

    def _old_lookup(self, intensity_percent: float):
        """Exactly master's IlluminationController._apply_lut."""
        intensity_percent = np.clip(intensity_percent, 0, 100)
        dac_percent = np.interp(intensity_percent, self._power_percent, self._dac_percent)
        return np.clip(dac_percent, 0, 100)

    def _find_repair_reason(self) -> Optional[str]:
        i_peak = find_rollover_peak(self._power_mw)
        if i_peak is not None:
            return f"output falls above DAC {self._dac_percent[i_peak]:.1f} %, calibrated range now ends there"
        order = np.argsort(self._dac_percent, kind="stable")
        worst = None
        for r in VERIFY_SETPOINTS:
            if r < VERIFY_MIN_GATED_PERCENT:
                continue
            dac = self._old_lookup(r)
            predicted = np.interp(dac, self._dac_percent[order], self._power_mw[order]) / self._max_power * 100
            error = (predicted - r) / r
            if abs(error) > VERIFY_REL_TOL and (worst is None or abs(error) > abs(worst[1])):
                worst = (r, error * 100)
        if worst is not None:
            return f"the old lookup is off by {worst[1]:+.1f} % at {worst[0]:g} %"
        return None

    def commanded_percent(self, intensity_percent: float, factor: float, max_output: float) -> Tuple[float, bool]:
        if self._fit is None:
            commanded = float(self._old_lookup(intensity_percent))
        else:
            fraction = intensity_percent / 100.0
            commanded = 100.0 * x_for_power_fraction(
                self._fit.anchor_power_mw, self._fit.anchor_x, self._fit.p_max_mw, fraction
            )
        return _clamp_to_ceiling(commanded, max_output)

    def cap_percent(self, max_output: float) -> float:
        return max_output * 100.0

    def identity_mismatch(self, wavelength_nm: Optional[int], controller_port: Optional[str]) -> Optional[str]:
        """Legacy files record neither wavelength nor port: nothing to compare (they apply by file name, as before)."""
        return None

    def describe(self) -> Dict[str, object]:
        description: Dict[str, object] = {
            "intensity_unit": "power_percent",
            "calibration_format": "legacy-repaired" if self.repair_reason else "legacy",
            "calibration_file": self.file_name,
        }
        if self.repair_reason:
            description["repair"] = self.repair_reason
        return description

    def _clamps_at(self, max_output: float) -> bool:
        return self.commanded_percent(self.cap_percent(max_output), 1.0, max_output)[1]

    def notes(self, factor: float, max_output: float) -> List[str]:
        notes = []
        if self.repair_reason:
            notes.append(
                f"legacy calibration repaired (optically unverified): {self.repair_reason}; recalibration recommended"
            )
        if self._clamps_at(max_output):
            notes.append(
                f"legacy calibration drives the DAC past Max Output near the top; those requests are clamped at "
                f"{max_output * 100:g} %: recalibrate"
            )
        return notes

    def status(self, factor: float, max_output: float) -> str:
        if self.repair_reason:
            return f"legacy, repaired (optically unverified): {self.repair_reason}"
        if self._clamps_at(max_output):
            return "legacy: clamped at Max Output near the top; recalibrate"
        return "legacy"


Calibration = Union[IntensityCalibration, LegacyCalibration]


def resolve_calibration_path(
    calibrations_dir: Path, referenced_file: Optional[str], wavelength_nm: Optional[int]
) -> Optional[Path]:
    """The file that applies to a channel (design §6.1): the one the illumination config references, else
    <wavelength>.csv (master applies that one whatever the config says). None when neither exists: a reference to a
    file that was never made is common (the config migration wrote one for every channel) and means uncalibrated."""
    candidates = []
    if referenced_file:
        candidates.append(Path(calibrations_dir) / Path(referenced_file).name)
    if wavelength_nm is not None:
        candidates.append(Path(calibrations_dir) / f"{wavelength_nm}.csv")
    for path in candidates:
        if path.is_file():
            return path
    return None


def calibration_status(
    calibrations_dir: Path,
    referenced_file: Optional[str],
    wavelength_nm: Optional[int],
    factor: float,
    max_output: float,
    controller_port: Optional[str] = None,
) -> str:
    """One line for the channel editor and the calibration dialog; "" when the channel has no calibration."""
    path = resolve_calibration_path(calibrations_dir, referenced_file, wavelength_nm)
    if path is None:
        return ""
    try:
        calibration = load_calibration(path)
    except CalibrationFileError as e:
        return f"invalid file: {e}"
    mismatch = calibration.identity_mismatch(wavelength_nm, controller_port) if controller_port else None
    if mismatch:
        return f"{path.name}: not applied: {mismatch}"
    return f"{path.name}: {calibration.status(factor, max_output)}"


INTENSITY_SUFFIX = {"power_percent": " % power", "dac_percent": " % DAC"}


def intensity_suffix(description: Dict[str, object]) -> str:
    """The live intensity control's suffix; software-intensity sources keep the plain " %"."""
    return INTENSITY_SUFFIX.get(description.get("intensity_unit"), " %")


def intensity_tooltip(description: Dict[str, object]) -> str:
    unit = description.get("intensity_unit")
    if unit == "dac_percent":
        return (
            "Percent of the DAC output, not linear in optical power. "
            "Utils > Illumination Power Calibration makes it linear."
        )
    if unit != "power_percent":
        return ""
    if str(description.get("calibration_format", "")).startswith("legacy"):
        text = f"Linear in power (legacy calibration {description.get('calibration_file')}"
        if "repair" in description:
            text += f"; repaired (optically unverified): {description['repair']}"
        return text + "). Recalibrate with Utils > Illumination Power Calibration."
    return (
        f"Linear in power: 50 % = half of {float(description['max_power_mw']):.4g} mW "
        f"(measured {description['measured_in']}, {str(description['calibrated_at'])[:10]}, "
        f"{description['calibration_file']})."
    )
