"""
Pytest fixtures for backend testing.

These fixtures provide easy setup for tests using the backend test harness.

Usage:
    def test_my_acquisition(acquisition_sim, stage_center):
        x, y, z = stage_center
        acquisition_sim.add_single_fov("test", x, y, z)
        acquisition_sim.set_channels(["DAPI"])
        result = acquisition_sim.run_and_wait()
        assert result.success
"""

from __future__ import annotations

from typing import Dict, Generator, List, Tuple

import pytest

from tests.harness.core.backend_context import BackendContext
from tests.harness.simulators.acquisition import AcquisitionSimulator


@pytest.fixture
def backend_ctx() -> Generator[BackendContext, None, None]:
    """
    Provides a simulated backend context for tests.

    Yields:
        BackendContext instance with simulated microscope and services
    """
    with BackendContext(simulation=True) as ctx:
        yield ctx


@pytest.fixture
def acquisition_sim(backend_ctx: BackendContext) -> AcquisitionSimulator:
    """
    Provides an acquisition simulator.

    Args:
        backend_ctx: BackendContext fixture

    Returns:
        AcquisitionSimulator instance
    """
    return AcquisitionSimulator(backend_ctx)


@pytest.fixture
def bus_only_acquisition_sim(backend_ctx: BackendContext) -> AcquisitionSimulator:
    """
    Provides an acquisition simulator that uses EventBus commands only.

    Args:
        backend_ctx: BackendContext fixture

    Returns:
        AcquisitionSimulator instance (bus_only=True)
    """
    return AcquisitionSimulator(backend_ctx, bus_only=True)


@pytest.fixture
def stage_limits(backend_ctx: BackendContext) -> Dict[str, Tuple[float, float]]:
    """
    Provides stage movement limits.

    Args:
        backend_ctx: BackendContext fixture

    Returns:
        Dict with keys 'x', 'y', 'z' mapping to (min, max) tuples
    """
    return backend_ctx.get_stage_limits()


@pytest.fixture
def stage_center(backend_ctx: BackendContext) -> Tuple[float, float, float]:
    """
    Provides the center position of the stage.

    Args:
        backend_ctx: BackendContext fixture

    Returns:
        (x, y, z) center position in mm
    """
    return backend_ctx.get_stage_center()


@pytest.fixture
def available_channels(backend_ctx: BackendContext) -> List[str]:
    """
    Provides list of available channel names.

    Args:
        backend_ctx: BackendContext fixture

    Returns:
        List of channel configuration names
    """
    return backend_ctx.get_available_channels()
