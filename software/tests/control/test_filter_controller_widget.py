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


def _controller(position, lands_on, slots=8):
    """A controller whose next_position / previous_position leave the wheel on `lands_on` (None = it did not move)."""
    c = MagicMock()
    c.available_filter_wheels = [1]
    c.get_filter_wheel_position.return_value = {1: position}
    c.get_filter_wheel_info.return_value = types.SimpleNamespace(
        index=1, number_of_slots=slots, slot_names=[str(i) for i in range(1, slots + 1)]
    )

    def step(wheel_id=1):
        if lands_on is not None:
            c.get_filter_wheel_position.return_value = {1: lands_on}

    c.next_position.side_effect = step
    c.previous_position.side_effect = step
    return c


def _widget(qtbot, controller):
    w = control.widgets.FilterControllerWidget(controller, MagicMock())
    qtbot.addWidget(w)
    controller.set_filter_wheel_position.reset_mock()
    return w


@pytest.mark.parametrize(
    "position, button, lands_on",
    [
        (8, "next", 1),  # the finding: Next at the last slot sent nothing; now the controller decides
        (1, "prev", 8),
        (8, "next", None),  # a controller that keeps its ends: the selection stays where it was
        (1, "prev", None),
        (3, "next", 4),
        (3, "prev", 2),
    ],
)
def test_next_and_previous_ask_the_controller_and_read_the_result_back(qtbot, position, button, lands_on):
    controller = _controller(position, lands_on)
    w = _widget(qtbot, controller)
    (w._next_buttons if button == "next" else w._prev_buttons)[1].click()
    (controller.next_position if button == "next" else controller.previous_position).assert_called_once_with(1)
    # the panel has no arithmetic of its own: it never sends a position itself
    controller.set_filter_wheel_position.assert_not_called()
    assert w._combo_boxes[1].currentIndex() == (lands_on if lands_on is not None else position) - 1


def test_a_controller_without_a_rotary_wrap_keeps_its_ends_by_default():
    """The ABC's own next_position / previous_position, as Optospin and Zaber inherit them."""
    from squid.abc import AbstractFilterWheelController, FilterWheelInfo

    class Stub(AbstractFilterWheelController):
        def __init__(self):
            self.position = {1: 8}
            self.sent = []

        def initialize(self, *a, **k): ...
        def close(self): ...
        def home(self, index=None): ...
        def get_filter_wheel_info(self, index):
            return FilterWheelInfo(index=index, number_of_slots=8, slot_names=[str(i) for i in range(1, 9)])

        def get_filter_wheel_position(self):
            return dict(self.position)

        def set_filter_wheel_position(self, positions):
            self.sent.append(dict(positions))
            self.position.update(positions)

        @property
        def available_filter_wheels(self):
            return [1]

        def get_delay_ms(self, *a, **k):
            return 0

        def get_delay_offset_ms(self, *a, **k):
            return 0

        def set_delay_ms(self, *a, **k): ...
        def set_delay_offset_ms(self, *a, **k): ...

    s = Stub()
    s.next_position(1)
    assert s.sent == [] and s.position == {1: 8}  # the end stays an end
    s.previous_position(1)
    assert s.sent == [{1: 7}]
    s.position = {1: 1}
    s.previous_position(1)
    assert s.sent == [{1: 7}]
    with pytest.raises(ValueError):
        s.next_position(2)


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
