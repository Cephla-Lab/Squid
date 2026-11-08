"""Find and start a standalone ndviewer_light process for a saved acquisition.

The child's output goes to ndviewer_light.log in the history folder (truncated per launch), so a viewer that exits
early can be explained to the user with the tail of that log (read_log_tail).
"""

import importlib.util
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import squid.logging
from squid.acquisition_history import history_dir

_SOFTWARE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_SUBMODULE_MAIN = os.path.join(_SOFTWARE_DIR, "control", "ndviewer_light", "__main__.py")

NOT_INSTALLED_REASON = "ndviewer_light is not installed: pip install it, or set its location in Preferences > Views"
LOG_FILE_NAME = "ndviewer_light.log"

_log = squid.logging.get_logger(__name__)


def default_log_path() -> str:
    return os.path.join(str(history_dir()), LOG_FILE_NAME)


def read_log_tail(path: str, lines: int = 10) -> str:
    """The last lines of the launch log ("" when it cannot be read)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return ""
    return "\n".join(text.rstrip().splitlines()[-lines:])


@dataclass
class LauncherResolution:
    command: Optional[List[str]]
    source: str  # "preference" | "path" | "package" | "submodule" | "none"
    reason: str
    env: Optional[Dict[str, str]] = None
    cwd: Optional[str] = None


def _package_importable() -> bool:
    try:
        return importlib.util.find_spec("ndviewer_light") is not None
    except (ImportError, ValueError):
        return False


def _split_windows_command_line(text: str) -> List[str]:
    """Split as Windows programs do (CommandLineToArgvW): only double quotes group words and a backslash is an
    ordinary character, so a path with an apostrophe splits and a quoted value such as --data="C:\a b" keeps its
    content without the quotes."""
    lexer = shlex.shlex(text, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    lexer.quotes = '"'
    lexer.escape = ""
    return list(lexer)


def _split_preference(preference: str) -> List[str]:
    if sys.platform == "win32":
        return _split_windows_command_line(preference)
    return shlex.split(preference)


def _resolve_preference(preference: str, python: str) -> Optional[LauncherResolution]:
    reason = "the location set in Preferences"
    path = preference.strip().strip("\"'")
    if os.path.isfile(path):
        return LauncherResolution([path], "preference", reason)
    if os.path.isdir(path) and os.path.isfile(os.path.join(path, "ndviewer_light", "__main__.py")):
        existing = os.environ.get("PYTHONPATH")
        pythonpath = path + os.pathsep + existing if existing else path
        return LauncherResolution(
            [python, "-m", "ndviewer_light"], "preference", reason, env={"PYTHONPATH": pythonpath}
        )
    try:
        tokens = _split_preference(preference)
    except ValueError as e:
        _log.warning(f"Ignoring the ndviewer_light location {preference!r} from Preferences: {e}")
        return None
    if tokens and (os.path.isabs(tokens[0]) is False or os.path.isfile(tokens[0])):
        # A command line such as "uv run ndviewer-light": trust the user's text.
        return LauncherResolution(tokens, "preference", reason)
    return None


def resolve_ndviewer_light(
    preference: str = "",
    *,
    which: Callable[[str], Optional[str]] = shutil.which,
    package_importable: Callable[[], bool] = _package_importable,
    submodule_main: str = _DEFAULT_SUBMODULE_MAIN,
    python: str = sys.executable,
) -> LauncherResolution:
    preference = (preference or "").strip()
    if preference:
        found = _resolve_preference(preference, python)
        if found is not None:
            return found

    exe = which("ndviewer-light")
    if exe:
        return LauncherResolution([exe], "path", "ndviewer-light on the PATH")
    if package_importable():
        return LauncherResolution(
            [python, "-m", "ndviewer_light"], "package", "ndviewer_light package in Squid's Python"
        )
    if os.path.isfile(submodule_main):
        return LauncherResolution(
            [python, "-m", "control.ndviewer_light"],
            "submodule",
            "Squid's ndviewer_light submodule",
            cwd=_SOFTWARE_DIR,
        )
    reason = NOT_INSTALLED_REASON
    if preference:
        reason += f" (the location set in Preferences was not found: {preference})"
    return LauncherResolution(None, "none", reason)


def launch_ndviewer_light(
    resolution: LauncherResolution,
    folder: str,
    *,
    popen: Callable = subprocess.Popen,
    log_path: Optional[str] = None,
) -> subprocess.Popen:
    """Start the viewer on folder, detached; its stdout and stderr go to log_path (default: the history folder's
    ndviewer_light.log), truncated first. Returns the Popen so the caller can check for an early exit."""
    if not resolution.command:
        raise RuntimeError(resolution.reason)
    log_path = log_path or default_log_path()
    os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    env = {**os.environ, **resolution.env} if resolution.env else None
    with open(log_path, "wb") as log:  # the child keeps its own handle; ours closes once it is started
        return popen(
            list(resolution.command) + [folder],
            cwd=resolution.cwd,
            env=env,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            **kwargs,
        )
