"""Tests for the V2 protocol schema and loader."""

import pytest
import tempfile
from pathlib import Path

from squid.core.protocol import (
    ExperimentProtocol,
    Round,
    FluidicsStep,
    ImagingStep,
    InterventionStep,
    FluidicsCommand,
    ProtocolLoader,
    ProtocolValidationError,
    ImagingConfig,
    ZStackConfig,
    FocusConfig,
    ChannelConfigOverride,
    FluidicsProtocol,
    FluidicsProtocolStep,
    ErrorHandlingConfig,
    FailureAction,
)


class TestV2ProtocolSchema:
    """Tests for V2 protocol schema models."""

    def test_minimal_protocol(self):
        """Test creating a minimal valid V2 protocol."""
        protocol = ExperimentProtocol(
            name="Test Protocol",
            imaging_configs={
                "standard": ImagingConfig(channels=["DAPI"]),
            },
            rounds=[
                Round(
                    name="Round 1",
                    steps=[ImagingStep(config="standard")],
                )
            ],
        )
        assert protocol.name == "Test Protocol"
        assert len(protocol.rounds) == 1
        assert protocol.total_imaging_steps() == 1

    def test_protocol_requires_rounds(self):
        """Test that protocol requires at least one round."""
        with pytest.raises(ValueError, match="at least one round"):
            ExperimentProtocol(name="Test", rounds=[])

    def test_fluidics_step(self):
        """Test V2 fluidics step creation (references named protocol)."""
        step = FluidicsStep(protocol="wash")
        assert step.step_type == "fluidics"
        assert step.protocol == "wash"

    def test_imaging_step(self):
        """Test V2 imaging step creation (references named config)."""
        step = ImagingStep(config="fish_standard", fovs="main_grid")
        assert step.step_type == "imaging"
        assert step.config == "fish_standard"
        assert step.fovs == "main_grid"

    def test_intervention_step(self):
        """Test intervention step creation."""
        step = InterventionStep(message="Replace slide")
        assert step.step_type == "intervention"
        assert step.message == "Replace slide"

    def test_imaging_config_z_planes_validation(self):
        """Test imaging config z_stack planes must be >= 1."""
        with pytest.raises(ValueError, match="planes must be >= 1"):
            ImagingConfig(
                channels=["DAPI"],
                z_stack=ZStackConfig(planes=0),
            )

    def test_imaging_config_step_um_validation(self):
        """Test imaging config z_stack step_um must be > 0."""
        with pytest.raises(ValueError, match="step_um must be > 0"):
            ImagingConfig(
                channels=["DAPI"],
                z_stack=ZStackConfig(step_um=0),
            )

    def test_imaging_config_channels_required(self):
        """Test imaging config requires at least one channel."""
        with pytest.raises(ValueError, match="channels must not be empty"):
            ImagingConfig(channels=[])

    def test_get_round_by_name(self):
        """Test finding a round by name."""
        protocol = ExperimentProtocol(
            name="Test",
            imaging_configs={
                "a": ImagingConfig(channels=["A"]),
                "b": ImagingConfig(channels=["B"]),
            },
            rounds=[
                Round(name="First", steps=[ImagingStep(config="a")]),
                Round(name="Second", steps=[ImagingStep(config="b")]),
            ],
        )

        found = protocol.get_round_by_name("Second")
        assert found is not None
        assert found.name == "Second"

        not_found = protocol.get_round_by_name("Third")
        assert not_found is None

    def test_get_imaging_steps(self):
        """Test getting all imaging steps across rounds."""
        protocol = ExperimentProtocol(
            name="Test",
            fluidics_protocols={
                "wash": FluidicsProtocol(
                    steps=[FluidicsProtocolStep(operation=FluidicsCommand.WASH)]
                ),
            },
            imaging_configs={
                "standard": ImagingConfig(channels=["A"]),
            },
            rounds=[
                Round(
                    name="Round 1",
                    steps=[
                        ImagingStep(config="standard"),
                        FluidicsStep(protocol="wash"),
                    ],
                ),
                Round(
                    name="Round 2",
                    steps=[
                        FluidicsStep(protocol="wash"),
                        ImagingStep(config="standard"),
                        ImagingStep(config="standard"),
                    ],
                ),
            ],
        )

        imaging_steps = protocol.get_imaging_steps()
        assert len(imaging_steps) == 3
        assert all(isinstance(s, ImagingStep) for s in imaging_steps)

    def test_validate_references(self):
        """Test reference validation catches missing resources."""
        protocol = ExperimentProtocol(
            name="Test",
            imaging_configs={
                "existing": ImagingConfig(channels=["A"]),
            },
            fov_sets={
                "grid_a": "/path/to/grid_a.csv",
            },
            rounds=[
                Round(
                    name="Round 1",
                    steps=[
                        ImagingStep(config="missing_config"),  # Missing
                        ImagingStep(config="existing", fovs="missing_fovs"),  # Missing
                        FluidicsStep(protocol="missing_protocol"),  # Missing
                    ],
                ),
            ],
        )

        errors = protocol.validate_references()
        assert len(errors) == 3
        assert any("missing_config" in e for e in errors)
        assert any("missing_fovs" in e for e in errors)
        assert any("missing_protocol" in e for e in errors)

    def test_channel_config_override(self):
        """Test channel configuration overrides."""
        config = ImagingConfig(
            channels=[
                "DAPI",
                ChannelConfigOverride(
                    name="Cy5",
                    exposure_time_ms=200,
                    illumination_intensity=80,
                ),
            ],
        )

        assert config.get_channel_names() == ["DAPI", "Cy5"]
        overrides = config.get_channel_overrides()
        assert len(overrides) == 1
        assert overrides[0].name == "Cy5"
        assert overrides[0].exposure_time_ms == 200

    def test_error_handling_config(self):
        """Test error handling configuration."""
        config = ErrorHandlingConfig(
            focus_failure=FailureAction.SKIP,
            fluidics_failure=FailureAction.ABORT,
            imaging_failure=FailureAction.WARN,
        )

        assert config.focus_failure == FailureAction.SKIP
        assert config.fluidics_failure == FailureAction.ABORT
        assert config.imaging_failure == FailureAction.WARN

    def test_channel_override_exposure_validation(self):
        """Test ChannelConfigOverride validates exposure_time_ms > 0."""
        with pytest.raises(ValueError, match="exposure_time_ms must be > 0"):
            ChannelConfigOverride(name="Cy5", exposure_time_ms=0)

        with pytest.raises(ValueError, match="exposure_time_ms must be > 0"):
            ChannelConfigOverride(name="Cy5", exposure_time_ms=-10)

        # Valid exposure should work
        override = ChannelConfigOverride(name="Cy5", exposure_time_ms=100)
        assert override.exposure_time_ms == 100

    def test_channel_override_intensity_validation(self):
        """Test ChannelConfigOverride validates illumination_intensity 0-100."""
        with pytest.raises(ValueError, match="illumination_intensity must be between 0 and 100"):
            ChannelConfigOverride(name="Cy5", illumination_intensity=-1)

        with pytest.raises(ValueError, match="illumination_intensity must be between 0 and 100"):
            ChannelConfigOverride(name="Cy5", illumination_intensity=101)

        # Valid intensities should work
        override_min = ChannelConfigOverride(name="Cy5", illumination_intensity=0)
        assert override_min.illumination_intensity == 0

        override_max = ChannelConfigOverride(name="Cy5", illumination_intensity=100)
        assert override_max.illumination_intensity == 100

    def test_focus_config_interval_validation(self):
        """Test FocusConfig validates interval_fovs >= 1."""
        with pytest.raises(ValueError, match="interval_fovs must be >= 1"):
            FocusConfig(enabled=True, interval_fovs=0)

        # Valid interval should work
        config = FocusConfig(enabled=True, interval_fovs=5)
        assert config.interval_fovs == 5


class TestV2ProtocolLoader:
    """Tests for V2 protocol loader."""

    def test_load_valid_yaml(self):
        """Test loading a valid V2 protocol YAML."""
        yaml_content = """
name: Test Protocol
version: "2.0"
description: A test protocol

imaging_configs:
  standard:
    channels:
      - DAPI
    z_stack:
      planes: 5

rounds:
  - name: Round 1
    steps:
      - step_type: imaging
        config: standard
"""
        loader = ProtocolLoader()
        protocol = loader.load_from_string(yaml_content)

        assert protocol.name == "Test Protocol"
        assert protocol.version == "2.0"
        assert len(protocol.rounds) == 1
        assert len(protocol.rounds[0].steps) == 1
        assert isinstance(protocol.rounds[0].steps[0], ImagingStep)
        assert protocol.rounds[0].steps[0].config == "standard"

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
        """Test channel validation against available channels."""
        protocol = ExperimentProtocol(
            name="Test",
            imaging_configs={
                "standard": ImagingConfig(channels=["DAPI", "GFP", "Unknown"]),
            },
            rounds=[
                Round(
                    name="Round 1",
                    steps=[ImagingStep(config="standard")],
                )
            ],
        )

        loader = ProtocolLoader()
        errors = loader.validate_channels(protocol, ["DAPI", "GFP", "Cy5"])

        assert len(errors) == 1
        assert "Unknown" in errors[0]

    def test_repeat_expansion(self):
        """Test repeat expansion with {i} substitution."""
        yaml_content = """
name: Test Repeat
version: "2.0"

fluidics_protocols:
  probe_1:
    steps:
      - operation: flow
        solution: p1
        volume_ul: 100
  probe_2:
    steps:
      - operation: flow
        solution: p2
        volume_ul: 100

imaging_configs:
  standard:
    channels: [DAPI]

rounds:
  - name: "Round {i}"
    repeat: 2
    steps:
      - step_type: fluidics
        protocol: probe_{i}
      - step_type: imaging
        config: standard
"""
        loader = ProtocolLoader()
        protocol = loader.load_from_string(yaml_content)

        assert len(protocol.rounds) == 2
        assert protocol.rounds[0].name == "Round 1"
        assert protocol.rounds[1].name == "Round 2"

        # Check {i} substitution in step references
        step0 = protocol.rounds[0].steps[0]
        assert isinstance(step0, FluidicsStep)
        assert step0.protocol == "probe_1"

        step1 = protocol.rounds[1].steps[0]
        assert isinstance(step1, FluidicsStep)
        assert step1.protocol == "probe_2"

    def test_save_and_load(self):
        """Test saving and loading a V2 protocol."""
        protocol = ExperimentProtocol(
            name="Round Trip Test",
            version="2.0",
            fluidics_protocols={
                "wash": FluidicsProtocol(
                    steps=[
                        FluidicsProtocolStep(
                            operation=FluidicsCommand.FLOW,
                            solution="test",
                            volume_ul=100,
                        )
                    ],
                ),
            },
            imaging_configs={
                "standard": ImagingConfig(channels=["DAPI"]),
            },
            rounds=[
                Round(
                    name="Round 1",
                    steps=[
                        FluidicsStep(protocol="wash"),
                        ImagingStep(config="standard"),
                    ],
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
            assert len(loaded.rounds[0].steps) == 2

    def test_mixed_step_types(self):
        """Test loading protocol with all step types."""
        yaml_content = """
name: Mixed Steps Test
version: "2.0"

fluidics_protocols:
  wash:
    steps:
      - operation: flow
        solution: buffer
        volume_ul: 500

imaging_configs:
  standard:
    channels: [DAPI]

rounds:
  - name: Round 1
    steps:
      - step_type: fluidics
        protocol: wash
      - step_type: imaging
        config: standard
      - step_type: intervention
        message: "Check focus"
      - step_type: imaging
        config: standard
"""
        loader = ProtocolLoader()
        protocol = loader.load_from_string(yaml_content)

        steps = protocol.rounds[0].steps
        assert len(steps) == 4
        assert isinstance(steps[0], FluidicsStep)
        assert isinstance(steps[1], ImagingStep)
        assert isinstance(steps[2], InterventionStep)
        assert isinstance(steps[3], ImagingStep)

    def test_focus_config_methods(self):
        """Test focus configuration methods."""
        yaml_content = """
name: Focus Methods Test
version: "2.0"

imaging_configs:
  laser_af:
    channels: [DAPI]
    focus:
      enabled: true
      method: laser
      interval_fovs: 1

  contrast_af:
    channels: [DAPI]
    focus:
      enabled: true
      method: contrast
      channel: DAPI
      interval_fovs: 5

  no_af:
    channels: [DAPI]
    focus:
      enabled: false

rounds:
  - name: Round 1
    steps:
      - step_type: imaging
        config: laser_af
"""
        loader = ProtocolLoader()
        protocol = loader.load_from_string(yaml_content)

        laser_config = protocol.imaging_configs["laser_af"]
        assert laser_config.focus.enabled is True
        assert laser_config.focus.method == "laser"
        assert laser_config.focus.interval_fovs == 1

        contrast_config = protocol.imaging_configs["contrast_af"]
        assert contrast_config.focus.enabled is True
        assert contrast_config.focus.method == "contrast"
        assert contrast_config.focus.channel == "DAPI"
        assert contrast_config.focus.interval_fovs == 5

        no_af_config = protocol.imaging_configs["no_af"]
        assert no_af_config.focus.enabled is False

    def test_invalid_step_type_raises_error(self):
        """Test that invalid step_type raises validation error."""
        yaml_content = """
name: Invalid Step Type Test
version: "2.0"

imaging_configs:
  standard:
    channels: [DAPI]

rounds:
  - name: Round 1
    steps:
      - step_type: invalid_type
        foo: bar
"""
        loader = ProtocolLoader()
        with pytest.raises(ProtocolValidationError):
            loader.load_from_string(yaml_content)

    def test_load_with_file_reference(self):
        """Test loading protocol with file: reference for imaging_config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create external config file
            config_path = Path(tmpdir) / "fish_config.yaml"
            config_path.write_text("""
description: External FISH config
channels:
  - DAPI
  - Cy5
z_stack:
  planes: 5
  step_um: 0.5
focus:
  enabled: true
  method: laser
""")

            # Create main protocol that references it
            protocol_content = f"""
name: File Reference Test
version: "2.0"

imaging_configs:
  fish_standard:
    file: fish_config.yaml

rounds:
  - name: Round 1
    steps:
      - step_type: imaging
        config: fish_standard
"""
            protocol_path = Path(tmpdir) / "protocol.yaml"
            protocol_path.write_text(protocol_content)

            loader = ProtocolLoader()
            protocol = loader.load(protocol_path)

            assert "fish_standard" in protocol.imaging_configs
            config = protocol.imaging_configs["fish_standard"]
            assert config.description == "External FISH config"
            assert config.z_stack.planes == 5
            assert config.focus.method == "laser"

    def test_file_reference_not_found_raises_error(self):
        """Test that missing file: reference raises error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            protocol_content = """
name: Missing File Test
version: "2.0"

imaging_configs:
  standard:
    file: nonexistent.yaml

rounds:
  - name: Round 1
    steps:
      - step_type: imaging
        config: standard
"""
            protocol_path = Path(tmpdir) / "protocol.yaml"
            protocol_path.write_text(protocol_content)

            loader = ProtocolLoader()
            with pytest.raises(ProtocolValidationError, match="not found"):
                loader.load(protocol_path)

    def test_fov_set_relative_path_resolution(self):
        """Test that relative FOV set paths are made absolute."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a dummy CSV file
            csv_path = Path(tmpdir) / "positions" / "main.csv"
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            csv_path.write_text("region,x (mm),y (mm)\nA,1.0,2.0\n")

            protocol_content = """
name: FOV Path Test
version: "2.0"

imaging_configs:
  standard:
    channels: [DAPI]

fov_sets:
  main_grid: positions/main.csv

rounds:
  - name: Round 1
    steps:
      - step_type: imaging
        config: standard
        fovs: main_grid
"""
            protocol_path = Path(tmpdir) / "protocol.yaml"
            protocol_path.write_text(protocol_content)

            loader = ProtocolLoader()
            protocol = loader.load(protocol_path)

            # Path should be absolute now
            assert Path(protocol.fov_sets["main_grid"]).is_absolute()
            assert protocol.fov_sets["main_grid"] == str(csv_path)

    def test_repeat_preserves_metadata(self):
        """Test that repeat expansion preserves round metadata."""
        yaml_content = """
name: Repeat Metadata Test
version: "2.0"

imaging_configs:
  standard:
    channels: [DAPI]

rounds:
  - name: "Round {i}"
    repeat: 3
    metadata:
      probe_set: "set_{i}"
      temperature: 37
    steps:
      - step_type: imaging
        config: standard
"""
        loader = ProtocolLoader()
        protocol = loader.load_from_string(yaml_content)

        assert len(protocol.rounds) == 3
        # Metadata should be substituted too
        assert protocol.rounds[0].metadata["probe_set"] == "set_1"
        assert protocol.rounds[1].metadata["probe_set"] == "set_2"
        assert protocol.rounds[2].metadata["probe_set"] == "set_3"
        # Non-string values preserved
        assert protocol.rounds[0].metadata["temperature"] == 37
