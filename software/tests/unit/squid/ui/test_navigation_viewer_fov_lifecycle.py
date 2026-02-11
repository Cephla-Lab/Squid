"""Tests for NavigationViewer FOV lifecycle with real EventBus.

Covers FOV registration/deregistration, CurrentFOVRegistered tolerance matching,
objective change overlay survival, acquisition red-to-blue flow, wellplate format
changes, and full integration with the ScanCoordinates backend manager.
"""

import pytest
import numpy as np
from unittest.mock import MagicMock

from squid.core.events import (
    EventBus,
    AcquisitionStateChanged,
    ClearScanCoordinatesCommand,
    CurrentFOVRegistered,
    ObjectiveChanged,
    ROIChanged,
    StageMovementStopped,
    WellplateFormatChanged,
    SelectedWellsChanged,
    SetWellSelectionScanCoordinatesCommand,
    SortScanCoordinatesCommand,
    BinningChanged,
)
from squid.backend.managers.scan_coordinates import (
    ScanCoordinates,
    AddScanCoordinateRegion,
    RemovedScanCoordinateRegion,
    ClearedScanCoordinates,
    FovCenter,
)
from squid.backend.managers.objective_store import ObjectiveStore
from squid.ui.widgets.display.navigation_viewer import NavigationViewer


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def event_bus():
    bus = EventBus()
    bus.start()
    yield bus
    bus.stop()


@pytest.fixture
def mock_objective_store():
    store = MagicMock(spec=ObjectiveStore)
    store.get_pixel_size_factor.return_value = 1.0
    return store


@pytest.fixture
def mock_camera():
    camera = MagicMock()
    camera.get_fov_size_mm.return_value = 1.0
    camera.get_fov_height_mm.return_value = 1.0
    camera.get_fov_width_mm.return_value = 1.0
    return camera


@pytest.fixture
def mock_stage():
    stage = MagicMock()
    pos = MagicMock()
    pos.x_mm = 20.0
    pos.y_mm = 20.0
    pos.z_mm = 0.0
    stage.get_pos.return_value = pos
    return stage


@pytest.fixture
def viewer(qtbot, monkeypatch, mock_objective_store, mock_camera, event_bus):
    """Create NavigationViewer with monkeypatched cv2.imread for headless testing."""
    # Monkeypatch cv2.imread to avoid loading real image files
    monkeypatch.setattr(
        "squid.ui.widgets.display.navigation_viewer.cv2.imread",
        lambda _path, *args, **kwargs: np.zeros((100, 150, 3), dtype=np.uint8),
    )
    # Monkeypatch os.path.isfile to avoid file existence check
    monkeypatch.setattr(
        "squid.ui.widgets.display.navigation_viewer.os.path.isfile",
        lambda _path: True,
    )

    # Pass EventBus directly (not UIEventBus) — handlers run on EventBus dispatch thread
    w = NavigationViewer(
        objectivestore=mock_objective_store,
        camera=mock_camera,
        sample="96 well plate",
        event_bus=event_bus,
    )
    qtbot.addWidget(w)
    yield w


@pytest.fixture
def scan_coords(event_bus, mock_objective_store, mock_stage, mock_camera):
    sc = ScanCoordinates(
        objectiveStore=mock_objective_store,
        stage=mock_stage,
        camera=mock_camera,
        event_bus=event_bus,
    )
    yield sc
    sc.shutdown()


# ============================================================================
# Helpers
# ============================================================================


def _make_fov_centers(positions, fov_w=1.0, fov_h=1.0):
    """Create list of FovCenter from (x, y) positions."""
    return [FovCenter(x_mm=x, y_mm=y, fov_width_mm=fov_w, fov_height_mm=fov_h) for x, y in positions]


# ============================================================================
# 1. TestFOVRegistration — Adding/removing pending FOVs
# ============================================================================


class TestFOVRegistration:

    def test_add_region_populates_pending(self, event_bus, viewer):
        fovs = _make_fov_centers([(20.0, 20.0), (21.0, 20.0), (20.0, 21.0)])
        event_bus.publish(AddScanCoordinateRegion(fov_centers=fovs))
        event_bus.drain()
        assert len(viewer._pending_fovs) == 3

    def test_remove_region_reduces_pending(self, event_bus, viewer):
        fovs = _make_fov_centers([(20.0, 20.0), (21.0, 20.0)])
        event_bus.publish(AddScanCoordinateRegion(fov_centers=fovs))
        event_bus.drain()
        assert len(viewer._pending_fovs) == 2

        event_bus.publish(RemovedScanCoordinateRegion(fov_centers=fovs[:1]))
        event_bus.drain()
        assert len(viewer._pending_fovs) == 1

    def test_clear_removes_all_pending(self, event_bus, viewer):
        fovs = _make_fov_centers([(20.0, 20.0), (21.0, 20.0)])
        event_bus.publish(AddScanCoordinateRegion(fov_centers=fovs))
        event_bus.drain()

        # Mark one as completed
        event_bus.publish(CurrentFOVRegistered(x_mm=20.0, y_mm=20.0, fov_width_mm=1.0, fov_height_mm=1.0))
        event_bus.drain()
        assert len(viewer._completed_fovs) == 1

        event_bus.publish(ClearedScanCoordinates())
        event_bus.drain()
        assert len(viewer._pending_fovs) == 0
        # Completed FOVs are kept
        assert len(viewer._completed_fovs) == 1

    def test_clear_overlay_removes_both(self, event_bus, viewer):
        fovs = _make_fov_centers([(20.0, 20.0)])
        event_bus.publish(AddScanCoordinateRegion(fov_centers=fovs))
        event_bus.publish(CurrentFOVRegistered(x_mm=20.0, y_mm=20.0, fov_width_mm=1.0, fov_height_mm=1.0))
        event_bus.drain()

        viewer.clear_overlay()
        assert len(viewer._pending_fovs) == 0
        assert len(viewer._completed_fovs) == 0

    def test_clear_command_with_display_flag_removes_pending_and_completed(self, event_bus, viewer):
        fovs = _make_fov_centers([(20.0, 20.0), (21.0, 20.0)])
        event_bus.publish(AddScanCoordinateRegion(fov_centers=fovs))
        event_bus.publish(CurrentFOVRegistered(x_mm=20.0, y_mm=20.0, fov_width_mm=1.0, fov_height_mm=1.0))
        event_bus.drain()
        assert len(viewer._pending_fovs) == 1
        assert len(viewer._completed_fovs) == 1

        event_bus.publish(ClearScanCoordinatesCommand(clear_displayed_fovs=True))
        event_bus.drain()
        assert len(viewer._pending_fovs) == 0
        assert len(viewer._completed_fovs) == 0

    def test_multiple_add_events_accumulate(self, event_bus, viewer):
        fovs1 = _make_fov_centers([(20.0, 20.0), (21.0, 20.0)])
        fovs2 = _make_fov_centers([(22.0, 20.0)])
        fovs3 = _make_fov_centers([(23.0, 20.0), (24.0, 20.0)])

        event_bus.publish(AddScanCoordinateRegion(fov_centers=fovs1))
        event_bus.publish(AddScanCoordinateRegion(fov_centers=fovs2))
        event_bus.publish(AddScanCoordinateRegion(fov_centers=fovs3))
        event_bus.drain()

        assert len(viewer._pending_fovs) == 5


# ============================================================================
# 2. TestCurrentFOVRegisteredMatching — The tolerance fix
# ============================================================================


class TestCurrentFOVRegisteredMatching:

    def test_exact_match_moves_pending_to_completed(self, event_bus, viewer):
        fovs = _make_fov_centers([(20.0, 20.0)])
        event_bus.publish(AddScanCoordinateRegion(fov_centers=fovs))
        event_bus.drain()
        assert len(viewer._pending_fovs) == 1

        event_bus.publish(CurrentFOVRegistered(x_mm=20.0, y_mm=20.0, fov_width_mm=1.0, fov_height_mm=1.0))
        event_bus.drain()
        assert len(viewer._pending_fovs) == 0
        assert len(viewer._completed_fovs) == 1

    def test_match_with_small_drift(self, event_bus, viewer):
        """CurrentFOVRegistered offset by 1e-4 mm → still matches (tolerance=1e-3)."""
        fovs = _make_fov_centers([(20.0, 20.0)])
        event_bus.publish(AddScanCoordinateRegion(fov_centers=fovs))
        event_bus.drain()

        event_bus.publish(CurrentFOVRegistered(x_mm=20.0001, y_mm=19.9999, fov_width_mm=1.0, fov_height_mm=1.0))
        event_bus.drain()
        assert len(viewer._pending_fovs) == 0
        assert len(viewer._completed_fovs) == 1

    def test_no_match_beyond_tolerance(self, event_bus, viewer, caplog):
        """Offset by 0.01mm → no match, warning logged."""
        import logging

        fovs = _make_fov_centers([(20.0, 20.0)])
        event_bus.publish(AddScanCoordinateRegion(fov_centers=fovs))
        event_bus.drain()

        with caplog.at_level(logging.WARNING):
            event_bus.publish(CurrentFOVRegistered(x_mm=20.01, y_mm=20.0, fov_width_mm=1.0, fov_height_mm=1.0))
            event_bus.drain()
        # Pending unchanged — no match found within tolerance
        assert len(viewer._pending_fovs) == 1
        # Still added to completed (always added regardless of match)
        assert len(viewer._completed_fovs) == 1
        # Warning should be logged about no matching pending FOV
        assert any("pending" in r.message.lower() or "match" in r.message.lower() for r in caplog.records)

    def test_match_removes_one_not_all(self, event_bus, viewer):
        """5 pending at different positions, match 1 → 4 pending, 1 completed."""
        positions = [(20.0 + i, 20.0) for i in range(5)]
        fovs = _make_fov_centers(positions)
        event_bus.publish(AddScanCoordinateRegion(fov_centers=fovs))
        event_bus.drain()
        assert len(viewer._pending_fovs) == 5

        event_bus.publish(CurrentFOVRegistered(x_mm=22.0, y_mm=20.0, fov_width_mm=1.0, fov_height_mm=1.0))
        event_bus.drain()
        assert len(viewer._pending_fovs) == 4
        assert len(viewer._completed_fovs) == 1

    def test_duplicate_registration_ignored(self, event_bus, viewer):
        """Same position registered twice → completed stays at 1."""
        fovs = _make_fov_centers([(20.0, 20.0)])
        event_bus.publish(AddScanCoordinateRegion(fov_centers=fovs))
        event_bus.drain()

        event_bus.publish(CurrentFOVRegistered(x_mm=20.0, y_mm=20.0, fov_width_mm=1.0, fov_height_mm=1.0))
        event_bus.publish(CurrentFOVRegistered(x_mm=20.0, y_mm=20.0, fov_width_mm=1.0, fov_height_mm=1.0))
        event_bus.drain()
        assert len(viewer._completed_fovs) == 1

    def test_duplicate_registration_does_not_raise_handler_errors(self, event_bus, viewer, caplog):
        """Duplicate CurrentFOVRegistered should not trigger handler exceptions."""
        import logging

        event_bus.publish(CurrentFOVRegistered(x_mm=20.0, y_mm=20.0, fov_width_mm=1.0, fov_height_mm=1.0))
        with caplog.at_level(logging.ERROR):
            event_bus.publish(CurrentFOVRegistered(x_mm=20.0002, y_mm=19.9998, fov_width_mm=1.0, fov_height_mm=1.0))
            event_bus.drain()

        assert len(viewer._completed_fovs) == 1
        assert not any(
            "CurrentFOVRegistered" in rec.message and "failed for event" in rec.message
            for rec in caplog.records
        )

    def test_no_pending_still_adds_completed(self, event_bus, viewer):
        """CurrentFOVRegistered with empty pending → 0 pending, 1 completed."""
        assert len(viewer._pending_fovs) == 0
        event_bus.publish(CurrentFOVRegistered(x_mm=20.0, y_mm=20.0, fov_width_mm=1.0, fov_height_mm=1.0))
        event_bus.drain()
        assert len(viewer._pending_fovs) == 0
        assert len(viewer._completed_fovs) == 1

    def test_fov_dimensions_carried_to_completed(self, event_bus, viewer):
        """width/height from event propagated to completed FovCenter."""
        fovs = _make_fov_centers([(20.0, 20.0)])
        event_bus.publish(AddScanCoordinateRegion(fov_centers=fovs))
        event_bus.drain()

        event_bus.publish(CurrentFOVRegistered(x_mm=20.0, y_mm=20.0, fov_width_mm=2.5, fov_height_mm=1.5))
        event_bus.drain()
        assert len(viewer._completed_fovs) == 1
        assert viewer._completed_fovs[0].fov_width_mm == pytest.approx(2.5)
        assert viewer._completed_fovs[0].fov_height_mm == pytest.approx(1.5)


# ============================================================================
# 3. TestObjectiveChangeOverlayLifecycle — FOVs survive objective change
# ============================================================================


class TestObjectiveChangeOverlayLifecycle:

    def test_pending_fovs_survive_objective_change(self, event_bus, viewer):
        fovs = _make_fov_centers([(20.0, 20.0), (21.0, 20.0)])
        event_bus.publish(AddScanCoordinateRegion(fov_centers=fovs))
        event_bus.drain()
        assert len(viewer._pending_fovs) == 2

        event_bus.publish(ObjectiveChanged(position=1, objective_name="20x"))
        event_bus.drain()
        assert len(viewer._pending_fovs) == 2

    def test_completed_fovs_survive_objective_change(self, event_bus, viewer):
        fovs = _make_fov_centers([(20.0, 20.0)])
        event_bus.publish(AddScanCoordinateRegion(fov_centers=fovs))
        event_bus.publish(CurrentFOVRegistered(x_mm=20.0, y_mm=20.0, fov_width_mm=1.0, fov_height_mm=1.0))
        event_bus.drain()
        assert len(viewer._completed_fovs) == 1

        event_bus.publish(ObjectiveChanged(position=1, objective_name="20x"))
        event_bus.drain()
        assert len(viewer._completed_fovs) == 1


class _DummyTimer:
    def __init__(self):
        self.active = False
        self.starts = 0

    def isActive(self):
        return self.active

    def start(self):
        self.active = True
        self.starts += 1

    def stop(self):
        self.active = False


class TestNavigationViewerPerformanceGuards:

    def test_completed_paint_flush_is_chunked(self, viewer):
        viewer._completed_paint_timer = _DummyTimer()
        viewer.scan_overlay_item = MagicMock()
        viewer.scan_overlay = np.zeros_like(viewer.scan_overlay)

        total = viewer._COMPLETED_PAINT_BATCH_SIZE + 5
        for i in range(total):
            viewer._queued_completed_fovs.append(
                FovCenter(
                    x_mm=20.0 + i * 0.001,
                    y_mm=20.0,
                    fov_width_mm=1.0,
                    fov_height_mm=1.0,
                )
            )

        viewer._flush_completed_fov_paints()
        assert len(viewer._queued_completed_fovs) == 5
        assert viewer._completed_paint_timer.starts == 1

        viewer._completed_paint_timer.active = False
        viewer._flush_completed_fov_paints()
        assert len(viewer._queued_completed_fovs) == 0

    def test_wellplate_change_clears_indexes(self, event_bus, viewer):
        fovs = _make_fov_centers([(20.0, 20.0), (21.0, 20.0)])
        event_bus.publish(AddScanCoordinateRegion(fov_centers=fovs))
        event_bus.publish(CurrentFOVRegistered(x_mm=20.0, y_mm=20.0, fov_width_mm=1.0, fov_height_mm=1.0))
        event_bus.drain()
        assert viewer._pending_fov_index
        assert viewer._completed_fov_index

        event_bus.publish(
            WellplateFormatChanged(
                format_name="96 well plate",
                rows=8,
                cols=12,
                well_spacing_mm=9.0,
                well_size_mm=6.0,
                a1_x_mm=14.38,
                a1_y_mm=11.24,
                a1_x_pixel=73,
                a1_y_pixel=73,
                number_of_skip=1,
            )
        )
        event_bus.drain()

        assert len(viewer._pending_fovs) == 0
        assert len(viewer._completed_fovs) == 0
        assert not viewer._pending_fov_index
        assert not viewer._completed_fov_index

    def test_overlay_redrawn_after_objective_change(self, event_bus, viewer):
        fovs = _make_fov_centers([(20.0, 20.0)])
        event_bus.publish(AddScanCoordinateRegion(fov_centers=fovs))
        event_bus.drain()

        event_bus.publish(ObjectiveChanged(position=1, objective_name="20x"))
        event_bus.drain()
        # FOVs still present after redraw trigger
        assert len(viewer._pending_fovs) == 1
        # scan_overlay was regenerated (not None, has non-zero pixels from FOV boxes)
        assert viewer.scan_overlay is not None
        assert viewer.scan_overlay.shape[0] > 0 and viewer.scan_overlay.shape[1] > 0

    def test_fov_dimensions_updated_on_objective_change(self, event_bus, viewer, mock_objective_store):
        """After changing pixel_size_factor, NavigationViewer recalculates FOV dimensions."""
        mock_objective_store.get_pixel_size_factor.return_value = 1.0
        initial_fov_w = viewer.fov_width_mm

        mock_objective_store.get_pixel_size_factor.return_value = 2.0
        event_bus.publish(ObjectiveChanged(position=1, objective_name="10x"))
        event_bus.drain()

        # viewer.fov_width_mm should have changed
        assert viewer.fov_width_mm != initial_fov_w

    def test_current_fov_marker_persists_after_objective_change(self, event_bus, viewer):
        event_bus.publish(WellplateFormatChanged(
            format_name="30 mm circle",
            rows=1, cols=1,
            well_spacing_mm=0.0, well_size_mm=30.0,
            a1_x_mm=20.0, a1_y_mm=20.0,
            a1_x_pixel=50, a1_y_pixel=50,
            number_of_skip=0,
        ))
        event_bus.drain()

        event_bus.publish(StageMovementStopped(x_mm=20.0, y_mm=20.0, z_mm=0.0))
        event_bus.drain()
        assert viewer.current_location_item is not None
        before = np.array(viewer.current_location_item.image)
        assert np.any(before != 0)

        event_bus.publish(ObjectiveChanged(position=1, objective_name="10x"))
        event_bus.drain()

        assert viewer.current_location_item is not None
        after = np.array(viewer.current_location_item.image)
        assert np.any(after != 0)

    def test_roi_changed_triggers_display_redraw(self, event_bus, viewer):
        redraw = MagicMock()
        viewer.update_display_properties = redraw

        event_bus.publish(ROIChanged(x_offset=0, y_offset=0, width=1200, height=800))
        event_bus.drain()

        redraw.assert_called_once_with(viewer.sample)


# ============================================================================
# 4. TestAcquisitionDisplayFlow — Red→blue transition + post-acquisition
# ============================================================================


class TestAcquisitionDisplayFlow:

    def test_full_acquisition_flow_red_to_blue(self, event_bus, viewer):
        positions = [(20.0, 20.0), (21.0, 20.0), (20.0, 21.0)]
        fovs = _make_fov_centers(positions)
        event_bus.publish(AddScanCoordinateRegion(fov_centers=fovs))
        event_bus.drain()

        for x, y in positions:
            event_bus.publish(CurrentFOVRegistered(x_mm=x, y_mm=y, fov_width_mm=1.0, fov_height_mm=1.0))
        event_bus.drain()

        assert len(viewer._pending_fovs) == 0
        assert len(viewer._completed_fovs) == 3

    def test_partial_acquisition_mixed_colors(self, event_bus, viewer):
        positions = [(20.0 + i, 20.0) for i in range(5)]
        fovs = _make_fov_centers(positions)
        event_bus.publish(AddScanCoordinateRegion(fov_centers=fovs))
        event_bus.drain()

        # Register 3 of 5
        for x, y in positions[:3]:
            event_bus.publish(CurrentFOVRegistered(x_mm=x, y_mm=y, fov_width_mm=1.0, fov_height_mm=1.0))
        event_bus.drain()

        assert len(viewer._pending_fovs) == 2
        assert len(viewer._completed_fovs) == 3

    def test_completed_fovs_persist_after_acquisition(self, event_bus, viewer):
        fovs = _make_fov_centers([(20.0, 20.0)])
        event_bus.publish(AddScanCoordinateRegion(fov_centers=fovs))
        event_bus.publish(CurrentFOVRegistered(x_mm=20.0, y_mm=20.0, fov_width_mm=1.0, fov_height_mm=1.0))
        event_bus.drain()

        # All completed — not cleared automatically
        assert len(viewer._completed_fovs) == 1

    def test_acquisition_start_keeps_completed_fovs(self, event_bus, viewer):
        """Starting a new acquisition should preserve completed FOVs (accumulate across runs)."""
        fovs = _make_fov_centers([(20.0, 20.0), (21.0, 20.0)])
        event_bus.publish(AddScanCoordinateRegion(fov_centers=fovs))
        event_bus.drain()

        # Simulate completing an acquisition
        event_bus.publish(CurrentFOVRegistered(x_mm=20.0, y_mm=20.0, fov_width_mm=1.0, fov_height_mm=1.0))
        event_bus.publish(CurrentFOVRegistered(x_mm=21.0, y_mm=20.0, fov_width_mm=1.0, fov_height_mm=1.0))
        event_bus.drain()
        assert len(viewer._completed_fovs) == 2
        assert len(viewer._pending_fovs) == 0

        # Start a new acquisition — completed should remain
        event_bus.publish(AcquisitionStateChanged(in_progress=True, experiment_id="exp_001"))
        event_bus.drain()
        assert len(viewer._completed_fovs) == 2

    def test_acquisition_abort_does_not_clear_completed(self, event_bus, viewer):
        """Aborting acquisition should NOT clear completed FOVs."""
        fovs = _make_fov_centers([(20.0, 20.0)])
        event_bus.publish(AddScanCoordinateRegion(fov_centers=fovs))
        event_bus.publish(CurrentFOVRegistered(x_mm=20.0, y_mm=20.0, fov_width_mm=1.0, fov_height_mm=1.0))
        event_bus.drain()
        assert len(viewer._completed_fovs) == 1

        # Abort should not clear completed
        event_bus.publish(AcquisitionStateChanged(in_progress=True, experiment_id="exp_001", is_aborting=True))
        event_bus.drain()
        assert len(viewer._completed_fovs) == 1

    def test_acquisition_end_preserves_completed(self, event_bus, viewer):
        """Acquisition ending (in_progress=False) should keep completed FOVs."""
        fovs = _make_fov_centers([(20.0, 20.0)])
        event_bus.publish(AddScanCoordinateRegion(fov_centers=fovs))
        event_bus.publish(CurrentFOVRegistered(x_mm=20.0, y_mm=20.0, fov_width_mm=1.0, fov_height_mm=1.0))
        event_bus.drain()
        assert len(viewer._completed_fovs) == 1

        # End of acquisition should keep completed
        event_bus.publish(AcquisitionStateChanged(in_progress=False, experiment_id="exp_001"))
        event_bus.drain()
        assert len(viewer._completed_fovs) == 1

    def test_new_scan_clears_pending_keeps_completed(self, event_bus, viewer):
        fovs = _make_fov_centers([(20.0, 20.0), (21.0, 20.0)])
        event_bus.publish(AddScanCoordinateRegion(fov_centers=fovs))
        event_bus.publish(CurrentFOVRegistered(x_mm=20.0, y_mm=20.0, fov_width_mm=1.0, fov_height_mm=1.0))
        event_bus.drain()
        assert len(viewer._completed_fovs) == 1

        event_bus.publish(ClearedScanCoordinates())
        event_bus.drain()
        assert len(viewer._pending_fovs) == 0
        assert len(viewer._completed_fovs) == 1

    def test_regression_two_runs_accumulate_completed_without_pending_leak(self, event_bus, viewer):
        """Running acquisition twice should leave no stale pending FOVs and accumulate completed FOVs."""
        run1 = _make_fov_centers([(20.0, 20.0), (21.0, 20.0), (22.0, 20.0)])
        run2 = _make_fov_centers([(25.0, 21.0), (26.0, 21.0), (27.0, 21.0)])

        event_bus.publish(AddScanCoordinateRegion(fov_centers=run1))
        event_bus.drain()
        for f in run1:
            event_bus.publish(
                CurrentFOVRegistered(
                    x_mm=f.x_mm,
                    y_mm=f.y_mm,
                    fov_width_mm=f.fov_width_mm,
                    fov_height_mm=f.fov_height_mm,
                )
            )
        event_bus.drain()
        assert len(viewer._pending_fovs) == 0
        assert len(viewer._completed_fovs) == len(run1)

        event_bus.publish(AddScanCoordinateRegion(fov_centers=run2))
        event_bus.drain()
        for f in run2:
            # Include realistic sub-micron float drift from motion controller rounding.
            event_bus.publish(
                CurrentFOVRegistered(
                    x_mm=f.x_mm + 1e-4,
                    y_mm=f.y_mm - 1e-4,
                    fov_width_mm=f.fov_width_mm,
                    fov_height_mm=f.fov_height_mm,
                )
            )
        event_bus.drain()

        assert len(viewer._pending_fovs) == 0
        assert len(viewer._completed_fovs) == len(run1) + len(run2)

    def test_regression_roi_regenerate_clears_completed_before_new_grid(self, event_bus, viewer):
        """ROI regenerate flow should clear displayed FOVs, then show only the new pending grid."""
        first_run = _make_fov_centers([(20.0, 20.0), (21.0, 20.0)])
        new_grid = _make_fov_centers([(30.0, 30.0), (31.0, 30.0), (32.0, 30.0)])

        event_bus.publish(AddScanCoordinateRegion(fov_centers=first_run))
        for f in first_run:
            event_bus.publish(
                CurrentFOVRegistered(
                    x_mm=f.x_mm,
                    y_mm=f.y_mm,
                    fov_width_mm=f.fov_width_mm,
                    fov_height_mm=f.fov_height_mm,
                )
            )
        event_bus.drain()
        assert len(viewer._completed_fovs) == len(first_run)

        # Matches AcquisitionSetupWidget._on_generate_fovs() behavior.
        event_bus.publish(ClearScanCoordinatesCommand(clear_displayed_fovs=True))
        event_bus.publish(ClearedScanCoordinates())
        event_bus.publish(AddScanCoordinateRegion(fov_centers=new_grid))
        event_bus.drain()

        assert len(viewer._completed_fovs) == 0
        assert len(viewer._pending_fovs) == len(new_grid)

    def test_regression_redraw_is_debounced_for_rapid_fov_updates(self, viewer, qtbot):
        """Rapid FOV updates should coalesce into one redraw to prevent UI stalls."""
        redraw_calls = 0
        original = viewer._redraw_scan_overlay

        def _counted_redraw():
            nonlocal redraw_calls
            redraw_calls += 1
            original()

        viewer._redraw_timer.timeout.disconnect()
        viewer._redraw_timer.timeout.connect(_counted_redraw)

        for i in range(200):
            viewer.register_fov_to_image(FovCenter(x_mm=20.0 + i * 0.01, y_mm=20.0))

        qtbot.wait(120)
        assert redraw_calls == 1

    def test_regression_current_fov_queues_batched_paint_path(self, event_bus, viewer):
        fovs = _make_fov_centers([(20.0, 20.0)])
        event_bus.publish(AddScanCoordinateRegion(fov_centers=fovs))
        event_bus.drain()

        viewer._queue_completed_fov_paint = MagicMock()

        event_bus.publish(CurrentFOVRegistered(x_mm=20.0, y_mm=20.0, fov_width_mm=1.0, fov_height_mm=1.0))
        event_bus.drain()

        viewer._queue_completed_fov_paint.assert_called_once()


# ============================================================================
# 5. TestWellplateFormatChange — Format switch lifecycle
# ============================================================================


class TestWellplateFormatChange:

    def test_format_change_clears_pending_and_completed(self, event_bus, viewer):
        fovs = _make_fov_centers([(20.0, 20.0)])
        event_bus.publish(AddScanCoordinateRegion(fov_centers=fovs))
        event_bus.publish(CurrentFOVRegistered(x_mm=20.0, y_mm=20.0, fov_width_mm=1.0, fov_height_mm=1.0))
        event_bus.drain()
        assert len(viewer._pending_fovs) == 0
        assert len(viewer._completed_fovs) == 1

        event_bus.publish(WellplateFormatChanged(
            format_name="384 well plate",
            rows=16, cols=24,
            well_spacing_mm=4.5, well_size_mm=3.4,
            a1_x_mm=12.13, a1_y_mm=8.99,
            a1_x_pixel=143, a1_y_pixel=90,
            number_of_skip=0,
        ))
        event_bus.drain()
        assert len(viewer._pending_fovs) == 0
        assert len(viewer._completed_fovs) == 0

    def test_format_change_recalculates_scale(self, event_bus, viewer):
        old_mm_per_pixel = viewer.mm_per_pixel

        event_bus.publish(WellplateFormatChanged(
            format_name="384 well plate",
            rows=16, cols=24,
            well_spacing_mm=4.5, well_size_mm=3.4,
            a1_x_mm=12.13, a1_y_mm=8.99,
            a1_x_pixel=143, a1_y_pixel=90,
            number_of_skip=0,
        ))
        event_bus.drain()
        # Both 96- and 384-well use SBS footprint with same mocked image,
        # so mm_per_pixel should be recalculated to the same value
        assert viewer.mm_per_pixel == pytest.approx(old_mm_per_pixel, rel=0.01)

    def test_mm_circle_format_uses_generated_circle_background(self, event_bus, viewer):
        event_bus.publish(WellplateFormatChanged(
            format_name="30 mm circle",
            rows=1, cols=1,
            well_spacing_mm=0.0, well_size_mm=30.0,
            a1_x_mm=0.0, a1_y_mm=0.0,
            a1_x_pixel=1, a1_y_pixel=1,
            number_of_skip=0,
        ))
        event_bus.drain()

        assert viewer.sample == "30 mm circle"
        assert viewer.slide is not None
        # If we had fallen back to mocked cv2.imread, the image would be all zeros.
        assert np.any(viewer.slide != 0)


# ============================================================================
# 6. TestFullIntegrationWithBackend — NavigationViewer + ScanCoordinates
# ============================================================================


class TestFullIntegrationWithBackend:

    def test_select_wells_populates_viewer_pending(self, event_bus, viewer, scan_coords):
        event_bus.publish(SelectedWellsChanged(
            format_name="96 well plate",
            selected_cells=((0, 0),),
        ))
        event_bus.publish(SetWellSelectionScanCoordinatesCommand(
            scan_size_mm=1.0, overlap_percent=10.0, shape="Square",
        ))
        event_bus.drain()

        # ScanCoordinates publishes AddScanCoordinateRegion, NavigationViewer receives it
        assert len(scan_coords.region_fov_coordinates) > 0
        assert len(viewer._pending_fovs) > 0

    def test_objective_change_with_backend_recompute(self, event_bus, viewer, scan_coords, mock_objective_store):
        event_bus.publish(SelectedWellsChanged(
            format_name="96 well plate",
            selected_cells=((0, 0),),
        ))
        event_bus.publish(SetWellSelectionScanCoordinatesCommand(
            scan_size_mm=3.0, overlap_percent=10.0, shape="Square",
        ))
        event_bus.drain()
        initial_pending = len(viewer._pending_fovs)
        assert initial_pending > 0

        # Change objective (different pixel_size_factor)
        mock_objective_store.get_pixel_size_factor.return_value = 0.5
        event_bus.publish(SetWellSelectionScanCoordinatesCommand(
            scan_size_mm=3.0, overlap_percent=10.0, shape="Square",
        ))
        event_bus.drain()
        new_pending = len(viewer._pending_fovs)
        # Different FOV count due to smaller FOV
        assert new_pending != initial_pending

    def test_acquisition_simulation_all_blue(self, event_bus, viewer, scan_coords):
        """Set up wells → CurrentFOVRegistered for every FOV → all pending→completed."""
        event_bus.publish(SelectedWellsChanged(
            format_name="96 well plate",
            selected_cells=((0, 0),),
        ))
        event_bus.publish(SetWellSelectionScanCoordinatesCommand(
            scan_size_mm=1.0, overlap_percent=10.0, shape="Square",
        ))
        event_bus.drain()

        # Get all FOV positions from scan_coords and register them
        all_coords = []
        for coords in scan_coords.region_fov_coordinates.values():
            all_coords.extend(coords)
        assert len(all_coords) > 0

        for coord in all_coords:
            event_bus.publish(CurrentFOVRegistered(
                x_mm=coord[0], y_mm=coord[1],
                fov_width_mm=1.0, fov_height_mm=1.0,
            ))
        event_bus.drain()

        assert len(viewer._pending_fovs) == 0
        assert len(viewer._completed_fovs) == len(all_coords)

    def test_acquisition_with_float_drift(self, event_bus, viewer, scan_coords):
        """CurrentFOVRegistered positions offset by 5e-4 from scan_coords → still match."""
        event_bus.publish(SelectedWellsChanged(
            format_name="96 well plate",
            selected_cells=((0, 0),),
        ))
        event_bus.publish(SetWellSelectionScanCoordinatesCommand(
            scan_size_mm=1.0, overlap_percent=10.0, shape="Square",
        ))
        event_bus.drain()

        all_coords = []
        for coords in scan_coords.region_fov_coordinates.values():
            all_coords.extend(coords)
        assert len(all_coords) > 0

        for coord in all_coords:
            # Add small drift within tolerance
            event_bus.publish(CurrentFOVRegistered(
                x_mm=coord[0] + 5e-4,
                y_mm=coord[1] - 5e-4,
                fov_width_mm=1.0, fov_height_mm=1.0,
            ))
        event_bus.drain()

        # All should match within 1e-3 tolerance
        assert len(viewer._pending_fovs) == 0
        assert len(viewer._completed_fovs) == len(all_coords)
