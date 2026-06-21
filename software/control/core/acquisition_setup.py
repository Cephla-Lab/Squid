"""Shared acquisition setup helpers.

Free functions used by MultiPointController (and future controllers such as
RecordZStackController) to set up experiment directories without duplicating
logic across controller classes.
"""

import os
from datetime import datetime
from typing import Optional, Tuple

from control import utils


def compute_pixel_size_um(objective_store, camera) -> Optional[float]:
    """Compute the physical pixel size in µm from objective and camera metadata.

    Returns the product of the objective's pixel-size factor and the camera's
    binned pixel size in µm, or None if either value is unavailable or an
    exception is raised.

    Args:
        objective_store: ObjectiveStore (or compatible object) with
            ``get_pixel_size_factor() -> Optional[float]``.
        camera: AbstractCamera (or compatible) with
            ``get_pixel_size_binned_um() -> Optional[float]``.

    Returns:
        Pixel size in µm, or None.
    """
    try:
        pixel_factor = objective_store.get_pixel_size_factor()
        sensor_pixel_um = camera.get_pixel_size_binned_um()
        if pixel_factor is not None and sensor_pixel_um is not None:
            return float(pixel_factor) * float(sensor_pixel_um)
        return None
    except Exception:
        return None


def create_experiment_dir(base_path: str, experiment_id: str) -> Tuple[str, str]:
    """Resolve a unique experiment ID and create its output directory.

    Appends a timestamp to *experiment_id* (spaces replaced with underscores)
    to guarantee uniqueness, then creates the directory tree under *base_path*.

    Args:
        base_path: Root directory for all experiments.
        experiment_id: Human-readable experiment name supplied by the user.

    Returns:
        A ``(resolved_id, dir_path)`` tuple where *resolved_id* is the
        timestamped identifier and *dir_path* is the absolute path of the
        newly created directory.
    """
    resolved_id = experiment_id.replace(" ", "_") + "_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f")
    dir_path = os.path.join(base_path, resolved_id)
    utils.ensure_directory_exists(dir_path)
    return resolved_id, dir_path
