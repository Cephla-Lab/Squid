import control.microscope
import tests.control.test_stubs as ts
from control.acquisition_yaml_loader import parse_acquisition_yaml


def test_select_wells_save_emits_wells_and_pattern(tmp_path):
    scope = control.microscope.Microscope.build_from_global_config(True)
    try:
        mpc = ts.get_test_multi_point_controller(scope)
        mpc.set_base_path(str(tmp_path))
        mpc.start_new_experiment("save_v2")
        # The wellplate widget sets the mode via set_xy_mode() before run_acquisition;
        # do the same here so _save_acquisition_yaml sees params.xy_mode == "Select Wells".
        mpc.set_xy_mode("Select Wells")
        mpc.set_scan_size(0.5)
        mpc.set_overlap_percent(10)
        sc = mpc.scanCoordinates
        sc.clear_regions()
        sc.add_region(well_id="A1", center_x=14.3, center_y=11.36, scan_size_mm=0.5, overlap_percent=10, shape="Square")
        channel = scope.live_controller.get_channels(scope.objective_store.current_objective)[0].name
        mpc.set_selected_configurations([channel])
        mpc.run_acquisition()
        # find the saved acquisition.yaml under the experiment dir
        saved = list((tmp_path).rglob("acquisition.yaml"))
        assert saved, "acquisition.yaml not written"
        data = parse_acquisition_yaml(str(saved[0]))
        assert data.wells == "A1"
        assert data.fov_pattern is not None and data.fov_pattern["type"] == "coverage"
        # Coverage pattern keys mirror the add_region / set_scan_size / set_overlap values above.
        assert data.fov_pattern["shape"] == "Square"
        assert data.fov_pattern["overlap_percent"] == 10.0
        assert data.fov_pattern["scan_size_mm"] == 0.5
        assert data.wellplate_regions is None
    finally:
        scope.close()


def test_non_select_wells_save_emits_legacy_regions(tmp_path):
    scope = control.microscope.Microscope.build_from_global_config(True)
    try:
        mpc = ts.get_test_multi_point_controller(scope)
        mpc.set_base_path(str(tmp_path))
        mpc.start_new_experiment("save_v2_legacy")
        # Do NOT call set_xy_mode("Select Wells"): a fresh test-stub controller keeps the
        # default xy_mode == "Current Position", which drives the legacy regions writer branch.
        assert mpc.xy_mode == "Current Position"
        mpc.set_scan_size(0.5)
        mpc.set_overlap_percent(10)
        sc = mpc.scanCoordinates
        sc.clear_regions()
        sc.add_region(well_id="A1", center_x=14.3, center_y=11.36, scan_size_mm=0.5, overlap_percent=10, shape="Square")
        channel = scope.live_controller.get_channels(scope.objective_store.current_objective)[0].name
        mpc.set_selected_configurations([channel])
        mpc.run_acquisition()
        # find the saved acquisition.yaml under the experiment dir
        saved = list((tmp_path).rglob("acquisition.yaml"))
        assert saved, "acquisition.yaml not written"
        data = parse_acquisition_yaml(str(saved[0]))
        # Legacy coordinate-based form: explicit regions with center_mm, no wells-by-name.
        assert isinstance(data.wellplate_regions, list) and data.wellplate_regions
        first = data.wellplate_regions[0]
        assert "name" in first
        assert "center_mm" in first
        assert data.wells is None
    finally:
        scope.close()
