"""Which Qt binding Squid runs on, chosen before qtpy is first imported.

qtpy picks its binding on its first import and ignores QT_API after that, so main_hcs.py calls
select_qt_api() before importing anything Qt. This module must not import qtpy itself, nor control._def
(importing it reads the whole configuration, with side effects).

Order of precedence:
1. the --qt-api command line option;
2. the SQUID_QT_API environment variable;
3. ``qt_api`` in the [GENERAL] section of the machine's configuration*.ini (the per-machine setting);
4. PyQt5.
The command line option survives Squid's restart after a settings change, which re-runs main_hcs.py with the same
arguments; the ini and environment settings are read again by the restarted process.
"""

import configparser
import glob
import os
from typing import Mapping, Optional, Sequence, Tuple

SUPPORTED_QT_APIS = ("pyqt5", "pyqt6")
DEFAULT_QT_API = "pyqt5"
ENV_VAR = "SQUID_QT_API"
OPTION = "--qt-api"
INI_SECTION = "GENERAL"
INI_KEY = "qt_api"


def qt_api_from_ini(directory: str = ".") -> Optional[str]:
    """``qt_api`` from [GENERAL] of the single configuration*.ini in ``directory``, or None if unset.

    None too when there is no ini, more than one, or it cannot be parsed: control._def reports those cases
    when it loads the configuration.
    """
    files = glob.glob(os.path.join(directory, "configuration*.ini"))
    if len(files) != 1:
        return None
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(files[0])
        value = parser.get(INI_SECTION, INI_KEY, fallback=None)
    except configparser.Error:
        return None
    if value is None:
        return None
    # Same conventions as control._def's reader: "# ..." after the value is a comment; quotes are allowed.
    value = value.split(" #", 1)[0].split("\t#", 1)[0].strip().strip("\"'")
    return value or None


def resolve_qt_api(argv: Sequence[str], environ: Mapping[str, str], ini_value: Optional[str] = None) -> Tuple[str, str]:
    """(binding, where it came from): ``--qt-api X`` / ``--qt-api=X`` in argv, else SQUID_QT_API, else the ini
    value, else PyQt5. An unsupported value raises ValueError naming its source."""
    requested: Optional[str] = None
    source = "default"
    for i, arg in enumerate(argv):
        if arg == OPTION and i + 1 < len(argv):
            requested, source = argv[i + 1], OPTION
        elif arg.startswith(OPTION + "="):
            requested, source = arg.split("=", 1)[1], OPTION
    if requested is None and environ.get(ENV_VAR):
        requested, source = environ[ENV_VAR], ENV_VAR
    if requested is None and ini_value:
        requested, source = ini_value, f"[{INI_SECTION}] {INI_KEY}"
    if requested is None:
        return DEFAULT_QT_API, source
    requested = requested.strip().lower()
    if requested not in SUPPORTED_QT_APIS:
        raise ValueError(
            f"Unsupported Qt binding '{requested}' (from {source}); use one of {', '.join(SUPPORTED_QT_APIS)}"
        )
    return requested, source


def qt_api_from(argv: Sequence[str], environ: Mapping[str, str], ini_value: Optional[str] = None) -> str:
    """The binding resolve_qt_api() picks."""
    return resolve_qt_api(argv, environ, ini_value)[0]


def select_qt_api(argv: Sequence[str], environ=os.environ, directory: str = ".") -> Tuple[str, str]:
    """Set QT_API for qtpy and return (binding, where it came from). Call before the first qtpy import."""
    qt_api, source = resolve_qt_api(argv, environ, qt_api_from_ini(directory))
    environ["QT_API"] = qt_api
    return qt_api, source
