import control.RCM_API as RCM_API
import control._def
import squid.logging
import json
import time

import serial
import serial.tools.list_ports


# USB serial number of the SmarAct SCU controller that drives the AION internal (slit) stage.
# Configure per machine with RCM_INTERNAL_STAGE_SN in the .ini (e.g. "SCU", "AL05NB1GA").
DEFAULT_RCM_INTERNAL_STAGE_SN = "AL05NB1GA"

SLIT_POSITIONS_UM = {88: 1500, 44: 900, 22: 300, 33: -300, 66: -900, 132: -1500}

# Scanner line speed (LINE_FREQUENCY, mrad/s) limits accepted by the GUI.
LINE_SPEED_MIN = 100
LINE_SPEED_MAX = 20000
SPEED_INPUT_MODES = ("line_speed", "fps")


class _ScanSpeedMixin:
    """Scan timing of the line scanner: line speed <-> frame rate <-> exposure.

    One frame is one sweep of the line across the scanned field. The scanner travels +/- the
    scan amplitude (AMPLITUDE_X, mrad, the amplitude for MAX_CAM_PIX_X pixels), scaled by the
    scanned width in pixels relative to that (FIELD_OF_VIEW_X / MAX_CAM_PIX_X, not capped at 1:
    a FIELD_OF_VIEW_X wider than MAX_CAM_PIX_X scans further), so
        sweep = 2 x amplitude x FIELD_OF_VIEW_X / MAX_CAM_PIX_X,
        scan time = sweep [mrad] / line speed [mrad/s],    fps = line speed / sweep.
    Measured on AION (camera + unit, 2026-10-22, frame latency vs settings): the time to a frame
    moves by exactly this scan time when the line speed, the amplitude or FIELD_OF_VIEW_X change
    (73 mrad, MAX_CAM_PIX_X 2048, FIELD_OF_VIEW_X 3200: 76 ms at 3000 mrad/s, 38 ms at 6000 or at
    36.5 mrad, 24 ms at FIELD_OF_VIEW_X 1024), plus a jump back of about 4 ms at the full width
    that is not counted here. The fps is the upper bound the scanner allows, not the camera's
    frame rate: the frame arrives ~56 ms after the scan (readout and transfer of a full Aries frame).

    The camera is level-triggered by the unit (Tucsen "trigger width" mode): its exposure is however
    long the unit holds the trigger high, which is the exposure delay (EXPOSURE_DELAY) followed by
    the scan. The camera's own exposure setting is ignored. So on this unit
        exposure = exposure delay + scan time,
    and a wanted exposure is reached by setting the line speed (line_speed_for_exposure).
    """

    max_cam_pix_x = None

    def scan_sweep_mrad(self):
        fraction = 1.0
        if self.max_cam_pix_x and self.fov_x:
            fraction = self.fov_x / self.max_cam_pix_x
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
    ANSWER_TIMEOUT_S = 0.5
    HOMING_WAIT_S = 15
    REFERENCE_MOVE_WAIT_S = 2
    COMMAND_GAP_S = 0.1
    DEFAULT_HOLD_MS = 10
    SLIT_CHANNEL = 0

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
        self._log.info(f"RCM internal stage (SmarAct SCU, USB SN '{serial_number}') connected on {port}; homing")
        self.home()

    @staticmethod
    def _find_port(serial_number):
        for port in serial.tools.list_ports.comports():
            if port.serial_number == serial_number:
                return port.device
        return None

    def _send(self, command):
        self.ser.reset_input_buffer()
        self.ser.write(f":{command}\n".encode("ascii"))
        time.sleep(self.COMMAND_GAP_S)

    def home(self):
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

    def set_slit_width(self, width_um):
        if width_um not in SLIT_POSITIONS_UM:
            raise ValueError(f"Unknown slit width {width_um} µm; expected one of {list(SLIT_POSITIONS_UM)}")
        self.current_slit_um = width_um
        target = SLIT_POSITIONS_UM[width_um] + int(round(self.offset_um))
        self._send(f"MPA{self.SLIT_CHANNEL}P{target}H{self.DEFAULT_HOLD_MS}")

    def set_offset(self, offset_um):
        self.offset_um = offset_um
        if self.current_slit_um is not None:
            self.set_slit_width(self.current_slit_um)

    def close(self):
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
