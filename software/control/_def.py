import os
import sys
import glob
from pathlib import Path
from configparser import ConfigParser
from typing import Union
import json
import csv
import squid.logging
from enum import Enum, auto

log = squid.logging.get_logger(__name__)


def conf_attribute_reader(string_value):
    """
    :brief: standardized way for reading config entries
    that are strings, in priority order
    JSON (with comments stripped if needed) -> None -> bool -> int -> float -> string
    Inline comments (# ...) are stripped, but # inside valid JSON is preserved.
    REMEMBER TO ENCLOSE PROPERTY NAMES IN LISTS/DICTS IN DOUBLE QUOTES
    """
    actualvalue = str(string_value).strip()

    # Try JSON first - handles valid JSON with # inside (like {"color": "#FF0000"})
    try:
        return json.loads(actualvalue)
    except (json.JSONDecodeError, ValueError):
        pass

    # JSON failed - strip inline comments if present
    # Only treat # as comment if preceded by whitespace (e.g., "value  # comment")
    if "#" in actualvalue:
        # For JSON-like values, try stripping from rightmost # positions
        # This handles cases like {"color": "#FF0000"}  # comment
        if actualvalue.startswith("[") or actualvalue.startswith("{"):
            hash_positions = [i for i, c in enumerate(actualvalue) if c == "#"]
            for pos in reversed(hash_positions):
                candidate = actualvalue[:pos].strip()
                try:
                    return json.loads(candidate)
                except (json.JSONDecodeError, ValueError):
                    continue
        # For non-JSON or if all JSON attempts failed, strip comments with whitespace before #
        # This preserves values like "my#tag" while stripping "value  # comment"
        # Find the earliest comment separator to handle "value\t# c1  # c2" correctly
        comment_positions = [actualvalue.find(sep) for sep in (" #", "\t#") if sep in actualvalue]
        if comment_positions:
            cut_pos = min(comment_positions)
            actualvalue = actualvalue[:cut_pos].rstrip()

    # Parse the (possibly stripped) value
    if actualvalue == "None":
        return None
    if actualvalue in ("True", "true"):
        return True
    if actualvalue in ("False", "false"):
        return False

    # Try JSON again (for cases like [1,2,3] # comment -> [1,2,3])
    try:
        return json.loads(actualvalue)
    except (json.JSONDecodeError, ValueError):
        pass

    # Try int
    try:
        return int(actualvalue)
    except ValueError:
        pass

    # Try float
    try:
        return float(actualvalue)
    except ValueError:
        pass

    return actualvalue


def populate_class_from_dict(myclass, options):
    """
    :brief: helper function to establish a compatibility
        layer between new way of storing config and current
        way of accessing it. assumes all class attributes are
        all-uppercase, and pattern-matches attributes in
        priority order dict/list (json) -> -> int -> float-> string
    REMEMBER TO ENCLOSE PROPERTY NAMES IN LISTS IN DOUBLE QUOTES
    """
    for key, value in options:
        if key.startswith("_") and key.endswith("options"):
            continue
        actualkey = key.upper()
        actualvalue = conf_attribute_reader(value)
        setattr(myclass, actualkey, actualvalue)


class TriggerMode:
    SOFTWARE = "Software Trigger"
    HARDWARE = "Hardware Trigger"
    CONTINUOUS = "Continuous Acquisition"

    @staticmethod
    def convert_to_var(option: Union[str, "TriggerMode"]) -> "TriggerMode":
        """
        Attempts to convert the given string to a TriggerMode.
        """
        if isinstance(option, TriggerMode):
            return option

        for name, value in vars(TriggerMode).items():
            if value == option or name == option.upper():
                return getattr(TriggerMode, name)
        raise ValueError(f"Invalid trigger mode: {option}")


class Acquisition:
    NUMBER_OF_FOVS_PER_AF = 3
    IMAGE_FORMAT = "bmp"
    IMAGE_DISPLAY_SCALING_FACTOR = 0.3
    DX = 0.9
    DY = 0.9
    DZ = 1.5
    NX = 1
    NY = 1
    USE_MULTIPROCESSING = True


class PosUpdate:
    INTERVAL_MS = 25


class MicrocontrollerDef:
    MSG_LENGTH = 24
    CMD_LENGTH = 8
    N_BYTES_POS = 4


USE_SEPARATE_MCU_FOR_DAC = False


class MCU_PINS:
    PWM1 = 5
    PWM2 = 4
    PWM3 = 22
    PWM4 = 3
    PWM5 = 23
    PWM6 = 2
    PWM7 = 1
    PWM9 = 6
    PWM10 = 7
    PWM11 = 8
    PWM12 = 9
    PWM13 = 10
    PWM14 = 15
    PWM15 = 24
    PWM16 = 25
    AF_LASER = 15


class CMD_SET:
    MOVE_X = 0
    MOVE_Y = 1
    MOVE_Z = 2
    MOVE_THETA = 3
    MOVE_W = 4
    HOME_OR_ZERO = 5
    TURN_ON_ILLUMINATION = 10
    TURN_OFF_ILLUMINATION = 11
    SET_ILLUMINATION = 12
    SET_ILLUMINATION_LED_MATRIX = 13
    ACK_JOYSTICK_BUTTON_PRESSED = 14
    ANALOG_WRITE_ONBOARD_DAC = 15
    SET_DAC80508_REFDIV_GAIN = 16
    SET_ILLUMINATION_INTENSITY_FACTOR = 17
    MOVETO_W = 18
    MOVE_W2 = 19
    MOVETO_X = 6
    MOVETO_Y = 7
    MOVETO_Z = 8
    SET_LIM = 9
    SET_LIM_SWITCH_POLARITY = 20
    CONFIGURE_STEPPER_DRIVER = 21
    SET_MAX_VELOCITY_ACCELERATION = 22
    SET_LEAD_SCREW_PITCH = 23
    SET_OFFSET_VELOCITY = 24
    CONFIGURE_STAGE_PID = 25
    ENABLE_STAGE_PID = 26
    DISABLE_STAGE_PID = 27
    SET_HOME_SAFETY_MERGIN = 28
    SET_PID_ARGUMENTS = 29
    SEND_HARDWARE_TRIGGER = 30
    SET_STROBE_DELAY = 31
    SET_AXIS_DISABLE_ENABLE = 32
    SET_TRIGGER_MODE = 33
    SET_PIN_LEVEL = 41
    INITFILTERWHEEL_W2 = 252
    INITFILTERWHEEL = 253
    INITIALIZE = 254
    RESET = 255


class CMD_SET2:
    ANALOG_WRITE_DAC8050X = 0
    SET_CAMERA_TRIGGER_FREQUENCY = 1
    START_CAMERA_TRIGGERING = 2
    STOP_CAMERA_TRIGGERING = 3


BIT_POS_JOYSTICK_BUTTON = 0
BIT_POS_SWITCH = 1


class HOME_OR_ZERO:
    HOME_NEGATIVE = 1  # motor moves along the negative direction (MCU coordinates)
    HOME_POSITIVE = 0  # motor moves along the negative direction (MCU coordinates)
    ZERO = 2


class AXIS:
    X = 0
    Y = 1
    Z = 2
    THETA = 3
    XY = 4
    W = 5
    W2 = 6


class LIMIT_CODE:
    X_POSITIVE = 0
    X_NEGATIVE = 1
    Y_POSITIVE = 2
    Y_NEGATIVE = 3
    Z_POSITIVE = 4
    Z_NEGATIVE = 5


class LIMIT_SWITCH_POLARITY:
    ACTIVE_LOW = 0
    ACTIVE_HIGH = 1
    DISABLED = 2
    X_HOME = 1
    Y_HOME = 1
    Z_HOME = 2


class ILLUMINATION_CODE:
    ILLUMINATION_SOURCE_LED_ARRAY_FULL = 0
    ILLUMINATION_SOURCE_LED_ARRAY_LEFT_HALF = 1
    ILLUMINATION_SOURCE_LED_ARRAY_RIGHT_HALF = 2
    ILLUMINATION_SOURCE_LED_ARRAY_LEFTB_RIGHTR = 3
    ILLUMINATION_SOURCE_LED_ARRAY_LOW_NA = 4
    ILLUMINATION_SOURCE_LED_ARRAY_LEFT_DOT = 5
    ILLUMINATION_SOURCE_LED_ARRAY_RIGHT_DOT = 6
    ILLUMINATION_SOURCE_LED_ARRAY_TOP_HALF = 7
    ILLUMINATION_SOURCE_LED_ARRAY_BOTTOM_HALF = 8
    ILLUMINATION_SOURCE_LED_EXTERNAL_FET = 20
    ILLUMINATION_SOURCE_405NM = 11
    ILLUMINATION_SOURCE_488NM = 12
    ILLUMINATION_SOURCE_638NM = 13
    ILLUMINATION_SOURCE_561NM = 14
    ILLUMINATION_SOURCE_730NM = 15


class VOLUMETRIC_IMAGING:
    NUM_PLANES_PER_VOLUME = 20


class CMD_EXECUTION_STATUS:
    COMPLETED_WITHOUT_ERRORS = 0
    IN_PROGRESS = 1
    CMD_CHECKSUM_ERROR = 2
    CMD_INVALID = 3
    CMD_EXECUTION_ERROR = 4
    ERROR_CODE_EMPTYING_THE_FLUDIIC_LINE_FAILED = 100


class SpotDetectionMode(Enum):
    """Specifies which spot to detect when multiple spots are present.

    SINGLE: Expect and detect single spot
    DUAL_RIGHT: In dual-spot case, use rightmost spot
    DUAL_LEFT: In dual-spot case, use leftmost spot
    MULTI_RIGHT: In multi-spot case, use rightmost spot
    MULTI_SECOND_RIGHT: In multi-spot case, use spot immediately left of rightmost spot
    """

    SINGLE = "single"
    DUAL_RIGHT = "dual_right"
    DUAL_LEFT = "dual_left"
    MULTI_RIGHT = "multi_right"
    MULTI_SECOND_RIGHT = "multi_second_right"


class FileSavingOption(Enum):
    """File saving options.

    INDIVIDUAL_IMAGES: Save each image as a separate file. Format is defined in Acquisition.IMAGE_FORMAT.
    MULTI_PAGE_TIFF: Save all images from a single FOV as a single multi-page TIFF file.
    OME_TIFF: Save data to OME-TIFF stacks with full metadata.
    ZARR_V3: Save data to Zarr v3 format with sharding.
    """

    INDIVIDUAL_IMAGES = "INDIVIDUAL_IMAGES"
    MULTI_PAGE_TIFF = "MULTI_PAGE_TIFF"
    OME_TIFF = "OME_TIFF"
    ZARR_V3 = "ZARR_V3"

    @staticmethod
    def convert_to_enum(option: Union[str, "FileSavingOption"]) -> "FileSavingOption":
        """
        Attempts to convert the given string to a FileSavingOption.  This ignores all letter cases.
        """
        if isinstance(option, FileSavingOption):
            return option

        try:
            return FileSavingOption[option.upper()]
        except KeyError:
            raise ValueError(f"Invalid file saving option: {option}")


class ZarrChunkMode(Enum):
    """Zarr chunk size configuration.

    FULL_FRAME: Each chunk is a full image plane (simplest, default).
    TILED_512: 512x512 pixel chunks for tiled visualization.
    TILED_256: 256x256 pixel chunks for fine-grained streaming.
    """

    FULL_FRAME = "full_frame"
    TILED_512 = "tiled_512"
    TILED_256 = "tiled_256"

    @staticmethod
    def convert_to_enum(option: Union[str, "ZarrChunkMode"]) -> "ZarrChunkMode":
        """Convert string or enum to ZarrChunkMode enum."""
        if isinstance(option, ZarrChunkMode):
            return option
        try:
            return ZarrChunkMode(option.lower())
        except ValueError:
            raise ValueError(
                f"Invalid zarr chunk mode: '{option}'. Expected 'full_frame', 'tiled_512', or 'tiled_256'."
            )


class ZarrCompression(Enum):
    """Zarr compression presets optimized for different use cases.

    NONE: No compression, maximum write speed (~2x faster than TIFF).
    FAST: blosc-lz4, ~1000 MB/s encode, ~2x compression ratio. Safe for 10-20 fps.
    BALANCED: blosc-zstd level 3, ~500 MB/s encode, ~3-4x ratio.
    BEST: blosc-zstd level 9, slowest but best compression ratio.
    """

    NONE = "none"
    FAST = "fast"
    BALANCED = "balanced"
    BEST = "best"

    @staticmethod
    def convert_to_enum(option: Union[str, "ZarrCompression"]) -> "ZarrCompression":
        """Convert string or enum to ZarrCompression enum."""
        if isinstance(option, ZarrCompression):
            return option
        try:
            return ZarrCompression(option.lower())
        except ValueError:
            raise ValueError(f"Invalid zarr compression: '{option}'. Expected 'none', 'fast', 'balanced', or 'best'.")


class FocusMeasureOperator(Enum):
    LAPE = "LAPE"  # LAPE has worked well for bright field images
    GLVA = "GLVA"  # GLVA works well for darkfield/fluorescence
    TENENGRAD = "TENENGRAD"

    @staticmethod
    def convert_to_enum(option: Union[str, "FocusMeasureOperator"]) -> "FocusMeasureOperator":
        """
        Attempts to convert the given string to a FocusMeasureOperator.  This ignores all letter cases.
        """
        if isinstance(option, FocusMeasureOperator):
            return option

        try:
            return FocusMeasureOperator[option.upper()]
        except KeyError:
            raise ValueError(f"Invalid focus measure operator: {option}")


class ZProjectionMode(Enum):
    """Z-projection mode for downsampled view generation.

    MIP: Max intensity projection - uses running maximum across all z-levels
    MIDDLE: Middle layer - uses only the middle z-level (z = NZ // 2)
    """

    MIP = "mip"
    MIDDLE = "middle"

    @staticmethod
    def convert_to_enum(option: Union[str, "ZProjectionMode"]) -> "ZProjectionMode":
        """Convert string or enum to ZProjectionMode enum."""
        if isinstance(option, ZProjectionMode):
            return option
        try:
            return ZProjectionMode(option.lower())
        except ValueError:
            raise ValueError(f"Invalid z-projection mode: '{option}'. Expected 'mip' or 'middle'.")


class DownsamplingMethod(Enum):
    """Interpolation method for downsampled view generation.

    INTER_LINEAR: Fast bilinear interpolation (~0.05ms). Each resolution is computed
        directly from the original image (parallel). Best for real-time previews.
    INTER_AREA_FAST: Gaussian pyramid downsampling (~1ms). Uses cv2.pyrDown chain
        (optimized 2x reductions) then INTER_AREA for final size. Good balance of
        speed and quality. Resolutions computed in parallel.
    INTER_AREA: High-quality area averaging (~18ms). Resolutions are computed in
        cascade (original→5um→10um→20um) for speed. Best for final output quality.
    """

    INTER_LINEAR = "inter_linear"
    INTER_AREA_FAST = "inter_area_fast"
    INTER_AREA = "inter_area"

    @staticmethod
    def convert_to_enum(option: Union[str, "DownsamplingMethod"]) -> "DownsamplingMethod":
        """Convert string or enum to DownsamplingMethod enum."""
        if isinstance(option, DownsamplingMethod):
            return option
        try:
            return DownsamplingMethod(option.lower())
        except ValueError:
            raise ValueError(
                f"Invalid downsampling method: '{option}'. "
                "Expected 'inter_linear', 'inter_area_fast', or 'inter_area'."
            )


class ZMotorConfig(Enum):
    """Z motor configuration options.

    STEPPER: Stepper motor only
    STEPPER_PIEZO: Stepper motor with piezo for fine Z control
    PIEZO: Piezo only
    """

    STEPPER = "STEPPER"
    STEPPER_PIEZO = "STEPPER + PIEZO"
    PIEZO = "PIEZO"

    @staticmethod
    def convert_to_enum(option: Union[str, "ZMotorConfig"]) -> "ZMotorConfig":
        """Convert string or enum to ZMotorConfig enum."""
        if isinstance(option, ZMotorConfig):
            return option
        for member in ZMotorConfig:
            if member.value == option:
                return member
        raise ValueError(f"Invalid Z motor config: '{option}'. Expected one of: {[m.value for m in ZMotorConfig]}")

    def has_piezo(self) -> bool:
        """Check if this configuration includes a piezo."""
        return "PIEZO" in self.value

    def is_piezo_only(self) -> bool:
        """Check if this configuration is piezo-only (no stepper)."""
        return self == ZMotorConfig.PIEZO


PRINT_CAMERA_FPS = True

###########################################################
#### machine specific configurations - to be overridden ###
###########################################################
DEFAULT_TRIGGER_MODE = TriggerMode.SOFTWARE

BUFFER_SIZE_LIMIT = 4095

# note: XY are the in-plane axes, Z is the focus axis

# change the following so that "backward" is "backward" - towards the single sided hall effect sensor
STAGE_MOVEMENT_SIGN_X = -1
STAGE_MOVEMENT_SIGN_Y = 1
STAGE_MOVEMENT_SIGN_Z = -1
STAGE_MOVEMENT_SIGN_THETA = 1
STAGE_MOVEMENT_SIGN_W = 1

STAGE_POS_SIGN_X = STAGE_MOVEMENT_SIGN_X
STAGE_POS_SIGN_Y = STAGE_MOVEMENT_SIGN_Y
STAGE_POS_SIGN_Z = STAGE_MOVEMENT_SIGN_Z
STAGE_POS_SIGN_THETA = STAGE_MOVEMENT_SIGN_THETA

TRACKING_MOVEMENT_SIGN_X = 1
TRACKING_MOVEMENT_SIGN_Y = 1
TRACKING_MOVEMENT_SIGN_Z = 1
TRACKING_MOVEMENT_SIGN_THETA = 1

USE_ENCODER_X = False
USE_ENCODER_Y = False
USE_ENCODER_Z = False
USE_ENCODER_THETA = False

ENCODER_POS_SIGN_X = 1
ENCODER_POS_SIGN_Y = 1
ENCODER_POS_SIGN_Z = 1
ENCODER_POS_SIGN_THETA = 1

ENCODER_STEP_SIZE_X_MM = 100e-6
ENCODER_STEP_SIZE_Y_MM = 100e-6
ENCODER_STEP_SIZE_Z_MM = 100e-6
ENCODER_STEP_SIZE_THETA = 1

FULLSTEPS_PER_REV_X = 200
FULLSTEPS_PER_REV_Y = 200
FULLSTEPS_PER_REV_Z = 200
FULLSTEPS_PER_REV_W = 200
FULLSTEPS_PER_REV_THETA = 200

# beginning of actuator specific configurations

SCREW_PITCH_X_MM = 1
SCREW_PITCH_Y_MM = 1
SCREW_PITCH_Z_MM = 0.012 * 25.4
SCREW_PITCH_W_MM = 1

MICROSTEPPING_DEFAULT_X = 8
MICROSTEPPING_DEFAULT_Y = 8
MICROSTEPPING_DEFAULT_Z = 8
MICROSTEPPING_DEFAULT_W = 64
MICROSTEPPING_DEFAULT_THETA = 8  # not used, to be removed

X_MOTOR_RMS_CURRENT_mA = 490
Y_MOTOR_RMS_CURRENT_mA = 490
Z_MOTOR_RMS_CURRENT_mA = 490
W_MOTOR_RMS_CURRENT_mA = 1900

X_MOTOR_I_HOLD = 0.5
Y_MOTOR_I_HOLD = 0.5
Z_MOTOR_I_HOLD = 0.5
W_MOTOR_I_HOLD = 0.5

MAX_VELOCITY_X_mm = 25
MAX_VELOCITY_Y_mm = 25
MAX_VELOCITY_Z_mm = 2
MAX_VELOCITY_W_mm = 3.19

MAX_ACCELERATION_X_mm = 500
MAX_ACCELERATION_Y_mm = 500
MAX_ACCELERATION_Z_mm = 20
MAX_ACCELERATION_W_mm = 300

# config encoder arguments
HAS_ENCODER_X = False
HAS_ENCODER_Y = False
HAS_ENCODER_Z = False
HAS_ENCODER_W = False

# enable PID control
ENABLE_PID_X = False
ENABLE_PID_Y = False
ENABLE_PID_Z = False
ENABLE_PID_W = False

# PID arguments
PID_P_X = int(1 << 12)
PID_I_X = int(0)
PID_D_X = int(0)

PID_P_Y = int(1 << 12)
PID_I_Y = int(0)
PID_D_Y = int(0)

PID_P_Z = int(1 << 12)
PID_I_Z = int(0)
PID_D_Z = int(1)

PID_P_W = int(1 << 12)
PID_I_W = int(1)
PID_D_W = int(1)

# flip direction True or False
ENCODER_FLIP_DIR_X = True
ENCODER_FLIP_DIR_Y = True
ENCODER_FLIP_DIR_Z = True
ENCODER_FLIP_DIR_W = False

# distance for each count (um)
ENCODER_RESOLUTION_UM_X = 0.05
ENCODER_RESOLUTION_UM_Y = 0.05
ENCODER_RESOLUTION_UM_Z = 0.1

# end of actuator specific configurations

SCAN_STABILIZATION_TIME_MS_X = 160
SCAN_STABILIZATION_TIME_MS_Y = 160
SCAN_STABILIZATION_TIME_MS_Z = 20
HOMING_ENABLED_X = True
HOMING_ENABLED_Y = True
HOMING_ENABLED_Z = False

SLEEP_TIME_S = 0.005

LED_MATRIX_R_FACTOR = 0
LED_MATRIX_G_FACTOR = 0
LED_MATRIX_B_FACTOR = 1

DEFAULT_SAVING_PATH = str(Path.home()) + "/Downloads"
ACQUISITION_CONFIGURATIONS_PATH = Path("user_profiles")
FILE_ID_PADDING = 0


class PLATE_READER:
    NUMBER_OF_ROWS = 8
    NUMBER_OF_COLUMNS = 12
    ROW_SPACING_MM = 9
    COLUMN_SPACING_MM = 9
    OFFSET_COLUMN_1_MM = 20
    OFFSET_ROW_A_MM = 20


CAMERA_PIXEL_SIZE_UM = {
    "IMX290": 2.9,
    "IMX178": 2.4,
    "IMX226": 1.85,
    "IMX250": 3.45,
    "IMX252": 3.45,
    "IMX273": 3.45,
    "IMX264": 3.45,
    "IMX265": 3.45,
    "IMX571": 3.76,
    "PYTHON300": 4.8,
}

TUBE_LENS_MM = 50
CAMERA_SENSOR = "IMX226"
TRACKERS = ["csrt", "kcf", "mil", "tld", "medianflow", "mosse", "daSiamRPN"]
DEFAULT_TRACKER = "csrt"

ENABLE_TRACKING = False
TRACKING_SHOW_MICROSCOPE_CONFIGURATIONS = False  # set to true when doing multimodal acquisition


class CAMERA_CONFIG:
    ROI_OFFSET_X_DEFAULT = None
    ROI_OFFSET_Y_DEFAULT = None
    ROI_WIDTH_DEFAULT = None
    ROI_HEIGHT_DEFAULT = None
    ROTATE_IMAGE_ANGLE = None
    FLIP_IMAGE = None  # 'Horizontal', 'Vertical', 'Both'
    CROP_WIDTH_UNBINNED = 4168
    CROP_HEIGHT_UNBINNED = 4168
    BINNING_FACTOR_DEFAULT = 2
    PIXEL_FORMAT_DEFAULT = "MONO12"
    TEMPERATURE_DEFAULT = 20
    FAN_SPEED_DEFAULT = 1
    BLACKLEVEL_VALUE_DEFAULT = 3
    AWB_RATIOS_R = 1.375
    AWB_RATIOS_G = 1
    AWB_RATIOS_B = 1.4141


class AF:
    STOP_THRESHOLD = 0.85
    CROP_WIDTH = 800
    CROP_HEIGHT = 800


class Tracking:
    SEARCH_AREA_RATIO = 10  # @@@ check
    CROPPED_IMG_RATIO = 10  # @@@ check
    BBOX_SCALE_FACTOR = 1.2
    DEFAULT_TRACKER = "csrt"
    INIT_METHODS = ["roi"]
    DEFAULT_INIT_METHOD = "roi"


SHOW_DAC_CONTROL = False


class SLIDE_POSITION:
    LOADING_X_MM = 30
    LOADING_Y_MM = 55
    SCANNING_X_MM = 3
    SCANNING_Y_MM = 3


class OUTPUT_GAINS:
    REFDIV = False
    CHANNEL0_GAIN = False
    CHANNEL1_GAIN = False
    CHANNEL2_GAIN = False
    CHANNEL3_GAIN = False
    CHANNEL4_GAIN = False
    CHANNEL5_GAIN = False
    CHANNEL6_GAIN = False
    CHANNEL7_GAIN = True


SLIDE_POTISION_SWITCHING_TIMEOUT_LIMIT_S = 10
SLIDE_POTISION_SWITCHING_HOME_EVERYTIME = False


class SOFTWARE_POS_LIMIT:
    X_POSITIVE = 56
    X_NEGATIVE = -0.5
    Y_POSITIVE = 56
    Y_NEGATIVE = -0.5
    Z_POSITIVE = 7
    Z_NEGATIVE = 0.05


SHOW_AUTOLEVEL_BTN = False
AUTOLEVEL_DEFAULT_SETTING = False

MULTIPOINT_AUTOFOCUS_CHANNEL = "BF LED matrix full"
# MULTIPOINT_AUTOFOCUS_CHANNEL = 'BF LED matrix left half'
MULTIPOINT_AUTOFOCUS_ENABLE_BY_DEFAULT = False
MULTIPOINT_BF_SAVING_OPTION = "Raw"
# MULTIPOINT_BF_SAVING_OPTION = 'RGB2GRAY'
# MULTIPOINT_BF_SAVING_OPTION = 'Green Channel Only'

DEFAULT_MULTIPOINT_NX = 1
DEFAULT_MULTIPOINT_NY = 1

ENABLE_FLEXIBLE_MULTIPOINT = True
USE_OVERLAP_FOR_FLEXIBLE = True
ENABLE_WELLPLATE_MULTIPOINT = True
ENABLE_RECORDING = False

RESUME_LIVE_AFTER_ACQUISITION = True

# When enabled, each multipoint acquisition will write a second log file scoped to that acquisition at:
#   <base_path>/<experiment_ID>/acquisition.log
ENABLE_PER_ACQUISITION_LOG = False

# Memory profiling - when enabled, shows real-time RAM usage in status bar during acquisition
# and logs periodic memory snapshots to help diagnose memory issues
ENABLE_MEMORY_PROFILING = True

# Simulated disk I/O for development (RAM/speed optimization)
# When enabled, images are encoded to memory buffers but NOT saved to disk
SIMULATED_DISK_IO_ENABLED = False
SIMULATED_DISK_IO_SPEED_MB_S = 200.0  # Target write speed in MB/s (HDD: 50-100, SATA SSD: 200-500, NVMe: 1000-3000)
SIMULATED_DISK_IO_COMPRESSION = True  # Exercise compression CPU/RAM for realistic simulation

# Per-component hardware simulation controls
# These settings only apply when running WITHOUT the --simulation flag.
# When --simulation is used, ALL components are simulated regardless of these settings.
# Values: False = use real hardware (default), True = simulate this component
SIMULATE_CAMERA = False
SIMULATE_MICROCONTROLLER = False  # Also controls stage (stage uses MCU)
SIMULATE_SPINNING_DISK = False  # XLight/Dragonfly
SIMULATE_FILTER_WHEEL = False
SIMULATE_OBJECTIVE_CHANGER = False
SIMULATE_LASER_AF_CAMERA = False  # Laser autofocus camera

# Acquisition Backpressure Settings
# Prevents RAM exhaustion when acquisition speed exceeds disk write speed
ACQUISITION_THROTTLING_ENABLED = True
ACQUISITION_MAX_PENDING_JOBS = 10  # Max jobs in flight before throttling
ACQUISITION_MAX_PENDING_MB = 2000.0  # Max pending MB before throttling
ACQUISITION_THROTTLE_TIMEOUT_S = 30.0  # Max wait time when throttled

CAMERA_SN = {"ch 1": "SN1", "ch 2": "SN2"}  # for multiple cameras, to be overwritten in the configuration file

ENABLE_STROBE_OUTPUT = False

ACQUISITION_PATTERN = "S-Pattern"  # 'S-Pattern', 'Unidirectional'
FOV_PATTERN = "Unidirectional"  # 'S-Pattern', 'Unidirectional'

Z_STACKING_CONFIG = "FROM BOTTOM"  # 'FROM BOTTOM', 'FROM TOP'
Z_STACKING_CONFIG_MAP = {0: "FROM BOTTOM", 1: "FROM CENTER", 2: "FROM TOP"}

DEFAULT_Z_POS_MM = 2

WELLPLATE_OFFSET_X_mm = 0  # x offset adjustment for using different plates
WELLPLATE_OFFSET_Y_mm = 0  # y offset adjustment for using different plates

# focus measure operator
FOCUS_MEASURE_OPERATOR = FocusMeasureOperator.LAPE

# controller version
CONTROLLER_VERSION = "Arduino Due"  # 'Teensy'

# How to read Spinnaker nodemaps, options are INDIVIDUAL or VALUE
CHOSEN_READ = "INDIVIDUAL"

# laser autofocus
SUPPORT_LASER_AUTOFOCUS = False
MAIN_CAMERA_MODEL = "MER2-1220-32U3M"
FOCUS_CAMERA_MODEL = "MER2-630-60U3M"
FOCUS_CAMERA_EXPOSURE_TIME_MS = 2
FOCUS_CAMERA_ANALOG_GAIN = 0
LASER_AF_AVERAGING_N = 3
LASER_AF_DISPLAY_SPOT_IMAGE = True
LASER_AF_CROP_WIDTH = 1536
LASER_AF_CROP_HEIGHT = 256
LASER_AF_SPOT_DETECTION_MODE = SpotDetectionMode.DUAL_LEFT.value
LASER_AF_RANGE = 100
DISPLACEMENT_SUCCESS_WINDOW_UM = 1.0
SPOT_CROP_SIZE = 100
CORRELATION_THRESHOLD = 0.7
PIXEL_TO_UM_CALIBRATION_DISTANCE = 6.0
LASER_AF_Y_WINDOW = 96
LASER_AF_X_WINDOW = 20
LASER_AF_MIN_PEAK_WIDTH = 10
LASER_AF_MIN_PEAK_DISTANCE = 10
LASER_AF_MIN_PEAK_PROMINENCE = 0.20
LASER_AF_SPOT_SPACING = 100
SHOW_LEGACY_DISPLACEMENT_MEASUREMENT_WINDOWS = False
LASER_AF_FILTER_SIGMA = None
LASER_AF_INITIALIZE_CROP_WIDTH = 1200
LASER_AF_INITIALIZE_CROP_HEIGHT = 800

MULTIPOINT_REFLECTION_AUTOFOCUS_ENABLE_BY_DEFAULT = False
MULTIPOINT_CONTRAST_AUTOFOCUS_ENABLE_BY_DEFAULT = False

RETRACT_OBJECTIVE_BEFORE_MOVING_TO_LOADING_POSITION = True
OBJECTIVE_RETRACTED_POS_MM = 0.1

TWO_CLASSIFICATION_MODELS = False
CLASSIFICATION_MODEL_PATH = "models/resnet18_en/version1/best.pt"
CLASSIFICATION_MODEL_PATH2 = "models/resnet18_en/version2/best.pt"
CLASSIFICATION_TEST_MODE = False
CLASSIFICATION_TH = 0.3

SEGMENTATION_MODEL_PATH = "models/m2unet_model_flat_erode1_wdecay5_smallbatch/model_4000_11.pth"
ENABLE_SEGMENTATION = True
USE_TRT_SEGMENTATION = False
SEGMENTATION_CROP = 1500

DISP_TH_DURING_MULTIPOINT = 0.95
SORT_DURING_MULTIPOINT = False

INVERTED_OBJECTIVE = False

ILLUMINATION_INTENSITY_FACTOR = 0.6

CAMERA_TYPE = "Default"
FOCUS_CAMERA_TYPE = "Default"

# Spinning disk confocal integration
ENABLE_SPINNING_DISK_CONFOCAL = False
USE_LDI_SERIAL_CONTROL = False
LDI_INTENSITY_MODE = "PC"
LDI_SHUTTER_MODE = "PC"
USE_CELESTA_ETHERNET_CONTROL = False
USE_ANDOR_LASER_CONTROL = False
ANDOR_LASER_VID = 0x1BDB
ANDOR_LASER_PID = 0x0300

XLIGHT_EMISSION_FILTER_MAPPING = {
    405: 1,
    470: 1,
    555: 1,
    640: 1,
    730: 1,
}  # TODO: This is not being used. Need to map wavelength to illumination source in LiveController
XLIGHT_SERIAL_NUMBER = "B00031BE"
XLIGHT_SLEEP_TIME_FOR_WHEEL = 0.25
XLIGHT_VALIDATE_WHEEL_POS = False
XLIGHT_ILLUMINATION_IRIS_DEFAULT = 100
XLIGHT_EMISSION_IRIS_DEFAULT = 100

# Dragonfly integration
USE_DRAGONFLY = False
DRAGONFLY_SERIAL_NUMBER = "00000000"

# Confocal.nl NL5 integration
ENABLE_NL5 = False
ENABLE_CELLX = False
CELLX_SN = None
CELLX_MODULATION = "EXT Digital"
NL5_USE_AOUT = False
NL5_USE_DOUT = True
NL5_TRIGGER_PIN = 2
NL5_WAVENLENGTH_MAP = {405: 1, 470: 2, 488: 2, 545: 3, 555: 3, 561: 3, 637: 4, 638: 4, 640: 4}

# Laser AF characterization mode
LASER_AF_CHARACTERIZATION_MODE = False

# Napari integration
USE_NAPARI_FOR_LIVE_VIEW = False
USE_NAPARI_FOR_MULTIPOINT = True
USE_NAPARI_FOR_MOSAIC_DISPLAY = True
USE_NAPARI_WELL_SELECTION = False
USE_NAPARI_FOR_LIVE_CONTROL = False
LIVE_ONLY_MODE = False

# NDViewer integration
ENABLE_NDVIEWER = False
MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM = 2

# Downsampled view settings (for Select Well Mode)
# SAVE_DOWNSAMPLED_WELL_IMAGES: Save individual well TIFFs (e.g., wells/A1_5um.tiff)
# DISPLAY_PLATE_VIEW: Show plate view tab in GUI during acquisition
# Note: Plate view TIFF (plate_10um.tiff) is always saved when either setting is enabled
SAVE_DOWNSAMPLED_WELL_IMAGES = False
DISPLAY_PLATE_VIEW = False
DOWNSAMPLED_WELL_RESOLUTIONS_UM = [5.0, 10.0, 20.0]
DOWNSAMPLED_PLATE_RESOLUTION_UM = 10.0  # Auto-added to DOWNSAMPLED_WELL_RESOLUTIONS_UM if not present
DOWNSAMPLED_Z_PROJECTION = ZProjectionMode.MIP
DOWNSAMPLED_INTERPOLATION_METHOD = DownsamplingMethod.INTER_AREA_FAST  # Balanced speed/quality default

# Downsampled view job timeouts
# DOWNSAMPLED_VIEW_JOB_TIMEOUT_S: Maximum time (seconds) to wait for all downsampled view
# jobs to complete at end of each timepoint. This timeout ensures acquisition doesn't hang
# indefinitely if a job gets stuck. For typical 96-well plates with 1-4 channels, 30 seconds
# is sufficient. For larger plates (384-well, 1536-well) with many channels, increase this
# value proportionally. As a rough guide: ~0.5s per well for processing, so 1536 wells
# could need up to ~800 seconds in worst case, though parallel processing makes it faster.
DOWNSAMPLED_VIEW_JOB_TIMEOUT_S = 30.0

# DOWNSAMPLED_VIEW_IDLE_TIMEOUT_S: Time (seconds) to wait after the last job result before
# assuming all jobs are complete. When the job input queue is empty but the last job may
# still be processing, we poll for results. If no new results arrive within this timeout,
# we assume all jobs have finished. 2 seconds is conservative - most jobs complete in
# <100ms, so this handles occasional slow jobs without adding unnecessary delay.
DOWNSAMPLED_VIEW_IDLE_TIMEOUT_S = 2.0

# Plate view zoom limits
# MIN_VISIBLE_PIXELS: At maximum zoom, ensure at least this many pixels are visible
# in the smallest dimension. 500 pixels allows inspecting cellular-level details.
PLATE_VIEW_MIN_VISIBLE_PIXELS = 500.0
# MAX_ZOOM_FACTOR: Cap zoom to prevent performance issues with large texture rendering.
# 10x is sufficient for most inspection tasks while maintaining smooth interaction.
PLATE_VIEW_MAX_ZOOM_FACTOR = 10.0

# Controller SN (needed when using multiple teensy-based connections)
CONTROLLER_SN = None

# Sci microscopy
SUPPORT_SCIMICROSCOPY_LED_ARRAY = False
SCIMICROSCOPY_LED_ARRAY_SN = None
SCIMICROSCOPY_LED_ARRAY_DISTANCE = 50
SCIMICROSCOPY_LED_ARRAY_DEFAULT_NA = 0.8
SCIMICROSCOPY_LED_ARRAY_DEFAULT_COLOR = [1, 1, 1]
SCIMICROSCOPY_LED_ARRAY_TURN_ON_DELAY = 0.03  # time to wait before trigger the camera (in seconds)

# Navigation Settings
ENABLE_CLICK_TO_MOVE_BY_DEFAULT = True

# Stitcher
IS_HCS = False
DYNAMIC_REGISTRATION = False
STITCH_COMPLETE_ACQUISITION = False

# Pseudo color settings
CHANNEL_COLORS_MAP = {
    "405": {"hex": 0x20ADF8, "name": "bop blue"},
    "488": {"hex": 0x1FFF00, "name": "green"},
    "561": {"hex": 0xFFCF00, "name": "yellow"},
    "638": {"hex": 0xFF0000, "name": "red"},
    "730": {"hex": 0x770000, "name": "dark red"},
    "R": {"hex": 0xFF0000, "name": "red"},
    "G": {"hex": 0x1FFF00, "name": "green"},
    "B": {"hex": 0x3300FF, "name": "blue"},
}
SAVE_IN_PSEUDO_COLOR = False
MERGE_CHANNELS = False

# Emission filter wheel
USE_EMISSION_FILTER_WHEEL = False
EMISSION_FILTER_WHEEL_TYPE = "SQUID"  # "SQUID", "ZABER", "OPTOSPIN"
EMISSION_FILTER_WHEEL_INDICES = [1]
# ZABER specific settings
ZABER_EMISSION_FILTER_WHEEL_DELAY_MS = 70
ZABER_EMISSION_FILTER_WHEEL_BLOCKING_CALL = False
# OPTOSPIN specific settings
FILTER_CONTROLLER_SERIAL_NUMBER = "A10NG007"  # used for both Zaber and Optospin for now
OPTOSPIN_EMISSION_FILTER_WHEEL_SPEED_HZ = 50
OPTOSPIN_EMISSION_FILTER_WHEEL_DELAY_MS = 70
OPTOSPIN_EMISSION_FILTER_WHEEL_TTL_TRIGGER = False
# SQUID specific settings
SQUID_FILTERWHEEL_MAX_INDEX = 8
SQUID_FILTERWHEEL_MIN_INDEX = 1
SQUID_FILTERWHEEL_OFFSET = 0.008
SQUID_FILTERWHEEL_MOTORSLOTINDEX = 3
SQUID_FILTERWHEEL_TRANSITIONS_PER_REVOLUTION = 4000

# Multi-wheel SQUID filter wheel configuration
# Motor slot 3 = W axis (first filter wheel), motor slot 4 = W2 axis (second filter wheel)
SQUID_FILTERWHEEL_CONFIGS = {
    1: {
        "motor_slot_index": 3,  # W axis
        "max_index": 8,
        "min_index": 1,
        "offset": 0.008,
        "transitions_per_revolution": 4000,
    },
    2: {
        "motor_slot_index": 4,  # W2 axis
        "max_index": 8,
        "min_index": 1,
        "offset": 0.008,
        "transitions_per_revolution": 4000,
    },
}

# Stage
USE_PRIOR_STAGE = False
PRIOR_STAGE_SN = ""

# camera blacklevel settings
DISPLAY_TOUPCAMER_BLACKLEVEL_SETTINGS = False


class HardwareTriggerMode:
    EDGE = 0  # Fixed pulse width (TRIGGER_PULSE_LENGTH_us)
    LEVEL = 1  # Variable pulse width (illumination_on_time)


HARDWARE_TRIGGER_MODE = HardwareTriggerMode.EDGE


def read_objectives_csv(file_path):
    objectives = {}
    with open(file_path, "r") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            objectives[row["name"]] = {
                "magnification": float(row["magnification"]),
                "NA": float(row["NA"]),
                "tube_lens_f_mm": float(row["tube_lens_f_mm"]),
            }
    return objectives


def read_sample_formats_csv(file_path):
    sample_formats = {}
    with open(file_path, "r") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            format_ = str(row["format"])
            format_key = f"{format_} well plate" if format_.isdigit() else format_
            sample_formats[format_key] = {
                "a1_x_mm": float(row["a1_x_mm"]),
                "a1_y_mm": float(row["a1_y_mm"]),
                "a1_x_pixel": int(row["a1_x_pixel"]),
                "a1_y_pixel": int(row["a1_y_pixel"]),
                "well_size_mm": float(row["well_size_mm"]),
                "well_spacing_mm": float(row["well_spacing_mm"]),
                "number_of_skip": int(row["number_of_skip"]),
                "rows": int(row["rows"]),
                "cols": int(row["cols"]),
            }
    return sample_formats


def load_formats():
    """Load formats, prioritizing cache for sample formats."""
    cache_path = "cache"
    default_path = "objective_and_sample_formats"

    # Load objectives (from default location)
    objectives = read_objectives_csv(os.path.join(default_path, "objectives.csv"))

    # Try cache first for sample formats, fall back to default if not found
    cached_formats_path = os.path.join(cache_path, "sample_formats.csv")
    default_formats_path = os.path.join(default_path, "sample_formats.csv")

    if os.path.exists(cached_formats_path):
        print("Using cached sample formats")
        sample_formats = read_sample_formats_csv(cached_formats_path)
    else:
        print("Using default sample formats")
        sample_formats = read_sample_formats_csv(default_formats_path)

    return objectives, sample_formats


OBJECTIVES_CSV_PATH = "objectives.csv"
SAMPLE_FORMATS_CSV_PATH = "sample_formats.csv"

OBJECTIVES, WELLPLATE_FORMAT_SETTINGS = load_formats()


def get_wellplate_settings(wellplate_format):
    if wellplate_format in WELLPLATE_FORMAT_SETTINGS:
        settings = WELLPLATE_FORMAT_SETTINGS[wellplate_format]
    elif wellplate_format == "0":
        settings = {
            "format": "0",
            "a1_x_mm": 0,
            "a1_y_mm": 0,
            "a1_x_pixel": 0,
            "a1_y_pixel": 0,
            "well_size_mm": 0,
            "well_spacing_mm": 0,
            "number_of_skip": 0,
            "rows": 1,
            "cols": 1,
        }
    else:
        raise ValueError(
            f"Invalid wellplate format: {wellplate_format}. Expected formats are: {list(WELLPLATE_FORMAT_SETTINGS.keys())} or '0'"
        )
    return settings


# limit switch
X_HOME_SWITCH_POLARITY = LIMIT_SWITCH_POLARITY.X_HOME
Y_HOME_SWITCH_POLARITY = LIMIT_SWITCH_POLARITY.Y_HOME
Z_HOME_SWITCH_POLARITY = LIMIT_SWITCH_POLARITY.Z_HOME

# home safety margin with (um) unit
X_HOME_SAFETY_MARGIN_UM = 50
Y_HOME_SAFETY_MARGIN_UM = 50
Z_HOME_SAFETY_MARGIN_UM = 100

# safety homing point (um) unit
# after homing process, move axis to the
# safety point
X_HOME_SAFETY_POINT = 0
Y_HOME_SAFETY_POINT = 0
Z_HOME_SAFETY_POINT = 1200

USE_XERYON = False
XERYON_SERIAL_NUMBER = "95130303033351E02050"
XERYON_SPEED = 80
XERYON_OBJECTIVE_SWITCHER_POS_1 = ["4x", "10x"]
XERYON_OBJECTIVE_SWITCHER_POS_2 = ["20x", "40x", "60x"]
XERYON_OBJECTIVE_SWITCHER_POS_2_OFFSET_MM = 2

# fluidics
RUN_FLUIDICS = False
FLUIDICS_CONFIG_PATH = "./merfish_config/MERFISH_config.json"

USE_TEMPLATE_MULTIPOINT = False

FILE_SAVING_OPTION = FileSavingOption.INDIVIDUAL_IMAGES

# Zarr v3 saving configuration
ZARR_CHUNK_MODE = ZarrChunkMode.FULL_FRAME
ZARR_COMPRESSION = ZarrCompression.FAST  # Safe for 10-20 fps, ~1000 MB/s encode

# Use 6D array with FOV dimension for non-HCS acquisitions (non-standard, not OME-NGFF compliant)
# When False (default): creates per-FOV 5D zarr files (OME-NGFF compliant)
# When True: creates single 6D zarr with shape (FOV, T, C, Z, Y, X)
ZARR_USE_6D_FOV_DIMENSION = False

##########################################################
#### start of loading machine specific configurations ####
##########################################################
CACHED_CONFIG_FILE_PATH = None

# Piezo configuration items
Z_MOTOR_CONFIG = ZMotorConfig.STEPPER

# the value of OBJECTIVE_PIEZO_CONTROL_VOLTAGE_RANGE is 2.5 or 5
OBJECTIVE_PIEZO_CONTROL_VOLTAGE_RANGE = 5
OBJECTIVE_PIEZO_RANGE_UM = 300
OBJECTIVE_PIEZO_HOME_UM = 20
OBJECTIVE_PIEZO_FLIP_DIR = False

MULTIPOINT_PIEZO_DELAY_MS = 20
MULTIPOINT_PIEZO_UPDATE_DISPLAY = True

USE_TERMINAL_CONSOLE = False
USE_JUPYTER_CONSOLE = False

# MCP Control Server - allows external tools (like Claude Code) to control the microscope
# When enabled, MCP-related menu items appear in Settings (Launch Claude Code, etc.)
# The server itself starts on-demand when user clicks "Launch Claude Code" or enables it manually.
# Security note: Server listens only on localhost (127.0.0.1).
# The python_exec command is disabled by default and must be explicitly enabled in the GUI.
ENABLE_MCP_SERVER_SUPPORT = True  # Set to False to hide all MCP-related menu items
CONTROL_SERVER_HOST = "127.0.0.1"
CONTROL_SERVER_PORT = 5050


# Slack Notifications - send real-time notifications during acquisition
class SlackNotifications:
    ENABLED = False
    BOT_TOKEN = None  # Slack Bot Token (xoxb-...) for API access
    CHANNEL_ID = None  # Slack Channel ID (C...) to post to
    NOTIFY_ON_ERROR = True
    NOTIFY_ON_TIMEPOINT_COMPLETE = True
    NOTIFY_ON_ACQUISITION_START = False
    NOTIFY_ON_ACQUISITION_FINISHED = True
    SEND_MOSAIC_SNAPSHOTS = True


try:
    with open("cache/config_file_path.txt", "r") as file:
        for line in file:
            CACHED_CONFIG_FILE_PATH = line
            break
except FileNotFoundError:
    CACHED_CONFIG_FILE_PATH = None

config_files = glob.glob("." + "/" + "configuration*.ini")
if config_files:
    if len(config_files) > 1:
        if CACHED_CONFIG_FILE_PATH in config_files:
            log.info(f"defaulting to last cached config file at '{CACHED_CONFIG_FILE_PATH}'")
            config_files = [CACHED_CONFIG_FILE_PATH]
        else:
            log.error("multiple machine configuration files found, the program will exit")
            sys.exit(1)
    log.info("load machine-specific configuration")
    # exec(open(config_files[0]).read())
    cfp = ConfigParser()
    cfp.read(config_files[0])
    var_items = list(locals().keys())
    for var_name in var_items:
        if type(locals()[var_name]) is type:
            continue
        varnamelower = var_name.lower()
        if varnamelower not in cfp.options("GENERAL"):
            continue
        value = cfp.get("GENERAL", varnamelower)
        actualvalue = conf_attribute_reader(value)
        locals()[var_name] = actualvalue
    for classkey in var_items:
        myclass = None
        classkeyupper = classkey.upper()
        pop_items = None
        try:
            pop_items = cfp.items(classkeyupper)
        except:
            continue
        if type(locals()[classkey]) is not type:
            continue
        myclass = locals()[classkey]
        populate_class_from_dict(myclass, pop_items)

    with open("cache/config_file_path.txt", "w") as file:
        file.write(config_files[0])
    CACHED_CONFIG_FILE_PATH = config_files[0]
else:
    log.warning("configuration*.ini file not found, defaulting to legacy configuration")
    config_files = glob.glob("." + "/" + "configuration*.txt")
    if config_files:
        if len(config_files) > 1:
            log.error("multiple machine configuration files found, the program will exit")
            sys.exit(1)
        log.info("load machine-specific configuration")
        exec(open(config_files[0]).read())
    else:
        log.error("machine-specific configuration not present, the program will exit")
        sys.exit(1)

try:
    with open("cache/objective_and_sample_format.txt", "r") as f:
        cached_settings = json.load(f)
        DEFAULT_OBJECTIVE = (
            cached_settings.get("objective") if cached_settings.get("objective") in OBJECTIVES else "20x"
        )
        WELLPLATE_FORMAT = str(cached_settings.get("wellplate_format"))
        WELLPLATE_FORMAT = WELLPLATE_FORMAT + " well plate" if WELLPLATE_FORMAT.isdigit() else WELLPLATE_FORMAT
        if WELLPLATE_FORMAT not in WELLPLATE_FORMAT_SETTINGS:
            WELLPLATE_FORMAT = "96 well plate"
except (FileNotFoundError, json.JSONDecodeError):
    DEFAULT_OBJECTIVE = "20x"
    WELLPLATE_FORMAT = "96 well plate"

NUMBER_OF_SKIP = WELLPLATE_FORMAT_SETTINGS[WELLPLATE_FORMAT][
    "number_of_skip"
]  # num rows/cols to skip on wellplate edge
WELL_SIZE_MM = WELLPLATE_FORMAT_SETTINGS[WELLPLATE_FORMAT]["well_size_mm"]
WELL_SPACING_MM = WELLPLATE_FORMAT_SETTINGS[WELLPLATE_FORMAT]["well_spacing_mm"]
A1_X_MM = WELLPLATE_FORMAT_SETTINGS[WELLPLATE_FORMAT]["a1_x_mm"]  # measured stage position - to update
A1_Y_MM = WELLPLATE_FORMAT_SETTINGS[WELLPLATE_FORMAT]["a1_y_mm"]  # measured stage position - to update
A1_X_PIXEL = WELLPLATE_FORMAT_SETTINGS[WELLPLATE_FORMAT]["a1_x_pixel"]  # coordinate on the png
A1_Y_PIXEL = WELLPLATE_FORMAT_SETTINGS[WELLPLATE_FORMAT]["a1_y_pixel"]  # coordinate on the png

##########################################################
##### end of loading machine specific configurations #####
##########################################################

# objective piezo
Z_MOTOR_CONFIG = ZMotorConfig.convert_to_enum(Z_MOTOR_CONFIG)
HAS_OBJECTIVE_PIEZO = Z_MOTOR_CONFIG.has_piezo()
IS_PIEZO_ONLY = Z_MOTOR_CONFIG.is_piezo_only()
MULTIPOINT_USE_PIEZO_FOR_ZSTACKS = HAS_OBJECTIVE_PIEZO

# convert str to enum
FILE_SAVING_OPTION = FileSavingOption.convert_to_enum(FILE_SAVING_OPTION)
ZARR_CHUNK_MODE = ZarrChunkMode.convert_to_enum(ZARR_CHUNK_MODE)
ZARR_COMPRESSION = ZarrCompression.convert_to_enum(ZARR_COMPRESSION)
FOCUS_MEASURE_OPERATOR = FocusMeasureOperator.convert_to_enum(FOCUS_MEASURE_OPERATOR)
DEFAULT_TRIGGER_MODE = TriggerMode.convert_to_var(DEFAULT_TRIGGER_MODE)

# saving path
if not (DEFAULT_SAVING_PATH.startswith(str(Path.home()))):
    DEFAULT_SAVING_PATH = str(Path.home()) + "/" + DEFAULT_SAVING_PATH.strip("/")

# Load Views settings from config file at startup
# These values override the defaults above and are accessed via control._def.XXX
if CACHED_CONFIG_FILE_PATH and os.path.exists(CACHED_CONFIG_FILE_PATH):
    try:
        _views_config = ConfigParser()
        _views_config.read(CACHED_CONFIG_FILE_PATH)
        if _views_config.has_section("VIEWS"):
            log.info("Loading Views settings from config file")
            if _views_config.has_option("VIEWS", "display_plate_view"):
                DISPLAY_PLATE_VIEW = _views_config.get("VIEWS", "display_plate_view").lower() in ("true", "1", "yes")
            if _views_config.has_option("VIEWS", "display_mosaic_view"):
                USE_NAPARI_FOR_MOSAIC_DISPLAY = _views_config.get("VIEWS", "display_mosaic_view").lower() in (
                    "true",
                    "1",
                    "yes",
                )
            # Support both old and new config key names for backward compatibility
            if _views_config.has_option("VIEWS", "save_downsampled_well_images"):
                SAVE_DOWNSAMPLED_WELL_IMAGES = _views_config.get("VIEWS", "save_downsampled_well_images").lower() in (
                    "true",
                    "1",
                    "yes",
                )
            elif _views_config.has_option("VIEWS", "generate_downsampled_well_images"):
                # Legacy config key
                SAVE_DOWNSAMPLED_WELL_IMAGES = _views_config.get(
                    "VIEWS", "generate_downsampled_well_images"
                ).lower() in ("true", "1", "yes")
            if _views_config.has_option("VIEWS", "downsampled_well_resolutions_um"):
                try:
                    _res_str = _views_config.get("VIEWS", "downsampled_well_resolutions_um")
                    DOWNSAMPLED_WELL_RESOLUTIONS_UM = [float(x.strip()) for x in _res_str.split(",") if x.strip()]
                except ValueError:
                    pass
            if _views_config.has_option("VIEWS", "downsampled_plate_resolution_um"):
                try:
                    DOWNSAMPLED_PLATE_RESOLUTION_UM = _views_config.getfloat("VIEWS", "downsampled_plate_resolution_um")
                except ValueError:
                    pass
            if _views_config.has_option("VIEWS", "downsampled_z_projection"):
                try:
                    DOWNSAMPLED_Z_PROJECTION = ZProjectionMode.convert_to_enum(
                        _views_config.get("VIEWS", "downsampled_z_projection")
                    )
                except ValueError:
                    pass
            if _views_config.has_option("VIEWS", "downsampled_interpolation_method"):
                try:
                    DOWNSAMPLED_INTERPOLATION_METHOD = DownsamplingMethod.convert_to_enum(
                        _views_config.get("VIEWS", "downsampled_interpolation_method")
                    )
                except ValueError:
                    pass
            if _views_config.has_option("VIEWS", "mosaic_view_target_pixel_size_um"):
                try:
                    MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM = _views_config.getfloat(
                        "VIEWS", "mosaic_view_target_pixel_size_um"
                    )
                except ValueError:
                    pass
            if _views_config.has_option("VIEWS", "enable_ndviewer"):
                ENABLE_NDVIEWER = _views_config.get("VIEWS", "enable_ndviewer").lower() in ("true", "1", "yes")
    except Exception as e:
        log.warning(f"Failed to load Views settings from config: {e}")

    # Load GENERAL settings from config file
    try:
        _general_config = ConfigParser()
        _general_config.read(CACHED_CONFIG_FILE_PATH)
        if _general_config.has_section("GENERAL"):
            if _general_config.has_option("GENERAL", "enable_memory_profiling"):
                ENABLE_MEMORY_PROFILING = _general_config.get("GENERAL", "enable_memory_profiling").lower() in (
                    "true",
                    "1",
                    "yes",
                )
                log.info(f"Loaded ENABLE_MEMORY_PROFILING={ENABLE_MEMORY_PROFILING} from config")
    except Exception as e:
        log.warning(f"Failed to load GENERAL settings from config: {e}")

    # Load per-component simulation settings from config file
    def _parse_sim_setting(value_str):
        """Parse simulation setting: True (simulate) or False (real hardware)."""
        val = value_str.strip().lower()
        if val in ("true", "1", "yes", "simulate"):
            return True
        # Everything else (false, none, auto, unrecognized) = real hardware
        return False

    try:
        _sim_config = ConfigParser()
        _sim_config.read(CACHED_CONFIG_FILE_PATH)
        if _sim_config.has_section("SIMULATION"):
            if _sim_config.has_option("SIMULATION", "simulate_camera"):
                SIMULATE_CAMERA = _parse_sim_setting(_sim_config.get("SIMULATION", "simulate_camera"))
                log.info(f"Loaded SIMULATE_CAMERA={SIMULATE_CAMERA} from config")
            if _sim_config.has_option("SIMULATION", "simulate_microcontroller"):
                SIMULATE_MICROCONTROLLER = _parse_sim_setting(_sim_config.get("SIMULATION", "simulate_microcontroller"))
                log.info(f"Loaded SIMULATE_MICROCONTROLLER={SIMULATE_MICROCONTROLLER} from config")
            if _sim_config.has_option("SIMULATION", "simulate_spinning_disk"):
                SIMULATE_SPINNING_DISK = _parse_sim_setting(_sim_config.get("SIMULATION", "simulate_spinning_disk"))
                log.info(f"Loaded SIMULATE_SPINNING_DISK={SIMULATE_SPINNING_DISK} from config")
            if _sim_config.has_option("SIMULATION", "simulate_filter_wheel"):
                SIMULATE_FILTER_WHEEL = _parse_sim_setting(_sim_config.get("SIMULATION", "simulate_filter_wheel"))
                log.info(f"Loaded SIMULATE_FILTER_WHEEL={SIMULATE_FILTER_WHEEL} from config")
            if _sim_config.has_option("SIMULATION", "simulate_objective_changer"):
                SIMULATE_OBJECTIVE_CHANGER = _parse_sim_setting(
                    _sim_config.get("SIMULATION", "simulate_objective_changer")
                )
                log.info(f"Loaded SIMULATE_OBJECTIVE_CHANGER={SIMULATE_OBJECTIVE_CHANGER} from config")
            if _sim_config.has_option("SIMULATION", "simulate_laser_af_camera"):
                SIMULATE_LASER_AF_CAMERA = _parse_sim_setting(_sim_config.get("SIMULATION", "simulate_laser_af_camera"))
                log.info(f"Loaded SIMULATE_LASER_AF_CAMERA={SIMULATE_LASER_AF_CAMERA} from config")
    except Exception as e:
        log.warning(f"Failed to load SIMULATION settings from config: {e}")
