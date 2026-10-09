"""Running an illumination power calibration with a power meter: the sweep, the checks, saving, and the session the
dialog (control/widgets_intensity_calibration.py) and the headless CLI (tools/generate_intensity_calibrations.py)
drive. No Qt.

Safety (design §8): the sweep writes the raw DAC (bypassing the lookup and the cap) and never goes above the channel's
ceiling (Max Output); it only steps upward, so a reading over the sensor limit is caught one step past it. Every way
out of a channel's run turns all illumination off and the DAC to 0. While a run or the test beam drives the light, the
background heartbeat is paused and this code feeds the controller's watchdog itself, so if it stops (a hung meter, a
frozen GUI) the firmware turns the light off within the watchdog timeout.

Operating condition (design §5): each reading is taken with the light pulsed on for it, like an acquisition
exposure; the pulse length is recorded, and a continuous hold at 50 % records how far live view departs.
Design: AI-docs Squid/to-do/2026-10-08-power-linearization-gui-design.md §5, §7, §8.
"""

import datetime
import subprocess
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

import control._def
import squid.logging
from control.models.illumination_config import IlluminationChannelConfig, IlluminationType
from squid.intensity_calibration import (
    CALIBRATIONS_DIR_NAME,
    VERIFY_MIN_GATED_PERCENT,
    VERIFY_REL_TOL,
    VERIFY_SETPOINTS,
    CalibrationError,
    IntensityCalibration,
    calibration_status,
    fit_curve,
    load_calibration,
    write_calibration,
)
from squid.power_meter import (
    PowerMeter,
    PowerMeterError,
    PowerMeterInfo,
    PowerMeterOverrange,
    SimulatedPowerMeter,
    ThorlabsPowerMeter,
)

_log = squid.logging.get_logger(__name__)

SWEEP_STEP_FRACTION = 0.005
N_DARK = 5
CONVERGE_REL = 0.005  # two consecutive readings within this (or 3 sigma_dark) count as settled
MAX_READS = 5  # readings per point before it is flagged as not settling
RESIDUAL_REL = VERIFY_REL_TOL / 2  # a reading this far from the fit is measured again
REMEASURE_READS = 3
TOP_REMEASURE_READS = 5  # the point that sets P_max is always measured again this many times
MAX_REMEASURED_POINTS = 20
DRIFT_WARN_FRACTION = 0.02
HOLD_PERCENT = 50.0
HOLD_S = 10.0
DROOP_WARN_FRACTION = 0.03
TEST_BEAM_DEFAULT_PERCENT = 10.0
TEST_BEAM_MAX_S = 60.0

Progress = Callable[[str, int, int], None]


class CalibrationCancelled(Exception):
    """The run was cancelled; the light is off."""


class SensorLimitExceeded(RuntimeError):
    """A reading went above the sensor limit or the meter's range; the light is off."""


class WatchdogDeadlineMissed(RuntimeError):
    """The controller heard nothing for longer than its watchdog, which may have turned the light off mid-channel."""


@dataclass(frozen=True)
class ChannelTarget:
    """A channel that can be calibrated: epi-illumination, with a wavelength, on a controller DAC port (D1-D8)."""

    name: str
    wavelength_nm: int
    controller_port: str
    source_code: int
    max_output: float
    calibration_file: Optional[str] = None

    @property
    def ceiling_percent(self) -> float:
        return self.max_output * 100.0


@dataclass(frozen=True)
class ChannelResult:
    calibration: IntensityCalibration
    warnings: Tuple[str, ...] = ()


def calibration_targets(config: Optional[IlluminationChannelConfig]) -> List[ChannelTarget]:
    if config is None:
        return []
    return [
        ChannelTarget(
            channel.name,
            channel.wavelength_nm,
            channel.controller_port,
            config.get_source_code(channel),
            channel.max_output,
            channel.intensity_calibration_file,
        )
        for channel in config.channels
        if channel.type == IlluminationType.EPI_ILLUMINATION
        and channel.wavelength_nm
        and channel.controller_port.startswith("D")
    ]


def measured_in_label(has_confocal: bool, confocal_mode: bool) -> str:
    """The imaging mode a calibration is measured in, as recorded in its file."""
    if not has_confocal:
        return "n/a"
    return "confocal" if confocal_mode else "widefield"


def software_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short=8", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


class WatchdogDeadline:
    """The controller's watchdog turns all illumination off - and disarms itself - when no message reaches it for
    timeout_s. A stall anywhere in a channel (a slow read, a settle or hold that overslept) can leave such a gap, so
    every message the channel sends is recorded with sent(), and check() comes before what needs the watchdog to have
    held: turning the light on (it would be unprotected) and every reading (the light may have gone off under it).
    Once any message has seen a gap that long, every later check() fails: a message after the watchdog fired does not
    arm it again. timeout_s None: the watchdog is not ours to keep (the heartbeat runs), nothing is checked."""

    def __init__(self, timeout_s: Optional[float] = None, clock: Callable[[], float] = time.monotonic):
        self.timeout_s = timeout_s
        self.clock = clock
        self._last_message = clock()
        self._missed_gap_s: Optional[float] = None

    def _note_gap(self) -> None:
        gap = self.clock() - self._last_message
        if self.timeout_s is not None and gap > self.timeout_s and self._missed_gap_s is None:
            self._missed_gap_s = gap

    def sent(self) -> None:
        self._note_gap()
        self._last_message = self.clock()

    def check(self) -> None:
        self._note_gap()
        if self._missed_gap_s is not None:
            raise WatchdogDeadlineMissed(
                f"the controller heard nothing for {self._missed_gap_s:.1f} s, longer than its {self.timeout_s:g} s "
                "watchdog, which may have turned the light off: this channel's readings cannot be trusted; check the "
                "meter connection and that the computer is not overloaded, then run it again"
            )


class MicrocontrollerDacOutput:
    """One illumination port driven through the controller DAC at a raw commanded %, bypassing the lookup and the cap.
    Remembers what it last commanded, which is also what the simulated meter reads. Light on is refused once the
    deadline was missed; DAC levels and light off are always sent."""

    def __init__(self, microcontroller, source_code: int, deadline: Optional[WatchdogDeadline] = None):
        self._mcu = microcontroller
        self._source_code = source_code
        self._deadline = WatchdogDeadline() if deadline is None else deadline
        # Firmware 1.0+ can turn every port off at once: a port turned on elsewhere must not light the sensor
        self._all_ports = microcontroller.supports_multi_port()
        self.commanded_percent = 0.0
        self.is_on = False

    def _send(self, command, *args) -> None:
        command(*args)
        self._deadline.sent()
        self._mcu.wait_till_operation_is_completed()

    def set_percent(self, percent: float) -> None:
        self._send(self._mcu.set_illumination, self._source_code, percent)
        self.commanded_percent = percent

    def on(self) -> None:
        self._deadline.check()
        self._send(self._mcu.turn_on_illumination)
        self.is_on = True

    def off(self) -> None:
        """All illumination off (every port on firmware 1.0+, the selected source on older firmware)."""
        self._send(self._mcu.turn_off_all_ports if self._all_ports else self._mcu.turn_off_illumination)
        self.is_on = False

    def safe_off(self) -> None:
        """Light off and DAC 0: how every run ends."""
        try:
            self.off()
        finally:
            self.set_percent(0.0)


class _Measurer:
    """Takes readings with the light pulsed on at a raw DAC level: waits, reads until two readings agree, feeds the
    watchdog before every read, checks its deadline around every read, and enforces the sensor limit on every
    reading."""

    def __init__(self, output, meter, sensor_limit_mw, settle_s, sleep, clock, keepalive, deadline):
        self.output = output
        self.meter = meter
        self.sensor_limit_mw = sensor_limit_mw
        self.settle_s = settle_s
        self.sleep = sleep
        self.clock = clock
        self.keepalive = keepalive
        self.deadline = deadline
        self.sigma_dark = 0.0
        self.on_times: List[float] = []

    def _feed(self) -> None:
        self.deadline.check()
        self.keepalive()
        self.deadline.sent()

    def _fed_read(self) -> float:
        """Feed the watchdog, read, and fail if the watchdog may have fired before or during the reading (it then
        stays disarmed; the session arms it again before the next channel)."""
        self._feed()
        reading = self.meter.read_mw()
        self.deadline.check()
        return reading

    def _read(self, percent: float) -> float:
        try:
            reading = self._fed_read()
        except PowerMeterOverrange as e:
            raise SensorLimitExceeded(
                f"meter overrange at DAC {percent:.1f} %: lower Max Output or use a higher-power sensor"
            ) from e
        if reading > self.sensor_limit_mw:
            raise SensorLimitExceeded(
                f"{reading:.4g} mW at DAC {percent:.1f} % exceeds the sensor limit ({self.sensor_limit_mw:g} mW): "
                "lower Max Output or use a higher-power sensor"
            )
        return reading

    def _converged(self, percent: float) -> Tuple[float, bool]:
        previous = self._read(percent)
        for _ in range(MAX_READS - 1):
            current = self._read(percent)
            if abs(current - previous) <= max(CONVERGE_REL * abs(current), 3.0 * self.sigma_dark):
                return current, True
            previous = current
        return previous, False

    def dark(self) -> np.ndarray:
        self.output.off()
        self.sleep(self.settle_s)
        return np.array([self._fed_read() for _ in range(N_DARK)])

    def at(self, percent: float) -> Tuple[float, bool]:
        """A settled reading at `percent` with the light on only for it: (reading, settled)."""
        self.output.set_percent(percent)
        self.output.on()
        start = self.clock()
        try:
            self.sleep(self.settle_s)
            return self._converged(percent)
        finally:
            self.output.off()
            self.on_times.append(self.clock() - start)

    def median_at(self, percent: float, repeats: int) -> float:
        return float(np.median([self.at(percent)[0] for _ in range(repeats)]))

    def hold(self, percent: float, duration_s: float, cancelled: Callable[[], bool]) -> Tuple[float, float]:
        """Light on continuously at `percent` for duration_s: (reading at the start, reading at the end)."""
        self.output.set_percent(percent)
        self.output.on()
        try:
            self.sleep(self.settle_s)
            start, _ = self._converged(percent)
            begin = self.clock()
            while self.clock() - begin < duration_s:
                if cancelled():
                    raise CalibrationCancelled("cancelled during the continuous hold")
                self._feed()
                self.sleep(min(0.5, duration_s))
            end, _ = self._converged(percent)
        finally:
            self.output.off()
        return start, end


def _points_to_remeasure(power: np.ndarray, fitted: np.ndarray, settled: np.ndarray, top: int, sigma_dark: float):
    """The top point, points that did not settle, and points far from the fit, worst first, at most
    MAX_REMEASURED_POINTS (design §5.2: isolated bad readings must not shape the lookup)."""
    tolerance = np.maximum(RESIDUAL_REL * np.nan_to_num(fitted), 5.0 * sigma_dark)
    residual = np.abs(power - fitted)
    far = [
        int(i) for i in np.argsort(-np.nan_to_num(residual)) if np.isfinite(fitted[i]) and residual[i] > tolerance[i]
    ]
    unsettled = [int(i) for i in np.nonzero(~settled)[0]]
    ordered = [top] + [i for i in far + unsettled if i != top]
    return list(dict.fromkeys(ordered))[:MAX_REMEASURED_POINTS]


def _anomaly_requests(calibration: IntensityCalibration, sigma_dark: float) -> List[float]:
    """Requests whose lookup lands on a measured point still far from the fit after re-measuring (a real kink or
    dip in the source): verification must try them, or the fixed grid can step over a large error."""
    fitted = calibration.power_mw_fit
    tolerance = np.maximum(RESIDUAL_REL * np.nan_to_num(fitted), 5.0 * sigma_dark)
    requests = set()
    for i in np.nonzero(np.isfinite(fitted))[0]:
        if abs(calibration.power_mw[i] - fitted[i]) > tolerance[i]:
            x = calibration.dac_fraction_of_full_scale[i]
            power = float(np.interp(x, calibration.anchor_x, calibration.anchor_power_mw))
            requests.add(round(100.0 * power / calibration.p_max_mw, 1))
    return sorted(r for r in requests if r > 0)


def calibrate_channel(
    target: ChannelTarget,
    output: MicrocontrollerDacOutput,
    meter: PowerMeter,
    factor: float,
    *,
    meter_info: PowerMeterInfo,
    measured_in: str,
    sensor_limit_mw: float,
    settle_s: Optional[float] = None,
    hold_s: float = HOLD_S,
    progress: Progress = lambda message, done, total: None,
    cancelled: Callable[[], bool] = lambda: False,
    keepalive: Callable[[], None] = lambda: None,
    deadline: Optional[WatchdogDeadline] = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    now: Optional[datetime.datetime] = None,
) -> ChannelResult:
    """Sweep, check, fit, verify and hold one channel (design §5).

    Raises CalibrationCancelled, SensorLimitExceeded, WatchdogDeadlineMissed, PowerMeterError or CalibrationError.
    However it ends, all illumination is off and the DAC at 0 afterwards. `deadline` is the one `output` sends through.
    """
    if meter_info.wavelength_range_nm is not None:
        low, high = meter_info.wavelength_range_nm
        if not low <= target.wavelength_nm <= high:
            raise PowerMeterError(f"{target.wavelength_nm} nm is outside the sensor's range ({low:g}-{high:g} nm)")
    measure = _Measurer(
        output,
        meter,
        sensor_limit_mw,
        meter_info.settle_s if settle_s is None else settle_s,
        sleep,
        clock,
        keepalive,
        WatchdogDeadline(clock=clock) if deadline is None else deadline,
    )
    dac = np.linspace(0.0, target.ceiling_percent, int(round(1.0 / SWEEP_STEP_FRACTION)) + 1)
    x = dac / 100.0 * factor
    total = dac.size + MAX_REMEASURED_POINTS + len(VERIFY_SETPOINTS) + 5
    done = 0

    def step(stage: str) -> None:
        nonlocal done
        if cancelled():
            raise CalibrationCancelled(f"{target.name}: cancelled")
        done += 1
        progress(f"{target.name}: {stage}", min(done, total), total)

    def measure_everything():
        meter.set_wavelength(target.wavelength_nm)
        output.safe_off()
        step("dark")
        dark_start = measure.dark()
        measure.sigma_dark = float(dark_start.std(ddof=1))
        raw = np.empty(dac.size)
        settled = np.empty(dac.size, dtype=bool)
        for i, percent in enumerate(dac):
            step(f"sweep {i + 1}/{dac.size}")
            raw[i], settled[i] = measure.at(percent)
        step("dark")
        dark_end = measure.dark()
        sigma_dark = float(np.sqrt((dark_start.var(ddof=1) + dark_end.var(ddof=1)) / 2.0))
        measure.sigma_dark = sigma_dark
        dark = np.linspace(dark_start.mean(), dark_end.mean(), dac.size)

        # Measure again: the point that sets P_max, points that did not settle, points far from a first fit
        preliminary = fit_curve(dac, x, raw - dark, sigma_dark)
        remeasure = _points_to_remeasure(raw - dark, preliminary.fitted_mw, settled, preliminary.top_index, sigma_dark)
        for i in remeasure:
            step(f"measuring DAC {dac[i]:.1f} % again")
            raw[i] = measure.median_at(dac[i], TOP_REMEASURE_READS if i == preliminary.top_index else REMEASURE_READS)
            settled[i] = True

        calibration = IntensityCalibration.from_sweep(
            channel=target.name,
            controller_port=target.controller_port,
            wavelength_nm=target.wavelength_nm,
            calibrated_at=(now or datetime.datetime.now()).isoformat(timespec="seconds"),
            software_commit=software_commit(),
            meter=meter_info.meter,
            sensor=meter_info.sensor,
            factor=factor,
            max_output=target.max_output,
            measured_in=measured_in,
            dark_mw=(float(dark_start.mean()), float(dark_end.mean())),
            drift_fraction=0.0,
            dac_percent=dac,
            power_raw_mw=raw,
            power_mw=raw - dark,
            sigma_dark=sigma_dark,
            pulse_on_s=float(np.median(measure.on_times)),
        )

        # Drift: the point where the fit first reaches half of P_max (above a lasing threshold by construction),
        # measured again now and compared with the sweep
        reference = int(np.nonzero(calibration.power_mw_fit >= 0.5 * calibration.p_max_mw)[0][0])
        step("drift check")
        again, _ = measure.at(dac[reference])
        drift = abs(again - raw[reference]) / max(raw[reference] - dark[reference], 1e-9)
        calibration = replace(calibration, drift_fraction=float(drift))

        points = []
        for r in sorted(set(VERIFY_SETPOINTS) | set(_anomaly_requests(calibration, sigma_dark))):
            step(f"verify {r:g} %")
            commanded, _ = calibration.commanded_percent(r, factor, target.max_output)
            measured = measure.at(commanded)[0] - float(dark_end.mean())
            expected = r / 100.0 * calibration.p_max_mw
            points.append((float(r), (measured - expected) / expected * 100.0))

        droop = None
        if hold_s > 0:
            step(f"continuous hold at {HOLD_PERCENT:g} % for {hold_s:g} s")
            commanded, _ = calibration.commanded_percent(HOLD_PERCENT, factor, target.max_output)
            start, end = measure.hold(commanded, hold_s, cancelled)
            dark_mw = float(dark_end.mean())
            droop = (end - start) / max(start - dark_mw, 1e-9)
        return calibration, points, droop, int(np.sum(~settled))

    try:
        calibration, points, droop, unsettled = measure_everything()
    except BaseException as original:
        try:
            output.safe_off()
        except Exception as off_error:
            _log.error(f"{target.name}: turning the light off after '{original}' failed: {off_error}")
        raise
    output.safe_off()

    gated = [error for r, error in points if r >= VERIFY_MIN_GATED_PERCENT]
    verdict = "pass" if all(abs(error) <= VERIFY_REL_TOL * 100.0 for error in gated) else "fail"
    calibration = replace(
        calibration,
        verification=verdict,
        verification_points=tuple(points),
        hold_s=float(hold_s if droop is not None else 0.0),
        hold_droop_fraction=None if droop is None else float(droop),
    )
    warnings = []
    if calibration.drift_fraction > DRIFT_WARN_FRACTION:
        warnings.append(
            f"source drifted {calibration.drift_fraction * 100:.1f} % during the sweep; let it warm up and re-run"
        )
    if droop is not None and abs(droop) > DROOP_WARN_FRACTION:
        warnings.append(
            f"output changes {droop * 100:+.1f} % after {hold_s:g} s of continuous light: live view departs from "
            "the calibration, short exposures match it"
        )
    if unsettled:
        warnings.append(f"{unsettled} readings did not settle (noisy or slow meter)")
    return ChannelResult(calibration, tuple(warnings))


class _FileMoves:
    """The file moves of one Save, recorded one at a time as each succeeds, so a failure part-way through undoes
    exactly what was done."""

    def __init__(self) -> None:
        self._done: List[Tuple[Path, Path]] = []

    def move(self, source: Path, target: Path) -> None:
        source.replace(target)
        self._done.append((source, target))

    def undo(self) -> None:
        """Move everything back, latest first; a move that cannot be undone does not stop the others."""
        failures = []
        for source, target in reversed(self._done):
            try:
                target.replace(source)
            except OSError as e:
                failures.append(e)
        self._done.clear()
        if failures:
            raise failures[0]


def backup_existing(path: Path, now: datetime.datetime, moves: _FileMoves) -> Optional[Path]:
    """Move an existing calibration and its PNG to backup/<stem>.<timestamp><suffix>; return the CSV's new path."""
    stamp = now.strftime("%Y%m%dT%H%M%S")
    backup_dir = path.parent / "backup"
    backed_up = None
    for source in (path, path.with_suffix(".png")):
        if source.exists():
            backup_dir.mkdir(exist_ok=True)
            target = backup_dir / f"{source.stem}.{stamp}{source.suffix}"
            moves.move(source, target)
            if source == path:
                backed_up = target
    return backed_up


def write_plot(calibration: IntensityCalibration, path: Path) -> None:
    """Left: power vs commanded DAC (measured and fit). Right: measured vs requested with the ±5 % band."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    c = calibration
    figure = Figure(figsize=(11, 4.5))
    FigureCanvasAgg(figure)
    curve, verify = figure.subplots(1, 2)
    curve.plot(c.dac_percent_commanded, c.power_mw, ".", markersize=3, label="measured (dark subtracted)")
    curve.plot(c.dac_percent_commanded, c.power_mw_fit, "-", label="monotone fit")
    if c.rollover:
        curve.axvline(c.top_dac_percent, linestyle="--", color="red", label="calibrated top (rollover)")
    curve.set_xlabel("DAC (% commanded)")
    curve.set_ylabel("power (mW)")
    curve.set_title(f"{c.channel} ({c.measured_in}, {c.calibrated_at[:10]}, {c.pulse_on_s * 1000:.0f} ms pulses)")
    curve.legend()
    line = np.array([0.0, 100.0])
    verify.plot(line, line, "k-", linewidth=0.8)
    verify.fill_between(line, line * (1 - VERIFY_REL_TOL), line * (1 + VERIFY_REL_TOL), alpha=0.2, label="±5 %")
    requested = np.array([point[0] for point in c.verification_points])
    errors = np.array([point[1] for point in c.verification_points])
    if requested.size:
        verify.plot(requested, requested * (1 + errors / 100.0), "o", label="measured")
    verify.set_xlabel("requested (% power)")
    verify.set_ylabel(f"measured (% of {c.p_max_mw:.4g} mW)")
    title = f"verification: {c.verification_summary()}"
    if c.hold_droop_fraction is not None:
        title += f"; {c.hold_s:g} s continuous: {c.hold_droop_fraction * 100:+.1f} %"
    verify.set_title(title)
    verify.legend()
    figure.savefig(path, dpi=120, bbox_inches="tight", format="png")  # explicit: staged files end in .staged


def stage_calibration_files(calibration: IntensityCalibration, calibrations_dir: Path) -> Tuple[Path, Path]:
    """Write the CSV and PNG under hidden temporary names next to their final ones and read the CSV back. Nothing the
    runtime uses changes (it resolves calibrations by name, and the old pre-2026-10 loader only globs *.csv). On a
    failure the staged files are removed."""
    calibrations_dir.mkdir(parents=True, exist_ok=True)
    final = calibrations_dir / calibration.file_name
    csv_staged = final.with_name(f".{final.name}.staged")
    png_staged = final.with_name(f".{final.stem}.png.staged")
    try:
        write_calibration(calibration, csv_staged)
        load_calibration(csv_staged)
        write_plot(calibration, png_staged)
    except BaseException:
        csv_staged.unlink(missing_ok=True)
        png_staged.unlink(missing_ok=True)
        raise
    return csv_staged, png_staged


def _discard_staged(staged: Sequence[Tuple[IntensityCalibration, Path, Path]]) -> None:
    for _, csv_staged, png_staged in staged:
        csv_staged.unlink(missing_ok=True)
        png_staged.unlink(missing_ok=True)


def _put_back(path: Path, content: Optional[bytes]) -> None:
    """Return a file to `content` (None: it did not exist) if it no longer has it - after a partial write."""
    if content is None:
        path.unlink(missing_ok=True)
    elif not path.exists() or path.read_bytes() != content:
        path.write_bytes(content)


class CalibrationSession:
    """What the dialog and the CLI drive: the meter, the channels, one run at a time, and saving.

    Owns no Qt. run() is meant for a worker thread; cancel() may be called from any thread. While run() or the test
    beam drives the light, the controller's background heartbeat is paused and feed_watchdog() keeps the watchdog
    alive instead, so the firmware turns the light off if the code driving it stops.
    """

    def __init__(self, microcontroller, config_repo, dac_driven: bool = True):
        self.microcontroller = microcontroller
        self.config_repo = config_repo
        self.dac_driven = dac_driven
        self.settle_s: Optional[float] = None  # None: the meter's own settle time
        self.hold_s = HOLD_S
        self.sleep: Callable[[float], None] = time.sleep
        self.clock: Callable[[], float] = time.monotonic
        self.meter: Optional[PowerMeter] = None
        self._active: Optional[MicrocontrollerDacOutput] = None
        self._cancelled = False
        self._paused_heartbeat_s: Optional[float] = None
        # True once a run or the test beam has driven a port: the controller is then left on that port at DAC 0,
        # and the live channel has to be re-sent (live start and snap only turn the light on)
        self.hardware_touched = False

    @property
    def factor(self) -> float:
        return self.microcontroller.illumination_intensity_factor

    @property
    def watchdog_protected(self) -> bool:
        """The controller's illumination watchdog is armed (firmware 1.1+, heartbeat running or paused by us)."""
        return self._paused_heartbeat_s is not None or self.microcontroller.heartbeat_interval_s is not None

    def calibrations_dir(self) -> Path:
        return self.config_repo.machine_configs_path / CALIBRATIONS_DIR_NAME

    def targets(self) -> List[ChannelTarget]:
        if not self.dac_driven:
            return []
        return calibration_targets(self.config_repo.get_illumination_config())

    def current_status(self, target: ChannelTarget) -> str:
        status = calibration_status(
            self.calibrations_dir(),
            target.calibration_file,
            target.wavelength_nm,
            self.factor,
            target.max_output,
            controller_port=target.controller_port,
        )
        return status or "none"

    def source_state(self) -> Tuple[bool, float]:
        """(light on, DAC output as a fraction of full scale) of the port being driven: what the simulated meter sees."""
        output = self._active
        if output is None:
            return False, 0.0
        return output.is_on, output.commanded_percent / 100.0 * self.factor

    def connect(self, resource: Optional[str] = None) -> PowerMeterInfo:
        self.disconnect()
        if self.microcontroller.is_simulated:
            self.meter = SimulatedPowerMeter(self.source_state)
        else:
            self.meter = ThorlabsPowerMeter(resource=resource)
        return self.meter.info

    def disconnect(self) -> None:
        """Never raises: it runs while the dialog closes, which must still turn the beam off and restore the live
        channel when the meter was unplugged."""
        meter, self.meter = self.meter, None
        if meter is not None:
            try:
                meter.close()
            except PowerMeterError as e:
                _log.warning(f"{e}")

    def read_mw(self) -> float:
        if self.meter is None:
            raise PowerMeterError("no power meter connected")
        return self.meter.read_mw()

    def cancel(self) -> None:
        self._cancelled = True

    def clear_cancel(self) -> None:
        self._cancelled = False

    # ---------------------------------------------------------------- watchdog ownership
    def _arm_watchdog(self) -> None:
        """Arm the controller's watchdog with a full timeout while this session owns it. It is one-shot: after it
        fires it stays off until armed again, so it is armed on taking it over, before every channel and on giving
        it back."""
        if self._paused_heartbeat_s is not None:
            self.microcontroller.set_watchdog_timeout(control._def.WATCHDOG_TIMEOUT_S)
            self.microcontroller.wait_till_operation_is_completed()

    def _pause_heartbeat(self) -> None:
        if self._paused_heartbeat_s is None:
            interval = self.microcontroller.heartbeat_interval_s
            if interval is not None:
                self.microcontroller.stop_heartbeat()
                self._paused_heartbeat_s = interval
                self._arm_watchdog()

    def _resume_heartbeat(self) -> None:
        """Give the watchdog back to the background heartbeat, re-armed first: the firmware's watchdog is one-shot
        (it disables itself after turning the light off), so if it fired while this session owned it - the code
        driving the light stopped - it would otherwise stay off for the rest of the session."""
        if self._paused_heartbeat_s is not None:
            self._arm_watchdog()
            interval, self._paused_heartbeat_s = self._paused_heartbeat_s, None
            self.microcontroller.start_heartbeat(interval_s=interval)

    def feed_watchdog(self) -> None:
        """Keep the watchdog alive while this session owns it (the background heartbeat is paused)."""
        if self._paused_heartbeat_s is not None:
            self.microcontroller.send_heartbeat()

    # ---------------------------------------------------------------- driving the light
    def _output_for(
        self, target: ChannelTarget, deadline: Optional[WatchdogDeadline] = None
    ) -> MicrocontrollerDacOutput:
        self.hardware_touched = True
        self._active = MicrocontrollerDacOutput(self.microcontroller, target.source_code, deadline)
        return self._active

    def test_beam_on(self, target: ChannelTarget, percent_of_ceiling: float) -> None:
        """Light `target` at a raw DAC level, for centering the sensor. The caller turns it off (the dialog does so
        after TEST_BEAM_MAX_S at the latest) and calls feed_watchdog() meanwhile."""
        if self.meter is not None:
            self.meter.set_wavelength(target.wavelength_nm)
        self._pause_heartbeat()
        output = self._output_for(target)
        try:
            output.off()
            output.set_percent(target.ceiling_percent * min(max(percent_of_ceiling, 0.0), 100.0) / 100.0)
            output.on()
        except Exception:
            try:
                output.safe_off()
            finally:
                self._resume_heartbeat()
            raise

    def test_beam_off(self, target: ChannelTarget) -> None:
        output = self._active if self._active is not None else self._output_for(target)
        try:
            output.safe_off()
        finally:
            self._resume_heartbeat()

    def run(
        self,
        targets: Sequence[ChannelTarget],
        *,
        measured_in: str,
        sensor_limit_mw: float,
        progress: Progress = lambda message, done, total: None,
        on_channel_done: Callable[[str, object], None] = lambda name, result: None,
    ) -> Dict[str, Union[ChannelResult, Exception]]:
        """Calibrate each target in turn. A failed channel is reported and the run goes on; a cancel stops it."""
        if self.meter is None:
            raise PowerMeterError("Connect a power meter first")
        results: Dict[str, Union[ChannelResult, Exception]] = {}
        self._pause_heartbeat()
        try:
            for target in targets:
                if self._cancelled:
                    break
                self._arm_watchdog()  # in case it fired during the previous channel
                owned = self._paused_heartbeat_s is not None
                deadline = WatchdogDeadline(control._def.WATCHDOG_TIMEOUT_S if owned else None, self.clock)
                try:
                    result: Union[ChannelResult, Exception] = calibrate_channel(
                        target,
                        self._output_for(target, deadline),
                        self.meter,
                        self.factor,
                        meter_info=self.meter.info,
                        measured_in=measured_in,
                        sensor_limit_mw=sensor_limit_mw,
                        settle_s=self.settle_s,
                        hold_s=self.hold_s,
                        progress=progress,
                        cancelled=lambda: self._cancelled,
                        keepalive=self.feed_watchdog,
                        deadline=deadline,
                        sleep=self.sleep,
                        clock=self.clock,
                    )
                except CalibrationCancelled as e:
                    result = e
                except (SensorLimitExceeded, WatchdogDeadlineMissed, PowerMeterError, CalibrationError) as e:
                    _log.warning(f"illumination calibration of {target.name} failed: {e}")
                    result = e
                results[target.name] = result
                on_channel_done(target.name, result)
                if isinstance(result, CalibrationCancelled):
                    break
        finally:
            self._active = None
            self._resume_heartbeat()
        return results

    def save(
        self, calibrations: Sequence[IntensityCalibration], now: Optional[datetime.datetime] = None
    ) -> List[Tuple[Path, Optional[Path]]]:
        """Back up, write CSV + PNG, and point each channel's intensity_calibration_file at its file. Refuses, writing
        nothing, if a channel was renamed, removed or re-ported since the run."""
        config = self.config_repo.get_illumination_config(for_edit=True)
        channels = {channel.name: channel for channel in (config.channels if config is not None else [])}
        changed = [
            c.channel
            for c in calibrations
            if c.channel not in channels
            or channels[c.channel].controller_port != c.controller_port
            or channels[c.channel].wavelength_nm != c.wavelength_nm
        ]
        if changed:
            raise ValueError(
                f"changed in the illumination config since the run (renamed, removed or re-ported): "
                f"{', '.join(changed)}; run the calibration again"
            )
        now = now or datetime.datetime.now()
        directory = self.calibrations_dir()
        # All or nothing: stage every channel's files first, so a failure (a full disk, a plot error) leaves every
        # active calibration as it was; then publish move by move, and undo every move made - and put back the config
        # file, which is rewritten in place - if any step fails
        staged: List[Tuple[IntensityCalibration, Path, Path]] = []
        try:
            for c in calibrations:
                staged.append((c, *stage_calibration_files(c, directory)))
        except BaseException:
            _discard_staged(staged)
            raise
        config_path = self.config_repo.machine_configs_path / "illumination_channel_config.yaml"
        config_before = config_path.read_bytes() if config_path.exists() else None
        moves = _FileMoves()
        published: List[Tuple[Path, Optional[Path]]] = []
        try:
            for c, csv_staged, png_staged in staged:
                path = directory / c.file_name
                published.append((path, backup_existing(path, now, moves)))
                moves.move(csv_staged, path)
                moves.move(png_staged, path.with_suffix(".png"))
            for c in calibrations:
                channels[c.channel].intensity_calibration_file = c.file_name
            self.config_repo.save_illumination_config(config)
        except BaseException as original:
            # each step on its own: one that fails must not leave the others undone
            for what, undo in (
                ("the illumination config", lambda: _put_back(config_path, config_before)),
                ("the previous calibration files", moves.undo),
                ("the staged files", lambda: _discard_staged(staged)),
            ):
                try:
                    undo()
                except Exception as restore_error:
                    _log.error(f"restoring {what} after '{original}' failed: {restore_error}")
            raise
        return published
