"""Unit tests for AlignmentWidget."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
from qtpy.QtWidgets import QApplication

from squid.ui.widgets.stage.alignment_widget import AlignmentWidget


@pytest.fixture(scope="module")
def qapp():
    """Create QApplication for widget tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def mock_napari_viewer():
    """Create a mock napari viewer."""
    viewer = MagicMock()
    viewer.layers = MagicMock()
    viewer.layers.__contains__ = MagicMock(return_value=False)
    viewer.add_image = MagicMock()
    return viewer


class TestAlignmentWidgetInit:
    """Tests for AlignmentWidget initialization."""

    def test_initialization(self, qapp, mock_napari_viewer):
        """Test widget initializes with correct state."""
        widget = AlignmentWidget(mock_napari_viewer)

        assert widget.state == AlignmentWidget.STATE_ALIGN
        assert widget._offset_x_mm == 0.0
        assert widget._offset_y_mm == 0.0
        assert widget._has_offset is False
        assert widget._reference_fov_position is None
        assert widget.btn_align.text() == "Align"
        assert not widget.btn_align.isEnabled()

    def test_initial_button_disabled(self, qapp, mock_napari_viewer):
        """Test button is disabled on init (until live view starts)."""
        widget = AlignmentWidget(mock_napari_viewer)
        assert not widget.btn_align.isEnabled()


class TestAlignmentWidgetEnable:
    """Tests for enable/disable methods."""

    def test_enable(self, qapp, mock_napari_viewer):
        """Test enable enables the button."""
        widget = AlignmentWidget(mock_napari_viewer)
        assert not widget.btn_align.isEnabled()

        widget.enable()
        assert widget.btn_align.isEnabled()

    def test_enable_idempotent(self, qapp, mock_napari_viewer):
        """Test enable is idempotent."""
        widget = AlignmentWidget(mock_napari_viewer)
        widget.enable()
        widget.enable()  # Second call should not raise
        assert widget.btn_align.isEnabled()

    def test_disable(self, qapp, mock_napari_viewer):
        """Test disable disables the button."""
        widget = AlignmentWidget(mock_napari_viewer)
        widget.enable()
        assert widget.btn_align.isEnabled()

        widget.disable()
        assert not widget.btn_align.isEnabled()


class TestAlignmentWidgetOffset:
    """Tests for offset properties and methods."""

    def test_has_offset_false_initially(self, qapp, mock_napari_viewer):
        """Test has_offset is False initially."""
        widget = AlignmentWidget(mock_napari_viewer)
        assert widget.has_offset is False

    def test_offset_zero_when_no_offset(self, qapp, mock_napari_viewer):
        """Test offset properties return 0 when no offset set."""
        widget = AlignmentWidget(mock_napari_viewer)
        assert widget.offset_x_mm == 0.0
        assert widget.offset_y_mm == 0.0

    def test_apply_offset_no_offset(self, qapp, mock_napari_viewer):
        """Test apply_offset returns original coords when no offset."""
        widget = AlignmentWidget(mock_napari_viewer)

        result = widget.apply_offset(10.0, 20.0)
        assert result == (10.0, 20.0)

    def test_apply_offset_with_offset(self, qapp, mock_napari_viewer):
        """Test apply_offset applies offset correctly."""
        widget = AlignmentWidget(mock_napari_viewer)
        widget._has_offset = True
        widget._offset_x_mm = 1.5
        widget._offset_y_mm = -2.0

        result = widget.apply_offset(10.0, 20.0)
        assert result == (11.5, 18.0)

    def test_offset_values_with_offset(self, qapp, mock_napari_viewer):
        """Test offset properties return correct values when offset set."""
        widget = AlignmentWidget(mock_napari_viewer)
        widget._has_offset = True
        widget._offset_x_mm = 1.5
        widget._offset_y_mm = -2.0

        assert widget.offset_x_mm == 1.5
        assert widget.offset_y_mm == -2.0


class TestAlignmentWidgetStateMachine:
    """Tests for state machine transitions."""

    def test_initial_state_is_align(self, qapp, mock_napari_viewer):
        """Test initial state is ALIGN."""
        widget = AlignmentWidget(mock_napari_viewer)
        assert widget.state == AlignmentWidget.STATE_ALIGN

    def test_reset_returns_to_align_state(self, qapp, mock_napari_viewer):
        """Test reset returns to ALIGN state."""
        widget = AlignmentWidget(mock_napari_viewer)
        widget.state = AlignmentWidget.STATE_CONFIRM
        widget._has_offset = True
        widget._offset_x_mm = 1.0
        widget._offset_y_mm = 2.0
        widget._current_folder = "/some/path"

        widget.reset()

        assert widget.state == AlignmentWidget.STATE_ALIGN
        assert widget.btn_align.text() == "Align"
        assert widget._has_offset is False
        assert widget._offset_x_mm == 0.0
        assert widget._offset_y_mm == 0.0
        assert widget._current_folder is None


class TestAlignmentWidgetSetCurrentPosition:
    """Tests for set_current_position method."""

    def test_set_current_position_no_pending_request(self, qapp, mock_napari_viewer):
        """Test set_current_position does nothing without pending request."""
        widget = AlignmentWidget(mock_napari_viewer)
        widget._pending_position_request = False

        widget.set_current_position(10.0, 20.0)
        # Should not change state
        assert widget.state == AlignmentWidget.STATE_ALIGN

    def test_set_current_position_with_pending_request(self, qapp, mock_napari_viewer):
        """Test set_current_position completes confirmation with pending request."""
        widget = AlignmentWidget(mock_napari_viewer)
        widget.state = AlignmentWidget.STATE_CONFIRM
        widget._pending_position_request = True
        widget._reference_fov_position = (5.0, 10.0)

        # Mock QMessageBox to avoid UI interaction
        with patch("squid.ui.widgets.stage.alignment_widget.QMessageBox"):
            widget.set_current_position(6.5, 12.0)

        assert widget._pending_position_request is False
        assert widget._has_offset is True
        assert widget._offset_x_mm == 1.5  # 6.5 - 5.0
        assert widget._offset_y_mm == 2.0  # 12.0 - 10.0
        assert widget.state == AlignmentWidget.STATE_CLEAR


class TestAlignmentWidgetClearOffset:
    """Tests for clearing offset."""

    def test_handle_clear_click(self, qapp, mock_napari_viewer):
        """Test _handle_clear_click clears offset and returns to ALIGN."""
        widget = AlignmentWidget(mock_napari_viewer)
        widget.state = AlignmentWidget.STATE_CLEAR
        widget._has_offset = True
        widget._offset_x_mm = 1.0
        widget._offset_y_mm = 2.0
        widget._reference_fov_position = (5.0, 10.0)
        widget._current_folder = "/some/path"

        # Connect signal spy
        signal_called = []
        widget.signal_offset_cleared.connect(lambda: signal_called.append(True))

        widget._handle_clear_click()

        assert widget.state == AlignmentWidget.STATE_ALIGN
        assert widget.btn_align.text() == "Align"
        assert widget._has_offset is False
        assert widget._offset_x_mm == 0.0
        assert widget._offset_y_mm == 0.0
        assert widget._reference_fov_position is None
        assert widget._current_folder is None
        assert len(signal_called) == 1


class TestAlignmentWidgetFindCenterFov:
    """Tests for _find_center_fov method."""

    def test_find_center_fov_single_point(self, qapp, mock_napari_viewer):
        """Test finding center FOV with single point."""
        widget = AlignmentWidget(mock_napari_viewer)

        coords = pd.DataFrame({
            "x (mm)": [10.0],
            "y (mm)": [20.0],
        })

        result = widget._find_center_fov(coords)
        assert result == 0

    def test_find_center_fov_multiple_points(self, qapp, mock_napari_viewer):
        """Test finding center FOV with multiple points."""
        widget = AlignmentWidget(mock_napari_viewer)

        # Grid of points: center should be index 4
        coords = pd.DataFrame({
            "x (mm)": [0.0, 1.0, 2.0, 0.0, 1.0, 2.0, 0.0, 1.0, 2.0],
            "y (mm)": [0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 2.0, 2.0, 2.0],
        })

        result = widget._find_center_fov(coords)
        assert result == 4  # Center point (1.0, 1.0)


class TestAlignmentWidgetAcquisitionParsing:
    """Tests for acquisition folder parsing."""

    def test_load_acquisition_info_missing_coordinates(self, qapp, mock_napari_viewer, tmp_path):
        """Test _load_acquisition_info raises for missing coordinates.csv."""
        widget = AlignmentWidget(mock_napari_viewer)

        with pytest.raises(FileNotFoundError, match="coordinates.csv not found"):
            widget._load_acquisition_info(str(tmp_path))

    def test_load_acquisition_info_valid_folder(self, qapp, mock_napari_viewer, tmp_path):
        """Test _load_acquisition_info with valid folder structure."""
        widget = AlignmentWidget(mock_napari_viewer)

        # Create coordinates.csv
        coords_df = pd.DataFrame({
            "region": ["A1", "A1", "A1"],
            "x (mm)": [0.0, 1.0, 2.0],
            "y (mm)": [0.0, 0.0, 0.0],
            "fov": [0, 1, 2],
        })
        coords_df.to_csv(tmp_path / "coordinates.csv", index=False)

        # Create ome_tiff folder with reference image
        ome_folder = tmp_path / "ome_tiff"
        ome_folder.mkdir()
        # Create a dummy tiff file
        import tifffile
        test_image = np.zeros((100, 100), dtype=np.uint16)
        tifffile.imwrite(str(ome_folder / "A1_1.ome.tiff"), test_image)

        result = widget._load_acquisition_info(str(tmp_path))

        assert result["first_region"] == "A1"
        assert result["center_fov_index"] == 1  # Middle FOV
        assert result["center_fov_position"] == (1.0, 0.0)
        assert "A1_1.ome.tiff" in result["image_path"]


class TestAlignmentWidgetNapariIntegration:
    """Tests for napari layer management."""

    def test_add_reference_layer_modifies_live_view(self, qapp, mock_napari_viewer):
        """Test _add_reference_layer modifies Live View layer."""
        widget = AlignmentWidget(mock_napari_viewer)

        # Setup mock viewer with Live View layer
        mock_live_layer = MagicMock()
        mock_live_layer.opacity = 0.5
        mock_live_layer.blending = "translucent"
        mock_live_layer.colormap = "gray"
        mock_napari_viewer.layers.__contains__ = MagicMock(side_effect=lambda x: x == "Live View")
        mock_napari_viewer.layers.__getitem__ = MagicMock(return_value=mock_live_layer)

        test_image = np.zeros((100, 100), dtype=np.uint16)
        widget._add_reference_layer(test_image)

        # Verify Live View was modified
        assert mock_live_layer.opacity == 1.0
        assert mock_live_layer.blending == "additive"
        assert mock_live_layer.colormap == "green"
        assert widget._modified_live_view is True

        # Verify reference layer was added
        mock_napari_viewer.add_image.assert_called_once()
        call_kwargs = mock_napari_viewer.add_image.call_args[1]
        assert call_kwargs["name"] == AlignmentWidget.REFERENCE_LAYER_NAME
        assert call_kwargs["colormap"] == "magenta"

    def test_remove_reference_layer(self, qapp, mock_napari_viewer):
        """Test _remove_reference_layer removes layer and restores Live View."""
        widget = AlignmentWidget(mock_napari_viewer)
        widget._modified_live_view = True
        widget._original_live_opacity = 0.5
        widget._original_live_blending = "translucent"
        widget._original_live_colormap = "gray"

        # Setup mock viewer
        mock_live_layer = MagicMock()
        def contains_side_effect(x):
            return x in ["Live View", AlignmentWidget.REFERENCE_LAYER_NAME]
        mock_napari_viewer.layers.__contains__ = MagicMock(side_effect=contains_side_effect)
        mock_napari_viewer.layers.__getitem__ = MagicMock(return_value=mock_live_layer)
        mock_napari_viewer.layers.remove = MagicMock()

        widget._remove_reference_layer()

        # Verify reference layer removed
        mock_napari_viewer.layers.remove.assert_called_once_with(
            AlignmentWidget.REFERENCE_LAYER_NAME
        )

        # Verify Live View restored
        assert mock_live_layer.opacity == 0.5
        assert mock_live_layer.blending == "translucent"
        assert mock_live_layer.colormap == "gray"
        assert widget._modified_live_view is False


class TestAlignmentWidgetSignals:
    """Tests for signal emissions."""

    def test_signal_move_to_position_on_alignment_start(self, qapp, mock_napari_viewer, tmp_path):
        """Test signal_move_to_position emitted when alignment starts."""
        widget = AlignmentWidget(mock_napari_viewer)

        # Create coordinates.csv
        coords_df = pd.DataFrame({
            "region": ["A1"],
            "x (mm)": [5.0],
            "y (mm)": [10.0],
        })
        coords_df.to_csv(tmp_path / "coordinates.csv", index=False)

        # Create ome_tiff folder with reference image
        ome_folder = tmp_path / "ome_tiff"
        ome_folder.mkdir()
        import tifffile
        test_image = np.zeros((100, 100), dtype=np.uint16)
        tifffile.imwrite(str(ome_folder / "A1_0.ome.tiff"), test_image)

        # Connect signal spy
        signal_data = []
        widget.signal_move_to_position.connect(lambda x, y: signal_data.append((x, y)))

        widget._start_alignment(str(tmp_path))

        assert len(signal_data) == 1
        assert signal_data[0] == (5.0, 10.0)

    def test_signal_offset_set_on_confirmation(self, qapp, mock_napari_viewer):
        """Test signal_offset_set emitted when offset confirmed."""
        widget = AlignmentWidget(mock_napari_viewer)
        widget._reference_fov_position = (5.0, 10.0)

        # Connect signal spy
        signal_data = []
        widget.signal_offset_set.connect(lambda x, y: signal_data.append((x, y)))

        with patch("squid.ui.widgets.stage.alignment_widget.QMessageBox"):
            widget._complete_confirmation(6.5, 12.0)

        assert len(signal_data) == 1
        assert signal_data[0] == (1.5, 2.0)  # offset = current - reference

    def test_signal_request_current_position(self, qapp, mock_napari_viewer):
        """Test signal_request_current_position emitted on confirm click."""
        widget = AlignmentWidget(mock_napari_viewer)
        widget.state = AlignmentWidget.STATE_CONFIRM

        # Connect signal spy
        signal_called = []
        widget.signal_request_current_position.connect(lambda: signal_called.append(True))

        widget._handle_confirm_click()

        assert len(signal_called) == 1
        assert widget._pending_position_request is True
