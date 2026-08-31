"""The Fluidics Protocol record tab: Start/pre-flight, the status card, Pause/Abort, the HELD
attention panel and crash recovery — a thin Qt face over the Qt-free ProtocolRunner."""

import os
import time
from typing import Callable, Dict, Optional

from qtpy.QtCore import QTimer, Signal
from qtpy.QtGui import QDesktopServices
from qtpy.QtCore import QUrl
from qtpy.QtWidgets import (
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
    signal_protocol_active = Signal(bool)
    signal_show_fluidics_tab = Signal()
    signal_run_notification = Signal(str)  # Slack-worthy: held / finished
    signal_reagent_rows = Signal(list)  # rows for the Reagents table

    REFRESH_MS = 500

    def __init__(
        self,
        service,
        protocol_tab,
        imaging_port_factory: Callable[[], object],
        busy_check: Optional[Callable[[], Optional[str]]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.service = service
        self.protocol_tab = protocol_tab
        self.imaging_port_factory = imaging_port_factory
        self.busy_check = busy_check
        self.fluidics_port = None
        self.runner: Optional[ProtocolRunner] = None
        self._resolved = None
        self._bridge = RunnerEventBridge(self)
        self._bridge.event_received.connect(self._on_runner_event)
        self._current_step_kind: Optional[str] = None
        self._current_step_label = ""
        self._hold: Optional[Hold] = None
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
        self.step_label = QLabel("—")
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
        running_layout.addWidget(self.step_label)
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
        imaging = len(protocol.imaging_rows())
        rounds = len(dict.fromkeys(r.get("round") for r in protocol.sequences if r.get("round")))
        self.summary_label.setText(f"{rounds} rounds · {len(protocol.sequences)} rows · {imaging} imaging")
        hint = None
        if self.fluidics_port is None:
            hint = "Initialize the fluidics system first (Fluidics display tab)"
        else:
            hint = self.protocol_tab.imaging_ready()
        self.start_button.setEnabled(hint is None and not self.is_run_active())
        self.hint_label.setText(hint or "")

    # ---------- start / recovery ----------

    def _guard_start(self) -> Optional[str]:
        if self.is_run_active():
            return "a protocol run is already in progress"
        if self.fluidics_port is None or self.service is None or not getattr(self.service, "initialized", False):
            return "Initialize the fluidics system first"
        if self.busy_check is not None:
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
            f"{len(resolved.steps)} steps · {imaging_steps} imaging sessions",
            f"fluidics est. {_hms(resolved.fluidics_estimate_s)}",
            f"run folder: {os.path.join(save_to, run_name)}_<start time>",
        ]
        if PreflightDialog([], summary, self).exec_() != QDialog.Accepted:
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
                imaging=self.imaging_port_factory(),
                fluidics=self.fluidics_port,
                run_name=run_name,
                listener=self._bridge.listener,
            ),
            resolved,
        )

    def offer_recovery(self, startup: bool = False) -> None:
        save_to = self.save_to_edit.text().strip() or state.load_ui_state().get("save_to") or ""
        runs = manifest_io.find_unfinished_runs(save_to) if save_to else []
        if not runs:
            if not startup:
                QMessageBox.information(self, "Nothing to resume", "No unfinished runs under the Save to directory.")
            return
        manifest = runs[0]
        if RecoveryDialog(manifest, self).exec_() != QDialog.Accepted:
            return
        if self.fluidics_port is None:
            QMessageBox.warning(self, "Cannot resume", "Initialize the fluidics system first.")
            return
        run_dir = manifest.run_dir
        try:
            protocol = load_protocol(os.path.join(run_dir, manifest_io.PROTOCOL_COPY_NAME))
            resolved = resolve_protocol(protocol, run_dir, fluidics=self.fluidics_port)
            fresh = manifest_io.read_manifest(run_dir)
            runner = ProtocolRunner(
                resolved,
                run_dir,
                imaging=self.imaging_port_factory(),
                fluidics=self.fluidics_port,
                run_name=fresh.run_name,
                listener=self._bridge.listener,
                manifest=fresh,
            )
        except Exception as e:
            QMessageBox.warning(self, "Cannot resume", str(e))
            return
        self._launch(runner, resolved)

    def _launch(self, runner: ProtocolRunner, resolved) -> None:
        self.runner = runner
        self._resolved = resolved
        self._hold = None
        self._current_step_kind = None
        self.protocol_tab.set_run_locked(True)
        self.idle_box.hide()
        self.running_box.show()
        self.held_box.hide()
        self.signal_acquisition_started.emit(True)
        self.signal_protocol_active.emit(True)
        runner.start()

    def end_run_for_exit(self, timeout: float = 15.0) -> bool:
        """closeEvent path: end an active run and wait for it to unwind."""
        if not self.is_run_active():
            return True
        self.runner.abort_run()
        return self.runner.wait(timeout)

    # ---------- runner events (GUI thread via the bridge) ----------

    def _on_runner_event(self, event) -> None:
        try:
            if isinstance(event, StepStarted):
                self._current_step_kind = event.kind
                self._current_step_label = event.label
                step = self._resolved.steps[event.step_index] if self._resolved else None
                if step is not None:
                    row_index = step.row_index if step.kind == "imaging" else step.row_indices[0]
                    self.protocol_tab.highlight_row(row_index)
                self.sequence_label.setText("—")
            elif isinstance(event, SequenceProgress):
                self.sequence_label.setText(f"sequence {event.position + 1}/{event.total} ({event.label})")
            elif isinstance(event, StepEnded):
                self._update_reagents()
            elif isinstance(event, StateChanged):
                if event.state is RunnerState.HELD and event.hold is not None:
                    self._show_hold(event.hold)
                elif event.state is not RunnerState.HELD:
                    self._hold = None
                    self.held_box.hide()
            elif isinstance(event, RunFinished):
                self._finish(event.outcome)
        except Exception:
            self._log.exception("Error handling a runner event")

    def _show_hold(self, hold: Hold) -> None:
        self._hold = hold
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
        container = QHBoxLayout()
        holder = QGroupBox()
        holder.setFlat(True)
        holder.setLayout(buttons)
        self.held_layout.addWidget(holder)
        del container
        self.held_box.show()
        self.signal_run_notification.emit(
            f"Fluidics protocol held at step {hold.step_index + 1} ({hold.kind}): {hold.reason}"
            + (f" — {hold.message}" if hold.message else "")
        )

    def _update_reagents(self) -> None:
        if self.runner is None:
            return
        manifest = self.runner.manifest
        last_step: Dict[int, float] = {}
        this_run: Dict[int, float] = {}
        for step in manifest.steps:
            for attempt in step.attempts:
                for port_str, ul in (attempt.reagent_used_ul or {}).items():
                    port = int(port_str)
                    this_run[port] = this_run.get(port, 0.0) + ul
                if attempt.reagent_used_ul:
                    last_step = {int(p): u for p, u in attempt.reagent_used_ul.items()}
        for port, ul in this_run.items():
            self._since_init_ul[port] = max(self._since_init_ul.get(port, 0.0), 0.0)
        rows = []
        ports = sorted(set(this_run) | set(self._since_init_ul))
        reagent_name = self._reagent_name
        for port in ports:
            rows.append(
                (
                    port,
                    reagent_name(port),
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
        manifest = self.runner.manifest if self.runner is not None else None
        if manifest is not None:
            for step in manifest.steps:
                for attempt in step.attempts:
                    for port_str, ul in (attempt.reagent_used_ul or {}).items():
                        port = int(port_str)
                        self._since_init_ul[port] = self._since_init_ul.get(port, 0.0) + ul
        self.protocol_tab.set_run_locked(False)
        self.protocol_tab.highlight_row(None)
        self.state_label.setText(f"Run {outcome}")
        self.signal_acquisition_started.emit(False)
        self.signal_protocol_active.emit(False)
        self.signal_run_notification.emit(f"Fluidics protocol run {outcome} ({self.folder_label.text()})")
        self.idle_box.show()
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
            if snap.step_index is not None:
                self.step_label.setText(
                    f"step {snap.step_index + 1}/{snap.total_steps} ({self._current_step_label}) · attempt {snap.attempt}"
                )
                self.progress_bar.setMaximum(snap.total_steps)
                self.progress_bar.setValue(min(snap.step_index + (0 if snap.outcome is None else 1), snap.total_steps))
            self.elapsed_label.setText(f"elapsed {_hms(snap.elapsed_s)}")
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
