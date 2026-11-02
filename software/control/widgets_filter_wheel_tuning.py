"""Utils > Filter Wheel Tuning...: Verify and Tune the Squid (Cephla) filter wheel from the GUI.

The decisions live in squid/filter_wheel_tuning.py, which the bench tool runs too; this file is the dialog around
them. It runs the work on the application's EXISTING Microcontroller and SquidFilterWheel - nothing is reset or
re-initialised - in a worker thread, so the window stays responsive and Cancel is answered.

However a run ends (pass, fail, cancel, exception) the session's restore puts the machine back: the driver settings
and ramp profile the run changed, encoder reporting off, the configured completion window, a home through the wheel
controller so its position record and turn counter are valid again, and a return to the slot the user was on.

After a successful Tune the proposal is shown next to what the machine runs now, with the measured time per slot.
Nothing is written until "Apply and save" is clicked; that writes the ini (with a backup), puts the profile into the
values the running software reads, re-configures the driver, re-homes and comes back to the user's slot.
"""

from typing import Callable, Optional

from qtpy.QtCore import Qt, Signal, QThread
from qtpy.QtWidgets import (
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

import squid.logging
from squid.filter_wheel_tuning import WheelTuningSession, machine_profile, refusal_reason

log = squid.logging.get_logger(__name__)

TUNING_OUT_DIR = "cache/filter_wheel_tuning"


def _search_note(rec) -> str:
    """How the proposal was arrived at, for the line under it: where steps were first lost, and whether the top speed
    had to come down."""
    between = rec.get("stall_edge_between")
    if rec.get("stall_edge_found") and between:
        note = (
            f"  (steps were lost above {between[0]:g} rev/s², first at {between[1]:g}; "
            f"margin {rec.get('margin', 0):g} applied)"
        )
    elif rec.get("stall_edge_found"):
        note = ""
    else:
        note = "  (no stall edge inside the ladder: the gentlest level as fast as the fastest)"
    if rec.get("speed_reduced"):
        others = "; ".join(
            f"{x['vmax']:g} rev/s: "
            + (f"{x['adjacent_ms_median']:.0f} ms per slot" if x.get("pass") else "nothing held")
            for x in rec.get("speeds_tried", [])
        )
        note += f"  TOP SPEED REDUCED: the wheel is faster with a lower top speed ({others})."
    return note


class TuningWorker(QThread):
    """Runs one session call off the GUI thread. Never raises into Qt: the outcome, exception included, comes back
    through signal_finished."""

    signal_log = Signal(str)
    signal_finished = Signal(object)  # the run's summary dict, or the Exception that ended it

    def __init__(self, session: WheelTuningSession, action: str, apply_record=None, parent=None):
        super().__init__(parent)
        self.session = session
        self.action = action
        self.apply_record = apply_record

    def run(self):
        try:
            if self.action == "apply":
                result = {"applied": self.session.apply(self.apply_record)}
            else:
                result = self.session.run(self.action)
        except Exception as e:  # noqa: BLE001
            log.error(f"Filter wheel {self.action} failed", exc_info=True)
            result = e
        self.signal_finished.emit(result)


def _profile_text(profile) -> str:
    return (
        f"{int(profile['microstepping_default_w'])} usteps/FS, "
        f"{float(profile['max_velocity_w_mm']):g} rev/s, "
        f"{float(profile['max_acceleration_w_mm']):g} rev/s²"
    )


def _timing_text(by_distance) -> str:
    if not by_distance:
        return ""
    return ", ".join(f"{k} slot{'s' if int(k) > 1 else ''} {v:.0f} ms" for k, v in by_distance.items())


class FilterWheelTuningDialog(QDialog):
    """Verify / Tune for the Squid filter wheel.

    Args:
        microcontroller: the application's controller. Not reset, not re-initialised.
        filter_wheel: the application's SquidFilterWheel. The run goes around it (it drives the W axis directly),
            but the restore goes through it, because it owns the wheel's position record.
        wheel_id: which configured wheel; only the first (W) can be tuned, W2 has no encoder interface here.
        busy_reason: called before a run; returns a sentence saying why the instrument is busy, or None. The GUI
            passes live view and acquisition state this way so this dialog does not have to know about either.
    """

    def __init__(
        self,
        microcontroller,
        filter_wheel,
        wheel_id: int = 1,
        busy_reason: Optional[Callable[[], Optional[str]]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Filter Wheel Tuning")
        self.microcontroller = microcontroller
        self.filter_wheel = filter_wheel
        self.wheel_id = wheel_id
        self.busy_reason = busy_reason
        self.worker: Optional[TuningWorker] = None
        self.proposal = None  # the tune result awaiting "Apply and save"
        self.session = WheelTuningSession(
            microcontroller, filter_wheel, wheel_id=wheel_id, log_fn=None, out_dir=TUNING_OUT_DIR
        )
        self._build()
        self._refresh_current()

    # ---------------------------------------------------------------- layout
    def _build(self):
        layout = QVBoxLayout()

        current_box = QGroupBox("This machine")
        grid = QGridLayout()
        self.label_current = QLabel("-")
        self.label_current_extra = QLabel("-")
        grid.addWidget(QLabel("Profile:"), 0, 0)
        grid.addWidget(self.label_current, 0, 1)
        grid.addWidget(QLabel("Also:"), 1, 0)
        grid.addWidget(self.label_current_extra, 1, 1)
        grid.setColumnStretch(1, 1)
        current_box.setLayout(grid)
        layout.addWidget(current_box)

        buttons = QHBoxLayout()
        self.button_verify = QPushButton("Verify")
        self.button_verify.setToolTip(
            "Run the endurance pattern (96 slot changes) at this machine's settings and report PASS or FAIL "
            "on lost steps."
        )
        self.button_verify.clicked.connect(lambda: self._start("verify"))
        self.button_tune = QPushButton("Tune")
        self.button_tune.setToolTip(
            "Find this wheel's own profile: screen the acceleration ladder, back off from the stall edge and "
            "confirm over 96 moves. Several minutes. Nothing is saved without your click."
        )
        self.button_tune.clicked.connect(lambda: self._start("tune"))
        self.button_cancel = QPushButton("Cancel")
        self.button_cancel.setToolTip("Stop after the move that is running; the machine is put back either way.")
        self.button_cancel.setEnabled(False)
        self.button_cancel.clicked.connect(self._cancel)
        buttons.addWidget(self.button_verify)
        buttons.addWidget(self.button_tune)
        buttons.addWidget(self.button_cancel)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.label_result = QLabel("")
        self.label_result.setWordWrap(True)
        self.label_result.setTextFormat(Qt.PlainText)
        layout.addWidget(self.label_result)

        self.proposal_box = QGroupBox("Proposed profile")
        proposal_layout = QGridLayout()
        self.label_proposed = QLabel("-")
        self.label_proposed_timing = QLabel("-")
        proposal_layout.addWidget(QLabel("Now:"), 0, 0)
        self.label_proposal_now = QLabel("-")
        proposal_layout.addWidget(self.label_proposal_now, 0, 1)
        proposal_layout.addWidget(QLabel("Proposed:"), 1, 0)
        proposal_layout.addWidget(self.label_proposed, 1, 1)
        proposal_layout.addWidget(QLabel("Measured:"), 2, 0)
        proposal_layout.addWidget(self.label_proposed_timing, 2, 1)
        self.button_apply = QPushButton("Apply and save")
        self.button_apply.setToolTip(
            "Write these three keys to the machine ini (a timestamped backup is saved first), use them now, "
            "re-configure the driver, re-home and come back to the slot you were on."
        )
        self.button_apply.clicked.connect(self._apply)
        proposal_layout.addWidget(self.button_apply, 3, 0, 1, 2)
        proposal_layout.setColumnStretch(1, 1)
        self.proposal_box.setLayout(proposal_layout)
        self.proposal_box.setVisible(False)
        layout.addWidget(self.proposal_box)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(240)
        layout.addWidget(self.log_view)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        self.button_close = QPushButton("Close")
        self.button_close.clicked.connect(self.close)
        close_row.addWidget(self.button_close)
        layout.addLayout(close_row)

        self.setLayout(layout)
        self.resize(720, 620)

    def _refresh_current(self):
        profile = machine_profile()
        self.label_current.setText(_profile_text(profile))
        try:
            fw = tuple(self.microcontroller.firmware_version)
            firmware = f"firmware {fw[0]}.{fw[1]}"
        except Exception:  # noqa: BLE001
            firmware = "firmware unknown"
        slot = self.session.current_slot()
        self.label_current_extra.setText(
            f"{profile['current_ma']:g} mA, completion window {profile['completion_window_deg']:g}°, "
            f"{firmware}, wheel {self.wheel_id} on slot {slot if slot is not None else '(unknown)'}"
        )
        self.label_proposal_now.setText(_profile_text(profile))

    # ---------------------------------------------------------------- running
    def _running(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def append_log(self, message: str):
        self.log_view.append(str(message))

    def _say(self, message: str):
        """Put a sentence where the operator is already looking - the result line and the log - rather than in a
        modal box: this dialog is driven while the instrument is doing something, and a modal would hide it."""
        self.label_result.setText(message)
        self.append_log(message)

    def _start(self, action: str):
        if self._running():
            return
        reason = refusal_reason(self.filter_wheel, self.microcontroller, wheel_id=self.wheel_id) or (
            self.busy_reason() if self.busy_reason else None
        )
        if reason:
            self._say(f"Not started. {reason}")
            return
        self.proposal = None
        self.proposal_box.setVisible(False)
        self.label_result.setText("")
        self.log_view.clear()
        self._set_running(True)
        self._start_worker(TuningWorker(self.session, action, parent=self))

    def _apply(self):
        if self._running() or not self.proposal:
            return
        self._set_running(True)
        self.button_apply.setEnabled(False)
        # Apply cannot be interrupted (the ini is already written when the wheel starts to re-home), and Cancel
        # would only reach the tuner of the PREVIOUS run.
        self.button_cancel.setEnabled(False)
        self._start_worker(TuningWorker(self.session, "apply", apply_record=self.proposal, parent=self))

    def _start_worker(self, worker: TuningWorker):
        """The run's log lines are written from the worker thread, so they reach the log view through a signal:
        a QTextEdit may only be touched from the GUI thread."""
        self.worker = worker
        worker.signal_log.connect(self.append_log)
        worker.signal_finished.connect(self._finished)
        self.session.log_fn = worker.signal_log.emit
        worker.start()

    def _cancel(self):
        if self._running():
            self.append_log("Cancelling: the run stops after the move it is on, then the machine is put back.")
            self.session.cancel()
            self.button_cancel.setEnabled(False)

    def _set_running(self, running: bool):
        self.button_verify.setEnabled(not running)
        self.button_tune.setEnabled(not running)
        self.button_apply.setEnabled(not running and bool(self.proposal))
        self.button_cancel.setEnabled(running)
        self.button_close.setEnabled(not running)

    def _finished(self, result):
        action = self.worker.action if self.worker is not None else ""
        self.session.clear_cancel()
        if isinstance(result, Exception):
            self.label_result.setText(f"{action} failed: {result}")
            self.append_log(f"ERROR: {result}")
        elif action == "apply":
            written = result.get("applied", {})
            self.label_result.setText(
                f"Applied and saved to {written.get('path')} (backup {written.get('backup')}). "
                f"The wheel runs the new profile now."
            )
            self.proposal = None
            self.proposal_box.setVisible(False)
        elif result.get("cancelled"):
            self.label_result.setText(f"{action} cancelled. The machine has been put back.")
        elif action == "verify":
            v = result.get("verify") or {}
            if v.get("pass"):
                self.label_result.setText(
                    f"PASS: {v.get('moves')} moves with no lost steps "
                    f"(drift {v.get('drift_usteps'):+d} usteps, limit {v.get('drift_limit_usteps')}); "
                    f"{_timing_text(v.get('by_distance_ms_median'))}"
                )
            else:
                self.label_result.setText(
                    f"FAIL: the wheel lost steps at its configured profile "
                    f"(drift {v.get('drift_usteps')} usteps, limit {v.get('drift_limit_usteps')}, "
                    f"worst single move {v.get('worst_move_usteps')}). Run Tune, or lower the acceleration."
                )
        elif action == "tune":
            rec = result.get("tune") or {}
            if rec.get("pass"):
                self.proposal = rec
                self.label_result.setText(
                    f"Tune finished: a profile confirmed over {rec.get('moves_confirmed')} moves. "
                    f"Nothing has been saved yet."
                )
                self.label_proposed.setText(_profile_text(rec))
                self.label_proposed_timing.setText(
                    f"{_timing_text(rec.get('by_distance_ms_median'))}" + _search_note(rec)
                )
                self.proposal_box.setVisible(True)
            else:
                self.label_result.setText(
                    "Tune did not find a profile this wheel runs cleanly. See the log; check the wheel's load, "
                    "the motor current and the encoder."
                )
        self._refresh_current()
        self._set_running(False)
        self.worker = None

    # ---------------------------------------------------------------- Qt
    def reject(self):
        # Escape (and any other route to QDialog.reject) does NOT go through closeEvent. While a run owns the
        # hardware the dialog must stay modal: released, the main window would let the user move a wheel - or start
        # an acquisition - that the tuner is driving.
        if self._running():
            self._say("A run is in progress. Cancel it and wait for the wheel to be put back before closing.")
            return
        self.session.log_fn = None
        super().reject()

    def accept(self):
        if not self._running():
            self.session.log_fn = None
        if self._running():
            self._say("A run is in progress. Cancel it and wait for the wheel to be put back before closing.")
            return
        super().accept()

    def closeEvent(self, event):
        if self._running():
            # Closing now would leave the wheel mid-run, outside the host's coordinate frame and with its position
            # record withdrawn. Cancel stops it and the restore follows.
            self._say("A run is in progress. Cancel it and wait for the wheel to be put back before closing.")
            event.ignore()
            return
        self.session.log_fn = None
        super().closeEvent(event)
