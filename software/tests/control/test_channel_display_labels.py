"""Channel display labeling (Task 9): camera dot + suffix, name-preserving identity.

The invariant under test: channel identity is the bare channel name everywhere.
Labels/icons are display decoration only; the bare name travels in Qt.UserRole
and readers use `item.data(Qt.UserRole) or item.text()`.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from qtpy.QtWidgets import QComboBox

from control.models.camera_registry import CameraDefinition, CameraRegistryConfig
from control.widgets import (
    CAMERA_DOT_COLORS,
    LiveControlWidget,
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


def test_dot_icon_deterministic(qtbot):
    icon_a = camera_dot_icon(2)
    icon_b = camera_dot_icon(2)
    assert not icon_a.isNull() and not icon_b.isNull()
    assert len(CAMERA_DOT_COLORS) >= 2
    assert camera_dot_icon(1).cacheKey() != 0


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


# ---------------------------------------------------------------------------
# LiveControlWidget dropdown wiring: userData carries the bare name
# ---------------------------------------------------------------------------


class _DropdownStub:
    """LiveControlWidget-shaped stub exposing only what the dropdown helpers use."""

    _channel_registry = LiveControlWidget._channel_registry
    _multi_camera = LiveControlWidget._multi_camera
    _add_mode_item = LiveControlWidget._add_mode_item
    _select_dropdown_entry = LiveControlWidget._select_dropdown_entry

    def __init__(self, registry, available_camera_ids):
        self.dropdown_modeSelection = QComboBox()
        self.liveController = MagicMock()
        self.liveController.microscope.config_repo.get_camera_registry.return_value = registry
        self.liveController.microscope.cameras = {camera_id: object() for camera_id in available_camera_ids}


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

    def test_unavailable_camera_entry_is_disabled(self, qtbot):
        widget = _DropdownStub(TWO_CAM, available_camera_ids=[1])  # camera 2 failed to open
        widget._add_mode_item(_Ch("DAPI", camera=None))
        widget._add_mode_item(_Ch("BF Color", camera=2))
        model = widget.dropdown_modeSelection.model()
        assert model.item(0).isEnabled()
        assert not model.item(1).isEnabled()
