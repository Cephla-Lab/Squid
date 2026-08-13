import control.microscope
from control.core.multi_point_utils import ScanPositionInformation
from control.core.scan_coordinates import ScanCoordinates


def test_from_scan_coordinates_snapshots_fov_lists():
    scope = control.microscope.Microscope.build_from_global_config(True)
    sc = ScanCoordinates(scope.objective_store, scope.stage, scope.camera)
    sc.region_centers = {"A1": [10.0, 10.0]}
    sc.region_fov_coordinates = {"A1": [(10.0, 10.0), (10.5, 10.0)]}

    info = ScanPositionInformation.from_scan_coordinates(sc)
    info.scan_region_fov_coords_mm["A1"][0] = (10.0, 10.0, 3.0)

    assert sc.region_fov_coordinates["A1"][0] == (10.0, 10.0)
