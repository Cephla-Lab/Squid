import sys

import pytest

from squid.qt_binding import DEFAULT_QT_API, qt_api_from, qt_api_from_ini, resolve_qt_api, select_qt_api


def test_default_is_pyqt5():
    assert qt_api_from(["main_hcs.py"], {}) == DEFAULT_QT_API == "pyqt5"


@pytest.mark.parametrize("argv", [["main_hcs.py", "--qt-api", "pyqt6"], ["main_hcs.py", "--qt-api=pyqt6"]])
def test_command_line_option(argv):
    assert qt_api_from(argv, {}) == "pyqt6"


def test_environment_variable_when_no_option():
    assert qt_api_from(["main_hcs.py"], {"SQUID_QT_API": "PyQt6 "}) == "pyqt6"


def test_ini_value_when_no_option_or_environment_variable():
    assert resolve_qt_api(["main_hcs.py"], {}, "pyqt6") == ("pyqt6", "[GENERAL] qt_api")


def test_order_of_precedence_option_then_environment_then_ini():
    assert resolve_qt_api(["main_hcs.py", "--qt-api", "pyqt5"], {"SQUID_QT_API": "pyqt6"}, "pyqt6") == (
        "pyqt5",
        "--qt-api",
    )
    assert resolve_qt_api(["main_hcs.py"], {"SQUID_QT_API": "pyqt5"}, "pyqt6") == ("pyqt5", "SQUID_QT_API")
    assert resolve_qt_api(["main_hcs.py"], {}, None) == ("pyqt5", "default")


def test_option_survives_the_restart_arguments():
    # gui_hcs restarts with sys.argv plus --skip-init, so the choice must still be read from there.
    assert qt_api_from(["main_hcs.py", "--qt-api", "pyqt6", "--start-server", "--skip-init"], {}) == "pyqt6"


@pytest.mark.parametrize(
    "argv, environ, ini, source",
    [
        (["main_hcs.py", "--qt-api", "pyside6"], {}, None, "--qt-api"),
        (["main_hcs.py"], {"SQUID_QT_API": "qt6"}, None, "SQUID_QT_API"),
        (["main_hcs.py"], {}, "pyqt4", r"\[GENERAL\] qt_api"),
    ],
)
def test_unsupported_value_is_refused_naming_its_source(argv, environ, ini, source):
    with pytest.raises(ValueError, match=f"Unsupported Qt binding .*{source}"):
        qt_api_from(argv, environ, ini)


def test_empty_environment_variable_means_unset():
    assert qt_api_from(["main_hcs.py"], {"SQUID_QT_API": ""}, "pyqt6") == "pyqt6"


def _write_ini(directory, body, name="configuration_test.ini"):
    (directory / name).write_text(body, encoding="utf-8")


@pytest.mark.parametrize(
    "line, expected",
    [
        ("qt_api = pyqt6", "pyqt6"),
        ("QT_API = PyQt6", "PyQt6"),  # ConfigParser keys are case-insensitive; the value is lowered later
        ('qt_api = "pyqt6"  # Qt 6 on this machine', "pyqt6"),
    ],
)
def test_ini_value_is_read_from_general(tmp_path, line, expected):
    _write_ini(tmp_path, f"[GENERAL]\nother = 1\n{line}\n\n[CAMERA_CONFIG]\nqt_api = ignored\n")
    assert qt_api_from_ini(str(tmp_path)) == expected


@pytest.mark.parametrize(
    "files",
    [
        {},  # no ini
        {"configuration_a.ini": "[GENERAL]\nother = 1\n"},  # key unset
        {"configuration_a.ini": "[CAMERA_CONFIG]\nqt_api = pyqt6\n"},  # not in [GENERAL]
        {"configuration_a.ini": "[GENERAL]\nqt_api = pyqt6\n", "configuration_b.ini": "[GENERAL]\nqt_api = pyqt6\n"},
        {"configuration_a.ini": "not an ini file"},  # unparsable: left to control._def to report
    ],
)
def test_ini_value_is_none_when_not_set_or_ambiguous(tmp_path, files):
    for name, body in files.items():
        _write_ini(tmp_path, body, name)
    assert qt_api_from_ini(str(tmp_path)) is None


def test_select_reads_the_ini_and_sets_qt_api_for_qtpy(tmp_path):
    _write_ini(tmp_path, "[GENERAL]\nqt_api = pyqt6\n")
    environ = {"QT_API": "pyqt5"}
    assert select_qt_api(["main_hcs.py"], environ, str(tmp_path)) == ("pyqt6", "[GENERAL] qt_api")
    assert environ["QT_API"] == "pyqt6"


def _write_cache(directory, cached_path):
    (directory / "cache").mkdir(exist_ok=True)
    (directory / "cache" / "config_file_path.txt").write_text(cached_path, encoding="utf-8")


@pytest.mark.parametrize(
    "cached",
    [
        "./configuration_b.ini",
        "configuration_b.ini",
        # The form control._def writes on Windows. On POSIX a backslash is part of the file name (and Squid
        # never writes one there), so this case is Windows-only.
        pytest.param(
            ".\\configuration_b.ini",
            marks=pytest.mark.skipif(sys.platform != "win32", reason="backslash is a path separator only on Windows"),
        ),
    ],
)
def test_several_inis_use_the_cached_one_like_control_def(tmp_path, cached):
    """With several configuration*.ini, control._def loads the one cache/config_file_path.txt names."""
    _write_ini(tmp_path, "[GENERAL]\nqt_api = pyqt5\n", "configuration_a.ini")
    _write_ini(tmp_path, "[GENERAL]\nqt_api = pyqt6\n", "configuration_b.ini")
    _write_cache(tmp_path, cached)

    assert qt_api_from_ini(str(tmp_path)) == "pyqt6"
    assert select_qt_api(["main_hcs.py"], {}, str(tmp_path)) == ("pyqt6", "[GENERAL] qt_api")


@pytest.mark.parametrize("cached", ["./configuration_c.ini", ""])
def test_several_inis_and_a_cache_naming_none_of_them_is_unset(tmp_path, cached):
    """control._def exits in this case; the selector just reads no ini."""
    _write_ini(tmp_path, "[GENERAL]\nqt_api = pyqt6\n", "configuration_a.ini")
    _write_ini(tmp_path, "[GENERAL]\nqt_api = pyqt6\n", "configuration_b.ini")
    _write_cache(tmp_path, cached)

    assert qt_api_from_ini(str(tmp_path)) is None


def test_single_ini_ignores_the_cache(tmp_path):
    _write_ini(tmp_path, "[GENERAL]\nqt_api = pyqt6\n", "configuration_a.ini")
    _write_cache(tmp_path, "./configuration_old.ini")

    assert qt_api_from_ini(str(tmp_path)) == "pyqt6"
