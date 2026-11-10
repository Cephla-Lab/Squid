"""Flexible Multipoint location list: Set XY / Set Z, Prev wrapping around, and the numbered
position labels and selected-position highlight on the navigation viewer."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

import control.widgets
import squid.abc
from control.widgets import FlexibleMultiPointWidget


class _Dropdown:
    def __init__(self, count, index):
        self._count = count
        self._index = index
        self.texts = {}

    def count(self):
        return self._count

    def currentIndex(self):
        return self._index

    def setCurrentIndex(self, index):
        self._index = index

    def setItemText(self, index, text):
        self.texts[index] = text


def _flexible_fake(n_locations=3, selected=0, stage_xyz=(50.0, 60.0, 2.5)):
    locations = np.array([[10.0 + i, 20.0 + i, 1.0 + i / 10] for i in range(n_locations)]).reshape(-1, 3)
    ids = np.array([f"R{i}" for i in range(n_locations)], dtype="<U20")
    scan = MagicMock()
    scan.region_centers = {str(name): [x, y, z] for name, (x, y, z) in zip(ids, locations)}
    scan.region_fov_coordinates = {str(name): [(x, y, z), (x + 1, y, z)] for name, (x, y, z) in zip(ids, locations)}
    stage = MagicMock()
    stage.get_pos.return_value = squid.abc.Pos(x_mm=stage_xyz[0], y_mm=stage_xyz[1], z_mm=stage_xyz[2], theta_rad=None)
    table = MagicMock()
    table.rowCount.return_value = n_locations
    fake = SimpleNamespace(
        location_list=locations,
        location_ids=ids,
        dropdown_location_list=_Dropdown(n_locations, selected),
        table_location_list=table,
        scanCoordinates=scan,
        navigationViewer=MagicMock(),
        stage=stage,
        use_overlap=True,
        entry_NX=SimpleNamespace(value=lambda: 2),
        entry_NY=SimpleNamespace(value=lambda: 3),
        entry_overlap=SimpleNamespace(value=lambda: 10.0),
        _log=MagicMock(),
    )
    for name in ("_selected_location_index", "_current_region_fovs", "refresh_position_labels"):
        setattr(fake, name, getattr(FlexibleMultiPointWidget, name).__get__(fake))
    fake._location_str = FlexibleMultiPointWidget._location_str
    return fake


@pytest.mark.parametrize("selected, expected", [(2, 1), (1, 0), (0, 2), (-1, 2)])
def test_previous_steps_back_and_wraps_to_the_last_location(selected, expected):
    fake = _flexible_fake(selected=selected)
    FlexibleMultiPointWidget.previous(fake)

    assert fake.dropdown_location_list.currentIndex() == expected
    x, y, z = fake.location_list[expected]
    fake.stage.move_x_to.assert_called_once_with(x)
    fake.stage.move_y_to.assert_called_once_with(y)
    fake.stage.move_z_to.assert_called_once_with(z)


def test_previous_with_an_empty_list_does_not_move_the_stage():
    fake = _flexible_fake(n_locations=0, selected=-1)
    FlexibleMultiPointWidget.previous(fake)
    fake.stage.move_x_to.assert_not_called()
    fake._log.error.assert_called_once()


def test_set_xy_retiles_the_selected_region_at_the_stage_xy_and_keeps_its_z():
    fake = _flexible_fake(selected=1)
    old_fovs = fake.scanCoordinates.region_fov_coordinates["R1"]
    old_z = fake.location_list[1, 2]

    FlexibleMultiPointWidget.update_xy(fake)

    assert list(fake.location_list[1]) == pytest.approx([50.0, 60.0, old_z])
    assert list(fake.location_list[0]) == pytest.approx([10.0, 20.0, 1.0])  # other locations untouched
    fake.navigationViewer.deregister_fovs_from_image.assert_called_once_with(old_fovs)
    fake.scanCoordinates.add_flexible_region.assert_called_once_with(
        "R1", 50.0, 60.0, old_z, 2, 3, overlap_percent=10.0
    )
    assert fake.dropdown_location_list.texts[1] == FlexibleMultiPointWidget._location_str(50.0, 60.0, old_z)
    fake.navigationViewer.set_position_labels.assert_called_once()
    labels = fake.navigationViewer.set_position_labels.call_args[0][0]
    assert [text for _, _, text in labels] == ["1", "2", "3"]
    assert labels[1][:2] == pytest.approx((50.0, 60.0))


def test_set_z_rewrites_the_z_of_the_selected_region_only():
    fake = _flexible_fake(selected=2)

    FlexibleMultiPointWidget.update_z(fake)

    assert fake.location_list[2, 2] == pytest.approx(2.5)
    assert fake.scanCoordinates.region_centers["R2"][2] == pytest.approx(2.5)
    assert [c[2] for c in fake.scanCoordinates.region_fov_coordinates["R2"]] == pytest.approx([2.5, 2.5])
    assert fake.scanCoordinates.region_centers["R0"][2] == pytest.approx(1.0)
    assert "z:2500.0" in fake.dropdown_location_list.texts[2]


@pytest.mark.parametrize("method", ["update_z", "update_xy"])
def test_set_xy_and_set_z_do_nothing_without_a_selection(method):
    fake = _flexible_fake(n_locations=0, selected=-1)
    getattr(FlexibleMultiPointWidget, method)(fake)
    fake._log.error.assert_called_once()
    fake.scanCoordinates.add_flexible_region.assert_not_called()


def test_set_z_tolerates_a_region_missing_from_the_shared_scan_coordinates():
    # The shared scan coordinates only hold this tab's regions while the tab is visible.
    fake = _flexible_fake(selected=0)
    fake.scanCoordinates.region_centers = {}
    fake.scanCoordinates.region_fov_coordinates = {}

    FlexibleMultiPointWidget.update_z(fake)

    assert fake.location_list[0, 2] == pytest.approx(2.5)


# ---- NavigationViewer ----


@pytest.fixture
def viewer(qapp):
    from control.core.core import NavigationViewer

    camera = MagicMock()
    camera.get_fov_size_mm.return_value = 0.9
    objective_store = MagicMock()
    objective_store.get_pixel_size_factor.return_value = 1.0
    return NavigationViewer(objective_store, camera, sample="glass slide")


def test_position_labels_are_numbered_and_the_current_one_is_highlighted(viewer):
    viewer.set_position_labels([(10.0, 10.0, "1"), (20.0, 15.0, "2"), (30.0, 20.0, "3")], current_index=1)

    assert [item.toPlainText() for item in viewer.position_label_items] == ["1", "2", "3"]
    assert [item.textItem.font().bold() for item in viewer.position_label_items] == [False, True, False]
    # Each label sits on the centre of its position's FOV box.
    top_left, bottom_right = viewer.get_FOV_pixel_coordinates(20.0, 15.0)
    pos = viewer.position_label_items[1].pos()
    assert (pos.x(), pos.y()) == pytest.approx(
        ((top_left[0] + bottom_right[0]) / 2, (top_left[1] + bottom_right[1]) / 2)
    )


def test_current_position_outlines_its_fovs_and_clears_again(viewer):
    viewer.set_position_labels([(10.0, 10.0, "1"), (20.0, 15.0, "2")])
    assert not viewer.position_highlight_overlay.any()

    viewer.set_current_position(0, [(10.0, 10.0, 1.0), (11.0, 10.0, 1.0)])
    assert viewer.position_highlight_overlay.any()
    assert viewer.position_label_items[0].textItem.font().bold()

    viewer.set_current_position(1)  # no FOV list: the highlight box goes away
    assert not viewer.position_highlight_overlay.any()
    assert viewer.position_label_items[1].textItem.font().bold()


def test_clearing_the_overlay_removes_labels_and_highlight(viewer):
    viewer.set_position_labels([(10.0, 10.0, "1")], current_index=0)
    viewer.set_current_position(0, [(10.0, 10.0)])

    viewer.clear_overlay()

    assert viewer.position_label_items == []
    assert not viewer.position_highlight_overlay.any()
