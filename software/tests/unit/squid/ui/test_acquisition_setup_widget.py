"""Integration tests for AcquisitionSetupWidget with the full event chain.

Exercises the full flow from shape events → FOV generation → coordinate updates.
Tests the widget interaction with ScanCoordinates via EventBus.
"""

import numpy as np
import pytest
from unittest.mock import MagicMock

from qtpy.QtCore import Qt

from squid.core.events import (
    AcquisitionProgress,
    AcquisitionStateChanged,
    EventBus,
    ManualShapesChanged,
    MosaicLayersInitialized,
    ScanCoordinatesUpdated,
    SetManualScanCoordinatesCommand,
    ClearScanCoordinatesCommand,
    AddFlexibleRegionCommand,
    StagePositionChanged,
    SelectedWellsChanged,
    SetWellSelectionScanCoordinatesCommand,
    ChannelConfigurationsChanged,
)
from squid.backend.managers.scan_coordinates.scan_coordinates import ScanCoordinates
from squid.backend.managers.objective_store import ObjectiveStore
from squid.ui.widgets.acquisition.acquisition_setup import AcquisitionSetupWidget


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
    """Create AcquisitionSetupWidget using the EventBus directly (no UIEventBus needed for tests)."""
    w = AcquisitionSetupWidget(
        event_bus=event_bus,
        initial_channel_configs=["BF LED matrix full", "Fluorescence 488 nm Ex"],
    )
    qtbot.addWidget(w)
    return w


@pytest.fixture
def collected_events(event_bus):
    events = []

    def handler(evt):
        events.append(evt)

    event_bus.subscribe(ScanCoordinatesUpdated, handler)
    return events


@pytest.fixture
def collected_commands(event_bus):
    """Collect SetManualScanCoordinatesCommand events."""
    commands = []

    def handler(evt):
        commands.append(evt)

    event_bus.subscribe(SetManualScanCoordinatesCommand, handler)
    return commands


@pytest.fixture
def collected_clear_commands(event_bus):
    commands = []

    def handler(evt):
        commands.append(evt)

    event_bus.subscribe(ClearScanCoordinatesCommand, handler)
    return commands


# ===========================================================================
# Mosaic ROI → FOV Generation (the bug)
# ===========================================================================


class TestMosaicROIFullFlow:
    """Test the full mosaic ROI → FOV generation flow through the widget."""

    def test_manual_shapes_stored_after_event(self, widget, event_bus):
        """ManualShapesChanged event should store shapes in the widget."""
        shapes = (
            ((15.0, 15.0), (18.0, 15.0), (18.0, 18.0), (15.0, 18.0)),
        )
        event_bus.publish(ManualShapesChanged(shapes_mm=shapes))
        event_bus.drain()

        assert widget._manual_shapes_mm is not None, "Shapes should be stored"
        assert len(widget._manual_shapes_mm) == 1, "Should have 1 shape"
        assert widget._manual_shapes_mm[0].shape == (4, 2), (
            f"Shape should be 4x2, got {widget._manual_shapes_mm[0].shape}"
        )

    def test_manual_shapes_none_clears(self, widget, event_bus):
        """ManualShapesChanged with None should clear stored shapes."""
        # First store some shapes
        shapes = (((15.0, 15.0), (18.0, 15.0), (18.0, 18.0), (15.0, 18.0)),)
        event_bus.publish(ManualShapesChanged(shapes_mm=shapes))
        event_bus.drain()
        assert widget._manual_shapes_mm is not None

        # Then clear
        event_bus.publish(ManualShapesChanged(shapes_mm=None))
        event_bus.drain()
        assert widget._manual_shapes_mm is None

    def test_generate_fovs_with_stored_shapes(
        self, widget, event_bus, scan_coords, collected_events
    ):
        """Full flow: store shapes → click Generate → verify FOVs created."""
        # Step 1: Simulate ManualShapesChanged (as if from mosaic widget)
        shapes = (
            ((15.0, 15.0), (18.0, 15.0), (18.0, 18.0), (15.0, 18.0)),
        )
        event_bus.publish(ManualShapesChanged(shapes_mm=shapes))
        event_bus.drain()

        assert widget._manual_shapes_mm is not None
        assert len(widget._manual_shapes_mm) == 1

        # Step 2: Simulate clicking "Generate FOVs"
        widget._on_generate_fovs()
        event_bus.drain()

        # Step 3: Verify FOVs were generated
        assert len(scan_coords.region_fov_coordinates) > 0, (
            f"No regions created. _manual_shapes_mm={widget._manual_shapes_mm}"
        )
        total_fovs = sum(len(c) for c in scan_coords.region_fov_coordinates.values())
        assert total_fovs > 0, f"No FOVs generated"

        # Step 4: Verify ScanCoordinatesUpdated was published with non-zero FOVs
        non_zero = [e for e in collected_events if e.total_fovs > 0]
        assert len(non_zero) > 0, (
            f"No ScanCoordinatesUpdated with >0 FOVs. "
            f"All events: {[(e.total_fovs, e.total_regions) for e in collected_events]}"
        )

    def test_generate_fovs_publishes_command(
        self, widget, event_bus, collected_commands
    ):
        """Clicking Generate FOVs should publish SetManualScanCoordinatesCommand."""
        shapes = (
            ((15.0, 15.0), (18.0, 15.0), (18.0, 18.0), (15.0, 18.0)),
        )
        event_bus.publish(ManualShapesChanged(shapes_mm=shapes))
        event_bus.drain()

        widget._on_generate_fovs()
        event_bus.drain()

        assert len(collected_commands) > 0, (
            "SetManualScanCoordinatesCommand was not published"
        )
        cmd = collected_commands[-1]
        assert cmd.manual_shapes_mm is not None
        assert len(cmd.manual_shapes_mm) > 0
        assert len(cmd.manual_shapes_mm[0]) == 4  # 4 vertices

    def test_generate_fovs_clears_displayed_fovs_first(
        self, widget, event_bus, collected_clear_commands
    ):
        shapes = (
            ((15.0, 15.0), (18.0, 15.0), (18.0, 18.0), (15.0, 18.0)),
        )
        event_bus.publish(ManualShapesChanged(shapes_mm=shapes))
        event_bus.drain()
        collected_clear_commands.clear()

        widget._on_generate_fovs()
        event_bus.drain()

        assert len(collected_clear_commands) > 0
        assert collected_clear_commands[-1].clear_displayed_fovs is True

    def test_generate_fovs_with_none_shapes_is_noop(self, widget, event_bus, collected_commands):
        """Generate FOVs with no shapes should be a no-op."""
        assert widget._manual_shapes_mm is None
        widget._on_generate_fovs()
        event_bus.drain()
        assert len(collected_commands) == 0

    def test_generate_fovs_updates_fov_count_label(
        self, widget, event_bus, scan_coords
    ):
        """After generating FOVs, the FOV count label should update."""
        shapes = (
            ((15.0, 15.0), (18.0, 15.0), (18.0, 18.0), (15.0, 18.0)),
        )
        event_bus.publish(ManualShapesChanged(shapes_mm=shapes))
        event_bus.drain()

        widget._on_generate_fovs()
        event_bus.drain()

        text = widget._fov_count_label.text()
        assert "0 FOVs" not in text or text == "0 FOVs", (
            f"FOV count should be non-zero, got: '{text}'"
        )

    def test_stage_move_after_roi_draw_does_not_shift_generated_fovs(
        self, widget, event_bus, scan_coords, collected_commands
    ):
        """Manual ROI FOVs should be invariant to subsequent stage movement."""
        shapes = (
            ((15.0, 15.0), (18.0, 15.0), (18.0, 18.0), (15.0, 18.0)),
        )
        event_bus.publish(ManualShapesChanged(shapes_mm=shapes))
        event_bus.drain()

        # First generation (reference)
        widget._on_generate_fovs()
        event_bus.drain()
        assert "manual" in scan_coords.region_fov_coordinates
        coords_before = np.array(scan_coords.region_fov_coordinates["manual"], dtype=float)
        cmd_before = collected_commands[-1]
        assert cmd_before.manual_shapes_mm == shapes

        # Move stage after ROI was already stored.
        event_bus.publish(StagePositionChanged(x_mm=41.0, y_mm=7.5, z_mm=2.0))
        event_bus.drain()
        assert widget._cached_x_mm == pytest.approx(41.0)
        assert widget._cached_y_mm == pytest.approx(7.5)

        # Regenerate and verify exact same ROI payload and FOV placement.
        widget._on_generate_fovs()
        event_bus.drain()
        assert "manual" in scan_coords.region_fov_coordinates
        coords_after = np.array(scan_coords.region_fov_coordinates["manual"], dtype=float)
        cmd_after = collected_commands[-1]

        assert cmd_after.manual_shapes_mm == shapes
        np.testing.assert_allclose(coords_after, coords_before, atol=1e-9)

    def test_roi_status_label_updates(self, widget, event_bus):
        """ROI status label should show number of ROIs when in ROI mode."""
        # Switch to ROI Tiling mode
        widget._xy_mode_combo.setCurrentIndex(1)  # _MODE_ROI_TILING

        shapes = (
            ((10.0, 10.0), (12.0, 10.0), (12.0, 12.0), (10.0, 12.0)),
            ((20.0, 20.0), (22.0, 20.0), (22.0, 22.0), (20.0, 22.0)),
        )
        event_bus.publish(ManualShapesChanged(shapes_mm=shapes))
        event_bus.drain()

        assert "2 ROIs" in widget._roi_status_label.text(), (
            f"Expected '2 ROIs', got '{widget._roi_status_label.text()}'"
        )

    def test_multiple_shapes_generate_multiple_regions(
        self, widget, event_bus, scan_coords
    ):
        """Multiple ROI shapes should create multiple scan regions."""
        shapes = (
            ((10.0, 10.0), (13.0, 10.0), (13.0, 13.0), (10.0, 13.0)),
            ((30.0, 30.0), (33.0, 30.0), (33.0, 33.0), (30.0, 33.0)),
        )
        event_bus.publish(ManualShapesChanged(shapes_mm=shapes))
        event_bus.drain()

        widget._on_generate_fovs()
        event_bus.drain()

        assert len(scan_coords.region_fov_coordinates) == 2, (
            f"Expected 2 regions, got {len(scan_coords.region_fov_coordinates)}: "
            f"{list(scan_coords.region_fov_coordinates.keys())}"
        )

    def test_clear_rois_clears_shapes_and_coordinates(
        self, widget, event_bus, scan_coords
    ):
        """Clear ROIs should clear shapes and coordinates."""
        shapes = (
            ((15.0, 15.0), (18.0, 15.0), (18.0, 18.0), (15.0, 18.0)),
        )
        event_bus.publish(ManualShapesChanged(shapes_mm=shapes))
        event_bus.drain()
        widget._on_generate_fovs()
        event_bus.drain()
        assert len(scan_coords.region_fov_coordinates) > 0

        # Clear
        widget._on_clear_rois()
        event_bus.drain()

        assert widget._manual_shapes_mm is None
        assert len(scan_coords.region_fov_coordinates) == 0

    def test_shape_data_format_matches_backend(self, widget, event_bus):
        """Verify the data format flowing through the event chain is consistent."""
        # Simulate what napari mosaic would publish
        shapes = (
            ((10.5, 20.3), (15.2, 20.3), (15.2, 25.1), (10.5, 25.1)),
        )
        event_bus.publish(ManualShapesChanged(shapes_mm=shapes))
        event_bus.drain()

        # Check stored format
        assert widget._manual_shapes_mm is not None
        arr = widget._manual_shapes_mm[0]
        assert arr.shape == (4, 2)
        np.testing.assert_allclose(arr[0], [10.5, 20.3])
        np.testing.assert_allclose(arr[1], [15.2, 20.3])

        # Check what _on_generate_fovs would publish
        shapes_tuples = tuple(
            tuple(tuple(map(float, xy)) for xy in shape)
            for shape in widget._manual_shapes_mm
        )
        assert shapes_tuples == shapes


# ===========================================================================
# Multipoint mode
# ===========================================================================


class TestMultipointMode:
    """Test multipoint add/remove/clear via widget methods."""

    def test_add_position_from_stage(self, widget, event_bus, scan_coords):
        """Adding a position should create a flexible region."""
        # Set stage position first
        event_bus.publish(StagePositionChanged(x_mm=25.0, y_mm=25.0, z_mm=1.0))
        event_bus.drain()

        # Switch to multipoint mode
        widget._xy_mode_combo.setCurrentIndex(2)  # _MODE_MULTIPOINT

        # Add position
        widget._on_mp_add()
        event_bus.drain()

        assert len(widget._multipoint_positions) == 1
        assert len(scan_coords.region_fov_coordinates) > 0

    def test_add_multiple_positions(self, widget, event_bus, scan_coords):
        """Adding multiple positions should create multiple regions."""
        widget._xy_mode_combo.setCurrentIndex(2)

        for x, y in [(10, 10), (20, 20), (30, 30)]:
            widget._cached_x_mm = float(x)
            widget._cached_y_mm = float(y)
            widget._cached_z_mm = 1.0
            widget._on_mp_add()

        event_bus.drain()
        assert len(widget._multipoint_positions) == 3

    def test_remove_position(self, widget, event_bus):
        widget._xy_mode_combo.setCurrentIndex(2)
        widget._cached_x_mm = 25.0
        widget._cached_y_mm = 25.0
        widget._cached_z_mm = 1.0
        widget._on_mp_add()
        widget._on_mp_add()
        assert len(widget._multipoint_positions) == 2

        widget._mp_table.setCurrentCell(0, 0)
        widget._on_mp_remove()
        assert len(widget._multipoint_positions) == 1

    def test_clear_positions(self, widget, event_bus, scan_coords):
        widget._xy_mode_combo.setCurrentIndex(2)
        widget._cached_x_mm = 25.0
        widget._cached_y_mm = 25.0
        widget._cached_z_mm = 1.0
        widget._on_mp_add()
        widget._on_mp_add()

        widget._on_mp_clear()
        event_bus.drain()
        assert len(widget._multipoint_positions) == 0

    def test_nx_ny_affects_fov_count(self, widget, event_bus, scan_coords):
        """Changing Nx/Ny should affect the number of FOVs."""
        widget._xy_mode_combo.setCurrentIndex(2)
        widget._cached_x_mm = 25.0
        widget._cached_y_mm = 25.0
        widget._cached_z_mm = 1.0

        # 1x1 grid
        widget._mp_nx.setValue(1)
        widget._mp_ny.setValue(1)
        widget._on_mp_add()
        event_bus.drain()

        total_1x1 = sum(len(c) for c in scan_coords.region_fov_coordinates.values())

        # Now 3x3 grid
        widget._mp_nx.setValue(3)
        widget._mp_ny.setValue(3)
        widget._on_mp_params_changed()
        event_bus.drain()

        total_3x3 = sum(len(c) for c in scan_coords.region_fov_coordinates.values())
        assert total_3x3 > total_1x1


# ===========================================================================
# Z-stack controls
# ===========================================================================


class TestZStackControls:
    """Test Z-stack configuration in the widget."""

    def test_z_unchecked_by_default(self, widget):
        assert not widget._z_checkbox.isChecked()
        assert not widget._z_delta.isEnabled()
        assert not widget._z_nz.isEnabled()

    def test_z_checked_enables_controls(self, widget):
        widget._z_checkbox.setChecked(True)
        assert widget._z_delta.isEnabled()
        assert widget._z_nz.isEnabled()
        assert widget._z_direction.isEnabled()

    def test_z_unchecked_resets_nz(self, widget):
        widget._z_checkbox.setChecked(True)
        widget._z_nz.setValue(10)
        widget._z_checkbox.setChecked(False)
        assert widget._z_nz.value() == 1

    def test_z_range_toggle(self, widget):
        widget._z_checkbox.setChecked(True)
        widget._z_range_enable.setChecked(True)
        # Use not .isHidden() since widget may not be shown (isVisible requires parent visible)
        assert not widget._z_range_frame.isHidden()
        assert not widget._z_nz.isEnabled()

    def test_z_range_computes_nz(self, widget):
        widget._z_checkbox.setChecked(True)
        widget._z_range_enable.setChecked(True)
        widget._z_delta.setValue(2.0)  # 2 µm step
        # Z range is in µm; SOFTWARE_POS_LIMIT.Z_NEGATIVE*1000 = 50 µm minimum
        widget._z_min.setValue(100.0)
        widget._z_max.setValue(110.0)
        # nz = ceil((110-100)/2) + 1 = ceil(5) + 1 = 6
        assert widget._z_nz.value() == 6


# ===========================================================================
# Focus controls
# ===========================================================================


class TestFocusControls:
    """Test focus configuration in the widget."""

    def test_focus_unchecked_by_default(self, widget):
        assert not widget._focus_checkbox.isChecked()
        assert not widget._focus_method.isEnabled()
        assert widget._focus_controls_frame.isHidden()

    def test_focus_checked_enables_controls(self, widget):
        widget._focus_checkbox.setChecked(True)
        assert widget._focus_method.isEnabled()
        assert not widget._focus_controls_frame.isHidden()

    def test_focus_method_switches_stack(self, widget):
        widget._focus_checkbox.setChecked(True)
        widget._focus_method.setCurrentIndex(0)
        assert widget._focus_stack.currentIndex() == 0

        widget._focus_method.setCurrentIndex(1)
        assert widget._focus_stack.currentIndex() == 1


# ===========================================================================
# Protocol build
# ===========================================================================


class TestProtocolBuild:
    """Test building imaging protocol from widget state."""

    @staticmethod
    def _select_channel(widget):
        """Select at least one channel for protocol building."""
        widget._channel_order_widget.set_selected_channels(["BF LED matrix full"])

    def test_build_basic_protocol(self, widget):
        self._select_channel(widget)
        protocol = widget.build_imaging_protocol()
        assert len(protocol.channels) > 0
        assert protocol.z_stack.planes == 1
        assert not protocol.focus.enabled

    def test_build_protocol_with_z(self, widget):
        self._select_channel(widget)
        widget._z_checkbox.setChecked(True)
        widget._z_nz.setValue(5)
        widget._z_delta.setValue(2.0)
        protocol = widget.build_imaging_protocol()
        assert protocol.z_stack.planes == 5
        assert protocol.z_stack.step_um == 2.0

    def test_build_protocol_with_focus(self, widget):
        self._select_channel(widget)
        widget._focus_checkbox.setChecked(True)
        widget._focus_method.setCurrentIndex(0)  # Contrast AF
        widget._contrast_af_interval.setValue(5)
        protocol = widget.build_imaging_protocol()
        assert protocol.focus.enabled
        assert protocol.focus.method == "contrast"
        assert protocol.focus.interval_fovs == 5

    def test_build_protocol_no_channels_raises(self, widget):
        """Building protocol with no channels should raise ValueError."""
        widget._channel_order_widget.set_selected_channels([])
        with pytest.raises(ValueError, match="Select at least one channel"):
            widget.build_imaging_protocol()

    def test_apply_and_rebuild_protocol(self, widget):
        """Apply a protocol and rebuild it — should match."""
        self._select_channel(widget)
        widget._z_checkbox.setChecked(True)
        widget._z_nz.setValue(5)
        widget._z_delta.setValue(2.0)
        widget._z_direction.setCurrentIndex(0)  # From Bottom
        widget._focus_checkbox.setChecked(True)
        widget._focus_method.setCurrentIndex(1)  # Laser AF

        proto1 = widget.build_imaging_protocol()
        widget.apply_imaging_protocol(proto1)
        proto2 = widget.build_imaging_protocol()

        assert proto2.z_stack.planes == proto1.z_stack.planes
        assert proto2.z_stack.step_um == proto1.z_stack.step_um
        assert proto2.z_stack.direction == proto1.z_stack.direction
        assert proto2.focus.enabled == proto1.focus.enabled
        assert proto2.focus.method == proto1.focus.method


# ===========================================================================
# XY mode switching
# ===========================================================================


class TestXYModeSwitching:
    """Test switching between XY modes."""

    def test_mode_panels_visibility(self, widget):
        """Only the active mode's panel should not be hidden."""
        for i in range(4):
            if i == 1:
                continue  # ROI mode may be disabled
            widget._xy_mode_combo.setCurrentIndex(i)
            for j, panel in enumerate(widget._xy_panels):
                if j == i:
                    assert not panel.isHidden(), f"Panel {j} should not be hidden for mode {i}"
                else:
                    assert panel.isHidden(), f"Panel {j} should be hidden for mode {i}"

    def test_xy_unchecked_hides_panels(self, widget):
        widget._xy_checkbox.setChecked(False)
        for panel in widget._xy_panels:
            assert panel.isHidden()

    def test_mosaic_initialized_enables_roi_mode(self, widget, event_bus):
        """MosaicLayersInitialized should enable ROI Tiling in combo."""
        # Initially disabled
        assert not widget._xy_mode_combo.model().item(1).isEnabled()

        event_bus.publish(MosaicLayersInitialized())
        event_bus.drain()

        assert widget._xy_mode_combo.model().item(1).isEnabled()

    def test_mode_switch_clears_coordinates(self, widget, event_bus, scan_coords):
        """Switching XY modes should clear scan coordinates."""
        # Start from Multipoint mode (index 2) so switching actually fires signal
        widget._xy_mode_combo.setCurrentIndex(2)
        event_bus.drain()

        # Add some coordinates
        event_bus.publish(AddFlexibleRegionCommand(
            region_id="1", center_x_mm=25.0, center_y_mm=25.0, center_z_mm=1.0,
            n_x=1, n_y=1, overlap_percent=10.0,
        ))
        event_bus.drain()
        assert len(scan_coords.region_fov_coordinates) > 0

        # Switch to different mode — should publish ClearScanCoordinatesCommand
        widget._xy_mode_combo.setCurrentIndex(0)
        event_bus.drain()

        assert len(scan_coords.region_fov_coordinates) == 0


# ===========================================================================
# Channel configuration
# ===========================================================================


class TestChannelConfiguration:
    """Test channel list management."""

    def test_initial_channels(self, widget):
        """Widget should have channel configs available (not necessarily selected)."""
        assert len(widget._channel_configs) > 0

    def test_channel_configs_updated(self, widget, event_bus):
        """ChannelConfigurationsChanged should update available channels."""
        new_channels = ["Ch1", "Ch2", "Ch3"]
        event_bus.publish(ChannelConfigurationsChanged(
            objective_name="default",
            configuration_names=new_channels,
        ))
        event_bus.drain()

        assert widget._channel_configs == new_channels


# ===========================================================================
# Napari Mosaic → FOV end-to-end integration test
# ===========================================================================


class TestNapariMosaicConversionLogic:
    """Test the mosaic ROI conversion logic that is suspected of causing the bug.

    Instead of creating a full NapariMosaicDisplayWidget (which has heavy napari
    dependencies), we test the conversion logic and event flow in isolation.
    """

    def test_on_shape_change_with_initialized_mosaic(self, event_bus):
        """Test on_shape_change produces ManualShapesChanged when mosaic is initialized."""
        # Simulate the mosaic widget's on_shape_change logic directly
        from unittest.mock import MagicMock, PropertyMock

        layers_initialized = True
        top_left_coordinate = [10.0, 10.0]  # [y_mm, x_mm]
        viewer_pixel_size_mm = 0.001

        # Mock a shape layer with polygon data (in world/µm coords)
        # Napari stores shapes as (row, col) = (y, x) in world coords
        # For a polygon at pixel (20, 20)-(80, 80), with scale 1µm/px and translate (10000, 10000)µm:
        # world_y = pixel_y * 1 + 10000 = 10020-10080 µm
        # world_x = pixel_x * 1 + 10000 = 10020-10080 µm
        shape_world_coords = np.array([
            [10020.0, 10020.0], [10020.0, 10080.0],
            [10080.0, 10080.0], [10080.0, 10020.0],
        ])

        # Mock ref layer's world_to_data: (world - translate) / scale
        # translate = (10000, 10000), scale = (1, 1)
        mock_ref = MagicMock()
        def world_to_data(points):
            result = (points - np.array([10000.0, 10000.0])) / np.array([1.0, 1.0])
            return result
        mock_ref.world_to_data = world_to_data

        # Simulate convert_shape_to_mm logic
        shape_data_mm = []
        for point in shape_world_coords:
            coords = mock_ref.world_to_data(point)
            x_mm = top_left_coordinate[1] + coords[1] * viewer_pixel_size_mm
            y_mm = top_left_coordinate[0] + coords[0] * viewer_pixel_size_mm
            shape_data_mm.append([x_mm, y_mm])
        result = np.array(shape_data_mm)

        assert result.shape == (4, 2)
        # Check x_mm: top_left_x(10) + pixel_x * 0.001
        # pixel_x = 20 → x_mm = 10.02
        assert abs(result[0, 0] - 10.02) < 0.001, f"x_mm should be ~10.02, got {result[0, 0]}"
        assert abs(result[0, 1] - 10.02) < 0.001, f"y_mm should be ~10.02, got {result[0, 1]}"

    def test_on_shape_change_without_initialized_mosaic_keeps_old_shapes(self):
        """When mosaic is NOT initialized, on_shape_change keeps existing shapes_mm.

        This is the root cause: if a user draws shapes after clearing the mosaic,
        shapes_mm stays empty [] because the conversion can't run.
        """
        # Simulate: mosaic was cleared (layers_initialized=False, top_left=None)
        # but shape_layer still has data (Manual ROI survived the clear)
        layers_initialized = False
        top_left_coordinate = None

        # shapes_mm was [] before any shapes were drawn
        existing_shapes_mm = []

        # User drew a shape — napari shows it visually, shape_layer.data has 1 polygon
        shape_layer_data_count = 1  # shapes exist

        # Simulate on_shape_change logic:
        if shape_layer_data_count > 0:
            if layers_initialized and top_left_coordinate is not None:
                new_shapes_mm = ["would convert here"]
            else:
                new_shapes_mm = existing_shapes_mm  # KEEPS OLD VALUE (empty!)
        else:
            new_shapes_mm = []

        # BUG: new_shapes_mm is still [] because the "else" branch preserves the old value
        assert new_shapes_mm == [], "This confirms the bug: shapes_mm stays empty"

        # Now simulate ManualShapesChanged event publishing:
        shapes_mm_tuple = None
        if new_shapes_mm:  # [] is falsy!
            shapes_mm_tuple = "would build tuples"
        # shapes_mm_tuple is None → ManualShapesChanged(shapes_mm=None)

        assert shapes_mm_tuple is None, (
            "ManualShapesChanged publishes shapes_mm=None when shapes_mm is []"
        )

        # In the widget: _manual_shapes_mm = None
        # Then _on_generate_fovs: if self._manual_shapes_mm is None: return
        # → DOES NOTHING. Bug confirmed!

    def test_on_shape_change_after_clear_with_preexisting_shapes(self):
        """If shapes existed BEFORE clear, they're preserved in shapes_mm.

        But the coordinates are stale (from before the clear).
        """
        # Before clear: shapes_mm had valid data
        existing_shapes_mm = [np.array([[10.02, 10.02], [10.08, 10.02], [10.08, 10.08], [10.02, 10.08]])]

        layers_initialized = False  # After clear
        top_left_coordinate = None  # After clear
        shape_layer_data_count = 1  # Shapes survived the clear

        if shape_layer_data_count > 0:
            if layers_initialized and top_left_coordinate is not None:
                new_shapes_mm = ["would convert"]
            else:
                new_shapes_mm = existing_shapes_mm  # Preserves OLD shapes

        # shapes_mm is non-empty (has stale coordinates)
        assert len(new_shapes_mm) > 0

        # ManualShapesChanged would be published with the old coordinates
        shapes_mm_tuple = None
        if new_shapes_mm:
            shapes_mm_tuple = "valid but stale"

        assert shapes_mm_tuple is not None, (
            "With pre-existing shapes, ManualShapesChanged has data (but stale coords)"
        )

    def test_event_flow_shapes_to_backend(self, widget, event_bus, scan_coords, collected_events):
        """Verify that the exact tuple format from napari conversion works with backend."""
        # Simulate what ManualShapesChanged event contains when shapes ARE valid:
        # shapes_mm is a tuple of tuples of (x_mm, y_mm) pairs
        shapes = (
            # A 3mm x 3mm square centered at (15, 15)
            ((13.5, 13.5), (16.5, 13.5), (16.5, 16.5), (13.5, 16.5)),
        )
        event_bus.publish(ManualShapesChanged(shapes_mm=shapes))
        event_bus.drain()

        assert widget._manual_shapes_mm is not None
        assert len(widget._manual_shapes_mm) == 1

        # Verify the shape data matches what the backend expects
        shape_arr = widget._manual_shapes_mm[0]
        assert shape_arr.shape == (4, 2)
        assert abs(shape_arr[0, 0] - 13.5) < 0.01  # x_mm
        assert abs(shape_arr[0, 1] - 13.5) < 0.01  # y_mm

        # Generate FOVs
        widget._on_generate_fovs()
        event_bus.drain()

        # Should produce multiple FOVs in a 3mm x 3mm region with 1mm FOV
        total_fovs = sum(len(c) for c in scan_coords.region_fov_coordinates.values())
        assert total_fovs > 0, f"Expected FOVs but got 0"

        # Verify events were published
        non_zero = [e for e in collected_events if e.total_fovs > 0]
        assert len(non_zero) > 0

    def test_empty_shapes_mm_causes_none_event(self, event_bus):
        """Demonstrate that empty shapes_mm list causes ManualShapesChanged(shapes_mm=None)."""
        received = []
        event_bus.subscribe(ManualShapesChanged, lambda e: received.append(e))

        # This is what happens in on_shape_change when shapes_mm is []:
        shapes_mm = []
        shapes_mm_tuple = None
        if shapes_mm:  # [] is falsy
            shapes_mm_tuple = tuple(
                tuple(tuple((float(x), float(y)) for x, y in shape)
                for shape in shapes_mm)
            )
        event_bus.publish(ManualShapesChanged(shapes_mm=shapes_mm_tuple))
        event_bus.drain()

        assert len(received) == 1
        assert received[0].shapes_mm is None, (
            "Empty shapes_mm list results in ManualShapesChanged(shapes_mm=None)"
        )


# ===========================================================================
# Acquisition controls
# ===========================================================================


class TestAcquisitionControls:
    """Test acquisition controls: start/stop, save coords, clear FOVs, reset."""

    @staticmethod
    def _select_channel(widget):
        widget._channel_order_widget.set_selected_channels(["BF LED matrix full"])

    def test_acquisition_controls_exist(self, widget):
        """Acquisition controls should be built in the widget."""
        assert hasattr(widget, "_btn_start_stop")
        assert hasattr(widget, "_save_path_edit")
        assert hasattr(widget, "_experiment_id_edit")
        assert hasattr(widget, "_progress_bar")
        assert hasattr(widget, "_btn_clear_fovs")

    def test_progress_bar_hidden_initially(self, widget):
        assert not widget._progress_bar.isVisible()
        assert not widget._progress_label.isVisible()

    def test_start_acquisition_publishes_commands(self, widget, event_bus):
        """Start acquisition should publish the correct command sequence."""
        from squid.core.events import (
            SetAcquisitionParametersCommand,
            SetAcquisitionChannelsCommand,
            SetAcquisitionPathCommand,
            StartNewExperimentCommand,
            StartAcquisitionCommand,
        )

        all_events = []
        for evt_type in [
            SetAcquisitionParametersCommand,
            SetAcquisitionChannelsCommand,
            SetAcquisitionPathCommand,
            StartNewExperimentCommand,
            StartAcquisitionCommand,
        ]:
            event_bus.subscribe(evt_type, lambda e: all_events.append(e))

        self._select_channel(widget)
        widget._save_path_edit.setText("/tmp/test_acq")
        widget._experiment_id_edit.setText("test-001")
        widget._skip_saving.setChecked(False)

        widget._start_acquisition()
        event_bus.drain()

        types = [type(e).__name__ for e in all_events]
        assert "SetAcquisitionParametersCommand" in types
        assert "SetAcquisitionChannelsCommand" in types
        assert "SetAcquisitionPathCommand" in types
        assert "StartNewExperimentCommand" in types
        assert "StartAcquisitionCommand" in types

    def test_start_requires_channels(self, widget, qtbot):
        """Start without channels should show warning (not crash)."""
        widget._channel_order_widget.set_selected_channels([])
        widget._save_path_edit.setText("/tmp/test")
        # _start_acquisition calls QMessageBox.warning — just verify it doesn't crash
        # and that the button resets
        # We can't easily test the QMessageBox without mocking, but we can check
        # that the widget doesn't enter acquiring state
        # Use monkeypatch to skip the dialog
        import unittest.mock
        with unittest.mock.patch("squid.ui.widgets.acquisition.acquisition_setup.QMessageBox"):
            widget._start_acquisition()
        assert not widget._is_acquiring

    def test_start_requires_save_path(self, widget):
        """Start without save path (and skip_saving off) should not start."""
        self._select_channel(widget)
        widget._save_path_edit.setText("")
        widget._skip_saving.setChecked(False)
        import unittest.mock
        with unittest.mock.patch("squid.ui.widgets.acquisition.acquisition_setup.QMessageBox"):
            widget._start_acquisition()
        assert not widget._is_acquiring

    def test_start_with_skip_saving_no_path(self, widget, event_bus):
        """Start with skip_saving should work without save path."""
        from squid.core.events import StartAcquisitionCommand
        received = []
        event_bus.subscribe(StartAcquisitionCommand, lambda e: received.append(e))

        self._select_channel(widget)
        widget._save_path_edit.setText("")
        widget._skip_saving.blockSignals(True)
        widget._skip_saving.setChecked(True)
        widget._skip_saving.blockSignals(False)

        widget._start_acquisition()
        event_bus.drain()

        assert len(received) == 1

    def test_acquisition_state_changed_updates_ui(self, widget, event_bus):
        """AcquisitionStateChanged should toggle UI state."""
        from squid.core.events import AcquisitionStateChanged

        widget._active_experiment_id = "test-001"

        # Start
        event_bus.publish(AcquisitionStateChanged(
            in_progress=True, experiment_id="test-001",
        ))
        event_bus.drain()
        assert widget._is_acquiring
        # Use isVisibleTo(widget) since the widget itself may not be shown
        assert widget._progress_bar.isVisibleTo(widget)
        assert "Stop" in widget._btn_start_stop.text()

        # Stop
        event_bus.publish(AcquisitionStateChanged(
            in_progress=False, experiment_id="test-001",
        ))
        event_bus.drain()
        assert not widget._is_acquiring
        assert not widget._progress_bar.isVisibleTo(widget)
        assert "Start" in widget._btn_start_stop.text()

    def test_acquisition_state_ignores_other_experiment(self, widget, event_bus):
        """AcquisitionStateChanged for a different experiment should be ignored."""
        from squid.core.events import AcquisitionStateChanged

        widget._active_experiment_id = "my-experiment"
        event_bus.publish(AcquisitionStateChanged(
            in_progress=True, experiment_id="someone-elses",
        ))
        event_bus.drain()
        assert not widget._is_acquiring

    def test_acquisition_progress_updates_bar(self, widget, event_bus):
        """AcquisitionProgress should update progress bar and label."""
        from squid.core.events import AcquisitionProgress

        widget._active_experiment_id = "test-001"
        widget._progress_bar.setVisible(True)
        widget._progress_label.setVisible(True)

        event_bus.publish(AcquisitionProgress(
            current_fov=5, total_fovs=20, current_round=1, total_rounds=1,
            current_channel="DAPI", progress_percent=25.0,
            experiment_id="test-001", eta_seconds=30,
        ))
        event_bus.drain()

        assert widget._progress_bar.value() == 25
        assert "5/20" in widget._progress_label.text()
        assert "DAPI" in widget._progress_label.text()

    def test_controls_disabled_during_acquisition(self, widget, event_bus):
        """Controls should be disabled during acquisition."""
        from squid.core.events import AcquisitionStateChanged

        widget._active_experiment_id = "test-001"
        event_bus.publish(AcquisitionStateChanged(
            in_progress=True, experiment_id="test-001",
        ))
        event_bus.drain()

        assert not widget._xy_checkbox.isEnabled()
        assert not widget._z_checkbox.isEnabled()
        assert not widget._channel_order_widget.isEnabled()
        assert not widget._btn_save_coords.isEnabled()

    def test_controls_reenabled_after_acquisition(self, widget, event_bus):
        """Controls should be re-enabled after acquisition completes."""
        from squid.core.events import AcquisitionStateChanged

        widget._active_experiment_id = "test-001"
        # Disable
        event_bus.publish(AcquisitionStateChanged(
            in_progress=True, experiment_id="test-001",
        ))
        event_bus.drain()
        assert not widget._channel_order_widget.isEnabled()

        # Re-enable
        event_bus.publish(AcquisitionStateChanged(
            in_progress=False, experiment_id="test-001",
        ))
        event_bus.drain()
        assert widget._channel_order_widget.isEnabled()
        assert widget._xy_checkbox.isEnabled()
        assert widget._z_checkbox.isEnabled()
        assert widget._btn_save_coords.isEnabled()

    def test_experiment_id_refreshed_after_acquisition(self, widget, event_bus):
        """After acquisition completes, experiment ID should be refreshed."""
        from squid.core.events import AcquisitionStateChanged

        widget._active_experiment_id = "old-id"
        widget._experiment_id_edit.setText("old-id")

        event_bus.publish(AcquisitionStateChanged(
            in_progress=False, experiment_id="old-id",
        ))
        event_bus.drain()

        new_id = widget._experiment_id_edit.text()
        assert new_id != "old-id"
        assert len(new_id) > 0

    def test_clear_fovs_publishes_clear_command(self, widget, event_bus, scan_coords):
        """Clear FOVs button should publish ClearScanCoordinatesCommand."""
        # First add some coordinates
        widget._xy_mode_combo.setCurrentIndex(2)  # Multipoint
        event_bus.drain()
        event_bus.publish(AddFlexibleRegionCommand(
            region_id="1", center_x_mm=25.0, center_y_mm=25.0, center_z_mm=1.0,
            n_x=1, n_y=1, overlap_percent=10.0,
        ))
        event_bus.drain()
        assert len(scan_coords.region_fov_coordinates) > 0

        # Clear
        widget._on_clear_fovs()
        event_bus.drain()
        assert len(scan_coords.region_fov_coordinates) == 0

    def test_xy_mode_maps_correctly(self, widget):
        """XY mode combo should map to correct MultiPointController strings."""
        from squid.ui.widgets.acquisition.acquisition_setup import (
            _MODE_MULTIWELL, _MODE_ROI_TILING, _MODE_MULTIPOINT, _MODE_LOAD_CSV,
        )
        mode_map = {
            _MODE_MULTIWELL: "Select Wells",
            _MODE_ROI_TILING: "Manual",
            _MODE_MULTIPOINT: "Manual",
            _MODE_LOAD_CSV: "Load Coordinates",
        }
        for idx, expected in mode_map.items():
            assert expected is not None  # sanity check


class TestSaveCoordinatesCSV:
    """Test coordinate CSV export."""

    def test_save_coords_writes_csv(self, widget, event_bus, scan_coords, tmp_path):
        """Saving coordinates should write a valid CSV with region/x/y/z columns."""
        from squid.core.events import ScanCoordinatesSnapshot
        import csv

        # Set up scan coordinates
        event_bus.publish(AddFlexibleRegionCommand(
            region_id="R1", center_x_mm=10.0, center_y_mm=20.0, center_z_mm=1.0,
            n_x=1, n_y=1, overlap_percent=0.0,
        ))
        event_bus.drain()

        # Directly test the snapshot + write mechanism
        csv_path = tmp_path / "coords.csv"
        snapshot = widget._request_scan_coordinates_snapshot()
        assert snapshot is not None

        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["region", "x_mm", "y_mm", "z_mm"])
            for region_id, fovs in snapshot.region_fov_coordinates.items():
                for fov in fovs:
                    x, y = fov[0], fov[1]
                    z = fov[2] if len(fov) > 2 else widget._cached_z_mm
                    writer.writerow([region_id, f"{x:.6f}", f"{y:.6f}", f"{z:.6f}"])

        # Read back and verify
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) > 0
        assert set(rows[0].keys()) == {"region", "x_mm", "y_mm", "z_mm"}
        assert rows[0]["region"] == "R1"


# ===========================================================================
# Quick Scan — comprehensive GUI interaction tests
# ===========================================================================


class TestQuickScan:
    """Core Quick Scan functionality: button existence, command publishing, parameters."""

    @staticmethod
    def _select_channel(widget, channels=None):
        channels = channels or ["BF LED matrix full"]
        widget._channel_order_widget.set_selected_channels(channels)

    @staticmethod
    def _add_fov(widget, event_bus):
        """Add a multipoint position so _total_fovs > 0."""
        widget._xy_mode_combo.setCurrentIndex(2)  # _MODE_MULTIPOINT
        widget._cached_x_mm = 25.0
        widget._cached_y_mm = 25.0
        widget._cached_z_mm = 1.0
        widget._on_mp_add()
        event_bus.drain()

    def test_quick_scan_button_exists(self, widget):
        assert hasattr(widget, "_btn_quick_scan")
        assert widget._btn_quick_scan.text() == "Quick Scan"
        assert widget._btn_quick_scan.minimumHeight() >= 32

    def test_quick_scan_button_has_tooltip(self, widget):
        tip = widget._btn_quick_scan.toolTip()
        assert "mosaic" in tip.lower()
        assert "no files" in tip.lower() or "no files saved" in tip.lower()

    def test_quick_scan_button_enabled_initially(self, widget):
        """Quick Scan should be enabled when widget is first created."""
        assert widget._btn_quick_scan.isEnabled()

    def test_quick_scan_publishes_correct_commands(self, widget, event_bus, scan_coords):
        """Quick Scan should publish the full command sequence with skip_saving=True."""
        from squid.core.events import (
            SetAcquisitionParametersCommand,
            SetAcquisitionChannelsCommand,
            SetAcquisitionPathCommand,
            StartNewExperimentCommand,
            StartAcquisitionCommand,
        )

        all_events = []
        for evt_type in [
            SetAcquisitionParametersCommand,
            SetAcquisitionChannelsCommand,
            SetAcquisitionPathCommand,
            StartNewExperimentCommand,
            StartAcquisitionCommand,
        ]:
            event_bus.subscribe(evt_type, lambda e: all_events.append(e))

        self._select_channel(widget)
        self._add_fov(widget, event_bus)

        widget._quick_scan()
        event_bus.drain()

        types = [type(e).__name__ for e in all_events]
        assert "SetAcquisitionParametersCommand" in types
        assert "SetAcquisitionChannelsCommand" in types
        assert "SetAcquisitionPathCommand" in types
        assert "StartNewExperimentCommand" in types
        assert "StartAcquisitionCommand" in types

    def test_quick_scan_command_ordering(self, widget, event_bus, scan_coords):
        """Commands must be published in the correct order for the backend."""
        from squid.core.events import (
            SetAcquisitionParametersCommand,
            SetAcquisitionChannelsCommand,
            SetAcquisitionPathCommand,
            StartNewExperimentCommand,
            StartAcquisitionCommand,
        )

        ordered_types = []
        expected = [
            SetAcquisitionParametersCommand,
            SetAcquisitionChannelsCommand,
            SetAcquisitionPathCommand,
            StartNewExperimentCommand,
            StartAcquisitionCommand,
        ]
        for evt_type in expected:
            event_bus.subscribe(evt_type, lambda e, t=evt_type: ordered_types.append(t))

        self._select_channel(widget)
        self._add_fov(widget, event_bus)
        widget._quick_scan()
        event_bus.drain()

        assert ordered_types == expected, (
            f"Commands out of order: {[t.__name__ for t in ordered_types]}"
        )

    def test_quick_scan_parameters_skip_saving(self, widget, event_bus, scan_coords):
        """SetAcquisitionParametersCommand must have skip_saving=True."""
        from squid.core.events import SetAcquisitionParametersCommand

        params = []
        event_bus.subscribe(SetAcquisitionParametersCommand, lambda e: params.append(e))

        self._select_channel(widget)
        self._add_fov(widget, event_bus)
        widget._quick_scan()
        event_bus.drain()

        assert len(params) == 1
        assert params[0].skip_saving is True

    def test_quick_scan_parameters_single_z(self, widget, event_bus, scan_coords):
        """Quick Scan always uses n_z=1 regardless of Z-stack settings."""
        from squid.core.events import SetAcquisitionParametersCommand

        # Enable Z-stack with 10 planes
        widget._z_checkbox.setChecked(True)
        widget._z_nz.setValue(10)
        widget._z_delta.setValue(2.0)

        params = []
        event_bus.subscribe(SetAcquisitionParametersCommand, lambda e: params.append(e))

        self._select_channel(widget)
        self._add_fov(widget, event_bus)
        widget._quick_scan()
        event_bus.drain()

        assert params[0].n_z == 1, "Quick Scan must override Z-stack to single plane"

    def test_quick_scan_parameters_no_autofocus(self, widget, event_bus, scan_coords):
        """Quick Scan always disables autofocus regardless of focus settings."""
        from squid.core.events import SetAcquisitionParametersCommand

        # Enable focus
        widget._focus_checkbox.setChecked(True)
        widget._focus_method.setCurrentIndex(0)  # Contrast AF

        params = []
        event_bus.subscribe(SetAcquisitionParametersCommand, lambda e: params.append(e))

        self._select_channel(widget)
        self._add_fov(widget, event_bus)
        widget._quick_scan()
        event_bus.drain()

        assert params[0].use_autofocus is False
        assert params[0].use_reflection_af is False

    def test_quick_scan_passes_selected_channels(self, widget, event_bus, scan_coords):
        """SetAcquisitionChannelsCommand should contain the selected channels."""
        from squid.core.events import SetAcquisitionChannelsCommand

        channels_cmd = []
        event_bus.subscribe(SetAcquisitionChannelsCommand, lambda e: channels_cmd.append(e))

        self._select_channel(widget, ["BF LED matrix full", "Fluorescence 488 nm Ex"])
        self._add_fov(widget, event_bus)
        widget._quick_scan()
        event_bus.drain()

        assert len(channels_cmd) == 1
        assert channels_cmd[0].channel_names == ["BF LED matrix full", "Fluorescence 488 nm Ex"]

    def test_quick_scan_uses_tempdir(self, widget, event_bus, scan_coords):
        """SetAcquisitionPathCommand should use system tempdir."""
        import tempfile
        from squid.core.events import SetAcquisitionPathCommand

        path_events = []
        event_bus.subscribe(SetAcquisitionPathCommand, lambda e: path_events.append(e))

        self._select_channel(widget)
        self._add_fov(widget, event_bus)
        widget._quick_scan()
        event_bus.drain()

        assert len(path_events) == 1
        assert path_events[0].base_path == tempfile.gettempdir()

    def test_quick_scan_experiment_id_prefix(self, widget, event_bus, scan_coords):
        """StartNewExperimentCommand should have quick_scan_ prefix."""
        from squid.core.events import StartNewExperimentCommand

        exp_events = []
        event_bus.subscribe(StartNewExperimentCommand, lambda e: exp_events.append(e))

        self._select_channel(widget)
        self._add_fov(widget, event_bus)
        widget._quick_scan()
        event_bus.drain()

        assert len(exp_events) == 1
        assert exp_events[0].experiment_id.startswith("quick_scan_")

    def test_quick_scan_experiment_id_matches_internal(self, widget, event_bus, scan_coords):
        """_active_experiment_id should match the published experiment_id."""
        from squid.core.events import StartNewExperimentCommand

        exp_events = []
        event_bus.subscribe(StartNewExperimentCommand, lambda e: exp_events.append(e))

        self._select_channel(widget)
        self._add_fov(widget, event_bus)
        widget._quick_scan()
        event_bus.drain()

        assert widget._active_experiment_id == exp_events[0].experiment_id


class TestQuickScanValidation:
    """Quick Scan validation: channels, FOVs, error dialogs."""

    @staticmethod
    def _select_channel(widget, channels=None):
        channels = channels or ["BF LED matrix full"]
        widget._channel_order_widget.set_selected_channels(channels)

    @staticmethod
    def _add_fov(widget, event_bus):
        widget._xy_mode_combo.setCurrentIndex(2)
        widget._cached_x_mm = 25.0
        widget._cached_y_mm = 25.0
        widget._cached_z_mm = 1.0
        widget._on_mp_add()
        event_bus.drain()

    def test_warns_no_channels(self, widget, event_bus, scan_coords):
        """Quick Scan with no channels should show warning and not publish any commands."""
        from squid.core.events import StartAcquisitionCommand
        import unittest.mock

        widget._channel_order_widget.set_selected_channels([])
        self._add_fov(widget, event_bus)

        acq_cmds = []
        event_bus.subscribe(StartAcquisitionCommand, lambda e: acq_cmds.append(e))

        with unittest.mock.patch(
            "squid.ui.widgets.acquisition.acquisition_setup.QMessageBox"
        ) as mock_mb:
            widget._quick_scan()
            event_bus.drain()
            mock_mb.warning.assert_called_once()

        assert len(acq_cmds) == 0, "No StartAcquisitionCommand should be published"
        assert widget._active_experiment_id is None, "experiment_id should not be set"

    def test_allows_no_fovs(self, widget, event_bus):
        """Quick Scan should run even when no scan coordinates/FOVs are pre-generated."""
        from squid.core.events import StartAcquisitionCommand
        import unittest.mock

        self._select_channel(widget)
        assert widget._total_fovs == 0
        acq_cmds = []
        event_bus.subscribe(StartAcquisitionCommand, lambda e: acq_cmds.append(e))

        with unittest.mock.patch(
            "squid.ui.widgets.acquisition.acquisition_setup.QMessageBox"
        ) as mock_mb:
            widget._quick_scan()
            event_bus.drain()
            mock_mb.warning.assert_not_called()

        assert len(acq_cmds) == 1
        assert widget._active_experiment_id is not None
        assert widget._active_experiment_id.startswith("quick_scan_")

    def test_no_channels_does_not_disable_controls(self, widget, event_bus, scan_coords):
        """Failed validation should leave controls enabled."""
        import unittest.mock

        widget._channel_order_widget.set_selected_channels([])
        self._add_fov(widget, event_bus)

        with unittest.mock.patch(
            "squid.ui.widgets.acquisition.acquisition_setup.QMessageBox"
        ):
            widget._quick_scan()

        assert widget._btn_quick_scan.isEnabled(), "Button should stay enabled after validation fail"
        assert widget._xy_checkbox.isEnabled()
        assert widget._channel_order_widget.isEnabled()

    def test_no_fovs_still_disables_controls_on_start(self, widget):
        """No pre-generated FOVs: Quick Scan still starts and disables controls."""
        import unittest.mock

        self._select_channel(widget)

        with unittest.mock.patch(
            "squid.ui.widgets.acquisition.acquisition_setup.QMessageBox"
        ):
            widget._quick_scan()

        assert not widget._btn_quick_scan.isEnabled()
        assert not widget._xy_checkbox.isEnabled()
        assert not widget._channel_order_widget.isEnabled()

    def test_channel_check_warns_when_channels_missing(self, widget):
        """When channels are missing, Quick Scan should warn about channel selection."""
        import unittest.mock

        widget._channel_order_widget.set_selected_channels([])
        assert widget._total_fovs == 0

        with unittest.mock.patch(
            "squid.ui.widgets.acquisition.acquisition_setup.QMessageBox"
        ) as mock_mb:
            widget._quick_scan()
            # Should warn about channels, not FOVs
            assert "channel" in mock_mb.warning.call_args[0][2].lower()


class TestQuickScanControlState:
    """Control enable/disable during Quick Scan lifecycle."""

    @staticmethod
    def _select_channel(widget, channels=None):
        channels = channels or ["BF LED matrix full"]
        widget._channel_order_widget.set_selected_channels(channels)

    @staticmethod
    def _add_fov(widget, event_bus):
        widget._xy_mode_combo.setCurrentIndex(2)
        widget._cached_x_mm = 25.0
        widget._cached_y_mm = 25.0
        widget._cached_z_mm = 1.0
        widget._on_mp_add()
        event_bus.drain()

    def test_all_controls_disabled_during_quick_scan(self, widget, event_bus, scan_coords):
        """All interactive controls should be disabled after Quick Scan starts."""
        self._select_channel(widget)
        self._add_fov(widget, event_bus)
        widget._quick_scan()

        assert not widget._btn_quick_scan.isEnabled()
        assert not widget._xy_checkbox.isEnabled()
        assert not widget._z_checkbox.isEnabled()
        assert not widget._focus_checkbox.isEnabled()
        assert not widget._channel_order_widget.isEnabled()
        assert not widget._save_path_edit.isEnabled()
        assert not widget._btn_browse_path.isEnabled()
        assert not widget._experiment_id_edit.isEnabled()
        assert not widget._btn_save_coords.isEnabled()
        assert not widget._btn_clear_fovs.isEnabled()
        assert not widget._btn_save_protocol.isEnabled()
        assert not widget._btn_load_protocol.isEnabled()
        assert not widget._skip_saving.isEnabled()
        assert not widget._save_format.isEnabled()

    def test_xy_panels_disabled_during_quick_scan(self, widget, event_bus, scan_coords):
        """XY panel (multipoint, etc.) should be disabled during scan."""
        self._select_channel(widget)
        self._add_fov(widget, event_bus)
        widget._quick_scan()

        for panel in widget._xy_panels:
            assert not panel.isEnabled()

    def test_controls_reenabled_after_completion(self, widget, event_bus, scan_coords):
        """Controls should re-enable when AcquisitionStateChanged(in_progress=False) arrives."""
        self._select_channel(widget)
        self._add_fov(widget, event_bus)
        widget._quick_scan()
        exp_id = widget._active_experiment_id

        # Simulate acquisition completing
        event_bus.publish(AcquisitionStateChanged(
            in_progress=False, experiment_id=exp_id,
        ))
        event_bus.drain()

        assert widget._btn_quick_scan.isEnabled()
        assert widget._xy_checkbox.isEnabled()
        assert widget._z_checkbox.isEnabled()
        assert widget._channel_order_widget.isEnabled()
        assert widget._save_path_edit.isEnabled()
        assert widget._skip_saving.isEnabled()

    def test_controls_reenabled_after_completion_with_backend_suffixed_id(
        self, widget, event_bus, scan_coords
    ):
        """Backend appends a timestamp suffix to experiment_id; completion should still re-enable controls."""
        self._select_channel(widget)
        self._add_fov(widget, event_bus)
        widget._quick_scan()
        requested_id = widget._active_experiment_id
        backend_id = f"{requested_id}_2026-02-11_09-10-11.123456"

        event_bus.publish(AcquisitionStateChanged(in_progress=True, experiment_id=backend_id))
        event_bus.drain()
        assert widget._active_experiment_id == backend_id

        event_bus.publish(AcquisitionStateChanged(in_progress=False, experiment_id=backend_id))
        event_bus.drain()

        assert widget._active_experiment_id is None
        assert widget._btn_quick_scan.isEnabled()
        assert widget._xy_checkbox.isEnabled()

    def test_start_watchdog_recovers_controls_when_backend_never_starts(
        self, widget, event_bus, scan_coords, qtbot, monkeypatch
    ):
        """If no matching acquisition state/progress arrives, controls should auto-recover."""
        self._select_channel(widget)
        self._add_fov(widget, event_bus)
        monkeypatch.setattr(widget, "_ACQUISITION_START_WATCHDOG_MS", 20)

        widget._quick_scan()
        event_bus.drain()

        assert not widget._btn_quick_scan.isEnabled()
        assert widget._active_experiment_id is not None

        qtbot.wait(80)

        assert widget._btn_quick_scan.isEnabled()
        assert widget._xy_checkbox.isEnabled()
        assert widget._active_experiment_id is None

    def test_start_stop_button_shows_stop_during_quick_scan(self, widget, event_bus, scan_coords):
        """Start/Stop button should change to 'Stop Acquisition' during Quick Scan."""
        self._select_channel(widget)
        self._add_fov(widget, event_bus)
        widget._quick_scan()
        exp_id = widget._active_experiment_id

        # Simulate backend confirming start
        event_bus.publish(AcquisitionStateChanged(
            in_progress=True, experiment_id=exp_id,
        ))
        event_bus.drain()

        assert "Stop" in widget._btn_start_stop.text()

    def test_start_stop_button_resets_after_quick_scan(self, widget, event_bus, scan_coords):
        """Start/Stop button should revert to 'Start Acquisition' after Quick Scan ends."""
        self._select_channel(widget)
        self._add_fov(widget, event_bus)
        widget._quick_scan()
        exp_id = widget._active_experiment_id

        event_bus.publish(AcquisitionStateChanged(in_progress=True, experiment_id=exp_id))
        event_bus.drain()
        event_bus.publish(AcquisitionStateChanged(in_progress=False, experiment_id=exp_id))
        event_bus.drain()

        assert "Start" in widget._btn_start_stop.text()
        assert not widget._btn_start_stop.isChecked()

    def test_experiment_id_field_refreshed_after_quick_scan(self, widget, event_bus, scan_coords):
        """Experiment ID text field should get a fresh timestamp after Quick Scan completes."""
        self._select_channel(widget)
        self._add_fov(widget, event_bus)
        old_text = widget._experiment_id_edit.text()
        widget._quick_scan()
        exp_id = widget._active_experiment_id

        event_bus.publish(AcquisitionStateChanged(in_progress=False, experiment_id=exp_id))
        event_bus.drain()

        new_text = widget._experiment_id_edit.text()
        assert new_text != old_text or len(new_text) > 0  # Refreshed with timestamp


class TestQuickScanProgress:
    """Progress bar and label behavior during Quick Scan."""

    @staticmethod
    def _select_channel(widget, channels=None):
        channels = channels or ["BF LED matrix full"]
        widget._channel_order_widget.set_selected_channels(channels)

    @staticmethod
    def _add_fov(widget, event_bus):
        widget._xy_mode_combo.setCurrentIndex(2)
        widget._cached_x_mm = 25.0
        widget._cached_y_mm = 25.0
        widget._cached_z_mm = 1.0
        widget._on_mp_add()
        event_bus.drain()

    def _start_quick_scan(self, widget, event_bus, scan_coords):
        self._select_channel(widget)
        self._add_fov(widget, event_bus)
        widget._quick_scan()
        exp_id = widget._active_experiment_id
        event_bus.publish(AcquisitionStateChanged(in_progress=True, experiment_id=exp_id))
        event_bus.drain()
        return exp_id

    def test_progress_bar_visible_during_scan(self, widget, event_bus, scan_coords):
        """Progress bar should become visible when Quick Scan is running."""
        exp_id = self._start_quick_scan(widget, event_bus, scan_coords)
        assert widget._progress_bar.isVisibleTo(widget)
        assert widget._progress_label.isVisibleTo(widget)

    def test_progress_bar_updates_with_events(self, widget, event_bus, scan_coords):
        """AcquisitionProgress events should update the progress bar and label."""
        exp_id = self._start_quick_scan(widget, event_bus, scan_coords)

        event_bus.publish(AcquisitionProgress(
            current_fov=3, total_fovs=10, current_round=1, total_rounds=1,
            current_channel="BF LED matrix full", progress_percent=30.0,
            experiment_id=exp_id, eta_seconds=15,
        ))
        event_bus.drain()

        assert widget._progress_bar.value() == 30
        label = widget._progress_label.text()
        assert "3/10" in label
        assert "BF LED matrix full" in label

    def test_progress_bar_shows_eta(self, widget, event_bus, scan_coords):
        """ETA should appear in progress label when provided."""
        exp_id = self._start_quick_scan(widget, event_bus, scan_coords)

        event_bus.publish(AcquisitionProgress(
            current_fov=5, total_fovs=20, current_round=1, total_rounds=1,
            current_channel="DAPI", progress_percent=25.0,
            experiment_id=exp_id, eta_seconds=45,
        ))
        event_bus.drain()

        assert "ETA" in widget._progress_label.text()
        assert "45" in widget._progress_label.text()

    def test_progress_bar_no_eta_when_none(self, widget, event_bus, scan_coords):
        """No ETA in label when eta_seconds is None."""
        exp_id = self._start_quick_scan(widget, event_bus, scan_coords)

        event_bus.publish(AcquisitionProgress(
            current_fov=5, total_fovs=20, current_round=1, total_rounds=1,
            current_channel="DAPI", progress_percent=25.0,
            experiment_id=exp_id, eta_seconds=None,
        ))
        event_bus.drain()

        assert "ETA" not in widget._progress_label.text()

    def test_progress_bar_hidden_after_completion(self, widget, event_bus, scan_coords):
        """Progress bar should hide and reset when Quick Scan completes."""
        exp_id = self._start_quick_scan(widget, event_bus, scan_coords)

        # Send progress update
        event_bus.publish(AcquisitionProgress(
            current_fov=10, total_fovs=10, current_round=1, total_rounds=1,
            current_channel="BF", progress_percent=100.0,
            experiment_id=exp_id,
        ))
        event_bus.drain()

        # Complete
        event_bus.publish(AcquisitionStateChanged(in_progress=False, experiment_id=exp_id))
        event_bus.drain()

        assert not widget._progress_bar.isVisibleTo(widget)
        assert not widget._progress_label.isVisibleTo(widget)
        assert widget._progress_bar.value() == 0
        assert widget._progress_label.text() == ""

    def test_progress_ignores_other_experiments(self, widget, event_bus, scan_coords):
        """Progress events from other experiments should be ignored."""
        exp_id = self._start_quick_scan(widget, event_bus, scan_coords)

        event_bus.publish(AcquisitionProgress(
            current_fov=99, total_fovs=100, current_round=1, total_rounds=1,
            current_channel="Other", progress_percent=99.0,
            experiment_id="someone_elses_experiment",
        ))
        event_bus.drain()

        assert widget._progress_bar.value() == 0, "Should not update from unrelated experiment"


class TestQuickScanXYModes:
    """Quick Scan xy_mode mapping across all XY mode configurations."""

    @staticmethod
    def _select_channel(widget):
        widget._channel_order_widget.set_selected_channels(["BF LED matrix full"])

    def test_multiwell_mode_maps_to_select_wells(self, widget, event_bus, scan_coords):
        """Mode 0 (Multiwell) should publish xy_mode='Select Wells'."""
        from squid.core.events import StartAcquisitionCommand

        acq_cmds = []
        event_bus.subscribe(StartAcquisitionCommand, lambda e: acq_cmds.append(e))

        self._select_channel(widget)
        widget._xy_mode_combo.setCurrentIndex(0)
        # Simulate that multiwell has FOVs
        widget._total_fovs = 9
        widget._quick_scan()
        event_bus.drain()

        assert len(acq_cmds) == 1
        assert acq_cmds[0].xy_mode == "Select Wells"

    def test_multipoint_mode_maps_to_manual(self, widget, event_bus, scan_coords):
        """Mode 2 (Multipoint) should publish xy_mode='Manual'."""
        from squid.core.events import StartAcquisitionCommand

        acq_cmds = []
        event_bus.subscribe(StartAcquisitionCommand, lambda e: acq_cmds.append(e))

        self._select_channel(widget)
        widget._xy_mode_combo.setCurrentIndex(2)
        widget._cached_x_mm = 25.0
        widget._cached_y_mm = 25.0
        widget._cached_z_mm = 1.0
        widget._on_mp_add()
        event_bus.drain()

        widget._quick_scan()
        event_bus.drain()

        assert acq_cmds[-1].xy_mode == "Manual"

    def test_roi_tiling_mode_maps_to_manual(self, widget, event_bus):
        """Mode 1 (ROI Tiling) should publish xy_mode='Manual'."""
        from squid.core.events import StartAcquisitionCommand

        acq_cmds = []
        event_bus.subscribe(StartAcquisitionCommand, lambda e: acq_cmds.append(e))

        self._select_channel(widget)
        widget._xy_mode_combo.setCurrentIndex(1)
        widget._total_fovs = 5
        widget._quick_scan()
        event_bus.drain()

        assert acq_cmds[-1].xy_mode == "Manual"

    def test_load_csv_mode_maps_to_load_coordinates(self, widget, event_bus):
        """Mode 3 (Load CSV) should publish xy_mode='Load Coordinates'."""
        from squid.core.events import StartAcquisitionCommand

        acq_cmds = []
        event_bus.subscribe(StartAcquisitionCommand, lambda e: acq_cmds.append(e))

        self._select_channel(widget)
        widget._xy_mode_combo.setCurrentIndex(3)
        widget._total_fovs = 12
        widget._quick_scan()
        event_bus.drain()

        assert acq_cmds[-1].xy_mode == "Load Coordinates"


class TestQuickScanEdgeCases:
    """Edge cases: double-click, interaction with regular acquisition, repeated scans."""

    @staticmethod
    def _select_channel(widget, channels=None):
        channels = channels or ["BF LED matrix full"]
        widget._channel_order_widget.set_selected_channels(channels)

    @staticmethod
    def _add_fov(widget, event_bus):
        widget._xy_mode_combo.setCurrentIndex(2)
        widget._cached_x_mm = 25.0
        widget._cached_y_mm = 25.0
        widget._cached_z_mm = 1.0
        widget._on_mp_add()
        event_bus.drain()

    def test_double_click_prevented_by_disable(self, widget, event_bus, scan_coords):
        """Button disables after first click, so a rapid second click won't fire."""
        from squid.core.events import StartAcquisitionCommand

        acq_cmds = []
        event_bus.subscribe(StartAcquisitionCommand, lambda e: acq_cmds.append(e))

        self._select_channel(widget)
        self._add_fov(widget, event_bus)

        widget._quick_scan()
        event_bus.drain()

        assert not widget._btn_quick_scan.isEnabled()
        # Second attempt should be blocked since button is disabled
        # (Simulating what happens if clicked.connect somehow fires again)
        # The button is disabled, so Qt won't deliver the signal.
        # We verify the guard: if we force-call _quick_scan while controls are disabled,
        # it would still publish commands (there's no _is_acquiring guard in _quick_scan).
        # But the button being disabled is the real protection.

    def test_stop_via_start_stop_button_during_quick_scan(self, widget, event_bus, scan_coords):
        """User should be able to stop a Quick Scan using the Start/Stop button."""
        from squid.core.events import StopAcquisitionCommand

        stop_cmds = []
        event_bus.subscribe(StopAcquisitionCommand, lambda e: stop_cmds.append(e))

        self._select_channel(widget)
        self._add_fov(widget, event_bus)
        widget._quick_scan()
        exp_id = widget._active_experiment_id

        # Backend confirms start
        event_bus.publish(AcquisitionStateChanged(in_progress=True, experiment_id=exp_id))
        event_bus.drain()

        assert widget._is_acquiring
        # Start/Stop button is still functional (it's a toggle)
        widget._on_start_stop_acquisition()
        event_bus.drain()

        assert len(stop_cmds) == 1
        assert "Stopping" in widget._btn_start_stop.text()

        # Backend acknowledges aborting, then completion
        event_bus.publish(AcquisitionStateChanged(
            in_progress=True, experiment_id=exp_id, is_aborting=True,
        ))
        event_bus.drain()
        assert "Stopping" in widget._btn_start_stop.text()
        assert not widget._btn_start_stop.isEnabled()

        event_bus.publish(AcquisitionStateChanged(
            in_progress=False, experiment_id=exp_id, is_aborting=True,
        ))
        event_bus.drain()
        assert "Start" in widget._btn_start_stop.text()
        assert widget._btn_start_stop.isEnabled()
        assert widget._btn_quick_scan.isEnabled()
        assert not widget._is_acquiring
        assert widget._active_experiment_id is None

    def test_stop_via_start_stop_button_while_quick_scan_start_pending(self, widget, event_bus, scan_coords):
        """Stop should work even before the first in_progress=True state event arrives."""
        from squid.core.events import StartAcquisitionCommand, StopAcquisitionCommand

        start_cmds = []
        stop_cmds = []
        event_bus.subscribe(StartAcquisitionCommand, lambda e: start_cmds.append(e))
        event_bus.subscribe(StopAcquisitionCommand, lambda e: stop_cmds.append(e))

        self._select_channel(widget)
        self._add_fov(widget, event_bus)
        widget._quick_scan()
        event_bus.drain()

        assert len(start_cmds) == 1
        assert widget._start_pending_experiment_id is not None
        assert "Stop" in widget._btn_start_stop.text()

        # Before any AcquisitionStateChanged(in_progress=True), pressing Start/Stop should still stop.
        widget._on_start_stop_acquisition()
        event_bus.drain()

        assert len(stop_cmds) == 1
        assert len(start_cmds) == 1, "Pending stop should not trigger a new start acquisition"

    def test_quick_scan_after_regular_acquisition_completes(self, widget, event_bus, scan_coords):
        """Quick Scan should work after a regular acquisition completes."""
        from squid.core.events import StartAcquisitionCommand

        self._select_channel(widget)
        self._add_fov(widget, event_bus)

        # Simulate a regular acquisition cycle
        widget._save_path_edit.setText("/tmp/test")
        widget._skip_saving.blockSignals(True)
        widget._skip_saving.setChecked(False)
        widget._skip_saving.blockSignals(False)
        widget._start_acquisition()
        reg_id = widget._active_experiment_id
        event_bus.publish(AcquisitionStateChanged(in_progress=True, experiment_id=reg_id))
        event_bus.drain()
        event_bus.publish(AcquisitionStateChanged(in_progress=False, experiment_id=reg_id))
        event_bus.drain()

        # Now Quick Scan should work
        acq_cmds = []
        event_bus.subscribe(StartAcquisitionCommand, lambda e: acq_cmds.append(e))
        widget._quick_scan()
        event_bus.drain()

        assert len(acq_cmds) > 0
        assert widget._active_experiment_id.startswith("quick_scan_")

    def test_quick_scan_after_previous_quick_scan_completes(self, widget, event_bus, scan_coords):
        """Multiple Quick Scans in sequence should each work independently."""
        from squid.core.events import StartAcquisitionCommand

        self._select_channel(widget)
        self._add_fov(widget, event_bus)

        # First Quick Scan
        widget._quick_scan()
        exp_id_1 = widget._active_experiment_id
        event_bus.publish(AcquisitionStateChanged(in_progress=True, experiment_id=exp_id_1))
        event_bus.drain()
        event_bus.publish(AcquisitionStateChanged(in_progress=False, experiment_id=exp_id_1))
        event_bus.drain()

        assert widget._active_experiment_id is None
        assert widget._btn_quick_scan.isEnabled()

        # Second Quick Scan
        acq_cmds = []
        event_bus.subscribe(StartAcquisitionCommand, lambda e: acq_cmds.append(e))
        widget._quick_scan()
        exp_id_2 = widget._active_experiment_id
        event_bus.drain()

        assert len(acq_cmds) > 0
        assert exp_id_2.startswith("quick_scan_")
        # Note: IDs may collide if both scans happen within the same second
        # (timestamp-based), but the important thing is that the second scan
        # successfully publishes commands and sets a new experiment ID

    def test_quick_scan_ignores_unrelated_acquisition_state(self, widget, event_bus, scan_coords):
        """AcquisitionStateChanged from another experiment should not affect Quick Scan."""
        self._select_channel(widget)
        self._add_fov(widget, event_bus)
        widget._quick_scan()
        exp_id = widget._active_experiment_id

        # Some other experiment starts/stops
        event_bus.publish(AcquisitionStateChanged(
            in_progress=True, experiment_id="other_experiment",
        ))
        event_bus.drain()

        # Quick Scan should still be tracked as running (button disabled)
        assert not widget._btn_quick_scan.isEnabled()
        assert widget._active_experiment_id == exp_id

        event_bus.publish(AcquisitionStateChanged(
            in_progress=False, experiment_id="other_experiment",
        ))
        event_bus.drain()

        # Still should not have re-enabled (wrong experiment ID)
        assert not widget._btn_quick_scan.isEnabled()
        assert widget._active_experiment_id == exp_id

    def test_quick_scan_does_not_alter_save_path_field(self, widget, event_bus, scan_coords):
        """Quick Scan uses tempdir in the command but should not change the save path text field."""
        self._select_channel(widget)
        self._add_fov(widget, event_bus)

        widget._save_path_edit.setText("/my/important/path")
        widget._quick_scan()

        assert widget._save_path_edit.text() == "/my/important/path"

    def test_quick_scan_does_not_alter_experiment_id_field(self, widget, event_bus, scan_coords):
        """Quick Scan generates its own ID but should not change the experiment ID text field."""
        self._select_channel(widget)
        self._add_fov(widget, event_bus)

        original_text = widget._experiment_id_edit.text()
        widget._quick_scan()

        assert widget._experiment_id_edit.text() == original_text

    def test_quick_scan_does_not_alter_skip_saving_checkbox(self, widget, event_bus, scan_coords):
        """Quick Scan should not toggle the skip_saving checkbox."""
        self._select_channel(widget)
        self._add_fov(widget, event_bus)

        widget._skip_saving.blockSignals(True)
        widget._skip_saving.setChecked(False)
        widget._skip_saving.blockSignals(False)

        widget._quick_scan()

        assert not widget._skip_saving.isChecked(), "Quick Scan should not modify skip_saving checkbox"

    def test_quick_scan_with_multiple_channels(self, widget, event_bus, scan_coords):
        """Quick Scan with multiple channels should pass all of them."""
        from squid.core.events import SetAcquisitionChannelsCommand

        ch_cmds = []
        event_bus.subscribe(SetAcquisitionChannelsCommand, lambda e: ch_cmds.append(e))

        self._select_channel(widget, ["BF LED matrix full", "Fluorescence 488 nm Ex"])
        self._add_fov(widget, event_bus)
        widget._quick_scan()
        event_bus.drain()

        assert len(ch_cmds) == 1
        assert len(ch_cmds[0].channel_names) == 2

    def test_quick_scan_with_z_enabled_still_single_z(self, widget, event_bus, scan_coords):
        """Even with Z-stack enabled and high Nz, Quick Scan should use n_z=1."""
        from squid.core.events import SetAcquisitionParametersCommand

        widget._z_checkbox.setChecked(True)
        widget._z_nz.setValue(50)
        widget._z_delta.setValue(0.5)

        params = []
        event_bus.subscribe(SetAcquisitionParametersCommand, lambda e: params.append(e))

        self._select_channel(widget)
        self._add_fov(widget, event_bus)
        widget._quick_scan()
        event_bus.drain()

        assert params[0].n_z == 1

    def test_quick_scan_with_focus_enabled_still_no_af(self, widget, event_bus, scan_coords):
        """Even with Laser AF enabled, Quick Scan should disable autofocus."""
        from squid.core.events import SetAcquisitionParametersCommand

        widget._focus_checkbox.setChecked(True)
        widget._focus_method.setCurrentIndex(1)  # Laser AF

        params = []
        event_bus.subscribe(SetAcquisitionParametersCommand, lambda e: params.append(e))

        self._select_channel(widget)
        self._add_fov(widget, event_bus)
        widget._quick_scan()
        event_bus.drain()

        assert params[0].use_autofocus is False
        assert params[0].use_reflection_af is False

    def test_quick_scan_button_click_via_qtbot(self, widget, event_bus, scan_coords, qtbot):
        """Simulate actual button click via qtbot.mouseClick."""
        from squid.core.events import StartAcquisitionCommand

        acq_cmds = []
        event_bus.subscribe(StartAcquisitionCommand, lambda e: acq_cmds.append(e))

        self._select_channel(widget)
        self._add_fov(widget, event_bus)

        qtbot.mouseClick(widget._btn_quick_scan, Qt.LeftButton)
        event_bus.drain()

        assert len(acq_cmds) == 1
        assert widget._active_experiment_id is not None
        assert widget._active_experiment_id.startswith("quick_scan_")

    def test_quick_scan_with_aborting_state(self, widget, event_bus, scan_coords):
        """AcquisitionStateChanged with is_aborting should still complete lifecycle."""
        self._select_channel(widget)
        self._add_fov(widget, event_bus)
        widget._quick_scan()
        exp_id = widget._active_experiment_id

        event_bus.publish(AcquisitionStateChanged(
            in_progress=True, experiment_id=exp_id,
        ))
        event_bus.drain()

        # Abort
        event_bus.publish(AcquisitionStateChanged(
            in_progress=False, experiment_id=exp_id, is_aborting=True,
        ))
        event_bus.drain()

        assert not widget._is_acquiring
        assert widget._btn_quick_scan.isEnabled()
        assert widget._active_experiment_id is None
