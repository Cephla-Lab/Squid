"""Tests for orchestrator control-panel protocol loading and start behavior."""

from unittest.mock import MagicMock

import pytest

from squid.core.events import EventBus
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
    panel._start_round_index = 2
    panel._start_step_index = 1
    panel._start_fov_index = 4
    panel._run_single_round = True

    panel._on_start_clicked()

    assert panel._start_round_index == 0
    assert panel._start_step_index == 0
    assert panel._start_fov_index == 0
    assert panel._run_single_round is False


def test_start_requires_validation(panel):
    panel._protocol_path = "/tmp/protocol.yaml"
    panel._base_path = "/tmp"
    panel._validated = False

    panel._on_start_clicked()

    panel._orchestrator.start_experiment.assert_not_called()
