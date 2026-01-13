"""Unit tests for ProtocolValidator."""

import pytest

from squid.core.protocol import (
    ExperimentProtocol,
    FluidicsCommand,
    FluidicsStep,
    ImagingStep,
    Round,
    RoundType,
)
from squid.core.protocol.schema import (
    ImagingDefaults,
    ProtocolDefaults,
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
            total_disk_bytes=1024 ** 3 * 2,  # 2 GB
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
    """Tests for ProtocolValidator class."""

    @pytest.fixture
    def simple_protocol(self) -> ExperimentProtocol:
        """Create a simple test protocol."""
        return ExperimentProtocol(
            name="Test Protocol",
            version="1.0",
            rounds=[
                Round(
                    name="Round 1",
                    type=RoundType.IMAGING,
                    imaging=ImagingStep(
                        channels=["DAPI", "FITC"],
                        z_planes=5,
                    ),
                ),
                Round(
                    name="Wash",
                    type=RoundType.WASH,
                    fluidics=[
                        FluidicsStep(
                            command=FluidicsCommand.WASH,
                            solution="PBS",
                            volume_ul=500.0,
                        ),
                    ],
                ),
            ],
        )

    @pytest.fixture
    def complex_protocol(self) -> ExperimentProtocol:
        """Create a complex test protocol with defaults."""
        return ExperimentProtocol(
            name="Complex Protocol",
            version="1.0",
            defaults=ProtocolDefaults(
                imaging=ImagingDefaults(
                    channels=["DAPI", "Cy3", "Cy5"],
                    z_planes=3,
                ),
            ),
            rounds=[
                Round(
                    name="Round 1",
                    type=RoundType.IMAGING,
                    fluidics=[
                        FluidicsStep(
                            command=FluidicsCommand.FLOW,
                            solution="probe_mix_1",
                            volume_ul=100.0,
                            flow_rate_ul_per_min=50.0,
                        ),
                        FluidicsStep(
                            command=FluidicsCommand.INCUBATE,
                            duration_s=300.0,
                        ),
                    ],
                    imaging=ImagingStep(),  # Uses defaults
                ),
                Round(
                    name="Wash",
                    type=RoundType.WASH,
                    fluidics=[
                        FluidicsStep(
                            command=FluidicsCommand.WASH,
                            solution="PBS",
                            volume_ul=500.0,
                        ),
                    ],
                ),
                Round(
                    name="Round 2",
                    type=RoundType.IMAGING,
                    requires_intervention=True,
                    intervention_message="Change slide",
                    fluidics=[
                        FluidicsStep(
                            command=FluidicsCommand.FLOW,
                            solution="probe_mix_2",
                            volume_ul=100.0,
                            flow_rate_ul_per_min=50.0,
                        ),
                    ],
                    imaging=ImagingStep(
                        channels=["DAPI", "Cy3"],  # Override default channels
                    ),
                ),
            ],
        )

    def test_validate_simple_protocol(self, simple_protocol):
        """Test validating a simple protocol."""
        validator = ProtocolValidator()
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
        validator = ProtocolValidator(available_channels={"DAPI", "FITC", "Cy3", "Cy5"})
        summary = validator.validate(simple_protocol, fov_count=1)

        assert summary.valid is True

    def test_validate_complex_protocol(self, complex_protocol):
        """Test validating a complex protocol with fluidics and interventions."""
        validator = ProtocolValidator()
        summary = validator.validate(complex_protocol, fov_count=20)

        assert summary.valid is True
        assert summary.total_rounds == 3

        # Should have multiple operation estimates
        assert len(summary.operation_estimates) > 3

        # Should have imaging estimates
        imaging_ops = [op for op in summary.operation_estimates if op.operation_type == "imaging"]
        assert len(imaging_ops) == 2  # Round 1 and Round 2

        # Should have fluidics estimates
        fluidics_ops = [op for op in summary.operation_estimates if op.operation_type == "fluidics"]
        assert len(fluidics_ops) == 3  # Round 1, Wash, Round 2

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
            rounds=[
                Round(
                    name="Preview",
                    imaging=ImagingStep(
                        channels=["DAPI"],
                        skip_saving=True,
                    ),
                ),
            ],
        )

        validator = ProtocolValidator()
        summary = validator.validate(protocol, fov_count=100)

        # Find imaging operation
        imaging_ops = [op for op in summary.operation_estimates if op.operation_type == "imaging"]
        assert len(imaging_ops) == 1
        assert imaging_ops[0].estimated_disk_bytes == 0

    def test_fluidics_time_from_volume_and_rate(self):
        """Test fluidics time calculated from volume and flow rate."""
        protocol = ExperimentProtocol(
            name="Fluidics Protocol",
            rounds=[
                Round(
                    name="Flow",
                    fluidics=[
                        FluidicsStep(
                            command=FluidicsCommand.FLOW,
                            solution="buffer",
                            volume_ul=1000.0,  # 1 mL
                            flow_rate_ul_per_min=100.0,  # 100 uL/min
                        ),
                    ],
                ),
            ],
        )

        validator = ProtocolValidator()
        summary = validator.validate(protocol, fov_count=1)

        # 1000 uL at 100 uL/min = 10 minutes = 600 seconds
        fluidics_ops = [op for op in summary.operation_estimates if op.operation_type == "fluidics"]
        assert len(fluidics_ops) == 1
        assert fluidics_ops[0].estimated_seconds == 600.0

    def test_incubate_time_from_duration(self):
        """Test incubate time uses duration_s directly."""
        protocol = ExperimentProtocol(
            name="Incubate Protocol",
            rounds=[
                Round(
                    name="Incubate",
                    fluidics=[
                        FluidicsStep(
                            command=FluidicsCommand.INCUBATE,
                            duration_s=300.0,  # 5 minutes
                        ),
                    ],
                ),
            ],
        )

        validator = ProtocolValidator()
        summary = validator.validate(protocol, fov_count=1)

        fluidics_ops = [op for op in summary.operation_estimates if op.operation_type == "fluidics"]
        assert len(fluidics_ops) == 1
        assert fluidics_ops[0].estimated_seconds == 300.0

    def test_validate_fluidics_missing_volume(self):
        """Test validation error for FLOW without volume."""
        protocol = ExperimentProtocol(
            name="Bad Fluidics",
            rounds=[
                Round(
                    name="Flow",
                    fluidics=[
                        FluidicsStep(
                            command=FluidicsCommand.FLOW,
                            solution="buffer",
                            # Missing volume_ul
                        ),
                    ],
                ),
            ],
        )

        validator = ProtocolValidator()
        summary = validator.validate(protocol, fov_count=1)

        assert summary.valid is False
        assert any("volume_ul" in err for err in summary.errors)

    def test_validate_fluidics_missing_duration(self):
        """Test validation error for INCUBATE without duration."""
        protocol = ExperimentProtocol(
            name="Bad Incubate",
            rounds=[
                Round(
                    name="Incubate",
                    fluidics=[
                        FluidicsStep(
                            command=FluidicsCommand.INCUBATE,
                            # Missing duration_s
                        ),
                    ],
                ),
            ],
        )

        validator = ProtocolValidator()
        summary = validator.validate(protocol, fov_count=1)

        assert summary.valid is False
        assert any("duration_s" in err for err in summary.errors)

    def test_validate_warns_long_experiment(self):
        """Test warning for experiments > 24 hours."""
        # Create a protocol that will estimate > 24 hours
        rounds = []
        for i in range(100):
            rounds.append(
                Round(
                    name=f"Round {i}",
                    fluidics=[
                        FluidicsStep(
                            command=FluidicsCommand.INCUBATE,
                            duration_s=1800.0,  # 30 min each
                        ),
                    ],
                )
            )

        protocol = ExperimentProtocol(
            name="Long Protocol",
            rounds=rounds,
        )

        validator = ProtocolValidator()
        summary = validator.validate(protocol, fov_count=1)

        # Should have a warning about long experiment
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
            rounds=[
                Round(
                    name="Round 1",
                    imaging=ImagingStep(
                        channels=["DAPI"],
                        use_autofocus=True,
                    ),
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
            rounds=[
                Round(
                    name="Round 1",
                    imaging=ImagingStep(channels=["DAPI"]),
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
        assert validator.estimate_disk_formatted(1024 ** 2 * 500) == "500.0 MB"
        assert validator.estimate_disk_formatted(1024 ** 3 * 2) == "2.0 GB"


class TestDefaultEstimates:
    """Tests for default timing and disk estimates."""

    def test_default_timing_estimates_exist(self):
        """Test that default timing estimates are defined."""
        assert "stage_move_seconds" in DEFAULT_TIMING_ESTIMATES
        assert "autofocus_seconds" in DEFAULT_TIMING_ESTIMATES
        assert "fluidics_per_step_seconds" in DEFAULT_TIMING_ESTIMATES

    def test_default_disk_estimates_exist(self):
        """Test that default disk estimates are defined."""
        assert "bytes_per_pixel_mono" in DEFAULT_DISK_ESTIMATES
        assert "compression_ratio" in DEFAULT_DISK_ESTIMATES
