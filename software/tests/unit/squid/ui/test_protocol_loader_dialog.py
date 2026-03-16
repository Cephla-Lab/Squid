"""Tests for the protocol loader dialog."""

from unittest.mock import MagicMock

from squid.core.events import ClearScanCoordinatesCommand, LoadScanCoordinatesCommand
from squid.ui.widgets.orchestrator.protocol_loader_dialog import ProtocolLoaderDialog


def test_load_protocol_prefills_output_directory_and_fovs(qtbot, tmp_path):
    outputs_dir = tmp_path / "outputs"
    outputs_dir.mkdir()
    fov_path = tmp_path / "fovs.csv"
    fov_path.write_text("region,x (mm),y (mm)\nregion_1,1.0,2.0\n")
    protocol_path = tmp_path / "protocol.yaml"
    protocol_path.write_text(
        "\n".join(
            [
                "name: Dialog Load Test",
                'version: "3.0"',
                "output_directory: outputs",
                "resources:",
                "  fov_file: fovs.csv",
                "imaging_protocols:",
                "  standard:",
                "    channels: [BF]",
                "rounds:",
                "  - name: Round 1",
                "    steps:",
                "      - step_type: imaging",
                "        protocol: standard",
            ]
        )
    )

    dialog = ProtocolLoaderDialog()
    qtbot.addWidget(dialog)

    dialog._load_protocol(str(protocol_path))

    assert dialog.get_output_path() == str(outputs_dir)
    assert dialog.get_fov_path() == str(fov_path)
    assert dialog.get_fov_positions()["region_1"] == [(1.0, 2.0, 0.0)]
    assert dialog._start_btn.isEnabled() is True


def test_load_protocol_publishes_preview_fovs(qtbot, tmp_path):
    event_bus = MagicMock()
    fov_path = tmp_path / "fovs.csv"
    fov_path.write_text("region,x (mm),y (mm),z (mm)\nregion_1,20.0,30.0,1.0\n")
    protocol_path = tmp_path / "protocol.yaml"
    protocol_path.write_text(
        "\n".join(
            [
                "name: Dialog Preview Publish Test",
                'version: "3.0"',
                "resources:",
                "  fov_file: fovs.csv",
                "imaging_protocols:",
                "  standard:",
                "    channels: [BF]",
                "rounds:",
                "  - name: Round 1",
                "    steps:",
                "      - step_type: imaging",
                "        protocol: standard",
            ]
        )
    )

    dialog = ProtocolLoaderDialog(event_bus=event_bus)
    qtbot.addWidget(dialog)

    dialog._load_protocol(str(protocol_path))

    published_events = [call.args[0] for call in event_bus.publish.call_args_list]
    assert any(isinstance(event, ClearScanCoordinatesCommand) for event in published_events)
    load_events = [
        event for event in published_events if isinstance(event, LoadScanCoordinatesCommand)
    ]
    assert len(load_events) == 1
    assert load_events[0].region_fov_coordinates == {"region_1": ((20.0, 30.0, 1.0),)}
