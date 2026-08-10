"""Channel display labeling (Task 9): camera dot + suffix, name-preserving identity.

The invariant under test: channel identity is the bare channel name everywhere.
Labels/icons are display decoration only; the bare name travels in Qt.UserRole
and readers use `item.data(Qt.UserRole) or item.text()`.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from qtpy.QtWidgets import QComboBox

from control.channel_sequence import UNAVAILABLE_CAMERA_TOOLTIP
from control.models.camera_registry import CameraDefinition, CameraRegistryConfig
from control.widgets import (
    LiveControlWidget,
    NapariLiveWidget,
    _make_channel_decorator,
    camera_dot_icon,
    channel_display_label,
)


class _Ch:
    def __init__(self, name, camera=None):
        self.name = name
        self.camera = camera


TWO_CAM = CameraRegistryConfig(
    cameras=[
        CameraDefinition(name="Main Camera", id=1, serial_number="SN1", type="Toupcam"),
        CameraDefinition(name="Side Camera", id=2, serial_number="SN2", type="Toupcam", hardware_trigger=False),
    ]
)
ONE_CAM = CameraRegistryConfig(cameras=[CameraDefinition(serial_number="SN1")])


def test_label_plain_when_single_camera_or_no_registry():
    assert channel_display_label(_Ch("DAPI"), ONE_CAM) == "DAPI"
    assert channel_display_label(_Ch("DAPI"), None) == "DAPI"


def test_label_plain_for_primary_suffixed_for_secondary():
    assert channel_display_label(_Ch("Fluorescence 488 nm Ex", camera=None), TWO_CAM) == "Fluorescence 488 nm Ex"
    assert channel_display_label(_Ch("Fluorescence 488 nm Ex", camera=1), TWO_CAM) == "Fluorescence 488 nm Ex"
    assert channel_display_label(_Ch("BF Color", camera=2), TWO_CAM) == "BF Color — Side Camera"


def test_label_for_unknown_camera_id_marks_unavailable():
    assert channel_display_label(_Ch("Ghost", camera=9), TWO_CAM) == "Ghost — camera 9 (unavailable)"


def _rgba_list(icon):
    """All pixel RGBA tuples of an icon's 12x12 rendering, for content comparison."""
    image = icon.pixmap(12, 12).toImage()
    return [image.pixelColor(x, y).getRgb() for y in range(image.height()) for x in range(image.width())]


def test_dot_icon_deterministic(qtbot):
    assert _rgba_list(camera_dot_icon(False)) == _rgba_list(camera_dot_icon(False))
    assert _rgba_list(camera_dot_icon(True)) == _rgba_list(camera_dot_icon(True))
    assert _rgba_list(camera_dot_icon(True)) != _rgba_list(camera_dot_icon(False))


def test_mono_icon_is_neutral_grey(qtbot):
    opaque = [(r, g, b) for (r, g, b, a) in _rgba_list(camera_dot_icon(False)) if a > 200]
    assert opaque, "mono icon should have opaque pixels"
    assert all(max(px) - min(px) < 30 for px in opaque), "mono dot must stay neutral (no hue)"


def test_color_icon_shows_distinct_rgb_wedges(qtbot):
    opaque = [(r, g, b) for (r, g, b, a) in _rgba_list(camera_dot_icon(True)) if a > 200]
    assert any(r - g > 60 and r - b > 60 for (r, g, b) in opaque), "expected a red wedge"
    assert any(g - r > 40 and g - b > 40 for (r, g, b) in opaque), "expected a green wedge"
    assert any(b - r > 60 and b - g > 60 for (r, g, b) in opaque), "expected a blue wedge"


# ---------------------------------------------------------------------------
# _make_channel_decorator: decorate(channel_name) -> (label, icon, enabled)
# ---------------------------------------------------------------------------


def _fake_live_controller(registry, channels, available_camera_ids):
    microscope = SimpleNamespace(
        config_repo=SimpleNamespace(get_camera_registry=lambda: registry),
        objective_store=SimpleNamespace(current_objective="20x"),
        cameras={camera_id: object() for camera_id in available_camera_ids},
    )
    by_name = {ch.name: ch for ch in channels}
    return SimpleNamespace(
        microscope=microscope,
        get_channel_by_name=lambda objective, name: by_name.get(name),
    )


class TestMakeChannelDecorator:
    def test_single_camera_returns_bare_name_no_icon(self):
        controller = _fake_live_controller(ONE_CAM, [_Ch("DAPI")], available_camera_ids=[1])
        decorate = _make_channel_decorator(lambda: controller)
        assert decorate("DAPI") == ("DAPI", None, True)

    def test_no_registry_returns_bare_name_no_icon(self):
        controller = _fake_live_controller(None, [_Ch("DAPI")], available_camera_ids=[1])
        decorate = _make_channel_decorator(lambda: controller)
        assert decorate("DAPI") == ("DAPI", None, True)

    def test_unknown_channel_returns_bare_name(self):
        controller = _fake_live_controller(TWO_CAM, [], available_camera_ids=[1, 2])
        decorate = _make_channel_decorator(lambda: controller)
        assert decorate("Ghost") == ("Ghost", None, True)

    def test_secondary_channel_gets_suffix_icon_and_enabled(self, qtbot):
        controller = _fake_live_controller(TWO_CAM, [_Ch("BF Color", camera=2)], available_camera_ids=[1, 2])
        decorate = _make_channel_decorator(lambda: controller)
        label, icon, enabled = decorate("BF Color")
        assert label == "BF Color — Side Camera"
        assert icon is not None and not icon.isNull()
        assert enabled is True

    def test_primary_channel_keeps_bare_name_but_gets_icon(self, qtbot):
        controller = _fake_live_controller(TWO_CAM, [_Ch("DAPI", camera=None)], available_camera_ids=[1, 2])
        decorate = _make_channel_decorator(lambda: controller)
        label, icon, enabled = decorate("DAPI")
        assert label == "DAPI"
        assert icon is not None and not icon.isNull()
        assert enabled is True

    def test_missing_camera_marks_disabled(self, qtbot):
        # Camera 2 is declared in the registry but failed to open (not in microscope.cameras)
        controller = _fake_live_controller(TWO_CAM, [_Ch("BF Color", camera=2)], available_camera_ids=[1])
        decorate = _make_channel_decorator(lambda: controller)
        label, icon, enabled = decorate("BF Color")
        assert label == "BF Color — Side Camera"
        assert enabled is False

    def test_icon_encodes_sensor_type_from_live_camera(self, qtbot):
        controller = _fake_live_controller(
            TWO_CAM, [_Ch("DAPI", camera=1), _Ch("BF Color", camera=2)], available_camera_ids=[1, 2]
        )
        controller.microscope.cameras[1] = SimpleNamespace(is_color=False)
        controller.microscope.cameras[2] = SimpleNamespace(is_color=True)
        decorate = _make_channel_decorator(lambda: controller)
        _, mono_icon, _ = decorate("DAPI")
        _, color_icon, _ = decorate("BF Color")
        assert _rgba_list(mono_icon) == _rgba_list(camera_dot_icon(False))
        assert _rgba_list(color_icon) == _rgba_list(camera_dot_icon(True))

    def test_missing_camera_icon_falls_back_to_registry_default_pixel_format(self, qtbot):
        registry = CameraRegistryConfig(
            cameras=[
                CameraDefinition(name="Main Camera", id=1, serial_number="SN1", type="Toupcam"),
                CameraDefinition(
                    name="Side Camera",
                    id=2,
                    serial_number="SN2",
                    type="Toupcam",
                    hardware_trigger=False,
                    default_pixel_format="RGB24",
                ),
            ]
        )
        controller = _fake_live_controller(registry, [_Ch("BF Color", camera=2)], available_camera_ids=[1])
        decorate = _make_channel_decorator(lambda: controller)
        _, icon, enabled = decorate("BF Color")
        assert enabled is False
        assert _rgba_list(icon) == _rgba_list(camera_dot_icon(True))


# ---------------------------------------------------------------------------
# LiveControlWidget dropdown wiring: userData carries the bare name
# ---------------------------------------------------------------------------


class _DropdownStub:
    """LiveControlWidget-shaped stub borrowing the REAL dropdown helper methods."""

    _channel_registry = LiveControlWidget._channel_registry
    _multi_camera = LiveControlWidget._multi_camera
    _add_mode_item = LiveControlWidget._add_mode_item
    _select_dropdown_entry = LiveControlWidget._select_dropdown_entry

    def __init__(self, registry, available_camera_ids):
        self.dropdown_modeSelection = QComboBox()
        self.liveController = MagicMock()
        self.liveController.microscope.config_repo.get_camera_registry.return_value = registry
        self.liveController.microscope.cameras = {camera_id: object() for camera_id in available_camera_ids}


class _NapariDropdownStub(_DropdownStub):
    """Same stub shape, borrowing NapariLiveWidget's copies of the methods —
    the two widgets' implementations are meant to stay byte-identical, and
    binding each class's own methods catches a one-copy edit."""

    _channel_registry = NapariLiveWidget._channel_registry
    _multi_camera = NapariLiveWidget._multi_camera
    _add_mode_item = NapariLiveWidget._add_mode_item
    _select_dropdown_entry = NapariLiveWidget._select_dropdown_entry


class TestLiveControlDropdown:
    def test_single_camera_entries_are_bare_names_no_icons(self, qtbot):
        widget = _DropdownStub(ONE_CAM, available_camera_ids=[1])
        widget._add_mode_item(_Ch("DAPI"))
        widget._add_mode_item(_Ch("BF LED matrix full"))
        combo = widget.dropdown_modeSelection
        assert [combo.itemText(i) for i in range(combo.count())] == ["DAPI", "BF LED matrix full"]
        assert [combo.itemData(i) for i in range(combo.count())] == ["DAPI", "BF LED matrix full"]
        assert all(combo.itemIcon(i).isNull() for i in range(combo.count()))

    def test_multi_camera_entries_decorated_but_userdata_is_bare_name(self, qtbot):
        widget = _DropdownStub(TWO_CAM, available_camera_ids=[1, 2])
        widget._add_mode_item(_Ch("DAPI", camera=None))
        widget._add_mode_item(_Ch("BF Color", camera=2))
        combo = widget.dropdown_modeSelection
        assert combo.itemText(0) == "DAPI"
        assert combo.itemText(1) == "BF Color — Side Camera"
        assert combo.itemData(0) == "DAPI"
        assert combo.itemData(1) == "BF Color"
        assert not combo.itemIcon(0).isNull()
        assert not combo.itemIcon(1).isNull()

    def test_select_dropdown_entry_finds_by_bare_name(self, qtbot):
        widget = _DropdownStub(TWO_CAM, available_camera_ids=[1, 2])
        widget._add_mode_item(_Ch("DAPI", camera=None))
        widget._add_mode_item(_Ch("BF Color", camera=2))
        widget._select_dropdown_entry("BF Color")
        assert widget.dropdown_modeSelection.currentIndex() == 1
        widget._select_dropdown_entry("nonexistent")  # no-op, keeps selection
        assert widget.dropdown_modeSelection.currentIndex() == 1

    @pytest.mark.parametrize("stub_class", [_DropdownStub, _NapariDropdownStub])
    def test_unavailable_camera_entry_is_disabled_with_tooltip(self, qtbot, stub_class):
        widget = stub_class(TWO_CAM, available_camera_ids=[1])  # camera 2 failed to open
        widget._add_mode_item(_Ch("DAPI", camera=None))
        widget._add_mode_item(_Ch("BF Color", camera=2))
        model = widget.dropdown_modeSelection.model()
        assert model.item(0).isEnabled()
        assert model.item(0).toolTip() == ""
        assert not model.item(1).isEnabled()
        assert model.item(1).toolTip() == UNAVAILABLE_CAMERA_TOOLTIP


class _LiveActivatedStub(_DropdownStub):
    """Adds the REAL LiveControlWidget activated handler plus a recording
    select_new_microscope_mode_by_name, so the test exercises the production
    reader itself (not a copy of its expression)."""

    _on_mode_dropdown_activated = LiveControlWidget._on_mode_dropdown_activated

    def __init__(self, registry, available_camera_ids):
        super().__init__(registry, available_camera_ids)
        self.selected_names = []

    def select_new_microscope_mode_by_name(self, config_name):
        self.selected_names.append(config_name)


class _NapariActivatedStub(_NapariDropdownStub):
    """Runs the REAL NapariLiveWidget.select_new_microscope_mode_by_name; the
    recording seam is liveController.get_channel_by_name (returning None stops
    the handler before set_microscope_mode/update_ui_for_mode)."""

    select_new_microscope_mode_by_name = NapariLiveWidget.select_new_microscope_mode_by_name

    def __init__(self, registry, available_camera_ids):
        super().__init__(registry, available_camera_ids)
        self.objectiveStore = SimpleNamespace(current_objective="20x")
        self._log = MagicMock()
        self.looked_up_names = []

        def record_lookup(objective, name):
            self.looked_up_names.append(name)
            return None

        self.liveController.get_channel_by_name = record_lookup


class TestDropdownActivatedReader:
    """Exercises the two REAL production dropdown readers — the exact code where
    a decorated label could leak into get_channel_by_name:
    LiveControlWidget._on_mode_dropdown_activated (connected to `activated` in
    add_components) and NapariLiveWidget.select_new_microscope_mode_by_name.
    Reverting either to the old activated[str]/itemText idiom fails these."""

    def test_live_widget_activated_handler_passes_bare_name(self, qtbot):
        widget = _LiveActivatedStub(TWO_CAM, available_camera_ids=[1, 2])
        widget._add_mode_item(_Ch("DAPI", camera=None))
        widget._add_mode_item(_Ch("BF Color", camera=2))
        combo = widget.dropdown_modeSelection
        assert combo.itemText(1) == "BF Color — Side Camera"  # decorated on screen
        # Same connection add_components makes, then a user activation:
        combo.activated.connect(widget._on_mode_dropdown_activated)
        combo.activated.emit(1)
        assert widget.selected_names == ["BF Color"]  # bare name, not the label

    def test_live_widget_activated_handler_falls_back_to_item_text(self, qtbot):
        # Robustness half of the idiom: an entry populated without userData
        # (legacy population) must still resolve via its (bare) text.
        widget = _LiveActivatedStub(ONE_CAM, available_camera_ids=[1])
        widget.dropdown_modeSelection.addItem("DAPI")  # no userData
        widget._on_mode_dropdown_activated(0)
        assert widget.selected_names == ["DAPI"]

    def test_napari_widget_handler_passes_bare_name_to_lookup(self, qtbot):
        widget = _NapariActivatedStub(TWO_CAM, available_camera_ids=[1, 2])
        widget._add_mode_item(_Ch("DAPI", camera=None))
        widget._add_mode_item(_Ch("BF Color", camera=2))
        assert widget.dropdown_modeSelection.itemText(1) == "BF Color — Side Camera"
        widget.select_new_microscope_mode_by_name(1)  # activated passes the index
        assert widget.looked_up_names == ["BF Color"]  # bare name reaches channel lookup

    def test_napari_widget_handler_falls_back_to_item_text(self, qtbot):
        widget = _NapariActivatedStub(ONE_CAM, available_camera_ids=[1])
        widget.dropdown_modeSelection.addItem("DAPI")  # no userData
        widget.select_new_microscope_mode_by_name(0)
        assert widget.looked_up_names == ["DAPI"]
