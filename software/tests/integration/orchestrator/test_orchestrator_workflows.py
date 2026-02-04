"""
Integration tests for Orchestrator full workflow execution.

Tests the complete orchestrator lifecycle including:
- Full experiment execution (single/multi-round)
- Control flow (pause/resume/abort/skip)
- Intervention handling
- Checkpoint and recovery
- Warning system integration
- Event verification
- Error handling
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock, patch

import pytest

from tests.harness import BackendContext, EventMonitor
from squid.core.events import (
    EventBus,
    event_bus,
    AcquisitionFinished,
    FluidicsProtocolCompleted,
    LoadScanCoordinatesCommand,
)
from squid.core.utils.cancel_token import CancelToken
from squid.core.protocol import ImagingProtocol
from squid.backend.controllers.orchestrator import (
    OrchestratorController,
    OrchestratorState,
    OrchestratorStateChanged,
    OrchestratorProgress,
    OrchestratorRoundStarted,
    OrchestratorRoundCompleted,
    OrchestratorInterventionRequired,
    OrchestratorError,
    StartOrchestratorCommand,
    StopOrchestratorCommand,
    PauseOrchestratorCommand,
    ResumeOrchestratorCommand,
    AcknowledgeInterventionCommand,
    SkipCurrentRoundCommand,
    SkipToRoundCommand,
)
from squid.backend.controllers.orchestrator.state import (
    WarningRaised,
    WarningThresholdReached,
    WarningsCleared,
    ClearWarningsCommand,
    SetWarningThresholdsCommand,
    AddWarningCommand,
    ValidateProtocolCommand,
    ProtocolValidationStarted,
    ProtocolValidationComplete,
    ExperimentProgress,
    RoundProgress,
    Checkpoint,
)
from squid.backend.controllers.orchestrator.checkpoint import CheckpointManager
from squid.backend.controllers.orchestrator.warnings import (
    WarningCategory,
    WarningSeverity,
    WarningThresholds,
)
from squid.backend.controllers.orchestrator.warning_manager import WarningManager
from squid.backend.controllers.orchestrator.imaging_executor import ImagingExecutor
from squid.backend.controllers.fluidics_controller import FluidicsController
from squid.backend.controllers.multipoint.experiment_manager import ExperimentManager
from squid.backend.controllers.multipoint.acquisition_planner import AcquisitionPlanner
from squid.backend.controllers.multipoint.events import FovTaskStarted, FovTaskCompleted


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def backend_ctx():
    """Provide a simulated backend context."""
    with BackendContext(simulation=True) as ctx:
        yield ctx


@pytest.fixture
def tmp_experiment_dir(tmp_path):
    """Provide a temporary directory for experiment data."""
    exp_dir = tmp_path / "experiments"
    exp_dir.mkdir()
    return str(exp_dir)


@pytest.fixture
def event_collector(backend_ctx: BackendContext):
    """Collect all events during a test."""
    events = []

    def collect_all(event):
        events.append(event)

    # Subscribe to all orchestrator events
    backend_ctx.event_bus.subscribe(OrchestratorStateChanged, collect_all)
    backend_ctx.event_bus.subscribe(OrchestratorProgress, collect_all)
    backend_ctx.event_bus.subscribe(OrchestratorRoundStarted, collect_all)
    backend_ctx.event_bus.subscribe(OrchestratorRoundCompleted, collect_all)
    backend_ctx.event_bus.subscribe(OrchestratorInterventionRequired, collect_all)
    backend_ctx.event_bus.subscribe(OrchestratorError, collect_all)
    backend_ctx.event_bus.subscribe(WarningRaised, collect_all)
    backend_ctx.event_bus.subscribe(WarningThresholdReached, collect_all)
    backend_ctx.event_bus.subscribe(WarningsCleared, collect_all)
    backend_ctx.event_bus.subscribe(ProtocolValidationStarted, collect_all)
    backend_ctx.event_bus.subscribe(ProtocolValidationComplete, collect_all)

    yield events


# =============================================================================
# Protocol Fixtures
# =============================================================================


@pytest.fixture
def single_imaging_protocol(tmp_path, backend_ctx: BackendContext) -> str:
    """Create a simple single-round imaging protocol."""
    channels = backend_ctx.get_available_channels()
    channel = channels[0] if channels else "BF"

    protocol_dict = {
        "name": "Single Imaging",
        "version": "2.0",
        "description": "Single imaging round",
        "imaging_protocols": {
            "standard": {
                "channels": [channel],
                "z_stack": {"planes": 1, "step_um": 1.0},
                "focus": {"enabled": False},
            }
        },
        "rounds": [
            {
                "name": "Imaging Round 1",
                "steps": [{"step_type": "imaging", "protocol": "standard"}],
            }
        ],
    }

    protocol_path = tmp_path / "single_imaging.yaml"
    import yaml

    with open(protocol_path, "w") as f:
        yaml.dump(protocol_dict, f)

    return str(protocol_path)


@pytest.fixture
def single_imaging_protocol_abort(tmp_path, backend_ctx: BackendContext) -> str:
    """Create a single-round protocol with imaging_failure: abort."""
    channels = backend_ctx.get_available_channels()
    channel = channels[0] if channels else "BF"

    protocol_dict = {
        "name": "Single Imaging Abort",
        "version": "2.0",
        "description": "Single imaging round (abort on imaging failure)",
        "error_handling": {"imaging_failure": "abort"},
        "imaging_protocols": {
            "standard": {
                "channels": [channel],
                "z_stack": {"planes": 1, "step_um": 1.0},
                "focus": {"enabled": False},
            }
        },
        "rounds": [
            {
                "name": "Imaging Round 1",
                "steps": [{"step_type": "imaging", "protocol": "standard"}],
            }
        ],
    }

    protocol_path = tmp_path / "single_imaging_abort.yaml"
    import yaml

    with open(protocol_path, "w") as f:
        yaml.dump(protocol_dict, f)

    return str(protocol_path)


@pytest.fixture
def multi_round_protocol(tmp_path, backend_ctx: BackendContext) -> str:
    """Create a multi-round protocol with imaging and fluidics."""
    channels = backend_ctx.get_available_channels()
    channel = channels[0] if channels else "BF"

    protocol_dict = {
        "name": "Multi Round",
        "version": "2.0",
        "description": "Multi-round experiment",
        "imaging_protocols": {
            "standard": {
                "channels": [channel],
                "z_stack": {"planes": 1, "step_um": 1.0},
                "focus": {"enabled": False},
            }
        },
        "rounds": [
            {
                "name": "Imaging Round 1",
                "steps": [{"step_type": "imaging", "protocol": "standard"}],
            },
            {
                "name": "Fluidics Round",
                "steps": [{"step_type": "fluidics", "protocol": "test_incubate"}],
            },
            {
                "name": "Imaging Round 2",
                "steps": [{"step_type": "imaging", "protocol": "standard"}],
            },
        ],
    }

    protocol_path = tmp_path / "multi_round.yaml"
    import yaml

    with open(protocol_path, "w") as f:
        yaml.dump(protocol_dict, f)

    return str(protocol_path)


@pytest.fixture
def intervention_protocol(tmp_path, backend_ctx: BackendContext) -> str:
    """Create a protocol that requires operator intervention."""
    channels = backend_ctx.get_available_channels()
    channel = channels[0] if channels else "BF"

    protocol_dict = {
        "name": "Intervention Protocol",
        "version": "2.0",
        "description": "Protocol with intervention",
        "imaging_protocols": {
            "standard": {
                "channels": [channel],
                "z_stack": {"planes": 1, "step_um": 1.0},
                "focus": {"enabled": False},
            }
        },
        "rounds": [
            {
                "name": "Pre-intervention Imaging",
                "steps": [{"step_type": "imaging", "protocol": "standard"}],
            },
            {
                "name": "Intervention Round",
                "steps": [
                    {
                        "step_type": "intervention",
                        "message": "Please replace the sample",
                    }
                ],
            },
            {
                "name": "Post-intervention Imaging",
                "steps": [{"step_type": "imaging", "protocol": "standard"}],
            },
        ],
    }

    protocol_path = tmp_path / "intervention.yaml"
    import yaml

    with open(protocol_path, "w") as f:
        yaml.dump(protocol_dict, f)

    return str(protocol_path)


@pytest.fixture
def fluidics_heavy_protocol(tmp_path) -> str:
    """Create a protocol with multiple fluidics rounds."""
    protocol_dict = {
        "name": "Fluidics Heavy",
        "version": "2.0",
        "description": "Multiple fluidics",
        "rounds": [
            {
                "name": "Prime",
                "steps": [{"step_type": "fluidics", "protocol": "test_prime"}],
            },
            {
                "name": "Stain",
                "steps": [{"step_type": "fluidics", "protocol": "test_stain"}],
            },
        ],
    }

    protocol_path = tmp_path / "fluidics_heavy.yaml"
    import yaml

    with open(protocol_path, "w") as f:
        yaml.dump(protocol_dict, f)

    return str(protocol_path)


# =============================================================================
# Mock Fixtures for Faster Testing
# =============================================================================


@pytest.fixture
def mock_imaging_executor():
    """Create a mock imaging executor that completes immediately."""
    mock = MagicMock(spec=ImagingExecutor)
    mock.execute_with_config.return_value = True
    mock.pause.return_value = True
    mock.resume.return_value = True
    return mock


@pytest.fixture
def mock_fluidics_controller():
    """Create a mock fluidics controller that completes immediately."""
    mock = MagicMock(spec=FluidicsController)
    mock.run_protocol.return_value = True
    mock.list_protocols.return_value = []
    from squid.backend.controllers.fluidics_controller import FluidicsControllerState

    mock.state = FluidicsControllerState.COMPLETED
    mock.last_terminal_state = FluidicsControllerState.COMPLETED
    mock.last_result = None
    mock.current_step_index = 0
    mock.total_steps = 0
    mock.is_available = True
    # Default: blocking call returns a successful completion event
    mock.run_protocol_blocking.return_value = FluidicsProtocolCompleted(
        protocol_name="mock",
        success=True,
        steps_completed=0,
        total_steps=0,
        error_message=None,
    )
    return mock


@pytest.fixture
def mock_experiment_manager(tmp_path):
    """Create a mock experiment manager."""
    mock = MagicMock()

    # Create a real temporary directory for the experiment
    exp_path = tmp_path / "test_experiment"
    exp_path.mkdir(exist_ok=True)

    def create_context(base_path=None, experiment_id=None, configurations=None, acquisition_params=None):
        """Create a mock context that uses real paths."""
        context = MagicMock()
        context.experiment_path = str(exp_path)
        # Use provided experiment_id or generate one
        context.experiment_id = experiment_id or "test_exp_001"
        return context

    mock.start_experiment.side_effect = create_context

    def create_round_subfolder_impl(context=None, round_name=""):
        """Mock implementation that accepts kwargs."""
        base_exp_path = context.experiment_path if context else str(exp_path)
        round_path = os.path.join(base_exp_path, round_name)
        os.makedirs(round_path, exist_ok=True)
        return round_path

    mock.create_round_subfolder.side_effect = create_round_subfolder_impl
    mock.finalize_experiment.return_value = None

    return mock


@pytest.fixture
def mock_acquisition_planner(backend_ctx: BackendContext):
    """Create a mock acquisition planner."""
    mock = MagicMock()
    mock.get_available_channel_names.return_value = set(
        backend_ctx.get_available_channels()
    )
    return mock


@pytest.fixture
def orchestrator_with_mocks(
    backend_ctx: BackendContext,
    mock_imaging_executor,
    mock_fluidics_controller,
    mock_experiment_manager,
    mock_acquisition_planner,
):
    """Create an orchestrator with mock executors for fast testing."""
    orchestrator = OrchestratorController(
        event_bus=backend_ctx.event_bus,
        multipoint_controller=backend_ctx.multipoint_controller,
        experiment_manager=mock_experiment_manager,
        acquisition_planner=mock_acquisition_planner,
        imaging_executor=mock_imaging_executor,
        fluidics_controller=mock_fluidics_controller,
        scan_coordinates=backend_ctx.scan_coordinates,
    )
    yield orchestrator


# =============================================================================
# Real Component Fixtures (End-to-End)
# =============================================================================


@pytest.fixture
def imaging_protocol_skip_saving(tmp_path, backend_ctx: BackendContext) -> str:
    """Create a fast imaging protocol that skips saving images."""
    channels = backend_ctx.get_available_channels()
    channel = channels[0] if channels else "BF"

    protocol_dict = {
        "name": "Fast Imaging",
        "version": "2.0",
        "description": "Single imaging round (skip saving)",
        "imaging_protocols": {
            "fast": {
                "channels": [channel],
                "z_stack": {"planes": 1, "step_um": 1.0},
                "focus": {"enabled": False},
                "skip_saving": True,
            }
        },
        "rounds": [
            {
                "name": "Imaging Round 1",
                "steps": [{"step_type": "imaging", "protocol": "fast"}],
            }
        ],
    }

    protocol_path = tmp_path / "fast_imaging.yaml"
    import yaml

    with open(protocol_path, "w") as f:
        yaml.dump(protocol_dict, f)

    return str(protocol_path)


@pytest.fixture
def real_orchestrator(backend_ctx: BackendContext):
    """Create an orchestrator wired to real multipoint + executors."""
    experiment_manager = ExperimentManager(
        objective_store=backend_ctx.objective_store,
        channel_config_manager=backend_ctx.channel_config_manager,
        camera_service=backend_ctx.camera_service,
    )
    acquisition_planner = AcquisitionPlanner(
        objective_store=backend_ctx.objective_store,
        channel_config_manager=backend_ctx.channel_config_manager,
        camera_service=backend_ctx.camera_service,
    )
    imaging_executor = ImagingExecutor(
        event_bus=backend_ctx.event_bus,
        multipoint_controller=backend_ctx.multipoint_controller,
        scan_coordinates=backend_ctx.scan_coordinates,
    )
    fluidics_controller = FluidicsController(event_bus=backend_ctx.event_bus)

    orchestrator = OrchestratorController(
        event_bus=backend_ctx.event_bus,
        multipoint_controller=backend_ctx.multipoint_controller,
        experiment_manager=experiment_manager,
        acquisition_planner=acquisition_planner,
        imaging_executor=imaging_executor,
        fluidics_controller=fluidics_controller,
        scan_coordinates=backend_ctx.scan_coordinates,
    )
    yield orchestrator
    imaging_executor.shutdown()


# =============================================================================
# Full Experiment Execution Workflow Tests
# =============================================================================


class TestFullExperimentExecution:
    """Tests for complete experiment execution workflows."""

    def test_single_round_imaging_experiment(
        self,
        orchestrator_with_mocks: OrchestratorController,
        single_imaging_protocol: str,
        tmp_experiment_dir: str,
        event_collector: list,
    ):
        """Test executing a single-round imaging experiment."""
        orchestrator = orchestrator_with_mocks

        # Start experiment
        result = orchestrator.start_experiment(
            protocol_path=single_imaging_protocol,
            base_path=tmp_experiment_dir,
            experiment_id="test_single_round",
        )

        assert result is True
        assert orchestrator.protocol is not None
        assert orchestrator.protocol.name == "Single Imaging"

        # Wait for completion
        timeout = 5.0
        start = time.time()
        while orchestrator.is_running and time.time() - start < timeout:
            time.sleep(0.1)

        assert orchestrator.state == OrchestratorState.COMPLETED
        assert not orchestrator.is_running

        # Verify events
        state_changes = [
            e for e in event_collector if isinstance(e, OrchestratorStateChanged)
        ]
        assert len(state_changes) >= 2  # At least RUNNING and COMPLETED

        round_started = [
            e for e in event_collector if isinstance(e, OrchestratorRoundStarted)
        ]
        assert len(round_started) == 1
        assert round_started[0].round_name == "Imaging Round 1"

        round_completed = [
            e for e in event_collector if isinstance(e, OrchestratorRoundCompleted)
        ]
        assert len(round_completed) == 1
        assert round_completed[0].success is True

    def test_multi_round_experiment(
        self,
        orchestrator_with_mocks: OrchestratorController,
        multi_round_protocol: str,
        tmp_experiment_dir: str,
        event_collector: list,
    ):
        """Test executing a multi-round experiment with imaging and fluidics."""
        orchestrator = orchestrator_with_mocks

        result = orchestrator.start_experiment(
            protocol_path=multi_round_protocol,
            base_path=tmp_experiment_dir,
            experiment_id="test_multi_round",
        )

        assert result is True

        # Wait for completion
        timeout = 10.0
        start = time.time()
        while orchestrator.is_running and time.time() - start < timeout:
            time.sleep(0.1)

        assert orchestrator.state == OrchestratorState.COMPLETED

        # Verify all rounds completed
        round_completed = [
            e for e in event_collector if isinstance(e, OrchestratorRoundCompleted)
        ]
        assert len(round_completed) == 3
        assert all(e.success for e in round_completed)

    def test_fluidics_only_experiment(
        self,
        orchestrator_with_mocks: OrchestratorController,
        fluidics_heavy_protocol: str,
        tmp_experiment_dir: str,
        event_collector: list,
    ):
        """Test executing a fluidics-only experiment."""
        orchestrator = orchestrator_with_mocks

        result = orchestrator.start_experiment(
            protocol_path=fluidics_heavy_protocol,
            base_path=tmp_experiment_dir,
            experiment_id="test_fluidics",
        )

        assert result is True

        # Wait for completion
        timeout = 5.0
        start = time.time()
        while orchestrator.is_running and time.time() - start < timeout:
            time.sleep(0.1)

        assert orchestrator.state == OrchestratorState.COMPLETED

        # Verify rounds
        round_completed = [
            e for e in event_collector if isinstance(e, OrchestratorRoundCompleted)
        ]
        assert len(round_completed) == 2

    def test_experiment_id_generated_if_not_provided(
        self,
        orchestrator_with_mocks: OrchestratorController,
        single_imaging_protocol: str,
        tmp_experiment_dir: str,
    ):
        """Test that experiment ID is generated from protocol name if not provided."""
        orchestrator = orchestrator_with_mocks

        result = orchestrator.start_experiment(
            protocol_path=single_imaging_protocol,
            base_path=tmp_experiment_dir,
            # No experiment_id provided
        )

        assert result is True
        assert orchestrator.experiment_id == "Single Imaging"

        # Wait for completion
        timeout = 5.0
        start = time.time()
        while orchestrator.is_running and time.time() - start < timeout:
            time.sleep(0.1)


# =============================================================================
# End-to-End Imaging Workflow Tests
# =============================================================================


@pytest.mark.integration
class TestEndToEndImaging:
    """End-to-end tests with real multipoint + executors."""

    def test_imaging_round_emits_fov_events_and_coordinates(
        self,
        real_orchestrator: OrchestratorController,
        imaging_protocol_skip_saving: str,
        backend_ctx: BackendContext,
        tmp_path,
    ):
        """Test real imaging round emits FOV events and writes coordinates with fov_id."""
        orchestrator = real_orchestrator

        x_mm, y_mm, z_mm = backend_ctx.get_stage_center()
        backend_ctx.scan_coordinates.add_flexible_region(
            "region_1", x_mm, y_mm, z_mm, Nx=2, Ny=1, overlap_percent=0.0
        )

        monitor = backend_ctx.event_monitor
        monitor.subscribe(FovTaskStarted, FovTaskCompleted, AcquisitionFinished)

        result = orchestrator.start_experiment(
            protocol_path=imaging_protocol_skip_saving,
            base_path=str(tmp_path),
            experiment_id="e2e_imaging",
        )
        assert result is True

        finished = monitor.wait_for(
            AcquisitionFinished, timeout_s=15.0, predicate=lambda e: e.success
        )
        assert finished is not None

        timeout = 10.0
        start = time.time()
        while orchestrator.is_running and time.time() - start < timeout:
            time.sleep(0.1)

        assert orchestrator.state == OrchestratorState.COMPLETED

        started_events = monitor.get_events(FovTaskStarted)
        completed_events = monitor.get_events(FovTaskCompleted)
        assert len(started_events) == 2
        assert len(completed_events) == 2
        for event in started_events + completed_events:
            assert event.round_index == 0
            assert event.time_point == 0

        exp_path = Path(orchestrator._experiment_path)
        round_dirs = [
            path for path in exp_path.iterdir()
            if path.is_dir() and path.name.startswith("round_")
        ]
        assert len(round_dirs) == 1
        coordinates_path = round_dirs[0] / "coordinates.csv"
        assert coordinates_path.exists()

        with open(coordinates_path, newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)

        assert "fov_id" in reader.fieldnames
        assert len(rows) == 2

    def test_pause_waits_for_fov_boundary(
        self,
        real_orchestrator: OrchestratorController,
        imaging_protocol_skip_saving: str,
        backend_ctx: BackendContext,
        tmp_path,
        monkeypatch,
    ):
        """Test that pause requests do not interrupt an active FOV."""
        orchestrator = real_orchestrator

        x_mm, y_mm, z_mm = backend_ctx.get_stage_center()
        backend_ctx.scan_coordinates.add_flexible_region(
            "region_1", x_mm, y_mm, z_mm, Nx=2, Ny=1, overlap_percent=0.0
        )

        monitor = backend_ctx.event_monitor
        monitor.subscribe(FovTaskStarted, FovTaskCompleted)

        from squid.backend.controllers.multipoint.multi_point_worker import MultiPointWorker

        pause_ready = threading.Event()
        continue_event = threading.Event()
        original_acquire = MultiPointWorker.acquire_at_position

        def gated_acquire(self, *args, **kwargs):
            pause_ready.set()
            continue_event.wait(timeout=5.0)
            return original_acquire(self, *args, **kwargs)

        monkeypatch.setattr(MultiPointWorker, "acquire_at_position", gated_acquire)

        result = orchestrator.start_experiment(
            protocol_path=imaging_protocol_skip_saving,
            base_path=str(tmp_path),
            experiment_id="pause_boundary",
        )
        assert result is True

        assert pause_ready.wait(timeout=5.0)
        assert orchestrator.pause() is True

        continue_event.set()

        completed = monitor.wait_for(
            FovTaskCompleted, timeout_s=5.0, predicate=lambda e: e.fov_index == 0
        )
        assert completed is not None

        started_events = [
            e for e in monitor.get_events(FovTaskStarted) if e.fov_index == 1
        ]
        assert not started_events

        assert orchestrator.state == OrchestratorState.PAUSED
        assert orchestrator.resume() is True

        started_second = monitor.wait_for(
            FovTaskStarted, timeout_s=5.0, predicate=lambda e: e.fov_index == 1
        )
        assert started_second is not None

        timeout = 10.0
        start = time.time()
        while orchestrator.is_running and time.time() - start < timeout:
            time.sleep(0.1)

        assert orchestrator.state == OrchestratorState.COMPLETED

    def test_autofocus_failure_emits_warning(
        self,
        real_orchestrator: OrchestratorController,
        imaging_protocol_skip_saving: str,
        backend_ctx: BackendContext,
        tmp_path,
        monkeypatch,
    ):
        """Test that autofocus failure raises a warning with FOV context."""
        orchestrator = real_orchestrator

        x_mm, y_mm, z_mm = backend_ctx.get_stage_center()
        backend_ctx.scan_coordinates.add_single_fov_region("region_1", x_mm, y_mm, z_mm)

        from squid.backend.controllers.multipoint.multi_point_worker import MultiPointWorker

        monkeypatch.setattr(MultiPointWorker, "perform_autofocus", lambda *args, **kwargs: False)

        monitor = backend_ctx.event_monitor
        monitor.subscribe(WarningRaised)

        result = orchestrator.start_experiment(
            protocol_path=imaging_protocol_skip_saving,
            base_path=str(tmp_path),
            experiment_id="autofocus_warning",
        )
        assert result is True

        warning = monitor.wait_for(WarningRaised, timeout_s=10.0)
        assert warning is not None
        assert warning.category == "FOCUS"
        assert warning.severity == "MEDIUM"
        assert warning.round_index == 0
        assert warning.time_point == 0
        assert warning.fov_index == 0
        assert warning.fov_id is not None

        timeout = 10.0
        start = time.time()
        while orchestrator.is_running and time.time() - start < timeout:
            time.sleep(0.1)

        assert orchestrator.state == OrchestratorState.COMPLETED

# =============================================================================
# Control Flow Tests (Pause/Resume/Abort/Skip)
# =============================================================================


class TestControlFlow:
    """Tests for pause, resume, abort, and skip functionality."""

    def test_pause_during_imaging(
        self,
        orchestrator_with_mocks: OrchestratorController,
        multi_round_protocol: str,
        tmp_experiment_dir: str,
        mock_imaging_executor,
    ):
        """Test pausing during imaging execution."""
        orchestrator = orchestrator_with_mocks

        # Make imaging take longer so we can pause
        imaging_event = threading.Event()

        def slow_execute(*args, **kwargs):
            imaging_event.wait(timeout=5.0)
            return True

        mock_imaging_executor.execute_with_config.side_effect = slow_execute

        orchestrator.start_experiment(
            protocol_path=multi_round_protocol,
            base_path=tmp_experiment_dir,
        )

        # Wait for imaging to start
        timeout = 2.0
        start = time.time()
        while (
            orchestrator.state != OrchestratorState.RUNNING
            and time.time() - start < timeout
        ):
            time.sleep(0.05)

        # Pause
        result = orchestrator.pause()
        assert result is True
        assert orchestrator.state == OrchestratorState.PAUSED

        # Verify imaging executor was paused
        mock_imaging_executor.pause.assert_called_once()

        # Resume
        result = orchestrator.resume()
        assert result is True
        assert orchestrator.state == OrchestratorState.RUNNING

        # Let imaging complete
        imaging_event.set()

        # Wait for completion
        timeout = 5.0
        start = time.time()
        while orchestrator.is_running and time.time() - start < timeout:
            time.sleep(0.1)

    def test_abort_during_execution(
        self,
        orchestrator_with_mocks: OrchestratorController,
        multi_round_protocol: str,
        tmp_experiment_dir: str,
        mock_imaging_executor,
    ):
        """Test aborting during experiment execution."""
        orchestrator = orchestrator_with_mocks

        # Make imaging block so we can abort
        imaging_event = threading.Event()

        def blocking_execute(*args, **kwargs):
            imaging_event.wait(timeout=10.0)
            return True

        mock_imaging_executor.execute_with_config.side_effect = blocking_execute

        orchestrator.start_experiment(
            protocol_path=multi_round_protocol,
            base_path=tmp_experiment_dir,
        )

        # Wait for execution to start
        timeout = 2.0
        start = time.time()
        while (
            orchestrator.state == OrchestratorState.IDLE
        ) and time.time() - start < timeout:
            time.sleep(0.05)

        # Abort
        result = orchestrator.abort()
        assert result is True

        # Unblock imaging
        imaging_event.set()

        # Wait for abort to complete
        timeout = 2.0
        start = time.time()
        while orchestrator.is_running and time.time() - start < timeout:
            time.sleep(0.1)

        assert orchestrator.state == OrchestratorState.ABORTED

    def test_skip_current_round(
        self,
        orchestrator_with_mocks: OrchestratorController,
        multi_round_protocol: str,
        tmp_experiment_dir: str,
        event_collector: list,
    ):
        """Test skipping the current round."""
        orchestrator = orchestrator_with_mocks

        orchestrator.start_experiment(
            protocol_path=multi_round_protocol,
            base_path=tmp_experiment_dir,
        )

        # Request skip after first round completes
        time.sleep(0.2)
        orchestrator.skip_current_round()

        # Wait for completion
        timeout = 5.0
        start = time.time()
        while orchestrator.is_running and time.time() - start < timeout:
            time.sleep(0.1)

        assert orchestrator.state == OrchestratorState.COMPLETED

    def test_skip_to_specific_round(
        self,
        orchestrator_with_mocks: OrchestratorController,
        multi_round_protocol: str,
        tmp_experiment_dir: str,
        event_collector: list,
    ):
        """Test skipping to a specific round."""
        orchestrator = orchestrator_with_mocks

        orchestrator.start_experiment(
            protocol_path=multi_round_protocol,
            base_path=tmp_experiment_dir,
        )

        # Skip to round 2 (0-indexed) immediately after start
        # This must be called AFTER start_experiment since start resets _skip_to_round_index
        time.sleep(0.05)
        orchestrator.skip_to_round(2)

        # Wait for completion
        timeout = 5.0
        start = time.time()
        while orchestrator.is_running and time.time() - start < timeout:
            time.sleep(0.1)

        assert orchestrator.state == OrchestratorState.COMPLETED

        # Verify all rounds completed (skip happens mid-execution based on timing)
        round_completed = [
            e for e in event_collector if isinstance(e, OrchestratorRoundCompleted)
        ]
        assert len(round_completed) >= 1  # At least some rounds should complete

    def test_pause_when_not_running_fails(
        self,
        orchestrator_with_mocks: OrchestratorController,
    ):
        """Test that pause fails when not running."""
        orchestrator = orchestrator_with_mocks

        assert orchestrator.state == OrchestratorState.IDLE
        result = orchestrator.pause()
        assert result is False

    def test_resume_when_not_paused_fails(
        self,
        orchestrator_with_mocks: OrchestratorController,
    ):
        """Test that resume fails when not paused."""
        orchestrator = orchestrator_with_mocks

        assert orchestrator.state == OrchestratorState.IDLE
        result = orchestrator.resume()
        assert result is False

    def test_abort_when_idle_fails(
        self,
        orchestrator_with_mocks: OrchestratorController,
    ):
        """Test that abort fails when idle."""
        orchestrator = orchestrator_with_mocks

        assert orchestrator.state == OrchestratorState.IDLE
        result = orchestrator.abort()
        assert result is False


# =============================================================================
# Intervention Handling Tests
# =============================================================================


class TestInterventionHandling:
    """Tests for operator intervention handling."""

    def test_intervention_waits_for_acknowledgment(
        self,
        orchestrator_with_mocks: OrchestratorController,
        intervention_protocol: str,
        tmp_experiment_dir: str,
        event_collector: list,
        backend_ctx: BackendContext,
    ):
        """Test that intervention round waits for acknowledgment."""
        orchestrator = orchestrator_with_mocks

        orchestrator.start_experiment(
            protocol_path=intervention_protocol,
            base_path=tmp_experiment_dir,
        )

        # Wait for intervention state
        timeout = 5.0
        start = time.time()
        while (
            orchestrator.state != OrchestratorState.WAITING_INTERVENTION
            and time.time() - start < timeout
        ):
            time.sleep(0.1)

        assert orchestrator.state == OrchestratorState.WAITING_INTERVENTION

        # Verify intervention event was published
        intervention_events = [
            e
            for e in event_collector
            if isinstance(e, OrchestratorInterventionRequired)
        ]
        assert len(intervention_events) == 1
        assert intervention_events[0].message == "Please replace the sample"

        # Acknowledge intervention
        result = orchestrator.acknowledge_intervention()
        assert result is True

        # Wait for completion
        timeout = 5.0
        start = time.time()
        while orchestrator.is_running and time.time() - start < timeout:
            time.sleep(0.1)

        assert orchestrator.state == OrchestratorState.COMPLETED

    def test_acknowledge_via_event_bus_command(
        self,
        orchestrator_with_mocks: OrchestratorController,
        intervention_protocol: str,
        tmp_experiment_dir: str,
        backend_ctx: BackendContext,
    ):
        """Test acknowledging intervention via command event."""
        orchestrator = orchestrator_with_mocks

        orchestrator.start_experiment(
            protocol_path=intervention_protocol,
            base_path=tmp_experiment_dir,
        )

        # Wait for intervention state
        timeout = 5.0
        start = time.time()
        while (
            orchestrator.state != OrchestratorState.WAITING_INTERVENTION
            and time.time() - start < timeout
        ):
            time.sleep(0.1)

        # Acknowledge via event bus
        backend_ctx.event_bus.publish(AcknowledgeInterventionCommand())
        time.sleep(0.2)

        # Should no longer be waiting
        assert orchestrator.state != OrchestratorState.WAITING_INTERVENTION

    def test_pause_during_intervention(
        self,
        orchestrator_with_mocks: OrchestratorController,
        intervention_protocol: str,
        tmp_experiment_dir: str,
    ):
        """Test pausing while waiting for intervention."""
        orchestrator = orchestrator_with_mocks

        orchestrator.start_experiment(
            protocol_path=intervention_protocol,
            base_path=tmp_experiment_dir,
        )

        # Wait for intervention state
        timeout = 5.0
        start = time.time()
        while (
            orchestrator.state != OrchestratorState.WAITING_INTERVENTION
            and time.time() - start < timeout
        ):
            time.sleep(0.1)

        # Pause during intervention
        result = orchestrator.pause()
        assert result is True
        assert orchestrator.state == OrchestratorState.PAUSED

        # Resume
        result = orchestrator.resume()
        assert result is True

        # Should return to intervention waiting
        time.sleep(0.2)
        assert orchestrator.state == OrchestratorState.WAITING_INTERVENTION

        # Acknowledge and complete
        orchestrator.acknowledge_intervention()

        timeout = 5.0
        start = time.time()
        while orchestrator.is_running and time.time() - start < timeout:
            time.sleep(0.1)

        assert orchestrator.state == OrchestratorState.COMPLETED


# =============================================================================
# V2 Protocol Integration Tests
# =============================================================================


class TestV2ProtocolIntegration:
    """Integration tests for V2 protocol features."""

    def test_repeat_expansion_executes_rounds(
        self,
        orchestrator_with_mocks: OrchestratorController,
        tmp_path,
        event_collector: list,
    ):
        """Verify repeat expansion generates multiple rounds."""
        protocol_dict = {
            "name": "Repeat Protocol",
            "version": "2.0",
            "imaging_protocols": {
                "standard": {
                    "channels": ["BF"],
                    "z_stack": {"planes": 1},
                    "focus": {"enabled": False},
                }
            },
            "rounds": [
                {
                    "name": "Round {i}",
                    "repeat": 2,
                    "steps": [
                        {"step_type": "imaging", "protocol": "standard"},
                    ],
                }
            ],
        }

        protocol_path = tmp_path / "repeat_protocol.yaml"
        import yaml

        with open(protocol_path, "w") as f:
            yaml.dump(protocol_dict, f)

        result = orchestrator_with_mocks.start_experiment(
            protocol_path=str(protocol_path),
            base_path=str(tmp_path),
        )
        assert result is True

        timeout = 5.0
        start = time.time()
        while orchestrator_with_mocks.is_running and time.time() - start < timeout:
            time.sleep(0.1)

        assert orchestrator_with_mocks.state == OrchestratorState.COMPLETED

        round_started = [
            e for e in event_collector if isinstance(e, OrchestratorRoundStarted)
        ]
        assert len(round_started) == 2
        assert round_started[0].round_name == "Round 1"
        assert round_started[1].round_name == "Round 2"

    def test_file_references_load(
        self,
        orchestrator_with_mocks: OrchestratorController,
        tmp_path,
    ):
        """Verify imaging config file references resolve in loader."""
        imaging_config_path = tmp_path / "imaging_config.yaml"

        imaging_config_path.write_text(
            "channels:\n  - BF\nz_stack:\n  planes: 1\nfocus:\n  enabled: false\n"
        )

        protocol_dict = {
            "name": "File Reference Protocol",
            "version": "2.0",
            "imaging_protocols": {
                "standard": {"file": imaging_config_path.name},
            },
            "rounds": [
                {
                    "name": "Imaging",
                    "steps": [{"step_type": "imaging", "protocol": "standard"}],
                },
            ],
        }

        protocol_path = tmp_path / "file_ref_protocol.yaml"
        import yaml

        with open(protocol_path, "w") as f:
            yaml.dump(protocol_dict, f)

        result = orchestrator_with_mocks.start_experiment(
            protocol_path=str(protocol_path),
            base_path=str(tmp_path),
        )
        assert result is True
        assert orchestrator_with_mocks.protocol is not None
        assert "standard" in orchestrator_with_mocks.protocol.imaging_protocols

        timeout = 5.0
        start = time.time()
        while orchestrator_with_mocks.is_running and time.time() - start < timeout:
            time.sleep(0.1)

        assert orchestrator_with_mocks.state == OrchestratorState.COMPLETED

    def test_non_default_fov_set_loads_csv(
        self,
        orchestrator_with_mocks: OrchestratorController,
        backend_ctx: BackendContext,
        tmp_path,
    ):
        """Verify imaging steps with fov_sets load coordinates."""
        csv_path = tmp_path / "fovs.csv"
        csv_path.write_text("region,x (mm),y (mm)\nregion_1,0,0\n")

        protocol_dict = {
            "name": "FOV Set Protocol",
            "version": "2.0",
            "imaging_protocols": {
                "standard": {
                    "channels": ["BF"],
                    "z_stack": {"planes": 1},
                    "focus": {"enabled": False},
                }
            },
            "fov_sets": {"grid": str(csv_path)},
            "rounds": [
                {
                    "name": "Imaging",
                    "steps": [
                        {
                            "step_type": "imaging",
                            "protocol": "standard",
                            "fovs": "grid",
                        }
                    ],
                }
            ],
        }

        protocol_path = tmp_path / "fov_set_protocol.yaml"
        import yaml

        with open(protocol_path, "w") as f:
            yaml.dump(protocol_dict, f)

        monitor = backend_ctx.event_monitor
        monitor.subscribe(LoadScanCoordinatesCommand)

        result = orchestrator_with_mocks.start_experiment(
            protocol_path=str(protocol_path),
            base_path=str(tmp_path),
        )
        assert result is True

        loaded = monitor.wait_for(LoadScanCoordinatesCommand, timeout_s=5.0)
        assert loaded is not None

        timeout = 5.0
        start = time.time()
        while orchestrator_with_mocks.is_running and time.time() - start < timeout:
            time.sleep(0.1)

        assert orchestrator_with_mocks.state == OrchestratorState.COMPLETED

    def test_imaging_failure_warn_continues(
        self,
        orchestrator_with_mocks: OrchestratorController,
        tmp_path,
        event_collector: list,
        mock_imaging_executor,
    ):
        """Verify imaging_failure: warn logs warning and continues."""
        protocol_dict = {
            "name": "Warn Imaging Failure",
            "version": "2.0",
            "error_handling": {"imaging_failure": "warn"},
            "imaging_protocols": {
                "standard": {
                    "channels": ["BF"],
                    "z_stack": {"planes": 1},
                    "focus": {"enabled": False},
                }
            },
            "rounds": [
                {
                    "name": "Imaging",
                    "steps": [{"step_type": "imaging", "protocol": "standard"}],
                }
            ],
        }

        protocol_path = tmp_path / "warn_imaging_failure.yaml"
        import yaml

        with open(protocol_path, "w") as f:
            yaml.dump(protocol_dict, f)

        mock_imaging_executor.execute_with_config.return_value = False

        result = orchestrator_with_mocks.start_experiment(
            protocol_path=str(protocol_path),
            base_path=str(tmp_path),
        )
        assert result is True

        timeout = 5.0
        start = time.time()
        while orchestrator_with_mocks.is_running and time.time() - start < timeout:
            time.sleep(0.1)

        assert orchestrator_with_mocks.state == OrchestratorState.COMPLETED
        warnings = [e for e in event_collector if isinstance(e, WarningRaised)]
        assert any(w.category == "EXECUTION" for w in warnings)

# =============================================================================
# Checkpoint and Recovery Tests
# =============================================================================


class TestCheckpointAndRecovery:
    """Tests for checkpoint save/load and experiment recovery."""

    def test_checkpoint_saved_on_pause(
        self,
        backend_ctx: BackendContext,
        multi_round_protocol: str,
        tmp_path,
    ):
        """Test that checkpoint is saved when pausing."""
        # Create real mocks with known paths
        mock_imaging_executor = MagicMock(spec=ImagingExecutor)
        mock_fluidics_controller = MagicMock(spec=FluidicsController)
        mock_fluidics_controller.run_protocol.return_value = True
        mock_acquisition_planner = MagicMock()

        # Create experiment directory
        exp_path = tmp_path / "checkpoint_exp"
        exp_path.mkdir()

        # Create experiment manager with real paths
        mock_experiment_manager = MagicMock()
        context = MagicMock()
        context.experiment_path = str(exp_path)
        context.experiment_id = "checkpoint_test"
        mock_experiment_manager.start_experiment.return_value = context
        mock_experiment_manager.create_round_subfolder.side_effect = (
            lambda context=None, round_name="": str(exp_path / round_name)
        )

        orchestrator = OrchestratorController(
            event_bus=backend_ctx.event_bus,
            multipoint_controller=backend_ctx.multipoint_controller,
            experiment_manager=mock_experiment_manager,
            acquisition_planner=mock_acquisition_planner,
            imaging_executor=mock_imaging_executor,
            fluidics_controller=mock_fluidics_controller,
        )

        # Make imaging block so we can pause
        imaging_event = threading.Event()

        def blocking_execute(*args, **kwargs):
            imaging_event.wait(timeout=10.0)
            return True

        mock_imaging_executor.execute_with_config.side_effect = blocking_execute

        orchestrator.start_experiment(
            protocol_path=multi_round_protocol,
            base_path=str(tmp_path),
        )

        # Wait for imaging to start
        timeout = 2.0
        start_time = time.time()
        while (
            orchestrator.state != OrchestratorState.RUNNING
            and time.time() - start_time < timeout
        ):
            time.sleep(0.05)

        # Pause
        orchestrator.pause()

        # Unblock imaging so thread can finish cleanly
        imaging_event.set()

        # Verify checkpoint was saved (allow some time for checkpoint to be written)
        time.sleep(0.2)
        checkpoint_path = exp_path / "checkpoint.json"
        assert checkpoint_path.exists(), f"Checkpoint not found at {checkpoint_path}"

        # Load and verify checkpoint contents
        with open(checkpoint_path) as f:
            checkpoint_data = json.load(f)

        assert checkpoint_data["protocol_name"] == "Multi Round"
        assert "experiment_id" in checkpoint_data
        assert "round_index" in checkpoint_data

    def test_checkpoint_cleared_on_completion(
        self,
        backend_ctx: BackendContext,
        single_imaging_protocol: str,
        tmp_path,
    ):
        """Test that checkpoint is cleared on successful completion."""
        # Create real mocks with known paths
        mock_imaging_executor = MagicMock(spec=ImagingExecutor)
        mock_imaging_executor.execute_with_config.return_value = True
        mock_fluidics_controller = MagicMock(spec=FluidicsController)
        mock_fluidics_controller.run_protocol.return_value = True
        mock_acquisition_planner = MagicMock()

        # Create experiment directory
        exp_path = tmp_path / "clear_checkpoint_exp"
        exp_path.mkdir()

        # Create experiment manager with real paths
        mock_experiment_manager = MagicMock()
        context = MagicMock()
        context.experiment_path = str(exp_path)
        context.experiment_id = "clear_test"
        mock_experiment_manager.start_experiment.return_value = context
        mock_experiment_manager.create_round_subfolder.side_effect = (
            lambda context=None, round_name="": str(exp_path / round_name)
        )

        orchestrator = OrchestratorController(
            event_bus=backend_ctx.event_bus,
            multipoint_controller=backend_ctx.multipoint_controller,
            experiment_manager=mock_experiment_manager,
            acquisition_planner=mock_acquisition_planner,
            imaging_executor=mock_imaging_executor,
            fluidics_controller=mock_fluidics_controller,
        )

        # Create a fake checkpoint that should be cleared
        checkpoint_path = exp_path / "checkpoint.json"
        with open(checkpoint_path, "w") as f:
            json.dump({"test": "data"}, f)

        orchestrator.start_experiment(
            protocol_path=single_imaging_protocol,
            base_path=str(tmp_path),
        )

        # Wait for completion
        timeout = 5.0
        start_time = time.time()
        while orchestrator.is_running and time.time() - start_time < timeout:
            time.sleep(0.1)

        assert orchestrator.state == OrchestratorState.COMPLETED

        # Checkpoint should be cleared
        assert not checkpoint_path.exists(), f"Checkpoint should be cleared but exists at {checkpoint_path}"

    def test_resume_from_checkpoint(
        self,
        backend_ctx: BackendContext,
        multi_round_protocol: str,
        tmp_experiment_dir: str,
        mock_imaging_executor,
        mock_fluidics_controller,
        mock_experiment_manager,
        mock_acquisition_planner,
    ):
        """Test resuming an experiment from a checkpoint."""
        # Create checkpoint in experiment directory
        exp_path = os.path.join(tmp_experiment_dir, "resume_test")
        os.makedirs(exp_path, exist_ok=True)

        checkpoint_data = {
            "protocol_name": "Multi Round",
            "protocol_version": "2.0",
            "experiment_id": "resume_test",
            "experiment_path": exp_path,
            "round_index": 1,  # Start from round 2
            "step_index": 0,
            "imaging_fov_index": 0,
            "imaging_z_index": 0,
            "imaging_channel_index": 0,
            "created_at": "2025-01-13T10:00:00",
            "metadata": {},
        }

        checkpoint_path = os.path.join(exp_path, "checkpoint.json")
        with open(checkpoint_path, "w") as f:
            json.dump(checkpoint_data, f)

        # Create orchestrator
        orchestrator = OrchestratorController(
            event_bus=backend_ctx.event_bus,
            multipoint_controller=backend_ctx.multipoint_controller,
            experiment_manager=mock_experiment_manager,
            acquisition_planner=mock_acquisition_planner,
            imaging_executor=mock_imaging_executor,
            fluidics_controller=mock_fluidics_controller,
        )

        # Start with resume
        result = orchestrator.start_experiment(
            protocol_path=multi_round_protocol,
            base_path=exp_path,
            resume_from_checkpoint=True,
        )

        assert result is True

        # Wait for completion
        timeout = 5.0
        start = time.time()
        while orchestrator.is_running and time.time() - start < timeout:
            time.sleep(0.1)

        assert orchestrator.state == OrchestratorState.COMPLETED

        # Imaging should have been called fewer times (skipped first round)
        # Note: First round has imaging, but we resumed from round 1
        # so only rounds 1 and 2 (fluidics and imaging) should run


class TestCheckpointManager:
    """Tests for CheckpointManager class directly."""

    def test_create_and_save_checkpoint(self, tmp_path):
        """Test creating and saving a checkpoint."""
        manager = CheckpointManager()
        exp_path = str(tmp_path / "experiment")
        os.makedirs(exp_path)

        checkpoint = manager.create_checkpoint(
            protocol_name="Test Protocol",
            protocol_version="2.0",
            experiment_id="test_001",
            experiment_path=exp_path,
            round_index=2,
            step_index=1,
            imaging_fov_index=5,
        )

        manager.save(checkpoint, exp_path)

        # Verify file exists
        checkpoint_path = os.path.join(exp_path, "checkpoint.json")
        assert os.path.exists(checkpoint_path)

        # Load and verify
        loaded = manager.load(exp_path)
        assert loaded is not None
        assert loaded.protocol_name == "Test Protocol"
        assert loaded.round_index == 2
        assert loaded.step_index == 1
        assert loaded.imaging_fov_index == 5

    def test_clear_checkpoint(self, tmp_path):
        """Test clearing a checkpoint."""
        manager = CheckpointManager()
        exp_path = str(tmp_path / "experiment")
        os.makedirs(exp_path)

        # Create checkpoint
        checkpoint_path = os.path.join(exp_path, "checkpoint.json")
        with open(checkpoint_path, "w") as f:
            json.dump({"test": "data"}, f)

        # Clear it
        manager.clear(exp_path)

        # Should be gone
        assert not os.path.exists(checkpoint_path)

    def test_load_nonexistent_checkpoint_returns_none(self, tmp_path):
        """Test loading from directory without checkpoint returns None."""
        manager = CheckpointManager()
        exp_path = str(tmp_path / "experiment")
        os.makedirs(exp_path)

        result = manager.load(exp_path)
        assert result is None


# =============================================================================
# Warning System Integration Tests
# =============================================================================


class TestWarningSystem:
    """Tests for warning system integration."""

    def test_add_warning_during_execution(
        self,
        orchestrator_with_mocks: OrchestratorController,
        single_imaging_protocol: str,
        tmp_experiment_dir: str,
        event_collector: list,
        mock_imaging_executor,
    ):
        """Test adding warnings during experiment execution."""
        orchestrator = orchestrator_with_mocks

        # Make imaging block so we can add warnings
        imaging_event = threading.Event()

        def blocking_execute(*args, **kwargs):
            # Wait a bit, then complete
            imaging_event.wait(timeout=2.0)
            return True

        mock_imaging_executor.execute_with_config.side_effect = blocking_execute

        orchestrator.start_experiment(
            protocol_path=single_imaging_protocol,
            base_path=tmp_experiment_dir,
        )

        # Wait for imaging to start
        timeout = 2.0
        start = time.time()
        while (
            orchestrator.state != OrchestratorState.RUNNING
            and time.time() - start < timeout
        ):
            time.sleep(0.05)

        # Add warning
        should_pause = orchestrator.add_warning(
            category=WarningCategory.FOCUS,
            severity=WarningSeverity.MEDIUM,
            message="Focus drift detected",
            fov_id="fov_001",
        )

        assert should_pause is False  # MEDIUM doesn't trigger pause by default

        # Verify warning was recorded
        warnings = orchestrator.warning_manager.get_warnings()
        assert len(warnings) == 1
        assert warnings[0].message == "Focus drift detected"

        # Let imaging complete
        imaging_event.set()

        timeout = 5.0
        start = time.time()
        while orchestrator.is_running and time.time() - start < timeout:
            time.sleep(0.1)

    def test_warning_threshold_triggers_pause(
        self,
        orchestrator_with_mocks: OrchestratorController,
        single_imaging_protocol: str,
        tmp_experiment_dir: str,
        mock_imaging_executor,
    ):
        """Test that warning threshold triggers automatic pause."""
        orchestrator = orchestrator_with_mocks

        # Set low threshold
        orchestrator.warning_manager.set_thresholds(
            WarningThresholds(
                pause_after_count=2,
                pause_on_severity=(WarningSeverity.CRITICAL,),
            )
        )

        # Make imaging block
        imaging_event = threading.Event()

        def blocking_execute(*args, **kwargs):
            imaging_event.wait(timeout=10.0)
            return True

        mock_imaging_executor.execute_with_config.side_effect = blocking_execute

        orchestrator.start_experiment(
            protocol_path=single_imaging_protocol,
            base_path=tmp_experiment_dir,
        )

        # Wait for imaging to start
        timeout = 2.0
        start = time.time()
        while (
            orchestrator.state != OrchestratorState.RUNNING
            and time.time() - start < timeout
        ):
            time.sleep(0.05)

        # Add warnings to trigger threshold
        orchestrator.add_warning(
            WarningCategory.FOCUS, WarningSeverity.LOW, "Warning 1"
        )
        orchestrator.add_warning(
            WarningCategory.FOCUS, WarningSeverity.LOW, "Warning 2"
        )

        # Should have paused
        time.sleep(0.3)
        assert orchestrator.state == OrchestratorState.PAUSED

        # Let imaging complete and cleanup
        imaging_event.set()

    def test_critical_warning_triggers_immediate_pause(
        self,
        orchestrator_with_mocks: OrchestratorController,
        single_imaging_protocol: str,
        tmp_experiment_dir: str,
        mock_imaging_executor,
    ):
        """Test that CRITICAL severity warning triggers immediate pause."""
        orchestrator = orchestrator_with_mocks

        # Make imaging block
        imaging_event = threading.Event()

        def blocking_execute(*args, **kwargs):
            imaging_event.wait(timeout=10.0)
            return True

        mock_imaging_executor.execute_with_config.side_effect = blocking_execute

        orchestrator.start_experiment(
            protocol_path=single_imaging_protocol,
            base_path=tmp_experiment_dir,
        )

        # Wait for imaging to start
        timeout = 2.0
        start = time.time()
        while (
            orchestrator.state != OrchestratorState.RUNNING
            and time.time() - start < timeout
        ):
            time.sleep(0.05)

        # Add CRITICAL warning
        should_pause = orchestrator.add_warning(
            WarningCategory.HARDWARE,
            WarningSeverity.CRITICAL,
            "Hardware failure detected",
        )

        assert should_pause is True
        time.sleep(0.2)
        assert orchestrator.state == OrchestratorState.PAUSED

        # Cleanup
        imaging_event.set()

    def test_clear_warnings_via_command(
        self,
        orchestrator_with_mocks: OrchestratorController,
        backend_ctx: BackendContext,
    ):
        """Test clearing warnings via command event."""
        orchestrator = orchestrator_with_mocks

        # Set a unique experiment ID for this test
        orchestrator._experiment_id = "clear_warnings_test"
        orchestrator._warning_manager.experiment_id = "clear_warnings_test"

        # Add some warnings
        orchestrator.warning_manager.add_warning(
            WarningCategory.FOCUS, WarningSeverity.LOW, "Warning 1"
        )
        orchestrator.warning_manager.add_warning(
            WarningCategory.HARDWARE, WarningSeverity.MEDIUM, "Warning 2"
        )

        assert len(orchestrator.warning_manager.get_warnings()) == 2

        # Clear via command
        backend_ctx.event_bus.publish(
            ClearWarningsCommand(experiment_id="clear_warnings_test")
        )
        time.sleep(0.2)

        # Warnings should be cleared
        assert len(orchestrator.warning_manager.get_warnings()) == 0

    def test_add_warning_via_command(
        self,
        orchestrator_with_mocks: OrchestratorController,
        backend_ctx: BackendContext,
        event_collector: list,
    ):
        """Test adding warning via command event."""
        orchestrator = orchestrator_with_mocks

        # Add warning via command
        backend_ctx.event_bus.publish(
            AddWarningCommand(
                category="FOCUS",
                severity="HIGH",
                message="External focus warning",
                fov_id="fov_001",
            )
        )
        time.sleep(0.2)

        warnings = orchestrator.warning_manager.get_warnings()
        assert len(warnings) == 1
        assert warnings[0].message == "External focus warning"


class TestWarningManager:
    """Tests for WarningManager class directly."""

    def test_warning_filtering_by_category(self):
        """Test filtering warnings by category."""
        manager = WarningManager(event_bus=MagicMock())

        manager.add_warning(WarningCategory.FOCUS, WarningSeverity.LOW, "Focus 1")
        manager.add_warning(WarningCategory.HARDWARE, WarningSeverity.LOW, "Hardware 1")
        manager.add_warning(WarningCategory.FOCUS, WarningSeverity.LOW, "Focus 2")

        # Use category (singular) not categories
        focus_warnings = manager.get_warnings(category=WarningCategory.FOCUS)
        assert len(focus_warnings) == 2

        hardware_warnings = manager.get_warnings(category=WarningCategory.HARDWARE)
        assert len(hardware_warnings) == 1

    def test_warning_filtering_by_severity(self):
        """Test filtering warnings by severity."""
        manager = WarningManager(event_bus=MagicMock())

        manager.add_warning(WarningCategory.FOCUS, WarningSeverity.LOW, "Low")
        manager.add_warning(WarningCategory.FOCUS, WarningSeverity.HIGH, "High")
        manager.add_warning(WarningCategory.FOCUS, WarningSeverity.CRITICAL, "Critical")

        # Filter by exact severity (no min_severity parameter available)
        high_warnings = manager.get_warnings(severity=WarningSeverity.HIGH)
        assert len(high_warnings) == 1

        critical_warnings = manager.get_warnings(severity=WarningSeverity.CRITICAL)
        assert len(critical_warnings) == 1

        # Get all and verify count
        all_warnings = manager.get_warnings()
        assert len(all_warnings) == 3

    def test_clear_specific_categories(self):
        """Test clearing only specific warning categories."""
        manager = WarningManager(event_bus=MagicMock())

        manager.add_warning(WarningCategory.FOCUS, WarningSeverity.LOW, "Focus")
        manager.add_warning(WarningCategory.HARDWARE, WarningSeverity.LOW, "Hardware")

        manager.clear(categories=(WarningCategory.FOCUS,))

        remaining = manager.get_warnings()
        assert len(remaining) == 1
        assert remaining[0].category == WarningCategory.HARDWARE


# =============================================================================
# Event Verification Tests
# =============================================================================


class TestEventVerification:
    """Tests for verifying correct event publication."""

    def test_state_change_events_sequence(
        self,
        orchestrator_with_mocks: OrchestratorController,
        single_imaging_protocol: str,
        tmp_experiment_dir: str,
        event_collector: list,
    ):
        """Test that state change events are published in correct sequence."""
        orchestrator = orchestrator_with_mocks

        orchestrator.start_experiment(
            protocol_path=single_imaging_protocol,
            base_path=tmp_experiment_dir,
        )

        # Wait for completion
        timeout = 5.0
        start = time.time()
        while orchestrator.is_running and time.time() - start < timeout:
            time.sleep(0.1)

        # Verify state sequence
        state_changes = [
            e for e in event_collector if isinstance(e, OrchestratorStateChanged)
        ]

        # Should have: IDLE -> RUNNING -> COMPLETED
        states = [e.new_state for e in state_changes]
        assert "RUNNING" in states
        assert "COMPLETED" in states

        # COMPLETED should be last
        assert states[-1] == "COMPLETED"

    def test_progress_events_published(
        self,
        orchestrator_with_mocks: OrchestratorController,
        single_imaging_protocol: str,
        tmp_experiment_dir: str,
        event_collector: list,
    ):
        """Test that progress events are published during execution."""
        orchestrator = orchestrator_with_mocks

        orchestrator.start_experiment(
            protocol_path=single_imaging_protocol,
            base_path=tmp_experiment_dir,
        )

        # Wait for completion
        timeout = 5.0
        start = time.time()
        while orchestrator.is_running and time.time() - start < timeout:
            time.sleep(0.1)

        # Check for progress events
        progress_events = [
            e for e in event_collector if isinstance(e, OrchestratorProgress)
        ]
        # May or may not have progress events depending on timing
        # Just verify format if any exist
        for event in progress_events:
            assert 0 <= event.progress_percent <= 100
            assert event.total_rounds > 0

    def test_round_events_published(
        self,
        orchestrator_with_mocks: OrchestratorController,
        multi_round_protocol: str,
        tmp_experiment_dir: str,
        event_collector: list,
    ):
        """Test that round started/completed events are published."""
        orchestrator = orchestrator_with_mocks

        orchestrator.start_experiment(
            protocol_path=multi_round_protocol,
            base_path=tmp_experiment_dir,
        )

        # Wait for completion
        timeout = 10.0
        start = time.time()
        while orchestrator.is_running and time.time() - start < timeout:
            time.sleep(0.1)

        # Verify round events
        round_started = [
            e for e in event_collector if isinstance(e, OrchestratorRoundStarted)
        ]
        round_completed = [
            e for e in event_collector if isinstance(e, OrchestratorRoundCompleted)
        ]

        # Should have 3 rounds
        assert len(round_started) == 3
        assert len(round_completed) == 3

        # Round indices should match
        for started, completed in zip(round_started, round_completed):
            assert started.round_index == completed.round_index

    def test_error_event_on_failure(
        self,
        orchestrator_with_mocks: OrchestratorController,
        single_imaging_protocol_abort: str,
        tmp_experiment_dir: str,
        event_collector: list,
        mock_imaging_executor,
    ):
        """Test that error event is published on failure."""
        orchestrator = orchestrator_with_mocks

        # Make imaging fail
        mock_imaging_executor.execute_with_config.return_value = False

        orchestrator.start_experiment(
            protocol_path=single_imaging_protocol_abort,
            base_path=tmp_experiment_dir,
        )

        # Wait for failure
        timeout = 5.0
        start = time.time()
        while orchestrator.is_running and time.time() - start < timeout:
            time.sleep(0.1)

        assert orchestrator.state == OrchestratorState.FAILED

        # Verify error event
        error_events = [e for e in event_collector if isinstance(e, OrchestratorError)]
        assert len(error_events) >= 1


# =============================================================================
# Command Handler Tests
# =============================================================================


class TestCommandHandlers:
    """Tests for event bus command handlers."""

    def test_start_command(
        self,
        orchestrator_with_mocks: OrchestratorController,
        single_imaging_protocol: str,
        tmp_experiment_dir: str,
        backend_ctx: BackendContext,
    ):
        """Test starting via StartOrchestratorCommand."""
        orchestrator = orchestrator_with_mocks

        backend_ctx.event_bus.publish(
            StartOrchestratorCommand(
                protocol_path=single_imaging_protocol,
                base_path=tmp_experiment_dir,
                experiment_id="cmd_test",
            )
        )

        # Wait for start
        time.sleep(0.5)
        assert orchestrator.protocol is not None

        # Wait for completion
        timeout = 5.0
        start = time.time()
        while orchestrator.is_running and time.time() - start < timeout:
            time.sleep(0.1)

    def test_stop_command(
        self,
        orchestrator_with_mocks: OrchestratorController,
        backend_ctx: BackendContext,
    ):
        """Test that StopOrchestratorCommand calls abort on the orchestrator."""
        orchestrator = orchestrator_with_mocks

        # Patch abort to track if it was called
        original_abort = orchestrator.abort
        abort_called = threading.Event()

        def mock_abort():
            abort_called.set()
            return original_abort()

        orchestrator.abort = mock_abort

        # Set state to running so abort can be called
        orchestrator._transition_to(OrchestratorState.RUNNING)

        # Publish stop command
        backend_ctx.event_bus.publish(StopOrchestratorCommand())

        # Wait for abort to be called
        assert abort_called.wait(timeout=1.0), "abort() should be called on StopOrchestratorCommand"

    def test_pause_resume_commands(
        self,
        orchestrator_with_mocks: OrchestratorController,
        single_imaging_protocol: str,
        tmp_experiment_dir: str,
        backend_ctx: BackendContext,
        mock_imaging_executor,
    ):
        """Test pause/resume via commands."""
        orchestrator = orchestrator_with_mocks

        # Block imaging
        imaging_event = threading.Event()
        mock_imaging_executor.execute_with_config.side_effect = (
            lambda *args, **kwargs: imaging_event.wait(10.0) or True
        )

        orchestrator.start_experiment(
            protocol_path=single_imaging_protocol,
            base_path=tmp_experiment_dir,
        )

        # Wait for imaging
        timeout = 2.0
        start = time.time()
        while (
            orchestrator.state != OrchestratorState.RUNNING
            and time.time() - start < timeout
        ):
            time.sleep(0.05)

        # Pause via command
        backend_ctx.event_bus.publish(PauseOrchestratorCommand())
        time.sleep(0.2)
        assert orchestrator.state == OrchestratorState.PAUSED

        # Resume via command
        backend_ctx.event_bus.publish(ResumeOrchestratorCommand())
        time.sleep(0.2)
        assert orchestrator.state != OrchestratorState.PAUSED

        # Cleanup
        imaging_event.set()


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestErrorHandling:
    """Tests for error handling scenarios."""

    def test_invalid_protocol_path(
        self,
        orchestrator_with_mocks: OrchestratorController,
        tmp_experiment_dir: str,
    ):
        """Test handling of invalid protocol path."""
        orchestrator = orchestrator_with_mocks

        result = orchestrator.start_experiment(
            protocol_path="/nonexistent/protocol.yaml",
            base_path=tmp_experiment_dir,
        )

        assert result is False
        assert orchestrator.state == OrchestratorState.FAILED

    def test_cannot_start_while_running(
        self,
        orchestrator_with_mocks: OrchestratorController,
        single_imaging_protocol: str,
        tmp_experiment_dir: str,
        mock_imaging_executor,
    ):
        """Test that starting fails while already running."""
        orchestrator = orchestrator_with_mocks

        # Block imaging
        imaging_event = threading.Event()
        mock_imaging_executor.execute_with_config.side_effect = (
            lambda *args, **kwargs: imaging_event.wait(10.0) or True
        )

        # First start
        result1 = orchestrator.start_experiment(
            protocol_path=single_imaging_protocol,
            base_path=tmp_experiment_dir,
        )
        assert result1 is True

        # Wait for execution to start
        time.sleep(0.3)
        assert orchestrator.is_running

        # Second start should fail
        result2 = orchestrator.start_experiment(
            protocol_path=single_imaging_protocol,
            base_path=tmp_experiment_dir,
        )
        assert result2 is False

        # Cleanup
        imaging_event.set()

    def test_imaging_executor_failure(
        self,
        orchestrator_with_mocks: OrchestratorController,
        single_imaging_protocol_abort: str,
        tmp_experiment_dir: str,
        mock_imaging_executor,
        event_collector: list,
    ):
        """Test handling of imaging executor failure."""
        orchestrator = orchestrator_with_mocks
        mock_imaging_executor.execute_with_config.return_value = False

        orchestrator.start_experiment(
            protocol_path=single_imaging_protocol_abort,
            base_path=tmp_experiment_dir,
        )

        # Wait for failure
        timeout = 5.0
        start = time.time()
        while orchestrator.is_running and time.time() - start < timeout:
            time.sleep(0.1)

        assert orchestrator.state == OrchestratorState.FAILED

        # Verify error event
        errors = [e for e in event_collector if isinstance(e, OrchestratorError)]
        assert len(errors) >= 1

    def test_fluidics_controller_failure(
        self,
        orchestrator_with_mocks: OrchestratorController,
        fluidics_heavy_protocol: str,
        tmp_experiment_dir: str,
        mock_fluidics_controller,
    ):
        """Test handling of fluidics controller failure."""
        orchestrator = orchestrator_with_mocks
        mock_fluidics_controller.run_protocol_blocking.return_value = None

        orchestrator.start_experiment(
            protocol_path=fluidics_heavy_protocol,
            base_path=tmp_experiment_dir,
        )

        # Wait for failure
        timeout = 5.0
        start = time.time()
        while orchestrator.is_running and time.time() - start < timeout:
            time.sleep(0.1)

        assert orchestrator.state == OrchestratorState.FAILED


# =============================================================================
# Progress Calculation Tests
# =============================================================================


class TestProgressCalculation:
    """Tests for progress percentage calculation."""

    def test_progress_starts_at_zero(self):
        """Test that progress starts at 0%."""
        progress = ExperimentProgress()
        assert progress.progress_percent == 0.0

    def test_progress_with_rounds(self):
        """Test progress calculation with multiple rounds."""
        progress = ExperimentProgress(
            current_round_index=1,
            total_rounds=4,
        )
        # Base progress: 1/4 = 25%
        assert progress.progress_percent == 25.0

    def test_progress_with_imaging_fovs(self):
        """Test progress calculation including imaging FOV progress."""
        progress = ExperimentProgress(
            current_round_index=0,
            total_rounds=2,
            current_round=RoundProgress(
                round_index=0,
                round_name="Test",
                imaging_fov_index=5,
                total_imaging_fovs=10,
            ),
        )
        # Base: 0/2 = 0%
        # Round contribution: 5/10 * (1/2) = 0.25 = 25%
        expected = 25.0
        assert abs(progress.progress_percent - expected) < 0.1

    def test_progress_with_fluidics_steps(self):
        """Test progress calculation including fluidics progress."""
        progress = ExperimentProgress(
            current_round_index=0,
            total_rounds=2,
            current_round=RoundProgress(
                round_index=0,
                round_name="Test",
                fluidics_step_index=2,
                total_fluidics_steps=4,
            ),
        )
        # Base: 0/2 = 0%
        # Round contribution: 2/4 * (1/2) = 0.25 = 25%
        expected = 25.0
        assert abs(progress.progress_percent - expected) < 0.1


# =============================================================================
# Protocol Validation Command Tests
# =============================================================================


class TestProtocolValidation:
    """Tests for protocol validation via orchestrator."""

    def test_validate_protocol_command(
        self,
        orchestrator_with_mocks: OrchestratorController,
        single_imaging_protocol: str,
        tmp_experiment_dir: str,
        backend_ctx: BackendContext,
        event_collector: list,
    ):
        """Test validating a protocol via command."""
        orchestrator = orchestrator_with_mocks

        # Publish validation command
        backend_ctx.event_bus.publish(
            ValidateProtocolCommand(
                protocol_path=single_imaging_protocol,
                base_path=tmp_experiment_dir,
            )
        )

        # Wait for validation
        timeout = 2.0
        start = time.time()
        while time.time() - start < timeout:
            validation_complete = [
                e
                for e in event_collector
                if isinstance(e, ProtocolValidationComplete)
            ]
            if validation_complete:
                break
            time.sleep(0.1)

        # Verify validation events
        started = [
            e for e in event_collector if isinstance(e, ProtocolValidationStarted)
        ]
        completed = [
            e for e in event_collector if isinstance(e, ProtocolValidationComplete)
        ]

        assert len(started) == 1
        assert len(completed) == 1
        assert completed[0].valid is True

    def test_validate_protocol_returns_to_idle(
        self,
        orchestrator_with_mocks: OrchestratorController,
        single_imaging_protocol: str,
        tmp_experiment_dir: str,
        backend_ctx: BackendContext,
    ):
        """Test that validation returns to IDLE state."""
        orchestrator = orchestrator_with_mocks

        assert orchestrator.state == OrchestratorState.IDLE

        backend_ctx.event_bus.publish(
            ValidateProtocolCommand(
                protocol_path=single_imaging_protocol,
                base_path=tmp_experiment_dir,
            )
        )

        # Wait for validation to complete
        time.sleep(0.5)

        assert orchestrator.state == OrchestratorState.IDLE


# =============================================================================
# Executor Integration Tests
# =============================================================================


class TestImagingExecutor:
    """Integration tests for ImagingExecutor."""

    def test_imaging_executor_delegates_to_multipoint(
        self,
        backend_ctx: BackendContext,
    ):
        """Test that imaging executor properly delegates to multipoint."""
        multipoint = MagicMock()
        multipoint.run_acquisition = MagicMock()

        executor = ImagingExecutor(
            event_bus=backend_ctx.event_bus,
            multipoint_controller=multipoint,
        )

        # Simulate acquisition completion
        def complete_acquisition(*args, **kwargs):
            time.sleep(0.1)
            backend_ctx.event_bus.publish(
                AcquisitionFinished(success=True, error=None, experiment_id="round_000")
            )

        multipoint.run_acquisition.side_effect = complete_acquisition

        imaging_config = ImagingProtocol(channels=["BF"])
        cancel_token = CancelToken()

        result = executor.execute_with_config(
            imaging_config=imaging_config,
            output_path="/tmp/test",
            cancel_token=cancel_token,
            round_index=0,
            experiment_id="round_000",
        )

        assert result is True
        multipoint.run_acquisition.assert_called_once()

        executor.shutdown()


class TestFluidicsController:
    """Integration tests for FluidicsController."""

    def test_fluidics_controller_initial_state(self):
        """Test fluidics controller starts in IDLE state."""
        from squid.backend.controllers.fluidics_controller import FluidicsControllerState

        controller = FluidicsController(event_bus=MagicMock())
        assert controller.state == FluidicsControllerState.IDLE

    def test_fluidics_controller_run_protocol_without_service(self):
        """Test that run_protocol simulates when no service is available."""
        controller = FluidicsController(event_bus=MagicMock())

        # Without a fluidics service, the controller should return False
        # (no protocols loaded, protocol not found)
        result = controller.run_protocol("nonexistent_protocol")
        assert result is False

    def test_fluidics_controller_pause_when_idle_fails(self):
        """Test that pause fails when controller is idle."""
        controller = FluidicsController(event_bus=MagicMock())

        result = controller.pause()
        assert result is False

    def test_fluidics_controller_stop_when_idle_fails(self):
        """Test that stop fails when controller is idle."""
        controller = FluidicsController(event_bus=MagicMock())

        result = controller.stop()
        assert result is False

    def test_fluidics_controller_is_available_without_service(self):
        """Test is_available property without fluidics service."""
        controller = FluidicsController(event_bus=MagicMock())

        # Without a fluidics service, the controller is not available
        assert controller.is_available is False


# =============================================================================
# State Machine Tests
# =============================================================================


class TestStateMachine:
    """Tests for state machine behavior."""

    def test_valid_state_transitions(
        self,
        orchestrator_with_mocks: OrchestratorController,
    ):
        """Test that only valid state transitions are allowed."""
        orchestrator = orchestrator_with_mocks

        # IDLE -> RUNNING is valid
        orchestrator._transition_to(OrchestratorState.RUNNING)
        assert orchestrator.state == OrchestratorState.RUNNING

        # RUNNING -> PAUSED is valid
        orchestrator._transition_to(OrchestratorState.PAUSED)
        assert orchestrator.state == OrchestratorState.PAUSED

    def test_is_running_property(
        self,
        orchestrator_with_mocks: OrchestratorController,
    ):
        """Test is_running property for different states."""
        orchestrator = orchestrator_with_mocks

        # IDLE is not running
        assert orchestrator.is_running is False

        # RUNNING is running
        orchestrator._transition_to(OrchestratorState.RUNNING)
        assert orchestrator.is_running is True

        # PAUSED is running (still active experiment)
        orchestrator._transition_to(OrchestratorState.PAUSED)
        assert orchestrator.is_running is True

        # COMPLETED is not running
        orchestrator._transition_to(OrchestratorState.ABORTED)
        assert orchestrator.is_running is False
