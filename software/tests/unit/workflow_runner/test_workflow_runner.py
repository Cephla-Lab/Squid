"""Tests for the Workflow Runner module.

Ported from upstream tests/control/test_workflow_runner.py (da8f193a),
adapted to arch_v2 with EventBus patterns.
"""

import os
import sys
import tempfile
import time
from unittest.mock import MagicMock

import pytest

from squid.backend.controllers.workflow_runner.models import (
    SequenceItem,
    SequenceType,
    Workflow,
)
from squid.backend.controllers.workflow_runner.state import (
    WorkflowRunnerState,
    StartWorkflowCommand,
    StopWorkflowCommand,
    PauseWorkflowCommand,
    ResumeWorkflowCommand,
    WorkflowRunnerStateChanged,
    WorkflowCycleStarted,
    WorkflowSequenceStarted,
    WorkflowSequenceFinished,
    WorkflowScriptOutput,
    WorkflowError,
    WorkflowLoadConfigRequest,
    WorkflowLoadConfigResponse,
)
from squid.backend.controllers.workflow_runner.workflow_runner_controller import (
    WorkflowRunnerController,
)
from squid.core.events import EventBus, AcquisitionFinished


# ============================================================================
# Data Model Tests
# ============================================================================


class TestSequenceItem:
    """Tests for SequenceItem dataclass."""

    def test_is_acquisition_true(self):
        seq = SequenceItem(name="Acquisition", sequence_type=SequenceType.ACQUISITION)
        assert seq.is_acquisition() is True

    def test_is_acquisition_false(self):
        seq = SequenceItem(
            name="Test Script",
            sequence_type=SequenceType.SCRIPT,
            script_path="/path/to/script.py",
        )
        assert seq.is_acquisition() is False

    def test_get_cycle_values_empty(self):
        seq = SequenceItem(name="Test", sequence_type=SequenceType.SCRIPT)
        assert seq.get_cycle_values() == []

    def test_get_cycle_values_single(self):
        seq = SequenceItem(
            name="Test",
            sequence_type=SequenceType.SCRIPT,
            cycle_arg_values="42",
        )
        assert seq.get_cycle_values() == [42]

    def test_get_cycle_values_multiple(self):
        seq = SequenceItem(
            name="Test",
            sequence_type=SequenceType.SCRIPT,
            cycle_arg_values="1,2,3,4,5",
        )
        assert seq.get_cycle_values() == [1, 2, 3, 4, 5]

    def test_get_cycle_values_with_spaces(self):
        seq = SequenceItem(
            name="Test",
            sequence_type=SequenceType.SCRIPT,
            cycle_arg_values="1, 2, 3",
        )
        assert seq.get_cycle_values() == [1, 2, 3]

    def test_get_cycle_values_invalid(self):
        seq = SequenceItem(
            name="Test",
            sequence_type=SequenceType.SCRIPT,
            cycle_arg_values="a,b,c",
        )
        with pytest.raises(ValueError, match="Invalid cycle values"):
            seq.get_cycle_values()

    def test_build_command_acquisition_raises(self):
        seq = SequenceItem(name="Acquisition", sequence_type=SequenceType.ACQUISITION)
        with pytest.raises(ValueError, match="Cannot build command for acquisition"):
            seq.build_command()

    def test_build_command_default_python(self):
        seq = SequenceItem(
            name="Test",
            sequence_type=SequenceType.SCRIPT,
            script_path="/path/to/script.py",
        )
        cmd = seq.build_command()
        assert cmd == [sys.executable, "/path/to/script.py"]

    def test_build_command_with_python_path(self):
        seq = SequenceItem(
            name="Test",
            sequence_type=SequenceType.SCRIPT,
            script_path="/path/to/script.py",
            python_path="/usr/bin/python3.10",
        )
        cmd = seq.build_command()
        assert cmd == ["/usr/bin/python3.10", "/path/to/script.py"]

    def test_build_command_with_conda_env(self):
        seq = SequenceItem(
            name="Test",
            sequence_type=SequenceType.SCRIPT,
            script_path="/path/to/script.py",
            conda_env="myenv",
        )
        cmd = seq.build_command()
        assert cmd == ["conda", "run", "-n", "myenv", "python", "/path/to/script.py"]

    def test_build_command_conda_overrides_python_path(self):
        seq = SequenceItem(
            name="Test",
            sequence_type=SequenceType.SCRIPT,
            script_path="/path/to/script.py",
            python_path="/usr/bin/python3.10",
            conda_env="myenv",
        )
        cmd = seq.build_command()
        assert "conda" in cmd
        assert "/usr/bin/python3.10" not in cmd

    def test_build_command_with_arguments(self):
        seq = SequenceItem(
            name="Test",
            sequence_type=SequenceType.SCRIPT,
            script_path="/path/to/script.py",
            arguments="--flag value --other",
        )
        cmd = seq.build_command()
        assert cmd == [sys.executable, "/path/to/script.py", "--flag", "value", "--other"]

    def test_build_command_with_cycle_value(self):
        seq = SequenceItem(
            name="Test",
            sequence_type=SequenceType.SCRIPT,
            script_path="/path/to/script.py",
            cycle_arg_name="port",
        )
        cmd = seq.build_command(cycle_value=5)
        assert cmd == [sys.executable, "/path/to/script.py", "--port", "5"]

    def test_build_command_no_cycle_arg_name(self):
        seq = SequenceItem(
            name="Test",
            sequence_type=SequenceType.SCRIPT,
            script_path="/path/to/script.py",
        )
        cmd = seq.build_command(cycle_value=5)
        assert cmd == [sys.executable, "/path/to/script.py"]


class TestWorkflow:
    """Tests for Workflow dataclass."""

    def test_create_default(self):
        workflow = Workflow.create_default()
        assert len(workflow.sequences) == 1
        assert workflow.sequences[0].name == "Acquisition"
        assert workflow.sequences[0].is_acquisition()
        assert workflow.sequences[0].included is True

    def test_get_included_sequences(self):
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
        workflow = Workflow.create_default()
        assert workflow.has_acquisition() is True

    def test_has_acquisition_false(self):
        workflow = Workflow(
            sequences=[
                SequenceItem(name="Script", sequence_type=SequenceType.SCRIPT),
            ]
        )
        assert workflow.has_acquisition() is False

    def test_ensure_acquisition_exists_adds(self):
        workflow = Workflow(
            sequences=[
                SequenceItem(name="Script", sequence_type=SequenceType.SCRIPT),
            ]
        )
        workflow.ensure_acquisition_exists()
        assert workflow.has_acquisition() is True
        assert workflow.sequences[0].name == "Acquisition"

    def test_ensure_acquisition_exists_no_duplicate(self):
        workflow = Workflow.create_default()
        original_count = len(workflow.sequences)
        workflow.ensure_acquisition_exists()
        assert len(workflow.sequences) == original_count

    def test_validate_cycle_args_no_values(self):
        workflow = Workflow(num_cycles=5)
        errors = workflow.validate_cycle_args()
        assert errors == []

    def test_validate_cycle_args_matching(self):
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
            assert rest_seq.config_path == orig_seq.config_path
            assert rest_seq.cycle_arg_name == orig_seq.cycle_arg_name
            assert rest_seq.cycle_arg_values == orig_seq.cycle_arg_values

    def test_save_and_load_file(self):
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

    def test_load_file_without_acquisition(self):
        """Test loading file without Acquisition preserves original sequences (no auto-add)."""
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
            # Workflows can now have zero acquisition sequences
            assert not loaded.has_acquisition()
            assert len(loaded.sequences) == 1
            assert loaded.sequences[0].name == "Script Only"
        finally:
            os.unlink(temp_path)

    def test_acquisition_with_config_path(self):
        """Test acquisition sequence with config_path."""
        workflow = Workflow(
            num_cycles=1,
            sequences=[
                SequenceItem(
                    name="Pre-scan",
                    sequence_type=SequenceType.ACQUISITION,
                    config_path="/path/to/prescan.yaml",
                    included=True,
                ),
                SequenceItem(
                    name="Main Acquisition",
                    sequence_type=SequenceType.ACQUISITION,
                    config_path=None,  # Uses current settings
                    included=True,
                ),
            ],
        )

        # Test serialization roundtrip
        data = workflow.to_dict()
        assert data["sequences"][0]["config_path"] == "/path/to/prescan.yaml"
        # Second sequence has no config_path (key not present since value is None)
        assert data["sequences"][1].get("config_path") is None

        restored = Workflow.from_dict(data, ensure_acquisition=False)
        assert restored.sequences[0].config_path == "/path/to/prescan.yaml"
        assert restored.sequences[1].config_path is None

    def test_multiple_acquisitions_save_load(self):
        """Test saving and loading a workflow with multiple acquisition sequences."""
        original = Workflow(
            num_cycles=2,
            sequences=[
                SequenceItem(
                    name="Pre-scan",
                    sequence_type=SequenceType.ACQUISITION,
                    config_path="/path/to/prescan.yaml",
                    included=True,
                ),
                SequenceItem(
                    name="Fluidics",
                    sequence_type=SequenceType.SCRIPT,
                    script_path="/path/to/fluidics.py",
                    arguments="--wash",
                    included=True,
                ),
                SequenceItem(
                    name="Main Scan",
                    sequence_type=SequenceType.ACQUISITION,
                    config_path=None,
                    included=True,
                ),
            ],
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            temp_path = f.name

        try:
            original.save_to_file(temp_path)
            loaded = Workflow.load_from_file(temp_path)

            assert loaded.num_cycles == 2
            assert len(loaded.sequences) == 3
            assert loaded.sequences[0].name == "Pre-scan"
            assert loaded.sequences[0].is_acquisition()
            assert loaded.sequences[0].config_path == "/path/to/prescan.yaml"
            assert loaded.sequences[1].name == "Fluidics"
            assert not loaded.sequences[1].is_acquisition()
            assert loaded.sequences[2].name == "Main Scan"
            assert loaded.sequences[2].is_acquisition()
            assert loaded.sequences[2].config_path is None
        finally:
            os.unlink(temp_path)

    def test_to_dict_only_includes_set_optional_fields(self):
        """Test that to_dict only includes optional fields that have values."""
        workflow = Workflow(
            sequences=[
                SequenceItem(
                    name="Acquisition",
                    sequence_type=SequenceType.ACQUISITION,
                    included=True,
                ),
            ],
        )
        data = workflow.to_dict()
        seq_dict = data["sequences"][0]
        # Only required fields should be present, not optional None fields
        assert "name" in seq_dict
        assert "type" in seq_dict
        assert "included" in seq_dict
        assert "script_path" not in seq_dict
        assert "arguments" not in seq_dict
        assert "config_path" not in seq_dict


# ============================================================================
# Controller Tests
# ============================================================================


class TestWorkflowRunnerController:
    """Tests for WorkflowRunnerController."""

    @pytest.fixture
    def event_bus(self):
        """Create a real EventBus for testing."""
        bus = EventBus()
        bus.start()
        yield bus
        bus.stop()
        bus.clear()

    @pytest.fixture
    def multipoint(self):
        """Create a mock MultiPointController."""
        mock = MagicMock()
        mock.experiment_ID = None
        mock.run_acquisition = MagicMock()
        return mock

    @pytest.fixture
    def controller(self, event_bus, multipoint):
        """Create a WorkflowRunnerController."""
        ctrl = WorkflowRunnerController(
            event_bus=event_bus,
            multipoint_controller=multipoint,
        )
        yield ctrl
        ctrl.shutdown()

    def _collect_events(self, event_bus, event_type):
        """Helper to collect events of a given type."""
        collected = []

        def handler(event):
            collected.append(event)

        event_bus.subscribe(event_type, handler)
        return collected

    def _wait_for_state(self, controller, target_state, timeout=5.0):
        """Wait for the controller to reach a target state."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if controller.state == target_state:
                return True
            time.sleep(0.05)
        return False

    def test_initial_state(self, controller):
        """Test initial state is IDLE."""
        assert controller.state == WorkflowRunnerState.IDLE

    def test_start_workflow_transitions_state(self, controller, event_bus):
        """Test starting a workflow changes state from IDLE."""
        # Create a script that takes a moment
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("import time; time.sleep(0.5)\n")
            temp_script = f.name

        try:
            workflow = Workflow(
                sequences=[
                    SequenceItem(
                        name="Test",
                        sequence_type=SequenceType.SCRIPT,
                        script_path=temp_script,
                        included=True,
                    ),
                ],
            )

            controller.start_workflow(workflow.to_dict())

            # Should transition to RUNNING_SCRIPT
            assert self._wait_for_state(controller, WorkflowRunnerState.RUNNING_SCRIPT)

            # Wait for completion
            assert self._wait_for_state(controller, WorkflowRunnerState.COMPLETED)
        finally:
            os.unlink(temp_script)

    def test_start_via_command(self, controller, event_bus):
        """Test starting workflow via EventBus command."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("print('hello')\n")
            temp_script = f.name

        try:
            workflow = Workflow(
                sequences=[
                    SequenceItem(
                        name="Test",
                        sequence_type=SequenceType.SCRIPT,
                        script_path=temp_script,
                        included=True,
                    ),
                ],
            )

            event_bus.publish(StartWorkflowCommand(workflow_dict=workflow.to_dict()))

            # Wait for completion
            assert self._wait_for_state(controller, WorkflowRunnerState.COMPLETED, timeout=10)
        finally:
            os.unlink(temp_script)

    def test_run_script_success(self, controller, event_bus):
        """Test running a successful script."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("print('Hello from test script')\n")
            temp_script = f.name

        try:
            workflow = Workflow(
                sequences=[
                    SequenceItem(
                        name="Test",
                        sequence_type=SequenceType.SCRIPT,
                        script_path=temp_script,
                        included=True,
                    ),
                ],
            )

            controller.start_workflow(workflow.to_dict())
            assert self._wait_for_state(controller, WorkflowRunnerState.COMPLETED)
        finally:
            os.unlink(temp_script)

    def test_run_script_failure(self, controller, event_bus):
        """Test running a script that fails results in FAILED state."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("import sys; sys.exit(1)\n")
            temp_script = f.name

        try:
            workflow = Workflow(
                sequences=[
                    SequenceItem(
                        name="Failing",
                        sequence_type=SequenceType.SCRIPT,
                        script_path=temp_script,
                        included=True,
                    ),
                ],
            )

            # Script failure is tracked; workflow continues through all sequences
            # but ends with FAILED state
            controller.start_workflow(workflow.to_dict())
            assert self._wait_for_state(controller, WorkflowRunnerState.FAILED)
        finally:
            os.unlink(temp_script)

    def test_run_script_not_found(self, controller, event_bus):
        """Test running a script that doesn't exist."""
        workflow = Workflow(
            sequences=[
                SequenceItem(
                    name="Missing",
                    sequence_type=SequenceType.SCRIPT,
                    script_path="/nonexistent/path/script.py",
                    included=True,
                ),
            ],
        )

        controller.start_workflow(workflow.to_dict())
        # Missing script returns False, workflow ends with FAILED
        assert self._wait_for_state(controller, WorkflowRunnerState.FAILED)

    def test_run_script_with_cycle_value(self, controller, event_bus):
        """Test running a script with cycle argument."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(
                "import argparse\n"
                "parser = argparse.ArgumentParser()\n"
                "parser.add_argument('--port', type=int)\n"
                "args = parser.parse_args()\n"
                "print(f'Port: {args.port}')\n"
            )
            temp_script = f.name

        try:
            workflow = Workflow(
                num_cycles=2,
                sequences=[
                    SequenceItem(
                        name="Test",
                        sequence_type=SequenceType.SCRIPT,
                        script_path=temp_script,
                        cycle_arg_name="port",
                        cycle_arg_values="42,43",
                        included=True,
                    ),
                ],
            )

            controller.start_workflow(workflow.to_dict())
            assert self._wait_for_state(controller, WorkflowRunnerState.COMPLETED)
        finally:
            os.unlink(temp_script)

    def test_abort_stops_workflow(self, controller, event_bus):
        """Test aborting a running workflow."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("import time; time.sleep(10)\n")
            temp_script = f.name

        try:
            workflow = Workflow(
                sequences=[
                    SequenceItem(
                        name="Long Running",
                        sequence_type=SequenceType.SCRIPT,
                        script_path=temp_script,
                        included=True,
                    ),
                ],
            )

            controller.start_workflow(workflow.to_dict())
            assert self._wait_for_state(controller, WorkflowRunnerState.RUNNING_SCRIPT)

            # Abort
            controller.abort()
            assert self._wait_for_state(controller, WorkflowRunnerState.ABORTED)
        finally:
            os.unlink(temp_script)

    def test_abort_via_command(self, controller, event_bus):
        """Test aborting via EventBus command."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("import time; time.sleep(10)\n")
            temp_script = f.name

        try:
            workflow = Workflow(
                sequences=[
                    SequenceItem(
                        name="Long Running",
                        sequence_type=SequenceType.SCRIPT,
                        script_path=temp_script,
                        included=True,
                    ),
                ],
            )

            controller.start_workflow(workflow.to_dict())
            assert self._wait_for_state(controller, WorkflowRunnerState.RUNNING_SCRIPT)

            event_bus.publish(StopWorkflowCommand())
            assert self._wait_for_state(controller, WorkflowRunnerState.ABORTED)
        finally:
            os.unlink(temp_script)

    def test_pause_and_resume(self, controller, event_bus):
        """Test pause and resume of workflow."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("import time; time.sleep(5)\n")
            temp_script = f.name

        try:
            workflow = Workflow(
                sequences=[
                    SequenceItem(
                        name="Long Running",
                        sequence_type=SequenceType.SCRIPT,
                        script_path=temp_script,
                        included=True,
                    ),
                ],
            )

            controller.start_workflow(workflow.to_dict())
            assert self._wait_for_state(controller, WorkflowRunnerState.RUNNING_SCRIPT)

            # Pause
            result = controller.pause()
            assert result is True
            assert controller.state == WorkflowRunnerState.PAUSED

            # Resume
            result = controller.resume()
            assert result is True

            # After resume, it should eventually complete or be in running state
            # Clean up by aborting
            time.sleep(0.2)
            controller.abort()
            assert self._wait_for_state(controller, WorkflowRunnerState.ABORTED)
        finally:
            os.unlink(temp_script)

    def test_acquisition_sequence(self, controller, event_bus, multipoint):
        """Test acquisition sequence calls multipoint and waits for AcquisitionFinished."""
        workflow = Workflow(
            sequences=[
                SequenceItem(
                    name="Acquisition",
                    sequence_type=SequenceType.ACQUISITION,
                    included=True,
                ),
            ],
        )

        controller.start_workflow(workflow.to_dict())
        assert self._wait_for_state(controller, WorkflowRunnerState.RUNNING_ACQUISITION)

        # Verify multipoint.run_acquisition was called
        time.sleep(0.2)
        multipoint.run_acquisition.assert_called_with(acquire_current_fov=False)

        # Controller sets experiment_ID on the mock; read it back for the reply
        experiment_id = multipoint.experiment_ID

        # Simulate AcquisitionFinished with matching experiment_id
        event_bus.publish(AcquisitionFinished(success=True, experiment_id=experiment_id))
        assert self._wait_for_state(controller, WorkflowRunnerState.COMPLETED)

    def test_acquisition_failure(self, controller, event_bus, multipoint):
        """Test acquisition failure results in FAILED workflow state."""
        workflow = Workflow(
            sequences=[
                SequenceItem(
                    name="Acquisition",
                    sequence_type=SequenceType.ACQUISITION,
                    included=True,
                ),
            ],
        )

        controller.start_workflow(workflow.to_dict())
        assert self._wait_for_state(controller, WorkflowRunnerState.RUNNING_ACQUISITION)

        # Controller sets experiment_ID on the mock; read it back for the reply
        time.sleep(0.2)
        experiment_id = multipoint.experiment_ID

        # Simulate failed acquisition with matching experiment_id
        event_bus.publish(AcquisitionFinished(success=False, experiment_id=experiment_id))
        assert self._wait_for_state(controller, WorkflowRunnerState.FAILED)

    def test_acquisition_ignores_mismatched_experiment_id(self, controller, event_bus, multipoint):
        """Test that AcquisitionFinished with wrong experiment_id is ignored."""
        workflow = Workflow(
            sequences=[
                SequenceItem(
                    name="Acquisition",
                    sequence_type=SequenceType.ACQUISITION,
                    included=True,
                ),
            ],
        )

        controller.start_workflow(workflow.to_dict())
        assert self._wait_for_state(controller, WorkflowRunnerState.RUNNING_ACQUISITION)

        time.sleep(0.2)
        experiment_id = multipoint.experiment_ID

        # Publish with wrong experiment_id - should be ignored
        event_bus.publish(AcquisitionFinished(success=True, experiment_id="wrong_id"))
        time.sleep(0.3)
        assert controller.state == WorkflowRunnerState.RUNNING_ACQUISITION

        # Now publish with correct experiment_id - should complete
        event_bus.publish(AcquisitionFinished(success=True, experiment_id=experiment_id))
        assert self._wait_for_state(controller, WorkflowRunnerState.COMPLETED)

    def test_state_change_events_published(self, controller, event_bus):
        """Test that state change events are published."""
        state_events = self._collect_events(event_bus, WorkflowRunnerStateChanged)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("print('hello')\n")
            temp_script = f.name

        try:
            workflow = Workflow(
                sequences=[
                    SequenceItem(
                        name="Test",
                        sequence_type=SequenceType.SCRIPT,
                        script_path=temp_script,
                        included=True,
                    ),
                ],
            )

            controller.start_workflow(workflow.to_dict())
            assert self._wait_for_state(controller, WorkflowRunnerState.COMPLETED)

            # Allow events to propagate
            time.sleep(0.3)

            # Should have at least: IDLE->RUNNING_SCRIPT, RUNNING_SCRIPT->COMPLETED
            assert len(state_events) >= 2
            assert state_events[0].new_state == "RUNNING_SCRIPT"
            assert state_events[-1].new_state == "COMPLETED"
        finally:
            os.unlink(temp_script)

    def test_script_output_events(self, controller, event_bus):
        """Test that script output is published as events."""
        output_events = self._collect_events(event_bus, WorkflowScriptOutput)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("print('test output line')\n")
            temp_script = f.name

        try:
            workflow = Workflow(
                sequences=[
                    SequenceItem(
                        name="Test",
                        sequence_type=SequenceType.SCRIPT,
                        script_path=temp_script,
                        included=True,
                    ),
                ],
            )

            controller.start_workflow(workflow.to_dict())
            assert self._wait_for_state(controller, WorkflowRunnerState.COMPLETED)

            # Allow events to propagate
            time.sleep(0.3)

            # Should have output events (command echo + actual output)
            lines = [e.line for e in output_events]
            assert any("test output line" in line for line in lines)
        finally:
            os.unlink(temp_script)

    def test_restart_after_completion(self, controller, event_bus):
        """Test that workflow can be restarted after completion."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("print('run')\n")
            temp_script = f.name

        try:
            workflow_dict = Workflow(
                sequences=[
                    SequenceItem(
                        name="Test",
                        sequence_type=SequenceType.SCRIPT,
                        script_path=temp_script,
                        included=True,
                    ),
                ],
            ).to_dict()

            # First run
            controller.start_workflow(workflow_dict)
            assert self._wait_for_state(controller, WorkflowRunnerState.COMPLETED)

            # Second run should succeed
            result = controller.start_workflow(workflow_dict)
            assert result is True
            assert self._wait_for_state(controller, WorkflowRunnerState.COMPLETED)
        finally:
            os.unlink(temp_script)

    def test_empty_workflow_fails(self, controller, event_bus):
        """Test that an empty workflow fails gracefully."""
        workflow = Workflow(sequences=[])
        error_events = self._collect_events(event_bus, WorkflowError)

        controller.start_workflow(workflow.to_dict())
        assert self._wait_for_state(controller, WorkflowRunnerState.FAILED)

        time.sleep(0.3)
        assert any("No sequences" in e.message for e in error_events)

    def test_multiple_cycles(self, controller, event_bus):
        """Test workflow with multiple cycles."""
        cycle_events = self._collect_events(event_bus, WorkflowCycleStarted)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("print('cycle')\n")
            temp_script = f.name

        try:
            workflow = Workflow(
                num_cycles=3,
                sequences=[
                    SequenceItem(
                        name="Test",
                        sequence_type=SequenceType.SCRIPT,
                        script_path=temp_script,
                        included=True,
                    ),
                ],
            )

            controller.start_workflow(workflow.to_dict())
            assert self._wait_for_state(controller, WorkflowRunnerState.COMPLETED)

            time.sleep(0.3)
            assert len(cycle_events) == 3
            assert cycle_events[0].current_cycle == 0
            assert cycle_events[1].current_cycle == 1
            assert cycle_events[2].current_cycle == 2
            assert all(e.total_cycles == 3 for e in cycle_events)
        finally:
            os.unlink(temp_script)

    def test_sequence_events(self, controller, event_bus):
        """Test that sequence started/finished events are published."""
        started_events = self._collect_events(event_bus, WorkflowSequenceStarted)
        finished_events = self._collect_events(event_bus, WorkflowSequenceFinished)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("print('ok')\n")
            temp_script = f.name

        try:
            workflow = Workflow(
                sequences=[
                    SequenceItem(
                        name="Script1",
                        sequence_type=SequenceType.SCRIPT,
                        script_path=temp_script,
                        included=True,
                    ),
                ],
            )

            controller.start_workflow(workflow.to_dict())
            assert self._wait_for_state(controller, WorkflowRunnerState.COMPLETED)

            time.sleep(0.3)
            assert len(started_events) >= 1
            assert started_events[0].sequence_name == "Script1"
            assert len(finished_events) >= 1
            assert finished_events[0].sequence_name == "Script1"
            assert finished_events[0].success is True
        finally:
            os.unlink(temp_script)

    def test_acquisition_with_config_path(self, controller, event_bus, multipoint):
        """Test acquisition with config_path publishes WorkflowLoadConfigRequest."""
        config_requests = self._collect_events(event_bus, WorkflowLoadConfigRequest)

        # Subscribe to config requests and auto-respond with success
        def auto_respond(event):
            event_bus.publish(WorkflowLoadConfigResponse(success=True))

        event_bus.subscribe(WorkflowLoadConfigRequest, auto_respond)

        workflow = Workflow(
            sequences=[
                SequenceItem(
                    name="Pre-scan",
                    sequence_type=SequenceType.ACQUISITION,
                    config_path="/path/to/config.yaml",
                    included=True,
                ),
            ],
        )

        controller.start_workflow(workflow.to_dict())
        assert self._wait_for_state(controller, WorkflowRunnerState.RUNNING_ACQUISITION)

        # Config request should have been published
        time.sleep(0.5)
        assert len(config_requests) >= 1
        assert config_requests[0].config_path == "/path/to/config.yaml"

        # Verify multipoint.run_acquisition was called (config loaded successfully)
        multipoint.run_acquisition.assert_called_with(acquire_current_fov=False)

        # Complete the acquisition
        experiment_id = multipoint.experiment_ID
        event_bus.publish(AcquisitionFinished(success=True, experiment_id=experiment_id))
        assert self._wait_for_state(controller, WorkflowRunnerState.COMPLETED)

    def test_acquisition_config_load_failure(self, controller, event_bus, multipoint):
        """Test acquisition fails when config loading fails."""
        # Subscribe to config requests and auto-respond with failure
        def auto_respond_failure(event):
            event_bus.publish(
                WorkflowLoadConfigResponse(success=False, error_message="File not found")
            )

        event_bus.subscribe(WorkflowLoadConfigRequest, auto_respond_failure)

        error_events = self._collect_events(event_bus, WorkflowError)

        workflow = Workflow(
            sequences=[
                SequenceItem(
                    name="Pre-scan",
                    sequence_type=SequenceType.ACQUISITION,
                    config_path="/nonexistent/config.yaml",
                    included=True,
                ),
            ],
        )

        controller.start_workflow(workflow.to_dict())
        assert self._wait_for_state(controller, WorkflowRunnerState.FAILED)

        # Verify multipoint.run_acquisition was NOT called
        time.sleep(0.3)
        multipoint.run_acquisition.assert_not_called()

        # Error event should have been published
        assert any("File not found" in e.message for e in error_events)

    def test_acquisition_without_config_path_skips_config_load(self, controller, event_bus, multipoint):
        """Test acquisition without config_path does not publish WorkflowLoadConfigRequest."""
        config_requests = self._collect_events(event_bus, WorkflowLoadConfigRequest)

        workflow = Workflow(
            sequences=[
                SequenceItem(
                    name="Acquisition",
                    sequence_type=SequenceType.ACQUISITION,
                    included=True,
                    # No config_path
                ),
            ],
        )

        controller.start_workflow(workflow.to_dict())
        assert self._wait_for_state(controller, WorkflowRunnerState.RUNNING_ACQUISITION)

        # No config request should have been published
        time.sleep(0.3)
        assert len(config_requests) == 0

        # Complete the acquisition
        experiment_id = multipoint.experiment_ID
        event_bus.publish(AcquisitionFinished(success=True, experiment_id=experiment_id))
        assert self._wait_for_state(controller, WorkflowRunnerState.COMPLETED)
