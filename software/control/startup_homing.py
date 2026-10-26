"""Rules for homing the stage on systems where the software cannot lower the objective."""

import enum

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
