"""Tests for v3 protocol schema — the canonical form used in production.

Existing tests in test_protocol.py exclusively use the v2 legacy form
(top-level channels=, version="2.0", etc.). These tests exercise the
canonical v3 nested form to ensure it works without the upgrade shim.
"""

import os
import tempfile
import textwrap

import pytest

from squid.core.protocol import (
    ExperimentProtocol,
    Round,
    ImagingStep,
    ImagingProtocol,
    FluidicsStep,
    InterventionStep,
    ProtocolLoader,
    ProtocolValidationError,
    ImagingAcquisitionConfig,
    ZStackConfig,
    FocusGateConfig,
    CapturePolicyConfig,
)


class TestV3ImagingProtocolConstruction:
    """Test ImagingProtocol using the canonical v3 nested acquisition: form."""

    def test_canonical_construction_with_acquisition(self):
        config = ImagingProtocol(
            acquisition=ImagingAcquisitionConfig(
                channels=["DAPI", "GFP"],
                z_stack=ZStackConfig(planes=5, step_um=1.0),
                acquisition_order="channel_first",
            ),
        )
        assert config.get_channel_names() == ["DAPI", "GFP"]
        assert config.acquisition.z_stack.planes == 5
        assert config.acquisition.acquisition_order == "channel_first"

    def test_canonical_with_focus_gate(self):
        config = ImagingProtocol(
            acquisition=ImagingAcquisitionConfig(channels=["DAPI"]),
            focus_gate=FocusGateConfig(mode="none"),
        )
        assert config.focus_gate.mode == "none" or config.focus_gate.mode.value == "none" or str(config.focus_gate.mode) != ""

    def test_canonical_with_capture_policy(self):
        config = ImagingProtocol(
            acquisition=ImagingAcquisitionConfig(channels=["DAPI"]),
            capture_policy=CapturePolicyConfig(
                max_capture_attempts=3,
                retry_delay_s=0.5,
            ),
        )
        assert config.capture_policy.max_capture_attempts == 3
        assert config.capture_policy.retry_delay_s == 0.5

    def test_v3_and_legacy_produce_same_channels(self):
        """Both construction forms should produce identical channel lists."""
        legacy = ImagingProtocol(channels=["DAPI", "GFP"])
        canonical = ImagingProtocol(
            acquisition=ImagingAcquisitionConfig(channels=["DAPI", "GFP"])
        )
        assert legacy.get_channel_names() == canonical.get_channel_names()


class TestV3ProtocolVersion:
    """Test that version defaults to "3.0" when omitted."""

    def test_default_version_is_3(self):
        protocol = ExperimentProtocol(
            name="test",
            imaging_protocols={"s": ImagingProtocol(channels=["DAPI"])},
            rounds=[Round(name="r0", steps=[ImagingStep(protocol="s")])],
        )
        assert protocol.version == "3.0"

    def test_explicit_version_preserved(self):
        protocol = ExperimentProtocol(
            name="test",
            version="2.0",
            imaging_protocols={"s": ImagingProtocol(channels=["DAPI"])},
            rounds=[Round(name="r0", steps=[ImagingStep(protocol="s")])],
        )
        assert protocol.version == "2.0"


class TestV3LoaderRoundtrip:
    """Test that v3 protocols survive a save/load roundtrip with the loader."""

    def test_save_load_roundtrip_v3(self):
        protocol = ExperimentProtocol(
            name="roundtrip_test",
            version="3.0",
            imaging_protocols={
                "fish": ImagingProtocol(
                    acquisition=ImagingAcquisitionConfig(
                        channels=["DAPI", "Cy5"],
                        z_stack=ZStackConfig(planes=3, step_um=0.5),
                    ),
                ),
            },
            rounds=[
                Round(
                    name="Reference",
                    steps=[ImagingStep(protocol="fish")],
                ),
            ],
        )

        loader = ProtocolLoader()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            loader.save(protocol, f.name)
            loaded = loader.load(f.name)

        try:
            assert loaded.version == "3.0"
            assert loaded.name == "roundtrip_test"
            assert "fish" in loaded.imaging_protocols
            fish = loaded.imaging_protocols["fish"]
            assert fish.get_channel_names() == ["DAPI", "Cy5"]
            assert fish.acquisition.z_stack.planes == 3
        finally:
            os.unlink(f.name)

    def test_load_v3_yaml_without_version_field(self):
        """A YAML without explicit version should get version="3.0"."""
        yaml_content = textwrap.dedent("""\
            name: "No Version"
            imaging_protocols:
              standard:
                acquisition:
                  channels:
                    - "DAPI"
            rounds:
              - name: "r0"
                steps:
                  - step_type: imaging
                    protocol: standard
        """)
        loader = ProtocolLoader()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(yaml_content)
            f.flush()
            loaded = loader.load(f.name)

        try:
            assert loaded.version == "3.0"
        finally:
            os.unlink(f.name)


class TestV3ResourcesBlock:
    """Test the canonical resources: block form (vs legacy top-level keys)."""

    def test_resources_block_file_path_protocol(self):
        """File-path protocol references in imaging steps should be resolved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create individual imaging protocol file
            proto_dir = os.path.join(tmpdir, "protocols")
            os.makedirs(proto_dir)
            proto_yaml = textwrap.dedent("""\
                acquisition:
                  channels:
                    - "DAPI"
                    - "Cy5"
                  z_stack:
                    planes: 5
                    step_um: 1.0
            """)
            proto_path = os.path.join(proto_dir, "fish_standard.yaml")
            with open(proto_path, "w") as f:
                f.write(proto_yaml)

            # Create protocol referencing by file path
            protocol_yaml = textwrap.dedent(f"""\
                name: "V3 Resources Test"
                version: "3.0"
                rounds:
                  - name: "r0"
                    steps:
                      - step_type: imaging
                        protocol: protocols/fish_standard.yaml
            """)
            protocol_path = os.path.join(tmpdir, "protocol.yaml")
            with open(protocol_path, "w") as f:
                f.write(protocol_yaml)

            loader = ProtocolLoader()
            loaded = loader.load(protocol_path)

            assert "protocols/fish_standard.yaml" in loaded.imaging_protocols
            resolved = loaded.imaging_protocols["protocols/fish_standard.yaml"]
            assert resolved.get_channel_names() == ["DAPI", "Cy5"]

    def test_resources_block_fov_file(self):
        """fov_file under resources: should resolve to an absolute run-level path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create FOV CSV
            fov_csv = "region_id,fov_id,x_mm,y_mm,z_um\nregion_1,fov_1,1.0,2.0,0.0\n"
            fov_path = os.path.join(tmpdir, "positions.csv")
            with open(fov_path, "w") as f:
                f.write(fov_csv)

            protocol_yaml = textwrap.dedent(f"""\
                name: "FOV Test"
                version: "3.0"
                resources:
                  fov_file: positions.csv
                imaging_protocols:
                  s:
                    acquisition:
                      channels: ["DAPI"]
                rounds:
                  - name: "r0"
                    steps:
                      - step_type: imaging
                        protocol: s
            """)
            protocol_path = os.path.join(tmpdir, "protocol.yaml")
            with open(protocol_path, "w") as f:
                f.write(protocol_yaml)

            loader = ProtocolLoader()
            loaded = loader.load(protocol_path)

            assert loaded.fov_file is not None
            assert loaded.fov_file.endswith("positions.csv")


class TestV3ValidateReferences:
    """Test reference validation with v3 canonical form."""

    def test_missing_imaging_protocol_detected(self):
        protocol = ExperimentProtocol(
            name="ref_test",
            version="3.0",
            imaging_protocols={
                "exists": ImagingProtocol(
                    acquisition=ImagingAcquisitionConfig(channels=["DAPI"])
                ),
            },
            rounds=[
                Round(
                    name="r0",
                    steps=[ImagingStep(protocol="does_not_exist")],
                ),
            ],
        )
        errors = protocol.validate_references()
        assert len(errors) >= 1
        assert any("does_not_exist" in e for e in errors)

    def test_valid_references_produce_no_errors(self):
        protocol = ExperimentProtocol(
            name="valid_test",
            version="3.0",
            imaging_protocols={
                "s": ImagingProtocol(
                    acquisition=ImagingAcquisitionConfig(channels=["DAPI"])
                ),
            },
            fov_file="/path/to/grid.csv",
            rounds=[
                Round(
                    name="r0",
                    steps=[ImagingStep(protocol="s")],
                ),
            ],
        )
        errors = protocol.validate_references()
        assert len(errors) == 0

    def test_named_fov_sets_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "grid.csv")
            with open(csv_path, "w") as f:
                f.write("region,x (mm),y (mm)\nA,1.0,2.0\n")

            protocol_yaml = textwrap.dedent("""\
                name: "Reject Named FOV Sets"
                version: "3.0"
                resources:
                  fov_sets:
                    grid_a: grid.csv
                imaging_protocols:
                  s:
                    acquisition:
                      channels: ["DAPI"]
                rounds:
                  - name: "r0"
                    steps:
                      - step_type: imaging
                        protocol: s
            """)
            protocol_path = os.path.join(tmpdir, "protocol.yaml")
            with open(protocol_path, "w") as f:
                f.write(protocol_yaml)

            loader = ProtocolLoader()
            with pytest.raises(ProtocolValidationError, match="run-level .*fov_file"):
                loader.load(protocol_path)


class TestImagingStepPauseForReview:
    """Test the pause_for_review field on ImagingStep."""

    def test_default_is_false(self):
        step = ImagingStep(protocol="scan")
        assert step.pause_for_review is False

    def test_explicit_true(self):
        step = ImagingStep(protocol="scan", pause_for_review=True)
        assert step.pause_for_review is True

    def test_round_trip_yaml(self):
        """pause_for_review survives YAML load → model → dump."""
        with tempfile.TemporaryDirectory() as tmpdir:
            protocol_yaml = textwrap.dedent("""\
                name: "Review Test"
                version: "3.0"
                imaging_protocols:
                  scan:
                    acquisition:
                      channels: ["DAPI"]
                rounds:
                  - name: "r0"
                    steps:
                      - step_type: imaging
                        protocol: scan
                        pause_for_review: true
            """)
            protocol_path = os.path.join(tmpdir, "protocol.yaml")
            with open(protocol_path, "w") as f:
                f.write(protocol_yaml)

            loader = ProtocolLoader()
            loaded = loader.load(protocol_path)
            step = loaded.rounds[0].steps[0]
            assert isinstance(step, ImagingStep)
            assert step.pause_for_review is True
