"""Tests for the Andor SDK3 camera driver.

pyAndorSDK3 ships with Andor's licensed SDK and is not installed in CI, so a fake module stands in
for it; only that SDK boundary is faked, the driver under test is the real one. The fake camera
models SDK3 the way pyAndorSDK3 exposes it: features are attributes, image data flows through
queue() / wait_buffer().
"""

import logging
import sys
import threading
import time
import types
from types import SimpleNamespace

import numpy as np
import pytest

from squid.abc import CameraAcquisitionMode
from squid.config import AndorCameraModel, CameraConfig, CameraPixelFormat, CameraVariant


class FakeAndorCamera:
    """An SDK3 camera handle with a Zyla 4.2's defaults."""

    def __init__(self, sensor=(2048, 2048), pixel_width_um=6.5, serial="VSC-00001"):
        self.CameraModel = "ZYLA-4.2P-USB3-C"
        self.SerialNumber = serial
        self.SensorWidth, self.SensorHeight = sensor
        self.PixelWidth = pixel_width_um
        self.min_ExposureTime = 1e-5
        self.max_ExposureTime = 30.0
        self.ExposureTime = 0.02
        self.AOIBinning = "1x1"
        self.AOILeft = 1
        self.AOITop = 1
        self.AOIWidth, self.AOIHeight = sensor
        self.PixelEncoding = "Mono16"
        self.SimplePreAmpGainControl = "16-bit (low noise & high well capacity)"
        self.TriggerMode = "Internal"
        self.CycleMode = "Continuous"
        self.LineScanSpeed = 100000.0  # rows per second: 10 us per row
        self.AuxiliaryOutSource = "FireRow1"  # the power-on default
        self.SensorTemperature = -5.0
        self.is_open = False
        self.acquiring = False
        self.software_triggers = 0
        self.frames = []  # 2-D uint16 arrays the "sensor" delivers, in order
        self.row_padding_bytes = 0  # SDK3 may pad rows: AOIStride > AOIWidth * 2
        self.scrub_requeued_buffers = False  # model the SDK writing into a buffer as soon as it gets it back
        self._queued = []

    # ---- layout the SDK reports for the current AOI
    @property
    def AOIStride(self):
        return self.AOIWidth * 2 + self.row_padding_bytes

    @property
    def ImageSizeBytes(self):
        return self.AOIStride * self.AOIHeight

    # ---- SDK calls
    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False

    def AcquisitionStart(self):
        self.acquiring = True

    def AcquisitionStop(self):
        self.acquiring = False

    def SoftwareTrigger(self):
        self.software_triggers += 1

    def queue(self, buf, size):
        if self.scrub_requeued_buffers:
            buf[:] = 0
        self._queued.append(buf)

    def flush(self):
        self._queued = []

    def wait_buffer(self, timeout_ms):
        if not self.frames or not self._queued:
            time.sleep(min(timeout_ms, 50) / 1000.0)
            raise TimeoutError("AT_WaitBuffer timeout")
        frame = self.frames.pop(0)
        buf = self._queued.pop(0)
        assert frame.shape == (self.AOIHeight, self.AOIWidth), "the sensor delivers the AOI"
        rows = buf[: self.ImageSizeBytes].reshape(self.AOIHeight, self.AOIStride)
        rows[:, : self.AOIWidth * 2] = np.frombuffer(frame.astype("<u2").tobytes(), dtype=np.uint8).reshape(
            self.AOIHeight, self.AOIWidth * 2
        )
        return SimpleNamespace(_np_data=buf)


class FakeAndorSDK3:
    cameras = []  # tests install the camera(s) a session should find

    @property
    def DeviceCount(self):
        return len(self.cameras)

    def GetCamera(self, index):
        return self.cameras[index]


def _install_fake_sdk():
    module = types.ModuleType("pyAndorSDK3")
    module.__file__ = "/nonexistent/pyAndorSDK3/__init__.py"
    utils = types.ModuleType("pyAndorSDK3.utils")
    utils.added_library_paths = []
    utils.add_library_path = utils.added_library_paths.append
    module.utils = utils
    module.AndorSDK3 = FakeAndorSDK3
    sys.modules["pyAndorSDK3"] = module
    sys.modules["pyAndorSDK3.utils"] = utils
    return module


fake_sdk = _install_fake_sdk()
import control.camera_andor as camera_andor  # noqa: E402 - needs the fake SDK in place


def _config(**overrides) -> CameraConfig:
    fields = dict(
        camera_type=CameraVariant.ANDOR,
        camera_model=AndorCameraModel.ZYLA_4_2P_USB3_C,
        default_pixel_format=CameraPixelFormat.MONO16,
        default_binning=(1, 1),
        crop_width=2048,
        crop_height=2048,
    )
    fields.update(overrides)
    return CameraConfig(**fields)


@pytest.fixture
def sdk_camera(monkeypatch):
    fake = FakeAndorCamera()
    monkeypatch.setattr(FakeAndorSDK3, "cameras", [fake])
    return fake


def make_camera(sdk_camera, monkeypatch, **config_overrides):
    monkeypatch.setattr(FakeAndorSDK3, "cameras", [sdk_camera])
    strobe_delays = []
    camera = camera_andor.AndorCamera(
        _config(**config_overrides),
        hw_trigger_fn=lambda illumination_time: True,
        hw_set_strobe_delay_ms_fn=strobe_delays.append,
    )
    camera.hw_strobe_delays_ms = strobe_delays
    return camera


@pytest.fixture
def camera(sdk_camera, monkeypatch):
    cam = make_camera(sdk_camera, monkeypatch)
    yield cam
    cam.close()


# ------------------------------------------------------------------ bring-up


def test_the_sdk_library_path_is_only_added_where_it_exists():
    # The fake package lives nowhere, so no platform folder can be added at import
    assert fake_sdk.utils.added_library_paths == []


@pytest.mark.parametrize("platform, folder", [("win32", "Windows"), ("linux", "Linux")])
def test_the_sdk_library_folder_matches_the_platform(platform, folder):
    assert camera_andor._sdk_library_dir("/pkg", platform) == f"/pkg/libs/{folder}/64"


def test_opens_the_first_camera(sdk_camera, monkeypatch):
    cam = make_camera(sdk_camera, monkeypatch)

    assert sdk_camera.is_open
    cam.close()
    assert not sdk_camera.is_open


def test_initial_pixel_format_comes_from_the_config(sdk_camera, monkeypatch):
    cam = make_camera(sdk_camera, monkeypatch, default_pixel_format=CameraPixelFormat.MONO12)

    assert sdk_camera.PixelEncoding == "Mono12"
    assert sdk_camera.SimplePreAmpGainControl == "12-bit (low noise)"
    assert cam.get_pixel_format() == CameraPixelFormat.MONO12
    cam.close()


def test_resolutions_follow_the_sensor_the_camera_reports(monkeypatch):
    zyla_5_5 = FakeAndorCamera(sensor=(2560, 2160))
    cam = make_camera(zyla_5_5, monkeypatch, crop_width=2560, crop_height=2160)

    assert cam.get_resolution() == (2560, 2160)
    cam.set_binning(2, 2)
    assert cam.get_resolution() == (1280, 1080)
    assert (3, 3) in cam.get_binning_options()
    cam.close()


def test_pixel_size_comes_from_the_camera(monkeypatch):
    big_pixels = FakeAndorCamera(pixel_width_um=11.0)
    cam = make_camera(big_pixels, monkeypatch)

    assert cam.get_pixel_size_unbinned_um() == 11.0
    cam.set_binning(2, 2)
    assert cam.get_pixel_size_binned_um() == 22.0
    cam.close()


def test_auxiliary_output_is_left_alone_unless_configured(sdk_camera, monkeypatch):
    monkeypatch.setattr(camera_andor, "ANDOR_AUX_OUTPUT_SOURCE", None)
    cam = make_camera(sdk_camera, monkeypatch)

    assert sdk_camera.AuxiliaryOutSource == "FireRow1"
    cam.close()


def test_auxiliary_output_follows_the_configured_source(sdk_camera, monkeypatch):
    monkeypatch.setattr(camera_andor, "ANDOR_AUX_OUTPUT_SOURCE", "FireAll")
    cam = make_camera(sdk_camera, monkeypatch)

    assert sdk_camera.AuxiliaryOutSource == "FireAll"
    cam.close()


# ------------------------------------------------------------------ settings


def test_analog_gain_is_reported_as_unsupported(camera):
    with pytest.raises(NotImplementedError):
        camera.set_analog_gain(2.0)
    with pytest.raises(NotImplementedError):
        camera.get_analog_gain()
    with pytest.raises(NotImplementedError):
        camera.get_gain_range()


def test_auto_white_balance_takes_the_on_flag(camera):
    with pytest.raises(NotImplementedError):
        camera.set_auto_white_balance_gains(on=True)


def test_exposure_in_hardware_trigger_mode_adds_the_readout_and_reports_the_strobe(camera, sdk_camera):
    camera.set_acquisition_mode(CameraAcquisitionMode.HARDWARE_TRIGGER)
    camera.set_exposure_time(20.0)

    # 2048 rows at 10 us per row = 20.48 ms of readout
    assert camera.get_strobe_time() == pytest.approx(20.48)
    assert sdk_camera.TriggerMode == "External"
    assert sdk_camera.ExposureTime == pytest.approx((20.0 + 20.48) / 1000.0)
    assert camera.hw_strobe_delays_ms[-1] == pytest.approx(20.48)
    assert camera.get_exposure_time() == 20.0


# ------------------------------------------------------------------ streaming and triggers


def test_not_ready_for_trigger_while_streaming_is_stopped(camera):
    camera.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)

    assert not camera.get_ready_for_trigger()
    camera.start_streaming()
    assert camera.get_ready_for_trigger()
    camera.stop_streaming()
    assert not camera.get_ready_for_trigger()


def test_software_trigger_delivers_numbered_frames(camera, sdk_camera):
    camera.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)
    camera.start_streaming()
    received = []
    camera.add_frame_callback(received.append)

    for value in (7, 8):
        sdk_camera.frames.append(np.full((2048, 2048), value, dtype=np.uint16))
        camera.send_trigger()
        frame = camera.read_camera_frame()
        assert frame is not None
        assert frame.frame.shape == (2048, 2048)
        assert frame.frame.dtype == np.uint16
        assert int(frame.frame[0, 0]) == value

    assert sdk_camera.software_triggers == 2
    assert [f.frame_id for f in received] == [1, 2]


def test_close_stops_the_read_thread_promptly(camera, caplog):
    camera.start_streaming()
    thread = camera._read_thread

    with caplog.at_level(logging.WARNING):
        camera.close()

    assert thread is not None and not thread.is_alive()
    assert not any("refused to exit" in record.getMessage() for record in caplog.records)


# ------------------------------------------------------------------ region of interest


def _roi_frame(camera, sdk_camera, value):
    sdk_camera.frames.append(np.full((sdk_camera.AOIHeight, sdk_camera.AOIWidth), value, dtype=np.uint16))
    camera.send_trigger()
    return camera.read_camera_frame()


def test_roi_is_set_through_the_aoi_features(camera, sdk_camera):
    camera.set_region_of_interest(256, 128, 1024, 512)

    # SDK3: width/height in (binned) pixels, left/top 1-based
    assert (sdk_camera.AOIWidth, sdk_camera.AOIHeight) == (1024, 512)
    assert (sdk_camera.AOILeft, sdk_camera.AOITop) == (257, 129)
    assert camera.get_region_of_interest() == (256, 128, 1024, 512)


def test_roi_offsets_are_in_binned_pixels_as_the_gui_passes_them(camera, sdk_camera):
    camera.set_binning(2, 2)

    camera.set_region_of_interest(100, 50, 400, 200)

    # left/top are unbinned sensor coordinates on the camera
    assert (sdk_camera.AOILeft, sdk_camera.AOITop) == (201, 101)
    assert (sdk_camera.AOIWidth, sdk_camera.AOIHeight) == (400, 200)
    assert camera.get_region_of_interest() == (100, 50, 400, 200)


def test_frames_follow_the_aoi_and_its_row_stride(camera, sdk_camera):
    sdk_camera.row_padding_bytes = 64
    camera.set_region_of_interest(256, 128, 1024, 512)
    camera.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)
    camera.start_streaming()

    frame = _roi_frame(camera, sdk_camera, 1234)

    assert frame.frame.shape == (512, 1024)
    assert int(frame.frame.min()) == 1234 and int(frame.frame.max()) == 1234


def test_frame_data_is_copied_before_the_buffer_goes_back_to_the_sdk(camera, sdk_camera):
    sdk_camera.scrub_requeued_buffers = True
    camera.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)
    camera.start_streaming()

    frame = _roi_frame(camera, sdk_camera, 99)

    assert int(frame.frame[0, 0]) == 99


def test_roi_change_reapplies_exposure_and_strobe_in_hardware_trigger_mode(camera, sdk_camera):
    camera.set_acquisition_mode(CameraAcquisitionMode.HARDWARE_TRIGGER)
    camera.set_exposure_time(20.0)

    camera.set_region_of_interest(0, 768, 2048, 512)

    # readout now covers 512 rows at 10 us per row
    assert camera.get_strobe_time() == pytest.approx(5.12)
    assert camera.hw_strobe_delays_ms[-1] == pytest.approx(5.12)
    assert sdk_camera.ExposureTime == pytest.approx((20.0 + 5.12) / 1000.0)
