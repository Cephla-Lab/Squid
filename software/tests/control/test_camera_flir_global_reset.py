"""Fake-nodemap tests for the FLIR driver's opt-in LEVEL trigger + global reset + trigger-ready output.

No camera and no PySpin: tests/control/flir_test_utils.py installs a fake PySpin module before the
driver is imported (Task 2). The hard rule under test is that with every opt-in at its default and
HARDWARE_TRIGGER_MODE = EDGE, the driver issues exactly the camera writes it issues today.
The opt-ins themselves (HARDWARE_TRIGGER_GLOBAL_RESET, CAMERA_TRIGGER_READY_OUTPUT and
use_level_trigger_global_reset) belong to the base branch (#654) and are tested in
test_camera_global_reset.py; this module tests only what PR A adds.
"""

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
