"""
Workflow Runner - Execute sequences of scripts and acquisitions.

This module provides data models and execution engine for running workflows
that combine external scripts with the built-in acquisition system.
"""

from dataclasses import dataclass, field
from enum import Enum
import shlex
from threading import Event, Lock, Thread
from typing import Callable, List, Optional
import subprocess
import sys

import yaml
from qtpy.QtCore import QObject, Signal

import squid.logging


class SequenceType(Enum):
    """Type of sequence step."""

    ACQUISITION = "acquisition"  # Built-in acquisition
    SCRIPT = "script"  # External script


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
    # Common:
    included: bool = True
    # Cycle arguments (optional) - pass different values to script for each cycle:
    cycle_arg_name: Optional[str] = None  # e.g., "port"
    cycle_arg_values: Optional[str] = None  # e.g., "1,2,3,4,5"

    def is_acquisition(self) -> bool:
        """Check if this is the built-in acquisition sequence."""
        return self.sequence_type == SequenceType.ACQUISITION

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
        if self.is_acquisition():
            raise ValueError("Cannot build command for acquisition sequence")

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
class Workflow:
    """Collection of sequences to run."""

    sequences: List[SequenceItem] = field(default_factory=list)
    num_cycles: int = 1

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
                values = seq.get_cycle_values()
                if len(values) != self.num_cycles:
                    errors.append(
                        f"Sequence '{seq.name}': has {len(values)} cycle values, but Cycles={self.num_cycles}"
                    )
        return errors

    def to_dict(self) -> dict:
        """Serialize to dictionary for YAML export."""
        return {
            "num_cycles": self.num_cycles,
            "sequences": [
                {
                    "name": s.name,
                    "type": s.sequence_type.value,
                    "included": s.included,
                    "script_path": s.script_path,
                    "arguments": s.arguments,
                    "python_path": s.python_path,
                    "conda_env": s.conda_env,
                    "cycle_arg_name": s.cycle_arg_name,
                    "cycle_arg_values": s.cycle_arg_values,
                }
                for s in self.sequences
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Workflow":
        """Deserialize from dictionary."""
        sequences = []
        for s in data.get("sequences", []):
            sequences.append(
                SequenceItem(
                    name=s["name"],
                    sequence_type=SequenceType(s["type"]),
                    included=s.get("included", True),
                    script_path=s.get("script_path"),
                    arguments=s.get("arguments"),
                    python_path=s.get("python_path"),
                    conda_env=s.get("conda_env"),
                    cycle_arg_name=s.get("cycle_arg_name"),
                    cycle_arg_values=s.get("cycle_arg_values"),
                )
            )
        workflow = cls(sequences=sequences, num_cycles=data.get("num_cycles", 1))
        workflow.ensure_acquisition_exists()
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
    signal_request_acquisition = Signal()  # request main window to start acquisition
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

    def set_workflow(self, workflow: Workflow):
        """Set the workflow to execute."""
        self._workflow = workflow

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
        self._resume_event.clear()
        self.signal_workflow_started.emit()
        success = True

        try:
            included_sequences = self._workflow.get_included_sequences()
            num_cycles = self._workflow.num_cycles
            self._log.info(f"Starting workflow with {len(included_sequences)} sequences, {num_cycles} cycle(s)")

            for cycle in range(num_cycles):
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
                        seq_success = self._run_acquisition()
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

    def _run_acquisition(self) -> bool:
        """Request acquisition from main thread and wait for completion."""
        self._acquisition_complete_event.clear()
        self.signal_request_acquisition.emit()
        self.signal_acquisition_waiting.emit()

        # Wait for acquisition to complete
        while not self._acquisition_complete_event.wait(timeout=1.0):
            if self._abort_requested:
                self._log.info("Acquisition wait aborted")
                return False

        # Log the actual acquisition path
        if self._acquisition_path_getter:
            try:
                actual_path = self._acquisition_path_getter()
                if actual_path:
                    self.signal_script_output.emit(f"Acquisition saved to: {actual_path}")
            except Exception as e:
                self._log.warning(f"Could not get acquisition path: {e}")

        return True

    def _run_script(self, seq: SequenceItem, cycle_value: Optional[int]) -> bool:
        """Execute a script sequence."""
        try:
            cmd = seq.build_command(cycle_value)
            self._log.info(f"Running command: {' '.join(cmd)}")
            self.signal_script_output.emit(f"$ {' '.join(cmd)}")

            with self._process_lock:
                self._current_process = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
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
