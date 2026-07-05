"""Tests for the two AcquisitionYAMLDropMixin generalizations needed to support a 3rd
widget_type (record_zstack) without breaking the existing wellplate/flexible behavior."""

from unittest.mock import MagicMock

from control.widgets import AcquisitionYAMLDropMixin


class _HostWithMultipointController(AcquisitionYAMLDropMixin):
    def __init__(self, camera):
        self.multipointController = MagicMock(camera=camera)

    def _get_expected_widget_type(self):
        return "wellplate"


def test_default_camera_hook_reads_multipoint_controller_camera():
    camera = object()
    host = _HostWithMultipointController(camera)
    assert host._get_camera_for_binning_check() is camera


def test_get_other_widget_name_maps_all_three_types():
    host = _HostWithMultipointController(camera=None)
    assert host._get_other_widget_name("wellplate") == "Wellplate Multipoint"
    assert host._get_other_widget_name("flexible") == "Flexible Multipoint"
    assert host._get_other_widget_name("record_zstack") == "Record + Z-Stack"
