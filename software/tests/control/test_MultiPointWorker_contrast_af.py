"""Contrast autofocus bookkeeping in MultiPointWorker: which channel it runs on, and how often."""

from unittest.mock import MagicMock

import control._def
from control.core.multi_point_worker import MultiPointWorker
from tests.control.test_MultiPointWorker_z_stack_order import _worker


def _af_worker(channel_lookup):
    w = _worker("CHANNEL FIRST", nz=1)
    w.do_reflection_af = False
    w.do_autofocus = True
    w.objectiveStore = MagicMock()
    w.objectiveStore.current_objective = "20x"
    w.liveController.get_channel_by_name.side_effect = channel_lookup
    w.liveController.get_channels.return_value = []
    w.autofocusController = MagicMock()
    w._select_config = MagicMock()
    return w


def test_contrast_af_channel_follows_a_live_change_of_the_setting(monkeypatch):
    # Preferences rebinds control._def.MULTIPOINT_AUTOFOCUS_CHANNEL; the worker must not use the
    # copy `from control._def import *` bound at import time.
    monkeypatch.setattr(control._def, "MULTIPOINT_AUTOFOCUS_CHANNEL", "Changed in Preferences")
    asked = []

    def lookup(objective, name):
        asked.append(name)
        return MagicMock()

    w = _af_worker(lookup)
    assert MultiPointWorker.perform_autofocus(w, "A1", 0) is True
    assert asked == ["Changed in Preferences"]
    w.autofocusController.autofocus.assert_called_once()


def test_missing_contrast_af_channel_fails_the_autofocus_instead_of_running_on_another_channel(monkeypatch):
    monkeypatch.setattr(control._def, "MULTIPOINT_AUTOFOCUS_CHANNEL", "No Such Channel")
    w = _af_worker(lambda objective, name: None)

    assert MultiPointWorker.perform_autofocus(w, "A1", 0) is False
    w._select_config.assert_not_called()
    w.autofocusController.autofocus.assert_not_called()
    assert "No Such Channel" in w._log.error.call_args[0][0]


def test_af_fov_count_advances_once_per_fov_whatever_the_stack_size():
    for order in ("CHANNEL FIRST", "Z FIRST"):
        w = _worker(order, nz=5)
        for fov in range(3):
            w.acquire_at_position("A1", "/tmp", fov)
        assert w.af_fov_count == 3
