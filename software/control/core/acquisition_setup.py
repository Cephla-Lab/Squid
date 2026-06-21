"""Shared acquisition setup helpers.

Free functions used by MultiPointController (and future controllers such as
RecordZStackController) to set up experiment directories without duplicating
logic across controller classes.
"""

import os
from datetime import datetime
from typing import Tuple

from control import utils


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
