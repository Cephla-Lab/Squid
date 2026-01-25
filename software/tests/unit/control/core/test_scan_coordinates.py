from squid.backend.managers.scan_coordinates import (
    ScanCoordinates,
    AddScanCoordinateRegion,
    RemovedScanCoordinateRegion,
    ClearedScanCoordinates,
)
from squid.core.events import EventBus, ScanCoordinatesUpdated, ClearScanCoordinatesCommand
from squid.backend.microscope import Microscope


def test_scan_coordinates_basic_operation():
    # The scope creates a scan config, but just for sanity/clarity we'll create our own below.
    scope = Microscope.build_from_global_config(simulated=True)

    add_count = 0
    remove_count = 0
    clear_count = 0
    updated_count = 0

    bus = EventBus()

    def on_add(_update: AddScanCoordinateRegion) -> None:
        nonlocal add_count
        add_count += 1

    def on_remove(_update: RemovedScanCoordinateRegion) -> None:
        nonlocal remove_count
        remove_count += 1

    def on_clear(_update: ClearedScanCoordinates) -> None:
        nonlocal clear_count
        clear_count += 1

    def on_updated(_update: ScanCoordinatesUpdated) -> None:
        nonlocal updated_count
        updated_count += 1

    scan_coordinates = ScanCoordinates(
        scope.objective_store, scope.stage, scope.camera, event_bus=bus
    )
    bus.subscribe(AddScanCoordinateRegion, on_add)
    bus.subscribe(RemovedScanCoordinateRegion, on_remove)
    bus.subscribe(ClearedScanCoordinates, on_clear)
    bus.subscribe(ScanCoordinatesUpdated, on_updated)

    # Use coordinates within simulated stage limits (typically 10-110mm range)
    single_fov_center = (50.0, 50.0, 3.0)
    flexible_center = (60.0, 60.0, 0.5)
    well_center = (70.0, 70.0, scope.stage.get_pos().z_mm)
    scan_coordinates.add_single_fov_region("single_fov", *single_fov_center)
    scan_coordinates.add_flexible_region("flexible_region", *flexible_center, 2, 2, 10)
    scan_coordinates.add_region(
        "well_region", well_center[0], well_center[1], 4, 10, "Circle"
    )
    bus.drain()

    assert add_count == 3
    assert remove_count == 0
    assert clear_count == 0
    assert updated_count == 3

    assert set(scan_coordinates.region_centers.keys()) == {
        "single_fov",
        "flexible_region",
        "well_region",
    }
    assert set([tuple(c) for c in scan_coordinates.region_centers.values()]) == {
        single_fov_center,
        flexible_center,
        well_center,
    }

    scan_coordinates.remove_region("single_fov")
    bus.drain()
    assert add_count == 3
    assert remove_count == 1
    assert clear_count == 0
    assert updated_count == 4

    assert set(scan_coordinates.region_centers.keys()) == {
        "flexible_region",
        "well_region",
    }
    assert set([tuple(c) for c in scan_coordinates.region_centers.values()]) == {
        flexible_center,
        well_center,
    }

    scan_coordinates.remove_region("well_region")
    bus.drain()
    assert add_count == 3
    assert remove_count == 2
    assert clear_count == 0
    assert updated_count == 5

    assert set(scan_coordinates.region_centers.keys()) == {"flexible_region"}
    assert set([tuple(c) for c in scan_coordinates.region_centers.values()]) == {
        flexible_center
    }

    scan_coordinates.clear_regions()
    bus.drain()
    assert add_count == 3
    assert remove_count == 2
    assert clear_count == 1
    assert updated_count == 6

    assert len(scan_coordinates.region_centers.keys()) == 0
    assert len(scan_coordinates.region_centers.values()) == 0


def test_scan_coordinates_shutdown_unsubscribes() -> None:
    scope = Microscope.build_from_global_config(simulated=True)
    bus = EventBus()
    scan_coordinates = ScanCoordinates(
        scope.objective_store, scope.stage, scope.camera, event_bus=bus
    )

    scan_coordinates.add_single_fov_region("single_fov", 50.0, 50.0, 3.0)
    bus.drain()

    bus.publish(ClearScanCoordinatesCommand())
    bus.drain()
    assert len(scan_coordinates.region_centers.keys()) == 0

    scan_coordinates.add_single_fov_region("single_fov", 50.0, 50.0, 3.0)
    bus.drain()

    scan_coordinates.shutdown()
    bus.publish(ClearScanCoordinatesCommand())
    bus.drain()

    assert len(scan_coordinates.region_centers.keys()) == 1


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


def test_is_manual_region():
    """Test the _is_manual_region helper method."""
    scope = Microscope.build_from_global_config(simulated=True)
    sc = ScanCoordinates(scope.objective_store, scope.stage, scope.camera)

    # Should match manual regions
    assert sc._is_manual_region("manual") is True
    assert sc._is_manual_region("manual0") is True
    assert sc._is_manual_region("manual1") is True
    assert sc._is_manual_region("manual99") is True

    # Should not match well names or other regions
    assert sc._is_manual_region("A1") is False
    assert sc._is_manual_region("B12") is False
    assert sc._is_manual_region("current") is False
    assert sc._is_manual_region("xymanual") is False  # Doesn't start with "manual"


def test_get_manual_region_index():
    """Test the _get_manual_region_index helper method."""
    scope = Microscope.build_from_global_config(simulated=True)
    sc = ScanCoordinates(scope.objective_store, scope.stage, scope.camera)

    assert sc._get_manual_region_index("manual") == 0
    assert sc._get_manual_region_index("manual0") == 0
    assert sc._get_manual_region_index("manual1") == 1
    assert sc._get_manual_region_index("manual42") == 42
