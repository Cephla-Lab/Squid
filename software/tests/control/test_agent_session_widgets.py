"""The input shield and the banner of an agent session."""

import pytest
from qtpy.QtCore import QEvent, QObject, Qt, QTimer, Signal
from qtpy.QtGui import QKeySequence, QWindow
from qtpy.QtWidgets import (
    QApplication,
    QDialog,
    QInputDialog,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QShortcut,
    QVBoxLayout,
    QWidget,
)

from control.agent_session_widgets import AgentSessionBanner, InputShield
from control.core.agent_session import SessionState

INPUT_EVENT_TYPES = [
    QEvent.MouseButtonPress, QEvent.MouseButtonRelease, QEvent.MouseButtonDblClick, QEvent.MouseMove, QEvent.Wheel,
    QEvent.KeyPress, QEvent.KeyRelease, QEvent.Shortcut, QEvent.ShortcutOverride, QEvent.ContextMenu,
    QEvent.TouchBegin, QEvent.TouchUpdate, QEvent.TouchEnd, QEvent.TabletPress, QEvent.TabletMove,
    QEvent.TabletRelease, QEvent.DragEnter, QEvent.DragMove, QEvent.DragLeave, QEvent.Drop, QEvent.InputMethod,
    QEvent.Gesture, QEvent.NativeGesture,
]  # fmt: skip


@pytest.fixture
def window(qtbot):
    win = QMainWindow()
    central = QWidget()
    layout = QVBoxLayout(central)
    win.outside = QPushButton("outside")
    win.line_edit = QLineEdit()
    layout.addWidget(win.outside)
    layout.addWidget(win.line_edit)
    win.setCentralWidget(central)
    win.banner = AgentSessionBanner()
    win.addToolBar(win.banner)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    QApplication.setActiveWindow(win)  # a shortcut fires only when its window is the active one
    return win


@pytest.fixture
def shield(window):
    """An active shield. The filter is on the application, so it is removed for the next test."""
    shield = InputShield(window.banner)
    shield.set_active(True)
    yield shield
    shield.set_active(False)


def test_shield_drops_a_click_outside_and_passes_a_click_on_the_banner(qtbot, window, shield):
    window.banner.show_state(SessionState.AGENT)
    with qtbot.assertNotEmitted(window.outside.clicked):
        qtbot.mouseClick(window.outside, Qt.LeftButton)
    with qtbot.waitSignal(window.banner.signal_take_over, timeout=1000):
        qtbot.mouseClick(window.banner.take_over_button, Qt.LeftButton)
    assert window.outside.isEnabled()  # the shield does not touch the enabled state


def test_shield_drops_keys_outside(qtbot, window, shield):
    qtbot.keyClicks(window.line_edit, "abc")
    assert window.line_edit.text() == ""


def test_inactive_shield_passes_everything(qtbot, window, shield):
    assert shield.is_active()
    shield.set_active(False)
    assert not shield.is_active()
    with qtbot.waitSignal(window.outside.clicked, timeout=1000):
        qtbot.mouseClick(window.outside, Qt.LeftButton)
    qtbot.keyClicks(window.line_edit, "abc")
    assert window.line_edit.text() == "abc"


def test_a_shortcut_does_not_fire_while_the_shield_is_active(qtbot, window):
    fired = []
    shortcut = QShortcut(QKeySequence("Ctrl+L"), window)
    shortcut.activated.connect(lambda: fired.append(1))
    qtbot.keyClick(window, Qt.Key_L, Qt.ControlModifier)
    assert fired == [1]  # the shortcut works in this test environment
    shield = InputShield(window.banner)
    shield.set_active(True)
    try:
        qtbot.keyClick(window, Qt.Key_L, Qt.ControlModifier)
    finally:
        shield.set_active(False)
    assert fired == [1]


def test_a_shortcut_of_the_banner_fires_while_the_shield_is_active(qtbot, window, shield):
    fired = []
    shortcut = QShortcut(QKeySequence("Ctrl+T"), window.banner)
    shortcut.setContext(Qt.ApplicationShortcut)
    shortcut.activated.connect(lambda: fired.append(1))
    window.banner.show_state(SessionState.AGENT)
    qtbot.keyClick(window, Qt.Key_T, Qt.ControlModifier)
    assert fired == [1]


def test_a_modal_dialog_gives_a_signal_and_stays_blocked_until_passthrough(qtbot, window, shield):
    dialog = QDialog(window)
    button = QPushButton("OK", dialog)
    button.clicked.connect(dialog.accept)
    dialog.setModal(True)
    with qtbot.waitSignal(shield.signal_modal_shown, timeout=1000):
        dialog.show()
        qtbot.waitExposed(dialog)
    qtbot.mouseClick(button, Qt.LeftButton)
    assert dialog.isVisible()
    shield.set_modal_passthrough(True)
    with qtbot.assertNotEmitted(window.outside.clicked):  # the passthrough is for the dialog only
        qtbot.mouseClick(window.outside, Qt.LeftButton)
    qtbot.mouseClick(button, Qt.LeftButton)
    qtbot.waitUntil(lambda: not dialog.isVisible(), timeout=1000)


def test_a_window_that_is_not_modal_gives_no_signal(qtbot, window, shield):
    other = QWidget()
    qtbot.addWidget(other)
    with qtbot.assertNotEmitted(shield.signal_modal_shown, wait=100):
        other.show()
        qtbot.waitExposed(other)


def test_each_activation_starts_with_no_passthrough(qtbot, window, shield):
    shield.set_modal_passthrough(True)
    shield.set_active(True)  # already active: nothing changes
    dialog = QDialog(window)
    button = QPushButton("OK", dialog)
    button.clicked.connect(dialog.accept)
    dialog.setModal(True)
    dialog.show()
    qtbot.waitExposed(dialog)
    shield.set_active(False)
    shield.set_active(True)
    qtbot.mouseClick(button, Qt.LeftButton)
    assert dialog.isVisible()
    dialog.close()


def test_shield_drops_a_click_in_a_second_top_level_window(qtbot, window, shield):
    second = QPushButton("second window")
    qtbot.addWidget(second)
    second.show()
    qtbot.waitExposed(second)
    with qtbot.assertNotEmitted(second.clicked):
        qtbot.mouseClick(second, Qt.LeftButton)


def test_shield_drops_each_type_of_input_event(window, shield):
    for event_type in INPUT_EVENT_TYPES:
        assert shield.eventFilter(window.outside, QEvent(event_type)), event_type
    for event_type in (QEvent.Paint, QEvent.Timer, QEvent.MetaCall, QEvent.FocusOut, QEvent.Close, QEvent.Show):
        assert not shield.eventFilter(window.outside, QEvent(event_type)), event_type


def test_an_event_for_the_window_of_widgets_goes_on_to_its_widget(qtbot, window, shield):
    """Qt sends a mouse or key event to the window object first, then to the widget. Only the widget is judged."""
    press = QEvent(QEvent.MouseButtonPress)
    assert not shield.eventFilter(window.windowHandle(), press)
    plain_window = QWindow()  # no widget gets its events, so nothing after this could drop them
    assert shield.eventFilter(plain_window, press)


def test_a_click_that_comes_through_the_window_system_is_judged_at_its_widget(qtbot, window, shield):
    """The path of a real click: the window system gives it to the window object, and Qt finds the widget."""
    window.banner.show_state(SessionState.AGENT)
    qtbot.wait(50)  # the banner got its place in the window
    handle = window.windowHandle()
    outside = window.outside.mapTo(window, window.outside.rect().center())
    take_over = window.banner.take_over_button.mapTo(window, window.banner.take_over_button.rect().center())
    with qtbot.assertNotEmitted(window.outside.clicked, wait=100):
        qtbot.mouseClick(handle, Qt.LeftButton, Qt.NoModifier, outside)
    with qtbot.waitSignal(window.banner.signal_take_over, timeout=1000):
        qtbot.mouseClick(handle, Qt.LeftButton, Qt.NoModifier, take_over)
    shield.set_active(False)
    with qtbot.waitSignal(window.outside.clicked, timeout=1000):  # the same click works with no shield
        qtbot.mouseClick(handle, Qt.LeftButton, Qt.NoModifier, outside)


def test_the_application_continues_to_operate_under_the_shield(qtbot, window, shield):
    class Sender(QObject):
        signal = Signal()

    seen = []
    sender = Sender()
    sender.signal.connect(lambda: seen.append("queued"), Qt.QueuedConnection)
    QTimer.singleShot(0, lambda: seen.append("timer"))
    sender.signal.emit()
    qtbot.waitUntil(lambda: sorted(seen) == ["queued", "timer"], timeout=1000)
    window.close()
    assert not window.isVisible()


def test_banner_shows_the_buttons_of_each_state(qtbot):
    banner = AgentSessionBanner()
    qtbot.addWidget(banner)
    banner.show_state(SessionState.NONE)
    assert banner.isHidden()
    banner.show_state(SessionState.AGENT)
    assert not banner.isHidden() and banner.label.text() == "The agent is driving."
    assert banner.take_over_button.isVisibleTo(banner) and banner.take_over_button.text() == "Take over"
    for button in (banner.hand_back_button, banner.end_button, banner.unlock_anyway_button):
        assert not button.isVisibleTo(banner)
    banner.show_state(SessionState.STOPPING, ["a command is in progress", "the stage is moving"])
    assert banner.label.text() == "Stopping. Waiting: a command is in progress, the stage is moving"
    for button in (banner.take_over_button, banner.hand_back_button, banner.end_button, banner.unlock_anyway_button):
        assert not button.isVisibleTo(banner)
    banner.show_state(SessionState.STOPPING, ["the stage is moving"], timed_out=True)
    assert "the stage is moving" in banner.label.text() and banner.unlock_anyway_button.isVisibleTo(banner)
    assert banner.unlock_anyway_button.text() == "Unlock anyway"
    banner.show_state(SessionState.HUMAN)
    assert banner.label.text() == "You are driving. The agent is waiting."
    assert banner.hand_back_button.isVisibleTo(banner) and banner.end_button.isVisibleTo(banner)
    assert (banner.hand_back_button.text(), banner.end_button.text()) == ("Hand back", "End session")
    assert not banner.take_over_button.isVisibleTo(banner) and not banner.unlock_anyway_button.isVisibleTo(banner)
    banner.show_state(SessionState.NONE)
    assert banner.isHidden()


def test_stopping_with_no_reason_yet_has_a_short_text(qtbot):
    banner = AgentSessionBanner()
    qtbot.addWidget(banner)
    banner.show_state(SessionState.STOPPING)
    assert banner.label.text() == "Stopping."


def test_banner_buttons_give_their_signals(qtbot):
    banner = AgentSessionBanner()
    qtbot.addWidget(banner)
    banner.show_state(SessionState.STOPPING, ["the stage is moving"], timed_out=True)
    with qtbot.waitSignal(banner.signal_unlock_anyway, timeout=1000):
        qtbot.mouseClick(banner.unlock_anyway_button, Qt.LeftButton)
    banner.show_state(SessionState.HUMAN)
    with qtbot.waitSignal(banner.signal_end, timeout=1000):
        qtbot.mouseClick(banner.end_button, Qt.LeftButton)


def test_hand_back_asks_for_one_line_and_gives_it(qtbot, monkeypatch):
    banner = AgentSessionBanner()
    qtbot.addWidget(banner)
    banner.show_state(SessionState.HUMAN)
    asked = []

    def get_text(parent, title, label):
        asked.append((parent, title, label))
        return "looked at one organism", True

    monkeypatch.setattr(QInputDialog, "getText", get_text)
    with qtbot.waitSignal(banner.signal_hand_back, timeout=1000) as blocker:
        qtbot.mouseClick(banner.hand_back_button, Qt.LeftButton)
    assert blocker.args == ["looked at one organism"]
    assert asked == [(banner, "Hand back", "What did you do? (optional)")]


def test_hand_back_that_is_cancelled_gives_no_signal(qtbot, monkeypatch):
    banner = AgentSessionBanner()
    qtbot.addWidget(banner)
    banner.show_state(SessionState.HUMAN)
    monkeypatch.setattr(QInputDialog, "getText", lambda parent, title, label: ("text that was typed", False))
    with qtbot.assertNotEmitted(banner.signal_hand_back):
        qtbot.mouseClick(banner.hand_back_button, Qt.LeftButton)


def test_the_banner_cannot_be_moved_or_hidden_from_the_toolbar_menu(qtbot, window):
    assert not window.banner.isMovable() and not window.banner.isFloatable()
    assert not window.banner.toggleViewAction().isVisible()
