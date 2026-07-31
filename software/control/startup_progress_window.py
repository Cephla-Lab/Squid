"""The startup status window, and the summary dialog shown when startup fails.

Initialization runs on the Qt main thread with no event loop (see main_hcs.py),
so this window is kept painted and its Abort button clickable by pumping
`QApplication.processEvents()` from progress updates and from the new serial
wait loops.  Three things make that safe:

  * The Abort button only sets a flag.  Teardown happens in main_hcs.py after
    the initialization stack has unwound - closing devices from inside a slot
    fired mid-`processEvents()` would pull them out from under the call still
    running below.
  * An application-level event filter *discards* user input aimed anywhere but
    this window.  `QEventLoop.ExcludeUserInputEvents` is not good enough: it
    defers input rather than dropping it, so an impatient user's clicks would
    be replayed into the main window the moment it appears.
  * A re-entrancy guard, because `SerialDevice.write_and_check` is also called
    from GUI slots at runtime and must never recurse into the pump.

`QtStartupReporter` is a context manager and should always be used as one - if
`finish()` were skipped the event filter would survive and silently swallow
input in every later widget.
"""

import os
import time
from typing import Callable, List, Optional

from qtpy.QtCore import QEvent, QObject, Qt, QUrl, Signal
from qtpy.QtGui import QDesktopServices
from qtpy.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import squid.logging
from control.startup_progress import (
    StartupAborted,
    StartupReporter,
    StepResult,
    StepState,
    state_color,
    state_label,
)

log = squid.logging.get_logger(__name__)

_USER_INPUT_EVENTS = frozenset(
    {
        QEvent.MouseButtonPress,
        QEvent.MouseButtonRelease,
        QEvent.MouseButtonDblClick,
        QEvent.KeyPress,
        QEvent.KeyRelease,
        QEvent.Wheel,
        QEvent.TouchBegin,
        QEvent.TouchUpdate,
        QEvent.TouchEnd,
        QEvent.ContextMenu,
    }
)


def format_checklist(results: List[StepResult], width: int = 34) -> str:
    """Render the checklist as plain text, for the summary dialog and clipboard."""
    lines = []
    for r in results:
        dots = "." * max(1, width - len(r.label))
        line = f"{r.label} {dots} {state_label(r.state):<12}"
        if r.state == StepState.READY and r.elapsed_s >= 0.05:
            line += f"{r.elapsed_s:6.1f} s"
        if r.detail:
            line += f"  {r.detail}"
        lines.append(line.rstrip())
    return "\n".join(lines)


def _remediation_hint(result: StepResult) -> Optional[str]:
    """Turn a failure into something the person in front of the scope can do."""
    name = type(result.exception).__name__ if result.exception is not None else ""
    detail = result.detail or ""
    if name == "SerialPortNotFoundError" or result.state == StepState.NOT_FOUND:
        return (
            f"{result.label}: the device did not appear as a serial port. "
            "Check that it is powered on and its USB cable is connected, then relaunch."
        )
    if name == "SerialDeviceTimeout":
        if "warm" in detail.lower():
            return (
                f"{result.label}: still warming up when the wait expired. Give it about a "
                "minute and relaunch, or raise Settings > Settings... > Startup device timeout."
            )
        return f"{result.label}: the device stopped responding. Power-cycle it and relaunch."
    if name in ("TimeoutError", "SquidTimeout"):
        return f"{result.label}: the move never completed. Check for an obstruction on the stage, then relaunch."
    return None


class _StartupInputFilter(QObject):
    """Discards user input and close requests not aimed at the startup window."""

    def __init__(self, window: QWidget, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._window = window

    def _belongs_to_startup(self, obj: QObject) -> bool:
        window = self._window
        if window is None:
            return False
        if obj is window:
            return True
        if isinstance(obj, QWidget):
            return window.isAncestorOf(obj)
        handle = window.windowHandle()
        return handle is not None and obj is handle

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        event_type = event.type()
        if event_type not in _USER_INPUT_EVENTS and event_type != QEvent.Close:
            return False
        if self._belongs_to_startup(obj):
            return False
        # True == "handled", which drops the event rather than deferring it.
        return True


class StartupProgressWindow(QDialog):
    """Live checklist of what initialization is doing, with an Abort button."""

    signal_abort_requested = Signal()

    def __init__(self, log_path: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._log_path = log_path
        self._rows = {}
        self._allow_close = False
        self._row_count = 0
        # No close button: the only way out during startup is Abort, which
        # unwinds cleanly.  A stray window close would strand open devices.
        self.setWindowFlags(Qt.Dialog | Qt.CustomizeWindowHint | Qt.WindowTitleHint | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("Starting Squid")
        self.setMinimumSize(620, 420)
        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        self._headline = QLabel("Initializing hardware…")
        self._headline.setStyleSheet("font-size: 12pt; font-weight: bold;")
        layout.addWidget(self._headline)

        self._elapsed_label = QLabel("")
        self._elapsed_label.setStyleSheet("color: #888888;")
        layout.addWidget(self._elapsed_label)

        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 4, 0, 4)
        self._grid.setHorizontalSpacing(10)
        self._grid.setVerticalSpacing(3)
        self._grid.setColumnStretch(3, 1)

        self._scroll = QScrollArea()
        self._scroll.setWidget(self._grid_host)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.StyledPanel)
        layout.addWidget(self._scroll, 1)

        button_row = QHBoxLayout()
        self._hint = QLabel("")
        self._hint.setStyleSheet("color: #888888;")
        self._hint.setWordWrap(True)
        button_row.addWidget(self._hint, 1)
        self.btn_abort = QPushButton("Abort Initialization")
        button_row.addWidget(self.btn_abort)
        layout.addLayout(button_row)

    def _connect_signals(self) -> None:
        self.btn_abort.clicked.connect(self._on_abort_clicked)

    def _on_abort_clicked(self) -> None:
        self.btn_abort.setEnabled(False)
        self.btn_abort.setText("Aborting…")
        self._headline.setText("Aborting — releasing hardware…")
        self.signal_abort_requested.emit()

    def add_row(self, result: StepResult) -> None:
        if result.key in self._rows:
            return
        dot = QLabel("●")
        name = QLabel(result.label)
        state = QLabel(state_label(result.state))
        detail = QLabel("")
        detail.setStyleSheet("font-family: monospace; color: #666666;")
        detail.setWordWrap(True)
        detail.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row = self._row_count
        self._row_count += 1
        self._grid.addWidget(dot, row, 0)
        self._grid.addWidget(name, row, 1)
        self._grid.addWidget(state, row, 2)
        self._grid.addWidget(detail, row, 3)
        self._rows[result.key] = (dot, name, state, detail)
        self.update_row(result)

    def update_row(self, result: StepResult) -> None:
        widgets = self._rows.get(result.key)
        if widgets is None:
            self.add_row(result)
            return
        dot, name, state, detail = widgets
        color = state_color(result.state)
        dot.setStyleSheet(f"font-size: 13pt; color: {color};")
        state.setText(state_label(result.state))
        state.setStyleSheet(f"color: {color};")
        name.setText(result.label)

        text = result.detail
        if result.state == StepState.READY and result.elapsed_s >= 0.05:
            text = text or f"{result.elapsed_s:.1f} s"
        elif result.state == StepState.CONNECTING and not result.interruptible:
            # Say so, rather than let an honestly-blocked window read as hung.
            text = text or "cannot be interrupted"
        detail.setText(text)

        if result.state in (StepState.CONNECTING, StepState.WARMING_UP):
            self._headline.setText(f"{result.label}…")
            self._scroll.ensureWidgetVisible(name)
            self._hint.setText(
                "" if result.interruptible else "This step cannot be interrupted; Abort takes effect when it finishes."
            )

    def update_elapsed(self, seconds: float) -> None:
        self._elapsed_label.setText(f"Elapsed {seconds:.0f} s   ·   log: {self._log_path}")

    def allow_close(self) -> None:
        self._allow_close = True

    def closeEvent(self, event) -> None:
        if self._allow_close:
            event.accept()
        else:
            event.ignore()


class StartupSummaryDialog(QDialog):
    """Shown when startup could not finish.  Names every problem, then quits."""

    def __init__(
        self,
        headline: str,
        results: List[StepResult],
        log_path: str,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._results = results
        self._log_path = log_path
        self._headline_text = headline
        self.setWindowTitle("Squid could not start")
        self.setMinimumSize(720, 520)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self._setup_ui()
        self._connect_signals()

    def details_text(self) -> str:
        parts = [self._headline_text, "", format_checklist(self._results), "", f"Log file: {self._log_path}"]
        return "\n".join(parts)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        headline = QLabel(self._headline_text)
        headline.setWordWrap(True)
        headline.setStyleSheet("font-size: 12pt; font-weight: bold;")
        layout.addWidget(headline)

        hints = [h for h in (_remediation_hint(r) for r in self._results if r.failed) if h]
        if hints:
            hint_label = QLabel("What to check:\n" + "\n".join(f"  • {h}" for h in hints))
            hint_label.setWordWrap(True)
            hint_label.setStyleSheet("background-color: #fff3cd; color: #000000; padding: 10px; border-radius: 4px;")
            layout.addWidget(hint_label)

        self._checklist = QPlainTextEdit(format_checklist(self._results))
        self._checklist.setReadOnly(True)
        self._checklist.setStyleSheet("font-family: monospace;")
        layout.addWidget(self._checklist, 1)

        path_label = QLabel(f"Full log: {self._log_path}")
        path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        path_label.setWordWrap(True)
        path_label.setStyleSheet("color: #666666;")
        layout.addWidget(path_label)

        button_row = QHBoxLayout()
        self.btn_copy = QPushButton("Copy details")
        self.btn_open_log = QPushButton("Open log folder")
        self.btn_quit = QPushButton("Quit")
        self.btn_quit.setDefault(True)
        button_row.addWidget(self.btn_copy)
        button_row.addWidget(self.btn_open_log)
        button_row.addStretch(1)
        button_row.addWidget(self.btn_quit)
        layout.addLayout(button_row)

    def _connect_signals(self) -> None:
        self.btn_copy.clicked.connect(self._on_copy)
        self.btn_open_log.clicked.connect(self._on_open_log)
        self.btn_quit.clicked.connect(self.accept)

    def _on_copy(self) -> None:
        QApplication.clipboard().setText(self.details_text())
        self.btn_copy.setText("Copied")

    def _on_open_log(self) -> None:
        folder = os.path.dirname(os.path.abspath(self._log_path))
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))


class QtStartupReporter(StartupReporter):
    """StartupReporter that drives `StartupProgressWindow`.

    Main-thread only.  Always use as a context manager so the application-level
    event filter is removed even if the body raises.
    """

    MIN_PUMP_INTERVAL_S = 0.05

    def __init__(self, app: QApplication, log_path: str, device_timeout_s: float = 90.0):
        super().__init__(device_timeout_s=device_timeout_s)
        self._app = app
        self._log_path = log_path
        self.window = StartupProgressWindow(log_path)
        self.window.signal_abort_requested.connect(self.request_abort)
        self._filter: Optional[_StartupInputFilter] = None
        self._in_pump = False
        self._last_pump = 0.0
        self._finished = False
        self._laser_engine = None
        self._prev_quit_on_last_window = app.quitOnLastWindowClosed()

    # ── lifecycle ──────────────────────────────────────────────────────────

    def __enter__(self) -> "QtStartupReporter":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.finish()
        return False

    def show(self) -> None:
        # show(), never exec_(): a nested event loop would never give control
        # back to the initialization code below.
        self._app.setQuitOnLastWindowClosed(False)
        self._filter = _StartupInputFilter(self.window)
        self._app.installEventFilter(self._filter)
        self.window.show()
        self.pump(force=True)

    def finish(self) -> None:
        """Remove the fence and close the window.  Idempotent."""
        if self._finished:
            return
        self._finished = True
        if self._filter is not None:
            self._app.removeEventFilter(self._filter)
            self._filter = None
        self._app.setQuitOnLastWindowClosed(self._prev_quit_on_last_window)
        self._detach_laser_engine()
        self.window.allow_close()
        self.window.close()

    # ── cooperative pumping ────────────────────────────────────────────────

    def pump(self, force: bool = False) -> None:
        if self._in_pump or self._finished:
            return
        now = time.monotonic()
        if not force and (now - self._last_pump) < self.MIN_PUMP_INTERVAL_S:
            return
        self._in_pump = True
        try:
            self._last_pump = now
            self.window.update_elapsed(self.elapsed_s)
            self._app.processEvents()
        finally:
            self._in_pump = False

    def sleep(self, seconds: float) -> None:
        """Sleep while keeping the window painted and Abort clickable."""
        if seconds <= 0:
            self.pump()
            return
        deadline = time.monotonic() + seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self.pump()
            time.sleep(min(0.02, remaining))
        self.pump()

    def _on_changed(self, result: StepResult) -> None:
        self.window.update_row(result)
        self.pump()

    # ── laser engine live status ───────────────────────────────────────────

    def attach_laser_engine(self, engine) -> None:
        """Mirror the engine's live per-channel status into its row."""
        if engine is None:
            return
        self._laser_engine = engine
        engine.status_updated.connect(self._on_laser_engine_status)

    def _on_laser_engine_status(self, status) -> None:
        result = self.get("laser_engine")
        if result is None or result.finished:
            return
        try:
            states = sorted({info.display_state.name for info in status.channels.values()})
            self.set_state("laser_engine", StepState.WARMING_UP, ", ".join(states))
        except Exception:
            log.debug("Could not format laser engine status for the startup window.", exc_info=True)

    def _detach_laser_engine(self) -> None:
        engine = self._laser_engine
        self._laser_engine = None
        if engine is None:
            return
        try:
            engine.status_updated.disconnect(self._on_laser_engine_status)
        except TypeError:
            # PyQt raises TypeError when the slot wasn't connected. RuntimeError
            # would mean the C++ object is gone - let that surface.
            pass

    # ── failure reporting ──────────────────────────────────────────────────

    def show_summary(self, error: BaseException) -> None:
        """Close the progress window and show the summary.  Blocks until Quit."""
        if isinstance(error, StartupAborted):
            what = error.step_label or error.step_key or "startup"
            headline = f"Initialization was aborted while waiting for: {what}.\nThe software will now exit."
        else:
            headline = f"Initialization failed. The software will now exit.\n\n{error}"
        results = self.results
        self.finish()
        dialog = StartupSummaryDialog(headline, results, self._log_path)
        dialog.exec_()
