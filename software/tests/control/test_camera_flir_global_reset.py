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
