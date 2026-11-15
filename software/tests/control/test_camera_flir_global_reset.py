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
