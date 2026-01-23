import tests.control.gui_test_stubs as gts
import squid.stage
from control.core.scan_coordinates import (
    ScanCoordinates,
    ScanCoordinatesUpdate,
    AddScanCoordinateRegion,
    RemovedScanCoordinateRegion,
    ClearedScanCoordinates,
)
from control.microscope import Microscope


def test_scan_coordinates_basic_operation():
    # The scope creates a scan config, but just for sanity/clarity we'll create our own below.
    scope = Microscope.build_from_global_config(simulated=True)

    add_count = 0
    remove_count = 0
    clear_count = 0
    update_count = 0

    def test_callback(update: ScanCoordinatesUpdate):
        nonlocal add_count, remove_count, clear_count, update_count
        if isinstance(update, AddScanCoordinateRegion):
            add_count += 1
        elif isinstance(update, RemovedScanCoordinateRegion):
            remove_count += 1
        elif isinstance(update, ClearedScanCoordinates):
            clear_count += 1
        else:
            raise ValueError(f"Unknown update case in scan coordinates test: {update.__class__}")
        update_count += 1

    scan_coordinates = ScanCoordinates(scope.objective_store, scope.stage, scope.camera, update_callback=test_callback)

    single_fov_center = (6.0, 7.0, 3.0)
    flexible_center = (8.0, 9.0, 0.5)
    well_center = (6.5, 8.5, scope.stage.get_pos().z_mm)
    scan_coordinates.add_single_fov_region("single_fov", *single_fov_center)
    scan_coordinates.add_flexible_region("flexible_region", *flexible_center, 2, 2, 10)
    scan_coordinates.add_region("well_region", well_center[0], well_center[1], 4, 10, "Circle")

    assert add_count == 3
    assert remove_count == 0
    assert clear_count == 0
    assert update_count == 3

    assert set(scan_coordinates.region_centers.keys()) == {"single_fov", "flexible_region", "well_region"}
    assert set([tuple(c) for c in scan_coordinates.region_centers.values()]) == {
        single_fov_center,
        flexible_center,
        well_center,
    }

    scan_coordinates.remove_region("single_fov")
    assert add_count == 3
    assert remove_count == 1
    assert clear_count == 0
    assert update_count == 4

    assert set(scan_coordinates.region_centers.keys()) == {"flexible_region", "well_region"}
    assert set([tuple(c) for c in scan_coordinates.region_centers.values()]) == {flexible_center, well_center}

    scan_coordinates.remove_region("well_region")
    assert add_count == 3
    assert remove_count == 2
    assert clear_count == 0
    assert update_count == 5

    assert set(scan_coordinates.region_centers.keys()) == {"flexible_region"}
    assert set([tuple(c) for c in scan_coordinates.region_centers.values()]) == {flexible_center}

    scan_coordinates.clear_regions()
    assert add_count == 3
    assert remove_count == 2
    assert clear_count == 1
    assert update_count == 6

    assert len(scan_coordinates.region_centers.keys()) == 0
    assert len(scan_coordinates.region_centers.values()) == 0


def test_sort_coordinates_manual_regions_preserve_drawing_order():
    """Manual regions stay in drawing order, come before wells, and ignore S-Pattern."""
    scope = Microscope.build_from_global_config(simulated=True)
    sc = ScanCoordinates(scope.objective_store, scope.stage, scope.camera)
    sc.acquisition_pattern = "S-Pattern"

    # Set up regions directly (bypass coordinate validation)
    sc.region_centers = {
        "A1": [10.0, 10.0],
        "manual1": [99.0, 99.0],  # Drawn second, far position
        "B1": [10.0, 20.0],
        "manual0": [10.0, 10.0],  # Drawn first, same position as A1
        "B2": [20.0, 20.0],
        "A2": [20.0, 10.0],
    }
    sc.region_fov_coordinates = {k: [(v[0], v[1], 0.0)] for k, v in sc.region_centers.items()}

    sc.sort_coordinates()

    keys = list(sc.region_centers.keys())
    # Manual regions first (drawing order), then wells (S-Pattern: row B reversed)
    assert keys == ["manual0", "manual1", "A1", "A2", "B2", "B1"]
