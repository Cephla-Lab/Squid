"""
Backend Test Harness Framework.

This package provides tools for programmatically testing backend features
via EventBus commands, simulating GUI interactions without loading the actual GUI.

Usage:
    from tests.harness import BackendContext, AcquisitionSimulator

    with BackendContext() as ctx:
        sim = AcquisitionSimulator(ctx)
        sim.add_single_fov("test", x=10, y=10, z=1)
        sim.set_channels(["DAPI"])
        result = sim.run_and_wait()
        assert result.success
"""

from tests.harness.core.backend_context import BackendContext
from tests.harness.core.event_monitor import EventMonitor
from tests.harness.simulators.acquisition import AcquisitionSimulator, AcquisitionResult

__all__ = [
    "BackendContext",
    "EventMonitor",
    "AcquisitionSimulator",
    "AcquisitionResult",
]
