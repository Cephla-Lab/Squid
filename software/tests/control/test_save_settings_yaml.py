"""Save Settings on the Wellplate and Flexible tabs: an acquisition.yaml written without acquiring,
which loads back through the same parser a real run's file does."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest
import yaml

import control.microscope
import control.widgets
import squid.camera.utils
import tests.control.test_stubs as ts
from control.acquisition_yaml_loader import parse_acquisition_yaml
from control.core.multi_point_utils import ScanPositionInformation


@pytest.fixture
def mpc():
    scope = control.microscope.Microscope.build_from_global_config(True)
    controller = ts.get_test_multi_point_controller(microscope=scope)
    names = [c.name for c in controller.liveController.get_channels(controller.objectiveStore.current_objective)]
    controller.set_selected_configurations(names[:2])
    controller.set_NZ(5)
    controller.set_deltaZ(2.0)
    controller.set_Nt(3)
    controller.set_deltat(60)
    controller.set_z_stacking_config(1)  # FROM CENTER, what the Relative z mode pushes
    controller.scanCoordinates.clear_regions()
    yield controller
    controller.close()
    scope.close()


def _two_wells(controller):
    x = controller.stage.get_config().X_AXIS.MIN_POSITION + 20
    y = controller.stage.get_config().Y_AXIS.MIN_POSITION + 20
    controller.scanCoordinates.add_region("C5", x, y, 1.0, 10, "Square")
    controller.scanCoordinates.add_region("D5", x + 9, y, 1.0, 10, "Square")
    return [(x, y), (x + 9, y)]


def test_wellplate_settings_file_loads_back_with_its_wells(mpc, tmp_path):
    mpc.set_widget_type("wellplate")
    mpc.set_xy_mode("Select Wells")
    mpc.set_scan_size(1.0)
    mpc.set_overlap_percent(10)
    centers = _two_wells(mpc)
    path = tmp_path / "survey.yaml"

    mpc.save_settings_yaml(str(path))

    data = parse_acquisition_yaml(str(path))
    assert data.widget_type == "wellplate"
    assert data.xy_mode == "Select Wells"
    assert [r["name"] for r in data.wellplate_regions] == ["C5", "D5"]
    for region, (x, y) in zip(data.wellplate_regions, centers):
        assert region["center_mm"][:2] == pytest.approx([x, y])
        assert region["fovs"], "the FOVs of each well are written too"
    assert (data.nz, data.nt) == (5, 3)
    assert data.delta_z_um == pytest.approx(2.0)
    assert data.delta_t_s == 60
    assert data.z_stacking_config == "FROM CENTER"
    assert data.channel_names == [c.name for c in mpc.selected_configurations]
    assert data.objective_name == mpc.objectiveStore.current_objective


def test_settings_file_is_not_stamped_as_a_run_and_records_the_frame_size(mpc, tmp_path):
    mpc.set_widget_type("wellplate")
    _two_wells(mpc)
    path = tmp_path / "settings.yaml"

    mpc.save_settings_yaml(str(path))

    raw = yaml.safe_load(path.read_text())
    # The fixture's start_new_experiment() gave the controller an experiment id; a settings file
    # is not that run.
    assert mpc.experiment_ID is not None
    assert raw["acquisition"]["experiment_id"] is None
    assert raw["acquisition"]["start_time"] is None
    assert raw["objective"]["image_size_px"] == list(squid.camera.utils.image_size_px(mpc.camera))
    # Nothing but the file is written: no experiment folder, no coordinates.csv.
    assert [p.name for p in tmp_path.iterdir()] == ["settings.yaml"]


def test_wellplate_settings_without_regions(mpc, tmp_path):
    mpc.set_widget_type("wellplate")
    _two_wells(mpc)
    path = tmp_path / "settings.yaml"

    mpc.save_settings_yaml(str(path), include_regions=False)

    assert not parse_acquisition_yaml(str(path)).wellplate_regions
    assert len(mpc.scanCoordinates.region_centers) == 2  # the shared scan coordinates are untouched


def test_flexible_settings_file_loads_back_with_its_positions(mpc, tmp_path):
    mpc.set_widget_type("flexible")
    mpc.set_NX(2)
    mpc.set_NY(3)
    positions = ScanPositionInformation(
        scan_region_coords_mm=[[10.0, 11.0, 1.5], [20.0, 21.0, 1.6]],
        scan_region_names=["R0", "R1"],
        scan_region_fov_coords_mm={},
    )
    path = tmp_path / "flexible.yaml"

    mpc.save_settings_yaml(str(path), scan_position_information=positions)

    data = parse_acquisition_yaml(str(path))
    assert data.widget_type == "flexible"
    assert (data.nx, data.ny) == (2, 3)
    assert [p["name"] for p in data.flexible_positions] == ["R0", "R1"]
    assert [p["center_mm"] for p in data.flexible_positions] == [[10.0, 11.0, 1.5], [20.0, 21.0, 1.6]]


def test_a_failed_write_is_raised_to_the_caller(mpc, tmp_path):
    mpc.set_widget_type("wellplate")
    with pytest.raises(OSError):
        mpc.save_settings_yaml(str(tmp_path / "no_such_folder" / "settings.yaml"))


# ---- the two tabs' save_settings(), on stand-in widgets around a real controller ----


def _flexible_fake(controller):
    return SimpleNamespace(
        multipointController=controller,
        _push_settings_to_controller=lambda: controller.set_widget_type("flexible"),
        location_list=np.array([[10.0, 11.0, 1.5], [20.0, 21.0, 1.6]]),
        location_ids=np.array(["R0", "R1"]),
        _log=MagicMock(),
    )


def _wellplate_fake(controller, xy_checked=True):
    return SimpleNamespace(
        multipointController=controller,
        _push_settings_to_controller=lambda: controller.set_widget_type("wellplate"),
        checkbox_xy=SimpleNamespace(isChecked=lambda: xy_checked),
        scanCoordinates=controller.scanCoordinates,
        _log=MagicMock(),
    )


def test_flexible_tab_saves_its_location_list(mpc, tmp_path):
    # The shared scan coordinates hold another tab's regions; the Flexible file must not carry them.
    _two_wells(mpc)
    path = tmp_path / "flexible.yaml"

    assert control.widgets.FlexibleMultiPointWidget.save_settings(_flexible_fake(mpc), str(path)) is True

    data = parse_acquisition_yaml(str(path))
    assert data.widget_type == "flexible"
    assert [p["name"] for p in data.flexible_positions] == ["R0", "R1"]
    assert data.flexible_positions[1]["center_mm"] == pytest.approx([20.0, 21.0, 1.6])


@pytest.mark.parametrize("xy_checked, expected_wells", [(True, ["C5", "D5"]), (False, [])])
def test_wellplate_tab_saves_the_selected_wells_only_with_xy_checked(mpc, tmp_path, xy_checked, expected_wells):
    _two_wells(mpc)
    path = tmp_path / "wellplate.yaml"

    assert control.widgets.WellplateMultiPointWidget.save_settings(_wellplate_fake(mpc, xy_checked), str(path))

    data = parse_acquisition_yaml(str(path))
    assert data.widget_type == "wellplate"
    assert [r["name"] for r in (data.wellplate_regions or [])] == expected_wells


def test_tab_reports_a_failed_save(mpc, tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(control.widgets.QMessageBox, "warning", lambda *args: warnings.append(args[1:]))
    fake = _wellplate_fake(mpc)

    saved = control.widgets.WellplateMultiPointWidget.save_settings(fake, str(tmp_path / "missing" / "s.yaml"))

    assert saved is False
    assert warnings and warnings[0][0] == "Save Error"
    fake._log.error.assert_called_once()


def test_tab_refuses_to_save_during_an_acquisition(tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(control.widgets.QMessageBox, "warning", lambda *args: warnings.append(args[1:]))
    controller = MagicMock()
    controller.acquisition_in_progress.return_value = True
    path = tmp_path / "s.yaml"

    for widget_class, fake in (
        (control.widgets.FlexibleMultiPointWidget, _flexible_fake(controller)),
        (control.widgets.WellplateMultiPointWidget, _wellplate_fake(controller)),
    ):
        assert widget_class.save_settings(fake, str(path)) is False
    assert len(warnings) == 2
    assert not path.exists()
    controller.save_settings_yaml.assert_not_called()


def test_image_size_px_applies_the_software_crop_to_the_roi():
    camera = MagicMock()
    camera.get_region_of_interest.return_value = (8, 16, 2000, 1500)
    camera.get_crop_size.return_value = (1800, 1800)
    assert squid.camera.utils.image_size_px(camera) == (1800, 1500)

    camera.get_crop_size.side_effect = RuntimeError("no crop")
    assert squid.camera.utils.image_size_px(camera) == (2000, 1500)

    camera.get_region_of_interest.side_effect = RuntimeError("no ROI")
    assert squid.camera.utils.image_size_px(camera) is None
