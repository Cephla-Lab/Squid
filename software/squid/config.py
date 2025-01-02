import enum
import math
from typing import Optional

import pydantic

import control._def as _def


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

"""
Returns the StageConfig that existed at process startup.
"""


def get_stage_config():
    return _stage_config
