"""NDViewer tab widget for browsing acquisitions.

Provides an embedded lightweight NDViewer for viewing acquisition data
within the main GUI. Features:
- Lazy loading to minimize startup impact
- Auto-updates when acquisition starts
- Navigation from plate view double-click
"""

import os
from typing import Optional

from qtpy.QtCore import Qt
from qtpy.QtWidgets import QLabel, QVBoxLayout, QWidget

import squid.core.logging


class NDViewerTab(QWidget):
    """Embedded NDViewer (ndviewer_light) for showing acquisitions.

    Designed to live inside an existing QTabWidget.
    """

    _PLACEHOLDER_WAITING = "NDViewer: waiting for an acquisition to start..."

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self._viewer = None
        self._dataset_path: Optional[str] = None

        self._layout = QVBoxLayout()
        self._layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(self._layout)

        self._placeholder = QLabel(self._PLACEHOLDER_WAITING)
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.addWidget(self._placeholder, 1)

    def _show_placeholder(self, message: str) -> None:
        """Show placeholder with message and hide viewer."""
        self._placeholder.setText(message)
        self._placeholder.setVisible(True)
        if self._viewer is not None:
            self._viewer.setVisible(False)

    def set_dataset_path(self, dataset_path: Optional[str]) -> None:
        """Point the embedded NDViewer at a dataset folder and refresh.

        Pass None to clear the view.

        Args:
            dataset_path: Path to acquisition dataset folder, or None to clear
        """
        self._log.debug(f"set_dataset_path called with: {dataset_path}")

        if dataset_path == self._dataset_path:
            self._log.debug("Dataset path unchanged, skipping")
            return
        self._dataset_path = dataset_path

        if not dataset_path:
            self._show_placeholder(self._PLACEHOLDER_WAITING)
            return

        if not os.path.isdir(dataset_path):
            self._log.warning(f"Dataset folder not found: {dataset_path}")
            self._show_placeholder(f"NDViewer: dataset folder not found:\n{dataset_path}")
            return

        try:
            # Lazy import to minimize startup impact
            # Import from the submodule location in arch_v2
            from squid.ui.widgets.ndviewer_light import LightweightViewer
        except ImportError as e:
            self._log.error(f"Failed to import ndviewer_light: {e}")
            self._show_placeholder(f"NDViewer: failed to import ndviewer_light:\n{e}")
            return

        try:
            if self._viewer is None:
                self._log.debug(f"Creating new LightweightViewer for: {dataset_path}")
                self._viewer = LightweightViewer(dataset_path)
                self._layout.addWidget(self._viewer, 1)
                self._log.debug("LightweightViewer created")
            else:
                self._log.debug(f"Reloading dataset: {dataset_path}")
                self._viewer.load_dataset(dataset_path)
                self._viewer.refresh()

            self._viewer.setVisible(True)
            self._placeholder.setVisible(False)
        except Exception as e:
            self._log.exception("NDViewerTab failed to load dataset")
            error_msg = str(e) if str(e) else type(e).__name__
            self._show_placeholder(
                f"NDViewer: failed to load dataset:\n{dataset_path}\n\nError: {error_msg}"
            )

    def go_to_fov(self, well_id: str, fov_index: int) -> bool:
        """Navigate the NDViewer to a specific well and FOV.

        Called when user double-clicks a location in the plate view.
        Maps (well_id, fov_index) to the flat xarray FOV dimension index.

        Args:
            well_id: Well identifier (e.g., "A1", "B2")
            fov_index: FOV index within that well

        Returns:
            True if navigation succeeded, False otherwise
        """
        if self._viewer is None:
            self._log.debug("go_to_fov: no viewer loaded")
            return False

        try:
            if not self._viewer.has_fov_dimension():
                self._log.debug("go_to_fov: no fov dimension available")
                return False

            target_flat_idx = self._find_flat_fov_index(well_id, fov_index)
            if target_flat_idx is None:
                self._log.debug(
                    f"go_to_fov: could not find FOV for well={well_id}, fov={fov_index}"
                )
                return False

            if self._viewer.set_current_index("fov", target_flat_idx):
                self._log.info(
                    f"go_to_fov: navigated to well={well_id}, fov={fov_index} "
                    f"(flat_idx={target_flat_idx})"
                )
                return True

            self._log.debug(f"go_to_fov: set_current_index failed for fov={target_flat_idx}")
            return False
        except Exception:
            self._log.exception(f"go_to_fov: unexpected error for well={well_id}, fov={fov_index}")
            return False

    def _find_flat_fov_index(self, well_id: str, fov_index: int) -> Optional[int]:
        """Find the flat xarray FOV index for a given (well_id, fov_index).

        The xarray FOV dimension is a flat list of all FOVs across all wells.
        Uses the viewer's public get_fov_list() API to get the FOV mapping.

        Args:
            well_id: Well identifier
            fov_index: FOV index within the well

        Returns:
            The flat index if found, None otherwise
        """
        fovs = self._viewer.get_fov_list()
        return next(
            (
                i
                for i, fov in enumerate(fovs)
                if fov["region"] == well_id and fov["fov"] == fov_index
            ),
            None,
        )

    def cleanup(self) -> None:
        """Clean up viewer resources.

        Call this before the widget is destroyed to release file handles
        and stop timers.
        """
        if self._viewer is not None:
            try:
                # Calling close() triggers LightweightViewer.closeEvent(),
                # which stops refresh timers and closes open file handles
                self._viewer.close()
            except Exception:
                self._log.exception("Error closing LightweightViewer")
            self._viewer = None
        self._dataset_path = None
