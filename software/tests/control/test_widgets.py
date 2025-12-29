import logging
from unittest.mock import MagicMock, patch

import control._def
import control.microscope
from control.widgets import check_ram_available_with_error_dialog

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
