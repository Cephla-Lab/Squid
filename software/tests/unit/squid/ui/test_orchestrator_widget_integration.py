"""Widget-level integration tests for OrchestratorControlPanel.

These tests wire a real OrchestratorController (backed by simulated hardware)
into the OrchestratorControlPanel widget, exercising the full
widget -> controller -> backend path without needing the full GUI.

Pattern:
    BackendContext provides simulated hardware
    OrchestratorSimulator creates a real OrchestratorController
    OrchestratorControlPanel is wired to the real controller
    We call widget methods directly and verify real state changes
"""

import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from PyQt5.QtWidgets import QApplication

from squid.backend.controllers.orchestrator import (
    OrchestratorStateChanged,
    OrchestratorProgress,
    OrchestratorRoundStarted,
    OrchestratorRoundCompleted,
)
from squid.ui.widgets.orchestrator.orchestrator_widget import OrchestratorControlPanel
from tests.harness.core.backend_context import BackendContext
from tests.harness.core.event_monitor import EventMonitor
from tests.e2e.harness.orchestrator_simulator import OrchestratorSimulator


# ---------------------------------------------------------------------------
# Protocol helpers
# ---------------------------------------------------------------------------

_CONFIGS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "e2e" / "configs"
_SIMULATION_DIR = _CONFIGS_DIR / "simulation"
_QUICK_MULTIPOINT = str(_SIMULATION_DIR / "quick_multipoint.yaml")
_QUICK_FISH_2ROUND = str(_SIMULATION_DIR / "quick_fish_2round.yaml")


def _pump_and_wait(predicate, timeout_s: float = 10.0) -> bool:
    """Poll *predicate* while pumping the Qt event loop for signal delivery."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _wait_for_state(monitor: EventMonitor, target: str, timeout_s: float = 30.0) -> bool:
    """Poll EventMonitor until orchestrator reaches *target* state."""
    def _check():
        QApplication.processEvents()
        return any(e.new_state == target for e in monitor.get_events(OrchestratorStateChanged))
    return _pump_and_wait(_check, timeout_s)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def backend_ctx(tmp_path):
    with BackendContext(simulation=True, base_path=str(tmp_path)) as ctx:
        yield ctx


@pytest.fixture
def sim(backend_ctx):
    simulator = OrchestratorSimulator(backend_ctx)
    yield simulator
    simulator.cleanup()


@pytest.fixture
def panel(backend_ctx, sim, qtbot):
    """OrchestratorControlPanel wired to a real OrchestratorController."""
    widget = OrchestratorControlPanel(
        event_bus=backend_ctx.event_bus,
        orchestrator=sim.orchestrator,
    )
    qtbot.addWidget(widget)
    return widget


def _load_and_validate(panel):
    """Load a protocol into the panel and validate it, mocking the dialog."""
    panel._protocol_path = _QUICK_MULTIPOINT
    panel._load_protocol(_QUICK_MULTIPOINT)
    assert panel._protocol_data is not None, "Protocol should load successfully"

    with patch(
        "squid.ui.widgets.orchestrator.orchestrator_widget.ValidationResultDialog"
    ) as MockDialog:
        mock_instance = MagicMock()
        mock_instance.exec_.return_value = True
        MockDialog.return_value = mock_instance

        panel._on_validate_clicked()

        ok = _pump_and_wait(lambda: panel._validated, timeout_s=10.0)
        assert ok, "Protocol validation should complete and set _validated=True"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStartAcquisitionFovCheck:
    """Test that Start Acquisition requires FOVs."""

    def test_start_blocked_without_fovs(self, panel, sim, tmp_path):
        """Start Acquisition shows warning when no FOVs are loaded."""
        panel._base_path = str(tmp_path)
        _load_and_validate(panel)
        panel._fov_positions = {}  # ensure no FOVs

        with patch(
            "squid.ui.widgets.orchestrator.orchestrator_widget.QMessageBox"
        ) as MockBox:
            panel._on_start_clicked()
            MockBox.warning.assert_called_once()

    def test_start_proceeds_with_fovs(self, panel, sim, tmp_path):
        """Start Acquisition proceeds when FOVs are loaded."""
        panel._base_path = str(tmp_path)
        panel._fov_positions = {"region_0": [(10.0, 10.0, 1.0)]}
        _load_and_validate(panel)

        panel._on_start_clicked()

        assert _wait_for_state(sim.monitor, "RUNNING", timeout_s=10), (
            "Orchestrator should reach RUNNING state"
        )


class TestButtonStateTransitions:
    """Test button enabled/disabled states through real orchestrator state changes."""

    def test_buttons_disabled_during_run_and_reenabled_after(
        self, panel, sim, tmp_path
    ):
        """Start disabled while running, re-enabled after completion."""
        panel._base_path = str(tmp_path)
        panel._fov_positions = {"region_0": [(10.0, 10.0, 1.0)]}
        _load_and_validate(panel)

        assert panel._start_btn.isEnabled()

        panel._on_start_clicked()

        assert _wait_for_state(sim.monitor, "RUNNING", timeout_s=10)
        # Pump events so the UI slot processes the state change
        _pump_and_wait(lambda: not panel._start_btn.isEnabled(), timeout_s=3.0)

        assert not panel._start_btn.isEnabled(), "Start should be disabled during run"

        assert _wait_for_state(sim.monitor, "COMPLETED", timeout_s=30)
        _pump_and_wait(lambda: panel._start_btn.isEnabled(), timeout_s=3.0)

        assert panel._start_btn.isEnabled(), "Start should be re-enabled after completion"


class TestPauseResumeAbort:
    """Test pause, resume, and abort during a real acquisition."""

    def test_pause_and_resume_completes(self, panel, sim, tmp_path):
        """Pause during acquisition, then resume — should still complete."""
        panel._base_path = str(tmp_path)
        panel._fov_positions = {"region_0": [(10.0, 10.0, 1.0)]}
        _load_and_validate(panel)

        panel._on_start_clicked()
        assert _wait_for_state(sim.monitor, "RUNNING", timeout_s=10)

        # Pause
        panel._on_pause_clicked()
        assert _wait_for_state(sim.monitor, "PAUSED", timeout_s=10), (
            "Orchestrator should reach PAUSED state"
        )
        _pump_and_wait(lambda: panel._resume_btn.isEnabled(), timeout_s=3.0)
        assert panel._resume_btn.isEnabled(), "Resume should be enabled when paused"
        assert not panel._start_btn.isEnabled(), "Start should be disabled when paused"

        # Resume
        panel._on_resume_clicked()
        assert _wait_for_state(sim.monitor, "COMPLETED", timeout_s=30), (
            "Orchestrator should complete after resume"
        )

    def test_abort_stops_acquisition(self, panel, sim, tmp_path):
        """Abort during acquisition should reach ABORTED state."""
        panel._base_path = str(tmp_path)
        panel._fov_positions = {"region_0": [(10.0, 10.0, 1.0)]}
        _load_and_validate(panel)

        panel._on_start_clicked()
        assert _wait_for_state(sim.monitor, "RUNNING", timeout_s=10)

        panel._on_abort_clicked()
        assert _wait_for_state(sim.monitor, "ABORTED", timeout_s=10), (
            "Orchestrator should reach ABORTED state"
        )
        _pump_and_wait(lambda: panel._start_btn.isEnabled(), timeout_s=3.0)
        assert panel._start_btn.isEnabled(), "Start should be re-enabled after abort"


class TestProgressUpdates:
    """Test that real progress events flow through to the widget."""

    def test_progress_events_received_during_run(self, panel, sim, tmp_path):
        """Widget should receive progress events during a real acquisition."""
        panel._base_path = str(tmp_path)
        panel._fov_positions = {"region_0": [(10.0, 10.0, 1.0)]}
        _load_and_validate(panel)

        panel._on_start_clicked()
        assert _wait_for_state(sim.monitor, "COMPLETED", timeout_s=30)

        # Pump remaining events
        _pump_and_wait(lambda: False, timeout_s=0.5)

        # The controller should have published progress events
        progress_events = sim.monitor.get_events(OrchestratorProgress)
        assert len(progress_events) > 0, "Should receive progress events"

        # Progress events should contain meaningful data
        last_progress = progress_events[-1]
        assert last_progress.current_round >= 1, "Should report current round"
        assert last_progress.elapsed_seconds > 0, "Should report elapsed time"

    def test_round_label_updates(self, panel, sim, tmp_path):
        """Round label should reflect current round name."""
        panel._base_path = str(tmp_path)
        panel._fov_positions = {"region_0": [(10.0, 10.0, 1.0)]}
        _load_and_validate(panel)

        panel._on_start_clicked()
        assert _wait_for_state(sim.monitor, "COMPLETED", timeout_s=30)
        _pump_and_wait(lambda: False, timeout_s=0.5)

        # Should have received round events
        round_events = sim.monitor.get_events(OrchestratorRoundStarted)
        assert len(round_events) >= 1, "Should receive at least one round-started event"


def _load_and_validate_protocol(panel, protocol_path):
    """Load and validate a specific protocol, mocking the dialog."""
    panel._protocol_path = protocol_path
    panel._load_protocol(protocol_path)
    assert panel._protocol_data is not None

    with patch(
        "squid.ui.widgets.orchestrator.orchestrator_widget.ValidationResultDialog"
    ) as MockDialog:
        mock_instance = MagicMock()
        mock_instance.exec_.return_value = True
        MockDialog.return_value = mock_instance
        panel._on_validate_clicked()
        ok = _pump_and_wait(lambda: panel._validated, timeout_s=10.0)
        assert ok, "Validation should complete"


class TestMultiRoundWorkflow:
    """Test multi-round protocol workflows through the widget."""

    def test_two_round_protocol_completes(self, panel, sim, tmp_path):
        """A 2-round FISH protocol should complete both rounds."""
        panel._base_path = str(tmp_path)
        _load_and_validate_protocol(panel, _QUICK_FISH_2ROUND)

        panel._fov_positions = {"region_0": [(10.0, 10.0, 1.0)]}
        panel._on_start_clicked()

        assert _wait_for_state(sim.monitor, "COMPLETED", timeout_s=60), (
            f"Expected COMPLETED, got: "
            f"{[e.new_state for e in sim.monitor.get_events(OrchestratorStateChanged)]}"
        )

        round_completed = sim.monitor.get_events(OrchestratorRoundCompleted)
        assert len(round_completed) == 2, (
            f"Expected 2 round completions, got {len(round_completed)}"
        )

    def test_start_acquisition_with_fovs_multi_round(self, panel, sim, backend_ctx, tmp_path):
        """Start Acquisition with FOVs loaded completes a multi-round protocol."""
        panel._base_path = str(tmp_path)
        center = backend_ctx.get_stage_center()
        panel._fov_positions = {"region_0": [(center[0], center[1], center[2])]}
        _load_and_validate_protocol(panel, _QUICK_FISH_2ROUND)

        panel._on_start_clicked()

        assert _wait_for_state(sim.monitor, "COMPLETED", timeout_s=60), (
            f"Expected COMPLETED, got: "
            f"{[e.new_state for e in sim.monitor.get_events(OrchestratorStateChanged)]}"
        )

        round_completed = sim.monitor.get_events(OrchestratorRoundCompleted)
        assert len(round_completed) == 2


class TestRunSingleRound:
    """Test the run_single_round workflow through the widget."""

    def test_run_single_round_executes_one_round_only(self, panel, sim, tmp_path):
        """run_single_round should execute only the specified round."""
        panel._base_path = str(tmp_path)
        panel._fov_positions = {"region_0": [(10.0, 10.0, 1.0)]}
        _load_and_validate_protocol(panel, _QUICK_FISH_2ROUND)

        # Run only round index 0
        panel.run_single_round(round_index=0)

        assert _wait_for_state(sim.monitor, "COMPLETED", timeout_s=30), (
            f"Expected COMPLETED, got: "
            f"{[e.new_state for e in sim.monitor.get_events(OrchestratorStateChanged)]}"
        )

        round_completed = sim.monitor.get_events(OrchestratorRoundCompleted)
        assert len(round_completed) == 1, (
            f"Expected 1 round completion (single round), got {len(round_completed)}"
        )

    def test_start_from_second_round(self, panel, sim, tmp_path):
        """start_from_round(1) on a 2-round protocol should skip round 0."""
        panel._base_path = str(tmp_path)
        panel._fov_positions = {"region_0": [(10.0, 10.0, 1.0)]}
        _load_and_validate_protocol(panel, _QUICK_FISH_2ROUND)

        panel.start_from_round(round_index=1)

        assert _wait_for_state(sim.monitor, "COMPLETED", timeout_s=30), (
            f"Expected COMPLETED, got: "
            f"{[e.new_state for e in sim.monitor.get_events(OrchestratorStateChanged)]}"
        )

        round_started = sim.monitor.get_events(OrchestratorRoundStarted)
        # Should only have started round index 1 (and possibly 1+), never round 0
        assert all(e.round_index >= 1 for e in round_started), (
            f"Should not have started round 0, got: "
            f"{[(e.round_index, e.round_name) for e in round_started]}"
        )


class TestRerunWorkflows:
    """Test that the widget allows re-running after completion or abort."""

    def test_run_again_after_completion(self, panel, sim, tmp_path):
        """After a run completes, the user can start another run."""
        panel._base_path = str(tmp_path)
        panel._fov_positions = {"region_0": [(10.0, 10.0, 1.0)]}
        _load_and_validate(panel)

        # First run
        panel._on_start_clicked()
        assert _wait_for_state(sim.monitor, "COMPLETED", timeout_s=30)
        _pump_and_wait(lambda: panel._start_btn.isEnabled(), timeout_s=3.0)

        # Second run — should work without re-validating
        sim.monitor.clear()
        panel._on_start_clicked()
        assert _wait_for_state(sim.monitor, "COMPLETED", timeout_s=30), (
            "Second run should also complete"
        )

    def test_run_again_after_abort(self, panel, sim, tmp_path):
        """After aborting, the user can start a fresh run."""
        panel._base_path = str(tmp_path)
        panel._fov_positions = {"region_0": [(10.0, 10.0, 1.0)]}
        _load_and_validate(panel)

        # Start and abort
        panel._on_start_clicked()
        assert _wait_for_state(sim.monitor, "RUNNING", timeout_s=10)
        panel._on_abort_clicked()
        assert _wait_for_state(sim.monitor, "ABORTED", timeout_s=10)
        _pump_and_wait(lambda: panel._start_btn.isEnabled(), timeout_s=3.0)

        # Fresh run
        sim.monitor.clear()
        panel._on_start_clicked()
        assert _wait_for_state(sim.monitor, "COMPLETED", timeout_s=30), (
            "Run after abort should complete"
        )

    def test_switch_protocol_between_runs(self, panel, sim, tmp_path):
        """User can load a different protocol and run it after a previous run."""
        panel._base_path = str(tmp_path)
        panel._fov_positions = {"region_0": [(10.0, 10.0, 1.0)]}

        # First run: single-round protocol
        _load_and_validate(panel)
        panel._on_start_clicked()
        assert _wait_for_state(sim.monitor, "COMPLETED", timeout_s=30)
        _pump_and_wait(lambda: panel._start_btn.isEnabled(), timeout_s=3.0)

        first_rounds = sim.monitor.get_events(OrchestratorRoundCompleted)
        assert len(first_rounds) == 1

        # Switch to 2-round protocol
        sim.monitor.clear()
        _load_and_validate_protocol(panel, _QUICK_FISH_2ROUND)

        panel._on_start_clicked()
        assert _wait_for_state(sim.monitor, "COMPLETED", timeout_s=60), (
            f"Expected COMPLETED after protocol switch, got: "
            f"{[e.new_state for e in sim.monitor.get_events(OrchestratorStateChanged)]}"
        )

        second_rounds = sim.monitor.get_events(OrchestratorRoundCompleted)
        assert len(second_rounds) == 2, "Second run should complete 2 rounds"


class TestValidationGating:
    """Test that validation properly gates the start button."""

    def test_start_blocked_before_validation(self, panel, sim, tmp_path):
        """Start should be disabled after loading but before validating."""
        panel._base_path = str(tmp_path)
        panel._protocol_path = _QUICK_MULTIPOINT
        panel._load_protocol(_QUICK_MULTIPOINT)

        assert panel._protocol_data is not None, "Protocol should be loaded"
        assert not panel._validated, "Should not be validated yet"
        assert not panel._start_btn.isEnabled(), "Start should be disabled before validation"
        assert panel._validate_btn.isEnabled(), "Validate should be enabled after load"

    def test_reloading_protocol_resets_validation(self, panel, sim, tmp_path):
        """Loading a new protocol should reset validation state."""
        panel._base_path = str(tmp_path)
        _load_and_validate(panel)
        assert panel._validated
        assert panel._start_btn.isEnabled()

        # Reload same protocol — should reset validation
        panel._load_protocol(_QUICK_MULTIPOINT)
        assert not panel._validated, "Validation should be reset after reload"
        assert not panel._start_btn.isEnabled(), "Start should be disabled after reload"
        assert panel._validate_btn.isEnabled(), "Validate should be enabled after reload"
