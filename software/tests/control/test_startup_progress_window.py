"""Tests for the startup window, its input fence, and the summary dialog."""

import pytest
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QApplication, QPushButton, QWidget

from control.startup_progress import StartupAborted, StepResult, StepState, declare_expected_steps
from control.startup_progress_window import (
    QtStartupReporter,
    StartupProgressWindow,
    StartupSummaryDialog,
    _remediation_hint,
    format_checklist,
)


@pytest.fixture
def reporter(qtbot):
    """A QtStartupReporter that is always torn down, even if a test fails."""
    instance = QtStartupReporter(QApplication.instance(), log_path=r"C:\tmp\main_hcs.log", device_timeout_s=90.0)
    qtbot.add_widget(instance.window)
    yield instance
    instance.finish()


# ── the window ─────────────────────────────────────────────────────────────


def test_rows_render_each_state(qtbot):
    window = StartupProgressWindow(log_path="somewhere/main_hcs.log")
    qtbot.add_widget(window)

    for index, state in enumerate(StepState):
        result = StepResult(key=f"k{index}", label=f"Device {index}", state=state)
        window.update_row(result)

    assert len(window._rows) == len(list(StepState))


def test_uninterruptible_step_says_so(qtbot):
    window = StartupProgressWindow(log_path="somewhere/main_hcs.log")
    qtbot.add_widget(window)
    result = StepResult(key="home", label="Homing stage", state=StepState.CONNECTING, interruptible=False)
    window.update_row(result)

    _, _, _, detail = window._rows["home"]
    assert "cannot be interrupted" in detail.text()


def test_window_refuses_to_close_until_allowed(qtbot):
    window = StartupProgressWindow(log_path="somewhere/main_hcs.log")
    qtbot.add_widget(window)
    window.show()

    window.close()
    assert window.isVisible(), "closing mid-startup would strand open devices"

    window.allow_close()
    window.close()
    assert not window.isVisible()


# ── abort ──────────────────────────────────────────────────────────────────


def test_abort_click_only_sets_a_flag(qtbot, reporter):
    """Teardown must never run from the slot - the init stack is still live."""
    closed = []
    reporter.register_opened("camera", lambda: closed.append("camera"))
    reporter.show()

    qtbot.mouseClick(reporter.window.btn_abort, Qt.LeftButton)

    assert reporter.is_abort_requested() is True
    assert closed == [], "the abort slot must not close anything"
    assert not reporter.window.btn_abort.isEnabled()


def test_abort_surfaces_at_the_next_step(qtbot, reporter):
    reporter.show()
    qtbot.mouseClick(reporter.window.btn_abort, Qt.LeftButton)

    with pytest.raises(StartupAborted):
        with reporter.step("next_device", label="Next device"):
            pytest.fail("body must not run after abort")


# ── the input fence ────────────────────────────────────────────────────────


def test_input_aimed_elsewhere_is_discarded_not_deferred(qtbot, reporter):
    """ExcludeUserInputEvents would defer these and replay them into the main
    window later; the filter must drop them instead."""
    stray = QPushButton("Somewhere else")
    qtbot.add_widget(stray)
    stray.show()
    presses = []
    stray.clicked.connect(lambda: presses.append(1))

    reporter.show()
    qtbot.mouseClick(stray, Qt.LeftButton)
    QApplication.instance().processEvents()
    assert presses == [], "input outside the startup window must be dropped"

    # And it must not come back once the fence is removed.
    reporter.finish()
    QApplication.instance().processEvents()
    assert presses == [], "a dropped event must not be replayed after finish()"


def test_the_abort_button_still_receives_clicks_through_the_fence(qtbot, reporter):
    reporter.show()
    qtbot.mouseClick(reporter.window.btn_abort, Qt.LeftButton)
    assert reporter.is_abort_requested() is True


def test_context_manager_removes_the_fence_even_when_the_body_raises(qtbot):
    app = QApplication.instance()
    stray = QPushButton("Somewhere else")
    qtbot.add_widget(stray)
    stray.show()
    presses = []
    stray.clicked.connect(lambda: presses.append(1))

    with pytest.raises(ValueError):
        with QtStartupReporter(app, log_path="x.log") as instance:
            qtbot.add_widget(instance.window)
            instance.show()
            raise ValueError("something went wrong during startup")

    # A leaked filter would silently swallow input in every later widget.
    qtbot.mouseClick(stray, Qt.LeftButton)
    app.processEvents()
    assert presses == [1]


def test_finish_is_idempotent(qtbot, reporter):
    reporter.show()
    reporter.finish()
    reporter.finish()


def test_quit_on_last_window_closed_is_restored(qtbot, reporter):
    app = QApplication.instance()
    before = app.quitOnLastWindowClosed()
    reporter.show()
    assert app.quitOnLastWindowClosed() is False
    reporter.finish()
    assert app.quitOnLastWindowClosed() == before


# ── pumping ────────────────────────────────────────────────────────────────


def test_pump_does_not_recurse(qtbot, reporter):
    """write_and_check is also called from GUI slots; the guard must hold."""
    reporter.show()
    depth = {"max": 0, "current": 0}
    original = reporter._app.processEvents

    def counting_process_events(*a, **kw):
        depth["current"] += 1
        depth["max"] = max(depth["max"], depth["current"])
        reporter.pump(force=True)  # re-entrant call, must be a no-op
        depth["current"] -= 1
        return original(*a, **kw)

    reporter._app.processEvents = counting_process_events
    try:
        reporter.pump(force=True)
    finally:
        reporter._app.processEvents = original

    assert depth["max"] == 1


def test_sleep_pumps_and_returns(qtbot, reporter):
    reporter.show()
    pumps = []
    original = reporter.pump
    reporter.pump = lambda force=False: pumps.append(1) or original(force)
    reporter.sleep(0.1)
    assert pumps, "sleep must keep the window painted"


def test_state_changes_reach_the_window(qtbot, reporter):
    reporter.show()
    with reporter.step("camera", label="Main camera"):
        pass
    _, name, state, _ = reporter.window._rows["camera"]
    assert name.text() == "Main camera"
    assert state.text() == "Ready"


# ── the summary dialog ─────────────────────────────────────────────────────


def make_failed_results():
    from control.startup_progress import StartupReporter

    reporter = StartupReporter()
    with reporter.step("microcontroller", label="Microcontroller"):
        pass
    with reporter.step("spinning_disk", label="Spinning disk (Cicero)") as progress:
        progress.not_found("no serial port found for SN='A5065KURA'")
        raise RuntimeError("stand-in")
    return reporter.results


def test_summary_lists_every_row_and_the_log_path(qtbot):
    results = make_failed_results()
    dialog = StartupSummaryDialog("Initialization failed.", results, r"C:\logs\main_hcs.log")
    qtbot.add_widget(dialog)

    text = dialog.details_text()
    assert "Microcontroller" in text
    assert "Spinning disk (Cicero)" in text
    assert "no serial port found" in text
    assert r"C:\logs\main_hcs.log" in text


def test_missing_device_gets_a_power_and_cable_hint():
    result = StepResult(key="spinning_disk", label="Spinning disk (Cicero)", state=StepState.NOT_FOUND)
    hint = _remediation_hint(result)
    assert "powered on" in hint
    assert "Spinning disk (Cicero)" in hint


def test_warmup_timeout_gets_a_wait_and_retry_hint():
    class SerialDeviceTimeout(Exception):
        pass

    result = StepResult(
        key="light_source",
        label="LDI laser engine",
        state=StepState.FAILED,
        detail="'run!' still returning 'ERR=System in Warmup State' after 90.0s",
        exception=SerialDeviceTimeout(),
    )
    hint = _remediation_hint(result)
    assert "warming up" in hint
    assert "Startup device timeout" in hint


def test_a_healthy_row_gets_no_hint():
    assert _remediation_hint(StepResult(key="camera", label="Main camera", state=StepState.READY)) is None


def test_summary_shows_the_hint_panel_only_when_there_is_something_to_say(qtbot):
    from qtpy.QtWidgets import QLabel

    def hint_text(results):
        dialog = StartupSummaryDialog("headline", results, "log.txt")
        qtbot.add_widget(dialog)
        return " ".join(label.text() for label in dialog.findChildren(QLabel))

    assert "What to check" in hint_text(make_failed_results())

    from control.startup_progress import StartupReporter

    clean = StartupReporter()
    with clean.step("camera", label="Main camera"):
        pass
    assert "What to check" not in hint_text(clean.results)


def test_checklist_formatting_is_aligned():
    results = make_failed_results()
    text = format_checklist(results)
    lines = text.splitlines()
    assert len(lines) == 2
    assert all("." in line for line in lines)
    assert "Not found" in text
