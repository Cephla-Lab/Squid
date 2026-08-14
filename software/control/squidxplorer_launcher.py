"""Launch SquidXplorer on a finished acquisition folder.

SquidXplorer (the ``squidmip`` package) lives in a separate repository with its own
virtual environment and a newer Python than the acquisition software runs on, so it
can only ever be started as a separate process. This module resolves the command to
run and starts it; the GUI owns everything the operator sees.

Resolution order:
  1. ``SQUIDXPLORER_PYTHON`` from the config, if it points at a real file.
  2. A ``squidmip-view`` executable on PATH.
  3. Nothing — the caller tells the operator how to configure it.

``SQUIDXPLORER_GUI_LOCK_DIR``, if set, is passed to the viewer as ``SQUIDMIP_GUI_LOCK_DIR``
so this handoff does not contend with a SquidXplorer already opened from a desktop shortcut.
"""

import os
import shutil
import subprocess
import tempfile
from typing import List, Optional, Tuple

import control._def
import squid.logging

_log = squid.logging.get_logger(__name__)

# SquidXplorer's GUI entry point reads its dataset from a bare sys.argv[1] with no
# argument parser, so the folder must arrive as exactly one argv element. Every call
# below uses the list form of Popen with shell=False for that reason: a path with a
# space in it that goes through a shell splits across argv, and SquidXplorer then
# opens empty with no error at all.
_VIEWER_MODULE = "squidmip._viewer"
_VIEWER_SCRIPT = "squidmip-view"
_GUI_LOCK_DIR_ENV = "SQUIDMIP_GUI_LOCK_DIR"


def squidxplorer_environment() -> Optional[dict]:
    """Environment for the viewer process, or None to inherit ours unchanged.

    SquidXplorer allows one GUI at a time per lock directory, and that directory defaults to
    ~/.cache/squidmip for every checkout on the machine. Handing the viewer its own directory
    is what lets a run open here while another SquidXplorer window is already up.
    """
    lock_dir = (getattr(control._def, "SQUIDXPLORER_GUI_LOCK_DIR", "") or "").strip()
    if not lock_dir:
        return None

    env = os.environ.copy()
    env[_GUI_LOCK_DIR_ENV] = lock_dir
    return env


def resolve_squidxplorer_command(run_dir: str) -> Optional[List[str]]:
    """Build the argv that opens *run_dir* in SquidXplorer, or None if it isn't installed."""
    configured_python = (getattr(control._def, "SQUIDXPLORER_PYTHON", "") or "").strip()
    if configured_python:
        if os.path.isfile(configured_python):
            return [configured_python, "-m", _VIEWER_MODULE, run_dir]
        _log.warning(
            f"SQUIDXPLORER_PYTHON is set to '{configured_python}', which is not a file. "
            f"Falling back to '{_VIEWER_SCRIPT}' on PATH."
        )

    on_path = shutil.which(_VIEWER_SCRIPT)
    if on_path:
        return [on_path, run_dir]

    return None


def launch_squidxplorer(run_dir: str) -> Tuple[subprocess.Popen, str]:
    """Start SquidXplorer on *run_dir*.

    Returns the process and the path of the file its stderr is redirected to, so the
    caller can report why it died if it exits immediately (the usual cause being
    SquidXplorer's single-instance lock refusing a second window).

    Raises FileNotFoundError if no SquidXplorer installation could be resolved.
    """
    command = resolve_squidxplorer_command(run_dir)
    if command is None:
        raise FileNotFoundError("No SquidXplorer installation found.")

    # A real file rather than a pipe: nothing reads this process's output while it
    # runs, and a pipe nobody drains would eventually block the viewer on write.
    fd, stderr_path = tempfile.mkstemp(suffix=".log", prefix="squidxplorer_launch_")
    stderr_file = os.fdopen(fd, "w")
    try:
        env = squidxplorer_environment()
        lock_note = f" with {_GUI_LOCK_DIR_ENV}={env[_GUI_LOCK_DIR_ENV]}" if env else ""
        _log.info(f"Launching SquidXplorer: {command}{lock_note}")
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=stderr_file,
            shell=False,
            env=env,
        )
    except Exception:
        stderr_file.close()
        try:
            os.unlink(stderr_path)
        except OSError as cleanup_err:
            _log.debug(f"Failed to remove {stderr_path} after a failed launch: {cleanup_err}")
        raise
    finally:
        # The child holds its own duplicate of the descriptor.
        if not stderr_file.closed:
            stderr_file.close()

    return process, stderr_path


def read_launch_error(stderr_path: str, max_chars: int = 1000) -> str:
    """Read back what a failed launch wrote to stderr. Never raises."""
    try:
        with open(stderr_path, "r", errors="replace") as f:
            return f.read().strip()[-max_chars:]
    except OSError as e:
        _log.debug(f"Could not read SquidXplorer launch stderr at {stderr_path}: {e}")
        return ""
