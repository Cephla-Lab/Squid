"""Test the exact event flow for Start Acquisition with scan coordinates.

Reproduces the user bug: "No FOVs defined" when starting acquisition after selecting wells.
Root cause: stage starts at (0,0) which is outside machine position limits, and
filter_coordinates_in_bounds drops all FOVs.
"""

import pytest
from unittest.mock import MagicMock

from squid.core.events import (
    ObjectiveChanged,
    EventBus,
    SelectedWellsChanged,
    SetWellSelectionScanCoordinatesCommand,
    SetLiveScanCoordinatesCommand,
    ClearScanCoordinatesCommand,
    SortScanCoordinatesCommand,
    ActiveAcquisitionTabChanged,
    WellplateFormatChanged,
)
from squid.backend.managers.scan_coordinates.scan_coordinates import ScanCoordinates
from squid.backend.managers.objective_store import ObjectiveStore


@pytest.fixture
def event_bus():
    bus = EventBus()
    bus.start()
    yield bus
    bus.stop()


@pytest.fixture
def mock_camera():
    camera = MagicMock()
    camera.get_fov_size_mm.return_value = 1.0  # 1mm FOV
    camera.get_fov_height_mm.return_value = 1.0
    return camera


@pytest.fixture
def mock_stage():
    stage = MagicMock()
    pos = MagicMock()
    pos.x_mm = 0.0
    pos.y_mm = 0.0
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


class TestWellSelectionAcquisitionFlow:
    """Test the exact event flow when user selects wells then clicks Start Acquisition."""

    def test_direct_well_selection_flow(self, scan_coords):
        """Directly call handlers to verify logic works."""
        # Step 1: User selects wells
        scan_coords._on_selected_wells_changed(
            SelectedWellsChanged(
                format_name="96 well plate",
                selected_cells=((0, 0), (0, 1)),  # wells A1, A2
            )
        )
        assert len(scan_coords._selected_well_cells) == 2

        # Step 2: Widget publishes SetWellSelectionScanCoordinatesCommand
        scan_coords._on_set_well_selection_scan_coordinates(
            SetWellSelectionScanCoordinatesCommand(
                scan_size_mm=1.0,
                overlap_percent=10.0,
                shape="Square",
            )
        )

        # Step 3: Verify coordinates were generated
        assert len(scan_coords.region_fov_coordinates) > 0, (
            f"Expected regions but got empty. "
            f"_selected_well_cells={scan_coords._selected_well_cells}, "
            f"_well_selection_scan_size_mm={scan_coords._well_selection_scan_size_mm}"
        )
        total_fovs = sum(len(c) for c in scan_coords.region_fov_coordinates.values())
        assert total_fovs > 0, f"Expected FOVs but got 0. regions={scan_coords.region_fov_coordinates}"

    def test_eventbus_well_selection_flow(self, event_bus, scan_coords):
        """Test the full event flow through the EventBus, matching toggle_acquisition."""
        # Step 1: User selects wells (SelectedWellsChanged)
        event_bus.publish(SelectedWellsChanged(
            format_name="96 well plate",
            selected_cells=((0, 0), (0, 1)),
        ))
        event_bus.drain()

        assert len(scan_coords._selected_well_cells) == 2, (
            f"SelectedWellsChanged not processed. cells={scan_coords._selected_well_cells}"
        )

        # Step 2: toggle_acquisition publishes coordinate command then start
        event_bus.publish(SetWellSelectionScanCoordinatesCommand(
            scan_size_mm=1.0,
            overlap_percent=10.0,
            shape="Square",
        ))
        event_bus.publish(SortScanCoordinatesCommand())
        event_bus.drain()

        # Step 3: Verify coordinates exist before StartAcquisitionCommand would be processed
        assert len(scan_coords.region_fov_coordinates) > 0, (
            f"No regions after SetWellSelectionScanCoordinatesCommand. "
            f"cells={scan_coords._selected_well_cells}, "
            f"scan_size={scan_coords._well_selection_scan_size_mm}"
        )
        total_fovs = sum(len(c) for c in scan_coords.region_fov_coordinates.values())
        assert total_fovs > 0, f"No FOVs. regions={list(scan_coords.region_fov_coordinates.keys())}"

    def test_current_position_out_of_bounds_gets_clamped(self, event_bus, scan_coords):
        """Test that Current Position mode clamps out-of-bounds positions instead of filtering."""
        # Stage at (0, 0) which may be outside SOFTWARE_POS_LIMIT
        event_bus.publish(SetLiveScanCoordinatesCommand(
            x_mm=0.0,
            y_mm=0.0,
            scan_size_mm=0.1,
            overlap_percent=10.0,
            shape="Square",
        ))
        event_bus.drain()

        # The fix clamps to bounds instead of dropping, so we should always get at least 1 FOV
        assert len(scan_coords.region_fov_coordinates) > 0, "No regions in Current Position mode"
        total_fovs = sum(len(c) for c in scan_coords.region_fov_coordinates.values())
        assert total_fovs > 0, "No FOVs in Current Position mode (position should be clamped to bounds)"

    def test_current_position_in_bounds(self, event_bus, scan_coords):
        """Test Current Position mode with a position within bounds."""
        # Use a position that's definitely within bounds for any machine config
        event_bus.publish(SetLiveScanCoordinatesCommand(
            x_mm=25.0,
            y_mm=25.0,
            scan_size_mm=0.1,
            overlap_percent=10.0,
            shape="Square",
        ))
        event_bus.drain()

        assert len(scan_coords.region_fov_coordinates) > 0
        total_fovs = sum(len(c) for c in scan_coords.region_fov_coordinates.values())
        assert total_fovs > 0

    def test_clear_then_well_selection(self, event_bus, scan_coords):
        """Test that ClearScanCoordinatesCommand doesn't clear _selected_well_cells."""
        # User selects wells
        event_bus.publish(SelectedWellsChanged(
            format_name="96 well plate",
            selected_cells=((0, 0),),
        ))
        event_bus.drain()

        # Tab switch clears coordinates
        event_bus.publish(ClearScanCoordinatesCommand())
        event_bus.drain()

        assert len(scan_coords.region_fov_coordinates) == 0, "Regions should be cleared"
        assert len(scan_coords._selected_well_cells) == 1, (
            "ClearScanCoordinatesCommand should NOT clear _selected_well_cells"
        )

        # toggle_acquisition re-publishes coordinates
        event_bus.publish(SetWellSelectionScanCoordinatesCommand(
            scan_size_mm=1.0,
            overlap_percent=10.0,
            shape="Square",
        ))
        event_bus.drain()

        assert len(scan_coords.region_fov_coordinates) > 0, (
            "Coordinates should be regenerated from stored _selected_well_cells"
        )

    def test_full_tab_switch_then_acquisition(self, event_bus, scan_coords):
        """Simulate: switch to wellplate tab, select wells, click Start Acquisition."""
        # 1. Tab switch fires ClearScanCoordinatesCommand + ActiveAcquisitionTabChanged
        event_bus.publish(ClearScanCoordinatesCommand())
        event_bus.publish(ActiveAcquisitionTabChanged(active_tab="wellplate"))
        event_bus.drain()

        # 2. User selects wells
        event_bus.publish(SelectedWellsChanged(
            format_name="96 well plate",
            selected_cells=((0, 0), (1, 0), (0, 1)),
        ))
        event_bus.drain()

        assert len(scan_coords._selected_well_cells) == 3

        # 3. User clicks Start Acquisition (toggle_acquisition flow)
        event_bus.publish(SetWellSelectionScanCoordinatesCommand(
            scan_size_mm=1.0,
            overlap_percent=10.0,
            shape="Square",
        ))
        event_bus.publish(SortScanCoordinatesCommand())
        event_bus.drain()

        total_fovs = sum(len(c) for c in scan_coords.region_fov_coordinates.values())
        assert total_fovs > 0, (
            f"Full flow: No FOVs. regions={list(scan_coords.region_fov_coordinates.keys())}, "
            f"cells={scan_coords._selected_well_cells}"
        )

    def test_well_selection_reapplies_after_wellplate_format_change(self, event_bus, scan_coords):
        """Format change should regenerate selected-well coordinates, not leave regions empty."""
        event_bus.publish(SelectedWellsChanged(
            format_name="96 well plate",
            selected_cells=((0, 0), (0, 1)),
        ))
        event_bus.publish(SetWellSelectionScanCoordinatesCommand(
            scan_size_mm=1.0,
            overlap_percent=10.0,
            shape="Square",
        ))
        event_bus.drain()
        assert len(scan_coords.region_fov_coordinates) > 0

        event_bus.publish(WellplateFormatChanged(
            format_name="384 well plate",
            rows=16,
            cols=24,
            well_spacing_mm=4.5,
            well_size_mm=3.4,
            a1_x_mm=12.13,
            a1_y_mm=8.99,
            a1_x_pixel=143,
            a1_y_pixel=90,
            number_of_skip=0,
        ))
        event_bus.drain()

        assert len(scan_coords.region_fov_coordinates) > 0, (
            "Wellplate format change should regenerate well-selection coordinates"
        )
        total_fovs = sum(len(c) for c in scan_coords.region_fov_coordinates.values())
        assert total_fovs > 0

    def test_well_selection_reapplies_after_objective_change(self, event_bus, scan_coords, mock_objective_store):
        """Objective change should regenerate selected-well coordinates using new pixel factor."""
        mock_objective_store.get_pixel_size_factor.return_value = 1.0
        event_bus.publish(SelectedWellsChanged(
            format_name="96 well plate",
            selected_cells=((0, 0),),
        ))
        event_bus.publish(SetWellSelectionScanCoordinatesCommand(
            scan_size_mm=3.0,
            overlap_percent=10.0,
            shape="Square",
        ))
        event_bus.drain()
        count_before = sum(len(c) for c in scan_coords.region_fov_coordinates.values())
        assert count_before > 0

        mock_objective_store.get_pixel_size_factor.return_value = 2.0
        event_bus.publish(ObjectiveChanged(
            position=0,
            objective_name="20x",
            magnification=20.0,
            pixel_size_um=2.0,
        ))
        event_bus.drain()
        count_after = sum(len(c) for c in scan_coords.region_fov_coordinates.values())

        assert count_after > 0
        assert count_after != count_before, (
            "Objective change should regenerate with updated FOV scaling"
        )
