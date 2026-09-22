import time
from typing import Optional, Callable, List, Sequence, Tuple, Dict
import threading

import pydantic

from squid.abc import AbstractCamera, CameraError
from squid.config import CameraConfig, CameraPixelFormat
from squid.abc import CameraFrame, CameraFrameFormat, CameraGainRange, CameraAcquisitionMode
from control.dcam import Dcam, Dcamapi
from control.dcamapi4 import *
import control.utils


class HamamatsuCapabilities(pydantic.BaseModel):
    binning_to_resolution: Dict[Tuple[int, int], Tuple[int, int]]


# DCAM ring depth. Live view takes the newest frame and drops the rest, so five is plenty. In a triggered
# mode every frame was asked for and none may be dropped: the ring has to cover the longest the read
# thread can be held up. Bench 2026-09-21 (hardware-sequenced bursts, 41 ms per frame): worst 152 ms, while
# the previous burst was being handed to the save jobs. The stall is a TIME, so faster frame rates need
# more frames for the same stall: 32 frames is 1.3 s at 41 ms and 0.5 s at 16 ms.
_LIVE_RING_FRAMES = 5
_TRIGGERED_RING_FRAMES = 32


class HamamatsuCamera(AbstractCamera):
    PIXEL_SIZE_UM: float = 6.5

    @staticmethod
    def _open(index=None, sn=None) -> Tuple[Dcam, HamamatsuCapabilities]:
        if index is None and sn is None:
            raise ValueError("You must specify one of either index or sn.")
        elif index is not None and sn is not None:
            raise ValueError("You must specify only 1 of index or sn")

        dcam_init_result = Dcamapi.init()
        if isinstance(dcam_init_result, bool):
            if not dcam_init_result:
                raise CameraError(
                    f"Dcam api initialization failed: {HamamatsuCamera._last_dcam_error_string_direct(Dcamapi.lasterr())}"
                )
            else:
                raise CameraError("Dcam api init gave true, which is not valid.")
        elif isinstance(dcam_init_result, tuple):
            dcam_init, cam_count = dcam_init_result
        else:
            raise CameraError("Dcam api init result is invalid.")

        if cam_count < 1:
            raise ValueError("No Dcam api cameras available - is the hardware plugged in?")

        if sn is not None:
            for idx in range(cam_count):
                cam = Dcam(idx)
                if sn == cam.dev_getstring(DCAM_IDSTR.CAMERAID):
                    index = idx
                    break

            if index is None:
                raise ValueError(f"Could not find camera with serial number: '{sn}'")

        if index is None:
            raise ValueError("Index is none after all checks, something is wrong!")

        camera = Dcam(index)

        if not camera.dev_open(index):
            raise CameraError(f"Failed to open camera with index={index}")

        supported_resolutions = {
            # C15440-20UP (ORCA-Fusion BT)
            (1, 1): (2304, 2304),
            (2, 2): (1152, 1152),
            (4, 4): (576, 576),
        }

        capabilities = HamamatsuCapabilities(binning_to_resolution=supported_resolutions)

        return camera, capabilities

    def __init__(
        self,
        camera_config: CameraConfig,
        hw_trigger_fn: Optional[Callable[[Optional[float]], bool]],
        hw_set_strobe_delay_ms_fn: Optional[Callable[[float], bool]],
    ):
        super().__init__(camera_config, hw_trigger_fn, hw_set_strobe_delay_ms_fn)

        self._read_thread_lock = threading.Lock()
        self._read_thread: Optional[threading.Thread] = None
        self._read_thread_keep_running = threading.Event()
        self._read_thread_keep_running.clear()
        self._read_thread_wait_period_s = 1.0
        self._read_thread_running = threading.Event()
        self._read_thread_running.clear()

        # Held across every capture transition (stop_streaming, start_streaming) and by the read thread
        # from its DCAM read through frame publication, so a read never straddles two captures.
        # stop_streaming() leaves the read thread running, so nothing else keeps them apart.
        # Taken after _trigger_lock, before _frame_lock.
        self._capture_lock = threading.Lock()
        self._frame_lock = threading.Lock()
        self._current_frame: Optional[CameraFrame] = None
        # How the read thread takes frames out of DCAM's ring; decided per capture in start_streaming().
        self._read_every_frame = False  # triggered modes: every frame, in order. Live: the newest only.
        self._ring_frames = _LIVE_RING_FRAMES
        self._frames_read = 0  # frames of THIS capture already delivered (DCAM counts from 0 at cap_start)
        # How far the camera's capture count (its framestamp) runs ahead of DCAM's transfer count: every
        # frame the camera captured that never reached host memory (a USB stall). See _read_frames().
        self._link_lost = 0
        # frame_id = this base + the stamp DCAM counts from 0 at every cap_start (see _read_newest_frame).
        self._frame_id_base = 1
        # The number (from 0 at cap_start) of the newest frame delivered in this capture; see _frame_number().
        self._last_frame_number = -1
        self._last_trigger_timestamp = 0
        self._trigger_sent = threading.Event()

        camera, capabilities = HamamatsuCamera._open(index=0)

        self._camera: Dcam = camera
        self._capabilities: HamamatsuCapabilities = capabilities
        self._sensor_modes: Dict[str, int] = self._discover_sensor_modes()
        self._sensor_mode_names_by_value: Dict[int, str] = {v: name for (name, v) in self._sensor_modes.items()}
        self._is_streaming = threading.Event()

        # We store exposure time so we don't need to worry about backing out strobe time from the
        # time stored on the camera.
        #
        # We just set it to some sane value to start.
        self._exposure_time_ms: int = 20
        self.set_exposure_time(self._exposure_time_ms)

        if self._config.default_sensor_mode:
            self.set_sensor_mode(self._config.default_sensor_mode)

    def close(self):
        self._cleanup_read_thread()

    def _set_prop(self, dcam_prop, prop_value):
        if not self._camera.prop_setvalue(dcam_prop, prop_value):
            self._log.error(f"Failed to set property {dcam_prop}={prop_value}: {self._camera.lasterr()}")
            return False
        return True

    def _allocate_read_buffers(self, count=5):
        # NOTE: The caller must hold the camera lock!
        if not self._camera.buf_alloc(count):
            self._log.error(f"Failed to allocate {count} buffers.")
            return False
        return True

    def _read_newest_frame(self) -> Optional[CameraFrame]:
        """The newest frame in DCAM's ring buffer, numbered by the CAMERA's frame stamp.

        The read loop takes the newest frame only. When two frames land between two reads the older
        one is never seen, and when a frame lands between the wake-up and this read the newer one is
        read twice. A host-side counter numbers both 1, 2, 3... as if nothing had happened; ids that
        follow the camera's stamp leave a hole for the first and repeat for the second, so a consumer
        that needs every frame in order (a hardware-sequenced burst) can tell.

        DCAM counts the stamp from 0 at every cap_start; _frame_id_base keeps ids increasing across
        restarts, and the very first id is still 1.
        """
        # Finish publishing an in-flight frame before a restart can rebase its IDs.
        with self._capture_lock:
            # The dcam driver handles setting the correct width and height, so we can use the
            # np frame directly. buf_getframe(-1) is what buf_getlastframedata() calls; it also
            # returns the DCAMBUF_FRAME that carries the stamp.
            result = self._camera.buf_getframe(-1)
            self._trigger_sent.clear()

            if isinstance(result, bool):
                self._log.error("Frame read resulted in boolean, must be an error.")
                return None

            frame_info, raw_frame = result
            return self._new_frame(self._frame_number(int(frame_info.framestamp)), raw_frame)

    def _new_frame(self, number: int, raw_frame) -> CameraFrame:
        """number: the frame's number since cap_start (NOT the camera's framestamp, which is only 16 bits)."""
        # NOTE: The caller must hold _capture_lock.
        processed_frame = self._process_raw_frame(raw_frame)
        with self._frame_lock:
            camera_frame = CameraFrame(
                frame_id=self._frame_id_base + number,
                timestamp=time.time(),
                frame=processed_frame,
                frame_format=self.get_frame_format(),
                frame_pixel_format=self.get_pixel_format(),
            )
            self._current_frame = camera_frame
        return camera_frame

    def _read_frames(self) -> List[CameraFrame]:
        """The frames to deliver for one FRAMEREADY wake-up, oldest first.

        Live view: the newest frame only - dropping frames to stay current is the right policy there.

        Triggered modes: EVERY frame since the last wake-up, in order. Each of them was asked for, and
        the read thread can run late (bench 2026-09-21: up to 152 ms, 3.7 frame periods, while another
        thread was busy); taking only the newest one then drops frames that are still sitting in the
        ring. DCAM puts frame n (counted from 0 at cap_start, the same count as its framestamp) into
        slot n % depth, and cap_transferinfo() says how many frames exist and which slot is the newest.

        That ring layout could not be checked against hardware when this was written, so every slot is
        verified: a slot whose framestamp is not the frame number expected is reported, and this
        wake-up falls back to the newest frame. ids follow the camera's stamps either way, so a consumer
        that needs every frame sees a hole rather than a wrong frame.
        """
        if not self._read_every_frame:
            frame = self._read_newest_frame()
            return [] if frame is None else [frame]

        # Finish publishing in-flight frames before a restart can rebase their IDs.
        with self._capture_lock:
            info = self._camera.cap_transferinfo()
            if isinstance(info, bool):
                self._log.error(f"cap_transferinfo failed ({self._last_dcam_error_string()}); taking the newest frame.")
                return self._newest_frame_only(total=None)

            total = int(info.nFrameCount)
            first = self._frames_read
            if total <= first:
                return []  # nothing new: a wake-up for a frame the previous pass already took
            overwritten = total - first - self._ring_frames
            if overwritten > 0:
                self._log.error(
                    f"{overwritten} frame(s) were overwritten in the camera's {self._ring_frames}-frame ring before "
                    "they could be read: the read thread was held up for too long. Their ids will be missing."
                )
                first = total - self._ring_frames

            frames = []
            newest_slot = int(info.nNewestFrameIndex)
            for number in range(first, total):  # transfer indices: they say which slot, and what has been read
                slot = (newest_slot - (total - 1 - number)) % self._ring_frames
                result = self._camera.buf_getframe(slot)
                if isinstance(result, bool):
                    self._log.error(f"Reading ring slot {slot} failed ({self._last_dcam_error_string()}).")
                    return frames + self._newest_frame_only(total)
                frame_info, raw_frame = result
                stamp = int(frame_info.framestamp)
                # The camera's stamp counts CAPTURED frames (16 bits); DCAM's count counts TRANSFERRED ones.
                # They agree until a frame is lost between the camera and host memory, after which the stamp
                # runs ahead by that many for the rest of the capture. The offset can only grow.
                lost_now = self._link_loss_change(stamp, number)
                if lost_now < 0:
                    self._log.error(
                        f"Ring slot {slot} holds framestamp {stamp}, expected {(number + self._link_lost) & 0xFFFF} "
                        f"(frame {number}; newest slot {newest_slot}, {total} frames, ring of {self._ring_frames}): the "
                        "ring is not laid out as assumed, or the slot was overwritten while it was read. Taking the "
                        "newest frame."
                    )
                    return frames + self._newest_frame_only(total)
                if lost_now:
                    self._note_link_loss(lost_now, f"ring slot {slot}, frame {number}")
                frames.append(self._new_frame(self._frame_number(stamp), raw_frame))
            self._frames_read = total
            self._trigger_sent.clear()
            return frames

    def _link_loss_change(self, stamp: int, number: int) -> int:
        """By how many frames the camera's stamp has run further ahead of DCAM's transfer count since the
        last frame: 0 normally, > 0 when frames were lost at the link, < 0 only if the ring is not laid out
        as assumed (a frame cannot be transferred before it is captured). Signed, 16-bit wrap-safe."""
        offset = (stamp - number) & 0xFFFF
        return ((offset - self._link_lost + 0x8000) & 0xFFFF) - 0x8000

    def _note_link_loss(self, lost_now: int, where: str) -> None:
        self._link_lost = (self._link_lost + lost_now) & 0xFFFF
        self._log.error(
            f"{lost_now} frame(s) the camera captured never reached host memory (lost between the camera and the "
            f"host, e.g. a USB stall; noticed at {where}). Their ids will be missing."
        )

    def _newest_frame_only(self, total: Optional[int]) -> List[CameraFrame]:
        """The fall-back of _read_frames(): the newest frame, with the reader left IN STEP WITH DCAM.

        total is DCAM's transfer count when known. _frames_read must come from it and never from the
        camera's stamp: with frames lost at the link the two differ, and a reader set from the stamp saw
        "nothing new" for as many wake-ups (bench 2026-09-21: one frame in seven for the rest of the capture).
        """
        # NOTE: The caller must hold _capture_lock.
        result = self._camera.buf_getframe(-1)
        self._trigger_sent.clear()
        if isinstance(result, bool):
            self._log.error("Frame read resulted in boolean, must be an error.")
            return []
        frame_info, raw_frame = result
        stamp = int(frame_info.framestamp)
        if total is None:  # cap_transferinfo() failed on the way in; ask once more, now that a frame was read
            info = self._camera.cap_transferinfo()
            total = None if isinstance(info, bool) else int(info.nFrameCount)
        if total is not None:
            lost_now = self._link_loss_change(stamp, total - 1)
            if lost_now > 0:
                self._note_link_loss(lost_now, "the newest frame")
            elif lost_now < 0:
                self._link_lost = (stamp - (total - 1)) & 0xFFFF  # resynchronise on the newest frame
            self._frames_read = total
        # else: leave _frames_read; the next wake-up resynchronises through the overwritten-frames path.
        return [self._new_frame(self._frame_number(stamp), raw_frame)]

    def _frame_number(self, framestamp: int) -> int:
        """The frame's number since cap_start, from the camera's framestamp.

        The stamp is a 16-BIT counter on the ORCA-Fusion BT (bench 2026-09-21: 65535 -> 0 while DCAM's own
        32-bit frame count went on to 65537), so it cannot be used as the number directly: after 65,536
        frames in one capture - 45 minutes at 24 fps - ids would start over. Frames are read in order and
        never more than a ring's worth apart, so the number is the last one plus the stamp's advance
        modulo 2**16. An advance of 0 is the same frame read again, and keeps its number.
        """
        # NOTE: The caller must hold _frame_lock.
        self._last_frame_number += (framestamp - self._last_frame_number) & 0xFFFF
        return self._last_frame_number

    def _read_frames_when_available(self):
        self._log.info("Starting Hamamatsu read thread.")
        self._read_thread_running.set()
        while self._read_thread_keep_running.is_set():
            # We really, really, do not want this thread to die prematurely, so catch all exceptions and try
            # to continue.
            try:
                wait_time = int(round(self._read_thread_wait_period_s * 1000.0))
                frame_ready = self._camera.wait_event(DCAMWAIT_CAPEVENT.FRAMEREADY, wait_time)

                if frame_ready:
                    # Send the local copy of each frame to all the callbacks so we are sure they get it
                    for camera_frame in self._read_frames():
                        self._propogate_frame(camera_frame)

                # NOTE(imo): I'm not sure if self._camera.wait_event actually yields to the python
                # interpreter.
                time.sleep(0.001)

            except Exception as e:
                self._log.exception("Exception in read loop, ignoring and trying to continue.")
        self._read_thread_running.clear()

    @staticmethod
    def _last_dcam_error_string_direct(last_error: DCAMERR):

        reverse_error_map = {enum_entry.value: enum_name for (enum_name, enum_entry) in DCAMERR.__members__.items()}

        if last_error not in reverse_error_map:
            return f"{last_error}:{reverse_error_map[last_error]}"
        else:
            return f"{last_error}:UNKNOWN_ERROR"

    def _last_dcam_error_string(self):
        return HamamatsuCamera._last_dcam_error_string_direct(self._camera.lasterr())

    def set_exposure_time(self, exposure_time_ms: float):
        camera_exposure_time_s = exposure_time_ms / 1000.0
        if self.get_acquisition_mode() == CameraAcquisitionMode.HARDWARE_TRIGGER:
            strobe_time_ms = self.get_strobe_time()
            camera_exposure_time_s += strobe_time_ms / 1000.0
            if self._hw_set_strobe_delay_ms_fn:
                self._log.debug(f"Setting hw strobe time to {strobe_time_ms} [ms]")
                self._hw_set_strobe_delay_ms_fn(strobe_time_ms)

        if not self._set_prop(DCAM_IDPROP.EXPOSURETIME, camera_exposure_time_s):
            raise CameraError(f"Failed to set exposure time to {exposure_time_ms=} ({camera_exposure_time_s=} [s])")

        self._exposure_time_ms = exposure_time_ms
        self._trigger_sent.clear()
        return True

    def get_exposure_time(self) -> float:
        return self._exposure_time_ms

    def get_exposure_limits(self) -> Tuple[float, float]:
        exposure_attr = self._camera.prop_getattr(DCAM_IDPROP.EXPOSURETIME)
        return exposure_attr.valuemin * 1000.0, exposure_attr.valuemax * 1000.0  # in ms

    def get_strobe_time(self) -> float:
        resolution = self.get_resolution()
        line_interval_s = self._camera.prop_getvalue(DCAM_IDPROP.INTERNAL_LINEINTERVAL) * resolution[1]
        trigger_delay_s = self._camera.prop_getvalue(DCAM_IDPROP.TRIGGERDELAY)

        if isinstance(line_interval_s, bool) or isinstance(trigger_delay_s, bool):
            raise CameraError("Failed to get strobe delay properties from camera")

        return (line_interval_s + trigger_delay_s) * 1000.0

    # READOUTSPEED values for the ORCA-Fusion BT (C15440-20UP), slowest (best SNR)
    # first. DCAM's own value texts are just the numbers, so we name the modes
    # after Hamamatsu's documented scan modes.
    _READOUTSPEED_NAMES = {1: "Ultra Quiet", 2: "Standard", 3: "Fast"}

    def _discover_sensor_modes(self) -> Dict[str, int]:
        # Squid's "sensor mode" maps to DCAM's READOUTSPEED property, and is
        # unrelated to DCAM's own SENSORMODE property (area/subarray mode).
        modes = {}
        readout_speed_attr = self._camera.prop_getattr(DCAM_IDPROP.READOUTSPEED)
        if readout_speed_attr is False:
            self._log.info("READOUTSPEED is not supported on this model; sensor mode selection unavailable.")
            return modes

        # Clamp to >=1: a fractional valuestep in (0, 1) would int() down to 0 and
        # crash range(). max(1, ...) also covers the 0/unset case identically to
        # the old truthiness check, since int(0.0) == 0 -> max(1, 0) == 1.
        step = max(1, int(readout_speed_attr.valuestep))
        for value in range(int(readout_speed_attr.valuemin), int(readout_speed_attr.valuemax) + 1, step):
            modes[self._READOUTSPEED_NAMES.get(value, str(value))] = value
        return modes

    def get_available_sensor_modes(self) -> Sequence[str]:
        return list(self._sensor_modes.keys())

    def get_sensor_mode(self) -> Optional[str]:
        if not self._sensor_modes:
            return None
        value = self._camera.prop_getvalue(DCAM_IDPROP.READOUTSPEED)
        if value is False:
            raise CameraError("Failed to read READOUTSPEED from camera")
        return self._sensor_mode_names_by_value.get(int(value))

    def set_sensor_mode(self, mode: str):
        if not self._sensor_modes:
            raise NotImplementedError("Sensor mode selection is not supported by this camera model.")
        if mode not in self._sensor_modes:
            raise ValueError(f"Unknown sensor mode '{mode}'. Valid modes: {list(self._sensor_modes.keys())}")

        # Hold the trigger lock (reentrant with _pause_streaming's) across the whole
        # switch so racing triggers are dropped until the new readout speed AND the
        # recalculated exposure/strobe are in effect, not just until streaming restarts.
        with self._trigger_lock:
            with self._pause_streaming():
                if not self._set_prop(DCAM_IDPROP.READOUTSPEED, self._sensor_modes[mode]):
                    raise CameraError(f"Failed to set sensor mode to '{mode}'")

            # Force exposure + strobe delay recalculation, since readout speed changes the line interval.
            self.set_exposure_time(self._exposure_time_ms)

    def set_frame_format(self, frame_format: CameraFrameFormat):
        if frame_format != CameraFrameFormat.RAW:
            raise ValueError("Only the RAW frame format is supported by this camera.")
        return True

    def get_frame_format(self) -> CameraFrameFormat:
        return CameraFrameFormat.RAW

    _PIXEL_FORMAT_TO_DCAM_FORMAT = {
        CameraPixelFormat.MONO8: DCAM_PIXELTYPE.MONO8,
        CameraPixelFormat.MONO16: DCAM_PIXELTYPE.MONO16,
    }

    def set_pixel_format(self, pixel_format: CameraPixelFormat):
        # For historical, allow passing in strings as long as they are valid pixel
        # formats.
        if isinstance(pixel_format, str):
            try:
                pixel_format = CameraPixelFormat(pixel_format)
            except ValueError:
                raise ValueError(f"Unknown or unsupported pixel format={pixel_format}")
        if pixel_format not in self._PIXEL_FORMAT_TO_DCAM_FORMAT:
            raise ValueError(f"Pixel format {pixel_format} is not supported by this camera.")

        with self._pause_streaming():
            if not self._set_prop(DCAM_IDPROP.IMAGE_PIXELTYPE, self._PIXEL_FORMAT_TO_DCAM_FORMAT[pixel_format]):
                raise CameraError(f"Failed to set pixel format to {pixel_format}")

    def get_pixel_format(self) -> CameraPixelFormat:
        raw_dcam_pixel_format = self._camera.prop_getvalue(DCAM_IDPROP.IMAGE_PIXELTYPE)

        if isinstance(raw_dcam_pixel_format, bool):
            raise CameraError("Failed to get pixel format from camera.")

        dcam_pixel_format = int(raw_dcam_pixel_format)
        _dcam_to_pixel = {v: k for (k, v) in self._PIXEL_FORMAT_TO_DCAM_FORMAT.items()}

        if dcam_pixel_format not in _dcam_to_pixel:
            raise ValueError(f"Camera returned unknown pixel format code: {dcam_pixel_format}")

        return _dcam_to_pixel[dcam_pixel_format]

    def get_available_pixel_formats(self) -> Sequence[CameraPixelFormat]:
        return list(self._PIXEL_FORMAT_TO_DCAM_FORMAT.keys())

    def get_resolution(self) -> Tuple[int, int]:
        return self._capabilities.binning_to_resolution[self.get_binning()]

    def set_binning(self, binning_factor_x: int, binning_factor_y: int):
        # TODO: We only support 1x1 binning for now. More may be added later.
        if binning_factor_x != 1 or binning_factor_y != 1:
            raise ValueError("Binning has not been implemented for this camera yet.")

    def get_binning(self) -> Tuple[int, int]:
        return (1, 1)

    def get_binning_options(self) -> Sequence[Tuple[int, int]]:
        return [(1, 1)]

    def get_pixel_size_unbinned_um(self) -> float:
        return self.PIXEL_SIZE_UM

    def get_pixel_size_binned_um(self) -> float:
        return self.PIXEL_SIZE_UM * self.get_binning()[0]

    def set_analog_gain(self, analog_gain: float):
        raise NotImplementedError("Analog gain is not implemented for this camera.")

    def get_analog_gain(self) -> float:
        raise NotImplementedError("Analog gain is not implemented for this camera.")

    def get_gain_range(self) -> CameraGainRange:
        raise NotImplementedError("Analog gain is not implemented for this camera.")

    def _ensure_read_thread_running(self):
        with self._read_thread_lock:
            if self._read_thread is not None and self._read_thread_running.is_set():
                self._log.debug("Read thread exists and thread is marked as running.")
                return True

            elif self._read_thread is not None:
                self._log.warning("Read thread already exists, but not marked as running.  Still attempting start.")

            self._read_thread = threading.Thread(target=self._read_frames_when_available, daemon=True)
            self._read_thread_keep_running.set()
            self._read_thread.start()

    def start_streaming(self):
        self._ensure_read_thread_running()

        if self._is_streaming.is_set():
            self._log.debug("Already streaming, start_streaming is noop")
            return True

        with self._capture_lock:
            # Every mode change restarts the capture (_pause_streaming), so deciding here is deciding per mode.
            self._read_every_frame = self.get_acquisition_mode() != CameraAcquisitionMode.CONTINUOUS
            self._ring_frames = _TRIGGERED_RING_FRAMES if self._read_every_frame else _LIVE_RING_FRAMES
            self._frames_read = 0
            self._link_lost = 0
            if not self._allocate_read_buffers(self._ring_frames):
                self._log.error(f"Couldn't allocate read buffers for streaming: {self._last_dcam_error_string()}")
                return False
            with self._frame_lock:
                # DCAM's frame stamp starts over at 0; ids must not.
                self._frame_id_base = self._current_frame.frame_id + 1 if self._current_frame else 1
                self._last_frame_number = -1  # and the numbering of this capture starts over with it
            if not self._camera.cap_start():
                self._log.error(f"Failed to start streaming: {self._last_dcam_error_string()}")
                return False

        self._trigger_sent.clear()
        self._is_streaming.set()
        return True

    def _cleanup_read_thread(self):
        self._log.debug("Cleaning up read thread.")
        with self._read_thread_lock:
            if self._read_thread is None:
                self._log.warning("No read thread, already not running?")
                return True

            self._read_thread_keep_running.clear()
            self._read_thread.join(1.1 * self._read_thread_wait_period_s)

            success = not self._read_thread.is_alive()
            if not success:
                self._log.warning("Read thread refused to exit!")

            self._read_thread = None
            self._read_thread_running.clear()

    def stop_streaming(self):
        self._log.debug("Stopping Hamamatsu streaming.")
        # Clear the flag before tearing down capture so get_ready_for_trigger()
        # reports not-ready for the whole teardown, not just after it.
        self._is_streaming.clear()
        success = True
        with self._capture_lock:
            if not self._camera.cap_stop():
                self._log.error(f"Failed to stop camera streaming: {self._last_dcam_error_string()}")
                success = False

            if not self._camera.buf_release():
                self._log.error(f"Failed to release camera buffers: {self._last_dcam_error_string()}")
                success = False

        self._log.debug(f"Stopped with {success=}")
        self._trigger_sent.clear()
        return success

    def get_is_streaming(self):
        return self._is_streaming.is_set()

    def read_camera_frame(self) -> Optional[CameraFrame]:
        if not self.get_is_streaming():
            self._log.error("Cannot read camera frame when not streaming.")
            return None

        if not self._read_thread_running.is_set():
            self._log.error("Fatal camera error: read thread not running!")
            return None

        starting_id = self.get_frame_id()
        timeout_s = (1.04 * self.get_total_frame_time() + 1000) / 1000.0
        timeout_time_s = time.time() + timeout_s
        while self.get_frame_id() == starting_id:
            if time.time() > timeout_time_s:
                self._log.warning(
                    f"Timed out after waiting {timeout_s=}[s] for frame ({starting_id=}), total_frame_time={self.get_total_frame_time()}."
                )
                return None
            time.sleep(0.001)

        with self._frame_lock:
            return self._current_frame

    def get_frame_id(self) -> int:
        with self._frame_lock:
            return self._current_frame.frame_id if self._current_frame else -1

    def get_white_balance_gains(self) -> Tuple[float, float, float]:
        raise NotImplementedError("White Balance Gains not implemented for the Hamamatsu driver.")

    def set_white_balance_gains(self, red_gain: float, green_gain: float, blue_gain: float):
        raise NotImplementedError("White Balance Gains not implemented for the Hamamatsu driver.")

    def set_auto_white_balance_gains(self) -> Tuple[float, float, float]:
        raise NotImplementedError("White Balance Gains not implemented for the Hamamatsu driver.")

    def set_black_level(self, black_level: float):
        raise NotImplementedError("Black levels are not implemented for the Hamamatsu driver.")

    def get_black_level(self) -> float:
        raise NotImplementedError("Black levels are not implemented for the Hamamatsu driver.")

    def _set_acquisition_mode_imp(self, acquisition_mode: CameraAcquisitionMode):
        with self._pause_streaming():
            if acquisition_mode == CameraAcquisitionMode.SOFTWARE_TRIGGER:
                dcam_trigger_source = DCAMPROP.TRIGGERSOURCE.SOFTWARE
            elif acquisition_mode == CameraAcquisitionMode.CONTINUOUS:
                dcam_trigger_source = DCAMPROP.TRIGGERSOURCE.INTERNAL
            elif acquisition_mode == CameraAcquisitionMode.HARDWARE_TRIGGER:
                dcam_trigger_source = DCAMPROP.TRIGGERSOURCE.EXTERNAL
                if not self._set_prop(DCAM_IDPROP.TRIGGERPOLARITY, DCAMPROP.TRIGGERPOLARITY.POSITIVE):
                    self._log.error(f"Failed to set positive trigger polarity for hardware trigger.")
                    return False
            else:
                raise ValueError(f"Unhandled {acquisition_mode=}")

            if not self._set_prop(DCAM_IDPROP.TRIGGERSOURCE, dcam_trigger_source):
                self._log.error(f"Failed to set acquisition mode to {acquisition_mode=}")
                return False
            self.set_exposure_time(self._exposure_time_ms)
        return True

    def get_acquisition_mode(self) -> CameraAcquisitionMode:
        dcam_mode_raw = self._camera.prop_getvalue(DCAM_IDPROP.TRIGGERSOURCE)

        if isinstance(dcam_mode_raw, bool):
            raise CameraError("Failed to get camera trigger source prop.")

        dcam_mode = int(dcam_mode_raw)

        if dcam_mode == DCAMPROP.TRIGGERSOURCE.EXTERNAL:
            return CameraAcquisitionMode.HARDWARE_TRIGGER
        elif dcam_mode == DCAMPROP.TRIGGERSOURCE.SOFTWARE:
            return CameraAcquisitionMode.SOFTWARE_TRIGGER
        elif dcam_mode == DCAMPROP.TRIGGERSOURCE.INTERNAL:
            return CameraAcquisitionMode.CONTINUOUS
        else:
            raise ValueError(f"Unknown dcam trigger source mode {dcam_mode=}")

    def _send_trigger_imp(self, illumination_time: Optional[float] = None):
        if self.get_acquisition_mode() == CameraAcquisitionMode.HARDWARE_TRIGGER and not self._hw_trigger_fn:
            raise CameraError("In HARDWARE_TRIGGER mode, but no hw trigger function given.")

        if not self.get_is_streaming():
            raise CameraError(f"Camera is not streaming, cannot send trigger.")

        if not self.get_ready_for_trigger():
            raise CameraError(
                f"Requested trigger too early (last trigger was {time.time() - self._last_trigger_timestamp} [s] ago), refusing."
            )
        if self.get_acquisition_mode() == CameraAcquisitionMode.HARDWARE_TRIGGER:
            self._hw_trigger_fn(illumination_time)
        elif self.get_acquisition_mode() == CameraAcquisitionMode.SOFTWARE_TRIGGER:
            if not self._camera.cap_firetrigger():
                raise CameraError(f"Failed to send software trigger: {self._last_dcam_error_string()}")

            self._last_trigger_timestamp = time.time()
            self._trigger_sent.set()

    def get_ready_for_trigger(self) -> bool:
        # Not ready while streaming is stopped (e.g. inside _pause_streaming() during a
        # sensor mode / ROI / pixel format change) - callers like LiveController skip
        # and retry instead of triggering into a stopped stream.
        if not self.get_is_streaming():
            return False
        if time.time() - self._last_trigger_timestamp > 1.5 * ((self.get_total_frame_time() + 4) / 1000.0):
            self._trigger_sent.clear()
        return not self._trigger_sent.is_set()

    def set_region_of_interest(self, offset_x: int, offset_y: int, width: int, height: int):
        # Numbers are in unbinned pixels. Supports C15440-20UP (ORCA-Fusion BT) only.
        # Trigger lock held across the pause AND the strobe/exposure recalculation, as
        # in set_sensor_mode.
        with self._trigger_lock:
            with self._pause_streaming():
                roi_mode_on = self._camera.prop_setvalue(DCAM_IDPROP.SUBARRAYMODE, DCAMPROP.MODE.ON)

                def fail(msg):
                    """
                    This is a helper for turning off roi mode if any of the sets below fail.
                    """
                    self._camera.prop_setvalue(DCAM_IDPROP.SUBARRAYMODE, DCAMPROP.MODE.OFF)
                    raise ValueError(msg)

                if not roi_mode_on:
                    raise CameraError("Failed to turn on roi mode on camera, cannot set roi.")

                offset_x = control.utils.truncate_to_interval(offset_x, 4)
                if not self._camera.prop_setvalue(DCAM_IDPROP.SUBARRAYHPOS, int(offset_x)):
                    fail("Could not set roi x offset.")

                width = control.utils.truncate_to_interval(width, 4)
                if not self._camera.prop_setvalue(DCAM_IDPROP.SUBARRAYHSIZE, int(width)):
                    fail("Could not set roi width.")

                offset_y = control.utils.truncate_to_interval(offset_y, 4)
                if not self._camera.prop_setvalue(DCAM_IDPROP.SUBARRAYVPOS, int(offset_y)):
                    fail("Could not set roi y offset.")

                height = control.utils.truncate_to_interval(height, 4)
                if not self._camera.prop_setvalue(DCAM_IDPROP.SUBARRAYVSIZE, int(height)):
                    fail("Could not set roi height.")

            # Force exposure + strobe delay recalculation if needed
            self.set_exposure_time(self.get_exposure_time())

    def get_region_of_interest(self) -> Tuple[int, int, int, int]:
        return (
            int(self._camera.prop_getvalue(DCAM_IDPROP.SUBARRAYHPOS)),
            int(self._camera.prop_getvalue(DCAM_IDPROP.SUBARRAYVPOS)),
            int(self._camera.prop_getvalue(DCAM_IDPROP.SUBARRAYHSIZE)),
            int(self._camera.prop_getvalue(DCAM_IDPROP.SUBARRAYVSIZE)),
        )

    def set_temperature(self, temperature_deg_c: Optional[float]):
        # Commented out since setting temperature is not supported in Model C15440-20UP (ORCA-Fusion BT)
        # self._camera.prop_setvalue(DCAM_IDPROP.SENSORTEMPERATURETARGET, temperature_deg_c)
        raise NotImplementedError("Setting temperature is not supported for this camera.")

    def get_temperature(self) -> float:
        return self._camera.prop_getvalue(DCAM_IDPROP.SENSORTEMPERATURE)

    def set_temperature_reading_callback(self, func) -> Callable[[float], None]:
        raise NotImplementedError("Setting temperature reading callback is not supported for this camera.")
