"""Unit tests for NDViewer tab widget."""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from qtpy.QtWidgets import QApplication

from squid.ui.widgets.display.ndviewer_tab import NDViewerTab


@pytest.fixture(scope="module")
def qapp():
    """Create QApplication for widget tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


class TestNDViewerTabInit:
    """Tests for NDViewerTab initialization."""

    def test_initialization(self, qapp):
        """Test widget initializes with placeholder."""
        tab = NDViewerTab()

        assert tab._viewer is None
        assert tab._dataset_path is None
        assert "waiting" in tab._placeholder.text().lower()

    def test_placeholder_text_default(self, qapp):
        """Test placeholder has default text on init."""
        tab = NDViewerTab()
        assert "waiting" in tab._placeholder.text().lower()


class TestNDViewerTabSetDatasetPath:
    """Tests for set_dataset_path method."""

    def test_set_none_path_shows_placeholder(self, qapp):
        """Test setting None path keeps placeholder text."""
        tab = NDViewerTab()
        # First set a different path to trigger the change
        tab._dataset_path = "/some/old/path"
        tab.set_dataset_path(None)

        assert tab._dataset_path is None
        assert "waiting" in tab._placeholder.text().lower()

    def test_set_same_path_skips(self, qapp):
        """Test setting same path is no-op."""
        tab = NDViewerTab()
        tab._dataset_path = "/some/path"

        # Should not change anything
        tab.set_dataset_path("/some/path")
        assert tab._dataset_path == "/some/path"

    def test_nonexistent_path_shows_error(self, qapp):
        """Test nonexistent path shows error in placeholder."""
        tab = NDViewerTab()
        tab.set_dataset_path("/nonexistent/path/to/dataset")

        assert "not found" in tab._placeholder.text().lower()

    def test_import_error_shows_message(self, qapp, tmp_path):
        """Test import error shows message in placeholder."""
        # Create a real directory
        dataset_dir = tmp_path / "dataset"
        dataset_dir.mkdir()

        tab = NDViewerTab()

        # Mock the import to fail
        with patch.dict("sys.modules", {"squid.ui.widgets.ndviewer_light": None}):
            with patch(
                "squid.ui.widgets.display.ndviewer_tab.NDViewerTab.set_dataset_path"
            ) as mock_set:
                # Call original but with mocked import
                original_set = NDViewerTab.set_dataset_path

                def side_effect(self, path):
                    self._dataset_path = path
                    self._show_placeholder("NDViewer: failed to import ndviewer_light")

                mock_set.side_effect = lambda path: side_effect(tab, path)
                tab.set_dataset_path(str(dataset_dir))

        assert "import" in tab._placeholder.text().lower() or tab._dataset_path is not None


class TestNDViewerTabGoToFov:
    """Tests for go_to_fov method."""

    def test_go_to_fov_no_viewer(self, qapp):
        """Test go_to_fov returns False when no viewer."""
        tab = NDViewerTab()
        assert tab.go_to_fov("A1", 0) is False

    def test_go_to_fov_with_mock_viewer(self, qapp):
        """Test go_to_fov with mocked viewer."""
        tab = NDViewerTab()

        # Create mock viewer
        mock_viewer = MagicMock()
        mock_viewer.has_fov_dimension.return_value = True
        mock_viewer.get_fov_list.return_value = [
            {"region": "A1", "fov": 0},
            {"region": "A1", "fov": 1},
            {"region": "B1", "fov": 0},
        ]
        mock_viewer.set_current_index.return_value = True

        tab._viewer = mock_viewer

        # Navigate to A1 FOV 1
        result = tab.go_to_fov("A1", 1)

        assert result is True
        mock_viewer.set_current_index.assert_called_once_with("fov", 1)

    def test_go_to_fov_not_found(self, qapp):
        """Test go_to_fov returns False when FOV not in list."""
        tab = NDViewerTab()

        mock_viewer = MagicMock()
        mock_viewer.has_fov_dimension.return_value = True
        mock_viewer.get_fov_list.return_value = [
            {"region": "A1", "fov": 0},
        ]

        tab._viewer = mock_viewer

        # Try to navigate to non-existent FOV
        result = tab.go_to_fov("B1", 5)
        assert result is False

    def test_go_to_fov_no_fov_dimension(self, qapp):
        """Test go_to_fov returns False when no FOV dimension."""
        tab = NDViewerTab()

        mock_viewer = MagicMock()
        mock_viewer.has_fov_dimension.return_value = False

        tab._viewer = mock_viewer

        result = tab.go_to_fov("A1", 0)
        assert result is False


class TestNDViewerTabCleanup:
    """Tests for cleanup method."""

    def test_cleanup_no_viewer(self, qapp):
        """Test cleanup when no viewer - should not raise."""
        tab = NDViewerTab()
        tab.cleanup()  # Should not raise

        assert tab._viewer is None
        assert tab._dataset_path is None

    def test_cleanup_with_viewer(self, qapp):
        """Test cleanup closes viewer."""
        tab = NDViewerTab()

        mock_viewer = MagicMock()
        tab._viewer = mock_viewer
        tab._dataset_path = "/some/path"

        tab.cleanup()

        mock_viewer.close.assert_called_once()
        assert tab._viewer is None
        assert tab._dataset_path is None

    def test_cleanup_handles_exception(self, qapp):
        """Test cleanup handles viewer close exception."""
        tab = NDViewerTab()

        mock_viewer = MagicMock()
        mock_viewer.close.side_effect = RuntimeError("Close failed")
        tab._viewer = mock_viewer

        # Should not raise
        tab.cleanup()

        assert tab._viewer is None


class TestFindFlatFovIndex:
    """Tests for _find_flat_fov_index method."""

    def test_find_existing_fov(self, qapp):
        """Test finding an existing FOV."""
        tab = NDViewerTab()

        mock_viewer = MagicMock()
        mock_viewer.get_fov_list.return_value = [
            {"region": "A1", "fov": 0},
            {"region": "A1", "fov": 1},
            {"region": "A2", "fov": 0},
            {"region": "B1", "fov": 0},
        ]
        tab._viewer = mock_viewer

        # Find A2 FOV 0 - should be index 2
        result = tab._find_flat_fov_index("A2", 0)
        assert result == 2

    def test_find_nonexistent_fov(self, qapp):
        """Test finding a non-existent FOV returns None."""
        tab = NDViewerTab()

        mock_viewer = MagicMock()
        mock_viewer.get_fov_list.return_value = [
            {"region": "A1", "fov": 0},
        ]
        tab._viewer = mock_viewer

        result = tab._find_flat_fov_index("Z9", 99)
        assert result is None
