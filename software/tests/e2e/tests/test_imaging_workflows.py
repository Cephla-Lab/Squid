"""
E2E tests for imaging workflows.

Tests tiled imaging, z-stack acquisition, and multi-region workflows
using the AcquisitionSimulator.
"""

from __future__ import annotations

import pytest

from squid.core.events import AcquisitionStateChanged, AcquisitionWorkerFinished
from tests.harness.core.assertions import assert_progress_monotonic

from tests.harness import BackendContext
from tests.harness.simulators import AcquisitionSimulator


@pytest.mark.e2e
@pytest.mark.imaging
class TestTiledImaging:
    """End-to-end tests for tiled imaging workflows."""

    def test_single_fov_acquisition(
        self,
        e2e_acquisition_sim: AcquisitionSimulator,
        e2e_backend_ctx: BackendContext,
    ):
        """Test acquiring a single FOV."""
        sim = e2e_acquisition_sim
        center = e2e_backend_ctx.get_stage_center()

        # Add single FOV
        sim.add_single_fov("region_1", x=center[0], y=center[1], z=center[2])

        # Configure minimal acquisition
        channels = e2e_backend_ctx.get_available_channels()
        if channels:
            sim.set_channels([channels[0]])

        sim.set_zstack(n_z=1)
        sim.set_skip_saving(True)

        # Run acquisition
        result = sim.run_and_wait(timeout_s=30)

        assert result.success, f"Acquisition failed: {result.error}"
        assert result.total_fovs >= 1

    def test_3x3_grid_acquisition(
        self,
        e2e_acquisition_sim: AcquisitionSimulator,
        e2e_backend_ctx: BackendContext,
    ):
        """Test acquiring a 3x3 grid of FOVs."""
        sim = e2e_acquisition_sim
        center = e2e_backend_ctx.get_stage_center()

        # Add 3x3 grid
        sim.add_grid_region(
            "region_1",
            center=center,
            n_x=3,
            n_y=3,
            overlap_pct=10.0,
        )

        # Configure acquisition
        channels = e2e_backend_ctx.get_available_channels()
        if channels:
            sim.set_channels([channels[0]])

        sim.set_zstack(n_z=1)
        sim.set_skip_saving(True)

        # Run acquisition
        result = sim.run_and_wait(timeout_s=60)

        assert result.success, f"Acquisition failed: {result.error}"
        assert result.total_fovs >= 9, f"Expected at least 9 FOVs, got {result.total_fovs}"
        assert_progress_monotonic(sim.monitor)

    def test_multi_region_acquisition(
        self,
        e2e_acquisition_sim: AcquisitionSimulator,
        e2e_backend_ctx: BackendContext,
    ):
        """Test acquiring multiple separate regions."""
        sim = e2e_acquisition_sim
        center = e2e_backend_ctx.get_stage_center()

        # Add two 2x2 grid regions
        sim.add_grid_region(
            "region_1",
            center=(center[0] - 1, center[1], center[2]),
            n_x=2,
            n_y=2,
        )
        sim.add_grid_region(
            "region_2",
            center=(center[0] + 1, center[1], center[2]),
            n_x=2,
            n_y=2,
        )

        # Configure
        channels = e2e_backend_ctx.get_available_channels()
        if channels:
            sim.set_channels([channels[0]])

        sim.set_zstack(n_z=1)
        sim.set_skip_saving(True)

        # Run
        result = sim.run_and_wait(timeout_s=60)

        assert result.success, f"Acquisition failed: {result.error}"
        assert result.total_fovs >= 8, f"Expected at least 8 FOVs (2 regions x 4), got {result.total_fovs}"


@pytest.mark.e2e
@pytest.mark.imaging
class TestZStackAcquisition:
    """End-to-end tests for z-stack acquisition workflows."""

    @pytest.mark.parametrize("mode", ["FROM BOTTOM", "FROM CENTER", "FROM TOP"])
    def test_5_plane_zstack_modes(
        self,
        e2e_acquisition_sim: AcquisitionSimulator,
        e2e_backend_ctx: BackendContext,
        mode: str,
    ):
        """Test 5-plane z-stack across supported modes."""
        sim = e2e_acquisition_sim
        center = e2e_backend_ctx.get_stage_center()

        sim.add_single_fov("region_1", x=center[0], y=center[1], z=center[2])

        channels = e2e_backend_ctx.get_available_channels()
        if channels:
            sim.set_channels([channels[0]])

        sim.set_zstack(n_z=5, delta_z_um=1.0, mode=mode)
        sim.set_skip_saving(True)

        result = sim.run_and_wait(timeout_s=30)

        assert result.success, f"Acquisition failed: {result.error}"
        assert_progress_monotonic(sim.monitor)

    def test_multichannel_zstack(
        self,
        e2e_acquisition_sim: AcquisitionSimulator,
        e2e_backend_ctx: BackendContext,
    ):
        """Test z-stack with multiple channels."""
        sim = e2e_acquisition_sim
        center = e2e_backend_ctx.get_stage_center()

        sim.add_single_fov("region_1", x=center[0], y=center[1], z=center[2])

        channels = e2e_backend_ctx.get_available_channels()
        if len(channels) >= 2:
            sim.set_channels(channels[:2])
        elif channels:
            sim.set_channels([channels[0]])

        sim.set_zstack(n_z=3, delta_z_um=1.5, mode="FROM BOTTOM")
        sim.set_skip_saving(True)

        result = sim.run_and_wait(timeout_s=30)

        assert result.success, f"Acquisition failed: {result.error}"

    def test_piezo_autofocus_zstack(
        self,
        e2e_acquisition_sim: AcquisitionSimulator,
        e2e_backend_ctx: BackendContext,
    ):
        """Test z-stack with piezo and autofocus enabled."""
        sim = e2e_acquisition_sim
        center = e2e_backend_ctx.get_stage_center()

        sim.add_single_fov("region_1", x=center[0], y=center[1], z=center[2])

        channels = e2e_backend_ctx.get_available_channels()
        if channels:
            sim.set_channels([channels[0]])

        sim.set_autofocus(contrast_af=True, laser_af=True)
        sim.set_zstack(n_z=3, delta_z_um=1.0, mode="FROM TOP", use_piezo=True)
        sim.set_skip_saving(True)

        result = sim.run_and_wait(timeout_s=30)

        assert result.success, f"Acquisition failed: {result.error}"


@pytest.mark.e2e
@pytest.mark.imaging
class TestGridWithZStack:
    """End-to-end tests for combined grid + z-stack workflows."""

    def test_2x2_grid_with_3_plane_zstack(
        self,
        e2e_acquisition_sim: AcquisitionSimulator,
        e2e_backend_ctx: BackendContext,
    ):
        """Test 2x2 grid with 3-plane z-stack."""
        sim = e2e_acquisition_sim
        center = e2e_backend_ctx.get_stage_center()

        sim.add_grid_region(
            "region_1",
            center=center,
            n_x=2,
            n_y=2,
            overlap_pct=10.0,
        )

        channels = e2e_backend_ctx.get_available_channels()
        if channels:
            sim.set_channels([channels[0]])

        sim.set_zstack(n_z=3, delta_z_um=1.0, mode="FROM CENTER")
        sim.set_skip_saving(True)

        result = sim.run_and_wait(timeout_s=60)

        assert result.success, f"Acquisition failed: {result.error}"
        assert result.total_fovs >= 4, f"Expected at least 4 FOVs, got {result.total_fovs}"


@pytest.mark.e2e
@pytest.mark.imaging
class TestTimeLapseAcquisition:
    """End-to-end tests for time-lapse workflows."""

    def test_3_timepoint_acquisition(
        self,
        e2e_acquisition_sim: AcquisitionSimulator,
        e2e_backend_ctx: BackendContext,
    ):
        """Test 3-timepoint acquisition."""
        sim = e2e_acquisition_sim
        center = e2e_backend_ctx.get_stage_center()

        sim.add_single_fov("region_1", x=center[0], y=center[1], z=center[2])

        channels = e2e_backend_ctx.get_available_channels()
        if channels:
            sim.set_channels([channels[0]])

        sim.set_zstack(n_z=1)
        sim.set_timelapse(n_t=3, delta_t_s=0.5)  # Short interval for testing
        sim.set_skip_saving(True)

        result = sim.run_and_wait(timeout_s=30)

        assert result.success, f"Acquisition failed: {result.error}"


@pytest.mark.e2e
@pytest.mark.imaging
class TestCoordinateModes:
    """End-to-end tests for alternate coordinate selection modes."""

    def test_select_wells_acquisition(
        self,
        e2e_acquisition_sim: AcquisitionSimulator,
        e2e_backend_ctx: BackendContext,
    ):
        """Test selecting wells for acquisition."""
        sim = e2e_acquisition_sim

        sim.select_wells(["A1", "B2"], scan_size_mm=1.0, overlap_pct=5.0)

        channels = e2e_backend_ctx.get_available_channels()
        if channels:
            sim.set_channels([channels[0]])

        sim.set_zstack(n_z=1)
        sim.set_skip_saving(True)

        result = sim.run_and_wait(timeout_s=30, xy_mode="Select Wells")

        assert result.success, f"Acquisition failed: {result.error}"
        assert result.total_fovs > 0

    def test_manual_roi_acquisition(
        self,
        e2e_acquisition_sim: AcquisitionSimulator,
        e2e_backend_ctx: BackendContext,
    ):
        """Test manual ROI scan coordinates."""
        sim = e2e_acquisition_sim
        center = e2e_backend_ctx.get_stage_center()

        half = 0.5
        shape = (
            (
                (center[0] - half, center[1] - half),
                (center[0] + half, center[1] - half),
                (center[0] + half, center[1] + half),
                (center[0] - half, center[1] + half),
            ),
        )
        sim.set_manual_scan(shapes_mm=shape, overlap_pct=10.0)

        channels = e2e_backend_ctx.get_available_channels()
        if channels:
            sim.set_channels([channels[0]])

        sim.set_zstack(n_z=1)
        sim.set_skip_saving(True)

        result = sim.run_and_wait(timeout_s=30, xy_mode="Manual")

        assert result.success, f"Acquisition failed: {result.error}"
        assert result.total_fovs > 0

    def test_load_coordinates_acquisition(
        self,
        e2e_acquisition_sim: AcquisitionSimulator,
        e2e_backend_ctx: BackendContext,
    ):
        """Test loading explicit coordinates."""
        sim = e2e_acquisition_sim
        center = e2e_backend_ctx.get_stage_center()

        sim.load_coordinates(
            {
                "region_1": [
                    (center[0], center[1], center[2]),
                    (center[0] + 0.2, center[1], center[2]),
                ],
            }
        )

        channels = e2e_backend_ctx.get_available_channels()
        if channels:
            sim.set_channels([channels[0]])

        sim.set_zstack(n_z=1)
        sim.set_skip_saving(True)

        result = sim.run_and_wait(timeout_s=30, xy_mode="Load Coordinates")

        assert result.success, f"Acquisition failed: {result.error}"
        assert result.total_fovs >= 2


@pytest.mark.e2e
@pytest.mark.imaging
class TestAcquisitionAbort:
    """End-to-end tests for acquisition abort handling."""

    def test_abort_during_acquisition(
        self,
        e2e_acquisition_sim: AcquisitionSimulator,
        e2e_backend_ctx: BackendContext,
    ):
        """Test aborting acquisition mid-execution."""
        import time

        sim = e2e_acquisition_sim
        center = e2e_backend_ctx.get_stage_center()

        # Set up a longer acquisition
        sim.add_grid_region(
            "region_1",
            center=center,
            n_x=3,
            n_y=3,
        )

        channels = e2e_backend_ctx.get_available_channels()
        if channels:
            sim.set_channels([channels[0]])

        sim.set_zstack(n_z=1)
        sim.set_skip_saving(True)

        # Start acquisition in background
        exp_id = sim.start()

        # Wait a bit then abort
        time.sleep(0.5)
        sim.stop()

        finish_event = sim.monitor.wait_for(
            AcquisitionWorkerFinished,
            timeout_s=30,
            predicate=lambda e: e.experiment_id.startswith(exp_id),
        )

        assert finish_event is not None, "Acquisition did not emit a finish event after abort"
        assert not finish_event.success, "Acquisition unexpectedly reported success after abort"

        state_changes = sim.monitor.get_events(AcquisitionStateChanged)
        assert state_changes, "Expected state change events during abort"
        assert not state_changes[-1].in_progress
