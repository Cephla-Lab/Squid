import tests.control.gui_test_stubs as gts
import control.utils
import squid.stage
from control.core.scan_coordinates import (
    ScanCoordinates,
    ScanCoordinatesUpdate,
    AddScanCoordinateRegion,
    RemovedScanCoordinateRegion,
    ClearedScanCoordinates,
)
from control.core.core import FocusMap
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

    single_fov_center = (20.0, 20.0, 3.0)
    flexible_center = (30.0, 30.0, 0.5)
    well_center = (25.0, 25.0, scope.stage.get_pos().z_mm)
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


def _make_scan_coordinates(pattern, region_names):
    """Build a ScanCoordinates with the given acquisition pattern and region names.

    Region positions are irrelevant to sorting, so they are all zeroed.
    """
    scope = Microscope.build_from_global_config(simulated=True)
    sc = ScanCoordinates(scope.objective_store, scope.stage, scope.camera)
    sc.acquisition_pattern = pattern
    sc.region_centers = {name: [0.0, 0.0] for name in region_names}
    sc.region_fov_coordinates = {name: [(0.0, 0.0, 0.0)] for name in region_names}
    return sc


def test_row_to_index_roundtrips_multi_letter_rows():
    """Row labels are bijective base-26, so AA is row 26 - not a duplicate of A."""
    for index in range(32):  # A..AF, the rows of a 1536-well plate
        assert control.utils.row_to_index(ScanCoordinates._index_to_row(index)) == index

    assert control.utils.row_to_index("A") == 0
    assert control.utils.row_to_index("Z") == 25
    assert control.utils.row_to_index("AA") == 26
    assert control.utils.row_to_index("AF") == 31


def test_sort_coordinates_orders_multi_letter_rows_after_single_letter():
    """1536-well rows AA..AF must come after Z, not interleave with A..F."""
    sc = _make_scan_coordinates("Unidirectional", ["AB1", "B1", "AA2", "A1", "Z1", "AA1", "A2"])

    sc.sort_coordinates()

    assert list(sc.region_centers.keys()) == ["A1", "A2", "B1", "Z1", "AA1", "AA2", "AB1"]


def test_sort_coordinates_s_pattern_treats_multi_letter_rows_as_distinct_rows():
    """Serpentine grouping must key on the whole row label, not its first character."""
    sc = _make_scan_coordinates("S-Pattern", ["A1", "A2", "AA1", "AA2", "AB1", "AB2"])

    sc.sort_coordinates()

    # Three separate rows (A, AA, AB), so only the middle one is reversed.
    assert list(sc.region_centers.keys()) == ["A1", "A2", "AA2", "AA1", "AB1", "AB2"]


def test_sort_coordinates_keeps_non_well_region_names():
    """Region names that aren't well IDs sort last instead of raising."""
    sc = _make_scan_coordinates("S-Pattern", ["B1", "current", "A1", "my region"])

    sc.sort_coordinates()

    assert list(sc.region_centers.keys()) == ["A1", "B1", "current", "my region"]


def test_focus_grid_for_region_registered_without_a_shape():
    """Widget code that writes the region dicts directly may leave no shape; the focus map grid
    (generate_grid -> region_contains_coordinate -> get_region_shape) used to raise KeyError on it."""
    sc = _make_scan_coordinates("Unidirectional", ["loaded"])
    sc.region_fov_coordinates["loaded"] = [(20.0 + 0.5 * i, 20.0 + 0.5 * j) for i in range(4) for j in range(4)]
    assert "loaded" not in sc.region_shapes

    grid = FocusMap().generate_grid_coordinates(sc, rows=3, cols=3)

    assert len(grid["loaded"]) == 9
    assert sc.get_region_shape("loaded") == "Square"


def _fresh_scan_coordinates(updates):
    from control.core.scan_coordinates import ScanCoordinates

    scope = Microscope.build_from_global_config(True)
    return ScanCoordinates(scope.objective_store, scope.stage, scope.camera, update_callback=updates.append)


def test_add_region_from_fovs_stores_3tuples_center_shape_and_notifies():
    import pytest

    from control.core.scan_coordinates import AddScanCoordinateRegion

    updates = []
    sc = _fresh_scan_coordinates(updates)

    sc.add_region_from_fovs("A1", [[10.0, 10.0, 3.0], [10.5, 10.0, 3.2]])

    assert sc.region_fov_coordinates["A1"] == [(10.0, 10.0, 3.0), (10.5, 10.0, 3.2)]
    assert sc.region_centers["A1"] == [10.25, 10.0, pytest.approx(3.1)]
    assert sc.region_shapes["A1"] == "Manual"
    assert isinstance(updates[-1], AddScanCoordinateRegion) and len(updates[-1].fov_centers) == 2


def test_add_region_from_fovs_without_full_z_stores_xy_only():
    sc = _fresh_scan_coordinates([])
    sc.add_region_from_fovs("B2", [[20.0, 20.0, 1.0], [20.5, 20.0]])
    assert sc.region_fov_coordinates["B2"] == [(20.0, 20.0), (20.5, 20.0)]
    assert sc.region_centers["B2"] == [20.25, 20.0]


def test_add_region_from_fovs_rejects_bad_input_without_storing():
    import pytest

    import control._def

    sc = _fresh_scan_coordinates([])
    with pytest.raises(ValueError):
        sc.add_region_from_fovs("empty", [])
    with pytest.raises(ValueError):
        sc.add_region_from_fovs("far", [[control._def.SOFTWARE_POS_LIMIT.X_POSITIVE + 100.0, 10.0]])
    with pytest.raises(ValueError):
        sc.add_region_from_fovs("deep", [[10.0, 10.0, control._def.SOFTWARE_POS_LIMIT.Z_POSITIVE + 100.0]])
    assert not sc.has_regions()
