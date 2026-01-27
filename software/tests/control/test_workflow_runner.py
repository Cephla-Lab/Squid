"""Tests for the Workflow Runner module."""

import os
import sys
import tempfile
import pytest

from control.workflow_runner import (
    SequenceItem,
    SequenceType,
    Workflow,
    WorkflowRunner,
)


class TestSequenceItem:
    """Tests for SequenceItem dataclass."""

    def test_is_acquisition_true(self):
        """Test is_acquisition returns True for ACQUISITION type."""
        seq = SequenceItem(name="Acquisition", sequence_type=SequenceType.ACQUISITION)
        assert seq.is_acquisition() is True

    def test_is_acquisition_false(self):
        """Test is_acquisition returns False for SCRIPT type."""
        seq = SequenceItem(
            name="Test Script",
            sequence_type=SequenceType.SCRIPT,
            script_path="/path/to/script.py",
        )
        assert seq.is_acquisition() is False

    def test_get_cycle_values_empty(self):
        """Test get_cycle_values with no values."""
        seq = SequenceItem(name="Test", sequence_type=SequenceType.SCRIPT)
        assert seq.get_cycle_values() == []

    def test_get_cycle_values_single(self):
        """Test get_cycle_values with single value."""
        seq = SequenceItem(
            name="Test",
            sequence_type=SequenceType.SCRIPT,
            cycle_arg_values="42",
        )
        assert seq.get_cycle_values() == [42]

    def test_get_cycle_values_multiple(self):
        """Test get_cycle_values with multiple values."""
        seq = SequenceItem(
            name="Test",
            sequence_type=SequenceType.SCRIPT,
            cycle_arg_values="1,2,3,4,5",
        )
        assert seq.get_cycle_values() == [1, 2, 3, 4, 5]

    def test_get_cycle_values_with_spaces(self):
        """Test get_cycle_values handles spaces."""
        seq = SequenceItem(
            name="Test",
            sequence_type=SequenceType.SCRIPT,
            cycle_arg_values="1, 2, 3",
        )
        assert seq.get_cycle_values() == [1, 2, 3]

    def test_get_cycle_values_invalid(self):
        """Test get_cycle_values with invalid values raises ValueError."""
        seq = SequenceItem(
            name="Test",
            sequence_type=SequenceType.SCRIPT,
            cycle_arg_values="a,b,c",
        )
        with pytest.raises(ValueError, match="Invalid cycle values"):
            seq.get_cycle_values()

    def test_build_command_acquisition_raises(self):
        """Test build_command raises for acquisition sequence."""
        seq = SequenceItem(name="Acquisition", sequence_type=SequenceType.ACQUISITION)
        with pytest.raises(ValueError, match="Cannot build command for acquisition"):
            seq.build_command()

    def test_build_command_default_python(self):
        """Test build_command uses sys.executable by default."""
        seq = SequenceItem(
            name="Test",
            sequence_type=SequenceType.SCRIPT,
            script_path="/path/to/script.py",
        )
        cmd = seq.build_command()
        assert cmd == [sys.executable, "/path/to/script.py"]

    def test_build_command_with_python_path(self):
        """Test build_command uses specified python_path."""
        seq = SequenceItem(
            name="Test",
            sequence_type=SequenceType.SCRIPT,
            script_path="/path/to/script.py",
            python_path="/usr/bin/python3.10",
        )
        cmd = seq.build_command()
        assert cmd == ["/usr/bin/python3.10", "/path/to/script.py"]

    def test_build_command_with_conda_env(self):
        """Test build_command uses conda environment."""
        seq = SequenceItem(
            name="Test",
            sequence_type=SequenceType.SCRIPT,
            script_path="/path/to/script.py",
            conda_env="myenv",
        )
        cmd = seq.build_command()
        assert cmd == ["conda", "run", "-n", "myenv", "python", "/path/to/script.py"]

    def test_build_command_conda_overrides_python_path(self):
        """Test conda_env takes priority over python_path."""
        seq = SequenceItem(
            name="Test",
            sequence_type=SequenceType.SCRIPT,
            script_path="/path/to/script.py",
            python_path="/usr/bin/python3.10",
            conda_env="myenv",
        )
        cmd = seq.build_command()
        # conda_env should be used, not python_path
        assert "conda" in cmd
        assert "/usr/bin/python3.10" not in cmd

    def test_build_command_with_arguments(self):
        """Test build_command includes arguments."""
        seq = SequenceItem(
            name="Test",
            sequence_type=SequenceType.SCRIPT,
            script_path="/path/to/script.py",
            arguments="--flag value --other",
        )
        cmd = seq.build_command()
        assert cmd == [sys.executable, "/path/to/script.py", "--flag", "value", "--other"]

    def test_build_command_with_cycle_value(self):
        """Test build_command includes cycle argument."""
        seq = SequenceItem(
            name="Test",
            sequence_type=SequenceType.SCRIPT,
            script_path="/path/to/script.py",
            cycle_arg_name="port",
        )
        cmd = seq.build_command(cycle_value=5)
        assert cmd == [sys.executable, "/path/to/script.py", "--port", "5"]

    def test_build_command_no_cycle_arg_name(self):
        """Test build_command ignores cycle_value without arg_name."""
        seq = SequenceItem(
            name="Test",
            sequence_type=SequenceType.SCRIPT,
            script_path="/path/to/script.py",
        )
        cmd = seq.build_command(cycle_value=5)
        # Should not include --port since cycle_arg_name is not set
        assert cmd == [sys.executable, "/path/to/script.py"]


class TestWorkflow:
    """Tests for Workflow dataclass."""

    def test_create_default(self):
        """Test create_default creates workflow with Acquisition."""
        workflow = Workflow.create_default()
        assert len(workflow.sequences) == 1
        assert workflow.sequences[0].name == "Acquisition"
        assert workflow.sequences[0].is_acquisition()
        assert workflow.sequences[0].included is True

    def test_get_included_sequences(self):
        """Test get_included_sequences filters correctly."""
        workflow = Workflow(
            sequences=[
                SequenceItem(name="A", sequence_type=SequenceType.ACQUISITION, included=True),
                SequenceItem(name="B", sequence_type=SequenceType.SCRIPT, included=False),
                SequenceItem(name="C", sequence_type=SequenceType.SCRIPT, included=True),
            ]
        )
        included = workflow.get_included_sequences()
        assert len(included) == 2
        assert included[0].name == "A"
        assert included[1].name == "C"

    def test_has_acquisition_true(self):
        """Test has_acquisition returns True when present."""
        workflow = Workflow.create_default()
        assert workflow.has_acquisition() is True

    def test_has_acquisition_false(self):
        """Test has_acquisition returns False when missing."""
        workflow = Workflow(
            sequences=[
                SequenceItem(name="Script", sequence_type=SequenceType.SCRIPT),
            ]
        )
        assert workflow.has_acquisition() is False

    def test_ensure_acquisition_exists_adds(self):
        """Test ensure_acquisition_exists adds Acquisition if missing."""
        workflow = Workflow(
            sequences=[
                SequenceItem(name="Script", sequence_type=SequenceType.SCRIPT),
            ]
        )
        workflow.ensure_acquisition_exists()
        assert workflow.has_acquisition() is True
        assert workflow.sequences[0].name == "Acquisition"

    def test_ensure_acquisition_exists_no_duplicate(self):
        """Test ensure_acquisition_exists doesn't add duplicate."""
        workflow = Workflow.create_default()
        original_count = len(workflow.sequences)
        workflow.ensure_acquisition_exists()
        assert len(workflow.sequences) == original_count

    def test_validate_cycle_args_no_values(self):
        """Test validate_cycle_args with no cycle values."""
        workflow = Workflow(num_cycles=5)
        errors = workflow.validate_cycle_args()
        assert errors == []

    def test_validate_cycle_args_matching(self):
        """Test validate_cycle_args with matching count."""
        workflow = Workflow(
            num_cycles=5,
            sequences=[
                SequenceItem(
                    name="Test",
                    sequence_type=SequenceType.SCRIPT,
                    included=True,
                    cycle_arg_values="1,2,3,4,5",
                ),
            ],
        )
        errors = workflow.validate_cycle_args()
        assert errors == []

    def test_validate_cycle_args_mismatch(self):
        """Test validate_cycle_args with mismatching count."""
        workflow = Workflow(
            num_cycles=5,
            sequences=[
                SequenceItem(
                    name="Test",
                    sequence_type=SequenceType.SCRIPT,
                    included=True,
                    cycle_arg_values="1,2,3",
                ),
            ],
        )
        errors = workflow.validate_cycle_args()
        assert len(errors) == 1
        assert "Test" in errors[0]
        assert "3" in errors[0]
        assert "5" in errors[0]

    def test_to_dict_and_from_dict_roundtrip(self):
        """Test serialization roundtrip."""
        original = Workflow(
            num_cycles=3,
            sequences=[
                SequenceItem(
                    name="Acquisition",
                    sequence_type=SequenceType.ACQUISITION,
                    included=True,
                ),
                SequenceItem(
                    name="Fluidics",
                    sequence_type=SequenceType.SCRIPT,
                    script_path="/path/to/fluidics.py",
                    arguments="--wash --cycles 3",
                    python_path="/usr/bin/python3.10",
                    conda_env=None,
                    included=True,
                    cycle_arg_name="port",
                    cycle_arg_values="1,2,3",
                ),
            ],
        )

        data = original.to_dict()
        restored = Workflow.from_dict(data)

        assert restored.num_cycles == original.num_cycles
        assert len(restored.sequences) == len(original.sequences)

        for orig_seq, rest_seq in zip(original.sequences, restored.sequences):
            assert rest_seq.name == orig_seq.name
            assert rest_seq.sequence_type == orig_seq.sequence_type
            assert rest_seq.included == orig_seq.included
            assert rest_seq.script_path == orig_seq.script_path
            assert rest_seq.arguments == orig_seq.arguments
            assert rest_seq.python_path == orig_seq.python_path
            assert rest_seq.conda_env == orig_seq.conda_env
            assert rest_seq.cycle_arg_name == orig_seq.cycle_arg_name
            assert rest_seq.cycle_arg_values == orig_seq.cycle_arg_values

    def test_save_and_load_file(self):
        """Test saving and loading from file."""
        original = Workflow(
            num_cycles=2,
            sequences=[
                SequenceItem(
                    name="Acquisition",
                    sequence_type=SequenceType.ACQUISITION,
                    included=True,
                ),
                SequenceItem(
                    name="Test Script",
                    sequence_type=SequenceType.SCRIPT,
                    script_path="/path/to/script.py",
                    arguments="--flag",
                    included=True,
                ),
            ],
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            temp_path = f.name

        try:
            original.save_to_file(temp_path)
            loaded = Workflow.load_from_file(temp_path)

            assert loaded.num_cycles == original.num_cycles
            assert len(loaded.sequences) == len(original.sequences)
            assert loaded.sequences[0].name == "Acquisition"
            assert loaded.sequences[1].name == "Test Script"
        finally:
            os.unlink(temp_path)

    def test_load_file_ensures_acquisition(self):
        """Test loading file without Acquisition adds one."""
        import yaml

        data = {
            "num_cycles": 1,
            "sequences": [
                {
                    "name": "Script Only",
                    "type": "script",
                    "included": True,
                    "script_path": "/path/to/script.py",
                    "arguments": None,
                    "python_path": None,
                    "conda_env": None,
                    "cycle_arg_name": None,
                    "cycle_arg_values": None,
                }
            ],
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            temp_path = f.name

        try:
            loaded = Workflow.load_from_file(temp_path)
            assert loaded.has_acquisition()
        finally:
            os.unlink(temp_path)


class TestWorkflowRunner:
    """Tests for WorkflowRunner class."""

    @pytest.fixture
    def runner(self):
        """Create a WorkflowRunner instance."""
        runner = WorkflowRunner()
        return runner

    def test_initial_state(self, runner):
        """Test initial runner state."""
        assert runner.is_running() is False

    def test_set_workflow(self, runner):
        """Test setting workflow."""
        workflow = Workflow.create_default()
        runner.set_workflow(workflow)
        assert runner._workflow == workflow

    def test_request_abort(self, runner):
        """Test abort request sets flag."""
        runner.request_abort()
        assert runner._abort_requested is True

    def test_on_acquisition_finished(self, runner):
        """Test acquisition finished sets event."""
        assert not runner._acquisition_complete_event.is_set()
        runner.on_acquisition_finished()
        assert runner._acquisition_complete_event.is_set()

    def test_run_script_success(self, runner):
        """Test running a successful script."""
        # Create a simple script that exits successfully
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("print('Hello from test script')\n")
            temp_script = f.name

        try:
            seq = SequenceItem(
                name="Test",
                sequence_type=SequenceType.SCRIPT,
                script_path=temp_script,
            )
            success = runner._run_script(seq, None)
            assert success is True
        finally:
            os.unlink(temp_script)

    def test_run_script_failure(self, runner):
        """Test running a script that fails."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("import sys; sys.exit(1)\n")
            temp_script = f.name

        try:
            seq = SequenceItem(
                name="Test",
                sequence_type=SequenceType.SCRIPT,
                script_path=temp_script,
            )
            success = runner._run_script(seq, None)
            assert success is False
        finally:
            os.unlink(temp_script)

    def test_run_script_not_found(self, runner):
        """Test running a script that doesn't exist."""
        seq = SequenceItem(
            name="Test",
            sequence_type=SequenceType.SCRIPT,
            script_path="/nonexistent/path/script.py",
        )
        success = runner._run_script(seq, None)
        assert success is False

    def test_run_script_with_cycle_value(self, runner):
        """Test running a script with cycle argument."""
        # Create a script that prints the port argument
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(
                """
import argparse
parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int)
args = parser.parse_args()
print(f'Port: {args.port}')
"""
            )
            temp_script = f.name

        try:
            seq = SequenceItem(
                name="Test",
                sequence_type=SequenceType.SCRIPT,
                script_path=temp_script,
                cycle_arg_name="port",
            )
            success = runner._run_script(seq, cycle_value=42)
            assert success is True
        finally:
            os.unlink(temp_script)

    def test_initial_pause_state(self, runner):
        """Test initial pause state is False."""
        assert runner.is_paused() is False

    def test_request_pause(self, runner):
        """Test pause request sets flag."""
        runner.request_pause()
        assert runner._pause_requested is True

    def test_request_resume_when_paused(self, runner):
        """Test resume clears paused state."""
        runner._paused = True
        runner._pause_requested = True
        runner.request_resume()
        assert runner._paused is False
        assert runner._pause_requested is False

    def test_request_stop(self, runner):
        """Test stop request sets abort flag."""
        runner.request_stop()
        assert runner._abort_requested is True

    def test_request_stop_when_paused(self, runner):
        """Test stop when paused resumes to allow exit."""
        runner._paused = True
        runner.request_stop()
        assert runner._abort_requested is True
        # resume_event should be set to allow workflow to exit
        assert runner._resume_event.is_set()
