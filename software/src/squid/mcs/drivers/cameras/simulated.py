"""Simulated camera implementations for testing and development.

This module provides simulated cameras that generate synthetic images:
- SimulatedCameraBase: Common camera functionality (exposure, gain, ROI, streaming)
- SimulatedFocusCamera: Laser autofocus camera with Z-coupled spot movement
- SimulatedMainCamera: Main microscope camera with cell-like images
"""

import functools
import threading
import time
from typing import Optional, Tuple, Sequence, Callable

import numpy as np

import squid.core.logging
from squid.core.config import CameraConfig, CameraPixelFormat
from squid.core.abc import (
    AbstractCamera,
    CameraAcquisitionMode,
    CameraFrameFormat,
    CameraFrame,
    CameraGainRange,
    CameraError,
)
from squid.mcs.drivers.cameras.camera_utils import camera_registry


class SimulatedCameraBase(AbstractCamera):
    """Base class for simulated cameras with common functionality.

    Provides standard camera operations (exposure, gain, binning, ROI, streaming)
    that are shared between focus and main camera implementations.

    Subclasses must implement _create_frame() to generate appropriate images.
    """

    PIXEL_SIZE_UM = 3.76
    # Sensor dimensions - kept moderate to ensure fast frame creation for tests
    # Configs with larger crop dimensions will be clamped to these bounds
    FULL_SENSOR_WIDTH = 3088
    FULL_SENSOR_HEIGHT = 2064

    @staticmethod
    def debug_log(method):
        """Decorator for logging method calls."""
        import inspect

        @functools.wraps(method)
        def _logged_method(self, *args, **kwargs):
            kwargs_pairs = tuple(f"{k}={v}" for (k, v) in kwargs.items())
            args_str = tuple(str(a) for a in args)
            current_frame = inspect.currentframe()
            self._log.debug(
                f"{inspect.getouterframes(current_frame)[1][3]} -> {method.__name__}({','.join(args_str + kwargs_pairs)})"
            )
            return method(self, *args, **kwargs)

        return _logged_method

    class MissingAttribImpl:
        """Placeholder for missing camera attributes during migration."""
        name_to_val = {}

        def __init__(self, name):
            self._log = squid.core.logging.get_logger(f"MissingAttribImpl({name})")
            self._val = self.name_to_val.get(name, None)

        def __get__(self, instance, owner):
            self._log.debug("Get")
            return self._val

        def __set__(self, instance, value):
            self._log.debug(f"Set={value}")
            self._val = value

        def __call__(self, *args, **kwargs):
            kwarg_pairs = [f"{k}={v}" for (k, v) in kwargs.items()]
            args_str = [str(a) for a in args]
            self._log.debug(
                f"Called(*args, **kwargs) -> Called({','.join(args_str)}, {','.join(kwarg_pairs)}"
            )
            return self._val

    def __init__(self, config: CameraConfig, **kwargs):
        super().__init__(config, **kwargs)
        self._lock = threading.RLock()
        self._frame_id = 0
        self._current_raw_frame = None
        self._current_frame = None

        self._exposure_time_ms = None
        self.set_exposure_time(20)
        self._frame_format = CameraFrameFormat.RAW
        self._pixel_format = None
        self.set_pixel_format(self._config.default_pixel_format)
        self._binning = None
        self.set_binning(
            self._config.default_binning[0], self._config.default_binning[1]
        )
        self._analog_gain = None
        self.set_analog_gain(0)
        self._white_balance_gains = None
        self.set_white_balance_gains(1.0, 1.0, 1.0)
        self._black_level = None
        self.set_black_level(0)
        self._acquisition_mode = None
        self.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)
        # Use crop dimensions from config if provided, otherwise use full sensor
        # Clamp to sensor bounds to avoid unexpectedly large frames
        roi_w = self._config.crop_width if self._config.crop_width else self.FULL_SENSOR_WIDTH
        roi_h = self._config.crop_height if self._config.crop_height else self.FULL_SENSOR_HEIGHT
        roi_w = min(roi_w, self.FULL_SENSOR_WIDTH)
        roi_h = min(roi_h, self.FULL_SENSOR_HEIGHT)
        self._roi = (0, 0, roi_w, roi_h)
        self._temperature_setpoint = None
        self._continue_streaming = False
        self._streaming_thread: Optional[threading.Thread] = None
        self._last_trigger_timestamp = 0
        self._missing_methods = {}

    def __getattr__(self, item):
        self._log.warning(f"Creating placeholder missing method: {item}")
        return self._missing_methods.get(item, SimulatedCameraBase.MissingAttribImpl(item))

    @debug_log
    def set_exposure_time(self, exposure_time_ms: float):
        self._exposure_time_ms = exposure_time_ms

    @debug_log
    def get_exposure_time(self) -> float:
        return self._exposure_time_ms

    @debug_log
    def get_strobe_time(self):
        return 3

    @debug_log
    def get_exposure_limits(self) -> Tuple[float, float]:
        return 1, 1000

    @debug_log
    def set_frame_format(self, frame_format: CameraFrameFormat):
        self._frame_format = frame_format

    @debug_log
    def get_frame_format(self) -> CameraFrameFormat:
        return self._frame_format

    @debug_log
    def set_pixel_format(self, pixel_format: CameraPixelFormat):
        self._pixel_format = pixel_format

    @debug_log
    def get_pixel_format(self) -> CameraPixelFormat:
        return self._pixel_format

    @debug_log
    def get_available_pixel_formats(self) -> Sequence[CameraPixelFormat]:
        return [
            CameraPixelFormat.MONO8,
            CameraPixelFormat.MONO12,
            CameraPixelFormat.MONO16,
        ]

    @debug_log
    def get_binning(self) -> Tuple[int, int]:
        return self._binning

    @debug_log
    def set_binning(self, x_binning: int, y_binning: int):
        self._binning = (x_binning, y_binning)

    @debug_log
    def get_binning_options(self) -> Sequence[Tuple[int, int]]:
        return [(1, 1), (2, 2), (3, 3)]

    @debug_log
    def get_resolution(self) -> Tuple[int, int]:
        binning_x, binning_y = self._binning
        _, _, roi_w, roi_h = self._roi
        width = roi_w // binning_x
        height = roi_h // binning_y
        return (width, height)

    @debug_log
    def get_pixel_size_unbinned_um(self) -> float:
        return self.PIXEL_SIZE_UM

    @debug_log
    def get_pixel_size_binned_um(self) -> float:
        return self.PIXEL_SIZE_UM * self.get_binning()[0]

    def get_crop_size(self) -> Tuple[Optional[int], Optional[int]]:
        """Override to clamp crop size to actual sensor dimensions.

        The config may specify crop dimensions larger than the simulated sensor.
        We need to return the actual output image dimensions for correct FOV calculation.
        """
        # Get base class crop size (from config, with software_crop_ratio applied)
        crop_width, crop_height = super().get_crop_size()

        # Get actual sensor resolution after binning
        resolution_width, resolution_height = self.get_resolution()

        # If config crop exceeds sensor, use sensor dimensions with software_crop_ratio applied
        if crop_width is None or crop_width > resolution_width:
            crop_width = int(resolution_width * self._software_crop_width_ratio)
        if crop_height is None or crop_height > resolution_height:
            crop_height = int(resolution_height * self._software_crop_height_ratio)

        return crop_width, crop_height

    # NOTE: We intentionally do NOT override get_fov_size_mm/get_fov_width_mm/get_fov_height_mm.
    # The base class implementation uses get_crop_size() which correctly accounts for software
    # cropping applied in _process_raw_frame(). The FOV must match the actual output image
    # dimensions, not the raw sensor resolution.

    @debug_log
    def set_analog_gain(self, analog_gain: float):
        valid_range = self.get_gain_range()
        if analog_gain > valid_range.max_gain or analog_gain < valid_range.min_gain:
            raise ValueError("Gain outside valid range.")
        self._analog_gain = analog_gain

    @debug_log
    def get_analog_gain(self) -> float:
        return self._analog_gain

    @debug_log
    def get_gain_range(self) -> CameraGainRange:
        return CameraGainRange(min_gain=0.0, max_gain=100.0, gain_step=2.0)

    def _start_streaming_thread(self):
        def stream_fn():
            self._log.info("Starting streaming thread...")
            last_frame_time = time.time()
            while self._continue_streaming:
                time_since = time.time() - last_frame_time
                if (
                    (self._exposure_time_ms / 1000.0) - time_since <= 0
                    and self._acquisition_mode == CameraAcquisitionMode.CONTINUOUS
                ):
                    self._next_frame()
                    last_frame_time = time.time()
                time.sleep(0.001)
            self._log.info("Stopping streaming...")

        self._streaming_thread = threading.Thread(target=stream_fn, daemon=True)
        self._streaming_thread.start()

    @debug_log
    def start_streaming(self):
        self._log.info(f"start_streaming called, mode={self._acquisition_mode}")
        if self._streaming_thread:
            if self._streaming_thread.is_alive() and self._continue_streaming:
                self._log.info("Already streaming, not starting again.")
                return
            elif self._streaming_thread.is_alive() and not self._continue_streaming:
                self._log.info(
                    "Looks like streaming is shutting down, waiting before restarting."
                )
                timeout_time = time.time() + 1
                while self._streaming_thread.is_alive() and timeout_time < time.time():
                    time.sleep(0.001)
                if self._streaming_thread.is_alive():
                    raise CameraError(
                        "Cannot start streaming, camera is inconsistent state"
                    )

        self._continue_streaming = True
        self._start_streaming_thread()

    @debug_log
    def stop_streaming(self):
        self._continue_streaming = False
        if self._streaming_thread:
            self._streaming_thread.join()

    @debug_log
    def get_is_streaming(self):
        with self._lock:
            return self._streaming_thread and self._streaming_thread.is_alive()

    @debug_log
    def read_camera_frame(self) -> CameraFrame:
        with self._lock:
            return self._current_frame

    @debug_log
    def get_white_balance_gains(self) -> Tuple[float, float, float]:
        return self._white_balance_gains

    @debug_log
    def set_white_balance_gains(
        self, red_gain: float, green_gain: float, blue_gain: float
    ):
        self._white_balance_gains = (red_gain, green_gain, blue_gain)

    @debug_log
    def set_auto_white_balance_gains(self) -> Tuple[float, float, float]:
        self.set_white_balance_gains(1.0, 1.0, 1.0)
        return self.get_white_balance_gains()

    @debug_log
    def set_black_level(self, black_level: float):
        self._black_level = black_level

    @debug_log
    def get_black_level(self) -> float:
        return self._black_level

    @debug_log
    def _set_acquisition_mode_imp(self, acquisition_mode: CameraAcquisitionMode):
        old_mode = self._acquisition_mode
        self._acquisition_mode = acquisition_mode
        if old_mode != acquisition_mode:
            self._current_raw_frame = None
            self._frame_id = 0

    @debug_log
    def get_acquisition_mode(self) -> CameraAcquisitionMode:
        return self._acquisition_mode

    @debug_log
    def send_trigger(self, illumination_time: Optional[float] = None):
        self._log.debug(f"send_trigger called, mode={self._acquisition_mode}")
        if self._acquisition_mode == CameraAcquisitionMode.CONTINUOUS:
            self._log.warning(
                "Sending triggers in continuous acquisition mode is not allowed."
            )
            return
        self._last_trigger_timestamp = time.time()
        self._next_frame()

    def _get_pixel_format_params(self) -> Tuple[int, type]:
        """Get max value and dtype for current pixel format."""
        if self.get_pixel_format() == CameraPixelFormat.MONO8:
            return 255, np.uint8
        elif self.get_pixel_format() == CameraPixelFormat.MONO12:
            return 4095, np.uint16
        elif self.get_pixel_format() == CameraPixelFormat.MONO16:
            return 65535, np.uint16
        else:
            raise NotImplementedError(
                f"Simulated camera does not support pixel_format={self.get_pixel_format()}"
            )

    def _get_clamped_roi(self) -> Tuple[int, int, int, int]:
        """Get ROI clamped to sensor bounds."""
        roi_x, roi_y, roi_w, roi_h = self._roi
        roi_x = max(0, min(roi_x, self.FULL_SENSOR_WIDTH - 1))
        roi_y = max(0, min(roi_y, self.FULL_SENSOR_HEIGHT - 1))
        roi_w = min(roi_w, self.FULL_SENSOR_WIDTH - roi_x)
        roi_h = min(roi_h, self.FULL_SENSOR_HEIGHT - roi_y)
        return roi_x, roi_y, roi_w, roi_h

    def _create_frame(self, width: int, height: int, max_val: int, dtype) -> np.ndarray:
        """Create a frame image. Subclasses must implement this."""
        raise NotImplementedError("Subclasses must implement _create_frame()")

    @debug_log
    def _next_frame(self):
        with self._lock:
            binning_x, binning_y = self.get_binning()
            max_val, dtype = self._get_pixel_format_params()
            roi_x, roi_y, roi_w, roi_h = self._get_clamped_roi()

            # Create the frame (subclass implements this)
            frame = self._create_frame(roi_w, roi_h, max_val, dtype)

            # Apply binning if needed
            if binning_x > 1 or binning_y > 1:
                new_h = roi_h // binning_y
                new_w = roi_w // binning_x
                frame = frame[:new_h * binning_y, :new_w * binning_x]
                frame = frame.reshape(new_h, binning_y, new_w, binning_x)
                frame = frame.mean(axis=(1, 3)).astype(dtype)

            # Handle MONO12 bit shift
            if self.get_pixel_format() == CameraPixelFormat.MONO12:
                frame = frame << 4

            self._frame_id += 1

            current_frame = CameraFrame(
                frame_id=self._frame_id,
                timestamp=time.time(),
                frame=self._process_raw_frame(frame),
                frame_format=self.get_frame_format(),
                frame_pixel_format=self.get_pixel_format(),
            )
            self._current_frame = current_frame

        self._propogate_frame(current_frame)

    @debug_log
    def get_ready_for_trigger(self) -> bool:
        # get_exposure_time() returns milliseconds, convert to seconds for comparison
        exposure_time_s = self.get_exposure_time() / 1000.0
        return time.time() - self._last_trigger_timestamp > exposure_time_s

    @debug_log
    def set_region_of_interest(
        self, offset_x: int, offset_y: int, width: int, height: int
    ):
        self._roi = (offset_x, offset_y, width, height)
        self._current_raw_frame = None

    @debug_log
    def get_region_of_interest(self) -> Tuple[int, int, int, int]:
        return self._roi

    @debug_log
    def set_temperature(self, temperature_deg_c: Optional[float]):
        self._temperature_setpoint = temperature_deg_c

    @debug_log
    def get_temperature(self) -> float:
        return self._temperature_setpoint

    @debug_log
    def set_temperature_reading_callback(self, callback: Callable):
        self._temperature_reading_callback = callback

    def get_frame_id(self) -> int:
        with self._lock:
            return self._frame_id

    @debug_log
    def close(self):
        pass


@camera_registry.register("simulated_focus")
class SimulatedFocusCamera(SimulatedCameraBase):
    """Simulated focus camera for laser autofocus.

    Generates laser spot images that move based on Z position changes.
    The spot position is coupled to stage Z and piezo Z via event subscriptions.
    """

    def __init__(self, config: CameraConfig, **kwargs):
        super().__init__(config, **kwargs)

        # Z position tracking for spot simulation
        self._stage_z_um = 0.0
        self._piezo_z_um = 0.0
        self._have_received_stage_z = False
        self._have_received_piezo_z = False
        self._initialization_z_um = None
        self._pending_initialization_z_capture = False

        # Pixels per micron of Z movement (calibration factor)
        self._spot_pixels_per_um = 5.0

        # Event bus for position tracking
        self._event_bus = None
        self._piezo = None  # Legacy fallback - PiezoStage object
        self._piezo_service = None  # PiezoService for simulation mode

    def set_piezo_service(self, piezo_service) -> None:
        """Set the piezo service for position tracking (simulation mode)."""
        self._piezo_service = piezo_service

    def set_event_bus(self, event_bus) -> None:
        """Set the event bus for position tracking."""
        if self._event_bus is not None:
            return
        self._event_bus = event_bus
        if event_bus is not None:
            from squid.core.events import StagePositionChanged, PiezoPositionChanged
            event_bus.subscribe(StagePositionChanged, self._on_stage_position_changed)
            event_bus.subscribe(PiezoPositionChanged, self._on_piezo_position_changed)

    def set_piezo(self, piezo) -> None:
        """Set the piezo reference for spot position simulation (legacy)."""
        self._piezo = piezo

    def _on_stage_position_changed(self, event) -> None:
        """Handle stage position change events."""
        self._stage_z_um = event.z_mm * 1000.0
        self._have_received_stage_z = True
        self._try_delayed_initialization_capture()

    def _on_piezo_position_changed(self, event) -> None:
        """Handle piezo position change events."""
        self._piezo_z_um = event.position_um
        self._have_received_piezo_z = True
        self._try_delayed_initialization_capture()

    def _try_delayed_initialization_capture(self) -> None:
        """Capture initialization Z if we now have position data and ROI was set."""
        if not self._pending_initialization_z_capture:
            return
        if not self._have_received_piezo_z:
            return
        self._initialization_z_um = self._get_total_z_um()
        self._pending_initialization_z_capture = False

    def _get_total_z_um(self) -> float:
        """Get total Z position in microns (stage + piezo).

        Prefers direct piezo read for immediate accuracy, since event-based
        position tracking can be delayed by the EventBus queue.
        """
        piezo_z = self._piezo_z_um  # Default to event-based value

        # Try PiezoService first (simulation mode)
        if self._piezo_service is not None:
            try:
                # PiezoService tracks position internally, home is at 150
                piezo_z = self._piezo_service.get_position() - 150.0
            except Exception:
                pass
        # Then try direct PiezoStage read (hardware mode)
        elif self._piezo is not None:
            try:
                piezo_home = getattr(self._piezo, '_home_position_um', 150)
                piezo_z = self._piezo.position - piezo_home
            except Exception:
                pass

        return self._stage_z_um + piezo_z

    def _get_spot_position(self) -> Tuple[int, int]:
        """Calculate spot position based on Z position."""
        roi_x, roi_y, roi_w, roi_h = self._roi
        home_x = roi_x + roi_w // 2
        home_y = roi_y + roi_h // 2

        if self._initialization_z_um is None:
            return home_x, home_y

        total_z = self._get_total_z_um()
        delta_z = total_z - self._initialization_z_um
        spot_x = home_x + int(delta_z * self._spot_pixels_per_um)
        spot_y = home_y

        return spot_x, spot_y

    def set_region_of_interest(
        self, offset_x: int, offset_y: int, width: int, height: int
    ):
        self._roi = (offset_x, offset_y, width, height)
        self._current_raw_frame = None
        # Capture initialization Z when ROI is set
        if self._have_received_piezo_z:
            self._initialization_z_um = self._get_total_z_um()
            self._pending_initialization_z_capture = False
        else:
            self._pending_initialization_z_capture = True

    def _create_frame(self, width: int, height: int, max_val: int, dtype) -> np.ndarray:
        """Create a laser AF spot frame."""
        roi_x, roi_y, _, _ = self._roi
        spot_x, spot_y = self._get_spot_position()
        # Convert to ROI-relative coordinates
        spot_x_rel = spot_x - roi_x
        spot_y_rel = spot_y - roi_y

        # Dark background
        background_val = int(max_val * 0.02)
        frame = np.full((height, width), background_val, dtype=dtype)

        # Gaussian spot
        y, x = np.ogrid[:height, :width]
        sigma_x = 30  # Wider in x direction
        sigma_y = 15  # Narrower in y direction
        spot = np.exp(-((x - spot_x_rel) ** 2 / (2 * sigma_x ** 2) +
                        (y - spot_y_rel) ** 2 / (2 * sigma_y ** 2)))

        spot_intensity = int(max_val * 0.95)
        frame = frame + (spot * spot_intensity).astype(dtype)
        frame = np.clip(frame, 0, max_val).astype(dtype)

        return frame


@camera_registry.register("simulated_main")
class SimulatedMainCamera(SimulatedCameraBase):
    """Simulated main camera for microscope display.

    Generates a simulated field of cells that can be panned in X, Y, and Z.
    The cell field is deterministic, so the same stage position will always
    show the same view, enabling testing of tiling and z-stacking workflows.

    Uses a CellFieldRenderer to generate the cell images, which can be
    replaced with custom implementations for different simulation needs.
    """

    # Default pixel size factor (lens_factor from ObjectiveStore)
    # This gets multiplied by sensor pixel size and binning
    # Default assumes 20x objective with 180mm tube lens and 50mm system tube lens
    # lens_factor = 180 / 20 / 50 = 0.18
    DEFAULT_PIXEL_SIZE_FACTOR = 0.18

    def __init__(self, config: CameraConfig, **kwargs):
        super().__init__(config, **kwargs)

        # Stage position tracking
        self._stage_x_um = 0.0
        self._stage_y_um = 0.0
        self._stage_z_um = 0.0
        self._event_bus = None

        # Objective tracking for FOV simulation
        self._pixel_size_factor = self.DEFAULT_PIXEL_SIZE_FACTOR
        self._objective_name = "20x"

        # Cell field renderer (lazy initialization to avoid import issues)
        self._cell_renderer = None

        # Vignetting simulation (0.0 = none, 1.0 = strong)
        # Makes tile boundaries visible in mosaics by darkening edges
        self._vignette_strength = 0.8  # Default: strong vignetting for visible tile boundaries
        self._vignette_mask = None  # Cached vignette mask
        self._vignette_logged = False  # Log once when first applied

    def _get_cell_renderer(self):
        """Get or create the cell renderer (lazy initialization)."""
        if self._cell_renderer is None:
            from squid.mcs.drivers.cameras.cell_renderer import SimpleCellFieldRenderer
            self._cell_renderer = SimpleCellFieldRenderer()
        return self._cell_renderer

    def set_event_bus(self, event_bus) -> None:
        """Set the event bus for stage position and objective tracking."""
        if self._event_bus is not None:
            return
        self._event_bus = event_bus
        if event_bus is not None:
            from squid.core.events import StagePositionChanged, ObjectiveChanged
            event_bus.subscribe(StagePositionChanged, self._on_stage_position_changed)
            event_bus.subscribe(ObjectiveChanged, self._on_objective_changed)

    def set_cell_renderer(self, renderer) -> None:
        """Set a custom cell field renderer.

        Args:
            renderer: Object implementing the CellFieldRenderer protocol
        """
        self._cell_renderer = renderer

    def _on_stage_position_changed(self, event) -> None:
        """Handle stage position change events."""
        self._stage_x_um = event.x_mm * 1000.0
        self._stage_y_um = event.y_mm * 1000.0
        self._stage_z_um = event.z_mm * 1000.0

    def _on_objective_changed(self, event) -> None:
        """Handle objective change events."""
        if event.pixel_size_um is not None:
            # pixel_size_um field contains the pixel_size_factor from ObjectiveStore
            self._pixel_size_factor = event.pixel_size_um
        if event.objective_name is not None:
            self._objective_name = event.objective_name

        # Calculate actual pixel size for logging
        pixel_size = self.PIXEL_SIZE_UM * self.get_binning()[0] * self._pixel_size_factor
        self._log.info(
            f"Objective changed to {self._objective_name}: "
            f"pixel_size_factor={self._pixel_size_factor:.4f}, "
            f"effective_pixel_size={pixel_size:.3f}um"
        )

    def set_vignette_strength(self, strength: float) -> None:
        """Set the vignetting strength for tile boundary visualization.

        Args:
            strength: 0.0 = no vignetting, 1.0 = strong vignetting (edges at ~30% brightness)
        """
        self._vignette_strength = max(0.0, min(1.0, strength))
        self._vignette_mask = None  # Clear cache to regenerate
        self._log.info(f"Vignette strength set to {self._vignette_strength:.2f}")

    def _apply_vignetting(self, frame: np.ndarray, max_val: int, dtype) -> np.ndarray:
        """Apply vignetting effect with visible border to show tile boundaries.

        Creates a dark border around the frame edges to make tile boundaries
        clearly visible in mosaics, even with autolevel enabled.
        """
        height, width = frame.shape

        # Check if we need to regenerate the vignette mask
        if self._vignette_mask is None or self._vignette_mask.shape != (height, width):
            # Create a mask that's 1.0 in center and drops sharply at edges
            # This creates a visible "frame" effect rather than gradual vignetting

            # Calculate distance from nearest edge (normalized 0-1)
            y_dist = np.minimum(np.arange(height), np.arange(height-1, -1, -1))[:, np.newaxis]
            x_dist = np.minimum(np.arange(width), np.arange(width-1, -1, -1))[np.newaxis, :]

            # Normalize to 0-1 (0 at edge, 1 at center)
            y_norm = y_dist / (height / 2)
            x_norm = x_dist / (width / 2)

            # Use minimum distance to any edge
            edge_dist = np.minimum(y_norm, x_norm)

            # Create sharp falloff near edges
            # Border width is ~10% of image on each side
            border_width = 0.15 * self._vignette_strength

            # Sharp transition: full brightness in center, dark at edges
            # Using sigmoid-like function for smooth but visible transition
            transition = np.clip((edge_dist - border_width * 0.5) / (border_width * 0.5), 0, 1)

            # Min brightness at edge (darker = more visible borders)
            min_brightness = 0.2  # Edges at 20% brightness
            self._vignette_mask = min_brightness + (1.0 - min_brightness) * transition

            if not self._vignette_logged:
                self._log.info(
                    f"Vignette mask created: shape={self._vignette_mask.shape}, "
                    f"strength={self._vignette_strength:.2f}, "
                    f"center={self._vignette_mask[height//2, width//2]:.3f}, "
                    f"corner={self._vignette_mask[0, 0]:.3f}, "
                    f"edge={self._vignette_mask[height//2, 0]:.3f}"
                )
                self._vignette_logged = True

        # Apply vignette mask
        vignetted = frame.astype(np.float32) * self._vignette_mask
        return np.clip(vignetted, 0, max_val).astype(dtype)

    def _create_frame(self, width: int, height: int, max_val: int, dtype) -> np.ndarray:
        """Create a microscope image with simulated cells."""
        # Calculate effective pixel size at sample plane
        # Use UNBINNED pixel size since _create_frame works with pre-binning dimensions.
        # The physical FOV = width * pixel_size_um must match what acquisition expects.
        # After binning in _next_frame(), pixel count shrinks but physical area stays same.
        pixel_size_um = self.PIXEL_SIZE_UM * self._pixel_size_factor

        # Calculate brightness scale based on exposure and gain
        # Reference: 100ms exposure, 0 gain = 1.0 scale
        exposure_scale = self._exposure_time_ms / 100.0
        # Gain is in dB-like units: each 20 units doubles brightness
        gain_scale = 2.0 ** (self._analog_gain / 20.0)
        brightness_scale = exposure_scale * gain_scale
        # Clamp to reasonable range
        brightness_scale = max(0.1, min(brightness_scale, 10.0))

        # Log first few frames at INFO level, then DEBUG
        if self._frame_id < 5:
            fov_width_um = width * pixel_size_um
            fov_height_um = height * pixel_size_um
            self._log.info(
                f"Creating frame: {width}x{height}px, FOV={fov_width_um/1000:.2f}x{fov_height_um/1000:.2f}mm, "
                f"stage=({self._stage_x_um:.1f}, {self._stage_y_um:.1f}, {self._stage_z_um:.1f})um, "
                f"pixel_size={pixel_size_um:.3f}um"
            )
        else:
            self._log.debug(
                f"Creating frame: stage=({self._stage_x_um:.1f}, {self._stage_y_um:.1f})um"
            )

        # Create background with noise (changes each frame like real camera)
        # Scale background with brightness, but cap to avoid washing out image
        base_background = 0.05 * min(brightness_scale, 2.0)
        base_noise = 0.02 * min(brightness_scale, 2.0)
        background_val = int(max_val * base_background)
        noise_range = int(max_val * base_noise)
        frame = np.random.randint(
            max(0, background_val - noise_range),
            min(max_val, background_val + noise_range + 1),
            size=(height, width),
            dtype=dtype
        )

        # Render cells at current stage position
        try:
            renderer = self._get_cell_renderer()
            frame = renderer.render_frame(
                frame=frame,
                stage_x_um=self._stage_x_um,
                stage_y_um=self._stage_y_um,
                stage_z_um=self._stage_z_um,
                pixel_size_um=pixel_size_um,
                max_val=max_val,
                brightness_scale=brightness_scale,
            )
        except Exception as e:
            self._log.error(f"Error rendering cells: {e}", exc_info=True)
            # Fall back to simple random spots if cell renderer fails
            y, x = np.ogrid[:height, :width]
            for _ in range(10):
                spot_y = np.random.randint(height // 4, 3 * height // 4)
                spot_x = np.random.randint(width // 4, 3 * width // 4)
                sigma = 30
                intensity = 0.5 * max_val
                spot = np.exp(-((x - spot_x) ** 2 + (y - spot_y) ** 2) / (2 * sigma ** 2))
                frame = frame + (spot * intensity).astype(dtype)
            frame = np.clip(frame, 0, max_val).astype(dtype)

        # Apply vignetting (radial falloff from center)
        # This makes tile boundaries visible when tiles overlap
        if self._vignette_strength > 0:
            # Log first application to confirm code is running
            if not getattr(self, '_vignette_logged', False):
                self._log.info(f"Applying vignetting with strength={self._vignette_strength}")
            frame = self._apply_vignetting(frame, max_val, dtype)

        return frame
