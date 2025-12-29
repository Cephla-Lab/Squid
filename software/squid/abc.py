import dataclasses
from contextlib import contextmanager
from abc import ABC, abstractmethod
from typing import Callable, Optional, Tuple, Sequence, List, Dict
import abc
import enum
import time

import pydantic
import numpy as np
from dataclasses import dataclass

import squid.logging
from squid.config import AxisConfig, StageConfig, CameraConfig, CameraPixelFormat
from squid.exceptions import SquidTimeout
import control.utils


@dataclass
class FilterWheelInfo:
    """Information about a single filter wheel."""

    index: int
    number_of_slots: int
    slot_names: List[str]


class FilterControllerError(Exception):
    """Custom exception for FilterController errors."""

    pass


class AbstractFilterWheelController(ABC):
    """Abstract base class defining the interface for different filter wheels controllers. A filter wheel controller may control one ormultiple filter wheels."""

    @abstractmethod
    def __init__(self):
        """Initialize the filter wheel and establish communication."""
        pass

    @abstractmethod
    def initialize(self, filter_wheel_indices: List[int]):
        """
        Initialize the settings of the filter wheel.

        Args:
            filter_wheel_indices: List of filter wheel indices to initialize.
        """
        pass

    @property
    @abstractmethod
    def available_filter_wheels(self) -> List[int]:
        """List of available filter wheel indices (e.g., [1, 2, 3, 4] for Optospin).

        Returns:
            List of integer indices for available wheels.
        """
        pass

    @abstractmethod
    def get_filter_wheel_info(self, index: int) -> FilterWheelInfo:
        """Get information about a specific filter wheel.

        Args:
            index: Filter wheel index.

        Returns:
            FilterWheelInfo containing slots and names.
        """
        pass

    @abstractmethod
    def home(self, index: int = None):
        """Home the filter wheel with the given index. If index is None, home all filter wheels."""
        pass

    @abstractmethod
    def set_filter_wheel_position(self, positions: Dict[int, int]):
        """
        Set the filter wheels to the given positions.

        Args:
            positions: A dictionary of filter wheel index to target position. For example, {1: 1, 2: 2} means set the first filter wheel to position 1 and the second filter wheel to position 2.
        """
        pass

    @abstractmethod
    def get_filter_wheel_position(self) -> Dict[int, int]:
        """Get the current filter wheel positions.

        Returns:
            A dictionary of filter wheel index to current position.
        """
        pass

    @abstractmethod
    def set_delay_offset_ms(self, delay_offset_ms: float):
        """Set the delay offset for the filter wheel."""
        pass

    @abstractmethod
    def get_delay_offset_ms(self) -> Optional[float]:
        """Get the delay offset for the filter wheel."""
        pass

    @abstractmethod
    def set_delay_ms(self, delay_ms: float):
        """Set the delay for the filter wheel."""
        pass

    @abstractmethod
    def get_delay_ms(self) -> Optional[float]:
        """Get the delay for the filter wheel."""
        pass

    @abstractmethod
    def close(self):
        """Close the filter wheel connection."""
        pass


class LightSource(ABC):
    """Abstract base class defining the interface for different light sources."""

    @abstractmethod
    def __init__(self):
        """Initialize the light source and establish communication."""
        pass

    @abstractmethod
    def initialize(self):
        """
        Initialize the connection and settings for the light source.
        Returns True if successful, False otherwise.
        """
        pass

    @abstractmethod
    def set_intensity_control_mode(self, mode):
        """
        Set intensity control mode.

        Args:
            mode: IntensityControlMode(Enum)
        """
        pass

    @abstractmethod
    def get_intensity_control_mode(self):
        """
        Get current intensity control mode.

        Returns:
            IntensityControlMode(Enum)
        """
        pass

    @abstractmethod
    def set_shutter_control_mode(self, mode):
        """
        Set shutter control mode.

        Args:
            mode: ShutterControlMode(Enum)
        """
        pass

    @abstractmethod
    def get_shutter_control_mode(self):
        """
        Get current shutter control mode.

        Returns:
            ShutterControlMode(Enum)
        """
        pass

    @abstractmethod
    def set_shutter_state(self, channel, state):
        """
        Turn a specific channel on or off.

        Args:
            channel: Channel ID
            state: True to turn on, False to turn off
        """
        pass

    @abstractmethod
    def get_shutter_state(self, channel):
        """
        Get the current shutter state of a specific channel.

        Args:
            channel: Channel ID

        Returns:
            bool: True if channel is on, False if off
        """
        pass

    @abstractmethod
    def set_intensity(self, channel, intensity):
        """
        Set the intensity for a specific channel.

        Args:
            channel: Channel ID
            intensity: Intensity value (0-100)
        """
        pass

    @abstractmethod
    def get_intensity(self, channel) -> float:
        """
        Get the current intensity of a specific channel.

        Args:
            channel: Channel ID

        Returns:
            float: Current intensity value
        """
        pass

    @abstractmethod
    def shut_down(self):
        """Safely shut down the light source."""
        pass


class Pos(pydantic.BaseModel):
    x_mm: float
    y_mm: float
    z_mm: float
    # NOTE/TODO(imo): If essentially none of our stages have a theta, this is probably fine.  But If it's a mix we probably want a better way of handling the "maybe has theta" case.
    theta_rad: Optional[float]


class StageStage(pydantic.BaseModel):
    busy: bool


class AbstractStage(metaclass=abc.ABCMeta):
    def __init__(self, stage_config: StageConfig):
        self._config = stage_config
        self._log = squid.logging.get_logger(self.__class__.__name__)

    @abc.abstractmethod
    def move_x(self, rel_mm: float, blocking: bool = True):
        pass

    @abc.abstractmethod
    def move_y(self, rel_mm: float, blocking: bool = True):
        pass

    @abc.abstractmethod
    def move_z(self, rel_mm: float, blocking: bool = True):
        pass

    @abc.abstractmethod
    def move_x_to(self, abs_mm: float, blocking: bool = True):
        pass

    @abc.abstractmethod
    def move_y_to(self, abs_mm: float, blocking: bool = True):
        pass

    @abc.abstractmethod
    def move_z_to(self, abs_mm: float, blocking: bool = True):
        pass

    # TODO(imo): We need a stop or halt or something along these lines
    # @abc.abstractmethod
    # def stop(self, blocking: bool=True):
    #     pass

    @abc.abstractmethod
    def get_pos(self) -> Pos:
        pass

    @abc.abstractmethod
    def get_state(self) -> StageStage:
        pass

    @abc.abstractmethod
    def home(self, x: bool, y: bool, z: bool, theta: bool, blocking: bool = True):
        pass

    @abc.abstractmethod
    def zero(self, x: bool, y: bool, z: bool, theta: bool, blocking: bool = True):
        pass

    @abc.abstractmethod
    def set_limits(
        self,
        x_pos_mm: Optional[float] = None,
        x_neg_mm: Optional[float] = None,
        y_pos_mm: Optional[float] = None,
        y_neg_mm: Optional[float] = None,
        z_pos_mm: Optional[float] = None,
        z_neg_mm: Optional[float] = None,
        theta_pos_rad: Optional[float] = None,
        theta_neg_rad: Optional[float] = None,
    ):
        pass

    def get_config(self) -> StageConfig:
        return self._config

    def wait_for_idle(self, timeout_s):
        start_time = time.time()
        while time.time() < start_time + timeout_s:
            if not self.get_state().busy:
                return
            # Sleep some small amount of time so we can yield to other threads if needed
            # while waiting.
            time.sleep(0.001)

        error_message = f"Timed out waiting after {timeout_s:0.3f} [s]"
        self._log.error(error_message)

        raise SquidTimeout(error_message)


class CameraAcquisitionMode(enum.Enum):
    SOFTWARE_TRIGGER = "Software Trigger"
    HARDWARE_TRIGGER = "Hardware Trigger"
    CONTINUOUS = "Continuous Acquisition"


class CameraFrameFormat(enum.Enum):
    """
    This is all known camera frame formats in the Cephla world, but not all cameras will
    support all of these.
    """

    RAW = "RAW"
    RGB = "RGB"


class CameraGainRange(pydantic.BaseModel):
    min_gain: float
    max_gain: float
    gain_step: float


# NOTE(imo): Dataclass because pydantic does not like the np.array since there's no reasonable default
# we can provide it.
@dataclass
class CameraFrame:
    frame_id: int
    timestamp: float
    frame: np.array
    frame_format: CameraFrameFormat
    frame_pixel_format: CameraPixelFormat

    def is_color(self):
        return CameraPixelFormat.is_color_format(self.frame_pixel_format)


class CameraError(RuntimeError):
    pass


class AbstractCamera(metaclass=abc.ABCMeta):
    @staticmethod
    def calculate_new_roi_for_binning(old_binning, old_roi, new_binning) -> Tuple[int, int, int, int]:
        """
        When changing binning, we may want the roi to change such that the FOV is the same as before the binning
        factors change. This calculates the new roi to keep the FOV the same given we change to this new resolution.

        This only needs to be done for some of the cameras. In other cameras the roi might be updated automatically.

        The binning factors must be 2-tuples of (binning_factor_x, binning_factor_y).

        The roi must be a 4-tuple of (offset_x, offset_y, width, height)

        Returns a 4-tuple of (offset_x, offset_y, width, height)
        """
        if not isinstance(old_binning, tuple) or not isinstance(new_binning, tuple) or not isinstance(old_roi, tuple):
            raise ValueError("Need tuple args.")

        def rounded_int(num) -> int:
            return int(round(num))

        return (
            old_roi[0] * old_binning[0] / new_binning[0],
            old_roi[1] * old_binning[1] / new_binning[1],
            old_roi[2] * old_binning[0] / new_binning[0],
            old_roi[3] * old_binning[1] / new_binning[1],
        )

    def __init__(
        self,
        camera_config: CameraConfig,
        hw_trigger_fn: Optional[Callable[[Optional[float]], bool]],
        hw_set_strobe_delay_ms_fn: Optional[Callable[[float], bool]],
    ):
        """
        Init should open the camera, configure it as needed based on camera_config and reasonable
        defaults, and make it immediately available for use in grabbing frames.

        The hw_trigger_fn arguments are: Optional[float] = illumination time in ms (if None, do not control illumination)
        The hw_set_strobe_delay_ms_fn arguments are: float = hardware strobe delay in ms.

        If you plan on using the HARDWARE acquisition mode, you *must* provide the hw_trigger_fn and hw_set_strobe_delay_ms_fn.
        Not doing so will result in failure later on when trying to switch acquisition modes.
        """
        self._config = camera_config
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._hw_trigger_fn: Optional[Callable[[Optional[float]], bool]] = hw_trigger_fn
        self._hw_set_strobe_delay_ms_fn: Optional[Callable[[float], bool]] = hw_set_strobe_delay_ms_fn

        # Frame callbacks is a list of (id, callback) managed by add_frame_callback and remove_frame_callback.
        # Your frame receiving functions should call self._send_frame_to_callbacks(frame), and doesn't need
        # to do more than that.
        self._frame_callbacks: List[Tuple[int, Callable[[CameraFrame], None]]] = []
        self._frame_callbacks_enabled = True

        # Software crop is applied after hardware crop (setting ROI). The ratio is based on size after hardware crop.
        # Default is 1.0, which means no software crop.
        self._software_crop_width_ratio = 1.0
        self._software_crop_height_ratio = 1.0

    @contextmanager
    def _pause_streaming(self):
        was_streaming = self.get_is_streaming()
        try:
            if was_streaming:
                self.stop_streaming()
            yield
        finally:
            if was_streaming:
                self.start_streaming()

    def enable_callbacks(self, enabled: bool):
        """
        This enables or disables propagation of frames to all the registered callbacks.  This should be used
        sparingly since any read_frame() with enable_callbacks = False will be lost to all callbacks.  Valid
        use cases are things like during-acquisition auto focus (whereby we need to capture a bunch of frames
        that aren't a part of the acquisition).  This is inherently fragile, though, so all effort should be
        made to design a system that has enabled_callbacks(True) as the default!
        """
        self._log.debug(f"enable_callbacks: {enabled=}")
        self._frame_callbacks_enabled = enabled

    def get_callbacks_enabled(self) -> bool:
        return self._frame_callbacks_enabled

    def add_frame_callback(self, frame_callback: Callable[[CameraFrame], None]) -> int:
        """
        Adds a new callback that will be called with the receipt of every new frame.  This callback
        should not block for a long time because it will be called in the frame receiving hot path!

        This np.ndarray is shared with all callbacks, so you should make a copy if you need to modify it.

        Returns the callback ID that can be used to remove the callback later if needed.
        """
        try:
            next_id = max(t[0] for t in self._frame_callbacks) + 1
        except ValueError:
            next_id = 1

        self._frame_callbacks.append((next_id, frame_callback))

        return next_id

    def remove_frame_callback(self, callback_id):
        try:
            idx_to_remove = [t[0] for t in self._frame_callbacks].index(callback_id)
            self._log.debug(f"Removing callback with id={callback_id} at idx={idx_to_remove}.")
            del self._frame_callbacks[idx_to_remove]
        except ValueError:
            self._log.warning(f"No callback with id={callback_id}, cannot remove it.")

    def _propogate_frame(self, camera_frame: CameraFrame):
        """
        Implementations can call this to propogate a new frame to all registered callbacks.  You should
        have already called _process_raw_frame to generate this (aka: all cropping and rotating should be done).

        Best practice is to send the same frame here as you assign to your self._current_frame (if you have one).
        """
        if not self._frame_callbacks_enabled:
            return
        for _, cb in self._frame_callbacks:
            cb(camera_frame)

    @abc.abstractmethod
    def set_exposure_time(self, exposure_time_ms: float):
        """
        Sets the exposure time in ms.  This should also take care of setting the strobe delay (if needed).  If in
        HARDWARE acquisition mode, you're guaranteed to have a self._hw_set_strobe_delay_ms_fn to help with this.
        """
        pass

    @abc.abstractmethod
    def get_exposure_time(self) -> float:
        """
        Returns the current exposure time in milliseconds.
        """
        pass

    @abc.abstractmethod
    def get_exposure_limits(self) -> Tuple[float, float]:
        """
        Return the valid range of exposure times in inclusive milliseconds.
        """
        pass

    @abc.abstractmethod
    def get_strobe_time(self) -> float:
        """
        Given the current exposure time we are using, what is the strobe time such that
        get_strobe_time() + get_exposure_time() == total frame time.  In milliseconds.
        """
        pass

    def get_total_frame_time(self) -> float:
        """
        The total sensor time for a single frame.  This is strobe time + exposure time in ms.
        """
        return self.get_exposure_time() + self.get_strobe_time()

    @abc.abstractmethod
    def set_frame_format(self, frame_format: CameraFrameFormat):
        """
        If this camera supports the given frame format, set it and make sure that
        all subsequent frames are in this format.

        If not, throw a ValueError.
        """
        pass

    @abc.abstractmethod
    def get_frame_format(self) -> CameraFrameFormat:
        pass

    @abc.abstractmethod
    def set_pixel_format(self, pixel_format: squid.config.CameraPixelFormat):
        """
        If this camera supports the given pixel format, enable it and make sure that all
        subsequent captures use this pixel format.

        If not, throw a ValueError.
        """
        pass

    @abc.abstractmethod
    def get_pixel_format(self) -> squid.config.CameraPixelFormat:
        pass

    @abc.abstractmethod
    def get_available_pixel_formats(self) -> Sequence[squid.config.CameraPixelFormat]:
        """
        Returns the list of pixel formats supported by the camera.
        """
        pass

    @abc.abstractmethod
    def set_binning(self, binning_factor_x: int, binning_factor_y: int):
        """
        Set the binning factor of the camera. Usually we set hardware binning here, so calling
        this may change buffer size, readout speed, etc. Update these settings as needed.
        """
        pass

    @abc.abstractmethod
    def get_binning(self) -> Tuple[int, int]:
        """
        Return the (binning_factor_x, binning_factor_y) of the camera right now.
        """
        pass

    @abc.abstractmethod
    def get_binning_options(self) -> Sequence[Tuple[int, int]]:
        """
        Return the list of binning options supported by the camera.
        """
        pass

    @abc.abstractmethod
    def get_resolution(self) -> Tuple[int, int]:
        """
        Returns the maximum resolution of the camera under the current binning setting.
        """
        pass

    @abc.abstractmethod
    def get_pixel_size_unbinned_um(self) -> float:
        """
        Returns the pixel size without binning in microns.
        """
        pass

    @abc.abstractmethod
    def get_pixel_size_binned_um(self) -> float:
        """
        Returns the pixel size after binning in microns.
        """
        pass

    @abc.abstractmethod
    def set_analog_gain(self, analog_gain: float):
        """
        Set analog gain as an input multiple.  EG 1 = no gain, 100 = 100x gain.
        """
        pass

    @abc.abstractmethod
    def get_analog_gain(self) -> float:
        """
        Returns gain in the same units as set_analog_gain.
        """
        pass

    @abc.abstractmethod
    def get_gain_range(self) -> CameraGainRange:
        """
        Returns the gain range, and minimum gain step, for this camera.
        """
        pass

    @abc.abstractmethod
    def start_streaming(self):
        """
        This starts camera frame streaming.  Whether this results in frames immediately depends
        on the current triggering mode.  If frames require triggering, no frames will come until
        triggers are sent.  If the camera is in continuous mode, frames will start immediately.

        This should be a noop if the camera is already streaming.
        """
        pass

    @abc.abstractmethod
    def stop_streaming(self):
        """
        Stops camera frame streaming, which means frames will only come in with a call go get_frame
        """
        pass

    @abc.abstractmethod
    def get_is_streaming(self):
        pass

    def _process_raw_frame(self, raw_frame: np.array) -> np.array:
        """
        Takes a raw nd array from a camera, and processes it such that it can be used directly in a
        CameraFrame as the frame field.  This takes care of rotating, resizing, etc the raw frame such that
        it respects this camera's settings.

        Your camera's image callback should use this.
        """
        # Apply rotation and flip
        image = control.utils.rotate_and_flip_image(
            raw_frame, rotate_image_angle=self._config.rotate_image_angle, flip_image=self._config.flip
        )

        # Apply software crop
        # Crop size should be scaled wrt the binning factor as the crop_width and crop_height are defined wrt the unbinned image size.
        crop_width, crop_height = self.get_crop_size()
        image = control.utils.crop_image(image, crop_width, crop_height)

        return image

    def get_crop_size(self) -> Tuple[int, int]:
        """
        Returns the final crop size of the image (after software crop).
        """
        binning_x, binning_y = self.get_binning()
        crop_width = int(self._config.crop_width / binning_x) if self._config.crop_width else None
        crop_height = int(self._config.crop_height / binning_y) if self._config.crop_height else None
        crop_width = int(crop_width * self._software_crop_width_ratio) if crop_width is not None else None
        crop_height = int(crop_height * self._software_crop_height_ratio) if crop_height is not None else None
        return crop_width, crop_height

    def get_fov_size_mm(self) -> float:
        """
        Returns the size of the camera field of view in millimeters (after ROI and software crop).
        """
        return self.get_crop_size()[0] * self.get_pixel_size_binned_um() / 1000

    def set_software_crop_ratio(self, width_ratio: float, height_ratio: float):
        """
        Set the software crop ratio. The final image size will be the hardware crop size multiplied by the
        software crop ratio rounded to the nearest integer.
        """
        if not (0 < width_ratio <= 1):
            raise ValueError(f"width_ratio must be > 0 and <= 1, got {width_ratio}")
        if not (0 < height_ratio <= 1):
            raise ValueError(f"height_ratio must be > 0 and <= 1, got {height_ratio}")
        self._software_crop_width_ratio = width_ratio
        self._software_crop_height_ratio = height_ratio

    def read_frame(self) -> Optional[np.ndarray]:
        """
        If needed, send a trigger to request a frame.  Then block and wait until the next frame comes in,
        and return it.  The frame that comes back will be rotated/flipped/etc based on this cameras config,
        so the caller can assume all that is done for them.

        These frames will be sent to registered callbacks as well.

        NOTE(imo): We might change this to get_frame to be consistent with everything else here, but
        since cameras previously used read_frame this decreases line change noise.

        Might return None if getting a frame timed out, or another error occurred.
        """
        full_frame = self.read_camera_frame()

        # read_camera_frame will have already printed an error, so just pass on the none.
        return full_frame.frame if full_frame else None

    @abc.abstractmethod
    def read_camera_frame(self) -> Optional[CameraFrame]:
        """
        This calls read_frame, but also fills in all the information such that you get a CameraFrame.  The
        frame in the CameraFrame will have had _process_raw_frame called on it already.

        Might return None if getting a frame timed out, or another error occurred.
        """
        pass

    @abc.abstractmethod
    def get_frame_id(self) -> int:
        """
        Returns the frame id of the current frame.  This should increase by 1 with every frame received
        from the camera
        """
        pass

    @abc.abstractmethod
    def get_white_balance_gains(self) -> Tuple[float, float, float]:
        """
        Returns the (R, G, B) white balance gains
        """
        pass

    @abc.abstractmethod
    def set_white_balance_gains(self, red_gain: float, green_gain: float, blue_gain: float):
        """
        Set the (R, G, B) white balance gains.
        """
        pass

    @abc.abstractmethod
    def set_auto_white_balance_gains(self, on: bool):
        """
        Turn auto white balance on or off.
        """
        pass

    @abc.abstractmethod
    def set_black_level(self, black_level: float):
        """
        Sets the black level of captured images.
        """
        pass

    @abc.abstractmethod
    def get_black_level(self) -> float:
        """
        Gets the black level set on the camera.
        """
        pass

    def set_acquisition_mode(self, acquisition_mode: CameraAcquisitionMode):
        """
        Sets the acquisition mode.  If you are specifying hardware trigger, and an external
        system needs to send the trigger, you must specify a hw_trigger_fn.  This function must be callable in such
        a way that it immediately sends a hardware trigger, and only returns when the trigger has been sent.

        hw_trigger_fn and hw_set_strobe_delay_ms_fn to the __init__ must have been valid for the duration of this
        camera's acquisition mode being set to HARDWARE
        """
        if acquisition_mode is CameraAcquisitionMode.HARDWARE_TRIGGER:
            if not self._hw_trigger_fn:
                raise ValueError(
                    "Cannot set HARDWARE_TRIGGER camera acquisition mode without a hw_trigger_fn.  You must provide one when constructing the camera."
                )
            if not self._hw_set_strobe_delay_ms_fn:
                raise ValueError(
                    "Cannot set HARDWARE_TRIGGER camera acquisition mode without a hw_set_strobe_delay_ms_fn.  You must provide one when constructing the camera."
                )

        return self._set_acquisition_mode_imp(acquisition_mode=acquisition_mode)

    @abc.abstractmethod
    def _set_acquisition_mode_imp(self, acquisition_mode: CameraAcquisitionMode):
        """
        Your subclass must implement this such that it switches the camera to this acquisition mode.  The top level
        set_acquisition_mode handles storing the self._hw_trigger_fn for you so you are guaranteed to have a valid
        callable self._hw_trigger_fn if in hardware trigger mode.

        If things like setting a remote strobe, or other settings, are needed when you change the mode you must
        handle that here.
        """
        pass

    @abc.abstractmethod
    def get_acquisition_mode(self) -> CameraAcquisitionMode:
        """
        Returns the current acquisition mode.
        """
        pass

    @abc.abstractmethod
    def send_trigger(self, illumination_time: Optional[float] = None):
        """
        If in an acquisition mode that needs triggering, send a trigger.  If in HARDWARE_TRIGGER mode, you are
        guaranteed to have a self._hw_trigger_fn and should call that.  If in CONTINUOUS mode, this can be
        a no-op.

        The illumination_time argument can be used for HARDWARE_TRIGGER cases where the hardware trigger mechanism
        knows how to control illumination (and may take into account a strobe delay).  If not using a hardware
        trigger system that controls illumination, a non-None illumination_time is allowed (but will be ignored)

        When this returns, it does not mean it is safe to immediately send another trigger.
        """
        pass

    @abc.abstractmethod
    def get_ready_for_trigger(self) -> bool:
        """
        Returns true if the camera is ready for another trigger, false otherwise.  Calling
        send_trigger when this is False will result in an exception from send_trigger.
        """
        pass

    @abc.abstractmethod
    def set_region_of_interest(self, offset_x: int, offset_y: int, width: int, height: int):
        """
        Set the region of interest of the camera so that returned frames only contain this subset of the full sensor image.
        """
        pass

    @abc.abstractmethod
    def get_region_of_interest(self) -> Tuple[int, int, int, int]:
        """
        Returns the region of interest as a tuple of (x corner, y corner, width, height)
        """
        pass

    @abc.abstractmethod
    def set_temperature(self, temperature_deg_c: Optional[float]):
        """
        Set the desired temperature of the camera in degrees C.  If None is given as input, use
        a sane default for the camera.
        """
        pass

    @abc.abstractmethod
    def get_temperature(self) -> float:
        """
        Get the current temperature of the camera in deg C.
        """
        pass

    @abc.abstractmethod
    def set_temperature_reading_callback(self, callback: Callable):
        """
        Set the callback to be called when the temperature reading changes.
        """
        pass

    @abc.abstractmethod
    def close(self):
        """
        Close the camera.
        """
        pass
