"""Fake-nodemap tests for the FLIR driver's opt-in LEVEL trigger + global reset + trigger-ready output.

No camera and no PySpin: tests/control/flir_test_utils.py installs a fake PySpin module before the
driver is imported (Task 2). The hard rule under test is that with every opt-in at its default and
HARDWARE_TRIGGER_MODE = EDGE, the driver issues exactly the camera writes it issues today.
The opt-ins themselves (HARDWARE_TRIGGER_GLOBAL_RESET, CAMERA_TRIGGER_READY_OUTPUT and
use_level_trigger_global_reset) belong to the base branch (#654) and are tested in
test_camera_global_reset.py; this module tests only what PR A adds.
"""

import pytest

import control._def


def test_flir_trigger_input_line_defaults_to_line2():
    assert control._def.FLIR_TRIGGER_INPUT_LINE == "Line2"


import tests.control.flir_test_utils as ft  # noqa: E402  the repo's style; installs the fake PySpin first
from control._def import HardwareTriggerMode, TriggerMode  # noqa: E402
from squid.abc import CameraAcquisitionMode, CameraError  # noqa: E402
import control.camera_flir as camera_flir  # noqa: E402


def _edge(monkeypatch):
    monkeypatch.setattr(control._def, "HARDWARE_TRIGGER_MODE", HardwareTriggerMode.EDGE)
    monkeypatch.setattr(control._def, "HARDWARE_TRIGGER_GLOBAL_RESET", False)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", False)
    monkeypatch.setattr(control._def, "FLIR_TRIGGER_INPUT_LINE", "Line2")


def test_edge_hardware_trigger_writes_exactly_todays_nodes(monkeypatch):
    _edge(monkeypatch)
    nodemap = ft.bfs_u3_63s4_nodemap()
    cam = ft.make_flir(nodemap)
    cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)
    assert nodemap.writes == [
        ("TriggerMode", "On"),
        ("TriggerSource", "Line2"),
        ("TriggerActivation", "RisingEdge"),
        ("ExposureAuto", "Off"),
        ("ExposureTime", ft.ROLLING_EXPOSURE_US_AT_20MS),
    ]
    assert cam.strobe_pushes == [62.0]
    assert cam.trigger_mode == TriggerMode.HARDWARE


def test_continuous_writes_exactly_todays_nodes(monkeypatch):
    _edge(monkeypatch)
    nodemap = ft.bfs_u3_63s4_nodemap()
    cam = ft.make_flir(nodemap, trigger_mode=TriggerMode.HARDWARE)
    cam._set_acquisition_mode_imp(CameraAcquisitionMode.CONTINUOUS)
    assert nodemap.writes == [("TriggerMode", "Off"), ("ExposureAuto", "Off"), ("ExposureTime", 20000.0)]
    assert cam.strobe_pushes == []


def test_software_trigger_writes_exactly_todays_nodes(monkeypatch):
    _edge(monkeypatch)
    nodemap = ft.bfs_u3_63s4_nodemap()
    cam = ft.make_flir(nodemap)
    cam._set_acquisition_mode_imp(CameraAcquisitionMode.SOFTWARE_TRIGGER)
    assert nodemap.writes == [
        ("TriggerMode", "On"),
        ("TriggerSource", "Software"),
        ("ExposureAuto", "Off"),
        ("ExposureTime", 20000.0),
    ]


def _level(monkeypatch, global_reset=False, ready=False, line="Line2"):
    monkeypatch.setattr(control._def, "HARDWARE_TRIGGER_MODE", HardwareTriggerMode.LEVEL)
    monkeypatch.setattr(control._def, "HARDWARE_TRIGGER_GLOBAL_RESET", global_reset)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", ready)
    monkeypatch.setattr(control._def, "FLIR_TRIGGER_INPUT_LINE", line)


LEVEL_ENTRY_WRITES = [
    ("TriggerMode", "On"),
    ("TriggerSource", "Line2"),
    ("TriggerSelector", "FrameStart"),
    ("TriggerActivation", "LevelHigh"),
    ("ExposureMode", "TriggerWidth"),
    ("TriggerOverlap", "Off"),
    ("AcquisitionFrameRateEnable", False),
]


def test_level_trigger_writes_the_trigger_width_node_set_and_no_exposure_time(monkeypatch):
    _level(monkeypatch)
    nodemap = ft.bfs_u3_63s4_nodemap()
    cam = ft.make_flir(nodemap)
    cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)
    assert nodemap.writes == LEVEL_ENTRY_WRITES + [("ExposureAuto", "Off")]
    assert cam.exposure_time == 20.0
    assert cam.strobe_pushes == [62.0]  # LEVEL without global reset keeps the rolling strobe model


def test_level_trigger_does_not_need_a_writable_exposure_time_node(monkeypatch):
    _level(monkeypatch)
    nodemap = ft.bfs_u3_63s4_nodemap()
    nodemap.node("ExposureTime").writable = False
    cam = ft.make_flir(nodemap)
    cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)  # no raise
    cam.set_exposure_time(5.0)
    assert cam.exposure_time == 5.0
    assert ("ExposureTime", 5000.0) not in nodemap.writes


def test_edge_still_requires_a_writable_exposure_time_node(monkeypatch):
    _edge(monkeypatch)
    nodemap = ft.bfs_u3_63s4_nodemap()
    nodemap.node("ExposureTime").writable = False
    with pytest.raises(CameraError, match="exposure"):
        ft.make_flir(nodemap).set_exposure_time(5.0)


LEVEL_LEAVE_WRITES = [
    ("TriggerMode", "Off"),
    ("TriggerSelector", "FrameStart"),
    ("TriggerActivation", "RisingEdge"),
    ("ExposureMode", "Timed"),
    ("TriggerOverlap", "Off"),
    ("AcquisitionFrameRateEnable", True),
    ("ExposureAuto", "Off"),
    ("ExposureTime", 20000.0),
]


def test_leaving_hardware_trigger_restores_only_what_level_changed(monkeypatch):
    _level(monkeypatch)
    nodemap = ft.bfs_u3_63s4_nodemap()
    cam = ft.make_flir(nodemap)
    cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)
    nodemap.writes.clear()
    cam._set_acquisition_mode_imp(CameraAcquisitionMode.CONTINUOUS)
    assert nodemap.writes == LEVEL_LEAVE_WRITES
    assert cam._restore_on_leave == {}


def test_leaving_after_edge_writes_nothing_extra(monkeypatch):
    _edge(monkeypatch)
    nodemap = ft.bfs_u3_63s4_nodemap()
    cam = ft.make_flir(nodemap)
    cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)
    nodemap.writes.clear()
    cam._set_acquisition_mode_imp(CameraAcquisitionMode.CONTINUOUS)
    assert nodemap.writes == [("TriggerMode", "Off"), ("ExposureAuto", "Off"), ("ExposureTime", 20000.0)]


def test_remembered_values_survive_a_second_entry(monkeypatch):
    _level(monkeypatch)
    nodemap = ft.bfs_u3_63s4_nodemap()
    cam = ft.make_flir(nodemap)
    for _ in range(2):
        cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)
        nodemap.writes.clear()
        cam._set_acquisition_mode_imp(CameraAcquisitionMode.CONTINUOUS)
    assert nodemap.writes == LEVEL_LEAVE_WRITES  # the originals, not LevelHigh / TriggerWidth


def _without_entry(nodemap, node_name, entry):
    node = nodemap.node(node_name)
    nodemap.add(ft.FakeEnumNode(node_name, [e for e in node.entries if e != entry], node.current))


@pytest.mark.parametrize(
    "previous, previous_writes",
    [
        (None, [("TriggerMode", "Off")]),
        (TriggerMode.CONTINUOUS, [("TriggerMode", "Off")]),
        (TriggerMode.SOFTWARE, [("TriggerMode", "On"), ("TriggerSource", "Software")]),
    ],
)
def test_failed_level_setup_puts_the_previous_mode_back(monkeypatch, previous, previous_writes):
    """No half switch: a camera without TriggerWidth is left in the mode it was in, not in LevelHigh with TriggerMode On."""
    _level(monkeypatch)
    nodemap = ft.bfs_u3_63s4_nodemap()
    _without_entry(nodemap, "ExposureMode", "TriggerWidth")
    cam = ft.make_flir(nodemap, trigger_mode=previous)
    with pytest.raises(CameraError, match="ExposureMode to TriggerWidth"):
        cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)
    assert nodemap.writes == [
        ("TriggerMode", "On"),
        ("TriggerSource", "Line2"),
        ("TriggerSelector", "FrameStart"),
        ("TriggerActivation", "LevelHigh"),
        # rollback: what was remembered, in order (ExposureMode was remembered before its write failed)
        ("TriggerSelector", "FrameStart"),
        ("TriggerActivation", "RisingEdge"),
        ("ExposureMode", "Timed"),
    ] + previous_writes + [("ExposureAuto", "Off"), ("ExposureTime", 20000.0)]
    assert cam.trigger_mode == previous
    assert cam._restore_on_leave == {}
    assert cam.strobe_pushes == []


def test_rollback_failure_is_logged_and_the_setup_error_raised(monkeypatch, caplog):
    _level(monkeypatch)
    nodemap = ft.bfs_u3_63s4_nodemap()
    nodemap.node("ExposureMode").writable = False  # the LEVEL write fails, and so does writing Timed back
    cam = ft.make_flir(nodemap, trigger_mode=TriggerMode.SOFTWARE)
    with pytest.raises(CameraError, match="ExposureMode to TriggerWidth"):
        cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)
    assert "Could not put the camera back in its previous trigger mode" in caplog.text
    assert "ExposureMode to TriggerWidth" in caplog.text  # the original error travels with the log line


def test_edge_failure_with_default_settings_is_todays_behavior(monkeypatch):
    """Defaults: today's code path and today's exception, no rollback writes."""
    _edge(monkeypatch)
    nodemap = ft.bfs_u3_63s4_nodemap()
    _without_entry(nodemap, "TriggerActivation", "RisingEdge")
    cam = ft.make_flir(nodemap, trigger_mode=TriggerMode.SOFTWARE)
    with pytest.raises(CameraError, match="RisingEdge"):
        cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)
    assert nodemap.writes == [("TriggerMode", "On"), ("TriggerSource", "Line2")]
    assert cam.trigger_mode == TriggerMode.SOFTWARE


def test_global_reset_on_writes_and_reads_back_sensor_shutter_mode(monkeypatch):
    _level(monkeypatch, global_reset=True)
    nodemap = ft.bfs_u3_63s4_nodemap()
    cam = ft.make_flir(nodemap)
    cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)
    assert nodemap.writes == LEVEL_ENTRY_WRITES + [("SensorShutterMode", "GlobalReset"), ("ExposureAuto", "Off")]
    assert cam.get_strobe_time() == camera_flir.GLOBAL_RESET_TRIGGER_LATENCY_US / 1000.0 == 0.0
    assert cam.strobe_pushes == [0.0]


@pytest.mark.parametrize(
    "trigger_mode, global_reset",
    [(HardwareTriggerMode.EDGE, False), (HardwareTriggerMode.LEVEL, False), (HardwareTriggerMode.EDGE, True)],
)
def test_global_reset_off_never_touches_sensor_shutter_mode(monkeypatch, trigger_mode, global_reset):
    monkeypatch.setattr(control._def, "HARDWARE_TRIGGER_MODE", trigger_mode)
    monkeypatch.setattr(control._def, "HARDWARE_TRIGGER_GLOBAL_RESET", global_reset)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", False)
    nodemap = ft.bfs_u3_63s4_nodemap()
    cam = ft.make_flir(nodemap)
    cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)
    assert "SensorShutterMode" not in [name for name, _ in nodemap.writes]
    assert cam.get_strobe_time() == 62.0


def test_global_reset_readback_mismatch_raises(monkeypatch):
    _level(monkeypatch, global_reset=True)
    nodemap = ft.bfs_u3_63s4_nodemap()
    nodemap.node("SensorShutterMode").readback = "Rolling"
    with pytest.raises(CameraError, match="SensorShutterMode=Rolling"):
        ft.make_flir(nodemap)._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)


def test_global_reset_missing_node_raises_when_on_and_is_untouched_when_off(monkeypatch):
    nodemap = ft.bfs_u3_63s4_nodemap()
    nodemap.remove("SensorShutterMode")
    _level(monkeypatch, global_reset=True)
    with pytest.raises(CameraError, match="HARDWARE_TRIGGER_GLOBAL_RESET"):
        ft.make_flir(nodemap)._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)
    _level(monkeypatch, global_reset=False)
    nodemap.writes.clear()
    ft.make_flir(nodemap)._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)
    assert nodemap.writes == LEVEL_ENTRY_WRITES + [("ExposureAuto", "Off")]


def test_global_reset_strobe_is_rolling_outside_hardware_trigger(monkeypatch):
    _level(monkeypatch, global_reset=True)
    cam = ft.make_flir(ft.bfs_u3_63s4_nodemap(), trigger_mode=TriggerMode.SOFTWARE)
    assert cam.get_strobe_time() == 62.0


def test_leaving_restores_sensor_shutter_mode_to_rolling(monkeypatch):
    _level(monkeypatch, global_reset=True)
    nodemap = ft.bfs_u3_63s4_nodemap()
    cam = ft.make_flir(nodemap)
    cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)
    nodemap.writes.clear()
    cam._set_acquisition_mode_imp(CameraAcquisitionMode.CONTINUOUS)
    assert nodemap.writes == LEVEL_LEAVE_WRITES[:6] + [("SensorShutterMode", "Rolling")] + LEVEL_LEAVE_WRITES[6:]


def test_setting_and_trigger_mode_alone_do_not_mean_global_reset(monkeypatch):
    """Only a confirmed switch puts the strobe on the global-reset model, not the setting plus trigger_mode HARDWARE."""
    _level(monkeypatch, global_reset=True)
    cam = ft.make_flir(ft.bfs_u3_63s4_nodemap(), trigger_mode=TriggerMode.HARDWARE)
    assert cam._global_reset_active() is False
    assert cam.get_strobe_time() == 62.0


def test_global_reset_is_only_active_until_the_next_mode_change(monkeypatch):
    _level(monkeypatch, global_reset=True)
    cam = ft.make_flir(ft.bfs_u3_63s4_nodemap())
    cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)
    assert cam._global_reset_active() is True
    cam._set_acquisition_mode_imp(CameraAcquisitionMode.SOFTWARE_TRIGGER)
    assert cam._global_reset_active() is False
    assert cam.get_strobe_time() == 62.0


def test_rejected_global_reset_puts_the_previous_mode_back(monkeypatch):
    _level(monkeypatch, global_reset=True)
    nodemap = ft.bfs_u3_63s4_nodemap()
    nodemap.node("SensorShutterMode").readback = "Rolling"  # accepts the write, reports rolling
    cam = ft.make_flir(nodemap, trigger_mode=TriggerMode.SOFTWARE)
    with pytest.raises(CameraError, match="SensorShutterMode=Rolling"):
        cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)
    assert nodemap.writes == LEVEL_ENTRY_WRITES + [("SensorShutterMode", "GlobalReset")] + LEVEL_LEAVE_WRITES[1:6] + [
        ("SensorShutterMode", "Rolling"),
        ("TriggerMode", "On"),
        ("TriggerSource", "Software"),
        ("ExposureAuto", "Off"),
        ("ExposureTime", 20000.0),
    ]
    assert cam.trigger_mode == TriggerMode.SOFTWARE
    assert cam._global_reset_active() is False
    assert cam.strobe_pushes == []


def test_failed_setup_from_hardware_trigger_refreshes_the_rolling_strobe(monkeypatch):
    """Re-arming hardware trigger (the confocal path) and failing: still hardware-triggered, but on the rolling model."""
    _level(monkeypatch, global_reset=True)
    nodemap = ft.bfs_u3_63s4_nodemap()
    nodemap.node("SensorShutterMode").readback = "Rolling"
    cam = ft.make_flir(nodemap, trigger_mode=TriggerMode.HARDWARE)
    with pytest.raises(CameraError, match="SensorShutterMode=Rolling"):
        cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)
    assert cam.trigger_mode == TriggerMode.HARDWARE
    assert cam._global_reset_active() is False
    assert nodemap.writes[-4:] == [
        ("TriggerMode", "On"),
        ("TriggerSource", "Line2"),
        ("ExposureAuto", "Off"),
        ("ExposureTime", ft.ROLLING_EXPOSURE_US_AT_20MS),  # Timed is back, so ExposureTime is written again
    ]
    assert cam.strobe_pushes == [62.0]


READY_WRITES = [
    ("LineSelector", "Line2"),
    ("V3_3Enable", False),
    ("LineMode", "Output"),
    ("LineSource", "FrameTriggerWait"),
    ("LineInverter", True),
]


def test_trigger_input_line_setting_selects_the_trigger_source(monkeypatch):
    _level(monkeypatch, line="Line3")
    nodemap = ft.bfs_u3_63s4_nodemap()
    ft.make_flir(nodemap)._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)
    assert ("TriggerSource", "Line3") in nodemap.writes


def test_unknown_trigger_input_line_raises(monkeypatch):
    _level(monkeypatch, line="Line9")
    nodemap = ft.bfs_u3_63s4_nodemap()
    cam = ft.make_flir(nodemap, trigger_mode=TriggerMode.CONTINUOUS)
    with pytest.raises(CameraError, match="Line9"):
        cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)
    assert nodemap.writes == [
        ("TriggerMode", "On"),
        ("TriggerMode", "Off"),
        ("ExposureAuto", "Off"),
        ("ExposureTime", 20000.0),
    ]
    assert cam.trigger_mode == TriggerMode.CONTINUOUS


@pytest.mark.parametrize("trigger_mode", [HardwareTriggerMode.EDGE, HardwareTriggerMode.LEVEL])
def test_ready_output_off_writes_no_line_nodes(monkeypatch, trigger_mode):
    monkeypatch.setattr(control._def, "HARDWARE_TRIGGER_MODE", trigger_mode)
    monkeypatch.setattr(control._def, "HARDWARE_TRIGGER_GLOBAL_RESET", False)
    monkeypatch.setattr(control._def, "CAMERA_TRIGGER_READY_OUTPUT", False)
    monkeypatch.setattr(control._def, "FLIR_TRIGGER_INPUT_LINE", "Line3")
    nodemap = ft.bfs_u3_63s4_nodemap()
    ft.make_flir(nodemap)._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)
    assert not [name for name, _ in nodemap.writes if name.startswith("Line") or name == "V3_3Enable"]


def test_ready_output_on_configures_line2_as_frame_trigger_wait_active_low(monkeypatch):
    _level(monkeypatch, global_reset=True, ready=True, line="Line3")
    nodemap = ft.bfs_u3_63s4_nodemap()
    ft.make_flir(nodemap)._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)
    expected = [w if w != ("TriggerSource", "Line2") else ("TriggerSource", "Line3") for w in LEVEL_ENTRY_WRITES]
    assert nodemap.writes == expected + [("SensorShutterMode", "GlobalReset")] + READY_WRITES + [
        ("ExposureAuto", "Off")
    ]


def test_rejected_ready_output_puts_the_previous_mode_back(monkeypatch):
    """The global reset written on the way in is undone too; the Line nodes are not (never remembered, design 6.2)."""
    _level(monkeypatch, global_reset=True, ready=True, line="Line3")
    nodemap = ft.bfs_u3_63s4_nodemap()
    nodemap.node("LineSource").readback = "ExposureActive"
    cam = ft.make_flir(nodemap, trigger_mode=TriggerMode.SOFTWARE)
    with pytest.raises(CameraError, match="LineSource=ExposureActive"):
        cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)
    after_failure = nodemap.writes[nodemap.writes.index(("LineInverter", True)) + 1 :]
    assert after_failure == LEVEL_LEAVE_WRITES[1:6] + [
        ("SensorShutterMode", "Rolling"),
        ("TriggerMode", "On"),
        ("TriggerSource", "Software"),
        ("ExposureAuto", "Off"),
        ("ExposureTime", 20000.0),
    ]
    assert cam.trigger_mode == TriggerMode.SOFTWARE
    assert cam._global_reset_active() is False


def test_ready_output_source_readback_mismatch_raises(monkeypatch):
    _level(monkeypatch, ready=True, line="Line3")
    nodemap = ft.bfs_u3_63s4_nodemap()
    nodemap.node("LineSource").readback = "ExposureActive"
    with pytest.raises(CameraError, match="LineSource=ExposureActive"):
        ft.make_flir(nodemap)._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)


def test_ready_output_inverter_readback_mismatch_raises(monkeypatch):
    _level(monkeypatch, ready=True, line="Line3")
    nodemap = ft.bfs_u3_63s4_nodemap()
    nodemap.node("LineInverter").SetValue = lambda v: nodemap.writes.append(
        ("LineInverter", v)
    )  # write recorded, value never changes
    with pytest.raises(CameraError, match="LineInverter"):
        ft.make_flir(nodemap)._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)


def test_ready_output_missing_node_raises(monkeypatch):
    _level(monkeypatch, ready=True, line="Line3")
    nodemap = ft.bfs_u3_63s4_nodemap()
    nodemap.remove("LineSource")
    with pytest.raises(CameraError, match="CAMERA_TRIGGER_READY_OUTPUT"):
        ft.make_flir(nodemap)._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)


def test_ready_output_not_configured_outside_hardware_trigger(monkeypatch):
    _level(monkeypatch, ready=True, line="Line3")
    nodemap = ft.bfs_u3_63s4_nodemap()
    ft.make_flir(nodemap)._set_acquisition_mode_imp(CameraAcquisitionMode.SOFTWARE_TRIGGER)
    assert not [name for name, _ in nodemap.writes if name.startswith("Line")]


def test_ready_output_stays_configured_on_leaving(monkeypatch):
    _level(monkeypatch, ready=True, line="Line3")
    nodemap = ft.bfs_u3_63s4_nodemap()
    cam = ft.make_flir(nodemap)
    cam._set_acquisition_mode_imp(CameraAcquisitionMode.HARDWARE_TRIGGER)
    nodemap.writes.clear()
    cam._set_acquisition_mode_imp(CameraAcquisitionMode.CONTINUOUS)
    assert not [name for name, _ in nodemap.writes if name.startswith("Line")]
