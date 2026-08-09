import pytest

import squid.config
from squid.abc import CameraAcquisitionMode, CameraFrame
from squid.camera.facade import ActiveCameraFacade
from squid.camera.utils import SimulatedCamera
from squid.config import CameraPixelFormat


def make_sim(serial, pixel_format=CameraPixelFormat.MONO16, hw=False):
    config = squid.config.get_camera_config().model_copy(
        update={"serial_number": serial, "default_pixel_format": pixel_format}
    )
    hw_trigger_fn = (lambda t: True) if hw else None
    hw_strobe_fn = (lambda ms: True) if hw else None
    return SimulatedCamera(config, hw_trigger_fn=hw_trigger_fn, hw_set_strobe_delay_ms_fn=hw_strobe_fn)


@pytest.fixture
def cameras():
    cam1 = make_sim("SN1", hw=True)
    cam2 = make_sim("SN2", hw=False)
    yield {1: cam1, 2: cam2}
    cam1.close()
    cam2.close()


def test_supports_hardware_trigger(cameras):
    assert cameras[1].supports_hardware_trigger() is True
    assert cameras[2].supports_hardware_trigger() is False


def test_delegates_to_active(cameras):
    facade = ActiveCameraFacade(cameras, active_id=1)
    cameras[1].set_exposure_time(11)
    cameras[2].set_exposure_time(22)
    assert facade.get_exposure_time() == 11
    facade.set_active(2)
    assert facade.get_exposure_time() == 22
    # Writes go to the active camera only
    facade.set_exposure_time(33)
    assert cameras[2].get_exposure_time() == 33
    assert cameras[1].get_exposure_time() == 11


def test_invalid_active_id_raises(cameras):
    with pytest.raises(ValueError):
        ActiveCameraFacade(cameras, active_id=9)
    facade = ActiveCameraFacade(cameras, active_id=1)
    with pytest.raises(ValueError):
        facade.set_active(9)


def test_callbacks_survive_switch_and_drop_inactive(cameras):
    facade = ActiveCameraFacade(cameras, active_id=1)
    received = []
    facade.add_frame_callback(lambda frame: received.append(frame.frame_id))

    cameras[1].send_trigger()  # active -> forwarded
    assert len(received) == 1
    cameras[2].send_trigger()  # inactive -> dropped
    assert len(received) == 1

    facade.set_active(2)
    cameras[2].send_trigger()  # now active -> forwarded, same registration
    assert len(received) == 2
    cameras[1].send_trigger()  # now inactive -> dropped
    assert len(received) == 2


def test_enable_callbacks_gates_forwarding(cameras):
    facade = ActiveCameraFacade(cameras, active_id=1)
    received = []
    facade.add_frame_callback(lambda frame: received.append(frame.frame_id))
    facade.enable_callbacks(False)
    assert facade.get_callbacks_enabled() is False
    cameras[1].send_trigger()
    assert received == []
    facade.enable_callbacks(True)
    cameras[1].send_trigger()
    assert len(received) == 1


def test_remove_frame_callback(cameras):
    facade = ActiveCameraFacade(cameras, active_id=1)
    received = []
    cb_id = facade.add_frame_callback(lambda frame: received.append(frame.frame_id))
    facade.remove_frame_callback(cb_id)
    cameras[1].send_trigger()
    assert received == []


def test_hardware_mode_on_unwired_active_raises(cameras):
    facade = ActiveCameraFacade(cameras, active_id=2)
    with pytest.raises(ValueError):
        facade.set_acquisition_mode(CameraAcquisitionMode.HARDWARE_TRIGGER)
    facade.set_active(1)
    facade.set_acquisition_mode(CameraAcquisitionMode.HARDWARE_TRIGGER)  # wired camera: OK
    assert facade.get_acquisition_mode() == CameraAcquisitionMode.HARDWARE_TRIGGER


def test_is_color_and_geometry_follow_active(cameras):
    cameras[2].set_pixel_format(CameraPixelFormat.RGB24)
    facade = ActiveCameraFacade(cameras, active_id=1)
    assert facade.is_color is False
    facade.set_active(2)
    assert facade.is_color is True
    assert facade.get_crop_size() == cameras[2].get_crop_size()
    assert facade.get_pixel_size_binned_um() == cameras[2].get_pixel_size_binned_um()


def test_close_closes_all(cameras):
    closed = []
    for cam_id, cam in cameras.items():
        cam.close = lambda cid=cam_id: closed.append(cid)
    facade = ActiveCameraFacade(cameras, active_id=1)
    facade.close()
    assert sorted(closed) == [1, 2]
