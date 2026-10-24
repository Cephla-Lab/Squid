"""Rules for homing the stage on systems where the software cannot lower the objective."""

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
