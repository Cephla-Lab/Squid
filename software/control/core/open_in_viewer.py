"""Open a finished acquisition in ndviewer_light, in a separate process.

Qt-free on purpose: the GUI widgets call these helpers and show the dialogs; everything here can be unit-tested
without the viewer, which Squid's CI never checks out (the ndviewer_light submodule stays uninitialised there).
"""

import importlib.util
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

import squid.logging

_log = squid.logging.get_logger(__name__)

# Top-level packages the viewer imports. Checked with find_spec on the top-level name only: a dotted
# find_spec would import the parent packages, and the submodule's shim imports the viewer and Qt.
VIEWER_PACKAGES: Tuple[str, ...] = ("ndv", "vispy", "tensorstore", "xarray", "dask", "superqt", "pyconify")
VIEWER_MODULE = "control.ndviewer_light.ndviewer_light"
SUBMODULE_HINT = "git submodule update --init software/control/ndviewer_light"
# The bundled viewer requires ndv>=0.5,<0.6: the floor the NDViewer tab checks before it builds the
# viewer (an install that pulled the submodule without upgrading ndv would otherwise get a half-working
# tab: names lost, navigation dead), and the command that installs it. ndv depends on ihist, whose
# metadata demands numpy>=2 although its wheels run on numpy 1.x; Squid keeps numpy below 2 (FLIR's
# PySpin for Python 3.10 is numpy 1 only), so both are installed without dependency resolution and
# ndv's other dependencies follow explicitly. Mirrors setup_22.04.sh.
NDV_MIN_VERSION = (0, 5)
NDV_INSTALL_COMMAND = (
    'pip install --no-deps "ihist>=0.1.3" "ndv>=0.5,<0.6" && '
    'pip install "cmap>=0.3" "psygnal>=0.12" "pydantic>=2.10" "typing-extensions>=4.10" '
    '"superqt[iconify]>=0.8.1" "pyconify>=0.2.1" pyopengl'
)


def ndv_version_problem(installed: str) -> Optional[str]:
    """None when the installed ndv meets the viewer's floor, else the message (with the upgrade command)."""
    parts = []
    for piece in installed.split(".")[:2]:
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    if tuple(parts) >= NDV_MIN_VERSION:
        return None
    return (
        f"NDViewer needs ndv {NDV_MIN_VERSION[0]}.{NDV_MIN_VERSION[1]} or newer; {installed} is installed.\n"
        f"Upgrade with:  {NDV_INSTALL_COMMAND}"
    )


def software_dir() -> str:
    """The directory that holds the `control` package (the cwd the viewer process is started in)."""
    import control

    return os.path.dirname(os.path.dirname(os.path.abspath(control.__file__)))


def viewer_entry_point(software_dir: str) -> str:
    """The bundled viewer's `python -m` entry point; absent when the submodule is not checked out."""
    return os.path.join(software_dir, "control", "ndviewer_light", "ndviewer_light", "__main__.py")


@dataclass(frozen=True)
class ViewerPreflight:
    """What a launch needs: the viewer's packages in this interpreter and the bundled entry point on disk."""

    missing_packages: Tuple[str, ...]
    entry_point: str
    entry_point_exists: bool

    @property
    def ok(self) -> bool:
        return not self.missing_packages and self.entry_point_exists

    def problem(self) -> str:
        """The dialog text when not ok; empty when ok."""
        lines = []
        if self.missing_packages:
            lines.append(f"Missing Python packages: {', '.join(self.missing_packages)}")
            # ndv and pyconify come through the ndv install command (numpy stays below 2); the rest plainly
            others = [name for name in self.missing_packages if name not in ("ndv", "pyconify")]
            if len(others) < len(self.missing_packages):
                lines.append(NDV_INSTALL_COMMAND)
            if others:
                lines.append(f"pip install {' '.join(others)}")
        if not self.entry_point_exists:
            lines.append(f"The viewer submodule is not checked out ({self.entry_point}).")
            lines.append(f"Run: {SUBMODULE_HINT}")
        return "\n".join(lines)


def check_viewer(
    software_dir: str,
    find_spec: Callable[[str], object] = importlib.util.find_spec,
    isfile: Callable[[str], bool] = os.path.isfile,
) -> ViewerPreflight:
    """Find every problem a launch would hit, without importing the viewer or Qt."""
    missing = tuple(name for name in VIEWER_PACKAGES if find_spec(name) is None)
    entry_point = viewer_entry_point(software_dir)
    return ViewerPreflight(missing, entry_point, bool(isfile(entry_point)))


def viewer_launch_command(run_dir: str) -> List[str]:
    return [sys.executable, "-m", VIEWER_MODULE, run_dir]


def launch_viewer(run_dir: str, software_dir: str, popen=subprocess.Popen):
    """Start the standalone viewer on run_dir and return at once; the process is never waited on.

    Started in its own session: otherwise it shares Squid's terminal session and dies with Squid
    when that terminal closes (SIGHUP); the viewer window is meant to outlive Squid.
    """
    _log.info(f"Opening {run_dir} in ndviewer_light")
    return popen(viewer_launch_command(run_dir), cwd=software_dir, start_new_session=True)


@dataclass(frozen=True)
class RunSnapshot:
    """The run's folder and skip-saving state, captured when the run starts.

    Captured at start because the fluidics imaging port restores the controller's base path in its own
    acquisition_finished slot, so the controller's fields at finish time can describe the wrong folder.
    """

    run_dir: Optional[str]
    skip_saving: bool


def finished_run_dir(
    snapshot: Optional[RunSnapshot], end_reason: Optional[str], isdir: Callable[[str], bool] = os.path.isdir
) -> Optional[str]:
    """The folder the button opens after a run, or None when there is nothing to open.

    A run that never started (failed_to_start) and a skip-saving run (a folder with parameter files only)
    give None; aborted and errored runs count, their data is there to look at.
    """
    if snapshot is None or not snapshot.run_dir or snapshot.skip_saving or end_reason == "failed_to_start":
        return None
    return snapshot.run_dir if isdir(snapshot.run_dir) else None
