"""Alignment widget for sample registration with previous acquisitions.

Allows users to align current sample position with a previous acquisition by:
1. Loading a past acquisition folder
2. Moving stage to a reference FOV position
3. Displaying reference image as translucent overlay
4. Calculating X/Y offset after manual alignment
5. Applying offset to future scan coordinates
"""

from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np
import pandas as pd
from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import QFileDialog, QHBoxLayout, QMessageBox, QPushButton, QWidget

import squid.core.logging


class AlignmentWidget(QWidget):
    """Self-contained widget for alignment workflow.

    The widget manages its own state and napari layers, communicating with
    external components (stage, live controller) via signals.
    """

    signal_move_to_position = Signal(float, float)  # x_mm, y_mm
    signal_offset_set = Signal(float, float)  # offset_x_mm, offset_y_mm
    signal_offset_cleared = Signal()
    signal_request_current_position = Signal()  # Response via set_current_position()

    # Button states
    STATE_ALIGN = "align"
    STATE_CONFIRM = "confirm"
    STATE_CLEAR = "clear"

    # Napari layer name
    REFERENCE_LAYER_NAME = "Alignment Reference"

    def __init__(self, napari_viewer, parent: Optional[QWidget] = None):
        """Initialize alignment widget.

        Args:
            napari_viewer: The napari viewer instance for layer management
            parent: Parent widget
        """
        super().__init__(parent)
        self._log = squid.core.logging.get_logger(self.__class__.__name__)

        self.viewer = napari_viewer
        self.state = self.STATE_ALIGN

        # Alignment state
        self._offset_x_mm = 0.0
        self._offset_y_mm = 0.0
        self._has_offset = False
        self._reference_fov_position: Optional[Tuple[float, float]] = None
        self._current_folder: Optional[str] = None
        self._original_live_opacity = 1.0
        self._original_live_blending = "additive"
        self._original_live_colormap = "gray"
        self._modified_live_view = False
        self._pending_position_request = False

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the button UI."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.btn_align = QPushButton("Align")
        self.btn_align.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_align.setMinimumWidth(100)  # Wide enough for "Confirm Offset"
        self.btn_align.setEnabled(False)  # Disabled until live view starts
        self.btn_align.clicked.connect(self._on_button_clicked)
        layout.addWidget(self.btn_align)

    def enable(self) -> None:
        """Enable the alignment button. Call when live view starts."""
        if not self.btn_align.isEnabled():
            self.btn_align.setEnabled(True)

    def disable(self) -> None:
        """Disable the alignment button."""
        if self.btn_align.isEnabled():
            self.btn_align.setEnabled(False)

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    @property
    def has_offset(self) -> bool:
        """Check if an alignment offset is currently active."""
        return self._has_offset

    @property
    def offset_x_mm(self) -> float:
        """Get X offset in mm (0 if no offset)."""
        return self._offset_x_mm if self._has_offset else 0.0

    @property
    def offset_y_mm(self) -> float:
        """Get Y offset in mm (0 if no offset)."""
        return self._offset_y_mm if self._has_offset else 0.0

    def apply_offset(self, x_mm: float, y_mm: float) -> Tuple[float, float]:
        """Apply the current alignment offset to coordinates.

        Args:
            x_mm: Original X coordinate in mm
            y_mm: Original Y coordinate in mm

        Returns:
            Tuple of (adjusted_x_mm, adjusted_y_mm)
        """
        return (x_mm + self.offset_x_mm, y_mm + self.offset_y_mm)

    def set_current_position(self, x_mm: float, y_mm: float) -> None:
        """Receive current stage position (response to signal_request_current_position).

        Called by main window when position is requested during confirm step.

        Args:
            x_mm: Current stage X position in mm
            y_mm: Current stage Y position in mm
        """
        if self._pending_position_request:
            self._pending_position_request = False
            self._complete_confirmation(x_mm, y_mm)

    def reset(self) -> None:
        """Reset widget to initial state."""
        self.state = self.STATE_ALIGN
        self.btn_align.setText("Align")
        self._current_folder = None
        self._reference_fov_position = None
        self._has_offset = False
        self._offset_x_mm = 0.0
        self._offset_y_mm = 0.0
        self._remove_reference_layer()

    # ─────────────────────────────────────────────────────────────────────────
    # Button Click Handler
    # ─────────────────────────────────────────────────────────────────────────

    def _on_button_clicked(self) -> None:
        """Handle button click based on current state."""
        if self.state == self.STATE_ALIGN:
            self._handle_align_click()
        elif self.state == self.STATE_CONFIRM:
            self._handle_confirm_click()
        elif self.state == self.STATE_CLEAR:
            self._handle_clear_click()

    def _handle_align_click(self) -> None:
        """Handle click in ALIGN state - open folder dialog."""
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Past Acquisition Folder",
            str(Path.home()),
        )
        if folder:
            self._start_alignment(folder)

    def _handle_confirm_click(self) -> None:
        """Handle click in CONFIRM state - request position and calculate offset."""
        self._pending_position_request = True
        self.signal_request_current_position.emit()

    def _handle_clear_click(self) -> None:
        """Handle click in CLEAR state - clear offset."""
        self._offset_x_mm = 0.0
        self._offset_y_mm = 0.0
        self._has_offset = False
        self._reference_fov_position = None
        self._current_folder = None

        self.state = self.STATE_ALIGN
        self.btn_align.setText("Align")

        self._remove_reference_layer()

        self.signal_offset_cleared.emit()
        self._log.info("Alignment offset cleared")

    # ─────────────────────────────────────────────────────────────────────────
    # Alignment Workflow
    # ─────────────────────────────────────────────────────────────────────────

    def _start_alignment(self, folder_path: str) -> None:
        """Start alignment workflow with selected folder."""
        try:
            info = self._load_acquisition_info(folder_path)
            self._current_folder = folder_path
            ref_x, ref_y = info["center_fov_position"]
            self._reference_fov_position = (ref_x, ref_y)

            self.state = self.STATE_CONFIRM
            self.btn_align.setText("Confirm Offset")

            self.signal_move_to_position.emit(ref_x, ref_y)
            self._load_reference_image(info["image_path"])
            self._log.info(f"Alignment started: ref_pos=({ref_x:.4f}, {ref_y:.4f})")

        except Exception as e:
            self._log.error(f"Failed to start alignment: {e}")
            QMessageBox.warning(self, "Alignment Error", str(e))
            self.reset()

    def _complete_confirmation(self, current_x: float, current_y: float) -> None:
        """Complete the confirmation step with current position."""
        if self._reference_fov_position is None:
            self._log.error("Cannot confirm: no reference position set")
            return

        ref_x, ref_y = self._reference_fov_position
        offset_x = current_x - ref_x
        offset_y = current_y - ref_y

        self._offset_x_mm = offset_x
        self._offset_y_mm = offset_y
        self._has_offset = True

        self._remove_reference_layer()

        self.state = self.STATE_CLEAR
        self.btn_align.setText("Clear Offset")

        self.signal_offset_set.emit(offset_x, offset_y)
        self._log.info(f"Alignment confirmed: offset=({offset_x:.4f}, {offset_y:.4f})mm")

        QMessageBox.information(
            self,
            "Alignment Applied",
            f"Offset applied:\nX: {offset_x:.4f} mm\nY: {offset_y:.4f} mm",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Acquisition Folder Parsing
    # ─────────────────────────────────────────────────────────────────────────

    def _load_acquisition_info(self, folder_path: str) -> dict:
        """Load acquisition info from a past acquisition folder.

        Returns dict with: coordinates, first_region, center_fov_index,
        center_fov_position, image_path
        """
        folder = Path(folder_path)

        coords_file = folder / "coordinates.csv"
        if not coords_file.exists():
            raise FileNotFoundError(f"coordinates.csv not found in {folder_path}")

        coords_df = pd.read_csv(coords_file)
        first_region = coords_df["region"].iloc[0]
        region_coords = coords_df[coords_df["region"] == first_region]

        num_fovs = len(region_coords)
        center_idx = self._find_center_fov(region_coords)
        center_fov = region_coords.iloc[center_idx]
        center_fov_position = (float(center_fov["x (mm)"]), float(center_fov["y (mm)"]))

        image_path = self._find_reference_image(folder, first_region, center_idx)

        self._log.info(
            f"Loaded acquisition info: region={first_region}, "
            f"center_fov={center_idx}/{num_fovs}, "
            f"position=({center_fov_position[0]:.4f}, {center_fov_position[1]:.4f})"
        )

        return {
            "coordinates": coords_df,
            "first_region": first_region,
            "center_fov_index": center_idx,
            "center_fov_position": center_fov_position,
            "image_path": str(image_path),
        }

    def _find_center_fov(self, region_coords: "pd.DataFrame") -> int:
        """Find the FOV index closest to the region center. O(n) complexity."""
        x = region_coords["x (mm)"].values
        y = region_coords["y (mm)"].values
        center_x = (x.min() + x.max()) / 2
        center_y = (y.min() + y.max()) / 2
        distances_sq = (x - center_x) ** 2 + (y - center_y) ** 2
        return int(distances_sq.argmin())

    def _find_reference_image(self, folder: Path, region: str, fov_idx: int) -> Path:
        """Find reference image in OME-TIFF or traditional timepoint folders."""
        # Try OME-TIFF folder first
        ome_tiff_folder = folder / "ome_tiff"
        if ome_tiff_folder.exists():
            ome_images = list(ome_tiff_folder.glob(f"{region}_{fov_idx}.ome.tiff"))
            if ome_images:
                self._log.info(f"Found OME-TIFF image: {ome_images[0]}")
                return ome_images[0]

        # Try traditional timepoint folders
        timepoint_folders = sorted(
            [d for d in folder.iterdir() if d.is_dir() and d.name.isdigit()],
            key=lambda x: int(x.name),
        )
        if timepoint_folders:
            last_timepoint = timepoint_folders[-1]
            for ext in ("tiff", "tif", "bmp"):
                images = sorted(last_timepoint.glob(f"{region}_{fov_idx}_0_*.{ext}"))
                if images:
                    self._log.info(f"Found traditional format image: {images[0]}")
                    return images[0]

        raise FileNotFoundError(
            f"No images found for region={region}, FOV={fov_idx} in {folder}. "
            f"Checked ome_tiff folder and timepoint folders."
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Napari Layer Management
    # ─────────────────────────────────────────────────────────────────────────

    def _load_reference_image(self, image_path: str) -> None:
        """Load reference image and add to napari viewer."""
        import tifffile

        if image_path.endswith((".tiff", ".tif", ".ome.tiff", ".ome.tif")):
            ref_image = tifffile.imread(image_path)
            # Reduce multi-dimensional images (T, C, Z, Y, X) to 2D
            while ref_image.ndim > 2:
                ref_image = ref_image[0]
            self._log.info(f"Loaded TIFF reference image, shape: {ref_image.shape}")
        else:
            ref_image = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
            if ref_image is None:
                raise ValueError(f"Failed to read image: {image_path}")

        self._add_reference_layer(ref_image)

    def _add_reference_layer(self, image: np.ndarray) -> None:
        """Add reference image as a napari layer with magenta/green overlay."""
        self._modified_live_view = False
        if "Live View" in self.viewer.layers:
            live_layer = self.viewer.layers["Live View"]
            self._original_live_opacity = live_layer.opacity
            self._original_live_blending = live_layer.blending
            self._original_live_colormap = live_layer.colormap
            live_layer.opacity = 1.0
            live_layer.blending = "additive"
            live_layer.colormap = "green"
            self._modified_live_view = True
        else:
            self._log.warning("Live View layer not found - reference image will be shown alone")

        if self.REFERENCE_LAYER_NAME in self.viewer.layers:
            self.viewer.layers[self.REFERENCE_LAYER_NAME].data = image
        else:
            self.viewer.add_image(
                image,
                name=self.REFERENCE_LAYER_NAME,
                visible=True,
                opacity=1.0,
                colormap="magenta",
                blending="additive",
            )
        self._log.debug("Reference layer added to napari viewer")

    def _remove_reference_layer(self) -> None:
        """Remove reference layer and restore live view opacity."""
        if self.viewer is None:
            return

        if self.REFERENCE_LAYER_NAME in self.viewer.layers:
            self.viewer.layers.remove(self.REFERENCE_LAYER_NAME)
            self._log.debug("Reference layer removed from napari viewer")

        if self._modified_live_view and "Live View" in self.viewer.layers:
            live_layer = self.viewer.layers["Live View"]
            live_layer.opacity = self._original_live_opacity
            live_layer.blending = self._original_live_blending
            live_layer.colormap = self._original_live_colormap
            self._modified_live_view = False
