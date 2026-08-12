"""Select the Qt binding for qtpy and pytest-qt.

Must be imported and called before the first ``qtpy`` import anywhere in the
process. Deliberately does not import any Qt binding itself: qtpy latches
onto an already-imported binding regardless of QT_API, so availability is
probed with ``importlib.util.find_spec`` instead.
"""

import importlib.util
import os


def select_qt_api() -> str:
    """Set QT_API/PYTEST_QT_API so qtpy and pytest-qt agree on one binding.

    Preference order:
    1. An explicit QT_API environment variable always wins.
    2. PyQt6, when installed.
    3. PyQt5 otherwise.

    Returns the selected api name (e.g. "pyqt6").
    """
    api = os.environ.get("QT_API")
    if not api:
        api = "pyqt6" if importlib.util.find_spec("PyQt6") is not None else "pyqt5"
        os.environ["QT_API"] = api
    os.environ.setdefault("PYTEST_QT_API", api)
    return api
