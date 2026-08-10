import importlib.util
import os

from squid.qt_binding import select_qt_api


def test_explicit_qt_api_wins(monkeypatch):
    monkeypatch.setenv("QT_API", "pyqt5")
    monkeypatch.delenv("PYTEST_QT_API", raising=False)

    assert select_qt_api() == "pyqt5"
    assert os.environ["QT_API"] == "pyqt5"
    # pytest-qt must agree with qtpy or the process loads two bindings.
    assert os.environ["PYTEST_QT_API"] == "pyqt5"


def test_prefers_pyqt6_when_available(monkeypatch):
    monkeypatch.delenv("QT_API", raising=False)
    monkeypatch.delenv("PYTEST_QT_API", raising=False)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())

    assert select_qt_api() == "pyqt6"
    assert os.environ["QT_API"] == "pyqt6"
    assert os.environ["PYTEST_QT_API"] == "pyqt6"


def test_falls_back_to_pyqt5_without_pyqt6(monkeypatch):
    monkeypatch.delenv("QT_API", raising=False)
    monkeypatch.delenv("PYTEST_QT_API", raising=False)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)

    assert select_qt_api() == "pyqt5"
    assert os.environ["QT_API"] == "pyqt5"
    assert os.environ["PYTEST_QT_API"] == "pyqt5"


def test_existing_pytest_qt_api_not_overwritten(monkeypatch):
    monkeypatch.setenv("QT_API", "pyqt5")
    monkeypatch.setenv("PYTEST_QT_API", "pyqt6")

    select_qt_api()
    assert os.environ["PYTEST_QT_API"] == "pyqt6"
