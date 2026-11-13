"""channel_overrides: exposure and intensity for one acquisition, with no change to the stored profile."""

import dataclasses
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import control._def
import control.acquisition_yaml_loader
import control.microscope
import control.widgets
import tests.control.test_MultiPointController as mpc_tests
import tests.control.test_stubs as ts
from control.acquisition_yaml_loader import CHANNEL_OVERRIDES_ONLY_SERVER, AcquisitionYAMLData, parse_acquisition_yaml
from control.core.fluidics_protocol import resolve
from tests.control.test_microscope_control_server import SAMPLE_WELLPLATE_YAML, create_mock_server

CHANNEL = "Fluorescence 488 nm Ex"
OVERRIDES = f'\nchannel_overrides:\n  "{CHANNEL}": {{exposure_time_ms: 20.0, illumination_intensity: 5.0}}\n'
EXPECTED = {CHANNEL: {"exposure_time_ms": 20.0, "illumination_intensity": 5.0}}


def write(tmp_path, extra=""):
    path = tmp_path / "acquisition.yaml"
    path.write_text(SAMPLE_WELLPLATE_YAML + extra)
    return str(path)


def test_channel_overrides_are_parsed(tmp_path):
    assert parse_acquisition_yaml(write(tmp_path, OVERRIDES)).channel_overrides == EXPECTED


def test_one_value_is_enough_and_whole_numbers_become_floats(tmp_path):
    data = parse_acquisition_yaml(write(tmp_path, f'\nchannel_overrides:\n  "{CHANNEL}": {{exposure_time_ms: 20}}\n'))
    assert data.channel_overrides == {CHANNEL: {"exposure_time_ms": 20.0}}
    assert isinstance(data.channel_overrides[CHANNEL]["exposure_time_ms"], float)


def test_no_key_gives_no_overrides(tmp_path):
    assert parse_acquisition_yaml(write(tmp_path)).channel_overrides == {}


@pytest.mark.parametrize(
    "block, message",
    [
        ('"Not In File": {exposure_time_ms: 20.0}', "names a channel that the acquisition does not use: 'Not In File'"),
        (f'"{CHANNEL}": {{gain: 2}}', "unknown key 'gain'"),
        (f'"{CHANNEL}": {{exposure_time_ms: 0}}', "exposure_time_ms"),
        (f'"{CHANNEL}": {{exposure_time_ms: true}}', "exposure_time_ms"),
        (f'"{CHANNEL}": {{illumination_intensity: 101}}', "illumination_intensity"),
        (f'"{CHANNEL}": 20', "exposure_time_ms, illumination_intensity or both"),
    ],
)
def test_bad_overrides_are_refused(tmp_path, block, message):
    with pytest.raises(ValueError, match=message):
        parse_acquisition_yaml(write(tmp_path, "\nchannel_overrides:\n  " + block + "\n"))


@pytest.fixture
def run(tmp_path):
    control._def.MERGE_CHANNELS = False
    scope = control.microscope.Microscope.build_from_global_config(True)
    tracker = mpc_tests.TestAcquisitionTracker()
    seen = []
    callbacks = dataclasses.replace(
        tracker.get_callbacks(),
        signal_current_configuration=lambda config: seen.append((config.name, config.exposure_time)),
    )
    mpc = ts.get_test_multi_point_controller(microscope=scope, callbacks=callbacks)
    mpc_tests.add_some_coordinates(mpc)
    mpc_tests.select_some_configs(mpc, scope.objective_store.current_objective)
    mpc.set_base_path(str(tmp_path))
    first = mpc.selected_configurations[0]
    return SimpleNamespace(mpc=mpc, tracker=tracker, seen=seen, name=first.name, stored_exposure=first.exposure_time)


def test_no_overrides_gives_the_selected_channels_themselves(run):
    assert run.mpc.effective_configurations(None) is run.mpc.selected_configurations
    assert run.mpc.effective_configurations({}) is run.mpc.selected_configurations


def test_overrides_go_on_copies(run):
    stored = list(run.mpc.selected_configurations)
    effective = run.mpc.effective_configurations({run.name: {"exposure_time_ms": 21.5}})
    assert effective[0].name == run.name and effective[0].exposure_time == 21.5 and effective[0] is not stored[0]
    assert effective[0].illumination_intensity == stored[0].illumination_intensity  # not named, not changed
    assert effective[1] is stored[1]
    assert run.mpc.selected_configurations == stored and stored[0].exposure_time == run.stored_exposure != 21.5


def test_an_override_for_a_channel_that_is_not_selected_is_refused(run):
    with pytest.raises(ValueError, match="not selected"):
        run.mpc.effective_configurations({"No Such Channel": {"exposure_time_ms": 21.5}})


def test_the_saved_channel_file_of_the_experiment_shows_the_overrides(run, tmp_path):
    run.mpc.start_new_experiment("exp", add_timestamp=False, channel_overrides={run.name: {"exposure_time_ms": 21.5}})
    assert "21.5" in (tmp_path / "exp" / "acquisition_channels.yaml").read_text()
    assert run.mpc.selected_configurations[0].exposure_time == run.stored_exposure


def test_a_run_uses_the_overrides_and_the_stored_channels_stay_as_they_are(run, tmp_path):
    overrides = {run.name: {"exposure_time_ms": 21.5}}
    run.mpc.start_new_experiment("exp", add_timestamp=False, channel_overrides=overrides)
    assert run.mpc.run_acquisition(channel_overrides=overrides)
    assert run.tracker.started_event.wait(5) and run.tracker.finished_event.wait(10)
    # every image of the run used the override; after the run the controller puts the stored channel back
    assert run.seen.count((run.name, 21.5)) >= 2 and (run.name, run.stored_exposure) not in run.seen[:-1]
    assert "21.5" in (tmp_path / "exp" / "acquisition.yaml").read_text()
    assert run.mpc.selected_configurations[0].exposure_time == run.stored_exposure


def test_a_run_with_no_overrides_uses_the_stored_values(run, tmp_path):
    run.mpc.start_new_experiment("exp", add_timestamp=False)
    assert run.mpc.run_acquisition()
    assert run.tracker.started_event.wait(5) and run.tracker.finished_event.wait(10)
    assert (run.name, run.stored_exposure) in run.seen


def test_the_server_passes_the_overrides_to_the_experiment_and_to_the_run(tmp_path):
    server = create_mock_server()
    server._cmd_run_acquisition_from_yaml(
        yaml_path=write(tmp_path, OVERRIDES), experiment_id="exp1", base_path=str(tmp_path)
    )
    server.multipoint_controller.start_new_experiment.assert_called_with("exp1", channel_overrides=EXPECTED)
    server.multipoint_controller.run_acquisition.assert_called_once_with(channel_overrides=EXPECTED)


def test_the_server_passes_none_for_a_file_with_no_overrides(tmp_path):
    server = create_mock_server()
    server._cmd_run_acquisition_from_yaml(yaml_path=write(tmp_path), experiment_id="exp1", base_path=str(tmp_path))
    server.multipoint_controller.start_new_experiment.assert_called_with("exp1", channel_overrides=None)
    server.multipoint_controller.run_acquisition.assert_called_once_with(channel_overrides=None)


def test_the_fluidics_loaders_refuse_a_file_with_overrides(tmp_path):
    path = write(tmp_path, OVERRIDES)
    name, folder = os.path.basename(path), os.path.dirname(path)
    with pytest.raises(ValueError, match=CHANNEL_OVERRIDES_ONLY_SERVER):
        resolve._load_settings_source(name, folder)
    with pytest.raises(ValueError, match=CHANNEL_OVERRIDES_ONLY_SERVER):
        resolve._load_coordinates_source(name, folder)


@pytest.mark.parametrize("from_server, loads", [(False, False), (True, True)])
def test_the_window_loads_a_file_with_overrides_only_for_the_server(monkeypatch, from_server, loads):
    yaml_data = AcquisitionYAMLData(widget_type="wellplate", channel_overrides={"A": {"exposure_time_ms": 20.0}})
    monkeypatch.setattr(control.acquisition_yaml_loader, "parse_acquisition_yaml", lambda path: yaml_data)
    warning = MagicMock()
    monkeypatch.setattr(control.widgets.QMessageBox, "warning", warning)
    host = MagicMock()
    host.objectiveStore.current_objective = "20x"
    host._get_expected_widget_type.return_value = "wellplate"
    host.multipointController.piezo = None
    host.multipointController.camera.get_binning.return_value = (1, 1)
    loader = control.widgets.AcquisitionYAMLDropMixin._load_acquisition_yaml
    assert loader(host, "acquisition.yaml", from_server=from_server) is loads
    assert warning.called is (not loads)
    if not loads:
        assert CHANNEL_OVERRIDES_ONLY_SERVER in warning.call_args.args[2]
        host._apply_yaml_settings.assert_not_called()
