"""Tests for the ToupTek (toupcam) driver that need no camera hardware.

The vendored SDK binding (control/toupcam.py) loads its shared library lazily,
so `control.camera_toupcam` imports fine on a machine with no ToupTek attached.
Every test here swaps `control.camera_toupcam.toupcam` for a fake SDK module
(constants still come from the real binding) so we exercise the real driver
logic against a scripted set of enumerated devices.
"""

import inspect
from typing import Dict, List, Optional

import pytest

import control.toupcam as real_toupcam
import squid.logging
from control.camera_toupcam import ToupcamCamera
from squid.abc import CameraPixelFormat
from squid.config import CameraConfig, CameraVariant


class FakeToupcamHandle:
    """Stand-in for an open toupcam.Toupcam handle."""

    def __init__(self, device_id: str, serial: Optional[str], serial_raises: bool = False):
        self.device_id = device_id
        self._serial = serial
        self._serial_raises = serial_raises
        self.closed = False
        self.awb_init_calls = 0
        self.awb_once_calls = 0

    def SerialNumber(self) -> str:
        if self._serial_raises:
            raise real_toupcam.HRESULTException(-1)
        return self._serial

    def Close(self):
        self.closed = True

    # --- used by the auto-white-balance tests -------------------------------
    def AwbInit(self):
        self.awb_init_calls += 1

    def AwbOnce(self):
        self.awb_once_calls += 1

    def get_WhiteBalanceGain(self):
        return (11, 22, 33)


class FakeDeviceSpec:
    def __init__(
        self,
        device_id: str,
        serial: Optional[str] = None,
        displayname: str = "FakeCam",
        flag: int = 0,
        resolutions=((3000, 2000), (1500, 1000)),
        openable: bool = True,
        serial_raises: bool = False,
    ):
        self.device_id = device_id
        self.serial = serial
        self.displayname = displayname
        self.flag = flag
        self.resolutions = resolutions
        self.openable = openable
        self.serial_raises = serial_raises


class _FakeResolution:
    def __init__(self, width, height):
        self.width = width
        self.height = height


class _FakeModel:
    def __init__(self, spec: FakeDeviceSpec):
        self.flag = spec.flag
        self.preview = len(spec.resolutions)
        self.still = 0
        self.res = [_FakeResolution(w, h) for (w, h) in spec.resolutions]


class _FakeDevice:
    def __init__(self, spec: FakeDeviceSpec):
        self.displayname = spec.displayname
        self.id = spec.device_id
        self.model = _FakeModel(spec)


class FakeToupcamSdk:
    """Fake `control.toupcam` module: fake Toupcam class, real constants.

    Attribute lookups that miss fall through to the real binding, so the driver
    still sees the genuine TOUPCAM_* constants and exception types.
    """

    def __init__(self, specs: List[FakeDeviceSpec]):
        self._specs = specs
        self.open_calls: List[str] = []
        self.handles: Dict[str, FakeToupcamHandle] = {}
        sdk = self

        class _FakeToupcam:
            @staticmethod
            def EnumV2():
                return [_FakeDevice(s) for s in sdk._specs]

            @staticmethod
            def Open(cam_id):
                sdk.open_calls.append(cam_id)
                spec = next((s for s in sdk._specs if s.device_id == cam_id), None)
                if spec is None or not spec.openable:
                    return None
                handle = FakeToupcamHandle(spec.device_id, spec.serial, spec.serial_raises)
                sdk.handles[cam_id] = handle
                return handle

        self.Toupcam = _FakeToupcam

    def __getattr__(self, name):
        # Constants (TOUPCAM_FLAG_*, ...) and exception types come from the real binding.
        return getattr(real_toupcam, name)


@pytest.fixture
def fake_sdk(monkeypatch):
    def _install(specs: List[FakeDeviceSpec]) -> FakeToupcamSdk:
        sdk = FakeToupcamSdk(specs)
        monkeypatch.setattr("control.camera_toupcam.toupcam", sdk)
        return sdk

    return _install


def _config(serial_number: Optional[str] = None) -> CameraConfig:
    return CameraConfig(
        camera_type=CameraVariant.TOUPCAM,
        default_pixel_format=CameraPixelFormat.MONO8,
        serial_number=serial_number,
    )


# --------------------------------------------------------------------------
# _open: index / no-serial behavior (pins today's behavior)
# --------------------------------------------------------------------------


def test_open_index_zero_opens_first_device(fake_sdk):
    sdk = fake_sdk([FakeDeviceSpec("port-a", serial="SN-A"), FakeDeviceSpec("port-b", serial="SN-B")])

    camera, capabilities = ToupcamCamera._open(index=0)

    assert camera is sdk.handles["port-a"]
    assert sdk.open_calls == ["port-a"]  # no probing when an index is given
    assert capabilities.binning_to_resolution == {(1, 1): (3000, 2000), (2, 2): (1500, 1000)}


def test_open_index_one_opens_second_device(fake_sdk):
    sdk = fake_sdk([FakeDeviceSpec("port-a", serial="SN-A"), FakeDeviceSpec("port-b", serial="SN-B")])

    camera, _ = ToupcamCamera._open(index=1)

    assert camera is sdk.handles["port-b"]
    assert sdk.open_calls == ["port-b"]


def test_open_capabilities_from_device_flags(fake_sdk):
    flag = real_toupcam.TOUPCAM_FLAG_FAN | real_toupcam.TOUPCAM_FLAG_BLACKLEVEL
    fake_sdk([FakeDeviceSpec("port-a", serial="SN-A", flag=flag)])

    _, capabilities = ToupcamCamera._open(index=0)

    assert capabilities.has_fan
    assert capabilities.has_black_level
    assert not capabilities.has_TEC
    assert not capabilities.has_low_noise_mode


def test_open_with_no_devices_raises(fake_sdk):
    fake_sdk([])

    with pytest.raises(ValueError, match="no Toupcam"):
        ToupcamCamera._open(index=0)


def test_open_with_index_out_of_range_raises_value_error(fake_sdk):
    fake_sdk([FakeDeviceSpec("port-a", serial="SN-A")])

    with pytest.raises(ValueError):
        ToupcamCamera._open(index=3)


def test_open_rejects_both_index_and_sn(fake_sdk):
    fake_sdk([FakeDeviceSpec("port-a", serial="SN-A")])

    with pytest.raises(ValueError, match="both"):
        ToupcamCamera._open(index=0, sn="SN-A")


# --------------------------------------------------------------------------
# _open: serial number matching
# --------------------------------------------------------------------------


def test_open_by_enumeration_id_does_not_probe(fake_sdk):
    sdk = fake_sdk(
        [
            FakeDeviceSpec("port-a", serial="SN-A"),
            FakeDeviceSpec("port-b", serial="SN-B"),
            FakeDeviceSpec("port-c", serial="SN-C"),
        ]
    )

    camera, _ = ToupcamCamera._open(sn="port-b")

    assert camera is sdk.handles["port-b"]
    assert not camera.closed
    # Matching an enumeration id must not open (probe) any other camera.
    assert sdk.open_calls == ["port-b"]


def test_open_by_true_serial_probes_and_closes_non_matches(fake_sdk):
    sdk = fake_sdk(
        [
            FakeDeviceSpec("port-a", serial="SN-A"),
            FakeDeviceSpec("port-b", serial="SN-B"),
            FakeDeviceSpec("port-c", serial="SN-C"),
        ]
    )

    camera, capabilities = ToupcamCamera._open(sn="SN-B")

    assert camera is sdk.handles["port-b"]
    assert not camera.closed, "the matched camera must stay open and be handed to the caller"
    assert sdk.handles["port-a"].closed, "a probed, non-matching camera must be closed again"
    # Probe port-a, then port-b (the match).  port-c is never touched, and the handle the
    # probe already opened is handed straight to the caller instead of being reopened.
    assert sdk.open_calls == ["port-a", "port-b"]
    assert sdk.open_calls.count("port-b") == 1, "the matched device must not be opened a second time"
    assert capabilities.binning_to_resolution == {(1, 1): (3000, 2000), (2, 2): (1500, 1000)}


def test_open_by_true_serial_uses_matching_devices_capabilities(fake_sdk):
    fake_sdk(
        [
            FakeDeviceSpec("port-a", serial="SN-A", flag=real_toupcam.TOUPCAM_FLAG_FAN),
            FakeDeviceSpec("port-b", serial="SN-B", flag=real_toupcam.TOUPCAM_FLAG_TEC_ONOFF),
        ]
    )

    _, capabilities = ToupcamCamera._open(sn="SN-B")

    assert capabilities.has_TEC
    assert not capabilities.has_fan


def test_open_by_true_serial_skips_devices_that_cannot_be_probed(fake_sdk):
    # A camera already opened by someone else cannot be probed; we must keep going.
    sdk = fake_sdk(
        [
            FakeDeviceSpec("port-a", serial="SN-A", openable=False),
            FakeDeviceSpec("port-b", serial="SN-B"),
        ]
    )

    camera, _ = ToupcamCamera._open(sn="SN-B")

    assert camera is sdk.handles["port-b"]


def test_open_by_true_serial_survives_serial_read_failure(fake_sdk):
    sdk = fake_sdk(
        [
            FakeDeviceSpec("port-a", serial=None, serial_raises=True),
            FakeDeviceSpec("port-b", serial="SN-B"),
        ]
    )

    camera, _ = ToupcamCamera._open(sn="SN-B")

    assert camera is sdk.handles["port-b"]
    assert sdk.handles["port-a"].closed


def test_open_with_unknown_serial_raises_listing_ids_and_serials(fake_sdk):
    fake_sdk(
        [
            FakeDeviceSpec("port-a", serial="SN-A"),
            FakeDeviceSpec("port-b", serial="SN-B"),
        ]
    )

    with pytest.raises(ValueError) as exc_info:
        ToupcamCamera._open(sn="SN-NOPE")

    message = str(exc_info.value)
    assert "SN-NOPE" in message
    for identifier in ("port-a", "port-b", "SN-A", "SN-B"):
        assert identifier in message, f"{identifier} missing from error message: {message}"


def test_open_with_unknown_serial_closes_every_probe(fake_sdk):
    sdk = fake_sdk([FakeDeviceSpec("port-a", serial="SN-A"), FakeDeviceSpec("port-b", serial="SN-B")])

    with pytest.raises(ValueError):
        ToupcamCamera._open(sn="SN-NOPE")

    assert set(sdk.handles) == {"port-a", "port-b"}, "every device should have been probed"
    assert all(handle.closed for handle in sdk.handles.values())


def test_open_does_not_leak_the_matched_handle_when_capabilities_fail(fake_sdk):
    # A device with no resolutions makes capability building fail *after* sn probing
    # already opened the matched device - that handle must not be leaked.
    sdk = fake_sdk([FakeDeviceSpec("port-a", serial="SN-A", resolutions=())])

    with pytest.raises(ValueError, match="No resolutions"):
        ToupcamCamera._open(sn="SN-A")

    assert sdk.handles["port-a"].closed


def test_open_raises_when_sdk_open_returns_none(fake_sdk):
    fake_sdk([FakeDeviceSpec("port-a", serial="SN-A", openable=False)])

    with pytest.raises(ValueError):
        ToupcamCamera._open(index=0)


# --------------------------------------------------------------------------
# Config -> open wiring
# --------------------------------------------------------------------------


def test_open_for_config_without_serial_uses_index_zero(monkeypatch):
    calls = []
    monkeypatch.setattr(
        ToupcamCamera, "_open", staticmethod(lambda index=None, sn=None: calls.append((index, sn)) or ("cam", "caps"))
    )

    assert ToupcamCamera._open_for_config(_config(serial_number=None)) == ("cam", "caps")
    assert calls == [(0, None)]


def test_open_for_config_with_serial_opens_by_serial(monkeypatch):
    calls = []
    monkeypatch.setattr(
        ToupcamCamera, "_open", staticmethod(lambda index=None, sn=None: calls.append((index, sn)) or ("cam", "caps"))
    )

    ToupcamCamera._open_for_config(_config(serial_number="SN-B"))

    assert calls == [(None, "SN-B")]


def test_open_for_config_treats_empty_serial_as_unset(monkeypatch):
    calls = []
    monkeypatch.setattr(
        ToupcamCamera, "_open", staticmethod(lambda index=None, sn=None: calls.append((index, sn)) or ("cam", "caps"))
    )

    ToupcamCamera._open_for_config(_config(serial_number=""))

    assert calls == [(0, None)]


class _StopInit(Exception):
    """Sentinel raised from a patched _open_for_config to end __init__ early."""


def test_init_routes_through_open_for_config(monkeypatch):
    seen = []

    def _fake_open_for_config(config):
        seen.append(config.serial_number)
        raise _StopInit()

    monkeypatch.setattr(ToupcamCamera, "_open_for_config", staticmethod(_fake_open_for_config))

    with pytest.raises(_StopInit):
        ToupcamCamera(_config(serial_number="SN-B"), None, None)

    assert seen == ["SN-B"]


# --------------------------------------------------------------------------
# set_auto_white_balance_gains
# --------------------------------------------------------------------------


def _camera_with_fake_handle() -> ToupcamCamera:
    """A ToupcamCamera with just enough state for the white balance methods."""
    camera = ToupcamCamera.__new__(ToupcamCamera)
    camera._camera = FakeToupcamHandle("port-a", "SN-A")
    camera._log = squid.logging.get_logger("test_camera_toupcam")
    return camera


def test_set_auto_white_balance_gains_signature_matches_abstract():
    parameters = list(inspect.signature(ToupcamCamera.set_auto_white_balance_gains).parameters)
    assert parameters == ["self", "on"]


def test_set_auto_white_balance_gains_on_triggers_sdk_awb():
    camera = _camera_with_fake_handle()

    result = camera.set_auto_white_balance_gains(on=True)

    assert camera._camera.awb_init_calls == 1
    assert result == (11, 22, 33)


def test_set_auto_white_balance_gains_off_does_not_trigger_awb():
    camera = _camera_with_fake_handle()

    camera.set_auto_white_balance_gains(on=False)

    assert camera._camera.awb_init_calls == 0
    assert camera._camera.awb_once_calls == 0
