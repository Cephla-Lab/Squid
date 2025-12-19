from squid.backend.managers.scan_coordinates import (
    ScanCoordinates,
    AddScanCoordinateRegion,
    RemovedScanCoordinateRegion,
    ClearedScanCoordinates,
)
from squid.core.events import EventBus, ScanCoordinatesUpdated
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
