"""LaserAutofocusController, driving hardware fakes that record what they are told to do."""

import itertools
import math
import time
import types
from unittest.mock import MagicMock

import numpy as np
import pytest

import control._def
from control._def import SpotDetectionMode
from control.core.config import ConfigRepository
from control.core.laser_auto_focus_controller import LaserAutofocusController
from control.models import LaserAFConfig
from control.widgets import LaserAutofocusSettingWidget

REFERENCE_X_PX = 760.0
PIXEL_TO_UM = 0.2
START_Z_UM = 150.0
BLANK = np.zeros((256, 1536), dtype=np.uint8)


def spot_frame(x_px):
    """A spot on the centre row of an otherwise black frame."""
    y, x = np.ogrid[: BLANK.shape[0], : BLANK.shape[1]]
    spot = 200 * np.exp(-((x - x_px) ** 2 + (y - BLANK.shape[0] / 2) ** 2) / (2 * 5.0**2))
    return np.round(spot).astype(np.uint8)


AT_REFERENCE = spot_frame(REFERENCE_X_PX)


class Hardware:
    """The microcontroller, focus camera and piezo of one controller, and the record of what they did."""

    range_um = 300.0  # of the piezo

    def __init__(self, frames):
        self.events = []
        self.laser_is_on = False
        self.position = START_Z_UM  # of the piezo
        self.move_error = None
        # the last frame repeats
        self._frames = itertools.chain(frames, itertools.repeat(frames[-1]))

    @property
    def moves_um(self):
        return [event[1] for event in self.events if event[0] == "move"]

    def turn_on_AF_laser(self):
        self.laser_is_on = True

    def turn_off_AF_laser(self):
        self.laser_is_on = False

    def wait_till_operation_is_completed(self):
        pass

    def move_relative(self, um):
        if self.move_error is not None:
            raise self.move_error
        self.events.append(("move", round(um, 6)))
        self.position += um

    def enable_callbacks(self, enabled):
        pass

    def next_frame(self):
        self.events.append(("frame",))
        return next(self._frames)


@pytest.fixture
def make_controller(monkeypatch):
    def make(frames, **settings):
        hardware = Hardware(frames)
        monkeypatch.setattr(time, "sleep", lambda seconds: hardware.events.append(("sleep", seconds)))
        no_profile = types.SimpleNamespace(
            microscope=types.SimpleNamespace(config_repo=MagicMock(current_profile=None))
        )
        controller = LaserAutofocusController(
            microcontroller=hardware, camera=hardware, liveController=no_profile, stage=None, piezo=hardware
        )
        controller.is_initialized = True
        controller.get_new_frame = hardware.next_frame
        controller.laser_af_properties = LaserAFConfig(
            **{
                "x_reference": REFERENCE_X_PX,
                "pixel_to_um": PIXEL_TO_UM,
                "has_reference": True,
                "spot_detection_mode": SpotDetectionMode.SINGLE,
                "laser_af_averaging_n": 1,
                "laser_af_range": 30.0,
                **settings,
            }
        )
        return controller, hardware

    return make


def test_reading_the_displacement_leaves_z_alone_when_there_is_no_spot(make_controller):
    controller, hardware = make_controller([BLANK], search_for_spot=True)

    assert math.isnan(controller.measure_displacement())
    assert hardware.moves_um == []


@pytest.mark.parametrize("search_for_spot", [True, False])
def test_move_to_target_searches_for_the_spot_only_when_the_setting_is_on(make_controller, search_for_spot):
    controller, hardware = make_controller([BLANK], search_for_spot=search_for_spot)

    assert not controller.move_to_target(0.0)
    assert bool(hardware.moves_um) == search_for_spot
    assert hardware.position == pytest.approx(START_Z_UM)


def test_move_to_target_reaches_the_target_from_where_the_search_found_the_spot(make_controller):
    found_2_um_from_the_reference = spot_frame(REFERENCE_X_PX + 2 / PIXEL_TO_UM)
    # reference, then nothing where the move starts and at the first search position, the spot at the second
    frames = [AT_REFERENCE, BLANK, BLANK, found_2_um_from_the_reference, AT_REFERENCE]
    controller, hardware = make_controller(frames, search_for_spot=True)
    assert controller.set_reference()

    assert controller.move_to_target(0.0)
    assert hardware.moves_um == [-10.0, -10.0, -2.0]


def test_move_to_target_fails_on_a_spot_that_stays_away_from_the_reference(make_controller):
    """Its crop around the reference is blank, so the correlation with the reference is not a number."""
    debris = spot_frame(REFERENCE_X_PX + 100)
    controller, hardware = make_controller([AT_REFERENCE, debris], search_for_spot=False)
    assert controller.set_reference()

    assert not controller.move_to_target(0.0)
    assert hardware.position == pytest.approx(START_Z_UM)


def test_laser_is_off_after_a_move_fails_during_the_search(make_controller):
    controller, hardware = make_controller([BLANK], search_for_spot=True)
    hardware.move_error = TimeoutError("piezo did not respond")

    with pytest.raises(TimeoutError):
        controller.move_to_target(0.0)

    assert not hardware.laser_is_on


def test_piezo_settles_after_the_search_restores_its_position(make_controller):
    controller, hardware = make_controller([BLANK], search_for_spot=True)

    controller.move_to_target(0.0)

    last_move = max(index for index, event in enumerate(hardware.events) if event[0] == "move")
    assert hardware.events[last_move + 1 :] == [("sleep", control._def.MULTIPOINT_PIEZO_DELAY_MS / 1000)]


@pytest.fixture
def settings_widget(make_controller, qtbot):
    controller, _ = make_controller([BLANK], search_for_spot=True)
    widget = LaserAutofocusSettingWidget(MagicMock(), MagicMock(), controller)
    qtbot.addWidget(widget)
    return widget, controller


def test_search_checkbox_shows_the_saved_setting(settings_widget):
    widget, controller = settings_widget
    assert widget.search_for_spot_checkbox.isChecked()

    controller.laser_af_properties = controller.laser_af_properties.model_copy(update={"search_for_spot": False})
    widget.update_values()

    assert not widget.search_for_spot_checkbox.isChecked()


def test_search_checkbox_is_applied_without_reinitialization(settings_widget):
    widget, controller = settings_widget

    widget.search_for_spot_checkbox.setChecked(False)
    widget.update_threshold_settings()

    assert controller.laser_af_properties.search_for_spot is False
    assert controller.is_initialized


def test_search_checkbox_is_applied_on_initialize(settings_widget, monkeypatch):
    widget, controller = settings_widget
    monkeypatch.setattr(controller, "initialize_auto", lambda: True)

    widget.search_for_spot_checkbox.setChecked(False)
    widget.apply_and_initialize()

    assert controller.laser_af_properties.search_for_spot is False


def test_displacement_comes_from_the_frames_that_were_read_when_the_last_read_fails(make_controller):
    ten_px_from_the_reference = spot_frame(REFERENCE_X_PX + 10)
    frames = [ten_px_from_the_reference, ten_px_from_the_reference, None]
    controller, _ = make_controller(frames, laser_af_averaging_n=3)

    assert controller.measure_displacement() == pytest.approx(10 * PIXEL_TO_UM, abs=0.05)


def test_displacement_is_nan_when_no_frame_can_be_read(make_controller):
    controller, hardware = make_controller([None])

    assert math.isnan(controller.measure_displacement())
    assert not hardware.laser_is_on


def test_settings_panel_offers_every_spot_detection_mode(settings_widget):
    widget, _ = settings_widget
    combo = widget.spot_mode_combo

    assert [combo.itemData(index) for index in range(combo.count())] == list(SpotDetectionMode)
    assert [combo.itemText(index) for index in range(combo.count())] == [
        "single",
        "multi_left",
        "multi_right",
        "multi_second_right",
    ]


def test_laser_af_is_not_initialized_from_a_profile_saved_with_line_profile_detection(tmp_path):
    from tests.control.core.config.test_repository import LASER_AF_PROFILE_SAVED_WITH_LINE_PROFILE_DETECTION

    (tmp_path / "machine_configs").mkdir()
    profile = tmp_path / "user_profiles" / "default"
    (profile / "channel_configs").mkdir(parents=True)
    (profile / "laser_af_configs").mkdir()
    (profile / "laser_af_configs" / "20x.yaml").write_text(LASER_AF_PROFILE_SAVED_WITH_LINE_PROFILE_DETECTION)
    repo = ConfigRepository(base_path=tmp_path)
    repo.set_profile("default")

    controller = LaserAutofocusController(
        microcontroller=MagicMock(),
        camera=MagicMock(),
        liveController=types.SimpleNamespace(microscope=types.SimpleNamespace(config_repo=repo)),
        stage=MagicMock(),
        objectiveStore=types.SimpleNamespace(current_objective="20x"),
    )

    assert not controller.is_initialized
    assert not controller.laser_af_properties.has_reference
