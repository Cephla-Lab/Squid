from pydantic import BaseModel, field_validator, model_validator
from pydantic_xml import BaseXmlModel, element, attr
from typing import List, Optional, Dict
from pathlib import Path
from enum import Enum
import base64
import json
import numpy as np

import control.utils_channel as utils_channel
from control._def import CHANNEL_COLORS_MAP
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


class ChannelType(str, Enum):
    """Type of imaging channel"""

    FLUORESCENCE = "fluorescence"
    LED_MATRIX = "led_matrix"


class NumericChannelMapping(BaseModel):
    """Mapping from numeric channel to illumination source and excitation wavelength"""

    illumination_source: int
    ex_wavelength: int


# Channel name constraints (also enforced in UI, but validated here for direct JSON edits)
CHANNEL_NAME_MAX_LENGTH = 64
CHANNEL_NAME_INVALID_CHARS = r'<>:"/\|?*' + "\0"


class ChannelDefinition(BaseModel):
    """Definition of a single imaging channel"""

    name: str
    type: ChannelType
    emission_filter_position: int = 1
    display_color: str = "#FFFFFF"
    enabled: bool = True
    # For fluorescence channels: maps to numeric channel (1-N)
    numeric_channel: Optional[int] = None
    # For LED matrix channels: direct illumination source
    illumination_source: Optional[int] = None
    # Excitation wavelength (for fluorescence, derived from numeric_channel_mapping)
    ex_wavelength: Optional[int] = None

    @field_validator("name", mode="after")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate channel name constraints"""
        if not v or not v.strip():
            raise ValueError("Channel name cannot be empty")
        if len(v) > CHANNEL_NAME_MAX_LENGTH:
            raise ValueError(f"Channel name exceeds maximum length of {CHANNEL_NAME_MAX_LENGTH} characters")
        invalid_found = [c for c in CHANNEL_NAME_INVALID_CHARS if c in v]
        if invalid_found:
            raise ValueError(f"Channel name contains invalid characters: {invalid_found}")
        return v

    @field_validator("display_color", mode="before")
    @classmethod
    def convert_color(cls, v):
        """Convert integer color to hex string if needed"""
        if isinstance(v, int):
            return f"#{v:06X}"
        return v

    @model_validator(mode="after")
    def validate_channel_type_fields(self):
        """Validate that required fields are set based on channel type"""
        if self.type == ChannelType.FLUORESCENCE and self.numeric_channel is None:
            raise ValueError(f"Fluorescence channel '{self.name}' must have numeric_channel set")
        if self.type == ChannelType.LED_MATRIX and self.illumination_source is None:
            raise ValueError(f"LED matrix channel '{self.name}' must have illumination_source set")
        return self

    def get_illumination_source(self, numeric_channel_mapping: Dict[str, NumericChannelMapping]) -> int:
        """Get the illumination source for this channel"""
        if self.type == ChannelType.LED_MATRIX:
            if self.illumination_source is None:
                raise ValueError(f"LED matrix channel '{self.name}' has no illumination_source")
            return self.illumination_source
        else:
            # Fluorescence: look up from numeric channel mapping
            mapping = numeric_channel_mapping.get(str(self.numeric_channel))
            if mapping:
                return mapping.illumination_source
            raise ValueError(
                f"Fluorescence channel '{self.name}' has no numeric_channel_mapping entry "
                f"for numeric_channel {self.numeric_channel}. "
                f"Check your numeric_channel_mapping configuration and add a mapping for this channel."
            )

    def get_ex_wavelength(self, numeric_channel_mapping: Dict[str, NumericChannelMapping]) -> Optional[int]:
        """Get the excitation wavelength for this channel"""
        if self.type == ChannelType.LED_MATRIX:
            return None
        else:
            mapping = numeric_channel_mapping.get(str(self.numeric_channel))
            if mapping:
                return mapping.ex_wavelength
            return self.ex_wavelength


class ConfocalOverrides(BaseModel):
    """Optional overrides for confocal mode.

    Only specify values that differ from widefield defaults.
    None values inherit from base settings.
    """

    exposure_time: Optional[float] = None
    analog_gain: Optional[float] = None
    illumination_intensity: Optional[float] = None
    z_offset: Optional[float] = None


class ObjectiveChannelSettings(BaseModel):
    """Per-objective settings for a channel.

    Base settings are used for widefield mode (or when confocal is not enabled).
    Optional confocal overrides specify only values that differ in confocal mode.
    """

    exposure_time: float = 25.0
    analog_gain: float = 0.0
    illumination_intensity: float = 20.0
    z_offset: float = 0.0

    # Optional confocal-specific overrides (only store differences)
    confocal: Optional[ConfocalOverrides] = None

    def get_effective_settings(self, confocal_mode: bool = False) -> "ObjectiveChannelSettings":
        """Get effective settings with confocal overrides applied if applicable.

        Args:
            confocal_mode: Whether the system is in confocal mode

        Returns:
            A new ObjectiveChannelSettings with effective values
        """
        if not confocal_mode or self.confocal is None:
            return ObjectiveChannelSettings(
                exposure_time=self.exposure_time,
                analog_gain=self.analog_gain,
                illumination_intensity=self.illumination_intensity,
                z_offset=self.z_offset,
                confocal=self.confocal,
            )

        # Apply confocal overrides (use override if set, otherwise use base)
        return ObjectiveChannelSettings(
            exposure_time=(
                self.confocal.exposure_time if self.confocal.exposure_time is not None else self.exposure_time
            ),
            analog_gain=self.confocal.analog_gain if self.confocal.analog_gain is not None else self.analog_gain,
            illumination_intensity=(
                self.confocal.illumination_intensity
                if self.confocal.illumination_intensity is not None
                else self.illumination_intensity
            ),
            z_offset=self.confocal.z_offset if self.confocal.z_offset is not None else self.z_offset,
            confocal=self.confocal,
        )


class ChannelDefinitionsConfig(BaseModel):
    """Root configuration for channel definitions"""

    max_fluorescence_channels: int = 5
    channels: List[ChannelDefinition] = []
    numeric_channel_mapping: Dict[str, NumericChannelMapping] = {}

    @model_validator(mode="after")
    def validate_channel_mappings(self):
        """Validate that all fluorescence channels have valid numeric_channel mappings.

        This catches configuration errors at startup rather than during use.
        """
        for channel in self.channels:
            if channel.type == ChannelType.FLUORESCENCE and channel.numeric_channel is not None:
                if str(channel.numeric_channel) not in self.numeric_channel_mapping:
                    available = list(self.numeric_channel_mapping.keys()) or ["(none defined)"]
                    raise ValueError(
                        f"Fluorescence channel '{channel.name}' references numeric_channel "
                        f"{channel.numeric_channel}, but no mapping exists for it. "
                        f"Available mappings: {available}. "
                        f"Add a mapping for '{channel.numeric_channel}' in numeric_channel_mapping."
                    )
        return self

    def get_enabled_channels(self) -> List[ChannelDefinition]:
        """Get list of enabled channels only"""
        return [ch for ch in self.channels if ch.enabled]

    def get_channel_by_name(self, name: str) -> Optional[ChannelDefinition]:
        """Get a channel by its name"""
        for ch in self.channels:
            if ch.name == name:
                return ch
        return None

    def save(self, path: Path) -> None:
        """Save configuration to JSON file"""
        try:
            with open(path, "w") as f:
                json.dump(self.model_dump(), f, indent=2)
        except (IOError, PermissionError) as e:
            raise IOError(f"Failed to save channel definitions to {path}: {e}")

    @classmethod
    def load(cls, path: Path) -> "ChannelDefinitionsConfig":
        """Load configuration from JSON file"""
        try:
            with open(path, "r") as f:
                data = json.load(f)
            return cls(**data)
        except FileNotFoundError:
            raise IOError(
                f"Channel definitions file not found: {path}. "
                f"Delete any partial config files and restart to regenerate defaults."
            )
        except json.JSONDecodeError as e:
            raise IOError(
                f"Invalid JSON in channel definitions file {path}: {e}. "
                f"Check the file for syntax errors or restore from channel_definitions.default.json."
            )
        except PermissionError:
            raise IOError(
                f"Permission denied reading {path}. " "Check file permissions and ensure the file is not locked."
            )

    @classmethod
    def generate_default(cls) -> "ChannelDefinitionsConfig":
        """Generate default channel definitions"""
        channels = [
            ChannelDefinition(
                name="BF LED matrix full",
                type=ChannelType.LED_MATRIX,
                illumination_source=0,
                emission_filter_position=1,
                display_color="#FFFFFF",
                enabled=True,
            ),
            ChannelDefinition(
                name="DF LED matrix",
                type=ChannelType.LED_MATRIX,
                illumination_source=3,
                emission_filter_position=1,
                display_color="#FFFFFF",
                enabled=True,
            ),
            ChannelDefinition(
                name="Fluorescence 405 nm Ex",
                type=ChannelType.FLUORESCENCE,
                numeric_channel=1,
                emission_filter_position=1,
                display_color=f"#{CHANNEL_COLORS_MAP.get('405', {}).get('hex', 0x20ADF8):06X}",
                enabled=True,
            ),
            ChannelDefinition(
                name="Fluorescence 488 nm Ex",
                type=ChannelType.FLUORESCENCE,
                numeric_channel=2,
                emission_filter_position=1,
                display_color=f"#{CHANNEL_COLORS_MAP.get('488', {}).get('hex', 0x1FFF00):06X}",
                enabled=True,
            ),
            ChannelDefinition(
                name="Fluorescence 561 nm Ex",
                type=ChannelType.FLUORESCENCE,
                numeric_channel=3,
                emission_filter_position=1,
                display_color=f"#{CHANNEL_COLORS_MAP.get('561', {}).get('hex', 0xFFCF00):06X}",
                enabled=True,
            ),
            ChannelDefinition(
                name="Fluorescence 638 nm Ex",
                type=ChannelType.FLUORESCENCE,
                numeric_channel=4,
                emission_filter_position=1,
                display_color=f"#{CHANNEL_COLORS_MAP.get('638', {}).get('hex', 0xFF0000):06X}",
                enabled=True,
            ),
            ChannelDefinition(
                name="Fluorescence 730 nm Ex",
                type=ChannelType.FLUORESCENCE,
                numeric_channel=5,
                emission_filter_position=1,
                display_color=f"#{CHANNEL_COLORS_MAP.get('730', {}).get('hex', 0x770000):06X}",
                enabled=True,
            ),
            ChannelDefinition(
                name="BF LED matrix low NA",
                type=ChannelType.LED_MATRIX,
                illumination_source=4,
                emission_filter_position=1,
                display_color="#FFFFFF",
                enabled=True,
            ),
            ChannelDefinition(
                name="BF LED matrix left half",
                type=ChannelType.LED_MATRIX,
                illumination_source=1,
                emission_filter_position=1,
                display_color="#FFFFFF",
                enabled=False,
            ),
            ChannelDefinition(
                name="BF LED matrix right half",
                type=ChannelType.LED_MATRIX,
                illumination_source=2,
                emission_filter_position=1,
                display_color="#FFFFFF",
                enabled=False,
            ),
            ChannelDefinition(
                name="BF LED matrix top half",
                type=ChannelType.LED_MATRIX,
                illumination_source=7,
                emission_filter_position=1,
                display_color="#FFFFFF",
                enabled=False,
            ),
            ChannelDefinition(
                name="BF LED matrix bottom half",
                type=ChannelType.LED_MATRIX,
                illumination_source=8,
                emission_filter_position=1,
                display_color="#FFFFFF",
                enabled=False,
            ),
            ChannelDefinition(
                name="BF LED matrix full_R",
                type=ChannelType.LED_MATRIX,
                illumination_source=0,
                emission_filter_position=1,
                display_color="#FF0000",
                enabled=False,
            ),
            ChannelDefinition(
                name="BF LED matrix full_G",
                type=ChannelType.LED_MATRIX,
                illumination_source=0,
                emission_filter_position=1,
                display_color="#00FF00",
                enabled=False,
            ),
            ChannelDefinition(
                name="BF LED matrix full_B",
                type=ChannelType.LED_MATRIX,
                illumination_source=0,
                emission_filter_position=1,
                display_color="#0000FF",
                enabled=False,
            ),
            ChannelDefinition(
                name="BF LED matrix full_RGB",
                type=ChannelType.LED_MATRIX,
                illumination_source=0,
                emission_filter_position=1,
                display_color="#FFFFFF",
                enabled=False,
            ),
        ]

        numeric_channel_mapping = {
            "1": NumericChannelMapping(illumination_source=11, ex_wavelength=405),
            "2": NumericChannelMapping(illumination_source=12, ex_wavelength=488),
            "3": NumericChannelMapping(illumination_source=14, ex_wavelength=561),
            "4": NumericChannelMapping(illumination_source=13, ex_wavelength=638),
            "5": NumericChannelMapping(illumination_source=15, ex_wavelength=730),
        }

        return cls(
            max_fluorescence_channels=5,
            channels=channels,
            numeric_channel_mapping=numeric_channel_mapping,
        )


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
            self.has_reference = False
            return
        self.reference_image = base64.b64encode(image.tobytes()).decode("utf-8")
        self.reference_image_shape = image.shape
        self.reference_image_dtype = str(image.dtype)
        self.has_reference = True

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
    ]

    config = ChannelConfig(modes=default_modes)
    xml_str = config.to_xml(pretty_print=True, encoding="utf-8")

    # Write to file
    path = Path(filename)
    if not path.parent.exists():
        path.parent.mkdir(parents=True)
    path.write_bytes(xml_str)
