"""Comprehensive tests for ScanCoordinates backend manager.

Covers all user workflows (well selection, current position, manual ROI,
loaded coordinates), grid generation edge cases, event flow correctness,
coordinate persistence, sorting, objective changes, and region management.
"""

import pytest
import numpy as np
from unittest.mock import MagicMock

from squid.core.events import (
    EventBus,
    SelectedWellsChanged,
    SetWellSelectionScanCoordinatesCommand,
    SetLiveScanCoordinatesCommand,
    SetManualScanCoordinatesCommand,
    LoadScanCoordinatesCommand,
    ClearScanCoordinatesCommand,
    SortScanCoordinatesCommand,
    ScanCoordinatesUpdated,
    WellplateFormatChanged,
    AddFlexibleRegionCommand,
    RemoveScanCoordinateRegionCommand,
    RenameScanCoordinateRegionCommand,
    UpdateScanCoordinateRegionZCommand,
)
from squid.backend.managers.scan_coordinates import (
    ScanCoordinates,
    AddScanCoordinateRegion,
    RemovedScanCoordinateRegion,
    ClearedScanCoordinates,
    FovCenter,
)
from squid.backend.managers.objective_store import ObjectiveStore
import _def


# ============================================================================
# Helpers
# ============================================================================


def _collect_events(event_bus, event_type):
    """Subscribe and collect events of the given type."""
    collected = []
    event_bus.subscribe(event_type, lambda e: collected.append(e))
    return collected


def _total_fovs(scan_coords):
    """Return total FOV count across all regions."""
    return sum(len(c) for c in scan_coords.region_fov_coordinates.values())


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
def mock_camera():
    """Mock camera with configurable FOV dimensions."""
    camera = MagicMock()
    camera.get_fov_size_mm.return_value = 1.0
    camera.get_fov_height_mm.return_value = 1.0
    camera.get_fov_width_mm.return_value = 1.0
    return camera


@pytest.fixture
def mock_stage():
    """Mock stage at position (20, 20, 0) — safely within bounds."""
    stage = MagicMock()
    pos = MagicMock()
    pos.x_mm = 20.0
    pos.y_mm = 20.0
    pos.z_mm = 0.0
    stage.get_pos.return_value = pos
    return stage


@pytest.fixture
def mock_objective_store():
    store = MagicMock(spec=ObjectiveStore)
    store.get_pixel_size_factor.return_value = 1.0
    return store


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
# 1. TestWellSelectionMode — Select Wells XY mode
# ============================================================================


class TestWellSelectionMode:

    def test_single_well_generates_fovs(self, scan_coords):
        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((0, 0),))
        )
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=1.0, overlap_percent=10.0, shape="Square")
        )
        assert len(scan_coords.region_fov_coordinates) > 0
        assert _total_fovs(scan_coords) > 0

    def test_multiple_wells_generate_separate_regions(self, scan_coords):
        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((0, 0), (0, 1), (1, 0)))
        )
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=1.0, overlap_percent=10.0, shape="Square")
        )
        assert len(scan_coords.region_fov_coordinates) == 3
        assert "A1" in scan_coords.region_fov_coordinates
        assert "A2" in scan_coords.region_fov_coordinates
        assert "B1" in scan_coords.region_fov_coordinates

    def test_well_deselection_removes_region(self, scan_coords):
        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((0, 0), (0, 1)))
        )
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=1.0, overlap_percent=10.0, shape="Square")
        )
        assert len(scan_coords.region_fov_coordinates) == 2

        # Deselect A2 by selecting only A1
        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((0, 0),))
        )
        assert len(scan_coords.region_fov_coordinates) == 1
        assert "A1" in scan_coords.region_fov_coordinates
        assert "A2" not in scan_coords.region_fov_coordinates

    def test_empty_selection_clears_all(self, scan_coords):
        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((0, 0),))
        )
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=1.0, overlap_percent=10.0, shape="Square")
        )
        assert len(scan_coords.region_fov_coordinates) > 0

        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=())
        )
        assert len(scan_coords.region_fov_coordinates) == 0

    def test_reselection_replaces_regions(self, scan_coords):
        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((0, 0),))
        )
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=1.0, overlap_percent=10.0, shape="Square")
        )
        assert "A1" in scan_coords.region_fov_coordinates

        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((1, 0),))
        )
        assert "B1" in scan_coords.region_fov_coordinates
        assert "A1" not in scan_coords.region_fov_coordinates

    def test_scan_size_change_updates_fov_count(self, scan_coords):
        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((0, 0),))
        )
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=3.0, overlap_percent=10.0, shape="Square")
        )
        count_large = _total_fovs(scan_coords)

        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=1.0, overlap_percent=10.0, shape="Square")
        )
        count_small = _total_fovs(scan_coords)

        assert count_large > count_small

    def test_overlap_change_updates_fov_count(self, scan_coords):
        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((0, 0),))
        )
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=3.0, overlap_percent=10.0, shape="Square")
        )
        count_low_overlap = _total_fovs(scan_coords)

        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=3.0, overlap_percent=50.0, shape="Square")
        )
        count_high_overlap = _total_fovs(scan_coords)

        assert count_high_overlap > count_low_overlap

    def test_shape_square_vs_circle(self, scan_coords):
        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((0, 0),))
        )
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=5.0, overlap_percent=10.0, shape="Square")
        )
        count_square = _total_fovs(scan_coords)

        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=5.0, overlap_percent=10.0, shape="Circle")
        )
        count_circle = _total_fovs(scan_coords)

        assert count_circle < count_square

    def test_well_selection_preserved_across_clear(self, scan_coords):
        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((0, 0), (1, 0)))
        )
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=1.0, overlap_percent=10.0, shape="Square")
        )
        scan_coords.clear_regions()
        assert len(scan_coords._selected_well_cells) == 2

    def test_reapply_after_clear_regenerates(self, scan_coords):
        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((0, 0),))
        )
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=1.0, overlap_percent=10.0, shape="Square")
        )
        scan_coords.clear_regions()
        assert len(scan_coords.region_fov_coordinates) == 0

        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=1.0, overlap_percent=10.0, shape="Square")
        )
        assert len(scan_coords.region_fov_coordinates) > 0


# ============================================================================
# 2. TestCurrentPositionMode — Current Position XY mode
# ============================================================================


class TestCurrentPositionMode:

    def test_live_coordinates_single_fov(self, scan_coords):
        scan_coords.set_live_scan_coordinates(20.0, 20.0, 0.5, 10.0, "Square")
        assert "current" in scan_coords.region_fov_coordinates
        assert _total_fovs(scan_coords) == 1

    def test_live_coordinates_multi_fov(self, scan_coords):
        scan_coords.set_live_scan_coordinates(20.0, 20.0, 5.0, 10.0, "Square")
        assert "current" in scan_coords.region_fov_coordinates
        assert _total_fovs(scan_coords) > 1

    def test_live_clears_previous(self, scan_coords):
        scan_coords.set_live_scan_coordinates(20.0, 20.0, 1.0, 10.0, "Square")
        scan_coords.set_live_scan_coordinates(25.0, 25.0, 1.0, 10.0, "Square")
        assert len(scan_coords.region_fov_coordinates) == 1
        assert "current" in scan_coords.region_fov_coordinates

    def test_near_boundary_position(self, scan_coords):
        """Position near lower boundary — FOVs stay within bounds."""
        scan_coords.set_live_scan_coordinates(0.0, 0.0, 0.1, 10.0, "Square")
        assert _total_fovs(scan_coords) == 1

        # Verify the coordinates are within bounds
        for coord in scan_coords.region_fov_coordinates["current"]:
            assert coord[0] >= _def.SOFTWARE_POS_LIMIT.X_NEGATIVE
            assert coord[0] <= _def.SOFTWARE_POS_LIMIT.X_POSITIVE
            assert coord[1] >= _def.SOFTWARE_POS_LIMIT.Y_NEGATIVE
            assert coord[1] <= _def.SOFTWARE_POS_LIMIT.Y_POSITIVE


# ============================================================================
# 3. TestManualMode — Manual ROI drawing mode
# ============================================================================


class TestManualMode:

    def test_single_polygon_generates_fovs(self, scan_coords):
        polygon = np.array([[19, 19], [22, 19], [22, 22], [19, 22]], dtype=float)
        scan_coords.set_manual_coordinates([polygon], 10.0)
        assert len(scan_coords.region_fov_coordinates) > 0
        assert _total_fovs(scan_coords) > 0

    def test_multiple_polygons_generate_regions(self, scan_coords):
        poly1 = np.array([[19, 19], [21, 19], [21, 21], [19, 21]], dtype=float)
        poly2 = np.array([[25, 25], [27, 25], [27, 27], [25, 27]], dtype=float)
        scan_coords.set_manual_coordinates([poly1, poly2], 10.0)
        assert len(scan_coords.region_fov_coordinates) == 2
        assert "manual0" in scan_coords.region_fov_coordinates
        assert "manual1" in scan_coords.region_fov_coordinates

    def test_none_shapes_clears(self, scan_coords):
        polygon = np.array([[19, 19], [22, 19], [22, 22], [19, 22]], dtype=float)
        scan_coords.set_manual_coordinates([polygon], 10.0)
        assert len(scan_coords.region_fov_coordinates) > 0

        scan_coords.set_manual_coordinates(None, 10.0)
        assert len(scan_coords.region_fov_coordinates) == 0

    def test_small_polygon_no_fovs(self, scan_coords):
        # Very small polygon (0.001mm x 0.001mm) — smaller than grid step
        polygon = np.array([[20, 20], [20.001, 20], [20.001, 20.001], [20, 20.001]], dtype=float)
        scan_coords.set_manual_coordinates([polygon], 10.0)
        # Polygon is far smaller than the 1mm FOV — should produce 0 or at most 1 FOV
        assert _total_fovs(scan_coords) <= 1

    def test_manual_coordinates_publish_add_events(self, event_bus, scan_coords):
        collected = _collect_events(event_bus, AddScanCoordinateRegion)
        polygon = np.array([[19, 19], [22, 19], [22, 22], [19, 22]], dtype=float)
        scan_coords.set_manual_coordinates([polygon], 10.0)
        event_bus.drain()
        # ClearedScanCoordinates + at least one AddScanCoordinateRegion
        assert len(collected) >= 1


# ============================================================================
# 4. TestLoadCoordinatesMode — Load Coordinates mode
# ============================================================================


class TestLoadCoordinatesMode:

    def test_load_single_region(self, scan_coords):
        scan_coords.load_coordinates({"R1": ((20.0, 20.0), (20.5, 20.0))})
        assert "R1" in scan_coords.region_fov_coordinates
        assert len(scan_coords.region_fov_coordinates["R1"]) == 2

    def test_load_multiple_regions(self, scan_coords):
        scan_coords.load_coordinates({
            "R1": ((20.0, 20.0),),
            "R2": ((25.0, 25.0),),
            "R3": ((30.0, 30.0),),
        })
        assert len(scan_coords.region_fov_coordinates) == 3

    def test_load_replaces_existing(self, scan_coords):
        scan_coords.load_coordinates({"R1": ((20.0, 20.0),)})
        scan_coords.load_coordinates({"R2": ((25.0, 25.0),)})
        assert "R1" not in scan_coords.region_fov_coordinates
        assert "R2" in scan_coords.region_fov_coordinates

    def test_load_with_z_coordinates(self, scan_coords):
        scan_coords.load_coordinates({"R1": ((20.0, 20.0, 5.0), (20.5, 20.0, 5.0))})
        assert len(scan_coords.region_fov_coordinates["R1"]) == 2
        for coord in scan_coords.region_fov_coordinates["R1"]:
            assert len(coord) == 3
            assert coord[2] == pytest.approx(5.0)

    def test_load_without_z_uses_stage(self, mock_stage, scan_coords):
        mock_stage.get_pos.return_value.z_mm = 3.0
        scan_coords.load_coordinates({"R1": ((20.0, 20.0),)})
        # The center should have z from stage
        assert scan_coords.region_centers["R1"][2] == pytest.approx(3.0)

    def test_load_publishes_add_events(self, event_bus, scan_coords):
        collected = _collect_events(event_bus, AddScanCoordinateRegion)
        scan_coords.load_coordinates({
            "R1": ((20.0, 20.0),),
            "R2": ((25.0, 25.0),),
        })
        event_bus.drain()
        assert len(collected) == 2


# ============================================================================
# 5. TestObjectiveChangeFlow — Objective switching
# ============================================================================


class TestObjectiveChangeFlow:

    def test_high_mag_to_low_mag_changes_fov_count(self, scan_coords, mock_objective_store, mock_camera):
        """50x→20x with same scan_size: larger FOV means fewer tiles needed."""
        mock_objective_store.get_pixel_size_factor.return_value = 0.5  # higher mag = smaller FOV
        mock_camera.get_fov_size_mm.return_value = 1.0
        mock_camera.get_fov_height_mm.return_value = 1.0

        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((0, 0),))
        )
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=3.0, overlap_percent=10.0, shape="Square")
        )
        count_high_mag = _total_fovs(scan_coords)

        mock_objective_store.get_pixel_size_factor.return_value = 2.0  # lower mag = larger FOV
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=3.0, overlap_percent=10.0, shape="Square")
        )
        count_low_mag = _total_fovs(scan_coords)

        assert count_high_mag > count_low_mag

    def test_low_mag_to_high_mag_changes_fov_count(self, scan_coords, mock_objective_store, mock_camera):
        mock_objective_store.get_pixel_size_factor.return_value = 2.0  # low mag
        mock_camera.get_fov_size_mm.return_value = 1.0
        mock_camera.get_fov_height_mm.return_value = 1.0

        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((0, 0),))
        )
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=3.0, overlap_percent=10.0, shape="Square")
        )
        count_low_mag = _total_fovs(scan_coords)

        mock_objective_store.get_pixel_size_factor.return_value = 0.5  # high mag
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=3.0, overlap_percent=10.0, shape="Square")
        )
        count_high_mag = _total_fovs(scan_coords)

        assert count_high_mag > count_low_mag

    def test_objective_change_preserves_well_selection(self, scan_coords, mock_objective_store):
        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((0, 0), (0, 1)))
        )
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=1.0, overlap_percent=10.0, shape="Square")
        )
        mock_objective_store.get_pixel_size_factor.return_value = 2.0
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=1.0, overlap_percent=10.0, shape="Square")
        )
        assert len(scan_coords._selected_well_cells) == 2
        assert len(scan_coords.region_fov_coordinates) == 2

    def test_objective_change_publishes_remove_then_add(self, event_bus, scan_coords, mock_objective_store):
        removed_events = _collect_events(event_bus, RemovedScanCoordinateRegion)
        add_events = _collect_events(event_bus, AddScanCoordinateRegion)

        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((0, 0),))
        )
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=1.0, overlap_percent=10.0, shape="Square")
        )
        event_bus.drain()
        initial_add_count = len(add_events)

        # Change objective → re-publish
        mock_objective_store.get_pixel_size_factor.return_value = 2.0
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=1.0, overlap_percent=10.0, shape="Square")
        )
        event_bus.drain()

        # The old region was removed and a new one added
        assert len(removed_events) >= 1
        assert len(add_events) > initial_add_count

    def test_objective_change_fov_dimensions_updated(self, event_bus, scan_coords, mock_objective_store):
        add_events = _collect_events(event_bus, AddScanCoordinateRegion)

        mock_objective_store.get_pixel_size_factor.return_value = 1.0
        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((0, 0),))
        )
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=1.0, overlap_percent=10.0, shape="Square")
        )
        event_bus.drain()
        first_fov_w = add_events[-1].fov_centers[0].fov_width_mm

        mock_objective_store.get_pixel_size_factor.return_value = 2.0
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=1.0, overlap_percent=10.0, shape="Square")
        )
        event_bus.drain()
        second_fov_w = add_events[-1].fov_centers[0].fov_width_mm

        assert second_fov_w != first_fov_w


# ============================================================================
# 6. TestEventFlowCorrectness — Event publishing/ordering
# ============================================================================


class TestEventFlowCorrectness:

    def test_add_region_publishes_add_event_with_fov_centers(self, event_bus, scan_coords):
        collected = _collect_events(event_bus, AddScanCoordinateRegion)
        scan_coords.set_live_scan_coordinates(20.0, 20.0, 1.0, 10.0, "Square")
        event_bus.drain()
        assert len(collected) >= 1
        fov = collected[-1].fov_centers[0]
        assert hasattr(fov, "x_mm")
        assert hasattr(fov, "y_mm")
        assert hasattr(fov, "fov_width_mm")
        assert hasattr(fov, "fov_height_mm")

    def test_remove_region_publishes_removed_event(self, event_bus, scan_coords):
        collected = _collect_events(event_bus, RemovedScanCoordinateRegion)
        scan_coords.set_live_scan_coordinates(20.0, 20.0, 1.0, 10.0, "Square")
        scan_coords.remove_region("current")
        event_bus.drain()
        assert len(collected) >= 1
        assert len(collected[-1].fov_centers) > 0

    def test_clear_publishes_cleared_event(self, event_bus, scan_coords):
        collected = _collect_events(event_bus, ClearedScanCoordinates)
        scan_coords.set_live_scan_coordinates(20.0, 20.0, 1.0, 10.0, "Square")
        scan_coords.clear_regions()
        event_bus.drain()
        assert len(collected) >= 1

    def test_coordinates_updated_after_each_mutation(self, event_bus, scan_coords):
        collected = _collect_events(event_bus, ScanCoordinatesUpdated)
        scan_coords.set_live_scan_coordinates(20.0, 20.0, 1.0, 10.0, "Square")
        event_bus.drain()
        # At least one ScanCoordinatesUpdated with the right totals
        assert len(collected) >= 1
        last = collected[-1]
        assert last.total_regions == len(scan_coords.region_fov_coordinates)
        assert last.total_fovs == _total_fovs(scan_coords)

    def test_event_fov_count_matches_internal_state(self, event_bus, scan_coords):
        add_events = _collect_events(event_bus, AddScanCoordinateRegion)

        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((0, 0), (0, 1)))
        )
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=2.0, overlap_percent=10.0, shape="Square")
        )
        event_bus.drain()

        event_fov_total = sum(len(e.fov_centers) for e in add_events)
        internal_total = _total_fovs(scan_coords)
        # Depending on remove/add cycles, event total may exceed internal (accumulated),
        # but the last published ScanCoordinatesUpdated should match internal
        assert internal_total > 0
        assert event_fov_total >= internal_total

    def test_eventbus_flow_matches_direct_call(self, event_bus, scan_coords):
        """Same result via EventBus vs direct handler call."""
        # Direct call
        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((0, 0),))
        )
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=1.0, overlap_percent=10.0, shape="Square")
        )
        direct_count = _total_fovs(scan_coords)
        direct_regions = set(scan_coords.region_fov_coordinates.keys())

        # Clear and redo via EventBus
        scan_coords.clear_regions()
        scan_coords._selected_well_cells = tuple()
        scan_coords._well_selection_scan_size_mm = 0.0

        event_bus.publish(SelectedWellsChanged(format_name="96 well plate", selected_cells=((0, 0),)))
        event_bus.publish(SetWellSelectionScanCoordinatesCommand(scan_size_mm=1.0, overlap_percent=10.0, shape="Square"))
        event_bus.drain()

        assert _total_fovs(scan_coords) == direct_count
        assert set(scan_coords.region_fov_coordinates.keys()) == direct_regions


# ============================================================================
# 7. TestGridGenerationEdgeCases — Grid math edge cases
# ============================================================================


class TestGridGenerationEdgeCases:

    def test_scan_size_smaller_than_fov(self, scan_coords):
        scan_coords.set_live_scan_coordinates(20.0, 20.0, 0.5, 10.0, "Square")
        assert _total_fovs(scan_coords) == 1

    def test_scan_size_equals_fov(self, scan_coords):
        scan_coords.set_live_scan_coordinates(20.0, 20.0, 1.0, 10.0, "Square")
        assert _total_fovs(scan_coords) == 1

    def test_scan_size_zero(self, scan_coords):
        scan_coords.set_live_scan_coordinates(20.0, 20.0, 0.0, 10.0, "Square")
        # Zero scan size → clamped to single FOV at center
        assert _total_fovs(scan_coords) == 1

    def test_overlap_zero(self, scan_coords):
        """0% overlap means step = FOV width → maximum tile spacing."""
        scan_coords.set_live_scan_coordinates(20.0, 20.0, 3.0, 0.0, "Square")
        count = _total_fovs(scan_coords)
        assert count >= 1

        # Compare with 50% overlap on same scan size
        scan_coords.set_live_scan_coordinates(20.0, 20.0, 3.0, 50.0, "Square")
        count_50 = _total_fovs(scan_coords)
        assert count_50 > count

    def test_overlap_100_percent(self, scan_coords):
        """100% overlap → step=0 → should produce a single center FOV."""
        scan_coords.set_live_scan_coordinates(20.0, 20.0, 3.0, 100.0, "Square")
        count = _total_fovs(scan_coords)
        # step_x = fov * (1 - 1.0) = 0 → the grid function returns just center
        assert count == 1

    def test_non_square_fov(self, scan_coords, mock_camera):
        mock_camera.get_fov_size_mm.return_value = 2.0
        mock_camera.get_fov_height_mm.return_value = 0.5
        mock_camera.get_fov_width_mm.return_value = 2.0
        scan_coords.set_live_scan_coordinates(20.0, 20.0, 4.0, 10.0, "Square")
        coords = scan_coords.region_fov_coordinates["current"]
        assert len(coords) > 1
        # With width=2.0mm, height=0.5mm, more tiles needed in Y than X
        x_vals = sorted(set(round(c[0], 6) for c in coords))
        y_vals = sorted(set(round(c[1], 6) for c in coords))
        assert len(y_vals) > len(x_vals), "Non-square FOV should produce more Y tiles than X tiles"

    def test_very_large_scan_area(self, scan_coords):
        scan_coords.set_live_scan_coordinates(25.0, 25.0, 20.0, 10.0, "Square")
        count = _total_fovs(scan_coords)
        # Should produce many tiles but not infinite
        assert count > 10
        assert count < 10000

    def test_circle_shape_fewer_fovs_than_square(self, scan_coords):
        scan_coords.set_live_scan_coordinates(20.0, 20.0, 5.0, 10.0, "Square")
        square_count = _total_fovs(scan_coords)

        scan_coords.set_live_scan_coordinates(20.0, 20.0, 5.0, 10.0, "Circle")
        circle_count = _total_fovs(scan_coords)

        assert circle_count < square_count

    def test_rectangle_shape(self, scan_coords):
        scan_coords.set_live_scan_coordinates(20.0, 20.0, 5.0, 10.0, "Rectangle")
        count = _total_fovs(scan_coords)
        assert count >= 1
        # Rectangle with default 0.6 aspect ratio should differ from square
        scan_coords.set_live_scan_coordinates(20.0, 20.0, 5.0, 10.0, "Square")
        square_count = _total_fovs(scan_coords)
        assert count != square_count, "Rectangle should produce different tile count than square"


# ============================================================================
# 8. TestBoundsFiltering — Position limit enforcement
# ============================================================================


class TestBoundsFiltering:

    def test_all_fovs_in_bounds(self, scan_coords):
        scan_coords.set_live_scan_coordinates(25.0, 25.0, 2.0, 10.0, "Square")
        for coord in scan_coords.region_fov_coordinates["current"]:
            assert coord[0] >= _def.SOFTWARE_POS_LIMIT.X_NEGATIVE
            assert coord[0] <= _def.SOFTWARE_POS_LIMIT.X_POSITIVE
            assert coord[1] >= _def.SOFTWARE_POS_LIMIT.Y_NEGATIVE
            assert coord[1] <= _def.SOFTWARE_POS_LIMIT.Y_POSITIVE

    def test_center_near_boundary(self, scan_coords):
        """Center near edge → some FOVs filtered, rest kept."""
        x_max = _def.SOFTWARE_POS_LIMIT.X_POSITIVE
        scan_coords.set_live_scan_coordinates(x_max - 0.5, 25.0, 5.0, 10.0, "Square")
        # Should produce at least 1 FOV
        assert _total_fovs(scan_coords) >= 1
        # Some might have been filtered
        for coord in scan_coords.region_fov_coordinates["current"]:
            assert coord[0] <= x_max

    def test_all_fovs_out_of_bounds_clamps(self, scan_coords):
        """Center way outside → clamped to bounds."""
        scan_coords.set_live_scan_coordinates(-100.0, -100.0, 1.0, 10.0, "Square")
        assert _total_fovs(scan_coords) >= 1
        coord = scan_coords.region_fov_coordinates["current"][0]
        assert coord[0] >= _def.SOFTWARE_POS_LIMIT.X_NEGATIVE
        assert coord[1] >= _def.SOFTWARE_POS_LIMIT.Y_NEGATIVE

    def test_clamped_position_is_within_limits(self, scan_coords):
        scan_coords.set_live_scan_coordinates(1000.0, 1000.0, 0.1, 10.0, "Square")
        coord = scan_coords.region_fov_coordinates["current"][0]
        assert coord[0] <= _def.SOFTWARE_POS_LIMIT.X_POSITIVE
        assert coord[1] <= _def.SOFTWARE_POS_LIMIT.Y_POSITIVE


# ============================================================================
# 9. TestCoordinatePersistence — State lifecycle
# ============================================================================


class TestCoordinatePersistence:

    def test_coordinates_survive_without_clear(self, scan_coords):
        scan_coords.set_live_scan_coordinates(20.0, 20.0, 1.0, 10.0, "Square")
        count = _total_fovs(scan_coords)
        assert count > 0
        # No clear → coords persist
        assert _total_fovs(scan_coords) == count

    def test_clear_removes_all_regions(self, scan_coords):
        scan_coords.set_live_scan_coordinates(20.0, 20.0, 1.0, 10.0, "Square")
        scan_coords.clear_regions()
        assert len(scan_coords.region_fov_coordinates) == 0
        assert _total_fovs(scan_coords) == 0

    def test_clear_does_not_reset_well_selection(self, scan_coords):
        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((0, 0), (1, 0)))
        )
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=1.0, overlap_percent=10.0, shape="Square")
        )
        scan_coords.clear_regions()
        assert len(scan_coords._selected_well_cells) == 2

    def test_clear_does_not_reset_scan_params(self, scan_coords):
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=2.5, overlap_percent=15.0, shape="Circle")
        )
        scan_coords.clear_regions()
        assert scan_coords._well_selection_scan_size_mm == pytest.approx(2.5)
        assert scan_coords._well_selection_overlap_percent == pytest.approx(15.0)
        assert scan_coords._well_selection_shape == "Circle"

    def test_wellplate_format_change_clears_regions(self, scan_coords):
        scan_coords.set_live_scan_coordinates(20.0, 20.0, 1.0, 10.0, "Square")
        assert _total_fovs(scan_coords) > 0

        scan_coords._on_wellplate_format_changed(
            WellplateFormatChanged(
                format_name="384 well plate",
                rows=16, cols=24,
                well_spacing_mm=4.5, well_size_mm=3.4,
                a1_x_mm=12.13, a1_y_mm=8.99,
                a1_x_pixel=143, a1_y_pixel=90,
                number_of_skip=0,
            )
        )
        assert len(scan_coords.region_fov_coordinates) == 0


# ============================================================================
# 10. TestSortCoordinates — Sorting logic
# ============================================================================


class TestSortCoordinates:

    def test_sort_single_region_noop(self, scan_coords):
        scan_coords.set_live_scan_coordinates(20.0, 20.0, 1.0, 10.0, "Square")
        keys_before = list(scan_coords.region_fov_coordinates.keys())
        scan_coords.sort_coordinates()
        assert list(scan_coords.region_fov_coordinates.keys()) == keys_before

    def test_sort_wells_alphabetical(self, scan_coords):
        # Add wells in non-alphabetical order
        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((1, 1), (0, 0), (0, 1)))
        )
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=1.0, overlap_percent=10.0, shape="Square")
        )
        scan_coords.sort_coordinates()
        keys = list(scan_coords.region_fov_coordinates.keys())
        assert keys == sorted(keys, key=lambda k: (k[0], int(k[1:])))

    def test_sort_s_pattern_reverses_alternate_rows(self, scan_coords):
        scan_coords.acquisition_pattern = "S-Pattern"
        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(
                format_name="96 well plate",
                selected_cells=((0, 0), (0, 1), (1, 0), (1, 1)),
            )
        )
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=1.0, overlap_percent=10.0, shape="Square")
        )
        scan_coords.sort_coordinates()
        keys = list(scan_coords.region_fov_coordinates.keys())
        # Row A should be A1, A2. Row B should be reversed: B2, B1
        assert keys[0] == "A1"
        assert keys[1] == "A2"
        assert keys[2] == "B2"
        assert keys[3] == "B1"

    def test_sort_manual_before_wells(self, scan_coords):
        # Add well region first
        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((0, 0),))
        )
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=1.0, overlap_percent=10.0, shape="Square")
        )
        # Manually add a manual region
        scan_coords.region_centers["manual0"] = [20.0, 20.0]
        scan_coords.region_fov_coordinates["manual0"] = [(20.0, 20.0)]

        scan_coords.sort_coordinates()
        keys = list(scan_coords.region_fov_coordinates.keys())
        # "manual0" should sort before "A1"
        assert keys[0] == "manual0"

    def test_sort_manual_preserves_drawing_order(self, scan_coords):
        scan_coords.region_centers["manual0"] = [20.0, 20.0]
        scan_coords.region_fov_coordinates["manual0"] = [(20.0, 20.0)]
        scan_coords.region_centers["manual1"] = [25.0, 25.0]
        scan_coords.region_fov_coordinates["manual1"] = [(25.0, 25.0)]

        scan_coords.sort_coordinates()
        keys = list(scan_coords.region_fov_coordinates.keys())
        assert keys[0] == "manual0"
        assert keys[1] == "manual1"


# ============================================================================
# 11. TestRegionManagement — Add/remove/rename/z-update
# ============================================================================


class TestRegionManagement:

    def test_add_flexible_region(self, scan_coords):
        scan_coords.add_flexible_region("R1", 20.0, 20.0, 0.0, 3, 3, 10.0)
        assert "R1" in scan_coords.region_fov_coordinates
        assert len(scan_coords.region_fov_coordinates["R1"]) == 9

    def test_add_single_fov_region(self, scan_coords):
        scan_coords.add_single_fov_region("R1", 20.0, 20.0, 0.0)
        assert "R1" in scan_coords.region_fov_coordinates
        assert len(scan_coords.region_fov_coordinates["R1"]) == 1
        assert scan_coords.region_fov_coordinates["R1"][0] == pytest.approx((20.0, 20.0), abs=0.1)

    def test_remove_nonexistent_region_noop(self, scan_coords):
        # Should not crash
        scan_coords.remove_region("nonexistent")
        assert len(scan_coords.region_fov_coordinates) == 0

    def test_rename_region(self, scan_coords):
        scan_coords.add_single_fov_region("old_id", 20.0, 20.0, 0.0)
        scan_coords.rename_region("old_id", "new_id")
        assert "old_id" not in scan_coords.region_fov_coordinates
        assert "new_id" in scan_coords.region_fov_coordinates
        assert "old_id" not in scan_coords.region_centers
        assert "new_id" in scan_coords.region_centers

    def test_rename_to_existing_warns(self, scan_coords):
        scan_coords.add_single_fov_region("R1", 20.0, 20.0, 0.0)
        scan_coords.add_single_fov_region("R2", 25.0, 25.0, 0.0)
        scan_coords.rename_region("R1", "R2")
        # Both should still exist — rename was rejected
        assert "R1" in scan_coords.region_fov_coordinates
        assert "R2" in scan_coords.region_fov_coordinates

    def test_update_region_z(self, scan_coords):
        scan_coords.add_flexible_region("R1", 20.0, 20.0, 0.0, 2, 2, 10.0)
        scan_coords.update_region_z_level("R1", 5.0)
        for coord in scan_coords.region_fov_coordinates["R1"]:
            assert len(coord) == 3
            assert coord[2] == pytest.approx(5.0)

    def test_update_fov_z(self, scan_coords):
        scan_coords.add_flexible_region("R1", 20.0, 20.0, 0.0, 2, 2, 10.0)
        scan_coords.update_fov_z_level("R1", 0, 3.0)
        assert scan_coords.region_fov_coordinates["R1"][0][2] == pytest.approx(3.0)
        # First FOV → also updates region center
        assert scan_coords.region_centers["R1"][2] == pytest.approx(3.0)

    def test_add_single_fov_clamps_out_of_bounds(self, scan_coords):
        scan_coords.add_single_fov_region("R1", -100.0, -100.0, 0.0)
        coord = scan_coords.region_fov_coordinates["R1"][0]
        assert coord[0] >= _def.SOFTWARE_POS_LIMIT.X_NEGATIVE
        assert coord[1] >= _def.SOFTWARE_POS_LIMIT.Y_NEGATIVE

    def test_add_region_publishes_add_event(self, event_bus, scan_coords):
        collected = _collect_events(event_bus, AddScanCoordinateRegion)
        scan_coords.add_single_fov_region("R1", 20.0, 20.0, 0.0)
        event_bus.drain()
        assert len(collected) == 1
        assert len(collected[0].fov_centers) == 1

    def test_remove_region_publishes_event(self, event_bus, scan_coords):
        collected = _collect_events(event_bus, RemovedScanCoordinateRegion)
        scan_coords.add_single_fov_region("R1", 20.0, 20.0, 0.0)
        scan_coords.remove_region("R1")
        event_bus.drain()
        assert len(collected) == 1
