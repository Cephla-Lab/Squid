"""Tests for the protocol loader dialog."""

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
                "        fovs: default",
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
