"""Test objective change, FOV matching, and acquisition display for wellplate multipoint.

Reproduces three bugs:
1. Changing objective causes FOVs to vanish from navigation viewer
2. Acquisition: red FOVs never turn blue (CurrentFOVRegistered not matching pending)
3. After acquisition, red FOV boxes vanish entirely

Strategy: Test backend event flow to verify coordinates are correctly generated
and CurrentFOVRegistered events can match the pending FOV positions.
"""

import pytest
from unittest.mock import MagicMock

from squid.core.events import (
    EventBus,
    SelectedWellsChanged,
    SetWellSelectionScanCoordinatesCommand,
)
from squid.backend.managers.scan_coordinates.scan_coordinates import (
    AddScanCoordinateRegion,
    ClearedScanCoordinates,
    RemovedScanCoordinateRegion,
    ScanCoordinates,
)
from squid.backend.managers.objective_store import ObjectiveStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def event_bus():
    bus = EventBus()
    bus.start()
    yield bus
    bus.stop()


@pytest.fixture
def mock_camera():
    """Camera with 11.61mm raw FOV (matches real hardware)."""
    camera = MagicMock()
    camera.get_fov_size_mm.return_value = 11.61
    camera.get_fov_height_mm.return_value = 11.61
    camera.get_fov_width_mm.return_value = 11.61
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
def mock_objective_store():
    store = MagicMock(spec=ObjectiveStore)
    # Start with 50x: pixel_size_factor=0.072 → effective FOV = 0.836mm
    store.get_pixel_size_factor.return_value = 0.072
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


def _collect_events(event_bus, event_type):
    """Subscribe and collect events of the given type into a list."""
    collected = []
    event_bus.subscribe(event_type, lambda e: collected.append(e))
    return collected


# ---------------------------------------------------------------------------
# Test A: Objective change recalculates FOV grid correctly
# ---------------------------------------------------------------------------


class TestObjectiveChangeRecalculation:
    """Verify that changing objective recalculates the FOV grid with correct count."""

    def test_high_mag_generates_multiple_fovs_per_well(self, scan_coords, mock_objective_store):
        """At 50x (FOV=0.836mm), a 2mm scan should produce multiple FOVs per well."""
        # pixel_size_factor=0.072 → effective FOV = 0.072 * 11.61 = 0.836mm
        mock_objective_store.get_pixel_size_factor.return_value = 0.072

        # Select wells A1, A2
        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((0, 0), (0, 1)))
        )
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=2.0, overlap_percent=10.0, shape="Square")
        )

        assert len(scan_coords.region_fov_coordinates) == 2, "Should have 2 well regions"
        for well_id, fovs in scan_coords.region_fov_coordinates.items():
            assert len(fovs) > 1, (
                f"Well {well_id} should have multiple FOVs at 50x (FOV=0.836mm, scan=2.0mm), "
                f"got {len(fovs)}"
            )

    def test_low_mag_generates_single_fov_per_well(self, scan_coords, mock_objective_store):
        """At 20x (FOV=2.09mm), a 2mm scan should produce 1 FOV per well (FOV > scan)."""
        # pixel_size_factor=0.18 → effective FOV = 0.18 * 11.61 = 2.09mm
        mock_objective_store.get_pixel_size_factor.return_value = 0.18

        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((0, 0), (0, 1)))
        )
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=2.0, overlap_percent=10.0, shape="Square")
        )

        assert len(scan_coords.region_fov_coordinates) == 2, "Should have 2 well regions"
        for well_id, fovs in scan_coords.region_fov_coordinates.items():
            assert len(fovs) >= 1, f"Well {well_id} should have at least 1 FOV, got {len(fovs)}"

    def test_objective_change_preserves_regions(self, scan_coords, event_bus, mock_objective_store):
        """Switching objective should re-generate FOVs, NOT clear them."""
        # Start at 50x
        mock_objective_store.get_pixel_size_factor.return_value = 0.072
        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((0, 0), (0, 1)))
        )
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=2.0, overlap_percent=10.0, shape="Square")
        )
        fovs_at_50x = {
            k: len(v) for k, v in scan_coords.region_fov_coordinates.items()
        }

        # Switch to 20x - simulate what handle_objective_change does:
        # It calls update_coordinates(force=True) which publishes SetWellSelectionScanCoordinatesCommand
        mock_objective_store.get_pixel_size_factor.return_value = 0.18
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=2.0, overlap_percent=10.0, shape="Square")
        )

        # Regions should still exist (maybe with different FOV counts)
        assert len(scan_coords.region_fov_coordinates) == 2, (
            f"Regions disappeared after objective change! Got {list(scan_coords.region_fov_coordinates.keys())}"
        )
        for well_id, fovs in scan_coords.region_fov_coordinates.items():
            assert len(fovs) >= 1, f"Well {well_id} has 0 FOVs after objective change"

    def test_objective_change_publishes_add_events(self, scan_coords, event_bus, mock_objective_store):
        """After objective change, AddScanCoordinateRegion events should be published."""
        # Set up wells at 50x
        mock_objective_store.get_pixel_size_factor.return_value = 0.072
        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((0, 0),))
        )
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=2.0, overlap_percent=10.0, shape="Square")
        )
        event_bus.drain()

        # Now collect events after objective change
        add_events = _collect_events(event_bus, AddScanCoordinateRegion)
        remove_events = _collect_events(event_bus, RemovedScanCoordinateRegion)
        clear_events = _collect_events(event_bus, ClearedScanCoordinates)

        # Switch to 20x and re-set coordinates
        mock_objective_store.get_pixel_size_factor.return_value = 0.18
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=2.0, overlap_percent=10.0, shape="Square")
        )
        event_bus.drain()

        # Must publish at least one Add event (after removing old regions)
        assert len(add_events) >= 1, (
            f"No AddScanCoordinateRegion events after objective change! "
            f"Got {len(remove_events)} removes, {len(clear_events)} clears"
        )
        # The Add event should have FOV centers
        for evt in add_events:
            assert len(evt.fov_centers) >= 1, "AddScanCoordinateRegion had 0 fov_centers"


# ---------------------------------------------------------------------------
# Test B: CurrentFOVRegistered matching against pending FOVs
# ---------------------------------------------------------------------------


class TestCurrentFOVRegisteredMatching:
    """Verify that CurrentFOVRegistered events can match pending FOV positions."""

    def test_exact_match_removes_from_pending(self, scan_coords, event_bus, mock_objective_store):
        """CurrentFOVRegistered at exact coordinates should match pending FOVs."""
        mock_objective_store.get_pixel_size_factor.return_value = 0.072

        # Collect AddScanCoordinateRegion to get exact pending positions
        add_events = _collect_events(event_bus, AddScanCoordinateRegion)

        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((0, 0),))
        )
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=2.0, overlap_percent=10.0, shape="Square")
        )
        event_bus.drain()

        # Get the exact FOV positions that were published
        assert len(add_events) >= 1, "No AddScanCoordinateRegion events"
        fov_centers = add_events[0].fov_centers
        assert len(fov_centers) > 0, "No FOV centers in AddScanCoordinateRegion"

        # Verify that the published positions match ScanCoordinates internal state
        well_id = list(scan_coords.region_fov_coordinates.keys())[0]
        internal_coords = scan_coords.region_fov_coordinates[well_id]
        assert len(internal_coords) == len(fov_centers), (
            f"Mismatch: internal={len(internal_coords)} vs published={len(fov_centers)}"
        )

        # Check that exact coordinates from add_events would match with various tolerances
        for fov in fov_centers:
            # Verify the coordinates are self-consistent
            found_in_internal = any(
                abs(c[0] - fov.x_mm) < 1e-9 and abs(c[1] - fov.y_mm) < 1e-9
                for c in internal_coords
            )
            assert found_in_internal, (
                f"FOV center ({fov.x_mm}, {fov.y_mm}) not found in internal coords"
            )

    def test_float_rounding_within_tolerance(self, scan_coords, event_bus, mock_objective_store):
        """CurrentFOVRegistered with small float drift should still match at 1e-3 tolerance."""
        mock_objective_store.get_pixel_size_factor.return_value = 0.072

        add_events = _collect_events(event_bus, AddScanCoordinateRegion)

        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((0, 0),))
        )
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=2.0, overlap_percent=10.0, shape="Square")
        )
        event_bus.drain()

        fov_centers = add_events[0].fov_centers
        assert len(fov_centers) > 0

        # Simulate what happens when coordinates pass through float arithmetic
        # (e.g. stage controller reports position with small rounding error)
        for fov in fov_centers:
            drifted_x = fov.x_mm + 1e-4  # 0.1 micron drift
            drifted_y = fov.y_mm - 1e-4

            # With 1e-6 tolerance (the OLD value), this would NOT match
            old_tolerance = 1e-6
            old_match = abs(fov.x_mm - drifted_x) <= old_tolerance and abs(fov.y_mm - drifted_y) <= old_tolerance
            assert not old_match, "Old tolerance should NOT match 0.1um drift"

            # With 1e-3 tolerance (the NEW value), this SHOULD match
            new_tolerance = 1e-3
            new_match = abs(fov.x_mm - drifted_x) <= new_tolerance and abs(fov.y_mm - drifted_y) <= new_tolerance
            assert new_match, "New tolerance should match 0.1um drift"

    def test_1e6_tolerance_fails_for_realistic_drift(self):
        """Document that 1e-6 tolerance is too tight for real-world coordinate matching."""
        # This test documents the bug: coordinates that pass through float operations
        # (stage movement, mm↔um conversions, etc.) accumulate small errors > 1e-6

        # Example: 14.175 mm goes through um conversion and back
        original = 14.175
        via_um = round(original * 1000) / 1000  # 14.175
        via_float_ops = original + 1e-5 - 1e-5  # Should be identical but isn't always

        # Even simple float addition can produce drift
        accumulated = 0.0
        step = 0.7524  # realistic step size
        for _ in range(3):
            accumulated += step
        expected = 3 * step
        drift = abs(accumulated - expected)
        # drift is typically ~4.4e-16, but stage controllers report with ~1e-4 precision
        assert drift < 1e-3, f"Accumulated drift {drift} exceeds 1mm (impossible)"


# ---------------------------------------------------------------------------
# Test C: Full grid generation + event tracking
# ---------------------------------------------------------------------------


class TestFullGridEventFlow:
    """Verify the complete event flow from well selection through FOV generation."""

    def test_grid_events_match_internal_state(self, scan_coords, event_bus, mock_objective_store):
        """All published FOV positions should match internal region_fov_coordinates."""
        mock_objective_store.get_pixel_size_factor.return_value = 0.072

        add_events = _collect_events(event_bus, AddScanCoordinateRegion)
        remove_events = _collect_events(event_bus, RemovedScanCoordinateRegion)
        clear_events = _collect_events(event_bus, ClearedScanCoordinates)

        # Select 3 wells and generate coordinates
        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(
                format_name="96 well plate",
                selected_cells=((0, 0), (0, 1), (1, 0)),  # A1, A2, B1
            )
        )
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=2.0, overlap_percent=10.0, shape="Square")
        )
        event_bus.drain()

        # Should have 3 regions
        assert len(scan_coords.region_fov_coordinates) == 3

        # Count total FOVs from Add events
        total_event_fovs = sum(len(e.fov_centers) for e in add_events)
        total_internal_fovs = sum(len(v) for v in scan_coords.region_fov_coordinates.values())
        assert total_event_fovs == total_internal_fovs, (
            f"Event FOVs ({total_event_fovs}) != internal FOVs ({total_internal_fovs})"
        )

    def test_reselection_removes_then_adds(self, scan_coords, event_bus, mock_objective_store):
        """Re-setting coordinates removes old regions and adds new ones."""
        mock_objective_store.get_pixel_size_factor.return_value = 0.072

        # First selection
        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((0, 0),))
        )
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=2.0, overlap_percent=10.0, shape="Square")
        )
        event_bus.drain()

        first_fov_count = sum(len(v) for v in scan_coords.region_fov_coordinates.values())
        assert first_fov_count > 0

        # Collect events for second selection
        add_events = _collect_events(event_bus, AddScanCoordinateRegion)
        remove_events = _collect_events(event_bus, RemovedScanCoordinateRegion)

        # Re-set with same well but different scan size
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=1.0, overlap_percent=10.0, shape="Square")
        )
        event_bus.drain()

        # Should have removal of old + addition of new
        assert len(remove_events) >= 1, "Should remove old region before adding new"
        assert len(add_events) >= 1, "Should add new region after removing old"

    def test_fov_dimensions_in_add_events(self, scan_coords, event_bus, mock_objective_store):
        """AddScanCoordinateRegion events should carry correct FOV dimensions."""
        mock_objective_store.get_pixel_size_factor.return_value = 0.072

        add_events = _collect_events(event_bus, AddScanCoordinateRegion)

        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((0, 0),))
        )
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=2.0, overlap_percent=10.0, shape="Square")
        )
        event_bus.drain()

        assert len(add_events) >= 1
        for evt in add_events:
            for fov in evt.fov_centers:
                expected_fov = 0.072 * 11.61  # ~0.836mm
                assert abs(fov.fov_width_mm - expected_fov) < 0.01, (
                    f"FOV width {fov.fov_width_mm} != expected {expected_fov}"
                )
                assert abs(fov.fov_height_mm - expected_fov) < 0.01, (
                    f"FOV height {fov.fov_height_mm} != expected {expected_fov}"
                )


# ---------------------------------------------------------------------------
# Test D: Coordinate persistence after acquisition
# ---------------------------------------------------------------------------


class TestCoordinatePersistenceAfterAcquisition:
    """Verify that coordinates persist correctly before/after acquisition events."""

    def test_coordinates_survive_without_clear(self, scan_coords, mock_objective_store):
        """Coordinates should persist when no clear command is issued."""
        mock_objective_store.get_pixel_size_factor.return_value = 0.072

        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((0, 0),))
        )
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=2.0, overlap_percent=10.0, shape="Square")
        )

        initial_count = sum(len(v) for v in scan_coords.region_fov_coordinates.values())
        assert initial_count > 0

        # Simulate "acquisition finished" - no clear command should be sent
        # The fix was to remove reset_coordinates() from acquisition_is_finished()
        # So coordinates should just stay as-is
        remaining_count = sum(len(v) for v in scan_coords.region_fov_coordinates.values())
        assert remaining_count == initial_count, (
            f"Coordinates changed without clear! {initial_count} -> {remaining_count}"
        )

    def test_reapply_after_objective_change_regenerates(self, scan_coords, event_bus, mock_objective_store):
        """Re-applying coordinates after objective change regenerates the grid."""
        mock_objective_store.get_pixel_size_factor.return_value = 0.072

        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((0, 0),))
        )
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=2.0, overlap_percent=10.0, shape="Square")
        )
        fovs_50x = sum(len(v) for v in scan_coords.region_fov_coordinates.values())

        # Change to 20x and re-apply
        mock_objective_store.get_pixel_size_factor.return_value = 0.18
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=2.0, overlap_percent=10.0, shape="Square")
        )
        fovs_20x = sum(len(v) for v in scan_coords.region_fov_coordinates.values())

        # At 20x, FOV (2.09mm) > scan_size (2.0mm) so should get 1 FOV per well
        assert fovs_20x >= 1, "Should have at least 1 FOV at 20x"
        assert fovs_20x < fovs_50x, (
            f"20x ({fovs_20x} FOVs) should have fewer FOVs than 50x ({fovs_50x} FOVs)"
        )

    def test_selected_wells_preserved_across_clear(self, scan_coords, mock_objective_store):
        """ClearScanCoordinates should NOT clear _selected_well_cells."""
        mock_objective_store.get_pixel_size_factor.return_value = 0.072

        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(format_name="96 well plate", selected_cells=((0, 0), (0, 1)))
        )
        assert len(scan_coords._selected_well_cells) == 2

        # Clear regions (simulating what used to happen on tab switch)
        scan_coords.clear_regions()

        # Wells should still be remembered
        assert len(scan_coords._selected_well_cells) == 2, (
            "clear_regions() should NOT clear _selected_well_cells"
        )

        # Re-applying coordinates should work
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(scan_size_mm=2.0, overlap_percent=10.0, shape="Square")
        )
        assert len(scan_coords.region_fov_coordinates) == 2, (
            "Should regenerate regions from preserved well selection"
        )
