"""
E2E tests for resource file path auto-loading.

Verifies that protocols with file-path protocol references,
fluidics_protocols_file, fluidics_config_file, and fov_file correctly
auto-load resources at experiment start.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from squid.core.protocol import ProtocolLoader
from tests.harness import BackendContext
from tests.e2e.harness import OrchestratorSimulator


@pytest.mark.e2e
@pytest.mark.orchestrator
class TestResourceFilePathsLoading:
    """Tests that resource file path fields load correctly at protocol load time."""

    def test_imaging_protocols_resolved_from_file_paths(
        self, resource_file_paths_full_protocol: str
    ):
        """Verify imaging protocols are resolved from file-path step references."""
        loader = ProtocolLoader()
        protocol = loader.load(resource_file_paths_full_protocol)

        # Protocols referenced by file path in steps should be resolved
        assert len(protocol.imaging_protocols) >= 2

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

    def test_fov_file_resolves_without_alias(
        self, resource_file_paths_full_protocol: str
    ):
        """Verify fov_file remains the only run-level FOV resource."""
        loader = ProtocolLoader()
        protocol = loader.load(resource_file_paths_full_protocol)

        assert protocol.fov_file is not None
        assert Path(protocol.fov_file).exists()

    def test_all_resource_files_exist(
        self, resource_file_paths_full_protocol: str
    ):
        """Verify all referenced resource files actually exist on disk."""
        loader = ProtocolLoader()
        protocol = loader.load(resource_file_paths_full_protocol)

        assert Path(protocol.fluidics_protocols_file).exists()
        assert Path(protocol.fluidics_config_file).exists()
        assert Path(protocol.fov_file).exists()

    def test_imaging_only_protocols_resolved(
        self, resource_file_paths_imaging_only_protocol: str
    ):
        """Verify file-path imaging protocols are resolved in imaging-only config."""
        loader = ProtocolLoader()
        protocol = loader.load(resource_file_paths_imaging_only_protocol)

        # bf_quick and fluorescence_405_fast referenced by file path
        assert len(protocol.imaging_protocols) >= 2


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
        - file-path imaging protocol references in step protocol fields
        - fluidics_protocols_file: external fluidics protocols (auto-loaded)
        - fluidics_config_file: fluidics hardware config (validated)
        - fov_file: run-level FOV positions CSV
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
        """Run experiment with file-path imaging protocol references.

        The protocol references bf_quick.yaml and fluorescence_405_fast.yaml
        by file path. This verifies the resolved protocols run correctly.
        """
        sim = e2e_orchestrator
        sim.load_protocol(resource_file_paths_imaging_only_protocol)

        result = sim.run_and_wait(timeout_s=60)

        assert result.success, f"Experiment failed: {result.error}"
        assert result.completed_rounds == 2
        assert result.final_state == "COMPLETED"
