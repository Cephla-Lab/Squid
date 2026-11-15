"""The control server's set_exposure (with a channel) and set_illumination_intensity change the value of the
channel, as the boxes of the live controls do.

Before, the two commands changed an object that was built for that one call. The next command that loaded the
channel (set_channel, the other of the two commands, an acquisition) got the stored value back. With the spinning
disk in, the stored value is the confocal one, so the image never showed the new value.
"""

import threading

import pytest

import control._def
from control.core.config import ConfigRepository
from control.core.multi_point_controller import MultiPointController
from control.core.multi_point_utils import MultiPointControllerFunctions
from control.microscope_control_server import MicroscopeControlServer
import tests.control.test_stubs as ts
from tests.control.spinning_disk_test_utils import build_simulated_microscope

CHANNEL = "Fluorescence 405 nm Ex"  # the channel of the illumination file that the test utils write
GENERAL = (20.0, 20.0)  # (exposure in ms, intensity in %) of general.yaml: the start for every objective
WIDEFIELD = (25.0, 30.0)  # of the file of the objective
CONFOCAL = (50.0, 60.0)  # of the confocal_override in that file

BOTH_MODES = pytest.mark.parametrize("confocal", [False, True], ids=["widefield", "confocal"])


def _write_profile(base_path, objective=None) -> None:
    """The profile "default": general.yaml, and a file for `objective` (with a confocal override) when it is given."""
    configs = base_path / "user_profiles" / "default" / "channel_configs"
    configs.mkdir(parents=True)
    (base_path / "user_profiles" / "default" / "laser_af_configs").mkdir()
    (configs / "general.yaml").write_text(
        "version: 1.0\n"
        "channels:\n"
        f'  - name: "{CHANNEL}"\n'
        '    display_color: "#FFFFFF"\n'
        "    camera_settings:\n"
        f"      exposure_time_ms: {GENERAL[0]}\n"
        "      gain_mode: 0.0\n"
        "    illumination_settings:\n"
        f'      illumination_channel: "{CHANNEL}"\n'
        f"      intensity: {GENERAL[1]}\n"
        "    z_offset_um: 0.0\n"
    )
    if objective is None:
        return
    (configs / f"{objective}.yaml").write_text(
        "version: 1.0\n"
        "channels:\n"
        f'  - name: "{CHANNEL}"\n'
        '    display_color: "#FFFFFF"\n'
        "    camera_settings:\n"
        f"      exposure_time_ms: {WIDEFIELD[0]}\n"
        "      gain_mode: 0.0\n"
        "    illumination_settings:\n"
        f"      intensity: {WIDEFIELD[1]}\n"
        "    z_offset_um: 0.0\n"
        "    confocal_override:\n"
        "      camera_settings:\n"
        f"        exposure_time_ms: {CONFOCAL[0]}\n"
        "        gain_mode: 0.0\n"
        "      illumination_settings:\n"
        f"        intensity: {CONFOCAL[1]}\n"
    )


@pytest.fixture
def scope(tmp_path):
    microscope = build_simulated_microscope(tmp_path)
    yield microscope
    microscope.close()


@pytest.fixture
def objective(scope) -> str:
    return scope.objective_store.current_objective


@pytest.fixture
def server(scope, objective, tmp_path):
    """The control server of a microscope whose objective has its own channel file."""
    _write_profile(tmp_path, objective)
    scope.config_repo.set_profile("default")
    return MicroscopeControlServer(microscope=scope)


def _loaded(scope) -> tuple:
    """(exposure in ms, intensity in %) of the channel, as the next command that loads it gets them."""
    channel = scope.live_controller.get_channel_by_name(scope.objective_store.current_objective, CHANNEL)
    return channel.exposure_time, channel.illumination_intensity


def _applied(scope) -> tuple:
    """(exposure of the camera in ms, intensity of the channel in use in %)."""
    return scope.camera.get_exposure_time(), scope.live_controller.currentConfiguration.illumination_intensity


@BOTH_MODES
def test_an_exposure_set_for_a_channel_is_still_there_when_the_channel_is_selected_again(scope, server, confocal):
    scope.live_controller.toggle_confocal_widefield(confocal)

    server._cmd_set_exposure(exposure_ms=77.0, channel=CHANNEL)
    assert scope.camera.get_exposure_time() == 77.0  # at once

    server._cmd_set_channel(channel_name=CHANNEL)
    assert scope.camera.get_exposure_time() == 77.0


@BOTH_MODES
def test_an_intensity_set_for_a_channel_is_still_there_when_the_channel_is_selected_again(scope, server, confocal):
    scope.live_controller.toggle_confocal_widefield(confocal)

    server._cmd_set_illumination_intensity(channel=CHANNEL, intensity=11.0)
    assert scope.live_controller.currentConfiguration.illumination_intensity == 11.0  # at once

    server._cmd_set_channel(channel_name=CHANNEL)
    assert scope.live_controller.currentConfiguration.illumination_intensity == 11.0


@BOTH_MODES
def test_the_intensity_command_does_not_undo_the_exposure_command(scope, server, confocal):
    scope.live_controller.toggle_confocal_widefield(confocal)

    server._cmd_set_exposure(exposure_ms=77.0, channel=CHANNEL)
    server._cmd_set_illumination_intensity(channel=CHANNEL, intensity=11.0)

    assert _applied(scope) == (77.0, 11.0)
    assert _loaded(scope) == (77.0, 11.0)


def test_values_set_with_the_disk_in_are_the_confocal_values_and_the_widefield_values_stay(scope, server):
    scope.live_controller.toggle_confocal_widefield(True)

    server._cmd_set_exposure(exposure_ms=77.0, channel=CHANNEL)
    server._cmd_set_illumination_intensity(channel=CHANNEL, intensity=11.0)

    assert _loaded(scope) == (77.0, 11.0)
    scope.live_controller.toggle_confocal_widefield(False)
    assert _loaded(scope) == WIDEFIELD


def test_values_set_in_widefield_leave_the_confocal_values(scope, server):
    server._cmd_set_exposure(exposure_ms=77.0, channel=CHANNEL)
    server._cmd_set_illumination_intensity(channel=CHANNEL, intensity=11.0)

    assert _loaded(scope) == (77.0, 11.0)
    scope.live_controller.toggle_confocal_widefield(True)
    assert _loaded(scope) == CONFOCAL


@BOTH_MODES
def test_the_values_are_saved_in_the_profile_as_when_a_person_changes_them_in_the_live_controls(
    scope, server, objective, tmp_path, confocal
):
    scope.live_controller.toggle_confocal_widefield(confocal)

    server._cmd_set_exposure(exposure_ms=77.0, channel=CHANNEL)
    server._cmd_set_illumination_intensity(channel=CHANNEL, intensity=11.0)

    read_again = ConfigRepository(base_path=tmp_path)  # as the next start of Squid reads the files
    read_again.set_profile("default")
    (channel,) = read_again.get_merged_channels(objective, confocal_mode=confocal)
    assert (channel.exposure_time, channel.illumination_intensity) == (77.0, 11.0)
    (other_mode,) = read_again.get_merged_channels(objective, confocal_mode=not confocal)
    assert (other_mode.exposure_time, other_mode.illumination_intensity) == (WIDEFIELD if confocal else CONFOCAL)


def test_an_objective_with_no_channel_file_gets_its_own_values_and_the_general_values_stay(scope, objective, tmp_path):
    """general.yaml is the start for every objective. A value that is set for the objective in use must not change
    it: that would change the value of the other objectives too."""
    _write_profile(tmp_path)
    scope.config_repo.set_profile("default")
    server = MicroscopeControlServer(microscope=scope)

    server._cmd_set_exposure(exposure_ms=77.0, channel=CHANNEL)
    server._cmd_set_illumination_intensity(channel=CHANNEL, intensity=11.0)
    server._cmd_set_channel(channel_name=CHANNEL)

    assert _applied(scope) == (77.0, 11.0)
    general = scope.config_repo.get_general_config().get_channel_by_name(CHANNEL)
    assert (general.exposure_time, general.illumination_intensity) == GENERAL


def test_a_channel_name_that_does_not_exist_is_refused_with_the_names_that_do(scope, server):
    with pytest.raises(ValueError, match="Available:.*Fluorescence 405 nm Ex"):
        server._cmd_set_exposure(exposure_ms=77.0, channel="Fluorescence 999 nm Ex")
    with pytest.raises(ValueError, match="Available:.*Fluorescence 405 nm Ex"):
        server._cmd_set_illumination_intensity(channel="Fluorescence 999 nm Ex", intensity=11.0)

    assert _loaded(scope) == WIDEFIELD


@BOTH_MODES
@pytest.mark.parametrize(
    "command, params",
    [("set_exposure", {"exposure_ms": 0.0}), ("set_illumination_intensity", {"intensity": 150.0})],
    ids=["exposure 0", "intensity 150"],
)
def test_a_value_that_the_channel_file_cannot_hold_is_refused_and_nothing_is_saved(
    scope, server, objective, tmp_path, confocal, command, params
):
    """The file loader refuses such a value and then drops the whole file of the objective at the next start."""
    scope.live_controller.toggle_confocal_widefield(confocal)
    channel_file = tmp_path / "user_profiles" / "default" / "channel_configs" / f"{objective}.yaml"
    before = channel_file.read_text()

    response = server._execute(command, {"channel": CHANNEL, **params})

    assert response["success"] is False
    assert channel_file.read_text() == before
    assert _loaded(scope) == (CONFOCAL if confocal else WIDEFIELD)


def test_two_callers_that_set_values_at_the_same_time_keep_both_values(scope, server, objective, monkeypatch):
    """Each control server client runs on its own thread, and the live controls on the GUI thread. A caller that
    saves its copy of the channel file must not undo a value that another caller saved meanwhile."""
    repo = scope.config_repo
    real_save = repo._save_yaml
    first_save_started, let_first_save_finish = threading.Event(), threading.Event()
    saves = []

    def save_yaml(path, model):
        saves.append(path)
        if len(saves) == 1:  # holds the first caller in the middle of its save
            first_save_started.set()
            let_first_save_finish.wait(timeout=10)
        real_save(path, model)

    monkeypatch.setattr(repo, "_save_yaml", save_yaml)

    def set_value(setting, value):
        repo.update_channel_setting(objective, CHANNEL, setting, value)

    exposure = threading.Thread(target=set_value, args=("ExposureTime", 77.0))
    exposure.start()
    assert first_save_started.wait(timeout=10)
    intensity = threading.Thread(target=set_value, args=("IlluminationIntensity", 11.0))
    intensity.start()
    intensity.join(timeout=2)  # with no serialization, the second caller saves now, while the first one is held
    let_first_save_finish.set()
    exposure.join(timeout=10)
    intensity.join(timeout=10)

    assert _loaded(scope) == (77.0, 11.0)
    read_again = ConfigRepository(base_path=repo.base_path)  # as the next start of Squid reads the files
    read_again.set_profile("default")
    (channel,) = read_again.get_merged_channels(objective)
    assert (channel.exposure_time, channel.illumination_intensity) == (77.0, 11.0)


def _multi_point_controller(scope, callbacks) -> MultiPointController:
    """A MultiPointController on the live controller of the microscope, as the control server uses it."""
    controller = MultiPointController(
        microscope=scope,
        live_controller=scope.live_controller,
        autofocus_controller=ts.get_test_autofocus_controller(
            scope.camera, scope.stage, scope.live_controller, scope.low_level_drivers.microcontroller
        ),
        scan_coordinates=ts.get_test_scan_coordinates(scope.objective_store, scope.stage, scope.camera),
        callbacks=callbacks,
        objective_store=scope.objective_store,
        laser_autofocus_controller=ts.get_test_laser_autofocus_controller(scope),
    )
    controller.set_base_path(ts.new_acquisition_base_path())
    controller.start_new_experiment("channel settings")
    return controller


@BOTH_MODES
def test_an_acquisition_takes_its_images_with_the_values_that_a_client_set(scope, server, monkeypatch, confocal):
    """Before, an acquisition loaded the stored values of the channel (the confocal ones with the spinning disk in),
    so a value that a client set had no effect on the images."""
    monkeypatch.setattr(control._def, "MERGE_CHANNELS", False)
    scope.live_controller.toggle_confocal_widefield(confocal)
    server._cmd_set_exposure(exposure_ms=77.0, channel=CHANNEL)
    server._cmd_set_illumination_intensity(channel=CHANNEL, intensity=11.0)

    finished = threading.Event()
    applied = []  # (channel, intensity) that the acquisition applies for each image
    exposures = []  # exposure of the camera at each image

    callbacks = MultiPointControllerFunctions(
        signal_acquisition_start=lambda params: None,
        signal_acquisition_finished=finished.set,
        signal_new_image=lambda frame, info: exposures.append(scope.camera.get_exposure_time()),
        signal_current_configuration=lambda config: applied.append((config.name, config.illumination_intensity)),
        signal_current_fov=lambda x_mm, y_mm: None,
        signal_overall_progress=lambda progress: None,
        signal_region_progress=lambda progress: None,
    )
    controller = _multi_point_controller(scope, callbacks)
    stage_config = scope.stage.get_config()
    controller.scanCoordinates.add_single_fov_region(
        "A1",
        center_x=stage_config.X_AXIS.MIN_POSITION + 1.0,
        center_y=stage_config.Y_AXIS.MIN_POSITION + 1.0,
        center_z=stage_config.Z_AXIS.MIN_POSITION + 0.1,
    )
    controller.set_selected_configurations([CHANNEL])  # as the run_acquisition command does
    controller.run_acquisition()
    assert finished.wait(timeout=30)

    assert applied and all(entry == (CHANNEL, 11.0) for entry in applied)
    assert exposures and all(exposure == 77.0 for exposure in exposures)
