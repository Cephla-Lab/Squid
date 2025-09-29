from typing import Optional, Callable
import os

import squid.logging
from squid.abc import Pos, AbstractStage
from squid.config import StageConfig
import control._def as _def
import control.utils

_log = squid.logging.get_logger(__package__)
_DEFAULT_CACHE_PATH = "cache/last_coords.txt"

"""
Attempts to load a cached stage position and return it.
"""


def get_cached_position(cache_path=_DEFAULT_CACHE_PATH) -> Optional[Pos]:
    if not os.path.isfile(cache_path):
        _log.debug(f"Cache file '{cache_path}' not found, no cached pos found.")
        return None
    with open(cache_path, "r") as f:
        for line in f:
            try:
                x, y, z = line.strip("\n").strip().split(",")
                x = float(x)
                y = float(y)
                z = float(z)
                return Pos(x_mm=x, y_mm=y, z_mm=z, theta_rad=None)
            except RuntimeError as e:
                raise e
                pass
    return None


"""
Write out the current x, y, z position, in mm, so we can use it later as a cached position.
"""


def cache_position(pos: Pos, stage_config: StageConfig, cache_path=_DEFAULT_CACHE_PATH):
    if stage_config is not None:  # StageConfig not implemented for Prior stage
        x_min = stage_config.X_AXIS.MIN_POSITION
        x_max = stage_config.X_AXIS.MAX_POSITION
        y_min = stage_config.Y_AXIS.MIN_POSITION
        y_max = stage_config.Y_AXIS.MAX_POSITION
        z_min = stage_config.Z_AXIS.MIN_POSITION
        z_max = stage_config.Z_AXIS.MAX_POSITION
        if not (x_min <= pos.x_mm <= x_max and y_min <= pos.y_mm <= y_max and z_min <= pos.z_mm <= z_max):
            raise ValueError(
                f"Position {pos} is not cacheable because it is outside of the min/max of at least one axis. x_range=({x_min}, {x_max}), y_range=({y_min}, {y_max}), z_range=({z_min}, {z_max})"
            )
    with open(cache_path, "w") as f:
        _log.debug(f"Writing position={pos} to cache path='{cache_path}'")
        f.write(",".join([str(pos.x_mm), str(pos.y_mm), str(pos.z_mm)]))


def _move_to_loading_position_impl(stage: AbstractStage, is_wellplate: bool):
    # Set our limits to something large.  Then later reset them back to the safe values.
    if is_wellplate:
        a_large_limit_mm = 125
        stage.set_limits(
            x_pos_mm=a_large_limit_mm,
            x_neg_mm=-a_large_limit_mm,
            y_pos_mm=a_large_limit_mm,
            y_neg_mm=-a_large_limit_mm,
        )

        stage._scanning_position_z_mm = stage.get_pos().z_mm
        stage.move_z_to(_def.OBJECTIVE_RETRACTED_POS_MM)
        stage.wait_for_idle(_def.SLIDE_POTISION_SWITCHING_TIMEOUT_LIMIT_S)

        # TODO: These values should not be hardcoded as we have stages with different blocks
        # for opening the clamp. I'm not sure why exactly this piece is designed this way and
        # how to name the variable properly. Right now they should work for all our stages.
        stage.move_y_to(15)
        stage.move_x_to(35)
        stage.move_y_to(_def.SLIDE_POSITION.LOADING_Y_MM)
        stage.move_x_to(_def.SLIDE_POSITION.LOADING_X_MM)

        stage.set_limits(
            x_pos_mm=stage.get_config().X_AXIS.MAX_POSITION,
            x_neg_mm=stage.get_config().X_AXIS.MIN_POSITION,
            y_pos_mm=stage.get_config().Y_AXIS.MAX_POSITION,
            y_neg_mm=stage.get_config().Y_AXIS.MIN_POSITION,
        )
    else:
        stage.move_y_to(_def.SLIDE_POSITION.LOADING_Y_MM)
        stage.move_x_to(_def.SLIDE_POSITION.LOADING_X_MM)


def _move_to_scanning_position_impl(stage: AbstractStage, is_wellplate: bool):
    if is_wellplate:
        stage.move_x_to(_def.SLIDE_POSITION.SCANNING_X_MM)
        stage.move_y_to(_def.SLIDE_POSITION.SCANNING_Y_MM)
        if stage._scanning_position_z_mm is not None:
            stage.move_z_to(stage._scanning_position_z_mm)
        stage._scanning_position_z_mm = None
    else:
        stage.move_y_to(_def.SLIDE_POSITION.SCANNING_Y_MM)
        stage.move_x_to(_def.SLIDE_POSITION.SCANNING_X_MM)


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
):
    """Move the stage back to scanning position from loading position.
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
        _log.info(f"Moving to scanning position. Blocking is True.")
        _move_to_scanning_position_impl(stage, is_wellplate)
        _log.info("Successfully moved to scanning position")
    else:
        return control.utils.threaded_operation_helper(
            _move_to_scanning_position_impl, callback, stage=stage, is_wellplate=is_wellplate
        )
