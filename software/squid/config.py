import enum
import math
from typing import Optional, Tuple, Union

import pydantic

import control._def as _def
from control.utils import FlipVariant


class DirectionSign(enum.IntEnum):
    DIRECTION_SIGN_POSITIVE = 1
    DIRECTION_SIGN_NEGATIVE = -1


class PIDConfig(pydantic.BaseModel):
    ENABLED: bool
    P: float
    I: float
    D: float


class AxisConfig(pydantic.BaseModel):
    MOVEMENT_SIGN: DirectionSign
    USE_ENCODER: bool
    ENCODER_SIGN: DirectionSign
    # If this is a linear axis, this is the distance the axis must move to see 1 encoder step.  If this
    # is a rotary axis, this is the radians travelled by the axis to see 1 encoder step.
    ENCODER_STEP_SIZE: float
    FULL_STEPS_PER_REV: float

    # For linear axes, this is the mm traveled by the axis when 1 full step is taken by the motor.  For rotary
    # axes, this is the rad traveled by the axis when 1 full step is taken by the motor.
    SCREW_PITCH: float

    # The number of microsteps per full step the axis uses (or should use if we can set it).
    # If MICROSTEPS_PER_STEP == 8, and SCREW_PITCH=2, then in 8 commanded steps the motor will do 1 full
    # step and so will travel a distance of 2.
    MICROSTEPS_PER_STEP: int

    # The Max speed the axis is allowed to travel in denoted in its native units.  This means mm/s for
    # linear axes, and radians/s for rotary axes.
    MAX_SPEED: float
    MAX_ACCELERATION: float

    # The min and maximum position of this axis in its native units.  This means mm for linear axes, and
    # radians for rotary.  `inf` is allowed (for something like a continuous rotary axis)
    MIN_POSITION: float
    MAX_POSITION: float

    # Some axes have a PID controller.  This says whether or not to use the PID control loop, and if so what
    # gains to use.
    PID: Optional[PIDConfig]

    def convert_to_real_units(self, usteps: float):
        if self.USE_ENCODER:
            return usteps * self.MOVEMENT_SIGN.value * self.ENCODER_STEP_SIZE * self.ENCODER_SIGN.value
        else:
            return (
                usteps
                * self.MOVEMENT_SIGN.value
                * self.SCREW_PITCH
                / (self.MICROSTEPS_PER_STEP * self.FULL_STEPS_PER_REV)
            )

    def convert_real_units_to_ustep(self, real_unit: float):
        return round(
            real_unit
            / (self.MOVEMENT_SIGN.value * self.SCREW_PITCH / (self.MICROSTEPS_PER_STEP * self.FULL_STEPS_PER_REV))
        )


class StageConfig(pydantic.BaseModel):
    X_AXIS: AxisConfig
    Y_AXIS: AxisConfig
    Z_AXIS: AxisConfig
    THETA_AXIS: AxisConfig


# NOTE(imo): This is temporary until we can just pass in instances of AxisConfig wherever we need it.  Having
# this getter for the temporary singleton will help with the refactor once we can get rid of it.
_stage_config = StageConfig(
    X_AXIS=AxisConfig(
        MOVEMENT_SIGN=_def.STAGE_MOVEMENT_SIGN_X,
        USE_ENCODER=_def.USE_ENCODER_X,
        ENCODER_SIGN=_def.ENCODER_POS_SIGN_X,
        ENCODER_STEP_SIZE=_def.ENCODER_STEP_SIZE_X_MM,
        FULL_STEPS_PER_REV=_def.FULLSTEPS_PER_REV_X,
        SCREW_PITCH=_def.SCREW_PITCH_X_MM,
        MICROSTEPS_PER_STEP=_def.MICROSTEPPING_DEFAULT_X,
        MAX_SPEED=_def.MAX_VELOCITY_X_mm,
        MAX_ACCELERATION=_def.MAX_ACCELERATION_X_mm,
        MIN_POSITION=_def.SOFTWARE_POS_LIMIT.X_NEGATIVE,
        MAX_POSITION=_def.SOFTWARE_POS_LIMIT.X_POSITIVE,
        PID=None,
    ),
    Y_AXIS=AxisConfig(
        MOVEMENT_SIGN=_def.STAGE_MOVEMENT_SIGN_Y,
        USE_ENCODER=_def.USE_ENCODER_Y,
        ENCODER_SIGN=_def.ENCODER_POS_SIGN_Y,
        ENCODER_STEP_SIZE=_def.ENCODER_STEP_SIZE_Y_MM,
        FULL_STEPS_PER_REV=_def.FULLSTEPS_PER_REV_Y,
        SCREW_PITCH=_def.SCREW_PITCH_Y_MM,
        MICROSTEPS_PER_STEP=_def.MICROSTEPPING_DEFAULT_Y,
        MAX_SPEED=_def.MAX_VELOCITY_Y_mm,
        MAX_ACCELERATION=_def.MAX_ACCELERATION_Y_mm,
        MIN_POSITION=_def.SOFTWARE_POS_LIMIT.Y_NEGATIVE,
        MAX_POSITION=_def.SOFTWARE_POS_LIMIT.Y_POSITIVE,
        PID=None,
    ),
    Z_AXIS=AxisConfig(
        MOVEMENT_SIGN=_def.STAGE_MOVEMENT_SIGN_Z,
        USE_ENCODER=_def.USE_ENCODER_Z,
        ENCODER_SIGN=_def.ENCODER_POS_SIGN_Z,
        ENCODER_STEP_SIZE=_def.ENCODER_STEP_SIZE_Z_MM,
        FULL_STEPS_PER_REV=_def.FULLSTEPS_PER_REV_Z,
        SCREW_PITCH=_def.SCREW_PITCH_Z_MM,
        MICROSTEPS_PER_STEP=_def.MICROSTEPPING_DEFAULT_Z,
        MAX_SPEED=_def.MAX_VELOCITY_Z_mm,
        MAX_ACCELERATION=_def.MAX_ACCELERATION_Z_mm,
        MIN_POSITION=_def.SOFTWARE_POS_LIMIT.Z_NEGATIVE,
        MAX_POSITION=_def.SOFTWARE_POS_LIMIT.Z_POSITIVE,
        PID=None,
    ),
    THETA_AXIS=AxisConfig(
        MOVEMENT_SIGN=_def.STAGE_MOVEMENT_SIGN_THETA,
        USE_ENCODER=_def.USE_ENCODER_THETA,
        ENCODER_SIGN=_def.ENCODER_POS_SIGN_THETA,
        ENCODER_STEP_SIZE=_def.ENCODER_STEP_SIZE_THETA,
        FULL_STEPS_PER_REV=_def.FULLSTEPS_PER_REV_THETA,
        SCREW_PITCH=2.0 * math.pi / _def.FULLSTEPS_PER_REV_THETA,
        MICROSTEPS_PER_STEP=_def.MICROSTEPPING_DEFAULT_Y,
        MAX_SPEED=2.0
        * math.pi
        / 4,  # NOTE(imo): I arbitrarily guessed this at 4 sec / rev, so it probably needs adjustment.
        MAX_ACCELERATION=_def.MAX_ACCELERATION_X_mm,
        MIN_POSITION=0,  # NOTE(imo): Min and Max need adjusting.  They are arbitrary right now!
        MAX_POSITION=2.0 * math.pi / 4,
        PID=None,
    ),
)


def get_stage_config() -> StageConfig:
    """
    Returns the StageConfig that existed at process startup.
    """
    return _stage_config


class CameraVariant(enum.Enum):
    TOUPCAM = "TOUPCAM"
    FLIR = "FLIR"
    HAMAMATSU = "HAMAMATSU"
    IDS = "IDS"
    TUCSEN = "TUCSEN"
    PHOTOMETRICS = "PHOTOMETRICS"
    TIS = "TIS"
    GXIPY = "GXIPY"
    ANDOR = "ANDOR"

    @staticmethod
    def from_string(cam_string: str) -> Optional["CameraVariant"]:
        """
        Attempts to convert the given string to a camera variant.  This ignores all letter cases.
        """
        try:
            return CameraVariant[cam_string.upper()]
        except KeyError:
            return None


class GxipyCameraModel(enum.Enum):
    MER2_1220_32U3M = "MER2-1220-32U3M"
    MER2_1220_32U3C = "MER2-1220-32U3C"
    MER2_630_60U3M = "MER2-630-60U3M"

    @staticmethod
    def from_string(cam_string: str) -> Optional["GxipyCameraModel"]:
        """
        Attempts to convert the given string to a Gxipy camera model.  This ignores all letter cases.
        """
        try:
            return GxipyCameraModel[cam_string.upper()]
        except KeyError:
            return None


class ToupcamCameraModel(enum.Enum):
    ITR3CMOS26000KMA = "ITR3CMOS26000KMA"
    ITR3CMOS09000KMA = "ITR3CMOS09000KMA"
    ITR3CMOS26000KPA = "ITR3CMOS26000KPA"

    @staticmethod
    def from_string(cam_string: str) -> Optional["ToupcamCameraModel"]:
        """
        Attempts to convert the given string to a Toupcam camera model.  This ignores all letter cases.
        """
        try:
            return ToupcamCameraModel[cam_string.upper()]
        except KeyError:
            return None


class TucsenCameraModel(enum.Enum):
    FL26_BW = "FL26-BW"
    DHYANA_400BSI_V3 = "DHYANA-400BSI-V3"
    ARIES_6506 = "ARIES-6506"
    ARIES_6510 = "ARIES-6510"

    @staticmethod
    def from_string(cam_string: str) -> Optional["TucsenCameraModel"]:
        """
        Attempts to convert the given string to a Tucsen camera model.  This ignores all letter cases.
        """
        try:
            return TucsenCameraModel[cam_string.upper()]
        except KeyError:
            return None


class HamamatsuCameraModel(enum.Enum):
    C15440_20UP = "C15440-20UP"
    C14440_20UP = "C14440-20UP"

    @staticmethod
    def from_string(cam_string: str) -> Optional["HamamatsuCameraModel"]:
        """
        Attempts to convert the given string to a Hamamatsu camera model.  This ignores all letter cases.
        """
        try:
            return HamamatsuCameraModel[cam_string.upper()]
        except KeyError:
            return None


class PhotometricsCameraModel(enum.Enum):
    KINETIX = "KINETIX"

    @staticmethod
    def from_string(cam_string: str) -> Optional["PhotometricsCameraModel"]:
        """
        Attempts to convert the given string to a Photometrics camera model.  This ignores all letter cases.
        """
        try:
            return PhotometricsCameraModel[cam_string.upper()]
        except KeyError:
            return None


class AndorCameraModel(enum.Enum):
    ZYLA_4_2P_USB3_C = "ZYLA-4.2P-USB3-C"  # ZL41 Cell 4.2

    @staticmethod
    def from_string(cam_string: str) -> Optional["AndorCameraModel"]:
        """
        Attempts to convert the given string to an Andor camera model.  This ignores all letter cases.
        """
        try:
            return AndorCameraModel[cam_string.upper()]
        except KeyError:
            return None


class CameraSensor(enum.Enum):
    """
    Some camera sensors may not be included here.
    """

    IMX290 = "IMX290"
    IMX178 = "IMX178"
    IMX226 = "IMX226"
    IMX250 = "IMX250"
    IMX252 = "IMX252"
    IMX273 = "IMX273"
    IMX264 = "IMX264"
    IMX265 = "IMX265"
    IMX571 = "IMX571"
    PYTHON300 = "PYTHON300"


class CameraPixelFormat(enum.Enum):
    """
    This is all known Pixel Formats in the Cephla world, but not all cameras will support
    all of these.
    """

    MONO8 = "MONO8"
    MONO10 = "MONO10"
    MONO12 = "MONO12"
    MONO14 = "MONO14"
    MONO16 = "MONO16"
    RGB24 = "RGB24"
    RGB32 = "RGB32"
    RGB48 = "RGB48"
    BAYER_RG8 = "BAYER_RG8"
    BAYER_RG12 = "BAYER_RG12"

    @staticmethod
    def is_color_format(pixel_format):
        return pixel_format in (
            CameraPixelFormat.RGB24,
            CameraPixelFormat.RGB32,
            CameraPixelFormat.RGB48,
            CameraPixelFormat.BAYER_RG8,
            CameraPixelFormat.BAYER_RG12,
        )

    @staticmethod
    def from_string(pixel_format_string):
        return CameraPixelFormat[pixel_format_string]


class RGBValue(pydantic.BaseModel):
    r: float
    g: float
    b: float


# TODO/NOTE(imo): We may need to add a model attrib here.
class CameraConfig(pydantic.BaseModel):
    """
    Most camera parameters are runtime configurable, so CameraConfig is more about defining what
    camera must be available and used for a particular function in the system.

    If we want to capture the settings a camera used for a particular capture, another model called
    CameraState, or something, might be more appropriate.
    """

    # NOTE(imo): Not "type" because that's a python builtin and can cause confusion
    camera_type: CameraVariant

    # Specific camera model. This will be used to determine the model-specific parameters, because one camera class may
    # support multiple models from the same brand.
    camera_model: Optional[
        Union[
            GxipyCameraModel,
            TucsenCameraModel,
            ToupcamCameraModel,
            HamamatsuCameraModel,
            PhotometricsCameraModel,
            AndorCameraModel,
        ]
    ] = None

    # The serial number of the camera. You may use this to select a specific camera to open if there are multiple
    # cameras using the same SDK/driver.
    serial_number: Optional[str] = None

    # The default readout data bit depth of the camera. Note that this may depend on the gain mode being used.
    default_pixel_format: CameraPixelFormat

    # The binning factor of the camera.  If None, the camera is not using binning, or use 1x1 as default.
    default_binning: Optional[Tuple[int, int]] = None

    # The default ROI of the camera for hardware cropping. Input should be: offset_x, offset_y, width, height
    default_roi: Optional[Tuple[Optional[int], Optional[int], Optional[int], Optional[int]]] = None

    # The angle the camera should rotate this image right as it comes off the camera,
    # and before giving it to the rest of the system.
    #
    # NOTE(imo): As of 2025-feb-17, this feature is inconsistently implemented!
    rotate_image_angle: Optional[float] = None

    # After rotation, the flip we should do to the image.
    #
    # NOTE(imo): As of 2025-feb-17, this feature is inconsistently implemented!
    flip: Optional[FlipVariant] = None

    # The width of the crop region of the camera. This will be used for cropping the image in software. Value should be relative to the unbinned image size.
    crop_width: Optional[int] = None

    # The height of the crop region of the camera. This will be used for cropping the image in software. Value should be relative to the unbinned image size.
    crop_height: Optional[int] = None

    # Set the temperature of the camera to this value once on initialization.
    default_temperature: Optional[float] = None

    # Set the fan speed of the camera to this value once on initialization.
    default_fan_speed: Optional[int] = None

    # Set the black level of the camera to this value once on initialization.
    default_black_level: Optional[int] = None

    # After initialization, set the white balance gains to this once. Only valid for color cameras.
    default_white_balance_gains: Optional[RGBValue] = None


def _old_camera_variant_to_enum(old_string) -> CameraVariant:
    if old_string == "Toupcam":
        return CameraVariant.TOUPCAM
    elif old_string == "FLIR":
        return CameraVariant.FLIR
    elif old_string == "Hamamatsu":
        return CameraVariant.HAMAMATSU
    elif old_string == "iDS":
        return CameraVariant.IDS
    elif old_string == "TIS":
        return CameraVariant.TIS
    elif old_string == "Tucsen":
        return CameraVariant.TUCSEN
    elif old_string == "Photometrics":
        return CameraVariant.PHOTOMETRICS
    elif old_string == "Andor":
        return CameraVariant.ANDOR
    elif old_string == "Default":
        return CameraVariant.GXIPY
    raise ValueError(f"Unknown old camera type {old_string=}")


_camera_config = CameraConfig(
    camera_type=_old_camera_variant_to_enum(_def.CAMERA_TYPE),
    camera_model=_def.MAIN_CAMERA_MODEL,
    default_pixel_format=_def.CAMERA_CONFIG.PIXEL_FORMAT_DEFAULT,
    default_binning=(_def.CAMERA_CONFIG.BINNING_FACTOR_DEFAULT, _def.CAMERA_CONFIG.BINNING_FACTOR_DEFAULT),
    default_roi=(
        _def.CAMERA_CONFIG.ROI_OFFSET_X_DEFAULT,
        _def.CAMERA_CONFIG.ROI_OFFSET_Y_DEFAULT,
        _def.CAMERA_CONFIG.ROI_WIDTH_DEFAULT,
        _def.CAMERA_CONFIG.ROI_HEIGHT_DEFAULT,
    ),
    rotate_image_angle=_def.CAMERA_CONFIG.ROTATE_IMAGE_ANGLE,
    flip=_def.CAMERA_CONFIG.FLIP_IMAGE,
    crop_width=_def.CAMERA_CONFIG.CROP_WIDTH_UNBINNED,
    crop_height=_def.CAMERA_CONFIG.CROP_HEIGHT_UNBINNED,
    default_temperature=_def.CAMERA_CONFIG.TEMPERATURE_DEFAULT,
    default_fan_speed=_def.CAMERA_CONFIG.FAN_SPEED_DEFAULT,
    default_black_level=_def.CAMERA_CONFIG.BLACKLEVEL_VALUE_DEFAULT,
    default_white_balance_gains=RGBValue(
        r=_def.CAMERA_CONFIG.AWB_RATIOS_R, g=_def.CAMERA_CONFIG.AWB_RATIOS_G, b=_def.CAMERA_CONFIG.AWB_RATIOS_B
    ),
)


def get_camera_config() -> CameraConfig:
    """
    Returns the CameraConfig that existed at process startup.
    """
    print(f"get_camera_config: {_camera_config}")
    return _camera_config


_autofocus_camera_config = CameraConfig(
    camera_type=_old_camera_variant_to_enum(_def.FOCUS_CAMERA_TYPE),
    camera_model=_def.FOCUS_CAMERA_MODEL,
    default_pixel_format=CameraPixelFormat.MONO8,
    default_binning=(1, 1),
    rotate_image_angle=None,
    flip=None,
)


def get_autofocus_camera_config() -> CameraConfig:
    """
    Returns the CameraConfig that existed at startup for the laser autofocus system.
    """
    return _autofocus_camera_config
