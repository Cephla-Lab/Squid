import logging
import os
import tempfile
from unittest.mock import MagicMock, Mock, patch

import pytest

import control._def
import control.microscope
from control.widgets import check_ram_available_with_error_dialog, NDViewerTab, SurfacePlotWidget

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
            config.name for config in mpc.liveController.get_channels(mpc.objectiveStore.current_objective)
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
            config.name for config in mpc.liveController.get_channels(mpc.objectiveStore.current_objective)
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
            config.name for config in mpc.liveController.get_channels(mpc.objectiveStore.current_objective)
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


# ============================================================================
# RAMMonitorWidget Tests
# ============================================================================

from control.widgets import RAMMonitorWidget
from control.core.memory_profiler import MemoryMonitor


@pytest.fixture
def ram_monitor_widget(qtbot):
    """Create a RAMMonitorWidget instance for testing."""
    widget = RAMMonitorWidget()
    qtbot.addWidget(widget)
    return widget


class TestRAMMonitorWidget:
    """Tests for RAMMonitorWidget lifecycle and state management."""

    def test_initial_state(self, ram_monitor_widget):
        """Test that widget initializes with correct state."""
        widget = ram_monitor_widget
        assert widget._memory_monitor is None
        assert widget._session_peak_mb == 0.0
        assert widget.label_current.text() == "--"
        assert widget.label_peak.text() == "--"
        assert widget.label_available.text() == "--"
        assert not widget._update_timer.isActive()

    def test_start_monitoring_starts_timer(self, ram_monitor_widget):
        """Test that start_monitoring starts the update timer."""
        widget = ram_monitor_widget
        widget.start_monitoring()

        assert widget._update_timer.isActive()

        # Clean up
        widget.stop_monitoring()

    def test_stop_monitoring_stops_timer(self, ram_monitor_widget):
        """Test that stop_monitoring stops timer and clears display."""
        widget = ram_monitor_widget
        widget.start_monitoring()
        assert widget._update_timer.isActive()

        widget.stop_monitoring()

        assert not widget._update_timer.isActive()
        assert widget.label_current.text() == "--"
        assert widget.label_peak.text() == "--"
        assert widget.label_available.text() == "--"

    def test_start_monitoring_updates_display(self, ram_monitor_widget, qtbot):
        """Test that start_monitoring triggers an immediate display update."""
        widget = ram_monitor_widget
        widget.start_monitoring()

        # Label should be updated (not "--" anymore, unless footprint unavailable)
        # Give a moment for the update to process
        qtbot.wait(100)

        # Both labels should show a value (or "N/A" if footprint unavailable)
        assert widget.label_current.text() != "--" and widget.label_available.text() != "--"

        widget.stop_monitoring()

    def test_connect_monitor_stops_timer(self, ram_monitor_widget):
        """Test that connecting to a monitor stops the timer."""
        widget = ram_monitor_widget
        widget.start_monitoring()
        assert widget._update_timer.isActive()

        # Create a monitor with signals disabled (no Qt app needed)
        monitor = MemoryMonitor(sample_interval_ms=100, enable_signals=False)

        widget.connect_monitor(monitor)

        assert not widget._update_timer.isActive()
        assert widget._memory_monitor is monitor

        # Clean up
        widget.disconnect_monitor()

    def test_disconnect_monitor_clears_reference(self, ram_monitor_widget):
        """Test that disconnect_monitor clears the monitor reference."""
        widget = ram_monitor_widget
        monitor = MemoryMonitor(sample_interval_ms=100, enable_signals=False)

        widget.connect_monitor(monitor)
        assert widget._memory_monitor is monitor

        widget.disconnect_monitor()

        assert widget._memory_monitor is None
        # Timer NOT started by disconnect - caller decides
        assert not widget._update_timer.isActive()

    def test_disconnect_monitor_does_not_restart_timer(self, ram_monitor_widget):
        """Test that disconnect_monitor does NOT restart the timer (caller decides)."""
        widget = ram_monitor_widget
        monitor = MemoryMonitor(sample_interval_ms=100, enable_signals=False)

        widget.start_monitoring()
        assert widget._update_timer.isActive()

        widget.connect_monitor(monitor)
        assert not widget._update_timer.isActive()

        widget.disconnect_monitor()

        # Timer should NOT be restarted by disconnect_monitor
        assert not widget._update_timer.isActive()

    def test_update_memory_display_skipped_when_connected(self, ram_monitor_widget):
        """Test that timer updates are skipped when connected to a monitor."""
        widget = ram_monitor_widget
        monitor = MemoryMonitor(sample_interval_ms=100, enable_signals=False)
        widget.connect_monitor(monitor)

        # Set a known label value
        widget.label_current.setText("TEST_VALUE")

        # Call update - should be skipped because monitor is connected
        widget._update_memory_display()

        # Label should not have changed
        assert widget.label_current.text() == "TEST_VALUE"

        widget.disconnect_monitor()

    def test_connect_none_monitor(self, ram_monitor_widget):
        """Test that connecting None monitor is handled gracefully."""
        widget = ram_monitor_widget
        widget.start_monitoring()

        # Should not raise
        widget.connect_monitor(None)

        assert widget._memory_monitor is None

        widget.stop_monitoring()

    def test_double_disconnect_safe(self, ram_monitor_widget):
        """Test that disconnecting twice is safe."""
        widget = ram_monitor_widget
        monitor = MemoryMonitor(sample_interval_ms=100, enable_signals=False)

        widget.connect_monitor(monitor)
        widget.disconnect_monitor()
        # Second disconnect should not raise
        widget.disconnect_monitor()

        assert widget._memory_monitor is None

    def test_session_peak_tracking(self, ram_monitor_widget, qtbot):
        """Test that session peak is tracked and displayed during monitoring."""
        widget = ram_monitor_widget
        widget.start_monitoring()

        # Let it run briefly to get some measurements
        qtbot.wait(100)

        # Session peak should be updated (may be 0.0 if footprint unavailable on this platform)
        assert widget._session_peak_mb >= 0.0
        # Peak label should show a value (or "N/A" if unavailable)
        assert widget.label_peak.text() != "--"

        widget.stop_monitoring()

    def test_start_monitoring_reset_peak_false(self, ram_monitor_widget, qtbot):
        """Test that reset_peak=False preserves the session peak."""
        widget = ram_monitor_widget

        # Start monitoring and let it record a peak
        widget.start_monitoring()
        qtbot.wait(100)
        initial_peak = widget._session_peak_mb

        widget.stop_monitoring()

        # Restart without resetting peak
        widget.start_monitoring(reset_peak=False)

        # Peak should be preserved (may increase but not decrease)
        assert widget._session_peak_mb >= initial_peak

        widget.stop_monitoring()


class TestRAMMonitorWidgetSignals:
    """Tests for RAMMonitorWidget signal handling (requires Qt signals)."""

    def test_footprint_signal_updates_display(self, ram_monitor_widget, qtbot):
        """Test that footprint_updated signal updates the display."""
        widget = ram_monitor_widget

        # Directly call the signal handler
        widget._on_footprint_updated(2048.0)  # 2 GB in MB

        assert widget.label_current.text() == "2.00 GB"
        assert widget.label_peak.text() == "2.00 GB"
        assert widget._session_peak_mb == 2048.0
        # Available RAM should also be updated
        assert widget.label_available.text() != "--"

    def test_footprint_signal_various_values(self, ram_monitor_widget):
        """Test footprint signal with various values and peak tracking."""
        widget = ram_monitor_widget

        # Test small value
        widget._on_footprint_updated(512.0)  # 0.5 GB
        assert widget.label_current.text() == "0.50 GB"
        assert widget.label_peak.text() == "0.50 GB"

        # Test larger value - peak should update
        widget._on_footprint_updated(8192.0)  # 8 GB
        assert widget.label_current.text() == "8.00 GB"
        assert widget.label_peak.text() == "8.00 GB"
        assert widget._session_peak_mb == 8192.0

        # Test smaller value - peak should stay at 8 GB
        widget._on_footprint_updated(10.24)  # ~10 MB = 0.01 GB
        assert widget.label_current.text() == "0.01 GB"
        assert widget.label_peak.text() == "8.00 GB"  # Peak unchanged
        assert widget._session_peak_mb == 8192.0


# ============================================================================
# MultiPointController Memory Monitoring Integration Tests
# ============================================================================


class TestMultiPointControllerMemoryMonitoring:
    """Tests for MultiPointController memory monitoring integration."""

    def test_memory_monitor_starts_when_enabled(self):
        """Test that memory monitoring starts when ENABLE_MEMORY_PROFILING=True."""
        original_value = control._def.ENABLE_MEMORY_PROFILING
        try:
            control._def.ENABLE_MEMORY_PROFILING = True

            scope = control.microscope.Microscope.build_from_global_config(True)
            mpc = ts.get_test_multi_point_controller(microscope=scope)

            # Verify monitor starts when acquisition begins
            assert mpc._memory_monitor is None  # Not started yet

            # Simulate what happens in run_acquisition
            # We can't run full acquisition, but we can test the conditional logic
            if control._def.ENABLE_MEMORY_PROFILING:
                from control.core.memory_profiler import MemoryMonitor

                mpc._memory_monitor = MemoryMonitor(
                    sample_interval_ms=200,
                    process_name="main",
                    track_children=True,
                    log_interval_s=30.0,
                )
                mpc._memory_monitor.start("TEST_ACQUISITION_START")

            assert mpc._memory_monitor is not None

            # Clean up
            if mpc._memory_monitor is not None:
                mpc._memory_monitor.stop()
                mpc._memory_monitor = None
        finally:
            control._def.ENABLE_MEMORY_PROFILING = original_value

    def test_memory_monitor_not_started_when_disabled(self):
        """Test that memory monitoring does not start when ENABLE_MEMORY_PROFILING=False."""
        original_value = control._def.ENABLE_MEMORY_PROFILING
        try:
            control._def.ENABLE_MEMORY_PROFILING = False

            scope = control.microscope.Microscope.build_from_global_config(True)
            mpc = ts.get_test_multi_point_controller(microscope=scope)

            # Verify monitor does not start when disabled
            assert mpc._memory_monitor is None

            # Simulate what happens in run_acquisition
            if control._def.ENABLE_MEMORY_PROFILING:
                from control.core.memory_profiler import MemoryMonitor

                mpc._memory_monitor = MemoryMonitor(
                    sample_interval_ms=200,
                    process_name="main",
                    track_children=True,
                )
                mpc._memory_monitor.start("TEST_ACQUISITION_START")

            # Monitor should still be None
            assert mpc._memory_monitor is None
        finally:
            control._def.ENABLE_MEMORY_PROFILING = original_value

    def test_memory_monitor_cleanup_on_stop(self):
        """Test that memory monitor is properly cleaned up after stopping."""
        original_value = control._def.ENABLE_MEMORY_PROFILING
        try:
            control._def.ENABLE_MEMORY_PROFILING = True

            scope = control.microscope.Microscope.build_from_global_config(True)
            mpc = ts.get_test_multi_point_controller(microscope=scope)

            from control.core.memory_profiler import MemoryMonitor

            mpc._memory_monitor = MemoryMonitor(
                sample_interval_ms=200,
                process_name="main",
                track_children=True,
                log_interval_s=30.0,
            )
            mpc._memory_monitor.start("TEST_START")

            # Simulate cleanup as done in _run_multipoint_acquisition
            report = mpc._memory_monitor.stop()
            mpc._memory_monitor = None

            assert mpc._memory_monitor is None
            assert report is not None
        finally:
            control._def.ENABLE_MEMORY_PROFILING = original_value

    def test_memory_monitor_has_signals_for_gui(self):
        """Test that memory monitor creates signals for GUI updates."""
        original_value = control._def.ENABLE_MEMORY_PROFILING
        try:
            control._def.ENABLE_MEMORY_PROFILING = True

            from control.core.memory_profiler import MemoryMonitor, HAS_QT

            monitor = MemoryMonitor(
                sample_interval_ms=200,
                process_name="main",
                track_children=True,
                log_interval_s=30.0,
                enable_signals=True,
            )

            if HAS_QT:
                assert monitor.signals is not None
                # Verify signal attributes exist
                assert hasattr(monitor.signals, "memory_updated")
                assert hasattr(monitor.signals, "footprint_updated")
        finally:
            control._def.ENABLE_MEMORY_PROFILING = original_value

    def test_ram_widget_connect_to_multipoint_monitor(self, qtbot):
        """Test that RAMMonitorWidget can connect to MultiPointController's monitor."""
        original_value = control._def.ENABLE_MEMORY_PROFILING
        try:
            control._def.ENABLE_MEMORY_PROFILING = True

            from control.core.memory_profiler import MemoryMonitor

            # Create widget and monitor
            widget = RAMMonitorWidget()
            qtbot.addWidget(widget)

            monitor = MemoryMonitor(
                sample_interval_ms=100,
                process_name="main",
                track_children=True,
                enable_signals=True,
            )
            monitor.start("INTEGRATION_TEST")

            # Connect widget to monitor (simulates what gui_hcs does)
            widget.connect_monitor(monitor)

            # Wait for signals
            qtbot.wait(200)

            # Widget should receive updates
            # (value depends on whether footprint is available on this platform)
            assert widget._memory_monitor is monitor

            # Disconnect
            widget.disconnect_monitor()
            assert widget._memory_monitor is None

            # Cleanup
            monitor.stop()
        finally:
            control._def.ENABLE_MEMORY_PROFILING = original_value


# ============================================================================
# BackpressureMonitorWidget Tests
# ============================================================================

from control.widgets import BackpressureMonitorWidget
from control.core.backpressure import BackpressureStats
from unittest.mock import Mock


@pytest.fixture
def bp_monitor_widget(qtbot):
    """Create a BackpressureMonitorWidget instance for testing."""
    widget = BackpressureMonitorWidget()
    qtbot.addWidget(widget)
    return widget


@pytest.fixture
def mock_bp_controller():
    """Create a mock BackpressureController for testing."""
    controller = Mock()
    controller.enabled = True
    controller.get_stats.return_value = BackpressureStats(
        pending_jobs=5,
        pending_bytes_mb=125.5,
        max_pending_jobs=10,
        max_pending_mb=500.0,
        is_throttled=False,
    )
    return controller


class TestBackpressureMonitorWidget:
    """Tests for BackpressureMonitorWidget lifecycle and state management."""

    def test_initial_state(self, bp_monitor_widget):
        """Test that widget initializes with correct state."""
        widget = bp_monitor_widget
        assert widget._controller is None
        assert widget._throttle_sticky_counter == 0
        assert widget.label_jobs.text() == "--"
        assert widget.label_bytes.text() == "--"
        assert widget.label_throttled.text() == ""
        assert not widget._update_timer.isActive()

    def test_start_monitoring_starts_timer(self, bp_monitor_widget, mock_bp_controller):
        """Test that start_monitoring starts the update timer."""
        widget = bp_monitor_widget
        widget.start_monitoring(mock_bp_controller)

        assert widget._update_timer.isActive()
        assert widget._controller is mock_bp_controller

        # Clean up
        widget.stop_monitoring()

    def test_start_monitoring_with_none_controller(self, bp_monitor_widget):
        """Test that start_monitoring with None controller is handled gracefully."""
        widget = bp_monitor_widget

        # Should not raise, but should log a warning
        widget.start_monitoring(None)

        assert widget._controller is None
        assert not widget._update_timer.isActive()

    def test_stop_monitoring_stops_timer(self, bp_monitor_widget, mock_bp_controller):
        """Test that stop_monitoring stops timer and clears display."""
        widget = bp_monitor_widget
        widget.start_monitoring(mock_bp_controller)
        assert widget._update_timer.isActive()

        widget.stop_monitoring()

        assert not widget._update_timer.isActive()
        assert widget._controller is None
        assert widget._throttle_sticky_counter == 0
        assert widget.label_jobs.text() == "--"
        assert widget.label_bytes.text() == "--"
        assert widget.label_throttled.text() == ""

    def test_start_monitoring_updates_display(self, bp_monitor_widget, mock_bp_controller, qtbot):
        """Test that start_monitoring triggers an immediate display update."""
        widget = bp_monitor_widget
        widget.start_monitoring(mock_bp_controller)

        # Labels should be updated immediately
        assert widget.label_jobs.text() == "5/10 jobs"
        assert widget.label_bytes.text() == "125.5/500.0 MB"

        widget.stop_monitoring()

    def test_start_monitoring_resets_sticky_counter(self, bp_monitor_widget, mock_bp_controller):
        """Test that start_monitoring resets the sticky throttle counter."""
        widget = bp_monitor_widget
        widget._throttle_sticky_counter = 3  # Simulate leftover state

        widget.start_monitoring(mock_bp_controller)

        assert widget._throttle_sticky_counter == 0

        widget.stop_monitoring()

    def test_display_format_with_max_values(self, bp_monitor_widget, mock_bp_controller):
        """Test that display shows current/max format."""
        widget = bp_monitor_widget
        mock_bp_controller.get_stats.return_value = BackpressureStats(
            pending_jobs=3,
            pending_bytes_mb=200.0,
            max_pending_jobs=20,
            max_pending_mb=1000.0,
            is_throttled=False,
        )

        widget.start_monitoring(mock_bp_controller)

        assert widget.label_jobs.text() == "3/20 jobs"
        assert widget.label_bytes.text() == "200.0/1000.0 MB"

        widget.stop_monitoring()


class TestBackpressureMonitorWidgetThrottling:
    """Tests for BackpressureMonitorWidget throttle indicator behavior."""

    def test_throttled_indicator_shown_when_throttled(self, bp_monitor_widget, mock_bp_controller):
        """Test that [THROTTLED] appears when is_throttled is True."""
        widget = bp_monitor_widget
        mock_bp_controller.get_stats.return_value = BackpressureStats(
            pending_jobs=10,
            pending_bytes_mb=500.0,
            max_pending_jobs=10,
            max_pending_mb=500.0,
            is_throttled=True,
        )

        widget.start_monitoring(mock_bp_controller)

        assert widget.label_throttled.text() == "[THROTTLED]"
        assert widget._throttle_sticky_counter == BackpressureMonitorWidget.THROTTLE_STICKY_CYCLES

        widget.stop_monitoring()

    def test_throttled_indicator_hidden_when_not_throttled(self, bp_monitor_widget, mock_bp_controller):
        """Test that [THROTTLED] is hidden when is_throttled is False."""
        widget = bp_monitor_widget
        mock_bp_controller.get_stats.return_value = BackpressureStats(
            pending_jobs=5,
            pending_bytes_mb=125.5,
            max_pending_jobs=10,
            max_pending_mb=500.0,
            is_throttled=False,
        )

        widget.start_monitoring(mock_bp_controller)

        assert widget.label_throttled.text() == ""

        widget.stop_monitoring()

    def test_sticky_throttle_countdown(self, bp_monitor_widget, mock_bp_controller):
        """Test that [THROTTLED] stays visible for THROTTLE_STICKY_CYCLES after release."""
        widget = bp_monitor_widget

        # First: throttled
        mock_bp_controller.get_stats.return_value = BackpressureStats(
            pending_jobs=10,
            pending_bytes_mb=500.0,
            max_pending_jobs=10,
            max_pending_mb=500.0,
            is_throttled=True,
        )
        widget.start_monitoring(mock_bp_controller)
        assert widget.label_throttled.text() == "[THROTTLED]"
        initial_counter = widget._throttle_sticky_counter

        # Now: not throttled - simulate countdown
        mock_bp_controller.get_stats.return_value = BackpressureStats(
            pending_jobs=5,
            pending_bytes_mb=250.0,
            max_pending_jobs=10,
            max_pending_mb=500.0,
            is_throttled=False,
        )

        # Each update should decrement the counter but keep [THROTTLED] visible
        for i in range(initial_counter - 1):
            widget._update_display()
            assert widget.label_throttled.text() == "[THROTTLED]"
            assert widget._throttle_sticky_counter == initial_counter - 1 - i

        # Final update should clear [THROTTLED]
        widget._update_display()
        assert widget.label_throttled.text() == ""
        assert widget._throttle_sticky_counter == 0

        widget.stop_monitoring()

    def test_throttle_reactivation_resets_counter(self, bp_monitor_widget, mock_bp_controller):
        """Test that re-throttling during countdown resets the counter."""
        widget = bp_monitor_widget

        # Start throttled
        mock_bp_controller.get_stats.return_value = BackpressureStats(
            pending_jobs=10, pending_bytes_mb=500.0, max_pending_jobs=10, max_pending_mb=500.0, is_throttled=True
        )
        widget.start_monitoring(mock_bp_controller)

        # Release throttle and count down partially
        mock_bp_controller.get_stats.return_value = BackpressureStats(
            pending_jobs=5, pending_bytes_mb=250.0, max_pending_jobs=10, max_pending_mb=500.0, is_throttled=False
        )
        widget._update_display()
        widget._update_display()
        assert widget._throttle_sticky_counter == BackpressureMonitorWidget.THROTTLE_STICKY_CYCLES - 2

        # Re-throttle - counter should reset to max
        mock_bp_controller.get_stats.return_value = BackpressureStats(
            pending_jobs=10, pending_bytes_mb=500.0, max_pending_jobs=10, max_pending_mb=500.0, is_throttled=True
        )
        widget._update_display()
        assert widget._throttle_sticky_counter == BackpressureMonitorWidget.THROTTLE_STICKY_CYCLES

        widget.stop_monitoring()


class TestBackpressureMonitorWidgetErrorHandling:
    """Tests for BackpressureMonitorWidget error handling."""

    def test_broken_pipe_error_handled(self, bp_monitor_widget, mock_bp_controller):
        """Test that BrokenPipeError is handled gracefully."""
        widget = bp_monitor_widget
        mock_bp_controller.get_stats.side_effect = BrokenPipeError("Connection closed")

        widget.start_monitoring(mock_bp_controller)

        # Should not raise - display should show initial values from start_monitoring's first call
        # (which raises), but widget should continue working
        widget._update_display()

        widget.stop_monitoring()

    def test_eof_error_handled(self, bp_monitor_widget, mock_bp_controller):
        """Test that EOFError is handled gracefully."""
        widget = bp_monitor_widget
        mock_bp_controller.get_stats.side_effect = EOFError("End of file")

        widget._controller = mock_bp_controller  # Bypass start_monitoring

        # Should not raise
        widget._update_display()

        widget.stop_monitoring()

    def test_generic_exception_handled(self, bp_monitor_widget, mock_bp_controller):
        """Test that generic exceptions are handled gracefully."""
        widget = bp_monitor_widget
        mock_bp_controller.get_stats.side_effect = RuntimeError("Unexpected error")

        widget._controller = mock_bp_controller  # Bypass start_monitoring

        # Should not raise
        widget._update_display()

        widget.stop_monitoring()

    def test_update_display_with_no_controller(self, bp_monitor_widget):
        """Test that _update_display does nothing when controller is None."""
        widget = bp_monitor_widget
        widget.label_jobs.setText("TEST")

        widget._update_display()

        # Label should not change since there's no controller
        assert widget.label_jobs.text() == "TEST"


class TestBackpressureMonitorWidgetTimer:
    """Tests for BackpressureMonitorWidget timer behavior."""

    def test_timer_interval(self, bp_monitor_widget):
        """Test that timer is configured with correct interval."""
        widget = bp_monitor_widget
        assert widget._update_timer.interval() == 500  # 500ms

    def test_timer_updates_periodically(self, bp_monitor_widget, mock_bp_controller, qtbot):
        """Test that timer triggers periodic updates."""
        widget = bp_monitor_widget

        widget.start_monitoring(mock_bp_controller)
        initial_calls = mock_bp_controller.get_stats.call_count

        # Wait for timer to fire at least once
        qtbot.wait(600)

        assert mock_bp_controller.get_stats.call_count > initial_calls

        widget.stop_monitoring()

    def test_double_stop_safe(self, bp_monitor_widget, mock_bp_controller):
        """Test that stopping twice is safe."""
        widget = bp_monitor_widget
        widget.start_monitoring(mock_bp_controller)

        widget.stop_monitoring()
        # Second stop should not raise
        widget.stop_monitoring()

        assert widget._controller is None
        assert not widget._update_timer.isActive()


# ============================================================================
# NDViewerTab Tests
# ============================================================================


@pytest.fixture
def ndviewer_tab(qtbot):
    """Create an NDViewerTab instance for testing."""
    widget = NDViewerTab()
    qtbot.addWidget(widget)
    return widget


class TestNDViewerTab:
    """Tests for NDViewerTab lifecycle and state management."""

    def test_initial_state(self, ndviewer_tab):
        """Test that widget initializes with correct state."""
        widget = ndviewer_tab
        assert widget._viewer is None
        assert widget._dataset_path is None
        # Check placeholder exists with correct text (isVisible() is False before widget shown)
        assert widget._placeholder is not None
        assert widget._placeholder.text() == NDViewerTab._PLACEHOLDER_WAITING

    def test_set_dataset_path_none_shows_placeholder(self, ndviewer_tab):
        """Test that setting None path shows the waiting placeholder."""
        widget = ndviewer_tab
        widget.set_dataset_path(None)

        assert widget._dataset_path is None
        assert widget._placeholder is not None
        assert widget._placeholder.text() == NDViewerTab._PLACEHOLDER_WAITING

    def test_set_dataset_path_invalid_shows_error(self, ndviewer_tab):
        """Test that setting an invalid path shows error placeholder."""
        widget = ndviewer_tab
        invalid_path = "/nonexistent/path/to/dataset"

        widget.set_dataset_path(invalid_path)

        assert widget._dataset_path == invalid_path
        assert widget._placeholder is not None
        assert "not found" in widget._placeholder.text()

    def test_set_dataset_path_same_path_skips(self, ndviewer_tab):
        """Test that setting the same path does not re-process."""
        widget = ndviewer_tab
        path = "/some/path"

        # First call sets path (will show error since invalid, but that's ok for this test)
        widget.set_dataset_path(path)
        assert widget._dataset_path == path

        # Track placeholder text after first call
        text_after_first = widget._placeholder.text()

        # Second call with same path should skip processing
        widget.set_dataset_path(path)

        # State should be unchanged
        assert widget._dataset_path == path
        assert widget._placeholder.text() == text_after_first

    def test_set_dataset_path_clears_when_none_after_valid(self, ndviewer_tab):
        """Test that setting None after a path clears the view."""
        widget = ndviewer_tab

        # Set to an invalid path first (so we have a non-None _dataset_path)
        widget.set_dataset_path("/invalid/path")
        assert widget._dataset_path == "/invalid/path"

        # Now set to None
        widget.set_dataset_path(None)

        assert widget._dataset_path is None
        assert widget._placeholder is not None
        assert widget._placeholder.text() == NDViewerTab._PLACEHOLDER_WAITING

    def test_show_placeholder_hides_existing_viewer(self, ndviewer_tab):
        """Test that _show_placeholder hides an existing viewer."""
        widget = ndviewer_tab

        # Inject a mock viewer
        mock_viewer = Mock()
        widget._viewer = mock_viewer

        widget._show_placeholder("Test message")

        mock_viewer.setVisible.assert_called_with(False)
        assert widget._placeholder.text() == "Test message"


class TestNDViewerTabNavigation:
    """Tests for NDViewerTab FOV navigation."""

    def test_go_to_fov_no_viewer_returns_false(self, ndviewer_tab):
        """Test that go_to_fov returns False when no viewer is loaded."""
        widget = ndviewer_tab
        result = widget.go_to_fov("A1", 0)
        assert result is False

    def test_go_to_fov_with_mock_viewer(self, ndviewer_tab):
        """Test go_to_fov with a mocked viewer (legacy/file-based mode)."""
        widget = ndviewer_tab

        # Create mock viewer in file-based mode (not push mode)
        mock_viewer = Mock()
        mock_viewer.is_push_mode_active.return_value = False
        mock_viewer.has_fov_dimension.return_value = True
        mock_viewer.get_fov_list.return_value = [
            {"region": "A1", "fov": 0},
            {"region": "A1", "fov": 1},
            {"region": "B2", "fov": 0},
        ]
        mock_viewer.set_current_index.return_value = True
        widget._viewer = mock_viewer

        # Navigate to A1, fov 0 (should be flat index 0)
        result = widget.go_to_fov("A1", 0)

        assert result is True
        mock_viewer.set_current_index.assert_called_once_with("fov", 0)

    def test_go_to_fov_finds_correct_flat_index(self, ndviewer_tab):
        """Test that go_to_fov finds the correct flat index (legacy/file-based mode)."""
        widget = ndviewer_tab

        # Create mock viewer in file-based mode (not push mode)
        mock_viewer = Mock()
        mock_viewer.is_push_mode_active.return_value = False
        mock_viewer.has_fov_dimension.return_value = True
        mock_viewer.get_fov_list.return_value = [
            {"region": "A1", "fov": 0},
            {"region": "A1", "fov": 1},
            {"region": "B2", "fov": 0},  # flat index 2
            {"region": "B2", "fov": 1},
        ]
        mock_viewer.set_current_index.return_value = True
        widget._viewer = mock_viewer

        # Navigate to B2, fov 0 (should be flat index 2)
        result = widget.go_to_fov("B2", 0)

        assert result is True
        mock_viewer.set_current_index.assert_called_once_with("fov", 2)

    def test_go_to_fov_not_found_returns_false(self, ndviewer_tab):
        """Test that go_to_fov returns False when FOV not found (legacy/file-based mode)."""
        widget = ndviewer_tab

        mock_viewer = Mock()
        mock_viewer.is_push_mode_active.return_value = False
        mock_viewer.has_fov_dimension.return_value = True
        mock_viewer.get_fov_list.return_value = [
            {"region": "A1", "fov": 0},
        ]
        widget._viewer = mock_viewer

        # Try to navigate to non-existent well
        result = widget.go_to_fov("Z9", 99)

        assert result is False
        mock_viewer.set_current_index.assert_not_called()

    def test_go_to_fov_no_fov_dimension_returns_false(self, ndviewer_tab):
        """Test that go_to_fov returns False when no fov dimension (legacy/file-based mode)."""
        widget = ndviewer_tab

        mock_viewer = Mock()
        mock_viewer.is_push_mode_active.return_value = False
        mock_viewer.has_fov_dimension.return_value = False
        widget._viewer = mock_viewer

        result = widget.go_to_fov("A1", 0)

        assert result is False

    def test_go_to_fov_handles_exception(self, ndviewer_tab):
        """Test that go_to_fov handles exceptions gracefully (legacy/file-based mode)."""
        widget = ndviewer_tab

        mock_viewer = Mock()
        mock_viewer.is_push_mode_active.return_value = False
        mock_viewer.has_fov_dimension.side_effect = RuntimeError("Viewer error")
        widget._viewer = mock_viewer

        # Should not raise, should return False
        result = widget.go_to_fov("A1", 0)

        assert result is False

    def test_go_to_fov_set_current_index_fails_returns_false(self, ndviewer_tab):
        """Test go_to_fov returns False when set_current_index fails (legacy/file-based mode)."""
        widget = ndviewer_tab

        mock_viewer = Mock()
        mock_viewer.is_push_mode_active.return_value = False
        mock_viewer.has_fov_dimension.return_value = True
        mock_viewer.get_fov_list.return_value = [{"region": "A1", "fov": 0}]
        mock_viewer.set_current_index.return_value = False  # Navigation fails
        widget._viewer = mock_viewer

        result = widget.go_to_fov("A1", 0)

        assert result is False
        mock_viewer.set_current_index.assert_called_once_with("fov", 0)

    def test_go_to_fov_get_fov_list_exception_returns_false(self, ndviewer_tab):
        """Test go_to_fov handles get_fov_list exceptions (legacy/file-based mode)."""
        widget = ndviewer_tab

        mock_viewer = Mock()
        mock_viewer.is_push_mode_active.return_value = False
        mock_viewer.has_fov_dimension.return_value = True
        mock_viewer.get_fov_list.side_effect = RuntimeError("FOV list error")
        widget._viewer = mock_viewer

        result = widget.go_to_fov("A1", 0)

        assert result is False

    def test_go_to_fov_push_mode_success(self, ndviewer_tab):
        """Test go_to_fov succeeds when push mode is active and navigation works."""
        widget = ndviewer_tab

        mock_viewer = Mock()
        mock_viewer.is_push_mode_active.return_value = True
        mock_viewer.go_to_well_fov.return_value = True
        widget._viewer = mock_viewer

        result = widget.go_to_fov("B2", 3)

        assert result is True
        mock_viewer.is_push_mode_active.assert_called_once()
        mock_viewer.go_to_well_fov.assert_called_once_with("B2", 3)

    def test_go_to_fov_push_mode_fails(self, ndviewer_tab):
        """Test go_to_fov returns False when push mode navigation fails."""
        widget = ndviewer_tab

        mock_viewer = Mock()
        mock_viewer.is_push_mode_active.return_value = True
        mock_viewer.go_to_well_fov.return_value = False  # Navigation fails
        widget._viewer = mock_viewer

        result = widget.go_to_fov("C3", 1)

        assert result is False
        mock_viewer.is_push_mode_active.assert_called_once()
        mock_viewer.go_to_well_fov.assert_called_once_with("C3", 1)

    def test_go_to_fov_push_mode_exception(self, ndviewer_tab):
        """Test go_to_fov handles exceptions in push mode."""
        widget = ndviewer_tab

        mock_viewer = Mock()
        mock_viewer.is_push_mode_active.return_value = True
        mock_viewer.go_to_well_fov.side_effect = RuntimeError("Push mode error")
        widget._viewer = mock_viewer

        result = widget.go_to_fov("A1", 0)

        assert result is False


class TestNDViewerTabCleanup:
    """Tests for NDViewerTab cleanup and close behavior."""

    def test_close_with_no_viewer(self, ndviewer_tab):
        """Test that close() is safe when no viewer exists."""
        widget = ndviewer_tab
        assert widget._viewer is None

        # Should not raise
        widget.close()

        assert widget._viewer is None
        assert widget._dataset_path is None

    def test_close_clears_viewer(self, ndviewer_tab):
        """Test that close() clears the viewer reference."""
        widget = ndviewer_tab

        # Create mock viewer
        mock_viewer = Mock()
        widget._viewer = mock_viewer
        widget._dataset_path = "/some/path"

        widget.close()

        assert widget._viewer is None
        assert widget._dataset_path is None
        mock_viewer.close.assert_called_once()

    def test_close_handles_viewer_exception(self, ndviewer_tab):
        """Test that close() handles viewer close() exceptions."""
        widget = ndviewer_tab

        mock_viewer = Mock()
        mock_viewer.close.side_effect = RuntimeError("Close error")
        widget._viewer = mock_viewer
        widget._dataset_path = "/some/path"

        # Should not raise
        widget.close()

        # Should still clear references despite exception
        assert widget._viewer is None
        assert widget._dataset_path is None

    def test_double_close_safe(self, ndviewer_tab):
        """Test that calling close() twice is safe."""
        widget = ndviewer_tab

        mock_viewer = Mock()
        widget._viewer = mock_viewer

        widget.close()
        assert mock_viewer.close.call_count == 1

        # Second close should not raise or call viewer.close() again
        widget.close()
        assert mock_viewer.close.call_count == 1


class TestNDViewerTabImportHandling:
    """Tests for NDViewerTab import and viewer creation handling."""

    def test_reload_existing_viewer(self, ndviewer_tab):
        """Test that existing viewer is reloaded when path changes."""
        widget = ndviewer_tab

        # Set up a mock viewer that's already loaded
        mock_viewer = Mock()
        mock_viewer.load_dataset.return_value = None
        mock_viewer.refresh.return_value = None
        widget._viewer = mock_viewer
        widget._dataset_path = "/old/path"

        with tempfile.TemporaryDirectory() as tmpdir:
            widget.set_dataset_path(tmpdir)

            # Should have called load_dataset and refresh on existing viewer
            mock_viewer.load_dataset.assert_called_once_with(tmpdir)
            mock_viewer.refresh.assert_called_once()

    def test_set_dataset_path_exception_shows_error(self, ndviewer_tab):
        """Test that viewer creation exception is handled."""
        widget = ndviewer_tab

        # Inject a mock viewer that raises on load
        mock_viewer = Mock()
        mock_viewer.load_dataset.side_effect = RuntimeError("Load failed")
        widget._viewer = mock_viewer
        widget._dataset_path = "/old/path"

        # Try to load new path - should catch exception and show error
        with tempfile.TemporaryDirectory() as tmpdir:
            widget.set_dataset_path(tmpdir)

            # Should show error in placeholder
            assert (
                "failed to load" in widget._placeholder.text().lower() or "error" in widget._placeholder.text().lower()
            )


class TestNDViewerTabPushAPI:
    """Tests for NDViewerTab push-based API (start_acquisition, register_image)."""

    def test_start_acquisition_import_failure(self, ndviewer_tab):
        """Test start_acquisition handles ndviewer_light import failure."""
        import builtins

        widget = ndviewer_tab
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "control.ndviewer_light" or (
                args and len(args) > 2 and args[2] and "ndviewer_light" in str(args[2])
            ):
                raise ImportError("No module named 'control.ndviewer_light'")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=mock_import):
            result = widget.start_acquisition(
                channels=["BF LED matrix full"],
                num_z=1,
                height=512,
                width=512,
                fov_labels=["A1:0"],
            )

        assert result is False
        assert "failed to import" in widget._placeholder.text().lower()

    def test_start_acquisition_viewer_raises_exception(self, ndviewer_tab):
        """Test start_acquisition handles viewer.start_acquisition exception."""
        widget = ndviewer_tab

        # Create a mock viewer that raises on start_acquisition
        mock_viewer = Mock()
        mock_viewer.start_acquisition.side_effect = RuntimeError("Viewer init failed")
        widget._viewer = mock_viewer

        result = widget.start_acquisition(
            channels=["BF LED matrix full"],
            num_z=1,
            height=512,
            width=512,
            fov_labels=["A1:0"],
        )

        assert result is False
        # Should show error in placeholder
        assert "failed to start acquisition" in widget._placeholder.text().lower()

    def test_start_acquisition_success_returns_true(self, ndviewer_tab):
        """Test start_acquisition returns True on success."""
        widget = ndviewer_tab

        # Create a mock viewer that succeeds
        mock_viewer = Mock()
        mock_viewer.start_acquisition.return_value = None
        widget._viewer = mock_viewer

        result = widget.start_acquisition(
            channels=["BF LED matrix full", "Fluorescence 488 nm Ex"],
            num_z=5,
            height=1024,
            width=1024,
            fov_labels=["A1:0", "A1:1", "B2:0"],
        )

        assert result is True
        mock_viewer.start_acquisition.assert_called_once_with(
            ["BF LED matrix full", "Fluorescence 488 nm Ex"],
            5,
            1024,
            1024,
            ["A1:0", "A1:1", "B2:0"],
        )
        mock_viewer.setVisible.assert_called_once_with(True)

    # -------------------------------------------------------------------------
    # register_image tests
    # -------------------------------------------------------------------------

    def test_register_image_no_viewer(self, ndviewer_tab):
        """Test register_image does nothing when no viewer exists."""
        widget = ndviewer_tab
        assert widget._viewer is None

        # Should not raise
        widget.register_image(t=0, fov_idx=0, z=0, channel="BF", filepath="/tmp/img.tiff")

    def test_register_image_success(self, ndviewer_tab):
        """Test register_image calls viewer method on success."""
        widget = ndviewer_tab

        mock_viewer = Mock()
        widget._viewer = mock_viewer

        widget.register_image(t=1, fov_idx=2, z=3, channel="Fluorescence 488 nm Ex", filepath="/data/img.tiff")

        mock_viewer.register_image.assert_called_once_with(1, 2, 3, "Fluorescence 488 nm Ex", "/data/img.tiff")

    def test_register_image_exception_handled(self, ndviewer_tab):
        """Test register_image handles viewer exceptions gracefully."""
        widget = ndviewer_tab

        mock_viewer = Mock()
        mock_viewer.register_image.side_effect = RuntimeError("Registration failed")
        widget._viewer = mock_viewer

        # Should not raise
        widget.register_image(t=0, fov_idx=0, z=0, channel="BF", filepath="/tmp/img.tiff")

        mock_viewer.register_image.assert_called_once()

    # -------------------------------------------------------------------------
    # load_fov tests
    # -------------------------------------------------------------------------

    def test_load_fov_no_viewer_returns_false(self, ndviewer_tab):
        """Test load_fov returns False when no viewer exists."""
        widget = ndviewer_tab
        assert widget._viewer is None

        result = widget.load_fov(fov=0)

        assert result is False

    def test_load_fov_success_returns_true(self, ndviewer_tab):
        """Test load_fov returns True on success."""
        widget = ndviewer_tab

        mock_viewer = Mock()
        widget._viewer = mock_viewer

        result = widget.load_fov(fov=5, t=2, z=3)

        assert result is True
        mock_viewer.load_fov.assert_called_once_with(5, 2, 3)

    def test_load_fov_with_defaults(self, ndviewer_tab):
        """Test load_fov passes None for optional parameters."""
        widget = ndviewer_tab

        mock_viewer = Mock()
        widget._viewer = mock_viewer

        result = widget.load_fov(fov=0)

        assert result is True
        mock_viewer.load_fov.assert_called_once_with(0, None, None)

    def test_load_fov_exception_returns_false(self, ndviewer_tab):
        """Test load_fov returns False when viewer raises exception."""
        widget = ndviewer_tab

        mock_viewer = Mock()
        mock_viewer.load_fov.side_effect = RuntimeError("Load failed")
        widget._viewer = mock_viewer

        result = widget.load_fov(fov=0)

        assert result is False
        mock_viewer.load_fov.assert_called_once()

    # -------------------------------------------------------------------------
    # end_acquisition tests
    # -------------------------------------------------------------------------

    def test_end_acquisition_no_viewer(self, ndviewer_tab):
        """Test end_acquisition does nothing when no viewer exists."""
        widget = ndviewer_tab
        assert widget._viewer is None

        # Should not raise
        widget.end_acquisition()

    def test_end_acquisition_success(self, ndviewer_tab):
        """Test end_acquisition calls viewer method on success."""
        widget = ndviewer_tab

        mock_viewer = Mock()
        widget._viewer = mock_viewer

        widget.end_acquisition()

        mock_viewer.end_acquisition.assert_called_once()

    def test_end_acquisition_exception_handled(self, ndviewer_tab):
        """Test end_acquisition handles viewer exceptions gracefully."""
        widget = ndviewer_tab

        mock_viewer = Mock()
        mock_viewer.end_acquisition.side_effect = RuntimeError("End failed")
        widget._viewer = mock_viewer

        # Should not raise
        widget.end_acquisition()

        mock_viewer.end_acquisition.assert_called_once()
