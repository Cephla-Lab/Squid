"""The GUI tests' ignore_stray_qevent_call fixture drops only the frame-less zero-argument QEvent call."""

import sys

import pytest
from qtpy.QtCore import QEvent, QTimer

from tests.control.stray_qevent import is_stray_qevent_call


def _qevent_call_error():
    try:
        QEvent()
    except TypeError as e:
        return e
    raise AssertionError("QEvent() accepted no arguments")


def test_the_stray_qevent_call_is_recognised_only_without_python_frames():
    error = _qevent_call_error()
    assert is_stray_qevent_call(TypeError, error, None)
    assert not is_stray_qevent_call(TypeError, error, error.__traceback__)  # raised by Python code: a real failure


@pytest.mark.parametrize(
    "error",
    [
        TypeError("arguments did not match any overloaded call:\n  QRect(): too many arguments"),
        TypeError("'NoneType' object is not callable"),
        ValueError("arguments did not match any overloaded call:\n  QEvent(QEvent.Type): not enough arguments"),
    ],
)
def test_other_frameless_errors_are_not_recognised(error):
    assert not is_stray_qevent_call(type(error), error, None)


def test_the_message_of_the_older_pyqt5_on_ci_is_recognised():
    error = TypeError(
        "arguments did not match any overloaded call:\n"
        "  QEvent(QEvent.Type): not enough arguments\n"
        "  QEvent(QEvent): not enough arguments"
    )
    assert is_stray_qevent_call(TypeError, error, None)


@pytest.mark.usefixtures("ignore_stray_qevent_call")
def test_a_stray_qevent_call_from_the_qt_loop_does_not_fail_the_test(qtbot):
    QTimer.singleShot(0, QEvent)  # Qt calls QEvent() with no arguments: the frame-less TypeError
    qtbot.wait(50)


@pytest.mark.usefixtures("ignore_stray_qevent_call")
def test_other_exceptions_still_reach_pytest_qt(request):
    manager = request.node.qt_exception_capture_manager
    error = TypeError("something else")
    sys.excepthook(TypeError, error, None)
    assert manager.exceptions[-1][1] is error
    manager.exceptions.clear()  # recorded as expected; keep it from failing this test
