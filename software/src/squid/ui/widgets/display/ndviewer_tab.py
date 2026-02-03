"""NDViewer tab widget for browsing acquisitions.

Provides an embedded lightweight NDViewer for viewing acquisition data
within the main GUI. Features:
- Lazy loading to minimize startup impact
- Auto-updates when acquisition starts
- Navigation from plate view double-click
- Push-based API for real-time image display during acquisition
"""

import os
from typing import List, Optional

from qtpy.QtCore import Qt, QTimer, Signal
from qtpy.QtWidgets import QLabel, QVBoxLayout, QWidget

import squid.core.logging
from squid.core.events import (
    EventBus,
    NDViewerAcquisitionEnded,
    NDViewerImageRegistered,
    NDViewerStartAcquisition,
    NDViewerStartZarrAcquisition,
    NDViewerStartZarrAcquisition6D,
    NDViewerZarrFrameWritten,
    auto_subscribe,
    auto_unsubscribe,
    handles,
)


class NDViewerTab(QWidget):
    """Embedded NDViewer (ndviewer_light) for showing acquisitions.

    Designed to live inside an existing QTabWidget.
    """

    _PLACEHOLDER_WAITING = "NDViewer: waiting for an acquisition to start..."

    # Qt signals for cross-thread dispatch from EventBus handlers to main thread
    # These signals can be emitted from any thread and will be received on the main thread
    _sig_start_acquisition = Signal(list, int, int, int, list, str)  # channels, num_z, height, width, fov_labels, experiment_id
    _sig_register_image = Signal(int, int, int, str, str, str)  # t, fov_idx, z, channel, filepath, experiment_id
    _sig_end_acquisition = Signal(str, str)  # experiment_id, dataset_path (empty string if None)
    # Zarr push-mode signals
    _sig_start_zarr_acquisition = Signal(list, list, int, list, int, int, str)  # fov_paths, channels, num_z, fov_labels, height, width, experiment_id
    _sig_start_zarr_acquisition_6d = Signal(list, list, int, list, int, int, list, str)  # region_paths, channels, num_z, fovs_per_region, height, width, region_labels, experiment_id
    _sig_notify_zarr_frame = Signal(int, int, int, str, str, int)  # t, fov_idx, z, channel, experiment_id, region_idx

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self._event_bus = event_bus
        self._viewer = None
        self._dataset_path: Optional[str] = None
        self._experiment_id: Optional[str] = None  # Track active push-mode acquisition
        self._subscriptions = []
        self._unsupported_extensions = set()

        self._layout = QVBoxLayout()
        self._layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(self._layout)

        self._placeholder = QLabel(self._PLACEHOLDER_WAITING)
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.addWidget(self._placeholder, 1)

        # Connect cross-thread signals to slots (queued connection ensures main thread execution)
        self._sig_start_acquisition.connect(self._handle_start_acquisition)
        self._sig_register_image.connect(self._handle_register_image)
        self._sig_end_acquisition.connect(self._handle_end_acquisition)
        self._sig_start_zarr_acquisition.connect(self._handle_start_zarr_acquisition)
        self._sig_start_zarr_acquisition_6d.connect(self._handle_start_zarr_acquisition_6d)
        self._sig_notify_zarr_frame.connect(self._handle_notify_zarr_frame)

        # Subscribe to EventBus events if available
        if self._event_bus is not None:
            self._subscriptions = auto_subscribe(self, self._event_bus)

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
        if dataset_path == self._dataset_path:
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
            from squid.ui.widgets.ndviewer_light import LightweightViewer
        except ImportError as e:
            self._log.error(f"Failed to import ndviewer_light: {e}")
            self._show_placeholder(f"NDViewer: failed to import ndviewer_light:\n{e}")
            return

        try:
            if self._viewer is None:
                self._viewer = LightweightViewer(dataset_path)
                self._layout.addWidget(self._viewer, 1)
            else:
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
        In push mode, uses the viewer's go_to_well_fov() method.
        In xarray mode, maps (well_id, fov_index) to the flat FOV dimension index.

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
            # Try push mode navigation first (more efficient during acquisition)
            if self._viewer.is_push_mode_active():
                if self._viewer.go_to_well_fov(well_id, fov_index):
                    self._log.info(
                        f"go_to_fov: navigated to well={well_id}, fov={fov_index} (push mode)"
                    )
                    return True
                self._log.debug(
                    f"go_to_fov: push mode go_to_well_fov failed for well={well_id}, fov={fov_index}"
                )
                return False

            # Fall back to xarray mode navigation
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
        # Unsubscribe from EventBus
        if self._event_bus is not None and self._subscriptions:
            auto_unsubscribe(self._subscriptions, self._event_bus)
            self._subscriptions = []

        if self._viewer is not None:
            try:
                # Calling close() triggers LightweightViewer.closeEvent(),
                # which stops refresh timers and closes open file handles
                self._viewer.close()
            except Exception:
                self._log.exception("Error closing LightweightViewer")
            self._viewer = None
        self._dataset_path = None
        self._experiment_id = None

    # ─────────────────────────────────────────────────────────────────────────
    # Push-based API for real-time acquisition display
    # ─────────────────────────────────────────────────────────────────────────

    def start_acquisition(
        self,
        channels: List[str],
        num_z: int,
        height: int,
        width: int,
        fov_labels: List[str],
        experiment_id: str,
    ) -> bool:
        """Configure NDViewer for push-mode acquisition.

        Called at acquisition start before any register_image() calls.

        Args:
            channels: Channel names, e.g. ["BF LED matrix full", "Fluorescence 488 nm Ex"]
            num_z: Number of z-levels
            height: Image height in pixels
            width: Image width in pixels
            fov_labels: FOV labels, e.g. ["A1:0", "A1:1", "A2:0"]
            experiment_id: Unique identifier for this acquisition

        Returns:
            True if push mode started successfully
        """
        self._log.debug(
            f"start_acquisition: {len(channels)} channels, {num_z} z, "
            f"{len(fov_labels)} FOVs, experiment={experiment_id}"
        )

        try:
            # Lazy import to minimize startup impact
            from squid.ui.widgets.ndviewer_light import LightweightViewer
        except ImportError as e:
            self._log.error(f"Failed to import ndviewer_light: {e}")
            self._show_placeholder(f"NDViewer: failed to import ndviewer_light:\n{e}")
            return False

        try:
            # Create viewer if needed
            if self._viewer is None:
                self._log.debug("Creating new LightweightViewer for push mode")
                self._viewer = LightweightViewer("")  # Empty path for push mode
                self._layout.addWidget(self._viewer, 1)

            # Start push-mode acquisition
            self._viewer.start_acquisition(channels, num_z, height, width, fov_labels)
            self._experiment_id = experiment_id

            self._viewer.setVisible(True)
            self._placeholder.setVisible(False)

            self._log.info(f"Push mode acquisition started: {experiment_id}")
            return True

        except Exception as e:
            self._log.exception("Failed to start push mode acquisition")
            error_msg = str(e) if str(e) else type(e).__name__
            self._show_placeholder(f"NDViewer: failed to start acquisition:\n{error_msg}")
            return False

    def register_image(
        self,
        t: int,
        fov_idx: int,
        z: int,
        channel: str,
        filepath: str,
        experiment_id: str,
    ) -> None:
        """Register a newly saved image file with the viewer.

        Called after each image is saved during acquisition.

        Args:
            t: Time index
            fov_idx: Flat FOV index across all wells
            z: Z-level index
            channel: Channel name
            filepath: Path to the saved image file
            experiment_id: Acquisition identifier (must match active acquisition)
        """
        if self._viewer is None:
            self._log.debug("register_image: no viewer")
            return

        if self._experiment_id != experiment_id:
            self._log.debug(
                f"register_image: ignoring image for experiment {experiment_id}, "
                f"active is {self._experiment_id}"
            )
            return

        if not filepath.lower().endswith((".tif", ".tiff")):
            ext = os.path.splitext(filepath)[1].lower()
            if ext not in self._unsupported_extensions:
                self._unsupported_extensions.add(ext)
                self._log.warning(
                    "NDViewer only supports uint16 TIFFs; ignoring %s files.",
                    ext or "(no extension)",
                )
            return

        try:
            self._viewer.register_image(t, fov_idx, z, channel, filepath)
        except Exception:
            self._log.exception(
                f"register_image failed: t={t}, fov={fov_idx}, z={z}, ch={channel}"
            )

    def end_acquisition(self, experiment_id: str, dataset_path: Optional[str] = None) -> None:
        """Mark the push-mode acquisition as ended.

        If no images were registered during push-mode (e.g., OME-TIFF mode),
        falls back to loading the dataset from the folder path.

        Args:
            experiment_id: Acquisition identifier (must match active acquisition)
            dataset_path: Optional path to dataset folder for file-based loading
        """
        if self._experiment_id != experiment_id:
            return

        self._log.debug(f"Acquisition ended: {experiment_id}")

        if self._viewer is not None:
            try:
                self._viewer.end_acquisition()
            except Exception:
                self._log.exception("end_acquisition failed")

            # Check if push-mode registered any images
            push_mode_has_data = (
                self._viewer.is_push_mode_active() and self._viewer.has_registered_images()
            )

            if not push_mode_has_data and dataset_path:
                # Fall back to file-based loading (for OME-TIFF or when push-mode didn't work)
                self._dataset_path = None
                # Delayed loading allows filesystem to sync after multiprocessing writes
                def start_retry() -> None:
                    self._load_dataset_with_retry(dataset_path, max_attempts=8, delay_ms=200)

                QTimer.singleShot(200, start_retry)

        self._experiment_id = None

    def _load_dataset_with_retry(
        self,
        dataset_path: str,
        attempt: int = 0,
        max_attempts: int = 8,
        delay_ms: int = 200,
    ) -> None:
        """Load dataset with retry for filesystem sync issues.

        Files may not be visible immediately after subprocess writers complete.
        This method retries loading with exponential backoff.
        """
        self.set_dataset_path(dataset_path)

        # Check if the viewer found any FOVs
        if self._viewer is not None:
            fov_list = self._viewer.get_fov_list()
            if fov_list:
                self._log.info(f"Loaded {len(fov_list)} FOVs from: {dataset_path}")
                return

        # No FOVs found - retry with delay if attempts remain
        if attempt < max_attempts - 1:
            next_delay = delay_ms * (2 ** attempt)  # Exponential backoff
            self._dataset_path = None  # Clear so next attempt reloads

            def retry() -> None:
                self._load_dataset_with_retry(dataset_path, attempt + 1, max_attempts, delay_ms)

            QTimer.singleShot(next_delay, retry)
        else:
            self._log.warning(f"Failed to load FOVs after {max_attempts} attempts: {dataset_path}")

    def load_fov(self, fov: int, t: Optional[int] = None, z: Optional[int] = None) -> bool:
        """Load a specific FOV in push mode.

        Args:
            fov: FOV index to load
            t: Optional time index (uses current if None)
            z: Optional z-level (uses current if None)

        Returns:
            True if load succeeded
        """
        if self._viewer is None:
            return False

        try:
            self._viewer.load_fov(fov, t, z)
            return True
        except Exception:
            self._log.exception(f"load_fov failed: fov={fov}, t={t}, z={z}")
            return False

    # ─────────────────────────────────────────────────────────────────────────
    # Zarr push-based API for real-time zarr acquisition display
    # ─────────────────────────────────────────────────────────────────────────

    def start_zarr_acquisition(
        self,
        fov_paths: List[str],
        channels: List[str],
        num_z: int,
        fov_labels: List[str],
        height: int,
        width: int,
        experiment_id: str,
    ) -> bool:
        """Configure NDViewer for zarr push-mode acquisition.

        Called at acquisition start when using ZARR_V3 format.
        The viewer opens zarr stores for live viewing as frames are written.

        Args:
            fov_paths: List of zarr paths per FOV
            channels: Channel names
            num_z: Number of z-levels
            fov_labels: FOV labels (e.g., ["A1:0", "A1:1"])
            height: Image height in pixels
            width: Image width in pixels
            experiment_id: Unique identifier for this acquisition

        Returns:
            True if zarr push mode started successfully
        """
        self._log.debug(
            f"start_zarr_acquisition: {len(fov_paths)} FOV paths, {len(channels)} channels, "
            f"{num_z} z, experiment={experiment_id}"
        )

        try:
            from squid.ui.widgets.ndviewer_light import LightweightViewer
        except ImportError as e:
            self._log.error(f"Failed to import ndviewer_light: {e}")
            self._show_placeholder(f"NDViewer: failed to import ndviewer_light:\n{e}")
            return False

        try:
            if self._viewer is None:
                self._log.debug("Creating new LightweightViewer for zarr push mode")
                self._viewer = LightweightViewer("")
                self._layout.addWidget(self._viewer, 1)

            self._viewer.start_zarr_acquisition(fov_paths, channels, num_z, fov_labels, height, width)
            self._experiment_id = experiment_id

            self._viewer.setVisible(True)
            self._placeholder.setVisible(False)

            self._log.info(f"Zarr push mode acquisition started: {experiment_id}")
            return True

        except Exception as e:
            self._log.exception("Failed to start zarr push mode acquisition")
            error_msg = str(e) if str(e) else type(e).__name__
            self._show_placeholder(f"NDViewer: failed to start zarr acquisition:\n{error_msg}")
            return False

    def notify_zarr_frame(
        self,
        t: int,
        fov_idx: int,
        z: int,
        channel: str,
        experiment_id: str,
        region_idx: int = 0,
    ) -> None:
        """Notify the viewer that a zarr frame has been written.

        Called after each frame is written to the zarr store.

        Args:
            t: Time index
            fov_idx: FOV index (flat for 5D, local for 6D)
            z: Z-level index
            channel: Channel name
            experiment_id: Acquisition identifier
            region_idx: Region index (for 6D mode)
        """
        if self._viewer is None:
            return
        if self._experiment_id != experiment_id:
            return

        try:
            self._viewer.notify_zarr_frame(t, fov_idx, z, channel)
        except Exception:
            self._log.exception(
                f"notify_zarr_frame failed: t={t}, fov={fov_idx}, z={z}, ch={channel}"
            )

    def start_zarr_acquisition_6d(
        self,
        region_paths: List[str],
        channels: List[str],
        num_z: int,
        fovs_per_region: List[int],
        height: int,
        width: int,
        region_labels: List[str],
        experiment_id: str,
    ) -> bool:
        """Configure NDViewer for 6D multi-region zarr acquisition.

        Each region has a single zarr store with shape (FOV, T, C, Z, Y, X).
        Delegates to start_zarr_acquisition_6d() on the viewer if available,
        otherwise falls back to start_zarr_acquisition() with flattened FOVs.

        Args:
            region_paths: Zarr paths per region
            channels: Channel names
            num_z: Number of z-levels
            fovs_per_region: FOV counts per region
            height: Image height in pixels
            width: Image width in pixels
            region_labels: Region labels
            experiment_id: Unique identifier for this acquisition

        Returns:
            True if started successfully
        """
        self._log.debug(
            f"start_zarr_acquisition_6d: {len(region_paths)} regions, "
            f"{len(channels)} channels, {num_z} z, experiment={experiment_id}"
        )

        try:
            from squid.ui.widgets.ndviewer_light import LightweightViewer
        except ImportError as e:
            self._log.error(f"Failed to import ndviewer_light: {e}")
            self._show_placeholder(f"NDViewer: failed to import ndviewer_light:\n{e}")
            return False

        try:
            if self._viewer is None:
                self._log.debug("Creating new LightweightViewer for 6D zarr push mode")
                self._viewer = LightweightViewer("")
                self._layout.addWidget(self._viewer, 1)

            if hasattr(self._viewer, "start_zarr_acquisition_6d"):
                self._viewer.start_zarr_acquisition_6d(
                    region_paths, channels, num_z, fovs_per_region, height, width, region_labels
                )
            else:
                # Fallback: flatten to 5D-compatible call
                fov_paths = []
                fov_labels = []
                for i, (rpath, rlabel, nfov) in enumerate(zip(region_paths, region_labels, fovs_per_region)):
                    for fov_idx in range(nfov):
                        fov_paths.append(rpath)
                        fov_labels.append(f"{rlabel}:{fov_idx}")
                self._viewer.start_zarr_acquisition(fov_paths, channels, num_z, fov_labels, height, width)

            self._experiment_id = experiment_id

            self._viewer.setVisible(True)
            self._placeholder.setVisible(False)

            self._log.info(f"6D zarr push mode acquisition started: {experiment_id}")
            return True

        except Exception as e:
            self._log.exception("Failed to start 6D zarr push mode acquisition")
            error_msg = str(e) if str(e) else type(e).__name__
            self._show_placeholder(f"NDViewer: failed to start 6D zarr acquisition:\n{error_msg}")
            return False

    # ─────────────────────────────────────────────────────────────────────────
    # EventBus handlers (run on dispatch thread, emit signals to main thread)
    # ─────────────────────────────────────────────────────────────────────────

    @handles(NDViewerStartAcquisition)
    def _on_ndviewer_start_acquisition(self, event: NDViewerStartAcquisition) -> None:
        """Handle NDViewerStartAcquisition event."""
        self._sig_start_acquisition.emit(
            event.channels, event.num_z, event.height, event.width,
            event.fov_labels, event.experiment_id,
        )

    @handles(NDViewerImageRegistered)
    def _on_ndviewer_image_registered(self, event: NDViewerImageRegistered) -> None:
        """Handle NDViewerImageRegistered event."""
        self._sig_register_image.emit(
            event.t, event.fov_idx, event.z, event.channel,
            event.filepath, event.experiment_id,
        )

    @handles(NDViewerAcquisitionEnded)
    def _on_ndviewer_acquisition_ended(self, event: NDViewerAcquisitionEnded) -> None:
        """Handle NDViewerAcquisitionEnded event."""
        self._sig_end_acquisition.emit(event.experiment_id, event.dataset_path or "")

    @handles(NDViewerStartZarrAcquisition)
    def _on_ndviewer_start_zarr_acquisition(self, event: NDViewerStartZarrAcquisition) -> None:
        """Handle NDViewerStartZarrAcquisition event."""
        self._sig_start_zarr_acquisition.emit(
            event.fov_paths, event.channels, event.num_z, event.fov_labels,
            event.height, event.width, event.experiment_id,
        )

    @handles(NDViewerStartZarrAcquisition6D)
    def _on_ndviewer_start_zarr_acquisition_6d(self, event: NDViewerStartZarrAcquisition6D) -> None:
        """Handle NDViewerStartZarrAcquisition6D event."""
        self._sig_start_zarr_acquisition_6d.emit(
            event.region_paths, event.channels, event.num_z, event.fovs_per_region,
            event.height, event.width, event.region_labels, event.experiment_id,
        )

    @handles(NDViewerZarrFrameWritten)
    def _on_ndviewer_zarr_frame_written(self, event: NDViewerZarrFrameWritten) -> None:
        """Handle NDViewerZarrFrameWritten event."""
        self._sig_notify_zarr_frame.emit(
            event.t, event.fov_idx, event.z, event.channel,
            event.experiment_id, event.region_idx,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Signal slots (run on main Qt thread)
    # ─────────────────────────────────────────────────────────────────────────

    def _handle_start_acquisition(
        self, channels: List[str], num_z: int, height: int, width: int,
        fov_labels: List[str], experiment_id: str,
    ) -> None:
        """Slot for _sig_start_acquisition."""
        self.start_acquisition(channels, num_z, height, width, fov_labels, experiment_id)

    def _handle_register_image(
        self, t: int, fov_idx: int, z: int, channel: str, filepath: str, experiment_id: str,
    ) -> None:
        """Slot for _sig_register_image."""
        self.register_image(t, fov_idx, z, channel, filepath, experiment_id)

    def _handle_end_acquisition(self, experiment_id: str, dataset_path: str) -> None:
        """Slot for _sig_end_acquisition."""
        self.end_acquisition(experiment_id, dataset_path or None)

    def _handle_start_zarr_acquisition(
        self, fov_paths: List[str], channels: List[str], num_z: int,
        fov_labels: List[str], height: int, width: int, experiment_id: str,
    ) -> None:
        """Slot for _sig_start_zarr_acquisition."""
        self.start_zarr_acquisition(fov_paths, channels, num_z, fov_labels, height, width, experiment_id)

    def _handle_start_zarr_acquisition_6d(
        self, region_paths: List[str], channels: List[str], num_z: int,
        fovs_per_region: List[int], height: int, width: int,
        region_labels: List[str], experiment_id: str,
    ) -> None:
        """Slot for _sig_start_zarr_acquisition_6d."""
        self.start_zarr_acquisition_6d(
            region_paths, channels, num_z, fovs_per_region,
            height, width, region_labels, experiment_id,
        )

    def _handle_notify_zarr_frame(
        self, t: int, fov_idx: int, z: int, channel: str, experiment_id: str, region_idx: int,
    ) -> None:
        """Slot for _sig_notify_zarr_frame."""
        self.notify_zarr_frame(t, fov_idx, z, channel, experiment_id, region_idx)
