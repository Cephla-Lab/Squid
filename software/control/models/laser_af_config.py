"""
Laser autofocus configuration models.

These models define per-objective laser autofocus settings, including
calibration data and detection parameters.
"""

import base64
from typing import List, Optional

import numpy as np
from pydantic import BaseModel, Field, model_validator

import control._def as _def
from control._def import SpotDetectionMode


class LaserAFConfig(BaseModel):
    """
    Laser autofocus configuration (per objective, YAML format).

    Stores calibration data, detection parameters, and reference images
    for the laser autofocus system.
    """

    version: int = Field(1, description="Configuration format version")

    # Crop region
    x_offset: float = Field(0, description="X offset for crop region")
    y_offset: float = Field(0, description="Y offset for crop region")
    width: int = Field(default_factory=lambda: _def.LASER_AF_CROP_WIDTH, description="Width of crop region")
    height: int = Field(default_factory=lambda: _def.LASER_AF_CROP_HEIGHT, description="Height of crop region")

    # Calibration
    pixel_to_um: float = Field(1.0, description="Pixels to micrometers conversion factor")
    x_reference: Optional[float] = Field(None, description="X reference position")
    has_reference: bool = Field(False, description="Whether a reference image exists")
    calibration_timestamp: str = Field("", description="Timestamp of last calibration")
    pixel_to_um_calibration_distance: float = Field(
        default_factory=lambda: _def.PIXEL_TO_UM_CALIBRATION_DISTANCE,
        description="Distance used for pixel-to-um calibration",
    )

    # Detection parameters
    laser_af_range: float = Field(
        default_factory=lambda: float(_def.LASER_AF_RANGE), description="Autofocus search range in um"
    )
    laser_af_averaging_n: int = Field(
        default_factory=lambda: _def.LASER_AF_AVERAGING_N, description="Number of measurements to average"
    )
    spot_detection_mode: SpotDetectionMode = Field(
        default_factory=lambda: SpotDetectionMode.parse(_def.LASER_AF_SPOT_DETECTION_MODE)[0],
        description="Spot detection mode",
    )
    spot_index: int = Field(
        default_factory=lambda: SpotDetectionMode.parse(_def.LASER_AF_SPOT_DETECTION_MODE)[1]
        or _def.LASER_AF_SPOT_INDEX,
        ge=1,
        description="For the nth_from_* modes, which spot counted from that side (1 = outermost)",
    )
    strip_spot_index: Optional[int] = Field(
        None,
        ge=1,
        description="Rank of the chosen reflection among the peaks inside the detection strip, counted from the "
        "same side; set by Initialize because the strip can crop neighbours away (None = spot_index)",
    )
    displacement_success_window_um: float = Field(
        default_factory=lambda: _def.DISPLACEMENT_SUCCESS_WINDOW_UM,
        description="Acceptable displacement window in um",
    )

    # Spot detection
    spot_crop_size: int = Field(default_factory=lambda: _def.SPOT_CROP_SIZE, description="Size of spot crop region")
    correlation_threshold: float = Field(
        default_factory=lambda: _def.CORRELATION_THRESHOLD, description="Correlation threshold"
    )
    y_window: int = Field(
        default_factory=lambda: _def.LASER_AF_Y_WINDOW, description="Y window half-height for detection"
    )
    x_window: int = Field(
        default_factory=lambda: _def.LASER_AF_X_WINDOW, description="X window half-width for detection"
    )
    min_peak_width: float = Field(
        default_factory=lambda: float(_def.LASER_AF_MIN_PEAK_WIDTH), description="Minimum peak width"
    )
    min_peak_distance: float = Field(
        default_factory=lambda: float(_def.LASER_AF_MIN_PEAK_DISTANCE), description="Minimum distance between peaks"
    )
    min_peak_prominence: float = Field(
        default_factory=lambda: _def.LASER_AF_MIN_PEAK_PROMINENCE, description="Minimum peak prominence"
    )
    spot_spacing: float = Field(
        default_factory=lambda: float(_def.LASER_AF_SPOT_SPACING), description="Expected spot spacing"
    )
    filter_sigma: Optional[float] = Field(
        default_factory=lambda: _def.LASER_AF_FILTER_SIGMA, description="Gaussian filter sigma (None to disable)"
    )

    # Camera settings
    focus_camera_exposure_time_ms: float = Field(
        default_factory=lambda: float(_def.FOCUS_CAMERA_EXPOSURE_TIME_MS),
        description="Focus camera exposure time in ms",
    )
    focus_camera_analog_gain: float = Field(
        default_factory=lambda: float(_def.FOCUS_CAMERA_ANALOG_GAIN), description="Focus camera analog gain"
    )

    # Initialization
    initialize_crop_width: int = Field(
        default_factory=lambda: _def.LASER_AF_INITIALIZE_CROP_WIDTH, description="Initial crop width"
    )
    initialize_crop_height: int = Field(
        default_factory=lambda: _def.LASER_AF_INITIALIZE_CROP_HEIGHT, description="Initial crop height"
    )

    # Reference image (base64 encoded)
    reference_image: Optional[str] = Field(None, description="Base64-encoded reference image data")
    reference_image_shape: Optional[List[int]] = Field(None, description="Shape of reference image array")
    reference_image_dtype: Optional[str] = Field(None, description="Data type of reference image array")

    model_config = {"extra": "forbid"}

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_spot_mode_names(cls, data):
        """Files saved before spot_index existed name the spot by mode alone (dual_left, multi_second_right, ...)."""
        if isinstance(data, dict) and isinstance(data.get("spot_detection_mode"), str):
            mode, implied_index = SpotDetectionMode.parse(data["spot_detection_mode"])
            data = {**data, "spot_detection_mode": mode}
            if implied_index is not None and "spot_index" not in data:
                data["spot_index"] = implied_index
        return data

    def spot_index_within_strip(self) -> int:
        """The rank strip measurements use: set by Initialize once the strip is placed, else the user's N."""
        return self.spot_index if self.strip_spot_index is None else self.strip_spot_index

    def get_spot_detection_mode(self) -> SpotDetectionMode:
        """Get the SpotDetectionMode enum value."""
        return self.spot_detection_mode

    def set_spot_detection_mode(self, mode: SpotDetectionMode) -> None:
        """Set the spot detection mode from enum."""
        self.spot_detection_mode = mode

    @property
    def reference_image_cropped(self) -> Optional[np.ndarray]:
        """Convert stored base64 data back to numpy array."""
        if self.reference_image is None:
            return None
        data = base64.b64decode(self.reference_image.encode("utf-8"))
        return np.frombuffer(data, dtype=np.dtype(self.reference_image_dtype)).reshape(self.reference_image_shape)

    def set_reference_image(self, image: Optional[np.ndarray]) -> None:
        """Convert numpy array to base64 encoded string or clear reference if None."""
        if image is None:
            self.reference_image = None
            self.reference_image_shape = None
            self.reference_image_dtype = None
            self.has_reference = False
            return
        self.reference_image = base64.b64encode(image.tobytes()).decode("utf-8")
        self.reference_image_shape = list(image.shape)
        self.reference_image_dtype = str(image.dtype)
        self.has_reference = True
