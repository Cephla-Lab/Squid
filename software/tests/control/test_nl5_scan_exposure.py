"""Exposure on a confocal unit that level-triggers the camera (AION / NL5 with NL5_USE_DOUT), headless.

The camera (Tucsen "trigger width" mode) exposes for as long as the unit holds its trigger high: the exposure delay
plus the scan, and ignores its own exposure setting. So the channel exposure sets the unit's line speed, the camera
is told the exposure the unit produces (what its waits and timeouts use), and the acquisition metadata records it.
"""

import json
import os

import pytest
import yaml

import logging

import control._def
from control._def import TriggerMode
from control.NL5 import LINE_SPEED_MAX, LINE_SPEED_MIN, NL5, _ScanSpeedMixin
from tests.control.test_aion_hw_trigger import (  # noqa: F401  (fixtures)
    FakeConfocalUnit,
    _fluorescence_acquisition,
    aion_scope,
    nl5_trigger_timeout_s,
)


class ScanTiming(_ScanSpeedMixin):
    """The AION as installed: 73 mrad, full 3200 px width, 10 ms delay, 3000 mrad/s."""

    def __init__(self, amplitude=73.0, fov_x=3200, max_cam_pix_x=3200, delay_ms=10, line_speed=3000):
        self.scan_amplitude = amplitude
        self.offset_x = 0.5
        self.bypass_offset = 0.0
        self.fov_x = fov_x
        self.max_cam_pix_x = max_cam_pix_x
        self.exposure_delay_ms = delay_ms
        self.line_speed = line_speed
        self.line_speed_writes = []

    def set_line_speed(self, speed, save_setting=False):
        self.line_speed = self.clamp_line_speed(speed)
        self.line_speed_writes.append(self.line_speed)


FakeAion = FakeConfocalUnit  # the fake unit carries the same scan timing


# The sweep is 2 x 73 mrad = 146 mrad: 48.67 ms at 3000 mrad/s, so the exposure is 58.67 ms, not the 25 ms set.
SWEEP_MRAD = 146.0
SCAN_MS_AT_3000 = 1000 * SWEEP_MRAD / 3000
EXPOSURE_MS_AT_3000 = 10 + SCAN_MS_AT_3000


def test_exposure_is_delay_plus_scan():
    unit = ScanTiming()
    assert unit.scan_sweep_mrad() == SWEEP_MRAD
    assert unit.scan_time_ms() == pytest.approx(SCAN_MS_AT_3000)
    assert unit.exposure_time_ms() == pytest.approx(EXPOSURE_MS_AT_3000)
    assert unit.exposure_time_ms(6000) == pytest.approx(10 + SCAN_MS_AT_3000 / 2)


def test_the_scanned_width_relative_to_the_unit_camera_width_scales_the_sweep():
    unit = ScanTiming(fov_x=1600)
    assert unit.scan_sweep_mrad() == SWEEP_MRAD / 2
    assert unit.exposure_time_ms() == pytest.approx(10 + SCAN_MS_AT_3000 / 2)
    # Not capped: on AION the unit reports MAX_CAM_PIX_X 2048 for a 3200 px camera, and FIELD_OF_VIEW_X 3200
    # scans 1.5625 x further (measured: 76 ms, not 48.7 ms, at 73 mrad and 3000 mrad/s).
    assert ScanTiming(max_cam_pix_x=2048).scan_sweep_mrad() == pytest.approx(SWEEP_MRAD * 3200 / 2048)
    assert ScanTiming(max_cam_pix_x=2048).scan_time_ms() == pytest.approx(76.04, abs=0.01)
    assert ScanTiming(max_cam_pix_x=None).scan_sweep_mrad() == SWEEP_MRAD  # width unknown: full sweep


def test_line_speed_for_exposure_inverts_the_exposure():
    unit = ScanTiming()
    assert unit.line_speed_for_exposure(EXPOSURE_MS_AT_3000) == 3000
    assert unit.line_speed_for_exposure(30.0) == 7300  # 146 mrad / 20 ms
    assert unit.exposure_time_ms(unit.line_speed_for_exposure(30.0)) == pytest.approx(30.0)
    # Integer line speeds: the exposure reached is the nearest the unit can do.
    assert unit.exposure_time_ms(unit.line_speed_for_exposure(25.0)) == pytest.approx(25.0, abs=0.01)


def test_line_speed_for_exposure_stays_within_the_unit_limits():
    unit = ScanTiming()
    assert unit.line_speed_for_exposure(10.0) == LINE_SPEED_MAX  # no time for a scan at all
    assert unit.line_speed_for_exposure(5.0) == LINE_SPEED_MAX  # shorter than the delay
    assert unit.line_speed_for_exposure(10_000.0) == LINE_SPEED_MIN
    low, high = unit.exposure_limits_ms()
    assert low == pytest.approx(10 + 1000 * SWEEP_MRAD / LINE_SPEED_MAX)  # 17.3 ms
    assert high == pytest.approx(10 + 1000 * SWEEP_MRAD / LINE_SPEED_MIN)  # 1470 ms


def test_apply_exposure_writes_the_line_speed_only_when_it_changes():
    unit = ScanTiming()
    assert unit.apply_exposure_time(EXPOSURE_MS_AT_3000) == pytest.approx(EXPOSURE_MS_AT_3000)
    assert unit.line_speed_writes == [3000]  # the first apply always writes: the unit's start-up speed is unknown
    unit.apply_exposure_time(EXPOSURE_MS_AT_3000)
    assert unit.line_speed_writes == [3000]
    assert unit.apply_exposure_time(30.0) == pytest.approx(30.0)
    assert unit.line_speed_writes == [3000, 7300]
    assert unit.apply_exposure_time(5.0) == pytest.approx(10 + 1000 * SWEEP_MRAD / LINE_SPEED_MAX)
    assert unit.line_speed_writes == [3000, 7300, LINE_SPEED_MAX]


def test_scan_timing_metadata_describes_the_exposure():
    unit = ScanTiming()
    metadata = unit.scan_timing_metadata(7300)
    assert metadata == {
        "line_speed_mrad_s": 7300,
        "scan_time_ms": pytest.approx(20.0),
        "exposure_ms": pytest.approx(30.0),
    }


def test_settings_metadata_holds_every_unit_setting_and_the_device_readback():
    unit = ScanTiming()
    unit.slit_width_um = 66
    assert unit.settings_metadata() == {
        "device": "NL5",
        "exposure_delay_ms": 10,
        "scan_amplitude_mrad": 73.0,
        "offset_x_mrad": 0.5,
        "bypass_offset_mrad": 0.0,
        "fov_x_px": 3200,
        "max_cam_pix_x": 3200,
        "sweep_mrad": SWEEP_MRAD,
        "line_speed_mrad_s": 3000,
        "slit_width_um": 66,
    }

    class StubRcm:
        FIELD_OF_VIEW_X, FIELD_OF_VIEW_Y, MAX_CAM_PIX_X, EXPOSURE_DELAY, LINE_FREQUENCY = 1101, 1102, 1702, 1802, 1509
        AMPLITUDE_X, OFFSET_SCAN_X, BYPASS_OFFSET = 1500, 1502, 1801
        LASER_1_SELECTED = 1201
        integers = {1101: 3200, 1102: 3200, 1702: 2048, 1802: 10, 1509: 3000, 1201: 1}
        floats = {1500: 73.0, 1502: 0.5, 1801: 0.0}

        def get_integer_parameter(self, param):
            return self.integers.get(param)

        def get_float_parameter(self, param):
            return self.floats.get(param)

    real = object.__new__(NL5)  # no device: just the metadata method on a configured instance
    for key, value in vars(unit).items():
        setattr(real, key, value)
    real.rcm = StubRcm()
    real.device_type = 5
    real.device_name = "AION"
    real._log = logging.getLogger("test")
    settings = real.settings_metadata()
    assert settings["device"] == "AION" and settings["device_type"] == 5
    assert settings["device_readback"] == {
        "FIELD_OF_VIEW_X": 3200,
        "FIELD_OF_VIEW_Y": 3200,
        "MAX_CAM_PIX_X": 2048,
        "EXPOSURE_DELAY": 10,
        "LINE_FREQUENCY": 3000,
        "AMPLITUDE_X": 73.0,
        "OFFSET_SCAN_X": 0.5,
        "BYPASS_OFFSET": 0.0,
        "LASER_1_SELECTED": 1,
    }


def _channel(mpc, name):
    return mpc.liveController.get_channel_by_name(mpc.objectiveStore.current_objective, name)


def test_in_hardware_trigger_the_channel_exposure_sets_the_line_speed_and_the_camera(aion_scope):
    camera = aion_scope.camera
    unit = FakeAion(camera)
    mpc, _tracker = _fluorescence_acquisition(aion_scope, unit)
    live = mpc.liveController
    assert live.scan_unit_sets_exposure()
    name = mpc.selected_configurations[0].name

    channel = _channel(mpc, name)
    channel.exposure_time = EXPOSURE_MS_AT_3000
    live.set_microscope_mode(channel)
    assert unit.line_speed == 3000
    assert camera.get_exposure_time() == pytest.approx(EXPOSURE_MS_AT_3000)
    assert camera.get_total_frame_time() == pytest.approx(EXPOSURE_MS_AT_3000 + camera.get_strobe_time())

    channel.exposure_time = 30.0
    live.set_microscope_mode(channel)
    assert unit.line_speed == 7300
    assert camera.get_exposure_time() == pytest.approx(30.0)

    # Shorter than the exposure delay: the fastest scan the unit allows, and the camera knows the real exposure.
    channel.exposure_time = 5.0
    live.set_microscope_mode(channel)
    assert unit.line_speed == LINE_SPEED_MAX
    assert camera.get_exposure_time() == pytest.approx(unit.exposure_limits_ms()[0])
    assert live.effective_exposure_time_ms(5.0) == pytest.approx(unit.exposure_limits_ms()[0])
    assert live.exposure_limits_ms() == unit.exposure_limits_ms()
    assert live.current_configuration_as_applied().exposure_time == pytest.approx(unit.exposure_limits_ms()[0])
    assert channel.exposure_time == 5.0  # the channel's setting is kept

    # Outside hardware trigger the camera's own exposure applies again.
    live.set_trigger_mode(TriggerMode.SOFTWARE)
    assert not live.scan_unit_sets_exposure()
    assert camera.get_exposure_time() == pytest.approx(5.0)
    assert live.exposure_limits_ms() == camera.get_exposure_limits()
    assert live.current_configuration_as_applied() is live.currentConfiguration
    live.set_trigger_mode(TriggerMode.HARDWARE)
    assert camera.get_exposure_time() == pytest.approx(unit.exposure_limits_ms()[0])


def test_a_scan_timing_change_reapplies_the_current_exposure(aion_scope):
    camera = aion_scope.camera
    unit = FakeAion(camera)
    mpc, _tracker = _fluorescence_acquisition(aion_scope, unit)
    live = mpc.liveController
    channel = _channel(mpc, mpc.selected_configurations[0].name)
    channel.exposure_time = 30.0
    live.set_microscope_mode(channel)
    assert unit.line_speed == 7300

    unit.set_exposure_delay(20)  # as the NL5 tab does; the GUI then calls apply_exposure_time for the channel
    live.apply_exposure_time(channel.exposure_time)
    assert unit.line_speed == 14600  # 146 mrad in the 10 ms left for the scan
    assert camera.get_exposure_time() == pytest.approx(30.0)


def test_without_a_scan_unit_the_camera_gets_the_channel_exposure(aion_scope):
    camera = aion_scope.camera
    mpc, _tracker = _fluorescence_acquisition(aion_scope, FakeConfocalUnit(camera))
    aion_scope.addons.nl5 = None
    live = mpc.liveController
    assert not live.scan_unit_sets_exposure()
    channel = _channel(mpc, mpc.selected_configurations[0].name)
    channel.exposure_time = 5.0
    live.set_microscope_mode(channel)
    assert camera.get_exposure_time() == pytest.approx(5.0)
    assert live.effective_exposure_time_ms(5.0) == 5.0


def test_acquisition_metadata_records_the_exposure_the_unit_produced(aion_scope):
    unit = FakeAion(aion_scope.camera)
    mpc, tracker = _fluorescence_acquisition(aion_scope, unit)
    mpc.selected_configurations[0].exposure_time = 25.0
    mpc.selected_configurations[1].exposure_time = 5.0  # shorter than the delay: clamped to the unit's fastest scan
    # acquisition_channels.yaml is written here, after the channels are chosen
    mpc.start_new_experiment("scan exposure")
    experiment_dir = os.path.join(mpc.base_path, mpc.experiment_ID)

    mpc.run_acquisition()
    assert tracker.started_event.wait(10)
    assert tracker.finished_event.wait(60)

    fastest_ms = unit.exposure_limits_ms()[0]
    names = [c.name for c in mpc.selected_configurations]
    expected = {names[0]: pytest.approx(25.0, abs=0.01), names[1]: pytest.approx(fastest_ms)}

    with open(os.path.join(experiment_dir, "acquisition.yaml")) as f:
        acquisition = yaml.safe_load(f)
    for channel in acquisition["channels"]:
        assert channel["camera_settings"]["exposure_time_ms"] == expected[channel["name"]]
    scan = acquisition["confocal_scan"]
    assert scan["device"] == "AION" and scan["exposure_from_scan"] is True
    assert scan["exposure_delay_ms"] == 10 and scan["sweep_mrad"] == SWEEP_MRAD
    assert scan["scan_amplitude_mrad"] == 73.0 and scan["offset_x_mrad"] == 0.5 and scan["bypass_offset_mrad"] == 0.0
    assert scan["fov_x_px"] == 3200 and scan["max_cam_pix_x"] == 3200 and scan["line_speed_mrad_s"] == unit.line_speed
    assert scan["camera_readout_ms"] == aion_scope.camera.get_strobe_time()
    assert scan["channels"][names[0]]["exposure_setting_ms"] == 25.0
    for name in names:
        wavelength = scan["channels"][name]["wavelength_nm"]
        assert wavelength in control._def.NL5_WAVENLENGTH_MAP
        assert scan["channels"][name]["laser_line"] == control._def.NL5_WAVENLENGTH_MAP[wavelength]
    assert scan["channels"][names[0]]["line_speed_mrad_s"] == unit.line_speed_for_exposure(25.0)
    assert scan["channels"][names[0]]["exposure_ms"] == pytest.approx(25.0, abs=0.01)
    assert scan["channels"][names[1]]["line_speed_mrad_s"] == LINE_SPEED_MAX
    assert scan["channels"][names[1]]["exposure_ms"] == pytest.approx(fastest_ms)

    with open(os.path.join(experiment_dir, "acquisition_channels.yaml")) as f:
        channels = yaml.safe_load(f)["channels"]
    assert {c["name"]: c["camera_settings"]["exposure_time_ms"] for c in channels} == expected

    with open(os.path.join(experiment_dir, "acquisition parameters.json")) as f:
        parameters = json.load(f)
    assert parameters["confocal_scan"]["channels"][names[0]]["exposure_ms"] == pytest.approx(25.0, abs=0.01)

    # The channels themselves keep their settings.
    assert [c.exposure_time for c in mpc.selected_configurations] == [25.0, 5.0]


def test_acquisition_metadata_is_unchanged_without_a_scan_unit(aion_scope):
    mpc, tracker = _fluorescence_acquisition(aion_scope, FakeConfocalUnit(aion_scope.camera))
    aion_scope.addons.nl5 = None
    mpc.selected_configurations[0].exposure_time = 5.0
    mpc.start_new_experiment("no scan unit")
    experiment_dir = os.path.join(mpc.base_path, mpc.experiment_ID)
    mpc.run_acquisition()
    assert tracker.finished_event.wait(60)
    with open(os.path.join(experiment_dir, "acquisition.yaml")) as f:
        acquisition = yaml.safe_load(f)
    assert "confocal_scan" not in acquisition
    assert acquisition["channels"][0]["camera_settings"]["exposure_time_ms"] == 5.0
    with open(os.path.join(experiment_dir, "acquisition parameters.json")) as f:
        assert "confocal_scan" not in json.load(f)


class _StubNl5(ScanTiming):
    """What NL5Widget needs of the unit; set_* calls are recorded, nothing is saved."""

    def __init__(self):
        super().__init__()
        self.internal_stage = None
        self.device_name = "AION"
        self.speed_input_mode = "line_speed"
        self.calls = []

    def set_line_speed(self, speed, save_setting=False):
        super().set_line_speed(speed)
        self.calls.append(("set_line_speed", speed))

    def set_exposure_delay(self, exposure_delay_ms):
        self.exposure_delay_ms = exposure_delay_ms
        self.calls.append(("set_exposure_delay", exposure_delay_ms))

    def set_fov_x(self, fov_x):
        self.fov_x = fov_x
        self.calls.append(("set_fov_x", fov_x))

    def set_speed_input_mode(self, mode):
        self.speed_input_mode = mode

    def start_acquisition(self):
        pass

    def set_bypass(self, enabled):
        pass


def test_nl5_widget_line_speed_box_follows_the_exposure(qtbot):
    from control.NL5Widget import NL5Widget

    nl5 = _StubNl5()
    widget = NL5Widget(nl5)
    qtbot.addWidget(widget)
    nl5.calls.clear()

    widget.set_line_speed_follows_exposure(True)
    assert widget.line_speed_input.isReadOnly()
    nl5.line_speed = 7300  # as apply_exposure_time() set it
    widget.show_line_speed(30.0, 7300)
    assert widget.line_speed_input.value() == 7300
    widget.apply_speed_input()  # Enter in the read-only box sends nothing
    assert nl5.calls == []

    # The frame-rate display follows too: 7300 mrad/s over a 146 mrad sweep.
    widget.speed_mode_combo.setCurrentIndex(widget.speed_mode_combo.findData("fps"))
    assert widget.line_speed_input.value() == pytest.approx(50.0)
    assert widget.line_speed_input.isReadOnly()
    widget.speed_mode_combo.setCurrentIndex(widget.speed_mode_combo.findData("line_speed"))

    changes = []
    widget.signal_scan_timing_changed.connect(lambda: changes.append(1))
    widget.exposure_delay_input.setValue(20)
    assert ("set_exposure_delay", 20) in nl5.calls
    assert changes == [1]

    widget.set_line_speed_follows_exposure(False)
    assert not widget.line_speed_input.isReadOnly()
    widget.line_speed_input.setValue(5000)
    widget.apply_speed_input()
    assert ("set_line_speed", 5000) in nl5.calls
