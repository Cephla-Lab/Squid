"""The startup window and its Qt reporter (control/startup_progress_window.py)."""

import pytest
from qtpy.QtCore import QEvent, QPoint, Qt
from qtpy.QtGui import QMouseEvent
from qtpy.QtWidgets import QApplication, QPushButton

from control.startup_progress import StartupAborted, StepResult, StepState
from control.startup_progress_window import (
    QtStartupReporter,
    StartupSummaryDialog,
    _remediation_hint,
    format_checklist,
)


@pytest.fixture
def reporter(qtbot):
    app = QApplication.instance()
    quit_on_last = app.quitOnLastWindowClosed()
    reporter = QtStartupReporter(app, log_path="/tmp/squid-test/main_hcs.log")
    qtbot.addWidget(reporter.window)
    yield reporter
    reporter.finish()
    assert app.quitOnLastWindowClosed() == quit_on_last


def row_texts(reporter, key):
    dot, name, state, detail = reporter.window._rows[key]
    return name.text(), state.text(), detail.text()


def click(widget):
    for kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
        event = QMouseEvent(kind, QPoint(2, 2), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(widget, event)


def test_rows_follow_the_steps(reporter):
    reporter.declare("camera", "Main camera", core=True)
    reporter.declare("prep_home_xyz", "Homing stage", core=True, interruptible=False)
    reporter.show()
    assert row_texts(reporter, "camera")[:2] == ("Main camera", "Pending")

    with reporter.step("camera", core=True):
        assert row_texts(reporter, "camera")[1] == "Connecting…"
    assert row_texts(reporter, "camera")[1] == "Ready"

    with reporter.step("prep_home_xyz", core=True, interruptible=False):
        assert row_texts(reporter, "prep_home_xyz")[2] == "cannot be interrupted"
        assert "cannot be interrupted" in reporter.window._hint.text()

    with reporter.step("cellx", label="CellX"):
        raise RuntimeError("no port")
    assert row_texts(reporter, "cellx")[1:] == ("Failed", "RuntimeError: no port")


def test_abort_button_only_sets_the_flag(reporter):
    reporter.show()
    with reporter.step("light_source", label="LDI"):
        click(reporter.window.btn_abort)
        assert reporter.is_abort_requested()
        assert not reporter.window.btn_abort.isEnabled()
    with pytest.raises(StartupAborted):
        with reporter.step("camera"):
            pass


def test_input_to_other_windows_is_dropped_while_starting_and_restored_after(reporter, qtbot):
    other = QPushButton("main window button")
    qtbot.addWidget(other)
    clicks = []
    other.clicked.connect(lambda: clicks.append(1))
    other.show()

    reporter.show()
    click(other)
    assert clicks == []  # discarded, not deferred into the main window

    reporter.finish()
    click(other)
    assert clicks == [1]


def test_the_window_cannot_be_closed_until_startup_finishes(reporter):
    reporter.show()
    reporter.window.close()
    assert reporter.window.isVisible()
    reporter.finish()
    assert not reporter.window.isVisible()
    reporter.pump(force=True)  # a late pump after finish() is a no-op


def test_sleep_pumps_and_returns(reporter):
    reporter.show()
    reporter.sleep(0.05)
    reporter.sleep(0)


def test_checklist_text_and_hints():
    missing = StepResult("spinning_disk", "Spinning disk", StepState.FAILED, "SerialPortNotFoundError: ...")
    missing.exception = type("SerialPortNotFoundError", (Exception,), {})()
    warming = StepResult(
        "light_source", "LDI laser engine", StepState.FAILED, "still returning 'ERR=System in Warmup State'"
    )
    warming.exception = type("SerialDeviceTimeout", (Exception,), {})()
    ready = StepResult("camera", "Main camera", StepState.READY, elapsed_s=1.23)

    text = format_checklist([ready, missing])
    assert "Main camera" in text and "Ready" in text and "1.2 s" in text
    assert "Spinning disk" in text and "Failed" in text

    assert "powered on" in _remediation_hint(missing)
    assert "warming up" in _remediation_hint(warming)
    assert _remediation_hint(ready) is None


def test_summary_dialog_lists_every_row(qtbot):
    results = [
        StepResult("camera", "Main camera", StepState.READY),
        StepResult("cellx", "CellX laser combiner", StepState.NOT_FOUND, "no serial port"),
    ]
    dialog = StartupSummaryDialog("Initialization failed.", results, "/tmp/squid-test/main_hcs.log")
    qtbot.addWidget(dialog)
    details = dialog.details_text()
    assert "Initialization failed." in details
    assert "CellX laser combiner" in details and "Not found" in details
    assert "/tmp/squid-test/main_hcs.log" in details
