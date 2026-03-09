"""
E2E tests for resource file path auto-loading.

Verifies that protocols with imaging_protocol_file, fluidics_protocols_file,
fluidics_config_file, and fov_file correctly auto-load resources at
experiment start.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from squid.core.events import AutofocusMode
from squid.core.protocol import ProtocolLoader
from tests.harness import BackendContext
from tests.e2e.harness import OrchestratorSimulator


@pytest.mark.e2e
@pytest.mark.orchestrator
class TestResourceFilePathsLoading:
    """Tests that resource file path fields load correctly at protocol load time."""

    def test_imaging_protocol_file_resolves_to_absolute(
        self, resource_file_paths_full_protocol: str
    ):
        """Verify imaging_protocol_file is resolved to absolute path."""
        loader = ProtocolLoader()
        protocol = loader.load(resource_file_paths_full_protocol)

        assert protocol.imaging_protocol_file is not None
        assert Path(protocol.imaging_protocol_file).is_absolute()

    def test_fluidics_protocols_file_resolves_to_absolute(
        self, resource_file_paths_full_protocol: str
    ):
        """Verify fluidics_protocols_file is resolved to absolute path."""
        loader = ProtocolLoader()
        protocol = loader.load(resource_file_paths_full_protocol)

        assert protocol.fluidics_protocols_file is not None
        assert Path(protocol.fluidics_protocols_file).is_absolute()

    def test_fluidics_config_file_resolves_to_absolute(
        self, resource_file_paths_full_protocol: str
    ):
        """Verify fluidics_config_file is resolved to absolute path."""
        loader = ProtocolLoader()
        protocol = loader.load(resource_file_paths_full_protocol)

        assert protocol.fluidics_config_file is not None
        assert Path(protocol.fluidics_config_file).is_absolute()

    def test_fov_file_resolves_to_absolute(
        self, resource_file_paths_full_protocol: str
    ):
        """Verify fov_file is resolved to absolute path."""
        loader = ProtocolLoader()
        protocol = loader.load(resource_file_paths_full_protocol)

        assert protocol.fov_file is not None
        assert Path(protocol.fov_file).is_absolute()

    def test_imaging_protocols_merged_from_file(
        self, resource_file_paths_full_protocol: str
    ):
        """Verify imaging protocols from external file are merged in."""
        loader = ProtocolLoader()
        protocol = loader.load(resource_file_paths_full_protocol)

        # These come from the external imaging_protocols.yaml
        assert "bf_quick" in protocol.imaging_protocols
        assert "fluorescence_405" in protocol.imaging_protocols
        assert "fluorescence_488_561" in protocol.imaging_protocols

    def test_fov_file_creates_default_fov_set(
        self, resource_file_paths_full_protocol: str
    ):
        """Verify fov_file is added to fov_sets as 'default'."""
        loader = ProtocolLoader()
        protocol = loader.load(resource_file_paths_full_protocol)

        assert "default" in protocol.fov_sets
        assert Path(protocol.fov_sets["default"]).exists()

    def test_imaging_protocol_file_inline_takes_precedence(
        self, resource_file_paths_imaging_only_protocol: str
    ):
        """Verify inline imaging_protocols override external file definitions."""
        loader = ProtocolLoader()
        protocol = loader.load(resource_file_paths_imaging_only_protocol)

        # fluorescence_405 is defined in both the file (3 z-planes) and inline (1 z-plane)
        # Inline should win
        assert protocol.imaging_protocols["fluorescence_405"].z_stack.planes == 1
        assert (
            protocol.imaging_protocols["fluorescence_405"].focus.mode
            == AutofocusMode.NONE
        )

        # bf_quick comes only from the file and should still be present
        assert "bf_quick" in protocol.imaging_protocols

    def test_all_resource_files_exist(
        self, resource_file_paths_full_protocol: str
    ):
        """Verify all referenced resource files actually exist on disk."""
        loader = ProtocolLoader()
        protocol = loader.load(resource_file_paths_full_protocol)

        assert Path(protocol.imaging_protocol_file).exists()
        assert Path(protocol.fluidics_protocols_file).exists()
        assert Path(protocol.fluidics_config_file).exists()
        assert Path(protocol.fov_file).exists()


@pytest.mark.e2e
@pytest.mark.orchestrator
class TestResourceFilePathsE2E:
    """E2E tests that run experiments using resource file path protocols."""

    def test_full_resource_paths_experiment(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        resource_file_paths_full_protocol: str,
    ):
        """Run a full experiment using all resource file path fields.

        This protocol uses:
        - imaging_protocol_file: external imaging protocol definitions
        - fluidics_protocols_file: external fluidics protocols (auto-loaded)
        - fluidics_config_file: fluidics hardware config (validated)
        - fov_file: FOV positions CSV (auto-loaded as 'default' fov_set)
        """
        sim = e2e_orchestrator

        # Load protocol — no manual FOV setup needed since fov_file is specified
        sim.load_protocol(resource_file_paths_full_protocol)

        # Run experiment
        result = sim.run_and_wait(timeout_s=60)

        assert result.success, f"Experiment failed: {result.error}"
        assert result.completed_rounds == 2
        assert result.final_state == "COMPLETED"

    def test_imaging_only_resource_paths_experiment(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        resource_file_paths_imaging_only_protocol: str,
    ):
        """Run experiment with imaging_protocol_file and inline override.

        The external file defines bf_quick, fluorescence_405, fluorescence_488_561.
        The protocol overrides fluorescence_405 inline (1 z-plane, no focus).
        This verifies the merged protocol runs correctly end-to-end.
        """
        sim = e2e_orchestrator
        sim.load_protocol(resource_file_paths_imaging_only_protocol)

        result = sim.run_and_wait(timeout_s=60)

        assert result.success, f"Experiment failed: {result.error}"
        assert result.completed_rounds == 2
        assert result.final_state == "COMPLETED"
