"""The Fluidics Protocol record tab: Start/pre-flight, the status card, Pause/Abort, the HELD
attention panel and crash recovery — a thin Qt face over the Qt-free ProtocolRunner."""

import os
import time
from typing import Callable, Dict, Optional

from qtpy.QtCore import QEventLoop, QTimer, QUrl, Signal
from qtpy.QtGui import QDesktopServices
from qtpy.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

import squid.logging
from control.core.fluidics_protocol import manifest as manifest_io
from control.core.fluidics_protocol.manifest import reagent_totals
from control.core.fluidics_protocol.events import (
    Hold,
    HoldAction,
    RunFinished,
    RunnerState,
    SequenceProgress,
    StateChanged,
    StepEnded,
    StepStarted,
)
from control.core.fluidics_protocol.resolve import ProtocolProblems, resolve_protocol
from control.core.fluidics_protocol.runner import ProtocolRunner
from control.models.fluidics_protocol import load_protocol
from control.widgets_fluidics import state
from control.widgets_fluidics.dialogs import PreflightDialog, RecoveryDialog
from control.widgets_fluidics.runner_bridge import RunnerEventBridge

_ACTIVE_STATES = (RunnerState.RUNNING, RunnerState.PAUSE_REQUESTED, RunnerState.PAUSED, RunnerState.HELD)


def _hms(seconds: Optional[float]) -> str:
    if seconds is None:
        return "—"
    seconds = int(seconds)
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


class FluidicsProtocolWidget(QFrame):
    signal_acquisition_started = Signal(bool)  # True at run start, False at run end (the gui contract)
    signal_show_fluidics_tab = Signal()
    signal_run_notification = Signal(str)  # Slack-worthy: held / finished
    signal_reagent_rows = Signal(list)  # rows for the Reagents table

    REFRESH_MS = 500

    def __init__(
        self,
        service,
        protocol_tab,
        imaging_port: object,
        busy_check: Callable[[], Optional[str]] = lambda: None,
        parent=None,
    ):
        super().__init__(parent)
        self._log = squid.logging.get_logger(__name__)
        self.service = service
        self.protocol_tab = protocol_tab
        self.imaging_port = imaging_port
        self.busy_check = busy_check
        self._recovery_checked_dir: Optional[str] = None
        self.fluidics_port = None
        self.runner: Optional[ProtocolRunner] = None
        self._resolved = None
        self._bridge = RunnerEventBridge(self)
        self._bridge.event_received.connect(self._on_runner_event)
        self._current_step_kind: Optional[str] = None
        self._current_round = "—"
        self._current_sequence = "—"
        self._since_init_ul: Dict[int, float] = {}

        self._build_ui()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        protocol_tab.signal_protocol_changed.connect(self._refresh_idle)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(self.REFRESH_MS)
        self._refresh_idle()

    # ---------- UI ----------

    def _build_ui(self) -> None:
        ui_state = state.load_ui_state()

        self.protocol_label = QLabel("(no protocol)")
        self.show_button = QPushButton("Show ↗")
        self.show_button.clicked.connect(self.signal_show_fluidics_tab.emit)
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Protocol:"))
        name_row.addWidget(self.protocol_label, 1)
        name_row.addWidget(self.show_button)

        self.summary_label = QLabel("—")
        self.summary_label.setWordWrap(True)

        self.save_to_edit = QLineEdit(ui_state.get("save_to") or "")
        self.save_to_edit.editingFinished.connect(self._save_to_changed)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_save_to)
        save_row = QHBoxLayout()
        save_row.addWidget(QLabel("Save to:"))
        save_row.addWidget(self.save_to_edit, 1)
        save_row.addWidget(browse)

        self.run_name_edit = QLineEdit(ui_state.get("run_name") or "")
        run_row = QHBoxLayout()
        run_row.addWidget(QLabel("Run name:"))
        run_row.addWidget(self.run_name_edit, 1)

        self.start_button = QPushButton("Start run…")
        self.start_button.clicked.connect(self.start_run)
        self.resume_button = QPushButton("Resume unfinished run…")
        self.resume_button.clicked.connect(lambda: self.offer_recovery(startup=False))
        self.hint_label = QLabel("")
        self.hint_label.setStyleSheet("color: gray;")

        self.idle_box = QGroupBox()
        idle_layout = QVBoxLayout()
        idle_layout.addLayout(name_row)
        idle_layout.addWidget(self.summary_label)
        idle_layout.addLayout(save_row)
        idle_layout.addLayout(run_row)
        idle_layout.addWidget(self.start_button)
        idle_layout.addWidget(self.resume_button)
        idle_layout.addWidget(self.hint_label)
        self.idle_box.setLayout(idle_layout)

        self.state_label = QLabel("—")
        self.round_label = QLabel("—")
        self.sequence_label = QLabel("—")
        self.elapsed_label = QLabel("—")
        self.progress_bar = QProgressBar()
        self.folder_label = QLabel("—")
        self.open_folder_button = QPushButton("Open")
        self.open_folder_button.clicked.connect(self._open_run_folder)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder_label, 1)
        folder_row.addWidget(self.open_folder_button)
        self.pause_button = QPushButton("Pause")
        self.pause_button.clicked.connect(self._pause_clicked)
        self.abort_step_button = QPushButton("Abort step")
        self.abort_step_button.clicked.connect(self._abort_step_clicked)
        self.abort_run_button = QPushButton("Abort run…")
        self.abort_run_button.clicked.connect(self._abort_run_clicked)
        buttons_row = QHBoxLayout()
        buttons_row.addWidget(self.pause_button)
        buttons_row.addWidget(self.abort_step_button)
        buttons_row.addWidget(self.abort_run_button)

        self.held_box = QGroupBox("Attention")
        self.held_box.setStyleSheet("QGroupBox { color: #b00020; }")
        self.held_layout = QVBoxLayout()
        self.held_box.setLayout(self.held_layout)
        self.held_box.hide()

        self.running_box = QGroupBox()
        running_layout = QVBoxLayout()
        running_layout.addWidget(self.state_label)
        running_layout.addWidget(self.round_label)
        running_layout.addWidget(self.sequence_label)
        running_layout.addWidget(self.elapsed_label)
        running_layout.addWidget(self.progress_bar)
        running_layout.addLayout(folder_row)
        running_layout.addLayout(buttons_row)
        running_layout.addWidget(self.held_box)
        self.running_box.setLayout(running_layout)
        self.running_box.hide()

        layout = QVBoxLayout()
        layout.addWidget(self.idle_box)
        layout.addWidget(self.running_box)
        layout.addStretch(1)
        self.setLayout(layout)

    def _browse_save_to(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Save runs under", self.save_to_edit.text())
        if path:
            self.save_to_edit.setText(path)
            state.save_ui_state(save_to=path)
            self._save_to_changed()

    # ---------- state ----------

    def set_fluidics_port(self, port) -> None:
        self.fluidics_port = port
        self._refresh_idle()

    def is_run_active(self) -> bool:
        return self.runner is not None and self.runner.state in _ACTIVE_STATES

    def _refresh_idle(self) -> None:
        protocol = self.protocol_tab.protocol
        self.protocol_label.setText(
            protocol.name or os.path.basename(self.protocol_tab.protocol_path or "") or "(no protocol)"
        )
        self.summary_label.setText(protocol.summary_line())
        hint = (
            "Initialize the fluidics system first (Fluidics display tab)"
            if self.fluidics_port is None
            else self.protocol_tab.imaging_ready()
        )
        self.start_button.setEnabled(hint is None and not self.is_run_active())
        self.hint_label.setText(hint or "")

    # ---------- start / recovery ----------

    def _guard_start(self) -> Optional[str]:
        if self.is_run_active():
            return "a protocol run is already in progress"
        if self.fluidics_port is None or self.service is None or not getattr(self.service, "initialized", False):
            return "Initialize the fluidics system first"
        busy = self.busy_check()
        if busy:
            return busy
        hint = self.protocol_tab.imaging_ready()
        if hint:
            return hint
        if not self.save_to_edit.text().strip():
            return "choose a Save to directory"
        if not self.run_name_edit.text().strip():
            return "give the run a name"
        return None

    def start_run(self) -> None:
        problem = self._guard_start()
        if problem:
            QMessageBox.warning(self, "Cannot start", problem)
            return
        protocol = self.protocol_tab.protocol
        save_to = self.save_to_edit.text().strip()
        run_name = self.run_name_edit.text().strip()
        base_dir = (
            os.path.dirname(os.path.abspath(self.protocol_tab.protocol_path))
            if self.protocol_tab.protocol_path
            else save_to
        )
        try:
            resolved = resolve_protocol(protocol, base_dir, fluidics=self.fluidics_port)
        except ProtocolProblems as problems:
            PreflightDialog(problems.problems, [], self).exec_()
            return
        except Exception as e:
            QMessageBox.warning(self, "Cannot start", str(e))
            return

        imaging_steps = sum(1 for s in resolved.steps if s.kind == "imaging")
        summary = [
            f"{imaging_steps} imaging session(s)",
            f"est. {_hms(resolved.total_estimate_s)} (imaging priced at a rough 1 s/FOV)",
            f"run folder: {os.path.join(save_to, run_name)}_<start time>",
        ]
        preflight = PreflightDialog([], summary, self, tec_option=True)
        if preflight.exec_() != QDialog.Accepted:
            return
        try:
            run_dir = manifest_io.create_run_dir(save_to, run_name)
        except (ValueError, FileExistsError, OSError) as e:
            QMessageBox.warning(self, "Cannot start", str(e))
            return
        state.save_ui_state(save_to=save_to, run_name=run_name)
        self._launch(
            ProtocolRunner(
                resolved,
                run_dir,
                imaging=self.imaging_port,
                fluidics=self.fluidics_port,
                run_name=run_name,
                listener=self._bridge.listener,
                disable_tec_at_end=preflight.disable_tec_at_end(),
            ),
            resolved,
        )

    def _save_to_changed(self) -> None:
        save_to = self.save_to_edit.text().strip()
        if save_to and save_to != self._recovery_checked_dir:
            self.offer_recovery(startup=True)

    def offer_recovery(self, startup: bool = False) -> None:
        if self.is_run_active():
            return
        busy = self.busy_check()
        if busy:
            if not startup:
                QMessageBox.warning(self, "Cannot resume", f"Cannot resume a run while {busy}.")
            return
        save_to = self.save_to_edit.text().strip() or state.load_ui_state().get("save_to") or ""
        if startup and save_to == self._recovery_checked_dir:
            return
        if self.fluidics_port is None:
            # A recovered run needs the fluidics system; the offer re-fires on system_ready.
            if not startup:
                QMessageBox.warning(self, "Cannot resume", "Initialize the fluidics system first.")
            return
        runs = manifest_io.find_unfinished_runs(save_to) if save_to else []
        self._recovery_checked_dir = save_to
        if not runs:
            if not startup:
                QMessageBox.information(self, "Nothing to resume", "No unfinished runs under the Save to directory.")
            return
        manifest = runs[0]
        if RecoveryDialog(manifest, self).exec_() != QDialog.Accepted:
            return
        run_dir = manifest.run_dir
        try:
            protocol = load_protocol(os.path.join(run_dir, manifest_io.PROTOCOL_COPY_NAME))
            resolved = resolve_protocol(protocol, run_dir, fluidics=self.fluidics_port)
            fresh = manifest_io.read_manifest(run_dir)
            runner = ProtocolRunner(
                resolved,
                run_dir,
                imaging=self.imaging_port,
                fluidics=self.fluidics_port,
                run_name=fresh.run_name,
                listener=self._bridge.listener,
                manifest=fresh,
            )
        except Exception as e:
            QMessageBox.warning(self, "Cannot resume", str(e))
            return
        # The editor must show the protocol the run is riding: highlights and the
        # run-lock apply to it, not to whatever protocol happened to be open.
        self.protocol_tab.set_protocol(protocol, os.path.join(run_dir, manifest_io.PROTOCOL_COPY_NAME))
        self._launch(runner, resolved)

    def _launch(self, runner: ProtocolRunner, resolved) -> None:
        self.runner = runner
        self._resolved = resolved
        self._current_step_kind = None
        self._current_round = "—"
        self._current_sequence = "—"
        self.protocol_tab.set_run_locked(True)
        self._set_run_visible(True)
        self.signal_acquisition_started.emit(True)
        runner.start()

    def _set_run_visible(self, active: bool) -> None:
        """The status card replaces the idle panel while a run rides; the HELD box is
        shown only by _show_hold."""
        self.idle_box.setHidden(active)
        self.running_box.setHidden(not active)
        self.held_box.hide()

    def _render_progress(self, snap) -> None:
        """Map the run's progress fraction (elapsed vs. the rough estimate, held just under full
        until it ends -- so the bar keeps moving through a long imaging step) onto the bar. With
        no fraction (an unpriced plan), fall back to counting completed steps."""
        if snap.progress_fraction is not None:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(round(100 * snap.progress_fraction))
        elif snap.step_index is not None:
            self.progress_bar.setMaximum(snap.total_steps)
            done = snap.step_index + (0 if snap.outcome is None else 1)
            self.progress_bar.setValue(min(done, snap.total_steps))

    def _render_running(self) -> None:
        self.round_label.setText(f"Round: {self._current_round}")
        self.sequence_label.setText(f"Sequence: {self._current_sequence}")

    def run_line(self) -> str:
        """One line for the device-status panel: just the run state."""
        return "idle" if self.runner is None else self.runner.snapshot().state.value

    def end_run_for_exit(self, timeout: float = 15.0) -> bool:
        """closeEvent path: end an active run and wait for it to unwind, pumping the event
        loop while waiting - an in-flight imaging step can only finish through queued
        signals delivered on this (GUI) thread, so a plain blocking wait would deadlock."""
        if not self.is_run_active():
            return True
        self.runner.abort_run()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.runner.wait(0.05):
                return True
            QApplication.processEvents(QEventLoop.AllEvents, 50)
        return self.runner.wait(0)

    # ---------- runner events (GUI thread via the bridge) ----------

    def _on_runner_event(self, event) -> None:
        try:
            if isinstance(event, StepStarted):
                self._current_step_kind = event.kind
                step = self._resolved.steps[event.step_index] if self._resolved else None
                if step is not None:
                    self._current_round = step.round or "—"
                    if step.kind == "imaging":
                        self._current_sequence = step.row.name or "imaging"
                        self.protocol_tab.highlight_row(step.row_index)
                    else:
                        self._current_sequence = step.rows[0].get("name") or "—"
                        self.protocol_tab.highlight_row(step.row_indices[0])
                self._render_running()
            elif isinstance(event, SequenceProgress):
                step = self._resolved.steps[event.step_index] if self._resolved else None
                if step is not None and step.kind == "fluidics" and 0 <= event.position < len(step.row_indices):
                    self._current_sequence = step.rows[event.position].get("name") or "—"
                    self.protocol_tab.highlight_row(step.row_indices[event.position])  # the row actually flowing
                    self._render_running()
            elif isinstance(event, StepEnded):
                self._update_reagents()
            elif isinstance(event, StateChanged):
                if event.state is RunnerState.HELD and event.hold is not None:
                    self._show_hold(event.hold)
                elif event.state is not RunnerState.HELD:
                    self.held_box.hide()
            elif isinstance(event, RunFinished):
                self._finish(event.outcome)
        except Exception:
            self._log.exception("Error handling a runner event")

    def _show_hold(self, hold: Hold) -> None:
        while self.held_layout.count():
            item = self.held_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        headline = QLabel(f"Held at step {hold.step_index + 1} ({hold.kind}): {hold.reason}")
        headline.setStyleSheet("font-weight: bold; color: #b00020;")
        self.held_layout.addWidget(headline)
        if hold.message:
            message = QLabel(hold.message)
            message.setWordWrap(True)
            self.held_layout.addWidget(message)
        self.held_layout.addWidget(QLabel("Pump halted · TEC output OFF" if hold.tec_before else "System made safe"))
        tec_checkbox = None
        if hold.tec_before is not None and any(hold.tec_before.output_enabled):
            targets = ", ".join(f"{t:.1f} °C" for t in hold.tec_before.targets)
            tec_checkbox = QCheckBox(f"Restore TEC output ({targets}) before continuing")
            tec_checkbox.setChecked(True)
            self.held_layout.addWidget(tec_checkbox)

        def act(action: HoldAction):
            restore = tec_checkbox.isChecked() if tec_checkbox is not None else False
            try:
                self.runner.hold_action(action, restore_tec=restore)
            except RuntimeError as e:
                self._log.warning(f"Hold action refused: {e}")

        buttons = QHBoxLayout()
        if hold.can_resume and hold.resume_position is not None:
            resume = QPushButton(f"Resume from seq {hold.resume_position + 1}")
            resume.clicked.connect(lambda: act(HoldAction.RESUME))
            buttons.addWidget(resume)
        restart = QPushButton("Restart step")
        restart.clicked.connect(lambda: act(HoldAction.RESTART))
        buttons.addWidget(restart)
        skip = QPushButton("Skip step")
        skip.clicked.connect(lambda: act(HoldAction.SKIP))
        buttons.addWidget(skip)
        if hold.can_accept:
            accept = QPushButton("Accept and continue")
            accept.clicked.connect(lambda: act(HoldAction.ACCEPT))
            buttons.addWidget(accept)
        end = QPushButton("End run…")

        def end_clicked():
            reply = QMessageBox.question(
                self, "End run", "End this protocol run?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                act(HoldAction.END)

        end.clicked.connect(end_clicked)
        buttons.addWidget(end)
        holder = QGroupBox()
        holder.setFlat(True)
        holder.setLayout(buttons)
        self.held_layout.addWidget(holder)
        self.held_box.show()
        self.signal_run_notification.emit(
            f"Fluidics protocol held at step {hold.step_index + 1} ({hold.kind}): {hold.reason}"
            + (f" — {hold.message}" if hold.message else "")
        )

    def _update_reagents(self) -> None:
        if self.runner is None:
            return
        this_run, last_step = reagent_totals(self.runner.manifest)
        rows = []
        for port in sorted(set(this_run) | set(self._since_init_ul)):
            rows.append(
                (
                    port,
                    self._reagent_name(port),
                    last_step.get(port, 0.0),
                    this_run.get(port, 0.0),
                    self._since_init_ul.get(port, 0.0) + this_run.get(port, 0.0),
                )
            )
        self.signal_reagent_rows.emit(rows)

    def _reagent_name(self, port: int) -> Optional[str]:
        try:
            return self.service.system.devices.selector_valves.port_to_reagent(port)
        except Exception:
            return None

    def _finish(self, outcome: str) -> None:
        if self.runner is not None:
            totals, _last = reagent_totals(self.runner.manifest)
            for port, ul in totals.items():
                self._since_init_ul[port] = self._since_init_ul.get(port, 0.0) + ul
        self.protocol_tab.set_run_locked(False)
        self.protocol_tab.highlight_row(None)
        self.state_label.setText(f"Run {outcome}")
        self.signal_acquisition_started.emit(False)
        self.signal_run_notification.emit(f"Fluidics protocol run {outcome} ({self.folder_label.text()})")
        self._set_run_visible(False)
        self._refresh_idle()

    # ---------- buttons / timer ----------

    def _pause_clicked(self) -> None:
        if self.runner is None:
            return
        if self.runner.state in (RunnerState.PAUSE_REQUESTED, RunnerState.PAUSED):
            self.runner.resume()
        else:
            self.runner.pause()

    def _abort_step_clicked(self) -> None:
        if self.runner is not None:
            self.runner.abort_step()

    def _abort_run_clicked(self) -> None:
        if self.runner is None:
            return
        reply = QMessageBox.question(
            self, "Abort run", "Abort the whole protocol run?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.runner.abort_run()

    def _open_run_folder(self) -> None:
        if self.runner is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.runner.run_dir)))

    def _refresh(self) -> None:
        try:
            runner = self.runner
            if runner is None:
                return
            snap = runner.snapshot()
            self.state_label.setText(f"State: {snap.state.value}")
            self._render_running()
            self._render_progress(snap)
            # imaging is priced at a rough 1 s/FOV (see IMAGING_SECONDS_PER_FOV); the total is a ballpark
            estimate = self._resolved.total_estimate_s if self._resolved else None
            self.elapsed_label.setText(f"elapsed {_hms(snap.elapsed_s)} / est. {_hms(estimate)}")
            self.folder_label.setText(str(runner.run_dir))
            if snap.state in (RunnerState.PAUSE_REQUESTED, RunnerState.PAUSED):
                self.pause_button.setText("Resume")
            elif self._current_step_kind == "imaging":
                self.pause_button.setText("Pause after imaging")
            else:
                self.pause_button.setText("Pause")
            active = snap.state in _ACTIVE_STATES
            for button in (self.pause_button, self.abort_step_button, self.abort_run_button):
                button.setEnabled(active)
        except Exception as e:  # Qt swallows timer-slot exceptions: log explicitly
            self._log.error(f"Protocol status refresh failed: {e}")
