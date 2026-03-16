"""Tests for orchestrator control-panel protocol loading and start behavior."""

from unittest.mock import MagicMock

import pytest

from squid.core.events import EventBus
from squid.core.events import LoadScanCoordinatesCommand
from squid.backend.controllers.orchestrator import (
    OrchestratorInterventionRequired,
    OrchestratorProgress,
    OrchestratorTimingSnapshot,
)
from squid.ui.widgets.orchestrator.orchestrator_widget import OrchestratorControlPanel


@pytest.fixture
def event_bus():
    bus = EventBus()
    bus.start()
    yield bus
    bus.stop()


@pytest.fixture
def panel(event_bus, qtbot):
    orchestrator = MagicMock()
    widget = OrchestratorControlPanel(event_bus=event_bus, orchestrator=orchestrator)
    qtbot.addWidget(widget)
    return widget


def test_load_protocol_uses_loader_repeat_expansion(panel, tmp_path):
    protocol_path = tmp_path / "repeat_protocol.yaml"
    protocol_path.write_text(
        "\n".join(
            [
                "name: Repeat UI Protocol",
                "version: \"2.0\"",
                "imaging_protocols:",
                "  standard:",
                "    channels: [BF]",
                "rounds:",
                "  - name: Round {i}",
                "    repeat: 2",
                "    steps:",
                "      - step_type: imaging",
                "        protocol: standard",
            ]
        )
    )

    panel._load_protocol(str(protocol_path))

    rounds = panel._protocol_data["rounds"]
    assert len(rounds) == 2
    assert rounds[0]["name"] == "Round 1"
    assert rounds[1]["name"] == "Round 2"


def test_start_failure_preserves_selected_start_position(panel):
    panel._orchestrator.start_experiment.return_value = False
    panel._protocol_path = "/tmp/protocol.yaml"
    panel._base_path = "/tmp"
    panel._validated = True
    panel._fov_positions = {"region0": [(0.0, 0.0, 0.0)]}
    panel._start_round_index = 1
    panel._start_step_index = 2
    panel._start_fov_index = 3
    panel._run_single_round = True

    panel._on_start_clicked()

    panel._orchestrator.start_experiment.assert_called_once_with(
        protocol_path="/tmp/protocol.yaml",
        base_path="/tmp",
        experiment_id=None,
        start_from_round=1,
        start_from_step=2,
        start_from_fov=3,
        run_single_round=True,
    )
    assert panel._start_round_index == 1
    assert panel._start_step_index == 2
    assert panel._start_fov_index == 3
    assert panel._run_single_round is True


def test_start_success_resets_start_position(panel):
    panel._orchestrator.start_experiment.return_value = True
    panel._protocol_path = "/tmp/protocol.yaml"
    panel._base_path = "/tmp"
    panel._validated = True
    panel._fov_positions = {"region0": [(0.0, 0.0, 0.0)]}
    panel._start_round_index = 2
    panel._start_step_index = 1
    panel._start_fov_index = 4
    panel._run_single_round = True

    panel._on_start_clicked()

    assert panel._start_round_index == 0
    assert panel._start_step_index == 0
    assert panel._start_fov_index == 0
    assert panel._run_single_round is False


def test_start_does_not_publish_scan_coordinates(panel, event_bus):
    published = []
    event_bus.subscribe(LoadScanCoordinatesCommand, published.append)

    panel._orchestrator.start_experiment.return_value = True
    panel._protocol_path = "/tmp/protocol.yaml"
    panel._base_path = "/tmp"
    panel._validated = True
    panel._protocol_data = {
        "resources": {"fov_file": "/tmp/fovs.csv"},
        "rounds": [
            {"steps": [{"step_type": "imaging", "protocol": "standard"}]},
        ],
    }
    panel._fov_positions = {"region0": [(0.0, 0.0, 0.0)]}

    panel._on_start_clicked()

    assert published == []


def test_start_requires_validation(panel):
    panel._protocol_path = "/tmp/protocol.yaml"
    panel._base_path = "/tmp"
    panel._validated = False

    panel._on_start_clicked()

    panel._orchestrator.start_experiment.assert_not_called()


def test_progress_update_populates_progress_section(panel):
    panel._on_progress_updated_ui(
        OrchestratorProgress(
            experiment_id="exp1",
            current_round=1,
            total_rounds=3,
            current_round_name="Round 1",
            progress_percent=25.0,
            eta_seconds=120.0,
            current_operation="imaging",
            current_step_name="Acquire",
            current_step_index=0,
            total_steps=2,
            current_fov_label="FOV 2",
            current_fov_index=1,
            total_fovs=5,
            attempt=2,
            elapsed_seconds=90.0,
            effective_run_seconds=80.0,
            paused_seconds=10.0,
            retry_overhead_seconds=4.0,
            intervention_overhead_seconds=0.0,
        )
    )

    # Progress bar and time labels should be updated
    assert panel._progress_bar.value() == 25
    assert panel._paused_time_label.text() == "10s"
    assert panel._retry_time_label.text() == "4s"
    assert "Round 1" in panel._round_label.text()
    assert panel._time_remaining_label.text() == "2m 00s"


def test_failure_intervention_shows_retry_skip_abort(panel):
    panel.show()
    panel._on_intervention_required_ui(
        OrchestratorInterventionRequired(
            experiment_id="exp1",
            round_index=0,
            round_name="Round 1",
            message="Capture failed",
            kind="failure",
            attempt=3,
            current_step_name="Acquire",
            current_fov_label="FOV 4",
            allowed_actions=("retry", "skip", "abort"),
        )
    )

    assert not panel._intervention_frame.isHidden()
    assert panel._retry_btn.isVisible()
    assert panel._skip_btn.isVisible()
    assert panel._abort_intervention_btn.isVisible()
    assert not panel._acknowledge_btn.isVisible()
    assert "Acquire" in panel._intervention_context.text()
    assert "FOV 4" in panel._intervention_context.text()
    assert panel._intervention_badge.text() == "RECOVERY REQUIRED"
    assert panel._intervention_title.text() == "Run needs operator recovery"


def test_timing_snapshot_updates_subsystem_breakdown(panel):
    panel._on_timing_snapshot_ui(
        OrchestratorTimingSnapshot(
            experiment_id="exp1",
            elapsed_seconds=120.0,
            effective_run_seconds=100.0,
            paused_seconds=12.0,
            retry_overhead_seconds=5.0,
            intervention_overhead_seconds=3.0,
            eta_seconds=45.0,
            subsystem_seconds={
                "fluidics": 30.0,
                "imaging": 55.0,
                "intervention": 10.0,
            },
        )
    )

    assert panel._subsystem_breakdown._values["imaging"] == 55.0
    assert 45.0 in panel._history["eta"]


def test_intervention_buttons_delegate_to_orchestrator(panel):
    panel._on_retry_clicked()
    panel._orchestrator.resolve_intervention.assert_called_with("retry")

    panel._on_skip_clicked()
    panel._orchestrator.resolve_intervention.assert_called_with("skip")

    panel._on_intervention_abort_clicked()
    panel._orchestrator.resolve_intervention.assert_called_with("abort")
