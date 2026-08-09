import pytest

import control._def
from control._def import PRIMARY_CAMERA_ID, TriggerMode
from control.core.config.repository import ConfigRepository
from control.microscope import Microscope
from control.models.camera_registry import CameraDefinition, CameraRegistryConfig
from squid.camera.facade import ActiveCameraFacade

TWO_CAMERA_REGISTRY = CameraRegistryConfig(
    cameras=[
        CameraDefinition(name="Main Camera", id=1, serial_number="SIM-1", type="Toupcam"),
        CameraDefinition(
            name="Side Camera",
            id=2,
            serial_number="SIM-2",
            type="Toupcam",
            hardware_trigger=False,
            default_pixel_format="RGB24",
        ),
    ]
)


@pytest.fixture
def two_camera_scope(monkeypatch):
    monkeypatch.setattr(ConfigRepository, "get_camera_registry", lambda self: TWO_CAMERA_REGISTRY)
    scope = Microscope.build_from_global_config(simulated=True, skip_init=True)
    yield scope
    scope.close()


@pytest.fixture
def single_camera_scope(monkeypatch):
    monkeypatch.setattr(ConfigRepository, "get_camera_registry", lambda self: None)
    scope = Microscope.build_from_global_config(simulated=True, skip_init=True)
    yield scope
    scope.close()


def test_single_camera_build_has_no_facade(single_camera_scope):
    scope = single_camera_scope
    assert not isinstance(scope.camera, ActiveCameraFacade)
    assert list(scope.cameras.keys()) == [PRIMARY_CAMERA_ID]
    assert scope.cameras[PRIMARY_CAMERA_ID] is scope.camera
    assert scope.active_camera_id == PRIMARY_CAMERA_ID
    with pytest.raises(ValueError):
        scope.set_active_camera(2)
    scope.set_active_camera(PRIMARY_CAMERA_ID)  # no-op, allowed


def test_two_camera_build_installs_facade(two_camera_scope):
    scope = two_camera_scope
    assert isinstance(scope.camera, ActiveCameraFacade)
    assert sorted(scope.cameras.keys()) == [1, 2]
    assert scope.active_camera_id == PRIMARY_CAMERA_ID
    assert scope.cameras[1].supports_hardware_trigger() is True
    assert scope.cameras[2].supports_hardware_trigger() is False
    # Per-camera config took effect
    assert scope.cameras[2]._config.serial_number == "SIM-2"


def test_switch_applies_software_trigger_and_mcu_mode(two_camera_scope):
    scope = two_camera_scope
    scope.set_active_camera(2)
    assert scope.active_camera_id == 2
    assert scope.camera.get_active_id() == 2
    assert scope.live_controller.trigger_mode == TriggerMode.SOFTWARE
    from squid.abc import CameraAcquisitionMode

    assert scope.cameras[2].get_acquisition_mode() == CameraAcquisitionMode.SOFTWARE_TRIGGER


def test_switch_restores_stored_mode_on_primary(two_camera_scope):
    scope = two_camera_scope
    scope.live_controller.set_trigger_mode(TriggerMode.HARDWARE)  # user choice on primary
    assert scope.get_stored_trigger_mode(1) == TriggerMode.HARDWARE
    scope.set_active_camera(2)
    assert scope.live_controller.trigger_mode == TriggerMode.SOFTWARE
    scope.set_active_camera(1)
    assert scope.live_controller.trigger_mode == TriggerMode.HARDWARE


def test_change_listener_fires_once_per_switch(two_camera_scope):
    scope = two_camera_scope
    seen = []
    scope.add_camera_change_listener(seen.append)
    scope.set_active_camera(2)
    scope.set_active_camera(2)  # no-op fast path: no second notification
    scope.set_active_camera(1)
    assert seen == [2, 1]


def test_failed_switch_rolls_back_active_camera(two_camera_scope, monkeypatch):
    """If applying the incoming camera's trigger mode raises mid-switch (unwired camera
    rejecting HARDWARE, MCU timeout), the switch must roll back: facade and active id
    restored to the outgoing camera, and no change listener notified."""
    scope = two_camera_scope
    seen = []
    scope.add_camera_change_listener(seen.append)

    def boom(mode):
        raise RuntimeError("simulated MCU timeout")

    monkeypatch.setattr(scope.live_controller, "set_trigger_mode", boom)

    with pytest.raises(RuntimeError, match="simulated MCU timeout"):
        scope.set_active_camera(2)

    assert scope.active_camera_id == 1
    assert scope.camera.get_active_id() == 1
    assert seen == []


def test_close_closes_all_cameras(two_camera_scope):
    scope = two_camera_scope
    closed = []
    for cam_id, cam in scope.cameras.items():
        cam.close = lambda cid=cam_id: closed.append(cid)
    scope.close()
    assert sorted(closed) == [1, 2]
