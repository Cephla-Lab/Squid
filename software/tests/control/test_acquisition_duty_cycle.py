"""Per-image overheads of a multipoint acquisition (AI-docs Squid/to-do/2026-10-21-aion-acquisition-duty-cycle.md).

- With a confocal unit triggering the camera (NL5/AION, NL5_USE_DOUT) the worker waits for the frame after
  send_trigger(), not a further full frame time.
- A channel that is unchanged between z levels is not re-applied with set_microscope_mode on every image.
- The disk space estimate does not trigger the camera.
"""

import types

import pytest

import control._def
import control.microscope
from control._def import TriggerMode
from control.core.multi_point_worker import MultiPointWorker
import tests.control.test_stubs as ts
from tests.control.test_MultiPointController import TestAcquisitionTracker, add_some_coordinates

# The AION fixture: a simulated Tucsen whose frames come only from the fake confocal unit's scans.
from tests.control.test_aion_hw_trigger import (  # noqa: F401 (fixtures)
    FakeConfocalUnit,
    _fluorescence_acquisition,
    aion_scope,
    nl5_trigger_timeout_s,
)


@pytest.fixture
def scope():
    scope = control.microscope.Microscope.build_from_global_config(True)
    yield scope
    scope.close()


def _record_sleeps(monkeypatch):
    sleeps = []
    original = MultiPointWorker._sleep

    def recording_sleep(self, sec):
        sleeps.append(sec)
        original(self, sec)

    monkeypatch.setattr(MultiPointWorker, "_sleep", recording_sleep)
    return sleeps


def _record_selected_configs(monkeypatch):
    selected = []
    original = MultiPointWorker._select_config

    def recording_select(self, config):
        selected.append(config.name)
        original(self, config)

    monkeypatch.setattr(MultiPointWorker, "_select_config", recording_select)
    return selected


def _fov_count(mpc):
    return sum(len(coords) for coords in mpc.scanCoordinates.region_fov_coordinates.values())


def _run(mpc, tracker, timeout_s=60):
    mpc.run_acquisition()
    assert tracker.started_event.wait(10)
    assert tracker.finished_event.wait(timeout_s)


def test_confocal_unit_trigger_waits_for_the_frame_not_a_frame_time(aion_scope, monkeypatch):
    """send_trigger() blocks through the scan; a further total-frame-time sleep ended long after the frame came."""
    frame_time_ms = 400.0
    monkeypatch.setattr(aion_scope.camera, "get_total_frame_time", lambda: frame_time_ms)
    sleeps = _record_sleeps(monkeypatch)
    unit = FakeConfocalUnit(aion_scope.camera)  # each scan delivers its frame within a few ms
    mpc, tracker = _fluorescence_acquisition(aion_scope, unit)
    expected = mpc.get_acquisition_image_count()

    _run(mpc, tracker)

    assert tracker.image_count == expected
    assert unit.scans == expected
    assert max(sleeps) < 0.5 * frame_time_ms / 1000  # no frame-time sleep after any trigger


def test_hardware_trigger_without_a_confocal_unit_still_sleeps_a_frame_time(scope, monkeypatch):
    """Cameras triggered by the microcontroller keep the timed wait: send_trigger() returns at once there."""
    assert scope.addons.nl5 is None
    frame_time_ms = 100.0
    monkeypatch.setattr(scope.camera, "get_total_frame_time", lambda: frame_time_ms)
    sleeps = _record_sleeps(monkeypatch)
    tracker = TestAcquisitionTracker()
    mpc = ts.get_test_multi_point_controller(microscope=scope, callbacks=tracker.get_callbacks())
    mpc.liveController.set_trigger_mode(TriggerMode.HARDWARE)
    mpc.scanCoordinates.clear_regions()
    mpc.scanCoordinates.add_single_fov_region(
        "single",
        center_x=mpc.stage.get_config().X_AXIS.MIN_POSITION + 1.0,
        center_y=mpc.stage.get_config().Y_AXIS.MIN_POSITION + 1.0,
        center_z=mpc.stage.get_config().Z_AXIS.MIN_POSITION + 1.0,
    )
    channel = mpc.liveController.get_channels(scope.objective_store.current_objective)[0].name
    mpc.set_selected_configurations([channel])
    mpc.set_NZ(2)

    _run(mpc, tracker)

    assert tracker.image_count == 2
    assert len([s for s in sleeps if s >= 0.5 * frame_time_ms / 1000]) == 2  # one per image


def test_an_unchanged_channel_is_applied_once_per_position(aion_scope, monkeypatch):
    selected = _record_selected_configs(monkeypatch)
    unit = FakeConfocalUnit(aion_scope.camera)
    mpc, tracker = _fluorescence_acquisition(aion_scope, unit, nz=3)
    channel = mpc.selected_configurations[0].name
    mpc.set_selected_configurations([channel])
    expected = mpc.get_acquisition_image_count()

    _run(mpc, tracker)

    assert tracker.image_count == expected
    assert selected == [channel] * _fov_count(mpc)  # once at each position, not at each of its 3 z levels


def test_alternating_channels_are_applied_for_every_image(aion_scope, monkeypatch):
    selected = _record_selected_configs(monkeypatch)
    unit = FakeConfocalUnit(aion_scope.camera)
    mpc, tracker = _fluorescence_acquisition(aion_scope, unit, nz=2)
    names = [c.name for c in mpc.selected_configurations]
    expected = mpc.get_acquisition_image_count()

    _run(mpc, tracker)

    assert tracker.image_count == expected
    assert selected == names * (_fov_count(mpc) * 2)


def _already_applied(applied, current, config):
    worker = types.SimpleNamespace(
        _applied_config=applied, liveController=types.SimpleNamespace(currentConfiguration=current)
    )
    return MultiPointWorker._config_already_applied(worker, config)


def test_a_channel_counts_as_applied_only_while_it_is_unchanged_and_current(scope):
    channels = scope.live_controller.get_channels(scope.objective_store.current_objective)
    config, other = channels[0], channels[1]
    applied = config.model_copy(deep=True)

    assert _already_applied(applied, config, config)
    assert not _already_applied(None, config, config)  # nothing applied yet at this position
    assert not _already_applied(applied, other, config)  # something else set another channel since

    edited = config.model_copy(deep=True)
    edited.camera_settings.exposure_time_ms += 1.0
    assert not _already_applied(applied, edited, edited)  # the channel was edited since it was applied


@pytest.mark.parametrize("trigger_mode", [TriggerMode.SOFTWARE, TriggerMode.HARDWARE])
def test_disk_space_estimate_does_not_trigger_the_camera(scope, monkeypatch, trigger_mode):
    """In hardware trigger mode the test frame never came (AION: a confocal scan, then a 1.1 s time-out)."""
    camera = scope.camera
    calls = []
    monkeypatch.setattr(camera, "send_trigger", lambda *a, **k: calls.append("send_trigger"))
    monkeypatch.setattr(camera, "read_camera_frame", lambda *a, **k: calls.append("read_camera_frame"))
    mpc = ts.get_test_multi_point_controller(microscope=scope)
    mpc.liveController.set_trigger_mode(trigger_mode)
    monkeypatch.setattr(control._def, "MERGE_CHANNELS", False)
    mpc.scanCoordinates.clear_regions()
    mpc.scanCoordinates.add_single_fov_region(
        "single",
        center_x=mpc.stage.get_config().X_AXIS.MIN_POSITION + 1.0,
        center_y=mpc.stage.get_config().Y_AXIS.MIN_POSITION + 1.0,
        center_z=mpc.stage.get_config().Z_AXIS.MIN_POSITION + 1.0,
    )
    mpc.set_selected_configurations([mpc.liveController.get_channels(scope.objective_store.current_objective)[0].name])
    mpc.set_NZ(3)

    estimate = mpc.get_estimated_acquisition_disk_storage()

    assert calls == []
    width, height = camera.get_crop_size()
    assert estimate >= 3 * width * height * 2  # three uncompressible 16-bit images
