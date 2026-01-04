import logging
from unittest.mock import MagicMock, patch

import pytest

import control._def
import control.microscope
from control.widgets import check_ram_available_with_error_dialog, SurfacePlotWidget

import tests.control.test_stubs as ts


def test_check_ram_available_with_error_dialog_performance_mode():
    """Test that RAM check is skipped when performance mode is enabled."""
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)
    logger = logging.getLogger("test")

    # When performance mode is enabled, should always return True (skip check)
    result = check_ram_available_with_error_dialog(mpc, logger, performance_mode=True)
    assert result is True


def test_check_ram_available_with_error_dialog_sufficient_ram():
    """Test that check passes when sufficient RAM is available."""
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)
    logger = logging.getLogger("test")

    # Store original value and enable mosaic display
    original_use_napari = control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY
    control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY = True

    try:
        # Set up a small scan area with one channel (need multiple FOVs for non-zero bounds)
        all_configuration_names = [
            config.name
            for config in mpc.channelConfigurationManager.get_configurations(mpc.objectiveStore.current_objective)
        ]
        x_min = mpc.stage.get_config().X_AXIS.MIN_POSITION + 0.01
        y_min = mpc.stage.get_config().Y_AXIS.MIN_POSITION + 0.01
        z_mid = (mpc.stage.get_config().Z_AXIS.MAX_POSITION - mpc.stage.get_config().Z_AXIS.MIN_POSITION) / 2.0
        mpc.scanCoordinates.add_flexible_region(1, x_min, y_min, z_mid, 3, 3, 0)
        mpc.set_selected_configurations(all_configuration_names[0:1])

        # With a small scan area and real available RAM, should pass
        result = check_ram_available_with_error_dialog(mpc, logger, performance_mode=False)
        assert result is True

    finally:
        control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY = original_use_napari


def test_check_ram_available_with_error_dialog_insufficient_ram():
    """Test that check fails when insufficient RAM is available."""
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)
    logger = logging.getLogger("test")

    # Store original value and enable mosaic display
    original_use_napari = control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY
    control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY = True

    try:
        # Set up scan with channels (need multiple FOVs for non-zero bounds)
        all_configuration_names = [
            config.name
            for config in mpc.channelConfigurationManager.get_configurations(mpc.objectiveStore.current_objective)
        ]
        x_min = mpc.stage.get_config().X_AXIS.MIN_POSITION + 0.01
        y_min = mpc.stage.get_config().Y_AXIS.MIN_POSITION + 0.01
        z_mid = (mpc.stage.get_config().Z_AXIS.MAX_POSITION - mpc.stage.get_config().Z_AXIS.MIN_POSITION) / 2.0
        mpc.scanCoordinates.add_flexible_region(1, x_min, y_min, z_mid, 3, 3, 0)
        mpc.set_selected_configurations(all_configuration_names)

        # Mock psutil to return very low available RAM
        mock_vmem = MagicMock()
        mock_vmem.available = 1024  # Only 1KB available

        with patch("psutil.virtual_memory", return_value=mock_vmem):
            with patch("control.widgets.error_dialog"):  # Mock dialog to avoid GUI
                result = check_ram_available_with_error_dialog(mpc, logger, performance_mode=False)
                assert result is False

    finally:
        control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY = original_use_napari


def test_check_ram_available_with_error_dialog_zero_estimate():
    """Test that check passes when RAM estimate is 0 (no regions or napari disabled)."""
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)
    logger = logging.getLogger("test")

    # Clear regions so estimate returns 0
    mpc.scanCoordinates.clear_regions()

    # Should pass since 0 bytes required
    result = check_ram_available_with_error_dialog(mpc, logger, performance_mode=False)
    assert result is True


def test_check_ram_available_with_error_dialog_factor_of_safety():
    """Test that factor of safety is applied to RAM estimate."""
    scope = control.microscope.Microscope.build_from_global_config(True)
    mpc = ts.get_test_multi_point_controller(microscope=scope)
    logger = logging.getLogger("test")

    # Store original value and enable mosaic display
    original_use_napari = control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY
    control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY = True

    try:
        # Set up scan (need multiple FOVs for non-zero bounds)
        all_configuration_names = [
            config.name
            for config in mpc.channelConfigurationManager.get_configurations(mpc.objectiveStore.current_objective)
        ]
        x_min = mpc.stage.get_config().X_AXIS.MIN_POSITION + 0.01
        y_min = mpc.stage.get_config().Y_AXIS.MIN_POSITION + 0.01
        z_mid = (mpc.stage.get_config().Z_AXIS.MAX_POSITION - mpc.stage.get_config().Z_AXIS.MIN_POSITION) / 2.0
        mpc.scanCoordinates.add_flexible_region(1, x_min, y_min, z_mid, 3, 3, 0)
        mpc.set_selected_configurations(all_configuration_names[0:1])

        base_estimate = mpc.get_estimated_mosaic_ram_bytes()
        if base_estimate == 0:
            return  # Skip test if no estimate available

        # Mock psutil to return RAM that's exactly equal to base estimate
        # With factor_of_safety > 1, this should fail
        mock_vmem = MagicMock()
        mock_vmem.available = base_estimate  # Exactly equal to base estimate

        with patch("psutil.virtual_memory", return_value=mock_vmem):
            with patch("control.widgets.error_dialog"):
                # With default factor_of_safety=1.15, should fail (needs 15% more)
                result = check_ram_available_with_error_dialog(
                    mpc, logger, factor_of_safety=1.15, performance_mode=False
                )
                assert result is False

                # With factor_of_safety=1.0, should pass (exact match)
                mock_vmem.available = base_estimate
                result = check_ram_available_with_error_dialog(
                    mpc, logger, factor_of_safety=1.0, performance_mode=False
                )
                assert result is True

    finally:
        control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY = original_use_napari


# ============================================================================
# SurfacePlotWidget Tests
# ============================================================================


@pytest.fixture
def surface_plot_widget(qtbot):
    """Create a SurfacePlotWidget instance for testing."""
    widget = SurfacePlotWidget()
    qtbot.addWidget(widget)
    return widget


class TestSurfacePlotWidget:
    """Tests for SurfacePlotWidget Z-stack handling and edge cases."""

    def test_add_point(self, surface_plot_widget):
        """Test that points are added correctly."""
        widget = surface_plot_widget
        widget.add_point(1.0, 2.0, 3.0, 1)
        widget.add_point(4.0, 5.0, 6.0, 1)

        assert len(widget.x) == 2
        assert len(widget.y) == 2
        assert len(widget.z) == 2
        assert len(widget.regions) == 2
        assert widget.x[0] == 1.0
        assert widget.y[1] == 5.0

    def test_clear_resets_state(self, surface_plot_widget):
        """Test that clear() resets all data and plot state."""
        widget = surface_plot_widget
        widget.add_point(1.0, 2.0, 3.0, 1)
        widget.plot()

        assert widget.plot_populated is True
        assert len(widget.x) == 1

        widget.clear()

        assert len(widget.x) == 0
        assert len(widget.y) == 0
        assert len(widget.z) == 0
        assert len(widget.regions) == 0
        assert len(widget.x_plot) == 0
        assert len(widget.y_plot) == 0
        assert len(widget.z_plot) == 0
        assert widget.plot_populated is False

    def test_empty_data_handling(self, surface_plot_widget):
        """Test that plotting with no data doesn't raise errors."""
        widget = surface_plot_widget
        # Should not raise
        widget.plot()
        assert widget.plot_populated is False

    def test_z_stack_uses_minimum_z(self, surface_plot_widget):
        """Test that Z-stack filtering selects minimum Z at each X,Y location."""
        widget = surface_plot_widget

        # Add multiple Z values at the same X,Y location (Z-stack)
        widget.add_point(10.0, 20.0, 100.0, 1)  # Z = 100
        widget.add_point(10.0, 20.0, 50.0, 1)  # Z = 50 (minimum)
        widget.add_point(10.0, 20.0, 75.0, 1)  # Z = 75

        widget.plot()

        # Should have only 1 unique X,Y location
        assert len(widget.x_plot) == 1
        assert len(widget.y_plot) == 1
        assert len(widget.z_plot) == 1

        # Should use the minimum Z value
        assert widget.z_plot[0] == 50.0
        assert widget.x_plot[0] == 10.0
        assert widget.y_plot[0] == 20.0

    def test_z_stack_multiple_locations(self, surface_plot_widget):
        """Test Z-stack filtering with multiple X,Y locations."""
        widget = surface_plot_widget

        # Location 1: Z-stack with min Z = 10
        widget.add_point(1.0, 1.0, 30.0, 1)
        widget.add_point(1.0, 1.0, 10.0, 1)
        widget.add_point(1.0, 1.0, 20.0, 1)

        # Location 2: Z-stack with min Z = 5
        widget.add_point(2.0, 2.0, 15.0, 1)
        widget.add_point(2.0, 2.0, 5.0, 1)

        widget.plot()

        # Should have 2 unique X,Y locations
        assert len(widget.x_plot) == 2
        assert len(widget.z_plot) == 2

        # Check minimum Z values are used (order may vary due to np.unique)
        z_values = set(widget.z_plot)
        assert z_values == {10.0, 5.0}

    def test_region_matches_min_z_point(self, surface_plot_widget):
        """Test that region corresponds to the point with minimum Z."""
        widget = surface_plot_widget

        # Add points at same X,Y with different regions
        # The point with min Z (region 2) should be selected
        widget.add_point(5.0, 5.0, 100.0, 1)  # Region 1, Z = 100
        widget.add_point(5.0, 5.0, 25.0, 2)  # Region 2, Z = 25 (minimum)
        widget.add_point(5.0, 5.0, 50.0, 3)  # Region 3, Z = 50

        widget.plot()

        # The filtered point should have region 2 (matching min Z)
        assert len(widget.z_plot) == 1
        assert widget.z_plot[0] == 25.0

    def test_single_fov_insufficient_spread(self, surface_plot_widget):
        """Test that single FOV (no X,Y spread) is handled without errors."""
        widget = surface_plot_widget

        # All points at same X,Y (single FOV)
        widget.add_point(50.0, 30.0, 10.0, 1)
        widget.add_point(50.0, 30.0, 20.0, 1)
        widget.add_point(50.0, 30.0, 30.0, 1)
        widget.add_point(50.0, 30.0, 40.0, 1)

        # Should not raise (no surface plotted, but scatter works)
        widget.plot()

        assert widget.plot_populated is True
        assert len(widget.x_plot) == 1  # Single unique location

    def test_single_point(self, surface_plot_widget):
        """Test handling of a single point."""
        widget = surface_plot_widget
        widget.add_point(10.0, 20.0, 30.0, 1)

        widget.plot()

        assert widget.plot_populated is True
        assert len(widget.x_plot) == 1
        assert widget.x_plot[0] == 10.0
        assert widget.y_plot[0] == 20.0
        assert widget.z_plot[0] == 30.0

    def test_xy_precision_grouping(self, surface_plot_widget):
        """Test that X,Y coordinates within precision tolerance are grouped."""
        widget = surface_plot_widget

        # Points very close together (within 4 decimal places = 0.0001)
        widget.add_point(1.00001, 2.00001, 100.0, 1)
        widget.add_point(1.00002, 2.00002, 50.0, 1)  # Should group with above

        # Point further away (different at 4 decimal places)
        widget.add_point(1.001, 2.001, 75.0, 1)  # Should be separate

        widget.plot()

        # Should have 2 unique locations (first two grouped, third separate)
        assert len(widget.x_plot) == 2

    def test_multiple_regions_surface(self, surface_plot_widget):
        """Test surface plotting with multiple regions."""
        widget = surface_plot_widget

        # Region 1: 2x2 grid
        widget.add_point(0.0, 0.0, 10.0, 1)
        widget.add_point(1.0, 0.0, 11.0, 1)
        widget.add_point(0.0, 1.0, 12.0, 1)
        widget.add_point(1.0, 1.0, 13.0, 1)

        # Region 2: 2x2 grid at different location
        widget.add_point(5.0, 5.0, 20.0, 2)
        widget.add_point(6.0, 5.0, 21.0, 2)
        widget.add_point(5.0, 6.0, 22.0, 2)
        widget.add_point(6.0, 6.0, 23.0, 2)

        widget.plot()

        assert widget.plot_populated is True
        assert len(widget.x_plot) == 8  # All 8 unique locations
