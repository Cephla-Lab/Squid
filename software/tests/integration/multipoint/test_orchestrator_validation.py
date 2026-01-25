"""
Integration tests for Orchestrator Protocol Validation.

Tests the protocol validation flow from command to completion event.
"""

from __future__ import annotations

import pytest

from tests.harness import BackendContext
from squid.backend.controllers.orchestrator.validation import (
    ValidationSummary,
)
from squid.backend.controllers.orchestrator.protocol_validator import ProtocolValidator
from squid.core.protocol.schema import ExperimentProtocol


def build_protocol(protocol_dict: dict) -> ExperimentProtocol:
    """Build a protocol from a dict using the current schema."""
    if "name" not in protocol_dict:
        metadata = protocol_dict.get("metadata")
        if isinstance(metadata, dict) and "name" in metadata:
            protocol_dict = dict(protocol_dict)
            protocol_dict["name"] = metadata["name"]
            if "description" not in protocol_dict:
                protocol_dict["description"] = metadata.get("description", "")
    return ExperimentProtocol.model_validate(protocol_dict)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def backend_ctx():
    """Provide a simulated backend context."""
    with BackendContext(simulation=True) as ctx:
        yield ctx


@pytest.fixture
def sample_protocol_path(tmp_path):
    """Create a sample protocol file for testing."""
    protocol_content = """
version: "2.0"
name: "Test Protocol"
description: "A simple test protocol"

imaging_configs:
  standard:
    channels: ["BF"]
    z_stack:
      planes: 3
      step_um: 0.5
    focus:
      enabled: false

rounds:
  - name: "Round 1"
    steps:
      - step_type: imaging
        config: standard
"""
    protocol_path = tmp_path / "test_protocol.yaml"
    protocol_path.write_text(protocol_content)
    return str(protocol_path)


@pytest.fixture
def invalid_channel_protocol_path(tmp_path):
    """Create a protocol with invalid channel names."""
    protocol_content = """
version: "2.0"
name: "Invalid Channel Protocol"
description: "Protocol with invalid channels"

imaging_configs:
  standard:
    channels: ["NONEXISTENT_CHANNEL_XYZ"]
    z_stack:
      planes: 1

rounds:
  - name: "Round 1"
    steps:
      - step_type: imaging
        config: standard
"""
    protocol_path = tmp_path / "invalid_channel_protocol.yaml"
    protocol_path.write_text(protocol_content)
    return str(protocol_path)


# =============================================================================
# ProtocolValidator Unit Integration Tests
# =============================================================================


class TestProtocolValidatorIntegration:
    """Integration tests for ProtocolValidator with real channel config."""

    def test_validator_with_channel_manager(self, backend_ctx: BackendContext):
        """Verify validator can be created from channel manager."""
        channel_manager = backend_ctx.channel_config_manager

        validator = ProtocolValidator.from_channel_manager(channel_manager)

        # Should have available channels
        assert validator._available_channels is not None
        # Should include at least some channels
        available_channels = backend_ctx.get_available_channels()
        assert len(available_channels) > 0

    def test_validate_with_real_channels(self, backend_ctx: BackendContext):
        """Verify validation with real available channels."""
        # Get real available channels
        available_channels = set(backend_ctx.get_available_channels())

        # Create validator with real channels
        validator = ProtocolValidator(available_channels=available_channels)

        # Create a protocol with valid channels
        if available_channels:
            valid_channel = list(available_channels)[0]

            # Build minimal protocol dict
            protocol_dict = {
                "name": "Test",
                "version": "2.0",
                "description": "",
                "imaging_configs": {
                    "standard": {
                        "channels": [valid_channel],
                        "z_stack": {"planes": 1},
                        "focus": {"enabled": False},
                    }
                },
                "rounds": [
                    {
                        "name": "Round 1",
                        "steps": [{"step_type": "imaging", "config": "standard"}],
                    }
                ],
            }

            protocol = build_protocol(protocol_dict)
            summary = validator.validate(protocol, fov_count=1)

            assert summary.valid, f"Protocol should be valid, got errors: {summary.errors}"
            assert summary.total_rounds == 1

    def test_validate_detects_invalid_channels(self, backend_ctx: BackendContext):
        """Verify validation detects channels not in channel manager."""
        # Get real available channels
        available_channels = set(backend_ctx.get_available_channels())

        # Create validator with real channels
        validator = ProtocolValidator(available_channels=available_channels)

        # Create a protocol with invalid channel
        protocol_dict = {
            "name": "Test",
            "version": "2.0",
            "description": "",
            "imaging_configs": {
                "standard": {
                    "channels": ["THIS_CHANNEL_DOES_NOT_EXIST"],
                    "z_stack": {"planes": 1},
                    "focus": {"enabled": False},
                }
            },
            "rounds": [
                {
                    "name": "Round 1",
                    "steps": [{"step_type": "imaging", "config": "standard"}],
                }
            ],
        }

        protocol = build_protocol(protocol_dict)
        summary = validator.validate(protocol, fov_count=1)

        # Should have warnings or errors about the invalid channel
        all_messages = list(summary.warnings) + list(summary.errors)
        channel_issue_found = any(
            "THIS_CHANNEL_DOES_NOT_EXIST" in msg or "channel" in msg.lower()
            for msg in all_messages
        )
        assert channel_issue_found, "Should detect invalid channel"


# =============================================================================
# Time and Disk Estimation Tests
# =============================================================================


class TestValidationEstimates:
    """Tests for time and disk space estimation."""

    def test_time_estimate_scales_with_fov_count(self, backend_ctx: BackendContext):
        """Verify time estimates scale with FOV count."""
        validator = ProtocolValidator()

        protocol_dict = {
            "name": "Test",
            "version": "2.0",
            "description": "",
            "imaging_configs": {
                "standard": {
                    "channels": ["BF"],
                    "z_stack": {"planes": 5},
                    "focus": {"enabled": False},
                }
            },
            "rounds": [
                {
                    "name": "Round 1",
                    "steps": [{"step_type": "imaging", "config": "standard"}],
                }
            ],
        }

        protocol = build_protocol(protocol_dict)

        # Validate with 1 FOV
        summary_1 = validator.validate(protocol, fov_count=1)

        # Validate with 10 FOVs
        summary_10 = validator.validate(protocol, fov_count=10)

        # Time should scale approximately with FOV count
        assert summary_10.total_estimated_seconds > summary_1.total_estimated_seconds

    def test_disk_estimate_scales_with_images(self, backend_ctx: BackendContext):
        """Verify disk estimates scale with total image count."""
        validator = ProtocolValidator(camera_resolution=(2048, 2048))

        protocol_dict = {
            "name": "Test",
            "version": "2.0",
            "description": "",
            "imaging_configs": {
                "standard": {
                    "channels": ["BF"],
                    "z_stack": {"planes": 1},
                    "focus": {"enabled": False},
                }
            },
            "rounds": [
                {
                    "name": "Round 1",
                    "steps": [{"step_type": "imaging", "config": "standard"}],
                }
            ],
        }

        protocol = build_protocol(protocol_dict)

        # Validate with 1 FOV
        summary_1 = validator.validate(protocol, fov_count=1)

        # Validate with 10 FOVs
        summary_10 = validator.validate(protocol, fov_count=10)

        # Disk should scale with FOV count
        assert summary_10.total_disk_bytes > summary_1.total_disk_bytes

    def test_multi_round_protocol_estimates(self, backend_ctx: BackendContext):
        """Verify estimates for multi-round protocols."""
        validator = ProtocolValidator()

        protocol_dict = {
            "name": "Test",
            "version": "2.0",
            "description": "",
            "imaging_configs": {
                "bf": {
                    "channels": ["BF"],
                    "z_stack": {"planes": 3},
                    "focus": {"enabled": False},
                },
                "dapi": {
                    "channels": ["DAPI"],
                    "z_stack": {"planes": 3},
                    "focus": {"enabled": False},
                },
                "gfp": {
                    "channels": ["GFP"],
                    "z_stack": {"planes": 3},
                    "focus": {"enabled": False},
                },
            },
            "rounds": [
                {
                    "name": "Round 1",
                    "steps": [{"step_type": "imaging", "config": "bf"}],
                },
                {
                    "name": "Round 2",
                    "steps": [{"step_type": "imaging", "config": "dapi"}],
                },
                {
                    "name": "Round 3",
                    "steps": [{"step_type": "imaging", "config": "gfp"}],
                },
            ],
        }

        protocol = build_protocol(protocol_dict)
        summary = validator.validate(protocol, fov_count=5)

        assert summary.total_rounds == 3
        assert len(summary.operation_estimates) >= 3


# =============================================================================
# Validation Summary Tests
# =============================================================================


class TestValidationSummary:
    """Tests for ValidationSummary dataclass."""

    def test_summary_has_errors_property(self):
        """Verify has_errors property works correctly."""
        # Summary with no errors
        summary_valid = ValidationSummary(
            protocol_name="Test",
            total_rounds=1,
            total_estimated_seconds=10.0,
            total_disk_bytes=1000,
            operation_estimates=(),
            errors=(),
            warnings=(),
            valid=True,
        )
        assert not summary_valid.has_errors

        # Summary with errors
        summary_invalid = ValidationSummary(
            protocol_name="Test",
            total_rounds=1,
            total_estimated_seconds=10.0,
            total_disk_bytes=1000,
            operation_estimates=(),
            errors=("Error 1", "Error 2"),
            warnings=(),
            valid=False,
        )
        assert summary_invalid.has_errors

    def test_summary_has_warnings_property(self):
        """Verify has_warnings property works correctly."""
        # Summary with no warnings
        summary_no_warn = ValidationSummary(
            protocol_name="Test",
            total_rounds=1,
            total_estimated_seconds=10.0,
            total_disk_bytes=1000,
            operation_estimates=(),
            errors=(),
            warnings=(),
            valid=True,
        )
        assert not summary_no_warn.has_warnings

        # Summary with warnings
        summary_warn = ValidationSummary(
            protocol_name="Test",
            total_rounds=1,
            total_estimated_seconds=10.0,
            total_disk_bytes=1000,
            operation_estimates=(),
            errors=(),
            warnings=("Warning 1",),
            valid=True,
        )
        assert summary_warn.has_warnings


# =============================================================================
# Operation Estimate Tests
# =============================================================================


class TestOperationEstimates:
    """Tests for operation-level estimates."""

    def test_imaging_operation_estimate(self, backend_ctx: BackendContext):
        """Verify imaging operation estimates are generated."""
        validator = ProtocolValidator()

        protocol_dict = {
            "name": "Test",
            "version": "2.0",
            "description": "",
            "imaging_configs": {
                "standard": {
                    "channels": ["BF", "DAPI"],
                    "z_stack": {"planes": 5},
                    "focus": {"enabled": False},
                }
            },
            "rounds": [
                {
                    "name": "Imaging Round",
                    "steps": [{"step_type": "imaging", "config": "standard"}],
                }
            ],
        }

        protocol = build_protocol(protocol_dict)
        summary = validator.validate(protocol, fov_count=10)

        # Find imaging operation estimate
        imaging_estimates = [
            e for e in summary.operation_estimates if e.operation_type == "imaging"
        ]
        assert len(imaging_estimates) > 0

        # Verify estimate has required fields
        estimate = imaging_estimates[0]
        assert estimate.round_name == "Imaging Round"
        assert estimate.estimated_seconds > 0
        assert estimate.estimated_disk_bytes > 0

    def test_wait_operation_estimate(self, backend_ctx: BackendContext):
        """Verify wait operation estimates are generated."""
        validator = ProtocolValidator()

        protocol_dict = {
            "name": "Test",
            "version": "2.0",
            "description": "",
            "fluidics_protocols": {
                "incubate_300": {
                    "steps": [
                        {
                            "operation": "incubate",
                            "duration_s": 300,
                        }
                    ]
                }
            },
            "rounds": [
                {
                    "name": "Wait Round",
                    "steps": [{"step_type": "fluidics", "protocol": "incubate_300"}],
                }
            ],
        }

        protocol = build_protocol(protocol_dict)
        summary = validator.validate(protocol, fov_count=1)

        # Find fluidics operation estimate
        fluidics_estimates = [
            e for e in summary.operation_estimates if e.operation_type == "fluidics"
        ]
        assert len(fluidics_estimates) > 0

        # Verify incubate time is included
        estimate = fluidics_estimates[0]
        assert estimate.estimated_seconds >= 300

    def test_intervention_operation_estimate(self, backend_ctx: BackendContext):
        """Verify intervention operation estimates show as indefinite."""
        validator = ProtocolValidator()

        protocol_dict = {
            "name": "Test",
            "version": "2.0",
            "description": "",
            "rounds": [
                {
                    "name": "Intervention Round",
                    "steps": [
                        {
                            "step_type": "intervention",
                            "message": "Please replace the sample",
                        }
                    ],
                }
            ],
        }

        protocol = build_protocol(protocol_dict)
        summary = validator.validate(protocol, fov_count=1)

        # Find intervention operation estimate
        intervention_estimates = [
            e for e in summary.operation_estimates if e.operation_type == "intervention"
        ]
        assert len(intervention_estimates) > 0

        # Intervention should have some estimate (even if minimal)
        estimate = intervention_estimates[0]
        assert estimate.round_name == "Intervention Round"
