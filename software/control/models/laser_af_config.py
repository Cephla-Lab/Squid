"""
Laser autofocus configuration models.

These models define per-objective laser autofocus settings, including
calibration data and detection parameters.
"""

import base64
from typing import List, Optional

import numpy as np
from pydantic import BaseModel, Field

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
    width: int = Field(1536, description="Width of crop region")
    height: int = Field(256, description="Height of crop region")

    # Calibration
    pixel_to_um: float = Field(1.0, description="Pixels to micrometers conversion factor")
    x_reference: Optional[float] = Field(None, description="X reference position")
    has_reference: bool = Field(False, description="Whether a reference image exists")
    calibration_timestamp: str = Field("", description="Timestamp of last calibration")
    pixel_to_um_calibration_distance: float = Field(6.0, description="Distance used for pixel-to-um calibration")

    # Detection parameters
    laser_af_range: float = Field(100.0, description="Autofocus search range in um")
    laser_af_averaging_n: int = Field(3, description="Number of measurements to average")
    spot_detection_mode: str = Field(
        SpotDetectionMode.DUAL_RIGHT.value,
        description="Spot detection mode (single, dual_left, dual_right)",
    )
    displacement_success_window_um: float = Field(1.0, description="Acceptable displacement window in um")

    # Spot detection
    spot_crop_size: int = Field(100, description="Size of spot crop region")
    correlation_threshold: float = Field(0.9, description="Correlation threshold")
    y_window: int = Field(96, description="Y window half-height for detection")
    x_window: int = Field(20, description="X window half-width for detection")
    min_peak_width: float = Field(10.0, description="Minimum peak width")
    min_peak_distance: float = Field(10.0, description="Minimum distance between peaks")
    min_peak_prominence: float = Field(0.25, description="Minimum peak prominence")
    spot_spacing: float = Field(100.0, description="Expected spot spacing")
    filter_sigma: Optional[float] = Field(None, description="Gaussian filter sigma (-1 to disable)")

    # Camera settings
    focus_camera_exposure_time_ms: float = Field(0.2, description="Focus camera exposure time in ms")
    focus_camera_analog_gain: float = Field(0.0, description="Focus camera analog gain")

    # Initialization
    initialize_crop_width: int = Field(1200, description="Initial crop width")
    initialize_crop_height: int = Field(800, description="Initial crop height")

    # Reference image (base64 encoded)
    reference_image: Optional[str] = Field(None, description="Base64-encoded reference image data")
    reference_image_shape: Optional[List[int]] = Field(None, description="Shape of reference image array")
    reference_image_dtype: Optional[str] = Field(None, description="Data type of reference image array")

    model_config = {"extra": "forbid"}

    def get_spot_detection_mode(self) -> SpotDetectionMode:
        """Get the SpotDetectionMode enum value."""
        return SpotDetectionMode(self.spot_detection_mode)

    def set_spot_detection_mode(self, mode: SpotDetectionMode) -> None:
        """Set the spot detection mode from enum."""
        self.spot_detection_mode = mode.value

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
