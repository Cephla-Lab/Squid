"""Unit tests for ProtocolValidator (V2 protocol format)."""

import pytest

from squid.core.protocol import (
    ExperimentProtocol,
    FluidicsStep,
    ImagingStep,
    InterventionStep,
    Round,
    ImagingProtocol,
    ZStackConfig,
    FocusConfig,
)
from squid.backend.controllers.orchestrator.validation import (
    OperationEstimate,
    ValidationSummary,
    DEFAULT_TIMING_ESTIMATES,
    DEFAULT_DISK_ESTIMATES,
)
from squid.backend.controllers.orchestrator.protocol_validator import (
    ProtocolValidator,
)


class TestOperationEstimate:
    """Tests for OperationEstimate dataclass."""

    def test_create_estimate(self):
        """Test creating an operation estimate."""
        estimate = OperationEstimate(
            operation_type="imaging",
            round_index=0,
            round_name="Round 1",
            description="3 channels, 5 z-planes",
            estimated_seconds=120.0,
            estimated_disk_bytes=1024 * 1024 * 100,
        )

        assert estimate.operation_type == "imaging"
        assert estimate.round_index == 0
        assert estimate.estimated_seconds == 120.0
        assert estimate.valid is True
        assert not estimate.has_errors
        assert not estimate.has_warnings

    def test_estimate_with_errors(self):
        """Test estimate with validation errors."""
        estimate = OperationEstimate(
            operation_type="imaging",
            round_index=1,
            round_name="Round 2",
            description="Invalid config",
            valid=False,
            validation_errors=("Channel FITC not available",),
        )

        assert estimate.valid is False
        assert estimate.has_errors
        assert "FITC" in estimate.validation_errors[0]

    def test_estimate_with_warnings(self):
        """Test estimate with validation warnings."""
        estimate = OperationEstimate(
            operation_type="fluidics",
            round_index=0,
            round_name="Wash",
            description="2 fluidics steps",
            validation_warnings=("No solution specified",),
        )

        assert estimate.valid is True
        assert estimate.has_warnings
        assert not estimate.has_errors


class TestValidationSummary:
    """Tests for ValidationSummary dataclass."""

    def test_create_empty(self):
        """Test creating an empty validation summary."""
        summary = ValidationSummary.create_empty("Test Protocol")

        assert summary.protocol_name == "Test Protocol"
        assert summary.total_rounds == 0
        assert summary.valid is True
        assert not summary.has_errors
        assert not summary.has_warnings

    def test_create_error(self):
        """Test creating an error summary."""
        summary = ValidationSummary.create_error("Bad Protocol", "Protocol has no rounds")

        assert summary.protocol_name == "Bad Protocol"
        assert summary.valid is False
        assert summary.has_errors
        assert "no rounds" in summary.errors[0]

    def test_estimated_hours(self):
        """Test estimated hours calculation."""
        summary = ValidationSummary(
            protocol_name="Test",
            total_rounds=5,
            total_estimated_seconds=7200.0,  # 2 hours
            total_disk_bytes=0,
            operation_estimates=(),
            errors=(),
            warnings=(),
            valid=True,
        )

        assert summary.estimated_hours == 2.0

    def test_estimated_disk_gb(self):
        """Test estimated disk GB calculation."""
        summary = ValidationSummary(
            protocol_name="Test",
            total_rounds=5,
            total_estimated_seconds=0.0,
            total_disk_bytes=1024**3 * 2,  # 2 GB
            operation_estimates=(),
            errors=(),
            warnings=(),
            valid=True,
        )

        assert summary.estimated_disk_gb == 2.0

    def test_get_errors_for_round(self):
        """Test getting errors for a specific round."""
        estimates = (
            OperationEstimate(
                operation_type="imaging",
                round_index=0,
                round_name="R1",
                description="",
                validation_errors=("Error 1",),
            ),
            OperationEstimate(
                operation_type="imaging",
                round_index=1,
                round_name="R2",
                description="",
                validation_errors=("Error 2", "Error 3"),
            ),
            OperationEstimate(
                operation_type="fluidics",
                round_index=1,
                round_name="R2",
                description="",
                validation_errors=("Error 4",),
            ),
        )

        summary = ValidationSummary(
            protocol_name="Test",
            total_rounds=2,
            total_estimated_seconds=0.0,
            total_disk_bytes=0,
            operation_estimates=estimates,
            errors=(),
            warnings=(),
            valid=False,
        )

        round_0_errors = summary.get_errors_for_round(0)
        assert len(round_0_errors) == 1
        assert "Error 1" in round_0_errors

        round_1_errors = summary.get_errors_for_round(1)
        assert len(round_1_errors) == 3


class TestProtocolValidator:
    """Tests for ProtocolValidator class with V2 protocol format."""

    @pytest.fixture
    def simple_protocol(self) -> ExperimentProtocol:
        """Create a simple V2 test protocol."""
        return ExperimentProtocol(
            name="Test Protocol",
            version="2.0",
            imaging_protocols={
                "standard": ImagingProtocol(
                    channels=["DAPI", "FITC"],
                    z_stack=ZStackConfig(planes=5),
                ),
            },
            rounds=[
                Round(
                    name="Round 1",
                    steps=[
                        ImagingStep(protocol="standard"),
                    ],
                ),
                Round(
                    name="Wash",
                    steps=[
                        FluidicsStep(protocol="wash"),
                    ],
                ),
            ],
        )

    @pytest.fixture
    def complex_protocol(self) -> ExperimentProtocol:
        """Create a complex V2 test protocol with multiple step types."""
        return ExperimentProtocol(
            name="Complex Protocol",
            version="2.0",
            imaging_protocols={
                "default_imaging": ImagingProtocol(
                    channels=["DAPI", "Cy3", "Cy5"],
                    z_stack=ZStackConfig(planes=3),
                ),
                "reduced_imaging": ImagingProtocol(
                    channels=["DAPI", "Cy3"],
                    z_stack=ZStackConfig(planes=3),
                ),
            },
            rounds=[
                Round(
                    name="Round 1",
                    steps=[
                        FluidicsStep(protocol="probe_1"),
                        ImagingStep(protocol="default_imaging"),
                        FluidicsStep(protocol="wash"),
                    ],
                ),
                Round(
                    name="Round 2",
                    steps=[
                        InterventionStep(message="Change slide"),
                        FluidicsStep(protocol="probe_2"),
                        ImagingStep(protocol="reduced_imaging"),
                        FluidicsStep(protocol="wash"),
                    ],
                ),
            ],
        )

    def test_validate_simple_protocol(self, simple_protocol):
        """Test validating a simple V2 protocol."""
        validator = ProtocolValidator(available_fluidics_protocols={"wash"})
        summary = validator.validate(simple_protocol, fov_count=10)

        assert summary.valid is True
        assert summary.protocol_name == "Test Protocol"
        assert summary.total_rounds == 2
        assert summary.total_estimated_seconds > 0
        assert summary.total_disk_bytes > 0

    def test_validate_with_unavailable_channels(self, simple_protocol):
        """Test validation fails with unavailable channels."""
        validator = ProtocolValidator(available_channels={"DAPI"})  # No FITC
        summary = validator.validate(simple_protocol, fov_count=1)

        assert summary.valid is False
        assert summary.has_errors
        assert any("FITC" in err for err in summary.errors)

    def test_validate_with_all_channels_available(self, simple_protocol):
        """Test validation passes with all channels available."""
        validator = ProtocolValidator(
            available_channels={"DAPI", "FITC", "Cy3", "Cy5"},
            available_fluidics_protocols={"wash"},
        )
        summary = validator.validate(simple_protocol, fov_count=1)

        assert summary.valid is True

    def test_validate_complex_protocol(self, complex_protocol):
        """Test validating a complex protocol with fluidics, imaging, and interventions."""
        validator = ProtocolValidator(
            available_fluidics_protocols={"probe_1", "probe_2", "wash"}
        )
        summary = validator.validate(complex_protocol, fov_count=20)

        assert summary.valid is True
        assert summary.total_rounds == 2

        # Should have multiple operation estimates
        assert len(summary.operation_estimates) > 2

        # Should have imaging estimates
        imaging_ops = [op for op in summary.operation_estimates if op.operation_type == "imaging"]
        assert len(imaging_ops) == 2  # Round 1 and Round 2

        # Should have fluidics estimates
        fluidics_ops = [op for op in summary.operation_estimates if op.operation_type == "fluidics"]
        assert len(fluidics_ops) == 4  # probe_1, wash, probe_2, wash

        # Should have intervention estimate
        intervention_ops = [op for op in summary.operation_estimates if op.operation_type == "intervention"]
        assert len(intervention_ops) == 1

    def test_time_estimation(self, simple_protocol):
        """Test time estimation scales with FOV count."""
        validator = ProtocolValidator()

        summary_10 = validator.validate(simple_protocol, fov_count=10)
        summary_100 = validator.validate(simple_protocol, fov_count=100)

        # More FOVs should mean more time
        assert summary_100.total_estimated_seconds > summary_10.total_estimated_seconds

    def test_disk_estimation(self, simple_protocol):
        """Test disk estimation scales with FOV count."""
        validator = ProtocolValidator()

        summary_10 = validator.validate(simple_protocol, fov_count=10)
        summary_100 = validator.validate(simple_protocol, fov_count=100)

        # More FOVs should mean more disk usage
        assert summary_100.total_disk_bytes > summary_10.total_disk_bytes

    def test_disk_estimation_with_skip_saving(self):
        """Test that skip_saving=True excludes disk estimation."""
        protocol = ExperimentProtocol(
            name="Preview Protocol",
            imaging_protocols={
                "preview": ImagingProtocol(
                    channels=["DAPI"],
                    skip_saving=True,
                ),
            },
            rounds=[
                Round(
                    name="Preview",
                    steps=[ImagingStep(protocol="preview")],
                ),
            ],
        )

        validator = ProtocolValidator()
        summary = validator.validate(protocol, fov_count=100)

        # Find imaging operation
        imaging_ops = [op for op in summary.operation_estimates if op.operation_type == "imaging"]
        assert len(imaging_ops) == 1
        assert imaging_ops[0].estimated_disk_bytes == 0

    def test_fluidics_time_default(self):
        """Test fluidics time estimated from default timing when no inline protocol is provided."""
        protocol = ExperimentProtocol(
            name="Fluidics Time Test",
            imaging_protocols={
                "minimal": ImagingProtocol(channels=["DAPI"]),
            },
            rounds=[
                Round(
                    name="Round 1",
                    steps=[
                        FluidicsStep(protocol="incubate"),
                        ImagingStep(protocol="minimal"),
                    ],
                ),
            ],
        )

        validator = ProtocolValidator(
            available_fluidics_protocols={"incubate"},
            timing_estimates={"fluidics_protocol_default_seconds": 600.0},
        )
        summary = validator.validate(protocol, fov_count=1)

        fluidics_ops = [op for op in summary.operation_estimates if op.operation_type == "fluidics"]
        assert len(fluidics_ops) == 1
        # Should estimate ~600s for the default fluidics protocol duration
        assert fluidics_ops[0].estimated_seconds >= 600

    def test_validate_missing_fluidics_protocol(self):
        """Test validation error for missing fluidics protocol reference."""
        protocol = ExperimentProtocol(
            name="Missing Fluidics",
            imaging_protocols={
                "standard": ImagingProtocol(channels=["DAPI"]),
            },
            rounds=[
                Round(
                    name="Round 1",
                    steps=[
                        FluidicsStep(protocol="nonexistent_protocol"),
                        ImagingStep(protocol="standard"),
                    ],
                ),
            ],
        )

        validator = ProtocolValidator(available_fluidics_protocols={"other"})
        summary = validator.validate(protocol, fov_count=1)

        assert summary.has_errors
        assert any("nonexistent_protocol" in err for err in summary.errors)

    def test_validate_missing_imaging_config(self):
        """Test validation error for missing imaging protocol reference."""
        protocol = ExperimentProtocol(
            name="Missing Config",
            imaging_protocols={
                "standard": ImagingProtocol(channels=["DAPI"]),
            },
            rounds=[
                Round(
                    name="Round 1",
                    steps=[
                        ImagingStep(protocol="nonexistent_config"),
                    ],
                ),
            ],
        )

        validator = ProtocolValidator()
        summary = validator.validate(protocol, fov_count=1)

        assert summary.has_errors
        assert any("nonexistent_config" in err for err in summary.errors)

    def test_inline_fluidics_protocols_disallowed(self):
        """Test validation error when inline fluidics protocols are provided."""
        protocol = ExperimentProtocol(
            name="Inline Fluidics",
            fluidics_protocols={
                "wash": {"steps": [{"operation": "wash"}]},
            },
            imaging_protocols={
                "standard": ImagingProtocol(channels=["DAPI"]),
            },
            rounds=[
                Round(
                    name="Round 1",
                    steps=[FluidicsStep(protocol="wash")],
                )
            ],
        )

        validator = ProtocolValidator()
        summary = validator.validate(protocol, fov_count=1)

        assert summary.has_errors
        assert any("Inline fluidics_protocols" in err for err in summary.errors)

    def test_validate_warns_long_experiment(self):
        """Test warning for experiments > 24 hours."""
        # Create a protocol with long incubation to exceed 24h
        protocol = ExperimentProtocol(
            name="Long Protocol",
            imaging_protocols={
                "standard": ImagingProtocol(
                    channels=["DAPI", "Cy3", "Cy5"],
                    z_stack=ZStackConfig(planes=5),
                ),
            },
            rounds=[
                Round(
                    name=f"Round {i}",
                    steps=[
                        FluidicsStep(protocol="long_incubate"),
                        ImagingStep(protocol="standard"),
                    ],
                )
                for i in range(3)  # 3 rounds x 12h = 36 hours
            ],
        )

        validator = ProtocolValidator(
            available_fluidics_protocols={"long_incubate"},
            timing_estimates={"fluidics_protocol_default_seconds": 43200},
        )
        summary = validator.validate(protocol, fov_count=10)

        # Should have a warning about long experiment (>24 hours)
        assert summary.has_warnings
        assert any("hours" in w for w in summary.warnings)

    def test_custom_timing_estimates(self):
        """Test using custom timing estimates."""
        custom_timing = {
            "stage_move_seconds": 1.0,  # Slower stage
            "autofocus_seconds": 5.0,  # Slower autofocus
        }

        protocol = ExperimentProtocol(
            name="Test",
            imaging_protocols={
                "with_af": ImagingProtocol(
                    channels=["DAPI"],
                    focus=FocusConfig(enabled=True, method="contrast"),
                ),
            },
            rounds=[
                Round(
                    name="Round 1",
                    steps=[ImagingStep(protocol="with_af")],
                ),
            ],
        )

        default_validator = ProtocolValidator()
        custom_validator = ProtocolValidator(timing_estimates=custom_timing)

        default_summary = default_validator.validate(protocol, fov_count=10)
        custom_summary = custom_validator.validate(protocol, fov_count=10)

        # Custom should be slower
        assert custom_summary.total_estimated_seconds > default_summary.total_estimated_seconds

    def test_custom_camera_resolution(self):
        """Test disk estimation with custom camera resolution."""
        protocol = ExperimentProtocol(
            name="Test",
            imaging_protocols={
                "standard": ImagingProtocol(channels=["DAPI"]),
            },
            rounds=[
                Round(
                    name="Round 1",
                    steps=[ImagingStep(protocol="standard")],
                ),
            ],
        )

        small_camera = ProtocolValidator(camera_resolution=(1024, 1024))
        large_camera = ProtocolValidator(camera_resolution=(4096, 4096))

        small_summary = small_camera.validate(protocol, fov_count=10)
        large_summary = large_camera.validate(protocol, fov_count=10)

        # Larger camera should mean more disk usage
        assert large_summary.total_disk_bytes > small_summary.total_disk_bytes

    def test_format_time(self):
        """Test time formatting helper."""
        validator = ProtocolValidator()

        assert validator.estimate_time_formatted(30) == "30s"
        assert validator.estimate_time_formatted(90) == "1m 30s"
        assert validator.estimate_time_formatted(3661) == "1h 1m"
        assert validator.estimate_time_formatted(7200) == "2h 0m"

    def test_format_disk(self):
        """Test disk formatting helper."""
        validator = ProtocolValidator()

        assert validator.estimate_disk_formatted(500) == "500 B"
        assert validator.estimate_disk_formatted(1024 * 500) == "500.0 KB"
        assert validator.estimate_disk_formatted(1024**2 * 500) == "500.0 MB"
        assert validator.estimate_disk_formatted(1024**3 * 2) == "2.0 GB"


class TestDefaultEstimates:
    """Tests for default timing and disk estimates."""

    def test_default_timing_estimates_exist(self):
        """Test that default timing estimates are defined."""
        assert "stage_move_seconds" in DEFAULT_TIMING_ESTIMATES
        assert "autofocus_seconds" in DEFAULT_TIMING_ESTIMATES

    def test_default_disk_estimates_exist(self):
        """Test that default disk estimates are defined."""
        assert "bytes_per_pixel_mono" in DEFAULT_DISK_ESTIMATES
        assert "compression_ratio" in DEFAULT_DISK_ESTIMATES
