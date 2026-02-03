"""
Workflow Runner data models.

Pure data classes for workflow sequences - no Qt or EventBus dependencies.
Ported from upstream control/workflow_runner.py (da8f193a).
"""

from dataclasses import dataclass, field
from enum import Enum
import shlex
import sys
from typing import List, Optional

import yaml


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
    def from_dict(cls, data: dict, ensure_acquisition: bool = True) -> "Workflow":
        """Deserialize from dictionary.

        Args:
            data: Dictionary with 'sequences' and 'num_cycles' keys.
            ensure_acquisition: If True (default), ensure an Acquisition sequence exists.
                Set to False when deserializing for execution (the UI already validated).
        """
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
        if ensure_acquisition:
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
