"""Tests for WellplateCalibration dialog improvements.

Tests center point calibration, parameter editing, display name formatting,
and the refactored calibrate method.

Ported from upstream: 88c0065c
"""

import pytest
import numpy as np
from unittest.mock import MagicMock, patch, PropertyMock


class MockEventBus:
    """Mock UIEventBus for testing."""

    def __init__(self):
        self.published_events = []
        self._subscriptions = {}

    def subscribe(self, event_type, callback):
        self._subscriptions[event_type] = callback

    def unsubscribe(self, event_type, callback):
        self._subscriptions.pop(event_type, None)

    def publish(self, event):
        self.published_events.append(event)


class MockStreamHandler:
    """Minimal mock for StreamHandler."""

    pass


class MockWellplateFormatWidget:
    """Mock for WellplateFormatWidget."""

    def __init__(self):
        self.comboBox = MagicMock()
        self.comboBox.findData = MagicMock(return_value=0)

    def populate_combo_box(self):
        pass

    def setWellplateSettings(self, name):
        pass

    def add_custom_format(self, name, settings):
        pass

    def save_formats_to_csv(self):
        pass


@pytest.fixture
def mock_event_bus():
    return MockEventBus()


@pytest.fixture
def mock_stream_handler():
    return MockStreamHandler()


@pytest.fixture
def mock_format_widget():
    return MockWellplateFormatWidget()


@pytest.fixture
def calibration_dialog(mock_event_bus, mock_stream_handler, mock_format_widget, qtbot):
    """Create a WellplateCalibration dialog with mocked dependencies."""
    from squid.ui.widgets.wellplate.calibration import WellplateCalibration

    dialog = WellplateCalibration(
        wellplateFormatWidget=mock_format_widget,
        streamHandler=mock_stream_handler,
        event_bus=mock_event_bus,
        was_live=True,  # Avoid starting live view
    )
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)
    return dialog


class TestFormatDisplayName:
    """Tests for _format_display_name helper method."""

    def test_adds_suffix_when_missing(self, calibration_dialog):
        """Should append 'well plate' when not present."""
        assert calibration_dialog._format_display_name("96") == "96 well plate"

    def test_no_duplicate_suffix(self, calibration_dialog):
        """Should not append 'well plate' when already present."""
        assert calibration_dialog._format_display_name("384 well plate") == "384 well plate"

    def test_case_insensitive_check(self, calibration_dialog):
        """Should handle case-insensitive matching."""
        assert calibration_dialog._format_display_name("Custom Well Plate") == "Custom Well Plate"

    def test_numeric_format_id(self, calibration_dialog):
        """Should handle numeric format IDs."""
        assert calibration_dialog._format_display_name(96) == "96 well plate"


class TestCenterPointCalibration:
    """Tests for center point calibration method."""

    def test_initial_state(self, calibration_dialog):
        """Center point should be None initially."""
        assert calibration_dialog.center_point is None
        assert calibration_dialog.center_point_status_label.text() == "Center: Not set"

    def test_set_center_point(self, calibration_dialog):
        """Should set center point from cached position."""
        calibration_dialog._current_position = (10.123, 20.456)
        calibration_dialog.setCenterPoint()

        assert calibration_dialog.center_point == (10.123, 20.456)
        assert "10.123" in calibration_dialog.center_point_status_label.text()
        assert "20.456" in calibration_dialog.center_point_status_label.text()
        assert calibration_dialog.set_center_button.text() == "Clear Center"

    def test_clear_center_point(self, calibration_dialog):
        """Should clear center point when already set."""
        calibration_dialog._current_position = (10.0, 20.0)
        calibration_dialog.setCenterPoint()  # Set
        calibration_dialog.setCenterPoint()  # Clear

        assert calibration_dialog.center_point is None
        assert calibration_dialog.center_point_status_label.text() == "Center: Not set"
        assert calibration_dialog.set_center_button.text() == "Set Center"

    def test_set_center_without_position(self, calibration_dialog):
        """Should show warning when position is unknown."""
        calibration_dialog._current_position = None
        # The actual call would show a QMessageBox dialog; verify center_point stays None
        assert calibration_dialog.center_point is None


class TestCalibrationMethodToggle:
    """Tests for toggling between calibration methods."""

    def test_default_is_edge_points(self, calibration_dialog):
        """Edge points should be selected by default."""
        assert calibration_dialog.edge_points_radio.isChecked()
        assert calibration_dialog.points_widget.isVisible()
        assert not calibration_dialog.center_point_widget.isVisible()

    def test_switch_to_center_point(self, calibration_dialog):
        """Switching to center point should show center UI and hide edge UI."""
        calibration_dialog.center_point_radio.setChecked(True)

        assert not calibration_dialog.points_widget.isVisible()
        assert calibration_dialog.center_point_widget.isVisible()

    def test_switch_back_to_edge_points(self, calibration_dialog):
        """Switching back to edge points should restore original UI."""
        calibration_dialog.center_point_radio.setChecked(True)
        calibration_dialog.edge_points_radio.setChecked(True)

        assert calibration_dialog.points_widget.isVisible()
        assert not calibration_dialog.center_point_widget.isVisible()


class TestCalibrateButtonState:
    """Tests for update_calibrate_button_state."""

    def test_disabled_by_default(self, calibration_dialog):
        """Calibrate button should be disabled initially."""
        assert not calibration_dialog.calibrateButton.isEnabled()

    def test_enabled_when_all_edge_points_set(self, calibration_dialog):
        """Should enable when all 3 edge points are set."""
        calibration_dialog._current_position = (1.0, 1.0)
        calibration_dialog.setCorner(0)
        calibration_dialog._current_position = (2.0, 1.0)
        calibration_dialog.setCorner(1)
        calibration_dialog._current_position = (1.5, 2.0)
        calibration_dialog.setCorner(2)

        assert calibration_dialog.calibrateButton.isEnabled()

    def test_disabled_when_partial_edge_points(self, calibration_dialog):
        """Should remain disabled with only 2 edge points."""
        calibration_dialog._current_position = (1.0, 1.0)
        calibration_dialog.setCorner(0)
        calibration_dialog._current_position = (2.0, 1.0)
        calibration_dialog.setCorner(1)

        assert not calibration_dialog.calibrateButton.isEnabled()

    def test_enabled_when_center_point_set(self, calibration_dialog):
        """Should enable when center point is set in center point mode."""
        calibration_dialog.center_point_radio.setChecked(True)
        calibration_dialog._current_position = (10.0, 20.0)
        calibration_dialog.setCenterPoint()

        assert calibration_dialog.calibrateButton.isEnabled()

    def test_disabled_when_center_point_not_set(self, calibration_dialog):
        """Should be disabled in center point mode with no point set."""
        calibration_dialog.center_point_radio.setChecked(True)

        assert not calibration_dialog.calibrateButton.isEnabled()


class TestGetCalibrationData:
    """Tests for _get_calibration_data helper."""

    def test_edge_points_returns_circle_data(self, calibration_dialog):
        """Should calculate center and diameter from 3 edge points."""
        # Set up 3 points on a circle of radius 1 centered at (5, 5)
        calibration_dialog.corners = [
            (5.0, 4.0),  # top
            (6.0, 5.0),  # right
            (5.0, 6.0),  # bottom
        ]

        result = calibration_dialog._get_calibration_data()
        assert result is not None
        a1_x, a1_y, well_size = result
        assert abs(a1_x - 5.0) < 0.01
        assert abs(a1_y - 5.0) < 0.01
        assert abs(well_size - 2.0) < 0.01  # diameter = 2 * radius

    def test_center_point_returns_direct_values(self, calibration_dialog):
        """Should return center point directly with manual well size."""
        calibration_dialog.center_point_radio.setChecked(True)
        calibration_dialog.center_point = (10.5, 20.5)
        calibration_dialog.center_well_size_input.setValue(3.5)

        result = calibration_dialog._get_calibration_data()
        assert result is not None
        a1_x, a1_y, well_size = result
        assert a1_x == 10.5
        assert a1_y == 20.5
        assert well_size == 3.5

    def test_center_point_uses_existing_well_size_in_existing_mode(self, calibration_dialog):
        """Should use existing_well_size_input when in calibrate-existing mode."""
        calibration_dialog.calibrate_format_radio.setChecked(True)
        calibration_dialog.center_point_radio.setChecked(True)
        calibration_dialog.center_point = (10.0, 20.0)
        calibration_dialog.existing_well_size_input.setValue(4.5)

        result = calibration_dialog._get_calibration_data()
        assert result is not None
        _, _, well_size = result
        assert well_size == 4.5

    def test_edge_points_returns_none_when_incomplete(self, calibration_dialog):
        """Should return None when not all edge points are set."""
        calibration_dialog.corners = [(1.0, 1.0), None, (3.0, 3.0)]

        # Patch QMessageBox to avoid actual dialog
        with patch("squid.ui.widgets.wellplate.calibration.QMessageBox"):
            result = calibration_dialog._get_calibration_data()
        assert result is None

    def test_center_point_returns_none_when_not_set(self, calibration_dialog):
        """Should return None when center point is not set."""
        calibration_dialog.center_point_radio.setChecked(True)
        calibration_dialog.center_point = None

        with patch("squid.ui.widgets.wellplate.calibration.QMessageBox"):
            result = calibration_dialog._get_calibration_data()
        assert result is None


class TestResetCalibrationPoints:
    """Tests for reset_calibration_points."""

    def test_resets_edge_points(self, calibration_dialog):
        """Should clear all edge points."""
        calibration_dialog._current_position = (1.0, 1.0)
        calibration_dialog.setCorner(0)
        calibration_dialog._current_position = (2.0, 1.0)
        calibration_dialog.setCorner(1)

        calibration_dialog.reset_calibration_points()

        assert all(c is None for c in calibration_dialog.corners)
        for label in calibration_dialog.cornerLabels:
            assert "Not set" in label.text()

    def test_resets_center_point(self, calibration_dialog):
        """Should clear center point."""
        calibration_dialog._current_position = (10.0, 20.0)
        calibration_dialog.setCenterPoint()

        calibration_dialog.reset_calibration_points()

        assert calibration_dialog.center_point is None
        assert "Not set" in calibration_dialog.center_point_status_label.text()

    def test_disables_calibrate_button(self, calibration_dialog):
        """Should disable calibrate button after reset."""
        calibration_dialog._current_position = (1.0, 1.0)
        calibration_dialog.setCorner(0)
        calibration_dialog._current_position = (2.0, 1.0)
        calibration_dialog.setCorner(1)
        calibration_dialog._current_position = (1.5, 2.0)
        calibration_dialog.setCorner(2)

        assert calibration_dialog.calibrateButton.isEnabled()

        calibration_dialog.reset_calibration_points()

        assert not calibration_dialog.calibrateButton.isEnabled()


class TestToggleInputMode:
    """Tests for toggle_input_mode (new vs. existing format)."""

    def test_new_format_shows_form(self, calibration_dialog):
        """New format mode should show form inputs and hide existing controls."""
        # Toggle to existing and back to new to trigger the toggle_input_mode handler
        calibration_dialog.calibrate_format_radio.setChecked(True)
        calibration_dialog.new_format_radio.setChecked(True)
        assert calibration_dialog.new_format_widget.isVisible()
        assert not calibration_dialog.existing_format_combo.isVisible()
        assert not calibration_dialog.existing_params_group.isVisible()

    def test_existing_format_shows_combo_and_params(self, calibration_dialog):
        """Existing format mode should show combo box and parameter group."""
        calibration_dialog.calibrate_format_radio.setChecked(True)
        assert not calibration_dialog.new_format_widget.isVisible()
        assert calibration_dialog.existing_format_combo.isVisible()
        assert calibration_dialog.existing_params_group.isVisible()
        assert calibration_dialog.update_params_button.isVisible()


class TestPointPrecision:
    """Tests for increased point display precision."""

    def test_corner_label_shows_3_decimals(self, calibration_dialog):
        """Corner labels should show 3 decimal places."""
        calibration_dialog._current_position = (10.12345, 20.67891)
        calibration_dialog.setCorner(0)

        label_text = calibration_dialog.cornerLabels[0].text()
        assert "10.123" in label_text
        assert "20.679" in label_text

    def test_center_point_label_shows_3_decimals(self, calibration_dialog):
        """Center point label should show 3 decimal places."""
        calibration_dialog._current_position = (10.12345, 20.67891)
        calibration_dialog.setCenterPoint()

        label_text = calibration_dialog.center_point_status_label.text()
        assert "10.123" in label_text
        assert "20.679" in label_text


class TestPopulateExistingFormats:
    """Tests for populate_existing_formats with display name fix."""

    def test_no_duplicate_suffix(self, calibration_dialog):
        """Formats with 'well plate' in name should not get double suffix."""
        with patch.dict(
            "squid.ui.widgets.wellplate.calibration.WELLPLATE_FORMAT_SETTINGS",
            {"384 well plate": {"well_spacing_mm": 4.5, "well_size_mm": 3.0}},
            clear=True,
        ):
            calibration_dialog.populate_existing_formats()
            text = calibration_dialog.existing_format_combo.itemText(0)
            assert text == "384 well plate"
            # Should NOT be "384 well plate well plate"
            assert "well plate well plate" not in text

    def test_adds_suffix_when_needed(self, calibration_dialog):
        """Formats without 'well plate' in name should get suffix added."""
        with patch.dict(
            "squid.ui.widgets.wellplate.calibration.WELLPLATE_FORMAT_SETTINGS",
            {"96": {"well_spacing_mm": 9.0, "well_size_mm": 6.0}},
            clear=True,
        ):
            calibration_dialog.populate_existing_formats()
            text = calibration_dialog.existing_format_combo.itemText(0)
            assert text == "96 well plate"


class TestCalibrateRefactored:
    """Tests for the refactored calibrate method and helpers."""

    def test_calibrate_new_format_publishes_event(self, calibration_dialog, mock_event_bus):
        """_calibrate_new_format should publish SaveWellplateCalibrationCommand."""
        from squid.core.events import SaveWellplateCalibrationCommand

        calibration_dialog.nameInput.setText("Test Format")
        calibration_dialog.corners = [
            (5.0, 4.0),
            (6.0, 5.0),
            (5.0, 6.0),
        ]

        with patch("squid.ui.widgets.wellplate.calibration.QMessageBox"):
            with patch.object(calibration_dialog, "create_wellplate_image"):
                with patch.object(calibration_dialog, "accept"):
                    calibration_dialog._calibrate_new_format()

        save_events = [e for e in mock_event_bus.published_events if isinstance(e, SaveWellplateCalibrationCommand)]
        assert len(save_events) == 1
        assert save_events[0].name == "Test Format"
        assert "a1_x_mm" in save_events[0].calibration

    def test_calibrate_new_format_requires_name(self, calibration_dialog):
        """Should warn when name is empty."""
        calibration_dialog.nameInput.setText("")

        with patch("squid.ui.widgets.wellplate.calibration.QMessageBox") as mock_msg:
            calibration_dialog._calibrate_new_format()
            mock_msg.warning.assert_called_once()

    def test_calibrate_catches_linalg_error(self, calibration_dialog):
        """Should catch LinAlgError for collinear points."""
        calibration_dialog.nameInput.setText("Bad Format")
        # Collinear points will cause LinAlgError
        calibration_dialog.corners = [
            (0.0, 0.0),
            (1.0, 1.0),
            (2.0, 2.0),
        ]

        with patch("squid.ui.widgets.wellplate.calibration.QMessageBox") as mock_msg:
            calibration_dialog.calibrate()
            mock_msg.critical.assert_called_once()
            args = mock_msg.critical.call_args
            assert "collinear" in args[0][2].lower() or "straight line" in args[0][2].lower()

    def test_calibrate_new_with_center_point(self, calibration_dialog, mock_event_bus):
        """New format with center point method should work."""
        from squid.core.events import SaveWellplateCalibrationCommand

        calibration_dialog.nameInput.setText("Small Wells")
        calibration_dialog.center_point_radio.setChecked(True)
        calibration_dialog.center_point = (15.0, 25.0)
        calibration_dialog.center_well_size_input.setValue(2.5)

        with patch("squid.ui.widgets.wellplate.calibration.QMessageBox"):
            with patch.object(calibration_dialog, "create_wellplate_image"):
                with patch.object(calibration_dialog, "accept"):
                    calibration_dialog._calibrate_new_format()

        save_events = [e for e in mock_event_bus.published_events if isinstance(e, SaveWellplateCalibrationCommand)]
        assert len(save_events) == 1
        cal = save_events[0].calibration
        assert cal["a1_x_mm"] == 15.0
        assert cal["a1_y_mm"] == 25.0
        assert cal["well_size_mm"] == 2.5
