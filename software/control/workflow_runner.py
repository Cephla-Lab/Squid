"""
Workflow Runner - Execute sequences of scripts and acquisitions.

This module provides data models and execution engine for running workflows
that combine external scripts with the built-in acquisition system.
"""

from dataclasses import dataclass, field, fields
from enum import Enum
import os
import re
import shlex
import time
from threading import Event, Lock, Thread
from typing import Any, Callable, Dict, List, Optional, Tuple
import subprocess
import sys

import yaml
from qtpy.QtCore import QObject, Signal

import squid.logging

#: Environment variables handed to script sequences. A script can use these to
#: pick up where the previous acquisition wrote its data without the operator
#: having to paste a timestamped path into the workflow. They are passed via the
#: environment rather than the argument string because `build_command` runs the
#: arguments through `shlex.split`, which in POSIX mode eats the backslashes in
#: a Windows path and splits it on spaces.
ENV_LAST_ACQUISITION_PATH = "SQUID_LAST_ACQUISITION_PATH"
ENV_CYCLE = "SQUID_CYCLE"
#: Folder of the acquisition.yaml the most recent *analyze* step wrote.
ENV_LAST_TARGETS_PATH = "SQUID_LAST_TARGETS_PATH"

#: An acquisition step whose config_path is this runs the acquisition.yaml the
#: most recent analyze step produced in the same workflow run.
LAST_TARGETS_PLACEHOLDER = "{last_targets}"

#: How long the GUI may take to switch objectives before the step fails. A
#: turret rotation with Z retract/restore takes seconds; minutes means it hung.
OBJECTIVE_STEP_TIMEOUT_S = 120.0

_WELL_RE = re.compile(r"^[A-Z]{1,2}\d{1,3}$")


def parse_well_list(text) -> List[str]:
    """Parse a typed well list ("c5, C6 D5") into unique upper-case names in order.

    Raises ValueError naming the tokens that are not well ids.
    """
    if text is None:
        return []
    if isinstance(text, (list, tuple)):
        tokens = [str(t) for t in text]
    else:
        tokens = re.split(r"[\s,;]+", str(text))
    wells, bad = [], []
    for token in tokens:
        name = token.strip().upper()
        if not name:
            continue
        if not _WELL_RE.match(name):
            bad.append(token.strip())
        elif name not in wells:
            wells.append(name)
    if bad:
        raise ValueError(f"Not valid well names: {', '.join(bad)}")
    return wells


class SequenceType(Enum):
    """Type of sequence step."""

    ACQUISITION = "acquisition"  # Built-in acquisition
    SCRIPT = "script"  # External script
    OBJECTIVE = "objective"  # Switch objective (and reload its laser-AF reference)
    ANALYZE = "analyze"  # Smart acquisition: survey -> analyzer -> follow-up acquisition.yaml


@dataclass
class SequenceItem:
    """Represents a single step in the workflow."""

    name: str
    sequence_type: SequenceType
    # For scripts only:
    script_path: Optional[str] = None
    arguments: Optional[str] = None
    python_path: Optional[str] = None  # e.g., "/usr/bin/python3.10" or "/home/user/venv/bin/python"
    conda_env: Optional[str] = None  # e.g., "fluidics_env" - if set, overrides python_path
    # For acquisitions only:
    config_path: Optional[str] = None  # Path to acquisition.yaml file; if None, uses current GUI settings
    wells: Optional[str] = None  # "C5,C6,D5": replaces a wellplate config's regions with these wells
    save_dir: Optional[str] = None  # base saving folder for this step; default: the tab's own folder
    # For objective steps only:
    objective: Optional[str] = None  # objective name as in objectives.csv, e.g. "20x"
    require_laser_af_reference: bool = True  # fail the step if that objective has no laser-AF reference
    # For analyze steps only:
    input_path: Optional[str] = None  # survey folder; default: the last acquisition of this run
    analyzer: str = "ring_grid"
    analyzer_params: Optional[Dict[str, Any]] = None
    template_path: Optional[str] = None  # follow-up acquisition.yaml (channels, recording, time series)
    output_dir: Optional[str] = None  # default: <input>/smart_targets
    carry_z: bool = False
    sweep_z: bool = False
    xy_offset_mm: Optional[List[float]] = None  # [dx, dy]; target_high = target_low + offset
    z_offset_mm: Optional[float] = None
    overlay: bool = True
    prefix: str = "smart"
    # Common:
    included: bool = True
    # Cycle arguments (optional) - pass different values to script for each cycle:
    cycle_arg_name: Optional[str] = None  # e.g., "port"
    cycle_arg_values: Optional[str] = None  # e.g., "1,2,3,4,5"

    def is_acquisition(self) -> bool:
        """Check if this is the built-in acquisition sequence."""
        return self.sequence_type == SequenceType.ACQUISITION

    def is_script(self) -> bool:
        return self.sequence_type == SequenceType.SCRIPT

    def is_objective(self) -> bool:
        return self.sequence_type == SequenceType.OBJECTIVE

    def is_analyze(self) -> bool:
        return self.sequence_type == SequenceType.ANALYZE

    def is_gate(self) -> bool:
        """Steps whose failure makes every later step meaningless (wrong objective, no targets)."""
        return self.is_objective() or self.is_analyze()

    def uses_last_targets(self) -> bool:
        return self.is_acquisition() and (self.config_path or "").strip() == LAST_TARGETS_PLACEHOLDER

    def summary(self) -> str:
        """One-line description for logs and the workflow table."""
        if self.is_acquisition():
            if self.uses_last_targets():
                text = "targets from the Analyze step"
            else:
                text = f"Config: {os.path.basename(self.config_path)}" if self.config_path else "Current GUI settings"
            if self.wells:
                text += f"  wells: {self.wells}"
            if (self.save_dir or "").strip():
                text += f"  -> {self.save_dir.strip()}"
            return text
        if self.is_objective():
            suffix = "  (laser AF reference required)" if self.require_laser_af_reference else ""
            return f"-> {self.objective}{suffix}"
        if self.is_analyze():
            parts = [self.analyzer, "->", os.path.basename(self.template_path or "?")]
            if self.xy_offset_mm:
                parts.append(f"xy offset {tuple(self.xy_offset_mm)} mm")
            if self.z_offset_mm:
                parts.append(f"z offset {self.z_offset_mm} mm")
            return " ".join(parts)
        return " ".join(self.build_command())

    def get_cycle_values(self) -> List[int]:
        """Parse comma-separated cycle values.

        Returns:
            List of integers parsed from cycle_arg_values.

        Raises:
            ValueError: If cycle_arg_values contains non-integer values.
        """
        if not self.cycle_arg_values:
            return []
        try:
            return [int(v.strip()) for v in self.cycle_arg_values.split(",")]
        except ValueError as e:
            raise ValueError(
                f"Invalid cycle values '{self.cycle_arg_values}': expected comma-separated integers"
            ) from e

    def build_command(self, cycle_value: Optional[int] = None) -> List[str]:
        """Build the command to execute this script.

        Priority:
        1. If conda_env is set: conda run -n <env> python <script> <args>
        2. If python_path is set: <python_path> <script> <args>
        3. Otherwise: sys.executable (same Python running Squid)
        """
        if not self.is_script():
            raise ValueError(f"Cannot build command for {self.sequence_type.value} sequence")

        if self.conda_env:
            cmd = ["conda", "run", "-n", self.conda_env, "python", self.script_path]
        elif self.python_path:
            cmd = [self.python_path, self.script_path]
        else:
            cmd = [sys.executable, self.script_path]

        if self.arguments:
            cmd.extend(shlex.split(self.arguments))

        if cycle_value is not None and self.cycle_arg_name:
            cmd.extend([f"--{self.cycle_arg_name}", str(cycle_value)])

        return cmd


@dataclass
class AcquisitionRequest:
    """What the runner asks the GUI to start: a config (already resolved), an optional well list, the step
    name, and an optional base saving folder."""

    config_path: str = ""
    wells: List[str] = field(default_factory=list)
    name: str = ""
    save_dir: str = ""


_SEQUENCE_DEFAULTS = {f.name: f.default for f in fields(SequenceItem) if f.name not in ("name", "sequence_type")}
_SEQUENCE_OPTIONAL_FIELDS = [name for name in _SEQUENCE_DEFAULTS if name != "included"]


@dataclass
class Workflow:
    """Collection of sequences to run."""

    sequences: List[SequenceItem] = field(default_factory=list)
    num_cycles: int = 1
    #: Stop at the first failed script/acquisition step. Objective and analyze
    #: steps stop the run regardless: nothing after them can be right.
    stop_on_failure: bool = False

    @classmethod
    def create_default(cls) -> "Workflow":
        """Create workflow with default Acquisition sequence."""
        return cls(sequences=[SequenceItem(name="Acquisition", sequence_type=SequenceType.ACQUISITION, included=True)])

    def get_included_sequences(self) -> List[SequenceItem]:
        """Get only the sequences that are included."""
        return [s for s in self.sequences if s.included]

    def has_acquisition(self) -> bool:
        """Check if workflow has an Acquisition sequence."""
        return any(s.is_acquisition() for s in self.sequences)

    def ensure_acquisition_exists(self):
        """Ensure the Acquisition sequence exists (add if missing)."""
        if not self.has_acquisition():
            self.sequences.insert(
                0, SequenceItem(name="Acquisition", sequence_type=SequenceType.ACQUISITION, included=True)
            )

    def validate_cycle_args(self) -> List[str]:
        """Validate cycle arguments match num_cycles. Returns list of errors."""
        errors = []
        for seq in self.get_included_sequences():
            if seq.cycle_arg_values:
                try:
                    values = seq.get_cycle_values()
                except ValueError as e:
                    errors.append(f"Sequence '{seq.name}': {e}")
                    continue
                if len(values) != self.num_cycles:
                    errors.append(
                        f"Sequence '{seq.name}': has {len(values)} cycle values, but Cycles={self.num_cycles}"
                    )
        return errors

    def validate(self) -> List[str]:
        """Everything that can be checked without hardware. Returns a list of errors (empty = ok)."""
        errors = self.validate_cycle_args()
        analyze_seen = False
        for seq in self.get_included_sequences():
            prefix = f"Sequence '{seq.name}':"
            if seq.is_acquisition():
                if seq.uses_last_targets():
                    if not analyze_seen:
                        errors.append(f"{prefix} uses {LAST_TARGETS_PLACEHOLDER} but no Analyze step runs before it")
                elif seq.config_path and not os.path.isfile(_config_file(seq.config_path)):
                    errors.append(f"{prefix} config file not found: {seq.config_path}")
                if seq.wells:
                    try:
                        wells = parse_well_list(seq.wells)
                    except ValueError as e:
                        errors.append(f"{prefix} {e}")
                        wells = None
                    if wells is not None and not wells:
                        errors.append(f"{prefix} the well list is empty")
                    if not seq.config_path or seq.uses_last_targets():
                        errors.append(f"{prefix} a well list needs a wellplate config file")
                    elif os.path.isfile(_config_file(seq.config_path)):
                        widget_type = _config_widget_type(seq.config_path)
                        if widget_type and widget_type != "wellplate":
                            errors.append(
                                f"{prefix} a well list only applies to a wellplate config ({widget_type} given)"
                            )
            elif seq.is_objective():
                if not (seq.objective or "").strip():
                    errors.append(f"{prefix} no objective given")
            elif seq.is_analyze():
                analyze_seen = True
                if not seq.template_path:
                    errors.append(f"{prefix} no template acquisition.yaml given")
                elif not os.path.isfile(_config_file(seq.template_path)):
                    errors.append(f"{prefix} template not found: {seq.template_path}")
                if seq.input_path and not os.path.isdir(seq.input_path):
                    errors.append(f"{prefix} input folder not found: {seq.input_path}")
                try:
                    from control.core.smart_acquisition import analyzers

                    if seq.analyzer not in analyzers.available():
                        errors.append(
                            f"{prefix} unknown analyzer '{seq.analyzer}' (have: {', '.join(analyzers.available())})"
                        )
                except ImportError as e:  # smart acquisition not importable: report, do not crash
                    errors.append(f"{prefix} smart acquisition unavailable: {e}")
                try:
                    from control.core.smart_acquisition.emit import validate_offsets

                    validate_offsets(seq.xy_offset_mm, seq.z_offset_mm)
                except ImportError:
                    pass
                except ValueError as e:
                    errors.append(f"{prefix} {e}")
                if seq.z_offset_mm and not (seq.carry_z or seq.sweep_z):
                    errors.append(f"{prefix} z_offset_mm has no effect without carry_z (or sweep_z)")
            elif seq.is_script():
                if not seq.script_path:
                    errors.append(f"{prefix} no script path given")
        return errors

    def to_dict(self) -> dict:
        """Serialize to dictionary for YAML export."""
        sequences = []
        for s in self.sequences:
            seq_dict = {
                "name": s.name,
                "type": s.sequence_type.value,
                "included": s.included,
            }
            # Only include optional fields that differ from their default, so a
            # file written by an older version round-trips byte-for-byte.
            for name in _SEQUENCE_OPTIONAL_FIELDS:
                value = getattr(s, name)
                if value is not None and value != _SEQUENCE_DEFAULTS[name]:
                    seq_dict[name] = value
            sequences.append(seq_dict)
        out = {"num_cycles": self.num_cycles, "sequences": sequences}
        if self.stop_on_failure:
            out["stop_on_failure"] = True
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "Workflow":
        """Deserialize from dictionary."""
        sequences = []
        for s in data.get("sequences", []):
            kwargs = {name: s[name] for name in _SEQUENCE_DEFAULTS if name in s and s[name] is not None}
            if "xy_offset_mm" in kwargs:
                kwargs["xy_offset_mm"] = [float(v) for v in kwargs["xy_offset_mm"]]
            sequences.append(SequenceItem(name=s["name"], sequence_type=SequenceType(s["type"]), **kwargs))
        workflow = cls(
            sequences=sequences,
            num_cycles=data.get("num_cycles", 1),
            stop_on_failure=bool(data.get("stop_on_failure", False)),
        )
        # Note: ensure_acquisition_exists() is no longer called - workflows can have
        # zero or multiple acquisition sequences as needed
        return workflow

    def save_to_file(self, file_path: str):
        """Save workflow to YAML file."""
        with open(file_path, "w") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    @classmethod
    def load_from_file(cls, file_path: str) -> "Workflow":
        """Load workflow from YAML file."""
        with open(file_path, "r") as f:
            data = yaml.safe_load(f)
        if data is None:
            raise ValueError(f"Workflow file '{file_path}' is empty")
        if not isinstance(data, dict):
            raise ValueError(f"Workflow file must contain a YAML dictionary, got {type(data).__name__}")
        return cls.from_dict(data)


def _config_file(path: str) -> str:
    """acquisition.yaml inside a saved acquisition folder, or the file itself."""
    if path and os.path.isdir(path):
        return os.path.join(path, "acquisition.yaml")
    return path


def _config_widget_type(path: str) -> Optional[str]:
    """The widget_type an acquisition.yaml runs in, or None when unreadable."""
    try:
        import yaml as _yaml

        with open(_config_file(path), "r", encoding="utf-8") as f:
            data = _yaml.safe_load(f) or {}
        return str((data.get("acquisition") or {}).get("widget_type") or "").lower() or None
    except Exception:
        return None


class WorkflowAborted(RuntimeError):
    """Raised inside an in-process step when the user pressed Stop."""


class WorkflowRunner(QObject):
    """Executes workflow sequences."""

    # Signals
    signal_workflow_started = Signal()
    signal_workflow_finished = Signal(bool)  # success
    signal_workflow_paused = Signal()
    signal_workflow_resumed = Signal()
    signal_cycle_started = Signal(int, int)  # current_cycle (0-indexed), total_cycles
    signal_sequence_started = Signal(int, str)  # index, name
    signal_sequence_finished = Signal(int, str, bool)  # index, name, success
    signal_script_output = Signal(str)  # stdout/stderr line
    signal_error = Signal(str)  # error message
    signal_request_acquisition = Signal(object)  # AcquisitionRequest
    signal_request_objective = Signal(str, bool)  # objective name, require laser-AF reference
    signal_acquisition_waiting = Signal()  # waiting for acquisition to complete

    def __init__(self, parent=None):
        super().__init__(parent)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._workflow: Optional[Workflow] = None
        self._running = False
        self._abort_requested = False
        self._pause_requested = False
        self._paused = False
        self._resume_event = Event()
        self._current_process: Optional[subprocess.Popen] = None
        self._process_lock = Lock()  # Protects _current_process access
        self._acquisition_complete_event = Event()
        self._current_cycle = 0
        self._thread: Optional[Thread] = None
        self._acquisition_path_getter: Optional[Callable[[], str]] = None
        self._acquisition_result_getter: Optional[Callable[[], Optional[str]]] = None
        self._last_acquisition_path: Optional[str] = None
        self._last_targets_path: Optional[str] = None
        self._acquisition_failed_msg: Optional[str] = None
        self._objective_complete_event = Event()
        self._objective_result: Tuple[bool, str] = (False, "")

    def set_workflow(self, workflow: Workflow):
        """Set the workflow to execute."""
        self._workflow = workflow

    def set_acquisition_result_getter(self, getter: Callable[[], Optional[str]]):
        """Set a function returning how the last acquisition ended ("completed", "failed_to_start", ...)."""
        self._acquisition_result_getter = getter

    def get_last_targets_path(self) -> Optional[str]:
        """acquisition.yaml written by the most recent analyze step of this run."""
        return self._last_targets_path

    def on_acquisition_failed(self, message: str):
        """Called by the GUI when an acquisition could not be started; completes the wait as a failure."""
        self._acquisition_failed_msg = message or "acquisition failed to start"
        self._acquisition_complete_event.set()

    def on_objective_finished(self, success: bool, message: str = ""):
        """Called by the GUI once an objective switch (and its checks) has finished."""
        self._objective_result = (bool(success), message or "")
        self._objective_complete_event.set()

    def set_acquisition_path_getter(self, getter: Callable[[], str]):
        """Set a function that returns the actual acquisition path (base + experiment_ID)."""
        self._acquisition_path_getter = getter

    def is_running(self) -> bool:
        """Check if workflow is currently running."""
        return self._running

    def is_paused(self) -> bool:
        """Check if workflow is paused."""
        return self._paused

    def request_pause(self):
        """Request the workflow to pause after current sequence completes."""
        self._log.info("Workflow pause requested")
        self._pause_requested = True

    def request_resume(self):
        """Resume a paused workflow."""
        if self._paused:
            self._log.info("Workflow resume requested")
            self._paused = False
            self._pause_requested = False
            self._resume_event.set()
            self.signal_workflow_resumed.emit()

    def request_stop(self):
        """Request the workflow to stop after current sequence completes."""
        self._log.info("Workflow stop requested")
        self._abort_requested = True
        # If paused, resume to allow the workflow to exit
        if self._paused:
            self._resume_event.set()

    def request_abort(self):
        """Request the workflow to abort immediately (kills current process)."""
        self._log.info("Workflow abort requested")
        self._abort_requested = True
        with self._process_lock:
            if self._current_process:
                self._current_process.terminate()
        # If paused, resume to allow the workflow to exit
        if self._paused:
            self._resume_event.set()

    def on_acquisition_finished(self):
        """Called when acquisition completes."""
        self._log.debug("Acquisition finished signal received")
        self._acquisition_complete_event.set()

    def start(self):
        """Start executing the workflow in a background thread."""
        if self._running:
            self._log.warning("Workflow already running")
            return

        self._thread = Thread(target=self._run, name="WorkflowRunner", daemon=True)
        self._thread.start()

    def _run(self):
        """Execute the workflow (runs in background thread)."""
        if not self._workflow:
            self._log.error("No workflow set")
            self.signal_error.emit("No workflow configured")
            self.signal_workflow_finished.emit(False)
            return

        self._running = True
        self._abort_requested = False
        self._pause_requested = False
        self._paused = False
        self._last_acquisition_path = None
        self._last_targets_path = None
        self._acquisition_failed_msg = None
        self._resume_event.clear()
        self.signal_workflow_started.emit()
        success = True

        try:
            included_sequences = self._workflow.get_included_sequences()
            num_cycles = self._workflow.num_cycles
            self._log.info(f"Starting workflow with {len(included_sequences)} sequences, {num_cycles} cycle(s)")

            stop = False
            for cycle in range(num_cycles):
                if stop:
                    break
                self._current_cycle = cycle

                # Emit cycle start signal and log
                self.signal_cycle_started.emit(cycle, num_cycles)
                self._log.info(f"Starting cycle {cycle + 1}/{num_cycles}")
                self.signal_script_output.emit(f"\n{'='*50}")
                self.signal_script_output.emit(f"CYCLE {cycle + 1}/{num_cycles}")
                self.signal_script_output.emit(f"{'='*50}")

                for seq in included_sequences:
                    if self._abort_requested:
                        self._log.info("Workflow stopped by user")
                        success = False
                        break

                    # Find actual index in full sequence list for UI highlighting
                    seq_index = self._workflow.sequences.index(seq)
                    self._log.info(f"Starting sequence: {seq.name}")
                    self.signal_sequence_started.emit(seq_index, seq.name)

                    seq_success = True
                    if seq.is_acquisition():
                        seq_success = self._run_acquisition(seq)
                    elif seq.is_objective():
                        seq_success = self._run_objective(seq)
                    elif seq.is_analyze():
                        seq_success = self._run_analyze(seq)
                    else:
                        cycle_value = None
                        if seq.cycle_arg_values:
                            values = seq.get_cycle_values()
                            if cycle < len(values):
                                cycle_value = values[cycle]
                        seq_success = self._run_script(seq, cycle_value)

                    self.signal_sequence_finished.emit(seq_index, seq.name, seq_success)

                    if not seq_success:
                        success = False
                        if self._abort_requested:
                            break
                        if seq.is_gate() or self._workflow.stop_on_failure:
                            self._log.error(f"Stopping the workflow: '{seq.name}' failed")
                            self.signal_script_output.emit(f"Stopping the workflow: '{seq.name}' failed")
                            stop = True
                            break

                    # Check for pause request after sequence completes
                    if self._pause_requested and not self._abort_requested:
                        self._log.info("Workflow paused")
                        self._paused = True
                        self.signal_workflow_paused.emit()
                        # Wait for resume
                        self._resume_event.wait()
                        self._resume_event.clear()
                        if self._abort_requested:
                            self._log.info("Workflow stopped while paused")
                            success = False
                            break

                if self._abort_requested:
                    break

        except Exception as e:
            self._log.exception(f"Workflow error: {e}")
            self.signal_error.emit(str(e))
            success = False

        finally:
            self._running = False
            self._paused = False
            self._log.info(f"Workflow finished, success={success}")
            self.signal_workflow_finished.emit(success)

    def _resolve_config_path(self, seq: SequenceItem) -> str:
        """The acquisition.yaml this step runs, with {last_targets} substituted."""
        if seq.uses_last_targets():
            if not self._last_targets_path:
                raise ValueError(f"'{LAST_TARGETS_PLACEHOLDER}' used before any Analyze step produced targets")
            return self._last_targets_path
        return seq.config_path or ""

    def _run_acquisition(self, seq=None) -> bool:
        """Request acquisition from main thread and wait for completion.

        Args:
            seq: the acquisition SequenceItem (a bare config path is accepted for compatibility).
        """
        if not isinstance(seq, SequenceItem):
            seq = SequenceItem(name="Acquisition", sequence_type=SequenceType.ACQUISITION, config_path=seq or None)
        try:
            config_path = self._resolve_config_path(seq)
            wells = parse_well_list(seq.wells)
        except ValueError as e:
            self._log.error(str(e))
            self.signal_error.emit(str(e))
            return False

        self._acquisition_complete_event.clear()
        self._acquisition_failed_msg = None
        self.signal_acquisition_waiting.emit()

        save_dir = (seq.save_dir or "").strip()
        request = AcquisitionRequest(config_path=config_path, wells=wells, name=seq.name, save_dir=save_dir)
        if config_path:
            self.signal_script_output.emit(f"Acquisition config: {config_path}")
        if wells:
            self.signal_script_output.emit(f"Wells: {', '.join(wells)}")
        if save_dir:
            self.signal_script_output.emit(f"Saving to: {save_dir}")
        self.signal_request_acquisition.emit(request)

        # Wait for completion (check abort periodically)
        while not self._acquisition_complete_event.wait(timeout=1.0):
            if self._abort_requested:
                self._log.info("Acquisition wait aborted")
                return False

        if self._acquisition_failed_msg:
            self._log.error(f"Acquisition '{seq.name}' failed: {self._acquisition_failed_msg}")
            self.signal_error.emit(f"Acquisition '{seq.name}' failed: {self._acquisition_failed_msg}")
            return False

        # Log the actual acquisition path, and remember it so that a following
        # script or analyze sequence can analyse what was just acquired.
        if self._acquisition_path_getter:
            try:
                actual_path = self._acquisition_path_getter()
                if actual_path:
                    self._last_acquisition_path = actual_path
                    self.signal_script_output.emit(f"Acquisition saved to: {actual_path}")
            except Exception as e:
                self._log.warning(f"Could not get acquisition path: {e}")

        if self._acquisition_result_getter:
            try:
                reason = self._acquisition_result_getter()
            except Exception as e:
                self._log.warning(f"Could not get acquisition result: {e}")
                reason = None
            if reason in ("failed_to_start", "error", "aborted", "user_abort"):
                message = f"Acquisition '{seq.name}' ended with: {reason}"
                self._log.error(message)
                self.signal_error.emit(message)
                return False
            if reason and reason != "completed":
                self.signal_script_output.emit(f"  acquisition ended with: {reason}")

        return True

    def _run_objective(self, seq: SequenceItem) -> bool:
        """Ask the GUI to switch objectives and wait for its verdict."""
        name = (seq.objective or "").strip()
        if not name:
            self.signal_error.emit(f"Objective step '{seq.name}': no objective given")
            return False

        self._objective_complete_event.clear()
        self._objective_result = (False, "no response from the GUI")
        self.signal_script_output.emit(f"Objective -> {name}")
        self.signal_request_objective.emit(name, bool(seq.require_laser_af_reference))

        started = time.monotonic()
        while not self._objective_complete_event.wait(timeout=1.0):
            if self._abort_requested:
                self._log.info("Objective wait aborted")
                return False
            if time.monotonic() - started > OBJECTIVE_STEP_TIMEOUT_S:
                message = f"Objective step '{seq.name}': switching to {name} timed out"
                self._log.error(message)
                self.signal_error.emit(message)
                return False

        ok, message = self._objective_result
        if not ok:
            text = f"Objective step '{seq.name}' failed: {message or 'unknown error'}"
            self._log.error(text)
            self.signal_error.emit(text)
            return False
        if message:
            self.signal_script_output.emit(f"  {message}")
        return True

    def _run_analyze(self, seq: SequenceItem) -> bool:
        """Run the smart-acquisition analysis in this thread and remember where the targets went."""
        acq_dir = seq.input_path or self._last_acquisition_path
        if not acq_dir:
            self.signal_error.emit(f"Analyze step '{seq.name}': no acquisition to analyse yet")
            return False

        def progress(done: int, total: int):
            if self._abort_requested:
                raise WorkflowAborted()
            self.signal_script_output.emit(f"  analysed {done}/{total} FOV(s)")

        self.signal_script_output.emit(f"Analysing {acq_dir} with '{seq.analyzer}'")
        try:
            from control.core.smart_acquisition import step

            summary = step.analyze_and_emit(
                acq_dir,
                seq.analyzer,
                seq.analyzer_params,
                seq.template_path,
                seq.output_dir,
                carry_z=seq.carry_z or seq.sweep_z,
                sweep_z=seq.sweep_z,
                xy_offset_mm=seq.xy_offset_mm,
                z_offset_mm=seq.z_offset_mm,
                overlay=seq.overlay,
                prefix=seq.prefix,
                progress_cb=progress,
                log=self._log,
            )
        except WorkflowAborted:
            self._log.info("Analysis aborted")
            return False
        except Exception as e:
            self._log.exception(f"Analysis failed: {e}")
            self.signal_error.emit(f"Analyze step '{seq.name}' failed: {e}")
            return False

        self._last_targets_path = summary["acquisition_yaml"]
        self.signal_script_output.emit(
            f"  {summary['written']} target(s) written to {summary['acquisition_yaml']} ({summary['widget_type']})"
        )
        for message in summary.get("errors", []):
            self.signal_script_output.emit(f"  warning: {message}")
        if summary["dropped_out_of_limits"]:
            self.signal_script_output.emit(
                f"  warning: dropped {summary['dropped_out_of_limits']} target(s) outside the stage limits"
            )
        if summary["inferred"]:
            self.signal_script_output.emit(f"  note: {summary['inferred']} target(s) inferred from the lattice fit")
        for message in summary.get("notes", []):
            self.signal_script_output.emit(f"  note: {message}")
        if summary["xy_offset_mm"] is not None or summary["z_offset_mm"] is not None:
            self.signal_script_output.emit(
                f"  applied offsets xy={summary['xy_offset_mm']} mm  z={summary['z_offset_mm']} mm"
            )
        return True

    def get_last_acquisition_path(self) -> Optional[str]:
        """Path of the most recently completed acquisition in this workflow."""
        return self._last_acquisition_path

    def _script_environment(self, cycle_value: Optional[int]) -> dict:
        """Environment for a script sequence, including the last acquisition path."""
        env = dict(os.environ)
        env[ENV_LAST_ACQUISITION_PATH] = self._last_acquisition_path or ""
        env[ENV_LAST_TARGETS_PATH] = self._last_targets_path or ""
        env[ENV_CYCLE] = str(cycle_value if cycle_value is not None else self._current_cycle + 1)
        return env

    def _run_script(self, seq: SequenceItem, cycle_value: Optional[int]) -> bool:
        """Execute a script sequence."""
        try:
            cmd = seq.build_command(cycle_value)
            self._log.info(f"Running command: {' '.join(cmd)}")
            self.signal_script_output.emit(f"$ {' '.join(cmd)}")
            if self._last_acquisition_path:
                self.signal_script_output.emit(f"  {ENV_LAST_ACQUISITION_PATH}={self._last_acquisition_path}")

            with self._process_lock:
                self._current_process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=self._script_environment(cycle_value),
                )

            # Stream output
            for line in self._current_process.stdout:
                line = line.rstrip()
                self._log.debug(f"Script output: {line}")
                self.signal_script_output.emit(line)

            self._current_process.wait()
            return_code = self._current_process.returncode

            if return_code != 0:
                error_msg = f"Script '{seq.name}' failed with exit code {return_code}"
                self._log.error(error_msg)
                self.signal_error.emit(error_msg)
                return False

            self._log.info(f"Script '{seq.name}' completed successfully")
            return True

        except FileNotFoundError as e:
            error_msg = f"Script '{seq.name}' failed: {e}"
            self._log.error(error_msg)
            self.signal_error.emit(error_msg)
            return False

        except Exception as e:
            error_msg = f"Script '{seq.name}' error: {e}"
            self._log.exception(error_msg)
            self.signal_error.emit(error_msg)
            return False

        finally:
            with self._process_lock:
                self._current_process = None
