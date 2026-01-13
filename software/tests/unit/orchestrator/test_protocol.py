"""Tests for the protocol schema and loader."""

import pytest
import tempfile
from pathlib import Path

from squid.core.protocol import (
    ExperimentProtocol,
    Round,
    FluidicsStep,
    ImagingStep,
    RoundType,
    FluidicsCommand,
    ProtocolLoader,
    ProtocolValidationError,
)


class TestProtocolSchema:
    """Tests for protocol schema models."""

    def test_minimal_protocol(self):
        """Test creating a minimal valid protocol."""
        protocol = ExperimentProtocol(
            name="Test Protocol",
            rounds=[
                Round(
                    name="Round 1",
                    imaging=ImagingStep(channels=["DAPI"]),
                )
            ],
        )
        assert protocol.name == "Test Protocol"
        assert len(protocol.rounds) == 1
        assert protocol.total_imaging_rounds() == 1

    def test_protocol_requires_rounds(self):
        """Test that protocol requires at least one round."""
        with pytest.raises(ValueError, match="at least one round"):
            ExperimentProtocol(name="Test", rounds=[])

    def test_fluidics_step(self):
        """Test fluidics step creation."""
        step = FluidicsStep(
            command=FluidicsCommand.FLOW,
            solution="test_solution",
            volume_ul=100,
            flow_rate_ul_per_min=50,
        )
        assert step.command == FluidicsCommand.FLOW
        assert step.volume_ul == 100
        assert step.repeats == 1

    def test_fluidics_step_repeats_validation(self):
        """Test fluidics step repeats must be >= 1."""
        with pytest.raises(ValueError, match="repeats must be >= 1"):
            FluidicsStep(command=FluidicsCommand.FLOW, repeats=0)

    def test_imaging_step_z_planes_validation(self):
        """Test imaging step z_planes must be >= 1."""
        with pytest.raises(ValueError, match="z_planes must be >= 1"):
            ImagingStep(z_planes=0)

    def test_imaging_step_z_step_validation(self):
        """Test imaging step z_step_um must be > 0."""
        with pytest.raises(ValueError, match="z_step_um must be > 0"):
            ImagingStep(z_step_um=0)

    def test_round_types(self):
        """Test different round types."""
        for round_type in RoundType:
            round_ = Round(name=f"{round_type.value} round", type=round_type)
            assert round_.type == round_type

    def test_get_round_by_name(self):
        """Test finding a round by name."""
        protocol = ExperimentProtocol(
            name="Test",
            rounds=[
                Round(name="First", imaging=ImagingStep(channels=["A"])),
                Round(name="Second", imaging=ImagingStep(channels=["B"])),
            ],
        )

        found = protocol.get_round_by_name("Second")
        assert found is not None
        assert found.name == "Second"

        not_found = protocol.get_round_by_name("Third")
        assert not_found is None

    def test_get_imaging_rounds(self):
        """Test getting only imaging rounds."""
        protocol = ExperimentProtocol(
            name="Test",
            rounds=[
                Round(name="Imaging 1", imaging=ImagingStep(channels=["A"])),
                Round(name="Wash", type=RoundType.WASH),
                Round(name="Imaging 2", imaging=ImagingStep(channels=["B"])),
            ],
        )

        imaging = protocol.get_imaging_rounds()
        assert len(imaging) == 2
        assert imaging[0].name == "Imaging 1"
        assert imaging[1].name == "Imaging 2"


class TestProtocolLoader:
    """Tests for protocol loader."""

    def test_load_valid_yaml(self):
        """Test loading a valid protocol YAML."""
        yaml_content = """
name: Test Protocol
version: "1.0"
description: A test protocol

rounds:
  - name: Round 1
    type: imaging
    imaging:
      channels:
        - DAPI
      z_planes: 5
"""
        loader = ProtocolLoader()
        protocol = loader.load_from_string(yaml_content)

        assert protocol.name == "Test Protocol"
        assert protocol.version == "1.0"
        assert len(protocol.rounds) == 1
        assert protocol.rounds[0].imaging.channels == ["DAPI"]
        assert protocol.rounds[0].imaging.z_planes == 5

    def test_load_invalid_yaml(self):
        """Test loading invalid YAML raises error."""
        loader = ProtocolLoader()

        with pytest.raises(ProtocolValidationError, match="Invalid YAML"):
            loader.load_from_string("name: [invalid yaml")

    def test_load_missing_required_fields(self):
        """Test loading YAML missing required fields."""
        loader = ProtocolLoader()

        # Missing name
        with pytest.raises(ProtocolValidationError):
            loader.load_from_string("rounds: []")

    def test_validate_channels(self):
        """Test channel validation."""
        protocol = ExperimentProtocol(
            name="Test",
            rounds=[
                Round(
                    name="Round 1",
                    imaging=ImagingStep(channels=["DAPI", "GFP", "Unknown"]),
                )
            ],
        )

        loader = ProtocolLoader()
        errors = loader.validate_channels(protocol, ["DAPI", "GFP", "Cy5"])

        assert len(errors) == 1
        assert "Unknown" in errors[0]

    def test_save_and_load(self):
        """Test saving and loading a protocol."""
        protocol = ExperimentProtocol(
            name="Round Trip Test",
            version="2.0",
            rounds=[
                Round(
                    name="Round 1",
                    fluidics=[
                        FluidicsStep(
                            command=FluidicsCommand.FLOW,
                            solution="test",
                            volume_ul=100,
                        )
                    ],
                    imaging=ImagingStep(channels=["DAPI"]),
                )
            ],
        )

        loader = ProtocolLoader()

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test_protocol.yaml"
            loader.save(protocol, path)

            loaded = loader.load(path)

            assert loaded.name == protocol.name
            assert loaded.version == protocol.version
            assert len(loaded.rounds) == len(protocol.rounds)

    def test_create_from_template(self):
        """Test creating a protocol from template."""
        loader = ProtocolLoader()
        protocol = loader.create_from_template(
            name="Template Test",
            num_rounds=3,
            channels=["DAPI", "GFP"],
            include_wash=True,
            z_planes=5,
        )

        assert protocol.name == "Template Test"
        # 3 imaging rounds + 2 wash rounds
        assert len(protocol.rounds) == 5
        assert protocol.total_imaging_rounds() == 3
