"""Tests for single-file imaging protocol load/save and file-path resolution."""

import tempfile
import textwrap
from pathlib import Path

import pytest
import yaml

from squid.core.protocol.loader import (
    load_imaging_protocol,
    save_imaging_protocol,
    ProtocolLoader,
    ProtocolValidationError,
)
from squid.core.protocol.imaging_protocol import ImagingProtocol


class TestLoadImagingProtocol:
    """Tests for load_imaging_protocol()."""

    def test_load_canonical_format(self, tmp_path: Path) -> None:
        proto_file = tmp_path / "test.yaml"
        proto_file.write_text(textwrap.dedent("""\
            description: "Test protocol"
            acquisition:
              channels:
                - "BF LED matrix full"
              z_stack:
                planes: 3
                step_um: 0.5
                direction: from_center
              acquisition_order: channel_first
            focus_gate:
              mode: none
            capture_policy:
              max_capture_attempts: 2
        """))
        protocol = load_imaging_protocol(proto_file)
        assert isinstance(protocol, ImagingProtocol)
        assert protocol.description == "Test protocol"
        assert protocol.z_stack.planes == 3
        assert protocol.get_channel_names() == ["BF LED matrix full"]
        assert protocol.capture_policy.max_capture_attempts == 2

    def test_load_legacy_flat_format(self, tmp_path: Path) -> None:
        """upgrade_legacy_shape handles old flat-format files."""
        proto_file = tmp_path / "legacy.yaml"
        proto_file.write_text(textwrap.dedent("""\
            description: "Legacy format"
            channels:
              - "Fluorescence 405 nm Ex"
            z_stack:
              planes: 5
              step_um: 1.0
            skip_saving: true
        """))
        protocol = load_imaging_protocol(proto_file)
        assert protocol.z_stack.planes == 5
        assert protocol.skip_saving is True

    def test_load_not_found_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_imaging_protocol("/nonexistent/path.yaml")

    def test_load_invalid_yaml_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text(": : invalid yaml {{")
        with pytest.raises(ProtocolValidationError, match="Invalid YAML"):
            load_imaging_protocol(bad)

    def test_load_non_dict_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "list.yaml"
        bad.write_text("- item1\n- item2\n")
        with pytest.raises(ProtocolValidationError, match="YAML mapping"):
            load_imaging_protocol(bad)


class TestSaveImagingProtocol:
    """Tests for save_imaging_protocol()."""

    def test_save_creates_file(self, tmp_path: Path) -> None:
        protocol = ImagingProtocol(
            description="Saved protocol",
            acquisition={"channels": ["BF LED matrix full"]},
        )
        out = tmp_path / "saved.yaml"
        save_imaging_protocol(protocol, out)
        assert out.exists()
        data = yaml.safe_load(out.read_text())
        assert data["description"] == "Saved protocol"

    def test_round_trip(self, tmp_path: Path) -> None:
        original = ImagingProtocol(
            description="Round trip test",
            acquisition={
                "channels": ["Fluorescence 405 nm Ex"],
                "z_stack": {"planes": 3, "step_um": 0.5, "direction": "from_center"},
                "acquisition_order": "z_first",
            },
            focus_gate={"mode": "focus_lock", "interval_fovs": 2},
            capture_policy={"max_capture_attempts": 3, "retry_delay_s": 0.5},
        )
        path = tmp_path / "roundtrip.yaml"
        save_imaging_protocol(original, path)
        loaded = load_imaging_protocol(path)
        assert loaded.description == original.description
        assert loaded.z_stack.planes == original.z_stack.planes
        assert loaded.acquisition_order == original.acquisition_order
        assert loaded.focus_gate.interval_fovs == original.focus_gate.interval_fovs
        assert loaded.capture_policy.max_capture_attempts == original.capture_policy.max_capture_attempts

    def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        protocol = ImagingProtocol(
            acquisition={"channels": ["BF LED matrix full"]},
        )
        out = tmp_path / "sub" / "dir" / "proto.yaml"
        save_imaging_protocol(protocol, out)
        assert out.exists()


class TestFilePathProtocolResolution:
    """Tests for ProtocolLoader resolving file-path protocol references."""

    def test_file_path_protocol_resolved(self, tmp_path: Path) -> None:
        """ImagingStep.protocol as file path is loaded and registered."""
        # Create protocol file
        proto_dir = tmp_path / "protocols"
        proto_dir.mkdir()
        proto_file = proto_dir / "test_proto.yaml"
        proto_file.write_text(textwrap.dedent("""\
            description: "File-path protocol"
            acquisition:
              channels:
                - "BF LED matrix full"
              z_stack:
                planes: 2
                step_um: 1.0
            focus_gate:
              mode: none
            capture_policy:
              max_capture_attempts: 1
        """))

        # Create experiment protocol referencing by file path
        experiment = tmp_path / "experiment.yaml"
        experiment.write_text(textwrap.dedent("""\
            name: "File Path Test"
            version: "3.0"
            rounds:
              - name: "R1"
                steps:
                  - step_type: imaging
                    protocol: protocols/test_proto.yaml
        """))

        loader = ProtocolLoader()
        protocol = loader.load(experiment)
        assert "protocols/test_proto.yaml" in protocol.imaging_protocols
        resolved = protocol.imaging_protocols["protocols/test_proto.yaml"]
        assert resolved.description == "File-path protocol"
        assert resolved.z_stack.planes == 2

    def test_missing_protocol_file_raises(self, tmp_path: Path) -> None:
        experiment = tmp_path / "experiment.yaml"
        experiment.write_text(textwrap.dedent("""\
            name: "Missing File Test"
            version: "3.0"
            rounds:
              - name: "R1"
                steps:
                  - step_type: imaging
                    protocol: nonexistent/proto.yaml
        """))
        loader = ProtocolLoader()
        with pytest.raises(ProtocolValidationError, match="not found"):
            loader.load(experiment)

    def test_non_file_protocol_ref_skipped(self, tmp_path: Path) -> None:
        """Protocol refs without . or / are left as-is (for profile resolution)."""
        experiment = tmp_path / "experiment.yaml"
        experiment.write_text(textwrap.dedent("""\
            name: "Name Ref Test"
            version: "3.0"
            resources:
              imaging_protocols:
                my_proto:
                  acquisition:
                    channels:
                      - "BF LED matrix full"
            rounds:
              - name: "R1"
                steps:
                  - step_type: imaging
                    protocol: my_proto
        """))
        loader = ProtocolLoader()
        protocol = loader.load(experiment)
        assert "my_proto" in protocol.imaging_protocols
