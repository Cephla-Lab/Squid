"""The filter wheel panel's Next / Previous buttons.

Review finding on #657: the controller wraps (last -> first is one slot across the index flag), but the buttons clamped
at the ends themselves and never sent a move there, so the wrap could not be reached from the GUI.
"""

import types
from unittest.mock import MagicMock

import pytest

import control.widgets
from squid.config import SquidFilterWheelConfig
from squid.filter_wheel_controller.cephla import SquidFilterWheel


def _controller(position, wraps, slots=8):
    c = MagicMock()
    c.available_filter_wheels = [1]
    c.get_filter_wheel_position.return_value = {1: position}
    c.get_filter_wheel_info.return_value = types.SimpleNamespace(
        index=1, number_of_slots=slots, slot_names=[str(i) for i in range(1, slots + 1)]
    )
    c.wraps_around.return_value = wraps
    return c


def _widget(qtbot, controller):
    w = control.widgets.FilterControllerWidget(controller, MagicMock())
    qtbot.addWidget(w)
    controller.set_filter_wheel_position.reset_mock()
    return w


@pytest.mark.parametrize(
    "position, button, wraps, expected",
    [
        (8, "next", True, 1),  # the finding: Next at the last slot sent nothing
        (1, "prev", True, 8),  # and Previous at the first
        (8, "next", False, None),  # a controller that does not wrap keeps its ends
        (1, "prev", False, None),
        (3, "next", True, 4),
        (3, "prev", False, 2),
    ],
)
def test_next_and_previous(qtbot, position, button, wraps, expected):
    controller = _controller(position, wraps)
    w = _widget(qtbot, controller)
    (w._next_buttons if button == "next" else w._prev_buttons)[1].click()
    if expected is None:
        controller.set_filter_wheel_position.assert_not_called()
    else:
        # exactly one move: updating the selection afterwards must not send it again
        controller.set_filter_wheel_position.assert_called_once_with({1: expected})
        assert w._combo_boxes[1].currentIndex() == expected - 1


def test_next_at_the_last_slot_is_one_slot_across_the_flag_on_a_real_controller(qtbot, tmp_path, monkeypatch):
    import squid.filter_wheel_controller.cephla as cephla

    monkeypatch.setattr(cephla, "_WHEEL_CACHE_PATH", str(tmp_path / "w.json"))
    cephla.cache_wheel_state({1: (8, 0)})
    mc = MagicMock()
    mc.firmware_version = (1, 6)
    cfg = SquidFilterWheelConfig(
        max_index=8, min_index=1, offset=0.008, motor_slot_index=3, transitions_per_revolution=4000
    )
    wheel = SquidFilterWheel(mc, cfg, skip_init=True)
    wheel.initialize([1])
    w = control.widgets.FilterControllerWidget(wheel, MagicMock())
    qtbot.addWidget(w)
    mc.move_w_to_usteps.reset_mock()
    w._next_buttons[1].click()
    turn = SquidFilterWheel._usteps_per_turn()
    mc.move_w_to_usteps.assert_called_once_with(SquidFilterWheel._target_pos_to_usteps(cfg, 1) + turn)
    assert wheel.get_filter_wheel_position() == {1: 1} and w._combo_boxes[1].currentIndex() == 0
