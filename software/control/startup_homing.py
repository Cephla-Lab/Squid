"""Rules for homing the stage on systems where the software cannot lower the objective."""

import enum
import hashlib

import control._def

OBJECTIVE_LOWERED_PROMPT = (
    "Turn the focus knob to lower the objective clear of the sample holder, then click Home. "
    "The stage will move to its X and Y limit switches."
)


class ObjectiveNotLoweredError(RuntimeError):
    """An XY home was requested on a piezo-only system without the per-operation confirmation."""


def require_objective_lowered(objective_lowered: bool, operation: str) -> None:
    """Raise unless the caller confirmed, for this one operation, that the objective was lowered.

    Only piezo-only systems need the confirmation: elsewhere the software lowers the objective itself.
    """
    if control._def.IS_PIEZO_ONLY and objective_lowered is not True:
        raise ObjectiveNotLoweredError(
            f"{operation} refused: on a piezo-only system the software cannot move the objective clear. "
            "Lower it with the focus knob, then confirm (objective_lowered=true)."
        )


class StageHomingInProgressError(RuntimeError):
    """A stage motion was requested while a homing operation holds the stage."""


class StageHomingRefused(RuntimeError):
    """A homing claim was refused before anything moved: something else drives the stage (the blocker given to
    Microscope.set_stage_homing_blocker), or the stage still reports motion. str(e) is "<operation> refused: <reason>";
    e.reason is the reason alone, a clause such as "an acquisition is running"."""

    def __init__(self, operation: str, reason: str):
        super().__init__(f"{operation} refused: {reason}")
        self.reason = reason


class StartupHoming(enum.Enum):
    """What startup did about homing the stage."""

    NOT_RUN = "not_run"  # skip_init: startup did not touch the stage
    HOMED = "homed"  # startup homed the stage
    ALREADY_HOMED = "already_homed"  # the controller vouched the stage is still homed (a warm start): not homed again
    DECLINED = "declined"  # a person cancelled the piezo-only prompt; the stage is not referenced


def startup_needs_objective_prompt() -> bool:
    """True when startup would send the stage to its X and Y limit switches with no software-lowered objective."""
    return bool(control._def.IS_PIEZO_ONLY and control._def.HOMING_ENABLED_X and control._def.HOMING_ENABLED_Y)


def home_stage_confirmation_text(has_z_lift: bool) -> str:
    """Home stage's confirmation: what moves, in the order Microscope.home_stage moves it, and that the stage will not
    come back."""
    z_homes = control._def.HOMING_ENABLED_Z and not control._def.USE_PI_FOCUS_STAGE
    moves = f"{'X, Y and Z' if z_homes else 'X and Y'} move to their limit switches."
    first = []
    if has_z_lift:
        first.append("the Z lift lowers")
    if control._def.USE_PI_FOCUS_STAGE:
        first.append("the objective retracts")  # the V-308, to its objective-clear end
    if first:
        moves = f"{' and '.join(first)}, then {moves}"
        moves = moves[0].upper() + moves[1:]
    return f"{moves} The stage will not return to where it is now."


CONFIG_SIGNATURE_FORMAT = 1  # bump when the fields or their rendering below change


def _number(value) -> str:
    """A number as text, independent of how the setting was written: floats by repr, ints and bools as ints."""
    if isinstance(value, (bool, int)):
        return str(int(value))
    return repr(float(value))


def config_signature_text() -> str:
    """The canonical text the configuration signature hashes: one name=value line per setting that the controller was
    configured with (Microcontroller.configure_actuators, apply_limit_switch_config) or that the homing direction
    derives from, so a change to any of them makes a stored "homed" verdict stale."""
    import squid.config  # not at module level: control.microcontroller imports this module

    stage = squid.config.get_stage_config()
    lines = [f"format={CONFIG_SIGNATURE_FORMAT}"]
    for a in ("X", "Y", "Z"):
        axis = getattr(stage, f"{a}_AXIS")
        fields = [
            (f"SCREW_PITCH_{a}_MM", _number(getattr(control._def, f"SCREW_PITCH_{a}_MM"))),
            (f"MICROSTEPPING_DEFAULT_{a}", _number(getattr(control._def, f"MICROSTEPPING_DEFAULT_{a}"))),
            (f"{a}_MOTOR_RMS_CURRENT_mA", _number(getattr(control._def, f"{a}_MOTOR_RMS_CURRENT_mA"))),
            (f"{a}_MOTOR_I_HOLD", _number(getattr(control._def, f"{a}_MOTOR_I_HOLD"))),
            (f"MAX_VELOCITY_{a}_mm", _number(getattr(control._def, f"MAX_VELOCITY_{a}_mm"))),
            (f"MAX_ACCELERATION_{a}_mm", _number(getattr(control._def, f"MAX_ACCELERATION_{a}_mm"))),
            (f"{a}_HOME_SAFETY_MARGIN_UM", str(int(getattr(control._def, f"{a}_HOME_SAFETY_MARGIN_UM")))),
            (f"{a}_LIMIT_SWITCH_CONFIG", str(int(control._def.limit_switch_config_byte(a.lower())))),
            (f"{a}_MOVEMENT_SIGN", str(int(axis.MOVEMENT_SIGN.value))),
            (f"{a}_USE_ENCODER", str(int(axis.USE_ENCODER))),
            (f"{a}_ENCODER_SIGN", str(int(axis.ENCODER_SIGN.value))),
            (f"{a}_ENCODER_STEP_SIZE", _number(axis.ENCODER_STEP_SIZE)),
            (f"{a}_FULL_STEPS_PER_REV", _number(axis.FULL_STEPS_PER_REV)),
        ]
        lines.extend(f"{name}={value}" for name, value in fields)
    return "\n".join(lines)


def config_signature() -> int:
    """The 32-bit configuration signature: the first 4 bytes, big-endian, of SHA-256 over the canonical text. Stable
    across processes and runs (never Python's hash())."""
    digest = hashlib.sha256(config_signature_text().encode("ascii")).digest()
    return int.from_bytes(digest[:4], "big")


def required_homed_axes_mask() -> int:
    """The axes (bit 1 << control._def.AXIS.<axis>) that startup must find homed to skip homing."""
    mask = 0
    if control._def.HOMING_ENABLED_X and control._def.HOMING_ENABLED_Y:
        mask |= 1 << control._def.AXIS.X | 1 << control._def.AXIS.Y
    if (control._def.HOMING_ENABLED_Z and not control._def.USE_PI_FOCUS_STAGE) or control._def.USE_Z_LIFT_STAGE:
        mask |= 1 << control._def.AXIS.Z
    return mask
