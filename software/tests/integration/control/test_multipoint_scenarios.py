"""
Integration tests for multipoint acquisition scenarios.

These tests simulate realistic acquisition workflows using the simulated microscope.
They verify the complete flow from controller to worker, including:
- Stage movements
- Camera triggering
- Z-stack execution
- Autofocus integration
- Progress tracking
- Abort handling
- Multiple timepoints
"""

import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock

import numpy as np
import pytest

import _def
import squid.backend.microscope as microscope
from squid.backend.controllers.multipoint import MultiPointController
from squid.backend.controllers.multipoint.multi_point_utils import (
    AcquisitionParameters,
    ScanPositionInformation,
)
from squid.core.events import (
    event_bus,
    AcquisitionStarted,
    AcquisitionProgress,
    AcquisitionWorkerProgress,
    AcquisitionWorkerFinished,
    AcquisitionFinished,
    AcquisitionStateChanged,
    AcquisitionCoordinates,
    CurrentFOVRegistered,
    PlateViewUpdate,
    PlateViewInit,
)

import tests.control.test_stubs as ts


@dataclass
class AcquisitionEventCollector:
    """Collects and tracks acquisition events for test assertions."""

    started_event: threading.Event = field(default_factory=threading.Event)
    finished_event: threading.Event = field(default_factory=threading.Event)
    worker_finished_event: threading.Event = field(default_factory=threading.Event)

    # Progress tracking
    progress_updates: List[AcquisitionProgress] = field(default_factory=list)
    worker_progress_updates: List[AcquisitionWorkerProgress] = field(default_factory=list)
    coordinate_updates: List[AcquisitionCoordinates] = field(default_factory=list)
    fov_registrations: List[CurrentFOVRegistered] = field(default_factory=list)
    state_changes: List[AcquisitionStateChanged] = field(default_factory=list)

    # Plate view events
    plate_view_inits: List[PlateViewInit] = field(default_factory=list)
    plate_view_updates: List[PlateViewUpdate] = field(default_factory=list)

    # Final result
    finished_result: Optional[AcquisitionFinished] = None
    worker_finished_result: Optional[AcquisitionWorkerFinished] = None

    def __post_init__(self):
        event_bus.start()
        event_bus.subscribe(AcquisitionStarted, self._on_started)
        event_bus.subscribe(AcquisitionFinished, self._on_finished)
        event_bus.subscribe(AcquisitionWorkerFinished, self._on_worker_finished)
        event_bus.subscribe(AcquisitionProgress, self._on_progress)
        event_bus.subscribe(AcquisitionWorkerProgress, self._on_worker_progress)
        event_bus.subscribe(AcquisitionCoordinates, self._on_coordinates)
        event_bus.subscribe(CurrentFOVRegistered, self._on_fov_registered)
        event_bus.subscribe(AcquisitionStateChanged, self._on_state_changed)
        event_bus.subscribe(PlateViewInit, self._on_plate_view_init)
        event_bus.subscribe(PlateViewUpdate, self._on_plate_view_update)

    def _on_started(self, evt):
        self.started_event.set()

    def _on_finished(self, evt):
        self.finished_result = evt
        self.finished_event.set()

    def _on_worker_finished(self, evt):
        self.worker_finished_result = evt
        self.worker_finished_event.set()

    def _on_progress(self, evt):
        self.progress_updates.append(evt)

    def _on_worker_progress(self, evt):
        self.worker_progress_updates.append(evt)

    def _on_coordinates(self, evt):
        self.coordinate_updates.append(evt)

    def _on_fov_registered(self, evt):
        self.fov_registrations.append(evt)

    def _on_state_changed(self, evt):
        self.state_changes.append(evt)

    def _on_plate_view_init(self, evt):
        self.plate_view_inits.append(evt)

    def _on_plate_view_update(self, evt):
        self.plate_view_updates.append(evt)

    @property
    def total_images_captured(self) -> int:
        return len(self.coordinate_updates)

    def wait_for_completion(self, timeout_s: float = 10.0) -> bool:
        """Wait for acquisition to complete. Returns True if completed, False on timeout."""
        return self.worker_finished_event.wait(timeout_s)


def _get_valid_coordinates(mpc: MultiPointController):
    """Get coordinates that are within the stage limits."""
    stage = mpc.stage
    cfg = stage.get_config()

    x_center = (cfg.X_AXIS.MIN_POSITION + cfg.X_AXIS.MAX_POSITION) / 2.0
    y_center = (cfg.Y_AXIS.MIN_POSITION + cfg.Y_AXIS.MAX_POSITION) / 2.0
    z_mid = (cfg.Z_AXIS.MIN_POSITION + cfg.Z_AXIS.MAX_POSITION) / 2.0

    return x_center, y_center, z_mid


def _select_channels(mpc: MultiPointController, count: int = 1):
    """Select first N channels for acquisition."""
    objective = mpc.objectiveStore.current_objective
    all_configs = [
        c.name for c in mpc.channelConfigurationManager.get_configurations(objective)
    ]
    mpc.set_selected_configurations(all_configs[:count])
    return all_configs[:count]


class TestBasicAcquisitionScenarios:
    """Test basic acquisition workflows."""

    def test_single_fov_single_channel(self):
        """Simplest case: 1 FOV, 1 channel, no z-stack, no timelapse."""
        _def.MERGE_CHANNELS = False
        scope = microscope.Microscope.build_from_global_config(True)
        mpc = ts.get_test_multi_point_controller(microscope=scope)
        collector = AcquisitionEventCollector()

        x, y, z = _get_valid_coordinates(mpc)
        mpc.scanCoordinates.add_single_fov_region("single", x, y, z)
        _select_channels(mpc, count=1)

        mpc.set_NZ(1)
        mpc.set_Nt(1)

        expected_images = mpc.get_acquisition_image_count()
        assert expected_images == 1

        mpc.run_acquisition()

        assert collector.wait_for_completion(timeout_s=10)
        assert collector.total_images_captured == expected_images
        assert collector.started_event.is_set()

    def test_single_fov_multi_channel(self):
        """1 FOV, 3 channels - verifies channel switching."""
        _def.MERGE_CHANNELS = False
        scope = microscope.Microscope.build_from_global_config(True)
        mpc = ts.get_test_multi_point_controller(microscope=scope)
        collector = AcquisitionEventCollector()

        x, y, z = _get_valid_coordinates(mpc)
        mpc.scanCoordinates.add_single_fov_region("multi_channel", x, y, z)
        channels = _select_channels(mpc, count=3)

        mpc.set_NZ(1)
        mpc.set_Nt(1)

        expected_images = len(channels)  # 1 FOV × 3 channels
        assert mpc.get_acquisition_image_count() == expected_images

        mpc.run_acquisition()

        assert collector.wait_for_completion(timeout_s=10)
        assert collector.total_images_captured == expected_images

    def test_multi_fov_grid(self):
        """3x3 FOV grid - verifies stage movement pattern."""
        _def.MERGE_CHANNELS = False
        scope = microscope.Microscope.build_from_global_config(True)
        mpc = ts.get_test_multi_point_controller(microscope=scope)
        collector = AcquisitionEventCollector()

        x, y, z = _get_valid_coordinates(mpc)
        # 3x3 grid = 9 FOVs
        mpc.scanCoordinates.add_flexible_region("grid", x, y, z, 3, 3, 0)
        _select_channels(mpc, count=1)

        mpc.set_NZ(1)
        mpc.set_Nt(1)

        expected_images = 9  # 9 FOVs × 1 channel
        assert mpc.get_acquisition_image_count() == expected_images

        mpc.run_acquisition()

        assert collector.wait_for_completion(timeout_s=15)
        assert collector.total_images_captured == expected_images
        assert len(collector.fov_registrations) == 9


class TestZStackScenarios:
    """Test z-stack acquisition patterns."""

    def test_basic_zstack_from_bottom(self):
        """Z-stack with 5 levels from bottom."""
        _def.MERGE_CHANNELS = False
        scope = microscope.Microscope.build_from_global_config(True)
        mpc = ts.get_test_multi_point_controller(microscope=scope)
        collector = AcquisitionEventCollector()

        x, y, z = _get_valid_coordinates(mpc)
        mpc.scanCoordinates.add_single_fov_region("zstack", x, y, z)
        _select_channels(mpc, count=1)

        nz = 5
        delta_z = 2.0  # 2 μm steps
        mpc.set_NZ(nz)
        mpc.set_deltaZ(delta_z)
        mpc.set_z_stacking_config(0)  # FROM BOTTOM
        mpc.set_Nt(1)

        expected_images = nz  # 5 z-levels × 1 channel
        assert mpc.get_acquisition_image_count() == expected_images

        mpc.run_acquisition()

        assert collector.wait_for_completion(timeout_s=15)
        assert collector.total_images_captured == expected_images

    def test_zstack_from_center(self):
        """Z-stack from center position (symmetric around focus)."""
        _def.MERGE_CHANNELS = False
        scope = microscope.Microscope.build_from_global_config(True)
        mpc = ts.get_test_multi_point_controller(microscope=scope)
        collector = AcquisitionEventCollector()

        x, y, z = _get_valid_coordinates(mpc)
        mpc.scanCoordinates.add_single_fov_region("zstack_center", x, y, z)
        _select_channels(mpc, count=1)

        nz = 5
        mpc.set_NZ(nz)
        mpc.set_deltaZ(1.0)
        mpc.set_z_stacking_config(1)  # FROM CENTER
        mpc.set_Nt(1)

        mpc.run_acquisition()

        assert collector.wait_for_completion(timeout_s=15)
        assert collector.total_images_captured == nz

    def test_zstack_multi_channel(self):
        """Z-stack with multiple channels per z-level."""
        _def.MERGE_CHANNELS = False
        scope = microscope.Microscope.build_from_global_config(True)
        mpc = ts.get_test_multi_point_controller(microscope=scope)
        collector = AcquisitionEventCollector()

        x, y, z = _get_valid_coordinates(mpc)
        mpc.scanCoordinates.add_single_fov_region("zstack_multi", x, y, z)
        channels = _select_channels(mpc, count=2)

        nz = 3
        mpc.set_NZ(nz)
        mpc.set_deltaZ(2.0)
        mpc.set_Nt(1)

        expected_images = nz * len(channels)  # 3 z-levels × 2 channels
        assert mpc.get_acquisition_image_count() == expected_images

        mpc.run_acquisition()

        assert collector.wait_for_completion(timeout_s=15)
        assert collector.total_images_captured == expected_images


class TestTimeLapseScenarios:
    """Test time-lapse acquisition patterns."""

    def test_timelapse_short_interval(self):
        """Time-lapse with 3 timepoints, 0.5s interval."""
        _def.MERGE_CHANNELS = False
        scope = microscope.Microscope.build_from_global_config(True)
        mpc = ts.get_test_multi_point_controller(microscope=scope)
        collector = AcquisitionEventCollector()

        x, y, z = _get_valid_coordinates(mpc)
        mpc.scanCoordinates.add_single_fov_region("timelapse", x, y, z)
        _select_channels(mpc, count=1)

        nt = 3
        dt = 0.5  # 500ms between timepoints
        mpc.set_NZ(1)
        mpc.set_Nt(nt)
        mpc.set_deltat(dt)

        expected_images = nt
        assert mpc.get_acquisition_image_count() == expected_images

        start_time = time.time()
        mpc.run_acquisition()

        assert collector.wait_for_completion(timeout_s=15)
        elapsed = time.time() - start_time

        # Should take at least (nt-1) * dt seconds for intervals
        assert elapsed >= (nt - 1) * dt * 0.8  # 80% tolerance for timing
        assert collector.total_images_captured == expected_images

    def test_timelapse_with_zstack(self):
        """Time-lapse with z-stack at each timepoint."""
        _def.MERGE_CHANNELS = False
        scope = microscope.Microscope.build_from_global_config(True)
        mpc = ts.get_test_multi_point_controller(microscope=scope)
        collector = AcquisitionEventCollector()

        x, y, z = _get_valid_coordinates(mpc)
        mpc.scanCoordinates.add_single_fov_region("timelapse_z", x, y, z)
        _select_channels(mpc, count=1)

        nt = 2
        nz = 3
        mpc.set_Nt(nt)
        mpc.set_NZ(nz)
        mpc.set_deltaZ(1.0)
        mpc.set_deltat(0.1)

        expected_images = nt * nz  # 2 timepoints × 3 z-levels
        assert mpc.get_acquisition_image_count() == expected_images

        mpc.run_acquisition()

        assert collector.wait_for_completion(timeout_s=15)
        assert collector.total_images_captured == expected_images


class TestAutofocusScenarios:
    """Test autofocus integration during acquisition."""

    def test_acquisition_with_contrast_af(self):
        """Acquisition with contrast-based autofocus."""
        _def.MERGE_CHANNELS = False
        scope = microscope.Microscope.build_from_global_config(True)
        mpc = ts.get_test_multi_point_controller(microscope=scope)
        collector = AcquisitionEventCollector()

        x, y, z = _get_valid_coordinates(mpc)
        # Multiple FOVs to test AF between positions
        mpc.scanCoordinates.add_single_fov_region("af1", x, y, z)
        mpc.scanCoordinates.add_single_fov_region("af2", x + 0.5, y + 0.5, z)
        _select_channels(mpc, count=1)

        mpc.set_NZ(1)
        mpc.set_Nt(1)
        mpc.set_af_flag(True)  # Enable contrast AF

        mpc.run_acquisition()

        assert collector.wait_for_completion(timeout_s=15)
        assert collector.total_images_captured == 2

    def test_acquisition_with_laser_af(self):
        """Acquisition with reflection/laser autofocus."""
        _def.MERGE_CHANNELS = False
        _def.SUPPORT_LASER_AUTOFOCUS = True
        scope = microscope.Microscope.build_from_global_config(True)
        mpc = ts.get_test_multi_point_controller(microscope=scope)
        collector = AcquisitionEventCollector()

        x, y, z = _get_valid_coordinates(mpc)
        mpc.scanCoordinates.add_single_fov_region("laser_af", x, y, z)
        _select_channels(mpc, count=1)

        mpc.set_NZ(1)
        mpc.set_Nt(1)
        mpc.set_reflection_af_flag(True)

        # Set up fake laser AF reference
        scope.addons.camera_focus.send_trigger()
        ref_image = scope.addons.camera_focus.read_frame()
        if ref_image is not None:
            mpc.laserAutoFocusController.laser_af_properties.set_reference_image(ref_image)

        mpc.run_acquisition()

        assert collector.wait_for_completion(timeout_s=15)
        assert collector.total_images_captured == 1


class TestAbortScenarios:
    """Test abort handling during acquisition."""

    def test_abort_during_acquisition(self):
        """Abort mid-acquisition should stop cleanly."""
        _def.MERGE_CHANNELS = False
        scope = microscope.Microscope.build_from_global_config(True)
        mpc = ts.get_test_multi_point_controller(microscope=scope)
        collector = AcquisitionEventCollector()

        x, y, z = _get_valid_coordinates(mpc)
        # Large grid to give time to abort
        mpc.scanCoordinates.add_flexible_region("abort_grid", x, y, z, 5, 5, 0)
        _select_channels(mpc, count=2)

        mpc.set_NZ(3)
        mpc.set_Nt(1)

        mpc.run_acquisition()

        # Wait for acquisition to start
        assert collector.started_event.wait(timeout=5)

        # Wait a bit then abort
        time.sleep(0.3)
        mpc.request_abort_aquisition()

        # Should complete (via abort) within reasonable time
        assert collector.wait_for_completion(timeout_s=10)

        # Should have captured fewer images than expected
        expected_total = 5 * 5 * 2 * 3  # 25 FOVs × 2 channels × 3 z-levels
        assert collector.total_images_captured < expected_total

    def test_abort_before_start(self):
        """Abort before acquisition starts should be handled gracefully."""
        _def.MERGE_CHANNELS = False
        scope = microscope.Microscope.build_from_global_config(True)
        mpc = ts.get_test_multi_point_controller(microscope=scope)

        x, y, z = _get_valid_coordinates(mpc)
        mpc.scanCoordinates.add_single_fov_region("pre_abort", x, y, z)
        _select_channels(mpc, count=1)

        # Request abort before starting - should not crash
        mpc.request_abort_aquisition()

        # Starting after abort request should still work (fresh start)
        collector = AcquisitionEventCollector()
        mpc.run_acquisition()

        assert collector.wait_for_completion(timeout_s=10)


class TestMultiRegionScenarios:
    """Test multi-region (multi-well) acquisition patterns."""

    def test_multiple_single_fov_regions(self):
        """Multiple regions, each with a single FOV."""
        _def.MERGE_CHANNELS = False
        scope = microscope.Microscope.build_from_global_config(True)
        mpc = ts.get_test_multi_point_controller(microscope=scope)
        collector = AcquisitionEventCollector()

        x, y, z = _get_valid_coordinates(mpc)

        # Simulate 4 wells
        for i in range(4):
            mpc.scanCoordinates.add_single_fov_region(
                f"well_{i}", x + i * 0.5, y + i * 0.5, z
            )

        _select_channels(mpc, count=1)
        mpc.set_NZ(1)
        mpc.set_Nt(1)

        expected_images = 4
        assert mpc.get_acquisition_image_count() == expected_images

        mpc.run_acquisition()

        assert collector.wait_for_completion(timeout_s=15)
        assert collector.total_images_captured == expected_images
        assert len(collector.fov_registrations) == 4

    def test_mixed_region_sizes(self):
        """Regions with different FOV counts."""
        _def.MERGE_CHANNELS = False
        scope = microscope.Microscope.build_from_global_config(True)
        mpc = ts.get_test_multi_point_controller(microscope=scope)
        collector = AcquisitionEventCollector()

        x, y, z = _get_valid_coordinates(mpc)

        # 1 FOV region
        mpc.scanCoordinates.add_single_fov_region("single", x, y, z)
        # 2x2 = 4 FOV region
        mpc.scanCoordinates.add_flexible_region("small_grid", x + 2, y + 2, z, 2, 2, 0)
        # 3x3 = 9 FOV region
        mpc.scanCoordinates.add_flexible_region("large_grid", x + 5, y + 5, z, 3, 3, 0)

        _select_channels(mpc, count=1)
        mpc.set_NZ(1)
        mpc.set_Nt(1)

        expected_images = 1 + 4 + 9  # 14 total FOVs
        assert mpc.get_acquisition_image_count() == expected_images

        mpc.run_acquisition()

        assert collector.wait_for_completion(timeout_s=20)
        assert collector.total_images_captured == expected_images


class TestProgressTrackingScenarios:
    """Test progress event publishing."""

    def test_progress_events_published(self):
        """Verify progress events are published during acquisition."""
        _def.MERGE_CHANNELS = False
        scope = microscope.Microscope.build_from_global_config(True)
        mpc = ts.get_test_multi_point_controller(microscope=scope)
        collector = AcquisitionEventCollector()

        x, y, z = _get_valid_coordinates(mpc)
        mpc.scanCoordinates.add_flexible_region("progress_test", x, y, z, 3, 3, 0)
        _select_channels(mpc, count=2)

        mpc.set_NZ(2)
        mpc.set_Nt(1)

        mpc.run_acquisition()

        assert collector.wait_for_completion(timeout_s=20)

        # Should have received progress updates
        assert len(collector.progress_updates) > 0
        assert len(collector.worker_progress_updates) > 0

        # Check progress monotonically increases (or stays same)
        for i in range(1, len(collector.progress_updates)):
            prev = collector.progress_updates[i - 1]
            curr = collector.progress_updates[i]
            assert curr.current_fov >= prev.current_fov

    def test_state_transitions(self):
        """Verify state machine transitions during acquisition."""
        _def.MERGE_CHANNELS = False
        scope = microscope.Microscope.build_from_global_config(True)
        mpc = ts.get_test_multi_point_controller(microscope=scope)
        collector = AcquisitionEventCollector()

        x, y, z = _get_valid_coordinates(mpc)
        mpc.scanCoordinates.add_single_fov_region("state_test", x, y, z)
        _select_channels(mpc, count=1)

        mpc.run_acquisition()

        assert collector.wait_for_completion(timeout_s=10)

        # Should have state change events
        assert len(collector.state_changes) > 0


class TestComplexAcquisitionScenarios:
    """Test complex, real-world-like acquisition patterns."""

    def test_full_wellplate_simulation(self):
        """Simulate a partial wellplate scan: 6 wells × 2x2 FOVs × 2 channels × 3 z-levels."""
        _def.MERGE_CHANNELS = False
        scope = microscope.Microscope.build_from_global_config(True)
        mpc = ts.get_test_multi_point_controller(microscope=scope)
        collector = AcquisitionEventCollector()

        x, y, z = _get_valid_coordinates(mpc)

        # 6 wells in a 2x3 pattern
        for row in range(2):
            for col in range(3):
                well_x = x + col * 2.0
                well_y = y + row * 2.0
                mpc.scanCoordinates.add_flexible_region(
                    f"well_{row}_{col}", well_x, well_y, z, 2, 2, 0
                )

        channels = _select_channels(mpc, count=2)
        nz = 3
        mpc.set_NZ(nz)
        mpc.set_deltaZ(1.0)
        mpc.set_Nt(1)

        # 6 wells × 4 FOVs × 2 channels × 3 z-levels = 144 images
        expected_images = 6 * 4 * len(channels) * nz
        assert mpc.get_acquisition_image_count() == expected_images

        mpc.run_acquisition()

        assert collector.wait_for_completion(timeout_s=60)
        assert collector.total_images_captured == expected_images

    def test_timelapse_multiwell_zstack(self):
        """Time-lapse with multiple wells and z-stacks - comprehensive test."""
        _def.MERGE_CHANNELS = False
        scope = microscope.Microscope.build_from_global_config(True)
        mpc = ts.get_test_multi_point_controller(microscope=scope)
        collector = AcquisitionEventCollector()

        x, y, z = _get_valid_coordinates(mpc)

        # 2 wells
        mpc.scanCoordinates.add_single_fov_region("well_A1", x, y, z)
        mpc.scanCoordinates.add_single_fov_region("well_A2", x + 1, y, z)

        channels = _select_channels(mpc, count=2)
        nt = 2
        nz = 2
        mpc.set_Nt(nt)
        mpc.set_NZ(nz)
        mpc.set_deltaZ(1.0)
        mpc.set_deltat(0.1)  # Fast interval for testing

        # 2 timepoints × 2 wells × 2 channels × 2 z-levels = 16 images
        expected_images = nt * 2 * len(channels) * nz
        assert mpc.get_acquisition_image_count() == expected_images

        mpc.run_acquisition()

        assert collector.wait_for_completion(timeout_s=30)
        assert collector.total_images_captured == expected_images


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_single_z_level(self):
        """NZ=1 should work correctly (no z-stack stepping)."""
        _def.MERGE_CHANNELS = False
        scope = microscope.Microscope.build_from_global_config(True)
        mpc = ts.get_test_multi_point_controller(microscope=scope)
        collector = AcquisitionEventCollector()

        x, y, z = _get_valid_coordinates(mpc)
        mpc.scanCoordinates.add_single_fov_region("nz1", x, y, z)
        _select_channels(mpc, count=1)

        mpc.set_NZ(1)
        mpc.set_Nt(1)

        mpc.run_acquisition()

        assert collector.wait_for_completion(timeout_s=10)
        assert collector.total_images_captured == 1

    def test_single_timepoint(self):
        """Nt=1 should work correctly (no timelapse waiting)."""
        _def.MERGE_CHANNELS = False
        scope = microscope.Microscope.build_from_global_config(True)
        mpc = ts.get_test_multi_point_controller(microscope=scope)
        collector = AcquisitionEventCollector()

        x, y, z = _get_valid_coordinates(mpc)
        mpc.scanCoordinates.add_single_fov_region("nt1", x, y, z)
        _select_channels(mpc, count=1)

        mpc.set_NZ(1)
        mpc.set_Nt(1)

        mpc.run_acquisition()

        assert collector.wait_for_completion(timeout_s=10)
        assert collector.total_images_captured == 1

    def test_empty_scan_coordinates(self):
        """No scan coordinates should complete quickly with 0 images."""
        _def.MERGE_CHANNELS = False
        scope = microscope.Microscope.build_from_global_config(True)
        mpc = ts.get_test_multi_point_controller(microscope=scope)
        collector = AcquisitionEventCollector()

        mpc.scanCoordinates.clear_regions()
        _select_channels(mpc, count=1)

        assert mpc.get_acquisition_image_count() == 0

        mpc.run_acquisition()

        assert collector.wait_for_completion(timeout_s=5)
        assert collector.total_images_captured == 0

    def test_large_z_range(self):
        """Large z-stack (20 levels) should complete successfully."""
        _def.MERGE_CHANNELS = False
        scope = microscope.Microscope.build_from_global_config(True)
        mpc = ts.get_test_multi_point_controller(microscope=scope)
        collector = AcquisitionEventCollector()

        x, y, z = _get_valid_coordinates(mpc)
        mpc.scanCoordinates.add_single_fov_region("large_z", x, y, z)
        _select_channels(mpc, count=1)

        nz = 20
        mpc.set_NZ(nz)
        mpc.set_deltaZ(0.5)  # 0.5 μm steps
        mpc.set_Nt(1)

        mpc.run_acquisition()

        assert collector.wait_for_completion(timeout_s=30)
        assert collector.total_images_captured == nz
