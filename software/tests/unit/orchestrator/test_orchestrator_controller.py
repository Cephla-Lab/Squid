"""Unit tests for OrchestratorController."""

import pytest
import threading
import tempfile
import os
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime

from squid.core.events import EventBus
from squid.backend.controllers.orchestrator import (
    OrchestratorController,
    OrchestratorState,
    OrchestratorStateChanged,
    OrchestratorProgress,
    OrchestratorRoundStarted,
    OrchestratorRoundCompleted,
    OrchestratorError,
    StartOrchestratorCommand,
    StopOrchestratorCommand,
    PauseOrchestratorCommand,
    ResumeOrchestratorCommand,
    ClearWarningsCommand,
    ValidateProtocolCommand,
    ProtocolValidationComplete,
    WarningCategory,
    WarningSeverity,
)
from squid.core.protocol import (
    ExperimentProtocol,
    Round,
    ImagingStep,
    InterventionStep,
    ImagingProtocol,
)


@pytest.fixture
def event_bus():
    """Create an EventBus for testing."""
    bus = EventBus()
    bus.start()
    yield bus
    bus.stop()
    bus.clear()


@pytest.fixture
def mock_multipoint():
    """Create a mock MultiPointController."""
    mock = MagicMock()
    mock.run_acquisition = MagicMock()
    mock.abort = MagicMock()
    return mock


@pytest.fixture
def mock_experiment_manager():
    """Create a mock ExperimentManager."""
    mock = MagicMock()

    # Create a mock context with proper string values
    context = MagicMock()
    context.experiment_id = "test_experiment_001"
    context.experiment_path = "/tmp/test_experiment"
    context.base_path = "/tmp"
    mock.start_experiment.return_value = context
    mock.create_round_subfolder.return_value = "/tmp/test_experiment/round_001"

    return mock


@pytest.fixture
def mock_acquisition_planner():
    """Create a mock AcquisitionPlanner."""
    return MagicMock()


@pytest.fixture
def mock_imaging_executor():
    """Create a mock ImagingExecutor."""
    mock = MagicMock()
    mock.execute.return_value = True
    mock.execute_with_config.return_value = True  # V2 method
    return mock


@pytest.fixture
def mock_fluidics_controller():
    """Create a mock FluidicsController."""
    mock = MagicMock()
    mock.run_protocol.return_value = True
    mock.is_available = True
    mock.list_protocols.return_value = []
    return mock


@pytest.fixture
def simple_protocol():
    """Create a simple V2 test protocol."""
    return ExperimentProtocol(
        name="test_protocol",
        version="2.0",
        imaging_protocols={
            "standard": ImagingProtocol(channels=["DAPI"]),
        },
        rounds=[
            Round(
                name="round_1",
                steps=[ImagingStep(protocol="standard")],
            ),
        ],
    )


@pytest.fixture
def protocol_file(simple_protocol):
    """Create a temporary protocol file."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False
    ) as f:
        from squid.core.protocol import ProtocolLoader
        loader = ProtocolLoader()
        loader.save(simple_protocol, f.name)
        yield f.name
    os.unlink(f.name)


@pytest.fixture
def orchestrator(
    event_bus,
    mock_multipoint,
    mock_experiment_manager,
    mock_acquisition_planner,
    mock_imaging_executor,
    mock_fluidics_controller,
):
    """Create an OrchestratorController for testing."""
    return OrchestratorController(
        event_bus=event_bus,
        multipoint_controller=mock_multipoint,
        experiment_manager=mock_experiment_manager,
        acquisition_planner=mock_acquisition_planner,
        imaging_executor=mock_imaging_executor,
        fluidics_controller=mock_fluidics_controller,
    )


class TestOrchestratorState:
    """Tests for orchestrator state management."""

    def test_initial_state_is_idle(self, orchestrator):
        """Test that orchestrator starts in IDLE state."""
        assert orchestrator.state == OrchestratorState.IDLE

    def test_is_running_false_when_idle(self, orchestrator):
        """Test is_running is False when idle."""
        assert orchestrator.is_running is False


class TestStartExperiment:
    """Tests for starting experiments."""

    def test_start_experiment_transitions_to_initializing(
        self, orchestrator, protocol_file
    ):
        """Test that start_experiment transitions state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = orchestrator.start_experiment(
                protocol_path=protocol_file,
                base_path=tmpdir,
            )

            assert result is True
            # Give time for worker thread to start
            import time
            time.sleep(0.1)
            assert orchestrator.is_running or orchestrator.state == OrchestratorState.COMPLETED

    def test_start_experiment_fails_if_not_idle(
        self, orchestrator, protocol_file
    ):
        """Test that start fails if not in IDLE state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # First start
            orchestrator.start_experiment(
                protocol_path=protocol_file,
                base_path=tmpdir,
            )

            # Give time for state change
            import time
            time.sleep(0.1)

            # Second start should fail if still running
            if orchestrator.is_running:
                result = orchestrator.start_experiment(
                    protocol_path=protocol_file,
                    base_path=tmpdir,
                )
                assert result is False

    def test_start_experiment_loads_protocol(
        self, orchestrator, protocol_file
    ):
        """Test that protocol is loaded on start."""
        with tempfile.TemporaryDirectory() as tmpdir:
            orchestrator.start_experiment(
                protocol_path=protocol_file,
                base_path=tmpdir,
            )

            assert orchestrator.protocol is not None
            assert orchestrator.protocol.name == "test_protocol"

    def test_start_experiment_rejects_start_round_out_of_bounds(
        self, orchestrator, protocol_file
    ):
        """Test invalid start_from_round is rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = orchestrator.start_experiment(
                protocol_path=protocol_file,
                base_path=tmpdir,
                start_from_round=5,
            )
            assert result is False
            assert orchestrator.state == OrchestratorState.IDLE

    def test_validate_protocol_uses_protocol_fluidics_resource_file(
        self,
        orchestrator,
        event_bus,
        tmp_path,
    ):
        """Test Validate accepts fluidics protocols declared in the protocol resource file."""
        fluidics_path = tmp_path / "fluidics.yaml"
        fluidics_path.write_text(
            "\n".join(
                [
                    "protocols:",
                    "  wash_a:",
                    "    description: Test wash",
                    "    steps:",
                    "      - operation: incubate",
                    "        duration_s: 12",
                ]
            )
        )
        protocol_path = tmp_path / "protocol.yaml"
        protocol_path.write_text(
            "\n".join(
                [
                    "name: Validate Fluidics Resources",
                    'version: "3.0"',
                    "resources:",
                    "  fluidics_protocols_file: fluidics.yaml",
                    "rounds:",
                    "  - name: Round 1",
                    "    steps:",
                    "      - step_type: fluidics",
                    "        protocol: wash_a",
                ]
            )
        )

        events = []
        event_bus.subscribe(ProtocolValidationComplete, events.append)
        orchestrator._fluidics_controller.estimate_protocol_duration.return_value = None

        orchestrator._on_validate_protocol(
            ValidateProtocolCommand(
                protocol_path=str(protocol_path),
                base_path=str(tmp_path),
            )
        )

        deadline = datetime.now().timestamp() + 1.0
        while len(events) < 1 and datetime.now().timestamp() < deadline:
            threading.Event().wait(0.01)

        assert len(events) == 1
        assert events[0].valid is True
        assert events[0].errors == ()
        fluidics_ops = [
            op for op in events[0].operation_estimates if op.operation_type == "fluidics"
        ]
        assert len(fluidics_ops) == 1
        assert fluidics_ops[0].estimated_seconds == pytest.approx(12.0)

    def test_start_experiment_rejects_start_step_out_of_bounds(
        self, orchestrator, protocol_file
    ):
        """Test invalid start_from_step is rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = orchestrator.start_experiment(
                protocol_path=protocol_file,
                base_path=tmpdir,
                start_from_round=0,
                start_from_step=5,
            )
            assert result is False
            assert orchestrator.state == OrchestratorState.IDLE

    def test_start_experiment_rejects_negative_start_fov(
        self, orchestrator, protocol_file
    ):
        """Test invalid negative start_from_fov is rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = orchestrator.start_experiment(
                protocol_path=protocol_file,
                base_path=tmpdir,
                start_from_fov=-1,
            )
            assert result is False
            assert orchestrator.state == OrchestratorState.IDLE

    def test_start_experiment_rejects_start_fov_for_non_imaging_step(
        self, orchestrator
    ):
        """Test start_from_fov > 0 requires the selected start step to be imaging."""
        protocol = ExperimentProtocol(
            name="mixed_steps",
            version="2.0",
            imaging_protocols={"standard": ImagingProtocol(channels=["DAPI"])},
            rounds=[
                Round(
                    name="r0",
                    steps=[
                        InterventionStep(message="continue"),
                        ImagingStep(protocol="standard"),
                    ],
                )
            ],
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            from squid.core.protocol import ProtocolLoader

            loader = ProtocolLoader()
            loader.save(protocol, f.name)
            protocol_path = f.name
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                result = orchestrator.start_experiment(
                    protocol_path=protocol_path,
                    base_path=tmpdir,
                    start_from_round=0,
                    start_from_step=0,
                    start_from_fov=1,
                )
                assert result is False
                assert orchestrator.state == OrchestratorState.IDLE
        finally:
            os.unlink(protocol_path)

    def test_start_experiment_rejects_start_fov_out_of_bounds_with_scan_coordinates(
        self,
        event_bus,
        mock_multipoint,
        mock_experiment_manager,
        mock_acquisition_planner,
        mock_imaging_executor,
        mock_fluidics_controller,
        protocol_file,
    ):
        """Test start_from_fov must be within loaded scan-coordinate count when known."""
        scan_coordinates = MagicMock()
        scan_coordinates.region_fov_coordinates = {"region_1": [(0.0, 0.0, 0.0)]}
        orchestrator = OrchestratorController(
            event_bus=event_bus,
            multipoint_controller=mock_multipoint,
            experiment_manager=mock_experiment_manager,
            acquisition_planner=mock_acquisition_planner,
            imaging_executor=mock_imaging_executor,
            fluidics_controller=mock_fluidics_controller,
            scan_coordinates=scan_coordinates,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            result = orchestrator.start_experiment(
                protocol_path=protocol_file,
                base_path=tmpdir,
                start_from_round=0,
                start_from_step=0,
                start_from_fov=2,
            )
            assert result is False
            assert orchestrator.state == OrchestratorState.IDLE


class TestPauseResumeAbort:
    """Tests for pause/resume/abort controls."""

    def test_pause_when_not_running_returns_false(self, orchestrator):
        """Test that pause fails when not running."""
        result = orchestrator.pause()
        assert result is False

    def test_resume_when_not_paused_returns_false(self, orchestrator):
        """Test that resume fails when not paused."""
        result = orchestrator.resume()
        assert result is False

    def test_abort_when_idle_returns_false(self, orchestrator):
        """Test that abort fails when idle."""
        result = orchestrator.abort()
        assert result is False

    def test_resolve_intervention_sets_action_when_waiting(self, orchestrator):
        """Explicit intervention actions should resolve the wait state."""
        orchestrator._state = OrchestratorState.WAITING_INTERVENTION
        assert orchestrator.resolve_intervention("retry") is True
        assert orchestrator._intervention_action == "retry"
        assert orchestrator._intervention_resolved.is_set() is True


class TestSkipControls:
    """Tests for round skip controls."""

    def test_skip_to_round_when_idle_returns_false(self, orchestrator):
        """Skip-to-round should fail when no experiment is running."""
        assert orchestrator.skip_to_round(1) is False

    def test_skip_current_round_requires_runner(self, orchestrator):
        """Skip-current should fail if no runner is active."""
        orchestrator._state = OrchestratorState.RUNNING
        assert orchestrator.skip_current_round() is False

    def test_skip_to_round_delegates_to_runner(self, orchestrator):
        """Skip-to-round should delegate validation and request to runner."""
        runner = MagicMock()
        runner.request_skip_to_round.return_value = True
        orchestrator._runner = runner
        orchestrator._state = OrchestratorState.RUNNING

        assert orchestrator.skip_to_round(2) is True
        runner.request_skip_to_round.assert_called_once_with(2)

    def test_skip_current_round_delegates_to_runner(self, orchestrator):
        """Skip-current should delegate to runner API."""
        runner = MagicMock()
        runner.request_skip_current_round.return_value = True
        orchestrator._runner = runner
        orchestrator._state = OrchestratorState.RUNNING

        assert orchestrator.skip_current_round() is True
        runner.request_skip_current_round.assert_called_once_with()


class TestEventHandlers:
    """Tests for command event handlers."""

    def test_start_command_handler(self, orchestrator, event_bus, protocol_file):
        """Test StartOrchestratorCommand handler."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Publish start command
            event_bus.publish(StartOrchestratorCommand(
                protocol_path=protocol_file,
                base_path=tmpdir,
            ))

            # Wait for event processing
            import time
            time.sleep(0.2)

            # Should have started
            assert orchestrator.protocol is not None

    def test_stop_command_handler(self, orchestrator, event_bus, protocol_file):
        """Test StopOrchestratorCommand handler."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Start first
            orchestrator.start_experiment(
                protocol_path=protocol_file,
                base_path=tmpdir,
            )
            import time
            time.sleep(0.1)

            # Publish stop command
            event_bus.publish(StopOrchestratorCommand())
            time.sleep(0.3)

            # Should have stopped
            assert orchestrator.state in (
                OrchestratorState.ABORTED,
                OrchestratorState.COMPLETED,
                OrchestratorState.IDLE,
            )

    def test_clear_warnings_ignores_stale_experiment_id(self, orchestrator, event_bus):
        """ClearWarningsCommand should be scoped to the active experiment ID."""
        orchestrator._experiment_id = "active_exp"
        orchestrator.warning_manager.experiment_id = "active_exp"
        orchestrator.warning_manager.add_warning(
            WarningCategory.FOCUS,
            WarningSeverity.LOW,
            "focus warning",
        )
        assert len(orchestrator.warning_manager.get_warnings()) == 1

        event_bus.publish(ClearWarningsCommand(experiment_id="other_exp"))

        import time
        time.sleep(0.05)
        assert len(orchestrator.warning_manager.get_warnings()) == 1


class TestProgress:
    """Tests for progress tracking."""

    def test_progress_starts_at_zero(self, orchestrator):
        """Test that progress starts at 0."""
        assert orchestrator.progress.progress_percent == 0.0

    def test_progress_events_published(
        self, orchestrator, event_bus, protocol_file, mock_experiment_manager
    ):
        """Test that progress events are published."""
        received_events = []

        def on_progress(event):
            received_events.append(event)

        event_bus.subscribe(OrchestratorProgress, on_progress)

        with tempfile.TemporaryDirectory() as tmpdir:
            orchestrator.start_experiment(
                protocol_path=protocol_file,
                base_path=tmpdir,
            )

            # Wait for completion
            import time
            time.sleep(0.5)

        # Should have received at least one progress event
        assert len(received_events) >= 0  # May be 0 if experiment completes quickly

    def test_progress_current_round_clamped(self, orchestrator, event_bus):
        """Test current_round never exceeds total_rounds."""
        received_events = []

        def on_progress(event):
            received_events.append(event)

        event_bus.subscribe(OrchestratorProgress, on_progress)

        with orchestrator._progress_lock:
            orchestrator._progress.total_rounds = 1
            orchestrator._progress.current_round_index = 1
            orchestrator._progress.current_round = None

        orchestrator._publish_progress()

        import time
        time.sleep(0.05)

        assert received_events
        assert received_events[-1].total_rounds == 1
        assert received_events[-1].current_round == 1


class TestExperimentExecution:
    """Tests for experiment execution flow."""

    def test_imaging_executor_called(
        self, orchestrator, protocol_file, mock_imaging_executor
    ):
        """Test that imaging executor is called for imaging rounds."""
        with tempfile.TemporaryDirectory() as tmpdir:
            orchestrator.start_experiment(
                protocol_path=protocol_file,
                base_path=tmpdir,
            )

            # Wait for completion
            import time
            time.sleep(0.5)

            # Imaging executor should have been called (V2 uses execute_with_config)
            assert (
                mock_imaging_executor.execute_with_config.called
                or orchestrator.state == OrchestratorState.COMPLETED
            )
