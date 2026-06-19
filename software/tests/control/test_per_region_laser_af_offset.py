"""Unit tests for the per-region laser-AF offset feature.

Backend tests construct minimal MultiPointWorker-/FocusMapWidget-shaped stubs and
call the real methods in isolation, mirroring tests/control/test_MultiPointWorker_offsets.py.
"""

import math
from dataclasses import fields
from unittest.mock import MagicMock

from control.core.multi_point_utils import AcquisitionParameters
from control.core.multi_point_worker import MultiPointWorker


def test_acquisition_parameters_has_region_offsets_field():
    names = {f.name for f in fields(AcquisitionParameters)}
    assert "region_laser_af_offsets" in names


def test_region_offsets_default_factory_is_empty_dict():
    fld = next(f for f in fields(AcquisitionParameters) if f.name == "region_laser_af_offsets")
    assert fld.default_factory() == {}


class _AFStub:
    """MultiPointWorker-ish object with just what perform_autofocus's laser-AF branch reads."""

    def __init__(self, offsets, move_result=True):
        self.do_reflection_af = True
        self.region_laser_af_offsets = offsets
        self._log = MagicMock()
        self.laser_auto_focus_controller = MagicMock()
        self.laser_auto_focus_controller.move_to_target.return_value = move_result
        self._laser_af_successes = 0
        self._laser_af_failures = 0
        # Only touched on the exception path:
        self.base_path = "/tmp"
        self.experiment_ID = "exp"
        self.time_point = 0

    perform_autofocus = MultiPointWorker.perform_autofocus


def test_perform_autofocus_uses_region_offset():
    w = _AFStub({"A1": 5.0})
    assert w.perform_autofocus("A1", 0) is True
    w.laser_auto_focus_controller.move_to_target.assert_called_once_with(5.0)
    assert w._laser_af_successes == 1


def test_perform_autofocus_defaults_to_zero_for_unmapped_region():
    w = _AFStub({"A1": 5.0})
    assert w.perform_autofocus("B2", 0) is True
    w.laser_auto_focus_controller.move_to_target.assert_called_once_with(0.0)


def test_perform_autofocus_failure_increments_and_returns_false():
    w = _AFStub({"A1": 5.0}, move_result=False)
    assert w.perform_autofocus("A1", 0) is False
    assert w._laser_af_failures == 1
