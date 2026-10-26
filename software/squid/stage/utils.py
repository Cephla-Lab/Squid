from typing import Optional, Callable, Tuple
import json
import math
import os

import squid.logging
from squid.abc import Pos, AbstractStage
from squid.config import StageConfig
import control._def as _def
import control.utils

_log = squid.logging.get_logger(__package__)
_DEFAULT_CACHE_PATH = "cache/last_coords.txt"
_MAX_CACHE_BYTES = 4096
# How far, per axis, the stage's X/Y may be from the loading position's and still count as there (is_at_loading_xy)
LOADING_XY_TOLERANCE_MM = 0.1


def get_cached_position(cache_path=_DEFAULT_CACHE_PATH) -> Optional[Pos]:
    """Load the stage position written by cache_position(), or None if there is no usable one.

    A cache file that cannot be read or parsed (truncated by a crash mid-write, edited by hand,
    filled with garbage, ...) is treated exactly like a missing one: a warning is logged and None
    is returned, so an unreadable cache cannot stop the software from starting.  A well-formed but
    stale position is returned as-is; it is not range checked against the stage's axis limits.
    """
    if not os.path.isfile(cache_path):
        _log.debug(f"Cache file '{cache_path}' not found, no cached pos found.")
        return None

    try:
        with open(cache_path, "r") as f:
            # A valid cache is one short line, so cap the read: a stray large file at this path
            # must not pull hundreds of MB into memory during startup.  Read one byte past the cap
            # so an oversized file is rejected below instead of parsed from its truncated prefix.
            contents = f.read(_MAX_CACHE_BYTES + 1)
    except (OSError, UnicodeDecodeError) as e:
        _log.warning(
            f"Cached position file '{cache_path}' could not be read ({e}). "
            "Continuing as if there is no cached position."
        )
        return None

    try:
        if len(contents) > _MAX_CACHE_BYTES:
            raise ValueError(f"file is larger than {_MAX_CACHE_BYTES} bytes")
        x, y, z = (float(v) for v in contents.strip().split(","))
        if not all(math.isfinite(v) for v in (x, y, z)):
            raise ValueError("non-finite coordinate")
    except ValueError as e:
        _log.warning(
            f"Cached position file '{cache_path}' is corrupted ({e}), contents={contents[:100].strip()!r}. "
            "Continuing as if there is no cached position."
        )
        return None

    return Pos(x_mm=x, y_mm=y, z_mm=z, theta_rad=None)


def z_is_referenced() -> bool:
    """True when Z is an absolute position: the Cephla Z is homed, or a PI V-308 is the Z.

    Mirrors Microscope.home_xyz, which references the V-308 at every start regardless of HOMING_ENABLED_Z
    (a Cephla XY stage with a V-308 has no Cephla Z to home, so it runs with homing_enabled_z off).
    Otherwise Z is the raw firmware step count: 0 at boot on an XY-only Cephla stage.
    """
    return _def.HOMING_ENABLED_Z or _def.USE_PI_FOCUS_STAGE


def z_stabilization_time_s() -> float:
    """Pause after a Z move before imaging.

    A blocking PI V-308 move returns only once the C-414 reports Z on target inside its per-move window
    (PI_FOCUS_ON_TARGET_TOL_*, 0.1-0.5 um by default), so the stepper's fixed SCAN_STABILIZATION_TIME_MS_Z
    only adds time: the V-308 uses PI_FOCUS_SCAN_STABILIZATION_TIME_MS_Z (default 0) instead. With the window
    disabled (MIN = 0, the controller's own +/-1 um, which calls sub-um steps done before they move) the
    stepper value applies. A move no larger than the window is "on target" before it starts; PIFocusStage
    covers those with its own fixed settle (PI_FOCUS_SUB_WINDOW_SETTLE_MS).
    """
    if _def.USE_PI_FOCUS_STAGE and _def.PI_FOCUS_ON_TARGET_TOL_MIN_UM:
        return _def.PI_FOCUS_SCAN_STABILIZATION_TIME_MS_Z / 1000
    return _def.SCAN_STABILIZATION_TIME_MS_Z / 1000


def cache_position(pos: Pos, stage_config: StageConfig, cache_path=_DEFAULT_CACHE_PATH, validate_z: bool = True):
    """Write out the current x, y, z position, in mm, so we can use it later as a cached position.

    Raises ValueError, writing nothing, when a validated axis is outside its soft limits.  X and Y are always
    validated; Z is skipped when validate_z=False (see z_is_referenced).
    """
    if stage_config is not None:  # StageConfig not implemented for Prior stage
        axes = [("x", stage_config.X_AXIS, pos.x_mm), ("y", stage_config.Y_AXIS, pos.y_mm)]
        if validate_z:
            axes.append(("z", stage_config.Z_AXIS, pos.z_mm))
        out_of_range = [
            f"{name}={value} not in ({axis.MIN_POSITION}, {axis.MAX_POSITION})"
            for name, axis, value in axes
            if not axis.MIN_POSITION <= value <= axis.MAX_POSITION
        ]
        if out_of_range:
            raise ValueError(f"Position {pos} is not cacheable, outside of the axis min/max: {'; '.join(out_of_range)}")
    _log.debug(f"Writing position={pos} to cache path='{cache_path}'")
    # Atomic write: a crash between truncating and writing would leave a partial file that
    # get_cached_position() can only discard, losing the position it was meant to preserve.
    tmp_path = f"{cache_path}.tmp"
    with open(tmp_path, "w") as f:
        f.write(",".join([str(pos.x_mm), str(pos.y_mm), str(pos.z_mm)]))
    os.replace(tmp_path, cache_path)


def _lower_lift_for_loading(stage: AbstractStage):
    # A Z lift (CombinedStage.lift) goes down before XY moves, taking the sample away from the objective.
    lift = getattr(stage, "lift", None)
    if lift is not None:
        stage._scanning_position_lift_mm = (lift.lower(), lift.current_format())


def _restore_lift_after_loading(stage: AbstractStage):
    # Leaving the loading position, the lift rises to the working height saved for the sample format now selected
    # (it may have changed with the sample). With none saved it goes back to the height it left, but only for the
    # same format: a different sample with no height of its own stays lowered.
    lift = getattr(stage, "lift", None)
    if lift is None or stage._scanning_position_lift_mm is None:
        return
    left_mm, left_format = stage._scanning_position_lift_mm
    sample_format = lift.current_format()
    target = lift.sample_height_mm()
    if target is not None:
        _log.info(f"Raising the Z lift to the {sample_format} height, {target:.3f} mm.")
    elif left_mm is not None and sample_format == left_format:
        target = left_mm
        _log.info(f"Raising the Z lift back to {target:.3f} mm (no height saved for {sample_format}).")
    else:
        _log.info(f"Z lift stays lowered: no height saved for {sample_format}.")
        stage._scanning_position_lift_mm = None
        return
    lift.move_to(target)
    # Forgotten only once the lift got there: a failed move keeps it, so retrying the return restores the lift
    # instead of skipping it.
    stage._scanning_position_lift_mm = None


# A Z within this of the retracted position counts as retracted (a read-back carries float noise, e.g. 0.1000008).
_RETRACTED_TOLERANCE_MM = 0.001


def objective_is_retracted(z_mm: float) -> bool:
    return z_mm <= _def.OBJECTIVE_RETRACTED_POS_MM + _RETRACTED_TOLERANCE_MM


def _retract_objective_for_loading(stage: AbstractStage):
    # The objective goes up to its retracted position (never down: one already above it stays), remembering where
    # it was; the way back lowers it there last.
    stage._scanning_position_z_mm = stage.get_pos().z_mm
    if not objective_is_retracted(stage._scanning_position_z_mm):
        stage.move_z_to(_def.OBJECTIVE_RETRACTED_POS_MM)
        stage.wait_for_idle(_def.SLIDE_POTISION_SWITCHING_TIMEOUT_LIMIT_S)


def _restore_objective_after_loading(stage: AbstractStage):
    z_mm = getattr(stage, "_scanning_position_z_mm", None)
    if z_mm is None:
        return
    stage.move_z_to(z_mm)
    # Forgotten only once Z got there: a failed move keeps it, so retrying the return lowers the objective again.
    stage._scanning_position_z_mm = None


def save_loading_state(stage: AbstractStage):
    """Z lift systems: record that the stage is at the loading position, with the Z and lift height to return to,
    so a Squid closed there starts again in the loading state (see load_loading_state)."""
    lift = getattr(stage, "lift", None)
    if lift is None:
        return
    lift_mm, lift_format = getattr(stage, "_scanning_position_lift_mm", None) or (None, None)
    state = {
        "scanning_z_mm": getattr(stage, "_scanning_position_z_mm", None),
        "lift_mm": lift_mm,
        "lift_format": lift_format,
    }
    path = _def.Z_LIFT_LOADING_STATE_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp_path, path)


def load_loading_state() -> Optional[dict]:
    """The state save_loading_state wrote, or None if the stage was not left at the loading position."""
    path = _def.Z_LIFT_LOADING_STATE_PATH
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError) as e:
        _log.warning(f"Unreadable loading position state {path} ({e}): treating the stage as at the loading position")
        return {}


def clear_loading_state():
    path = _def.Z_LIFT_LOADING_STATE_PATH
    if os.path.isfile(path):
        os.remove(path)


def _finite_mm_in(value, low: float, high: float) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and low <= value <= high
    )


def restore_loading_state(stage: AbstractStage, state: dict) -> bool:
    """Put back what the move to the loading position remembered, so the return to scanning raises the lift and
    offers the Z it left, as if Squid had not been closed. Returns False if any of it was missing or invalid (an
    unreadable file gives {}): then the return still raises the lift to the working height saved for the sample
    format selected by then (with none it stays lowered), and the objective stays retracted, to refocus by hand."""
    complete = True
    z_mm = state.get("scanning_z_mm")
    z_axis = stage.get_config().Z_AXIS
    if z_mm is not None and not _finite_mm_in(z_mm, z_axis.MIN_POSITION, z_axis.MAX_POSITION):
        _log.warning(f"Loading position state: invalid Z {z_mm!r}; the objective stays retracted on the return.")
        z_mm, complete = None, False
    stage._scanning_position_z_mm = z_mm

    lift = stage.lift
    lift_mm, lift_format = state.get("lift_mm"), state.get("lift_format")
    if _finite_mm_in(lift_mm, lift.min_mm, lift.max_mm) and isinstance(lift_format, str):
        stage._scanning_position_lift_mm = (lift_mm, lift_format)
    else:
        # Unknown: the return raises the lift to the current format's saved height only (never "back to" a height)
        _log.warning(
            f"Loading position state: no valid lift height ({lift_mm!r}, {lift_format!r}); on the return the lift "
            "rises to the sample format's saved working height, or stays lowered if none is saved."
        )
        stage._scanning_position_lift_mm = (None, None)
        complete = False
    return complete


def _loading_xy_mm() -> Tuple[float, float]:
    """(X, Y) of the loading position, where move_xy_to_loading_position ends for a wellplate and a slide alike."""
    return _def.SLIDE_POSITION.LOADING_X_MM, _def.SLIDE_POSITION.LOADING_Y_MM


def is_at_loading_xy(stage: AbstractStage) -> bool:
    """True when the stage's X and Y are each within LOADING_XY_TOLERANCE_MM of the loading position's."""
    loading_x_mm, loading_y_mm = _loading_xy_mm()
    pos = stage.get_pos()
    return (
        abs(pos.x_mm - loading_x_mm) <= LOADING_XY_TOLERANCE_MM
        and abs(pos.y_mm - loading_y_mm) <= LOADING_XY_TOLERANCE_MM
    )


def move_xy_to_loading_position(stage: AbstractStage, is_wellplate: bool):
    """XY to the loading position, by the same path the loading move takes (lift and Z are not moved)."""
    loading_x_mm, loading_y_mm = _loading_xy_mm()
    # Set our limits to something large.  Then later reset them back to the safe values.
    if is_wellplate:
        a_large_limit_mm = 125
        stage.set_limits(
            x_pos_mm=a_large_limit_mm,
            x_neg_mm=-a_large_limit_mm,
            y_pos_mm=a_large_limit_mm,
            y_neg_mm=-a_large_limit_mm,
        )

        # TODO: These values should not be hardcoded as we have stages with different blocks
        # for opening the clamp. I'm not sure why exactly this piece is designed this way and
        # how to name the variable properly. Right now they should work for all our stages.
        stage.move_y_to(15)
        stage.move_x_to(35)
        stage.move_y_to(loading_y_mm)
        stage.move_x_to(loading_x_mm)

        stage.set_limits(
            x_pos_mm=stage.get_config().X_AXIS.MAX_POSITION,
            x_neg_mm=stage.get_config().X_AXIS.MIN_POSITION,
            y_pos_mm=stage.get_config().Y_AXIS.MAX_POSITION,
            y_neg_mm=stage.get_config().Y_AXIS.MIN_POSITION,
        )
    else:
        stage.move_y_to(loading_y_mm)
        stage.move_x_to(loading_x_mm)


def _clear_sample_for_loading_xy(stage: AbstractStage, is_wellplate: bool):
    # The objective retracts first; with a Z lift, before the lift lowers. The way back raises the lift before it
    # lowers the objective, so the sample never rises toward an objective already at its working height.
    if is_wellplate or getattr(stage, "lift", None) is not None:
        _retract_objective_for_loading(stage)
    _lower_lift_for_loading(stage)


def _move_to_loading_position_impl(stage: AbstractStage, is_wellplate: bool):
    _clear_sample_for_loading_xy(stage, is_wellplate)
    # Recorded once the lift is down, before XY moves: a Squid that dies on the way still starts lowered and locked
    save_loading_state(stage)
    move_xy_to_loading_position(stage, is_wellplate)


def return_to_loading_position(stage: AbstractStage, is_wellplate: bool):
    """A still-homed startup whose restored loading state (load_loading_state) says the stage is at the loading
    position, while it is elsewhere: a session that died on its way back to scanning, which raises the lift and may
    lower the objective before it clears the state. Back by the move to the loading position's steps, in its order:
    the objective retracts and the lift lowers before XY travels.

    The state is not recorded again, and the Z and lift height to return to stay the ones the stage holds (the
    restored state's, see restore_loading_state), not the current, possibly raised, ones the retract and the lower
    note on the stage."""
    return_heights = getattr(stage, "_scanning_position_z_mm", None), getattr(stage, "_scanning_position_lift_mm", None)
    _clear_sample_for_loading_xy(stage, is_wellplate)
    stage._scanning_position_z_mm, stage._scanning_position_lift_mm = return_heights
    move_xy_to_loading_position(stage, is_wellplate)


def _move_to_scanning_position_impl(stage: AbstractStage, is_wellplate: bool, restore_z: bool = True):
    if is_wellplate:
        stage.move_x_to(_def.SLIDE_POSITION.SCANNING_X_MM)
        stage.move_y_to(_def.SLIDE_POSITION.SCANNING_Y_MM)
    else:
        stage.move_y_to(_def.SLIDE_POSITION.SCANNING_Y_MM)
        stage.move_x_to(_def.SLIDE_POSITION.SCANNING_X_MM)
    _restore_lift_after_loading(stage)
    if restore_z:
        _restore_objective_after_loading(stage)
    elif getattr(stage, "_scanning_position_z_mm", None) is not None:
        # Asked to leave it retracted (StageUtils asks on a Z lift system): the saved Z is forgotten only once the
        # lift got there, like a restore.
        _log.info(f"The objective stays retracted (it was at {stage._scanning_position_z_mm:.4f} mm).")
        stage._scanning_position_z_mm = None
    clear_loading_state()


def move_to_loading_position(
    stage: AbstractStage,
    blocking: bool = True,
    callback: Optional[Callable[[bool, Optional[str]], None]] = None,
    is_wellplate: bool = True,
):
    """Move the stage to loading position so it is clear for loading a sample.
    Args:
        blocking: If True, wait for the move to complete before returning.
                    If False, return immediately and run the operation in a separate thread. callback will be called when done.
        callback: Optional callback function called when movement completes.
                    Receives (success: bool, error_message: Optional[str])
        **kwargs: Additional arguments to pass to the operation.
    Returns:
        threading.Thread: The thread handling the movement. None if blocking is True.
    """
    if blocking and callback:
        raise ValueError("Callback is not supported when blocking is True")
    if blocking:
        _log.info(f"Moving to loading position. Blocking is True.")
        _move_to_loading_position_impl(stage, is_wellplate)
        _log.info("Successfully moved to loading position")
    else:
        return control.utils.threaded_operation_helper(
            _move_to_loading_position_impl, callback, stage=stage, is_wellplate=is_wellplate
        )


def move_to_scanning_position(
    stage: AbstractStage,
    blocking: bool = True,
    callback: Optional[Callable[[bool, Optional[str]], None]] = None,
    is_wellplate: bool = True,
    restore_z: bool = True,
):
    """Move the stage back to scanning position from loading position.
    Args:
        blocking: If True, wait for the move to complete before returning.
                    If False, return immediately and run the operation in a separate thread. callback will be called when done.
        callback: Optional callback function called when movement completes.
                    Receives (success: bool, error_message: Optional[str])
        restore_z: False leaves an objective retracted for the loading position there (after the Z lift rose).
        **kwargs: Additional arguments to pass to the operation.
    Returns:
        threading.Thread: The thread handling the movement. None if blocking is True.
    """
    if blocking and callback:
        raise ValueError("Callback is not supported when blocking is True")
    if blocking:
        _log.info(f"Moving to scanning position. Blocking is True.")
        _move_to_scanning_position_impl(stage, is_wellplate, restore_z)
        _log.info("Successfully moved to scanning position")
    else:
        return control.utils.threaded_operation_helper(
            _move_to_scanning_position_impl, callback, stage=stage, is_wellplate=is_wellplate, restore_z=restore_z
        )


def move_z_axis_to_safety_position(stage: AbstractStage):
    if _def.USE_PI_FOCUS_STAGE:
        # The V-308 has no Z_HOME_SAFETY_POINT concept; its retracted position is the safe Z.
        stage.move_z_to(_def.OBJECTIVE_RETRACTED_POS_MM)
    else:
        stage.move_z_to(int(_def.Z_HOME_SAFETY_POINT) / 1000.0)
