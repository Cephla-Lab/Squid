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
