import pytest

import control._def
import control.microscope
import tests.control.test_stubs as ts
from control.acquisition_yaml_loader import AcquisitionYAMLData
from control.core.acquisition_settings import (
    acquisition_data_from_blocks,
    apply_acquisition_settings,
    export_acquisition_settings,
    parse_wells,
)


def _controller():
    control._def.MERGE_CHANNELS = False
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)
    return scope, mpc


def _channel_names(scope, mpc, n=2):
    return [m.name for m in mpc.liveController.get_channels(scope.objective_store.current_objective)][:n]


def _some_fovs(mpc):
    limits = mpc.stage.get_config()
    x0 = limits.X_AXIS.MIN_POSITION + 1.0
    y0 = limits.Y_AXIS.MIN_POSITION + 1.0
    z0 = limits.Z_AXIS.MIN_POSITION + 0.5
    return [[x0, y0, z0], [x0 + 0.5, y0, z0 + 0.01]]


def test_apply_rebuilds_regions_from_fovs_and_sets_every_sticky_field():
    scope, mpc = _controller()
    channels = _channel_names(scope, mpc)
    mpc.set_z_range(5.0, 6.0)  # stale value that must be replaced
    data = AcquisitionYAMLData(
        widget_type="wellplate",
        xy_mode="Load Coordinates",
        nz=3,
        delta_z_um=2.0,
        nt=4,
        delta_t_s=1.0,
        channel_names=channels,
        contrast_af=True,
        laser_af=False,
        use_piezo=False,
        scan_size_mm=1.0,
        overlap_percent=5.0,
        z_range_mm=(1.0, 1.1),
        skip_saving=True,
        wellplate_regions=[{"name": "A1", "fovs": _some_fovs(mpc)}],
    )

    applied = apply_acquisition_settings(mpc, mpc.scanCoordinates, scope, data)

    assert applied.regions == 1 and applied.fovs == 2 and applied.channels == channels and applied.nz == 3
    assert len(mpc.scanCoordinates.region_fov_coordinates["A1"]) == 2
    assert mpc.NZ == 3 and mpc.deltaZ == pytest.approx(0.002) and mpc.Nt == 4 and mpc.deltat == 1.0
    assert mpc.do_autofocus is True and mpc.do_reflection_af is False and mpc.use_piezo is False
    assert mpc.z_range == [1.0, 1.1]
    assert mpc.focus_map is None and mpc.region_laser_af_offsets == {}
    assert mpc.skip_saving is True and mpc.widget_type == "wellplate" and mpc.xy_mode == "Load Coordinates"
    assert mpc.scan_size_mm == 1.0 and mpc.overlap_percent == 5.0
    assert [c.name for c in mpc.selected_configurations] == channels


def test_apply_without_z_range_resets_the_controllers_z_range():
    scope, mpc = _controller()
    mpc.set_z_range(5.0, 6.0)
    data = AcquisitionYAMLData(
        widget_type="wellplate",
        channel_names=_channel_names(scope, mpc, 1),
        wellplate_regions=[{"name": "A1", "fovs": _some_fovs(mpc)}],
    )
    apply_acquisition_settings(mpc, mpc.scanCoordinates, scope, data)
    assert mpc.z_range is None


def test_apply_rejects_unknown_channels_before_touching_regions():
    scope, mpc = _controller()
    mpc.scanCoordinates.add_region_from_fovs("keep", _some_fovs(mpc))
    data = AcquisitionYAMLData(
        widget_type="wellplate",
        channel_names=["No Such Channel"],
        wellplate_regions=[{"name": "A1", "fovs": _some_fovs(mpc)}],
    )
    with pytest.raises(ValueError, match="Invalid channels"):
        apply_acquisition_settings(mpc, mpc.scanCoordinates, scope, data)
    assert "keep" in mpc.scanCoordinates.region_fov_coordinates


def test_apply_without_regions_raises():
    scope, mpc = _controller()
    data = AcquisitionYAMLData(widget_type="wellplate", channel_names=_channel_names(scope, mpc, 1))
    with pytest.raises(ValueError, match="No wells or regions"):
        apply_acquisition_settings(mpc, mpc.scanCoordinates, scope, data)


def test_export_then_apply_round_trips():
    scope, mpc = _controller()
    channels = _channel_names(scope, mpc)
    mpc.set_selected_configurations(channels)
    mpc.set_NZ(4)
    mpc.set_deltaZ(1.5)
    mpc.set_af_flag(True)
    mpc.set_z_range(1.0, 1.0045)
    mpc.set_widget_type("wellplate")
    mpc.set_xy_mode("Load Coordinates")
    mpc.set_scan_size(0.0)
    mpc.set_overlap_percent(10.0)
    mpc.scanCoordinates.add_region_from_fovs("A1", _some_fovs(mpc))

    settings, coordinates = export_acquisition_settings(mpc, mpc.scanCoordinates, scope.objective_store, scope.camera)

    assert settings["channels"] == channels and settings["z_stack"]["nz"] == 4
    assert settings["z_stack"]["delta_z_um"] == pytest.approx(1.5) and settings["autofocus"]["contrast_af"] is True
    assert settings["objective"]["name"] == scope.objective_store.current_objective
    assert coordinates["regions"][0]["name"] == "A1" and len(coordinates["regions"][0]["fovs"]) == 2

    scope2, mpc2 = _controller()
    applied = apply_acquisition_settings(
        mpc2, mpc2.scanCoordinates, scope2, acquisition_data_from_blocks(settings, coordinates)
    )
    assert applied.fovs == 2 and mpc2.NZ == 4 and mpc2.Nt == 1 and mpc2.z_range == [1.0, 1.0045]
    assert mpc2.scanCoordinates.region_fov_coordinates["A1"] == mpc.scanCoordinates.region_fov_coordinates["A1"]


def test_parse_wells_range_and_list():
    settings = {"a1_x_mm": 10.0, "a1_y_mm": 20.0, "well_spacing_mm": 9.0}
    assert parse_wells("A1:B2", settings) == {
        "A1": (10.0, 20.0),
        "A2": (19.0, 20.0),
        "B1": (10.0, 29.0),
        "B2": (19.0, 29.0),
    }
    assert parse_wells("A1,C3", settings) == {"A1": (10.0, 20.0), "C3": (28.0, 38.0)}


def test_apply_rejects_an_unknown_z_stacking_config_before_touching_regions():
    scope, mpc = _controller()
    mpc.scanCoordinates.add_region_from_fovs("keep", _some_fovs(mpc))
    data = AcquisitionYAMLData(
        widget_type="wellplate",
        channel_names=_channel_names(scope, mpc, 1),
        z_stacking_config="FROM NOWHERE",
        wellplate_regions=[{"name": "A1", "fovs": _some_fovs(mpc)}],
    )
    with pytest.raises(ValueError, match="z_stacking_config"):
        apply_acquisition_settings(mpc, mpc.scanCoordinates, scope, data)
    assert "keep" in mpc.scanCoordinates.region_fov_coordinates
