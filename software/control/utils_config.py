from pydantic import BaseModel, field_validator
from pydantic_xml import BaseXmlModel, element, attr
from typing import List, Optional
from pathlib import Path
import base64
import numpy as np

import control.utils_channel as utils_channel
from control._def import (
    FOCUS_CAMERA_EXPOSURE_TIME_MS,
    FOCUS_CAMERA_ANALOG_GAIN,
    LASER_AF_RANGE,
    LASER_AF_AVERAGING_N,
    LASER_AF_CROP_WIDTH,
    LASER_AF_CROP_HEIGHT,
    LASER_AF_SPOT_DETECTION_MODE,
    DISPLACEMENT_SUCCESS_WINDOW_UM,
    SPOT_CROP_SIZE,
    CORRELATION_THRESHOLD,
    PIXEL_TO_UM_CALIBRATION_DISTANCE,
    LASER_AF_Y_WINDOW,
    LASER_AF_X_WINDOW,
    LASER_AF_MIN_PEAK_WIDTH,
    LASER_AF_MIN_PEAK_DISTANCE,
    LASER_AF_MIN_PEAK_PROMINENCE,
    LASER_AF_SPOT_SPACING,
    LASER_AF_FILTER_SIGMA,
)
from control._def import SpotDetectionMode


class LaserAFConfig(BaseModel):
    """Pydantic model for laser autofocus configuration"""

    x_offset: float = 0
    y_offset: float = 0
    width: int = LASER_AF_CROP_WIDTH
    height: int = LASER_AF_CROP_HEIGHT
    pixel_to_um: float = 1
    has_reference: bool = False  # Track if reference has been set
    laser_af_averaging_n: int = LASER_AF_AVERAGING_N
    displacement_success_window_um: float = (
        DISPLACEMENT_SUCCESS_WINDOW_UM  # if the displacement is within this window, we consider the move successful
    )
    spot_crop_size: int = SPOT_CROP_SIZE  # Size of region to crop around spot for correlation
    correlation_threshold: float = CORRELATION_THRESHOLD  # Minimum correlation coefficient for valid alignment
    pixel_to_um_calibration_distance: float = (
        PIXEL_TO_UM_CALIBRATION_DISTANCE  # Distance moved in um during calibration
    )
    calibration_timestamp: str = ""  # Timestamp of calibration performed
    laser_af_range: float = LASER_AF_RANGE  # Maximum reasonable displacement in um
    focus_camera_exposure_time_ms: float = FOCUS_CAMERA_EXPOSURE_TIME_MS
    focus_camera_analog_gain: float = FOCUS_CAMERA_ANALOG_GAIN
    spot_detection_mode: SpotDetectionMode = SpotDetectionMode(LASER_AF_SPOT_DETECTION_MODE)
    y_window: int = LASER_AF_Y_WINDOW  # Half-height of y-axis crop
    x_window: int = LASER_AF_X_WINDOW  # Half-width of centroid window
    min_peak_width: float = LASER_AF_MIN_PEAK_WIDTH  # Minimum width of peaks
    min_peak_distance: float = LASER_AF_MIN_PEAK_DISTANCE  # Minimum distance between peaks
    min_peak_prominence: float = LASER_AF_MIN_PEAK_PROMINENCE  # Minimum peak prominence
    spot_spacing: float = LASER_AF_SPOT_SPACING  # Expected spacing between spots
    filter_sigma: Optional[int] = LASER_AF_FILTER_SIGMA  # Sigma for Gaussian filter
    x_reference: Optional[float] = 0  # Reference position in um
    reference_image: Optional[str] = None  # Stores base64 encoded reference image for cross-correlation check
    reference_image_shape: Optional[tuple] = None
    reference_image_dtype: Optional[str] = None
    initialize_crop_width: int = 1200  # Width of the center crop used for initialization
    initialize_crop_height: int = 800  # Height of the center crop used for initialization

    @property
    def reference_image_cropped(self) -> Optional[np.ndarray]:
        """Convert stored base64 data back to numpy array"""
        if self.reference_image is None:
            return None
        data = base64.b64decode(self.reference_image.encode("utf-8"))
        return np.frombuffer(data, dtype=np.dtype(self.reference_image_dtype)).reshape(self.reference_image_shape)

    @field_validator("spot_detection_mode", mode="before")
    @classmethod
    def validate_spot_detection_mode(cls, v):
        """Convert string to SpotDetectionMode enum if needed"""
        if isinstance(v, str):
            return SpotDetectionMode(v)
        return v

    def set_reference_image(self, image: Optional[np.ndarray]) -> None:
        """Convert numpy array to base64 encoded string or clear reference if None"""
        if image is None:
            self.reference_image = None
            self.reference_image_shape = None
            self.reference_image_dtype = None
            return
        self.reference_image = base64.b64encode(image.tobytes()).decode("utf-8")
        self.reference_image_shape = image.shape
        self.reference_image_dtype = str(image.dtype)

    def model_dump(self, serialize=False, **kwargs):
        """Ensure proper serialization of enums to strings"""
        data = super().model_dump(**kwargs)
        if serialize:
            if "spot_detection_mode" in data and isinstance(data["spot_detection_mode"], SpotDetectionMode):
                data["spot_detection_mode"] = data["spot_detection_mode"].value
        return data


class ChannelMode(BaseXmlModel, tag="mode"):
    """Channel configuration model"""

    id: str = attr(name="ID")
    name: str = attr(name="Name")
    exposure_time: float = attr(name="ExposureTime")
    analog_gain: float = attr(name="AnalogGain")
    illumination_source: int = attr(name="IlluminationSource")
    illumination_intensity: float = attr(name="IlluminationIntensity")
    camera_sn: Optional[str] = attr(name="CameraSN", default=None)
    z_offset: float = attr(name="ZOffset")
    emission_filter_position: int = attr(name="EmissionFilterPosition", default=1)
    selected: bool = attr(name="Selected", default=False)
    color: Optional[str] = None  # Not stored in XML but computed from name

    def __init__(self, **data):
        super().__init__(**data)
        self.color = utils_channel.get_channel_color(self.name)


class ChannelConfig(BaseXmlModel, tag="modes"):
    """Root configuration file model"""

    modes: List[ChannelMode] = element(tag="mode")


def get_attr_name(attr_name: str) -> str:
    """Get the attribute name for a given configuration attribute"""
    attr_map = {
        "ID": "id",
        "Name": "name",
        "ExposureTime": "exposure_time",
        "AnalogGain": "analog_gain",
        "IlluminationSource": "illumination_source",
        "IlluminationIntensity": "illumination_intensity",
        "CameraSN": "camera_sn",
        "ZOffset": "z_offset",
        "EmissionFilterPosition": "emission_filter_position",
        "Selected": "selected",
        "Color": "color",
    }
    return attr_map[attr_name]


def generate_default_configuration(filename: str) -> None:
    """Generate default configuration using Pydantic models"""
    default_modes = [
        ChannelMode(
            id="1",
            name="BF LED matrix full",
            exposure_time=12,
            analog_gain=0,
            illumination_source=0,
            illumination_intensity=5,
            camera_sn="",
            z_offset=0.0,
        ),
        ChannelMode(
            id="4",
            name="DF LED matrix",
            exposure_time=22,
            analog_gain=0,
            illumination_source=3,
            illumination_intensity=5,
            camera_sn="",
            z_offset=0.0,
        ),
        ChannelMode(
            id="5",
            name="Fluorescence 405 nm Ex",
            exposure_time=25,
            analog_gain=10,
            illumination_source=11,
            illumination_intensity=20,
            camera_sn="",
            z_offset=0.0,
        ),
        ChannelMode(
            id="6",
            name="Fluorescence 488 nm Ex",
            exposure_time=25,
            analog_gain=10,
            illumination_source=12,
            illumination_intensity=20,
            camera_sn="",
            z_offset=0.0,
        ),
        ChannelMode(
            id="7",
            name="Fluorescence 638 nm Ex",
            exposure_time=25,
            analog_gain=10,
            illumination_source=13,
            illumination_intensity=20,
            camera_sn="",
            z_offset=0.0,
        ),
        ChannelMode(
            id="8",
            name="Fluorescence 561 nm Ex",
            exposure_time=25,
            analog_gain=10,
            illumination_source=14,
            illumination_intensity=20,
            camera_sn="",
            z_offset=0.0,
        ),
        ChannelMode(
            id="12",
            name="Fluorescence 730 nm Ex",
            exposure_time=25,
            analog_gain=10,
            illumination_source=15,
            illumination_intensity=20,
            camera_sn="",
            z_offset=0.0,
        ),
        ChannelMode(
            id="9",
            name="BF LED matrix low NA",
            exposure_time=20,
            analog_gain=0,
            illumination_source=4,
            illumination_intensity=20,
            camera_sn="",
            z_offset=0.0,
        ),
        # Commented out modes for reference
        # ChannelMode(
        #     id="10",
        #     name="BF LED matrix left dot",
        #     exposure_time=20,
        #     analog_gain=0,
        #     illumination_source=5,
        #     illumination_intensity=20,
        #     camera_sn="",
        #     z_offset=0.0
        # ),
        # ChannelMode(
        #     id="11",
        #     name="BF LED matrix right dot",
        #     exposure_time=20,
        #     analog_gain=0,
        #     illumination_source=6,
        #     illumination_intensity=20,
        #     camera_sn="",
        #     z_offset=0.0
        # ),
        ChannelMode(
            id="2",
            name="BF LED matrix left half",
            exposure_time=16,
            analog_gain=0,
            illumination_source=1,
            illumination_intensity=5,
            camera_sn="",
            z_offset=0.0,
        ),
        ChannelMode(
            id="3",
            name="BF LED matrix right half",
            exposure_time=16,
            analog_gain=0,
            illumination_source=2,
            illumination_intensity=5,
            camera_sn="",
            z_offset=0.0,
        ),
        ChannelMode(
            id="12",
            name="BF LED matrix top half",
            exposure_time=20,
            analog_gain=0,
            illumination_source=7,
            illumination_intensity=20,
            camera_sn="",
            z_offset=0.0,
        ),
        ChannelMode(
            id="13",
            name="BF LED matrix bottom half",
            exposure_time=20,
            analog_gain=0,
            illumination_source=8,
            illumination_intensity=20,
            camera_sn="",
            z_offset=0.0,
        ),
        ChannelMode(
            id="14",
            name="BF LED matrix full_R",
            exposure_time=12,
            analog_gain=0,
            illumination_source=0,
            illumination_intensity=5,
            camera_sn="",
            z_offset=0.0,
        ),
        ChannelMode(
            id="15",
            name="BF LED matrix full_G",
            exposure_time=12,
            analog_gain=0,
            illumination_source=0,
            illumination_intensity=5,
            camera_sn="",
            z_offset=0.0,
        ),
        ChannelMode(
            id="16",
            name="BF LED matrix full_B",
            exposure_time=12,
            analog_gain=0,
            illumination_source=0,
            illumination_intensity=5,
            camera_sn="",
            z_offset=0.0,
        ),
        ChannelMode(
            id="21",
            name="BF LED matrix full_RGB",
            exposure_time=12,
            analog_gain=0,
            illumination_source=0,
            illumination_intensity=5,
            camera_sn="",
            z_offset=0.0,
        ),
        ChannelMode(
            id="20",
            name="USB Spectrometer",
            exposure_time=20,
            analog_gain=0,
            illumination_source=6,
            illumination_intensity=0,
            camera_sn="",
            z_offset=0.0,
        ),
    ]

    config = ChannelConfig(modes=default_modes)
    xml_str = config.to_xml(pretty_print=True, encoding="utf-8")

    # Write to file
    path = Path(filename)
    if not path.parent.exists():
        path.parent.mkdir(parents=True)
    path.write_bytes(xml_str)
