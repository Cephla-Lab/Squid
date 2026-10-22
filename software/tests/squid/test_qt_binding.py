import pytest

from squid.qt_binding import DEFAULT_QT_API, qt_api_from, select_qt_api


def test_default_is_pyqt5():
    assert qt_api_from(["main_hcs.py"], {}) == DEFAULT_QT_API == "pyqt5"


@pytest.mark.parametrize("argv", [["main_hcs.py", "--qt-api", "pyqt6"], ["main_hcs.py", "--qt-api=pyqt6"]])
def test_command_line_option(argv):
    assert qt_api_from(argv, {}) == "pyqt6"


def test_environment_variable_when_no_option():
    assert qt_api_from(["main_hcs.py"], {"SQUID_QT_API": "PyQt6 "}) == "pyqt6"


def test_option_wins_over_environment_variable():
    assert qt_api_from(["main_hcs.py", "--qt-api", "pyqt5"], {"SQUID_QT_API": "pyqt6"}) == "pyqt5"


def test_option_survives_the_restart_arguments():
    # gui_hcs restarts with sys.argv plus --skip-init, so the choice must still be read from there.
    assert qt_api_from(["main_hcs.py", "--qt-api", "pyqt6", "--start-server", "--skip-init"], {}) == "pyqt6"


@pytest.mark.parametrize("value", ["pyside6", "qt6", ""])
def test_unsupported_value_is_refused(value):
    if value == "":
        assert qt_api_from(["main_hcs.py"], {"SQUID_QT_API": value}) == "pyqt5"  # empty means unset
        return
    with pytest.raises(ValueError, match="Unsupported Qt binding"):
        qt_api_from(["main_hcs.py", "--qt-api", value], {})


def test_select_sets_qt_api_for_qtpy():
    environ = {"QT_API": "pyqt5"}
    assert select_qt_api(["main_hcs.py", "--qt-api", "pyqt6"], environ) == "pyqt6"
    assert environ["QT_API"] == "pyqt6"
