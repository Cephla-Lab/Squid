"""Tests for the V2 protocol schema and loader."""

import pytest
import tempfile
from pathlib import Path

from squid.core.events import AutofocusMode
from squid.core.protocol import (
    ExperimentProtocol,
    Round,
    FluidicsStep,
    ImagingStep,
    InterventionStep,
    ProtocolLoader,
    ProtocolValidationError,
    ImagingProtocol,
    ZStackConfig,
    FocusConfig,
    ChannelConfigOverride,
    ErrorHandlingConfig,
    FailureAction,
)


class TestV2ProtocolSchema:
    """Tests for V2 protocol schema models."""

    def test_minimal_protocol(self):
        """Test creating a minimal valid V2 protocol."""
        protocol = ExperimentProtocol(
            name="Test Protocol",
            imaging_protocols={
                "standard": ImagingProtocol(channels=["DAPI"]),
            },
            rounds=[
                Round(
                    name="Round 1",
                    steps=[ImagingStep(protocol="standard")],
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
        step = ImagingStep(protocol="fish_standard", fovs="main_grid")
        assert step.step_type == "imaging"
        assert step.protocol == "fish_standard"
        assert step.fovs == "main_grid"

    def test_intervention_step(self):
        """Test intervention step creation."""
        step = InterventionStep(message="Replace slide")
        assert step.step_type == "intervention"
        assert step.message == "Replace slide"

    def test_imaging_config_z_planes_validation(self):
        """Test imaging config z_stack planes must be >= 1."""
        with pytest.raises(ValueError, match="planes must be >= 1"):
            ImagingProtocol(
                channels=["DAPI"],
                z_stack=ZStackConfig(planes=0),
            )

    def test_imaging_config_step_um_validation(self):
        """Test imaging config z_stack step_um must be > 0."""
        with pytest.raises(ValueError, match="step_um must be > 0"):
            ImagingProtocol(
                channels=["DAPI"],
                z_stack=ZStackConfig(step_um=0),
            )

    def test_imaging_config_channels_required(self):
        """Test imaging config requires at least one channel."""
        with pytest.raises(ValueError, match="channels must not be empty"):
            ImagingProtocol(channels=[])

    def test_get_round_by_name(self):
        """Test finding a round by name."""
        protocol = ExperimentProtocol(
            name="Test",
            imaging_protocols={
                "a": ImagingProtocol(channels=["A"]),
                "b": ImagingProtocol(channels=["B"]),
            },
            rounds=[
                Round(name="First", steps=[ImagingStep(protocol="a")]),
                Round(name="Second", steps=[ImagingStep(protocol="b")]),
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
            imaging_protocols={
                "standard": ImagingProtocol(channels=["A"]),
            },
            rounds=[
                Round(
                    name="Round 1",
                    steps=[
                        ImagingStep(protocol="standard"),
                        FluidicsStep(protocol="wash"),
                    ],
                ),
                Round(
                    name="Round 2",
                    steps=[
                        FluidicsStep(protocol="wash"),
                        ImagingStep(protocol="standard"),
                        ImagingStep(protocol="standard"),
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
            imaging_protocols={
                "existing": ImagingProtocol(channels=["A"]),
            },
            fov_sets={
                "grid_a": "/path/to/grid_a.csv",
            },
            rounds=[
                Round(
                    name="Round 1",
                    steps=[
                        ImagingStep(protocol="missing_config"),  # Missing
                        ImagingStep(protocol="existing", fovs="missing_fovs"),  # Missing
                        FluidicsStep(protocol="missing_protocol"),  # Missing
                    ],
                ),
            ],
        )

        errors = protocol.validate_references()
        # FluidicsStep references are only validated when fluidics_protocols
        # dict is non-empty (they may be resolved externally), so we only
        # expect the imaging protocol + FOV set errors here.
        assert len(errors) == 2
        assert any("missing_config" in e for e in errors)
        assert any("missing_fovs" in e for e in errors)

    def test_validate_references_allows_default_fovs(self):
        """The reserved FOV alias 'default' should be accepted."""
        protocol = ExperimentProtocol(
            name="Default FOV Alias",
            imaging_protocols={
                "existing": ImagingProtocol(channels=["A"]),
            },
            rounds=[
                Round(
                    name="Round 1",
                    steps=[ImagingStep(protocol="existing", fovs="default")],
                ),
            ],
        )

        errors = protocol.validate_references()
        assert errors == []

    def test_channel_config_override(self):
        """Test channel configuration overrides."""
        config = ImagingProtocol(
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
            FocusConfig(interval_fovs=0)

        # Valid interval should work
        config = FocusConfig(mode=AutofocusMode.CONTRAST, interval_fovs=5)
        assert config.interval_fovs == 5


class TestV2ProtocolLoader:
    """Tests for V2 protocol loader."""

    def test_load_valid_yaml(self):
        """Test loading a valid V2 protocol YAML."""
        yaml_content = """
name: Test Protocol
version: "2.0"
description: A test protocol

imaging_protocols:
  standard:
    channels:
      - DAPI
    z_stack:
      planes: 5

rounds:
  - name: Round 1
    steps:
      - step_type: imaging
        protocol: standard
"""
        loader = ProtocolLoader()
        protocol = loader.load_from_string(yaml_content)

        assert protocol.name == "Test Protocol"
        assert protocol.version == "2.0"
        assert len(protocol.rounds) == 1
        assert len(protocol.rounds[0].steps) == 1
        assert isinstance(protocol.rounds[0].steps[0], ImagingStep)
        assert protocol.rounds[0].steps[0].protocol == "standard"

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
            imaging_protocols={
                "standard": ImagingProtocol(channels=["DAPI", "GFP", "Unknown"]),
            },
            rounds=[
                Round(
                    name="Round 1",
                    steps=[ImagingStep(protocol="standard")],
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

imaging_protocols:
  standard:
    channels: [DAPI]

rounds:
  - name: "Round {i}"
    repeat: 2
    steps:
      - step_type: fluidics
        protocol: probe_{i}
      - step_type: imaging
        protocol: standard
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
            imaging_protocols={
                "standard": ImagingProtocol(channels=["DAPI"]),
            },
            rounds=[
                Round(
                    name="Round 1",
                    steps=[
                        FluidicsStep(protocol="wash"),
                        ImagingStep(protocol="standard"),
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

imaging_protocols:
  standard:
    channels: [DAPI]

rounds:
  - name: Round 1
    steps:
      - step_type: fluidics
        protocol: wash
      - step_type: imaging
        protocol: standard
      - step_type: intervention
        message: "Check focus"
      - step_type: imaging
        protocol: standard
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

imaging_protocols:
  laser_af:
    channels: [DAPI]
    focus:
      mode: laser_reflection
      interval_fovs: 1

  contrast_af:
    channels: [DAPI]
    focus:
      mode: contrast
      channel: DAPI
      interval_fovs: 5

  no_af:
    channels: [DAPI]
    focus:
      mode: none

rounds:
  - name: Round 1
    steps:
      - step_type: imaging
        protocol: laser_af
"""
        loader = ProtocolLoader()
        protocol = loader.load_from_string(yaml_content)

        laser_config = protocol.imaging_protocols["laser_af"]
        assert laser_config.focus.mode == AutofocusMode.LASER_REFLECTION
        assert laser_config.focus.interval_fovs == 1

        contrast_config = protocol.imaging_protocols["contrast_af"]
        assert contrast_config.focus.mode == AutofocusMode.CONTRAST
        assert contrast_config.focus.channel == "DAPI"
        assert contrast_config.focus.interval_fovs == 5

        no_af_config = protocol.imaging_protocols["no_af"]
        assert no_af_config.focus.mode == AutofocusMode.NONE

    def test_invalid_step_type_raises_error(self):
        """Test that invalid step_type raises validation error."""
        yaml_content = """
name: Invalid Step Type Test
version: "2.0"

imaging_protocols:
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
  mode: laser_reflection
""")

            # Create main protocol that references it
            protocol_content = f"""
name: File Reference Test
version: "2.0"

imaging_protocols:
  fish_standard:
    file: fish_config.yaml

rounds:
  - name: Round 1
    steps:
      - step_type: imaging
        protocol: fish_standard
"""
            protocol_path = Path(tmpdir) / "protocol.yaml"
            protocol_path.write_text(protocol_content)

            loader = ProtocolLoader()
            protocol = loader.load(protocol_path)

            assert "fish_standard" in protocol.imaging_protocols
            config = protocol.imaging_protocols["fish_standard"]
            assert config.description == "External FISH config"
            assert config.z_stack.planes == 5
            assert config.focus.mode == AutofocusMode.LASER_REFLECTION

    def test_file_reference_not_found_raises_error(self):
        """Test that missing file: reference raises error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            protocol_content = """
name: Missing File Test
version: "2.0"

imaging_protocols:
  standard:
    file: nonexistent.yaml

rounds:
  - name: Round 1
    steps:
      - step_type: imaging
        protocol: standard
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

imaging_protocols:
  standard:
    channels: [DAPI]

fov_sets:
  main_grid: positions/main.csv

rounds:
  - name: Round 1
    steps:
      - step_type: imaging
        protocol: standard
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

imaging_protocols:
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
        protocol: standard
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

    def test_acquisition_order_default(self):
        """Test that acquisition_order defaults to channel_first."""
        config = ImagingProtocol(channels=["DAPI"])
        assert config.acquisition_order == "channel_first"

    def test_acquisition_order_z_first(self):
        """Test setting acquisition_order to z_first."""
        config = ImagingProtocol(
            channels=["DAPI", "Cy5"],
            acquisition_order="z_first",
        )
        assert config.acquisition_order == "z_first"

    def test_acquisition_order_roundtrips_through_yaml(self):
        """Test that acquisition_order survives save/load cycle."""
        yaml_content = """
name: Acquisition Order Test
version: "2.0"

imaging_protocols:
  z_first_protocol:
    channels: [DAPI, Cy5]
    acquisition_order: z_first

rounds:
  - name: Round 1
    steps:
      - step_type: imaging
        protocol: z_first_protocol
"""
        loader = ProtocolLoader()
        protocol = loader.load_from_string(yaml_content)

        assert protocol.imaging_protocols["z_first_protocol"].acquisition_order == "z_first"

    def test_profile_protocol_resolution(self):
        """Test that imaging protocol names are resolved from ConfigRepository."""
        from unittest.mock import MagicMock

        # Create a mock ConfigRepository
        mock_repo = MagicMock()
        profile_protocol = ImagingProtocol(
            channels=["DAPI", "Cy5"],
            acquisition_order="z_first",
        )
        mock_repo.get_imaging_protocol.return_value = profile_protocol

        yaml_content = """
name: Profile Resolution Test
version: "2.0"

imaging_protocols: {}

rounds:
  - name: Round 1
    steps:
      - step_type: imaging
        protocol: from_profile
"""
        loader = ProtocolLoader(config_repo=mock_repo)
        protocol = loader.load_from_string(yaml_content)

        # Protocol should have been resolved from profile
        assert "from_profile" in protocol.imaging_protocols
        assert protocol.imaging_protocols["from_profile"].acquisition_order == "z_first"
        mock_repo.get_imaging_protocol.assert_called_with("from_profile")

    def test_profile_resolution_not_found_raises_error(self):
        """Test that unresolvable protocol names cause validation error."""
        from unittest.mock import MagicMock

        mock_repo = MagicMock()
        mock_repo.get_imaging_protocol.return_value = None

        yaml_content = """
name: Missing Profile Test
version: "2.0"

imaging_protocols: {}

rounds:
  - name: Round 1
    steps:
      - step_type: imaging
        protocol: nonexistent
"""
        loader = ProtocolLoader(config_repo=mock_repo)
        with pytest.raises(ProtocolValidationError, match="Invalid resource references"):
            loader.load_from_string(yaml_content)

    def test_substitution_in_non_repeated_round_raises_error(self):
        """Test that {i} substitution in a non-repeated round raises error."""
        yaml_content = """
name: Bad Substitution Test
version: "2.0"

imaging_protocols:
  standard:
    channels: [DAPI]

rounds:
  - name: "Round {i}"
    steps:
      - step_type: imaging
        protocol: standard
"""
        loader = ProtocolLoader()
        with pytest.raises(ProtocolValidationError, match="has no 'repeat' field"):
            loader.load_from_string(yaml_content)


class TestResourceFilePaths:
    """Tests for resource file path fields (imaging_protocol_file, etc.)."""

    def test_imaging_protocol_file_merged_into_imaging_protocols(self):
        """Test that imaging_protocol_file contents are merged into imaging_protocols."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create external imaging protocols file
            imaging_file = Path(tmpdir) / "imaging_protos.yaml"
            imaging_file.write_text(
                """
fish_standard:
  channels: [DAPI, Cy5]
  z_stack:
    planes: 5
brightfield:
  channels: [BF]
"""
            )

            protocol_content = f"""
name: Imaging File Test
version: "2.0"

imaging_protocol_file: imaging_protos.yaml

rounds:
  - name: Round 1
    steps:
      - step_type: imaging
        protocol: fish_standard
"""
            protocol_path = Path(tmpdir) / "protocol.yaml"
            protocol_path.write_text(protocol_content)

            loader = ProtocolLoader()
            protocol = loader.load(protocol_path)

            assert "fish_standard" in protocol.imaging_protocols
            assert "brightfield" in protocol.imaging_protocols
            assert protocol.imaging_protocols["fish_standard"].z_stack.planes == 5

    def test_imaging_protocol_file_inline_takes_precedence(self):
        """Test that inline imaging_protocols override file definitions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imaging_file = Path(tmpdir) / "imaging_protos.yaml"
            imaging_file.write_text(
                """
standard:
  channels: [DAPI]
  z_stack:
    planes: 3
"""
            )

            protocol_content = f"""
name: Inline Precedence Test
version: "2.0"

imaging_protocol_file: imaging_protos.yaml

imaging_protocols:
  standard:
    channels: [DAPI, Cy5]
    z_stack:
      planes: 10

rounds:
  - name: Round 1
    steps:
      - step_type: imaging
        protocol: standard
"""
            protocol_path = Path(tmpdir) / "protocol.yaml"
            protocol_path.write_text(protocol_content)

            loader = ProtocolLoader()
            protocol = loader.load(protocol_path)

            # Inline (10 planes) should win over file (3 planes)
            assert protocol.imaging_protocols["standard"].z_stack.planes == 10
            assert len(protocol.imaging_protocols["standard"].get_channel_names()) == 2

    def test_imaging_protocol_file_not_found_raises_error(self):
        """Test that missing imaging_protocol_file raises error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            protocol_content = """
name: Missing Imaging File Test
version: "2.0"

imaging_protocol_file: nonexistent.yaml

imaging_protocols:
  standard:
    channels: [DAPI]

rounds:
  - name: Round 1
    steps:
      - step_type: imaging
        protocol: standard
"""
            protocol_path = Path(tmpdir) / "protocol.yaml"
            protocol_path.write_text(protocol_content)

            loader = ProtocolLoader()
            with pytest.raises(ProtocolValidationError, match="Imaging protocol file not found"):
                loader.load(protocol_path)

    def test_fov_file_added_to_fov_sets_as_default(self):
        """Test that fov_file is added to fov_sets as 'default'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "positions.csv"
            csv_path.write_text("region,x (mm),y (mm)\nA,1.0,2.0\n")

            protocol_content = """
name: FOV File Test
version: "2.0"

fov_file: positions.csv

imaging_protocols:
  standard:
    channels: [DAPI]

rounds:
  - name: Round 1
    steps:
      - step_type: imaging
        protocol: standard
        fovs: default
"""
            protocol_path = Path(tmpdir) / "protocol.yaml"
            protocol_path.write_text(protocol_content)

            loader = ProtocolLoader()
            protocol = loader.load(protocol_path)

            assert "default" in protocol.fov_sets
            assert Path(protocol.fov_sets["default"]).is_absolute()
            assert protocol.fov_sets["default"] == str(csv_path)

    def test_fov_file_does_not_overwrite_existing_default(self):
        """Test that fov_file doesn't overwrite an existing 'default' fov_set."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv1 = Path(tmpdir) / "existing.csv"
            csv1.write_text("region,x (mm),y (mm)\nA,1.0,2.0\n")

            csv2 = Path(tmpdir) / "new.csv"
            csv2.write_text("region,x (mm),y (mm)\nB,3.0,4.0\n")

            protocol_content = """
name: FOV No Overwrite Test
version: "2.0"

fov_file: new.csv

imaging_protocols:
  standard:
    channels: [DAPI]

fov_sets:
  default: existing.csv

rounds:
  - name: Round 1
    steps:
      - step_type: imaging
        protocol: standard
        fovs: default
"""
            protocol_path = Path(tmpdir) / "protocol.yaml"
            protocol_path.write_text(protocol_content)

            loader = ProtocolLoader()
            protocol = loader.load(protocol_path)

            # Existing "default" entry should be preserved
            assert protocol.fov_sets["default"] == str(csv1)

    def test_resource_paths_resolved_to_absolute(self):
        """Test that all resource file paths are resolved to absolute paths."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create required files
            (Path(tmpdir) / "imaging.yaml").write_text("standard:\n  channels: [DAPI]\n")
            (Path(tmpdir) / "fluidics.yaml").write_text("{}\n")
            (Path(tmpdir) / "config.json").write_text("{}\n")
            (Path(tmpdir) / "fovs.csv").write_text("region,x (mm),y (mm)\nA,1.0,2.0\n")

            protocol_content = """
name: Path Resolution Test
version: "2.0"

imaging_protocol_file: imaging.yaml
fluidics_protocols_file: fluidics.yaml
fluidics_config_file: config.json
fov_file: fovs.csv

imaging_protocols:
  standard:
    channels: [DAPI]

rounds:
  - name: Round 1
    steps:
      - step_type: imaging
        protocol: standard
"""
            protocol_path = Path(tmpdir) / "protocol.yaml"
            protocol_path.write_text(protocol_content)

            loader = ProtocolLoader()
            protocol = loader.load(protocol_path)

            assert Path(protocol.imaging_protocol_file).is_absolute()
            assert Path(protocol.fluidics_protocols_file).is_absolute()
            assert Path(protocol.fluidics_config_file).is_absolute()
            assert Path(protocol.fov_file).is_absolute()

    def test_resource_fields_default_to_none(self):
        """Test that resource file fields default to None when not specified."""
        protocol = ExperimentProtocol(
            name="No Resources",
            imaging_protocols={"a": ImagingProtocol(channels=["DAPI"])},
            rounds=[Round(name="R1", steps=[ImagingStep(protocol="a")])],
        )
        assert protocol.imaging_protocol_file is None
        assert protocol.fluidics_protocols_file is None
        assert protocol.fluidics_config_file is None
        assert protocol.fov_file is None

    def test_resource_fields_roundtrip_through_save_load(self):
        """Test that resource file fields survive save/load cycle."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create the files so path resolution works
            (Path(tmpdir) / "imaging.yaml").write_text("extra:\n  channels: [Cy5]\n")
            (Path(tmpdir) / "fovs.csv").write_text("region,x (mm),y (mm)\nA,1.0,2.0\n")

            protocol_content = """
name: Roundtrip Test
version: "2.0"

imaging_protocol_file: imaging.yaml
fov_file: fovs.csv

imaging_protocols:
  standard:
    channels: [DAPI]

rounds:
  - name: Round 1
    steps:
      - step_type: imaging
        protocol: standard
"""
            protocol_path = Path(tmpdir) / "protocol.yaml"
            protocol_path.write_text(protocol_content)

            loader = ProtocolLoader()
            protocol = loader.load(protocol_path)

            # Save and reload
            save_path = Path(tmpdir) / "saved_protocol.yaml"
            loader.save(protocol, save_path)

            # The saved file should contain the absolute paths
            reloaded = loader.load(save_path)
            assert reloaded.imaging_protocol_file is not None
            assert reloaded.fov_file is not None
            assert reloaded.fluidics_protocols_file is None  # Not set originally

    def test_imaging_protocol_file_invalid_format_raises_error(self):
        """Test that non-dict imaging_protocol_file raises error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            imaging_file = Path(tmpdir) / "bad_imaging.yaml"
            imaging_file.write_text("- item1\n- item2\n")

            protocol_content = """
name: Bad Format Test
version: "2.0"

imaging_protocol_file: bad_imaging.yaml

imaging_protocols:
  standard:
    channels: [DAPI]

rounds:
  - name: Round 1
    steps:
      - step_type: imaging
        protocol: standard
"""
            protocol_path = Path(tmpdir) / "protocol.yaml"
            protocol_path.write_text(protocol_content)

            loader = ProtocolLoader()
            with pytest.raises(ProtocolValidationError, match="YAML mapping"):
                loader.load(protocol_path)


class TestResolveProtocolChannels:
    """Tests for resolve_protocol_channels function."""

    def test_resolves_channels_by_name(self):
        """Test that channel names are resolved to AcquisitionChannel objects."""
        from unittest.mock import MagicMock
        from squid.backend.controllers.orchestrator.imaging_executor import (
            resolve_protocol_channels,
        )

        protocol = ImagingProtocol(channels=["DAPI", "Cy5"])

        mock_service = MagicMock()
        dapi_config = MagicMock()
        dapi_config.name = "DAPI"
        cy5_config = MagicMock()
        cy5_config.name = "Cy5"
        mock_service.get_channel_configuration_by_name.side_effect = (
            lambda obj, name: {"DAPI": dapi_config, "Cy5": cy5_config}.get(name)
        )

        result = resolve_protocol_channels(protocol, mock_service, "10X")
        assert len(result) == 2
        assert result[0].name == "DAPI"
        assert result[1].name == "Cy5"

    def test_raises_on_missing_channel(self):
        """Test that ValueError is raised when a channel is not found."""
        from unittest.mock import MagicMock
        from squid.backend.controllers.orchestrator.imaging_executor import (
            resolve_protocol_channels,
        )

        protocol = ImagingProtocol(channels=["DAPI", "NonExistent"])

        mock_service = MagicMock()
        dapi_config = MagicMock()
        dapi_config.name = "DAPI"
        mock_service.get_channel_configuration_by_name.side_effect = (
            lambda obj, name: {"DAPI": dapi_config}.get(name)
        )

        with pytest.raises(ValueError, match="NonExistent"):
            resolve_protocol_channels(protocol, mock_service, "10X")

    def test_applies_channel_override(self):
        """Test that ChannelConfigOverride values are applied to resolved channels."""
        from unittest.mock import MagicMock
        from squid.backend.controllers.orchestrator.imaging_executor import (
            resolve_protocol_channels,
        )
        from squid.core.config.models import AcquisitionChannel, CameraSettings, IlluminationSettings

        protocol = ImagingProtocol(
            channels=[
                ChannelConfigOverride(
                    name="Cy5",
                    exposure_time_ms=200,
                    illumination_intensity=80,
                ),
            ],
        )

        mock_service = MagicMock()
        channel = AcquisitionChannel(
            name="Cy5",
            camera_settings=CameraSettings(exposure_time_ms=100, gain_mode=1.0),
            illumination_settings=IlluminationSettings(intensity=50),
        )
        mock_service.get_channel_configuration_by_name.return_value = channel

        result = resolve_protocol_channels(protocol, mock_service, "10X")
        assert len(result) == 1
        assert result[0].camera_settings.exposure_time_ms == 200
        assert result[0].illumination_settings.intensity == 80
        # Original channel should not be mutated
        assert channel.camera_settings.exposure_time_ms == 100
        assert channel.illumination_settings.intensity == 50
