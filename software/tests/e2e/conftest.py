"""
E2E test fixtures and configuration.

This module provides pytest fixtures for end-to-end testing of the Squid
microscope control software. All fixtures use simulation mode and do not
require real hardware.
"""

from __future__ import annotations

from pathlib import Path
from typing import Generator

import pytest

from tests.harness import BackendContext
from tests.harness.simulators import AcquisitionSimulator
from tests.e2e.harness.orchestrator_simulator import OrchestratorSimulator


# Path to e2e configuration files
E2E_CONFIG_DIR = Path(__file__).parent / "configs"


# =============================================================================
# Pytest Configuration
# =============================================================================


def pytest_configure(config):
    """Register custom markers for e2e tests."""
    config.addinivalue_line(
        "markers",
        "e2e: End-to-end integration tests (may be slow)"
    )
    config.addinivalue_line(
        "markers",
        "orchestrator: Tests for orchestrator workflows"
    )
    config.addinivalue_line(
        "markers",
        "checkpoint: Tests for checkpoint/recovery functionality"
    )
    config.addinivalue_line(
        "markers",
        "imaging: Tests for imaging workflows"
    )
    config.addinivalue_line(
        "markers",
        "fluidics: Tests for fluidics workflows"
    )


# =============================================================================
# Path Fixtures
# =============================================================================


@pytest.fixture
def e2e_config_dir() -> Path:
    """Provide path to e2e configuration directory."""
    return E2E_CONFIG_DIR


@pytest.fixture
def protocols_dir(e2e_config_dir: Path) -> Path:
    """Provide path to protocols directory."""
    return e2e_config_dir / "protocols"


@pytest.fixture
def fluidics_config_dir(e2e_config_dir: Path) -> Path:
    """Provide path to fluidics config directory."""
    return e2e_config_dir / "fluidics"


# =============================================================================
# Backend Context Fixtures
# =============================================================================


@pytest.fixture
def e2e_backend_ctx(tmp_path) -> Generator[BackendContext, None, None]:
    """
    Provide a BackendContext configured for e2e testing.

    Uses a temporary directory for experiment output.
    """
    with BackendContext(simulation=True, base_path=str(tmp_path)) as ctx:
        yield ctx


# =============================================================================
# Simulator Fixtures
# =============================================================================


@pytest.fixture
def e2e_acquisition_sim(e2e_backend_ctx: BackendContext) -> Generator[AcquisitionSimulator, None, None]:
    """Provide an AcquisitionSimulator for imaging e2e tests."""
    sim = AcquisitionSimulator(e2e_backend_ctx)
    yield sim


@pytest.fixture
def e2e_orchestrator(e2e_backend_ctx: BackendContext) -> Generator[OrchestratorSimulator, None, None]:
    """Provide an OrchestratorSimulator for orchestrator e2e tests."""
    sim = OrchestratorSimulator(e2e_backend_ctx)
    yield sim
    sim.cleanup()


# =============================================================================
# Protocol Fixtures
# =============================================================================


@pytest.fixture
def single_round_imaging_protocol(protocols_dir: Path) -> str:
    """Path to single round imaging protocol."""
    return str(protocols_dir / "single_round_imaging.yaml")


@pytest.fixture
def tiled_zstack_protocol(protocols_dir: Path) -> str:
    """Path to tiled z-stack imaging protocol."""
    return str(protocols_dir / "tiled_zstack.yaml")


@pytest.fixture
def multi_round_fish_protocol(protocols_dir: Path) -> str:
    """Path to multi-round FISH protocol."""
    return str(protocols_dir / "multi_round_fish.yaml")


@pytest.fixture
def fluidics_only_protocol(protocols_dir: Path) -> str:
    """Path to fluidics-only protocol."""
    return str(protocols_dir / "fluidics_only.yaml")


@pytest.fixture
def intervention_protocol(protocols_dir: Path) -> str:
    """Path to intervention protocol."""
    return str(protocols_dir / "intervention_protocol.yaml")


@pytest.fixture
def v2_full_protocol(protocols_dir: Path) -> str:
    """Path to V2 full-featured protocol."""
    return str(protocols_dir / "v2_full.yaml")


@pytest.fixture
def resource_file_paths_full_protocol(protocols_dir: Path) -> str:
    """Path to protocol using all resource file path fields."""
    return str(protocols_dir / "resource_file_paths_full.yaml")


@pytest.fixture
def resource_file_paths_imaging_only_protocol(protocols_dir: Path) -> str:
    """Path to protocol using imaging_protocol_file with inline override."""
    return str(protocols_dir / "resource_file_paths_imaging_only.yaml")


# =============================================================================
# Fluidics Config Fixtures
# =============================================================================


@pytest.fixture
def simulation_fluidics_config(fluidics_config_dir: Path) -> str:
    """Path to simulation fluidics configuration."""
    return str(fluidics_config_dir / "simulation_fluidics.json")


# =============================================================================
# Helper Fixtures
# =============================================================================


@pytest.fixture
def stage_center(e2e_backend_ctx: BackendContext) -> tuple:
    """Get the stage center position."""
    return e2e_backend_ctx.get_stage_center()


@pytest.fixture
def available_channels(e2e_backend_ctx: BackendContext) -> list:
    """Get list of available channel names."""
    return e2e_backend_ctx.get_available_channels()


@pytest.fixture
def experiment_output_dir(tmp_path) -> Path:
    """Provide a temporary directory for experiment output."""
    output_dir = tmp_path / "e2e_output"
    output_dir.mkdir()
    return output_dir
