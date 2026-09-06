"""LiveController must not send a live trigger while the microcontroller is busy.

Sending SEND_HARDWARE_TRIGGER while a MOVETO_* command is in flight corrupts the
MCU's single command-completion slot: the firmware keeps executing commands but
reports IN_PROGRESS for everything, and every later
wait_till_operation_is_completed() times out until the trigger stream stops
(observed on an instrument on 2026-09-02 during focus-map point navigation with
live view running).

trigger_acquisition() returning False makes the trigger timer re-check within
10 ms, so refusing to trigger while the MCU is busy holds live during stage
motion and resumes automatically afterwards.
"""

from unittest.mock import patch

import control.microscope
import tests.control.test_stubs as ts


def _get_live_controller(scope):
    return ts.get_test_live_controller(scope, scope.objective_store.default_objective)


def test_no_trigger_sent_while_microcontroller_busy():
    scope = control.microscope.Microscope.build_from_global_config(True)
    controller = _get_live_controller(scope)
    micro = scope.low_level_drivers.microcontroller

    with patch.object(controller.camera, "get_ready_for_trigger", return_value=True), patch.object(
        controller.camera, "send_trigger"
    ) as send_trigger, patch.object(micro, "is_busy", return_value=True):
        assert controller.trigger_acquisition() is False
        send_trigger.assert_not_called()


def test_trigger_sent_when_microcontroller_idle():
    scope = control.microscope.Microscope.build_from_global_config(True)
    controller = _get_live_controller(scope)
    micro = scope.low_level_drivers.microcontroller

    with patch.object(controller.camera, "get_ready_for_trigger", return_value=True), patch.object(
        controller.camera, "send_trigger"
    ) as send_trigger, patch.object(micro, "is_busy", return_value=False):
        assert controller.trigger_acquisition() is True
        send_trigger.assert_called_once()
