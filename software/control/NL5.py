import control.RCM_API as RCM_API
import control._def
import squid.logging
import dataclasses
import json
import math
import re
import threading
import time

import serial
import serial.tools.list_ports


# USB serial number of the SmarAct SCU controller that drives the AION internal (slit) stage.
# Configure per machine with RCM_INTERNAL_STAGE_SN in the .ini (e.g. "SCU", "AL05NB1GA").
DEFAULT_RCM_INTERNAL_STAGE_SN = "AL05NB1GA"

SLIT_POSITIONS_UM = {88: 1500, 44: 900, 22: 300, 33: -300, 66: -900, 132: -1500}

# Slit oscillation limits accepted by the GUI. The serial link would allow ~30 Hz (two ~16 ms move commands
# per cycle), but the stage has to travel and settle: measured on AION, a 100 µm move is within 1 µm of its
# target after ~134 ms, so at 3 Hz (167 ms per end) a +/-50 µm swing reaches both ends.
# The slits sit 600 µm apart, so a swing of more than 300 µm would reach the neighbouring slit.
SLIT_OSCILLATION_MAX_UM = 250
SLIT_OSCILLATION_MIN_HZ = 0.1
SLIT_OSCILLATION_MAX_HZ = 3.0
# Sweep speed limits. The stage moves ~1.5 µm/ms on its own and trails a ramp of setpoints by ~100 ms (measured);
# with a setpoint every 20 ms, above ~500 µm/s the steps are too coarse to be a sweep.
SLIT_SWEEP_MIN_UM_S = 5
SLIT_SWEEP_MAX_UM_S = 500
SLIT_OSCILLATION_MODES = ("jump", "sweep")


@dataclasses.dataclass(frozen=True)
class SlitOscillation:
    """How the slit oscillates around its centre (the selected slit's position plus the slit offset).

    mode "jump": move to centre + plus_um, then centre - minus_um, ..., holding each end, at frequency_hz.
    mode "sweep": travel between the two ends at a constant speed_um_s, waiting at each end until the stage is
    there; the frequency follows from the speed and the swing.
    """

    plus_um: float = 50
    minus_um: float = 50
    mode: str = "jump"
    frequency_hz: float = 1.0
    speed_um_s: float = 200

    def validate(self):
        if not (0 <= self.plus_um <= SLIT_OSCILLATION_MAX_UM and 0 <= self.minus_um <= SLIT_OSCILLATION_MAX_UM):
            raise ValueError(
                f"Oscillation positions must be 0-{SLIT_OSCILLATION_MAX_UM} µm, got +{self.plus_um} / -{self.minus_um}"
            )
        if self.mode not in SLIT_OSCILLATION_MODES:
            raise ValueError(f"Unknown oscillation mode {self.mode!r}; expected one of {SLIT_OSCILLATION_MODES}")
        if not SLIT_OSCILLATION_MIN_HZ <= self.frequency_hz <= SLIT_OSCILLATION_MAX_HZ:
            raise ValueError(
                f"Oscillation frequency {self.frequency_hz} Hz outside "
                f"{SLIT_OSCILLATION_MIN_HZ}-{SLIT_OSCILLATION_MAX_HZ} Hz"
            )
        if not SLIT_SWEEP_MIN_UM_S <= self.speed_um_s <= SLIT_SWEEP_MAX_UM_S:
            raise ValueError(
                f"Sweep speed {self.speed_um_s} µm/s outside {SLIT_SWEEP_MIN_UM_S}-{SLIT_SWEEP_MAX_UM_S} µm/s"
            )

    def sweep_frequency_hz(self):
        """Upper bound of the sweep's frequency: the travel time alone, without the waits at the ends."""
        travel_um = 2 * (self.plus_um + self.minus_um)
        return self.speed_um_s / travel_um if travel_um > 0 else 0.0

    def describe(self):
        swing = f"+{self.plus_um:g} / -{self.minus_um:g} µm"
        if self.mode == "sweep":
            return f"{swing}, sweep at {self.speed_um_s:g} µm/s"
        return f"{swing}, jump at {self.frequency_hz:g} Hz"


# Scanner line speed (LINE_FREQUENCY, mrad/s) limits accepted by the GUI.
LINE_SPEED_MIN = 100
LINE_SPEED_MAX = 20000
SPEED_INPUT_MODES = ("line_speed", "fps")


class _ScanSpeedMixin:
    """Conversion between the scanner line speed and the scan-limited frame rate.

    One frame is one sweep of the line across the scanned field. The scanner travels +/- the
    scan amplitude (AMPLITUDE_X, mrad), so the sweep is twice the amplitude, scaled by
    FIELD_OF_VIEW_X / MAX_CAM_PIX_X, and
        fps = line_speed [mrad/s] / sweep [mrad].
    On the AION that ratio is not capped at 1: the unit reports MAX_CAM_PIX_X = 2048 for the
    3200 px Aries, and FIELD_OF_VIEW_X = 2700 at 69 mrad scans about 2736 camera rows, i.e. a
    sweep of 2 x 69 x 2700 / 2048 = 182 mrad (36.4 ms at 5000 mrad/s, measured 2026-10-22).
    The NL5 keeps the cap at 1 (not measured beyond the full field).
    Measured on AION: the time to a frame grows by 2 x amplitude / line speed, so halving the
    amplitude or FIELD_OF_VIEW_X saved 26 ms at 73 mrad and 3000 mrad/s.
    The exposure delay, the jump back (~4 ms at the full width) and the camera's readout and
    transfer (~42 ms for a full Aries frame) are not included, so this is the upper bound the
    scanner allows, not the camera frame rate.

    The camera is level-triggered by the unit (Tucsen "trigger width" mode): its exposure is however
    long the unit holds the trigger high, which is the exposure delay (EXPOSURE_DELAY) followed by
    the scan. The camera's own exposure setting is ignored. So on this unit
        exposure = exposure delay + scan time,
    and a wanted exposure is reached by setting the line speed (line_speed_for_exposure). Measured on
    AION (camera + unit, 2026-10-22): the frame latency follows this exposure within ~1 ms from 25 to
    200 ms.
    """

    max_cam_pix_x = None
    # Slit oscillation settings for this session (not saved); immutable, so the shared default is safe.
    slit_oscillation = SlitOscillation()

    def scan_sweep_mrad(self):
        fraction = 1.0
        if self.max_cam_pix_x and self.fov_x:
            fraction = self.fov_x / self.max_cam_pix_x
            if not getattr(self, "is_aion", False):
                fraction = min(1.0, fraction)
        return max(1e-6, 2 * self.scan_amplitude * fraction)

    def line_speed_to_fps(self, line_speed):
        return line_speed / self.scan_sweep_mrad()

    def fps_to_line_speed(self, fps):
        return int(round(fps * self.scan_sweep_mrad()))

    @staticmethod
    def clamp_line_speed(speed):
        return int(min(LINE_SPEED_MAX, max(LINE_SPEED_MIN, round(speed))))

    def scan_time_ms(self, line_speed=None):
        """Time of one sweep at `line_speed` (default: the current line speed)."""
        speed = self.line_speed if line_speed is None else line_speed
        return 1000.0 * self.scan_sweep_mrad() / speed

    def exposure_time_ms(self, line_speed=None):
        """The camera exposure the unit produces: exposure delay + scan time."""
        return self.exposure_delay_ms + self.scan_time_ms(line_speed)

    def line_speed_for_exposure(self, exposure_ms):
        """The line speed (within the unit's limits) whose exposure is closest to `exposure_ms`."""
        scan_ms = exposure_ms - self.exposure_delay_ms
        if scan_ms <= 0:
            return LINE_SPEED_MAX
        return self.clamp_line_speed(1000.0 * self.scan_sweep_mrad() / scan_ms)

    def exposure_limits_ms(self):
        """(min, max) exposure the unit can produce with the current delay, amplitude and FOV."""
        return self.exposure_time_ms(LINE_SPEED_MAX), self.exposure_time_ms(LINE_SPEED_MIN)

    # The line speed last written to the unit by set_line_speed(), whoever called it. None until the first write:
    # the unit's line speed at start-up is not known to match the saved one.
    _line_speed_written = None

    def set_line_speed(self, speed, save_setting=False):
        """Clamp, send to the unit (_send_line_speed) and record what was written, so that apply_exposure_time()
        knows the unit's real line speed after a manual change too."""
        self.line_speed = self.clamp_line_speed(speed)
        self._send_line_speed(self.line_speed)
        self._line_speed_written = self.line_speed
        if save_setting:
            self.save_settings()

    def _send_line_speed(self, line_speed):
        raise NotImplementedError

    def apply_exposure_time(self, exposure_ms):
        """Set the line speed for this exposure; returns the exposure the unit will actually produce.

        The line speed is sent to the unit only when it differs from the last one written, by this path or by a
        manual set_line_speed() (at most one write per channel change). The first call always writes.
        """
        speed = self.line_speed_for_exposure(exposure_ms)
        if speed != self._line_speed_written:
            self.set_line_speed(speed)
        return self.exposure_time_ms()

    def scan_timing_metadata(self, line_speed=None):
        """The scan timing behind an exposure at `line_speed` (default: the current one), for acquisition metadata."""
        speed = self.line_speed if line_speed is None else line_speed
        return {
            "line_speed_mrad_s": speed,
            "scan_time_ms": self.scan_time_ms(speed),
            "exposure_ms": self.exposure_time_ms(speed),
        }

    def settings_metadata(self):
        """Every setting of the unit Squid holds, for acquisition metadata (the real unit adds the device's readback)."""
        metadata = {
            "device": getattr(self, "device_name", "NL5"),
            "exposure_delay_ms": self.exposure_delay_ms,
            "scan_amplitude_mrad": self.scan_amplitude,
            "offset_x_mrad": self.offset_x,
            "bypass_offset_mrad": self.bypass_offset,
            "fov_x_px": self.fov_x,
            "max_cam_pix_x": self.max_cam_pix_x,
            "sweep_mrad": self.scan_sweep_mrad(),
            "line_speed_mrad_s": self.line_speed,
        }
        for key in ("slit_width_um", "slit_offset_um"):
            if hasattr(self, key):
                metadata[key] = getattr(self, key)
        return metadata

    def set_speed_input_mode(self, mode):
        if mode not in SPEED_INPUT_MODES:
            raise ValueError(f"Unknown speed input mode {mode!r}; expected one of {SPEED_INPUT_MODES}")
        self.speed_input_mode = mode
        self.save_settings()

    def load_settings(self):
        try:
            with open("NL5_settings.json", "r") as file:
                settings = json.load(file)
                self.scan_amplitude = settings.get("scan_amplitude", 70.0)
                self.offset_x = settings.get("offset_x", 0.0)
                self.bypass_offset = settings.get("bypass_offset", 0.0)
                self.fov_x = settings.get("fov_x", 2048)
                self.exposure_delay_ms = settings.get("exposure_delay_ms", 30)
                self.line_speed = settings.get("line_speed", 3000)
                self.slit_width_um = settings.get("slit_width_um", 88)
                self.slit_offset_um = settings.get("slit_offset_um", 0.0)
                self.speed_input_mode = settings.get("speed_input_mode", "line_speed")
                if self.speed_input_mode not in SPEED_INPUT_MODES:
                    self.speed_input_mode = "line_speed"

        except FileNotFoundError:
            self.scan_amplitude = 70.0
            self.offset_x = 0.0
            self.bypass_offset = 0.0
            self.exposure_delay_ms = 30
            self.line_speed = 3000
            self.fov_x = 2048
            self.slit_width_um = 88
            self.slit_offset_um = 0.0
            self.speed_input_mode = "line_speed"

    def save_settings(self):
        settings = {
            "scan_amplitude": self.scan_amplitude,
            "offset_x": self.offset_x,
            "bypass_offset": self.bypass_offset,
            "fov_x": self.fov_x,
            "exposure_delay_ms": self.exposure_delay_ms,
            "line_speed": self.line_speed,
            "slit_width_um": self.slit_width_um,
            "slit_offset_um": self.slit_offset_um,
            "speed_input_mode": self.speed_input_mode,
        }
        with open("NL5_settings.json", "w") as file:
            json.dump(settings, file)


class RCMInternalStage:
    BAUD = 9600
    ANSWER_TIMEOUT_S = 0.1  # a GP reply comes back ~31 ms after the query (measured)
    # A command takes ~16 ms to send at 9600 baud. Without a write timeout a stalled port blocks the write,
    # and with it the serial lock and every join of the oscillation thread (stop, home, shutdown), forever.
    WRITE_TIMEOUT_S = 1.0
    HOMING_WAIT_S = 15
    REFERENCE_MOVE_WAIT_S = 2
    COMMAND_GAP_S = 0.1
    DEFAULT_HOLD_MS = 10
    SLIT_CHANNEL = 0
    STOP_POLL_S = 0.05  # how quickly the oscillation thread notices a stop
    # Sweep: a new setpoint every SWEEP_UPDATE_S (a move command takes ~16 ms to send; faster ones queue up).
    # At each end, read the position back every ARRIVE_POLL_S (leaving the line free in between) until it is
    # within ARRIVE_TOLERANCE_UM (a 100 µm move settles to 1 µm in ~134 ms), giving up after ARRIVE_TIMEOUT_S.
    # Each setpoint is held for SWEEP_HOLD_MS, well past the longest gap to the next one (the end wait, a
    # missing reply, then up to 200 ms for the first 1 µm step at 5 µm/s); the next setpoint replaces the hold.
    SWEEP_UPDATE_S = 0.02
    SWEEP_HOLD_MS = 5000
    ARRIVE_TOLERANCE_UM = 1.0
    ARRIVE_TIMEOUT_S = 0.5
    ARRIVE_POLL_S = 0.01
    _POSITION_REPLY = re.compile(r"P(-?\d+(?:\.\d+)?)$")

    def __init__(self, serial_number):
        self._log = squid.logging.get_logger(self.__class__.__name__)
        port = self._find_port(serial_number)
        if port is None:
            raise RuntimeError(f"RCM internal stage with USB serial number '{serial_number}' not found")
        self.ser = serial.Serial(
            port=port,
            baudrate=self.BAUD,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.ANSWER_TIMEOUT_S,
            write_timeout=self.WRITE_TIMEOUT_S,
            rtscts=False,
            dsrdtr=False,
            xonxoff=False,
        )
        self.ser.dtr = False
        self.offset_um = 0.0
        self.current_slit_um = None
        # Serialises commands from the GUI thread and the oscillation thread.
        self._serial_lock = threading.Lock()
        self._oscillation_thread = None
        self._oscillation_stop = threading.Event()
        self.oscillation = None  # SlitOscillation last started; the running thread reads it at every move
        self._osc_to_plus = True  # the end the oscillation heads for next
        self._osc_offset_um = 0.0  # where the oscillation last sent the slit, relative to its centre
        self._log.info(f"RCM internal stage (SmarAct SCU, USB SN '{serial_number}') connected on {port}; homing")
        self.home()

    @staticmethod
    def _find_port(serial_number):
        for port in serial.tools.list_ports.comports():
            if port.serial_number == serial_number:
                return port.device
        return None

    def _send(self, command, gap_s=None):
        with self._serial_lock:
            self.ser.reset_input_buffer()
            self.ser.write(f":{command}\n".encode("ascii"))
            time.sleep(self.COMMAND_GAP_S if gap_s is None else gap_s)

    def read_position_um(self):
        """The stage's position (GP, µm with 0.1 µm resolution), or None if it did not answer."""
        with self._serial_lock:
            self.ser.reset_input_buffer()
            self.ser.write(f":GP{self.SLIT_CHANNEL}\n".encode("ascii"))
            reply = self.ser.readline().decode("ascii", "replace").strip()
        match = self._POSITION_REPLY.search(reply)
        return float(match.group(1)) if match else None

    def _slit_centre(self):
        return SLIT_POSITIONS_UM[self.current_slit_um] + int(round(self.offset_um))

    def home(self):
        # Commands sent during homing abort it, so pause the oscillation and resume it afterwards.
        was_oscillating = self.is_oscillating()
        self._join_oscillation_thread()
        ch = self.SLIT_CHANNEL
        self._send(f"SCLF{ch}F5000")
        self._send(f"SST{ch}T22")
        # CS homes the stage; sending commands during this aborts the process
        self._send(f"CS{ch}")
        time.sleep(self.HOMING_WAIT_S)
        self._send(f"MTR{ch}Z1")
        time.sleep(self.REFERENCE_MOVE_WAIT_S)
        if self.current_slit_um is not None:
            self.set_slit_width(self.current_slit_um)
        if was_oscillating:
            self.start_oscillation(self.oscillation)

    def set_slit_width(self, width_um):
        if width_um not in SLIT_POSITIONS_UM:
            raise ValueError(f"Unknown slit width {width_um} µm; expected one of {list(SLIT_POSITIONS_UM)}")
        self.current_slit_um = width_um
        if self.is_oscillating():
            return  # the oscillation moves around the new centre from its next move
        self._send(f"MPA{self.SLIT_CHANNEL}P{self._slit_centre()}H{self.DEFAULT_HOLD_MS}")

    def set_offset(self, offset_um):
        self.offset_um = offset_um
        if self.current_slit_um is not None:
            self.set_slit_width(self.current_slit_um)

    def start_oscillation(self, oscillation):
        """Oscillate the slit around its centre as described by a SlitOscillation.

        The centre is the selected slit's position plus the slit offset, so changing either moves the
        oscillation with it. Runs until stop_oscillation(); calling this again while it runs changes the
        parameters (also the mode) from the next move, without restarting the cycle.
        """
        if self.current_slit_um is None:
            raise RuntimeError("Select a slit before starting the oscillation")
        if self.is_oscillating():
            self.update_oscillation(oscillation)
            return
        oscillation.validate()
        if self._oscillation_thread is not None:
            # one that ended on an error left the slit somewhere along its swing: back to the centre first
            self.stop_oscillation()
        self.oscillation = oscillation
        self._osc_to_plus = True
        self._osc_offset_um = 0.0  # the slit is at its centre
        self._oscillation_stop.clear()
        self._oscillation_thread = threading.Thread(target=self._oscillate, name="SlitOscillation", daemon=True)
        self._oscillation_thread.start()
        self._log.info(f"Slit oscillation started: {oscillation.describe()}")

    def update_oscillation(self, oscillation):
        """Change the settings (also the mode) of a running oscillation from its next move; never starts one."""
        oscillation.validate()
        self.oscillation = oscillation
        if self.is_oscillating():
            self._log.info(f"Slit oscillation changed: {oscillation.describe()}")

    def is_oscillating(self):
        return self._oscillation_thread is not None and self._oscillation_thread.is_alive()

    def stop_oscillation(self):
        """Stop the oscillation, also one that ended on an error, and return the slit to its centre."""
        if self._oscillation_thread is None:
            return
        self._join_oscillation_thread()
        self.set_slit_width(self.current_slit_um)
        self._log.info("Slit oscillation stopped")

    def _join_oscillation_thread(self):
        if self._oscillation_thread is not None:
            self._oscillation_stop.set()
            self._oscillation_thread.join()
            self._oscillation_thread = None
            # the thread's last move went out without the usual gap; give the SCU that gap now
            time.sleep(self.COMMAND_GAP_S)

    def _oscillate(self):
        try:
            self._oscillate_loop()
        except Exception:
            self._log.exception("Slit oscillation stopped by an error")

    def _oscillate_loop(self):
        # Each mode runs until a stop or until the mode is changed, then hands over where it left off.
        self._warned = set()
        while not self._oscillation_stop.is_set():
            if self.oscillation.mode == "sweep":
                self._sweep()
            else:
                self._jump()

    def _running(self, mode):
        return not self._oscillation_stop.is_set() and self.oscillation.mode == mode

    def _warn_once(self, key, message):
        if key not in self._warned:
            self._warned.add(key)
            self._log.warning(message)

    def _sleep_until(self, deadline, mode):
        # time.sleep keeps sub-ms timing on Windows; Event.wait overshoots by 5-11 ms (measured).
        while self._running(mode):
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                break
            time.sleep(min(remaining, self.STOP_POLL_S))

    def _move(self, offset_um, hold_ms):
        self._send(f"MPA{self.SLIT_CHANNEL}P{self._slit_centre() + int(round(offset_um))}H{hold_ms}", gap_s=0)
        self._osc_offset_um = offset_um

    def _end(self, oscillation):
        return oscillation.plus_um if self._osc_to_plus else -oscillation.minus_um

    def _jump(self):
        deadline = time.perf_counter()
        while self._running("jump"):
            oscillation = self.oscillation
            half_period_s = 0.5 / oscillation.frequency_hz
            # Hold each end past the next move's due time, so a late move never finds the closed loop let go;
            # the next move replaces the hold anyway.
            self._move(self._end(oscillation), math.ceil(2 * half_period_s * 1000))
            self._osc_to_plus = not self._osc_to_plus
            deadline += half_period_s
            now = time.perf_counter()
            if now > deadline:
                self._warn_once(
                    "jump",
                    f"Slit oscillation fell {(now - deadline) * 1000:.0f} ms behind its "
                    f"{half_period_s * 1000:.0f} ms half period at {oscillation.frequency_hz} Hz",
                )
                deadline = now
            self._sleep_until(deadline, "jump")

    def _sweep(self):
        """Step the target towards the next end at the set speed, a setpoint every SWEEP_UPDATE_S; at the end,
        wait until the stage is there before turning round."""
        last = time.perf_counter()
        while self._running("sweep"):
            oscillation = self.oscillation
            now = time.perf_counter()
            step_um = oscillation.speed_um_s * (now - last)
            last = now
            end_um = self._end(oscillation)
            if self._osc_to_plus:
                offset_um = min(end_um, self._osc_offset_um + step_um)
            else:
                offset_um = max(end_um, self._osc_offset_um - step_um)
            if round(offset_um) != round(self._osc_offset_um) or offset_um == end_um:
                self._move(offset_um, self.SWEEP_HOLD_MS)
            else:
                self._osc_offset_um = offset_um  # below 1 µm since the last setpoint: nothing to send yet
            if offset_um == end_um:
                self._wait_until_at(end_um)
                self._osc_to_plus = not self._osc_to_plus
                now = last = time.perf_counter()  # the wait at the end is not travel
            # always pause between setpoints, also after turning round (with no swing, every pass turns round)
            self._sleep_until(now + self.SWEEP_UPDATE_S, "sweep")

    def _wait_until_at(self, offset_um):
        centre = self._slit_centre()
        give_up = time.perf_counter() + self.ARRIVE_TIMEOUT_S
        position = None
        while self._running("sweep") and time.perf_counter() < give_up:
            if self._slit_centre() != centre:
                # the slit or the offset changed while waiting: go to the end around the new centre
                centre = self._slit_centre()
                self._move(offset_um, self.SWEEP_HOLD_MS)
                give_up = time.perf_counter() + self.ARRIVE_TIMEOUT_S
            position = self.read_position_um()
            if position is not None and abs(position - (centre + int(round(offset_um)))) <= self.ARRIVE_TOLERANCE_UM:
                return
            self._sleep_until(time.perf_counter() + self.ARRIVE_POLL_S, "sweep")
        if self._running("sweep"):
            last_read = "no position reply" if position is None else f"last read {position} µm"
            self._warn_once(
                "arrive",
                f"Slit sweep turned round without reaching {centre + int(round(offset_um))} µm within "
                f"{self.ARRIVE_TIMEOUT_S * 1000:.0f} ms ({last_read})",
            )

    def close(self):
        try:
            self.stop_oscillation()
        finally:
            if self.ser.is_open:
                self.ser.close()


class NL5(_ScanSpeedMixin):

    def __init__(self):
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.rcm = RCM_API.RCM_API()
        time.sleep(0.5)
        data = self.rcm.get_available_devices()
        self._log.info(f"RCM devices: {data!r}")
        self.rcm.initialize_device(simulated=False)
        self.load_settings()

        # Device type per RCM_API.h: Dev_NL5 = 4, Dev_AION = 5. The AION is the line scanner with the
        # internal slit stage; the available-devices string ("aion/<name>") is the fallback when the
        # type query is unavailable.
        self.device_type = self.rcm.get_device_type()
        self.is_aion = self.device_type == getattr(self.rcm, "Dev_AION", 5) or bool(data and "aion/" in data)
        self.device_name = "AION" if self.is_aion else "NL5"
        self._log.info(f"RCM device type {self.device_type} -> {self.device_name}")

        # Full camera width known to the RCM; used for the line speed <-> frame rate conversion.
        self.max_cam_pix_x = self.rcm.get_integer_parameter(self.rcm.MAX_CAM_PIX_X) or None
        self._log.info(
            f"Scan speed conversion: amplitude_x={self.scan_amplitude} mrad, fov_x={self.fov_x} px, "
            f"max_cam_pix_x={self.max_cam_pix_x} -> sweep={self.scan_sweep_mrad():.2f} mrad, "
            f"line_speed={self.line_speed} mrad/s = {self.line_speed_to_fps(self.line_speed):.2f} fps"
        )

        self.internal_stage = None
        if self.is_aion:
            sn = getattr(control._def, "RCM_INTERNAL_STAGE_SN", None) or DEFAULT_RCM_INTERNAL_STAGE_SN
            try:
                self.internal_stage = RCMInternalStage(sn)
                self.internal_stage.set_offset(self.slit_offset_um)
                self.internal_stage.set_slit_width(self.slit_width_um)
            except Exception as e:
                self._log.error(
                    f"Failed to initialize the RCM internal (slit) stage with USB SN '{sn}': {e}. "
                    "Slit controls will be hidden; set RCM_INTERNAL_STAGE_SN in the .ini to the SCU controller's USB serial number."
                )
                self.internal_stage = None

    def set_scan_amplitude(self, amplitude):
        self.scan_amplitude = amplitude
        self.rcm.set_float_parameter(self.rcm.AMPLITUDE_X, amplitude)

    def set_offset_x(self, offset_x):
        self.offset_x = offset_x
        self.rcm.set_float_parameter(self.rcm.OFFSET_SCAN_X, offset_x)

    def start_acquisition(self):
        ret = self.rcm.start_acquisition()
        print(f"Start acquisition: {ret}")

    def start_continuous_acquisition(self):
        self.rcm.start_acquisition()

    def stop_continuous_acquisition(self):
        self.rcm.stop_continuous_acquisition()

    def set_bypass(self, enabled):
        if enabled:
            self.rcm.set_bypass(1)
        else:
            self.rcm.set_bypass(0)

    def set_active_channel(self, channel):
        self.active_channel = channel
        for i in range(1, 5):
            self.rcm.set_integer_parameter(getattr(self.rcm, f"LASER_{i}_SELECTED"), 1 if i == channel else 0)

    def set_laser_power(self, channel, power):
        self.rcm.set_integer_parameter(getattr(self.rcm, f"LASER_{channel}_POWER"), power)

    def set_bypass_offset(self, offset):
        self.bypass_offset = offset
        self.rcm.set_float_parameter(self.rcm.BYPASS_OFFSET, offset)

    def _send_line_speed(self, line_speed):
        self.rcm.set_integer_parameter(self.rcm.LINE_FREQUENCY, line_speed)  # speed in mrad/s

    def set_fov_x(self, fov_x):
        self.fov_x = fov_x
        self.rcm.set_integer_parameter(self.rcm.FIELD_OF_VIEW_X, fov_x)
        self.save_settings()

    def set_exposure_delay(self, exposure_delay_ms):
        self.exposure_delay_ms = exposure_delay_ms
        self.rcm.set_integer_parameter(self.rcm.EXPOSURE_DELAY, exposure_delay_ms)

    # RCM parameters read back from the device for acquisition metadata (name -> integer or float).
    DEVICE_READBACK_PARAMETERS = (
        ("FIELD_OF_VIEW_X", int),
        ("FIELD_OF_VIEW_Y", int),
        ("MAX_CAM_PIX_X", int),
        ("MAX_CAM_PIX_Y", int),
        ("EXPOSURE_DELAY", int),
        ("LINE_FREQUENCY", int),
        ("AMPLITUDE_X", float),
        ("AMPLITUDE_Y", float),
        ("OFFSET_SCAN_X", float),
        ("OFFSET_SCAN_Y", float),
        ("BYPASS_OFFSET", float),
        ("LASER_1_SELECTED", int),
        ("LASER_2_SELECTED", int),
        ("LASER_3_SELECTED", int),
        ("LASER_4_SELECTED", int),
        ("LASER_1_POWER", int),
        ("LASER_2_POWER", int),
        ("LASER_3_POWER", int),
        ("LASER_4_POWER", int),
    )

    def settings_metadata(self):
        metadata = super().settings_metadata()
        metadata["device_type"] = self.device_type
        readback = {}
        for name, kind in self.DEVICE_READBACK_PARAMETERS:
            param = getattr(self.rcm, name, None)
            if param is None:
                continue
            try:
                if kind is float:
                    readback[name] = self.rcm.get_float_parameter(param)
                else:
                    readback[name] = self.rcm.get_integer_parameter(param)
            except Exception as e:
                self._log.warning(f"Could not read RCM parameter {name}: {e}")
                readback[name] = None
        metadata["device_readback"] = readback
        return metadata

    def set_slit_width(self, width_um):
        self.slit_width_um = width_um
        if self.internal_stage is not None:
            self.internal_stage.set_slit_width(width_um)
        self.save_settings()

    def set_slit_offset(self, offset_um):
        self.slit_offset_um = offset_um
        if self.internal_stage is not None:
            self.internal_stage.set_offset(offset_um)
        self.save_settings()

    def home_internal_stage(self):
        if self.internal_stage is not None:
            self.internal_stage.home()

    def set_slit_oscillation(self, oscillation):
        """Keep the oscillation settings (not saved to NL5_settings.json); a running oscillation takes them
        from its next move."""
        oscillation.validate()
        self.slit_oscillation = oscillation
        if self.internal_stage is not None:
            self.internal_stage.update_oscillation(oscillation)  # never starts one

    def start_slit_oscillation(self):
        if self.internal_stage is not None:
            self.internal_stage.start_oscillation(self.slit_oscillation)

    def stop_slit_oscillation(self):
        if self.internal_stage is not None:
            self.internal_stage.stop_oscillation()

    def is_slit_oscillating(self):
        return self.internal_stage is not None and self.internal_stage.is_oscillating()


class NL5_Simulation(_ScanSpeedMixin):

    def __init__(self):
        self.load_settings()
        self.internal_stage = None
        self.max_cam_pix_x = 2048
        self.device_type = 4
        self.is_aion = False
        self.device_name = "NL5"

    def set_scan_amplitude(self, amplitude):
        self.scan_amplitude = amplitude
        pass

    def set_offset_x(self, offset_x):
        self.offset_x = offset_x
        pass

    def start_acquisition(self):
        pass

    def start_continuous_acquisition(self):
        pass

    def stop_continuous_acquisition(self):
        pass

    def set_bypass(self, enabled):
        pass

    def set_active_channel(self, channel):
        pass

    def set_laser_power(self, channel, power):
        pass

    def set_bypass_offset(self, offset):
        self.bypass_offset = offset
        pass

    def _send_line_speed(self, line_speed):
        pass

    def set_fov_x(self, fov_x):
        self.fov_x = fov_x
        self.save_settings()

    def set_exposure_delay(self, exposure_delay_ms):
        self.exposure_delay_ms = exposure_delay_ms
        pass

    def set_slit_oscillation(self, oscillation):
        oscillation.validate()
        self.slit_oscillation = oscillation

    def start_slit_oscillation(self):
        pass

    def stop_slit_oscillation(self):
        pass

    def is_slit_oscillating(self):
        return False
