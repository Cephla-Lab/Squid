"""The Zmotion shim switches the light on before a controller-run z-stack (writing pBLV alone does not
switch it; only a CtrlBL run does)."""

import threading

from control.zmotion_spike import ZmotionMcuShim


class _FakeLink:
    def __init__(self):
        self.lock = threading.RLock()
        self.vars = {}
        self.ctrlbl_runs = 0

    def set_var(self, name, value):
        self.vars[name] = value

    def run_task(self, task, name):
        assert name == "CtrlBL"
        self.ctrlbl_runs += 1


def _shim(brightness=5):
    link = _FakeLink()
    shim = ZmotionMcuShim(link)
    if brightness is not None:
        shim.set_illumination(0, brightness)
    return shim, link


def test_light_off_then_stack_runs_ctrlbl_once():
    shim, link = _shim()
    shim.turn_off_illumination()  # acquisition start / stop_live: pBLV=0 applied
    assert link.vars["pBLV"] == 0
    runs = link.ctrlbl_runs

    assert shim.ensure_light_on() is True
    assert link.vars["pBLV"] == 5
    assert link.ctrlbl_runs == runs + 1

    assert shim.ensure_light_on() is False  # already on: no second stray pulse
    assert link.ctrlbl_runs == runs + 1


def test_pblv_written_without_ctrlbl_is_not_taken_as_on():
    shim, link = _shim()
    shim.turn_off_illumination()
    shim._send_blv_if_changed()  # what the stack start used to do: pBLV written, light still off
    shim._light_wanted = True
    shim._send_blv_if_changed()
    assert link.vars["pBLV"] == 5
    assert shim.ensure_light_on() is True


def test_hardware_trigger_leaves_the_light_applied():
    shim, link = _shim()
    shim.send_hardware_trigger(control_illumination=True)
    assert shim.ensure_light_on() is False


def test_unknown_brightness_still_runs_ctrlbl():
    shim, link = _shim(brightness=None)  # slider never seen: pBLV left as found, state unknown
    assert shim.ensure_light_on() is True
    assert "pBLV" not in link.vars
    assert link.ctrlbl_runs == 1
