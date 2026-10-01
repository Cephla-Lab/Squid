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


def test_parse_well_name_basic():
    from control.widgets import _parse_well_name

    assert _parse_well_name("C4") == (2, 3)
    assert _parse_well_name("A1") == (0, 0)
    assert _parse_well_name("not-a-well") == (None, None)


def test_load_well_regions_selects_items_and_emits_signal():
    from control.widgets import _load_well_regions

    well_widget = MagicMock()
    well_widget.rowCount.return_value = 8
    well_widget.columnCount.return_value = 12
    item = MagicMock()
    well_widget.item.return_value = item

    _load_well_regions(well_widget, [{"name": "C4", "center_mm": [1, 2, 3], "shape": "Square"}])

    item.setSelected.assert_called_once_with(True)
    well_widget.signal_wellSelected.emit.assert_called_once_with(True)


def test_load_well_regions_noop_when_widget_is_none():
    from control.widgets import _load_well_regions

    _load_well_regions(None, [{"name": "C4"}])  # must not raise
