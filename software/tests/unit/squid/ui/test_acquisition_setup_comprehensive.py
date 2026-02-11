"""Comprehensive tests for AcquisitionSetupWidget cross-cutting interactions.

Documents 10 bugs/surprising behaviors discovered through deep code analysis.
Tests assert CURRENT behavior (even when buggy) so the team can prioritize fixes.

Bug reference table:
  #1  _manual_shapes_mm survives mode switch (stale shapes on reactivation)
  #2  _multipoint_positions survives mode switch (orphaned positions)
  #3  _next_region_id never resets (IDs jump after clear+re-add)
  #4  XY OFF→ON doesn't call _update_xy_coordinates (coords stay cleared)
  #5  Objective/binning changes ignored when tab inactive (_is_active_tab guard)
  #6  CSV-loaded coordinates never recalculated on objective change
  #7  apply_imaging_protocol doesn't restore focus method when focus.mode=none
  #8  Z-range state (z_range_enable, z_min, z_max) not saved/loaded in protocol
  #9  ROI status label text persists after mode switch (hidden but stale)
  #10 Well scan param changes while not in multiwell mode silently dropped
"""

import pytest
from unittest.mock import MagicMock

from squid.core.events import (
    AutofocusMode,
    ActiveAcquisitionTabChanged,
    AddFlexibleRegionCommand,
    BinningChanged,
    ChannelConfigurationsChanged,
    ClearManualShapesCommand,
    ClearScanCoordinatesCommand,
    EventBus,
    LoadScanCoordinatesCommand,
    ManualShapeDrawingEnabledChanged,
    ManualShapesChanged,
    MosaicLayersCleared,
    MosaicLayersInitialized,
    ObjectiveChanged,
    ScanCoordinatesUpdated,
    SetManualScanCoordinatesCommand,
    SetWellSelectionScanCoordinatesCommand,
    StagePositionChanged,
)
from squid.backend.managers.objective_store import ObjectiveStore
from squid.backend.managers.scan_coordinates.scan_coordinates import ScanCoordinates
from squid.core.protocol.imaging_protocol import FocusConfig, ImagingProtocol, ZStackConfig
from squid.ui.widgets.acquisition.acquisition_setup import (
    AcquisitionSetupWidget,
    _MODE_LOAD_CSV,
    _MODE_MULTIPOINT,
    _MODE_MULTIWELL,
    _MODE_ROI_TILING,
)


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def event_bus():
    bus = EventBus()
    bus.start()
    yield bus
    bus.stop()


@pytest.fixture
def mock_camera():
    camera = MagicMock()
    camera.get_fov_size_mm.return_value = 1.0
    camera.get_fov_height_mm.return_value = 1.0
    return camera


@pytest.fixture
def mock_stage():
    stage = MagicMock()
    pos = MagicMock()
    pos.x_mm = 25.0
    pos.y_mm = 25.0
    pos.z_mm = 1.0
    stage.get_pos.return_value = pos
    return stage


@pytest.fixture
def mock_objective_store():
    store = MagicMock(spec=ObjectiveStore)
    store.get_pixel_size_factor.return_value = 1.0
    return store


@pytest.fixture
def scan_coords(event_bus, mock_objective_store, mock_stage, mock_camera):
    sc = ScanCoordinates(
        objectiveStore=mock_objective_store,
        stage=mock_stage,
        camera=mock_camera,
        event_bus=event_bus,
    )
    yield sc
    sc.shutdown()


@pytest.fixture
def widget(event_bus, qtbot):
    w = AcquisitionSetupWidget(
        event_bus=event_bus,
        initial_channel_configs=["BF LED matrix full", "Fluorescence 488 nm Ex"],
    )
    qtbot.addWidget(w)
    return w


@pytest.fixture
def widget_with_fov(event_bus, qtbot):
    """Widget with camera_fov_size_mm set for well scan coverage sync tests."""
    w = AcquisitionSetupWidget(
        event_bus=event_bus,
        initial_channel_configs=["BF LED matrix full", "Fluorescence 488 nm Ex"],
        camera_fov_size_mm=1.0,
    )
    qtbot.addWidget(w)
    return w


# Collected event fixtures
@pytest.fixture
def collected_clear(event_bus):
    events = []
    event_bus.subscribe(ClearScanCoordinatesCommand, lambda e: events.append(e))
    return events


@pytest.fixture
def collected_well_cmd(event_bus):
    events = []
    event_bus.subscribe(SetWellSelectionScanCoordinatesCommand, lambda e: events.append(e))
    return events


@pytest.fixture
def collected_drawing_changed(event_bus):
    events = []
    event_bus.subscribe(ManualShapeDrawingEnabledChanged, lambda e: events.append(e))
    return events


@pytest.fixture
def collected_region_cmd(event_bus):
    events = []
    event_bus.subscribe(AddFlexibleRegionCommand, lambda e: events.append(e))
    return events


@pytest.fixture
def collected_scan_updated(event_bus):
    events = []
    event_bus.subscribe(ScanCoordinatesUpdated, lambda e: events.append(e))
    return events


@pytest.fixture
def collected_manual_cmd(event_bus):
    events = []
    event_bus.subscribe(SetManualScanCoordinatesCommand, lambda e: events.append(e))
    return events


# ===========================================================================
# Helpers
# ===========================================================================


def _make_shapes(*rects):
    """Build shape tuples from (x0, y0, x1, y1) bounding boxes."""
    return tuple(
        ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
        for x0, y0, x1, y1 in rects
    )


def _inject_shapes(widget, event_bus, shapes):
    """Publish ManualShapesChanged with the given shapes and drain."""
    event_bus.publish(ManualShapesChanged(shapes_mm=shapes))
    event_bus.drain()


def _set_stage_position(event_bus, x, y, z):
    """Publish a StagePositionChanged event and drain."""
    event_bus.publish(StagePositionChanged(x_mm=x, y_mm=y, z_mm=z))
    event_bus.drain()


def _add_mp_position(widget, event_bus, x, y, z):
    """Set cached stage position and call _on_mp_add."""
    widget._cached_x_mm = float(x)
    widget._cached_y_mm = float(y)
    widget._cached_z_mm = float(z)
    widget._on_mp_add()
    event_bus.drain()


def _activate_tab(event_bus):
    """Publish ActiveAcquisitionTabChanged(active_tab="setup") and drain."""
    event_bus.publish(ActiveAcquisitionTabChanged(active_tab="setup"))
    event_bus.drain()


def _deactivate_tab(event_bus):
    """Publish ActiveAcquisitionTabChanged(active_tab="other") and drain."""
    event_bus.publish(ActiveAcquisitionTabChanged(active_tab="other"))
    event_bus.drain()


def _init_mosaic(event_bus):
    """Publish MosaicLayersInitialized and drain."""
    event_bus.publish(MosaicLayersInitialized())
    event_bus.drain()


def _select_channel(widget, name):
    """Check a channel by name in the channel order widget."""
    widget._channel_order_widget.set_selected_channels([name])


def _select_channels(widget, names):
    """Check multiple channels by name in order."""
    widget._channel_order_widget.set_selected_channels(names)


# ===========================================================================
# A. TestModeSwitchingCrossContamination (16 tests)
# ===========================================================================


class TestModeSwitchingCrossContamination:
    """Cross-contamination and state leaks when switching XY modes."""

    def test_roi_to_multiwell_clears_backend(self, widget, event_bus, scan_coords, collected_clear):
        """Switching from ROI to Multiwell publishes ClearScanCoordinatesCommand."""
        _init_mosaic(event_bus)
        widget._xy_mode_combo.setCurrentIndex(_MODE_ROI_TILING)
        event_bus.drain()
        collected_clear.clear()

        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIWELL)
        event_bus.drain()

        assert len(collected_clear) > 0, "Mode switch should clear backend"

    def test_roi_to_multiwell_shapes_survive(self, widget, event_bus):
        """Bug #1: _manual_shapes_mm survives mode switch — stale shapes persist."""
        _init_mosaic(event_bus)
        widget._xy_mode_combo.setCurrentIndex(_MODE_ROI_TILING)
        shapes = _make_shapes((10, 10, 13, 13))
        _inject_shapes(widget, event_bus, shapes)
        assert widget._manual_shapes_mm is not None

        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIWELL)
        event_bus.drain()

        # Bug #1: shapes are NOT cleared on mode switch
        assert widget._manual_shapes_mm is not None, (
            "Bug #1: _manual_shapes_mm survives mode switch"
        )

    def test_multipoint_to_roi_clears_backend(self, widget, event_bus, collected_clear):
        """Switching from Multipoint to ROI clears backend."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIPOINT)
        _add_mp_position(widget, event_bus, 25, 25, 1)
        collected_clear.clear()

        _init_mosaic(event_bus)
        widget._xy_mode_combo.setCurrentIndex(_MODE_ROI_TILING)
        event_bus.drain()

        assert len(collected_clear) > 0

    def test_multipoint_to_roi_positions_survive(self, widget, event_bus):
        """Bug #2: _multipoint_positions survives mode switch."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIPOINT)
        _add_mp_position(widget, event_bus, 25, 25, 1)
        assert len(widget._multipoint_positions) == 1

        _init_mosaic(event_bus)
        widget._xy_mode_combo.setCurrentIndex(_MODE_ROI_TILING)
        event_bus.drain()

        # Bug #2: positions NOT cleared on mode switch
        assert len(widget._multipoint_positions) == 1, (
            "Bug #2: _multipoint_positions survives mode switch"
        )

    def test_full_mode_cycle_publishes_clear_each_time(self, widget, event_bus, collected_clear):
        """Cycling through all modes publishes ClearScanCoordinatesCommand each time."""
        _init_mosaic(event_bus)
        collected_clear.clear()

        modes = [_MODE_ROI_TILING, _MODE_MULTIPOINT, _MODE_LOAD_CSV, _MODE_MULTIWELL]
        for mode in modes:
            widget._xy_mode_combo.setCurrentIndex(mode)
            event_bus.drain()

        assert len(collected_clear) >= len(modes), (
            f"Expected >= {len(modes)} clears, got {len(collected_clear)}"
        )

    def test_roi_exit_disables_drawing(self, widget, event_bus, collected_drawing_changed):
        """Leaving ROI mode disables shape drawing."""
        _init_mosaic(event_bus)
        widget._xy_mode_combo.setCurrentIndex(_MODE_ROI_TILING)
        event_bus.drain()
        collected_drawing_changed.clear()

        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIWELL)
        event_bus.drain()

        disabled = [e for e in collected_drawing_changed if not e.enabled]
        assert len(disabled) > 0, "Should disable drawing when leaving ROI mode"

    def test_roi_entry_enables_drawing_when_xy_checked(self, widget, event_bus, collected_drawing_changed):
        """Entering ROI mode enables drawing when XY is checked."""
        _init_mosaic(event_bus)
        assert widget._xy_checkbox.isChecked()
        collected_drawing_changed.clear()

        widget._xy_mode_combo.setCurrentIndex(_MODE_ROI_TILING)
        event_bus.drain()

        enabled = [e for e in collected_drawing_changed if e.enabled]
        assert len(enabled) > 0, "Should enable drawing when entering ROI mode with XY checked"

    def test_roi_entry_does_not_enable_drawing_when_xy_unchecked(self, widget, event_bus, collected_drawing_changed):
        """Entering ROI mode does NOT enable drawing when XY is unchecked."""
        _init_mosaic(event_bus)
        widget._xy_checkbox.setChecked(False)
        event_bus.drain()
        collected_drawing_changed.clear()

        widget._xy_mode_combo.setCurrentIndex(_MODE_ROI_TILING)
        event_bus.drain()

        # _on_xy_mode_changed checks _xy_checkbox.isChecked() before enabling drawing
        enabled = [e for e in collected_drawing_changed if e.enabled]
        assert len(enabled) == 0, "Should NOT enable drawing when XY is unchecked"

    def test_next_region_id_increments_across_clears(self, widget, event_bus):
        """Bug #3: _next_region_id never resets — IDs jump after clear+re-add."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIPOINT)
        _add_mp_position(widget, event_bus, 10, 10, 1)
        _add_mp_position(widget, event_bus, 20, 20, 1)
        first_ids = [rid for rid, _, _, _ in widget._multipoint_positions]

        widget._on_mp_clear()
        event_bus.drain()

        _add_mp_position(widget, event_bus, 30, 30, 1)
        new_id = widget._multipoint_positions[0][0]

        # Bug #3: _next_region_id is now 3, not reset to 1
        assert int(new_id) > int(first_ids[-1]), (
            f"Bug #3: _next_region_id never resets. After clear, new ID={new_id}, "
            f"previous IDs={first_ids}"
        )

    def test_mosaic_cleared_forces_switch_from_roi(self, widget, event_bus):
        """MosaicLayersCleared forces switch from ROI to Multiwell."""
        _init_mosaic(event_bus)
        widget._xy_mode_combo.setCurrentIndex(_MODE_ROI_TILING)
        event_bus.drain()

        event_bus.publish(MosaicLayersCleared())
        event_bus.drain()

        assert widget._xy_mode_combo.currentIndex() == _MODE_MULTIWELL

    def test_multipoint_positions_restored_on_switch_back(self, widget, event_bus, scan_coords):
        """Bug #2 corollary: switching back to Multipoint restores orphaned positions."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIPOINT)
        _add_mp_position(widget, event_bus, 25, 25, 1)
        assert len(widget._multipoint_positions) == 1

        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIWELL)
        event_bus.drain()

        # Switch back — positions still there but NOT auto-republished
        # (mode switch fires ClearScanCoordinatesCommand, but _on_xy_mode_changed
        # doesn't call _publish_multipoint_regions)
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIPOINT)
        event_bus.drain()

        assert len(widget._multipoint_positions) == 1, (
            "Orphaned positions survive round-trip mode switch"
        )

    def test_clear_command_does_not_clear_manual_shapes(self, widget, event_bus):
        """ClearScanCoordinatesCommand does NOT clear _manual_shapes_mm."""
        _init_mosaic(event_bus)
        widget._xy_mode_combo.setCurrentIndex(_MODE_ROI_TILING)
        shapes = _make_shapes((10, 10, 13, 13))
        _inject_shapes(widget, event_bus, shapes)
        assert widget._manual_shapes_mm is not None

        event_bus.publish(ClearScanCoordinatesCommand())
        event_bus.drain()

        assert widget._manual_shapes_mm is not None, (
            "ClearScanCoordinatesCommand should NOT clear _manual_shapes_mm"
        )

    def test_mode_switch_does_not_publish_clear_manual_shapes(self, widget, event_bus):
        """Mode switch does NOT publish ClearManualShapesCommand."""
        collected = []
        event_bus.subscribe(ClearManualShapesCommand, lambda e: collected.append(e))
        _init_mosaic(event_bus)
        widget._xy_mode_combo.setCurrentIndex(_MODE_ROI_TILING)
        event_bus.drain()
        collected.clear()

        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIWELL)
        event_bus.drain()

        assert len(collected) == 0, (
            "Mode switch does NOT publish ClearManualShapesCommand — "
            "only _on_clear_rois does"
        )

    def test_clear_rois_button_clears_shapes(self, widget, event_bus):
        """_on_clear_rois does clear _manual_shapes_mm (contrast with mode switch)."""
        _init_mosaic(event_bus)
        widget._xy_mode_combo.setCurrentIndex(_MODE_ROI_TILING)
        shapes = _make_shapes((10, 10, 13, 13))
        _inject_shapes(widget, event_bus, shapes)

        widget._on_clear_rois()
        event_bus.drain()

        assert widget._manual_shapes_mm is None

    def test_mode_switch_hides_old_panel_shows_new(self, widget, event_bus):
        """Mode switch shows only the new mode's panel."""
        _init_mosaic(event_bus)
        for target_mode in [_MODE_ROI_TILING, _MODE_MULTIPOINT, _MODE_LOAD_CSV, _MODE_MULTIWELL]:
            widget._xy_mode_combo.setCurrentIndex(target_mode)
            for i, panel in enumerate(widget._xy_panels):
                if i == target_mode:
                    assert not panel.isHidden(), f"Panel {i} should be visible for mode {target_mode}"
                else:
                    assert panel.isHidden(), f"Panel {i} should be hidden for mode {target_mode}"


# ===========================================================================
# B. TestXYToggleBehavior (9 tests)
# ===========================================================================


class TestXYToggleBehavior:
    """XY checkbox on/off interactions."""

    def test_xy_off_publishes_clear(self, widget, event_bus, collected_clear):
        """Unchecking XY publishes ClearScanCoordinatesCommand."""
        collected_clear.clear()
        widget._xy_checkbox.setChecked(False)
        event_bus.drain()
        assert len(collected_clear) > 0

    def test_xy_off_hides_all_panels(self, widget):
        """Unchecking XY hides all XY panels."""
        widget._xy_checkbox.setChecked(False)
        for panel in widget._xy_panels:
            assert panel.isHidden()

    def test_xy_off_disables_combo(self, widget):
        """Unchecking XY disables the mode combo."""
        widget._xy_checkbox.setChecked(False)
        assert not widget._xy_mode_combo.isEnabled()

    def test_xy_on_shows_correct_panel(self, widget):
        """Checking XY shows the panel for the current mode."""
        widget._xy_checkbox.setChecked(False)
        widget._xy_checkbox.setChecked(True)
        idx = widget._xy_mode_combo.currentIndex()
        assert not widget._xy_panels[idx].isHidden()

    def test_xy_on_enables_combo(self, widget):
        """Checking XY enables the mode combo."""
        widget._xy_checkbox.setChecked(False)
        widget._xy_checkbox.setChecked(True)
        assert widget._xy_mode_combo.isEnabled()

    def test_xy_off_on_coordinates_not_restored(self, widget, event_bus, scan_coords, collected_clear):
        """Bug #4: XY OFF→ON doesn't call _update_xy_coordinates — coords stay cleared."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIPOINT)
        _add_mp_position(widget, event_bus, 25, 25, 1)
        total_before = sum(len(c) for c in scan_coords.region_fov_coordinates.values())
        assert total_before > 0

        widget._xy_checkbox.setChecked(False)
        event_bus.drain()
        assert sum(len(c) for c in scan_coords.region_fov_coordinates.values()) == 0

        collected_clear.clear()
        widget._xy_checkbox.setChecked(True)
        event_bus.drain()

        # Bug #4: coordinates are NOT restored — _on_xy_toggled doesn't call _update_xy_coordinates
        total_after = sum(len(c) for c in scan_coords.region_fov_coordinates.values())
        assert total_after == 0, (
            "Bug #4: XY OFF→ON does NOT restore coordinates"
        )

    def test_xy_off_unchecks_draw_rois_button(self, widget, event_bus):
        """Unchecking XY unchecks the draw ROIs button."""
        _init_mosaic(event_bus)
        widget._xy_mode_combo.setCurrentIndex(_MODE_ROI_TILING)
        widget._btn_draw_rois.setChecked(True)
        assert widget._btn_draw_rois.isChecked()

        widget._xy_checkbox.setChecked(False)
        assert not widget._btn_draw_rois.isChecked()

    def test_xy_off_in_multiwell_clears_backend(self, widget, event_bus, collected_clear):
        """XY off while in multiwell clears backend."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIWELL)
        event_bus.drain()
        collected_clear.clear()

        widget._xy_checkbox.setChecked(False)
        event_bus.drain()
        assert len(collected_clear) > 0

    def test_xy_off_does_not_clear_multipoint_positions(self, widget, event_bus):
        """XY off does not clear _multipoint_positions list."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIPOINT)
        _add_mp_position(widget, event_bus, 25, 25, 1)
        assert len(widget._multipoint_positions) == 1

        widget._xy_checkbox.setChecked(False)
        event_bus.drain()

        assert len(widget._multipoint_positions) == 1, (
            "XY off should not clear _multipoint_positions list"
        )


# ===========================================================================
# C. TestTabActivation (11 tests)
# ===========================================================================


class TestTabActivation:
    """Tab activation/deactivation interactions."""

    def test_activate_in_roi_with_shapes_regenerates_fovs(
        self, widget, event_bus, scan_coords, collected_manual_cmd
    ):
        """Activating tab in ROI mode with shapes regenerates FOVs."""
        _init_mosaic(event_bus)
        widget._xy_mode_combo.setCurrentIndex(_MODE_ROI_TILING)
        shapes = _make_shapes((10, 10, 13, 13))
        _inject_shapes(widget, event_bus, shapes)

        _deactivate_tab(event_bus)
        collected_manual_cmd.clear()

        _activate_tab(event_bus)

        assert len(collected_manual_cmd) > 0, "Should regenerate FOVs on tab activation"

    def test_activate_in_roi_without_shapes_no_fov_generation(
        self, widget, event_bus, collected_manual_cmd
    ):
        """Activating tab in ROI mode without shapes doesn't generate FOVs."""
        _init_mosaic(event_bus)
        widget._xy_mode_combo.setCurrentIndex(_MODE_ROI_TILING)
        assert widget._manual_shapes_mm is None

        _deactivate_tab(event_bus)
        collected_manual_cmd.clear()

        _activate_tab(event_bus)

        assert len(collected_manual_cmd) == 0

    def test_activate_in_multiwell_publishes_well_command(
        self, widget, event_bus, collected_well_cmd
    ):
        """Activating tab in multiwell mode publishes well scan command."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIWELL)
        _deactivate_tab(event_bus)
        collected_well_cmd.clear()

        _activate_tab(event_bus)

        assert len(collected_well_cmd) > 0

    def test_activate_in_multipoint_republishes_positions(
        self, widget, event_bus, collected_region_cmd
    ):
        """Activating tab in multipoint mode re-publishes positions."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIPOINT)
        _add_mp_position(widget, event_bus, 25, 25, 1)

        _deactivate_tab(event_bus)
        collected_region_cmd.clear()

        _activate_tab(event_bus)

        assert len(collected_region_cmd) > 0

    def test_deactivate_disables_roi_drawing(self, widget, event_bus, collected_drawing_changed):
        """Deactivating tab disables ROI drawing."""
        _init_mosaic(event_bus)
        widget._xy_mode_combo.setCurrentIndex(_MODE_ROI_TILING)
        event_bus.drain()
        collected_drawing_changed.clear()

        _deactivate_tab(event_bus)

        disabled = [e for e in collected_drawing_changed if not e.enabled]
        assert len(disabled) > 0

    def test_deactivate_activate_reenables_drawing(self, widget, event_bus, collected_drawing_changed):
        """Deactivating then activating re-enables ROI drawing."""
        _init_mosaic(event_bus)
        widget._xy_mode_combo.setCurrentIndex(_MODE_ROI_TILING)
        event_bus.drain()

        _deactivate_tab(event_bus)
        collected_drawing_changed.clear()
        _activate_tab(event_bus)

        enabled = [e for e in collected_drawing_changed if e.enabled]
        assert len(enabled) > 0

    def test_objective_change_while_inactive_ignored(self, widget, event_bus, collected_well_cmd):
        """Bug #5: Objective changes while tab inactive are ignored."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIWELL)
        _deactivate_tab(event_bus)
        collected_well_cmd.clear()

        event_bus.publish(ObjectiveChanged(position=1, objective_name="20x"))
        event_bus.drain()

        # Bug #5: _on_objective_changed has `if self._is_active_tab:` guard
        assert len(collected_well_cmd) == 0, (
            "Bug #5: Objective change while inactive is ignored"
        )

    def test_objective_change_while_active_recalculates(self, widget, event_bus, collected_well_cmd):
        """Objective change while active recalculates coordinates."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIWELL)
        _activate_tab(event_bus)
        collected_well_cmd.clear()

        event_bus.publish(ObjectiveChanged(position=1, objective_name="20x"))
        event_bus.drain()

        assert len(collected_well_cmd) > 0

    def test_tab_activate_after_objective_change_recovers(
        self, widget, event_bus, collected_well_cmd
    ):
        """Activating tab after objective change recovers via _update_xy_coordinates."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIWELL)
        _deactivate_tab(event_bus)
        event_bus.publish(ObjectiveChanged(position=1, objective_name="20x"))
        event_bus.drain()
        collected_well_cmd.clear()

        _activate_tab(event_bus)

        # _on_active_tab_changed calls _update_xy_coordinates which recovers
        assert len(collected_well_cmd) > 0, (
            "Tab activation should recover from missed objective change"
        )

    def test_binning_change_while_inactive_ignored(self, widget, event_bus, collected_well_cmd):
        """Bug #5: Binning changes while tab inactive are ignored."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIWELL)
        _deactivate_tab(event_bus)
        collected_well_cmd.clear()

        event_bus.publish(BinningChanged(binning_x=2, binning_y=2))
        event_bus.drain()

        assert len(collected_well_cmd) == 0, (
            "Bug #5: Binning change while inactive is ignored"
        )

    def test_binning_change_while_active_recalculates(self, widget, event_bus, collected_well_cmd):
        """Binning change while active recalculates coordinates."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIWELL)
        _activate_tab(event_bus)
        collected_well_cmd.clear()

        event_bus.publish(BinningChanged(binning_x=2, binning_y=2))
        event_bus.drain()

        assert len(collected_well_cmd) > 0

    def test_csv_coordinates_not_recalculated_on_objective_change(
        self, widget, event_bus, scan_coords
    ):
        """Bug #6: CSV-loaded coordinates are never recalculated on objective change.

        _update_xy_coordinates has no _MODE_LOAD_CSV case, so objective/binning
        changes are silently ignored for CSV-loaded coordinates.
        """
        widget._xy_mode_combo.setCurrentIndex(_MODE_LOAD_CSV)
        event_bus.drain()

        # Simulate loading CSV coordinates directly via the event
        event_bus.publish(LoadScanCoordinatesCommand(
            region_fov_coordinates={"r1": ((10.0, 20.0, 1.0), (11.0, 21.0, 1.0))},
        ))
        event_bus.drain()
        fovs_before = sum(len(c) for c in scan_coords.region_fov_coordinates.values())
        assert fovs_before == 2

        # Objective change while active and in CSV mode
        collected_load = []
        event_bus.subscribe(LoadScanCoordinatesCommand, lambda e: collected_load.append(e))
        event_bus.publish(ObjectiveChanged(position=1, objective_name="20x"))
        event_bus.drain()

        # Bug #6: _update_xy_coordinates has no _MODE_LOAD_CSV branch.
        # No LoadScanCoordinatesCommand is re-published, so coordinates are stale.
        assert len(collected_load) == 0, (
            "Bug #6: CSV coordinates are never re-published on objective change"
        )


# ===========================================================================
# D. TestProtocolSaveLoadRoundTrip (14 tests)
# ===========================================================================


class TestProtocolSaveLoadRoundTrip:
    """Protocol build/apply round-trip tests."""

    def test_basic_channels_only(self, widget):
        """Basic round-trip: channels only, no Z, no focus."""
        _select_channel(widget, "BF LED matrix full")
        proto = widget.build_imaging_protocol()
        assert proto.channels == ["BF LED matrix full"]
        assert proto.z_stack.planes == 1
        assert proto.focus.mode == AutofocusMode.NONE

        widget.apply_imaging_protocol(proto)
        proto2 = widget.build_imaging_protocol()
        assert proto2.channels == proto.channels

    def test_z_stack_round_trip_from_bottom(self, widget):
        """Z-stack round-trip with 'from_bottom' direction."""
        _select_channel(widget, "BF LED matrix full")
        widget._z_checkbox.setChecked(True)
        widget._z_nz.setValue(5)
        widget._z_delta.setValue(2.0)
        widget._z_direction.setCurrentIndex(0)  # From Bottom

        proto = widget.build_imaging_protocol()
        assert proto.z_stack.planes == 5
        assert proto.z_stack.step_um == 2.0
        assert proto.z_stack.direction == "from_bottom"

        widget.apply_imaging_protocol(proto)
        proto2 = widget.build_imaging_protocol()
        assert proto2.z_stack.planes == 5
        assert proto2.z_stack.step_um == 2.0
        assert proto2.z_stack.direction == "from_bottom"

    def test_z_stack_round_trip_from_center(self, widget):
        """Z-stack round-trip with 'from_center' direction."""
        _select_channel(widget, "BF LED matrix full")
        widget._z_checkbox.setChecked(True)
        widget._z_nz.setValue(11)
        widget._z_delta.setValue(0.5)
        widget._z_direction.setCurrentIndex(1)  # From Center

        proto = widget.build_imaging_protocol()
        widget.apply_imaging_protocol(proto)
        proto2 = widget.build_imaging_protocol()
        assert proto2.z_stack.direction == "from_center"

    def test_z_stack_round_trip_from_top(self, widget):
        """Z-stack round-trip with 'from_top' direction."""
        _select_channel(widget, "BF LED matrix full")
        widget._z_checkbox.setChecked(True)
        widget._z_nz.setValue(3)
        widget._z_delta.setValue(1.0)
        widget._z_direction.setCurrentIndex(2)  # From Top

        proto = widget.build_imaging_protocol()
        widget.apply_imaging_protocol(proto)
        proto2 = widget.build_imaging_protocol()
        assert proto2.z_stack.direction == "from_top"

    def test_contrast_af_round_trip(self, widget):
        """Contrast AF round-trip."""
        _select_channel(widget, "BF LED matrix full")
        widget._focus_checkbox.setChecked(True)
        widget._focus_method.setCurrentIndex(0)
        widget._contrast_af_interval.setValue(7)

        proto = widget.build_imaging_protocol()
        assert proto.focus.mode == AutofocusMode.CONTRAST
        assert proto.focus.interval_fovs == 7

        widget.apply_imaging_protocol(proto)
        proto2 = widget.build_imaging_protocol()
        assert proto2.focus.mode == AutofocusMode.CONTRAST
        assert proto2.focus.interval_fovs == 7

    def test_laser_af_round_trip(self, widget):
        """Laser AF round-trip."""
        _select_channel(widget, "BF LED matrix full")
        widget._focus_checkbox.setChecked(True)
        widget._focus_method.setCurrentIndex(1)
        widget._laser_af_interval.setValue(5)

        proto = widget.build_imaging_protocol()
        assert proto.focus.mode == AutofocusMode.LASER_REFLECTION

        widget.apply_imaging_protocol(proto)
        proto2 = widget.build_imaging_protocol()
        assert proto2.focus.mode == AutofocusMode.LASER_REFLECTION
        assert proto2.focus.interval_fovs == 5

    def test_no_z_no_focus_round_trip(self, widget):
        """No Z, no focus round-trip."""
        _select_channel(widget, "BF LED matrix full")
        proto = widget.build_imaging_protocol()
        assert proto.z_stack.planes == 1
        assert proto.focus.mode == AutofocusMode.NONE

        widget.apply_imaging_protocol(proto)
        proto2 = widget.build_imaging_protocol()
        assert proto2.z_stack.planes == 1
        assert proto2.focus.mode == AutofocusMode.NONE

    def test_missing_channels_silently_dropped(self, widget):
        """Apply protocol with channels not in widget — silently dropped."""
        proto = ImagingProtocol(
            channels=["BF LED matrix full", "NonexistentChannel"],
            z_stack=ZStackConfig(planes=1),
            focus=FocusConfig(mode=AutofocusMode.NONE),
        )
        widget.apply_imaging_protocol(proto)
        selected = widget._channel_order_widget.get_selected_channels_ordered()
        assert "NonexistentChannel" not in selected
        assert "BF LED matrix full" in selected

    def test_modify_after_apply_then_rebuild(self, widget):
        """Modify widget after apply, rebuild picks up changes."""
        _select_channel(widget, "BF LED matrix full")
        proto = widget.build_imaging_protocol()
        widget.apply_imaging_protocol(proto)

        # Now modify
        widget._z_checkbox.setChecked(True)
        widget._z_nz.setValue(10)

        proto2 = widget.build_imaging_protocol()
        assert proto2.z_stack.planes == 10

    def test_skip_saving_round_trip(self, widget):
        """skip_saving round-trip."""
        _select_channel(widget, "BF LED matrix full")
        widget._skip_saving.blockSignals(True)
        widget._skip_saving.setChecked(True)
        widget._skip_saving.blockSignals(False)

        proto = widget.build_imaging_protocol()
        assert proto.skip_saving is True

        widget._skip_saving.blockSignals(True)
        widget._skip_saving.setChecked(False)
        widget._skip_saving.blockSignals(False)

        widget.apply_imaging_protocol(proto)
        assert widget._skip_saving.isChecked()

    def test_save_format_round_trip_ome_tiff(self, widget):
        _select_channel(widget, "BF LED matrix full")
        widget._save_format.setCurrentIndex(0)
        proto = widget.build_imaging_protocol()
        assert proto.save_format == "ome-tiff"
        widget.apply_imaging_protocol(proto)
        assert widget._save_format.currentIndex() == 0

    def test_save_format_round_trip_tiff(self, widget):
        _select_channel(widget, "BF LED matrix full")
        widget._save_format.setCurrentIndex(1)
        proto = widget.build_imaging_protocol()
        assert proto.save_format == "tiff"
        widget.apply_imaging_protocol(proto)
        assert widget._save_format.currentIndex() == 1

    def test_save_format_round_trip_zarr(self, widget):
        _select_channel(widget, "BF LED matrix full")
        widget._save_format.setCurrentIndex(2)
        proto = widget.build_imaging_protocol()
        assert proto.save_format == "zarr-v3"
        widget.apply_imaging_protocol(proto)
        assert widget._save_format.currentIndex() == 2

    def test_focus_disabled_method_not_restored(self, widget):
        """Bug #7: apply_imaging_protocol doesn't restore focus method when mode=none."""
        _select_channel(widget, "BF LED matrix full")
        # Set up widget with laser AF
        widget._focus_checkbox.setChecked(True)
        widget._focus_method.setCurrentIndex(1)  # Laser AF
        widget._focus_checkbox.setChecked(False)

        # Build protocol with focus disabled
        proto = widget.build_imaging_protocol()
        assert proto.focus.mode == AutofocusMode.NONE

        # Reset widget to contrast AF
        widget._focus_checkbox.setChecked(True)
        widget._focus_method.setCurrentIndex(0)  # Contrast AF

        # Apply the disabled-focus protocol
        widget.apply_imaging_protocol(proto)

        # Bug #7: focus method is NOT restored when focus mode is none
        # The apply code only enters the if-block when focus mode is not none
        assert widget._focus_method.currentIndex() == 0, (
            "Bug #7: focus method not restored when focus.mode=none — "
            "combo stays at whatever it was before apply"
        )

    def test_z_range_state_not_persisted(self, widget):
        """Bug #8: Z-range state not saved/loaded in protocol."""
        _select_channel(widget, "BF LED matrix full")
        widget._z_checkbox.setChecked(True)
        widget._z_range_enable.setChecked(True)
        widget._z_min.setValue(100.0)
        widget._z_max.setValue(200.0)

        proto = widget.build_imaging_protocol()

        # Bug #8: protocol has no z_range fields — only planes/step_um/direction
        assert not hasattr(proto.z_stack, "z_range_enabled"), (
            "Bug #8: z_range state is not in the protocol model"
        )

        # Computed Nz is preserved but z_min/z_max source data is lost
        widget._z_range_enable.setChecked(False)
        widget._z_min.setValue(0)
        widget._z_max.setValue(0)

        widget.apply_imaging_protocol(proto)
        # After apply, z_range_enable is NOT restored
        assert not widget._z_range_enable.isChecked(), (
            "Bug #8: z_range_enable not restored from protocol"
        )


# ===========================================================================
# E. TestFOVCountUpdates (9 tests)
# ===========================================================================


class TestFOVCountUpdates:
    """FOV count label and _total_fovs tracking."""

    def test_scan_coordinates_updated_sets_label(self, widget, event_bus):
        """ScanCoordinatesUpdated updates label text and _total_fovs."""
        event_bus.publish(ScanCoordinatesUpdated(total_regions=2, total_fovs=42, region_ids=("a", "b")))
        event_bus.drain()
        assert widget._total_fovs == 42
        assert "42" in widget._fov_count_label.text()

    def test_clear_sets_label_zero(self, widget, event_bus):
        """Clear → label shows 0."""
        event_bus.publish(ScanCoordinatesUpdated(total_regions=2, total_fovs=42, region_ids=("a", "b")))
        event_bus.drain()
        event_bus.publish(ScanCoordinatesUpdated(total_regions=0, total_fovs=0, region_ids=()))
        event_bus.drain()
        assert widget._total_fovs == 0
        assert "0" in widget._fov_count_label.text()

    def test_roi_generation_updates_label(self, widget, event_bus, scan_coords):
        """End-to-end: ROI generation → label updated."""
        _init_mosaic(event_bus)
        widget._xy_mode_combo.setCurrentIndex(_MODE_ROI_TILING)
        shapes = _make_shapes((10, 10, 13, 13))
        _inject_shapes(widget, event_bus, shapes)
        widget._on_generate_fovs()
        event_bus.drain()

        assert widget._total_fovs > 0
        label_text = widget._fov_count_label.text()
        assert str(widget._total_fovs) in label_text, (
            f"Label should contain FOV count {widget._total_fovs}, got '{label_text}'"
        )

    def test_multipoint_add_updates_label(self, widget, event_bus, scan_coords):
        """End-to-end: multipoint add → label updated."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIPOINT)
        _add_mp_position(widget, event_bus, 25, 25, 1)

        assert widget._total_fovs > 0

    def test_mode_switch_drops_fov_count_to_zero(self, widget, event_bus, scan_coords):
        """Mode switch → label drops to 0."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIPOINT)
        _add_mp_position(widget, event_bus, 25, 25, 1)
        assert widget._total_fovs > 0

        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIWELL)
        event_bus.drain()

        assert widget._total_fovs == 0

    def test_roi_status_label_updates_on_shapes(self, widget, event_bus):
        """ROI status label updates on ManualShapesChanged."""
        _init_mosaic(event_bus)
        widget._xy_mode_combo.setCurrentIndex(_MODE_ROI_TILING)
        shapes = _make_shapes((10, 10, 13, 13), (20, 20, 23, 23))
        _inject_shapes(widget, event_bus, shapes)

        assert "2 ROIs" in widget._roi_status_label.text()

    def test_roi_status_label_single_roi(self, widget, event_bus):
        """ROI status label shows '1 ROIs' for a single shape."""
        _init_mosaic(event_bus)
        widget._xy_mode_combo.setCurrentIndex(_MODE_ROI_TILING)
        shapes = _make_shapes((10, 10, 13, 13))
        _inject_shapes(widget, event_bus, shapes)

        assert "1 ROIs" in widget._roi_status_label.text()

    def test_roi_status_label_cleared(self, widget, event_bus):
        """ROI status label shows '0 ROIs' when shapes cleared."""
        _init_mosaic(event_bus)
        widget._xy_mode_combo.setCurrentIndex(_MODE_ROI_TILING)
        shapes = _make_shapes((10, 10, 13, 13))
        _inject_shapes(widget, event_bus, shapes)
        _inject_shapes(widget, event_bus, None)

        assert "0 ROIs" in widget._roi_status_label.text()

    def test_roi_status_label_stale_after_mode_switch(self, widget, event_bus):
        """Bug #9: ROI status label text persists after mode switch (hidden but stale)."""
        _init_mosaic(event_bus)
        widget._xy_mode_combo.setCurrentIndex(_MODE_ROI_TILING)
        shapes = _make_shapes((10, 10, 13, 13), (20, 20, 23, 23))
        _inject_shapes(widget, event_bus, shapes)
        assert "2 ROIs" in widget._roi_status_label.text()

        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIWELL)
        event_bus.drain()

        # Bug #9: label text persists (it's hidden but stale)
        assert "2 ROIs" in widget._roi_status_label.text(), (
            "Bug #9: ROI status label text persists after mode switch"
        )


# ===========================================================================
# F. TestWellScanParameterInteractions (9 tests)
# ===========================================================================


class TestWellScanParameterInteractions:
    """Well scan size/coverage/overlap sync and command publishing."""

    def test_scan_size_change_publishes_command(self, widget, event_bus, collected_well_cmd):
        """Changing scan size publishes SetWellSelectionScanCoordinatesCommand."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIWELL)
        event_bus.drain()
        collected_well_cmd.clear()

        widget._well_scan_size.setValue(2.0)
        event_bus.drain()

        assert len(collected_well_cmd) > 0
        assert collected_well_cmd[-1].scan_size_mm == 2.0

    def test_overlap_change_publishes_command(self, widget, event_bus, collected_well_cmd):
        """Changing overlap publishes command."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIWELL)
        event_bus.drain()
        collected_well_cmd.clear()

        widget._well_overlap.setValue(20.0)
        event_bus.drain()

        assert len(collected_well_cmd) > 0
        assert collected_well_cmd[-1].overlap_percent == 20.0

    def test_shape_change_publishes_command(self, widget, event_bus, collected_well_cmd):
        """Changing shape publishes command."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIWELL)
        event_bus.drain()
        collected_well_cmd.clear()

        widget._well_scan_shape.setCurrentIndex(1)  # Circle
        event_bus.drain()

        assert len(collected_well_cmd) > 0
        assert collected_well_cmd[-1].shape == "Circle"

    def test_command_not_published_when_not_multiwell(self, widget, event_bus, collected_well_cmd):
        """Bug #10: Well scan param changes while not in multiwell are silently dropped."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIPOINT)
        event_bus.drain()
        collected_well_cmd.clear()

        widget._well_scan_size.setValue(3.0)
        event_bus.drain()

        # Bug #10: _publish_well_scan_command checks mode before publishing
        assert len(collected_well_cmd) == 0, (
            "Bug #10: Well scan changes not in multiwell mode are dropped"
        )

    def test_params_preserved_across_mode_switch(self, widget, event_bus):
        """Well scan spinbox values survive mode switch."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIWELL)
        widget._well_scan_size.setValue(3.5)
        widget._well_overlap.setValue(15.0)

        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIPOINT)
        event_bus.drain()
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIWELL)
        event_bus.drain()

        assert widget._well_scan_size.value() == 3.5
        assert widget._well_overlap.value() == 15.0

    def test_scan_size_coverage_bidirectional_sync(self, widget_with_fov, event_bus):
        """Scan size and coverage sync bidirectionally via well_selection_widget."""
        w = widget_with_fov
        # Give it a well_selection_widget with well_size_mm
        mock_well_widget = MagicMock()
        mock_well_widget.well_size_mm = 6.0
        w._well_selection_widget = mock_well_widget

        w._xy_mode_combo.setCurrentIndex(_MODE_MULTIWELL)

        # Change coverage — should update scan size
        w._well_coverage.setValue(50.0)
        size_at_50 = w._well_scan_size.value()

        w._well_coverage.setValue(80.0)
        size_at_80 = w._well_scan_size.value()

        # Higher coverage should mean larger scan size
        assert size_at_80 >= size_at_50, (
            f"Higher coverage should give larger scan size: {size_at_80} vs {size_at_50}"
        )

    def test_no_infinite_loop_in_bidirectional_sync(self, widget_with_fov, event_bus):
        """Bidirectional sync does not cause infinite loop (blockSignals used)."""
        w = widget_with_fov
        mock_well_widget = MagicMock()
        mock_well_widget.well_size_mm = 6.0
        w._well_selection_widget = mock_well_widget

        w._xy_mode_combo.setCurrentIndex(_MODE_MULTIWELL)

        # This should complete without hanging
        w._well_scan_size.setValue(2.0)
        w._well_coverage.setValue(30.0)
        w._well_scan_size.setValue(4.0)

    def test_no_sync_when_fov_size_zero(self, widget, event_bus):
        """No coverage sync when camera_fov_size_mm=0."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIWELL)

        # With camera_fov_size_mm=0 (default), coverage sync is skipped
        original_coverage = widget._well_coverage.value()
        widget._well_scan_size.setValue(5.0)

        # Coverage should remain unchanged since fov_size_mm is 0
        assert widget._well_coverage.value() == original_coverage

    def test_coverage_change_updates_scan_size(self, widget_with_fov, event_bus):
        """Coverage change updates scan size (reverse direction)."""
        w = widget_with_fov
        mock_well_widget = MagicMock()
        mock_well_widget.well_size_mm = 6.0
        w._well_selection_widget = mock_well_widget

        w._xy_mode_combo.setCurrentIndex(_MODE_MULTIWELL)

        # Set a known starting coverage and capture the resulting scan size
        w._well_coverage.setValue(20.0)
        size_at_20 = w._well_scan_size.value()

        # Increase coverage — scan size must increase
        w._well_coverage.setValue(90.0)
        size_at_90 = w._well_scan_size.value()

        assert size_at_90 > size_at_20, (
            f"Higher coverage should give larger scan size: {size_at_90} vs {size_at_20}"
        )


# ===========================================================================
# G. TestZStackEdgeCases (9 tests)
# ===========================================================================


class TestZStackEdgeCases:
    """Z-stack edge cases and range mode."""

    def test_z_range_toggle_populates_from_cached_z(self, widget, event_bus):
        """Z-range toggle populates z_min/z_max from cached Z position."""
        _set_stage_position(event_bus, 25, 25, 2.5)
        widget._z_checkbox.setChecked(True)
        widget._z_range_enable.setChecked(True)

        assert widget._z_min.value() == pytest.approx(2500.0, abs=1.0), (
            "z_min should be cached_z_mm * 1000"
        )
        assert widget._z_max.value() == pytest.approx(2500.0, abs=1.0)

    def test_nz_computation_ceil_formula(self, widget):
        """Nz = ceil((z_max - z_min) / dz) + 1."""
        widget._z_checkbox.setChecked(True)
        widget._z_range_enable.setChecked(True)
        widget._z_delta.setValue(2.0)
        widget._z_min.setValue(100.0)
        widget._z_max.setValue(110.0)
        # nz = ceil((110-100)/2) + 1 = 6
        assert widget._z_nz.value() == 6

    def test_z_min_equals_z_max_gives_nz_1(self, widget):
        """z_min == z_max → Nz = 1."""
        widget._z_checkbox.setChecked(True)
        widget._z_range_enable.setChecked(True)
        widget._z_delta.setValue(2.0)
        widget._z_min.setValue(100.0)
        widget._z_max.setValue(100.0)
        # nz = ceil(0/2) + 1 = 1
        assert widget._z_nz.value() == 1

    def test_very_small_dz_caps_nz(self, widget):
        """Very small dz — Nz capped by spinbox max (2000)."""
        widget._z_checkbox.setChecked(True)
        widget._z_range_enable.setChecked(True)
        widget._z_delta.setValue(0.01)
        widget._z_min.setValue(0.0)
        widget._z_max.setValue(1000.0)
        # nz = ceil(1000/0.01) + 1 = 100001, but spinbox max is 2000
        assert widget._z_nz.value() == 2000

    def test_z_off_resets_nz(self, widget):
        """Z off resets Nz to 1."""
        widget._z_checkbox.setChecked(True)
        widget._z_nz.setValue(10)
        widget._z_checkbox.setChecked(False)
        assert widget._z_nz.value() == 1

    def test_z_off_disables_range(self, widget):
        """Z off disables z_range_enable and hides range frame."""
        widget._z_checkbox.setChecked(True)
        widget._z_range_enable.setChecked(True)
        assert not widget._z_range_frame.isHidden()

        widget._z_checkbox.setChecked(False)
        assert not widget._z_range_enable.isChecked()
        assert widget._z_range_frame.isHidden()

    def test_z_off_on_reenables_controls(self, widget):
        """Z off→on re-enables dz and Nz controls."""
        widget._z_checkbox.setChecked(True)
        widget._z_checkbox.setChecked(False)
        assert not widget._z_delta.isEnabled()

        widget._z_checkbox.setChecked(True)
        assert widget._z_delta.isEnabled()
        assert widget._z_nz.isEnabled()

    def test_z_range_disables_manual_nz(self, widget):
        """Z-range mode disables manual Nz entry."""
        widget._z_checkbox.setChecked(True)
        assert widget._z_nz.isEnabled()

        widget._z_range_enable.setChecked(True)
        assert not widget._z_nz.isEnabled()

    def test_z_range_unchecked_reenables_manual_nz(self, widget):
        """Unchecking Z-range re-enables manual Nz entry."""
        widget._z_checkbox.setChecked(True)
        widget._z_range_enable.setChecked(True)
        assert not widget._z_nz.isEnabled()

        widget._z_range_enable.setChecked(False)
        assert widget._z_nz.isEnabled()


# ===========================================================================
# H. TestMultipointInteractions (12 tests)
# ===========================================================================


class TestMultipointInteractions:
    """Multipoint add/remove/clear and grid parameter interactions."""

    def test_add_updates_table(self, widget, event_bus):
        """Add: table row count matches positions."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIPOINT)
        _add_mp_position(widget, event_bus, 25, 25, 1)
        assert widget._mp_table.rowCount() == 1

    def test_add_publishes_coordinates(self, widget, event_bus, scan_coords):
        """Add: coordinates published to backend."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIPOINT)
        _add_mp_position(widget, event_bus, 25, 25, 1)
        assert len(scan_coords.region_fov_coordinates) > 0

    def test_add_multiple_all_in_table(self, widget, event_bus):
        """Add multiple: all in table."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIPOINT)
        for x, y in [(10, 10), (20, 20), (30, 30)]:
            _add_mp_position(widget, event_bus, x, y, 1)
        assert widget._mp_table.rowCount() == 3

    def test_add_multiple_all_published(self, widget, event_bus, scan_coords):
        """Add multiple: all published to backend."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIPOINT)
        for x, y in [(10, 10), (20, 20), (30, 30)]:
            _add_mp_position(widget, event_bus, x, y, 1)
        assert len(scan_coords.region_fov_coordinates) == 3

    def test_remove_updates_table(self, widget, event_bus):
        """Remove: table updated."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIPOINT)
        _add_mp_position(widget, event_bus, 10, 10, 1)
        _add_mp_position(widget, event_bus, 20, 20, 1)
        assert widget._mp_table.rowCount() == 2

        widget._mp_table.setCurrentCell(0, 0)
        widget._on_mp_remove()
        event_bus.drain()
        assert widget._mp_table.rowCount() == 1

    def test_remove_remaining_republished(self, widget, event_bus, scan_coords):
        """Remove: remaining positions re-published."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIPOINT)
        _add_mp_position(widget, event_bus, 10, 10, 1)
        _add_mp_position(widget, event_bus, 20, 20, 1)

        widget._mp_table.setCurrentCell(0, 0)
        widget._on_mp_remove()
        event_bus.drain()

        # Should have exactly 1 region remaining
        assert len(scan_coords.region_fov_coordinates) == 1

    def test_clear_empties_table(self, widget, event_bus):
        """Clear: table empty."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIPOINT)
        _add_mp_position(widget, event_bus, 25, 25, 1)
        widget._on_mp_clear()
        assert widget._mp_table.rowCount() == 0

    def test_clear_publishes_clear_command(self, widget, event_bus, collected_clear):
        """Clear: ClearScanCoordinatesCommand published."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIPOINT)
        _add_mp_position(widget, event_bus, 25, 25, 1)
        collected_clear.clear()

        widget._on_mp_clear()
        event_bus.drain()
        assert len(collected_clear) > 0

    def test_nx_ny_change_affects_fov_count(self, widget, event_bus, scan_coords):
        """Nx/Ny change: grid size changes FOV count."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIPOINT)
        widget._mp_nx.setValue(1)
        widget._mp_ny.setValue(1)
        _add_mp_position(widget, event_bus, 25, 25, 1)

        total_1x1 = sum(len(c) for c in scan_coords.region_fov_coordinates.values())

        widget._mp_nx.setValue(3)
        widget._mp_ny.setValue(3)
        widget._on_mp_params_changed()
        event_bus.drain()

        total_3x3 = sum(len(c) for c in scan_coords.region_fov_coordinates.values())
        assert total_3x3 > total_1x1

    def test_overlap_change_republishes(self, widget, event_bus, collected_region_cmd):
        """Overlap change: re-publishes regions."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIPOINT)
        _add_mp_position(widget, event_bus, 25, 25, 1)
        collected_region_cmd.clear()

        widget._mp_overlap.setValue(20.0)
        event_bus.drain()

        assert len(collected_region_cmd) > 0
        assert collected_region_cmd[-1].overlap_percent == 20.0

    def test_uses_cached_stage_position(self, widget, event_bus):
        """Add position uses cached stage position."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIPOINT)
        _set_stage_position(event_bus, 12.5, 17.3, 0.5)

        widget._on_mp_add()
        event_bus.drain()

        rid, x, y, z = widget._multipoint_positions[-1]
        assert x == pytest.approx(12.5)
        assert y == pytest.approx(17.3)
        assert z == pytest.approx(0.5)

    def test_remove_invalid_row_is_noop(self, widget, event_bus):
        """Remove with no selection is no-op."""
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIPOINT)
        _add_mp_position(widget, event_bus, 25, 25, 1)

        # No row selected (currentRow() returns -1)
        widget._mp_table.setCurrentCell(-1, -1)
        widget._on_mp_remove()
        assert len(widget._multipoint_positions) == 1


# ===========================================================================
# I. TestChannelOrderWidgetIntegration (7 tests)
# ===========================================================================


class TestChannelOrderWidgetIntegration:
    """Channel order widget integration with AcquisitionSetupWidget."""

    def test_set_channels_preserves_existing_selections(self, widget, event_bus):
        """set_channels preserves existing selections."""
        _select_channels(widget, ["BF LED matrix full"])
        selected_before = widget._channel_order_widget.get_selected_channels_ordered()
        assert "BF LED matrix full" in selected_before

        # Update channels — BF LED still exists
        event_bus.publish(ChannelConfigurationsChanged(
            objective_name="default",
            configuration_names=["BF LED matrix full", "Fluorescence 488 nm Ex", "Fluorescence 561 nm Ex"],
        ))
        event_bus.drain()

        selected_after = widget._channel_order_widget.get_selected_channels_ordered()
        assert "BF LED matrix full" in selected_after

    def test_set_selected_channels_reorders(self, widget):
        """set_selected_channels reorders and checks channels."""
        _select_channels(widget, ["Fluorescence 488 nm Ex", "BF LED matrix full"])
        selected = widget._channel_order_widget.get_selected_channels_ordered()
        assert selected == ["Fluorescence 488 nm Ex", "BF LED matrix full"]

    def test_non_existent_channels_silently_dropped(self, widget):
        """Non-existent channels silently dropped in set_selected_channels."""
        _select_channels(widget, ["BF LED matrix full", "NonexistentChannel"])
        selected = widget._channel_order_widget.get_selected_channels_ordered()
        assert "NonexistentChannel" not in selected
        assert "BF LED matrix full" in selected

    def test_channel_configs_changed_event_updates_widget(self, widget, event_bus):
        """ChannelConfigurationsChanged event updates widget."""
        event_bus.publish(ChannelConfigurationsChanged(
            objective_name="default",
            configuration_names=["Ch1", "Ch2", "Ch3"],
        ))
        event_bus.drain()

        all_channels = widget._channel_order_widget.get_all_channels()
        assert all_channels == ["Ch1", "Ch2", "Ch3"]

    def test_empty_selection_returns_empty(self, widget):
        """Empty selection returns []."""
        widget._channel_order_widget.clear_selection()
        assert widget._channel_order_widget.get_selected_channels_ordered() == []

    def test_order_matches_get_selected(self, widget):
        """Order in list matches get_selected_channels_ordered()."""
        _select_channels(widget, ["Fluorescence 488 nm Ex", "BF LED matrix full"])
        ordered = widget._channel_order_widget.get_selected_channels_ordered()
        assert ordered[0] == "Fluorescence 488 nm Ex"
        assert ordered[1] == "BF LED matrix full"

    def test_build_protocol_uses_channel_order(self, widget):
        """build_imaging_protocol uses channel order from widget."""
        _select_channels(widget, ["Fluorescence 488 nm Ex", "BF LED matrix full"])
        proto = widget.build_imaging_protocol()
        assert proto.channels[0] == "Fluorescence 488 nm Ex"
        assert proto.channels[1] == "BF LED matrix full"


# ===========================================================================
# J. TestMosaicInitialization (5 tests)
# ===========================================================================


class TestMosaicInitialization:
    """Mosaic initialization and clearing effects on ROI mode."""

    def test_roi_mode_disabled_initially(self, widget):
        """ROI mode disabled initially (before mosaic init)."""
        assert not widget._xy_mode_combo.model().item(_MODE_ROI_TILING).isEnabled()

    def test_mosaic_initialized_enables_roi(self, widget, event_bus):
        """MosaicLayersInitialized enables ROI mode."""
        _init_mosaic(event_bus)
        assert widget._xy_mode_combo.model().item(_MODE_ROI_TILING).isEnabled()

    def test_mosaic_cleared_disables_roi(self, widget, event_bus):
        """MosaicLayersCleared disables ROI mode."""
        _init_mosaic(event_bus)
        assert widget._xy_mode_combo.model().item(_MODE_ROI_TILING).isEnabled()

        event_bus.publish(MosaicLayersCleared())
        event_bus.drain()

        assert not widget._xy_mode_combo.model().item(_MODE_ROI_TILING).isEnabled()

    def test_mosaic_cleared_while_in_roi_switches_to_multiwell(self, widget, event_bus):
        """MosaicLayersCleared while in ROI → switches to Multiwell."""
        _init_mosaic(event_bus)
        widget._xy_mode_combo.setCurrentIndex(_MODE_ROI_TILING)
        event_bus.drain()

        event_bus.publish(MosaicLayersCleared())
        event_bus.drain()

        assert widget._xy_mode_combo.currentIndex() == _MODE_MULTIWELL

    def test_mosaic_cleared_while_in_multipoint_no_mode_change(self, widget, event_bus):
        """MosaicLayersCleared while in Multipoint → no mode change."""
        _init_mosaic(event_bus)
        widget._xy_mode_combo.setCurrentIndex(_MODE_MULTIPOINT)
        event_bus.drain()

        event_bus.publish(MosaicLayersCleared())
        event_bus.drain()

        assert widget._xy_mode_combo.currentIndex() == _MODE_MULTIPOINT


# ===========================================================================
# K. TestStagePositionCaching (3 tests)
# ===========================================================================


class TestStagePositionCaching:
    """Stage position caching via StagePositionChanged."""

    def test_stage_position_cached(self, widget, event_bus):
        """StagePositionChanged caches x, y, z."""
        _set_stage_position(event_bus, 12.5, 17.3, 0.5)
        assert widget._cached_x_mm == pytest.approx(12.5)
        assert widget._cached_y_mm == pytest.approx(17.3)
        assert widget._cached_z_mm == pytest.approx(0.5)

    def test_set_z_min_uses_cached_z(self, widget, event_bus):
        """Set Z-min button uses cached Z (via lambda connection)."""
        _set_stage_position(event_bus, 25, 25, 3.0)
        widget._z_checkbox.setChecked(True)

        widget._btn_set_zmin.click()
        assert widget._z_min.value() == pytest.approx(3000.0, abs=1.0)

    def test_set_z_max_uses_cached_z(self, widget, event_bus):
        """Set Z-max button uses cached Z (via lambda connection)."""
        _set_stage_position(event_bus, 25, 25, 4.5)
        widget._z_checkbox.setChecked(True)

        widget._btn_set_zmax.click()
        assert widget._z_max.value() == pytest.approx(4500.0, abs=1.0)
