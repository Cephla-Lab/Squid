import control.RCM_API as RCM_API
import control._def
import squid.logging
import json
import math
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

# Scanner line speed (LINE_FREQUENCY, mrad/s) limits accepted by the GUI.
LINE_SPEED_MIN = 100
LINE_SPEED_MAX = 20000
SPEED_INPUT_MODES = ("line_speed", "fps")


class _ScanSpeedMixin:
    """Conversion between the scanner line speed and the scan-limited frame rate.

    One frame is one sweep of the line across the scanned field. The scanner travels +/- the
    scan amplitude (AMPLITUDE_X, mrad), so the sweep is twice the amplitude, scaled by the
    scanned fraction of the camera width (FIELD_OF_VIEW_X / MAX_CAM_PIX_X), and
        fps = line_speed [mrad/s] / sweep [mrad].
    Measured on AION: the time to a frame grows by 2 x amplitude / line speed, so halving the
    amplitude or FIELD_OF_VIEW_X saved 26 ms at 73 mrad and 3000 mrad/s.
    The exposure delay, the jump back (~3 ms) and the camera's readout and transfer (~30 ms
    for a full Aries frame) are not included, so this is the upper bound the scanner allows,
    not the camera frame rate.
    """

    max_cam_pix_x = None

    def scan_sweep_mrad(self):
        fraction = 1.0
        if self.max_cam_pix_x and self.fov_x:
            fraction = min(1.0, self.fov_x / self.max_cam_pix_x)
        return max(1e-6, 2 * self.scan_amplitude * fraction)

    def line_speed_to_fps(self, line_speed):
        return line_speed / self.scan_sweep_mrad()

    def fps_to_line_speed(self, fps):
        return int(round(fps * self.scan_sweep_mrad()))

    @staticmethod
    def clamp_line_speed(speed):
        return int(min(LINE_SPEED_MAX, max(LINE_SPEED_MIN, round(speed))))

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
    ANSWER_TIMEOUT_S = 0.5
    HOMING_WAIT_S = 15
    REFERENCE_MOVE_WAIT_S = 2
    COMMAND_GAP_S = 0.1
    DEFAULT_HOLD_MS = 10
    SLIT_CHANNEL = 0
    STOP_POLL_S = 0.05  # how quickly the oscillation thread notices a stop

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
        self.oscillation = None  # (plus_um, minus_um, frequency_hz) last started
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
            self.start_oscillation(*self.oscillation)

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

    def start_oscillation(self, plus_um, minus_um, frequency_hz):
        """Move the slit back and forth between centre + plus_um and centre - minus_um at frequency_hz.

        The centre is the selected slit's position plus the slit offset, so changing either moves the
        oscillation with it. Runs until stop_oscillation(); calling this again while it runs changes the
        parameters from the next move, without restarting the cycle.
        """
        if self.current_slit_um is None:
            raise RuntimeError("Select a slit before starting the oscillation")
        if not (0 <= plus_um <= SLIT_OSCILLATION_MAX_UM and 0 <= minus_um <= SLIT_OSCILLATION_MAX_UM):
            raise ValueError(
                f"Oscillation positions must be 0-{SLIT_OSCILLATION_MAX_UM} µm, got +{plus_um} / -{minus_um}"
            )
        if not SLIT_OSCILLATION_MIN_HZ <= frequency_hz <= SLIT_OSCILLATION_MAX_HZ:
            raise ValueError(
                f"Oscillation frequency {frequency_hz} Hz outside "
                f"{SLIT_OSCILLATION_MIN_HZ}-{SLIT_OSCILLATION_MAX_HZ} Hz"
            )
        self.oscillation = (plus_um, minus_um, frequency_hz)  # read by the running thread at each move
        if self.is_oscillating():
            self._log.info(f"Slit oscillation changed: +{plus_um} / -{minus_um} µm at {frequency_hz} Hz")
            return
        self._join_oscillation_thread()  # one that ended on an error
        self._oscillation_stop.clear()
        self._oscillation_thread = threading.Thread(target=self._oscillate, name="SlitOscillation", daemon=True)
        self._oscillation_thread.start()
        self._log.info(f"Slit oscillation started: +{plus_um} / -{minus_um} µm at {frequency_hz} Hz")

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
        to_plus = True
        warned = False
        deadline = time.perf_counter()
        while not self._oscillation_stop.is_set():
            plus_um, minus_um, frequency_hz = self.oscillation
            half_period_s = 0.5 / frequency_hz
            target = self._slit_centre() + int(round(plus_um if to_plus else -minus_um))
            # Hold each end past the next move's due time, so a late move never finds the closed loop let go;
            # the next move replaces the hold anyway.
            hold_ms = math.ceil(2 * half_period_s * 1000)
            self._send(f"MPA{self.SLIT_CHANNEL}P{target}H{hold_ms}", gap_s=0)
            to_plus = not to_plus
            deadline += half_period_s
            now = time.perf_counter()
            if now > deadline:
                if not warned:
                    self._log.warning(
                        f"Slit oscillation fell {(now - deadline) * 1000:.0f} ms behind its "
                        f"{half_period_s * 1000:.0f} ms half period at {frequency_hz} Hz"
                    )
                    warned = True
                deadline = now
            # time.sleep keeps sub-ms timing on Windows; Event.wait overshoots by 5-11 ms (measured).
            while not self._oscillation_stop.is_set():
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    break
                time.sleep(min(remaining, self.STOP_POLL_S))

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

    def set_line_speed(self, speed, save_setting=False):
        self.line_speed = self.clamp_line_speed(speed)
        self.rcm.set_integer_parameter(self.rcm.LINE_FREQUENCY, self.line_speed)  # speed in mrad/s
        if save_setting:
            self.save_settings()

    def set_fov_x(self, fov_x):
        self.fov_x = fov_x
        self.rcm.set_integer_parameter(self.rcm.FIELD_OF_VIEW_X, fov_x)
        self.save_settings()

    def set_exposure_delay(self, exposure_delay_ms):
        self.exposure_delay_ms = exposure_delay_ms
        self.rcm.set_integer_parameter(self.rcm.EXPOSURE_DELAY, exposure_delay_ms)

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

    def start_slit_oscillation(self, plus_um, minus_um, frequency_hz):
        if self.internal_stage is not None:
            self.internal_stage.start_oscillation(plus_um, minus_um, frequency_hz)

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

    def set_line_speed(self, speed, save_setting=False):
        self.line_speed = self.clamp_line_speed(speed)
        if save_setting:
            self.save_settings()

    def set_fov_x(self, fov_x):
        self.fov_x = fov_x
        self.save_settings()

    def set_exposure_delay(self, exposure_delay_ms):
        self.exposure_delay_ms = exposure_delay_ms
        pass

    def start_slit_oscillation(self, plus_um, minus_um, frequency_hz):
        pass

    def stop_slit_oscillation(self):
        pass

    def is_slit_oscillating(self):
        return False
