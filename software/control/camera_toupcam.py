import math
import time
from typing import Optional, Tuple, Sequence, Dict

import numpy as np
import pydantic

import control.utils
import squid.logging
from squid.abc import (
    AbstractCamera,
    CameraAcquisitionMode,
    CameraGainRange,
    CameraFrameFormat,
    CameraPixelFormat,
    CameraFrame,
)
from squid.config import CameraConfig, ToupcamCameraModel
from control._def import *

import threading
import control.toupcam as toupcam
from control.toupcam_exceptions import hresult_checker

log = squid.logging.get_logger(__name__)


class ToupCamCapabilities(pydantic.BaseModel):
    binning_to_resolution: Dict[Tuple[int, int], Tuple[int, int]]
    has_fan: bool
    has_TEC: bool
    has_low_noise_mode: bool
    has_black_level: bool
    # Monochrome sensor.  Decides which pixel formats the camera can produce, so it is
    # what get_available_pixel_formats reports off.
    is_mono: bool = True


class StrobeInfo(pydantic.BaseModel):
    strobe_time_us: float
    trigger_delay_us: float


def get_sn_by_model(camera_model: ToupcamCameraModel):
    try:
        device_list = toupcam.Toupcam.EnumV2()
    except:
        log.error("Problem generating Toupcam device list")
        return None
    for dev in device_list:
        if dev.displayname == camera_model.value:
            return dev.id
    return None  # return None if no device with the specified model_name is connected


class ToupcamCamera(AbstractCamera):
    TOUPCAM_OPTION_RAW_RAW_VAL = 1
    TOUPCAM_OPTION_RAW_RGB_VAL = 0
    PIXEL_SIZE_UM = 3.76

    @staticmethod
    def _event_callback(event_number, camera):
        if event_number == toupcam.TOUPCAM_EVENT_IMAGE:
            camera._on_frame_callback()

    # Pixel formats the SDK debayers for us.  These are the only ones that need the RGB
    # frame format; everything else (MONO*) is read straight off the sensor in RAW mode.
    _RGB_PIXEL_FORMATS = (
        CameraPixelFormat.RGB24,
        CameraPixelFormat.RGB32,
        CameraPixelFormat.RGB48,
    )

    @staticmethod
    def _frame_format_for_pixel_format(pixel_format: CameraPixelFormat) -> CameraFrameFormat:
        """
        The frame format a pixel format has to be read in.

        The RGB formats are produced by the SDK's own debayering, which only runs in RGB
        frame format (TOUPCAM_OPTION_RAW=0).  MONO formats come off the sensor in RAW mode.
        """
        if pixel_format in ToupcamCamera._RGB_PIXEL_FORMATS:
            return CameraFrameFormat.RGB
        return CameraFrameFormat.RAW

    @staticmethod
    def _row_pitch_bytes(width: int, pixel_size_in_bytes: int) -> int:
        """
        Bytes from the start of one row to the start of the next, in RGB frame format.

        This must match the SDK's default row pitch, which is what PullImageV2 writes:
        RGB32 rows are packed (Width * 4), every other format is padded out to a 4 byte
        boundary (TDIBWIDTHBYTES(bits_per_pixel * Width)).  See the PullImageV4 rowPitch
        table in toupcam.py.
        """
        if pixel_size_in_bytes == 4:
            return width * 4
        return (width * pixel_size_in_bytes * 8 + 31) // 32 * 4

    @staticmethod
    def _calculate_strobe_info(
        camera: toupcam.Toupcam, pixel_size: int, exposure_time_ms: float, capabilities: ToupCamCapabilities
    ) -> StrobeInfo:
        log = squid.logging.get_logger("ToupcamCamera._calculate_strobe_delay")
        # use camera arguments such as resolutuon, ROI, exposure time, set max FPS, bandwidth to calculate the trigger delay time

        # The line length table below is indexed by the sensor's readout depth - what
        # TOUPCAM_OPTION_BITDEPTH selects - and not by the size of the pixel we are handed.
        # They differ for the debayered RGB formats: RGB24 (3 bytes) and RGB32 (4 bytes)
        # are 8 bit readouts, RGB48 (6 bytes) is a 16 bit one.  Deriving pixel_bits from
        # the byte size alone leaves line_length at 0 for those, which then divides by zero.
        pixel_bits = 8 if pixel_size in (1, 3, 4) else 16
        line_length = 0
        low_noise = 0

        try:
            resolution_width, resolution_height = camera.get_Size()
        except toupcam.HRESULTException as ex:
            log.exception("get resolution fail, hr=0x{:x}".format(ex.hr))
            raise

        xoffset, yoffset, roi_width, roi_height = camera.get_Roi()

        try:
            bandwidth = camera.get_Option(toupcam.TOUPCAM_OPTION_BANDWIDTH)
        except toupcam.HRESULTException as ex:
            log.exception("get badwidth fail, hr=0x{:x}".format(ex.hr))
            raise

        if capabilities.has_low_noise_mode:
            try:
                low_noise = camera.get_Option(toupcam.TOUPCAM_OPTION_LOW_NOISE)
            except toupcam.HRESULTException as ex:
                log.exception("get low_noise fail, hr=0x{:x}".format(ex.hr))

        if resolution_width == 6224 and resolution_height == 4168:
            if pixel_bits == 8:
                line_length = 1200 * (roi_width / 6224)
                if line_length < 450:
                    line_length = 450
            elif pixel_bits == 16:
                if low_noise == 1:
                    line_length = 5000
                elif low_noise == 0:
                    line_length = 2500
        elif resolution_width == 3104 and resolution_height == 2084:
            if pixel_bits == 8:
                line_length = 906
            elif pixel_bits == 16:
                line_length = 1200
        elif resolution_width == 2064 and resolution_height == 1386:
            if pixel_bits == 8:
                line_length = 454
            elif pixel_bits == 16:
                line_length = 790

        line_length = int(line_length / (bandwidth / 100.0))
        row_time = line_length / 72

        # MAX_PRECISE_FRAMERATE can be rejected by the camera in certain
        # transient states (notably right after a TRIGGER option flip), and
        # is not relevant for trigger modes anyway — PRECISE_FRAMERATE only
        # paces continuous/video mode. Fall back to a high value so vheight
        # floors at roi_height + 56 (the sensor minimum) when the read fails,
        # instead of propagating out and breaking the mode switch.
        try:
            max_framerate_tenths_fps = camera.get_Option(toupcam.TOUPCAM_OPTION_MAX_PRECISE_FRAMERATE)
        except toupcam.HRESULTException as ex:
            log.warning(f"get max_framerate fail (using fallback) --> {control.toupcam_exceptions.explain(ex)}")
            max_framerate_tenths_fps = None

        if max_framerate_tenths_fps is not None:
            # need reset value, because the default value is only 90% of setting value
            try:
                camera.put_Option(toupcam.TOUPCAM_OPTION_PRECISE_FRAMERATE, max_framerate_tenths_fps)
            except toupcam.HRESULTException as ex:
                log.warning(f"put max_framerate fail (skipping) --> {control.toupcam_exceptions.explain(ex)}")
            max_framerate_fps = max_framerate_tenths_fps / 10.0
        else:
            # Sensor-floor fallback: high enough that vheight clamps to
            # roi_height + 56 in the check below.
            max_framerate_fps = 600.0

        vheight = 72000000 / (max_framerate_fps * line_length)
        if vheight < roi_height + 56:
            vheight = roi_height + 56

        """
        The trigger delay in [ms].  This is the time after the trigger but before the camera actually
        starts the exposure.  For larger exposure times, this is ~0.  But for small exposure times this
        can actually be multiples of the exposure time.  It's included in the strobe time since it looks
        like strobe delay for both hardware and software trigger purposes.  See the "TRG_DELAY&ROW_TIME&TOTAL_RESET"
        pdf from toupcam.
        """
        exposure_time_us = exposure_time_ms * 1000.0
        exposure_length = int(72 * exposure_time_us / line_length)

        if vheight >= exposure_length - 1:
            shr = vheight - exposure_length
        else:
            shr = 1

        trigger_delay_us = (shr * line_length) / 72
        strobe_time = int(vheight * row_time)

        log.debug(
            f"New strobe time calculated as {strobe_time} [us]. {resolution_width=}, {resolution_height=}, {pixel_bits=}, {line_length=}, {low_noise=}, {vheight=}, {trigger_delay_us=}"
        )

        return StrobeInfo(strobe_time_us=strobe_time, trigger_delay_us=trigger_delay_us)

    @staticmethod
    def _read_serial_number(camera: toupcam.Toupcam) -> Optional[str]:
        """
        Best effort read of the true serial number from an already open camera handle.

        The Toupcam SDK only exposes the serial number once a device is open, and the
        vendored python binding has used both spellings over time, so try each.  Returns
        None if the serial number could not be read.
        """
        log = squid.logging.get_logger("ToupcamCamera._read_serial_number")
        getter = getattr(camera, "SerialNumber", None) or getattr(camera, "get_SerialNumber", None)
        if getter is None:
            log.warning("This toupcam binding has no serial number getter.")
            return None
        try:
            return getter()
        except Exception:
            log.exception("Failed to read the serial number from an open toupcam device.")
            return None

    @staticmethod
    def _close_quietly(camera: toupcam.Toupcam):
        try:
            camera.Close()
        except Exception:
            squid.logging.get_logger("ToupcamCamera._close_quietly").exception(
                "Failed to close a probed toupcam device."
            )

    @staticmethod
    def _capabilities_for_device(device: toupcam.ToupcamDeviceV2) -> ToupCamCapabilities:
        log = squid.logging.get_logger("ToupcamCamera._capabilities_for_device")

        resolution_list = []
        for r in device.model.res:
            log.info("\t = [{} x {}]".format(r.width, r.height))
            resolution_list.append((r.width, r.height))
        if len(resolution_list) == 0:
            raise ValueError("No resolutions found for camera")
        resolution_list.sort(key=lambda x: x[0] * x[1], reverse=True)

        highest_res = resolution_list[0]

        binning_res = {}
        for res in resolution_list:
            x_binning = int(highest_res[0] / res[0])
            y_binning = int(highest_res[1] / res[1])
            binning_res[(x_binning, y_binning)] = res

        return ToupCamCapabilities(
            binning_to_resolution=binning_res,
            has_fan=(device.model.flag & toupcam.TOUPCAM_FLAG_FAN) > 0,
            has_TEC=(device.model.flag & toupcam.TOUPCAM_FLAG_TEC_ONOFF) > 0,
            has_low_noise_mode=(device.model.flag & toupcam.TOUPCAM_FLAG_LOW_NOISE) > 0,
            has_black_level=(device.model.flag & toupcam.TOUPCAM_FLAG_BLACKLEVEL) > 0,
            is_mono=(device.model.flag & toupcam.TOUPCAM_FLAG_MONO) > 0,
        )

    # Opens the camera in RGB gain white balance mode.  The SDK fixes the white balance
    # mode at open time and the two modes are mutually exclusive, so this has to be part
    # of the camId string handed to Toupcam_Open - it cannot be switched afterwards.
    WB_RGB_OPEN_SUFFIX = ";wb=rgb"

    @staticmethod
    def _open_id_for_device(device: toupcam.ToupcamDeviceV2) -> str:
        """
        The camId string to open `device` with.

        Color cameras get the RGB gain white balance mode appended.  Without it the SDK
        serves Temp/Tint white balance instead, and the whole RGB gain API this driver
        uses (AwbInit, get/put_WhiteBalanceGain) reports "not implemented".  Mono cameras
        have no white balance at all, so they are opened plain.
        """
        if device.model.flag & toupcam.TOUPCAM_FLAG_MONO:
            return device.id
        return device.id + ToupcamCamera.WB_RGB_OPEN_SUFFIX

    @staticmethod
    def _resolve_sn_to_index(
        devices: Sequence[toupcam.ToupcamDeviceV2], sn: str
    ) -> Tuple[int, Optional[toupcam.Toupcam]]:
        """
        Find the device in `devices` that `sn` refers to.  See _open for the accepted strings.

        Returns (index, camera), where camera is an already open handle for the matched
        device when we had to open it to read its serial number (the caller owns it and
        must not open the device a second time), and None when the match came from the
        enumeration data alone.  Raises ValueError when nothing matches.
        """
        log = squid.logging.get_logger("ToupcamCamera._resolve_sn_to_index")

        # Pass 1: the opaque enumeration id.  Free - no device needs to be opened.
        for idx, device in enumerate(devices):
            if device.id == sn:
                log.info(f"Matched {sn=} against the enumeration id of device {idx}.")
                return idx, None

        # Pass 2: the true serial number, which the SDK only reports for an open device.
        # We open each candidate in turn and close it again unless it is the one we want.
        # A device that is already open elsewhere (eg: the other camera of a 2 camera
        # system) cannot be probed, so treat a failed open as "not this one" and continue.
        log.info(f"No enumeration id matched {sn=}, probing {len(devices)} device(s) for their serial numbers.")
        descriptions = []
        for idx, device in enumerate(devices):
            try:
                # Opened the same way _open would, so the handle we keep for the match is
                # already in the right white balance mode and never needs reopening.
                camera = toupcam.Toupcam.Open(ToupcamCamera._open_id_for_device(device))
            except Exception:
                log.exception(f"Failed to open toupcam device {idx} (id={device.id}) while probing serial numbers.")
                camera = None

            if camera is None:
                log.warning(f"Could not open toupcam device {idx} (id={device.id}) to read its serial number.")
                descriptions.append(f"id={device.id} (serial unavailable, could not open)")
                continue

            keep_open = False
            try:
                serial = ToupcamCamera._read_serial_number(camera)
                log.info(f"Probed toupcam device {idx}: id={device.id}, serial={serial}")
                if serial is not None and serial == sn:
                    keep_open = True
                    return idx, camera
                descriptions.append(
                    f"id={device.id} serial={serial}" if serial is not None else f"id={device.id} (serial unavailable)"
                )
            finally:
                if not keep_open:
                    ToupcamCamera._close_quietly(camera)

        raise ValueError(
            f"Could not find a Toupcam camera matching serial_number={sn}.  Available cameras: "
            f"{'; '.join(descriptions)}.  Use one of those id or serial strings as the camera's serial_number."
        )

    @staticmethod
    def _open(index=None, sn=None) -> Tuple[toupcam.Toupcam, ToupCamCapabilities]:
        """
        Open a toupcam device and work out its capabilities.

        Args:
            index: 0 based index into the EnumV2 device list.  When neither index nor sn
                is given, the first enumerated device (index 0) is opened.
            sn: identifies the camera to open.  Two forms are accepted, tried in order:
                  1. the opaque enumeration id (toupcam.ToupcamDeviceV2.id, ie: the string
                     Toupcam_Open takes).  Matching this costs nothing.
                  2. the camera's true serial number as reported by Toupcam.SerialNumber()
                     (eg: "TP110826145730ABCD1234FEDC56787").  The SDK only exposes this
                     for an open device, so devices are opened one at a time until one
                     matches, and any non matching device is closed again.
                Specifying both index and sn is an error.
        """
        log = squid.logging.get_logger("ToupcamCamera._open")
        log.info(f"Opening toupcam with {index=}, {sn=}")

        if index is not None and sn is not None:
            raise ValueError("You specified both a device index and a sn, this is not allowed.")

        devices = toupcam.Toupcam.EnumV2()
        if len(devices) <= 0:
            raise ValueError("There are no Toupcam V2 devices.  Is the camera connected and powered on?")

        for idx, device in enumerate(devices):
            log.info(
                "Camera {}: {}: flag = {:#x}, preview = {}, still = {}".format(
                    idx,
                    device.displayname,
                    device.model.flag,
                    device.model.preview,
                    device.model.still,
                )
            )

        # Non-None only when resolving the sn left us holding an open handle for the match.
        camera: Optional[toupcam.Toupcam] = None
        if sn is not None:
            (index, camera) = ToupcamCamera._resolve_sn_to_index(devices, sn)
        elif index is None:
            index = 0

        if not 0 <= index < len(devices):
            raise ValueError(f"Toupcam device index={index} is out of range, only {len(devices)} device(s) enumerated.")

        device = devices[index]
        try:
            capabilities = ToupcamCamera._capabilities_for_device(device)
            if camera is None:
                camera = toupcam.Toupcam.Open(ToupcamCamera._open_id_for_device(device))
            if camera is None:
                raise ValueError(f"Failed to open Toupcam device {index} (id={device.id}).  Is it in use already?")
        except Exception:
            # Don't leak a device we opened (or that sn probing left us holding).
            if camera is not None:
                ToupcamCamera._close_quietly(camera)
            raise

        return camera, capabilities

    @staticmethod
    def _open_for_config(config: CameraConfig) -> Tuple[toupcam.Toupcam, ToupCamCapabilities]:
        """
        Open the camera this config points at: the one matching config.serial_number when
        the config gives one (needed when more than one toupcam is connected), otherwise
        the first enumerated camera.
        """
        if config.serial_number:
            return ToupcamCamera._open(sn=config.serial_number)
        return ToupcamCamera._open(index=0)

    def __init__(self, config: CameraConfig, hw_trigger_fn, hw_set_strobe_delay_ms_fn):
        super().__init__(config, hw_trigger_fn, hw_set_strobe_delay_ms_fn)

        self._current_frame: Optional[CameraFrame] = None
        self._camera: Optional[toupcam.Toupcam] = None

        # These are used only in both software and hw trigger mode.  We use them to make sure we don't send a trigger
        # when a frame is already in progress.  The send_trigger method should be the only one setting this to True
        # (and setting the timestamp), and the raw frame callback can set the _trigger_sent to False when
        # it receives a frame.
        self._trigger_sent = False
        self._last_trigger_timestamp = 0

        # _raw_camera_stream_started keeps track of the ToupcamCamera <-> hardware stream. This should always be running,
        # because it is how we get notified by the camera that new frames are available.  Our _on_frame_callback
        # is what the camera driver calls when a new frame is available.
        self._raw_camera_stream_started = False
        self._raw_frame_callback_lock = threading.Lock()
        (self._camera, self._capabilities) = ToupcamCamera._open_for_config(config)
        self._pixel_format = self._config.default_pixel_format
        self._binning = self._config.default_binning

        # Since we need to set the on-camera exposure time different depending on our trigger mode
        # (eg: sometimes we compensate for a strobe delay when hardware triggering), we can't back
        # out our users' exposure time easily from the camera value.  To get around this, we need
        # to store the exposure time they give to us.
        #
        # Because it is better than nothing, we initialize our stored value to whatever is on the
        # camera at startup (but then set_exposure_time will modify it when a user sets exposure time)
        self._exposure_time = self._get_raw_exposure_time()

        # toupcam temperature
        self.temperature_reading_callback = None
        self.terminate_read_temperature_thread = False
        self.thread_read_temperature = threading.Thread(target=self._check_temperature, daemon=True)
        self.thread_read_temperature.start()

        self._configure_camera()
        self._start_raw_camera_stream()
        self._update_internal_settings()

        # Per-frame timing diagnostics — accumulates a small rolling window
        # in _on_frame_callback and logs every N frames so we can see where
        # the per-frame time goes in continuous vs trigger mode.
        self._diag_last_callback_start_ns: Optional[int] = None
        self._diag_frame_log_every = 30
        self._log_startup_option_dump()

    def _log_startup_option_dump(self):
        """Dump every Toupcam option that plausibly affects FPS so we can
        compare configured state against what the diagnostic log claims.
        Runs once at construction; suppresses errors so unsupported options
        on a given SKU don't abort startup."""
        rate_options = [
            ("TRIGGER", toupcam.TOUPCAM_OPTION_TRIGGER),
            ("PRECISE_FRAMERATE", toupcam.TOUPCAM_OPTION_PRECISE_FRAMERATE),
            ("MAX_PRECISE_FRAMERATE", toupcam.TOUPCAM_OPTION_MAX_PRECISE_FRAMERATE),
            ("FRAMERATE_LIMIT", toupcam.TOUPCAM_OPTION_FRAMERATE),
            ("BANDWIDTH", toupcam.TOUPCAM_OPTION_BANDWIDTH),
            ("DDR_DEPTH", toupcam.TOUPCAM_OPTION_DDR_DEPTH),
            ("RAW", toupcam.TOUPCAM_OPTION_RAW),
            ("LINEAR", toupcam.TOUPCAM_OPTION_LINEAR),
            ("CURVE", toupcam.TOUPCAM_OPTION_CURVE),
            ("MULTITHREAD", toupcam.TOUPCAM_OPTION_MULTITHREAD),
            ("LOW_NOISE", toupcam.TOUPCAM_OPTION_LOW_NOISE),
        ]
        parts = []
        for name, opt in rate_options:
            try:
                parts.append(f"{name}={self._camera.get_Option(opt)}")
            except Exception:
                parts.append(f"{name}=?")
        try:
            ae = self._camera.get_AutoExpoEnable()
            parts.append(f"AUTOEXP={ae}")
        except Exception:
            parts.append("AUTOEXP=?")
        self._log.info("Toupcam startup option dump: " + ", ".join(parts))

    def _start_raw_camera_stream(self):
        """
        Make sure the camera is setup to tell us when frames are available.
        """
        try:
            self._log.debug("Starting raw stream in PullModeWithCallback.")
            self._camera.StartPullModeWithCallback(self._event_callback, self)
            self._raw_camera_stream_started = True
        except toupcam.HRESULTException as ex:
            self._raw_camera_stream_started = False
            self._log.exception("failed to start camera, hr=0x{:x}".format(ex.hr))
            raise ex

    def _rgb_image_from_read_buffer(self, width: int, height: int, pixel_size: int) -> np.array:
        """
        Unpack a frame the SDK produced in RGB frame format out of the internal read buffer.

        Rows are padded out to the pitch _row_pitch_bytes describes, so the padding has to
        be sliced off before the buffer can be seen as an image.  Not every frame read this
        way is colour: RGB frame format also covers the SDK's Grey8/Grey16 output (a mono
        pixel format in RGB mode), which stays 2D.  Colour frames come back as
        (height, width, 3) - uint8 for RGB24/RGB32, uint16 for RGB48 - with RGB32's fourth
        byte per pixel dropped, since the rest of squid expects 3 channel colour frames.
        """
        row_pitch = ToupcamCamera._row_pitch_bytes(width, pixel_size)
        rows = np.frombuffer(self._internal_read_buffer, dtype=np.uint8, count=row_pitch * height).reshape(
            height, row_pitch
        )
        packed = rows[:, : width * pixel_size]

        if pixel_size in (2, 6):
            # uint16 components: one for Grey16, three for RGB48.  .view needs a contiguous
            # buffer, and slicing the padding off broke that.
            wide = np.ascontiguousarray(packed).view(np.uint16)
            return wide.reshape(height, width) if pixel_size == 2 else wide.reshape(height, width, 3)

        if pixel_size == 1:  # Grey8
            return packed.reshape(height, width)

        image = packed.reshape(height, width, pixel_size)
        return image[:, :, :3] if pixel_size == 4 else image

    def _on_frame_callback(self):
        """
        This is the callback that we have the toupcam software call when a frame is ready.  It should always be running.
        """
        callback_start_ns = time.perf_counter_ns()
        with self._raw_frame_callback_lock:
            # Since we are receiving a frame callback, we know things are setup properly.
            self._raw_camera_stream_started = True

            # Make sure that if this was triggered by a software trigger, or we switched to software triggering
            # while waiting for this frame, that we allow subsequent software triggers.
            self._trigger_sent = False

            # get the image from the camera
            pull_start_ns = time.perf_counter_ns()
            try:
                self._camera.PullImageV2(
                    self._internal_read_buffer, self._get_pixel_size_in_bytes() * 8, None
                )  # the second camera is number of bits per pixel - ignored in RAW mode
            except toupcam.HRESULTException as ex:
                # TODO(imo): Propagate error in some way and handle
                self._log.error("pull image failed, hr=0x{:x}".format(ex.hr))
            pull_done_ns = time.perf_counter_ns()

            this_frame_id = (self._current_frame.frame_id if self._current_frame else 0) + 1
            this_timestamp = time.time()
            this_frame_format = self.get_frame_format()
            this_pixel_format = self.get_pixel_format()

            (x_offset, y_offset, width, height) = self.get_region_of_interest()
            pixel_size = self._get_pixel_size_in_bytes()

            if this_frame_format == CameraFrameFormat.RGB:
                current_raw_image = self._rgb_image_from_read_buffer(width, height, pixel_size)
            elif pixel_size == 1:
                current_raw_image = np.frombuffer(self._internal_read_buffer, dtype="uint8").reshape(height, width)
            elif pixel_size == 2:
                current_raw_image = np.frombuffer(self._internal_read_buffer, dtype="uint16").reshape(height, width)
            else:
                self._log.error(f"Cannot handle a RAW frame with {pixel_size=}, dropping it.")
                return

            process_start_ns = time.perf_counter_ns()
            current_frame = CameraFrame(
                frame_id=this_frame_id,
                timestamp=this_timestamp,
                frame=self._process_raw_frame(current_raw_image),
                frame_format=this_frame_format,
                frame_pixel_format=this_pixel_format,
            )
            process_done_ns = time.perf_counter_ns()

            # Before releasing the lock, set the new current fram with the incremented frame id so other methods can
            # see we have a new frame. This should be the only place we modify _current_frame outside of init, and
            # since we hold a lock this whole time, we know that the frame id is still correct.
            self._current_frame = current_frame

        # Propagate the local copy so we are sure it's the correct frame that goes out.
        propagate_start_ns = time.perf_counter_ns()
        self._propogate_frame(current_frame)
        propagate_done_ns = time.perf_counter_ns()

        # Per-frame timing diagnostic — logged every N frames so we can spot
        # which stage paces continuous mode. Inter-frame interval is the gap
        # between consecutive callback entries; pull/process/propagate are
        # per-stage durations. All in milliseconds.
        if this_frame_id % self._diag_frame_log_every == 0:
            inter_frame_ms = (
                (callback_start_ns - self._diag_last_callback_start_ns) / 1e6
                if self._diag_last_callback_start_ns is not None
                else 0.0
            )
            try:
                mode_name = self.get_acquisition_mode().name
            except Exception:
                mode_name = "?"
            self._log.info(
                f"frame {this_frame_id} ({mode_name}): "
                f"interval={inter_frame_ms:.1f}ms "
                f"pull={(pull_done_ns - pull_start_ns) / 1e6:.1f}ms "
                f"process={(process_done_ns - process_start_ns) / 1e6:.1f}ms "
                f"propagate={(propagate_done_ns - propagate_start_ns) / 1e6:.1f}ms "
                f"total={(propagate_done_ns - callback_start_ns) / 1e6:.1f}ms"
            )
        self._diag_last_callback_start_ns = callback_start_ns

    def _update_internal_settings(self, send_exposure=True):
        """
        This needs to be called when a camera side setting changes that needs a:
          * read buffer size update
          * strobe delay recalc

        It might be called in a performance sensitive context, so you should make sure any updates here
        are as fast as they can be.
        """
        # resize the buffer
        _, _, width, height = self._camera.get_Roi()

        # calculate buffer size
        pixel_size = self._get_pixel_size_in_bytes()
        if self.get_frame_format() == CameraFrameFormat.RGB:
            buffer_size = ToupcamCamera._row_pitch_bytes(width, pixel_size) * height
        else:
            buffer_size = width * pixel_size * height
        # create the buffer
        self._internal_read_buffer = bytes(buffer_size)

        image_exposure_time_ms = self.get_exposure_time()
        camera_exposure_time_ms = self._calculate_camera_exposure_time(image_exposure_time_ms)
        self._strobe_info = ToupcamCamera._calculate_strobe_info(
            camera=self._camera,
            pixel_size=self._get_pixel_size_in_bytes(),
            exposure_time_ms=camera_exposure_time_ms,
            capabilities=self._capabilities,
        )
        if self._hw_set_strobe_delay_ms_fn and self.get_acquisition_mode() == CameraAcquisitionMode.HARDWARE_TRIGGER:
            self._hw_set_strobe_delay_ms_fn(self.get_strobe_time())

        if send_exposure:
            self._calculate_and_set_camera_exposure_time(image_exposure_time_ms)

        self._log.debug(
            f"image size: {width=} x {height=}, {buffer_size=}, strobe_time={self.get_strobe_time()} [ms], exposure_time={self.get_exposure_time()} [ms], full frame time={self.get_total_frame_time()} [ms], {send_exposure=}"
        )

    def _check_temperature(self):
        while not self.terminate_read_temperature_thread:
            time.sleep(2)
            temperature = self.get_temperature()
            if self.temperature_reading_callback is not None:
                try:
                    self.temperature_reading_callback(temperature)
                except TypeError as ex:
                    self._log.error("Temperature read callback failed due to error: " + repr(ex))
                    pass

    def _configure_camera(self):
        """
        Run our initial configuration to get the camera into a know and safe starting state.
        """
        # Disable auto-exposure BEFORE StartPullModeWithCallback runs. The Toupcam
        # SDK's OPTION_DDR_DEPTH "auto" default caches only one frame in video
        # mode when auto-exposure is enabled (per the SDK doc), which forces the
        # host PullImage path to serialize with sensor readout — observed as
        # ~2 fps continuous mode when the stream was started fresh in continuous
        # mode versus ~10 fps when started in trigger mode and flipped. Squid
        # sets exposure explicitly per channel, so disabling auto-exposure has
        # no user-visible effect besides unlocking full DDR buffering.
        try:
            self._camera.put_AutoExpoEnable(False)
        except toupcam.HRESULTException as ex:
            self._log.warning(f"Could not disable auto-exposure: {control.toupcam_exceptions.explain(ex)}")

        if self._capabilities.has_low_noise_mode:
            self._camera.put_Option(toupcam.TOUPCAM_OPTION_LOW_NOISE, 0)

        self._set_fan_speed(self._config.default_fan_speed)

        # set temperature
        if self._config.default_temperature is None:
            if self._capabilities.has_TEC:
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_TEC, 0)
                self._log.info("TEC disabled (default_temperature is None)")
        else:
            self.set_temperature(self._config.default_temperature)

        # The frame format has to follow the pixel format: a colour camera configured with
        # an RGB default_pixel_format needs the SDK's debayering, which only runs in RGB
        # frame format.  Hard coding RAW here made every RGB format fail to configure.
        self._raw_set_frame_format(ToupcamCamera._frame_format_for_pixel_format(self._pixel_format))
        self._raw_set_pixel_format(self._pixel_format)
        try:
            self.set_black_level(self._config.default_black_level)
        except NotImplementedError:
            self._log.warning("Black level is not supported by this toupcam model, ignoring default black level value")

        # We can't trigger update_internal_settings yet, because the strobe calc will fail.  So set the res
        # using the raw helper.
        (width, height) = self._capabilities.binning_to_resolution[self._binning]
        self._raw_set_resolution(width, height)

        # TODO: Do hardware cropping here (set ROI)

    def set_temperature_reading_callback(self, func):
        self.temperature_reading_callback = func

    def _get_raw_exposure_time(self) -> float:
        return self._camera.get_ExpoTime() / 1000.0  # microseconds -> milliseconds

    def close(self):
        self.terminate_read_temperature_thread = True
        self.thread_read_temperature.join()
        self._set_fan_speed(0)
        self._camera.Close()
        self._camera = None

    def start_streaming(self):
        self._log.info("start streaming requested")
        if not self._raw_camera_stream_started:
            self._start_raw_camera_stream()

    def stop_streaming(self):
        self._camera.Stop()
        self._raw_camera_stream_started = False

    def get_is_streaming(self):
        return self._raw_camera_stream_started

    def set_exposure_time(self, exposure_time_ms: float):
        # Since we have to set the on-camera exposure time differently depending on the trigger mode
        # and the calculated strobe delay, it is tricky to get the exposure time from the
        # camera.  To get around this, we store it.
        self._exposure_time = exposure_time_ms

        self._update_internal_settings(send_exposure=True)

    def _calculate_camera_exposure_time(self, image_exposure_time_ms):
        exposure_for_camera_ms = image_exposure_time_ms
        # In the calls below, we need to make sure we convert to microseconds.
        if self.get_acquisition_mode() == CameraAcquisitionMode.HARDWARE_TRIGGER:
            # Only add the strobe_time_us, and not strobe_time_us + trigger_delay_us.  We'll tell the lighting
            # to come on at strobe_time_us + trigger_delay_us since that's when the common (all row) exposure time
            # starts, but if we tell that to the camera we'll get an extra trigger_delay_us of exposure.
            exposure_for_camera_ms += self._strobe_info.strobe_time_us / 1000.0

        return exposure_for_camera_ms

    def _calculate_and_set_camera_exposure_time(self, image_exposure_time_ms):
        exposure_for_camera_us = int(self._calculate_camera_exposure_time(image_exposure_time_ms) * 1000.0)
        self._log.debug(
            f"Sending exposure {exposure_for_camera_us} [us] to camera for image_exposure_time={1000 * image_exposure_time_ms} [us]"
        )
        self._camera.put_ExpoTime(exposure_for_camera_us)

    def get_exposure_time(self) -> float:
        return self._exposure_time

    def get_exposure_limits(self) -> Tuple[float, float]:
        (min_exposure, max_exposure, default_exposure) = self._camera.get_ExpTimeRange()
        return min_exposure / 1000.0, max_exposure / 1000.0  # us -> ms

    @staticmethod
    def _user_gain_to_toupcam(user_gain):
        """
        0-40 is the valid user range.  This must map to 100-10000 in toupcam
        """
        return int(100 * (10 ** (user_gain / 20)))

    @staticmethod
    def _toupcam_gain_to_user(toupcam_gain):
        return 20 * math.log10(toupcam_gain / 100)

    def set_analog_gain(self, analog_gain):
        gain_range = self.get_gain_range()

        clamped_gain = max(gain_range.min_gain, min(analog_gain, gain_range.max_gain))

        if clamped_gain != analog_gain:
            self._log.warning(
                f"Requested {analog_gain=} is outside the range {gain_range.min_gain} to {gain_range.max_gain}"
            )

        # for touptek cameras gain is 100-10000 (for 1x - 100x)
        self._log.info(f"Trying to set analog gain = {clamped_gain}")
        self._camera.put_ExpoAGain(self._user_gain_to_toupcam(clamped_gain))

    def _raw_set_pixel_format(self, pixel_format: CameraPixelFormat):
        if self.get_frame_format() == CameraFrameFormat.RAW:
            if pixel_format == CameraPixelFormat.MONO8:
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 0)
            elif pixel_format == CameraPixelFormat.MONO12:
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 1)
            elif pixel_format == CameraPixelFormat.MONO14:
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 1)
            elif pixel_format == CameraPixelFormat.MONO16:
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 1)
            else:
                raise ValueError(f"Unsupported pixel format: {pixel_format=}")
        else:
            # RGB data format
            if pixel_format == CameraPixelFormat.MONO8:
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 0)
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_RGB, 3)  # for monochrome camera only
            elif pixel_format == CameraPixelFormat.MONO12:
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 1)
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_RGB, 4)  # for monochrome camera only
            elif pixel_format == CameraPixelFormat.MONO14:
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 1)
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_RGB, 4)  # for monochrome camera only
            elif pixel_format == CameraPixelFormat.MONO16:
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 1)
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_RGB, 4)  # for monochrome camera only
            elif pixel_format == CameraPixelFormat.RGB24:
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 0)
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_RGB, 0)
            elif pixel_format == CameraPixelFormat.RGB32:
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 0)
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_RGB, 2)
            elif pixel_format == CameraPixelFormat.RGB48:
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_BITDEPTH, 1)
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_RGB, 1)
            else:
                raise ValueError(f"Unsupported pixel format: {pixel_format=}")

        # NOTE(imo): Ideally we'd query pixel_format from the device instead of storing the state here, but it's
        # impossible to do so - the settings for a particular depth are not unique.  EG MONO12 and MONO14 both
        # have the same settings.  I'm not sure how this works?  But just store the pixel format here...
        self._pixel_format = pixel_format

    def set_pixel_format(self, pixel_format: CameraPixelFormat):
        # Validate before touching the camera.  The frame format switch below and the pixel
        # format itself have to land together: applying the first and then failing on the
        # second leaves a frame/pixel format pair that _get_pixel_size_in_bytes cannot map,
        # which breaks every later frame and exposure change rather than just this call.
        available = self.get_available_pixel_formats()
        if pixel_format not in available:
            raise ValueError(
                f"Unsupported pixel format: {pixel_format=}. This camera supports: "
                f"{', '.join(pf.name for pf in available)}."
            )

        with self._pause_streaming():
            # Switching between a MONO and an RGB format is also a frame format switch, so
            # do it here rather than making every caller pair the two calls up themselves.
            self._raw_set_frame_format(ToupcamCamera._frame_format_for_pixel_format(pixel_format))
            self._raw_set_pixel_format(pixel_format)
            self.set_black_level(self._config.default_black_level)
        self._update_internal_settings()

    def get_pixel_format(self) -> CameraPixelFormat:
        return self._pixel_format

    def get_available_pixel_formats(self) -> Sequence[CameraPixelFormat]:
        """
        The pixel formats this camera can actually produce.

        A monochrome sensor reads out grey levels; a colour one is debayered by the SDK
        into RGB.  Reading a colour sensor with a MONO format is possible but hands back
        undebayered Bayer data that looks like a grid artifact, so it is not offered.

        Reporting this properly matters for the GUI: the camera settings tab falls back to
        a hard coded mono list when this raises, which on a colour camera offers formats
        that cannot be set and hides the ones that can.
        """
        if self._capabilities.is_mono:
            return (
                CameraPixelFormat.MONO8,
                CameraPixelFormat.MONO12,
                CameraPixelFormat.MONO14,
                CameraPixelFormat.MONO16,
            )
        return ToupcamCamera._RGB_PIXEL_FORMATS

    def set_auto_exposure(self, enabled: bool):
        try:
            self._camera.put_AutoExpoEnable(enabled)
        except toupcam.HRESULTException as ex:
            self._log.exception("Unable to set auto exposure: " + repr(ex))
            raise

    def _raw_set_frame_format(self, data_format: CameraFrameFormat):
        if data_format == CameraFrameFormat.RGB:
            self._camera.put_Option(
                toupcam.TOUPCAM_OPTION_RAW, ToupcamCamera.TOUPCAM_OPTION_RAW_RGB_VAL
            )  # 0 is RGB mode, 1 is RAW mode
            # The SDK's byte order defaults to BGR on Windows (RGB elsewhere).  The rest of
            # squid treats colour frames as RGB, so pin it rather than inheriting a channel
            # swap that only shows up on one platform.  Not every model implements it.
            try:
                self._camera.put_Option(toupcam.TOUPCAM_OPTION_BYTEORDER, 0)  # 0 is RGB, 1 is BGR
            except toupcam.HRESULTException as ex:
                self._log.warning(
                    f"Could not pin the byte order to RGB, colour channels may be swapped --> "
                    f"{control.toupcam_exceptions.explain(ex)}"
                )
        elif data_format == CameraFrameFormat.RAW:
            self._camera.put_Option(
                toupcam.TOUPCAM_OPTION_RAW, ToupcamCamera.TOUPCAM_OPTION_RAW_RAW_VAL
            )  # 1 is RAW mode, 0 is RGB mode

    def set_frame_format(self, data_format: CameraFrameFormat):
        with self._pause_streaming():
            self._raw_set_frame_format(data_format)
        self._update_internal_settings()

    def get_frame_format(self) -> CameraFrameFormat:
        camera_val = self._camera.get_Option(toupcam.TOUPCAM_OPTION_RAW)

        if camera_val == ToupcamCamera.TOUPCAM_OPTION_RAW_RAW_VAL:
            return CameraFrameFormat.RAW
        elif camera_val == ToupcamCamera.TOUPCAM_OPTION_RAW_RGB_VAL:
            return CameraFrameFormat.RGB
        else:
            raise ValueError(f"Camera returned unknown frame format: value={camera_val}")

    def set_binning(self, binning_factor_x: int, binning_factor_y: int):
        with self._pause_streaming():
            if (binning_factor_x, binning_factor_y) not in self._capabilities.binning_to_resolution:
                raise ValueError(f"Binning ({binning_factor_x},{binning_factor_y}) not supported by camera")
            width, height = self._capabilities.binning_to_resolution[(binning_factor_x, binning_factor_y)]
            self._raw_set_resolution(width, height)
            self._binning = (binning_factor_x, binning_factor_y)
            self._log.debug(f"Setting binning to {binning_factor_x},{binning_factor_y} -> {width},{height}")

            # We will disable hardware cropping until hardware trigger issue is resolved.
            # old_binning = self._binning
            # self._binning = (binning_factor_x, binning_factor_y)
            # old_roi = self.get_region_of_interest()

        # new_roi = AbstractCamera.calculate_new_roi_for_binning(old_binning, old_roi, self._binning)
        # self._log.debug(f"Changing roi from {old_roi=} to {new_roi=} to keep FOV the same after resolution change.")
        # self.set_region_of_interest(*new_roi)

        self._update_internal_settings()

    def _raw_set_resolution(self, width, height):
        try:
            self._camera.put_Size(width, height)
        except toupcam.HRESULTException as ex:
            err_type = hresult_checker(ex, "E_INVALIDARG", "E_BUSY", "E_ACCESDENIED", "E_UNEXPECTED")
            if err_type == "E_INVALIDARG":
                self._log.exception(f"Resolution ({width},{height}) not supported by camera")
            else:
                self._log.exception(f"Resolution cannot be set due to error: " + err_type)
            raise

    def get_temperature(self):
        try:
            return self._camera.get_Temperature() / 10
        except toupcam.HRESULTException as ex:
            error_type = hresult_checker(ex)
            self._log.exception("Could not get temperature, error: " + error_type)
            raise

    def set_temperature(self, temperature):
        try:
            self._camera.put_Temperature(int(temperature * 10))
        except toupcam.HRESULTException as ex:
            error_type = hresult_checker(ex)
            self._log.exception("Unable to set temperature: " + error_type)
            raise

    def _set_fan_speed(self, speed):
        try:
            self._camera.put_Option(toupcam.TOUPCAM_OPTION_FAN, speed)
        except toupcam.HRESULTException as ex:
            error_type = hresult_checker(ex)
            self._log.exception("Unable to set fan speed: " + error_type)
            raise

    def _set_trigger_width_mode(self):
        self._camera.IoControl(1, toupcam.TOUPCAM_IOCONTROLTYPE_SET_PWMSOURCE, 1)  # set PWM source to GPIO0
        self._camera.IoControl(1, toupcam.TOUPCAM_IOCONTROLTYPE_SET_TRIGGERSOURCE, 4)  # trigger source to PWM

    def _set_gain_mode(self, mode):
        if mode == "LCG":
            self._camera.put_Option(toupcam.TOUPCAM_OPTION_CG, 0)
        elif mode == "HCG":
            self._camera.put_Option(toupcam.TOUPCAM_OPTION_CG, 1)
        elif mode == "HDR":
            self._camera.put_Option(toupcam.TOUPCAM_OPTION_CG, 2)

    def send_trigger(self, illumination_time: Optional[float] = None):
        if self.get_acquisition_mode() == CameraAcquisitionMode.HARDWARE_TRIGGER and not self._hw_trigger_fn:
            raise RuntimeError("In HARDWARE_TRIGGER mode, but no hw trigger function given.")

        if not self.get_ready_for_trigger():
            raise RuntimeError(
                f"Requested trigger too early (last trigger was {time.time() - self._last_trigger_timestamp} [s] ago), refusing."
            )

        if self.get_acquisition_mode() == CameraAcquisitionMode.HARDWARE_TRIGGER:
            self._log.debug(f"Sending hardware trigger with {illumination_time=}")
            self._hw_trigger_fn(illumination_time)
        elif self.get_acquisition_mode() == CameraAcquisitionMode.SOFTWARE_TRIGGER:
            self._log.debug("Sending software trigger..")
            self._camera.Trigger(1)

        self._last_trigger_timestamp = time.time()
        self._trigger_sent = True

    def get_ready_for_trigger(self) -> bool:
        # TODO(imo): Should we pass in the timeout?  This might be fine since it's calculated based on the exposure time.
        trigger_timeout_s = 1.5 * self._get_raw_exposure_time() / 1000 * 1.02 + 4
        trigger_age = time.time() - self._last_trigger_timestamp
        trigger_too_old = trigger_age > trigger_timeout_s
        trigger_sent = self._trigger_sent
        if trigger_sent and trigger_too_old:
            self._log.warning(
                f"Previous software trigger timed out after {trigger_timeout_s} [s]. Assuming it failed and allowing re-trigger."
            )
            self._trigger_sent = False
        elif trigger_sent:
            return False
        return True

    def _stop_exposure(self):
        if self.get_is_streaming() and self._trigger_sent == True:
            self._camera.Trigger(0)
            self._trigger_sent = False
        else:
            pass

    def get_strobe_time(self) -> float:
        # Use both strobe_time_us and trigger_delay_us here because our notion of "strobe time" is when the
        # last row first starts exposing.  For the toupcam, this happens after trigger delay + strobe time.
        #
        # For software lighting, sleeping get_strobe_time() + get_exposure_time() works.  For hardware triggering,
        # we need to ignore trigger_delay_us since the camera itself imposes that delay after it sees the trigger.
        return (self._strobe_info.strobe_time_us + self._strobe_info.trigger_delay_us) / 1000.0

    def set_region_of_interest(self, offset_x: int, offset_y: int, width: int, height: int):
        roi_offset_x = control.utils.truncate_to_interval(offset_x, 2)
        roi_offset_y = control.utils.truncate_to_interval(offset_y, 2)
        roi_width = control.utils.truncate_to_interval(width, 2)
        roi_height = control.utils.truncate_to_interval(height, 2)
        with self._pause_streaming():
            try:
                self._camera.put_Roi(roi_offset_x, roi_offset_y, roi_width, roi_height)
            except toupcam.HRESULTException as ex:
                self._log.exception("ROI bounds invalid, not changing ROI.")

        self._update_internal_settings()

    def get_binning(self) -> Tuple[int, int]:
        return self._binning

    def get_binning_options(self) -> Sequence[Tuple[int, int]]:
        return self._capabilities.binning_to_resolution.keys()

    def get_resolution(self) -> Tuple[int, int]:
        return self._capabilities.binning_to_resolution[self._binning]

    def get_pixel_size_unbinned_um(self) -> float:
        return self.PIXEL_SIZE_UM

    def get_pixel_size_binned_um(self) -> float:
        return (
            self.PIXEL_SIZE_UM * self.get_binning()[0]
        )  # We will use the same binning factor in width and height for now

    def get_analog_gain(self) -> float:
        return self._toupcam_gain_to_user(self._camera.get_ExpoAGain())

    def get_gain_range(self) -> CameraGainRange:
        (min_gain, max_gain, default_gain) = self._camera.get_ExpoAGainRange()
        return CameraGainRange(
            min_gain=self._toupcam_gain_to_user(min_gain), max_gain=self._toupcam_gain_to_user(max_gain), gain_step=0.01
        )

    def read_camera_frame(self):
        # TODO(imo): Seems like the timeout should be something passed in, not hard coded.
        timeout_s = (self.get_exposure_time() / 1000) * 1.02 + 4
        timeout_end_time_s = time.time() + timeout_s
        starting_frame_id = self.get_frame_id()

        while time.time() < timeout_end_time_s:
            if self.get_frame_id() != starting_frame_id:
                return self._current_frame
            time.sleep(0.001)

        self._log.error(f"Timed out after {timeout_s} [s] waiting for a frame.")

        return None

    def get_frame_id(self) -> int:
        return self._current_frame.frame_id if self._current_frame else -1

    def get_white_balance_gains(self) -> Tuple[float, float, float]:
        return self._camera.get_WhiteBalanceGain()

    def set_white_balance_gains(self, red_gain: float, green_gain: float, blue_gain: float):
        # The SDK takes integer gains (c_int * 3) and ctypes rejects floats outright, but
        # AbstractCamera types these as float and cached gains come back off disk as
        # floats, so round rather than hand them straight through.
        self._camera.put_WhiteBalanceGain((round(red_gain), round(green_gain), round(blue_gain)))

    def set_auto_white_balance_gains(self, on: bool) -> Tuple[float, float, float]:
        """
        Turn auto white balance on or off, and return the resulting (R, G, B) gains.

        The SDK's auto white balance (AwbInit) is a one push operation: it works out the
        gains once and leaves them set, so there is no continuous adjustment to turn back
        off here.  (The SDK does have a continuous mode, TOUPCAM_OPTION_AWB_CONTINUOUS,
        but this driver never enables it.)
        """
        if on:
            self._camera.AwbInit()
        else:
            self._log.debug("Auto white balance is one push on toupcam cameras, nothing to turn off.")
        return self.get_white_balance_gains()

    _BLACK_LEVEL_MAPPING = {
        (CameraFrameFormat.RAW, CameraPixelFormat.MONO8): 1,
        (CameraFrameFormat.RAW, CameraPixelFormat.MONO12): 16,
        (CameraFrameFormat.RAW, CameraPixelFormat.MONO14): 64,
        (CameraFrameFormat.RAW, CameraPixelFormat.MONO16): 256,
        # TODO(imo): We didn't set a black level factor if outside of 1 of the 4 options above, but still used the factor.  Is the mapping below correct, or is black level ignored for RGB?
        (CameraFrameFormat.RGB, CameraPixelFormat.MONO8): 1,
        (CameraFrameFormat.RGB, CameraPixelFormat.MONO12): 16,
        (CameraFrameFormat.RGB, CameraPixelFormat.MONO14): 64,
        (CameraFrameFormat.RGB, CameraPixelFormat.MONO16): 256,
        (CameraFrameFormat.RGB, CameraPixelFormat.RGB24): 1,  # Bit depth of 8 -> same as MONO8
        (CameraFrameFormat.RGB, CameraPixelFormat.RGB32): 1,  # Bit depth of 8 -> same as MONO8
        (CameraFrameFormat.RGB, CameraPixelFormat.RGB48): 256,  # Bit depth of 16 -> same as MONO16
    }

    def _get_black_level_factor(self):
        frame_and_format = (self.get_frame_format(), self.get_pixel_format())
        if frame_and_format not in ToupcamCamera._BLACK_LEVEL_MAPPING:
            raise ValueError(f"Unknown combo for black level: {frame_and_format=}")

        return ToupcamCamera._BLACK_LEVEL_MAPPING[frame_and_format]

    _PIXEL_SIZE_MAPPING = {
        (CameraFrameFormat.RAW, CameraPixelFormat.MONO8): 1,
        (CameraFrameFormat.RAW, CameraPixelFormat.MONO12): 2,
        (CameraFrameFormat.RAW, CameraPixelFormat.MONO14): 2,
        (CameraFrameFormat.RAW, CameraPixelFormat.MONO16): 2,
        (CameraFrameFormat.RGB, CameraPixelFormat.MONO8): 1,
        (CameraFrameFormat.RGB, CameraPixelFormat.MONO12): 2,
        (CameraFrameFormat.RGB, CameraPixelFormat.MONO14): 2,
        (CameraFrameFormat.RGB, CameraPixelFormat.MONO16): 2,
        (CameraFrameFormat.RGB, CameraPixelFormat.RGB24): 3,
        (CameraFrameFormat.RGB, CameraPixelFormat.RGB32): 4,
        (CameraFrameFormat.RGB, CameraPixelFormat.RGB48): 6,
    }

    def _get_pixel_size_in_bytes(self):
        frame_and_format = (self.get_frame_format(), self.get_pixel_format())
        if frame_and_format not in ToupcamCamera._PIXEL_SIZE_MAPPING:
            raise ValueError(f"Unknown combo for pixel size: {frame_and_format=}")

        return ToupcamCamera._PIXEL_SIZE_MAPPING[frame_and_format]

    def get_black_level(self) -> float:
        if not self._capabilities.has_black_level:
            raise NotImplementedError("This toupcam does not have black level setting.")

        raw_black_level = self._camera.get_Option(toupcam.TOUPCAM_OPTION_BLACKLEVEL)

        return raw_black_level / self._get_black_level_factor()

    def set_black_level(self, black_level: float):
        if not self._capabilities.has_black_level:
            raise NotImplementedError("This toupcam does not have black level setting.")
        raw_black_level = black_level * self._get_black_level_factor()

        try:
            self._camera.put_Option(toupcam.TOUPCAM_OPTION_BLACKLEVEL, raw_black_level)
        except toupcam.HRESULTException as ex:
            print("put blacklevel fail, hr=0x{:x}".format(ex.hr))

    def _set_acquisition_mode_imp(self, acquisition_mode: CameraAcquisitionMode):
        if acquisition_mode == CameraAcquisitionMode.CONTINUOUS:
            trigger_option_value = 0
        elif acquisition_mode == CameraAcquisitionMode.SOFTWARE_TRIGGER:
            trigger_option_value = 1
        elif acquisition_mode == CameraAcquisitionMode.HARDWARE_TRIGGER:
            trigger_option_value = 2
        else:
            raise ValueError(f"Do not know how to handle {acquisition_mode=}")
        self._camera.put_Option(toupcam.TOUPCAM_OPTION_TRIGGER, trigger_option_value)

        if acquisition_mode == CameraAcquisitionMode.HARDWARE_TRIGGER:
            if HARDWARE_TRIGGER_MODE == HardwareTriggerMode.LEVEL:
                try:
                    self._camera.put_Option(toupcam.TOUPCAM_OPTION_TRIGGER, 2)
                except toupcam.HRESULTException as ex:
                    error_type = hresult_checker(ex)
                    # TODO(imo): Propagate error in some way and handle
                    self._log.error("Unable to set option_trigger to 2: " + error_type)

                try:
                    # set IO controltype to PWM mode
                    self._camera.IoControl(0, toupcam.TOUPCAM_IOCONTROLTYPE_SET_TRIGGERSOURCE, 4)
                    self._camera.IoControl(2, toupcam.TOUPCAM_IOCONTROLTYPE_SET_GPIODIR, 0)
                    self._camera.IoControl(2, toupcam.TOUPCAM_IOCONTROLTYPE_SET_PWMSOURCE, 1)
                except toupcam.HRESULTException as ex:
                    error_type = hresult_checker(ex)
                    # TODO(imo): Propagate error in some way and handle
                    self._log.error("Unable to select trigger source: " + error_type)
            else:
                # select trigger source to GPIO0
                try:
                    self._camera.IoControl(1, toupcam.TOUPCAM_IOCONTROLTYPE_SET_TRIGGERSOURCE, 1)
                except toupcam.HRESULTException as ex:
                    error_type = hresult_checker(ex)
                    self._log.exception("Unable to select trigger source: " + error_type)
                    raise
                # set GPIO1 to trigger wait
                try:
                    self._camera.IoControl(3, toupcam.TOUPCAM_IOCONTROLTYPE_SET_OUTPUTMODE, 0)
                    self._camera.IoControl(3, toupcam.TOUPCAM_IOCONTROLTYPE_SET_OUTPUTINVERTER, 0)
                except toupcam.HRESULTException as ex:
                    error_type = hresult_checker(ex)
                    self._log.exception("Unable to set GPIO1 for trigger ready: " + error_type)
                    raise
        # Re-set exposure time to force strobe to get set to the remote.
        self.set_exposure_time(self.get_exposure_time())

    def get_acquisition_mode(self) -> CameraAcquisitionMode:
        trigger_option_value = self._camera.get_Option(toupcam.TOUPCAM_OPTION_TRIGGER)
        if trigger_option_value == 0:
            return CameraAcquisitionMode.CONTINUOUS
        elif trigger_option_value == 1:
            return CameraAcquisitionMode.SOFTWARE_TRIGGER
        elif trigger_option_value == 2:
            return CameraAcquisitionMode.HARDWARE_TRIGGER
        else:
            raise ValueError(f"Received unknown trigger option from toupcam: {trigger_option_value}")

    def get_region_of_interest(self) -> Tuple[int, int, int, int]:
        return self._camera.get_Roi()
