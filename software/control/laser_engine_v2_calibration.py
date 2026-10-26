"""Laser engine v2 lines in Utils > Illumination Power Calibration: which channels are engine lines, getting a line
ready, driving it during a run, and what a run leaves behind. The run itself is squid/intensity_calibration_run.py
(CalibrationSession, calibrate_channel); this is the engine side it is handed. No widgets.

The sweep sets raw levels (% of the line's firmware ceiling). The checks after it go through the engine's set-point
call (LaserEngineV2LightSource.set_intensity) with the new calibration installed as a candidate in the
IlluminationController, so what is verified is what live view and acquisitions send. A reading counts only at the
set-point asked for: the line READY (on DF line 3 with the shutter open), holding the target Squid sent and at it.
Light on/off is the Squid controller's TTL for the line's port, through the run's watchdog deadline; the engine also
disarms by itself a few seconds after Squid stops polling it.
"""

import math
import time
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from control._def import source_code_to_port_index
from control.laser_engine_v2 import LaserEngineV2Error
from control.laser_engine_v2_link import EngineLinkError
from control.laser_engine_v2_status import SOURCE_560_LINE, LineInfo, LineState
from control.models.illumination_config import IlluminationType
from squid.intensity_calibration import EngineDrive, EngineLineIdentity, IntensityCalibration
from squid.intensity_calibration_run import (
    CalibrationCancelled,
    ChannelTarget,
    LightSourceStopped,
    MicrocontrollerDacOutput,
    Progress,
    WatchdogDeadline,
)

# STAT? prints set-points to 3 decimals (A or V): a target within this of the value sent is the value sent
TARGET_TOLERANCE = 0.0005 + 1e-6
# The engine opens the DF shutter on the poll after line 3 reads READY (it holds it open while the 560 is in use)
SHUTTER_OPEN_TIMEOUT_S = 5.0


def _is_aom(engine, line: int) -> bool:
    return engine.variant == "DF" and line == SOURCE_560_LINE


class EngineLineOutput:
    """One engine line as calibrate_channel drives it: the engine counterpart of MicrocontrollerDacOutput (the output
    protocol is documented there)."""

    overrange_advice = "use a higher-power sensor, or spread the beam over the sensor"

    def __init__(
        self,
        microcontroller,
        target: ChannelTarget,
        engine,
        light_source,
        illumination,
        deadline: Optional[WatchdogDeadline] = None,
    ):
        self._ttl = MicrocontrollerDacOutput(microcontroller, target.source_code, deadline)
        self._engine = engine
        self._light_source = light_source
        self._illumination = illumination
        self._wavelength = target.wavelength_nm
        self._line = target.engine_line
        self._key = f"L{target.engine_line}"
        self.is_aom = _is_aom(engine, target.engine_line)
        self._candidate: Optional[IntensityCalibration] = None
        self._expected_drive = 0.0  # % of the ceiling the line must hold: what was last asked for

    @property
    def is_on(self) -> bool:
        return self._ttl.is_on

    def level_text(self, percent: float) -> str:
        return f"{self._key} at {percent:.1f} % of its ceiling"

    def _engine_call(self, call: Callable, *args):
        try:
            return call(*args)
        except (LaserEngineV2Error, EngineLinkError) as e:
            raise LightSourceStopped(f"{self._key}: {e}") from e

    def _ready_line(self) -> LineInfo:
        status = self._engine_call(self._engine.poll_once)
        if status is None:
            raise LightSourceStopped(f"{self._key}: laser engine connection lost")
        info = status.channels[self._key]
        if info.state != LineState.READY:
            raise LightSourceStopped(f"{self._key} left READY ({info.state.name}): {info.reason}")
        if self.is_aom and status.shutter_open is not True:
            raise LightSourceStopped(f"{self._key}: the shutter is not open")
        return info

    def _sent_value(self, drive_percent: float, ceiling: float) -> float:
        """What LINE<n>:SET carries for a drive, computed as the engine computes it (set_line_intensity: amps
        formatted to 4 decimals; _aom_volts_for: volts rounded to the millivolt), in Python floats - numpy rounds an
        exact half differently (39 % of 2.045 A is 0.79755: sent as 0.7975)."""
        percent = max(0.0, min(100.0, float(drive_percent)))
        if self.is_aom:
            return round(float(ceiling) * min(1.0, max(0.0, percent / 100.0)), 3)
        return float(f"{percent / 100.0 * float(ceiling):.4f}")

    def _check_level(self) -> None:
        """A point is read only at the level asked for: the line holds the target sent (at STAT? precision) and has
        reached it (the firmware ends a ramp by setting now to target)."""
        info = self._ready_line()
        expected = self._sent_value(self._expected_drive, info.max)
        if abs(info.target - expected) > TARGET_TOLERANCE:
            raise LightSourceStopped(
                f"{self._key} holds {info.target:g}, not the {expected:g} Squid set: the set-point was not taken or "
                "was changed"
            )
        if info.now != info.target:
            raise LightSourceStopped(f"{self._key} did not reach its set-point ({info.now:g} of {info.target:g})")

    def _line_moved(self) -> None:
        """The line's set-point changed under the IlluminationController (a run, the test beam): every wavelength on
        the line sends its intensity again at its next set_intensity, even at an unchanged %."""
        self._illumination.forget_intensities(self._engine.wavelengths_for_line(self._line))

    def set_percent(self, percent: float) -> None:
        self._expected_drive = percent
        self._engine_call(self._engine.set_line_intensity, self._line, percent)
        self._line_moved()
        self._check_level()

    def set_power_percent(self, requested: float) -> None:
        """The engine's set-power call, as live view and acquisitions make it, with the candidate installed; the line
        must then hold the drive the candidate gives for `requested`."""
        self._expected_drive = self._candidate.commanded_percent(requested, 1.0, 1.0)[0]
        channel = self._light_source.channel_mappings[self._wavelength]  # what IlluminationController passes
        self._engine_call(self._light_source.set_intensity, channel, requested)
        self._line_moved()
        self._check_level()

    def check(self) -> None:
        """After every reading: the line must still be READY (a fault, the cover, the 560 key or a closed shutter would
        make the reading dark or wrong) and still hold the set-point asked for (another client, a firmware change).
        Counts as use, so the 560 nm source cannot idle off during a run."""
        self._check_level()
        self._engine.note_use([self._key])

    def on(self) -> None:
        self._ttl.set_percent(0.0)  # selects the port; the engine takes its level over USB, the controller DAC stays 0
        self._ttl.on()

    def off(self) -> None:
        self._ttl.off()

    def safe_off(self) -> None:
        """TTL off, then the line's set-point to 0; the line stays enabled, as Squid keeps engine lines up."""
        self._expected_drive = 0.0
        try:
            self._ttl.safe_off()
        finally:
            self._engine_call(self._engine.set_line_intensity, self._line, 0.0)
            self._line_moved()

    def recorded_identity(self) -> Dict[str, object]:
        drive = self._engine.line_identity(self._line)
        if not isinstance(drive, EngineLineIdentity):
            raise LightSourceStopped(f"{self._key}: {drive.reason}")
        fields: Dict[str, object] = {"engine": drive}
        if self.is_aom:
            status = self._engine.source_status
            measured = status.power_mw if status is not None else math.nan
            fields["source_power_mw"] = (float(self._engine.source_power_setpoint_mw), float(measured))
        return fields

    def use_candidate(self, calibration: IntensityCalibration) -> None:
        self._candidate = calibration
        self._illumination.install_candidate(self._wavelength, calibration)

    def clear_candidate(self) -> None:
        self._candidate = None
        self._illumination.clear_candidate(self._wavelength)


class EngineCalibration:
    """What CalibrationSession needs from an instrument whose light source is laser engine v2."""

    def __init__(self, illumination):
        self.illumination = illumination
        self.light_source = illumination.light_source
        self.engine = illumination.light_source.engine
        self.ready_timeout_s = self.engine.READY_TIMEOUT_S
        self.shutter_open_timeout_s = SHUTTER_OPEN_TIMEOUT_S

    def targets(self, config) -> List[ChannelTarget]:
        """Epi-illumination channels whose port is the engine line their wavelength drives (the TTL map's port: with a
        wavelength on two ports, only the one the map uses), on lines fitted in this variant."""
        if config is None or self.engine.is_connection_lost():
            return []
        status = self.engine.get_latest_status() or self.engine.poll_once()
        if status is None:
            return []
        targets = []
        for channel in config.channels:
            if channel.type != IlluminationType.EPI_ILLUMINATION or not channel.wavelength_nm:
                continue
            line = self.engine.line_for_wavelength(channel.wavelength_nm)
            if line is None or channel.controller_port != f"D{line}":
                continue
            info = status.channels.get(f"L{line}")
            if info is None or info.state == LineState.UNUSED:
                continue
            targets.append(
                ChannelTarget(
                    channel.name,
                    channel.wavelength_nm,
                    channel.controller_port,
                    config.get_source_code(channel),
                    1.0,  # the firmware ceiling is the maximum: Max Output is not used on engine lines
                    channel.intensity_calibration_file,
                    engine_line=line,
                )
            )
        return targets

    def drive(self, wavelength_nm: int) -> EngineDrive:
        return self.light_source.engine_drive(wavelength_nm)

    def ceiling_text(self, target: ChannelTarget) -> str:
        drive = self.drive(target.wavelength_nm)
        return f"{drive.ceiling:g} {drive.ceiling_unit}" if isinstance(drive, EngineLineIdentity) else "unknown"

    def run_notes(self, targets: Iterable[ChannelTarget]) -> List[str]:
        notes = []
        for target in targets:
            if _is_aom(self.engine, target.engine_line):
                mw = self.engine.source_power_setpoint_mw
                if mw is None:
                    notes.append(f"{target.name}: the 560 nm source is not configured.")
                else:
                    notes.append(
                        f"{target.name} is measured at the 560 nm laser power set in the Laser Engine tab "
                        f"({mw:.0f} mW); its calibration holds at any laser power."
                    )
        return notes

    def prepare(
        self,
        target: ChannelTarget,
        progress: Progress,
        keepalive: Callable[[], None],
        cancelled: Callable[[], bool],
    ) -> None:
        """Wake the line and wait until the engine reports it READY (TEC at temperature; for the 560 nm line the source
        at the operator's power) and, on DF line 3, the shutter open. The light stays off; keepalive() runs on every
        wait step."""
        key = f"L{target.engine_line}"

        def stop() -> bool:
            keepalive()
            status = self.engine.get_latest_status()
            info = status.channels.get(key) if status is not None else None
            if info is not None and not info.is_ready:
                progress(f"{target.name}: waiting for {key} ({info.reason})", 0, 1)
            return cancelled()

        self.engine.wake_up(key)
        try:
            ready = self.engine.wait_until_ready([key], timeout_s=self.ready_timeout_s, cancel_fn=stop)
        except LaserEngineV2Error as e:
            raise LightSourceStopped(f"{target.name}: {e}") from e
        if not ready:
            if cancelled():
                raise CalibrationCancelled(f"{target.name}: cancelled")
            if self.engine.is_connection_lost():
                raise LightSourceStopped(f"{target.name}: laser engine connection lost")
            status = self.engine.get_latest_status()
            info = status.channels.get(key) if status is not None else None
            reason = info.reason if info is not None else "no status from the laser engine"
            raise LightSourceStopped(f"{target.name}: {key} not ready within {self.ready_timeout_s:g} s ({reason})")
        if _is_aom(self.engine, target.engine_line):
            self._wait_for_shutter(target, keepalive, cancelled)

    def _wait_for_shutter(self, target: ChannelTarget, keepalive: Callable[[], None], cancelled) -> None:
        deadline = time.monotonic() + self.shutter_open_timeout_s
        while True:
            keepalive()
            status = self.engine.poll_once()
            if status is not None and status.shutter_open:
                return
            if cancelled():
                raise CalibrationCancelled(f"{target.name}: cancelled")
            if status is None or time.monotonic() >= deadline:
                raise LightSourceStopped(
                    f"{target.name}: L3 is ready but the shutter did not open (see the Laser Engine tab)"
                )
            time.sleep(self.engine.query_interval_s)

    def ready_now(self, target: ChannelTarget) -> None:
        """For the test beam, on the GUI thread (no waiting): wake the line and refuse unless it is READY now (and, on
        DF line 3, the shutter open)."""
        key = f"L{target.engine_line}"
        self.engine.wake_up(key)
        status = self.engine.poll_once()
        info = status.channels.get(key) if status is not None else None
        if info is None:
            raise LightSourceStopped(f"{key}: laser engine connection lost")
        if not info.is_ready:
            raise LightSourceStopped(f"{key} is not ready ({info.reason}); try again in a moment")
        if _is_aom(self.engine, target.engine_line) and not status.shutter_open:
            raise LightSourceStopped(f"{key}: the shutter is not open yet; try again in a moment")

    def output(self, microcontroller, target: ChannelTarget, deadline: Optional[WatchdogDeadline] = None):
        return EngineLineOutput(microcontroller, target, self.engine, self.light_source, self.illumination, deadline)

    def simulated_light(self, microcontroller, target: ChannelTarget) -> Tuple[bool, float]:
        """What a simulated meter sees from this line (simulation only): the simulated controller's TTL for the port,
        and the simulated engine's actual level while the line is on (DF line 3 only through an open shutter), as a
        fraction of the ceiling - never what was merely asked for."""
        fake = self.engine.sim_engine
        line = fake.lines[target.engine_line - 1]
        ttl = microcontroller.simulated_port_is_on(source_code_to_port_index(target.source_code))
        through = not _is_aom(self.engine, target.engine_line) or fake.shut_open
        if not (ttl and through and line["st"] == "ON"):
            return False, 0.0
        return True, line["now"] / line["max"]
