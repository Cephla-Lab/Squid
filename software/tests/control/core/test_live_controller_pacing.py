"""The live FPS is a trigger rate: the interval to the next trigger runs from the start of the last one, so a
trigger that blocks (a confocal scan on NL5/AION) does not halve the rate."""

import time
from unittest.mock import patch

import pytest

import control.microscope
import tests.control.test_stubs as ts


def _live_controller():
    scope = control.microscope.Microscope.build_from_global_config(True)
    controller = ts.get_test_live_controller(scope, scope.objective_store.default_objective)
    controller.is_live = True
    controller._set_trigger_fps(10)  # 100 ms interval
    controller._stop_existing_timer()
    return controller


def _run_timer_fn(controller, trigger):
    with patch.object(controller, "trigger_acquisition", side_effect=trigger), patch.object(
        controller, "_start_new_timer"
    ) as start_new_timer:
        controller._trigger_acquisition_timer_fn()
    return start_new_timer


def test_a_blocking_trigger_shortens_the_wait_to_the_next_one():
    controller = _live_controller()

    start_new_timer = _run_timer_fn(controller, lambda: time.sleep(0.06) or True)

    interval_ms = start_new_timer.call_args.kwargs["maybe_custom_interval_ms"]
    assert 20 <= interval_ms <= 41  # 100 ms - ~60 ms spent in the trigger


def test_a_trigger_longer_than_the_interval_triggers_again_at_once():
    controller = _live_controller()

    start_new_timer = _run_timer_fn(controller, lambda: time.sleep(0.12) or True)

    assert start_new_timer.call_args.kwargs["maybe_custom_interval_ms"] == 0


def test_a_zero_interval_starts_the_timer_at_once():
    controller = _live_controller()

    with patch("threading.Timer") as timer:
        controller._start_new_timer(maybe_custom_interval_ms=0)

    assert timer.call_args.args[0] == 0  # not the default interval: 0 is a real wait, not "unset"


@pytest.mark.parametrize("busy, expected", [(True, {}), (False, {"maybe_custom_interval_ms": 10})])
def test_no_trigger_keeps_the_existing_waits(busy, expected):
    controller = _live_controller()
    micro = controller.microscope.low_level_drivers.microcontroller

    with patch.object(micro, "is_busy", return_value=busy):
        start_new_timer = _run_timer_fn(controller, lambda: False)

    assert start_new_timer.call_args.kwargs == expected
