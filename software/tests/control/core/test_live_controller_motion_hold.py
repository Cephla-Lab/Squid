"""LiveController must not fire triggers while the microcontroller is busy.

Triggering while a MOVETO_* command is in flight corrupts the MCU's single
command-status slot: the firmware keeps executing commands but reports
IN_PROGRESS for everything, and every later wait times out until the trigger
stream stops (observed on-instrument 2026-09-02 during focus-map navigation
with live view running). See the guard in LiveController.trigger_acquisition.
"""

from unittest.mock import patch

import pytest

import control.microscope
import tests.control.test_stubs as ts


def _controller_and_micro():
    scope = control.microscope.Microscope.build_from_global_config(True)
    controller = ts.get_test_live_controller(scope, scope.objective_store.default_objective)
    return controller, scope.low_level_drivers.microcontroller


@pytest.mark.parametrize("busy,expected_triggers", [(True, 0), (False, 1)])
def test_trigger_is_held_while_the_microcontroller_is_busy(busy, expected_triggers):
    controller, micro = _controller_and_micro()

    with patch.object(controller.camera, "get_ready_for_trigger", return_value=True), patch.object(
        controller.camera, "send_trigger"
    ) as send_trigger, patch.object(micro, "is_busy", return_value=busy):
        assert controller.trigger_acquisition() is (not busy)
        assert send_trigger.call_count == expected_triggers


def test_busy_hold_rechecks_at_frame_cadence_not_every_10ms():
    # A held trigger re-checks at the normal frame interval: each re-check spins up
    # a fresh Timer thread, and a stage move lasts hundreds of ticks at 10 ms.
    controller, micro = _controller_and_micro()
    controller.is_live = True

    with patch.object(micro, "is_busy", return_value=True), patch.object(
        controller, "_start_new_timer"
    ) as start_new_timer:
        controller._trigger_acquisition_timer_fn()

    start_new_timer.assert_called_once_with(maybe_custom_interval_ms=controller.timer_trigger_interval)


def test_snap_refuses_when_microcontroller_stays_busy():
    # snap() fires the same trigger as live and must not reproduce the wedge when
    # clicked mid-move; it waits for the MCU and gives up rather than triggering.
    controller, micro = _controller_and_micro()

    with patch.object(micro, "wait_till_operation_is_completed", side_effect=TimeoutError("busy")), patch.object(
        controller.camera, "send_trigger"
    ) as send_trigger:
        assert controller.snap() is False
        send_trigger.assert_not_called()
