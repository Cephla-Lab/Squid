"""
E2E tests for injected failure scenarios.
"""

from __future__ import annotations

import pytest

from squid.core.events import AcquisitionWorkerFinished
from tests.harness import BackendContext
from tests.harness.core.fault_injection import CameraFaultInjector
from tests.harness.simulators import AcquisitionSimulator
from tests.e2e.harness import OrchestratorSimulator


@pytest.mark.e2e
@pytest.mark.imaging
class TestAcquisitionFaults:
    """End-to-end tests for acquisition fault handling."""

    def test_camera_fault_aborts_acquisition(
        self,
        e2e_acquisition_sim: AcquisitionSimulator,
        e2e_backend_ctx: BackendContext,
    ):
        """Inject a camera fault and ensure acquisition fails."""
        sim = e2e_acquisition_sim
        center = e2e_backend_ctx.get_stage_center()
        injector = CameraFaultInjector(e2e_backend_ctx)

        sim.add_grid_region("region_1", center=center, n_x=2, n_y=2)

        channels = e2e_backend_ctx.get_available_channels()
        if channels:
            sim.set_channels([channels[0]])

        sim.set_zstack(n_z=1)
        sim.set_skip_saving(True)

        injector.fail_after(1)
        try:
            sim.monitor.clear()
            exp_id = sim.start()
            finish_event = sim.monitor.wait_for(
                AcquisitionWorkerFinished,
                timeout_s=30,
                predicate=lambda e: e.experiment_id.startswith(exp_id),
            )
            assert finish_event is not None, "Acquisition did not finish after fault"
            assert not finish_event.success, "Acquisition succeeded despite camera fault"
        finally:
            injector.reset()


@pytest.mark.e2e
@pytest.mark.orchestrator
class TestOrchestratorFaults:
    """End-to-end tests for orchestrator fault handling."""

    def test_camera_fault_fails_orchestrator(
        self,
        e2e_orchestrator: OrchestratorSimulator,
        e2e_backend_ctx: BackendContext,
        single_round_imaging_protocol: str,
    ):
        """Inject a camera fault during orchestrated imaging."""
        sim = e2e_orchestrator
        center = e2e_backend_ctx.get_stage_center()
        injector = CameraFaultInjector(e2e_backend_ctx)

        sim.load_protocol(single_round_imaging_protocol)
        sim.add_grid_region("region_1", center=center, n_x=2, n_y=2)

        injector.fail_after(1)
        try:
            result = sim.run_and_wait(timeout_s=60)
        finally:
            injector.reset()

        assert not result.success
        assert result.final_state in ("FAILED", "ABORTED")
        assert result.error_events or result.error is not None
