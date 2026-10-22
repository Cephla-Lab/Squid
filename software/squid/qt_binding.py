"""Which Qt binding Squid runs on, chosen before qtpy is first imported.

qtpy picks its binding on its first import and ignores QT_API after that, so main_hcs.py calls
select_qt_api() before importing anything Qt. This module must not import qtpy itself.

Order of precedence: the --qt-api command line option, then the SQUID_QT_API environment variable, then
PyQt5. The command line option survives Squid's restart after a settings change, which re-runs main_hcs.py
with the same arguments.
"""

import os
from typing import Mapping, Optional, Sequence

SUPPORTED_QT_APIS = ("pyqt5", "pyqt6")
DEFAULT_QT_API = "pyqt5"
ENV_VAR = "SQUID_QT_API"
OPTION = "--qt-api"


def qt_api_from(argv: Sequence[str], environ: Mapping[str, str]) -> str:
    """The binding requested by ``--qt-api X`` / ``--qt-api=X`` in argv, else by SQUID_QT_API, else PyQt5."""
    requested: Optional[str] = None
    for i, arg in enumerate(argv):
        if arg == OPTION and i + 1 < len(argv):
            requested = argv[i + 1]
        elif arg.startswith(OPTION + "="):
            requested = arg.split("=", 1)[1]
    if requested is None:
        requested = environ.get(ENV_VAR) or DEFAULT_QT_API
    requested = requested.strip().lower()
    if requested not in SUPPORTED_QT_APIS:
        raise ValueError(
            f"Unsupported Qt binding '{requested}' (from {OPTION} or {ENV_VAR}); use one of {', '.join(SUPPORTED_QT_APIS)}"
        )
    return requested


def select_qt_api(argv: Sequence[str], environ=os.environ) -> str:
    """Set QT_API for qtpy from argv / SQUID_QT_API and return it. Call before the first qtpy import."""
    qt_api = qt_api_from(argv, environ)
    environ["QT_API"] = qt_api
    return qt_api
