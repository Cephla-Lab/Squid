import configparser
import gc
import os
import json
import yaml
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional, TYPE_CHECKING

import psutil

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from control.core.memory_profiler import MemoryMonitor

import squid.logging
from control.core.config import ConfigRepository
from control.core.core import TrackingController, LiveController
from control.core.multi_point_controller import MultiPointController
from control.core.downsampled_views import format_well_id
from control.core.geometry_utils import get_effective_well_size, calculate_well_coverage
from control.microcontroller import Microcontroller
from control.piezo import PiezoStage
import control.utils as utils
import control._def  # Import module for runtime access to MCP-modifiable settings
from squid.abc import AbstractStage, AbstractCamera, AbstractFilterWheelController
from squid.stage.utils import move_to_loading_position, move_to_scanning_position, move_z_axis_to_safety_position
from squid.config import CameraPixelFormat

# set QT_API environment variable
os.environ["QT_API"] = "pyqt5"

# qt libraries
import qtpy
from qtpy.QtCore import *
from qtpy.QtWidgets import *
from qtpy.QtGui import *

import pyqtgraph as pg
import pandas as pd
import napari
from napari.utils.colormaps import Colormap, AVAILABLE_COLORMAPS
import re
import cv2
import math
import locale
import time
from datetime import datetime
import itertools
import numpy as np
from scipy.spatial import Delaunay
import shutil
from control._def import *
from PIL import Image, ImageDraw, ImageFont


def error_dialog(message: str, title: str = "Error"):
    msg = QMessageBox()
    msg.setIcon(QMessageBox.Warning)
    msg.setText(message)
    msg.setWindowTitle(title)
    msg.setStandardButtons(QMessageBox.Ok)
    msg.setDefaultButton(QMessageBox.Ok)
    retval = msg.exec_()
    return


def check_space_available_with_error_dialog(
    multi_point_controller: MultiPointController, logger: logging.Logger, factor_of_safecty: float = 1.03
) -> bool:
    # To check how much disk space is required, we need to have the MultiPointController all configured.  That is
    # a precondition of this function.
    save_directory = multi_point_controller.base_path
    available_disk_space = utils.get_available_disk_space(save_directory)
    space_required = factor_of_safecty * multi_point_controller.get_estimated_acquisition_disk_storage()
    image_count = multi_point_controller.get_acquisition_image_count()

    logger.info(
        f"Checking space available: {space_required=}, {available_disk_space=}, {image_count=}, {save_directory=}"
    )
    if space_required > available_disk_space:
        megabytes_required = int(space_required / 1024 / 1024)
        megabytes_available = int(available_disk_space / 1024 / 1024)
        error_message = (
            f"This acquisition will capture {image_count:,} images, which will"
            f" require {megabytes_required:,} [MB], but '{save_directory}' only has {megabytes_available:,} [MB] available."
        )
        logger.error(error_message)
        error_dialog(error_message, title="Not Enough Disk Space")
        return False
    return True


def check_ram_available_with_error_dialog(
    multi_point_controller: MultiPointController,
    logger: logging.Logger,
    factor_of_safety: float = 1.15,
    performance_mode: bool = False,
) -> bool:
    """Check if enough RAM is available for mosaic view."""
    import psutil

    # Skip check if performance mode is enabled (mosaic view is disabled)
    if performance_mode:
        logger.info("Performance mode enabled, skipping RAM check for mosaic view")
        return True

    ram_required = factor_of_safety * multi_point_controller.get_estimated_mosaic_ram_bytes()
    available_ram = psutil.virtual_memory().available

    logger.info(f"Checking RAM available: {ram_required=}, {available_ram=}")

    if ram_required > available_ram:
        mb_required = int(ram_required / 1024 / 1024)
        mb_available = int(available_ram / 1024 / 1024)
        error_message = (
            f"This acquisition's mosaic view will require approximately {mb_required:,} MB RAM, "
            f"but only {mb_available:,} MB is currently available.\n\n"
            f"Consider enabling Performance Mode to disable mosaic view during acquisition."
        )
        logger.error(error_message)
        error_dialog(error_message, title="Not Enough RAM")
        return False
    return True


def get_last_used_saving_path() -> str:
    """Get the last used saving path from cache file, or return the default."""
    cache_file = "cache/last_saving_path.txt"
    try:
        with open(cache_file, "r") as f:
            path = f.read().strip()
            if path and os.path.isdir(path):
                return path
    except OSError:
        pass
    return DEFAULT_SAVING_PATH


def save_last_used_saving_path(path: str) -> None:
    """Save the last used saving path to cache file."""
    if path:  # Only save non-empty paths
        cache_file = "cache/last_saving_path.txt"
        try:
            os.makedirs("cache", exist_ok=True)
            with open(cache_file, "w") as f:
                f.write(path)
        except OSError:
            pass  # Silently fail - caching is a convenience feature


class WrapperWindow(QMainWindow):
    def __init__(self, content_widget, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setCentralWidget(content_widget)
        self.hide()

    def closeEvent(self, event):
        self.hide()
        event.ignore()

    def closeForReal(self, event):
        super().closeEvent(event)


class NDViewerTab(QWidget):
    """
    Embedded NDViewer (ndviewer_light) for showing the latest acquisition.

    This is designed to live inside an existing QTabWidget (no separate QApplication / process).
    """

    _PLACEHOLDER_WAITING = "NDViewer: waiting for an acquisition to start..."

    def __init__(self, parent=None):
        super().__init__(parent)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._viewer = None
        self._dataset_path: Optional[str] = None

        self._layout = QVBoxLayout()
        self._layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(self._layout)

        self._placeholder = QLabel(self._PLACEHOLDER_WAITING)
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._layout.addWidget(self._placeholder, 1)

    def _show_placeholder(self, message: str) -> None:
        """Show placeholder with message and hide viewer."""
        self._placeholder.setText(message)
        self._placeholder.setVisible(True)
        if self._viewer is not None:
            self._viewer.setVisible(False)

    def set_dataset_path(self, dataset_path: Optional[str]) -> None:
        """
        Point the embedded NDViewer at a dataset folder and refresh.

        Pass None to clear the view.
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
            # Lazy import so the main UI doesn't pay NDV import costs until needed
            from control import ndviewer_light
        except ImportError as e:
            self._log.error(f"Failed to import ndviewer_light: {e}")
            self._show_placeholder(f"NDViewer: failed to import ndviewer_light:\n{e}")
            return

        # ndviewer_light handles gracefully degraded rendering if NDV is partially unavailable.
        # Complete failures to load or create the viewer fall through to the exception handler below.
        try:
            if self._viewer is None:
                self._log.debug(f"Creating new LightweightViewer for: {dataset_path}")
                self._viewer = ndviewer_light.LightweightViewer(dataset_path)
                self._layout.addWidget(self._viewer, 1)
                self._log.debug(f"LightweightViewer created, ndv_viewer={self._viewer.ndv_viewer is not None}")
            else:
                self._log.debug(f"Reloading dataset: {dataset_path}")
                self._viewer.load_dataset(dataset_path)
                self._viewer.refresh()

            self._viewer.setVisible(True)
            self._placeholder.setVisible(False)
        except Exception as e:
            self._log.exception("NDViewerTab failed to load dataset")
            error_msg = str(e) if str(e) else type(e).__name__
            self._show_placeholder(f"NDViewer: failed to load dataset:\n{dataset_path}\n\nError: {error_msg}")

    def go_to_fov(self, well_id: str, fov_index: int) -> bool:
        """
        Navigate the NDViewer to a specific well and FOV.

        Called when user double-clicks a location in the plate view.
        Maps (well_id, fov_index) to the flat xarray FOV dimension index.

        Returns:
            True if navigation succeeded, False otherwise.
        """
        if self._viewer is None:
            self._log.debug("go_to_fov: no viewer loaded")
            return False

        try:
            # Try push-based mode first (active during/after acquisition)
            if self._viewer.is_push_mode_active():
                if self._viewer.go_to_well_fov(well_id, fov_index):
                    self._log.info(f"go_to_fov: navigated to well={well_id}, fov={fov_index} (push mode)")
                    return True
                self._log.warning(
                    f"go_to_fov: push mode navigation failed for well={well_id}, fov={fov_index}. "
                    f"FOV may not be registered yet or well ID format may not match."
                )
                return False

            # Fall back to legacy mode (viewing existing datasets)
            if not self._viewer.has_fov_dimension():
                self._log.debug("go_to_fov: no fov dimension available")
                return False

            target_flat_idx = self._find_flat_fov_index(well_id, fov_index)
            if target_flat_idx is None:
                self._log.debug(f"go_to_fov: could not find FOV for well={well_id}, fov={fov_index}")
                return False

            if self._viewer.set_current_index("fov", target_flat_idx):
                self._log.info(f"go_to_fov: navigated to well={well_id}, fov={fov_index} (flat_idx={target_flat_idx})")
                return True

            self._log.debug(f"go_to_fov: set_current_index failed for fov={target_flat_idx}")
            return False
        except Exception:
            self._log.exception(f"go_to_fov: unexpected error for well={well_id}, fov={fov_index}")
            return False

    def _find_flat_fov_index(self, well_id: str, fov_index: int) -> Optional[int]:
        """
        Find the flat xarray FOV index for a given (well_id, fov_index).

        The xarray FOV dimension is a flat list of all FOVs across all wells.
        Uses the viewer's public get_fov_list() API to get the FOV mapping.

        The FOV list contains dictionaries with keys:
            - "region": str - The well ID (e.g., "A1", "B2")
            - "fov": int - The FOV index within that well

        Returns:
            The flat index if found, None otherwise. Returns None if the FOV list
            is empty (e.g., when get_fov_list() catches an internal error).
        """
        fovs = self._viewer.get_fov_list()
        return next(
            (i for i, fov in enumerate(fovs) if fov["region"] == well_id and fov["fov"] == fov_index),
            None,
        )

    # -------------------------------------------------------------------------
    # Push-based API for live acquisition (no polling)
    # -------------------------------------------------------------------------

    def _ensure_viewer_ready(self, context: str = "acquisition") -> bool:
        """Ensure ndviewer_light is imported and viewer widget is created.

        Args:
            context: Description for logging (e.g., "acquisition", "zarr acquisition")

        Returns:
            True if viewer is ready, False if import or creation failed.
        """
        try:
            from control import ndviewer_light
        except ImportError as e:
            self._log.error(f"Failed to import ndviewer_light: {e}")
            self._show_placeholder(f"NDViewer: failed to import ndviewer_light:\n{e}")
            return False

        if self._viewer is None:
            self._log.debug(f"Creating new LightweightViewer for {context}")
            self._viewer = ndviewer_light.LightweightViewer()
            self._layout.addWidget(self._viewer, 1)

        return True

    def start_acquisition(
        self,
        channels: List[str],
        num_z: int,
        height: int,
        width: int,
        fov_labels: List[str],
    ) -> bool:
        """Configure viewer for a new acquisition.

        Args:
            channels: List of channel names (e.g., ["BF LED matrix full", "Fluorescence 488 nm Ex"])
            num_z: Number of z-levels
            height: Image height in pixels
            width: Image width in pixels
            fov_labels: List of FOV labels (e.g., ["A1:0", "A1:1", "A2:0"])

        Returns:
            True if successful, False otherwise.
        """
        if not self._ensure_viewer_ready("TIFF acquisition"):
            return False

        try:
            self._viewer.start_acquisition(channels, num_z, height, width, fov_labels)
            self._viewer.setVisible(True)
            self._placeholder.setVisible(False)
            self._log.info(
                f"NDViewer configured for acquisition: {len(channels)} channels, "
                f"{num_z} z-levels, {len(fov_labels)} FOVs"
            )
            return True
        except Exception as e:
            self._log.exception("Failed to start acquisition in NDViewer")
            error_msg = str(e) if str(e) else type(e).__name__
            self._show_placeholder(f"NDViewer: failed to start acquisition:\n{error_msg}")
            return False

    def register_image(self, t: int, fov_idx: int, z: int, channel: str, filepath: str) -> None:
        """Register a newly saved image file.

        Called on main thread via Qt signal from worker thread.

        Args:
            t: Timepoint index
            fov_idx: FOV index
            z: Z-level index
            channel: Channel name
            filepath: Path to the saved image file
        """
        if self._viewer is None:
            return
        try:
            self._viewer.register_image(t, fov_idx, z, channel, filepath)
        except Exception:
            self._log.exception(
                f"Failed to register image: t={t}, fov={fov_idx}, z={z}, " f"channel={channel}, filepath={filepath}"
            )

    def load_fov(self, fov: int, t: Optional[int] = None, z: Optional[int] = None) -> bool:
        """Load and display a specific FOV.

        Args:
            fov: FOV index to display
            t: Timepoint index (None = use current)
            z: Z-level index (None = use current)

        Returns:
            True if successful, False otherwise.
        """
        if self._viewer is None:
            self._log.debug("load_fov: no viewer loaded")
            return False
        try:
            self._viewer.load_fov(fov, t, z)
            return True
        except Exception:
            self._log.exception(f"load_fov: failed for fov={fov}, t={t}, z={z}")
            return False

    def end_acquisition(self) -> None:
        """Mark acquisition as ended.

        Call this when acquisition completes. The viewer remains usable
        for navigating the acquired data.
        """
        if self._viewer is None:
            return
        try:
            self._viewer.end_acquisition()
            self._log.debug("NDViewer acquisition ended")
        except Exception:
            self._log.exception("Failed to end NDViewer acquisition")

    # -------------------------------------------------------------------------
    # Zarr Push-based API for live acquisition (requires ndviewer_light zarr support)
    # -------------------------------------------------------------------------

    def start_zarr_acquisition(
        self,
        fov_paths: List[str],
        channels: List[str],
        num_z: int,
        fov_labels: List[str],
        height: int,
        width: int,
    ) -> bool:
        """Configure viewer for zarr-based live acquisition (5D per-FOV mode).

        Args:
            fov_paths: List of zarr paths per FOV
            channels: List of channel names
            num_z: Number of z-levels
            fov_labels: List of FOV labels (e.g., ["A1:0", "A1:1"])
            height: Image height in pixels
            width: Image width in pixels

        Returns:
            True if successful, False otherwise.
        """
        if not self._ensure_viewer_ready("Zarr 5D acquisition"):
            return False

        try:
            # Check if ndviewer_light has zarr support
            if not hasattr(self._viewer, "start_zarr_acquisition"):
                self._log.warning(
                    "ndviewer_light doesn't support zarr push API. "
                    "Live viewing not available for Zarr format. "
                    "Update ndviewer_light submodule to enable this feature."
                )
                self._show_placeholder(
                    "NDViewer: zarr live view requires ndviewer_light with zarr support.\n"
                    "Update the ndviewer_light submodule."
                )
                return False

            self._viewer.start_zarr_acquisition(fov_paths, channels, num_z, fov_labels, height, width)
            self._viewer.setVisible(True)
            self._placeholder.setVisible(False)
            self._log.info(
                f"NDViewer configured for zarr acquisition: {len(channels)} channels, "
                f"{num_z} z-levels, {len(fov_labels)} FOVs"
            )
            return True
        except Exception as e:
            self._log.exception("Failed to start zarr acquisition in NDViewer")
            error_msg = str(e) if str(e) else type(e).__name__
            self._show_placeholder(f"NDViewer: failed to start zarr acquisition:\n{error_msg}")
            return False

    def start_zarr_acquisition_6d(
        self,
        region_paths: List[str],
        channels: List[str],
        num_z: int,
        fovs_per_region: List[int],
        height: int,
        width: int,
        region_labels: List[str],
    ) -> bool:
        """Configure viewer for 6D multi-region zarr acquisition.

        Args:
            region_paths: List of zarr paths (one per region)
            channels: List of channel names
            num_z: Number of z-levels
            fovs_per_region: List of FOV counts per region
            height: Image height in pixels
            width: Image width in pixels
            region_labels: List of region labels (e.g., ["region_1", "region_2"])

        Returns:
            True if successful, False otherwise.
        """
        if not self._ensure_viewer_ready("Zarr 6D acquisition"):
            return False

        try:
            # Check if ndviewer_light has 6D regions support
            if not hasattr(self._viewer, "start_zarr_acquisition_6d"):
                self._log.warning(
                    "ndviewer_light doesn't support 6D multi-region mode. "
                    "Update ndviewer_light submodule to enable this feature."
                )
                self._show_placeholder(
                    "NDViewer: 6D multi-region mode requires updated ndviewer_light.\n"
                    "Update the ndviewer_light submodule."
                )
                return False

            self._viewer.start_zarr_acquisition_6d(
                region_paths, channels, num_z, fovs_per_region, height, width, region_labels
            )
            self._viewer.setVisible(True)
            self._placeholder.setVisible(False)

            total_fovs = sum(fovs_per_region)
            self._log.info(
                f"NDViewer configured for 6D multi-region: {len(region_paths)} regions, "
                f"{total_fovs} total FOVs, {len(channels)} channels, {num_z} z-levels"
            )
            return True
        except Exception as e:
            self._log.exception("Failed to start 6D multi-region zarr acquisition in NDViewer")
            error_msg = str(e) if str(e) else type(e).__name__
            self._show_placeholder(f"NDViewer: failed to start 6D regions acquisition:\n{error_msg}")
            return False

    def notify_zarr_frame(self, t: int, fov_idx: int, z: int, channel: str, region_idx: int = 0) -> None:
        """Notify viewer that a zarr frame was written.

        Called on main thread via Qt signal from worker thread.

        Args:
            t: Timepoint index
            fov_idx: FOV index (local to region in 6D mode, flat index otherwise)
            z: Z-level index
            channel: Channel name
            region_idx: Region index (only used in 6D multi-region mode)
        """
        self._log.debug(f"notify_zarr_frame called: t={t}, fov={fov_idx}, z={z}, ch={channel}")
        if self._viewer is None:
            self._log.warning("notify_zarr_frame: viewer is None")
            return
        try:
            if hasattr(self._viewer, "notify_zarr_frame"):
                self._viewer.notify_zarr_frame(t, fov_idx, z, channel, region_idx)
            else:
                self._log.warning("viewer doesn't have notify_zarr_frame method")
        except Exception:
            self._log.exception(
                f"Failed to notify zarr frame: t={t}, fov={fov_idx}, z={z}, "
                f"channel={channel}, region_idx={region_idx}"
            )

    def end_zarr_acquisition(self) -> None:
        """Mark zarr acquisition as ended.

        Call this when zarr acquisition completes. The viewer remains usable
        for navigating the acquired data.
        """
        if self._viewer is None:
            return
        try:
            if hasattr(self._viewer, "end_zarr_acquisition"):
                self._viewer.end_zarr_acquisition()
                self._log.debug("NDViewer zarr acquisition ended")
        except Exception:
            self._log.exception("Failed to end zarr acquisition in NDViewer")

    def close(self) -> None:
        """Clean up viewer resources."""
        if self._viewer is not None:
            try:
                # Calling close() triggers LightweightViewer.closeEvent(),
                # which stops refresh timers and closes open file handles
                self._viewer.close()
            except Exception:
                self._log.exception("Error closing LightweightViewer")
            self._viewer = None
        self._dataset_path = None


class CollapsibleGroupBox(QWidget):
    """A collapsible group box with arrow indicator for expand/collapse."""

    def __init__(self, title, collapsed=False):
        super().__init__()
        self._collapsed = collapsed
        self._title = title

        # Main layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 8)
        main_layout.setSpacing(0)

        # Header button with arrow
        self._header = QPushButton()
        self._header.setStyleSheet(
            """
            QPushButton {
                text-align: left;
                padding: 8px;
                font-weight: bold;
                background-color: palette(button);
                border: 1px solid palette(mid);
                border-bottom: none;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }
            QPushButton:hover {
                background-color: palette(light);
            }
            """
        )
        self._header.clicked.connect(self._toggle)
        main_layout.addWidget(self._header)

        # Content widget with border to show grouping
        self.content_widget = QFrame()
        self.content_widget.setObjectName("collapsibleContent")
        self.content_widget.setFrameShape(QFrame.StyledPanel)
        self.content_widget.setStyleSheet(
            """
            QFrame#collapsibleContent {
                border: 1px solid palette(mid);
                border-top: none;
                border-bottom-left-radius: 4px;
                border-bottom-right-radius: 4px;
                background-color: palette(base);
            }
            QFrame#collapsibleContent QLabel {
                border: none;
                background: transparent;
            }
            """
        )
        self.content = QVBoxLayout(self.content_widget)
        self.content.setContentsMargins(15, 10, 10, 10)
        main_layout.addWidget(self.content_widget)

        # Set initial state
        self._update_header()
        self.content_widget.setVisible(not collapsed)

    def _update_header(self):
        arrow = "▼" if not self._collapsed else "▶"
        self._header.setText(f"{arrow}  {self._title}")

    def _toggle(self):
        self._collapsed = not self._collapsed
        self._update_header()
        self.content_widget.setVisible(not self._collapsed)

    def setCollapsed(self, collapsed):
        """Programmatically set collapsed state."""
        if self._collapsed != collapsed:
            self._collapsed = collapsed
            self._update_header()
            self.content_widget.setVisible(not collapsed)

    def isCollapsed(self):
        """Return current collapsed state."""
        return self._collapsed


class ConfigEditor(QDialog):
    def __init__(self, config):
        super().__init__()
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.config = config

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area_widget = QWidget()
        self.scroll_area_layout = QVBoxLayout()
        self.scroll_area_widget.setLayout(self.scroll_area_layout)
        self.scroll_area.setWidget(self.scroll_area_widget)

        self.save_config_button = QPushButton("Save Config")
        self.save_config_button.clicked.connect(self.save_config)
        self.save_to_file_button = QPushButton("Save to File")
        self.save_to_file_button.clicked.connect(self.save_to_file)
        self.load_config_button = QPushButton("Load Config from File")
        self.load_config_button.clicked.connect(self.load_config_from_file)

        layout = QVBoxLayout()
        layout.addWidget(self.scroll_area)
        layout.addWidget(self.save_config_button)
        layout.addWidget(self.save_to_file_button)
        layout.addWidget(self.load_config_button)

        self.config_value_widgets = {}

        self.setLayout(layout)
        self.setWindowTitle("Configuration Editor")
        self.init_ui()

    def init_ui(self):
        self.groups = {}
        for section in self.config.sections():
            group_box = CollapsibleGroupBox(section)
            group_layout = QVBoxLayout()

            section_value_widgets = {}

            self.groups[section] = group_box

            for option in self.config.options(section):
                if option.startswith("_") and option.endswith("_options"):
                    continue
                option_value = self.config.get(section, option)
                option_name = QLabel(option)
                option_layout = QHBoxLayout()
                option_layout.addWidget(option_name)
                if f"_{option}_options" in self.config.options(section):
                    option_value_list = self.config.get(section, f"_{option}_options")
                    values = option_value_list.strip("[]").split(",")
                    for i in range(len(values)):
                        values[i] = values[i].strip()
                    if option_value not in values:
                        values.append(option_value)
                    combo_box = QComboBox()
                    combo_box.addItems(values)
                    combo_box.setCurrentText(option_value)
                    option_layout.addWidget(combo_box)
                    section_value_widgets[option] = combo_box
                else:
                    option_input = QLineEdit(option_value)
                    option_layout.addWidget(option_input)
                    section_value_widgets[option] = option_input
                group_layout.addLayout(option_layout)

            self.config_value_widgets[section] = section_value_widgets
            group_box.content.addLayout(group_layout)
            self.scroll_area_layout.addWidget(group_box)

    def save_config(self):
        for section in self.config.sections():
            for option in self.config.options(section):
                if option.startswith("_") and option.endswith("_options"):
                    continue
                old_val = self.config.get(section, option)
                widget = self.config_value_widgets[section][option]
                if type(widget) is QLineEdit:
                    self.config.set(section, option, widget.text())
                else:
                    self.config.set(section, option, widget.currentText())
                if old_val != self.config.get(section, option):
                    print(self.config.get(section, option))

    def save_to_filename(self, filename: str):
        try:
            with open(filename, "w") as configfile:
                self.config.write(configfile)
                return True
        except IOError:
            self._log.exception(f"Failed to write config file to '{filename}'")
            return False

    def save_to_file(self):
        self.save_config()
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Config File", "", "INI Files (*.ini);;All Files (*)")
        if file_path:
            if not self.save_to_filename(file_path):
                QMessageBox.warning(
                    self, "Warning", f"Failed to write config file to '{file_path}'.  Check permissions!"
                )

    def load_config_from_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Load Config File", "", "INI Files (*.ini);;All Files (*)")
        if file_path:
            self.config.read(file_path)
            # Clear and re-initialize the UI
            self.scroll_area_widget.deleteLater()
            self.scroll_area_widget = QWidget()
            self.scroll_area_layout = QVBoxLayout()
            self.scroll_area_widget.setLayout(self.scroll_area_layout)
            self.scroll_area.setWidget(self.scroll_area_widget)
            self.init_ui()


class ConfigEditorBackwardsCompatible(ConfigEditor):
    def __init__(self, config, original_filepath, main_window):
        super().__init__(config)
        self.original_filepath = original_filepath
        self.main_window = main_window

        self.apply_exit_button = QPushButton("Apply and Exit")
        self.apply_exit_button.clicked.connect(self.apply_and_exit)

        self.layout().addWidget(self.apply_exit_button)

    def apply_and_exit(self):
        self.save_config()
        with open(self.original_filepath, "w") as configfile:
            self.config.write(configfile)
        try:
            self.main_window.close()
        except (AttributeError, RuntimeError):
            # main_window may be None or already closed
            pass
        self.close()


class AcquisitionYAMLDropMixin:
    """Mixin class providing drag-and-drop functionality for loading acquisition YAML files.

    Widgets using this mixin must:
    1. Call `self.setAcceptDrops(True)` in __init__
    2. Have `self._log`, `self.multipointController`, `self.objectiveStore` attributes
    3. Implement `_get_expected_widget_type()` returning "wellplate" or "flexible"
    4. Implement `_apply_yaml_settings(yaml_data)` to apply settings to the widget
    """

    def _is_valid_yaml_drop(self, file_path: str) -> bool:
        """Check if the path is a valid YAML file or a folder containing acquisition.yaml."""
        if file_path.endswith(".yaml") or file_path.endswith(".yml"):
            return True
        # Check if it's a directory containing acquisition.yaml
        if os.path.isdir(file_path):
            yaml_path = os.path.join(file_path, "acquisition.yaml")
            if os.path.isfile(yaml_path):
                return True
        return False

    def _resolve_yaml_path(self, file_path: str) -> str:
        """Resolve the actual YAML file path from a file or folder."""
        if file_path.endswith(".yaml") or file_path.endswith(".yml"):
            return file_path
        # Check if it's a directory containing acquisition.yaml
        if os.path.isdir(file_path):
            yaml_path = os.path.join(file_path, "acquisition.yaml")
            if os.path.isfile(yaml_path):
                return yaml_path
        return file_path

    def dragEnterEvent(self, event):
        """Handle drag enter event for YAML file or folder drops."""
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                file_path = url.toLocalFile()
                if self._is_valid_yaml_drop(file_path):
                    event.accept()
                    # Visual feedback - dashed border (store original for restore)
                    if not hasattr(self, "_original_stylesheet"):
                        self._original_stylesheet = self.styleSheet()
                    self.setStyleSheet(
                        self._original_stylesheet + f" {self.__class__.__name__} {{ border: 3px dashed #4a90d9; }}"
                    )
                    return
        event.ignore()

    def dragLeaveEvent(self, event):
        """Handle drag leave event."""
        if hasattr(self, "_original_stylesheet"):
            self.setStyleSheet(self._original_stylesheet)
        event.accept()

    def dropEvent(self, event):
        """Handle drop event for YAML file or folder."""
        if hasattr(self, "_original_stylesheet"):
            self.setStyleSheet(self._original_stylesheet)
        paths = [u.toLocalFile() for u in event.mimeData().urls()]
        yaml_paths = [self._resolve_yaml_path(p) for p in paths if self._is_valid_yaml_drop(p)]
        if yaml_paths:
            if len(yaml_paths) > 1 and hasattr(self, "_log"):
                self._log.warning(
                    "Multiple YAML files/folders dropped (%d). Only loading the first: %s",
                    len(yaml_paths),
                    yaml_paths[0],
                )
            self._load_acquisition_yaml(yaml_paths[0])
        event.accept()

    def _get_expected_widget_type(self) -> str:
        """Return the expected widget_type for this widget. Override in subclass."""
        raise NotImplementedError("Subclass must implement _get_expected_widget_type()")

    def _get_other_widget_name(self) -> str:
        """Return the name of the other widget type for error messages."""
        if self._get_expected_widget_type() == "wellplate":
            return "Flexible Multipoint"
        return "Wellplate Multipoint"

    def _load_acquisition_yaml(self, file_path: str):
        """Load acquisition settings from YAML file."""
        from control.acquisition_yaml_loader import parse_acquisition_yaml, validate_hardware

        try:
            yaml_data = parse_acquisition_yaml(file_path)
        except Exception as e:
            QMessageBox.warning(self, "Load Error", f"Failed to parse YAML file:\n{e}")
            return

        # Check widget type
        expected_type = self._get_expected_widget_type()
        if yaml_data.widget_type != expected_type:
            QMessageBox.warning(
                self,
                "Widget Type Mismatch",
                f"This YAML is for '{yaml_data.widget_type}' mode.\n"
                f"Please drop this file on the {self._get_other_widget_name()} widget instead.",
            )
            return

        # Validate hardware
        current_binning = (1, 1)
        try:
            camera = getattr(self.multipointController, "camera", None)
            if camera and hasattr(camera, "get_binning"):
                current_binning = tuple(camera.get_binning())
        except Exception as e:
            self._log.warning(
                "Could not get camera binning for validation; using default %s: %s",
                current_binning,
                e,
            )

        validation = validate_hardware(yaml_data, self.objectiveStore.current_objective, current_binning)

        if not validation.is_valid:
            dialog = AcquisitionYAMLMismatchDialog(validation, self)
            dialog.exec_()
            return

        # Apply settings with signal blocking
        self._apply_yaml_settings(yaml_data)
        self._log.info(f"Loaded acquisition settings from: {file_path}")

    def _apply_yaml_settings(self, yaml_data):
        """Apply parsed YAML settings to widget controls. Override in subclass."""
        raise NotImplementedError("Subclass must implement _apply_yaml_settings()")


class AcquisitionYAMLMismatchDialog(QDialog):
    """Dialog shown when hardware configuration doesn't match loaded YAML settings."""

    def __init__(self, validation_result, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cannot Load Settings")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)

        # Warning icon and title
        title_layout = QHBoxLayout()
        icon_label = QLabel()
        icon_label.setPixmap(self.style().standardIcon(QStyle.SP_MessageBoxWarning).pixmap(32, 32))
        title_layout.addWidget(icon_label)
        title_label = QLabel("<b>Hardware Configuration Mismatch</b>")
        title_label.setStyleSheet("font-size: 14px;")
        title_layout.addWidget(title_label)
        title_layout.addStretch()
        layout.addLayout(title_layout)

        layout.addSpacing(10)

        # Mismatch details
        message_label = QLabel(validation_result.message)
        message_label.setWordWrap(True)
        message_label.setStyleSheet("background-color: #fff3cd; padding: 10px; border-radius: 4px;")
        layout.addWidget(message_label)

        layout.addSpacing(10)

        # Instructions
        instruction_label = QLabel(
            "Please update your hardware settings to match the YAML file, then drag and drop again."
        )
        instruction_label.setWordWrap(True)
        instruction_label.setStyleSheet("color: #666;")
        layout.addWidget(instruction_label)

        layout.addSpacing(15)

        # OK button
        button_layout = QHBoxLayout()
        ok_btn = QPushButton("OK")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        button_layout.addStretch()
        button_layout.addWidget(ok_btn)
        layout.addLayout(button_layout)


class PreferencesDialog(QDialog):
    """User-friendly preferences dialog with tabbed interface for common settings."""

    signal_config_changed = Signal()

    def __init__(self, config, config_filepath, parent=None, on_restart=None):
        super().__init__(parent)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.config = config
        self.config_filepath = config_filepath
        self._on_restart = on_restart  # Optional callback for application restart
        self.setWindowTitle("Preferences")
        self.setMinimumWidth(500)
        self.setMinimumHeight(600)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # Tab widget
        self.tab_widget = QTabWidget()
        layout.addWidget(self.tab_widget)

        # Create tabs
        self._create_general_tab()
        self._create_acquisition_tab()
        self._create_camera_tab()
        self._create_views_tab()
        self._create_advanced_tab()
        self._create_development_tab()

        # Buttons
        button_layout = QHBoxLayout()
        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self._save_and_close)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)

        button_layout.addStretch()
        button_layout.addWidget(self.save_button)
        button_layout.addWidget(self.cancel_button)
        layout.addLayout(button_layout)

    def _create_general_tab(self):
        tab = QWidget()
        layout = QFormLayout(tab)
        layout.setSpacing(10)

        # File Saving Format
        self.file_saving_combo = QComboBox()
        self.file_saving_combo.addItems([e.name for e in FileSavingOption])
        current_value = self._get_config_value("GENERAL", "file_saving_option", "OME_TIFF")
        self.file_saving_combo.setCurrentText(current_value)
        layout.addRow("File Saving Format:", self.file_saving_combo)

        # Zarr Compression (only visible when ZARR_V3 is selected)
        self.zarr_compression_combo = QComboBox()
        self.zarr_compression_combo.addItems(["none", "fast", "balanced", "best"])
        self.zarr_compression_combo.setToolTip(
            "none: No compression, maximum speed (~2x faster than TIFF)\n"
            "fast: blosc-lz4, ~1000 MB/s, ~2x compression (default)\n"
            "balanced: blosc-zstd level 3, ~500 MB/s, ~3-4x compression\n"
            "best: blosc-zstd level 9, slower but best compression"
        )
        zarr_compression_value = self._get_config_value("GENERAL", "zarr_compression", "fast")
        self.zarr_compression_combo.setCurrentText(zarr_compression_value)
        self.zarr_compression_label = QLabel("Zarr Compression:")
        layout.addRow(self.zarr_compression_label, self.zarr_compression_combo)

        # Show/hide zarr options based on file saving format selection
        self._update_zarr_options_visibility()
        self.file_saving_combo.currentTextChanged.connect(self._update_zarr_options_visibility)

        # Default Saving Path
        path_widget = QWidget()
        path_layout = QHBoxLayout(path_widget)
        path_layout.setContentsMargins(0, 0, 0, 0)
        self.saving_path_edit = QLineEdit()
        self.saving_path_edit.setText(
            self._get_config_value("GENERAL", "default_saving_path", str(Path.home() / "Downloads"))
        )
        browse_button = QPushButton("Browse...")
        browse_button.clicked.connect(self._browse_saving_path)
        path_layout.addWidget(self.saving_path_edit)
        path_layout.addWidget(browse_button)
        layout.addRow("Default Saving Path:", path_widget)

        self.tab_widget.addTab(tab, "General")

    def _create_acquisition_tab(self):
        tab = QWidget()
        layout = QFormLayout(tab)
        layout.setSpacing(10)

        # Autofocus Channel
        self.autofocus_channel_edit = QLineEdit()
        self.autofocus_channel_edit.setText(
            self._get_config_value("GENERAL", "multipoint_autofocus_channel", "BF LED matrix full")
        )
        layout.addRow("Autofocus Channel:", self.autofocus_channel_edit)

        # Enable Flexible Multipoint
        self.flexible_multipoint_checkbox = QCheckBox()
        self.flexible_multipoint_checkbox.setChecked(
            self._get_config_bool("GENERAL", "enable_flexible_multipoint", True)
        )
        layout.addRow("Enable Flexible Multipoint:", self.flexible_multipoint_checkbox)

        self.tab_widget.addTab(tab, "Acquisition")

    def _create_camera_tab(self):
        tab = QWidget()
        layout = QFormLayout(tab)
        layout.setSpacing(10)

        # Restart warning label
        restart_label = QLabel("Note: Camera settings require software restart to take effect.")
        restart_label.setStyleSheet("color: #666; font-style: italic;")
        layout.addRow(restart_label)

        # Default Binning Factor
        self.binning_spinbox = QSpinBox()
        self.binning_spinbox.setRange(1, 4)
        self.binning_spinbox.setValue(self._get_config_int("CAMERA_CONFIG", "binning_factor_default", 2))
        layout.addRow("Default Binning Factor:", self.binning_spinbox)

        # Image Flip
        self.flip_combo = QComboBox()
        self.flip_combo.addItems(["None", "Vertical", "Horizontal", "Both"])
        current_flip = self._get_config_value("CAMERA_CONFIG", "flip_image", "None")
        self.flip_combo.setCurrentText(current_flip)
        layout.addRow("Image Flip:", self.flip_combo)

        # Temperature Default
        self.temperature_spinbox = QSpinBox()
        self.temperature_spinbox.setRange(-20, 40)
        self.temperature_spinbox.setValue(self._get_config_int("CAMERA_CONFIG", "temperature_default", 20))
        self.temperature_spinbox.setSuffix(" °C")
        layout.addRow("Temperature Default:", self.temperature_spinbox)

        # ROI Width
        self.roi_width_spinbox = QSpinBox()
        self.roi_width_spinbox.setRange(0, 10000)
        self.roi_width_spinbox.setSpecialValueText("Auto")
        roi_width = self._get_config_value("CAMERA_CONFIG", "roi_width_default", "None")
        if roi_width == "None":
            self.roi_width_spinbox.setValue(0)
        else:
            try:
                self.roi_width_spinbox.setValue(int(roi_width))
            except ValueError:
                self._log.warning(f"Invalid roi_width_default value '{roi_width}', using Auto")
                self.roi_width_spinbox.setValue(0)
        layout.addRow("ROI Width:", self.roi_width_spinbox)

        # ROI Height
        self.roi_height_spinbox = QSpinBox()
        self.roi_height_spinbox.setRange(0, 10000)
        self.roi_height_spinbox.setSpecialValueText("Auto")
        roi_height = self._get_config_value("CAMERA_CONFIG", "roi_height_default", "None")
        if roi_height == "None":
            self.roi_height_spinbox.setValue(0)
        else:
            try:
                self.roi_height_spinbox.setValue(int(roi_height))
            except ValueError:
                self._log.warning(f"Invalid roi_height_default value '{roi_height}', using Auto")
                self.roi_height_spinbox.setValue(0)
        layout.addRow("ROI Height:", self.roi_height_spinbox)

        self.tab_widget.addTab(tab, "Camera")

    def _create_advanced_tab(self):
        tab = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea { background-color: palette(light); border: none; }"
            "QScrollArea > QWidget > QWidget { background-color: palette(light); }"
        )
        scroll_content = QWidget()
        layout = QVBoxLayout(scroll_content)

        # Stage & Motion section (requires restart)
        stage_group = CollapsibleGroupBox("Stage && Motion *", collapsed=True)
        stage_layout = QFormLayout()

        self.max_vel_x = QDoubleSpinBox()
        self.max_vel_x.setRange(0.1, 100)
        self.max_vel_x.setValue(self._get_config_float("GENERAL", "max_velocity_x_mm", 30))
        self.max_vel_x.setSuffix(" mm/s")
        stage_layout.addRow("Max Velocity X:", self.max_vel_x)

        self.max_vel_y = QDoubleSpinBox()
        self.max_vel_y.setRange(0.1, 100)
        self.max_vel_y.setValue(self._get_config_float("GENERAL", "max_velocity_y_mm", 30))
        self.max_vel_y.setSuffix(" mm/s")
        stage_layout.addRow("Max Velocity Y:", self.max_vel_y)

        self.max_vel_z = QDoubleSpinBox()
        self.max_vel_z.setRange(0.1, 20)
        self.max_vel_z.setValue(self._get_config_float("GENERAL", "max_velocity_z_mm", 3.8))
        self.max_vel_z.setSuffix(" mm/s")
        stage_layout.addRow("Max Velocity Z:", self.max_vel_z)

        self.max_accel_x = QDoubleSpinBox()
        self.max_accel_x.setRange(1, 2000)
        self.max_accel_x.setValue(self._get_config_float("GENERAL", "max_acceleration_x_mm", 500))
        self.max_accel_x.setSuffix(" mm/s2")
        stage_layout.addRow("Max Acceleration X:", self.max_accel_x)

        self.max_accel_y = QDoubleSpinBox()
        self.max_accel_y.setRange(1, 2000)
        self.max_accel_y.setValue(self._get_config_float("GENERAL", "max_acceleration_y_mm", 500))
        self.max_accel_y.setSuffix(" mm/s2")
        stage_layout.addRow("Max Acceleration Y:", self.max_accel_y)

        self.max_accel_z = QDoubleSpinBox()
        self.max_accel_z.setRange(1, 500)
        self.max_accel_z.setValue(self._get_config_float("GENERAL", "max_acceleration_z_mm", 100))
        self.max_accel_z.setSuffix(" mm/s2")
        stage_layout.addRow("Max Acceleration Z:", self.max_accel_z)

        self.scan_stab_x = QSpinBox()
        self.scan_stab_x.setRange(0, 1000)
        self.scan_stab_x.setValue(self._get_config_int("GENERAL", "scan_stabilization_time_ms_x", 25))
        self.scan_stab_x.setSuffix(" ms")
        stage_layout.addRow("Scan Stabilization X:", self.scan_stab_x)

        self.scan_stab_y = QSpinBox()
        self.scan_stab_y.setRange(0, 1000)
        self.scan_stab_y.setValue(self._get_config_int("GENERAL", "scan_stabilization_time_ms_y", 25))
        self.scan_stab_y.setSuffix(" ms")
        stage_layout.addRow("Scan Stabilization Y:", self.scan_stab_y)

        self.scan_stab_z = QSpinBox()
        self.scan_stab_z.setRange(0, 1000)
        self.scan_stab_z.setValue(self._get_config_int("GENERAL", "scan_stabilization_time_ms_z", 20))
        self.scan_stab_z.setSuffix(" ms")
        stage_layout.addRow("Scan Stabilization Z:", self.scan_stab_z)

        stage_group.content.addLayout(stage_layout)
        layout.addWidget(stage_group)

        # Zarr v3 Options section
        zarr_group = CollapsibleGroupBox("Zarr v3 Options", collapsed=True)
        zarr_layout = QFormLayout()

        self.zarr_chunk_mode_combo = QComboBox()
        self.zarr_chunk_mode_combo.addItems(["full_frame", "tiled_512", "tiled_256"])
        self.zarr_chunk_mode_combo.setToolTip(
            "full_frame: Each chunk is a full image plane (simplest, default)\n"
            "tiled_512: 512x512 pixel chunks for tiled visualization\n"
            "tiled_256: 256x256 pixel chunks for fine-grained streaming"
        )
        zarr_chunk_mode_value = self._get_config_value("GENERAL", "zarr_chunk_mode", "full_frame")
        self.zarr_chunk_mode_combo.setCurrentText(zarr_chunk_mode_value)
        zarr_layout.addRow("Chunk Mode:", self.zarr_chunk_mode_combo)

        self.zarr_6d_fov_checkbox = QCheckBox()
        self.zarr_6d_fov_checkbox.setToolTip(
            "When enabled, non-HCS acquisitions use a single 6D zarr per region\n"
            "with shape (FOV, T, C, Z, Y, X). This is non-standard but groups\n"
            "all FOVs together. When disabled (default), creates separate 5D\n"
            "OME-NGFF compliant zarr files per FOV."
        )
        zarr_6d_fov_value = self._get_config_bool("GENERAL", "zarr_use_6d_fov_dimension", False)
        self.zarr_6d_fov_checkbox.setChecked(zarr_6d_fov_value)
        zarr_layout.addRow("Use 6D FOV Dimension:", self.zarr_6d_fov_checkbox)

        zarr_group.content.addLayout(zarr_layout)
        layout.addWidget(zarr_group)

        # Contrast Autofocus section
        af_group = CollapsibleGroupBox("Contrast Autofocus", collapsed=True)
        af_layout = QFormLayout()

        self.af_stop_threshold = QDoubleSpinBox()
        self.af_stop_threshold.setRange(0.1, 1.0)
        self.af_stop_threshold.setSingleStep(0.05)
        self.af_stop_threshold.setValue(self._get_config_float("AF", "stop_threshold", 0.85))
        af_layout.addRow("Stop Threshold:", self.af_stop_threshold)

        self.af_crop_width = QSpinBox()
        self.af_crop_width.setRange(100, 4000)
        self.af_crop_width.setValue(self._get_config_int("AF", "crop_width", 800))
        self.af_crop_width.setSuffix(" px")
        af_layout.addRow("Crop Width:", self.af_crop_width)

        self.af_crop_height = QSpinBox()
        self.af_crop_height.setRange(100, 4000)
        self.af_crop_height.setValue(self._get_config_int("AF", "crop_height", 800))
        self.af_crop_height.setSuffix(" px")
        af_layout.addRow("Crop Height:", self.af_crop_height)

        af_group.content.addLayout(af_layout)
        layout.addWidget(af_group)

        # Hardware Configuration section
        hw_group = CollapsibleGroupBox("Hardware Configuration", collapsed=True)
        hw_layout = QFormLayout()

        self.z_motor_combo = QComboBox()
        self.z_motor_combo.addItems(["STEPPER", "STEPPER + PIEZO", "PIEZO", "LINEAR"])
        self.z_motor_combo.setCurrentText(self._get_config_value("GENERAL", "z_motor_config", "STEPPER"))
        hw_layout.addRow("Z Motor Config *:", self.z_motor_combo)

        self.spinning_disk_checkbox = QCheckBox()
        self.spinning_disk_checkbox.setChecked(self._get_config_bool("GENERAL", "enable_spinning_disk_confocal", False))
        hw_layout.addRow("Enable Spinning Disk *:", self.spinning_disk_checkbox)

        self.led_r_factor = QDoubleSpinBox()
        self.led_r_factor.setRange(0.0, 1.0)
        self.led_r_factor.setSingleStep(0.1)
        self.led_r_factor.setValue(self._get_config_float("GENERAL", "led_matrix_r_factor", 1.0))
        hw_layout.addRow("LED Matrix R Factor:", self.led_r_factor)

        self.led_g_factor = QDoubleSpinBox()
        self.led_g_factor.setRange(0.0, 1.0)
        self.led_g_factor.setSingleStep(0.1)
        self.led_g_factor.setValue(self._get_config_float("GENERAL", "led_matrix_g_factor", 1.0))
        hw_layout.addRow("LED Matrix G Factor:", self.led_g_factor)

        self.led_b_factor = QDoubleSpinBox()
        self.led_b_factor.setRange(0.0, 1.0)
        self.led_b_factor.setSingleStep(0.1)
        self.led_b_factor.setValue(self._get_config_float("GENERAL", "led_matrix_b_factor", 1.0))
        hw_layout.addRow("LED Matrix B Factor:", self.led_b_factor)

        self.illumination_factor = QDoubleSpinBox()
        self.illumination_factor.setRange(0.0, 1.0)
        self.illumination_factor.setSingleStep(0.1)
        self.illumination_factor.setValue(self._get_config_float("GENERAL", "illumination_intensity_factor", 0.6))
        hw_layout.addRow("Illumination Intensity Factor:", self.illumination_factor)

        hw_group.content.addLayout(hw_layout)
        layout.addWidget(hw_group)

        # Software Position Limits section
        limits_group = CollapsibleGroupBox("Software Position Limits", collapsed=True)
        limits_layout = QFormLayout()

        self.limit_x_pos = QDoubleSpinBox()
        self.limit_x_pos.setRange(0, 500)
        self.limit_x_pos.setValue(self._get_config_float("SOFTWARE_POS_LIMIT", "x_positive", 115))
        self.limit_x_pos.setSuffix(" mm")
        limits_layout.addRow("X Positive:", self.limit_x_pos)

        self.limit_x_neg = QDoubleSpinBox()
        self.limit_x_neg.setRange(0, 500)
        self.limit_x_neg.setValue(self._get_config_float("SOFTWARE_POS_LIMIT", "x_negative", 5))
        self.limit_x_neg.setSuffix(" mm")
        limits_layout.addRow("X Negative:", self.limit_x_neg)

        self.limit_y_pos = QDoubleSpinBox()
        self.limit_y_pos.setRange(0, 500)
        self.limit_y_pos.setValue(self._get_config_float("SOFTWARE_POS_LIMIT", "y_positive", 76))
        self.limit_y_pos.setSuffix(" mm")
        limits_layout.addRow("Y Positive:", self.limit_y_pos)

        self.limit_y_neg = QDoubleSpinBox()
        self.limit_y_neg.setRange(0, 500)
        self.limit_y_neg.setValue(self._get_config_float("SOFTWARE_POS_LIMIT", "y_negative", 4))
        self.limit_y_neg.setSuffix(" mm")
        limits_layout.addRow("Y Negative:", self.limit_y_neg)

        self.limit_z_pos = QDoubleSpinBox()
        self.limit_z_pos.setRange(0, 50)
        self.limit_z_pos.setValue(self._get_config_float("SOFTWARE_POS_LIMIT", "z_positive", 6))
        self.limit_z_pos.setSuffix(" mm")
        limits_layout.addRow("Z Positive:", self.limit_z_pos)

        self.limit_z_neg = QDoubleSpinBox()
        self.limit_z_neg.setRange(0, 50)
        self.limit_z_neg.setDecimals(3)
        self.limit_z_neg.setValue(self._get_config_float("SOFTWARE_POS_LIMIT", "z_negative", 0.05))
        self.limit_z_neg.setSuffix(" mm")
        limits_layout.addRow("Z Negative:", self.limit_z_neg)

        limits_group.content.addLayout(limits_layout)
        layout.addWidget(limits_group)

        # Tracking section (hidden - widgets exist for config persistence)
        tracking_group = CollapsibleGroupBox("Tracking", collapsed=True)
        tracking_layout = QFormLayout()

        self.enable_tracking_checkbox = QCheckBox()
        self.enable_tracking_checkbox.setChecked(self._get_config_bool("GENERAL", "enable_tracking", False))
        tracking_layout.addRow("Enable Tracking:", self.enable_tracking_checkbox)

        self.default_tracker_combo = QComboBox()
        self.default_tracker_combo.addItems(["csrt", "kcf", "mil", "tld", "medianflow", "mosse", "daSiamRPN"])
        self.default_tracker_combo.setCurrentText(self._get_config_value("TRACKING", "default_tracker", "csrt"))
        tracking_layout.addRow("Default Tracker:", self.default_tracker_combo)

        self.search_area_ratio = QSpinBox()
        self.search_area_ratio.setRange(1, 50)
        self.search_area_ratio.setValue(self._get_config_int("TRACKING", "search_area_ratio", 10))
        tracking_layout.addRow("Search Area Ratio:", self.search_area_ratio)

        tracking_group.content.addLayout(tracking_layout)
        layout.addWidget(tracking_group)
        tracking_group.hide()  # Hidden but widgets exist for config save/load

        # Acquisition Throttling section
        throttle_group = CollapsibleGroupBox("Acquisition Throttling", collapsed=True)
        throttle_layout = QFormLayout()

        self.throttling_enabled_checkbox = QCheckBox()
        self.throttling_enabled_checkbox.setChecked(
            self._get_config_bool(
                "GENERAL", "acquisition_throttling_enabled", control._def.ACQUISITION_THROTTLING_ENABLED
            )
        )
        self.throttling_enabled_checkbox.setToolTip(
            "When enabled, acquisition pauses when pending jobs or RAM usage exceeds limits.\n"
            "Prevents RAM exhaustion when acquisition speed exceeds disk write speed."
        )
        throttle_layout.addRow("Enable Throttling:", self.throttling_enabled_checkbox)

        self.max_pending_jobs_spinbox = QSpinBox()
        self.max_pending_jobs_spinbox.setRange(1, 100)
        self.max_pending_jobs_spinbox.setValue(
            self._get_config_int("GENERAL", "acquisition_max_pending_jobs", control._def.ACQUISITION_MAX_PENDING_JOBS)
        )
        self.max_pending_jobs_spinbox.setToolTip(
            "Maximum number of jobs in flight before throttling.\n"
            "Higher values allow more parallelism but use more RAM."
        )
        throttle_layout.addRow("Max Pending Jobs:", self.max_pending_jobs_spinbox)

        self.max_pending_mb_spinbox = QDoubleSpinBox()
        self.max_pending_mb_spinbox.setRange(100.0, 10000.0)
        self.max_pending_mb_spinbox.setSingleStep(100.0)
        self.max_pending_mb_spinbox.setValue(
            self._get_config_float("GENERAL", "acquisition_max_pending_mb", control._def.ACQUISITION_MAX_PENDING_MB)
        )
        self.max_pending_mb_spinbox.setSuffix(" MB")
        self.max_pending_mb_spinbox.setToolTip(
            "Maximum RAM usage (MB) for pending jobs before throttling.\n"
            "Higher values allow faster acquisition but risk RAM exhaustion."
        )
        throttle_layout.addRow("Max Pending RAM:", self.max_pending_mb_spinbox)

        self.throttle_timeout_spinbox = QDoubleSpinBox()
        self.throttle_timeout_spinbox.setRange(5.0, 300.0)
        self.throttle_timeout_spinbox.setSingleStep(5.0)
        self.throttle_timeout_spinbox.setValue(
            self._get_config_float(
                "GENERAL", "acquisition_throttle_timeout_s", control._def.ACQUISITION_THROTTLE_TIMEOUT_S
            )
        )
        self.throttle_timeout_spinbox.setSuffix(" s")
        self.throttle_timeout_spinbox.setToolTip(
            "Maximum time to wait when throttled before reporting a warning.\n"
            "If disk I/O cannot keep up within this time, acquisition logs a warning."
        )
        throttle_layout.addRow("Throttle Timeout:", self.throttle_timeout_spinbox)

        throttle_group.content.addLayout(throttle_layout)
        layout.addWidget(throttle_group)

        # Diagnostics section
        diagnostics_group = CollapsibleGroupBox("Diagnostics", collapsed=True)
        diagnostics_layout = QFormLayout()

        self.enable_memory_profiling_checkbox = QCheckBox()
        self.enable_memory_profiling_checkbox.setChecked(
            self._get_config_bool("GENERAL", "enable_memory_profiling", control._def.ENABLE_MEMORY_PROFILING)
        )
        self.enable_memory_profiling_checkbox.setToolTip(
            "Show real-time RAM usage in status bar during acquisition.\n"
            "Also logs periodic memory snapshots to help diagnose memory issues."
        )
        diagnostics_layout.addRow("Enable RAM Monitoring:", self.enable_memory_profiling_checkbox)

        diagnostics_group.content.addLayout(diagnostics_layout)
        layout.addWidget(diagnostics_group)

        # Developer Options section
        dev_options_group = CollapsibleGroupBox("Developer Options", collapsed=True)
        dev_options_layout = QFormLayout()

        self.show_dev_tab_checkbox = QCheckBox()
        self.show_dev_tab_checkbox.setChecked(self._get_config_bool("GENERAL", "show_dev_tab", False))
        self.show_dev_tab_checkbox.setToolTip("Show the Dev tab with development/testing settings")
        self.show_dev_tab_checkbox.stateChanged.connect(self._toggle_dev_tab_visibility)
        dev_options_layout.addRow("Show Dev Tab:", self.show_dev_tab_checkbox)

        dev_options_group.content.addLayout(dev_options_layout)
        layout.addWidget(dev_options_group)

        # Legend for restart indicator
        legend_label = QLabel("* Requires software restart to take effect")
        legend_label.setStyleSheet("color: #666; font-style: italic;")
        layout.addWidget(legend_label)

        layout.addStretch()
        scroll.setWidget(scroll_content)

        tab_layout = QVBoxLayout(tab)
        tab_layout.addWidget(scroll)
        self.tab_widget.addTab(tab, "Advanced")

    def _create_views_tab(self):
        # NOTE: Views settings read from control._def (runtime state) instead of config file.
        # This enables MCP commands to modify these settings for RAM usage diagnostics,
        # with changes reflected when this dialog opens. See PR #424 for context.
        # This pattern may be modified if the settings architecture is refactored.

        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(10)

        # Plate View section
        plate_group = CollapsibleGroupBox("Plate View")
        plate_layout = QFormLayout()

        # Save Downsampled Well Images
        self.save_downsampled_checkbox = QCheckBox()
        self.save_downsampled_checkbox.setChecked(control._def.SAVE_DOWNSAMPLED_WELL_IMAGES)
        self.save_downsampled_checkbox.setToolTip(
            "Save individual well TIFFs (e.g., wells/A1_5um.tiff, wells/A1_10um.tiff)"
        )
        plate_layout.addRow("Save Downsampled Well Images:", self.save_downsampled_checkbox)

        # Display Plate View
        self.display_plate_view_checkbox = QCheckBox()
        self.display_plate_view_checkbox.setChecked(control._def.DISPLAY_PLATE_VIEW)
        self.display_plate_view_checkbox.setToolTip(
            "Show plate view tab in GUI during acquisition.\n"
            "Note: Plate view TIFF is always saved when either option is enabled."
        )
        plate_layout.addRow("Display Plate View:", self.display_plate_view_checkbox)

        # Well Resolutions (comma-separated)
        self.well_resolutions_edit = QLineEdit()
        default_resolutions = ", ".join(str(r) for r in control._def.DOWNSAMPLED_WELL_RESOLUTIONS_UM)
        self.well_resolutions_edit.setText(default_resolutions)
        self.well_resolutions_edit.setToolTip(
            "Comma-separated list of resolution values in micrometers (e.g., 5.0, 10.0, 20.0)"
        )
        # Validator for comma-separated positive numbers
        from qtpy.QtCore import QRegularExpression
        from qtpy.QtGui import QRegularExpressionValidator

        well_res_pattern = QRegularExpression(r"^\s*\d+(\.\d+)?(\s*,\s*\d+(\.\d+)?)*\s*$")
        self.well_resolutions_edit.setValidator(QRegularExpressionValidator(well_res_pattern))
        plate_layout.addRow("Well Resolutions (μm):", self.well_resolutions_edit)

        # Target Pixel Size
        self.plate_resolution_spinbox = QDoubleSpinBox()
        self.plate_resolution_spinbox.setRange(1.0, 100.0)
        self.plate_resolution_spinbox.setSingleStep(1.0)
        self.plate_resolution_spinbox.setValue(control._def.DOWNSAMPLED_PLATE_RESOLUTION_UM)
        self.plate_resolution_spinbox.setSuffix(" μm")
        self.plate_resolution_spinbox.setToolTip("Pixel size for the plate view overview image")
        plate_layout.addRow("Target Pixel Size:", self.plate_resolution_spinbox)

        # Z-Projection Mode
        self.z_projection_combo = QComboBox()
        self.z_projection_combo.addItems(["mip", "middle"])
        current_projection = control._def.DOWNSAMPLED_Z_PROJECTION.value
        self.z_projection_combo.setCurrentText(current_projection)
        plate_layout.addRow("Z-Projection Mode:", self.z_projection_combo)

        # Interpolation Method
        self.interpolation_method_combo = QComboBox()
        self.interpolation_method_combo.addItems(["inter_linear", "inter_area_fast", "inter_area"])
        current_interp = control._def.DOWNSAMPLED_INTERPOLATION_METHOD.value
        self.interpolation_method_combo.setCurrentText(current_interp)
        self.interpolation_method_combo.setToolTip(
            "inter_linear: Fastest (~0.05ms), good for real-time previews\n"
            "inter_area_fast: Balanced (~1ms), pyramid downsampling\n"
            "inter_area: Slowest (~18ms), highest quality for final output"
        )
        plate_layout.addRow("Interpolation Method:", self.interpolation_method_combo)

        plate_group.content.addLayout(plate_layout)
        layout.addWidget(plate_group)

        # Mosaic View section
        mosaic_group = CollapsibleGroupBox("Mosaic View")
        mosaic_layout = QFormLayout()

        # Display Mosaic View
        self.display_mosaic_view_checkbox = QCheckBox()
        self.display_mosaic_view_checkbox.setChecked(control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY)
        mosaic_layout.addRow("Display Mosaic View:", self.display_mosaic_view_checkbox)

        # Mosaic Target Pixel Size
        self.mosaic_pixel_size_spinbox = QDoubleSpinBox()
        self.mosaic_pixel_size_spinbox.setRange(0.5, 20.0)
        self.mosaic_pixel_size_spinbox.setSingleStep(0.5)
        self.mosaic_pixel_size_spinbox.setValue(control._def.MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM)
        self.mosaic_pixel_size_spinbox.setSuffix(" μm")
        mosaic_layout.addRow("Target Pixel Size:", self.mosaic_pixel_size_spinbox)

        mosaic_group.content.addLayout(mosaic_layout)
        layout.addWidget(mosaic_group)

        # NDViewer section
        ndviewer_group = CollapsibleGroupBox("NDViewer")
        ndviewer_layout = QFormLayout()

        # Enable NDViewer
        self.enable_ndviewer_checkbox = QCheckBox()
        self.enable_ndviewer_checkbox.setChecked(control._def.ENABLE_NDVIEWER)
        self.enable_ndviewer_checkbox.setToolTip("Enable the NDViewer tab for viewing acquired datasets")
        ndviewer_layout.addRow("Enable NDViewer *:", self.enable_ndviewer_checkbox)

        ndviewer_group.content.addLayout(ndviewer_layout)
        layout.addWidget(ndviewer_group)

        layout.addStretch()
        self.tab_widget.addTab(tab, "Views")

    def _create_development_tab(self):
        """Create the Development tab for development/testing settings."""
        self.dev_tab = QWidget()
        layout = QVBoxLayout(self.dev_tab)
        layout.setSpacing(10)

        # Use Simulated Hardware section
        hw_sim_group = CollapsibleGroupBox("Use Simulated Hardware *")
        hw_sim_layout = QFormLayout()

        # Helper to create simulation checkboxes
        def create_sim_checkbox(config_key):
            checkbox = QCheckBox()
            current = self._get_config_value("SIMULATION", config_key, "false").lower()
            checkbox.setChecked(current in ("true", "1", "yes", "simulate"))
            return checkbox

        sim_tooltip = "Simulate this component (even without --simulation flag).\nWith --simulation flag, ALL components are always simulated."

        self.sim_camera_checkbox = create_sim_checkbox("simulate_camera")
        self.sim_camera_checkbox.setToolTip(sim_tooltip)
        hw_sim_layout.addRow("Simulate Camera:", self.sim_camera_checkbox)

        self.sim_mcu_checkbox = create_sim_checkbox("simulate_microcontroller")
        self.sim_mcu_checkbox.setToolTip(sim_tooltip)
        hw_sim_layout.addRow("Simulate MCU/Stage:", self.sim_mcu_checkbox)

        self.sim_spinning_disk_checkbox = create_sim_checkbox("simulate_spinning_disk")
        self.sim_spinning_disk_checkbox.setToolTip(sim_tooltip)
        hw_sim_layout.addRow("Simulate Spinning Disk:", self.sim_spinning_disk_checkbox)

        self.sim_filter_wheel_checkbox = create_sim_checkbox("simulate_filter_wheel")
        self.sim_filter_wheel_checkbox.setToolTip(sim_tooltip)
        hw_sim_layout.addRow("Simulate Filter Wheel:", self.sim_filter_wheel_checkbox)

        self.sim_objective_changer_checkbox = create_sim_checkbox("simulate_objective_changer")
        self.sim_objective_changer_checkbox.setToolTip(sim_tooltip)
        hw_sim_layout.addRow("Simulate Objective Changer:", self.sim_objective_changer_checkbox)

        self.sim_laser_af_camera_checkbox = create_sim_checkbox("simulate_laser_af_camera")
        self.sim_laser_af_camera_checkbox.setToolTip(sim_tooltip)
        hw_sim_layout.addRow("Simulate Laser AF Camera:", self.sim_laser_af_camera_checkbox)

        hw_sim_group.content.addLayout(hw_sim_layout)
        layout.addWidget(hw_sim_group)

        # Simulated Disk I/O section
        dev_group = CollapsibleGroupBox("Simulated Disk I/O *")
        dev_layout = QFormLayout()

        self.simulated_io_checkbox = QCheckBox()
        self.simulated_io_checkbox.setChecked(self._get_config_bool("GENERAL", "simulated_disk_io_enabled", False))
        self.simulated_io_checkbox.setToolTip(
            "When enabled, images are encoded to memory but NOT saved to disk.\n"
            "Use this for development/testing to avoid SSD wear."
        )
        dev_layout.addRow("Enable Simulated Disk I/O:", self.simulated_io_checkbox)

        self.simulated_io_speed_spinbox = QDoubleSpinBox()
        self.simulated_io_speed_spinbox.setRange(10.0, 3000.0)
        self.simulated_io_speed_spinbox.setValue(
            self._get_config_float("GENERAL", "simulated_disk_io_speed_mb_s", 200.0)
        )
        self.simulated_io_speed_spinbox.setSuffix(" MB/s")
        self.simulated_io_speed_spinbox.setToolTip(
            "Simulated write speed: HDD: 50-100, SATA SSD: 200-500, NVMe: 1000-3000 MB/s"
        )
        dev_layout.addRow("Simulated Write Speed:", self.simulated_io_speed_spinbox)

        self.simulated_io_compression_checkbox = QCheckBox()
        self.simulated_io_compression_checkbox.setChecked(
            self._get_config_bool("GENERAL", "simulated_disk_io_compression", True)
        )
        self.simulated_io_compression_checkbox.setToolTip(
            "When enabled, images are compressed during simulation (more realistic CPU/RAM usage)"
        )
        dev_layout.addRow("Simulate Compression:", self.simulated_io_compression_checkbox)

        dev_group.content.addLayout(dev_layout)
        layout.addWidget(dev_group)

        # Legend
        legend_label = QLabel("* Requires software restart to take effect")
        legend_label.setStyleSheet("color: #666; font-style: italic;")
        layout.addWidget(legend_label)

        layout.addStretch()
        self._dev_tab_index = self.tab_widget.addTab(self.dev_tab, "Dev")

        # Initially hide if not enabled
        if not self._get_config_bool("GENERAL", "show_dev_tab", False):
            self.tab_widget.setTabVisible(self._dev_tab_index, False)

    def _toggle_dev_tab_visibility(self, state):
        """Show or hide the Dev tab based on checkbox state."""
        # Handle both PyQt5 (int) and PyQt6 (CheckState enum) signal types
        # PyQt6 enums have .value property, integers don't - use getattr for compatibility
        state_value = getattr(state, "value", state)
        checked_value = getattr(Qt.Checked, "value", Qt.Checked)
        self.tab_widget.setTabVisible(self._dev_tab_index, state_value == checked_value)

    def _get_config_value(self, section, option, default=""):
        try:
            return self.config.get(section, option)
        except (configparser.NoSectionError, configparser.NoOptionError):
            return default

    def _get_config_bool(self, section, option, default=False):
        try:
            val = self.config.get(section, option)
            return str(val).strip().lower() in ("true", "1", "yes", "on")
        except (configparser.NoSectionError, configparser.NoOptionError):
            return default

    def _get_config_int(self, section, option, default=0):
        try:
            return int(self.config.get(section, option))
        except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
            return default

    def _get_config_float(self, section, option, default=0.0):
        try:
            return float(self.config.get(section, option))
        except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
            return default

    def _floats_equal(self, a, b, epsilon=1e-4):
        """Compare two floats with epsilon tolerance to avoid precision issues."""
        return abs(a - b) < epsilon

    def _browse_saving_path(self):
        path = QFileDialog.getExistingDirectory(self, "Select Default Saving Path", self.saving_path_edit.text())
        if path:
            if os.access(path, os.W_OK):
                self.saving_path_edit.setText(path)
            else:
                QMessageBox.warning(self, "Invalid Path", f"The selected directory is not writable:\n{path}")

    def _update_zarr_options_visibility(self):
        """Show/hide zarr options based on file saving format."""
        is_zarr = self.file_saving_combo.currentText() == "ZARR_V3"
        self.zarr_compression_label.setVisible(is_zarr)
        self.zarr_compression_combo.setVisible(is_zarr)

    def _ensure_section(self, section):
        """Ensure a config section exists, creating it if necessary."""
        if not self.config.has_section(section):
            self.config.add_section(section)

    def _apply_settings(self) -> bool:
        """Apply settings to config file. Returns True on success, False on failure."""
        # Ensure all required sections exist
        for section in ["GENERAL", "CAMERA_CONFIG", "AF", "SOFTWARE_POS_LIMIT", "TRACKING", "VIEWS"]:
            self._ensure_section(section)

        # General settings
        self.config.set("GENERAL", "file_saving_option", self.file_saving_combo.currentText())
        self.config.set("GENERAL", "zarr_compression", self.zarr_compression_combo.currentText())
        self.config.set("GENERAL", "zarr_chunk_mode", self.zarr_chunk_mode_combo.currentText())
        self.config.set(
            "GENERAL", "zarr_use_6d_fov_dimension", "true" if self.zarr_6d_fov_checkbox.isChecked() else "false"
        )
        self.config.set("GENERAL", "default_saving_path", self.saving_path_edit.text())
        self.config.set("GENERAL", "show_dev_tab", "true" if self.show_dev_tab_checkbox.isChecked() else "false")

        # Acquisition settings
        self.config.set("GENERAL", "multipoint_autofocus_channel", self.autofocus_channel_edit.text())
        self.config.set(
            "GENERAL",
            "enable_flexible_multipoint",
            "true" if self.flexible_multipoint_checkbox.isChecked() else "false",
        )

        # Camera settings
        self.config.set("CAMERA_CONFIG", "binning_factor_default", str(self.binning_spinbox.value()))
        self.config.set("CAMERA_CONFIG", "flip_image", self.flip_combo.currentText())
        self.config.set("CAMERA_CONFIG", "temperature_default", str(self.temperature_spinbox.value()))
        roi_width = "None" if self.roi_width_spinbox.value() == 0 else str(self.roi_width_spinbox.value())
        roi_height = "None" if self.roi_height_spinbox.value() == 0 else str(self.roi_height_spinbox.value())
        self.config.set("CAMERA_CONFIG", "roi_width_default", roi_width)
        self.config.set("CAMERA_CONFIG", "roi_height_default", roi_height)

        # Advanced - Stage & Motion
        self.config.set("GENERAL", "max_velocity_x_mm", str(self.max_vel_x.value()))
        self.config.set("GENERAL", "max_velocity_y_mm", str(self.max_vel_y.value()))
        self.config.set("GENERAL", "max_velocity_z_mm", str(self.max_vel_z.value()))
        self.config.set("GENERAL", "max_acceleration_x_mm", str(self.max_accel_x.value()))
        self.config.set("GENERAL", "max_acceleration_y_mm", str(self.max_accel_y.value()))
        self.config.set("GENERAL", "max_acceleration_z_mm", str(self.max_accel_z.value()))
        self.config.set("GENERAL", "scan_stabilization_time_ms_x", str(self.scan_stab_x.value()))
        self.config.set("GENERAL", "scan_stabilization_time_ms_y", str(self.scan_stab_y.value()))
        self.config.set("GENERAL", "scan_stabilization_time_ms_z", str(self.scan_stab_z.value()))

        # Advanced - Autofocus
        self.config.set("AF", "stop_threshold", str(self.af_stop_threshold.value()))
        self.config.set("AF", "crop_width", str(self.af_crop_width.value()))
        self.config.set("AF", "crop_height", str(self.af_crop_height.value()))

        # Advanced - Hardware
        self.config.set("GENERAL", "z_motor_config", self.z_motor_combo.currentText())
        self.config.set(
            "GENERAL",
            "enable_spinning_disk_confocal",
            "true" if self.spinning_disk_checkbox.isChecked() else "false",
        )
        self.config.set("GENERAL", "led_matrix_r_factor", str(self.led_r_factor.value()))
        self.config.set("GENERAL", "led_matrix_g_factor", str(self.led_g_factor.value()))
        self.config.set("GENERAL", "led_matrix_b_factor", str(self.led_b_factor.value()))
        self.config.set("GENERAL", "illumination_intensity_factor", str(self.illumination_factor.value()))

        # Advanced - Development Settings
        self.config.set(
            "GENERAL",
            "simulated_disk_io_enabled",
            "true" if self.simulated_io_checkbox.isChecked() else "false",
        )
        self.config.set("GENERAL", "simulated_disk_io_speed_mb_s", str(self.simulated_io_speed_spinbox.value()))
        self.config.set(
            "GENERAL",
            "simulated_disk_io_compression",
            "true" if self.simulated_io_compression_checkbox.isChecked() else "false",
        )

        # Advanced - Acquisition Throttling
        self.config.set(
            "GENERAL",
            "acquisition_throttling_enabled",
            "true" if self.throttling_enabled_checkbox.isChecked() else "false",
        )
        self.config.set("GENERAL", "acquisition_max_pending_jobs", str(self.max_pending_jobs_spinbox.value()))
        self.config.set("GENERAL", "acquisition_max_pending_mb", str(self.max_pending_mb_spinbox.value()))
        self.config.set("GENERAL", "acquisition_throttle_timeout_s", str(self.throttle_timeout_spinbox.value()))

        # Advanced - Position Limits
        self.config.set("SOFTWARE_POS_LIMIT", "x_positive", str(self.limit_x_pos.value()))
        self.config.set("SOFTWARE_POS_LIMIT", "x_negative", str(self.limit_x_neg.value()))
        self.config.set("SOFTWARE_POS_LIMIT", "y_positive", str(self.limit_y_pos.value()))
        self.config.set("SOFTWARE_POS_LIMIT", "y_negative", str(self.limit_y_neg.value()))
        self.config.set("SOFTWARE_POS_LIMIT", "z_positive", str(self.limit_z_pos.value()))
        self.config.set("SOFTWARE_POS_LIMIT", "z_negative", str(self.limit_z_neg.value()))

        # Advanced - Tracking (hidden but still saved)
        self.config.set("GENERAL", "enable_tracking", "true" if self.enable_tracking_checkbox.isChecked() else "false")
        self.config.set("TRACKING", "default_tracker", self.default_tracker_combo.currentText())
        self.config.set("TRACKING", "search_area_ratio", str(self.search_area_ratio.value()))

        # Advanced - Diagnostics
        self.config.set(
            "GENERAL",
            "enable_memory_profiling",
            "true" if self.enable_memory_profiling_checkbox.isChecked() else "false",
        )

        # Views settings
        self.config.set(
            "VIEWS",
            "save_downsampled_well_images",
            "true" if self.save_downsampled_checkbox.isChecked() else "false",
        )
        self.config.set(
            "VIEWS",
            "display_plate_view",
            "true" if self.display_plate_view_checkbox.isChecked() else "false",
        )
        self.config.set("VIEWS", "downsampled_well_resolutions_um", self.well_resolutions_edit.text())
        self.config.set("VIEWS", "downsampled_plate_resolution_um", str(self.plate_resolution_spinbox.value()))
        self.config.set("VIEWS", "downsampled_z_projection", self.z_projection_combo.currentText())
        self.config.set("VIEWS", "downsampled_interpolation_method", self.interpolation_method_combo.currentText())
        self.config.set(
            "VIEWS",
            "display_mosaic_view",
            "true" if self.display_mosaic_view_checkbox.isChecked() else "false",
        )
        self.config.set("VIEWS", "mosaic_view_target_pixel_size_um", str(self.mosaic_pixel_size_spinbox.value()))
        self.config.set(
            "VIEWS",
            "enable_ndviewer",
            "true" if self.enable_ndviewer_checkbox.isChecked() else "false",
        )

        # Hardware Simulation settings (in [SIMULATION] section)
        self._ensure_section("SIMULATION")
        self.config.set("SIMULATION", "simulate_camera", str(self.sim_camera_checkbox.isChecked()).lower())
        self.config.set("SIMULATION", "simulate_microcontroller", str(self.sim_mcu_checkbox.isChecked()).lower())
        self.config.set(
            "SIMULATION", "simulate_spinning_disk", str(self.sim_spinning_disk_checkbox.isChecked()).lower()
        )
        self.config.set("SIMULATION", "simulate_filter_wheel", str(self.sim_filter_wheel_checkbox.isChecked()).lower())
        self.config.set(
            "SIMULATION", "simulate_objective_changer", str(self.sim_objective_changer_checkbox.isChecked()).lower()
        )
        self.config.set(
            "SIMULATION", "simulate_laser_af_camera", str(self.sim_laser_af_camera_checkbox.isChecked()).lower()
        )

        # Save to file
        try:
            with open(self.config_filepath, "w") as f:
                self.config.write(f)
            self._log.info(f"Configuration saved to {self.config_filepath}")
        except OSError as e:
            self._log.exception("Failed to save configuration")
            QMessageBox.warning(
                self,
                "Error",
                (
                    f"Failed to save configuration to:\n"
                    f"{self.config_filepath}\n\n"
                    "Please check that:\n"
                    "- You have write permission to this location.\n"
                    "- The file is not open in another application.\n"
                    "- The disk is not full or write-protected.\n\n"
                    f"System error: {e}"
                ),
            )
            return False

        # Update runtime values for settings that can be applied live
        try:
            self._apply_live_settings()
        except Exception:
            self._log.exception("Failed to apply live settings")

        self.signal_config_changed.emit()
        return True

    def _apply_live_settings(self):
        """Apply settings that can take effect without restart."""
        # File saving option
        control._def.FILE_SAVING_OPTION = control._def.FileSavingOption.convert_to_enum(
            self.file_saving_combo.currentText()
        )

        # Zarr compression (only applicable when using ZARR_V3)
        control._def.ZARR_COMPRESSION = control._def.ZarrCompression.convert_to_enum(
            self.zarr_compression_combo.currentText()
        )

        # Zarr chunk mode
        control._def.ZARR_CHUNK_MODE = control._def.ZarrChunkMode.convert_to_enum(
            self.zarr_chunk_mode_combo.currentText()
        )

        # Zarr 6D FOV dimension
        control._def.ZARR_USE_6D_FOV_DIMENSION = self.zarr_6d_fov_checkbox.isChecked()

        # Default saving path
        control._def.DEFAULT_SAVING_PATH = self.saving_path_edit.text()

        # Autofocus channel
        control._def.MULTIPOINT_AUTOFOCUS_CHANNEL = self.autofocus_channel_edit.text()

        # Flexible multipoint
        control._def.ENABLE_FLEXIBLE_MULTIPOINT = self.flexible_multipoint_checkbox.isChecked()

        # AF settings
        control._def.AF.STOP_THRESHOLD = self.af_stop_threshold.value()
        control._def.AF.CROP_WIDTH = self.af_crop_width.value()
        control._def.AF.CROP_HEIGHT = self.af_crop_height.value()

        # LED matrix factors
        control._def.LED_MATRIX_R_FACTOR = self.led_r_factor.value()
        control._def.LED_MATRIX_G_FACTOR = self.led_g_factor.value()
        control._def.LED_MATRIX_B_FACTOR = self.led_b_factor.value()

        # Illumination intensity factor
        control._def.ILLUMINATION_INTENSITY_FACTOR = self.illumination_factor.value()

        # Development settings - simulated disk I/O
        control._def.SIMULATED_DISK_IO_ENABLED = self.simulated_io_checkbox.isChecked()
        control._def.SIMULATED_DISK_IO_SPEED_MB_S = self.simulated_io_speed_spinbox.value()
        control._def.SIMULATED_DISK_IO_COMPRESSION = self.simulated_io_compression_checkbox.isChecked()

        # Acquisition throttling settings
        control._def.ACQUISITION_THROTTLING_ENABLED = self.throttling_enabled_checkbox.isChecked()
        control._def.ACQUISITION_MAX_PENDING_JOBS = self.max_pending_jobs_spinbox.value()
        control._def.ACQUISITION_MAX_PENDING_MB = self.max_pending_mb_spinbox.value()
        control._def.ACQUISITION_THROTTLE_TIMEOUT_S = self.throttle_timeout_spinbox.value()

        # Software position limits
        control._def.SOFTWARE_POS_LIMIT.X_POSITIVE = self.limit_x_pos.value()
        control._def.SOFTWARE_POS_LIMIT.X_NEGATIVE = self.limit_x_neg.value()
        control._def.SOFTWARE_POS_LIMIT.Y_POSITIVE = self.limit_y_pos.value()
        control._def.SOFTWARE_POS_LIMIT.Y_NEGATIVE = self.limit_y_neg.value()
        control._def.SOFTWARE_POS_LIMIT.Z_POSITIVE = self.limit_z_pos.value()
        control._def.SOFTWARE_POS_LIMIT.Z_NEGATIVE = self.limit_z_neg.value()

        # Tracking settings (hidden but still updated)
        control._def.ENABLE_TRACKING = self.enable_tracking_checkbox.isChecked()
        control._def.Tracking.DEFAULT_TRACKER = self.default_tracker_combo.currentText()
        control._def.Tracking.SEARCH_AREA_RATIO = self.search_area_ratio.value()

        # Diagnostics settings
        control._def.ENABLE_MEMORY_PROFILING = self.enable_memory_profiling_checkbox.isChecked()

        # Views settings
        control._def.SAVE_DOWNSAMPLED_WELL_IMAGES = self.save_downsampled_checkbox.isChecked()
        control._def.DISPLAY_PLATE_VIEW = self.display_plate_view_checkbox.isChecked()
        # Parse comma-separated resolutions
        resolutions_str = self.well_resolutions_edit.text()
        try:
            control._def.DOWNSAMPLED_WELL_RESOLUTIONS_UM = [
                float(x.strip()) for x in resolutions_str.split(",") if x.strip()
            ]
        except ValueError:
            self._log.warning(f"Invalid well resolutions format: {resolutions_str}")
        control._def.DOWNSAMPLED_PLATE_RESOLUTION_UM = self.plate_resolution_spinbox.value()
        control._def.DOWNSAMPLED_Z_PROJECTION = control._def.ZProjectionMode.convert_to_enum(
            self.z_projection_combo.currentText()
        )
        control._def.DOWNSAMPLED_INTERPOLATION_METHOD = control._def.DownsamplingMethod.convert_to_enum(
            self.interpolation_method_combo.currentText()
        )
        control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY = self.display_mosaic_view_checkbox.isChecked()
        control._def.MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM = self.mosaic_pixel_size_spinbox.value()
        control._def.ENABLE_NDVIEWER = self.enable_ndviewer_checkbox.isChecked()

    def _get_changes(self):
        """Get list of settings that have changed from current config.
        Returns list of (name, old, new, requires_restart) tuples."""
        changes = []

        # General settings (live update)
        old_val = self._get_config_value("GENERAL", "file_saving_option", "OME_TIFF")
        new_val = self.file_saving_combo.currentText()
        if old_val != new_val:
            changes.append(("File Saving Format", old_val, new_val, False))

        old_val = self._get_config_bool("GENERAL", "zarr_use_6d_fov_dimension", False)
        new_val = self.zarr_6d_fov_checkbox.isChecked()
        if old_val != new_val:
            changes.append(("Use 6D FOV Dimension", str(old_val), str(new_val), False))

        old_val = self._get_config_value("GENERAL", "default_saving_path", str(Path.home() / "Downloads"))
        new_val = self.saving_path_edit.text()
        if old_val != new_val:
            changes.append(("Default Saving Path", old_val, new_val, False))

        old_val = self._get_config_bool("GENERAL", "show_dev_tab", False)
        new_val = self.show_dev_tab_checkbox.isChecked()
        if old_val != new_val:
            changes.append(("Show Dev Tab", str(old_val), str(new_val), False))

        # Acquisition settings (live update)
        old_val = self._get_config_value("GENERAL", "multipoint_autofocus_channel", "BF LED matrix full")
        new_val = self.autofocus_channel_edit.text()
        if old_val != new_val:
            changes.append(("Autofocus Channel", old_val, new_val, False))

        old_val = self._get_config_bool("GENERAL", "enable_flexible_multipoint", True)
        new_val = self.flexible_multipoint_checkbox.isChecked()
        if old_val != new_val:
            changes.append(("Enable Flexible Multipoint", str(old_val), str(new_val), False))

        # Camera settings (require restart)
        old_val = self._get_config_int("CAMERA_CONFIG", "binning_factor_default", 2)
        new_val = self.binning_spinbox.value()
        if old_val != new_val:
            changes.append(("Default Binning Factor", str(old_val), str(new_val), True))

        old_val = self._get_config_value("CAMERA_CONFIG", "flip_image", "None")
        new_val = self.flip_combo.currentText()
        if old_val != new_val:
            changes.append(("Image Flip", old_val, new_val, True))

        old_val = self._get_config_int("CAMERA_CONFIG", "temperature_default", 20)
        new_val = self.temperature_spinbox.value()
        if old_val != new_val:
            changes.append(("Temperature Default", f"{old_val} °C", f"{new_val} °C", True))

        old_val = self._get_config_value("CAMERA_CONFIG", "roi_width_default", "None")
        new_val = "None" if self.roi_width_spinbox.value() == 0 else str(self.roi_width_spinbox.value())
        if old_val != new_val:
            changes.append(("ROI Width", old_val, new_val, True))

        old_val = self._get_config_value("CAMERA_CONFIG", "roi_height_default", "None")
        new_val = "None" if self.roi_height_spinbox.value() == 0 else str(self.roi_height_spinbox.value())
        if old_val != new_val:
            changes.append(("ROI Height", old_val, new_val, True))

        # Advanced - Stage & Motion (require restart)
        old_val = self._get_config_float("GENERAL", "max_velocity_x_mm", 30)
        new_val = self.max_vel_x.value()
        if not self._floats_equal(old_val, new_val):
            changes.append(("Max Velocity X", f"{old_val} mm/s", f"{new_val} mm/s", True))

        old_val = self._get_config_float("GENERAL", "max_velocity_y_mm", 30)
        new_val = self.max_vel_y.value()
        if not self._floats_equal(old_val, new_val):
            changes.append(("Max Velocity Y", f"{old_val} mm/s", f"{new_val} mm/s", True))

        old_val = self._get_config_float("GENERAL", "max_velocity_z_mm", 3.8)
        new_val = self.max_vel_z.value()
        if not self._floats_equal(old_val, new_val):
            changes.append(("Max Velocity Z", f"{old_val} mm/s", f"{new_val} mm/s", True))

        old_val = self._get_config_float("GENERAL", "max_acceleration_x_mm", 500)
        new_val = self.max_accel_x.value()
        if not self._floats_equal(old_val, new_val):
            changes.append(("Max Acceleration X", f"{old_val} mm/s2", f"{new_val} mm/s2", True))

        old_val = self._get_config_float("GENERAL", "max_acceleration_y_mm", 500)
        new_val = self.max_accel_y.value()
        if not self._floats_equal(old_val, new_val):
            changes.append(("Max Acceleration Y", f"{old_val} mm/s2", f"{new_val} mm/s2", True))

        old_val = self._get_config_float("GENERAL", "max_acceleration_z_mm", 100)
        new_val = self.max_accel_z.value()
        if not self._floats_equal(old_val, new_val):
            changes.append(("Max Acceleration Z", f"{old_val} mm/s2", f"{new_val} mm/s2", True))

        old_val = self._get_config_int("GENERAL", "scan_stabilization_time_ms_x", 25)
        new_val = self.scan_stab_x.value()
        if old_val != new_val:
            changes.append(("Scan Stabilization X", f"{old_val} ms", f"{new_val} ms", True))

        old_val = self._get_config_int("GENERAL", "scan_stabilization_time_ms_y", 25)
        new_val = self.scan_stab_y.value()
        if old_val != new_val:
            changes.append(("Scan Stabilization Y", f"{old_val} ms", f"{new_val} ms", True))

        old_val = self._get_config_int("GENERAL", "scan_stabilization_time_ms_z", 20)
        new_val = self.scan_stab_z.value()
        if old_val != new_val:
            changes.append(("Scan Stabilization Z", f"{old_val} ms", f"{new_val} ms", True))

        # Advanced - Autofocus (live update)
        old_val = self._get_config_float("AF", "stop_threshold", 0.85)
        new_val = self.af_stop_threshold.value()
        if not self._floats_equal(old_val, new_val):
            changes.append(("AF Stop Threshold", str(old_val), str(new_val), False))

        old_val = self._get_config_int("AF", "crop_width", 800)
        new_val = self.af_crop_width.value()
        if old_val != new_val:
            changes.append(("AF Crop Width", f"{old_val} px", f"{new_val} px", False))

        old_val = self._get_config_int("AF", "crop_height", 800)
        new_val = self.af_crop_height.value()
        if old_val != new_val:
            changes.append(("AF Crop Height", f"{old_val} px", f"{new_val} px", False))

        # Advanced - Hardware (require restart)
        old_val = self._get_config_value("GENERAL", "z_motor_config", "STEPPER")
        new_val = self.z_motor_combo.currentText()
        if old_val != new_val:
            changes.append(("Z Motor Config", old_val, new_val, True))

        old_val = self._get_config_bool("GENERAL", "enable_spinning_disk_confocal", False)
        new_val = self.spinning_disk_checkbox.isChecked()
        if old_val != new_val:
            changes.append(("Enable Spinning Disk", str(old_val), str(new_val), True))

        # LED matrix factors (live update)
        old_val = self._get_config_float("GENERAL", "led_matrix_r_factor", 1.0)
        new_val = self.led_r_factor.value()
        if not self._floats_equal(old_val, new_val):
            changes.append(("LED Matrix R Factor", str(old_val), str(new_val), False))

        old_val = self._get_config_float("GENERAL", "led_matrix_g_factor", 1.0)
        new_val = self.led_g_factor.value()
        if not self._floats_equal(old_val, new_val):
            changes.append(("LED Matrix G Factor", str(old_val), str(new_val), False))

        old_val = self._get_config_float("GENERAL", "led_matrix_b_factor", 1.0)
        new_val = self.led_b_factor.value()
        if not self._floats_equal(old_val, new_val):
            changes.append(("LED Matrix B Factor", str(old_val), str(new_val), False))

        old_val = self._get_config_float("GENERAL", "illumination_intensity_factor", 0.6)
        new_val = self.illumination_factor.value()
        if not self._floats_equal(old_val, new_val):
            changes.append(("Illumination Intensity Factor", str(old_val), str(new_val), False))

        # Advanced - Development Settings
        # Enable/disable requires restart (for warning banner/dialog), but speed/compression
        # take effect on next acquisition since each acquisition starts a fresh subprocess
        old_val = self._get_config_bool("GENERAL", "simulated_disk_io_enabled", False)
        new_val = self.simulated_io_checkbox.isChecked()
        if old_val != new_val:
            changes.append(("Simulated Disk I/O", str(old_val), str(new_val), True))

        old_val = self._get_config_float("GENERAL", "simulated_disk_io_speed_mb_s", 200.0)
        new_val = self.simulated_io_speed_spinbox.value()
        if not self._floats_equal(old_val, new_val):
            changes.append(("Simulated Write Speed", f"{old_val} MB/s", f"{new_val} MB/s", False))

        old_val = self._get_config_bool("GENERAL", "simulated_disk_io_compression", True)
        new_val = self.simulated_io_compression_checkbox.isChecked()
        if old_val != new_val:
            changes.append(("Simulate Compression", str(old_val), str(new_val), False))

        # Advanced - Acquisition Throttling (takes effect on next acquisition)
        old_val = self._get_config_bool(
            "GENERAL", "acquisition_throttling_enabled", control._def.ACQUISITION_THROTTLING_ENABLED
        )
        new_val = self.throttling_enabled_checkbox.isChecked()
        if old_val != new_val:
            changes.append(("Acquisition Throttling", str(old_val), str(new_val), False))

        old_val = self._get_config_int(
            "GENERAL", "acquisition_max_pending_jobs", control._def.ACQUISITION_MAX_PENDING_JOBS
        )
        new_val = self.max_pending_jobs_spinbox.value()
        if old_val != new_val:
            changes.append(("Max Pending Jobs", str(old_val), str(new_val), False))

        old_val = self._get_config_float(
            "GENERAL", "acquisition_max_pending_mb", control._def.ACQUISITION_MAX_PENDING_MB
        )
        new_val = self.max_pending_mb_spinbox.value()
        if not self._floats_equal(old_val, new_val):
            changes.append(("Max Pending RAM", f"{old_val} MB", f"{new_val} MB", False))

        old_val = self._get_config_float(
            "GENERAL", "acquisition_throttle_timeout_s", control._def.ACQUISITION_THROTTLE_TIMEOUT_S
        )
        new_val = self.throttle_timeout_spinbox.value()
        if not self._floats_equal(old_val, new_val):
            changes.append(("Throttle Timeout", f"{old_val} s", f"{new_val} s", False))

        # Advanced - Position Limits (live update)
        old_val = self._get_config_float("SOFTWARE_POS_LIMIT", "x_positive", 115)
        new_val = self.limit_x_pos.value()
        if not self._floats_equal(old_val, new_val):
            changes.append(("X Positive Limit", f"{old_val} mm", f"{new_val} mm", False))

        old_val = self._get_config_float("SOFTWARE_POS_LIMIT", "x_negative", 5)
        new_val = self.limit_x_neg.value()
        if not self._floats_equal(old_val, new_val):
            changes.append(("X Negative Limit", f"{old_val} mm", f"{new_val} mm", False))

        old_val = self._get_config_float("SOFTWARE_POS_LIMIT", "y_positive", 76)
        new_val = self.limit_y_pos.value()
        if not self._floats_equal(old_val, new_val):
            changes.append(("Y Positive Limit", f"{old_val} mm", f"{new_val} mm", False))

        old_val = self._get_config_float("SOFTWARE_POS_LIMIT", "y_negative", 4)
        new_val = self.limit_y_neg.value()
        if not self._floats_equal(old_val, new_val):
            changes.append(("Y Negative Limit", f"{old_val} mm", f"{new_val} mm", False))

        old_val = self._get_config_float("SOFTWARE_POS_LIMIT", "z_positive", 6)
        new_val = self.limit_z_pos.value()
        if not self._floats_equal(old_val, new_val):
            changes.append(("Z Positive Limit", f"{old_val} mm", f"{new_val} mm", False))

        old_val = self._get_config_float("SOFTWARE_POS_LIMIT", "z_negative", 0.05)
        new_val = self.limit_z_neg.value()
        if not self._floats_equal(old_val, new_val):
            changes.append(("Z Negative Limit", f"{old_val} mm", f"{new_val} mm", False))

        # Advanced - Tracking (hidden but still tracked)
        old_val = self._get_config_bool("GENERAL", "enable_tracking", False)
        new_val = self.enable_tracking_checkbox.isChecked()
        if old_val != new_val:
            changes.append(("Enable Tracking", str(old_val), str(new_val), False))

        old_val = self._get_config_value("TRACKING", "default_tracker", "csrt")
        new_val = self.default_tracker_combo.currentText()
        if old_val != new_val:
            changes.append(("Default Tracker", old_val, new_val, False))

        old_val = self._get_config_int("TRACKING", "search_area_ratio", 10)
        new_val = self.search_area_ratio.value()
        if old_val != new_val:
            changes.append(("Search Area Ratio", str(old_val), str(new_val), False))

        # Advanced - Diagnostics (live update)
        old_val = self._get_config_bool("GENERAL", "enable_memory_profiling", control._def.ENABLE_MEMORY_PROFILING)
        new_val = self.enable_memory_profiling_checkbox.isChecked()
        if old_val != new_val:
            changes.append(("Enable RAM Monitoring", str(old_val), str(new_val), False))

        # Views settings (live update)
        # NOTE: Compare against control._def values (runtime state) since UI is initialized from control._def.
        # This enables MCP commands to modify these settings for RAM usage diagnostics.
        # See PR #424 for context. This pattern may change if settings architecture is refactored.
        old_val = control._def.SAVE_DOWNSAMPLED_WELL_IMAGES
        new_val = self.save_downsampled_checkbox.isChecked()
        if old_val != new_val:
            changes.append(("Save Downsampled Well Images", str(old_val), str(new_val), False))

        old_val = control._def.DISPLAY_PLATE_VIEW
        new_val = self.display_plate_view_checkbox.isChecked()
        if old_val != new_val:
            changes.append(("Display Plate View *", str(old_val), str(new_val), True))

        old_val = ", ".join(str(r) for r in control._def.DOWNSAMPLED_WELL_RESOLUTIONS_UM)
        new_val = self.well_resolutions_edit.text()
        if old_val != new_val:
            changes.append(("Well Resolutions", old_val, new_val, False))

        old_val = control._def.DOWNSAMPLED_PLATE_RESOLUTION_UM
        new_val = self.plate_resolution_spinbox.value()
        if not self._floats_equal(old_val, new_val):
            changes.append(("Target Pixel Size", f"{old_val} μm", f"{new_val} μm", False))

        old_val = control._def.DOWNSAMPLED_Z_PROJECTION.value
        new_val = self.z_projection_combo.currentText()
        if old_val != new_val:
            changes.append(("Z-Projection Mode", old_val, new_val, False))

        old_val = control._def.DOWNSAMPLED_INTERPOLATION_METHOD.value
        new_val = self.interpolation_method_combo.currentText()
        if old_val != new_val:
            changes.append(("Interpolation Method", old_val, new_val, False))

        old_val = control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY
        new_val = self.display_mosaic_view_checkbox.isChecked()
        if old_val != new_val:
            changes.append(("Display Mosaic View *", str(old_val), str(new_val), True))

        old_val = control._def.MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM
        new_val = self.mosaic_pixel_size_spinbox.value()
        if not self._floats_equal(old_val, new_val):
            changes.append(("Mosaic Target Pixel Size", f"{old_val} μm", f"{new_val} μm", False))

        old_val = control._def.ENABLE_NDVIEWER
        new_val = self.enable_ndviewer_checkbox.isChecked()
        if old_val != new_val:
            changes.append(("Enable NDViewer *", str(old_val), str(new_val), True))

        # Hardware Simulation settings (require restart)
        old_val = self._get_config_value("SIMULATION", "simulate_camera", "false").lower() == "true"
        new_val = self.sim_camera_checkbox.isChecked()
        if old_val != new_val:
            changes.append(("Simulate Camera *", str(old_val), str(new_val), True))

        old_val = self._get_config_value("SIMULATION", "simulate_microcontroller", "false").lower() == "true"
        new_val = self.sim_mcu_checkbox.isChecked()
        if old_val != new_val:
            changes.append(("Simulate MCU/Stage *", str(old_val), str(new_val), True))

        old_val = self._get_config_value("SIMULATION", "simulate_spinning_disk", "false").lower() == "true"
        new_val = self.sim_spinning_disk_checkbox.isChecked()
        if old_val != new_val:
            changes.append(("Simulate Spinning Disk *", str(old_val), str(new_val), True))

        old_val = self._get_config_value("SIMULATION", "simulate_filter_wheel", "false").lower() == "true"
        new_val = self.sim_filter_wheel_checkbox.isChecked()
        if old_val != new_val:
            changes.append(("Simulate Filter Wheel *", str(old_val), str(new_val), True))

        old_val = self._get_config_value("SIMULATION", "simulate_objective_changer", "false").lower() == "true"
        new_val = self.sim_objective_changer_checkbox.isChecked()
        if old_val != new_val:
            changes.append(("Simulate Objective Changer *", str(old_val), str(new_val), True))

        old_val = self._get_config_value("SIMULATION", "simulate_laser_af_camera", "false").lower() == "true"
        new_val = self.sim_laser_af_camera_checkbox.isChecked()
        if old_val != new_val:
            changes.append(("Simulate Laser AF Camera *", str(old_val), str(new_val), True))

        return changes

    def _offer_restart_dialog(self):
        """Show a dialog offering to restart the application now."""
        msg = QMessageBox(self)
        msg.setWindowTitle("Restart Required")
        msg.setText("Settings have been saved. This change requires a restart to take effect.")
        msg.setInformativeText("Would you like to restart now?")
        msg.setIcon(QMessageBox.Information)
        restart_btn = msg.addButton("Restart Now", QMessageBox.AcceptRole)
        msg.addButton("Later", QMessageBox.RejectRole)
        msg.exec_()
        if msg.clickedButton() == restart_btn:
            self._trigger_restart()

    def _trigger_restart(self):
        """Trigger application restart via callback."""
        if self._on_restart:
            try:
                self._on_restart()
            except Exception as e:
                self._log.exception("Failed to restart application")
                QMessageBox.warning(
                    self,
                    "Restart Failed",
                    f"An error occurred while trying to restart the application.\n\n"
                    f"Error: {e}\n\nPlease restart the application manually.",
                )
        else:
            self._log.error("No restart callback configured")
            QMessageBox.warning(
                self,
                "Restart Failed",
                "Could not trigger automatic restart.\nPlease restart the application manually.",
            )

    def _save_and_close(self):
        changes = self._get_changes()

        if not changes:
            self.accept()
            return

        # Check if any changes require restart
        requires_restart = any(change[3] for change in changes)

        # For single change, save directly without confirmation
        if len(changes) == 1:
            if not self._apply_settings():
                return  # Save failed, dialog stays open
            if requires_restart:
                self._offer_restart_dialog()
            self.accept()
            return

        # For multiple changes, show confirmation dialog
        dialog = QDialog(self)
        dialog.setWindowTitle("Confirm Changes")
        dialog.setMinimumWidth(450)
        if self.isModal():
            dialog.setModal(True)
        layout = QVBoxLayout(dialog)

        label = QLabel("The following settings will be changed:")
        layout.addWidget(label)

        # Create text showing before/after for each change
        changes_text = QTextEdit()
        changes_text.setReadOnly(True)
        changes_lines = []
        for name, old_val, new_val, needs_restart in changes:
            restart_note = " [restart required]" if needs_restart else ""
            changes_lines.append(f"{name}{restart_note}:\n  Before: {old_val}\n  After:  {new_val}")
        changes_text.setPlainText("\n\n".join(changes_lines))
        changes_text.setMinimumHeight(200)
        layout.addWidget(changes_text)

        # Only show restart warning if at least one change requires restart
        if requires_restart:
            note_label = QLabel(
                "Note: Settings marked [restart required] will only take effect after restarting the software."
            )
            note_label.setStyleSheet("color: #666; font-style: italic;")
            note_label.setWordWrap(True)
            layout.addWidget(note_label)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        # Track which button was clicked
        dialog.restart_requested = False

        if requires_restart:
            save_restart_btn = QPushButton("Save and Restart")
            save_restart_btn.setToolTip("Save settings and restart the application now")

            def on_save_restart():
                dialog.restart_requested = True
                dialog.accept()

            save_restart_btn.clicked.connect(on_save_restart)
            button_layout.addWidget(save_restart_btn)

        save_btn = QPushButton("Save")
        cancel_btn = QPushButton("Cancel")
        save_btn.clicked.connect(dialog.accept)
        cancel_btn.clicked.connect(dialog.reject)
        button_layout.addWidget(save_btn)
        button_layout.addWidget(cancel_btn)
        layout.addLayout(button_layout)

        if dialog.exec_() == QDialog.Accepted:
            if self._apply_settings():
                if dialog.restart_requested:
                    self._trigger_restart()
                self.accept()
            # If save failed, dialog stays open (error already shown)


class StageUtils(QDialog):
    """Dialog containing microscope utility functions like homing, zeroing, and slide positioning."""

    signal_threaded_stage_move_started = Signal()
    signal_loading_position_reached = Signal()
    signal_scanning_position_reached = Signal()

    def __init__(self, stage: AbstractStage, live_controller: LiveController, is_wellplate: bool, parent=None):
        super().__init__(parent)
        self.log = squid.logging.get_logger(self.__class__.__name__)
        self.stage = stage
        self.live_controller = live_controller
        self.is_wellplate = is_wellplate
        self.slide_position = None

        self.setWindowTitle("Stage Utils")
        self.setModal(False)  # Allow interaction with main window while dialog is open
        self.setup_ui()

    def setup_ui(self):
        """Setup the UI components."""
        # Create buttons
        self.btn_home_X = QPushButton("Home X")
        self.btn_home_X.setDefault(False)
        self.btn_home_X.setEnabled(HOMING_ENABLED_X)

        self.btn_home_Y = QPushButton("Home Y")
        self.btn_home_Y.setDefault(False)
        self.btn_home_Y.setEnabled(HOMING_ENABLED_Y)

        self.btn_home_Z = QPushButton("Home Z")
        self.btn_home_Z.setDefault(False)
        self.btn_home_Z.setEnabled(HOMING_ENABLED_Z)

        self.btn_zero_X = QPushButton("Zero X")
        self.btn_zero_X.setDefault(False)

        self.btn_zero_Y = QPushButton("Zero Y")
        self.btn_zero_Y.setDefault(False)

        self.btn_zero_Z = QPushButton("Zero Z")
        self.btn_zero_Z.setDefault(False)

        self.btn_load_slide = QPushButton("Move To Loading Position")
        self.btn_load_slide.setStyleSheet("background-color: #C2C2FF")

        # Connect buttons to functions
        self.btn_home_X.clicked.connect(self.home_x)
        self.btn_home_Y.clicked.connect(self.home_y)
        self.btn_home_Z.clicked.connect(self.home_z)
        self.btn_zero_X.clicked.connect(self.zero_x)
        self.btn_zero_Y.clicked.connect(self.zero_y)
        self.btn_zero_Z.clicked.connect(self.zero_z)
        self.btn_load_slide.clicked.connect(self.switch_position)

        # Layout
        main_layout = QVBoxLayout()

        # Homing section
        homing_group = QGroupBox("Homing")
        homing_layout = QHBoxLayout()
        homing_layout.addWidget(self.btn_home_X)
        homing_layout.addWidget(self.btn_home_Y)
        homing_layout.addWidget(self.btn_home_Z)
        homing_group.setLayout(homing_layout)

        # Zero section
        zero_group = QGroupBox("Zero Position")
        zero_layout = QHBoxLayout()
        zero_layout.addWidget(self.btn_zero_X)
        zero_layout.addWidget(self.btn_zero_Y)
        zero_layout.addWidget(self.btn_zero_Z)
        zero_group.setLayout(zero_layout)

        # Slide positioning section
        slide_group = QGroupBox("Slide Positioning")
        slide_layout = QVBoxLayout()
        slide_layout.addWidget(self.btn_load_slide)
        slide_group.setLayout(slide_layout)

        # Add sections to main layout
        main_layout.addWidget(homing_group)
        main_layout.addWidget(zero_group)
        main_layout.addWidget(slide_group)

        # Close button
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        main_layout.addWidget(close_button)

        self.setLayout(main_layout)

    def home_x(self):
        """Home X axis with confirmation dialog."""
        self._show_confirmation_dialog(x=True, y=False, z=False, theta=False)

    def home_y(self):
        """Home Y axis with confirmation dialog."""
        self._show_confirmation_dialog(x=False, y=True, z=False, theta=False)

    def home_z(self):
        """Home Z axis with confirmation dialog."""
        self._show_confirmation_dialog(x=False, y=False, z=True, theta=False)
        move_z_axis_to_safety_position(self.stage)

    def _show_confirmation_dialog(self, x: bool, y: bool, z: bool, theta: bool):
        """Display a confirmation dialog and home the specified axis if confirmed."""
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Information)
        msg.setText("Confirm your action")
        msg.setInformativeText("Click OK to run homing")
        msg.setWindowTitle("Confirmation")
        msg.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        msg.setDefaultButton(QMessageBox.Cancel)
        retval = msg.exec_()
        if QMessageBox.Ok == retval:
            self.stage.home(x=x, y=y, z=z, theta=theta)

    def zero_x(self):
        """Zero X axis position."""
        self.stage.zero(x=True, y=False, z=False, theta=False)

    def zero_y(self):
        """Zero Y axis position."""
        self.stage.zero(x=False, y=True, z=False, theta=False)

    def zero_z(self):
        """Zero Z axis position."""
        self.stage.zero(x=False, y=False, z=True, theta=False)

    def switch_position(self):
        """Switch between loading and scanning positions."""
        self._was_live = self.live_controller.is_live
        if self._was_live:
            self.live_controller.stop_live()
        self.signal_threaded_stage_move_started.emit()
        if self.slide_position != "loading":
            move_to_loading_position(
                self.stage,
                blocking=False,
                callback=self._callback_loading_position_reached,
                is_wellplate=self.is_wellplate,
            )
        else:
            move_to_scanning_position(
                self.stage,
                blocking=False,
                callback=self._callback_scanning_position_reached,
                is_wellplate=self.is_wellplate,
            )
        self.btn_load_slide.setEnabled(False)

    def _callback_loading_position_reached(self, success: bool, error_message: Optional[str]):
        """Handle slide loading position reached signal."""
        self.slide_position = "loading"
        self.btn_load_slide.setStyleSheet("background-color: #C2FFC2")
        self.btn_load_slide.setText("Move to Scanning Position")
        self.btn_load_slide.setEnabled(True)
        if self._was_live:
            self.live_controller.start_live()
        if not success:
            QMessageBox.warning(self, "Error", error_message)
        self.signal_loading_position_reached.emit()

    def _callback_scanning_position_reached(self, success: bool, error_message: Optional[str]):
        """Handle slide scanning position reached signal."""
        self.slide_position = "scanning"
        self.btn_load_slide.setStyleSheet("background-color: #C2C2FF")
        self.btn_load_slide.setText("Move to Loading Position")
        self.btn_load_slide.setEnabled(True)
        if self._was_live:
            self.live_controller.start_live()
        if not success:
            QMessageBox.warning(self, "Error", error_message)
        self.signal_scanning_position_reached.emit()


class LaserAutofocusSettingWidget(QWidget):

    signal_newExposureTime = Signal(float)
    signal_newAnalogGain = Signal(float)
    signal_apply_settings = Signal()
    signal_laser_spot_location = Signal(np.ndarray, float, float)

    def __init__(self, streamHandler, liveController: LiveController, laserAutofocusController, stretch=True):
        super().__init__()
        self.streamHandler = streamHandler
        self.liveController: LiveController = liveController
        self.laserAutofocusController = laserAutofocusController
        self.stretch = stretch
        self.liveController.set_trigger_fps(10)
        self.streamHandler.set_display_fps(10)

        # Enable background filling
        self.setAutoFillBackground(True)

        # Create and set background color
        palette = self.palette()
        palette.setColor(self.backgroundRole(), QColor(240, 240, 240))
        self.setPalette(palette)

        self.spinboxes = {}
        self.init_ui()
        self.update_calibration_label()

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(9, 9, 9, 9)

        # Live control group
        live_group = QFrame()
        live_group.setFrameStyle(QFrame.Panel | QFrame.Raised)
        live_layout = QVBoxLayout()

        # Live button
        self.btn_live = QPushButton("Start Live")
        self.btn_live.setCheckable(True)
        self.btn_live.setStyleSheet("background-color: #C2C2FF")

        # Exposure time control
        exposure_layout = QHBoxLayout()
        exposure_layout.addWidget(QLabel("Focus Camera Exposure (ms):"))
        self.exposure_spinbox = QDoubleSpinBox()
        self.exposure_spinbox.setKeyboardTracking(False)
        self.exposure_spinbox.setSingleStep(0.1)
        try:
            exposure_min_ms, exposure_max_ms = self.laserAutofocusController.camera.get_exposure_limits()
        except Exception:
            exposure_min_ms, exposure_max_ms = 0.01, 10000.0
        self.exposure_spinbox.setRange(exposure_min_ms, exposure_max_ms)
        self.exposure_spinbox.setValue(self.laserAutofocusController.laser_af_properties.focus_camera_exposure_time_ms)
        exposure_layout.addWidget(self.exposure_spinbox)

        # Analog gain control
        analog_gain_layout = QHBoxLayout()
        analog_gain_layout.addWidget(QLabel("Focus Camera Analog Gain:"))
        self.analog_gain_spinbox = QDoubleSpinBox()
        self.analog_gain_spinbox.setKeyboardTracking(False)
        self.analog_gain_spinbox.setRange(0, 24)
        self.analog_gain_spinbox.setValue(self.laserAutofocusController.laser_af_properties.focus_camera_analog_gain)
        analog_gain_layout.addWidget(self.analog_gain_spinbox)

        # Add to live group
        live_layout.addWidget(self.btn_live)
        live_layout.addLayout(exposure_layout)
        live_layout.addLayout(analog_gain_layout)
        live_group.setLayout(live_layout)

        # Non-threshold property group
        non_threshold_group = QFrame()
        non_threshold_group.setFrameStyle(QFrame.Panel | QFrame.Raised)
        non_threshold_layout = QVBoxLayout()

        # Add non-threshold property spinboxes
        self._add_spinbox(non_threshold_layout, "Spot Crop Size (pixels):", "spot_crop_size", 1, 500, 0)
        self._add_spinbox(
            non_threshold_layout, "Calibration Distance (μm):", "pixel_to_um_calibration_distance", 0.1, 20.0, 2
        )
        non_threshold_group.setLayout(non_threshold_layout)

        # Settings group
        settings_group = QFrame()
        settings_group.setFrameStyle(QFrame.Panel | QFrame.Raised)
        settings_layout = QVBoxLayout()

        # Add threshold property spinboxes
        self._add_spinbox(settings_layout, "Laser AF Averaging N:", "laser_af_averaging_n", 1, 100, 0)
        self._add_spinbox(
            settings_layout, "Displacement Success Window (μm):", "displacement_success_window_um", 0.1, 10.0, 2
        )
        self._add_spinbox(settings_layout, "Correlation Threshold:", "correlation_threshold", 0.1, 1.0, 2, 0.1)
        self._add_spinbox(settings_layout, "Laser AF Range (μm):", "laser_af_range", 1, 1000, 1)
        self.update_threshold_button = QPushButton("Apply without Re-initialization")
        settings_layout.addWidget(self.update_threshold_button)
        settings_group.setLayout(settings_layout)

        # Create spot detection group
        spot_detection_group = QFrame()
        spot_detection_group.setFrameStyle(QFrame.Panel | QFrame.Raised)
        spot_detection_layout = QVBoxLayout()

        # Add spot detection related spinboxes
        self._add_spinbox(spot_detection_layout, "Y Window (pixels):", "y_window", 1, 500, 0)
        self._add_spinbox(spot_detection_layout, "X Window (pixels):", "x_window", 1, 500, 0)
        self._add_spinbox(spot_detection_layout, "Min Peak Width:", "min_peak_width", 1, 100, 1)
        self._add_spinbox(spot_detection_layout, "Min Peak Distance:", "min_peak_distance", 1, 100, 1)
        self._add_spinbox(spot_detection_layout, "Min Peak Prominence:", "min_peak_prominence", 0.01, 1.0, 2, 0.1)
        self._add_spinbox(spot_detection_layout, "Spot Spacing (pixels):", "spot_spacing", 1, 1000, 1)
        self._add_spinbox(spot_detection_layout, "Filter Sigma:", "filter_sigma", 0, 100, 1, allow_none=True)

        # Spot detection mode combo box
        spot_mode_layout = QHBoxLayout()
        spot_mode_layout.addWidget(QLabel("Spot Detection Mode:"))
        self.spot_mode_combo = QComboBox()
        for mode in SpotDetectionMode:
            self.spot_mode_combo.addItem(mode.value, mode)
        current_index = self.spot_mode_combo.findData(
            self.laserAutofocusController.laser_af_properties.spot_detection_mode
        )
        self.spot_mode_combo.setCurrentIndex(current_index)
        spot_mode_layout.addWidget(self.spot_mode_combo)
        spot_detection_layout.addLayout(spot_mode_layout)

        # Add Run Spot Detection button
        self.run_spot_detection_button = QPushButton("Run Spot Detection")
        self.run_spot_detection_button.setEnabled(False)  # Disabled by default
        spot_detection_layout.addWidget(self.run_spot_detection_button)
        spot_detection_group.setLayout(spot_detection_layout)

        # Initialize button
        initialize_group = QFrame()
        initialize_layout = QVBoxLayout()
        self.initialize_button = QPushButton("Initialize")
        self.initialize_button.setStyleSheet("background-color: #C2C2FF")
        initialize_layout.addWidget(self.initialize_button)
        initialize_group.setLayout(initialize_layout)

        # Add Laser AF Characterization Mode checkbox
        characterization_group = QFrame()
        characterization_layout = QHBoxLayout()
        self.characterization_checkbox = QCheckBox("Laser AF Characterization Mode")
        self.characterization_checkbox.setChecked(self.laserAutofocusController.characterization_mode)
        characterization_layout.addWidget(self.characterization_checkbox)
        characterization_group.setLayout(characterization_layout)

        # Add to main layout
        layout.addWidget(live_group)
        layout.addWidget(non_threshold_group)
        layout.addWidget(settings_group)
        layout.addWidget(spot_detection_group)
        layout.addWidget(initialize_group)
        layout.addWidget(characterization_group)
        self.setLayout(layout)

        if not self.stretch:
            layout.addStretch()

        # Connect all signals to slots
        self.btn_live.clicked.connect(self.toggle_live)
        self.exposure_spinbox.valueChanged.connect(self.update_exposure_time)
        self.analog_gain_spinbox.valueChanged.connect(self.update_analog_gain)
        self.update_threshold_button.clicked.connect(self.update_threshold_settings)
        self.run_spot_detection_button.clicked.connect(self.run_spot_detection)
        self.initialize_button.clicked.connect(self.apply_and_initialize)
        self.characterization_checkbox.toggled.connect(self.toggle_characterization_mode)

    def _add_spinbox(
        self,
        layout,
        label: str,
        property_name: str,
        min_val: float,
        max_val: float,
        decimals: int,
        step: float = 1,
        allow_none=False,
    ) -> None:
        """Helper method to add a labeled spinbox to the layout."""
        box_layout = QHBoxLayout()
        box_layout.addWidget(QLabel(label))

        spinbox = QDoubleSpinBox()
        spinbox.setKeyboardTracking(False)
        if allow_none:
            spinbox.setRange(min_val - step, max_val)
            spinbox.setSpecialValueText("None")
        else:
            spinbox.setRange(min_val, max_val)
        spinbox.setDecimals(decimals)
        spinbox.setSingleStep(step)
        # Get initial value from laser_af_properties
        current_value = getattr(self.laserAutofocusController.laser_af_properties, property_name)
        if allow_none and current_value is None:
            spinbox.setValue(min_val - step)
        else:
            spinbox.setValue(current_value)

        box_layout.addWidget(spinbox)
        layout.addLayout(box_layout)

        # Store spinbox reference
        self.spinboxes[property_name] = spinbox

    def toggle_live(self, pressed):
        if pressed:
            self.liveController.start_live()
            self.btn_live.setText("Stop Live")
            self.run_spot_detection_button.setEnabled(False)
        else:
            self.liveController.stop_live()
            self.btn_live.setText("Start Live")
            self.run_spot_detection_button.setEnabled(True)

    def stop_live(self):
        """Used for stopping live when switching to other tabs"""
        self.toggle_live(False)
        self.btn_live.setChecked(False)

    def toggle_characterization_mode(self, state):
        self.laserAutofocusController.characterization_mode = state

    def update_exposure_time(self, value):
        self.signal_newExposureTime.emit(value)

    def update_analog_gain(self, value):
        self.signal_newAnalogGain.emit(value)

    def update_values(self):
        """Update all widget values from the controller properties"""
        self.clear_labels()

        # Update spinboxes
        for prop_name, spinbox in self.spinboxes.items():
            current_value = getattr(self.laserAutofocusController.laser_af_properties, prop_name)
            if current_value is None:
                # For spinboxes that allow None, set to minimum (shows "None" special text)
                spinbox.setValue(spinbox.minimum())
            else:
                spinbox.setValue(current_value)

        # Update exposure and gain
        self.exposure_spinbox.setValue(self.laserAutofocusController.laser_af_properties.focus_camera_exposure_time_ms)
        self.analog_gain_spinbox.setValue(self.laserAutofocusController.laser_af_properties.focus_camera_analog_gain)

        # Update spot detection mode
        current_mode = self.laserAutofocusController.laser_af_properties.spot_detection_mode
        index = self.spot_mode_combo.findData(current_mode)
        if index >= 0:
            self.spot_mode_combo.setCurrentIndex(index)

        self.update_threshold_button.setEnabled(self.laserAutofocusController.is_initialized)
        self.update_calibration_label()

    def apply_and_initialize(self):
        self.clear_labels()

        updates = {
            "laser_af_averaging_n": int(self.spinboxes["laser_af_averaging_n"].value()),
            "displacement_success_window_um": self.spinboxes["displacement_success_window_um"].value(),
            "spot_crop_size": int(self.spinboxes["spot_crop_size"].value()),
            "correlation_threshold": self.spinboxes["correlation_threshold"].value(),
            "pixel_to_um_calibration_distance": self.spinboxes["pixel_to_um_calibration_distance"].value(),
            "laser_af_range": self.spinboxes["laser_af_range"].value(),
            "spot_detection_mode": self.spot_mode_combo.currentData(),
            "y_window": int(self.spinboxes["y_window"].value()),
            "x_window": int(self.spinboxes["x_window"].value()),
            "min_peak_width": self.spinboxes["min_peak_width"].value(),
            "min_peak_distance": self.spinboxes["min_peak_distance"].value(),
            "min_peak_prominence": self.spinboxes["min_peak_prominence"].value(),
            "spot_spacing": self.spinboxes["spot_spacing"].value(),
            "filter_sigma": self.spinboxes["filter_sigma"].value(),
            "focus_camera_exposure_time_ms": self.exposure_spinbox.value(),
            "focus_camera_analog_gain": self.analog_gain_spinbox.value(),
            "has_reference": False,
        }
        self.laserAutofocusController.set_laser_af_properties(updates)
        self.laserAutofocusController.initialize_auto()
        self.signal_apply_settings.emit()
        self.update_threshold_button.setEnabled(True)
        self.update_calibration_label()

    def update_threshold_settings(self):
        updates = {
            "laser_af_averaging_n": int(self.spinboxes["laser_af_averaging_n"].value()),
            "displacement_success_window_um": self.spinboxes["displacement_success_window_um"].value(),
            "correlation_threshold": self.spinboxes["correlation_threshold"].value(),
            "laser_af_range": self.spinboxes["laser_af_range"].value(),
        }
        self.laserAutofocusController.update_threshold_properties(updates)

    def update_calibration_label(self):
        # Show calibration result
        # Clear previous calibration label if it exists
        if hasattr(self, "calibration_label"):
            self.calibration_label.deleteLater()

        # Create and add new calibration label
        self.calibration_label = QLabel()
        self.calibration_label.setText(
            f"Calibration Result: {self.laserAutofocusController.laser_af_properties.pixel_to_um:.3f} um/pixel\nPerformed at {self.laserAutofocusController.laser_af_properties.calibration_timestamp}"
        )
        self.layout().addWidget(self.calibration_label)

    def illuminate_and_get_frame(self):
        # Get a frame from the live controller.  We need to reach deep into the liveController here which
        # is not ideal.
        self.liveController.microscope.low_level_drivers.microcontroller.turn_on_AF_laser()
        self.liveController.microscope.low_level_drivers.microcontroller.wait_till_operation_is_completed()
        self.liveController.trigger_acquisition()

        try:
            frame = self.liveController.camera.read_frame()
        finally:
            self.liveController.microscope.low_level_drivers.microcontroller.turn_off_AF_laser()
            self.liveController.microscope.low_level_drivers.microcontroller.wait_till_operation_is_completed()

        return frame

    def clear_labels(self):
        # Remove any existing error or correlation labels
        if hasattr(self, "spot_detection_error_label"):
            self.spot_detection_error_label.deleteLater()
            delattr(self, "spot_detection_error_label")

        if hasattr(self, "correlation_label"):
            self.correlation_label.deleteLater()
            delattr(self, "correlation_label")

    def run_spot_detection(self):
        """Run spot detection with current settings and emit results"""
        params = {
            "y_window": int(self.spinboxes["y_window"].value()),
            "x_window": int(self.spinboxes["x_window"].value()),
            "min_peak_width": self.spinboxes["min_peak_width"].value(),
            "min_peak_distance": self.spinboxes["min_peak_distance"].value(),
            "min_peak_prominence": self.spinboxes["min_peak_prominence"].value(),
            "spot_spacing": self.spinboxes["spot_spacing"].value(),
        }
        mode = self.spot_mode_combo.currentData()
        sigma = self.spinboxes["filter_sigma"].value()

        frame = self.illuminate_and_get_frame()
        if frame is not None:
            try:
                result = utils.find_spot_location(frame, mode=mode, params=params, filter_sigma=sigma, debug_plot=True)
                if result is not None:
                    x, y = result
                    self.signal_laser_spot_location.emit(frame, x, y)
                else:
                    raise Exception("No spot detection result returned")
            except Exception:
                # Show error message
                # Clear previous error label if it exists
                if hasattr(self, "spot_detection_error_label"):
                    self.spot_detection_error_label.deleteLater()

                # Create and add new error label
                self.spot_detection_error_label = QLabel("Spot detection failed!")
                self.layout().addWidget(self.spot_detection_error_label)

    def show_cross_correlation_result(self, value):
        """Show cross-correlation value from validating laser af images"""
        # Clear previous correlation label if it exists
        if hasattr(self, "correlation_label"):
            self.correlation_label.deleteLater()

        # Create and add new correlation label
        self.correlation_label = QLabel()
        self.correlation_label.setText(f"Cross-correlation: {value:.3f}")
        self.layout().addWidget(self.correlation_label)


class SpinningDiskConfocalWidget(QWidget):

    signal_toggle_confocal_widefield = Signal(bool)

    def __init__(self, xlight):
        super(SpinningDiskConfocalWidget, self).__init__()

        self.xlight = xlight

        self.init_ui()

        self.dropdown_emission_filter.setCurrentText(str(self.xlight.get_emission_filter()))
        self.dropdown_dichroic.setCurrentText(str(self.xlight.get_dichroic()))
        if self.xlight.has_dichroic_filter_slider:
            self.filter_slider.setValue(self.xlight.get_filter_slider())

        self.dropdown_emission_filter.currentIndexChanged.connect(self.set_emission_filter)
        self.dropdown_dichroic.currentIndexChanged.connect(self.set_dichroic)

        self.disk_position_state = self.xlight.get_disk_position()

        self.signal_toggle_confocal_widefield.emit(self.disk_position_state)  # signal initial state

        if self.disk_position_state == 1:
            self.btn_toggle_widefield.setText("Switch to Widefield")

        self.btn_toggle_widefield.clicked.connect(self.toggle_disk_position)
        self.btn_toggle_motor.clicked.connect(self.toggle_motor)

        if self.xlight.has_dichroic_filter_slider:
            self.filter_slider.valueChanged.connect(self.set_filter_slider)

        if self.xlight.has_illumination_iris_diaphragm:
            illumination_iris = self.xlight.illumination_iris
            self.slider_illumination_iris.setValue(illumination_iris)
            self.spinbox_illumination_iris.setValue(illumination_iris)

            self.slider_illumination_iris.sliderReleased.connect(lambda: self.update_illumination_iris(True))
            # Update spinbox values during sliding without sending to hardware
            self.slider_illumination_iris.valueChanged.connect(self.spinbox_illumination_iris.setValue)
            self.spinbox_illumination_iris.editingFinished.connect(lambda: self.update_illumination_iris(False))
        if self.xlight.has_emission_iris_diaphragm:
            emission_iris = self.xlight.emission_iris
            self.slider_emission_iris.setValue(emission_iris)
            self.spinbox_emission_iris.setValue(emission_iris)

            self.slider_emission_iris.sliderReleased.connect(lambda: self.update_emission_iris(True))
            # Update spinbox values during sliding without sending to hardware
            self.slider_emission_iris.valueChanged.connect(self.spinbox_emission_iris.setValue)
            self.spinbox_emission_iris.editingFinished.connect(lambda: self.update_emission_iris(False))

    def init_ui(self):

        emissionFilterLayout = QHBoxLayout()
        emissionFilterLayout.addWidget(QLabel("Emission Position"))
        self.dropdown_emission_filter = QComboBox(self)
        self.dropdown_emission_filter.addItems([str(i + 1) for i in range(8)])
        emissionFilterLayout.addWidget(self.dropdown_emission_filter)

        dichroicLayout = QHBoxLayout()
        dichroicLayout.addWidget(QLabel("Dichroic Position"))
        self.dropdown_dichroic = QComboBox(self)
        self.dropdown_dichroic.addItems([str(i + 1) for i in range(5)])
        dichroicLayout.addWidget(self.dropdown_dichroic)

        illuminationIrisLayout = QHBoxLayout()
        illuminationIrisLayout.addWidget(QLabel("Illumination Iris"))
        self.slider_illumination_iris = QSlider(Qt.Horizontal)
        self.slider_illumination_iris.setRange(0, 100)
        self.spinbox_illumination_iris = QSpinBox()
        self.spinbox_illumination_iris.setRange(0, 100)
        self.spinbox_illumination_iris.setKeyboardTracking(False)
        illuminationIrisLayout.addWidget(self.slider_illumination_iris)
        illuminationIrisLayout.addWidget(self.spinbox_illumination_iris)

        emissionIrisLayout = QHBoxLayout()
        emissionIrisLayout.addWidget(QLabel("Emission Iris"))
        self.slider_emission_iris = QSlider(Qt.Horizontal)
        self.slider_emission_iris.setRange(0, 100)
        self.spinbox_emission_iris = QSpinBox()
        self.spinbox_emission_iris.setRange(0, 100)
        self.spinbox_emission_iris.setKeyboardTracking(False)
        emissionIrisLayout.addWidget(self.slider_emission_iris)
        emissionIrisLayout.addWidget(self.spinbox_emission_iris)

        filterSliderLayout = QHBoxLayout()
        filterSliderLayout.addWidget(QLabel("Filter Slider"))
        # self.filter_slider = QComboBox(self)
        # self.filter_slider.addItems(["0", "1", "2", "3"])
        self.filter_slider = QSlider(Qt.Horizontal)
        self.filter_slider.setRange(0, 3)
        self.filter_slider.setTickPosition(QSlider.TicksBelow)
        self.filter_slider.setTickInterval(1)
        filterSliderLayout.addWidget(self.filter_slider)

        self.btn_toggle_widefield = QPushButton("Switch to Confocal")

        self.btn_toggle_motor = QPushButton("Disk Motor On")
        self.btn_toggle_motor.setCheckable(True)

        layout = QGridLayout(self)

        # row 1
        if self.xlight.has_dichroic_filter_slider:
            layout.addLayout(filterSliderLayout, 0, 0, 1, 2)
        layout.addWidget(self.btn_toggle_motor, 0, 2)
        layout.addWidget(self.btn_toggle_widefield, 0, 3)

        # row 2
        if self.xlight.has_dichroic_filters_wheel:
            layout.addWidget(QLabel("Dichroic Filter Wheel"), 1, 0)
            layout.addWidget(self.dropdown_dichroic, 1, 1)
        if self.xlight.has_illumination_iris_diaphragm:
            layout.addLayout(illuminationIrisLayout, 1, 2, 1, 2)

        # row 3
        if self.xlight.has_emission_filters_wheel:
            layout.addWidget(QLabel("Emission Filter Wheel"), 2, 0)
            layout.addWidget(self.dropdown_emission_filter, 2, 1)
        if self.xlight.has_emission_iris_diaphragm:
            layout.addLayout(emissionIrisLayout, 2, 2, 1, 2)

        layout.setColumnStretch(2, 1)
        layout.setColumnStretch(3, 1)
        self.setLayout(layout)

    @Slot(bool)
    def enable_all_buttons(self, enable: bool):
        self.dropdown_emission_filter.setEnabled(enable)
        self.dropdown_dichroic.setEnabled(enable)
        self.btn_toggle_widefield.setEnabled(enable)
        self.btn_toggle_motor.setEnabled(enable)
        self.slider_illumination_iris.setEnabled(enable)
        self.spinbox_illumination_iris.setEnabled(enable)
        self.slider_emission_iris.setEnabled(enable)
        self.spinbox_emission_iris.setEnabled(enable)
        if self.xlight.has_dichroic_filter_slider:
            self.filter_slider.setEnabled(enable)

    def block_iris_control_signals(self, block: bool):
        self.slider_illumination_iris.blockSignals(block)
        self.spinbox_illumination_iris.blockSignals(block)
        self.slider_emission_iris.blockSignals(block)
        self.spinbox_emission_iris.blockSignals(block)

    def toggle_disk_position(self):
        self.enable_all_buttons(False)
        target_position = 0 if self.disk_position_state == 1 else 1

        def on_finished(success, error_msg):
            QMetaObject.invokeMethod(
                self, "_on_disk_position_toggled", Qt.QueuedConnection, Q_ARG(int, target_position)
            )

        utils.threaded_operation_helper(self.xlight.set_disk_position, on_finished, position=target_position)

    @Slot(int)
    def _on_disk_position_toggled(self, position):
        self.disk_position_state = position
        if position == 1:
            self.btn_toggle_widefield.setText("Switch to Widefield")
        else:
            self.btn_toggle_widefield.setText("Switch to Confocal")
        self.enable_all_buttons(True)
        self.signal_toggle_confocal_widefield.emit(self.disk_position_state)

    def toggle_motor(self):
        self.enable_all_buttons(False)
        state = self.btn_toggle_motor.isChecked()

        def on_finished(success, error_msg):
            QMetaObject.invokeMethod(self, "enable_all_buttons", Qt.QueuedConnection, Q_ARG(bool, True))

        utils.threaded_operation_helper(self.xlight.set_disk_motor_state, on_finished, state=state)

    def set_emission_filter(self, index):
        self.enable_all_buttons(False)
        selected_pos = self.dropdown_emission_filter.currentText()
        self.xlight.set_emission_filter(selected_pos)
        self.enable_all_buttons(True)

    def set_dichroic(self, index):
        self.enable_all_buttons(False)
        selected_pos = self.dropdown_dichroic.currentText()
        self.xlight.set_dichroic(selected_pos)
        self.enable_all_buttons(True)

    def update_illumination_iris(self, from_slider: bool):
        self.block_iris_control_signals(True)  # avoid signals triggered by enable/disable buttons
        self.enable_all_buttons(False)
        if from_slider:
            value = self.slider_illumination_iris.value()
        else:
            value = self.spinbox_illumination_iris.value()
            self.slider_illumination_iris.setValue(value)
        self.xlight.set_illumination_iris(value)
        self.enable_all_buttons(True)
        self.block_iris_control_signals(False)

    def update_emission_iris(self, from_slider: bool):
        self.block_iris_control_signals(True)  # avoid signals triggered by enable/disable buttons
        self.enable_all_buttons(False)
        if from_slider:
            value = self.slider_emission_iris.value()
        else:
            value = self.spinbox_emission_iris.value()
            self.slider_emission_iris.setValue(value)
        self.xlight.set_emission_iris(value)
        self.enable_all_buttons(True)
        self.block_iris_control_signals(False)

    def set_filter_slider(self, index):
        self.enable_all_buttons(False)
        position = str(self.filter_slider.value())

        def on_finished(success, error_msg):
            QMetaObject.invokeMethod(self, "enable_all_buttons", Qt.QueuedConnection, Q_ARG(bool, True))

        utils.threaded_operation_helper(self.xlight.set_filter_slider, on_finished, position=position)

    def get_confocal_mode(self) -> bool:
        """Get current confocal mode state.

        Returns:
            True if in confocal mode, False if in widefield mode.
        """
        return bool(self.disk_position_state)


class DragonflyConfocalWidget(QWidget):

    signal_toggle_confocal_widefield = Signal(bool)

    def __init__(self, dragonfly):
        super(DragonflyConfocalWidget, self).__init__()

        self.dragonfly = dragonfly

        self.init_ui()

        # Initialize current states from hardware
        try:
            current_modality = self.dragonfly.get_modality()
            self.confocal_mode = current_modality == "CONFOCAL" if current_modality else False

            current_dichroic = self.dragonfly.get_port_selection_dichroic()
            if current_dichroic is not None:
                self.dropdown_dichroic.setCurrentText(str(current_dichroic))

            current_port1_filter = self.dragonfly.get_emission_filter(1)
            if current_port1_filter is not None:
                self.dropdown_port1_emission_filter.setCurrentText(str(current_port1_filter))

            current_port2_filter = self.dragonfly.get_emission_filter(2)
            if current_port2_filter is not None:
                self.dropdown_port2_emission_filter.setCurrentText(str(current_port2_filter))

            current_field_aperture = self.dragonfly.get_field_aperture_wheel_position()
            if current_field_aperture is not None:
                self.dropdown_field_aperture.setCurrentText(str(current_field_aperture))

            motor_state = self.dragonfly.get_disk_motor_state()
            if motor_state is not None:
                self.btn_disk_motor.setChecked(motor_state)

        except Exception as e:
            print(f"Error initializing widget state: {e}")

        # Set initial button text
        if self.confocal_mode:
            self.btn_toggle_confocal.setText("Switch to Widefield")
        else:
            self.btn_toggle_confocal.setText("Switch to Confocal")

        # Connect signals
        self.btn_toggle_confocal.clicked.connect(self.toggle_confocal_mode)
        self.btn_disk_motor.clicked.connect(self.toggle_disk_motor)
        self.dropdown_dichroic.currentIndexChanged.connect(self.set_dichroic)
        self.dropdown_port1_emission_filter.currentIndexChanged.connect(self.set_port1_emission_filter)
        self.dropdown_port2_emission_filter.currentIndexChanged.connect(self.set_port2_emission_filter)
        self.dropdown_field_aperture.currentIndexChanged.connect(self.set_field_aperture)

        # Emit initial state
        self.signal_toggle_confocal_widefield.emit(self.confocal_mode)

    def init_ui(self):
        main_layout = QVBoxLayout()

        layout_confocal = QHBoxLayout()
        # Row 1: Switch to Confocal button, Disk Motor button, Dichroic dropdown
        self.btn_toggle_confocal = QPushButton("Switch to Confocal")
        self.btn_disk_motor = QPushButton("Disk Motor On")
        self.btn_disk_motor.setCheckable(True)

        dichroic_label = QLabel("Port Selection")
        dichroic_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.dropdown_dichroic = QComboBox(self)
        self.dropdown_dichroic.addItems(self.dragonfly.get_port_selection_dichroic_info())

        layout_confocal.addWidget(self.btn_toggle_confocal)
        layout_confocal.addWidget(self.btn_disk_motor)
        layout_confocal.addWidget(dichroic_label)
        layout_confocal.addWidget(self.dropdown_dichroic)

        layout_wheels = QGridLayout()
        # Row 2: Camera Port 1 Emission Filter and Field Aperture
        port1_emission_label = QLabel("Port 1 Emission Filter")
        self.dropdown_port1_emission_filter = QComboBox(self)
        self.dropdown_port1_emission_filter.addItems(self.dragonfly.get_emission_filter_info(1))

        port1_aperture_label = QLabel("Field Aperture")
        self.dropdown_field_aperture = QComboBox(self)
        self.dropdown_field_aperture.addItems(self.dragonfly.get_field_aperture_info())

        layout_wheels.addWidget(port1_emission_label, 0, 0)
        layout_wheels.addWidget(self.dropdown_port1_emission_filter, 0, 1)
        layout_wheels.addWidget(port1_aperture_label, 0, 2)
        layout_wheels.addWidget(self.dropdown_field_aperture, 0, 3)

        # Row 3: Camera Port 2 Emission Filter and Field Aperture
        port2_emission_label = QLabel("Port 2 Emission Filter")
        self.dropdown_port2_emission_filter = QComboBox(self)
        self.dropdown_port2_emission_filter.addItems(self.dragonfly.get_emission_filter_info(2))

        layout_wheels.addWidget(port2_emission_label, 1, 0)
        layout_wheels.addWidget(self.dropdown_port2_emission_filter, 1, 1)

        main_layout.addLayout(layout_confocal)
        main_layout.addLayout(layout_wheels)

        self.setLayout(main_layout)

    def enable_all_buttons(self, enable: bool):
        """Enable or disable all controls"""
        self.btn_toggle_confocal.setEnabled(enable)
        self.btn_disk_motor.setEnabled(enable)
        self.dropdown_dichroic.setEnabled(enable)
        self.dropdown_port1_emission_filter.setEnabled(enable)
        self.dropdown_port2_emission_filter.setEnabled(enable)
        self.dropdown_field_aperture.setEnabled(enable)

    def toggle_confocal_mode(self):
        """Toggle between confocal and widefield modes"""
        self.enable_all_buttons(False)
        try:
            if self.confocal_mode:
                # Switch to widefield
                self.dragonfly.set_modality("BF")  # or whatever widefield mode string is
                self.confocal_mode = False
                self.btn_toggle_confocal.setText("Switch to Confocal")
            else:
                # Switch to confocal
                self.dragonfly.set_modality("CONFOCAL")
                self.confocal_mode = True
                self.btn_toggle_confocal.setText("Switch to Widefield")

            self.signal_toggle_confocal_widefield.emit(self.confocal_mode)
        except Exception as e:
            print(f"Error toggling confocal mode: {e}")
        finally:
            self.enable_all_buttons(True)

    def toggle_disk_motor(self):
        """Toggle disk motor on/off"""
        self.enable_all_buttons(False)
        try:
            if self.btn_disk_motor.isChecked():
                self.dragonfly.set_disk_motor_state(True)
            else:
                self.dragonfly.set_disk_motor_state(False)
        except Exception as e:
            print(f"Error toggling disk motor: {e}")
        finally:
            self.enable_all_buttons(True)

    def set_dichroic(self, index):
        """Set dichroic position"""
        self.enable_all_buttons(False)
        try:
            selected_pos = self.dropdown_dichroic.currentIndex()
            self.dragonfly.set_port_selection_dichroic(selected_pos + 1)
        except Exception as e:
            print(f"Error setting dichroic: {e}")
        finally:
            self.enable_all_buttons(True)

    def set_port1_emission_filter(self, index):
        """Set port 1 emission filter position"""
        self.enable_all_buttons(False)
        try:
            selected_pos = self.dropdown_port1_emission_filter.currentIndex()
            self.dragonfly.set_emission_filter(1, selected_pos + 1)
        except Exception as e:
            print(f"Error setting port 1 emission filter: {e}")
        finally:
            self.enable_all_buttons(True)

    def set_port2_emission_filter(self, index):
        """Set port 2 emission filter position"""
        self.enable_all_buttons(False)
        try:
            selected_pos = self.dropdown_port2_emission_filter.currentIndex()
            self.dragonfly.set_emission_filter(2, selected_pos + 1)
        except Exception as e:
            print(f"Error setting port 2 emission filter: {e}")
        finally:
            self.enable_all_buttons(True)

    def set_field_aperture(self, index):
        """Set port 1 field aperture position"""
        self.enable_all_buttons(False)
        try:
            selected_pos = self.dropdown_field_aperture.currentIndex()
            self.dragonfly.set_field_aperture_wheel_position(selected_pos + 1)
        except Exception as e:
            print(f"Error setting port 1 field aperture: {e}")
        finally:
            self.enable_all_buttons(True)

    def get_confocal_mode(self) -> bool:
        """Get current confocal mode state.

        Returns:
            True if in confocal mode, False if in widefield mode.
        """
        return self.confocal_mode


class ObjectivesWidget(QWidget):
    signal_objective_changed = Signal()

    def __init__(self, objective_store, objective_changer=None):
        super(ObjectivesWidget, self).__init__()
        self.objectiveStore = objective_store
        self.objective_changer = objective_changer
        self.init_ui()
        self.dropdown.setCurrentText(self.objectiveStore.current_objective)

    def init_ui(self):
        self.dropdown = QComboBox(self)
        self.dropdown.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.dropdown.addItems(self.objectiveStore.objectives_dict.keys())
        self.dropdown.currentTextChanged.connect(self.on_objective_changed)

        layout = QHBoxLayout()
        layout.addWidget(QLabel("Objective Lens"))
        layout.addWidget(self.dropdown)
        self.setLayout(layout)

    def on_objective_changed(self, objective_name):
        self.objectiveStore.set_current_objective(objective_name)
        if USE_XERYON:
            if objective_name in XERYON_OBJECTIVE_SWITCHER_POS_1 and self.objective_changer.currentPosition() != 1:
                self.objective_changer.moveToPosition1()
            elif objective_name in XERYON_OBJECTIVE_SWITCHER_POS_2 and self.objective_changer.currentPosition() != 2:
                self.objective_changer.moveToPosition2()
        self.signal_objective_changed.emit()


class CameraSettingsWidget(QFrame):

    signal_binning_changed = Signal()

    def __init__(
        self,
        camera: AbstractCamera,
        include_gain_exposure_time=False,
        include_camera_temperature_setting=False,
        include_camera_auto_wb_setting=False,
        main=None,
        *args,
        **kwargs,
    ):

        super().__init__(*args, **kwargs)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.camera: AbstractCamera = camera
        self.add_components(
            include_gain_exposure_time, include_camera_temperature_setting, include_camera_auto_wb_setting
        )
        # set frame style
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def add_components(
        self, include_gain_exposure_time, include_camera_temperature_setting, include_camera_auto_wb_setting
    ):

        # add buttons and input fields
        self.entry_exposureTime = QDoubleSpinBox()
        self.entry_exposureTime.setKeyboardTracking(False)
        self.entry_exposureTime.setMinimum(self.camera.get_exposure_limits()[0])
        self.entry_exposureTime.setMaximum(self.camera.get_exposure_limits()[1])
        self.entry_exposureTime.setSingleStep(1)
        self.entry_exposureTime.setValue(20)
        self.camera.set_exposure_time(20)

        self.entry_analogGain = QDoubleSpinBox()
        try:
            gain_range = self.camera.get_gain_range()
            self.entry_analogGain.setMinimum(gain_range.min_gain)
            self.entry_analogGain.setMaximum(gain_range.max_gain)
            self.entry_analogGain.setSingleStep(gain_range.gain_step)
            self.entry_analogGain.setValue(gain_range.min_gain)
            self.camera.set_analog_gain(gain_range.min_gain)
        except NotImplementedError:
            self._log.info("Camera does not support analog gain, disabling analog gain control.")
            self.entry_analogGain.setValue(0)
            self.entry_analogGain.setEnabled(False)

        self.dropdown_pixelFormat = QComboBox()
        try:
            pixel_formats = self.camera.get_available_pixel_formats()
            pixel_formats = [pf.name for pf in pixel_formats]
        except NotImplementedError:
            pixel_formats = ["MONO8", "MONO12", "MONO14", "MONO16", "BAYER_RG8", "BAYER_RG12"]
        self.dropdown_pixelFormat.addItems(pixel_formats)
        if self.camera.get_pixel_format() is not None:
            self.dropdown_pixelFormat.setCurrentText(self.camera.get_pixel_format().name)
        else:
            print("setting camera's default pixel format")
            self.camera.set_pixel_format(CameraPixelFormat.from_string(CAMERA_CONFIG.PIXEL_FORMAT_DEFAULT))
            self.dropdown_pixelFormat.setCurrentText(CAMERA_CONFIG.PIXEL_FORMAT_DEFAULT)
        self.dropdown_pixelFormat.setSizePolicy(QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed))
        # to do: load and save pixel format in configurations

        self.entry_ROI_offset_x = QSpinBox()
        roi_info = self.camera.get_region_of_interest()
        max_x, max_y = self.camera.get_resolution()
        self.entry_ROI_offset_x.setValue(roi_info[0])
        self.entry_ROI_offset_x.setSingleStep(8)
        self.entry_ROI_offset_x.setFixedWidth(60)
        self.entry_ROI_offset_x.setMinimum(0)
        self.entry_ROI_offset_x.setMaximum(max_x)
        self.entry_ROI_offset_x.setKeyboardTracking(False)
        self.entry_ROI_offset_y = QSpinBox()
        self.entry_ROI_offset_y.setValue(roi_info[1])
        self.entry_ROI_offset_y.setSingleStep(8)
        self.entry_ROI_offset_y.setFixedWidth(60)
        self.entry_ROI_offset_y.setMinimum(0)
        self.entry_ROI_offset_y.setMaximum(max_y)
        self.entry_ROI_offset_y.setKeyboardTracking(False)
        self.entry_ROI_width = QSpinBox()
        self.entry_ROI_width.setMinimum(16)
        self.entry_ROI_width.setMaximum(max_x)
        self.entry_ROI_width.setValue(roi_info[2])
        self.entry_ROI_width.setSingleStep(8)
        self.entry_ROI_width.setFixedWidth(60)
        self.entry_ROI_width.setKeyboardTracking(False)
        self.entry_ROI_height = QSpinBox()
        self.entry_ROI_height.setSingleStep(8)
        self.entry_ROI_height.setMinimum(16)
        self.entry_ROI_height.setMaximum(max_y)
        self.entry_ROI_height.setValue(roi_info[3])
        self.entry_ROI_height.setFixedWidth(60)
        self.entry_ROI_height.setKeyboardTracking(False)
        self.entry_temperature = QDoubleSpinBox()
        self.entry_temperature.setKeyboardTracking(False)
        self.entry_temperature.setMaximum(25)
        self.entry_temperature.setMinimum(-50)
        self.entry_temperature.setDecimals(1)
        self.label_temperature_measured = QLabel()
        # self.label_temperature_measured.setNum(0)
        self.label_temperature_measured.setFrameStyle(QFrame.Panel | QFrame.Sunken)

        # connection
        self.entry_exposureTime.valueChanged.connect(self.camera.set_exposure_time)
        self.entry_analogGain.valueChanged.connect(self.set_analog_gain_if_supported)
        self.dropdown_pixelFormat.currentTextChanged.connect(
            lambda s: self.camera.set_pixel_format(CameraPixelFormat.from_string(s))
        )
        self.entry_ROI_offset_x.valueChanged.connect(self.set_ROI_offset)
        self.entry_ROI_offset_y.valueChanged.connect(self.set_ROI_offset)
        self.entry_ROI_height.valueChanged.connect(self.set_Height)
        self.entry_ROI_width.valueChanged.connect(self.set_Width)

        # layout
        self.camera_layout = QVBoxLayout()
        if include_gain_exposure_time:
            exposure_line = QHBoxLayout()
            exposure_line.addWidget(QLabel("Exposure Time (ms)"))
            exposure_line.addWidget(self.entry_exposureTime)
            self.camera_layout.addLayout(exposure_line)
            gain_line = QHBoxLayout()
            gain_line.addWidget(QLabel("Analog Gain"))
            gain_line.addWidget(self.entry_analogGain)
            self.camera_layout.addLayout(gain_line)

        format_line = QHBoxLayout()
        format_line.addWidget(QLabel("Pixel Format"))
        format_line.addWidget(self.dropdown_pixelFormat)
        try:
            current_binning = self.camera.get_binning()
            current_binning_string = "x".join([str(current_binning[0]), str(current_binning[1])])
            binning_options = [f"{binning[0]}x{binning[1]}" for binning in self.camera.get_binning_options()]
            self.dropdown_binning = QComboBox()
            self.dropdown_binning.addItems(binning_options)
            self.dropdown_binning.setCurrentText(current_binning_string)

            self.dropdown_binning.currentTextChanged.connect(self.set_binning)
        except AttributeError as ae:
            print(ae)
            self.dropdown_binning = QComboBox()
            self.dropdown_binning.setEnabled(False)
            pass
        format_line.addWidget(QLabel("Binning"))
        format_line.addWidget(self.dropdown_binning)
        self.camera_layout.addLayout(format_line)

        if include_camera_temperature_setting:
            temp_line = QHBoxLayout()
            temp_line.addWidget(QLabel("Set Temperature (C)"))
            temp_line.addWidget(self.entry_temperature)
            temp_line.addWidget(QLabel("Actual Temperature (C)"))
            temp_line.addWidget(self.label_temperature_measured)
            try:
                self.entry_temperature.valueChanged.connect(self.set_temperature)
                self.camera.set_temperature_reading_callback(self.update_measured_temperature)
            except AttributeError:
                pass
            self.camera_layout.addLayout(temp_line)

        roi_line = QHBoxLayout()
        roi_line.addWidget(QLabel("Height"))
        roi_line.addWidget(self.entry_ROI_height)
        roi_line.addStretch()
        roi_line.addWidget(QLabel("Y-offset"))
        roi_line.addWidget(self.entry_ROI_offset_y)
        roi_line.addStretch()
        roi_line.addWidget(QLabel("Width"))
        roi_line.addWidget(self.entry_ROI_width)
        roi_line.addStretch()
        roi_line.addWidget(QLabel("X-offset"))
        roi_line.addWidget(self.entry_ROI_offset_x)
        self.camera_layout.addLayout(roi_line)

        if DISPLAY_TOUPCAMER_BLACKLEVEL_SETTINGS is True:
            blacklevel_line = QHBoxLayout()
            blacklevel_line.addWidget(QLabel("Black Level"))

            self.label_blackLevel = QSpinBox()
            self.label_blackLevel.setKeyboardTracking(False)
            self.label_blackLevel.setMinimum(0)
            self.label_blackLevel.setMaximum(31)
            self.label_blackLevel.valueChanged.connect(self.update_blacklevel)
            self.label_blackLevel.setSuffix(" ")

            blacklevel_line.addWidget(self.label_blackLevel)

            self.camera_layout.addLayout(blacklevel_line)

        if include_camera_auto_wb_setting and CameraPixelFormat.is_color_format(self.camera.get_pixel_format()):
            # auto white balance
            self.btn_auto_wb = QPushButton("Auto White Balance")
            self.btn_auto_wb.setCheckable(True)
            self.btn_auto_wb.setChecked(False)
            self.btn_auto_wb.clicked.connect(self.toggle_auto_wb)

            self.camera_layout.addWidget(self.btn_auto_wb)

        self.setLayout(self.camera_layout)

    def set_analog_gain_if_supported(self, gain):
        try:
            self.camera.set_analog_gain(gain)
        except NotImplementedError:
            self._log.warning(f"Cannot set gain to {gain}, gain not supported.")

    def toggle_auto_wb(self, pressed):
        # 0: OFF  1:CONTINUOUS  2:ONCE
        if pressed:
            # Run auto white balance once, then uncheck
            self.camera.set_auto_white_balance_gains(on=True)
        else:
            self.camera.set_auto_white_balance_gains(on=False)
            r, g, b = self.camera.get_white_balance_gains()
            self.camera.set_white_balance_gains(r, g, b)

    def set_exposure_time(self, exposure_time):
        self.entry_exposureTime.setValue(exposure_time)

    def set_analog_gain(self, analog_gain):
        self.entry_analogGain.setValue(analog_gain)

    def set_Width(self):
        width = int(self.entry_ROI_width.value() // 8) * 8
        self.entry_ROI_width.blockSignals(True)
        self.entry_ROI_width.setValue(width)
        self.entry_ROI_width.blockSignals(False)
        offset_x = (self.camera.get_resolution()[0] - self.entry_ROI_width.value()) / 2
        offset_x = int(offset_x // 8) * 8
        self.entry_ROI_offset_x.blockSignals(True)
        self.entry_ROI_offset_x.setValue(offset_x)
        self.entry_ROI_offset_x.blockSignals(False)
        self.camera.set_region_of_interest(
            self.entry_ROI_offset_x.value(),
            self.entry_ROI_offset_y.value(),
            self.entry_ROI_width.value(),
            self.entry_ROI_height.value(),
        )

    def set_Height(self):
        height = int(self.entry_ROI_height.value() // 8) * 8
        self.entry_ROI_height.blockSignals(True)
        self.entry_ROI_height.setValue(height)
        self.entry_ROI_height.blockSignals(False)
        offset_y = (self.camera.get_resolution()[1] - self.entry_ROI_height.value()) / 2
        offset_y = int(offset_y // 8) * 8
        self.entry_ROI_offset_y.blockSignals(True)
        self.entry_ROI_offset_y.setValue(offset_y)
        self.entry_ROI_offset_y.blockSignals(False)
        self.camera.set_region_of_interest(
            self.entry_ROI_offset_x.value(),
            self.entry_ROI_offset_y.value(),
            self.entry_ROI_width.value(),
            self.entry_ROI_height.value(),
        )

    def set_ROI_offset(self):
        self.camera.set_region_of_interest(
            self.entry_ROI_offset_x.value(),
            self.entry_ROI_offset_y.value(),
            self.entry_ROI_width.value(),
            self.entry_ROI_height.value(),
        )

    def set_temperature(self):
        try:
            self.camera.set_temperature(float(self.entry_temperature.value()))
        except AttributeError:
            self._log.warning("Cannot set temperature - not supported.")

    def update_measured_temperature(self, temperature):
        self.label_temperature_measured.setNum(temperature)

    def set_binning(self, binning_text):
        binning_parts = binning_text.split("x")
        binning_x = int(binning_parts[0])
        binning_y = int(binning_parts[1])

        self.camera.set_binning(binning_x, binning_y)

        self.entry_ROI_offset_x.blockSignals(True)
        self.entry_ROI_offset_y.blockSignals(True)
        self.entry_ROI_height.blockSignals(True)
        self.entry_ROI_width.blockSignals(True)

        # TODO: move these calculations to camera class as they can be different for different cameras
        def round_to_8(val):
            return int(8 * val // 8)

        x_offset, y_offset, width, height = self.camera.get_region_of_interest()
        x_max, y_max = self.camera.get_resolution()
        self.entry_ROI_height.setMaximum(y_max)
        self.entry_ROI_width.setMaximum(x_max)

        self.entry_ROI_offset_x.setMaximum(x_max)
        self.entry_ROI_offset_y.setMaximum(y_max)

        self.entry_ROI_offset_x.setValue(round_to_8(x_offset))
        self.entry_ROI_offset_y.setValue(round_to_8(y_offset))
        self.entry_ROI_height.setValue(round_to_8(height))
        self.entry_ROI_width.setValue(round_to_8(width))

        self.entry_ROI_offset_x.blockSignals(False)
        self.entry_ROI_offset_y.blockSignals(False)
        self.entry_ROI_height.blockSignals(False)
        self.entry_ROI_width.blockSignals(False)

        self.signal_binning_changed.emit()

    def update_blacklevel(self, blacklevel):
        try:
            self.camera.set_black_level(blacklevel)
        except AttributeError:
            self._log.warning("Cannot set black level - not supported.")


class ProfileWidget(QFrame):

    signal_profile_changed = Signal()

    def __init__(self, config_repo: ConfigRepository, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.config_repo = config_repo

        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        self.setup_ui()

    def setup_ui(self):
        # Create widgets
        self.dropdown_profiles = QComboBox()
        self.dropdown_profiles.addItems(self.config_repo.get_available_profiles())
        if self.config_repo.current_profile:
            self.dropdown_profiles.setCurrentText(self.config_repo.current_profile)
        sizePolicy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.dropdown_profiles.setSizePolicy(sizePolicy)

        self.btn_newProfile = QPushButton("Save As")

        # Connect signals
        self.dropdown_profiles.currentTextChanged.connect(self.load_profile)
        self.btn_newProfile.clicked.connect(self.create_new_profile)

        # Layout
        layout = QHBoxLayout()
        layout.addWidget(QLabel("Configuration Profile"))
        layout.addWidget(self.dropdown_profiles, 2)
        layout.addWidget(self.btn_newProfile)

        self.setLayout(layout)

    def load_profile(self):
        """Load the selected profile."""
        profile_name = self.dropdown_profiles.currentText()
        # Load the profile (ensures defaults and sets as current)
        self.config_repo.load_profile(profile_name)
        self.signal_profile_changed.emit()

    def create_new_profile(self):
        """Create a new profile with current configurations."""
        dialog = QInputDialog()
        profile_name, ok = dialog.getText(self, "New Profile", "Enter new profile name:", QLineEdit.Normal, "")

        if ok and profile_name:
            try:
                current = self.config_repo.current_profile
                if current:
                    self.config_repo.copy_profile(current, profile_name)
                    self.config_repo.set_profile(profile_name)
                else:
                    # No current profile, create empty
                    self.config_repo.create_profile(profile_name)
                    self.config_repo.load_profile(profile_name)
                # Update profile dropdown
                self.dropdown_profiles.addItem(profile_name)
                self.dropdown_profiles.setCurrentText(profile_name)
                # Notify listeners that profile changed
                self.signal_profile_changed.emit()
            except ValueError as e:
                QMessageBox.warning(self, "Error", str(e))

    def get_current_profile(self):
        """Return the currently selected profile name."""
        return self.dropdown_profiles.currentText()


class LiveControlWidget(QFrame):

    signal_newExposureTime = Signal(float)
    signal_newAnalogGain = Signal(float)
    signal_autoLevelSetting = Signal(bool)
    signal_live_configuration = Signal(object)
    signal_start_live = Signal()

    def __init__(
        self,
        streamHandler,
        liveController,
        objectiveStore,
        show_trigger_options=True,
        show_display_options=False,
        show_autolevel=False,
        autolevel=False,
        stretch=True,
        main=None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.liveController: LiveController = liveController
        self.camera = self.liveController.microscope.camera
        self.streamHandler = streamHandler
        self.objectiveStore = objectiveStore
        self.fps_trigger = 10
        self.fps_display = 10
        self.liveController.set_trigger_fps(self.fps_trigger)
        self.streamHandler.set_display_fps(self.fps_display)

        channels = self.liveController.get_channels(self.objectiveStore.current_objective)
        if not channels:
            self._log.error("No channels available - cannot initialize LiveControlWidget")
            self.currentConfiguration = None
        else:
            self.currentConfiguration = channels[0]

        self.add_components(show_trigger_options, show_display_options, show_autolevel, autolevel, stretch)
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        if self.currentConfiguration:
            self.liveController.set_microscope_mode(self.currentConfiguration)
            self.update_ui_for_mode(self.currentConfiguration)

        self.is_switching_mode = False  # flag used to prevent from settings being set by twice - from both mode change slot and value change slot; another way is to use blockSignals(True)

    def add_components(self, show_trigger_options, show_display_options, show_autolevel, autolevel, stretch):
        # line 0: trigger mode
        self.dropdown_triggerManu = QComboBox()
        self.dropdown_triggerManu.addItems([TriggerMode.SOFTWARE, TriggerMode.HARDWARE, TriggerMode.CONTINUOUS])
        self.dropdown_triggerManu.setCurrentText(self.camera.get_acquisition_mode().value)
        sizePolicy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.dropdown_triggerManu.setSizePolicy(sizePolicy)

        # line 1: fps
        self.entry_triggerFPS = QDoubleSpinBox()
        self.entry_triggerFPS.setKeyboardTracking(False)
        self.entry_triggerFPS.setMinimum(0.02)
        self.entry_triggerFPS.setMaximum(1000)
        self.entry_triggerFPS.setSingleStep(1)
        self.entry_triggerFPS.setValue(self.fps_trigger)
        self.entry_triggerFPS.setDecimals(0)

        self.dropdown_modeSelection = QComboBox()
        for microscope_configuration in self.liveController.get_channels(self.objectiveStore.current_objective):
            self.dropdown_modeSelection.addItems([microscope_configuration.name])
        self.dropdown_modeSelection.setCurrentText(self.currentConfiguration.name)
        self.dropdown_modeSelection.setSizePolicy(sizePolicy)

        self.btn_live = QPushButton("Start Live")
        self.btn_live.setCheckable(True)
        self.btn_live.setChecked(False)
        self.btn_live.setDefault(False)
        self.btn_live.setStyleSheet("background-color: #C2C2FF")
        self.btn_live.setSizePolicy(sizePolicy)

        # line 3: exposure time and analog gain associated with the current mode
        self.entry_exposureTime = QDoubleSpinBox()
        self.entry_exposureTime.setKeyboardTracking(False)
        self.entry_exposureTime.setMinimum(self.camera.get_exposure_limits()[0])
        self.entry_exposureTime.setMaximum(self.camera.get_exposure_limits()[1])
        self.entry_exposureTime.setSingleStep(1)
        self.entry_exposureTime.setSuffix(" ms")
        self.entry_exposureTime.setValue(0)
        self.entry_exposureTime.setSizePolicy(sizePolicy)

        self.entry_analogGain = QDoubleSpinBox()
        self.entry_analogGain.setKeyboardTracking(False)
        # Not all cameras support analog gain, so attempt to get the gain
        # to check this
        try:
            gain_range = self.camera.get_gain_range()
            self.entry_analogGain.setMinimum(gain_range.min_gain)
            self.entry_analogGain.setMaximum(gain_range.max_gain)
            self.entry_analogGain.setSingleStep(gain_range.gain_step)
            self.entry_analogGain.setValue(gain_range.min_gain)
            self.entry_analogGain.setSizePolicy(sizePolicy)
            self.camera.set_analog_gain(gain_range.min_gain)
        except NotImplementedError:
            self._log.info("Analog gain not supported,  disabling it in live control widget.")
            self.entry_analogGain.setValue(0)
            self.entry_analogGain.setEnabled(False)

        self.slider_illuminationIntensity = QSlider(Qt.Horizontal)
        self.slider_illuminationIntensity.setTickPosition(QSlider.TicksBelow)
        self.slider_illuminationIntensity.setMinimum(0)
        self.slider_illuminationIntensity.setMaximum(100)
        self.slider_illuminationIntensity.setValue(100)
        self.slider_illuminationIntensity.setSingleStep(2)

        self.entry_illuminationIntensity = QDoubleSpinBox()
        self.entry_illuminationIntensity.setKeyboardTracking(False)
        self.entry_illuminationIntensity.setMinimum(0)
        self.entry_illuminationIntensity.setMaximum(100)
        self.entry_illuminationIntensity.setSingleStep(1)
        self.entry_illuminationIntensity.setSuffix("%")
        self.entry_illuminationIntensity.setValue(100)

        # line 4: display fps and resolution scaling
        self.entry_displayFPS = QDoubleSpinBox()
        self.entry_displayFPS.setKeyboardTracking(False)
        self.entry_displayFPS.setMinimum(1)
        self.entry_displayFPS.setMaximum(240)
        self.entry_displayFPS.setSingleStep(1)
        self.entry_displayFPS.setDecimals(0)
        self.entry_displayFPS.setValue(self.fps_display)

        self.slider_resolutionScaling = QSlider(Qt.Horizontal)
        self.slider_resolutionScaling.setTickPosition(QSlider.TicksBelow)
        self.slider_resolutionScaling.setMinimum(10)
        self.slider_resolutionScaling.setMaximum(100)
        self.slider_resolutionScaling.setValue(100)
        self.slider_resolutionScaling.setSingleStep(10)

        self.label_resolutionScaling = QSpinBox()
        self.label_resolutionScaling.setKeyboardTracking(False)
        self.label_resolutionScaling.setMinimum(10)
        self.label_resolutionScaling.setMaximum(100)
        self.label_resolutionScaling.setValue(self.slider_resolutionScaling.value())
        self.label_resolutionScaling.setSuffix(" %")
        self.slider_resolutionScaling.setSingleStep(5)

        self.slider_resolutionScaling.valueChanged.connect(lambda v: self.label_resolutionScaling.setValue(round(v)))
        self.label_resolutionScaling.valueChanged.connect(lambda v: self.slider_resolutionScaling.setValue(round(v)))

        # autolevel
        self.btn_autolevel = QPushButton("Autolevel")
        self.btn_autolevel.setCheckable(True)
        self.btn_autolevel.setChecked(autolevel)

        # Determine the maximum width needed
        self.entry_illuminationIntensity.setMinimumWidth(self.btn_live.sizeHint().width())
        self.btn_autolevel.setMinimumWidth(self.btn_autolevel.sizeHint().width())

        max_width = max(self.btn_autolevel.minimumWidth(), self.entry_illuminationIntensity.minimumWidth())

        # Set the fixed width for all three widgets
        self.entry_illuminationIntensity.setFixedWidth(max_width)
        self.btn_autolevel.setFixedWidth(max_width)

        # connections
        self.entry_triggerFPS.valueChanged.connect(self.liveController.set_trigger_fps)
        self.entry_displayFPS.valueChanged.connect(self.streamHandler.set_display_fps)
        self.slider_resolutionScaling.valueChanged.connect(self.streamHandler.set_display_resolution_scaling)
        self.slider_resolutionScaling.valueChanged.connect(self.liveController.set_display_resolution_scaling)
        self.dropdown_modeSelection.activated[str].connect(self.select_new_microscope_mode_by_name)
        self.dropdown_triggerManu.currentIndexChanged.connect(self.update_trigger_mode)
        self.btn_live.clicked.connect(self.toggle_live)
        self.entry_exposureTime.valueChanged.connect(self.update_config_exposure_time)
        self.entry_analogGain.valueChanged.connect(self.update_config_analog_gain)
        self.entry_illuminationIntensity.valueChanged.connect(self.update_config_illumination_intensity)
        self.entry_illuminationIntensity.valueChanged.connect(
            lambda x: self.slider_illuminationIntensity.setValue(int(x))
        )
        self.slider_illuminationIntensity.valueChanged.connect(self.entry_illuminationIntensity.setValue)
        self.btn_autolevel.toggled.connect(self.signal_autoLevelSetting.emit)

        # layout
        grid_line1 = QHBoxLayout()
        grid_line1.addWidget(QLabel("Live Configuration"))
        grid_line1.addWidget(self.dropdown_modeSelection, 2)
        grid_line1.addWidget(self.btn_live, 1)

        grid_line2 = QHBoxLayout()
        grid_line2.addWidget(QLabel("Exposure Time"))
        grid_line2.addWidget(self.entry_exposureTime)
        gain_label = QLabel(" Analog Gain")
        gain_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        grid_line2.addWidget(gain_label)
        grid_line2.addWidget(self.entry_analogGain)
        if show_autolevel:
            grid_line2.addWidget(self.btn_autolevel)

        grid_line4 = QHBoxLayout()
        grid_line4.addWidget(QLabel("Illumination"))
        grid_line4.addWidget(self.slider_illuminationIntensity)
        grid_line4.addWidget(self.entry_illuminationIntensity)

        grid_line0 = QHBoxLayout()
        if show_trigger_options:
            grid_line0.addWidget(QLabel("Trigger Mode"))
            grid_line0.addWidget(self.dropdown_triggerManu)
            grid_line0.addWidget(QLabel("Trigger FPS"))
            grid_line0.addWidget(self.entry_triggerFPS)

        grid_line05 = QHBoxLayout()
        show_dislpay_fps = False
        if show_display_options:
            resolution_label = QLabel("Display Resolution")
            resolution_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid_line05.addWidget(resolution_label)
            grid_line05.addWidget(self.slider_resolutionScaling)
            if show_dislpay_fps:
                grid_line05.addWidget(QLabel("Display FPS"))
                grid_line05.addWidget(self.entry_displayFPS)
            else:
                grid_line05.addWidget(self.label_resolutionScaling)

        self.grid = QVBoxLayout()
        if show_trigger_options:
            self.grid.addLayout(grid_line0)
        self.grid.addLayout(grid_line1)
        self.grid.addLayout(grid_line2)
        self.grid.addLayout(grid_line4)
        if show_display_options:
            self.grid.addLayout(grid_line05)
        if not stretch:
            self.grid.addStretch()
        self.setLayout(self.grid)

    def toggle_live(self, pressed):
        if pressed:
            self.liveController.start_live()
            self.btn_live.setText("Stop Live")
            self.signal_start_live.emit()
        else:
            self.liveController.stop_live()
            self.btn_live.setText("Start Live")

    def toggle_autolevel(self, autolevel_on):
        self.btn_autolevel.setChecked(autolevel_on)

    def update_camera_settings(self):
        self.signal_newAnalogGain.emit(self.entry_analogGain.value())
        self.signal_newExposureTime.emit(self.entry_exposureTime.value())

    def refresh_mode_list(self):
        # Update the mode selection dropdown (only show enabled channels)
        self.dropdown_modeSelection.blockSignals(True)
        self.dropdown_modeSelection.clear()
        first_config = None
        for microscope_configuration in self.liveController.get_channels(self.objectiveStore.current_objective):
            if not first_config:
                first_config = microscope_configuration
            self.dropdown_modeSelection.addItem(microscope_configuration.name)
        self.dropdown_modeSelection.blockSignals(False)

        # Update to first configuration
        if self.dropdown_modeSelection.count() > 0:
            self.update_ui_for_mode(first_config)
            self.liveController.set_microscope_mode(first_config)

    def select_new_microscope_mode_by_name(self, config_name):
        maybe_new_config = self.liveController.get_channel_by_name(self.objectiveStore.current_objective, config_name)

        if not maybe_new_config:
            self._log.error(f"User attempted to select config named '{config_name}' but it does not exist!")
            return

        self.liveController.set_microscope_mode(maybe_new_config)
        self.update_ui_for_mode(maybe_new_config)

    def update_ui_for_mode(self, config):
        try:
            self.is_switching_mode = True
            self.currentConfiguration = config
            self.dropdown_modeSelection.setCurrentText(config.name if config else "Unknown")
            if self.currentConfiguration:
                self.signal_live_configuration.emit(self.currentConfiguration)

                # update the exposure time and analog gain settings according to the selected configuration
                self.entry_exposureTime.setValue(self.currentConfiguration.exposure_time)
                self.entry_analogGain.setValue(self.currentConfiguration.analog_gain)
                self.entry_illuminationIntensity.setValue(self.currentConfiguration.illumination_intensity)
        finally:
            self.is_switching_mode = False

    def update_trigger_mode(self):
        self.liveController.set_trigger_mode(self.dropdown_triggerManu.currentText())

    def update_config_exposure_time(self, new_value):
        if self.is_switching_mode == False:
            self.currentConfiguration.exposure_time = new_value
            self.liveController.microscope.config_repo.update_channel_setting(
                self.objectiveStore.current_objective, self.currentConfiguration.name, "ExposureTime", new_value
            )
            self.signal_newExposureTime.emit(new_value)

    def update_config_analog_gain(self, new_value):
        if self.is_switching_mode == False:
            self.currentConfiguration.analog_gain = new_value
            self.liveController.microscope.config_repo.update_channel_setting(
                self.objectiveStore.current_objective, self.currentConfiguration.name, "AnalogGain", new_value
            )
            self.signal_newAnalogGain.emit(new_value)

    def update_config_illumination_intensity(self, new_value):
        if self.is_switching_mode == False:
            self.currentConfiguration.illumination_intensity = new_value
            self.liveController.microscope.config_repo.update_channel_setting(
                self.objectiveStore.current_objective,
                self.currentConfiguration.name,
                "IlluminationIntensity",
                new_value,
            )
            self.liveController.update_illumination()

    def set_trigger_mode(self, trigger_mode):
        self.dropdown_triggerManu.setCurrentText(trigger_mode)
        self.liveController.set_trigger_mode(self.dropdown_triggerManu.currentText())


class PiezoWidget(QFrame):
    def __init__(self, piezo: PiezoStage, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.piezo = piezo
        self.piezo_displacement_um = 0.00
        self.add_components()

    def add_components(self):
        # Row 1: Slider and Double Spin Box for direct control
        self.slider = QSlider(Qt.Horizontal, self)
        self.slider.setMinimum(0)
        self.slider.setMaximum(int(self.piezo.range_um * 100))  # Multiplied by 100 for 0.01 precision
        self.slider.setValue(int(self.piezo._home_position_um * 100))

        self.spinBox = QDoubleSpinBox(self)
        self.spinBox.setRange(0.0, self.piezo.range_um)
        self.spinBox.setDecimals(2)
        self.spinBox.setSingleStep(1)
        self.spinBox.setSuffix(" μm")
        self.spinBox.setKeyboardTracking(False)
        self.spinBox.setValue(self.piezo._home_position_um)

        # Row 3: Home Button
        self.home_btn = QPushButton(f" Set to {self.piezo._home_position_um} μm ", self)

        hbox1 = QHBoxLayout()
        hbox1.addWidget(self.home_btn)
        hbox1.addWidget(self.slider)
        hbox1.addWidget(self.spinBox)

        # Row 2: Increment Double Spin Box, Move Up and Move Down Buttons
        self.increment_spinBox = QDoubleSpinBox(self)
        self.increment_spinBox.setKeyboardTracking(False)
        self.increment_spinBox.setRange(0.0, 100.0)
        self.increment_spinBox.setDecimals(2)
        self.increment_spinBox.setSingleStep(1)
        self.increment_spinBox.setValue(1.00)
        self.increment_spinBox.setSuffix(" μm")
        self.move_up_btn = QPushButton("Move Up", self)
        self.move_down_btn = QPushButton("Move Down", self)

        hbox2 = QHBoxLayout()
        hbox2.addWidget(self.increment_spinBox)
        hbox2.addWidget(self.move_up_btn)
        hbox2.addWidget(self.move_down_btn)

        # Vertical Layout to include all HBoxes
        vbox = QVBoxLayout()
        vbox.addLayout(hbox1)
        vbox.addLayout(hbox2)

        self.setLayout(vbox)

        # Connect signals and slots
        self.slider.valueChanged.connect(self.update_from_slider)
        self.spinBox.valueChanged.connect(self.update_from_spinBox)
        self.move_up_btn.clicked.connect(lambda: self.adjust_position(True))
        self.move_down_btn.clicked.connect(lambda: self.adjust_position(False))
        self.home_btn.clicked.connect(self.home)

    def update_from_slider(self, value):
        self.piezo_displacement_um = value / 100  # Convert back to float with two decimal places
        self.update_spinBox()
        self.update_piezo_position()

    def update_from_spinBox(self, value):
        self.piezo_displacement_um = value
        self.update_slider()
        self.update_piezo_position()

    def update_spinBox(self):
        self.spinBox.blockSignals(True)
        self.spinBox.setValue(self.piezo_displacement_um)
        self.spinBox.blockSignals(False)

    def update_slider(self):
        self.slider.blockSignals(True)
        self.slider.setValue(int(self.piezo_displacement_um * 100))
        self.slider.blockSignals(False)

    def update_piezo_position(self):
        self.piezo.move_to(self.piezo_displacement_um)

    def adjust_position(self, up):
        increment = self.increment_spinBox.value()
        if up:
            self.piezo_displacement_um = min(self.piezo.range_um, self.spinBox.value() + increment)
        else:
            self.piezo_displacement_um = max(0, self.spinBox.value() - increment)
        self.update_spinBox()
        self.update_slider()
        self.update_piezo_position()

    def home(self):
        self.piezo.home()
        self.piezo_displacement_um = self.piezo._home_position_um
        self.update_spinBox()
        self.update_slider()

    def update_displacement_um_display(self, displacement=None):
        if displacement is None:
            displacement = self.piezo.position
        self.piezo_displacement_um = round(displacement, 2)
        self.update_spinBox()
        self.update_slider()


class RecordingWidget(QFrame):
    def __init__(self, streamHandler, imageSaver, main=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.imageSaver = imageSaver  # for saving path control
        self.streamHandler = streamHandler
        self.base_path_is_set = False
        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def add_components(self):
        self.btn_setSavingDir = QPushButton("Browse")
        self.btn_setSavingDir.setDefault(False)
        self.btn_setSavingDir.setIcon(QIcon("icon/folder.png"))

        self.lineEdit_savingDir = QLineEdit()
        self.lineEdit_savingDir.setReadOnly(True)
        self.lineEdit_savingDir.setText("Choose a base saving directory")

        self.lineEdit_savingDir.setText(DEFAULT_SAVING_PATH)
        self.imageSaver.set_base_path(DEFAULT_SAVING_PATH)

        self.lineEdit_experimentID = QLineEdit()

        self.entry_saveFPS = QDoubleSpinBox()
        self.entry_saveFPS.setKeyboardTracking(False)
        self.entry_saveFPS.setMinimum(0.02)
        self.entry_saveFPS.setMaximum(1000)
        self.entry_saveFPS.setSingleStep(1)
        self.entry_saveFPS.setValue(1)
        self.streamHandler.set_save_fps(1)

        self.entry_timeLimit = QSpinBox()
        self.entry_timeLimit.setKeyboardTracking(False)
        self.entry_timeLimit.setMinimum(-1)
        self.entry_timeLimit.setMaximum(60 * 60 * 24 * 30)
        self.entry_timeLimit.setSingleStep(1)
        self.entry_timeLimit.setValue(-1)

        self.btn_record = QPushButton("Record")
        self.btn_record.setCheckable(True)
        self.btn_record.setChecked(False)
        self.btn_record.setDefault(False)

        grid_line1 = QGridLayout()
        grid_line1.addWidget(QLabel("Saving Path"))
        grid_line1.addWidget(self.lineEdit_savingDir, 0, 1)
        grid_line1.addWidget(self.btn_setSavingDir, 0, 2)

        grid_line2 = QGridLayout()
        grid_line2.addWidget(QLabel("Experiment ID"), 0, 0)
        grid_line2.addWidget(self.lineEdit_experimentID, 0, 1)

        grid_line3 = QGridLayout()
        grid_line3.addWidget(QLabel("Saving FPS"), 0, 0)
        grid_line3.addWidget(self.entry_saveFPS, 0, 1)
        grid_line3.addWidget(QLabel("Time Limit (s)"), 0, 2)
        grid_line3.addWidget(self.entry_timeLimit, 0, 3)

        self.grid = QVBoxLayout()
        self.grid.addLayout(grid_line1)
        self.grid.addLayout(grid_line2)
        self.grid.addLayout(grid_line3)
        self.grid.addWidget(self.btn_record)
        self.setLayout(self.grid)

        # add and display a timer - to be implemented
        # self.timer = QTimer()

        # connections
        self.btn_setSavingDir.clicked.connect(self.set_saving_dir)
        self.btn_record.clicked.connect(self.toggle_recording)
        self.entry_saveFPS.valueChanged.connect(self.streamHandler.set_save_fps)
        self.entry_timeLimit.valueChanged.connect(self.imageSaver.set_recording_time_limit)
        self.imageSaver.stop_recording.connect(self.stop_recording)

    def set_saving_dir(self):
        dialog = QFileDialog()
        save_dir_base = dialog.getExistingDirectory(None, "Select Folder")
        self.imageSaver.set_base_path(save_dir_base)
        self.lineEdit_savingDir.setText(save_dir_base)
        self.base_path_is_set = True

    def toggle_recording(self, pressed):
        if self.base_path_is_set == False:
            self.btn_record.setChecked(False)
            msg = QMessageBox()
            msg.setText("Please choose base saving directory first")
            msg.exec_()
            return
        if pressed:
            self.lineEdit_experimentID.setEnabled(False)
            self.btn_setSavingDir.setEnabled(False)
            self.imageSaver.start_new_experiment(self.lineEdit_experimentID.text())
            self.streamHandler.start_recording()
        else:
            self.streamHandler.stop_recording()
            self.lineEdit_experimentID.setEnabled(True)
            self.btn_setSavingDir.setEnabled(True)

    # stop_recording can be called by imageSaver
    def stop_recording(self):
        self.lineEdit_experimentID.setEnabled(True)
        self.btn_record.setChecked(False)
        self.streamHandler.stop_recording()
        self.btn_setSavingDir.setEnabled(True)


class NavigationWidget(QFrame):
    def __init__(
        self,
        stage: AbstractStage,
        main=None,
        widget_configuration="full",
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.log = squid.logging.get_logger(self.__class__.__name__)
        self.stage = stage
        self.widget_configuration = widget_configuration
        self.slide_position = None
        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

        self.position_update_timer = QTimer()
        self.position_update_timer.setInterval(100)
        self.position_update_timer.timeout.connect(self._update_position)
        self.position_update_timer.start()

    def _update_position(self):
        pos = self.stage.get_pos()
        self.label_Xpos.setNum(pos.x_mm)
        self.label_Ypos.setNum(pos.y_mm)
        # NOTE: The z label is in um
        self.label_Zpos.setNum(pos.z_mm * 1000)

    def add_components(self):
        x_label = QLabel("X :")
        x_label.setFixedWidth(20)
        self.label_Xpos = QLabel()
        self.label_Xpos.setNum(0)
        self.label_Xpos.setFrameStyle(QFrame.Panel | QFrame.Sunken)
        self.entry_dX = QDoubleSpinBox()
        self.entry_dX.setMinimum(0)
        self.entry_dX.setMaximum(25)
        self.entry_dX.setSingleStep(0.2)
        self.entry_dX.setValue(0)
        self.entry_dX.setDecimals(3)
        self.entry_dX.setSuffix(" mm")
        self.entry_dX.setKeyboardTracking(False)
        self.btn_moveX_forward = QPushButton("Forward")
        self.btn_moveX_forward.setDefault(False)
        self.btn_moveX_backward = QPushButton("Backward")
        self.btn_moveX_backward.setDefault(False)

        self.checkbox_clickToMove = QCheckBox("Click to Move")
        self.checkbox_clickToMove.setChecked(False)
        self.checkbox_clickToMove.setSizePolicy(QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed))

        y_label = QLabel("Y :")
        y_label.setFixedWidth(20)
        self.label_Ypos = QLabel()
        self.label_Ypos.setNum(0)
        self.label_Ypos.setFrameStyle(QFrame.Panel | QFrame.Sunken)
        self.entry_dY = QDoubleSpinBox()
        self.entry_dY.setMinimum(0)
        self.entry_dY.setMaximum(25)
        self.entry_dY.setSingleStep(0.2)
        self.entry_dY.setValue(0)
        self.entry_dY.setDecimals(3)
        self.entry_dY.setSuffix(" mm")

        self.entry_dY.setKeyboardTracking(False)
        self.btn_moveY_forward = QPushButton("Forward")
        self.btn_moveY_forward.setDefault(False)
        self.btn_moveY_backward = QPushButton("Backward")
        self.btn_moveY_backward.setDefault(False)

        self.z_label = QLabel("Z :")
        self.z_label.setFixedWidth(20)
        self.label_Zpos = QLabel()
        self.label_Zpos.setNum(0)
        self.label_Zpos.setFrameStyle(QFrame.Panel | QFrame.Sunken)
        self.entry_dZ = QDoubleSpinBox()
        self.entry_dZ.setMinimum(0)
        self.entry_dZ.setMaximum(1000)
        self.entry_dZ.setSingleStep(0.2)
        self.entry_dZ.setValue(0)
        self.entry_dZ.setDecimals(3)
        self.entry_dZ.setSuffix(" μm")
        self.entry_dZ.setKeyboardTracking(False)
        self.btn_moveZ_forward = QPushButton("Forward")
        self.btn_moveZ_forward.setDefault(False)
        self.btn_moveZ_backward = QPushButton("Backward")
        self.btn_moveZ_backward.setDefault(False)

        grid_line0 = QGridLayout()
        grid_line0.addWidget(x_label, 0, 0)
        grid_line0.addWidget(self.label_Xpos, 0, 1)
        grid_line0.addWidget(self.entry_dX, 0, 2)
        grid_line0.addWidget(self.btn_moveX_forward, 0, 3)
        grid_line0.addWidget(self.btn_moveX_backward, 0, 4)

        grid_line0.addWidget(y_label, 1, 0)
        grid_line0.addWidget(self.label_Ypos, 1, 1)
        grid_line0.addWidget(self.entry_dY, 1, 2)
        grid_line0.addWidget(self.btn_moveY_forward, 1, 3)
        grid_line0.addWidget(self.btn_moveY_backward, 1, 4)

        grid_line0.addWidget(self.z_label, 2, 0)
        grid_line0.addWidget(self.label_Zpos, 2, 1)
        grid_line0.addWidget(self.entry_dZ, 2, 2)
        grid_line0.addWidget(self.btn_moveZ_forward, 2, 3)
        grid_line0.addWidget(self.btn_moveZ_backward, 2, 4)

        # Hide Z controls in piezo-only mode (Z is controlled via piezo widget)
        if IS_PIEZO_ONLY:
            self.z_label.setVisible(False)
            self.label_Zpos.setVisible(False)
            self.entry_dZ.setVisible(False)
            self.btn_moveZ_forward.setVisible(False)
            self.btn_moveZ_backward.setVisible(False)

        self.grid = QVBoxLayout()
        self.grid.addLayout(grid_line0)
        self.set_click_to_move(ENABLE_CLICK_TO_MOVE_BY_DEFAULT)
        if not ENABLE_CLICK_TO_MOVE_BY_DEFAULT:
            grid_line3 = QHBoxLayout()
            grid_line3.addWidget(self.checkbox_clickToMove, 1)
            self.grid.addLayout(grid_line3)
        self.setLayout(self.grid)

        self.entry_dX.valueChanged.connect(self.set_deltaX)
        self.entry_dY.valueChanged.connect(self.set_deltaY)
        self.entry_dZ.valueChanged.connect(self.set_deltaZ)

        self.btn_moveX_forward.clicked.connect(self.move_x_forward)
        self.btn_moveX_backward.clicked.connect(self.move_x_backward)
        self.btn_moveY_forward.clicked.connect(self.move_y_forward)
        self.btn_moveY_backward.clicked.connect(self.move_y_backward)
        self.btn_moveZ_forward.clicked.connect(self.move_z_forward)
        self.btn_moveZ_backward.clicked.connect(self.move_z_backward)

    def set_click_to_move(self, enabled):
        self.log.info(f"Click to move enabled={enabled}")
        self.setEnabled_all(enabled)
        self.checkbox_clickToMove.setChecked(enabled)

    def get_click_to_move_enabled(self):
        return self.checkbox_clickToMove.isChecked()

    def setEnabled_all(self, enabled):
        self.checkbox_clickToMove.setEnabled(enabled)
        self.btn_moveX_forward.setEnabled(enabled)
        self.btn_moveX_backward.setEnabled(enabled)
        self.btn_moveY_forward.setEnabled(enabled)
        self.btn_moveY_backward.setEnabled(enabled)
        self.btn_moveZ_forward.setEnabled(enabled)
        self.btn_moveZ_backward.setEnabled(enabled)

    def move_x_forward(self):
        self.stage.move_x(self.entry_dX.value())

    def move_x_backward(self):
        self.stage.move_x(-self.entry_dX.value())

    def move_y_forward(self):
        self.stage.move_y(self.entry_dY.value())

    def move_y_backward(self):
        self.stage.move_y(-self.entry_dY.value())

    def move_z_forward(self):
        self.stage.move_z(self.entry_dZ.value() / 1000)

    def move_z_backward(self):
        self.stage.move_z(-self.entry_dZ.value() / 1000)

    def set_deltaX(self, value):
        mm_per_ustep = 1.0 / self.stage.x_mm_to_usteps(1.0)
        deltaX = round(value / mm_per_ustep) * mm_per_ustep
        self.entry_dX.setValue(deltaX)

    def set_deltaY(self, value):
        mm_per_ustep = 1.0 / self.stage.y_mm_to_usteps(1.0)
        deltaY = round(value / mm_per_ustep) * mm_per_ustep
        self.entry_dY.setValue(deltaY)

    def set_deltaZ(self, value):
        mm_per_ustep = 1.0 / self.stage.z_mm_to_usteps(1.0)
        deltaZ = round(value / 1000 / mm_per_ustep) * mm_per_ustep * 1000
        self.entry_dZ.setValue(deltaZ)


class DACControWidget(QFrame):
    def __init__(self, microcontroller, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.microcontroller = microcontroller
        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def add_components(self):
        self.slider_DAC0 = QSlider(Qt.Horizontal)
        self.slider_DAC0.setTickPosition(QSlider.TicksBelow)
        self.slider_DAC0.setMinimum(0)
        self.slider_DAC0.setMaximum(100)
        self.slider_DAC0.setSingleStep(1)
        self.slider_DAC0.setValue(0)

        self.entry_DAC0 = QDoubleSpinBox()
        self.entry_DAC0.setMinimum(0)
        self.entry_DAC0.setMaximum(100)
        self.entry_DAC0.setSingleStep(0.1)
        self.entry_DAC0.setValue(0)
        self.entry_DAC0.setKeyboardTracking(False)

        self.slider_DAC1 = QSlider(Qt.Horizontal)
        self.slider_DAC1.setTickPosition(QSlider.TicksBelow)
        self.slider_DAC1.setMinimum(0)
        self.slider_DAC1.setMaximum(100)
        self.slider_DAC1.setValue(0)
        self.slider_DAC1.setSingleStep(1)

        self.entry_DAC1 = QDoubleSpinBox()
        self.entry_DAC1.setMinimum(0)
        self.entry_DAC1.setMaximum(100)
        self.entry_DAC1.setSingleStep(0.1)
        self.entry_DAC1.setValue(0)
        self.entry_DAC1.setKeyboardTracking(False)

        # connections
        self.entry_DAC0.valueChanged.connect(self.set_DAC0)
        self.entry_DAC0.valueChanged.connect(self.slider_DAC0.setValue)
        self.slider_DAC0.valueChanged.connect(self.entry_DAC0.setValue)
        self.entry_DAC1.valueChanged.connect(self.set_DAC1)
        self.entry_DAC1.valueChanged.connect(self.slider_DAC1.setValue)
        self.slider_DAC1.valueChanged.connect(self.entry_DAC1.setValue)

        # layout
        grid_line1 = QHBoxLayout()
        grid_line1.addWidget(QLabel("DAC0"))
        grid_line1.addWidget(self.slider_DAC0)
        grid_line1.addWidget(self.entry_DAC0)
        grid_line1.addWidget(QLabel("DAC1"))
        grid_line1.addWidget(self.slider_DAC1)
        grid_line1.addWidget(self.entry_DAC1)

        self.grid = QGridLayout()
        self.grid.addLayout(grid_line1, 1, 0)
        self.setLayout(self.grid)

    def set_DAC0(self, value):
        self.microcontroller.analog_write_onboard_DAC(0, round(value * 65535 / 100))

    def set_DAC1(self, value):
        self.microcontroller.analog_write_onboard_DAC(1, round(value * 65535 / 100))


class AutoFocusWidget(QFrame):
    signal_autoLevelSetting = Signal(bool)

    def __init__(self, autofocusController, main=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.autofocusController = autofocusController
        self.log = squid.logging.get_logger(self.__class__.__name__)
        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        self.stage = self.autofocusController.stage

    def add_components(self):
        self.entry_delta = QDoubleSpinBox()
        self.entry_delta.setMinimum(0)
        self.entry_delta.setMaximum(20)
        self.entry_delta.setSingleStep(0.2)
        self.entry_delta.setDecimals(3)
        self.entry_delta.setSuffix(" μm")
        self.entry_delta.setValue(1.524)
        self.entry_delta.setKeyboardTracking(False)
        self.entry_delta.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.autofocusController.set_deltaZ(1.524)

        self.entry_N = QSpinBox()
        self.entry_N.setMinimum(3)
        self.entry_N.setMaximum(10000)
        self.entry_N.setFixedWidth(self.entry_N.sizeHint().width())
        self.entry_N.setMaximum(20)
        self.entry_N.setSingleStep(1)
        self.entry_N.setValue(10)
        self.entry_N.setKeyboardTracking(False)
        self.entry_N.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.autofocusController.set_N(10)

        self.btn_autofocus = QPushButton("Autofocus")
        self.btn_autofocus.setDefault(False)
        self.btn_autofocus.setCheckable(True)
        self.btn_autofocus.setChecked(False)

        self.btn_autolevel = QPushButton("Autolevel")
        self.btn_autolevel.setCheckable(True)
        self.btn_autolevel.setChecked(False)
        self.btn_autolevel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # layout
        self.grid = QVBoxLayout()
        grid_line0 = QHBoxLayout()
        grid_line0.addWidget(QLabel("\u0394 Z"))
        grid_line0.addWidget(self.entry_delta)
        grid_line0.addSpacing(20)
        grid_line0.addWidget(QLabel("# of Z-Planes"))
        grid_line0.addWidget(self.entry_N)
        grid_line0.addSpacing(20)
        grid_line0.addWidget(self.btn_autolevel)

        self.grid.addLayout(grid_line0)
        self.grid.addWidget(self.btn_autofocus)
        self.setLayout(self.grid)

        # connections
        self.btn_autofocus.toggled.connect(lambda: self.autofocusController.autofocus(False))
        self.btn_autolevel.toggled.connect(self.signal_autoLevelSetting.emit)
        self.entry_delta.valueChanged.connect(self.set_deltaZ)
        self.entry_N.valueChanged.connect(self.autofocusController.set_N)
        self.autofocusController.autofocusFinished.connect(self.autofocus_is_finished)

    def set_deltaZ(self, value):
        mm_per_ustep = 1.0 / self.stage.get_config().Z_AXIS.convert_real_units_to_ustep(1.0)
        deltaZ = round(value / 1000 / mm_per_ustep) * mm_per_ustep * 1000
        self.log.debug(f"{deltaZ=}")

        self.entry_delta.setValue(deltaZ)
        self.autofocusController.set_deltaZ(deltaZ)

    def autofocus_is_finished(self):
        self.btn_autofocus.setChecked(False)


class FilterControllerWidget(QFrame):
    """Widget for controlling filter wheel(s).

    Supports both single and multiple filter wheels. When multiple wheels are
    available, displays a tabbed interface with one tab per wheel.
    """

    def __init__(
        self,
        filterController: AbstractFilterWheelController,
        liveController: LiveController,
        main=None,
        config_repo=None,
        *args,
        **kwargs
    ):
        super().__init__(*args, **kwargs)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.filterController: AbstractFilterWheelController = filterController
        self.liveController = liveController
        self.config_repo = config_repo

        # Get available wheel indices
        self._wheel_indices = list(filterController.available_filter_wheels) or [1]

        # Track combo boxes and buttons per wheel (wheel_id -> widget)
        self._combo_boxes: Dict[int, QComboBox] = {}
        self._home_buttons: Dict[int, QPushButton] = {}
        self._get_pos_buttons: Dict[int, QPushButton] = {}
        self._next_buttons: Dict[int, QPushButton] = {}
        self._prev_buttons: Dict[int, QPushButton] = {}

        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def _get_wheel_name(self, wheel_id: int) -> str:
        """Get display name for a wheel from config or generate default."""
        if self.config_repo:
            try:
                registry = self.config_repo.get_filter_wheel_registry()
                if registry and registry.filter_wheels:
                    for wheel in registry.filter_wheels:
                        if wheel.id == wheel_id and wheel.name:
                            return wheel.name
            except Exception as e:
                self._log.warning(f"Failed to get filter wheel name for wheel_id={wheel_id}: {e}")
        # Default name
        return f"Wheel {wheel_id}"

    def add_components(self):
        main_layout = QVBoxLayout()

        # If multiple wheels, use tabs; otherwise use simple layout
        use_tabs = len(self._wheel_indices) > 1

        if use_tabs:
            self.tab_widget = QTabWidget()
            for wheel_id in self._wheel_indices:
                tab = self._create_wheel_tab(wheel_id)
                wheel_name = self._get_wheel_name(wheel_id)
                self.tab_widget.addTab(tab, wheel_name)
            main_layout.addWidget(self.tab_widget)
        else:
            # Single wheel - use simple layout
            wheel_id = self._wheel_indices[0]
            wheel_widget = self._create_wheel_tab(wheel_id)
            main_layout.addWidget(wheel_widget)

        # Shared checkbox for all wheels
        self.checkBox = QCheckBox("Disable filter wheel movement on changing Microscope Configuration", self)
        self.checkBox.stateChanged.connect(self.disable_movement_by_switching_channels)
        main_layout.addWidget(self.checkBox)

        # Info label
        info_label = QLabel("For acquisition, filter wheel positions need to be set in channel configurations.")
        main_layout.addWidget(info_label)

        self.setLayout(main_layout)

    def _create_wheel_tab(self, wheel_id: int) -> QWidget:
        """Create a widget for controlling a single wheel."""
        widget = QWidget()
        layout = QGridLayout()

        # Get filter wheel info to populate combo box
        try:
            wheel_info = self.filterController.get_filter_wheel_info(wheel_id)
            num_positions = wheel_info.number_of_slots
        except Exception:
            num_positions = 8  # Fallback

        # Get position names from registry if available
        position_names = {}
        if self.config_repo:
            try:
                registry = self.config_repo.get_filter_wheel_registry()
                if registry and registry.filter_wheels:
                    for wheel in registry.filter_wheels:
                        if wheel.id == wheel_id:
                            position_names = wheel.positions
                            break
            except Exception as e:
                self._log.warning(f"Failed to get filter position names for wheel {wheel_id}: {e}")

        # Position combo box
        combo_box = QComboBox()
        for i in range(1, num_positions + 1):
            # Try both int and string keys (YAML may load as strings)
            filter_name = position_names.get(i) or position_names.get(str(i)) or f"Position {i}"
            combo_box.addItem(f"{i}: {filter_name}")
        self._combo_boxes[wheel_id] = combo_box

        # Create buttons
        get_pos_btn = QPushButton("Get Position")
        home_btn = QPushButton("Home")
        next_btn = QPushButton("Next")
        prev_btn = QPushButton("Previous")

        self._get_pos_buttons[wheel_id] = get_pos_btn
        self._home_buttons[wheel_id] = home_btn
        self._next_buttons[wheel_id] = next_btn
        self._prev_buttons[wheel_id] = prev_btn

        # Layout
        layout.addWidget(QLabel("Position:"), 0, 0)
        layout.addWidget(combo_box, 0, 1)
        layout.addWidget(get_pos_btn, 0, 2)
        layout.addWidget(home_btn, 1, 0)
        layout.addWidget(next_btn, 1, 1)
        layout.addWidget(prev_btn, 1, 2)

        widget.setLayout(layout)

        # Connect signals with wheel_id captured in closures
        combo_box.currentIndexChanged.connect(lambda idx, wid=wheel_id: self._on_selection_change(wid, idx))
        get_pos_btn.clicked.connect(lambda checked, wid=wheel_id: self._update_position_from_controller(wid))
        home_btn.clicked.connect(lambda checked, wid=wheel_id: self._home(wid))
        next_btn.clicked.connect(lambda checked, wid=wheel_id: self._go_to_next_position(wid))
        prev_btn.clicked.connect(lambda checked, wid=wheel_id: self._go_to_previous_position(wid))

        return widget

    def _home(self, wheel_id: int):
        """Home a specific filter wheel."""
        self.filterController.home(wheel_id)

    def _update_position_from_controller(self, wheel_id: int):
        """Poll the current position from the controller and update the dropdown."""
        try:
            current_pos = self.filterController.get_filter_wheel_position().get(wheel_id, 1)
            combo_box = self._combo_boxes.get(wheel_id)
            if combo_box:
                combo_box.blockSignals(True)
                combo_box.setCurrentIndex(current_pos - 1)
                combo_box.blockSignals(False)
            self._log.debug(f"Filter wheel {wheel_id} position updated: {current_pos}")
        except Exception as e:
            self._log.error(f"Error getting filter wheel {wheel_id} position: {e}")

    def _on_selection_change(self, wheel_id: int, index: int):
        """Handle position selection from combo box."""
        if index >= 0:
            position = index + 1
            self.filterController.set_filter_wheel_position({wheel_id: position})

    def _go_to_next_position(self, wheel_id: int):
        """Move to the next position."""
        try:
            current_pos = self.filterController.get_filter_wheel_position().get(wheel_id, 1)
            wheel_info = self.filterController.get_filter_wheel_info(wheel_id)
            max_pos = wheel_info.number_of_slots

            if current_pos < max_pos:
                new_pos = current_pos + 1
                self.filterController.set_filter_wheel_position({wheel_id: new_pos})
                combo_box = self._combo_boxes.get(wheel_id)
                if combo_box:
                    combo_box.setCurrentIndex(new_pos - 1)
        except Exception as e:
            self._log.error(f"Error moving wheel {wheel_id} to next position: {e}")

    def _go_to_previous_position(self, wheel_id: int):
        """Move to the previous position."""
        try:
            current_pos = self.filterController.get_filter_wheel_position().get(wheel_id, 1)

            if current_pos > 1:
                new_pos = current_pos - 1
                self.filterController.set_filter_wheel_position({wheel_id: new_pos})
                combo_box = self._combo_boxes.get(wheel_id)
                if combo_box:
                    combo_box.setCurrentIndex(new_pos - 1)
        except Exception as e:
            self._log.error(f"Error moving wheel {wheel_id} to previous position: {e}")

    def disable_movement_by_switching_channels(self, state):
        """Enable/disable automatic filter wheel movement when changing channels."""
        if state:
            self.liveController.enable_channel_auto_filter_switching = False
        else:
            self.liveController.enable_channel_auto_filter_switching = True

    # Backward compatibility properties and methods
    @property
    def wheel_index(self):
        """Get the first wheel index for backward compatibility."""
        return self._wheel_indices[0] if self._wheel_indices else 1

    @property
    def comboBox(self):
        """Get the first combo box for backward compatibility."""
        return self._combo_boxes.get(self.wheel_index)

    def home(self):
        """Home the first filter wheel for backward compatibility."""
        self._home(self.wheel_index)

    def update_position_from_controller(self):
        """Update position for first wheel for backward compatibility."""
        self._update_position_from_controller(self.wheel_index)

    def on_selection_change(self, index):
        """Handle selection change for first wheel for backward compatibility."""
        self._on_selection_change(self.wheel_index, index)

    def go_to_next_position(self):
        """Go to next position on first wheel for backward compatibility."""
        self._go_to_next_position(self.wheel_index)

    def go_to_previous_position(self):
        """Go to previous position on first wheel for backward compatibility."""
        self._go_to_previous_position(self.wheel_index)


class StatsDisplayWidget(QFrame):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initUI()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def initUI(self):
        self.layout = QVBoxLayout()
        self.table_widget = QTableWidget()
        self.table_widget.setColumnCount(2)
        self.table_widget.verticalHeader().hide()
        self.table_widget.horizontalHeader().hide()
        self.table_widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.layout.addWidget(self.table_widget)
        self.setLayout(self.layout)

    def display_stats(self, stats):
        print("displaying parasite stats")
        locale.setlocale(locale.LC_ALL, "")
        self.table_widget.setRowCount(len(stats))
        row = 0
        for key, value in stats.items():
            key_item = QTableWidgetItem(str(key))
            value_item = None
            try:
                value_item = QTableWidgetItem(f"{value:n}")
            except:
                value_item = QTableWidgetItem(str(value))
            self.table_widget.setItem(row, 0, key_item)
            self.table_widget.setItem(row, 1, value_item)
            row += 1


class WellSelectionWidget(QTableWidget):
    signal_wellSelected = Signal(bool)
    signal_wellSelectedPos = Signal(float, float)

    def __init__(self, format_, wellplateFormatWidget, *args, **kwargs):
        super(WellSelectionWidget, self).__init__(*args, **kwargs)
        self.wellplateFormatWidget = wellplateFormatWidget
        self.cellDoubleClicked.connect(self.onDoubleClick)
        self.itemSelectionChanged.connect(self.onSelectionChanged)
        self.fixed_height = 400
        self.setFormat(format_)

    def setFormat(self, format_):
        self.format = format_
        settings = self.wellplateFormatWidget.getWellplateSettings(self.format)
        self.rows = settings["rows"]
        self.columns = settings["cols"]
        self.spacing_mm = settings["well_spacing_mm"]
        self.number_of_skip = settings["number_of_skip"]
        self.a1_x_mm = settings["a1_x_mm"]
        self.a1_y_mm = settings["a1_y_mm"]
        self.a1_x_pixel = settings["a1_x_pixel"]
        self.a1_y_pixel = settings["a1_y_pixel"]
        self.well_size_mm = settings["well_size_mm"]

        self.setRowCount(self.rows)
        self.setColumnCount(self.columns)
        self.initUI()
        self.setData()

    def initUI(self):
        # Disable editing, scrollbars, and other interactions
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.verticalScrollBar().setDisabled(True)
        self.horizontalScrollBar().setDisabled(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setTabKeyNavigation(False)
        self.setDragEnabled(False)
        self.setAcceptDrops(False)
        self.setDragDropOverwriteMode(False)
        self.setMouseTracking(False)

        if self.format == "1536 well plate":
            font = QFont()
            font.setPointSize(6)  # You can adjust this value as needed
        else:
            font = QFont()
        self.horizontalHeader().setFont(font)
        self.verticalHeader().setFont(font)

        self.setLayout()

    def setLayout(self):
        # Calculate available space and cell size
        header_height = self.horizontalHeader().height()
        available_height = self.fixed_height - header_height  # Fixed height of 408 pixels

        # Calculate cell size based on the minimum of available height and width
        cell_size = available_height // self.rowCount()

        self.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.verticalHeader().setDefaultSectionSize(cell_size)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.horizontalHeader().setDefaultSectionSize(cell_size)

        # Ensure sections do not resize
        self.verticalHeader().setMinimumSectionSize(cell_size)
        self.verticalHeader().setMaximumSectionSize(cell_size)
        self.horizontalHeader().setMinimumSectionSize(cell_size)
        self.horizontalHeader().setMaximumSectionSize(cell_size)

        row_header_width = self.verticalHeader().width()

        # Calculate total width and height
        total_height = (self.rowCount() * cell_size) + header_height
        total_width = (self.columnCount() * cell_size) + row_header_width

        # Set the widget's fixed size
        self.setFixedHeight(total_height)
        self.setFixedWidth(total_width)

        # Force the widget to update its layout
        self.updateGeometry()
        self.viewport().update()

    def onWellplateChanged(self):
        self.setFormat(self.wellplateFormatWidget.wellplate_format)

    def setData(self):
        for i in range(self.rowCount()):
            for j in range(self.columnCount()):
                item = self.item(i, j)
                if not item:  # Create a new item if none exists
                    item = QTableWidgetItem()
                    self.setItem(i, j, item)
                # Reset to selectable by default
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)

        if self.number_of_skip > 0 and self.format != 0:
            for i in range(self.number_of_skip):
                for j in range(self.columns):  # Apply to rows
                    self.item(i, j).setFlags(self.item(i, j).flags() & ~Qt.ItemIsSelectable)
                    self.item(self.rows - 1 - i, j).setFlags(
                        self.item(self.rows - 1 - i, j).flags() & ~Qt.ItemIsSelectable
                    )
                for k in range(self.rows):  # Apply to columns
                    self.item(k, i).setFlags(self.item(k, i).flags() & ~Qt.ItemIsSelectable)
                    self.item(k, self.columns - 1 - i).setFlags(
                        self.item(k, self.columns - 1 - i).flags() & ~Qt.ItemIsSelectable
                    )

        # Update row headers
        row_headers = []
        for i in range(self.rows):
            if i < 26:
                label = chr(ord("A") + i)
            else:
                first_letter = chr(ord("A") + (i // 26) - 1)
                second_letter = chr(ord("A") + (i % 26))
                label = first_letter + second_letter
            row_headers.append(label)
        self.setVerticalHeaderLabels(row_headers)

        # Adjust vertical header width after setting labels
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

    def onDoubleClick(self, row, col):
        print("double click well", row, col)
        if (row >= 0 + self.number_of_skip and row <= self.rows - 1 - self.number_of_skip) and (
            col >= 0 + self.number_of_skip and col <= self.columns - 1 - self.number_of_skip
        ):
            x_mm = col * self.spacing_mm + self.a1_x_mm + WELLPLATE_OFFSET_X_mm
            y_mm = row * self.spacing_mm + self.a1_y_mm + WELLPLATE_OFFSET_Y_mm
            self.signal_wellSelectedPos.emit(x_mm, y_mm)
            print("well location:", (x_mm, y_mm))
            self.signal_wellSelected.emit(True)
        else:
            self.signal_wellSelected.emit(False)

    def onSingleClick(self, row, col):
        print("single click well", row, col)
        if (row >= 0 + self.number_of_skip and row <= self.rows - 1 - self.number_of_skip) and (
            col >= 0 + self.number_of_skip and col <= self.columns - 1 - self.number_of_skip
        ):
            self.signal_wellSelected.emit(True)
        else:
            self.signal_wellSelected.emit(False)

    def onSelectionChanged(self):
        # Check if there are any selected indexes before proceeding
        if self.format != "glass slide":
            has_selection = bool(self.selectedIndexes())
            self.signal_wellSelected.emit(has_selection)

    def get_selected_cells(self):
        list_of_selected_cells = []
        print("getting selected cells...")
        if self.format == "glass slide":
            return list_of_selected_cells
        for index in self.selectedIndexes():
            row, col = index.row(), index.column()
            # Check if the cell is within the allowed bounds
            if (row >= 0 + self.number_of_skip and row <= self.rows - 1 - self.number_of_skip) and (
                col >= 0 + self.number_of_skip and col <= self.columns - 1 - self.number_of_skip
            ):
                list_of_selected_cells.append((row, col))
        if list_of_selected_cells:
            print("cells:", list_of_selected_cells)
        else:
            print("no cells")
        return list_of_selected_cells

    def resizeEvent(self, event):
        self.initUI()
        super().resizeEvent(event)

    def wheelEvent(self, event):
        # Ignore wheel events to prevent scrolling
        event.ignore()

    def scrollTo(self, index, hint=QAbstractItemView.EnsureVisible):
        pass

    def set_white_boundaries_style(self):
        style = """
        QTableWidget {
            gridline-color: white;
            border: 1px solid white;
        }
        QHeaderView::section {
            color: white;
        }
        """
        self.setStyleSheet(style)


class FlexibleMultiPointWidget(AcquisitionYAMLDropMixin, QFrame):

    signal_acquisition_started = Signal(bool)  # true = started, false = finished
    signal_acquisition_channels = Signal(list)  # list channels
    signal_acquisition_shape = Signal(int, float)  # Nz, dz

    def __init__(
        self,
        stage: AbstractStage,
        navigationViewer,
        multipointController,
        objectiveStore,
        scanCoordinates,
        focusMapWidget,
        napariMosaicWidget=None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        # Enable drag-and-drop so we can warn users that flexible YAML loading isn't supported yet.
        self.setAcceptDrops(True)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.acquisition_start_time = None
        self.last_used_locations = None
        self.last_used_location_ids = None
        self.stage = stage
        self.navigationViewer = navigationViewer
        self.multipointController = multipointController
        self.objectiveStore = objectiveStore
        self.scanCoordinates = scanCoordinates
        self.focusMapWidget = focusMapWidget
        self.napariMosaicWidget = napariMosaicWidget
        self.performance_mode = False
        self.base_path_is_set = False
        self.location_list = np.empty((0, 3), dtype=float)
        self.location_ids = np.empty((0,), dtype="<U20")
        self.use_overlap = USE_OVERLAP_FOR_FLEXIBLE
        self.add_components()
        self.setup_layout()
        self.setup_connections()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        self.is_current_acquisition_widget = False
        self.acquisition_in_place = False

    def add_components(self):
        self.btn_setSavingDir = QPushButton("Browse")
        self.btn_setSavingDir.setDefault(False)
        self.btn_setSavingDir.setIcon(QIcon("icon/folder.png"))

        self.lineEdit_savingDir = QLineEdit()
        self.lineEdit_savingDir.setReadOnly(True)

        last_path = get_last_used_saving_path()
        self.lineEdit_savingDir.setText(last_path)
        self.multipointController.set_base_path(last_path)
        self.base_path_is_set = True

        self.lineEdit_experimentID = QLineEdit()
        self.lineEdit_experimentID.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.lineEdit_experimentID.setFixedWidth(96)

        self.dropdown_location_list = QComboBox()
        self.dropdown_location_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.btn_add = QPushButton("Add")
        self.btn_remove = QPushButton("Remove")
        self.btn_previous = QPushButton("Previous")
        self.btn_next = QPushButton("Next")
        self.btn_clear = QPushButton("Clear")

        self.btn_load_last_executed = QPushButton("Prev Used Locations")

        self.btn_export_locations = QPushButton("Export Location List")
        self.btn_import_locations = QPushButton("Import Location List")
        self.btn_show_table_location_list = QPushButton("Edit")  # Open / Edit

        # editable points table
        self.table_location_list = QTableWidget()
        self.table_location_list.setColumnCount(4)
        header_labels = ["x", "y", "z", "ID"]
        self.table_location_list.setHorizontalHeaderLabels(header_labels)
        self.btn_update_z = QPushButton("Update Z")

        self.entry_deltaX = QDoubleSpinBox()
        self.entry_deltaX.setMinimum(0)
        self.entry_deltaX.setMaximum(5)
        self.entry_deltaX.setSingleStep(0.1)
        self.entry_deltaX.setValue(Acquisition.DX)
        self.entry_deltaX.setDecimals(3)
        self.entry_deltaX.setSuffix(" mm")
        self.entry_deltaX.setKeyboardTracking(False)

        self.entry_NX = QSpinBox()
        self.entry_NX.setMinimum(1)
        self.entry_NX.setMaximum(1000)
        self.entry_NX.setMinimumWidth(self.entry_NX.sizeHint().width())
        self.entry_NX.setMaximum(50)
        self.entry_NX.setSingleStep(1)
        self.entry_NX.setValue(1)
        self.entry_NX.setKeyboardTracking(False)
        # self.entry_NX.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.entry_deltaY = QDoubleSpinBox()
        self.entry_deltaY.setMinimum(0)
        self.entry_deltaY.setMaximum(5)
        self.entry_deltaY.setSingleStep(0.1)
        self.entry_deltaY.setValue(Acquisition.DX)
        self.entry_deltaY.setDecimals(3)
        self.entry_deltaY.setSuffix(" mm")
        self.entry_deltaY.setKeyboardTracking(False)

        self.entry_NY = QSpinBox()
        self.entry_NY.setMinimum(1)
        self.entry_NY.setMaximum(1000)
        self.entry_NY.setMinimumWidth(self.entry_NX.sizeHint().width())
        self.entry_NY.setMaximum(50)
        self.entry_NY.setSingleStep(1)
        self.entry_NY.setValue(1)
        self.entry_NY.setKeyboardTracking(False)
        # self.entry_NY.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.entry_overlap = QDoubleSpinBox()
        self.entry_overlap.setKeyboardTracking(False)
        self.entry_overlap.setRange(0, 99)
        self.entry_overlap.setDecimals(1)
        self.entry_overlap.setSuffix(" %")
        self.entry_overlap.setValue(10)
        self.entry_overlap.setKeyboardTracking(False)

        self.entry_deltaZ = QDoubleSpinBox()
        self.entry_deltaZ.setKeyboardTracking(False)
        self.entry_deltaZ.setMinimum(0)
        self.entry_deltaZ.setMaximum(1000)
        self.entry_deltaZ.setSingleStep(0.1)
        self.entry_deltaZ.setValue(Acquisition.DZ)
        self.entry_deltaZ.setDecimals(3)
        self.entry_deltaZ.setSuffix(" μm")
        self.entry_deltaZ.setKeyboardTracking(False)
        # self.entry_deltaZ.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.entry_NZ = QSpinBox()
        self.entry_NZ.setMinimum(1)
        self.entry_NZ.setMaximum(2000)
        self.entry_NZ.setSingleStep(1)
        self.entry_NZ.setValue(1)
        self.entry_NZ.setKeyboardTracking(False)

        self.entry_dt = QDoubleSpinBox()
        self.entry_dt.setKeyboardTracking(False)
        self.entry_dt.setMinimum(0)
        self.entry_dt.setMaximum(12 * 3600)
        self.entry_dt.setSingleStep(1)
        self.entry_dt.setValue(0)
        self.entry_dt.setSuffix(" s")
        self.entry_dt.setKeyboardTracking(False)
        # self.entry_dt.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.entry_Nt = QSpinBox()
        self.entry_Nt.setMinimum(1)
        self.entry_Nt.setMaximum(10000)  # @@@ to be changed
        self.entry_Nt.setSingleStep(1)
        self.entry_Nt.setValue(1)
        self.entry_Nt.setKeyboardTracking(False)

        # Calculate a consistent width
        max_delta_width = max(
            self.entry_deltaZ.sizeHint().width(),
            self.entry_dt.sizeHint().width(),
            self.entry_deltaX.sizeHint().width(),
            self.entry_deltaY.sizeHint().width(),
        )
        self.entry_deltaZ.setFixedWidth(max_delta_width)
        self.entry_dt.setFixedWidth(max_delta_width)
        self.entry_deltaX.setFixedWidth(max_delta_width)
        self.entry_deltaY.setFixedWidth(max_delta_width)

        max_num_width = max(
            self.entry_NX.sizeHint().width(),
            self.entry_NY.sizeHint().width(),
            self.entry_NZ.sizeHint().width(),
            self.entry_Nt.sizeHint().width(),
        )
        self.entry_NX.setFixedWidth(max_num_width)
        self.entry_NY.setFixedWidth(max_num_width)
        self.entry_NZ.setFixedWidth(max_num_width)
        self.entry_Nt.setFixedWidth(max_num_width)

        self.list_configurations = QListWidget()
        for microscope_configuration in self.multipointController.liveController.get_channels(
            self.objectiveStore.current_objective
        ):
            self.list_configurations.addItems([microscope_configuration.name])
        self.list_configurations.setSelectionMode(
            QAbstractItemView.MultiSelection
        )  # ref: https://doc.qt.io/qt-5/qabstractitemview.html#SelectionMode-enum

        self.checkbox_withAutofocus = QCheckBox("Contrast AF")
        self.checkbox_withAutofocus.setChecked(MULTIPOINT_CONTRAST_AUTOFOCUS_ENABLE_BY_DEFAULT)
        self.multipointController.set_af_flag(MULTIPOINT_CONTRAST_AUTOFOCUS_ENABLE_BY_DEFAULT)

        self.checkbox_withReflectionAutofocus = QCheckBox("Reflection AF")
        self.checkbox_withReflectionAutofocus.setChecked(MULTIPOINT_REFLECTION_AUTOFOCUS_ENABLE_BY_DEFAULT)
        self.multipointController.set_reflection_af_flag(MULTIPOINT_REFLECTION_AUTOFOCUS_ENABLE_BY_DEFAULT)

        self.checkbox_genAFMap = QCheckBox("Generate Focus Map")
        self.checkbox_genAFMap.setChecked(False)

        self.checkbox_useFocusMap = QCheckBox("Use Focus Map")
        self.checkbox_useFocusMap.setChecked(False)

        self.checkbox_usePiezo = QCheckBox("Piezo Z-Stack")
        self.checkbox_usePiezo.setChecked(MULTIPOINT_USE_PIEZO_FOR_ZSTACKS)

        self.checkbox_stitchOutput = QCheckBox("Stitch Scans")
        self.checkbox_stitchOutput.setChecked(False)

        self.checkbox_skipSaving = QCheckBox("Skip Saving")
        self.checkbox_skipSaving.setChecked(False)

        self.checkbox_set_z_range = QCheckBox("Set Z-range")
        self.checkbox_set_z_range.toggled.connect(self.toggle_z_range_controls)

        # Add new components for Z-range
        self.entry_minZ = QDoubleSpinBox()
        self.entry_minZ.setKeyboardTracking(False)
        self.entry_minZ.setMinimum(SOFTWARE_POS_LIMIT.Z_NEGATIVE * 1000)  # Convert to μm
        self.entry_minZ.setMaximum(SOFTWARE_POS_LIMIT.Z_POSITIVE * 1000)  # Convert to μm
        self.entry_minZ.setSingleStep(1)  # Step by 1 μm
        self.entry_minZ.setValue(self.stage.get_pos().z_mm * 1000)  # Set to current position
        self.entry_minZ.setSuffix(" μm")
        # self.entry_minZ.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.set_minZ_button = QPushButton("Set")
        self.set_minZ_button.clicked.connect(self.set_z_min)

        self.entry_maxZ = QDoubleSpinBox()
        self.entry_maxZ.setKeyboardTracking(False)
        self.entry_maxZ.setMinimum(SOFTWARE_POS_LIMIT.Z_NEGATIVE * 1000)  # Convert to μm
        self.entry_maxZ.setMaximum(SOFTWARE_POS_LIMIT.Z_POSITIVE * 1000)  # Convert to μm
        self.entry_maxZ.setSingleStep(1)  # Step by 1 μm
        self.entry_maxZ.setValue(self.stage.get_pos().z_mm * 1000)  # Set to current position
        self.entry_maxZ.setSuffix(" μm")
        # self.entry_maxZ.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.set_maxZ_button = QPushButton("Set")
        self.set_maxZ_button.clicked.connect(self.set_z_max)

        self.combobox_z_stack = QComboBox()
        self.combobox_z_stack.addItems(["From Bottom (Z-min)", "From Center", "From Top (Z-max)"])

        self.btn_startAcquisition = QPushButton("Start\n Acquisition ")
        self.btn_startAcquisition.setStyleSheet("background-color: #C2C2FF")
        self.btn_startAcquisition.setCheckable(True)
        self.btn_startAcquisition.setChecked(False)
        # self.btn_startAcquisition.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # Add snap images button
        self.btn_snap_images = QPushButton("Snap Images")
        self.btn_snap_images.clicked.connect(self.on_snap_images)
        self.btn_snap_images.setCheckable(False)
        self.btn_snap_images.setChecked(False)

        self.progress_label = QLabel("Region -/-")
        self.progress_bar = QProgressBar()
        self.eta_label = QLabel("--:--:--")
        self.progress_bar.setVisible(False)
        self.progress_label.setVisible(False)
        self.eta_label.setVisible(False)
        self.eta_timer = QTimer()

        # layout
        self.grid_line0 = QHBoxLayout()
        self.grid_line0.addWidget(QLabel("Saving Path"))
        self.grid_line0.addWidget(self.lineEdit_savingDir)
        self.grid_line0.addWidget(self.btn_setSavingDir)
        self.grid_line0.addWidget(QLabel("ID"))
        self.grid_line0.addWidget(self.lineEdit_experimentID)

        self.grid_location_list_line1 = QGridLayout()
        temp3 = QHBoxLayout()
        temp3.addWidget(QLabel("Location List"))
        temp3.addWidget(self.dropdown_location_list)
        self.grid_location_list_line1.addLayout(temp3, 0, 0, 1, 6)  # Span across all columns except the last
        self.grid_location_list_line1.addWidget(self.btn_update_z, 0, 6, 1, 2)  # Align with other buttons

        self.grid_location_list_line2 = QGridLayout()
        # Make all buttons span 2 columns for consistent width
        self.grid_location_list_line2.addWidget(self.btn_add, 1, 0, 1, 2)
        self.grid_location_list_line2.addWidget(self.btn_remove, 1, 2, 1, 2)
        self.grid_location_list_line2.addWidget(self.btn_next, 1, 4, 1, 2)
        self.grid_location_list_line2.addWidget(self.btn_clear, 1, 6, 1, 2)

        self.grid_location_list_line3 = QGridLayout()
        self.grid_location_list_line3.addWidget(self.btn_import_locations, 2, 0, 1, 3)
        self.grid_location_list_line3.addWidget(self.btn_export_locations, 2, 3, 1, 3)
        self.grid_location_list_line3.addWidget(self.btn_show_table_location_list, 2, 6, 1, 2)

        # Create spacer items
        EDGE_SPACING = 4  # Adjust this value as needed
        edge_spacer = QSpacerItem(EDGE_SPACING, 0, QSizePolicy.Fixed, QSizePolicy.Minimum)

        # Create first row layouts
        if self.use_overlap:
            xy_half = QHBoxLayout()
            xy_half.addWidget(QLabel("Nx"))
            xy_half.addWidget(self.entry_NX)
            xy_half.addStretch(1)
            xy_half.addWidget(QLabel("Ny"))
            xy_half.addWidget(self.entry_NY)
            xy_half.addSpacerItem(edge_spacer)

            overlap_half = QHBoxLayout()
            overlap_half.addSpacerItem(edge_spacer)
            overlap_half.addWidget(QLabel("FOV Overlap"), alignment=Qt.AlignRight)
            overlap_half.addWidget(self.entry_overlap)
        else:
            # Create alternate first row layouts (dx, dy) instead of (overlap %)
            x_half = QHBoxLayout()
            x_half.addWidget(QLabel("dx"))
            x_half.addWidget(self.entry_deltaX)
            x_half.addStretch(1)
            x_half.addWidget(QLabel("Nx"))
            x_half.addWidget(self.entry_NX)
            x_half.addSpacerItem(edge_spacer)

            y_half = QHBoxLayout()
            y_half.addSpacerItem(edge_spacer)
            y_half.addWidget(QLabel("dy"))
            y_half.addWidget(self.entry_deltaY)
            y_half.addStretch(1)
            y_half.addWidget(QLabel("Ny"))
            y_half.addWidget(self.entry_NY)

        # Create second row layouts
        dz_half = QHBoxLayout()
        dz_half.addWidget(QLabel("dz"))
        dz_half.addWidget(self.entry_deltaZ)
        dz_half.addStretch(1)
        dz_half.addWidget(QLabel("Nz"))
        dz_half.addWidget(self.entry_NZ)
        dz_half.addSpacerItem(edge_spacer)

        dt_half = QHBoxLayout()
        dt_half.addSpacerItem(edge_spacer)
        dt_half.addWidget(QLabel("dt"))
        dt_half.addWidget(self.entry_dt)
        dt_half.addStretch(1)
        dt_half.addWidget(QLabel("Nt"))
        dt_half.addWidget(self.entry_Nt)

        self.grid_acquisition = QGridLayout()
        # Add the layouts to grid_line1
        if self.use_overlap:
            self.grid_acquisition.addLayout(xy_half, 3, 0, 1, 4)
            self.grid_acquisition.addLayout(overlap_half, 3, 4, 1, 4)
        else:
            self.grid_acquisition.addLayout(x_half, 3, 0, 1, 4)
            self.grid_acquisition.addLayout(y_half, 3, 4, 1, 4)
        self.grid_acquisition.addLayout(dz_half, 4, 0, 1, 4)
        self.grid_acquisition.addLayout(dt_half, 4, 4, 1, 4)

        self.z_min_layout = QHBoxLayout()
        self.z_min_layout.addWidget(self.set_minZ_button)
        self.z_min_layout.addWidget(QLabel("Z-min"), Qt.AlignRight)
        self.z_min_layout.addWidget(self.entry_minZ)
        self.z_min_layout.addSpacerItem(edge_spacer)

        self.z_max_layout = QHBoxLayout()
        self.z_max_layout.addSpacerItem(edge_spacer)
        self.z_max_layout.addWidget(self.set_maxZ_button)
        self.z_max_layout.addWidget(QLabel("Z-max"), Qt.AlignRight)
        self.z_max_layout.addWidget(self.entry_maxZ)

        self.grid_acquisition.addLayout(self.z_min_layout, 5, 0, 1, 4)  # hide this in toggle
        self.grid_acquisition.addLayout(self.z_max_layout, 5, 4, 1, 4)  # hide this in toggle

        grid_af = QVBoxLayout()
        grid_af.addWidget(self.checkbox_withAutofocus)
        if SUPPORT_LASER_AUTOFOCUS:
            grid_af.addWidget(self.checkbox_withReflectionAutofocus)
        # grid_af.addWidget(self.checkbox_genAFMap)  # we are not using auto-focus map for now
        grid_af.addWidget(self.checkbox_useFocusMap)
        if HAS_OBJECTIVE_PIEZO:
            grid_af.addWidget(self.checkbox_usePiezo)
            if IS_PIEZO_ONLY:
                self.checkbox_usePiezo.setChecked(True)
                self.checkbox_usePiezo.setVisible(False)
        grid_af.addWidget(self.checkbox_set_z_range)
        grid_af.addWidget(self.checkbox_skipSaving)

        grid_config = QHBoxLayout()
        grid_config.addWidget(self.list_configurations)
        grid_config.addSpacerItem(edge_spacer)

        button_layout = QVBoxLayout()
        button_layout.addWidget(self.btn_snap_images)
        button_layout.addWidget(self.btn_startAcquisition)

        grid_acquisition = QHBoxLayout()
        grid_acquisition.addSpacerItem(edge_spacer)
        grid_acquisition.addLayout(grid_af)
        grid_acquisition.addLayout(button_layout)

        self.grid_acquisition.addLayout(grid_config, 6, 0, 3, 4)
        self.grid_acquisition.addLayout(grid_acquisition, 6, 4, 3, 4)

        # Columns 0-3: Combined stretch factor = 4
        # Columns 4-7: Combined stretch factor = 4
        for i in range(4):
            self.grid_location_list_line1.setColumnStretch(i, 1)
            self.grid_location_list_line2.setColumnStretch(i, 1)
            self.grid_location_list_line3.setColumnStretch(i, 1)
            self.grid_acquisition.setColumnStretch(i, 1)

            self.grid_location_list_line1.setColumnStretch(i + 4, 1)
            self.grid_location_list_line2.setColumnStretch(i + 4, 1)
            self.grid_location_list_line3.setColumnStretch(i + 4, 1)
            self.grid_acquisition.setColumnStretch(i + 4, 1)

        self.grid_location_list_line1.setRowStretch(0, 0)  # Location list row
        self.grid_location_list_line2.setRowStretch(1, 0)  # Button row
        self.grid_location_list_line3.setRowStretch(2, 0)  # Import/Export buttons
        self.grid_acquisition.setRowStretch(0, 0)  # Nx/Ny and overlap row
        self.grid_acquisition.setRowStretch(1, 0)  # dz/Nz and dt/Nt row
        self.grid_acquisition.setRowStretch(2, 0)  # Z-range row
        self.grid_acquisition.setRowStretch(3, 1)  # Configuration/AF row - allow this to stretch
        self.grid_acquisition.setRowStretch(4, 0)  # Last row

        # Row : Progress Bar
        self.row_progress_layout = QHBoxLayout()
        self.row_progress_layout.addWidget(self.progress_label)
        self.row_progress_layout.addWidget(self.progress_bar)
        self.row_progress_layout.addWidget(self.eta_label)

        # add and display a timer - to be implemented
        # self.timer = QTimer()

    def setup_connections(self):
        # connections
        if self.use_overlap:
            self.entry_overlap.valueChanged.connect(self.update_fov_positions)
        else:
            self.entry_deltaX.valueChanged.connect(self.update_fov_positions)
            self.entry_deltaY.valueChanged.connect(self.update_fov_positions)
        self.entry_NX.valueChanged.connect(self.update_fov_positions)
        self.entry_NY.valueChanged.connect(self.update_fov_positions)
        # self.btn_add.clicked.connect(self.update_fov_positions) #TODO: this is handled in the add_location method - to be removed
        # self.btn_remove.clicked.connect(self.update_fov_positions) #TODO: this is handled in the remove_location method - to be removed
        self.entry_deltaZ.valueChanged.connect(self.set_deltaZ)
        self.entry_dt.valueChanged.connect(self.multipointController.set_deltat)
        self.entry_NX.valueChanged.connect(self.multipointController.set_NX)
        self.entry_NY.valueChanged.connect(self.multipointController.set_NY)
        self.entry_NZ.valueChanged.connect(self.multipointController.set_NZ)
        self.entry_Nt.valueChanged.connect(self.multipointController.set_Nt)
        self.checkbox_genAFMap.toggled.connect(self.multipointController.set_gen_focus_map_flag)
        self.checkbox_useFocusMap.toggled.connect(self.focusMapWidget.setEnabled)
        self.checkbox_withAutofocus.toggled.connect(self.multipointController.set_af_flag)
        self.checkbox_withReflectionAutofocus.toggled.connect(self.multipointController.set_reflection_af_flag)
        self.checkbox_usePiezo.toggled.connect(self.multipointController.set_use_piezo)
        self.checkbox_skipSaving.toggled.connect(self.multipointController.set_skip_saving)
        self.btn_setSavingDir.clicked.connect(self.set_saving_dir)
        self.btn_startAcquisition.clicked.connect(self.toggle_acquisition)
        self.multipointController.acquisition_finished.connect(self.acquisition_is_finished)
        self.list_configurations.itemSelectionChanged.connect(self.emit_selected_channels)
        # self.combobox_z_stack.currentIndexChanged.connect(self.signal_z_stacking.emit)

        self.multipointController.signal_acquisition_progress.connect(self.update_acquisition_progress)
        self.multipointController.signal_region_progress.connect(self.update_region_progress)
        self.signal_acquisition_started.connect(self.display_progress_bar)
        self.eta_timer.timeout.connect(self.update_eta_display)

        self.btn_add.clicked.connect(self.add_location)
        self.btn_remove.clicked.connect(self.remove_location)
        self.btn_previous.clicked.connect(self.previous)
        self.btn_next.clicked.connect(self.next)
        self.btn_clear.clicked.connect(self.clear)
        self.btn_load_last_executed.clicked.connect(self.load_last_used_locations)
        self.btn_export_locations.clicked.connect(self.export_location_list)
        self.btn_import_locations.clicked.connect(self.import_location_list)

        self.table_location_list.cellClicked.connect(self.cell_was_clicked)
        self.table_location_list.cellChanged.connect(self.cell_was_changed)
        self.btn_show_table_location_list.clicked.connect(self.table_location_list.show)
        self.btn_update_z.clicked.connect(self.update_z)
        self.dropdown_location_list.currentIndexChanged.connect(self.go_to)

        self.shortcut = QShortcut(QKeySequence(";"), self)
        self.shortcut.activated.connect(self.btn_add.click)

        self.toggle_z_range_controls(False)
        self.multipointController.set_use_piezo(self.checkbox_usePiezo.isChecked())

    def setup_layout(self):
        self.grid = QVBoxLayout()
        self.grid.addLayout(self.grid_line0)
        self.grid.addLayout(self.grid_location_list_line1)
        self.grid.addLayout(self.grid_location_list_line2)
        self.grid.addLayout(self.grid_location_list_line3)
        self.grid.addLayout(self.grid_acquisition)
        self.grid.addLayout(self.row_progress_layout)
        self.setLayout(self.grid)

    def toggle_z_range_controls(self, state):
        is_visible = bool(state)

        # Hide/show widgets in z_min_layout
        for i in range(self.z_min_layout.count()):
            widget = self.z_min_layout.itemAt(i).widget()
            if widget is not None:
                widget.setVisible(is_visible)
            widget = self.z_max_layout.itemAt(i).widget()
            if widget is not None:
                widget.setVisible(is_visible)

        # Disable reflection autofocus checkbox if Z-range is visible
        self.checkbox_withReflectionAutofocus.setEnabled(not is_visible)
        # Enable/disable NZ entry based on the inverse of is_visible
        self.entry_NZ.setEnabled(not is_visible)
        current_z = self.stage.get_pos().z_mm * 1000
        self.entry_minZ.setValue(current_z)
        if is_visible:
            self._reset_reflection_af_reference()
        self.entry_maxZ.setValue(current_z)

        if not is_visible:
            try:
                self.entry_minZ.valueChanged.disconnect(self.update_z_max)
                self.entry_maxZ.valueChanged.disconnect(self.update_z_min)
                self.entry_minZ.valueChanged.disconnect(self.update_Nz)
                self.entry_maxZ.valueChanged.disconnect(self.update_Nz)
                self.entry_deltaZ.valueChanged.disconnect(self.update_Nz)
            except:
                pass
            # When Z-range is not specified, set Z-min and Z-max to current Z position
            current_z = self.stage.get_pos().z_mm * 1000
            self.entry_minZ.setValue(current_z)
            self.entry_maxZ.setValue(current_z)
        else:
            self.entry_minZ.valueChanged.connect(self.update_z_max)
            self.entry_maxZ.valueChanged.connect(self.update_z_min)
            self.entry_minZ.valueChanged.connect(self.update_Nz)
            self.entry_maxZ.valueChanged.connect(self.update_Nz)
            self.entry_deltaZ.valueChanged.connect(self.update_Nz)

        # Update the layout
        self.grid.update()
        self.updateGeometry()
        self.update()

    def init_z(self, z_pos_mm=None):
        if z_pos_mm is None:
            z_pos_mm = self.stage.get_pos().z_mm

        # block entry update signals
        self.entry_minZ.blockSignals(True)
        self.entry_maxZ.blockSignals(True)

        # set entry range values bith to current z pos
        self.entry_minZ.setValue(z_pos_mm * 1000)
        self.entry_maxZ.setValue(z_pos_mm * 1000)
        print("init z-level flexible:", self.entry_minZ.value())

        # reallow updates from entry sinals (signal enforces min <= max when we update either entry)
        self.entry_minZ.blockSignals(False)
        self.entry_maxZ.blockSignals(False)

    def set_z_min(self):
        z_value = self.stage.get_pos().z_mm * 1000  # Convert to μm
        self.entry_minZ.setValue(z_value)
        self._reset_reflection_af_reference()

    def set_z_max(self):
        z_value = self.stage.get_pos().z_mm * 1000  # Convert to μm
        self.entry_maxZ.setValue(z_value)

    def update_z_min(self, z_pos_um):
        if z_pos_um < self.entry_minZ.value():
            self.entry_minZ.setValue(z_pos_um)
            self._reset_reflection_af_reference()

    def update_z_max(self, z_pos_um):
        if z_pos_um > self.entry_maxZ.value():
            self.entry_maxZ.setValue(z_pos_um)

    def _reset_reflection_af_reference(self):
        if (
            self.checkbox_withReflectionAutofocus.isChecked()
            and not self.multipointController.laserAutoFocusController.set_reference()
        ):
            error_dialog("Failed to set reference for reflection autofocus. Is the laser autofocus initialized?")

    def update_z(self):
        z_mm = self.stage.get_pos().z_mm
        index = self.dropdown_location_list.currentIndex()
        self.location_list[index, 2] = z_mm
        self.scanCoordinates.region_centers[self.location_ids[index]][2] = z_mm
        self.scanCoordinates.region_fov_coordinates[self.location_ids[index]] = [
            (coord[0], coord[1], z_mm)
            for coord in self.scanCoordinates.region_fov_coordinates[self.location_ids[index]]
        ]
        location_str = f"x:{round(self.location_list[index,0],3)} mm  y:{round(self.location_list[index,1],3)} mm  z:{round(z_mm * 1000.0,3)} μm"
        self.dropdown_location_list.setItemText(index, location_str)

    def update_Nz(self):
        z_min = self.entry_minZ.value()
        z_max = self.entry_maxZ.value()
        dz = self.entry_deltaZ.value()
        nz = math.ceil((z_max - z_min) / dz) + 1
        self.entry_NZ.setValue(nz)

    def update_region_progress(self, current_fov, num_fovs):
        self._log.debug(f"Updating region progress for {current_fov=}, {num_fovs=}")
        self.progress_bar.setMaximum(num_fovs)
        self.progress_bar.setValue(current_fov)

        if self.acquisition_start_time is not None and current_fov > 0:
            elapsed_time = time.time() - self.acquisition_start_time
            Nt = self.entry_Nt.value()
            dt = self.entry_dt.value()

            # Calculate total processed FOVs and total FOVs
            processed_fovs = (
                (self.current_region - 1) * num_fovs
                + current_fov
                + self.current_time_point * self.num_regions * num_fovs
            )
            total_fovs = self.num_regions * num_fovs * Nt
            remaining_fovs = total_fovs - processed_fovs

            # Calculate ETA
            fov_per_second = processed_fovs / elapsed_time
            self.eta_seconds = (
                remaining_fovs / fov_per_second + (Nt - 1 - self.current_time_point) * dt if fov_per_second > 0 else 0
            )
            self.update_eta_display()

            # Start or restart the timer
            self.eta_timer.start(1000)  # Update every 1000 ms (1 second)

    def update_acquisition_progress(self, current_region, num_regions, current_time_point):
        self._log.debug(
            f"updating acquisition progress for {current_region=}, {num_regions=}, {current_time_point=}..."
        )
        self.current_region = current_region
        self.current_time_point = current_time_point

        if self.current_region == 1 and self.current_time_point == 0:  # First region
            self.acquisition_start_time = time.time()
            self.num_regions = num_regions

        progress_parts = []
        # Update timepoint progress if there are multiple timepoints and the timepoint has changed
        if self.entry_Nt.value() > 1:
            progress_parts.append(f"Time {current_time_point + 1}/{self.entry_Nt.value()}")

        # Update region progress if there are multiple regions
        if num_regions > 1:
            progress_parts.append(f"Region {current_region}/{num_regions}")

        # Set the progress label text, ensuring it's not empty
        progress_text = "  ".join(progress_parts)
        self.progress_label.setText(progress_text if progress_text else "Progress")

        self.progress_bar.setValue(0)

    def update_eta_display(self):
        if self.eta_seconds > 0:
            self.eta_seconds -= 1  # Decrease by 1 second
            hours, remainder = divmod(int(self.eta_seconds), 3600)
            minutes, seconds = divmod(remainder, 60)
            if hours > 0:
                eta_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            else:
                eta_str = f"{minutes:02d}:{seconds:02d}"
            self.eta_label.setText(f"{eta_str}")
        else:
            self.eta_timer.stop()
            self.eta_label.setText("00:00")

    def display_progress_bar(self, show):
        self.progress_label.setVisible(show)
        self.progress_bar.setVisible(show)
        self.eta_label.setVisible(show)
        if show:
            self.progress_bar.setValue(0)
            self.progress_label.setText("Region 0/0")
            self.eta_label.setText("--:--")
            self.acquisition_start_time = None
        else:
            self.eta_timer.stop()

    def update_fov_positions(self):
        if not self.isVisible():
            return

        if self.scanCoordinates.has_regions():
            self.scanCoordinates.clear_regions()

        for i, (x, y, z) in enumerate(self.location_list):
            region_id = self.location_ids[i]
            if self.use_overlap:
                self.scanCoordinates.add_flexible_region(
                    region_id,
                    x,
                    y,
                    z,
                    self.entry_NX.value(),
                    self.entry_NY.value(),
                    overlap_percent=self.entry_overlap.value(),
                )
            else:
                self.scanCoordinates.add_flexible_region_with_step_size(
                    region_id,
                    x,
                    y,
                    z,
                    self.entry_NX.value(),
                    self.entry_NY.value(),
                    self.entry_deltaX.value(),
                    self.entry_deltaY.value(),
                )

    def set_deltaZ(self, value):
        if self.checkbox_usePiezo.isChecked():
            deltaZ = value
        else:
            mm_per_ustep = 1.0 / self.stage.get_config().Z_AXIS.convert_real_units_to_ustep(1.0)
            deltaZ = round(value / 1000 / mm_per_ustep) * mm_per_ustep * 1000
        self.entry_deltaZ.setValue(deltaZ)
        self.multipointController.set_deltaZ(deltaZ)

    def set_saving_dir(self):
        dialog = QFileDialog()
        save_dir_base = dialog.getExistingDirectory(None, "Select Folder")
        if save_dir_base:  # Only update if user didn't cancel
            self.multipointController.set_base_path(save_dir_base)
            self.lineEdit_savingDir.setText(save_dir_base)
            self.base_path_is_set = True
            save_last_used_saving_path(save_dir_base)

    def emit_selected_channels(self):
        selected_channels = [item.text() for item in self.list_configurations.selectedItems()]
        self.signal_acquisition_channels.emit(selected_channels)

    def refresh_channel_list(self):
        """Refresh the channel list after configuration changes."""
        # Remember currently selected channels
        selected_names = [item.text() for item in self.list_configurations.selectedItems()]

        # Clear and repopulate
        self.list_configurations.blockSignals(True)
        self.list_configurations.clear()
        for config in self.multipointController.liveController.get_channels(self.objectiveStore.current_objective):
            self.list_configurations.addItem(config.name)

        # Restore selection where possible
        for i in range(self.list_configurations.count()):
            item = self.list_configurations.item(i)
            if item.text() in selected_names:
                item.setSelected(True)
        self.list_configurations.blockSignals(False)

    def toggle_acquisition(self, pressed):
        self._log.debug(f"FlexibleMultiPointWidget.toggle_acquisition, {pressed=}")
        if self.base_path_is_set == False:
            self.btn_startAcquisition.setChecked(False)
            error_dialog("Please choose base saving directory first")
            return
        if not self.list_configurations.selectedItems():  # no channel selected
            self.btn_startAcquisition.setChecked(False)
            error_dialog("Please select at least one imaging channel first")
            return
        if pressed:
            if self.multipointController.acquisition_in_progress():
                self._log.warning("Acquisition in progress or aborting, cannot start another yet.")
                self.btn_startAcquisition.setChecked(False)
                return

            # add the current location to the location list if the list is empty
            if len(self.location_list) == 0:
                self.add_location()
                self.acquisition_in_place = True

            if self.checkbox_set_z_range.isChecked():
                # Set Z-range (convert from μm to mm)
                minZ = self.entry_minZ.value() / 1000
                maxZ = self.entry_maxZ.value() / 1000
                self.multipointController.set_z_range(minZ, maxZ)
            else:
                z = self.stage.get_pos().z_mm
                dz = self.entry_deltaZ.value()
                Nz = self.entry_NZ.value()
                self.multipointController.set_z_range(z, z + dz / 1000 * (Nz - 1))

            if self.checkbox_useFocusMap.isChecked():
                self.focusMapWidget.fit_surface()
                self.multipointController.set_focus_map(self.focusMapWidget.focusMap)
            else:
                self.multipointController.set_focus_map(None)

            # Set acquisition parameters
            self.multipointController.set_deltaZ(self.entry_deltaZ.value())
            self.multipointController.set_NZ(self.entry_NZ.value())
            self.multipointController.set_deltat(self.entry_dt.value())
            self.multipointController.set_Nt(self.entry_Nt.value())
            self.multipointController.set_use_piezo(self.checkbox_usePiezo.isChecked())
            self.multipointController.set_af_flag(self.checkbox_withAutofocus.isChecked())
            self.multipointController.set_reflection_af_flag(self.checkbox_withReflectionAutofocus.isChecked())
            self.multipointController.set_base_path(self.lineEdit_savingDir.text())
            self.multipointController.set_use_fluidics(False)
            self.multipointController.set_skip_saving(self.checkbox_skipSaving.isChecked())
            self.multipointController.set_widget_type("flexible")
            self.multipointController.set_selected_configurations(
                (item.text() for item in self.list_configurations.selectedItems())
            )
            self.multipointController.start_new_experiment(self.lineEdit_experimentID.text())

            if self.checkbox_skipSaving.isChecked():
                self._log.info("Skipping disk space check - image saving is disabled")
            elif not check_space_available_with_error_dialog(self.multipointController, self._log):
                self._log.error("Failed to start acquisition.  Not enough disk space available.")
                self.btn_startAcquisition.setChecked(False)
                return

            if not check_ram_available_with_error_dialog(
                self.multipointController, self._log, performance_mode=self.performance_mode
            ):
                self._log.error("Failed to start acquisition.  Not enough RAM available.")
                self.btn_startAcquisition.setChecked(False)
                return

            # @@@ to do: add a widgetManger to enable and disable widget
            # @@@ to do: emit signal to widgetManager to disable other widgets
            self.is_current_acquisition_widget = True  # keep track of what widget started the acquisition
            self.btn_startAcquisition.setText("Stop\n Acquisition ")
            self.setEnabled_all(False)

            # emit signals
            self.signal_acquisition_started.emit(True)
            self.signal_acquisition_shape.emit(self.entry_NZ.value(), self.entry_deltaZ.value())

            # Start coordinate-based acquisition
            self.multipointController.run_acquisition()
        else:
            # This must eventually propagate through and call out acquisition_finished.
            self.multipointController.request_abort_aquisition()

    def load_last_used_locations(self):
        if self.last_used_locations is None or len(self.last_used_locations) == 0:
            return
        self.clear_only_location_list()

        for row, row_ind in zip(self.last_used_locations, self.last_used_location_ids):
            x = row[0]
            y = row[1]
            z = row[2]
            name = row_ind[0]
            if not np.any(np.all(self.location_list[:, :2] == [x, y], axis=1)):
                location_str = (
                    "x:" + str(round(x, 3)) + "mm  y:" + str(round(y, 3)) + "mm  z:" + str(round(1000 * z, 1)) + "μm"
                )
                self.dropdown_location_list.addItem(location_str)
                self.location_list = np.vstack((self.location_list, [[x, y, z]]))
                self.location_ids = np.append(self.location_ids, name)
                self.table_location_list.insertRow(self.table_location_list.rowCount())
                self.table_location_list.setItem(
                    self.table_location_list.rowCount() - 1, 0, QTableWidgetItem(str(round(x, 3)))
                )
                self.table_location_list.setItem(
                    self.table_location_list.rowCount() - 1, 1, QTableWidgetItem(str(round(y, 3)))
                )
                self.table_location_list.setItem(
                    self.table_location_list.rowCount() - 1, 2, QTableWidgetItem(str(round(z * 1000, 1)))
                )
                self.table_location_list.setItem(self.table_location_list.rowCount() - 1, 3, QTableWidgetItem(name))
                index = self.dropdown_location_list.count() - 1
                self.dropdown_location_list.setCurrentIndex(index)
                print(self.location_list)
            else:
                print("Duplicate values not added based on x and y.")
                # to-do: update z coordinate

    def add_location(self):
        # Get raw positions without rounding
        pos = self.stage.get_pos()
        x = pos.x_mm
        y = pos.y_mm
        z = pos.z_mm
        region_id = f"R{len(self.location_ids)}"

        # Check for duplicates using rounded values for comparison
        if not np.any(np.all(self.location_list[:, :2] == [round(x, 3), round(y, 3)], axis=1)):
            # Block signals to prevent triggering cell_was_changed
            self.table_location_list.blockSignals(True)
            self.dropdown_location_list.blockSignals(True)

            # Store actual values in location_list
            self.location_list = np.vstack((self.location_list, [[x, y, z]]))
            self.location_ids = np.append(self.location_ids, region_id)

            # Update both UI elements at the same time
            location_str = f"x:{round(x,3)} mm  y:{round(y,3)} mm  z:{round(z*1000,1)} μm"
            self.dropdown_location_list.addItem(location_str)
            row = self.table_location_list.rowCount()
            self.table_location_list.insertRow(row)
            self.table_location_list.setItem(row, 0, QTableWidgetItem(str(round(x, 3))))
            self.table_location_list.setItem(row, 1, QTableWidgetItem(str(round(y, 3))))
            self.table_location_list.setItem(row, 2, QTableWidgetItem(str(round(z * 1000, 1))))
            self.table_location_list.setItem(row, 3, QTableWidgetItem(region_id))

            # Store actual values in region coordinates
            if self.use_overlap:
                self.scanCoordinates.add_flexible_region(
                    region_id,
                    x,
                    y,
                    z,
                    self.entry_NX.value(),
                    self.entry_NY.value(),
                    overlap_percent=self.entry_overlap.value(),
                )
            else:
                self.scanCoordinates.add_flexible_region_with_step_size(
                    region_id,
                    x,
                    y,
                    z,
                    self.entry_NX.value(),
                    self.entry_NY.value(),
                    self.entry_deltaX.value(),
                    self.entry_deltaY.value(),
                )

            # Set the current index to the newly added location
            self.dropdown_location_list.setCurrentIndex(len(self.location_ids) - 1)
            self.table_location_list.selectRow(row)

            # Re-enable signals
            self.table_location_list.blockSignals(False)
            self.dropdown_location_list.blockSignals(False)
            print(f"Added Region: {region_id} - x={x}, y={y}, z={z}")
        else:
            print("Invalid Region: Duplicate Location")

    def remove_location(self):
        index = self.dropdown_location_list.currentIndex()
        if index >= 0:
            # Remove region ID and associated data
            region_id = self.location_ids[index]
            print(f"Removing region: {region_id}")

            # Block signals to prevent unintended UI updates
            self.table_location_list.blockSignals(True)
            self.dropdown_location_list.blockSignals(True)

            # Remove from data structures
            self.location_list = np.delete(self.location_list, index, axis=0)
            self.location_ids = np.delete(self.location_ids, index)

            # Remove from both UI elements
            self.dropdown_location_list.removeItem(index)
            self.table_location_list.removeRow(index)

            # Remove scanCoordinates dictionaries and remove region overlay
            self.scanCoordinates.region_centers.pop(region_id, None)
            self.navigationViewer.deregister_fovs_from_image(
                self.scanCoordinates.region_fov_coordinates.pop(region_id, [])
            )

            """
            # Reindex remaining regions and update UI
            for i in range(index, len(self.location_ids)):
                old_id = self.location_ids[i]
                new_id = f"R{i}"
                self.location_ids[i] = new_id

                # Update dictionaries
                self.scanCoordinates.region_centers[new_id] = self.scanCoordinates.region_centers.pop(old_id, None)
                self.scanCoordinates.region_fov_coordinates[new_id] = self.scanCoordinates.region_fov_coordinates.pop(
                    old_id, []
                )

                # Update UI with new ID and coordinates
                x, y, z = self.location_list[i]
                location_str = f"x:{round(x, 3)} mm  y:{round(y, 3)} mm  z:{round(z * 1000, 1)} μm"
                self.dropdown_location_list.setItemText(i, location_str)
                self.table_location_list.setItem(i, 3, QTableWidgetItem(new_id))
            """

            # Clear overlay if no locations remain
            if len(self.location_list) == 0:
                self.navigationViewer.clear_overlay()

            print(f"Remaining location IDs: {self.location_ids}")
            for region_id, fov_coords in self.scanCoordinates.region_fov_coordinates.items():
                self.navigationViewer.register_fovs_to_image(fov_coords)

            # Re-enable signals
            self.table_location_list.blockSignals(False)
            self.dropdown_location_list.blockSignals(False)

    def next(self):
        index = self.dropdown_location_list.currentIndex()
        # max_index = self.dropdown_location_list.count() - 1
        # index = min(index + 1, max_index)
        num_regions = self.dropdown_location_list.count()
        if num_regions <= 0:
            self._log.error("Cannot move to next location, because there are no locations in the list")
            return

        index = (index + 1) % num_regions
        self.dropdown_location_list.setCurrentIndex(index)
        x = self.location_list[index, 0]
        y = self.location_list[index, 1]
        z = self.location_list[index, 2]
        self.stage.move_x_to(x)
        self.stage.move_y_to(y)
        self.stage.move_z_to(z)

    def previous(self):
        index = self.dropdown_location_list.currentIndex()
        index = max(index - 1, 0)
        self.dropdown_location_list.setCurrentIndex(index)
        x = self.location_list[index, 0]
        y = self.location_list[index, 1]
        z = self.location_list[index, 2]
        self.stage.move_x_to(x)
        self.stage.move_y_to(y)
        self.stage.move_z_to(z)

    def clear(self):
        self.location_list = np.empty((0, 3), dtype=float)
        self.location_ids = np.empty((0,), dtype="<U20")
        self.scanCoordinates.clear_regions()
        self.dropdown_location_list.clear()
        self.table_location_list.setRowCount(0)
        self.navigationViewer.clear_overlay()

        self._log.info("Cleared all locations and overlays.")

    def clear_only_location_list(self):
        self.location_list = np.empty((0, 3), dtype=float)
        self.location_ids = np.empty((0,), dtype="<U20")
        self.dropdown_location_list.clear()
        self.table_location_list.setRowCount(0)

    def go_to(self, index):
        if index != -1:
            if index < len(self.location_list):  # to avoid giving errors when adding new points
                x = self.location_list[index, 0]
                y = self.location_list[index, 1]
                z = self.location_list[index, 2]
                self.stage.move_x_to(x)
                self.stage.move_y_to(y)
                self.stage.move_z_to(z)
                self.table_location_list.selectRow(index)

    def cell_was_clicked(self, row, column):
        self.dropdown_location_list.setCurrentIndex(row)

    def cell_was_changed(self, row, column):
        # Get region ID
        region_id = self.location_ids[row]

        # Clear all FOVs for this region
        if region_id in self.scanCoordinates.region_fov_coordinates.keys():
            self.navigationViewer.deregister_fovs_from_image(self.scanCoordinates.region_fov_coordinates[region_id])

        # Handle the changed value
        val_edit = self.table_location_list.item(row, column).text()

        if column < 2:  # X or Y coordinate changed
            self.location_list[row, column] = float(val_edit)
            x, y, z = self.location_list[row]

            # Update region coordinates and FOVs for new position
            if self.use_overlap:
                self.scanCoordinates.add_flexible_region(
                    region_id,
                    x,
                    y,
                    z,
                    self.entry_NX.value(),
                    self.entry_NY.value(),
                    overlap_percent=self.entry_overlap.value(),
                )
            else:
                self.scanCoordinates.add_flexible_region_with_step_size(
                    region_id,
                    x,
                    y,
                    z,
                    self.entry_NX.value(),
                    self.entry_NY.value(),
                    self.entry_deltaX.value(),
                    self.entry_deltaY.value(),
                )

        elif column == 2:  # Z coordinate changed
            z = float(val_edit) / 1000
            self.location_list[row, 2] = z
            self.scanCoordinates.region_centers[region_id][2] = z
        else:  # ID changed
            new_id = val_edit
            self.location_ids[row] = new_id
            # Update dictionary keys
            if region_id in self.scanCoordinates.region_centers:
                self.scanCoordinates.region_centers[new_id] = self.scanCoordinates.region_centers.pop(region_id)
            if region_id in self.scanCoordinates.region_fov_coordinates:
                self.scanCoordinates.region_fov_coordinates[new_id] = self.scanCoordinates.region_fov_coordinates.pop(
                    region_id
                )

        # Update UI
        location_str = f"x:{round(self.location_list[row,0],3)} mm  y:{round(self.location_list[row,1],3)} mm  z:{round(1000*self.location_list[row,2],3)} μm"
        self.dropdown_location_list.setItemText(row, location_str)
        self.go_to(row)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_A and event.modifiers() == Qt.ControlModifier:
            self.add_location()
        else:
            super().keyPressEvent(event)

    def update_location_z_level(self, index, z_mm):
        self.table_location_list.blockSignals(True)
        self.dropdown_location_list.blockSignals(True)

        self.location_list[index, 2] = z_mm
        location_str = (
            "x:"
            + str(round(self.location_list[index, 0], 3))
            + "mm  y:"
            + str(round(self.location_list[index, 1], 3))
            + "mm  z:"
            + str(round(1000 * z_mm, 1))
            + "μm"
        )
        self.dropdown_location_list.setItemText(index, location_str)
        if self.table_location_list.rowCount() > index:
            self.table_location_list.setItem(index, 2, QTableWidgetItem(str(round(1000 * z_mm, 1))))

        self.table_location_list.blockSignals(False)
        self.dropdown_location_list.blockSignals(False)

    def export_location_list(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "Export Location List", "", "CSV Files (*.csv);;All Files (*)")
        if file_path:
            location_list_df = pd.DataFrame(self.location_list, columns=["x (mm)", "y (mm)", "z (mm)"])
            location_list_df["ID"] = self.location_ids
            location_list_df["i"] = 0
            location_list_df["j"] = 0
            location_list_df["k"] = 0
            location_list_df.to_csv(file_path, index=False, header=True)

    def import_location_list(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Import Location List", "", "CSV Files (*.csv);;All Files (*)")
        if file_path:
            location_list_df = pd.read_csv(file_path)
            location_list_df_relevant = None
            try:
                location_list_df_relevant = location_list_df[["x (mm)", "y (mm)", "z (mm)"]]
            except KeyError:
                self._log.error("Improperly formatted location list being imported")
                return
            if "ID" in location_list_df.columns:
                location_list_df_relevant["ID"] = location_list_df["ID"].astype(str)
            else:
                location_list_df_relevant["ID"] = "None"
            self.clear_only_location_list()

            self.table_location_list.blockSignals(True)
            self.dropdown_location_list.blockSignals(True)
            for index, row in location_list_df_relevant.iterrows():
                x = row["x (mm)"]
                y = row["y (mm)"]
                z = row["z (mm)"]
                region_id = row["ID"]
                if not np.any(np.all(self.location_list[:, :2] == [x, y], axis=1)):
                    location_str = (
                        "x:"
                        + str(round(x, 3))
                        + "mm  y:"
                        + str(round(y, 3))
                        + "mm  z:"
                        + str(round(1000.0 * z, 3))
                        + "μm"
                    )
                    self.dropdown_location_list.addItem(location_str)
                    index = self.dropdown_location_list.count() - 1
                    self.dropdown_location_list.setCurrentIndex(index)
                    self.location_list = np.vstack((self.location_list, [[x, y, z]]))
                    self.location_ids = np.append(self.location_ids, region_id)
                    self.table_location_list.insertRow(self.table_location_list.rowCount())
                    self.table_location_list.setItem(
                        self.table_location_list.rowCount() - 1, 0, QTableWidgetItem(str(round(x, 3)))
                    )
                    self.table_location_list.setItem(
                        self.table_location_list.rowCount() - 1, 1, QTableWidgetItem(str(round(y, 3)))
                    )
                    self.table_location_list.setItem(
                        self.table_location_list.rowCount() - 1, 2, QTableWidgetItem(str(round(1000 * z, 1)))
                    )
                    self.table_location_list.setItem(
                        self.table_location_list.rowCount() - 1, 3, QTableWidgetItem(region_id)
                    )
                    if self.use_overlap:
                        self.scanCoordinates.add_flexible_region(
                            region_id,
                            x,
                            y,
                            z,
                            self.entry_NX.value(),
                            self.entry_NY.value(),
                            overlap_percent=self.entry_overlap.value(),
                        )
                    else:
                        self.scanCoordinates.add_flexible_region_with_step_size(
                            region_id,
                            x,
                            y,
                            z,
                            self.entry_NX.value(),
                            self.entry_NY.value(),
                            self.entry_deltaX.value(),
                            self.entry_deltaY.value(),
                        )
                else:
                    self._log.warning("Duplicate values not added based on x and y.")
            self.table_location_list.blockSignals(False)
            self.dropdown_location_list.blockSignals(False)
            self._log.debug(self.location_list)

    def on_snap_images(self):
        if not self.list_configurations.selectedItems():
            QMessageBox.warning(self, "Warning", "Please select at least one imaging channel")
            return

        # Set the selected channels for acquisition
        self.multipointController.set_selected_configurations(
            [item.text() for item in self.list_configurations.selectedItems()]
        )
        # Set the acquisition parameters
        self.multipointController.set_deltaZ(0)
        self.multipointController.set_NZ(1)
        self.multipointController.set_deltat(0)
        self.multipointController.set_Nt(1)
        self.multipointController.set_use_piezo(False)
        self.multipointController.set_af_flag(False)
        self.multipointController.set_reflection_af_flag(False)
        self.multipointController.set_use_fluidics(False)

        z = self.stage.get_pos().z_mm
        self.multipointController.set_z_range(z, z)

        # Start the acquisition process for the single FOV
        self.multipointController.start_new_experiment("snapped images" + self.lineEdit_experimentID.text())
        self.multipointController.run_acquisition(acquire_current_fov=True)

    def acquisition_is_finished(self):
        self._log.debug(
            f"In FlexibleMultiPointWidget, got acquisition_is_finished with {self.is_current_acquisition_widget=}"
        )

        if not self.is_current_acquisition_widget:
            return  # Skip if this wasn't the widget that started acquisition

        if not self.acquisition_in_place:
            self.last_used_locations = self.location_list.copy()
            self.last_used_location_ids = self.location_ids.copy()
        else:
            self.clear_only_location_list()
            self.acquisition_in_place = False

        self.signal_acquisition_started.emit(False)
        self.btn_startAcquisition.setChecked(False)
        self.btn_startAcquisition.setText("Start\n Acquisition ")
        self.setEnabled_all(True)
        self.is_current_acquisition_widget = False

    def setEnabled_all(self, enabled, exclude_btn_startAcquisition=True):
        self.btn_setSavingDir.setEnabled(enabled)
        self.lineEdit_savingDir.setEnabled(enabled)
        self.lineEdit_experimentID.setEnabled(enabled)
        self.entry_NX.setEnabled(enabled)
        self.entry_NY.setEnabled(enabled)
        self.entry_deltaZ.setEnabled(enabled)
        self.entry_NZ.setEnabled(enabled)
        self.entry_dt.setEnabled(enabled)
        self.entry_Nt.setEnabled(enabled)
        if not self.use_overlap:
            self.entry_deltaX.setEnabled(enabled)
            self.entry_deltaY.setEnabled(enabled)
        else:
            self.entry_overlap.setEnabled(enabled)
        self.list_configurations.setEnabled(enabled)
        self.checkbox_genAFMap.setEnabled(enabled)
        self.checkbox_useFocusMap.setEnabled(enabled)
        self.checkbox_withAutofocus.setEnabled(enabled)
        self.checkbox_withReflectionAutofocus.setEnabled(enabled)
        self.checkbox_stitchOutput.setEnabled(enabled)
        self.checkbox_set_z_range.setEnabled(enabled)

        if exclude_btn_startAcquisition is not True:
            self.btn_startAcquisition.setEnabled(enabled)

    def disable_the_start_aquisition_button(self):
        self.btn_startAcquisition.setEnabled(False)

    def enable_the_start_aquisition_button(self):
        self.btn_startAcquisition.setEnabled(True)

    def set_performance_mode(self, enabled):
        self.performance_mode = enabled

    # ========== Drag-and-Drop for Loading Acquisition YAML ==========
    # Uses AcquisitionYAMLDropMixin for drag-drop handling
    def dropEvent(self, event):
        if hasattr(self, "_original_stylesheet"):
            self.setStyleSheet(self._original_stylesheet)

        if not event.mimeData().hasUrls():
            event.ignore()
            return

        paths = [url.toLocalFile() for url in event.mimeData().urls()]
        yaml_paths = [self._resolve_yaml_path(path) for path in paths if self._is_valid_yaml_drop(path)]
        if not yaml_paths:
            event.ignore()
            return

        QMessageBox.information(
            self,
            "Not Supported",
            "Flexible multipoint YAML drag-and-drop is not supported yet.",
        )
        event.acceptProposedAction()

    def _get_expected_widget_type(self) -> str:
        """Return the expected widget_type for this widget."""
        return "flexible"

    def _apply_yaml_settings(self, yaml_data):
        """Apply parsed YAML settings to widget controls."""
        # Collect widgets to block signals
        widgets_to_block = [
            self.entry_NX,
            self.entry_NY,
            self.entry_NZ,
            self.entry_deltaZ,
            self.entry_Nt,
            self.entry_dt,
            self.list_configurations,
            self.checkbox_withAutofocus,
            self.checkbox_withReflectionAutofocus,
            self.checkbox_usePiezo,
        ]

        # Add optional widgets if they exist
        if hasattr(self, "entry_deltaX"):
            widgets_to_block.append(self.entry_deltaX)
        if hasattr(self, "entry_deltaY"):
            widgets_to_block.append(self.entry_deltaY)
        if hasattr(self, "entry_overlap"):
            widgets_to_block.append(self.entry_overlap)

        for widget in widgets_to_block:
            widget.blockSignals(True)

        try:
            # Grid settings (flexible specific)
            self.entry_NX.setValue(yaml_data.nx)
            self.entry_NY.setValue(yaml_data.ny)
            if hasattr(self, "entry_deltaX") and not self.use_overlap:
                self.entry_deltaX.setValue(yaml_data.delta_x_mm)
                self.entry_deltaY.setValue(yaml_data.delta_y_mm)
            if hasattr(self, "entry_overlap") and self.use_overlap:
                self.entry_overlap.setValue(yaml_data.overlap_percent)

            # Z-stack settings
            self.entry_NZ.setValue(yaml_data.nz)
            self.entry_deltaZ.setValue(yaml_data.delta_z_um)

            # Piezo setting
            self.checkbox_usePiezo.setChecked(yaml_data.use_piezo)

            # Time series settings
            self.entry_Nt.setValue(yaml_data.nt)
            self.entry_dt.setValue(yaml_data.delta_t_s)

            # Channels
            if yaml_data.channel_names:
                self.list_configurations.clearSelection()
                for i in range(self.list_configurations.count()):
                    item = self.list_configurations.item(i)
                    if item.text() in yaml_data.channel_names:
                        item.setSelected(True)

            # Autofocus
            self.checkbox_withAutofocus.setChecked(yaml_data.contrast_af)
            self.checkbox_withReflectionAutofocus.setChecked(yaml_data.laser_af)

            # Load positions if present
            if yaml_data.flexible_positions:
                self._load_positions(yaml_data.flexible_positions)

        finally:
            # Unblock all signals
            for widget in widgets_to_block:
                widget.blockSignals(False)

            # Update FOV positions to reflect new NX, NY, delta values
            self.update_fov_positions()

    def _load_positions(self, positions):
        """Load positions from YAML into the location list."""
        # Clear existing locations
        self.clear_only_location_list()

        for pos in positions:
            name = pos.get("name", f"R{len(self.location_ids)}")
            center = pos.get("center_mm", [0, 0, 0])

            if len(center) >= 3:
                x, y, z = center[0], center[1], center[2]
            elif len(center) == 2:
                x, y = center[0], center[1]
                # Get current stage Z if available, otherwise use 0
                stage = getattr(self, "stage", None)
                if stage is not None:
                    try:
                        z = stage.get_pos().z_mm
                    except (AttributeError, Exception):
                        z = 0.0
                else:
                    z = 0.0
            else:
                continue

            # Add to data structures
            self.location_list = np.vstack((self.location_list, [[x, y, z]]))
            self.location_ids = np.append(self.location_ids, name)

            # Update UI - dropdown
            location_str = f"x:{round(x, 3)} mm  y:{round(y, 3)} mm  z:{round(z * 1000, 1)} um"
            self.dropdown_location_list.addItem(location_str)

            # Update UI - table
            row = self.table_location_list.rowCount()
            self.table_location_list.insertRow(row)
            self.table_location_list.setItem(row, 0, QTableWidgetItem(str(round(x, 3))))
            self.table_location_list.setItem(row, 1, QTableWidgetItem(str(round(y, 3))))
            self.table_location_list.setItem(row, 2, QTableWidgetItem(str(round(z * 1000, 1))))
            self.table_location_list.setItem(row, 3, QTableWidgetItem(name))

            # Add to scan coordinates
            if self.use_overlap:
                self.scanCoordinates.add_flexible_region(
                    name,
                    x,
                    y,
                    z,
                    self.entry_NX.value(),
                    self.entry_NY.value(),
                    overlap_percent=self.entry_overlap.value(),
                )
            else:
                self.scanCoordinates.add_flexible_region_with_step_size(
                    name,
                    x,
                    y,
                    z,
                    self.entry_NX.value(),
                    self.entry_NY.value(),
                    self.entry_deltaX.value(),
                    self.entry_deltaY.value(),
                )


class WellplateMultiPointWidget(AcquisitionYAMLDropMixin, QFrame):

    signal_acquisition_started = Signal(bool)
    signal_acquisition_channels = Signal(list)
    signal_acquisition_shape = Signal(int, float)  # acquisition Nz, dz
    signal_manual_shape_mode = Signal(bool)  # enable manual shape layer on mosaic display
    signal_toggle_live_scan_grid = Signal(bool)  # enable/disable live scan grid
    # Signal to set acquisition running state from any thread (used by TCP server)
    signal_set_acquisition_running = Signal(bool, int, float)  # is_running, nz, delta_z_um

    def __init__(
        self,
        stage: AbstractStage,
        navigationViewer,
        multipointController,
        liveController,
        objectiveStore,
        scanCoordinates,
        focusMapWidget=None,
        napariMosaicWidget=None,
        tab_widget: Optional[QTabWidget] = None,
        well_selection_widget: Optional[WellSelectionWidget] = None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(True)  # Enable drag-and-drop for loading acquisition YAML
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.stage = stage
        self.navigationViewer = navigationViewer
        self.multipointController = multipointController
        self.liveController = liveController
        self.objectiveStore = objectiveStore
        self.scanCoordinates = scanCoordinates
        self.focusMapWidget = focusMapWidget
        self.napariMosaicWidget = napariMosaicWidget
        self.performance_mode = False
        self.tab_widget: Optional[QTabWidget] = tab_widget
        self.well_selection_widget: Optional[WellSelectionWidget] = well_selection_widget
        self.base_path_is_set = False
        self.well_selected = False
        self.num_regions = 0
        self.acquisition_start_time = None
        self.manual_shape = None
        self.eta_seconds = 0
        self.is_current_acquisition_widget = False

        self.shapes_mm = None

        # TODO (hl): these along with update_live_coordinates need to move out of this class
        self._last_update_time = 0
        self._last_x_mm = None
        self._last_y_mm = None

        # Add state tracking for coordinates
        self.has_loaded_coordinates = False

        # Cache for loaded coordinates dataframe (restored when switching back to Load Coordinates mode)
        self.cached_loaded_coordinates_df = None
        self.cached_loaded_file_path = None

        # Add state tracking for Z parameters
        self.stored_z_params = {"dz": None, "nz": None, "z_min": None, "z_max": None, "z_mode": "From Bottom"}

        # Add state tracking for Time parameters
        self.stored_time_params = {"dt": None, "nt": None}

        # Add state tracking for XY mode parameters
        self.stored_xy_params = {
            "Current Position": {"scan_size": None, "coverage": None, "scan_shape": None},
            "Select Wells": {"scan_size": None, "coverage": None, "scan_shape": None},
        }

        # Track previous XY mode for parameter storage
        self._previous_xy_mode = None

        # Track XY mode before unchecking, for restoration when re-checking
        self._xy_mode_before_uncheck = None

        # Track loading from cache
        self._loading_from_cache = False

        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        self.set_default_scan_size()

    def add_components(self):
        self.entry_well_coverage = QDoubleSpinBox()
        self.entry_well_coverage.setKeyboardTracking(False)
        self.entry_well_coverage.setRange(1, 999.99)
        self.entry_well_coverage.setValue(100)
        self.entry_well_coverage.setSuffix("%")
        self.entry_well_coverage.setDecimals(0)
        btn_width = self.entry_well_coverage.sizeHint().width()

        self.btn_setSavingDir = QPushButton("Browse")
        self.btn_setSavingDir.setDefault(False)
        self.btn_setSavingDir.setIcon(QIcon("icon/folder.png"))
        self.btn_setSavingDir.setFixedWidth(btn_width)

        self.lineEdit_savingDir = QLineEdit()
        last_path = get_last_used_saving_path()
        self.lineEdit_savingDir.setText(last_path)
        self.multipointController.set_base_path(last_path)
        self.base_path_is_set = True

        self.lineEdit_experimentID = QLineEdit()

        # Update scan size entry
        self.entry_scan_size = QDoubleSpinBox()
        self.entry_scan_size.setKeyboardTracking(False)
        self.entry_scan_size.setRange(0.1, 100)
        self.entry_scan_size.setValue(0.1)
        self.entry_scan_size.setSuffix(" mm")

        self.entry_overlap = QDoubleSpinBox()
        self.entry_overlap.setKeyboardTracking(False)
        self.entry_overlap.setRange(0, 99)
        self.entry_overlap.setValue(10)
        self.entry_overlap.setSuffix("%")
        self.entry_overlap.setFixedWidth(btn_width)

        # Add z-min and z-max entries
        self.entry_minZ = QDoubleSpinBox()
        self.entry_minZ.setKeyboardTracking(False)
        self.entry_minZ.setMinimum(SOFTWARE_POS_LIMIT.Z_NEGATIVE * 1000)  # Convert to μm
        self.entry_minZ.setMaximum(SOFTWARE_POS_LIMIT.Z_POSITIVE * 1000)  # Convert to μm
        self.entry_minZ.setSingleStep(1)  # Step by 1 μm
        self.entry_minZ.setValue(self.stage.get_pos().z_mm * 1000)  # Set to minimum
        self.entry_minZ.setSuffix(" μm")
        # self.entry_minZ.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.set_minZ_button = QPushButton("Set Z-min")
        self.set_minZ_button.clicked.connect(self.set_z_min)

        self.goto_minZ_button = QPushButton("Go To")
        self.goto_minZ_button.clicked.connect(self.goto_z_min)
        self.goto_minZ_button.setFixedWidth(50)

        self.entry_maxZ = QDoubleSpinBox()
        self.entry_maxZ.setKeyboardTracking(False)
        self.entry_maxZ.setMinimum(SOFTWARE_POS_LIMIT.Z_NEGATIVE * 1000)  # Convert to μm
        self.entry_maxZ.setMaximum(SOFTWARE_POS_LIMIT.Z_POSITIVE * 1000)  # Convert to μm
        self.entry_maxZ.setSingleStep(1)  # Step by 1 μm
        self.entry_maxZ.setValue(self.stage.get_pos().z_mm * 1000)  # Set to maximum
        self.entry_maxZ.setSuffix(" μm")
        # self.entry_maxZ.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.set_maxZ_button = QPushButton("Set Z-max")
        self.set_maxZ_button.clicked.connect(self.set_z_max)

        self.goto_maxZ_button = QPushButton("Go To")
        self.goto_maxZ_button.clicked.connect(self.goto_z_max)
        self.goto_maxZ_button.setFixedWidth(50)

        self.entry_deltaZ = QDoubleSpinBox()
        self.entry_deltaZ.setKeyboardTracking(False)
        self.entry_deltaZ.setMinimum(0)
        self.entry_deltaZ.setMaximum(1000)
        self.entry_deltaZ.setSingleStep(0.1)
        self.entry_deltaZ.setValue(Acquisition.DZ)
        self.entry_deltaZ.setDecimals(3)
        # self.entry_deltaZ.setEnabled(False)
        self.entry_deltaZ.setSuffix(" μm")
        self.entry_deltaZ.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.entry_NZ = QSpinBox()
        self.entry_NZ.setMinimum(1)
        self.entry_NZ.setMaximum(2000)
        self.entry_NZ.setSingleStep(1)
        self.entry_NZ.setValue(1)
        self.entry_NZ.setEnabled(False)

        self.entry_dt = QDoubleSpinBox()
        self.entry_dt.setKeyboardTracking(False)
        self.entry_dt.setMinimum(0)
        self.entry_dt.setMaximum(24 * 3600)
        self.entry_dt.setSingleStep(1)
        self.entry_dt.setValue(0)
        self.entry_dt.setSuffix(" s")
        self.entry_dt.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.entry_Nt = QSpinBox()
        self.entry_Nt.setMinimum(1)
        self.entry_Nt.setMaximum(5000)
        self.entry_Nt.setSingleStep(1)
        self.entry_Nt.setValue(1)

        self.combobox_z_stack = QComboBox()
        self.combobox_z_stack.addItems(["From Bottom (Z-min)", "From Center", "From Top (Z-max)"])
        self.combobox_z_stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.list_configurations = QListWidget()
        for microscope_configuration in self.liveController.get_channels(self.objectiveStore.current_objective):
            self.list_configurations.addItems([microscope_configuration.name])
        self.list_configurations.setSelectionMode(QAbstractItemView.MultiSelection)

        # Add a combo box for shape selection
        self.combobox_shape = QComboBox()
        self.combobox_shape.addItems(["Square", "Circle", "Rectangle"])
        self.combobox_shape.setFixedWidth(btn_width)
        # self.combobox_shape.currentTextChanged.connect(self.on_shape_changed)

        self.btn_save_scan_coordinates = QPushButton("Save Coordinates")
        self.btn_load_scan_coordinates = QPushButton("Load New Coords")

        # Add text area for showing loaded file path
        self.text_loaded_coordinates = QLineEdit()
        self.text_loaded_coordinates.setReadOnly(True)
        self.text_loaded_coordinates.setPlaceholderText("No file loaded")

        self.checkbox_genAFMap = QCheckBox("Generate Focus Map")
        self.checkbox_genAFMap.setChecked(False)

        self.checkbox_useFocusMap = QCheckBox("Use Focus Map")
        self.checkbox_useFocusMap.setChecked(False)

        self.checkbox_withAutofocus = QCheckBox("Contrast AF")
        self.checkbox_withAutofocus.setChecked(MULTIPOINT_CONTRAST_AUTOFOCUS_ENABLE_BY_DEFAULT)
        self.multipointController.set_af_flag(MULTIPOINT_CONTRAST_AUTOFOCUS_ENABLE_BY_DEFAULT)

        self.checkbox_withReflectionAutofocus = QCheckBox("Laser AF")
        self.checkbox_withReflectionAutofocus.setChecked(MULTIPOINT_REFLECTION_AUTOFOCUS_ENABLE_BY_DEFAULT)
        self.multipointController.set_reflection_af_flag(MULTIPOINT_REFLECTION_AUTOFOCUS_ENABLE_BY_DEFAULT)

        self.checkbox_usePiezo = QCheckBox("Piezo Z-Stack")
        self.checkbox_usePiezo.setChecked(MULTIPOINT_USE_PIEZO_FOR_ZSTACKS)

        self.checkbox_stitchOutput = QCheckBox("Stitch Scans")
        self.checkbox_stitchOutput.setChecked(False)

        self.checkbox_skipSaving = QCheckBox("Skip Saving")
        self.checkbox_skipSaving.setChecked(False)

        self.btn_startAcquisition = QPushButton("Start\n Acquisition ")
        self.btn_startAcquisition.setStyleSheet("background-color: #C2C2FF")
        self.btn_startAcquisition.setCheckable(True)
        self.btn_startAcquisition.setChecked(False)
        # self.btn_startAcquisition.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.progress_label = QLabel("Region -/-")
        self.progress_bar = QProgressBar()
        self.eta_label = QLabel("--:--:--")
        self.progress_bar.setVisible(False)
        self.progress_label.setVisible(False)
        self.eta_label.setVisible(False)
        self.eta_timer = QTimer()

        # Add snap images button
        self.btn_snap_images = QPushButton("Snap Images")
        self.btn_snap_images.clicked.connect(self.on_snap_images)
        self.btn_snap_images.setCheckable(False)
        self.btn_snap_images.setChecked(False)

        # Add acquisition tabs with checkboxes and frames
        # XY Tab
        self.xy_frame = QFrame()

        self.checkbox_xy = QCheckBox("XY")
        self.checkbox_xy.setChecked(True)

        self.combobox_xy_mode = QComboBox()
        self.combobox_xy_mode.addItems(["Current Position", "Select Wells", "Manual", "Load Coordinates"])
        self.combobox_xy_mode.setEnabled(True)  # Initially enabled since XY is checked
        # disable manual mode on init (before mosaic is loaded) - identify the index of the manual mode by name
        _manual_index = self.combobox_xy_mode.findText("Manual")
        self.combobox_xy_mode.model().item(_manual_index).setEnabled(False)

        xy_layout = QHBoxLayout()
        xy_layout.setContentsMargins(8, 4, 8, 4)
        xy_layout.addWidget(self.checkbox_xy)
        xy_layout.addWidget(self.combobox_xy_mode)
        self.xy_frame.setLayout(xy_layout)

        # Z Tab
        self.z_frame = QFrame()

        self.checkbox_z = QCheckBox("Z")
        self.checkbox_z.setChecked(False)

        self.combobox_z_mode = QComboBox()
        self.combobox_z_mode.addItems(["From Bottom", "Set Range"])
        self.combobox_z_mode.setEnabled(False)  # Initially disabled since Z is unchecked

        z_layout = QHBoxLayout()
        z_layout.setContentsMargins(8, 4, 8, 4)
        z_layout.addWidget(self.checkbox_z)
        z_layout.addWidget(self.combobox_z_mode)
        self.z_frame.setLayout(z_layout)

        # Time Tab
        self.time_frame = QFrame()

        self.checkbox_time = QCheckBox("Time")
        self.checkbox_time.setChecked(False)

        time_layout = QHBoxLayout()
        time_layout.setContentsMargins(8, 4, 8, 4)
        time_layout.addWidget(self.checkbox_time)
        time_layout.addStretch()  # Fill horizontal space
        self.time_frame.setLayout(time_layout)

        # Main layout
        main_layout = QVBoxLayout()
        self.setLayout(main_layout)

        #  Saving Path
        saving_path_layout = QHBoxLayout()
        saving_path_layout.addWidget(QLabel("Saving Path"))
        saving_path_layout.addWidget(self.lineEdit_savingDir)
        saving_path_layout.addWidget(self.btn_setSavingDir)
        main_layout.addLayout(saving_path_layout)

        # Experiment ID
        row_1_layout = QHBoxLayout()
        row_1_layout.addWidget(QLabel("Experiment ID"))
        row_1_layout.addWidget(self.lineEdit_experimentID)
        main_layout.addLayout(row_1_layout)

        # Acquisition tabs row
        tabs_layout = QHBoxLayout()
        tabs_layout.setSpacing(4)  # Small spacing between frames
        tabs_layout.addWidget(self.xy_frame, 2)  # Give XY frame more space (weight 2)
        tabs_layout.addWidget(self.z_frame, 1)  # Z frame gets weight 1
        tabs_layout.addWidget(self.time_frame, 1)  # Time frame gets weight 1
        main_layout.addLayout(tabs_layout)

        # Scan Shape, FOV overlap, and Save / Load Scan Coordinates
        # Frame for orange background
        self.xy_controls_frame = QFrame()

        self.row_2_layout = QGridLayout()
        self.row_2_layout.setContentsMargins(4, 2, 4, 2)
        self.scan_shape_label = QLabel("Scan Shape")
        self.scan_size_label = QLabel("Scan Size")
        self.coverage_label = QLabel("Coverage")
        self.fov_overlap_label = QLabel("FOV Overlap")

        self.row_2_layout.addWidget(self.scan_shape_label, 0, 0)
        self.row_2_layout.addWidget(self.combobox_shape, 0, 1)
        self.row_2_layout.addWidget(self.scan_size_label, 0, 2)
        self.row_2_layout.addWidget(self.entry_scan_size, 0, 3)
        self.row_2_layout.addWidget(self.coverage_label, 0, 4)
        self.row_2_layout.addWidget(self.entry_well_coverage, 0, 5)
        self.row_2_layout.addWidget(self.fov_overlap_label, 1, 0)
        self.row_2_layout.addWidget(self.entry_overlap, 1, 1)
        self.row_2_layout.addWidget(self.btn_save_scan_coordinates, 1, 2, 1, 4)

        self.xy_controls_frame.setLayout(self.row_2_layout)
        main_layout.addWidget(self.xy_controls_frame)

        # Frame for Load Coordinates UI (initially hidden)
        self.load_coordinates_frame = QFrame()
        load_coords_layout = QHBoxLayout()
        load_coords_layout.setContentsMargins(4, 2, 4, 2)
        load_coords_layout.addWidget(self.btn_load_scan_coordinates)
        load_coords_layout.addWidget(self.text_loaded_coordinates)
        self.load_coordinates_frame.setLayout(load_coords_layout)
        self.load_coordinates_frame.setVisible(False)  # Initially hidden
        main_layout.addWidget(self.load_coordinates_frame)

        grid = QGridLayout()

        # Z controls frame for dz/Nz (left half of row 1) with blue background
        self.z_controls_dz_frame = QFrame()

        self.dz_layout = QHBoxLayout()
        self.dz_layout.setContentsMargins(4, 2, 4, 2)
        self.dz_layout.addWidget(QLabel("dz"))
        self.dz_layout.addWidget(self.entry_deltaZ)
        self.dz_layout.addWidget(QLabel("Nz"))
        self.dz_layout.addWidget(self.entry_NZ)

        self.z_controls_dz_frame.setLayout(self.dz_layout)
        grid.addWidget(self.z_controls_dz_frame, 0, 0)

        # Time controls frame with green background
        self.time_controls_frame = QFrame()

        # dt and Nt
        self.dt_layout = QHBoxLayout()
        self.dt_layout.setContentsMargins(4, 2, 4, 2)
        self.dt_layout.addWidget(QLabel("dt"))
        self.dt_layout.addWidget(self.entry_dt)
        self.dt_layout.addWidget(QLabel("Nt"))
        self.dt_layout.addWidget(self.entry_Nt)

        self.time_controls_frame.setLayout(self.dt_layout)
        grid.addWidget(self.time_controls_frame, 0, 2)

        # Create informational labels for when modes are not selected
        self.z_not_selected_label = QLabel("Z stack not selected")
        self.z_not_selected_label.setAlignment(Qt.AlignCenter)
        self.z_not_selected_label.setStyleSheet(
            """
            QLabel {
                background-color: palette(button);
                border: 1px solid palette(mid);
                border-radius: 4px;
                padding: 0px;
                color: palette(text);
            }
        """
        )
        self.z_not_selected_label.setVisible(False)

        self.time_not_selected_label = QLabel("Time lapse not selected")
        self.time_not_selected_label.setAlignment(Qt.AlignCenter)
        self.time_not_selected_label.setStyleSheet(
            """
            QLabel {
                background-color: palette(button);
                border: 1px solid palette(mid);
                border-radius: 4px;
                padding: 0px;
                color: palette(text);
            }
        """
        )
        self.time_not_selected_label.setVisible(False)

        # Z controls frame for Z-min and Z-max (full row 2) with blue background
        self.z_controls_range_frame = QFrame()
        z_range_layout = QHBoxLayout()
        z_range_layout.setContentsMargins(4, 2, 4, 2)

        # Z-min
        self.z_min_layout = QHBoxLayout()
        self.z_min_layout.addWidget(self.entry_minZ)
        self.z_min_layout.addWidget(self.set_minZ_button)
        self.z_min_layout.addWidget(self.goto_minZ_button)
        z_range_layout.addLayout(self.z_min_layout)

        # Spacer to maintain original spacing between Z-min and Z-max
        z_range_layout.addStretch()

        # Z-max
        self.z_max_layout = QHBoxLayout()
        self.z_max_layout.addWidget(self.entry_maxZ)
        self.z_max_layout.addWidget(self.set_maxZ_button)
        self.z_max_layout.addWidget(self.goto_maxZ_button)
        z_range_layout.addLayout(self.z_max_layout)

        self.z_controls_range_frame.setLayout(z_range_layout)
        self.z_controls_range_frame.setVisible(False)  # Initially hidden (shown when "Set Range" mode)
        grid.addWidget(self.z_controls_range_frame, 1, 0, 1, 3)  # Span full row (columns 0, 1, 2)

        # Configuration list
        grid.addWidget(self.list_configurations, 2, 0)

        # Options and Start button
        options_layout = QVBoxLayout()
        options_layout.addWidget(self.checkbox_withAutofocus)
        if SUPPORT_LASER_AUTOFOCUS:
            options_layout.addWidget(self.checkbox_withReflectionAutofocus)
        # options_layout.addWidget(self.checkbox_genAFMap)  # We are not using AF map now
        options_layout.addWidget(self.checkbox_useFocusMap)
        if HAS_OBJECTIVE_PIEZO:
            options_layout.addWidget(self.checkbox_usePiezo)
            if IS_PIEZO_ONLY:
                self.checkbox_usePiezo.setChecked(True)
                self.checkbox_usePiezo.setVisible(False)
        options_layout.addWidget(self.checkbox_skipSaving)

        button_layout = QVBoxLayout()
        button_layout.addWidget(self.btn_snap_images)
        button_layout.addWidget(self.btn_startAcquisition)

        bottom_right = QHBoxLayout()
        bottom_right.addLayout(options_layout)
        bottom_right.addSpacing(2)
        bottom_right.addLayout(button_layout)

        grid.addLayout(bottom_right, 2, 2)
        spacer_widget = QWidget()
        spacer_widget.setFixedWidth(2)
        grid.addWidget(spacer_widget, 0, 1)

        # Add informational labels to grid (initially hidden)
        grid.addWidget(self.z_not_selected_label, 0, 0)
        grid.addWidget(self.time_not_selected_label, 0, 2)

        # Set column stretches
        grid.setColumnStretch(0, 1)  # Middle spacer
        grid.setColumnStretch(1, 0)  # Middle spacer
        grid.setColumnStretch(2, 1)  # Middle spacer

        main_layout.addLayout(grid)
        # Row 5: Progress Bar
        row_progress_layout = QHBoxLayout()
        row_progress_layout.addWidget(self.progress_label)
        row_progress_layout.addWidget(self.progress_bar)
        row_progress_layout.addWidget(self.eta_label)
        main_layout.addLayout(row_progress_layout)
        self.toggle_z_range_controls(False)  # Initially hide Z-range controls

        # Initialize Z and Time controls visibility based on checkbox states
        if not self.checkbox_z.isChecked():
            self.hide_z_controls()
        if not self.checkbox_time.isChecked():
            self.hide_time_controls()

        # Update control visibility based on both states
        self.update_control_visibility()

        # Initialize scan controls visibility based on XY checkbox state
        self.update_scan_control_ui()

        # Update tab styles now that all frames are created
        self.update_tab_styles()

        # Initialize previous XY mode tracking
        self._previous_xy_mode = self.combobox_xy_mode.currentText()

        # Connections
        self.btn_setSavingDir.clicked.connect(self.set_saving_dir)
        self.btn_startAcquisition.clicked.connect(self.toggle_acquisition)
        self.entry_deltaZ.valueChanged.connect(self.set_deltaZ)
        self.entry_NZ.valueChanged.connect(self.multipointController.set_NZ)
        self.entry_dt.valueChanged.connect(self.multipointController.set_deltat)
        self.entry_Nt.valueChanged.connect(self.multipointController.set_Nt)
        self.entry_overlap.valueChanged.connect(self.update_coordinates)
        self.entry_overlap.valueChanged.connect(self.update_coverage_from_scan_size)
        self.entry_scan_size.valueChanged.connect(self.update_coordinates)
        self.entry_scan_size.valueChanged.connect(self.update_coverage_from_scan_size)
        # Coverage is read-only, derived from scan_size, FOV, and overlap
        self.combobox_shape.currentTextChanged.connect(self.on_shape_changed)
        self.checkbox_withAutofocus.toggled.connect(self.multipointController.set_af_flag)
        self.checkbox_withReflectionAutofocus.toggled.connect(self.multipointController.set_reflection_af_flag)
        self.checkbox_genAFMap.toggled.connect(self.multipointController.set_gen_focus_map_flag)
        self.checkbox_useFocusMap.toggled.connect(self.focusMapWidget.setEnabled)
        self.checkbox_useFocusMap.toggled.connect(self.multipointController.set_manual_focus_map_flag)
        self.checkbox_usePiezo.toggled.connect(self.multipointController.set_use_piezo)
        self.checkbox_skipSaving.toggled.connect(self.multipointController.set_skip_saving)
        self.list_configurations.itemSelectionChanged.connect(self.emit_selected_channels)
        self.multipointController.acquisition_finished.connect(self.acquisition_is_finished)
        self.multipointController.signal_acquisition_progress.connect(self.update_acquisition_progress)
        self.multipointController.signal_region_progress.connect(self.update_region_progress)
        self.signal_acquisition_started.connect(self.display_progress_bar)
        # Connect signal for setting acquisition state from external sources (e.g., TCP server)
        self.signal_set_acquisition_running.connect(self.set_acquisition_running_state)
        self.eta_timer.timeout.connect(self.update_eta_display)
        if not self.performance_mode and self.napariMosaicWidget is not None:
            self.napariMosaicWidget.signal_layers_initialized.connect(self.enable_manual_ROI)

        # Connect save/clear coordinates button
        self.btn_save_scan_coordinates.clicked.connect(self.on_save_or_clear_coordinates_clicked)
        self.btn_load_scan_coordinates.clicked.connect(self.on_load_coordinates_clicked)

        # Connect acquisition tabs
        self.checkbox_xy.toggled.connect(self.on_xy_toggled)
        self.combobox_xy_mode.currentTextChanged.connect(self.on_xy_mode_changed)
        self.checkbox_z.toggled.connect(self.on_z_toggled)
        self.combobox_z_mode.currentTextChanged.connect(self.on_z_mode_changed)
        self.checkbox_time.toggled.connect(self.on_time_toggled)

        # Load cached acquisition settings
        self.load_multipoint_widget_config_from_cache()

        # Connect settings saving to relevant value changes
        self.checkbox_xy.toggled.connect(self.save_multipoint_widget_config_to_cache)
        self.combobox_xy_mode.currentTextChanged.connect(self.save_multipoint_widget_config_to_cache)
        self.checkbox_z.toggled.connect(self.save_multipoint_widget_config_to_cache)
        self.combobox_z_mode.currentTextChanged.connect(self.save_multipoint_widget_config_to_cache)
        self.checkbox_time.toggled.connect(self.save_multipoint_widget_config_to_cache)
        self.entry_overlap.valueChanged.connect(self.save_multipoint_widget_config_to_cache)
        self.entry_dt.valueChanged.connect(self.save_multipoint_widget_config_to_cache)
        self.entry_Nt.valueChanged.connect(self.save_multipoint_widget_config_to_cache)
        self.entry_deltaZ.valueChanged.connect(self.save_multipoint_widget_config_to_cache)
        self.entry_NZ.valueChanged.connect(self.save_multipoint_widget_config_to_cache)
        self.list_configurations.itemSelectionChanged.connect(self.save_multipoint_widget_config_to_cache)
        self.checkbox_withAutofocus.toggled.connect(self.save_multipoint_widget_config_to_cache)
        self.checkbox_withReflectionAutofocus.toggled.connect(self.save_multipoint_widget_config_to_cache)

    def enable_manual_ROI(self):
        _manual_index = self.combobox_xy_mode.findText("Manual")
        self.combobox_xy_mode.model().item(_manual_index).setEnabled(True)

    def initialize_live_scan_grid_state(self):
        """Initialize live scan grid state - call this after all external connections are made"""
        enable_live_scan_grid = (
            self.checkbox_xy.isChecked() and self.combobox_xy_mode.currentText() == "Current Position"
        )
        self.signal_toggle_live_scan_grid.emit(enable_live_scan_grid)

    def save_multipoint_widget_config_to_cache(self):
        """Save current acquisition settings to cache"""
        try:
            os.makedirs("cache", exist_ok=True)

            settings = {
                "xy_enabled": self.checkbox_xy.isChecked(),
                "xy_mode": self.combobox_xy_mode.currentText(),
                "z_enabled": self.checkbox_z.isChecked(),
                "z_mode": self.combobox_z_mode.currentText(),
                "time_enabled": self.checkbox_time.isChecked(),
                "fov_overlap": self.entry_overlap.value(),
                "dt": self.entry_dt.value(),
                "nt": self.entry_Nt.value(),
                "dz": self.entry_deltaZ.value(),
                "nz": self.entry_NZ.value(),
                "selected_channels": [item.text() for item in self.list_configurations.selectedItems()],
                "contrast_af": self.checkbox_withAutofocus.isChecked(),
                "laser_af": self.checkbox_withReflectionAutofocus.isChecked(),
            }

            with open("cache/multipoint_widget_config.yaml", "w") as f:
                yaml.dump(settings, f, default_flow_style=False, sort_keys=False)

        except Exception as e:
            self._log.warning(f"Failed to save acquisition settings to cache: {e}")

    def load_multipoint_widget_config_from_cache(self):
        """Load acquisition settings from cache if it exists"""
        try:
            cache_file = "cache/multipoint_widget_config.yaml"
            if not os.path.exists(cache_file):
                return

            with open(cache_file, "r") as f:
                settings = yaml.safe_load(f)

            # Block signals to prevent triggering save during load
            self.checkbox_xy.blockSignals(True)
            self.combobox_xy_mode.blockSignals(True)
            self.checkbox_z.blockSignals(True)
            self.combobox_z_mode.blockSignals(True)
            self.checkbox_time.blockSignals(True)
            self.entry_overlap.blockSignals(True)
            self.entry_dt.blockSignals(True)
            self.entry_Nt.blockSignals(True)
            self.entry_deltaZ.blockSignals(True)
            self.entry_NZ.blockSignals(True)
            self.list_configurations.blockSignals(True)
            self.checkbox_withAutofocus.blockSignals(True)
            self.checkbox_withReflectionAutofocus.blockSignals(True)

            # Set flag to prevent automatic file dialog when loading "Load Coordinates" mode from cache
            self._loading_from_cache = True

            # Load settings
            self.checkbox_xy.setChecked(settings.get("xy_enabled", True))

            xy_mode = settings.get("xy_mode", "Current Position")
            if xy_mode in ["Current Position", "Select Wells", "Manual", "Load Coordinates"]:
                self.combobox_xy_mode.setCurrentText(xy_mode)

            # If XY is checked and mode is Manual at startup, uncheck XY and change mode to Current Position
            if self.checkbox_xy.isChecked() and self.combobox_xy_mode.currentText() == "Manual":
                self.checkbox_xy.setChecked(False)
                self.combobox_xy_mode.setCurrentText("Current Position")
                # Set the "before uncheck" mode to Current Position, so re-checking XY stays at Current Position
                self._xy_mode_before_uncheck = "Current Position"
                self._log.info(
                    "XY was checked with Manual mode at startup - unchecked XY and changed mode to Current Position"
                )

            self.checkbox_z.setChecked(settings.get("z_enabled", False))

            z_mode = settings.get("z_mode", "From Bottom")
            if z_mode in ["From Bottom", "Set Range"]:
                self.combobox_z_mode.setCurrentText(z_mode)

            self.checkbox_time.setChecked(settings.get("time_enabled", False))
            self.entry_overlap.setValue(settings.get("fov_overlap", 10))
            self.entry_dt.setValue(settings.get("dt", 0))
            self.entry_Nt.setValue(settings.get("nt", 1))
            self.entry_deltaZ.setValue(settings.get("dz", 1.0))
            self.entry_NZ.setValue(settings.get("nz", 1))

            # Restore selected channels
            selected_channels = settings.get("selected_channels", [])
            if selected_channels:
                self.list_configurations.clearSelection()
                for i in range(self.list_configurations.count()):
                    item = self.list_configurations.item(i)
                    if item.text() in selected_channels:
                        item.setSelected(True)

            # Restore autofocus settings
            self.checkbox_withAutofocus.setChecked(settings.get("contrast_af", False))
            self.checkbox_withReflectionAutofocus.setChecked(settings.get("laser_af", False))

            # Unblock signals
            self.checkbox_xy.blockSignals(False)
            self.combobox_xy_mode.blockSignals(False)
            self.checkbox_z.blockSignals(False)
            self.combobox_z_mode.blockSignals(False)
            self.checkbox_time.blockSignals(False)
            self.entry_overlap.blockSignals(False)
            self.entry_dt.blockSignals(False)
            self.entry_Nt.blockSignals(False)
            self.entry_deltaZ.blockSignals(False)
            self.entry_NZ.blockSignals(False)
            self.list_configurations.blockSignals(False)
            self.checkbox_withAutofocus.blockSignals(False)
            self.checkbox_withReflectionAutofocus.blockSignals(False)

            # Update UI state based on loaded settings
            self.update_scan_control_ui()
            self.update_control_visibility()
            self.update_tab_styles()  # Update tab visual styles based on checkbox states

            # Ensure XY mode combobox is properly enabled based on loaded XY state
            self.combobox_xy_mode.setEnabled(self.checkbox_xy.isChecked())

            # Ensure Z controls and Z mode combobox are properly enabled based on loaded Z state
            self.combobox_z_mode.setEnabled(self.checkbox_z.isChecked())
            if self.checkbox_z.isChecked():
                self.show_z_controls(True)
                # Also ensure Z range controls are properly toggled based on loaded Z mode
                if self.combobox_z_mode.currentText() == "Set Range":
                    self.toggle_z_range_controls(True)

            # Ensure Time controls are properly shown based on loaded Time state
            if self.checkbox_time.isChecked():
                self.show_time_controls(True)

            # Clear the cache loading flag
            self._loading_from_cache = False

            self._log.info("Loaded acquisition settings from cache")

        except Exception as e:
            self._log.warning(f"Failed to load acquisition settings from cache: {e}")
            # Clear the flag even on error
            self._loading_from_cache = False

    def update_tab_styles(self):
        """Update tab frame styles based on checkbox states"""
        # Active tab styles (checked) - custom colors for each tab
        xy_active_style = """
            QFrame {
                border: 1px solid #FF8C00;
                border-radius: 2px;
            }
        """

        # Orange background with opaque widget backgrounds to prevent color bleed
        xy_controls_style = """
            QFrame {
                background-color: rgba(255, 140, 0, 0.15);
            }
            QFrame QComboBox, QFrame QSpinBox, QFrame QDoubleSpinBox {
                background-color: white;
                color: black;
            }
            QFrame QComboBox:disabled, QFrame QSpinBox:disabled, QFrame QDoubleSpinBox:disabled {
                background-color: palette(button);
                color: palette(disabled-text);
            }
            QFrame QComboBox QAbstractItemView {
                background-color: white;
                color: black;
                selection-background-color: palette(highlight);
                selection-color: palette(highlighted-text);
            }
            QFrame QPushButton {
                background-color: #FFD9B3;
            }
            QFrame QLabel {
                background-color: transparent;
            }
        """

        z_active_style = """
            QFrame {
                border: 1px solid palette(highlight);
                border-radius: 2px;
            }
        """

        # Blue background for Z controls with opaque widget backgrounds
        z_controls_style = """
            QFrame {
                background-color: rgba(0, 120, 215, 0.15);
            }
            QFrame QComboBox, QFrame QSpinBox, QFrame QDoubleSpinBox {
                background-color: white;
            }
            QFrame QPushButton {
                background-color: #C2D9FF;
            }
            QFrame QLabel {
                background-color: transparent;
            }
        """

        time_active_style = """
            QFrame {
                border: 1px solid #00A000;
                border-radius: 2px;
            }
        """

        # Green background for Time controls with opaque widget backgrounds
        time_controls_style = """
            QFrame {
                background-color: rgba(0, 160, 0, 0.15);
            }
            QFrame QComboBox, QFrame QSpinBox, QFrame QDoubleSpinBox {
                background-color: white;
            }
            QFrame QPushButton {
                background-color: #C2FFC2;
            }
            QFrame QLabel {
                background-color: transparent;
            }
        """

        # Inactive tab style (unchecked) - uses default Qt inactive tab colors
        inactive_style = """
            QFrame {
                border: 1px solid palette(mid);
                border-radius: 2px;
            }
        """

        # Apply styles based on checkbox states
        self.xy_frame.setStyleSheet(xy_active_style if self.checkbox_xy.isChecked() else inactive_style)
        if hasattr(self, "xy_controls_frame"):
            self.xy_controls_frame.setStyleSheet(xy_controls_style if self.checkbox_xy.isChecked() else "")
        if hasattr(self, "load_coordinates_frame"):
            self.load_coordinates_frame.setStyleSheet(xy_controls_style if self.checkbox_xy.isChecked() else "")

        self.z_frame.setStyleSheet(z_active_style if self.checkbox_z.isChecked() else inactive_style)
        if hasattr(self, "z_controls_dz_frame"):
            self.z_controls_dz_frame.setStyleSheet(z_controls_style if self.checkbox_z.isChecked() else "")
        if hasattr(self, "z_controls_range_frame"):
            self.z_controls_range_frame.setStyleSheet(z_controls_style if self.checkbox_z.isChecked() else "")

        self.time_frame.setStyleSheet(time_active_style if self.checkbox_time.isChecked() else inactive_style)
        if hasattr(self, "time_controls_frame"):
            self.time_controls_frame.setStyleSheet(time_controls_style if self.checkbox_time.isChecked() else "")

    def on_xy_toggled(self, checked):
        """Handle XY checkbox toggle"""
        self.combobox_xy_mode.setEnabled(checked)

        if not checked:
            # Store the current mode before unchecking
            self._xy_mode_before_uncheck = self.combobox_xy_mode.currentText()

            # Switch mode to "Current Position" when unchecking
            self.combobox_xy_mode.setCurrentText("Current Position")
        else:
            # When checking XY, restore previous mode if it exists
            if self._xy_mode_before_uncheck is not None:
                # Check if previous mode was Manual
                if self._xy_mode_before_uncheck == "Manual":
                    # If mosaic view has been cleared (no shapes), stay at "Current Position"
                    if self.shapes_mm is None or len(self.shapes_mm) == 0:
                        self.combobox_xy_mode.setCurrentText("Current Position")
                        self._log.info("Manual mode had no shapes, staying at Current Position")
                    else:
                        # Shapes exist, restore Manual mode
                        self.combobox_xy_mode.setCurrentText("Manual")
                else:
                    # For non-Manual modes, always restore
                    self.combobox_xy_mode.setCurrentText(self._xy_mode_before_uncheck)

        self.update_tab_styles()

        # Show/hide scan shape and coordinate controls
        self.update_scan_control_ui()

        if checked:
            self.update_coordinates()  # to-do: what does this do? is it needed?
            if self.combobox_xy_mode.currentText() == "Current Position":
                self.signal_toggle_live_scan_grid.emit(True)
        else:
            self.signal_toggle_live_scan_grid.emit(False)  # disable live scan grid regardless of XY mode

        self._log.debug(f"XY acquisition {'enabled' if checked else 'disabled'}")

    def on_xy_mode_changed(self, mode):
        """Handle XY mode dropdown change"""
        self._log.debug(f"XY mode changed to: {mode}")

        # Store current mode's parameters before switching (if we know the previous mode)
        # We need to track the previous mode to store its parameters
        if hasattr(self, "_previous_xy_mode") and self._previous_xy_mode in ["Current Position", "Select Wells"]:
            self.store_xy_mode_parameters(self._previous_xy_mode)

        # Restore parameters for the new mode
        if mode in ["Current Position", "Select Wells"]:
            self.restore_xy_mode_parameters(mode)

        # Update UI based on the new mode
        self.update_scan_control_ui()

        # Handle coordinate restoration/clearing based on mode
        if mode == "Load Coordinates":
            # If no file has been loaded previously, open file dialog immediately
            # But skip if we're loading from cache
            if self.cached_loaded_coordinates_df is None and not getattr(self, "_loading_from_cache", False):
                QTimer.singleShot(100, self.on_load_coordinates_clicked)
            else:
                # Restore cached coordinates when switching to Load Coordinates mode
                self.restore_cached_coordinates()
        else:
            # When switching away from Load Coordinates, clear coordinates and update based on new mode
            if hasattr(self, "_previous_xy_mode") and self._previous_xy_mode == "Load Coordinates":
                self.scanCoordinates.clear_regions()

        # Store the current mode as previous for next time
        self._previous_xy_mode = mode

        if mode == "Manual":
            self.signal_manual_shape_mode.emit(True)
        elif mode == "Load Coordinates":
            # Don't update coordinates or emit signals for Load Coordinates mode
            pass
        else:
            self.update_coordinates()  # to-do: what does this do? is it needed?

        if mode == "Current Position":
            self.signal_toggle_live_scan_grid.emit(True)  # enable live scan grid
        else:
            self.signal_toggle_live_scan_grid.emit(False)  # disable live scan grid

    def update_scan_control_ui(self):
        """Update scan control UI based on XY checkbox and mode selection"""
        xy_checked = self.checkbox_xy.isChecked()
        xy_mode = self.combobox_xy_mode.currentText()

        # Handle Load Coordinates mode separately
        if xy_checked and xy_mode == "Load Coordinates":
            # Hide the two-line xy_controls_frame
            self.xy_controls_frame.setVisible(False)
            # Show the Load Coordinates frame
            self.load_coordinates_frame.setVisible(True)
            return

        # Show/hide the entire XY controls frame based on XY checkbox
        self.xy_controls_frame.setVisible(xy_checked)
        # Hide the Load Coordinates frame for all other modes
        self.load_coordinates_frame.setVisible(False)

        # Handle coverage field based on XY mode
        if xy_checked:
            if xy_mode in ["Current Position", "Manual"]:
                # For Current Position and Manual modes, coverage should be N/A and disabled
                self.entry_well_coverage.blockSignals(True)
                self.entry_well_coverage.setRange(0, 0)  # Allow 0 for N/A mode
                self.entry_well_coverage.setValue(0)  # Set to 0 for N/A indicator
                self.entry_well_coverage.setEnabled(False)
                self.entry_well_coverage.setSuffix(" (N/A)")
                self.entry_well_coverage.blockSignals(False)
                if xy_mode == "Manual":
                    # hide the row of scan shape, scan size and coverage
                    self.scan_shape_label.setVisible(False)
                    self.combobox_shape.setVisible(False)
                    self.scan_size_label.setVisible(False)
                    self.entry_scan_size.setVisible(False)
                    self.coverage_label.setVisible(False)
                    self.entry_well_coverage.setVisible(False)
                elif xy_mode == "Current Position":
                    # show the row of scan shape, scan size and coverage
                    self.scan_shape_label.setVisible(True)
                    self.combobox_shape.setVisible(True)
                    self.scan_size_label.setVisible(True)
                    self.entry_scan_size.setVisible(True)
                    self.coverage_label.setVisible(True)
                    self.entry_well_coverage.setVisible(True)
            elif xy_mode == "Select Wells":
                # For Select Wells mode, coverage is read-only (derived from scan_size, FOV, overlap)
                self.entry_well_coverage.blockSignals(True)
                self.entry_well_coverage.setRange(0, 999.99)  # Allow any display value
                self.entry_well_coverage.setSuffix("%")
                self.entry_well_coverage.setReadOnly(True)
                self.entry_well_coverage.blockSignals(False)

                # Derive coverage from current scan_size (scan_size is the source of truth)
                self.update_coverage_from_scan_size()

                # Coverage is always read-only but visually enabled for display
                self.entry_well_coverage.setEnabled(True)

                # show the row of scan shape, scan size and coverage
                self.scan_shape_label.setVisible(True)
                self.combobox_shape.setVisible(True)
                self.scan_size_label.setVisible(True)
                self.entry_scan_size.setVisible(True)
                self.coverage_label.setVisible(True)
                self.entry_well_coverage.setVisible(True)

    def set_coordinates_to_current_position(self):
        """Set scan coordinates to current stage position (single FOV)"""
        if self.tab_widget and self.tab_widget.currentWidget() != self:
            return

        # Clear existing regions
        if self.scanCoordinates.has_regions():
            self.scanCoordinates.clear_regions()

        # Get current position and add it as a single region
        pos = self.stage.get_pos()
        x = pos.x_mm
        y = pos.y_mm

        # Add current position as a single FOV with minimal scan size
        scan_size_mm = 0.01  # Very small scan size for single FOV
        overlap_percent = 0  # No overlap needed for single FOV
        shape = "Square"  # Default shape

        self.scanCoordinates.add_region("current", x, y, scan_size_mm, overlap_percent, shape)

    def on_z_toggled(self, checked):
        """Handle Z checkbox toggle"""
        self.update_tab_styles()

        # Enable/disable the Z mode dropdown
        self.combobox_z_mode.setEnabled(checked)

        if checked:
            # Z Stack enabled - restore stored parameters and show controls
            self.restore_z_parameters()
            self.show_z_controls(True)
        else:
            # Z Stack disabled - store current parameters and hide controls
            self.store_z_parameters()
            self.hide_z_controls()

        # Update visibility based on both Z and Time states
        self.update_control_visibility()

        self._log.debug(f"Z acquisition {'enabled' if checked else 'disabled'}")

    def on_z_mode_changed(self, mode):
        """Handle Z mode dropdown change"""
        # Show/hide Z-min/Z-max controls based on mode
        self.toggle_z_range_controls(mode == "Set Range")
        self._log.debug(f"Z mode changed to: {mode}")

    def on_time_toggled(self, checked):
        """Handle Time checkbox toggle"""
        self.update_tab_styles()

        if checked:
            # Time lapse enabled - restore stored parameters and show controls
            self.restore_time_parameters()
            self.show_time_controls(True)
        else:
            # Time lapse disabled - store current parameters and hide controls
            self.store_time_parameters()
            self.hide_time_controls()

        # Update visibility based on both Z and Time states
        self.update_control_visibility()

        self._log.debug(f"Time acquisition {'enabled' if checked else 'disabled'}")

    def store_xy_mode_parameters(self, mode):
        """Store current scan size and shape parameters for the given XY mode.

        Coverage is not stored as it is derived from scan_size, FOV, and overlap.
        """
        if mode in self.stored_xy_params:
            self.stored_xy_params[mode]["scan_size"] = self.entry_scan_size.value()
            self.stored_xy_params[mode]["scan_shape"] = self.combobox_shape.currentText()

    def restore_xy_mode_parameters(self, mode):
        """Restore stored scan size and shape parameters for the given XY mode."""
        if mode in self.stored_xy_params:
            # Restore scan size for both Current Position and Select Wells modes
            if self.stored_xy_params[mode]["scan_size"] is not None:
                self.entry_scan_size.blockSignals(True)
                self.entry_scan_size.setValue(self.stored_xy_params[mode]["scan_size"])
                self.entry_scan_size.blockSignals(False)
            else:
                # Set default values if no stored value exists
                if mode == "Current Position":
                    # For current position, use a small default scan size
                    self.entry_scan_size.blockSignals(True)
                    self.entry_scan_size.setValue(0.1)  # Small default for single FOV
                    self.entry_scan_size.blockSignals(False)
                elif mode == "Select Wells":
                    # For select wells, use a larger default scan size
                    self.entry_scan_size.blockSignals(True)
                    self.entry_scan_size.setValue(1.0)  # Larger default for well coverage
                    self.entry_scan_size.blockSignals(False)

            # Restore scan shape for both modes
            if self.stored_xy_params[mode]["scan_shape"] is not None:
                self.combobox_shape.blockSignals(True)
                self.combobox_shape.setCurrentText(self.stored_xy_params[mode]["scan_shape"])
                self.combobox_shape.blockSignals(False)
            else:
                # Set default shape if no stored value exists
                self.combobox_shape.blockSignals(True)
                if mode == "Current Position":
                    # For current position, default to Square (simple single FOV)
                    self.combobox_shape.setCurrentText("Square")
                elif mode == "Select Wells":
                    # For select wells, use the format-based default from set_default_shape
                    self.set_default_shape()
                self.combobox_shape.blockSignals(False)

            # Coverage restoration for Select Wells mode is handled in update_scan_control_ui()
            # to avoid conflicts with range setting and UI state management

    def store_z_parameters(self):
        """Store current Z parameters before hiding controls"""
        self.stored_z_params["dz"] = self.entry_deltaZ.value()
        self.stored_z_params["nz"] = self.entry_NZ.value()
        self.stored_z_params["z_min"] = self.entry_minZ.value()
        self.stored_z_params["z_max"] = self.entry_maxZ.value()
        self.stored_z_params["z_mode"] = self.combobox_z_mode.currentText()

    def restore_z_parameters(self):
        """Restore stored Z parameters when showing controls"""
        if self.stored_z_params["dz"] is not None:
            self.entry_deltaZ.setValue(self.stored_z_params["dz"])
        if self.stored_z_params["nz"] is not None:
            self.entry_NZ.setValue(self.stored_z_params["nz"])
        if self.stored_z_params["z_min"] is not None:
            self.entry_minZ.setValue(self.stored_z_params["z_min"])
        if self.stored_z_params["z_max"] is not None:
            self.entry_maxZ.setValue(self.stored_z_params["z_max"])
        self.combobox_z_mode.setCurrentText(self.stored_z_params["z_mode"])

    def hide_z_controls(self):
        """Hide Z-related controls and set single-slice parameters"""
        # Hide dz/Nz widgets
        for i in range(self.dz_layout.count()):
            widget = self.dz_layout.itemAt(i).widget()
            if widget:
                widget.setVisible(False)

        # Hide Z-min/Z-max controls
        for layout in (self.z_min_layout, self.z_max_layout):
            for i in range(layout.count()):
                widget = layout.itemAt(i).widget()
                if widget:
                    widget.setVisible(False)

        # Set single-slice parameters
        current_z = self.stage.get_pos().z_mm * 1000  # Convert to μm
        self.entry_NZ.setValue(1)
        self.entry_minZ.setValue(current_z)
        self.entry_maxZ.setValue(current_z)
        self.combobox_z_mode.blockSignals(True)
        self.combobox_z_mode.setCurrentText("From Bottom")
        self.combobox_z_mode.blockSignals(False)

    def show_z_controls(self, visible):
        """Show Z-related controls"""
        # Show dz/Nz widgets
        for i in range(self.dz_layout.count()):
            widget = self.dz_layout.itemAt(i).widget()
            if widget:
                widget.setVisible(visible)

        # Show/hide Z-min/Z-max based on dropdown selection AND visibility
        # Only show range controls if Z is enabled (visible=True) AND mode is "Set Range"
        show_range = visible and self.combobox_z_mode.currentText() == "Set Range"
        self.toggle_z_range_controls(show_range)

    def store_time_parameters(self):
        """Store current Time parameters before hiding controls"""
        self.stored_time_params["dt"] = self.entry_dt.value()
        self.stored_time_params["nt"] = self.entry_Nt.value()

    def restore_time_parameters(self):
        """Restore stored Time parameters when showing controls"""
        if self.stored_time_params["dt"] is not None:
            self.entry_dt.setValue(self.stored_time_params["dt"])
        if self.stored_time_params["nt"] is not None:
            self.entry_Nt.setValue(self.stored_time_params["nt"])

    def hide_time_controls(self):
        """Hide Time-related controls and set single-timepoint parameters"""
        # Hide dt/Nt widgets
        for i in range(self.dt_layout.count()):
            widget = self.dt_layout.itemAt(i).widget()
            if widget:
                widget.setVisible(False)

        # Set single-timepoint parameters
        self.entry_dt.setValue(0)
        self.entry_Nt.setValue(1)

    def show_time_controls(self, visible):
        """Show Time-related controls"""
        # Show dt/Nt widgets
        for i in range(self.dt_layout.count()):
            widget = self.dt_layout.itemAt(i).widget()
            if widget:
                widget.setVisible(visible)

    def update_control_visibility(self):
        """Update visibility of controls and informational labels based on Z and Time states"""
        z_checked = self.checkbox_z.isChecked()
        time_checked = self.checkbox_time.isChecked()

        if time_checked and not z_checked:
            # Time lapse selected but Z stack not - show "Z stack not selected" message
            self.z_not_selected_label.setVisible(True)
            self.time_not_selected_label.setVisible(False)
            # Hide actual Z controls
            for i in range(self.dz_layout.count()):
                widget = self.dz_layout.itemAt(i).widget()
                if widget:
                    widget.setVisible(False)
            # Show Time controls
            self.show_time_controls(True)
        elif z_checked and not time_checked:
            # Z stack selected but Time lapse not - show "Time lapse not selected" message
            self.time_not_selected_label.setVisible(True)
            self.z_not_selected_label.setVisible(False)
            # Hide actual Time controls
            for i in range(self.dt_layout.count()):
                widget = self.dt_layout.itemAt(i).widget()
                if widget:
                    widget.setVisible(False)
            # Show Z controls
            self.show_z_controls(True)
        else:
            # Both selected or both unselected - hide informational labels
            self.z_not_selected_label.setVisible(False)
            self.time_not_selected_label.setVisible(False)

            # Show/hide actual controls based on individual states
            self.show_z_controls(z_checked)
            self.show_time_controls(time_checked)

    def update_region_progress(self, current_fov, num_fovs):
        self.progress_bar.setMaximum(num_fovs)
        self.progress_bar.setValue(current_fov)

        if self.acquisition_start_time is not None and current_fov > 0:
            elapsed_time = time.time() - self.acquisition_start_time
            Nt = self.entry_Nt.value()
            dt = self.entry_dt.value()

            # Calculate total processed FOVs and total FOVs
            processed_fovs = (
                (self.current_region - 1) * num_fovs
                + current_fov
                + self.current_time_point * self.num_regions * num_fovs
            )
            total_fovs = self.num_regions * num_fovs * Nt
            remaining_fovs = total_fovs - processed_fovs

            # Calculate ETA
            fov_per_second = processed_fovs / elapsed_time
            self.eta_seconds = (
                remaining_fovs / fov_per_second + (Nt - 1 - self.current_time_point) * dt if fov_per_second > 0 else 0
            )
            self.update_eta_display()

            # Start or restart the timer
            self.eta_timer.start(1000)  # Update every 1000 ms (1 second)

    def update_acquisition_progress(self, current_region, num_regions, current_time_point):
        self.current_region = current_region
        self.current_time_point = current_time_point

        if self.current_region == 1 and self.current_time_point == 0:  # First region
            self.acquisition_start_time = time.time()
            self.num_regions = num_regions

        progress_parts = []
        # Update timepoint progress if there are multiple timepoints and the timepoint has changed
        if self.entry_Nt.value() > 1:
            progress_parts.append(f"Time {current_time_point + 1}/{self.entry_Nt.value()}")

        # Update region progress if there are multiple regions
        if num_regions > 1:
            progress_parts.append(f"Region {current_region}/{num_regions}")

        # Set the progress label text, ensuring it's not empty
        progress_text = "  ".join(progress_parts)
        self.progress_label.setText(progress_text if progress_text else "Progress")
        self.progress_bar.setValue(0)

    def update_eta_display(self):
        if self.eta_seconds > 0:
            self.eta_seconds -= 1  # Decrease by 1 second
            hours, remainder = divmod(int(self.eta_seconds), 3600)
            minutes, seconds = divmod(remainder, 60)
            if hours > 0:
                eta_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            else:
                eta_str = f"{minutes:02d}:{seconds:02d}"
            self.eta_label.setText(f"{eta_str}")
        else:
            self.eta_timer.stop()
            self.eta_label.setText("00:00")

    def display_progress_bar(self, show):
        self.progress_label.setVisible(show)
        self.progress_bar.setVisible(show)
        self.eta_label.setVisible(show)
        if show:
            self.progress_bar.setValue(0)
            self.progress_label.setText("Region 0/0")
            self.eta_label.setText("--:--")
            self.acquisition_start_time = None
        else:
            self.eta_timer.stop()

    def toggle_z_range_controls(self, is_visible):
        # Show/hide the entire range frame (Z-min and Z-max)
        if hasattr(self, "z_controls_range_frame"):
            self.z_controls_range_frame.setVisible(is_visible)

        # Also control individual widgets for compatibility
        for layout in (self.z_min_layout, self.z_max_layout):
            for i in range(layout.count()):
                widget = layout.itemAt(i).widget()
                if widget:
                    widget.setVisible(is_visible)

        # Disable and uncheck reflection autofocus checkbox if Z-range is visible
        if is_visible:
            self.checkbox_withReflectionAutofocus.setChecked(False)
        self.checkbox_withReflectionAutofocus.setEnabled(not is_visible)
        # Enable/disable NZ entry based on the inverse of is_visible
        self.entry_NZ.setEnabled(not is_visible)
        current_z = self.stage.get_pos().z_mm * 1000
        self.entry_minZ.setValue(current_z)
        if is_visible:
            self._reset_reflection_af_reference()
        self.entry_maxZ.setValue(current_z)

        # Safely connect or disconnect signals
        try:
            if is_visible:
                self.entry_minZ.valueChanged.connect(self.update_z_max)
                self.entry_maxZ.valueChanged.connect(self.update_z_min)
                self.entry_minZ.valueChanged.connect(self.update_Nz)
                self.entry_maxZ.valueChanged.connect(self.update_Nz)
                self.entry_deltaZ.valueChanged.connect(self.update_Nz)
                self.update_Nz()
            else:
                self.entry_minZ.valueChanged.disconnect(self.update_z_max)
                self.entry_maxZ.valueChanged.disconnect(self.update_z_min)
                self.entry_minZ.valueChanged.disconnect(self.update_Nz)
                self.entry_maxZ.valueChanged.disconnect(self.update_Nz)
                self.entry_deltaZ.valueChanged.disconnect(self.update_Nz)
        except TypeError:
            # Handle case where signals might not be connected/disconnected
            pass

        # Update the layout
        self.updateGeometry()
        self.update()

    def set_default_scan_size(self):
        if self.checkbox_xy.isChecked() and self.combobox_xy_mode.currentText() == "Select Wells":
            self._log.debug(f"Sample Format: {self.navigationViewer.sample}")
            self.combobox_shape.blockSignals(True)
            self.entry_scan_size.blockSignals(True)

            self.set_default_shape()

            if "glass slide" in self.navigationViewer.sample:
                self.entry_scan_size.setValue(
                    0.1
                )  # init to 0.1mm when switching to 'glass slide' (for imaging a single FOV by default)
                self.entry_scan_size.setEnabled(True)
            else:
                # Set scan_size to effective well size (100% coverage)
                effective_well_size = self.get_effective_well_size()
                self.entry_scan_size.setValue(round(effective_well_size, 3))

            # Coverage is read-only, derive it from scan_size
            self.update_coverage_from_scan_size()
            self.update_coordinates()

            self.combobox_shape.blockSignals(False)
            self.entry_scan_size.blockSignals(False)
        else:
            # Update stored settings for "Select Wells" mode for use later.
            # Coverage is derived from scan_size, so we only store scan_size and shape.
            if "glass slide" not in self.navigationViewer.sample:
                effective_well_size = self.get_effective_well_size()
                scan_size = round(effective_well_size, 3)
                self.stored_xy_params["Select Wells"]["scan_size"] = scan_size
            else:
                # For glass slide, use default scan size
                self.stored_xy_params["Select Wells"]["scan_size"] = 0.1

            self.stored_xy_params["Select Wells"]["scan_shape"] = (
                "Square" if self.scanCoordinates.format in ["384 well plate", "1536 well plate"] else "Circle"
            )

        # change scan size to single FOV if XY is checked and mode is "Current Position"
        if self.checkbox_xy.isChecked() and self.combobox_xy_mode.currentText() == "Current Position":
            self.entry_scan_size.setValue(0.1)

    def set_default_shape(self):
        if self.scanCoordinates.format in ["384 well plate", "1536 well plate"]:
            self.combobox_shape.setCurrentText("Square")
        # elif self.scanCoordinates.format in ["4 slide"]:
        #     self.combobox_shape.setCurrentText("Rectangle")
        elif self.scanCoordinates.format != 0:
            self.combobox_shape.setCurrentText("Circle")

    def get_effective_well_size(self):
        well_size = self.scanCoordinates.well_size_mm
        shape = self.combobox_shape.currentText()
        is_round_well = self.scanCoordinates.format not in ["384 well plate", "1536 well plate"]
        fov_size_mm = self.navigationViewer.camera.get_fov_size_mm() * self.objectiveStore.get_pixel_size_factor()
        return get_effective_well_size(well_size, fov_size_mm, shape, is_round_well)

    def reset_coordinates(self):
        # Called after acquisition - preserve scan_size, update coverage display
        if self.combobox_xy_mode.currentText() == "Select Wells":
            self.update_coverage_from_scan_size()
        self.update_coordinates()

    def on_shape_changed(self):
        # Called when scan shape changes - scan_size stays constant, coverage updates
        # (coverage is read-only, derived from scan_size and effective_well_size)
        if self.combobox_xy_mode.currentText() == "Select Wells":
            self.update_coverage_from_scan_size()
        self.update_coordinates()

    def update_manual_shape(self, shapes_data_mm):
        if self.tab_widget and self.tab_widget.currentWidget() != self:
            return

        if shapes_data_mm and len(shapes_data_mm) > 0:
            self.shapes_mm = shapes_data_mm
            self._log.debug(f"Manual ROIs updated with {len(self.shapes_mm)} shapes")
        else:
            self.shapes_mm = None
            self._log.debug("No valid shapes found, cleared manual ROIs")
        self.update_coordinates()

    def convert_pixel_to_mm(self, pixel_coords):
        # Convert pixel coordinates to millimeter coordinates
        mm_coords = pixel_coords * self.napariMosaicWidget.viewer_pixel_size_mm
        mm_coords += np.array(
            [self.napariMosaicWidget.top_left_coordinate[1], self.napariMosaicWidget.top_left_coordinate[0]]
        )
        return mm_coords

    def update_coverage_from_scan_size(self):
        self.entry_well_coverage.blockSignals(True)
        if "glass slide" not in self.navigationViewer.sample:
            well_size_mm = self.scanCoordinates.well_size_mm
            scan_size = self.entry_scan_size.value()
            overlap_percent = self.entry_overlap.value()
            fov_size_mm = self.navigationViewer.camera.get_fov_size_mm() * self.objectiveStore.get_pixel_size_factor()
            shape = self.combobox_shape.currentText()
            is_round_well = self.scanCoordinates.format not in ["384 well plate", "1536 well plate"]

            coverage = calculate_well_coverage(
                scan_size, fov_size_mm, overlap_percent, shape, well_size_mm, is_round_well
            )

            self.entry_well_coverage.setValue(coverage)
            self._log.debug(f"Coverage: {coverage}%")
        else:
            # Glass slide mode - coverage not applicable
            self.entry_well_coverage.setValue(0)
        self.entry_well_coverage.blockSignals(False)

    def update_dz(self):
        z_min = self.entry_minZ.value()
        z_max = self.entry_maxZ.value()
        nz = self.entry_NZ.value()
        dz = (z_max - z_min) / (nz - 1) if nz > 1 else 0
        self.entry_deltaZ.setValue(dz)

    def update_Nz(self):
        z_min = self.entry_minZ.value()
        z_max = self.entry_maxZ.value()
        dz = self.entry_deltaZ.value()
        nz = math.ceil((z_max - z_min) / dz) + 1
        self.entry_NZ.setValue(nz)

    def set_z_min(self):
        z_value = self.stage.get_pos().z_mm * 1000  # Convert to μm
        self.entry_minZ.setValue(z_value)
        self._reset_reflection_af_reference()

    def set_z_max(self):
        z_value = self.stage.get_pos().z_mm * 1000  # Convert to μm
        self.entry_maxZ.setValue(z_value)

    def goto_z_min(self):
        z_value_mm = self.entry_minZ.value() / 1000  # Convert from μm to mm
        self.stage.move_z_to(z_value_mm)

    def goto_z_max(self):
        z_value_mm = self.entry_maxZ.value() / 1000  # Convert from μm to mm
        self.stage.move_z_to(z_value_mm)

    def update_z_min(self, z_pos_um):
        if z_pos_um < self.entry_minZ.value():
            self.entry_minZ.setValue(z_pos_um)
            self._reset_reflection_af_reference()

    def update_z_max(self, z_pos_um):
        if z_pos_um > self.entry_maxZ.value():
            self.entry_maxZ.setValue(z_pos_um)

    def _reset_reflection_af_reference(self):
        if self.checkbox_withReflectionAutofocus.isChecked():
            was_live = self.liveController.is_live
            if was_live:
                self.liveController.stop_live()
            if not self.multipointController.laserAutoFocusController.set_reference():
                error_dialog("Failed to set reference for reflection autofocus. Is the laser autofocus initialized?")
            if was_live:
                self.liveController.start_live()

    def init_z(self, z_pos_mm=None):
        # sets initial z range form the current z position used after startup of the GUI
        if z_pos_mm is None:
            z_pos_mm = self.stage.get_pos().z_mm

        # block entry update signals
        self.entry_minZ.blockSignals(True)
        self.entry_maxZ.blockSignals(True)

        # set entry range values bith to current z pos
        self.entry_minZ.setValue(z_pos_mm * 1000)
        self.entry_maxZ.setValue(z_pos_mm * 1000)
        self._log.debug(f"Init z-level wellplate: {self.entry_minZ.value()}")

        # reallow updates from entry sinals (signal enforces min <= max when we update either entry)
        self.entry_minZ.blockSignals(False)
        self.entry_maxZ.blockSignals(False)

    def update_coordinates(self):
        if self.tab_widget and self.tab_widget.currentWidget() != self:
            return

        # If XY is not checked, use current position instead of scan coordinates
        if not self.checkbox_xy.isChecked():
            self.set_coordinates_to_current_position()
            return

        scan_size_mm = self.entry_scan_size.value()
        overlap_percent = self.entry_overlap.value()
        shape = self.combobox_shape.currentText()

        if self.combobox_xy_mode.currentText() == "Manual":
            self.scanCoordinates.set_manual_coordinates(self.shapes_mm, overlap_percent)

        elif self.combobox_xy_mode.currentText() == "Current Position":
            pos = self.stage.get_pos()
            self.scanCoordinates.set_live_scan_coordinates(pos.x_mm, pos.y_mm, scan_size_mm, overlap_percent, shape)
        else:
            if self.scanCoordinates.has_regions():
                self.scanCoordinates.clear_regions()
            self.scanCoordinates.set_well_coordinates(scan_size_mm, overlap_percent, shape)

    def handle_objective_change(self):
        """Handle objective change - update coverage and coordinates.

        When the objective changes, the FOV size changes, which affects both the
        effective well size (for Circle shapes) and the coverage calculation.
        Scan_size stays constant; coverage is recalculated and coordinates are
        updated to reflect the new tile positions.
        """
        if self.tab_widget and self.tab_widget.currentWidget() != self:
            return
        if self.combobox_xy_mode.currentText() == "Select Wells":
            # Coverage is read-only, derived from scan_size and FOV
            self.update_coverage_from_scan_size()
        self.update_coordinates()

    def update_well_coordinates(self, selected):
        if self.tab_widget and self.tab_widget.currentWidget() != self:
            return

        # If XY is not checked, use current position instead
        if not self.checkbox_xy.isChecked():
            self.set_coordinates_to_current_position()  # to-do: is it needed?
            return

        if self.combobox_xy_mode.currentText() != "Select Wells":
            return

        if selected:
            scan_size_mm = self.entry_scan_size.value()
            overlap_percent = self.entry_overlap.value()
            shape = self.combobox_shape.currentText()
            self.scanCoordinates.set_well_coordinates(scan_size_mm, overlap_percent, shape)
        elif self.scanCoordinates.has_regions():
            self.scanCoordinates.clear_regions()

    def update_live_coordinates(self, pos: squid.abc.Pos):
        if self.tab_widget and self.tab_widget.currentWidget() != self:
            return
        # Don't update scan coordinates if we're navigating focus points. A temporary fix for focus map with glass slide.
        # This disables updating scanning grid when focus map is checked
        if self.focusMapWidget is not None and self.focusMapWidget.enabled:
            return
        # Don't update live coordinates if XY is not checked - coordinates should stay at current position
        if not self.checkbox_xy.isChecked():
            return

        x_mm = pos.x_mm
        y_mm = pos.y_mm
        # Check if x_mm or y_mm has changed
        position_changed = (x_mm != self._last_x_mm) or (y_mm != self._last_y_mm)
        if not position_changed or time.time() - self._last_update_time < 0.5:
            return
        scan_size_mm = self.entry_scan_size.value()
        overlap_percent = self.entry_overlap.value()
        shape = self.combobox_shape.currentText()
        self.scanCoordinates.set_live_scan_coordinates(x_mm, y_mm, scan_size_mm, overlap_percent, shape)
        self._last_update_time = time.time()
        self._last_x_mm = x_mm
        self._last_y_mm = y_mm

    def toggle_acquisition(self, pressed):
        self._log.debug(f"WellplateMultiPointWidget.toggle_acquisition, {pressed=}")
        if not self.base_path_is_set:
            self.btn_startAcquisition.setChecked(False)
            QMessageBox.warning(self, "Warning", "Please choose base saving directory first")
            return

        if not self.list_configurations.selectedItems():
            self.btn_startAcquisition.setChecked(False)
            QMessageBox.warning(self, "Warning", "Please select at least one imaging channel")
            return

        if pressed:
            if self.multipointController.acquisition_in_progress():
                self._log.warning("Acquisition in progress or aborting, cannot start another yet.")
                self.btn_startAcquisition.setChecked(False)
                return

            # if XY is not checked, use current position
            if not self.checkbox_xy.isChecked():
                self.set_coordinates_to_current_position()

            self.scanCoordinates.sort_coordinates()

            if self.combobox_z_mode.currentText() == "Set Range":
                # Set Z-range (convert from μm to mm)
                minZ = self.entry_minZ.value() / 1000  # Convert from μm to mm
                maxZ = self.entry_maxZ.value() / 1000  # Convert from μm to mm
                self.multipointController.set_z_range(minZ, maxZ)
                self._log.debug(f"Set z-range: ({minZ}, {maxZ})")
            else:
                z = self.stage.get_pos().z_mm
                dz = self.entry_deltaZ.value()
                Nz = self.entry_NZ.value()
                self.multipointController.set_z_range(z, z + dz * (Nz - 1))

            if self.checkbox_useFocusMap.isChecked():
                # Try to fit the surface
                if self.focusMapWidget.fit_surface():
                    # If fit successful, set the surface fitter in controller
                    self.multipointController.set_focus_map(self.focusMapWidget.focusMap)
                else:
                    QMessageBox.warning(self, "Warning", "Failed to fit focus surface")
                    self.btn_startAcquisition.setChecked(False)
                    return
            else:
                # If checkbox not checked, set surface fitter to None
                self.multipointController.set_focus_map(None)

            self.multipointController.set_deltaZ(self.entry_deltaZ.value())
            self.multipointController.set_NZ(self.entry_NZ.value())
            self.multipointController.set_deltat(self.entry_dt.value())
            self.multipointController.set_Nt(self.entry_Nt.value())
            self.multipointController.set_use_piezo(self.checkbox_usePiezo.isChecked())
            self.multipointController.set_af_flag(self.checkbox_withAutofocus.isChecked())
            self.multipointController.set_reflection_af_flag(self.checkbox_withReflectionAutofocus.isChecked())
            self.multipointController.set_base_path(self.lineEdit_savingDir.text())
            self.multipointController.set_use_fluidics(False)
            self.multipointController.set_skip_saving(self.checkbox_skipSaving.isChecked())
            self.multipointController.set_widget_type("wellplate")
            self.multipointController.set_scan_size(self.entry_scan_size.value())
            self.multipointController.set_overlap_percent(self.entry_overlap.value())
            self.multipointController.set_xy_mode(self.combobox_xy_mode.currentText())
            self.multipointController.set_selected_configurations(
                [item.text() for item in self.list_configurations.selectedItems()]
            )
            self.multipointController.start_new_experiment(self.lineEdit_experimentID.text())

            if self.checkbox_skipSaving.isChecked():
                self._log.info("Skipping disk space check - image saving is disabled")
            elif not check_space_available_with_error_dialog(self.multipointController, self._log):
                self.btn_startAcquisition.setChecked(False)
                self._log.error("Failed to start acquisition.  Not enough disk space available.")
                return

            if not check_ram_available_with_error_dialog(
                self.multipointController, self._log, performance_mode=self.performance_mode
            ):
                self.btn_startAcquisition.setChecked(False)
                self._log.error("Failed to start acquisition.  Not enough RAM available.")
                return

            # Update UI to show acquisition is running
            self._set_ui_acquisition_running(self.entry_NZ.value(), self.entry_deltaZ.value())

            # Start acquisition
            self.multipointController.run_acquisition()

        else:
            # This must eventually propagate through and call our aquisition_is_finished, or else we'll be left
            # in an odd state.
            self.multipointController.request_abort_aquisition()

    def _set_ui_acquisition_running(self, nz: int, delta_z_um: float, set_button_checked: bool = False):
        """Update UI to reflect that acquisition is running.

        Args:
            nz: Number of Z slices
            delta_z_um: Z step size in microns
            set_button_checked: If True, also set the button to checked state
                (needed when called externally, not from button click)
        """
        self.is_current_acquisition_widget = True
        self.setEnabled_all(False)
        if set_button_checked:
            self.btn_startAcquisition.setChecked(True)
        self.btn_startAcquisition.setText("Stop\n Acquisition ")
        # Emit signals to notify other components
        self.signal_acquisition_started.emit(True)
        self.signal_acquisition_shape.emit(nz, delta_z_um)

    @Slot(bool, int, float)
    def set_acquisition_running_state(self, is_running: bool, nz: int = 1, delta_z_um: float = 1.0) -> None:
        """Set the widget's acquisition state (called from TCP server via QMetaObject.invokeMethod).

        This is invoked on the main thread when acquisition is started from an external source
        (e.g., TCP server). Uses Qt.BlockingQueuedConnection to ensure GUI is fully updated
        before acquisition starts.

        Note: Exceptions in slots called via BlockingQueuedConnection are silently swallowed
        by Qt, so we catch and log them explicitly here.
        """
        self._log.debug(f"set_acquisition_running_state: is_running={is_running}, nz={nz}, delta_z_um={delta_z_um}")
        try:
            if is_running:
                self._set_ui_acquisition_running(nz, delta_z_um, set_button_checked=True)
            else:
                self.acquisition_is_finished()
        except Exception as e:
            self._log.error(f"Exception in set_acquisition_running_state: {e}", exc_info=True)

    def acquisition_is_finished(self):
        self._log.debug(
            f"In WellMultiPointWidget, got acquisition_is_finished with {self.is_current_acquisition_widget=}"
        )
        if not self.is_current_acquisition_widget:
            return  # Skip if this wasn't the widget that started acquisition

        self.signal_acquisition_started.emit(False)
        self.is_current_acquisition_widget = False
        self.btn_startAcquisition.setChecked(False)
        self.btn_startAcquisition.setText("Start\n Acquisition ")
        if self.focusMapWidget is not None and self.focusMapWidget.focus_points:
            self.focusMapWidget.disable_updating_focus_points_on_signal()
        self.reset_coordinates()
        if self.focusMapWidget is not None and self.focusMapWidget.focus_points:
            self.focusMapWidget.update_focus_point_display()
            self.focusMapWidget.enable_updating_focus_points_on_signal()
        self.setEnabled_all(True)
        self.toggle_coordinate_controls(self.has_loaded_coordinates)

    def setEnabled_all(self, enabled):
        for widget in self.findChildren(QWidget):
            if (
                widget != self.btn_startAcquisition
                and widget != self.progress_bar
                and widget != self.progress_label
                and widget != self.eta_label
            ):
                widget.setEnabled(enabled)

            if self.scanCoordinates.format == "glass slide":
                self.entry_well_coverage.setEnabled(False)

        # Restore scan controls visibility based on XY checkbox state
        if enabled:
            self.update_scan_control_ui()

            # Restore mode dropdown states based on their respective checkboxes
            self.combobox_xy_mode.setEnabled(self.checkbox_xy.isChecked())
            self.combobox_z_mode.setEnabled(self.checkbox_z.isChecked())

            # Restore Z controls based on Z mode
            if self.checkbox_z.isChecked() and self.combobox_z_mode.currentText() == "Set Range":
                # In Set Range mode, Nz should be disabled
                self.entry_NZ.setEnabled(False)

            # Restore coverage based on XY mode
            if self.checkbox_xy.isChecked() and self.combobox_xy_mode.currentText() == "Current Position":
                # In Current Position mode, coverage should be disabled (N/A)
                self.entry_well_coverage.setEnabled(False)

    def disable_the_start_aquisition_button(self):
        self.btn_startAcquisition.setEnabled(False)

    def enable_the_start_aquisition_button(self):
        self.btn_startAcquisition.setEnabled(True)

    def set_performance_mode(self, enabled):
        self.performance_mode = enabled

    def set_saving_dir(self):
        dialog = QFileDialog()
        save_dir_base = dialog.getExistingDirectory(None, "Select Folder")
        if save_dir_base:  # Only update if user didn't cancel
            self.multipointController.set_base_path(save_dir_base)
            self.lineEdit_savingDir.setText(save_dir_base)
            self.base_path_is_set = True
            save_last_used_saving_path(save_dir_base)

    def on_snap_images(self):
        if not self.list_configurations.selectedItems():
            QMessageBox.warning(self, "Warning", "Please select at least one imaging channel")
            return

        # Set the selected channels for acquisition
        self.multipointController.set_selected_configurations(
            [item.text() for item in self.list_configurations.selectedItems()]
        )
        # Set the acquisition parameters
        self.multipointController.set_deltaZ(0)
        self.multipointController.set_NZ(1)
        self.multipointController.set_deltat(0)
        self.multipointController.set_Nt(1)
        self.multipointController.set_use_piezo(False)
        self.multipointController.set_af_flag(False)
        self.multipointController.set_reflection_af_flag(False)
        self.multipointController.set_use_fluidics(False)

        z = self.stage.get_pos().z_mm
        self.multipointController.set_z_range(z, z)
        # Start the acquisition process for the single FOV
        self.multipointController.start_new_experiment("snapped images" + self.lineEdit_experimentID.text())
        self.multipointController.run_acquisition(acquire_current_fov=True)

    def set_deltaZ(self, value):
        if self.checkbox_usePiezo.isChecked():
            deltaZ = value
        else:
            mm_per_ustep = 1.0 / self.stage.get_config().Z_AXIS.convert_real_units_to_ustep(1.0)
            deltaZ = round(value / 1000 / mm_per_ustep) * mm_per_ustep * 1000
        self.entry_deltaZ.setValue(deltaZ)
        self.multipointController.set_deltaZ(deltaZ)

    def emit_selected_channels(self):
        selected_channels = [item.text() for item in self.list_configurations.selectedItems()]
        self.signal_acquisition_channels.emit(selected_channels)

    def refresh_channel_list(self):
        """Refresh the channel list after configuration changes."""
        # Remember currently selected channels
        selected_names = [item.text() for item in self.list_configurations.selectedItems()]

        # Clear and repopulate
        self.list_configurations.blockSignals(True)
        self.list_configurations.clear()
        for config in self.liveController.get_channels(self.objectiveStore.current_objective):
            self.list_configurations.addItem(config.name)

        # Restore selection where possible
        for i in range(self.list_configurations.count()):
            item = self.list_configurations.item(i)
            if item.text() in selected_names:
                item.setSelected(True)
        self.list_configurations.blockSignals(False)

    def toggle_coordinate_controls(self, has_coordinates: bool):
        """Toggle button text and control states based on whether coordinates are loaded"""
        if has_coordinates:
            self.btn_save_scan_coordinates.setText("Clear Coordinates")
            # Disable scan controls when coordinates are loaded
            self.combobox_shape.setEnabled(False)
            self.entry_scan_size.setEnabled(False)
            self.entry_well_coverage.setEnabled(False)
            self.entry_overlap.setEnabled(False)
            # Disable well selector
            if self.well_selection_widget is not None:
                self.well_selection_widget.setEnabled(False)
        else:
            self.btn_save_scan_coordinates.setText("Save Coordinates")
            # Re-enable scan controls when coordinates are cleared - use update_scan_control_ui for proper logic
            self.update_scan_control_ui()

        self.has_loaded_coordinates = has_coordinates

    def on_save_or_clear_coordinates_clicked(self):
        """Handle save/clear coordinates button click"""
        if self.has_loaded_coordinates:
            # Clear coordinates
            self.scanCoordinates.clear_regions()
            self.toggle_coordinate_controls(has_coordinates=False)
            # Update display/coordinates as needed
            self.update_coordinates()
        else:
            # Save coordinates (existing save functionality)
            self.save_coordinates()

    def on_load_coordinates_clicked(self):
        """Open file dialog and load coordinates from selected CSV file"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Load Scan Coordinates", "", "CSV Files (*.csv);;All Files (*)"  # Default directory
        )

        if file_path:
            self._log.info(f"Loading coordinates from {file_path}")
            self.load_coordinates(file_path)

    def restore_cached_coordinates(self):
        """Restore previously loaded coordinates from cached dataframe"""
        if self.cached_loaded_coordinates_df is None:
            return

        df = self.cached_loaded_coordinates_df

        # Clear existing coordinates
        self.scanCoordinates.clear_regions()

        # Load coordinates into scanCoordinates from cached dataframe
        for region_id in df["region"].unique():
            region_points = df[df["region"] == region_id]
            coords = list(zip(region_points["x (mm)"], region_points["y (mm)"]))
            self.scanCoordinates.region_fov_coordinates[region_id] = coords

            # Calculate and store region center (average of points)
            center_x = region_points["x (mm)"].mean()
            center_y = region_points["y (mm)"].mean()
            self.scanCoordinates.region_centers[region_id] = (center_x, center_y)

            # Register FOVs with navigation viewer
            for x, y in coords:
                self.navigationViewer.register_fov_to_image(x, y)

        # Update text area to show loaded file path
        if self.cached_loaded_file_path:
            self.text_loaded_coordinates.setText(f"Loaded: {self.cached_loaded_file_path}")

    def load_coordinates(self, file_path: str):
        """Load scan coordinates from a CSV file.

        Args:
            file_path: Path to CSV file containing coordinates
        """
        try:
            # Read coordinates from CSV
            df = pd.read_csv(file_path)

            # Validate CSV format
            required_columns = ["region", "x (mm)", "y (mm)"]
            if not all(col in df.columns for col in required_columns):
                raise ValueError("CSV file must contain 'region', 'x (mm)', and 'y (mm)' columns")

            # Cache the dataframe and file path
            self.cached_loaded_coordinates_df = df.copy()
            self.cached_loaded_file_path = file_path

            # Clear existing coordinates
            self.scanCoordinates.clear_regions()

            # Load coordinates into scanCoordinates
            for region_id in df["region"].unique():
                region_points = df[df["region"] == region_id]
                coords = list(zip(region_points["x (mm)"], region_points["y (mm)"]))
                self.scanCoordinates.region_fov_coordinates[region_id] = coords

                # Calculate and store region center (average of points)
                center_x = region_points["x (mm)"].mean()
                center_y = region_points["y (mm)"].mean()
                self.scanCoordinates.region_centers[region_id] = (center_x, center_y)

                # Register FOVs with navigation viewer
                self.navigationViewer.register_fovs_to_image(coords)

            self._log.info(f"Loaded {len(df)} coordinates from {file_path}")

            # Update text area to show loaded file path
            self.text_loaded_coordinates.setText(f"Loaded: {file_path}")

        except Exception as e:
            self._log.error(f"Failed to load coordinates: {str(e)}")
            QMessageBox.warning(self, "Load Error", f"Failed to load coordinates from {file_path}\nError: {str(e)}")

    def save_coordinates(self):
        """Save scan coordinates to a CSV file.

        Opens a file dialog for the user to specify a folder name and location.
        Coordinates are saved in CSV format with headers for each objective.
        """
        # Open file dialog for user to specify folder name and location
        folder_path, _ = QFileDialog.getSaveFileName(
            self, "Create Folder for Scan Coordinates", "", "Folder"  # Default directory
        )

        if folder_path:
            # Create the folder if it doesn't exist
            os.makedirs(folder_path, exist_ok=True)

            folder_name = os.path.basename(folder_path)

            current_objective = self.objectiveStore.current_objective

            def _helper_save_coordinates(self, file_path: str):
                # Get coordinates from scanCoordinates
                coordinates = []
                for region_id, fov_coords in self.scanCoordinates.region_fov_coordinates.items():
                    for x, y in fov_coords:
                        coordinates.append([region_id, x, y])

                # Save to CSV with headers

                df = pd.DataFrame(coordinates, columns=["region", "x (mm)", "y (mm)"])
                df.to_csv(file_path, index=False)

                self._log.info(f"Saved scan coordinates to {file_path}")

            try:
                for objective_name in self.objectiveStore.objectives_dict.keys():
                    if objective_name == current_objective:
                        continue
                    else:
                        self.objectiveStore.set_current_objective(objective_name)
                        self.update_coordinates()
                        obj_file_path = os.path.join(folder_path, f"{folder_name}_{objective_name}.csv")
                        _helper_save_coordinates(self, obj_file_path)

                self.objectiveStore.set_current_objective(current_objective)
                self.update_coordinates()
                obj_file_path = os.path.join(folder_path, f"{folder_name}_{current_objective}.csv")
                _helper_save_coordinates(self, obj_file_path)

            except Exception as e:
                self._log.error(f"Failed to save coordinates: {str(e)}")
                QMessageBox.warning(self, "Save Error", f"Failed to save coordinates to {folder_path}\nError: {str(e)}")

    # ========== Drag-and-Drop for Loading Acquisition YAML ==========
    # Uses AcquisitionYAMLDropMixin for drag-drop handling

    def _get_expected_widget_type(self) -> str:
        """Return the expected widget_type for this widget."""
        return "wellplate"

    def _apply_yaml_settings(self, yaml_data):
        """Apply parsed YAML settings to widget controls."""
        # Collect widgets to block signals
        widgets_to_block = [
            self.entry_NZ,
            self.entry_deltaZ,
            self.entry_Nt,
            self.entry_dt,
            self.entry_overlap,
            self.entry_scan_size,
            self.combobox_shape,
            self.list_configurations,
            self.checkbox_withAutofocus,
            self.checkbox_withReflectionAutofocus,
            self.combobox_xy_mode,
            self.checkbox_xy,
            self.checkbox_z,
            self.checkbox_time,
            self.combobox_z_mode,
            self.checkbox_usePiezo,
        ]

        for widget in widgets_to_block:
            widget.blockSignals(True)

        try:
            # Z-stack settings
            self.checkbox_z.setChecked(yaml_data.nz > 1)
            self.entry_NZ.setValue(yaml_data.nz)
            self.entry_deltaZ.setValue(yaml_data.delta_z_um)

            # Z mode - map YAML config to combobox text
            z_mode_map = {
                "FROM BOTTOM": "From Bottom",
                "SET RANGE": "Set Range",
            }
            z_mode = z_mode_map.get(yaml_data.z_stacking_config, "From Bottom")
            self.combobox_z_mode.setCurrentText(z_mode)

            # Piezo setting
            self.checkbox_usePiezo.setChecked(yaml_data.use_piezo)

            # Time series settings
            self.checkbox_time.setChecked(yaml_data.nt > 1)
            self.entry_Nt.setValue(yaml_data.nt)
            self.entry_dt.setValue(yaml_data.delta_t_s)

            # Overlap
            self.entry_overlap.setValue(yaml_data.overlap_percent)

            # Scan size and shape (wellplate specific)
            if yaml_data.scan_size_mm:
                self.entry_scan_size.setValue(yaml_data.scan_size_mm)
            if yaml_data.scan_shape:
                index = self.combobox_shape.findText(yaml_data.scan_shape)
                if index >= 0:
                    self.combobox_shape.setCurrentIndex(index)

            # Channels
            if yaml_data.channel_names:
                self.list_configurations.clearSelection()
                for i in range(self.list_configurations.count()):
                    item = self.list_configurations.item(i)
                    if item.text() in yaml_data.channel_names:
                        item.setSelected(True)

            # Autofocus
            self.checkbox_withAutofocus.setChecked(yaml_data.contrast_af)
            self.checkbox_withReflectionAutofocus.setChecked(yaml_data.laser_af)

            # XY mode - set to Select Wells for wellplate YAML
            if yaml_data.xy_mode in ["Current Position", "Select Wells", "Manual", "Load Coordinates"]:
                self.combobox_xy_mode.setCurrentText(yaml_data.xy_mode)

            # Load well regions if present and update XY checkbox state
            if yaml_data.wellplate_regions:
                self._load_well_regions(yaml_data.wellplate_regions)
                self.checkbox_xy.setChecked(True)
            else:
                self.checkbox_xy.setChecked(False)

        finally:
            # Unblock all signals
            for widget in widgets_to_block:
                widget.blockSignals(False)

            # Enable/disable mode dropdowns based on checkbox states
            self.combobox_z_mode.setEnabled(self.checkbox_z.isChecked())
            self.combobox_xy_mode.setEnabled(self.checkbox_xy.isChecked())

            # Update all UI components based on checkbox states and mode selections
            self.update_scan_control_ui()
            self.update_control_visibility()
            self.update_tab_styles()
            self.update_coordinates()

    def _load_well_regions(self, regions):
        """Load well regions from YAML and select them in the well selector."""
        if not self.well_selection_widget:
            return

        # Block signals during batch selection to prevent multiple updates
        self.well_selection_widget.blockSignals(True)

        try:
            # Clear current selection
            self.well_selection_widget.clearSelection()

            has_selection = False
            # Parse well names and select them
            for region in regions:
                well_name = region.get("name", "")
                if not well_name:
                    continue

                # Parse well name (e.g., "C4" -> row=2, col=3)
                row, col = self._parse_well_name(well_name)
                if row is not None and col is not None:
                    # Check bounds
                    if row < self.well_selection_widget.rowCount() and col < self.well_selection_widget.columnCount():
                        item = self.well_selection_widget.item(row, col)
                        if item:
                            item.setSelected(True)
                            has_selection = True
        finally:
            # Unblock signals
            self.well_selection_widget.blockSignals(False)

        # Emit signal once to trigger coordinate update
        self.well_selection_widget.signal_wellSelected.emit(has_selection)

    def _parse_well_name(self, well_name: str):
        """Parse well name like 'C4' to (row, col) indices."""
        match = re.match(r"^([A-Z]+)(\d+)$", well_name.upper())
        if not match:
            return None, None

        row_str, col_str = match.groups()

        # Convert row letters to index (A=0, B=1, ..., AA=26, etc.)
        row = 0
        for char in row_str:
            row = row * 26 + (ord(char) - ord("A") + 1)
        row -= 1  # Convert to 0-based index

        col = int(col_str) - 1  # Convert to 0-based index

        return row, col


class MultiPointWithFluidicsWidget(QFrame):
    """A simplified version of WellplateMultiPointWidget for use with fluidics"""

    signal_acquisition_started = Signal(bool)
    signal_acquisition_channels = Signal(list)
    signal_acquisition_shape = Signal(int, float)  # acquisition Nz, dz

    def __init__(
        self,
        stage: AbstractStage,
        navigationViewer,
        multipointController,
        objectiveStore,
        scanCoordinates,
        napariMosaicWidget=None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.stage = stage
        self.navigationViewer = navigationViewer
        self.multipointController = multipointController
        self.objectiveStore = objectiveStore
        self.scanCoordinates = scanCoordinates
        self.napariMosaicWidget = napariMosaicWidget
        self.performance_mode = False

        self.base_path_is_set = False
        self.acquisition_start_time = None
        self.eta_seconds = 0
        self.nRound = 0
        self.is_current_acquisition_widget = False

        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def set_performance_mode(self, enabled):
        self.performance_mode = enabled

    def add_components(self):
        self.btn_setSavingDir = QPushButton("Browse")
        self.btn_setSavingDir.setDefault(False)
        self.btn_setSavingDir.setIcon(QIcon("icon/folder.png"))

        self.lineEdit_savingDir = QLineEdit()
        self.lineEdit_savingDir.setText(DEFAULT_SAVING_PATH)
        self.multipointController.set_base_path(DEFAULT_SAVING_PATH)
        self.base_path_is_set = True

        self.lineEdit_experimentID = QLineEdit()

        # Z-stack controls
        self.entry_deltaZ = QDoubleSpinBox()
        self.entry_deltaZ.setKeyboardTracking(False)
        self.entry_deltaZ.setMinimum(0)
        self.entry_deltaZ.setMaximum(1000)
        self.entry_deltaZ.setSingleStep(0.1)
        self.entry_deltaZ.setValue(Acquisition.DZ)
        self.entry_deltaZ.setDecimals(3)
        self.entry_deltaZ.setSuffix(" μm")
        self.entry_deltaZ.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.entry_NZ = QSpinBox()
        self.entry_NZ.setMinimum(1)
        self.entry_NZ.setMaximum(2000)
        self.entry_NZ.setSingleStep(1)
        self.entry_NZ.setValue(1)

        # Channel configurations
        self.list_configurations = QListWidget()
        for microscope_configuration in self.multipointController.liveController.get_channels(
            self.objectiveStore.current_objective
        ):
            self.list_configurations.addItems([microscope_configuration.name])
        self.list_configurations.setSelectionMode(QAbstractItemView.MultiSelection)

        # Reflection AF checkbox
        self.checkbox_withReflectionAutofocus = QCheckBox("Reflection AF")
        self.checkbox_withReflectionAutofocus.setChecked(MULTIPOINT_REFLECTION_AUTOFOCUS_ENABLE_BY_DEFAULT)
        self.multipointController.set_reflection_af_flag(MULTIPOINT_REFLECTION_AUTOFOCUS_ENABLE_BY_DEFAULT)

        # Piezo checkbox
        self.checkbox_usePiezo = QCheckBox("Piezo Z-Stack")
        self.checkbox_usePiezo.setChecked(MULTIPOINT_USE_PIEZO_FOR_ZSTACKS)

        # Start acquisition button
        self.btn_startAcquisition = QPushButton("Start\n Acquisition ")
        self.btn_startAcquisition.setStyleSheet("background-color: #C2C2FF")
        self.btn_startAcquisition.setCheckable(True)
        self.btn_startAcquisition.setChecked(False)
        self.btn_startAcquisition.setEnabled(False)

        # Progress indicators
        self.progress_label = QLabel("Round -/-")
        self.progress_bar = QProgressBar()
        self.eta_label = QLabel("--:--:--")
        self.progress_bar.setVisible(False)
        self.progress_label.setVisible(False)
        self.eta_label.setVisible(False)
        self.eta_timer = QTimer()

        # Layout setup
        main_layout = QVBoxLayout()
        self.setLayout(main_layout)

        # Saving Path
        saving_path_layout = QHBoxLayout()
        saving_path_layout.addWidget(QLabel("Saving Path"))
        saving_path_layout.addWidget(self.lineEdit_savingDir)
        saving_path_layout.addWidget(self.btn_setSavingDir)
        main_layout.addLayout(saving_path_layout)

        # Experiment ID
        exp_id_layout = QHBoxLayout()
        exp_id_layout.addWidget(QLabel("Experiment ID"))
        exp_id_layout.addWidget(self.lineEdit_experimentID)

        self.btn_load_coordinates = QPushButton("Load Coordinates")
        exp_id_layout.addWidget(self.btn_load_coordinates)

        self.btn_init_fluidics = QPushButton("Init Fluidics")
        # exp_id_layout.addWidget(self.btn_init_fluidics)

        main_layout.addLayout(exp_id_layout)

        # Z-stack controls
        z_stack_layout = QHBoxLayout()
        z_stack_layout.addWidget(QLabel("dz"))
        z_stack_layout.addWidget(self.entry_deltaZ)
        z_stack_layout.addWidget(QLabel("Nz"))
        z_stack_layout.addWidget(self.entry_NZ)

        # Rounds input
        z_stack_layout.addWidget(QLabel("Fluidics Rounds:"))
        self.entry_rounds = QLineEdit()
        z_stack_layout.addWidget(self.entry_rounds)

        main_layout.addLayout(z_stack_layout)

        # Grid layout for channel list and options
        grid = QGridLayout()

        # Channel configurations on left
        grid.addWidget(self.list_configurations, 0, 0)

        # Options layout
        options_layout = QVBoxLayout()
        if SUPPORT_LASER_AUTOFOCUS:
            options_layout.addWidget(self.checkbox_withReflectionAutofocus)
        if HAS_OBJECTIVE_PIEZO:
            options_layout.addWidget(self.checkbox_usePiezo)
            if IS_PIEZO_ONLY:
                self.checkbox_usePiezo.setChecked(True)
                self.checkbox_usePiezo.setVisible(False)

        grid.addLayout(options_layout, 0, 2)

        # Start button on far right
        grid.addWidget(self.btn_startAcquisition, 0, 4)

        # Add spacers between columns
        spacer_widget1 = QWidget()
        spacer_widget1.setFixedWidth(2)
        grid.addWidget(spacer_widget1, 0, 1)

        spacer_widget2 = QWidget()
        spacer_widget2.setFixedWidth(2)
        grid.addWidget(spacer_widget2, 0, 3)

        # Set column stretches
        grid.setColumnStretch(0, 2)  # Channel list - half width
        grid.setColumnStretch(1, 0)  # First spacer
        grid.setColumnStretch(2, 1)  # Options
        grid.setColumnStretch(3, 0)  # Second spacer
        grid.setColumnStretch(4, 1)  # Start button

        main_layout.addLayout(grid)

        # Progress bar layout
        progress_layout = QHBoxLayout()
        progress_layout.addWidget(self.progress_label)
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.eta_label)
        main_layout.addLayout(progress_layout)

        # Connect signals
        self.btn_setSavingDir.clicked.connect(self.set_saving_dir)
        self.btn_startAcquisition.clicked.connect(self.toggle_acquisition)
        self.btn_load_coordinates.clicked.connect(self.on_load_coordinates_clicked)
        # self.btn_init_fluidics.clicked.connect(self.init_fluidics)
        self.entry_deltaZ.valueChanged.connect(self.set_deltaZ)
        self.entry_NZ.valueChanged.connect(self.multipointController.set_NZ)
        self.checkbox_withReflectionAutofocus.toggled.connect(self.multipointController.set_reflection_af_flag)
        self.checkbox_usePiezo.toggled.connect(self.multipointController.set_use_piezo)
        self.list_configurations.itemSelectionChanged.connect(self.emit_selected_channels)
        self.multipointController.acquisition_finished.connect(self.acquisition_is_finished)
        self.multipointController.signal_acquisition_progress.connect(self.update_acquisition_progress)
        self.multipointController.signal_region_progress.connect(self.update_region_progress)
        self.signal_acquisition_started.connect(self.display_progress_bar)
        self.eta_timer.timeout.connect(self.update_eta_display)

    # The following methods are copied from WellplateMultiPointWidget with minimal modifications
    def toggle_acquisition(self, pressed):
        rounds = self.get_rounds()
        if pressed:
            if not self.base_path_is_set:
                self.btn_startAcquisition.setChecked(False)
                QMessageBox.warning(self, "Warning", "Please choose base saving directory first")
                return

            if not self.list_configurations.selectedItems():
                self.btn_startAcquisition.setChecked(False)
                QMessageBox.warning(self, "Warning", "Please select at least one imaging channel")
                return

            if self.multipointController.acquisition_in_progress():
                self._log.warning("Acquisition in progress or aborting, cannot start another yet.")
                self.btn_startAcquisition.setChecked(False)
                return

            if not rounds:
                self.btn_startAcquisition.setChecked(False)
                QMessageBox.warning(self, "Warning", "Please enter valid round numbers (1-24)")
                return

            num_fovs = sum(len(coords) for coords in self.scanCoordinates.region_fov_coordinates.values())
            if num_fovs <= 0:
                self.btn_startAcquisition.setChecked(False)
                QMessageBox.warning(self, "Warning", "Please load coordinates first")
                return

            msg = (
                f"About to start acquisition with:\n"
                f"- Regions: {len(self.scanCoordinates.region_fov_coordinates)}\n"
                f"- FOVs: {num_fovs}\n"
                f"- Rounds: {len(rounds)}\n\n"
                f"Continue?"
            )
            reply = QMessageBox.question(
                self,
                "Confirm Acquisition",
                msg,
                QMessageBox.Ok | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if reply != QMessageBox.Ok:
                self.btn_startAcquisition.setChecked(False)
                return

            self.setEnabled_all(False)
            self.is_current_acquisition_widget = True
            self.btn_startAcquisition.setText("Stop\n Acquisition ")

            self.multipointController.set_deltaZ(self.entry_deltaZ.value())
            self.multipointController.set_NZ(self.entry_NZ.value())
            self.multipointController.set_use_piezo(self.checkbox_usePiezo.isChecked())
            self.multipointController.set_reflection_af_flag(self.checkbox_withReflectionAutofocus.isChecked())
            self.multipointController.set_base_path(self.lineEdit_savingDir.text())
            self.multipointController.set_use_fluidics(True)  # may be set to False from other widgets
            self.multipointController.set_selected_configurations(
                [item.text() for item in self.list_configurations.selectedItems()]
            )
            self.multipointController.set_Nt(len(rounds))
            self.multipointController.fluidics.set_rounds(rounds)
            self.multipointController.start_new_experiment(self.lineEdit_experimentID.text())

            # Emit signals
            self.signal_acquisition_started.emit(True)
            self.signal_acquisition_shape.emit(self.entry_NZ.value(), self.entry_deltaZ.value())

            # Start acquisition
            self.multipointController.run_acquisition()
        else:
            self.multipointController.request_abort_aquisition()

    def set_saving_dir(self):
        """Open dialog to set saving directory"""
        dialog = QFileDialog()
        save_dir_base = dialog.getExistingDirectory(None, "Select Folder")
        self.multipointController.set_base_path(save_dir_base)
        self.lineEdit_savingDir.setText(save_dir_base)
        self.base_path_is_set = True

    def update_dz(self):
        z_min = self.entry_minZ.value()
        z_max = self.entry_maxZ.value()
        nz = self.entry_NZ.value()
        dz = (z_max - z_min) / (nz - 1) if nz > 1 else 0
        self.entry_deltaZ.setValue(dz)

    def update_Nz(self):
        z_min = self.entry_minZ.value()
        z_max = self.entry_maxZ.value()
        dz = self.entry_deltaZ.value()
        nz = math.ceil((z_max - z_min) / dz) + 1
        self.entry_NZ.setValue(nz)

    def set_deltaZ(self, value):
        """Set Z-stack step size, adjusting for piezo if needed"""
        if self.checkbox_usePiezo.isChecked():
            deltaZ = value
        else:
            mm_per_ustep = 1.0 / self.stage.get_config().Z_AXIS.convert_real_units_to_ustep(1.0)
            deltaZ = round(value / 1000 / mm_per_ustep) * mm_per_ustep * 1000
        self.entry_deltaZ.setValue(deltaZ)
        self.multipointController.set_deltaZ(deltaZ)

    def emit_selected_channels(self):
        """Emit signal with list of selected channel names"""
        selected_channels = [item.text() for item in self.list_configurations.selectedItems()]
        self.signal_acquisition_channels.emit(selected_channels)

    def acquisition_is_finished(self):
        """Handle acquisition completion"""
        self._log.debug(
            f"In MultiPointWithFluidicsWidget, got acquisition_is_finished with {self.is_current_acquisition_widget=}"
        )
        if not self.is_current_acquisition_widget:
            return  # Skip if this wasn't the widget that started acquisition

        self.signal_acquisition_started.emit(False)
        self.is_current_acquisition_widget = False
        self.btn_startAcquisition.setChecked(False)
        self.btn_startAcquisition.setText("Start\n Acquisition ")
        self.setEnabled_all(True)

    def setEnabled_all(self, enabled):
        """Enable/disable all widget controls"""
        for widget in self.findChildren(QWidget):
            if (
                widget != self.btn_startAcquisition
                and widget != self.progress_bar
                and widget != self.progress_label
                and widget != self.eta_label
            ):
                widget.setEnabled(enabled)

    def disable_the_start_aquisition_button(self):
        self.btn_startAcquisition.setEnabled(False)

    def enable_the_start_aquisition_button(self):
        self.btn_startAcquisition.setEnabled(True)

    def update_region_progress(self, current_fov, num_fovs):
        self.progress_bar.setMaximum(num_fovs)
        self.progress_bar.setValue(current_fov)

        if self.acquisition_start_time is not None and current_fov > 0:
            elapsed_time = time.time() - self.acquisition_start_time
            Nt = self.nRound

            # Calculate total processed FOVs and total FOVs
            processed_fovs = (
                (self.current_region - 1) * num_fovs
                + current_fov
                + self.current_time_point * self.num_regions * num_fovs
            )
            total_fovs = self.num_regions * num_fovs * Nt
            remaining_fovs = total_fovs - processed_fovs

            # Calculate ETA
            fov_per_second = processed_fovs / elapsed_time
            self.eta_seconds = remaining_fovs / fov_per_second if fov_per_second > 0 else 0
            self.update_eta_display()

            # Start or restart the timer
            self.eta_timer.start(1000)  # Update every 1000 ms (1 second)

    def update_acquisition_progress(self, current_region, num_regions, current_time_point):
        self.current_region = current_region
        self.current_time_point = current_time_point

        if self.current_region == 1 and self.current_time_point == 0:  # First region
            self.acquisition_start_time = time.time()
            self.num_regions = num_regions

        progress_parts = []
        # Update timepoint progress if there are multiple timepoints and the timepoint has changed
        if self.nRound > 1:
            progress_parts.append(f"Round {current_time_point + 1}/{self.nRound}")

        # Update region progress if there are multiple regions
        if num_regions > 1:
            progress_parts.append(f"Region {current_region}/{num_regions}")

        # Set the progress label text, ensuring it's not empty
        progress_text = "  ".join(progress_parts)
        self.progress_label.setText(progress_text if progress_text else "Progress")
        self.progress_bar.setValue(0)

    def update_eta_display(self):
        """Update the estimated time remaining display"""
        if self.eta_seconds > 0:
            self.eta_seconds -= 1  # Decrease by 1 second
            hours, remainder = divmod(int(self.eta_seconds), 3600)
            minutes, seconds = divmod(remainder, 60)
            if hours > 0:
                eta_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            else:
                eta_str = f"{minutes:02d}:{seconds:02d}"
            self.eta_label.setText(f"{eta_str}")
        else:
            self.eta_timer.stop()
            self.eta_label.setText("00:00")

    def display_progress_bar(self, show):
        """Show/hide progress tracking widgets"""
        self.progress_label.setVisible(show)
        self.progress_bar.setVisible(show)
        self.eta_label.setVisible(show)
        if show:
            self.progress_bar.setValue(0)
            self.progress_label.setText("Round 0/0")
            self.eta_label.setText("--:--")
            self.acquisition_start_time = None
        else:
            self.eta_timer.stop()

    def on_load_coordinates_clicked(self):
        """Open file dialog and load coordinates from selected CSV file"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Load Scan Coordinates", "", "CSV Files (*.csv);;All Files (*)"
        )

        if file_path:
            self._log.info(f"Loading coordinates from {file_path}")
            self.load_coordinates(file_path)

    def load_coordinates(self, file_path: str):
        """Load scan coordinates from a CSV file.

        Args:
            file_path: Path to CSV file containing coordinates
        """
        try:
            # Read coordinates from CSV
            df = pd.read_csv(file_path)

            # Validate CSV format
            required_columns = ["region", "x (mm)", "y (mm)"]
            if not all(col in df.columns for col in required_columns):
                raise ValueError("CSV file must contain 'region', 'x (mm)', and 'y (mm)' columns")

            # Clear existing coordinates
            self.scanCoordinates.clear_regions()

            # Load coordinates into scanCoordinates
            for region_id in df["region"].unique():
                region_points = df[df["region"] == region_id]
                coords = list(zip(region_points["x (mm)"], region_points["y (mm)"]))
                self.scanCoordinates.region_fov_coordinates[region_id] = coords

                # Calculate and store region center (average of points)
                center_x = region_points["x (mm)"].mean()
                center_y = region_points["y (mm)"].mean()
                self.scanCoordinates.region_centers[region_id] = (center_x, center_y)

                # Register FOVs with navigation viewer
                self.navigationViewer.register_fovs_to_image(coords)

            self._log.info(f"Loaded {len(df)} coordinates from {file_path}")

        except Exception as e:
            self._log.error(f"Failed to load coordinates: {str(e)}")
            QMessageBox.warning(self, "Load Error", f"Failed to load coordinates from {file_path}\nError: {str(e)}")

    def init_fluidics(self):
        """Initialize the fluidics system"""
        # self.multipointController.fluidics.initialize()
        self.btn_startAcquisition.setEnabled(True)

    def get_rounds(self) -> list:
        """Parse rounds input string into a list of round numbers.

        Accepts formats like:
        - Single numbers: "1,3,5"
        - Ranges: "1-3,5,7-10"

        Returns:
            List of integers representing rounds, sorted without duplicates.
            Empty list if input is invalid.
        """
        try:
            rounds_str = self.entry_rounds.text().strip()
            if not rounds_str:
                return []

            rounds = []

            # Split by comma and process each part
            for part in rounds_str.split(","):
                part = part.strip()
                if "-" in part:
                    # Handle range (e.g., "1-3")
                    start, end = map(int, part.split("-"))
                    if start < 1 or end > 24 or start > end:
                        raise ValueError(
                            f"Invalid range {part}: Numbers must be between 1 and 24, and start must be <= end"
                        )
                    rounds.extend(range(start, end + 1))
                else:
                    # Handle single number
                    num = int(part)
                    if num < 1 or num > 24:
                        raise ValueError(f"Invalid number {num}: Must be between 1 and 24")
                    rounds.append(num)

            self.nRound = len(rounds)

            return rounds

        except ValueError as e:
            QMessageBox.warning(self, "Invalid Input", str(e))
            return []
        except Exception as e:
            QMessageBox.warning(self, "Invalid Input", "Please enter valid round numbers (e.g., '1-3,5,7-10')")
            return []


class FluidicsWidget(QWidget):

    log_message_signal = Signal(str)
    fluidics_initialized_signal = Signal()

    def __init__(self, fluidics, parent=None):
        super().__init__(parent)
        self._log = squid.logging.get_logger(self.__class__.__name__)

        # Initialize data structures
        self.fluidics = fluidics
        self.fluidics.log_callback = self.log_message_signal.emit
        self.set_sequence_callbacks()

        # Set up the UI
        self.setup_ui()
        self.log_message_signal.connect(self.log_status)

    def setup_ui(self):
        # Main layout
        main_layout = QHBoxLayout()
        self.setLayout(main_layout)

        # Left side - Control panels
        left_panel = QVBoxLayout()

        # Fluidics Control panel
        fluidics_control_group = QGroupBox("Fluidics Control")
        fluidics_control_layout = QVBoxLayout()

        # First row - Initialize and Load Sequences
        init_row = QHBoxLayout()
        self.btn_initialize = QPushButton("Initialize")
        self.btn_load_sequences = QPushButton("Load Sequences")
        init_row.addWidget(self.btn_initialize)
        init_row.addWidget(self.btn_load_sequences)
        fluidics_control_layout.addLayout(init_row)

        # Second row - Prime Ports
        prime_row = QHBoxLayout()
        prime_row.addWidget(QLabel("Prime Ports:"))
        prime_row.addWidget(QLabel("Ports"))
        self.txt_prime_ports = QLineEdit()
        prime_row.addWidget(self.txt_prime_ports)
        prime_row.addWidget(QLabel("Fill Tubing With"))
        self.prime_fill_combo = QComboBox()
        self.prime_fill_combo.addItems(self.fluidics.available_port_names)
        self.prime_fill_combo.setCurrentIndex(25 - 1)  # Usually Port 25 should be the common wash buffer port
        prime_row.addWidget(self.prime_fill_combo)
        prime_row.addWidget(QLabel("Volume (µL)"))
        self.txt_prime_volume = QLineEdit()
        self.txt_prime_volume.setText("2000")
        prime_row.addWidget(self.txt_prime_volume)
        self.btn_prime_start = QPushButton("Start")
        prime_row.addWidget(self.btn_prime_start)
        fluidics_control_layout.addLayout(prime_row)

        # Third row - Clean Up
        cleanup_row = QHBoxLayout()
        cleanup_row.addWidget(QLabel("Clean Up:"))
        cleanup_row.addWidget(QLabel("Ports"))
        self.txt_cleanup_ports = QLineEdit()
        cleanup_row.addWidget(self.txt_cleanup_ports)
        cleanup_row.addWidget(QLabel("Fill Tubing With"))
        self.cleanup_fill_combo = QComboBox()
        self.cleanup_fill_combo.addItems(self.fluidics.available_port_names)
        self.cleanup_fill_combo.setCurrentIndex(25 - 1)
        cleanup_row.addWidget(self.cleanup_fill_combo)
        cleanup_row.addWidget(QLabel("Volume (µL)"))
        self.txt_cleanup_volume = QLineEdit()
        self.txt_cleanup_volume.setText("2000")
        cleanup_row.addWidget(self.txt_cleanup_volume)
        cleanup_row.addWidget(QLabel("Repeat"))
        self.txt_cleanup_repeat = QLineEdit()
        self.txt_cleanup_repeat.setText("3")
        cleanup_row.addWidget(self.txt_cleanup_repeat)
        self.btn_cleanup_start = QPushButton("Start")
        cleanup_row.addWidget(self.btn_cleanup_start)
        fluidics_control_layout.addLayout(cleanup_row)

        fluidics_control_group.setLayout(fluidics_control_layout)
        left_panel.addWidget(fluidics_control_group)

        # Manual Control panel
        manual_control_group = QGroupBox("Manual Control")
        manual_control_layout = QVBoxLayout()

        # First row - Port, Flow Rate, Volume, Flow button
        manual_row1 = QHBoxLayout()
        manual_row1.addWidget(QLabel("Port"))
        self.manual_port_combo = QComboBox()
        self.manual_port_combo.addItems(self.fluidics.available_port_names)
        manual_row1.addWidget(self.manual_port_combo)
        manual_row1.addWidget(QLabel("Flow Rate (µL/min)"))
        self.txt_manual_flow_rate = QLineEdit()
        self.txt_manual_flow_rate.setText("500")
        manual_row1.addWidget(self.txt_manual_flow_rate)
        manual_row1.addWidget(QLabel("Volume (µL)"))
        self.txt_manual_volume = QLineEdit()
        manual_row1.addWidget(self.txt_manual_volume)
        self.btn_manual_flow = QPushButton("Flow")
        manual_row1.addWidget(self.btn_manual_flow)
        manual_control_layout.addLayout(manual_row1)

        # Second row - Empty Syringe Pump button
        manual_row2 = QHBoxLayout()
        self.btn_empty_syringe_pump = QPushButton("Empty Syringe Pump To Waste")
        manual_row2.addWidget(self.btn_empty_syringe_pump)
        manual_control_layout.addLayout(manual_row2)

        manual_control_group.setLayout(manual_control_layout)
        left_panel.addWidget(manual_control_group)

        # Status panel
        status_group = QGroupBox("Status")
        status_layout = QVBoxLayout()

        self.status_text = QTextEdit()
        self.status_text.setReadOnly(True)
        status_layout.addWidget(self.status_text)

        status_group.setLayout(status_layout)
        left_panel.addWidget(status_group)

        # Add left panel to main layout
        main_layout.addLayout(left_panel, 1)

        # Right side - Sequences panel
        right_panel = QVBoxLayout()

        sequences_group = QGroupBox("Sequences")
        sequences_layout = QVBoxLayout()

        # Table for sequences
        self.sequences_table = QTableView()
        sequences_layout.addWidget(self.sequences_table)

        # Emergency Stop button
        self.btn_emergency_stop = QPushButton("Emergency Stop")
        self.btn_emergency_stop.setStyleSheet("background-color: red; color: white; font-weight: bold;")
        sequences_layout.addWidget(self.btn_emergency_stop)

        sequences_group.setLayout(sequences_layout)
        right_panel.addWidget(sequences_group)

        # Add right panel to main layout
        main_layout.addLayout(right_panel, 1)

        # Connect signals
        self.btn_initialize.clicked.connect(self.initialize_fluidics)
        self.btn_load_sequences.clicked.connect(self.load_sequences)
        self.btn_prime_start.clicked.connect(self.start_prime)
        self.btn_cleanup_start.clicked.connect(self.start_cleanup)
        self.btn_manual_flow.clicked.connect(self.start_manual_flow)
        self.btn_empty_syringe_pump.clicked.connect(self.empty_syringe_pump)
        self.btn_emergency_stop.clicked.connect(self.emergency_stop)

        self.enable_controls(False)
        self.btn_emergency_stop.setEnabled(False)

    def initialize_fluidics(self):
        """Initialize the fluidics system"""
        self.log_status("Initializing fluidics system...")
        self.fluidics.initialize()
        self.btn_initialize.setEnabled(False)
        self.enable_controls(True)
        self.btn_emergency_stop.setEnabled(True)
        self.fluidics_initialized_signal.emit()

    def set_sequence_callbacks(self):
        callbacks = {
            "on_finished": self.on_finish,
            "on_error": self.on_finish,
            "on_estimate": self.on_estimate,
            "update_progress": self.update_progress,
        }
        self.fluidics.worker_callbacks = callbacks

    def set_manual_control_callbacks(self):
        # TODO: use better logging description
        callbacks = {
            "on_finished": lambda: self.on_finish("Operation completed"),
            "on_error": self.on_finish,
            "on_estimate": None,
            "update_progress": None,
        }
        self.fluidics.worker_callbacks = callbacks

    def load_sequences(self):
        """Open file dialog to load sequences from CSV"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Load Fluidics Sequences", "", "CSV Files (*.csv);;All Files (*)"
        )

        if file_path:
            self.log_status(f"Loading sequences from {file_path}")
            try:
                self.sequence_df = self.fluidics.load_sequences(file_path)
                self.sequence_df.drop("include", axis=1, inplace=True)
                model = PandasTableModel(self.sequence_df, self.fluidics.available_port_names)
                self.sequences_table.setModel(model)
                self.sequences_table.resizeColumnsToContents()
                self.sequences_table.horizontalHeader().setStretchLastSection(True)
                self.log_status(f"Loaded {len(self.sequence_df)} sequences")
            except Exception as e:
                self.log_status(f"Error loading sequences: {str(e)}")

    def start_prime(self):
        self.set_manual_control_callbacks()
        ports = self.get_port_list(self.txt_prime_ports.text())
        fill_port = self.prime_fill_combo.currentIndex() + 1
        volume = int(self.txt_prime_volume.text())

        if not ports or not fill_port or not volume:
            return

        self.log_status(f"Starting prime: Ports {ports}, Fill with {fill_port}, Volume {volume}µL")
        self.fluidics.priming(ports, fill_port, volume)
        self.enable_controls(False)
        self.set_sequence_callbacks()

    def start_cleanup(self):
        self.set_manual_control_callbacks()
        ports = self.get_port_list(self.txt_cleanup_ports.text())
        fill_port = self.cleanup_fill_combo.currentIndex() + 1
        volume = int(self.txt_cleanup_volume.text())
        repeat = int(self.txt_cleanup_repeat.text())

        if not ports or not fill_port or not volume or not repeat:
            return

        self.log_status(f"Starting cleanup: Ports {ports}, Fill with {fill_port}, Volume {volume}µL, Repeat {repeat}x")
        self.fluidics.clean_up(ports, fill_port, volume, repeat)
        self.enable_controls(False)
        self.set_sequence_callbacks()

    def start_manual_flow(self):
        self.set_manual_control_callbacks()
        port = self.manual_port_combo.currentIndex() + 1
        flow_rate = int(self.txt_manual_flow_rate.text())
        volume = int(self.txt_manual_volume.text())

        if not port or not flow_rate or not volume:
            return

        self.log_status(f"Flow reagent: Port {port}, Flow rate {flow_rate}µL/min, Volume {volume}µL")
        self.fluidics.manual_flow(port, flow_rate, volume)
        self.enable_controls(False)
        self.set_sequence_callbacks()

    def empty_syringe_pump(self):
        self.log_status("Empty syringe pump to waste")
        self.enable_controls(False)
        self.fluidics.empty_syringe_pump()
        self.log_status("Operation completed")
        self.enable_controls(True)

    def emergency_stop(self):
        self.fluidics.emergency_stop()

    def get_port_list(self, text: str) -> list:
        """Parse ports input string into a list of numbers.

        Accepts formats like:
        - Single numbers: "1,3,5"
        - Ranges: "1-3,5,7-10"

        Returns:
            List of integers representing rounds, sorted without duplicates.
            Empty list if input is invalid.
        """
        try:
            ports_str = text.strip()
            if not ports_str:
                return [i for i in range(1, len(self.fluidics.available_port_names) + 1)]

            port_list = []

            # Split by comma and process each part
            for part in ports_str.split(","):
                part = part.strip()
                if "-" in part:
                    # Handle range (e.g., "1-3")
                    start, end = map(int, part.split("-"))
                    if start < 1 or end > 28 or start > end:
                        raise ValueError(
                            f"Invalid range {part}: Numbers must be between 1 and 28, and start must be <= end"
                        )
                    port_list.extend(range(start, end + 1))
                else:
                    # Handle single number
                    num = int(part)
                    if num < 1 or num > 28:
                        raise ValueError(f"Invalid number {num}: Must be between 1 and 28")
                    port_list.append(num)

            return port_list

        except ValueError as e:
            QMessageBox.warning(self, "Invalid Input", str(e))
            return []
        except Exception as e:
            QMessageBox.warning(self, "Invalid Input", "Please enter valid port numbers (e.g., '1-3,5,7-10')")
            return []

    def update_progress(self, idx, seq_num, status):
        self.sequences_table.model().set_current_row(idx)
        self.log_message_signal.emit(f"Sequence {self.sequence_df.iloc[idx]['sequence_name']} {status}")

    def on_finish(self, status=None):
        self.enable_controls(True)
        try:
            self.sequences_table.model().set_current_row(-1)
        except:
            pass
        if status is None:
            status = "Sequence section completed"
        self.fluidics.reset_abort()
        self.log_message_signal.emit(status)

    def on_estimate(self, time, n):
        self.log_message_signal.emit(f"Estimated time: {time}s, Sequences: {n}")

    def enable_controls(self, enabled: bool):
        self.btn_load_sequences.setEnabled(enabled)
        self.btn_prime_start.setEnabled(enabled)
        self.btn_cleanup_start.setEnabled(enabled)
        self.btn_manual_flow.setEnabled(enabled)
        self.btn_empty_syringe_pump.setEnabled(enabled)

    def log_status(self, message):
        current_time = QDateTime.currentDateTime().toString("hh:mm:ss")
        self.status_text.append(f"[{current_time}] {message}")
        # Scroll to bottom
        scrollbar = self.status_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        # Also log to console
        self._log.info(message)


class PandasTableModel(QAbstractTableModel):
    """Model for displaying pandas DataFrame in a QTableView"""

    def __init__(self, data, port_names=None):
        super().__init__()
        self._data = data
        self._current_row = -1
        self._port_names = port_names or []
        self._column_name_map = {
            "sequence_name": "Sequence Name",
            "fluidic_port": "Fluidic Port",
            "fill_tubing_with": "Fill Tubing With",
            "flow_rate": "Flow Rate (µL/min)",
            "volume": "Volume (µL)",
            "incubation_time": "Incubation (min)",
            "repeat": "Repeat",
        }

    def rowCount(self, parent=None):
        return len(self._data)

    def columnCount(self, parent=None):
        return len(self._data.columns)

    def data(self, index, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            value = self._data.iloc[index.row(), index.column()]
            if pd.isna(value):
                return ""

            # Map port numbers to names for specific columns
            column_name = self._data.columns[index.column()]
            if column_name in ["fluidic_port", "fill_tubing_with"] and self._port_names:
                try:
                    # Convert value to integer and get corresponding name
                    port_num = int(value)
                    if 1 <= port_num <= len(self._port_names):
                        return self._port_names[port_num - 1]
                except (ValueError, TypeError):
                    pass

            return str(value)

        elif role == Qt.BackgroundRole:
            # Highlight the current row
            if index.row() == self._current_row:
                return QBrush(QColor(173, 216, 230))  # Light blue
            else:
                return QBrush(QColor(255, 255, 255))  # White
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            original_name = str(self._data.columns[section])
            return self._column_name_map.get(original_name, original_name)
        if orientation == Qt.Vertical and role == Qt.DisplayRole:
            return str(section + 1)
        return None

    def set_current_row(self, row_index):
        self._current_row = row_index
        self.dataChanged.emit(self.index(0, 0), self.index(self.rowCount() - 1, self.columnCount() - 1))


class FocusMapWidget(QFrame):
    """Widget for managing focus map points and surface fitting"""

    def __init__(self, stage: AbstractStage, navigationViewer, scanCoordinates, focusMap):
        super().__init__()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        self._allow_updating_focus_points_on_signal = True

        # Store controllers
        self.stage = stage
        self.navigationViewer = navigationViewer
        self.scanCoordinates = scanCoordinates
        self.focusMap = focusMap

        # Store focus points in widget
        self.focus_points = []  # list of (region_id, x, y, z) tuples
        self.enabled = False  # toggled when focus map enabled for next acquisition

        self.setup_ui()
        self.make_connections()
        self.setEnabled(False)
        self.add_margin = True  # margin for focus grid makes it smaller, but will avoid points at the borders

    def setup_ui(self):
        """Create and arrange UI components"""
        self.layout = QVBoxLayout(self)

        # Point combo and Z control
        controls_layout = QHBoxLayout()
        controls_layout.addWidget(QLabel("Focus Point:"))
        self.point_combo = QComboBox()
        controls_layout.addWidget(self.point_combo, stretch=1)
        self.update_z_btn = QPushButton("Update Z")
        controls_layout.addWidget(self.update_z_btn)
        self.layout.addLayout(controls_layout)

        # Point control buttons - line 1
        point_controls = QHBoxLayout()
        self.add_point_btn = QPushButton("Add")
        self.remove_point_btn = QPushButton("Remove")
        self.next_point_btn = QPushButton("Next")
        self.edit_point_btn = QPushButton("Edit")
        point_controls.addWidget(self.add_point_btn)
        point_controls.addWidget(self.remove_point_btn)
        point_controls.addWidget(self.next_point_btn)
        point_controls.addWidget(self.edit_point_btn)
        self.layout.addLayout(point_controls)

        # Point control buttons - line 2
        point_controls_2 = QHBoxLayout()
        point_controls_2.addWidget(QLabel("Focus Grid:"))
        self.rows_spin = QSpinBox()
        self.rows_spin.setKeyboardTracking(False)
        self.rows_spin.setRange(1, 10)
        self.rows_spin.setValue(4)
        point_controls_2.addWidget(self.rows_spin)
        x_label = QLabel("×")
        x_label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        point_controls_2.addWidget(x_label)
        self.cols_spin = QSpinBox()
        self.cols_spin.setKeyboardTracking(False)
        self.cols_spin.setRange(1, 10)
        self.cols_spin.setValue(4)
        point_controls_2.addWidget(self.cols_spin)
        self.export_btn = QPushButton("Export")
        self.export_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.import_btn = QPushButton("Import")
        self.import_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        point_controls_2.addWidget(self.export_btn)
        point_controls_2.addWidget(self.import_btn)
        self.layout.addLayout(point_controls_2)

        # Surface fitting controls
        settings_layout = QHBoxLayout()
        settings_layout.addWidget(QLabel("Fitting Method:"))
        self.fit_method_combo = QComboBox()
        self.fit_method_combo.addItems(["spline", "rbf", "constant"])
        settings_layout.addWidget(self.fit_method_combo)
        settings_layout.addWidget(QLabel("Smoothing:"))
        self.smoothing_spin = QDoubleSpinBox()
        self.smoothing_spin.setKeyboardTracking(False)
        self.smoothing_spin.setRange(0.01, 1.0)
        self.smoothing_spin.setValue(0.1)
        self.smoothing_spin.setSingleStep(0.05)
        settings_layout.addWidget(self.smoothing_spin)
        self.by_region_checkbox = QCheckBox("Fit by Region")
        self.by_region_checkbox.setChecked(False)
        settings_layout.addWidget(self.by_region_checkbox)
        self.layout.addLayout(settings_layout)

        # Status label - reserve space even when hidden
        self.status_label = QLabel()
        self.status_label.setText(" ")  # Empty text to keep space
        self.layout.addWidget(self.status_label)

    def make_connections(self):
        # Auto-navigate when point selection changes
        self.point_combo.currentIndexChanged.connect(self.goto_selected_point)

        # Update Z for current point
        self.update_z_btn.clicked.connect(self.update_current_z)

        # Connect grid size changes
        self.rows_spin.valueChanged.connect(self.regenerate_grid)
        self.cols_spin.valueChanged.connect(self.regenerate_grid)

        # Connect point control buttons
        self.add_point_btn.clicked.connect(self.add_current_point)
        self.remove_point_btn.clicked.connect(self.remove_current_point)
        self.next_point_btn.clicked.connect(self.goto_next_point)
        self.edit_point_btn.clicked.connect(self.edit_current_point)
        self.export_btn.clicked.connect(self.export_focus_points)
        self.import_btn.clicked.connect(self.import_focus_points)

        # Connect fitting method change
        self.fit_method_combo.currentTextChanged.connect(self._match_by_region_box)

    def update_point_list(self):
        """Update point selection combo showing grid coordinates for points"""
        self.point_combo.blockSignals(True)
        curr_focus_point = self.point_combo.currentIndex()
        self.point_combo.clear()
        for idx, (region_id, x, y, z) in enumerate(self.focus_points):
            point_text = (
                f"{region_id}: "
                + "x:"
                + str(round(x, 3))
                + "mm  y:"
                + str(round(y, 3))
                + "mm  z:"
                + str(round(1000 * z, 2))
                + "μm"
            )
            self.point_combo.addItem(point_text)
        self.point_combo.setCurrentIndex(max(0, min(curr_focus_point, len(self.focus_points) - 1)))
        self.point_combo.blockSignals(False)

    def edit_current_point(self):
        """Edit coordinates of current point in a popup dialog"""
        index = self.point_combo.currentIndex()
        if 0 <= index < len(self.focus_points):
            region_id, x, y, z = self.focus_points[index]

            # Create dialog
            dialog = QDialog(self)
            dialog.setWindowTitle("Edit Focus Point")
            layout = QFormLayout()

            # Add coordinate spinboxes with good precision
            x_spin = QDoubleSpinBox()
            x_spin.setKeyboardTracking(False)
            x_spin.setRange(SOFTWARE_POS_LIMIT.X_NEGATIVE, SOFTWARE_POS_LIMIT.X_POSITIVE)
            x_spin.setDecimals(3)
            x_spin.setValue(x)
            x_spin.setSuffix(" mm")

            y_spin = QDoubleSpinBox()
            y_spin.setKeyboardTracking(False)
            y_spin.setRange(SOFTWARE_POS_LIMIT.Y_NEGATIVE, SOFTWARE_POS_LIMIT.Y_POSITIVE)
            y_spin.setDecimals(3)
            y_spin.setValue(y)
            y_spin.setSuffix(" mm")

            z_spin = QDoubleSpinBox()
            z_spin.setKeyboardTracking(False)
            z_spin.setRange(
                SOFTWARE_POS_LIMIT.Z_NEGATIVE * 1000, SOFTWARE_POS_LIMIT.Z_POSITIVE * 1000
            )  # Convert mm limits to μm
            z_spin.setDecimals(2)
            z_spin.setValue(z * 1000)  # Convert mm to μm
            z_spin.setSuffix(" μm")

            layout.addRow("X:", x_spin)
            layout.addRow("Y:", y_spin)
            layout.addRow("Z:", z_spin)

            # Add OK/Cancel buttons
            buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addRow(buttons)
            dialog.setLayout(layout)

            # Show dialog and handle result
            if dialog.exec_() == QDialog.Accepted:
                new_x = x_spin.value()
                new_y = y_spin.value()
                new_z = z_spin.value() / 1000  # Convert μm back to mm for storage
                self.focus_points[index] = (region_id, new_x, new_y, new_z)
                self.update_point_list()
                self.update_focus_point_display()

    def update_focus_point_display(self):
        """Update all focus points on navigation viewer"""
        self.navigationViewer.clear_focus_points()
        for _, x, y, _ in self.focus_points:
            self.navigationViewer.register_focus_point(x, y)

    def generate_grid(self, rows=4, cols=4):
        """Generate focus point grid that spans scan bounds"""
        if self.enabled:
            self.point_combo.blockSignals(True)
            self.focus_points.clear()
            self.navigationViewer.clear_focus_points()
            self.status_label.setText(" ")
            current_z = self.stage.get_pos().z_mm

            # Use FocusMap to generate coordinates
            coordinates = self.focusMap.generate_grid_coordinates(
                self.scanCoordinates, rows=rows, cols=cols, add_margin=self.add_margin
            )

            # Add points with current z coordinate
            for region_id, coords_list in coordinates.items():
                for coords in coords_list:
                    self.focus_points.append((region_id, coords[0], coords[1], current_z))
                    self.navigationViewer.register_focus_point(coords[0], coords[1])

            self.update_point_list()
            self.point_combo.blockSignals(False)

    def regenerate_grid(self):
        """Generate focus point grid given updated dims"""
        self.generate_grid(self.rows_spin.value(), self.cols_spin.value())

    def add_current_point(self):
        # Check if any scan regions exist
        if not self.scanCoordinates.has_regions():
            QMessageBox.warning(self, "No Regions Defined", "Please define scan regions before adding focus points.")
            return

        pos = self.stage.get_pos()
        region_id = None

        # If by_region checkbox is checked, ask for region ID
        if self.by_region_checkbox.isChecked():
            region_ids = list(self.scanCoordinates.region_centers.keys())
            if not region_ids:
                QMessageBox.warning(
                    self, "No Regions Defined", "Please define scan regions before adding focus points."
                )
                return

            region_id, ok = QInputDialog.getItem(
                self, "Select Region", "Choose a region:", [str(r) for r in region_ids], 0, False
            )
            if not ok or not region_id:
                return
            region_id = str(region_id)  # Ensure string format
        else:
            # Find the closest region to current position
            closest_region = None
            min_distance = float("inf")
            for rid, center in self.scanCoordinates.region_centers.items():
                dx = center[0] - pos.x_mm
                dy = center[1] - pos.y_mm
                distance = dx * dx + dy * dy
                if distance < min_distance:
                    min_distance = distance
                    closest_region = rid
            region_id = closest_region

        if region_id is not None:
            self.focus_points.append((region_id, pos.x_mm, pos.y_mm, pos.z_mm))
            self.update_point_list()
            self.navigationViewer.register_focus_point(pos.x_mm, pos.y_mm)
        else:
            QMessageBox.warning(self, "Region Error", "Could not determine a valid region for this focus point.")

    def remove_current_point(self):
        index = self.point_combo.currentIndex()
        if 0 <= index < len(self.focus_points):
            self.focus_points.pop(index)
            self.update_point_list()
            self.update_focus_point_display()

    def goto_next_point(self):
        if not self.focus_points:
            return
        current = self.point_combo.currentIndex()
        next_index = (current + 1) % len(self.focus_points)
        self.point_combo.setCurrentIndex(next_index)
        self.goto_selected_point()

    def goto_selected_point(self):
        if self.enabled:
            index = self.point_combo.currentIndex()
            if 0 <= index < len(self.focus_points):
                _, x, y, z = self.focus_points[index]
                self.stage.move_x_to(x)
                self.stage.move_y_to(y)
                self.stage.move_z_to(z)

    def update_current_z(self):
        index = self.point_combo.currentIndex()
        if 0 <= index < len(self.focus_points):
            new_z = self.stage.get_pos().z_mm
            region_id, x, y, _ = self.focus_points[index]
            self.focus_points[index] = (region_id, x, y, new_z)
            self.update_point_list()

    def get_region_points_dict(self):
        points_dict = {}
        for region_id, x, y, z in self.focus_points:
            if region_id not in points_dict:
                points_dict[region_id] = []
            points_dict[region_id].append((x, y, z))
        return points_dict

    def fit_surface(self):
        try:
            method = self.fit_method_combo.currentText()
            rows = self.rows_spin.value()
            cols = self.cols_spin.value()
            by_region = self.by_region_checkbox.isChecked()

            # Validate settings
            if by_region:
                scan_regions = set(self.scanCoordinates.region_centers.keys())
                focus_regions = set(region_id for region_id, _, _, _ in self.focus_points)
                if focus_regions != scan_regions:
                    QMessageBox.warning(
                        self,
                        "Region Mismatch",
                        "The focus points region IDs do not match the scan regions. Please uncheck 'By Region' or select the correct regions.",
                    )
                    return False

            if method == "constant" and (rows != 1 or cols != 1):
                QMessageBox.warning(
                    self,
                    "Confirm Your Configuration",
                    "For 'constant' method, grid size should be 1×1.\nUse 'constant' with 'By Region' checked to define a Z value for each region.",
                )
                return False

            if method != "constant" and (rows < 2 or cols < 2):
                QMessageBox.warning(
                    self,
                    "Confirm Your Configuration",
                    "For surface fitting methods ('spline' or 'rbf'), a grid size of at least 2×2 is recommended.\nAlternatively, use 1x1 grid and 'constant' with 'By Region' checked to define a Z value for each region.",
                )
                return False

            self.focusMap.set_method(method)
            self.focusMap.set_fit_by_region(by_region)
            self.focusMap.smoothing_factor = self.smoothing_spin.value()

            mean_error, std_error = self.focusMap.fit(self.get_region_points_dict())

            self.status_label.setText(f"Surface fit: {mean_error:.3f} mm mean error")
            return True

        except Exception as e:
            self.status_label.setText(f"Fitting failed: {str(e)}")
            return False

    def _match_by_region_box(self):
        if self.fit_method_combo.currentText() == "constant":
            self.by_region_checkbox.setChecked(True)

    def export_focus_points(self):
        """Export focus points to a CSV file"""
        if not self.focus_points:
            QMessageBox.warning(self, "No Focus Points", "There are no focus points to export.")
            return

        file_path, _ = QFileDialog.getSaveFileName(self, "Export Focus Points", "", "CSV Files (*.csv);;All Files (*)")
        if not file_path:
            return
        if not file_path.lower().endswith(".csv"):
            file_path += ".csv"

        try:
            with open(file_path, "w", newline="") as csvfile:
                writer = csv.writer(csvfile)
                # Write header
                writer.writerow(["Region_ID", "X_mm", "Y_mm", "Z_um"])

                # Write data
                for region_id, x, y, z in self.focus_points:
                    writer.writerow([region_id, x, y, z])

            self.status_label.setText(f"Exported {len(self.focus_points)} points to {file_path}")

        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export focus points: {str(e)}")

    def import_focus_points(self):
        """Import focus points from a CSV file"""
        file_path, _ = QFileDialog.getOpenFileName(self, "Import Focus Points", "", "CSV Files (*.csv);;All Files (*)")

        if not file_path:
            return

        try:
            # Read the CSV file
            imported_points = []
            with open(file_path, "r", newline="") as csvfile:
                reader = csv.reader(csvfile)
                header = next(reader)  # Skip header row

                # Validate header
                required_columns = ["Region_ID", "X_mm", "Y_mm", "Z_um"]
                if not all(col in header for col in required_columns):
                    QMessageBox.warning(
                        self, "Invalid Format", f"CSV file must contain columns: {', '.join(required_columns)}"
                    )
                    return

                # Get column indices
                region_idx = header.index("Region_ID")
                x_idx = header.index("X_mm")
                y_idx = header.index("Y_mm")
                z_idx = header.index("Z_um")

                # Read data
                for row in reader:
                    if len(row) >= 4:
                        try:
                            region_id = str(row[region_idx])
                            x = float(row[x_idx])
                            y = float(row[y_idx])
                            z = float(row[z_idx])
                            imported_points.append((region_id, x, y, z))
                        except (ValueError, IndexError):
                            continue

            # If by_region is checked, validate regions
            if self.by_region_checkbox.isChecked():
                scan_regions = set(self.scanCoordinates.region_centers.keys())
                focus_regions = set(region_id for region_id, _, _, _ in imported_points)

                if not focus_regions == scan_regions:
                    response = QMessageBox.warning(
                        self,
                        "Region Mismatch",
                        f"The imported focus points have regions: {', '.join(sorted(focus_regions))}\n\n"
                        f"Current scan has regions: {', '.join(sorted(scan_regions))}\n\n"
                        "Import anyway (disable 'By Region') or cancel?",
                        QMessageBox.Ok | QMessageBox.Cancel,
                        QMessageBox.Cancel,
                    )

                    if response == QMessageBox.Cancel:
                        return
                    else:
                        # User chose to continue, uncheck by_region
                        self.by_region_checkbox.setChecked(False)

            # Clear existing points and add imported ones
            self.focus_points = imported_points
            self.update_point_list()
            self.update_focus_point_display()

            self.status_label.setText(f"Imported {len(imported_points)} focus points")

        except Exception as e:
            QMessageBox.critical(self, "Import Error", f"Failed to import focus points: {str(e)}")

    def on_regions_updated(self):
        if not self._allow_updating_focus_points_on_signal:
            return
        if self.scanCoordinates.has_regions():
            self.generate_grid(self.rows_spin.value(), self.cols_spin.value())

    def disable_updating_focus_points_on_signal(self):
        self._allow_updating_focus_points_on_signal = False

    def enable_updating_focus_points_on_signal(self):
        self._allow_updating_focus_points_on_signal = True

    def setEnabled(self, enabled):
        self.enabled = enabled
        super().setEnabled(enabled)
        self.navigationViewer.focus_point_overlay_item.setVisible(enabled)
        self.on_regions_updated()

    def resizeEvent(self, event):
        """Handle resize events to maintain button sizing"""
        super().resizeEvent(event)
        self.update_z_btn.setFixedWidth(self.edit_point_btn.width())


class AlignmentWidget(QWidget):
    """
    Self-contained widget for alignment workflow.

    Allows users to align current sample position with a previous acquisition by:
    1. Loading a past acquisition folder
    2. Moving stage to a reference FOV position
    3. Displaying reference image as translucent overlay
    4. Calculating X/Y offset after manual alignment
    5. Applying offset to future scan coordinates

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

    def __init__(self, napari_viewer, parent=None):
        """
        Initialize alignment widget.

        Args:
            napari_viewer: The napari viewer instance for layer management
            parent: Parent widget
        """
        super().__init__(parent)
        self._log = squid.logging.get_logger(self.__class__.__name__)

        self.viewer = napari_viewer
        self.state = self.STATE_ALIGN

        # Alignment state
        self._offset_x_mm = 0.0
        self._offset_y_mm = 0.0
        self._has_offset = False
        self._reference_fov_position = None  # (x_mm, y_mm)
        self._current_folder = None
        self._original_live_opacity = 1.0
        self._original_live_blending = "additive"
        self._pending_position_request = False

        self._setup_ui()

    def _setup_ui(self):
        """Setup the button UI."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.btn_align = QPushButton("Align")
        self.btn_align.setCursor(Qt.PointingHandCursor)
        self.btn_align.setMinimumWidth(100)  # Wide enough for "Confirm Offset"
        self.btn_align.setEnabled(False)  # Disabled until live view starts
        self.btn_align.clicked.connect(self._on_button_clicked)
        layout.addWidget(self.btn_align)

    def enable(self):
        """Enable the alignment button if currently disabled. Call when live view starts."""
        if not self.btn_align.isEnabled():
            self.btn_align.setEnabled(True)

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

    def apply_offset(self, x_mm: float, y_mm: float) -> tuple[float, float]:
        """Apply the current alignment offset to coordinates."""
        return (x_mm + self.offset_x_mm, y_mm + self.offset_y_mm)

    def set_current_position(self, x_mm: float, y_mm: float):
        """
        Receive current stage position (response to signal_request_current_position).

        Called by gui_hcs when position is requested during confirm step.
        """
        if self._pending_position_request:
            self._pending_position_request = False
            self._complete_confirmation(x_mm, y_mm)

    def reset(self):
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

    def _on_button_clicked(self):
        """Handle button click based on current state."""
        if self.state == self.STATE_ALIGN:
            self._handle_align_click()
        elif self.state == self.STATE_CONFIRM:
            self._handle_confirm_click()
        elif self.state == self.STATE_CLEAR:
            self._handle_clear_click()

    def _handle_align_click(self):
        """Handle click in ALIGN state - open folder dialog."""
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Past Acquisition Folder",
            str(Path.home()),
        )
        if folder:
            self._start_alignment(folder)

    def _handle_confirm_click(self):
        """Handle click in CONFIRM state - request position and calculate offset."""
        self._pending_position_request = True
        self.signal_request_current_position.emit()

    def _handle_clear_click(self):
        """Handle click in CLEAR state - clear offset."""
        self._offset_x_mm = 0.0
        self._offset_y_mm = 0.0
        self._has_offset = False
        self._reference_fov_position = None
        self._current_folder = None

        self.state = self.STATE_ALIGN
        self.btn_align.setText("Align")

        self.signal_offset_cleared.emit()
        self._log.info("Alignment offset cleared")

    # ─────────────────────────────────────────────────────────────────────────
    # Alignment Workflow
    # ─────────────────────────────────────────────────────────────────────────

    def _start_alignment(self, folder_path: str):
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

    def _complete_confirmation(self, current_x: float, current_y: float):
        """Complete the confirmation step with current position."""
        if self._reference_fov_position is None:
            self._log.error("Cannot confirm: no reference position set")
            QMessageBox.warning(self, "Alignment Error", "No reference position set. Please load an acquisition first.")
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
        """
        Load acquisition info from a past acquisition folder.

        Returns dict with: coordinates, first_region, center_fov_index, center_fov_position, image_path
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

    def _load_reference_image(self, image_path: str):
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

    def _add_reference_layer(self, image: np.ndarray):
        """Add reference image as a napari layer with magenta/green overlay."""
        self._modified_live_view = False
        self._contrast_connected = False
        if "Live View" in self.viewer.layers:
            live_layer = self.viewer.layers["Live View"]
            self._original_live_opacity = live_layer.opacity
            self._original_live_blending = live_layer.blending
            self._original_live_colormap = live_layer.colormap
            live_layer.opacity = 1.0
            live_layer.blending = "additive"
            live_layer.colormap = "green"
            live_layer.events.contrast_limits.connect(self._sync_contrast_limits)
            self._contrast_connected = True
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
        # Sync initial contrast limits from Live View
        if self._contrast_connected and self.REFERENCE_LAYER_NAME in self.viewer.layers:
            ref_layer = self.viewer.layers[self.REFERENCE_LAYER_NAME]
            ref_layer.contrast_limits = live_layer.contrast_limits
        self._log.debug("Reference layer added to napari viewer")

    def _sync_contrast_limits(self, event):
        """Sync contrast limits from Live View to reference layer."""
        if self.REFERENCE_LAYER_NAME in self.viewer.layers:
            self.viewer.layers[self.REFERENCE_LAYER_NAME].contrast_limits = event.value

    def _remove_reference_layer(self):
        """Remove reference layer and restore live view settings."""
        if self.REFERENCE_LAYER_NAME in self.viewer.layers:
            self.viewer.layers.remove(self.REFERENCE_LAYER_NAME)
            self._log.debug("Reference layer removed from napari viewer")

        if getattr(self, "_modified_live_view", False) and "Live View" in self.viewer.layers:
            live_layer = self.viewer.layers["Live View"]
            if getattr(self, "_contrast_connected", False):
                live_layer.events.contrast_limits.disconnect(self._sync_contrast_limits)
                self._contrast_connected = False
            live_layer.opacity = self._original_live_opacity
            live_layer.blending = self._original_live_blending
            live_layer.colormap = self._original_live_colormap
            self._modified_live_view = False


class NapariLiveWidget(QWidget):
    signal_coordinates_clicked = Signal(int, int, int, int)
    signal_newExposureTime = Signal(float)
    signal_newAnalogGain = Signal(float)
    signal_autoLevelSetting = Signal(bool)

    def __init__(
        self,
        streamHandler,
        liveController,
        stage: AbstractStage,
        objectiveStore,
        contrastManager,
        wellSelectionWidget=None,
        show_trigger_options=True,
        show_display_options=True,
        show_autolevel=False,
        autolevel=False,
        parent=None,
    ):
        super().__init__(parent)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.streamHandler = streamHandler
        self.liveController: LiveController = liveController
        self.stage = stage
        self.objectiveStore = objectiveStore
        self.wellSelectionWidget = wellSelectionWidget
        self.live_configuration = self.liveController.currentConfiguration
        self.image_width = 0
        self.image_height = 0
        self.dtype = np.uint8
        self.channels = set()
        self.init_live = False
        self.init_live_rgb = False
        self.init_scale = False
        self.previous_scale = None
        self.previous_center = None
        self.last_was_autofocus = False
        self.fps_trigger = 10
        self.fps_display = 10
        self.contrastManager = contrastManager
        self.is_switching_mode = False  # Guard to prevent duplicate MCU commands during mode switch

        self.initNapariViewer()
        self.addNapariGrayclipColormap()
        self.initControlWidgets(show_trigger_options, show_display_options, show_autolevel, autolevel)
        self.update_ui_for_mode(self.live_configuration)

    def initNapariViewer(self):
        self.viewer = napari.Viewer(show=False)
        self.viewerWidget = self.viewer.window._qt_window
        self.viewer.dims.axis_labels = ["Y-axis", "X-axis"]
        self.layout = QVBoxLayout()
        self.layout.addWidget(self.viewerWidget)
        self.setLayout(self.layout)
        self.customizeViewer()

    def customizeViewer(self):
        # # Hide the status bar (which includes the activity button)
        # if hasattr(self.viewer.window, "_status_bar"):
        #     self.viewer.window._status_bar.hide()

        # Disable napari's native menu bar so it doesn't take over macOS global menu bar
        if sys.platform == "darwin":
            self.viewer.window.main_menu.setNativeMenuBar(False)
        self.viewer.window.main_menu.hide()

        # Hide the layer buttons
        if hasattr(self.viewer.window._qt_viewer, "layerButtons"):
            self.viewer.window._qt_viewer.layerButtons.hide()

    def updateHistogram(self, layer):
        if self.histogram_widget is not None and layer.data is not None:
            self.pg_image_item.setImage(layer.data, autoLevels=False)
            self.histogram_widget.setLevels(*layer.contrast_limits)
            self.histogram_widget.setHistogramRange(layer.data.min(), layer.data.max())

            # Set the histogram widget's region to match the layer's contrast limits
            self.histogram_widget.region.setRegion(layer.contrast_limits)

            # Update colormap only if it has changed
            if hasattr(self, "last_colormap") and self.last_colormap != layer.colormap.name:
                self.histogram_widget.gradient.setColorMap(self.createColorMap(layer.colormap))
            self.last_colormap = layer.colormap.name

    def createColorMap(self, colormap):
        colors = colormap.colors
        positions = np.linspace(0, 1, len(colors))
        return pg.ColorMap(positions, colors)

    def initControlWidgets(self, show_trigger_options, show_display_options, show_autolevel, autolevel):
        # Initialize histogram widget
        self.pg_image_item = pg.ImageItem()
        self.histogram_widget = pg.HistogramLUTWidget(image=self.pg_image_item)
        self.histogram_widget.setFixedWidth(100)
        self.histogram_dock = self.viewer.window.add_dock_widget(self.histogram_widget, area="right", name="hist")
        self.histogram_dock.setFeatures(QDockWidget.NoDockWidgetFeatures)
        self.histogram_dock.setTitleBarWidget(QWidget())
        self.histogram_widget.region.sigRegionChanged.connect(self.on_histogram_region_changed)
        self.histogram_widget.region.sigRegionChangeFinished.connect(self.on_histogram_region_changed)

        # Microscope Configuration (only enabled channels)
        self.dropdown_modeSelection = QComboBox()
        for config in self.liveController.get_channels(self.objectiveStore.current_objective):
            self.dropdown_modeSelection.addItem(config.name)
        self.dropdown_modeSelection.setCurrentText(self.live_configuration.name)
        self.dropdown_modeSelection.activated.connect(self.select_new_microscope_mode_by_name)

        # Live button
        self.btn_live = QPushButton("Start Live")
        self.btn_live.setCheckable(True)
        gradient_style = """
            QPushButton {
                background-color: qlineargradient(spread:pad, x1:0, y1:0, x2:0, y2:1,
                                                  stop:0 #D6D6FF, stop:1 #C2C2FF);
                border-radius: 5px;
                color: black;
                border: 1px solid #A0A0A0;
            }
            QPushButton:checked {
                background-color: qlineargradient(spread:pad, x1:0, y1:0, x2:0, y2:1,
                                                  stop:0 #FFD6D6, stop:1 #FFC2C2);
                border: 1px solid #A0A0A0;
            }
            QPushButton:hover {
                background-color: qlineargradient(spread:pad, x1:0, y1:0, x2:0, y2:1,
                                                  stop:0 #E0E0FF, stop:1 #D0D0FF);
            }
            QPushButton:pressed {
                background-color: qlineargradient(spread:pad, x1:0, y1:0, x2:0, y2:1,
                                                  stop:0 #9090C0, stop:1 #8080B0);
            }
        """
        self.btn_live.setStyleSheet(gradient_style)
        # self.btn_live.setStyleSheet("font-weight: bold; background-color: #7676F7") #6666D3
        current_height = self.btn_live.sizeHint().height()
        self.btn_live.setFixedHeight(int(current_height * 1.5))
        self.btn_live.clicked.connect(self.toggle_live)

        # Exposure Time
        self.entry_exposureTime = QDoubleSpinBox()
        self.entry_exposureTime.setRange(*self.liveController.camera.get_exposure_limits())
        self.entry_exposureTime.setValue(self.live_configuration.exposure_time)
        self.entry_exposureTime.setSuffix(" ms")
        self.entry_exposureTime.valueChanged.connect(self.update_config_exposure_time)

        # Analog Gain
        self.entry_analogGain = QDoubleSpinBox()
        self.entry_analogGain.setRange(0, 24)
        self.entry_analogGain.setSingleStep(0.1)
        self.entry_analogGain.setValue(self.live_configuration.analog_gain)
        # self.entry_analogGain.setSuffix('x')
        self.entry_analogGain.valueChanged.connect(self.update_config_analog_gain)

        # Illumination Intensity
        self.slider_illuminationIntensity = QSlider(Qt.Horizontal)
        self.slider_illuminationIntensity.setRange(0, 100)
        self.slider_illuminationIntensity.setValue(int(self.live_configuration.illumination_intensity))
        self.slider_illuminationIntensity.setTickPosition(QSlider.TicksBelow)
        self.slider_illuminationIntensity.setTickInterval(10)
        self.slider_illuminationIntensity.valueChanged.connect(self.update_config_illumination_intensity)
        self.label_illuminationIntensity = QLabel(str(self.slider_illuminationIntensity.value()) + "%")
        self.slider_illuminationIntensity.valueChanged.connect(
            lambda v: self.label_illuminationIntensity.setText(str(v) + "%")
        )

        # Trigger mode
        self.dropdown_triggerMode = QComboBox()
        trigger_modes = [
            ("Software", TriggerMode.SOFTWARE),
            ("Hardware", TriggerMode.HARDWARE),
            ("Continuous", TriggerMode.CONTINUOUS),
        ]
        for display_name, mode in trigger_modes:
            self.dropdown_triggerMode.addItem(display_name, mode)
        self.dropdown_triggerMode.currentIndexChanged.connect(self.on_trigger_mode_changed)

        # Trigger FPS
        self.entry_triggerFPS = QDoubleSpinBox()
        self.entry_triggerFPS.setRange(0.02, 1000)
        self.entry_triggerFPS.setValue(self.fps_trigger)
        # self.entry_triggerFPS.setSuffix(" fps")
        self.entry_triggerFPS.valueChanged.connect(self.liveController.set_trigger_fps)

        # Display FPS
        self.entry_displayFPS = QDoubleSpinBox()
        self.entry_displayFPS.setRange(1, 240)
        self.entry_displayFPS.setValue(self.fps_display)
        # self.entry_displayFPS.setSuffix(" fps")
        self.entry_displayFPS.valueChanged.connect(self.streamHandler.set_display_fps)

        # Resolution Scaling
        self.slider_resolutionScaling = QSlider(Qt.Horizontal)
        self.slider_resolutionScaling.setRange(10, 100)
        self.slider_resolutionScaling.setValue(100)
        self.slider_resolutionScaling.setTickPosition(QSlider.TicksBelow)
        self.slider_resolutionScaling.setTickInterval(10)
        self.slider_resolutionScaling.valueChanged.connect(self.update_resolution_scaling)
        self.label_resolutionScaling = QLabel(str(self.slider_resolutionScaling.value()) + "%")
        self.slider_resolutionScaling.valueChanged.connect(lambda v: self.label_resolutionScaling.setText(str(v) + "%"))

        # Autolevel
        self.btn_autolevel = QPushButton("Autolevel")
        self.btn_autolevel.setCheckable(True)
        self.btn_autolevel.setChecked(autolevel)
        self.btn_autolevel.clicked.connect(self.signal_autoLevelSetting.emit)

        def make_row(label_widget, entry_widget, value_label=None):
            row = QHBoxLayout()
            row.addWidget(label_widget)
            row.addWidget(entry_widget)
            if value_label:
                row.addWidget(value_label)
            return row

        control_layout = QVBoxLayout()

        # Add widgets to layout
        control_layout.addWidget(self.dropdown_modeSelection)
        control_layout.addWidget(self.btn_live)
        control_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding))

        row1 = make_row(QLabel("Exposure Time"), self.entry_exposureTime)
        control_layout.addLayout(row1)

        row2 = make_row(QLabel("Illumination"), self.slider_illuminationIntensity, self.label_illuminationIntensity)
        control_layout.addLayout(row2)

        row3 = make_row((QLabel("Analog Gain")), self.entry_analogGain)
        control_layout.addLayout(row3)
        control_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding))

        if show_trigger_options:
            row0 = make_row(QLabel("Trigger Mode"), self.dropdown_triggerMode)
            control_layout.addLayout(row0)
            row00 = make_row(QLabel("Trigger FPS"), self.entry_triggerFPS)
            control_layout.addLayout(row00)
            control_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding))

        if show_display_options:
            row4 = make_row((QLabel("Display FPS")), self.entry_displayFPS)
            control_layout.addLayout(row4)
            row5 = make_row(QLabel("Display Resolution"), self.slider_resolutionScaling, self.label_resolutionScaling)
            control_layout.addLayout(row5)
            control_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding))

        if show_autolevel:
            control_layout.addWidget(self.btn_autolevel)
            control_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding))

        control_layout.addStretch(1)

        add_live_controls = False
        if USE_NAPARI_FOR_LIVE_CONTROL or add_live_controls:
            live_controls_widget = QWidget()
            live_controls_widget.setLayout(control_layout)
            # layer_list_widget.setFixedWidth(270)

            layer_controls_widget = self.viewer.window._qt_viewer.dockLayerControls.widget()
            layer_list_widget = self.viewer.window._qt_viewer.dockLayerList.widget()

            self.viewer.window._qt_viewer.layerButtons.hide()
            self.viewer.window.remove_dock_widget(self.viewer.window._qt_viewer.dockLayerControls)
            self.viewer.window.remove_dock_widget(self.viewer.window._qt_viewer.dockLayerList)

            # Add the actual dock widgets
            self.dock_layer_controls = self.viewer.window.add_dock_widget(
                layer_controls_widget, area="left", name="layer controls", tabify=True
            )
            self.dock_layer_list = self.viewer.window.add_dock_widget(
                layer_list_widget, area="left", name="layer list", tabify=True
            )
            self.dock_live_controls = self.viewer.window.add_dock_widget(
                live_controls_widget, area="left", name="live controls", tabify=True
            )

            self.viewer.window.window_menu.addAction(self.dock_live_controls.toggleViewAction())

        if USE_NAPARI_WELL_SELECTION:
            well_selector_layout = QVBoxLayout()
            # title_label = QLabel("Well Selector")
            # title_label.setAlignment(Qt.AlignCenter)  # Center the title
            # title_label.setStyleSheet("font-weight: bold;")  # Optional: style the title
            # well_selector_layout.addWidget(title_label)

            well_selector_row = QHBoxLayout()
            well_selector_row.addStretch(1)
            well_selector_row.addWidget(self.wellSelectionWidget)
            well_selector_row.addStretch(1)
            well_selector_layout.addLayout(well_selector_row)
            well_selector_layout.addStretch()

            well_selector_dock_widget = QWidget()
            well_selector_dock_widget.setLayout(well_selector_layout)
            self.dock_well_selector = self.viewer.window.add_dock_widget(
                well_selector_dock_widget, area="bottom", name="well selector"
            )
            self.dock_well_selector.setFixedHeight(self.dock_well_selector.minimumSizeHint().height())

        layer_controls_widget = self.viewer.window._qt_viewer.dockLayerControls.widget()
        layer_list_widget = self.viewer.window._qt_viewer.dockLayerList.widget()

        self.viewer.window._qt_viewer.layerButtons.hide()
        self.viewer.window.remove_dock_widget(self.viewer.window._qt_viewer.dockLayerControls)
        self.viewer.window.remove_dock_widget(self.viewer.window._qt_viewer.dockLayerList)
        self.print_window_menu_items()

    def print_window_menu_items(self):
        print("Items in window_menu:")
        for action in self.viewer.window.window_menu.actions():
            print(action.text())

    def on_histogram_region_changed(self):
        if self.live_configuration.name:
            min_val, max_val = self.histogram_widget.region.getRegion()
            self.updateContrastLimits(self.live_configuration.name, min_val, max_val)

    def toggle_live(self, pressed):
        if pressed:
            self.liveController.start_live()
            self.btn_live.setText("Stop Live")
        else:
            self.liveController.stop_live()
            self.btn_live.setText("Start Live")

    def toggle_live_controls(self, show):
        if show:
            self.dock_live_controls.show()
        else:
            self.dock_live_controls.hide()

    def toggle_well_selector(self, show):
        if show:
            self.dock_well_selector.show()
        else:
            self.dock_well_selector.hide()

    def replace_well_selector(self, wellSelector):
        self.viewer.window.remove_dock_widget(self.dock_well_selector)
        self.wellSelectionWidget = wellSelector
        well_selector_layout = QHBoxLayout()
        well_selector_layout.addStretch(1)  # Add stretch on the left
        well_selector_layout.addWidget(self.wellSelectionWidget)
        well_selector_layout.addStretch(1)  # Add stretch on the right
        well_selector_dock_widget = QWidget()
        well_selector_dock_widget.setLayout(well_selector_layout)
        self.dock_well_selector = self.viewer.window.add_dock_widget(
            well_selector_dock_widget, area="bottom", name="well selector", tabify=True
        )

    def select_new_microscope_mode_by_name(self, config_index):
        config_name = self.dropdown_modeSelection.itemText(config_index)
        maybe_new_config = self.liveController.get_channel_by_name(self.objectiveStore.current_objective, config_name)

        if not maybe_new_config:
            self._log.error(f"User attempted to select config named '{config_name}' but it does not exist!")
            return

        self.liveController.set_microscope_mode(maybe_new_config)
        self.update_ui_for_mode(maybe_new_config)

    def update_ui_for_mode(self, config):
        try:
            self.is_switching_mode = True
            self.live_configuration = config
            self.dropdown_modeSelection.setCurrentText(config.name if config else "Unknown")
            if self.live_configuration:
                self.entry_exposureTime.setValue(self.live_configuration.exposure_time)
                self.entry_analogGain.setValue(self.live_configuration.analog_gain)
                self.slider_illuminationIntensity.setValue(int(self.live_configuration.illumination_intensity))
        finally:
            self.is_switching_mode = False

    def update_config_exposure_time(self, new_value):
        if self.is_switching_mode:
            return
        self.live_configuration.exposure_time = new_value
        self.liveController.microscope.config_repo.update_channel_setting(
            self.objectiveStore.current_objective, self.live_configuration.name, "ExposureTime", new_value
        )
        self.signal_newExposureTime.emit(new_value)

    def update_config_analog_gain(self, new_value):
        if self.is_switching_mode:
            return
        self.live_configuration.analog_gain = new_value
        self.liveController.microscope.config_repo.update_channel_setting(
            self.objectiveStore.current_objective, self.live_configuration.name, "AnalogGain", new_value
        )
        self.signal_newAnalogGain.emit(new_value)

    def update_config_illumination_intensity(self, new_value):
        if self.is_switching_mode:
            return
        self.live_configuration.illumination_intensity = new_value
        self.liveController.microscope.config_repo.update_channel_setting(
            self.objectiveStore.current_objective, self.live_configuration.name, "IlluminationIntensity", new_value
        )
        self.liveController.update_illumination()

    def update_resolution_scaling(self, value):
        self.streamHandler.set_display_resolution_scaling(value)
        self.liveController.set_display_resolution_scaling(value)

    def refresh_mode_list(self):
        """Refresh the mode selection dropdown (only show enabled channels)"""
        self.dropdown_modeSelection.blockSignals(True)
        self.dropdown_modeSelection.clear()
        first_config = None
        for config in self.liveController.get_channels(self.objectiveStore.current_objective):
            if not first_config:
                first_config = config
            self.dropdown_modeSelection.addItem(config.name)
        self.dropdown_modeSelection.blockSignals(False)

        if self.dropdown_modeSelection.count() > 0 and first_config:
            self.update_ui_for_mode(first_config)
            self.liveController.set_microscope_mode(first_config)

    def on_trigger_mode_changed(self, index):
        # Get the actual value using user data
        actual_value = self.dropdown_triggerMode.itemData(index)
        print(f"Selected: {self.dropdown_triggerMode.currentText()} (actual value: {actual_value})")

    def addNapariGrayclipColormap(self):
        if hasattr(napari.utils.colormaps.AVAILABLE_COLORMAPS, "grayclip"):
            return
        grayclip = []
        for i in range(255):
            grayclip.append([i / 255, i / 255, i / 255])
        grayclip.append([1, 0, 0])
        napari.utils.colormaps.AVAILABLE_COLORMAPS["grayclip"] = napari.utils.Colormap(name="grayclip", colors=grayclip)

    def initLiveLayer(self, channel, image_height, image_width, image_dtype, rgb=False):
        """Initializes the full canvas for each channel based on the acquisition parameters."""
        self.viewer.layers.clear()
        self.image_width = image_width
        self.image_height = image_height
        if self.dtype != np.dtype(image_dtype):

            self.contrastManager.scale_contrast_limits(
                np.dtype(image_dtype)
            )  # Fix This to scale existing contrast limits to new dtype range
            self.dtype = image_dtype

        self.channels.add(channel)
        self.live_configuration.name = channel

        if rgb:
            canvas = np.zeros((image_height, image_width, 3), dtype=self.dtype)
        else:
            canvas = np.zeros((image_height, image_width), dtype=self.dtype)
        limits = self.getContrastLimits(self.dtype)
        layer = self.viewer.add_image(
            canvas,
            name="Live View",
            visible=True,
            rgb=rgb,
            colormap="grayclip",
            contrast_limits=limits,
            blending="additive",
        )
        layer.contrast_limits = self.contrastManager.get_limits(self.live_configuration.name, self.dtype)
        layer.mouse_double_click_callbacks.append(self.onDoubleClick)
        layer.events.contrast_limits.connect(self.signalContrastLimits)
        self.updateHistogram(layer)

        if not self.init_scale:
            self.resetView()
            self.previous_scale = self.viewer.camera.zoom
            self.previous_center = self.viewer.camera.center
        else:
            self.viewer.camera.zoom = self.previous_scale
            self.viewer.camera.center = self.previous_center

    def updateLiveLayer(self, image, from_autofocus=False):
        """Updates the canvas with the new image data."""
        if self.dtype != np.dtype(image.dtype):
            self.contrastManager.scale_contrast_limits(np.dtype(image.dtype))
            self.dtype = np.dtype(image.dtype)
            self.init_live = False
            self.init_live_rgb = False

        if not self.live_configuration.name:
            self.live_configuration.name = self.liveController.currentConfiguration.name
        rgb = len(image.shape) >= 3

        if not rgb and not self.init_live or "Live View" not in self.viewer.layers:
            self.initLiveLayer(self.live_configuration.name, image.shape[0], image.shape[1], image.dtype, rgb)
            self.init_live = True
            self.init_live_rgb = False
            print("init live")
        elif rgb and not self.init_live_rgb:
            self.initLiveLayer(self.live_configuration.name, image.shape[0], image.shape[1], image.dtype, rgb)
            self.init_live_rgb = True
            self.init_live = False
            print("init live rgb")

        layer = self.viewer.layers["Live View"]
        layer.data = image
        layer.contrast_limits = self.contrastManager.get_limits(self.live_configuration.name)
        self.updateHistogram(layer)

        if from_autofocus:
            # save viewer scale
            if not self.last_was_autofocus:
                self.previous_scale = self.viewer.camera.zoom
                self.previous_center = self.viewer.camera.center
            # resize to cropped view
            self.resetView()
            self.last_was_autofocus = True
        else:
            if not self.init_scale:
                # init viewer scale
                self.resetView()
                self.previous_scale = self.viewer.camera.zoom
                self.previous_center = self.viewer.camera.center
                self.init_scale = True
            elif self.last_was_autofocus:
                # return to to original view
                self.viewer.camera.zoom = self.previous_scale
                self.viewer.camera.center = self.previous_center
            # save viewer scale
            self.previous_scale = self.viewer.camera.zoom
            self.previous_center = self.viewer.camera.center
            self.last_was_autofocus = False
        layer.refresh()

    def onDoubleClick(self, layer, event):
        """Handle double-click events and emit centered coordinates if within the data range."""
        coords = layer.world_to_data(event.position)
        layer_shape = layer.data.shape[0:2] if len(layer.data.shape) >= 3 else layer.data.shape

        if coords is not None and (0 <= int(coords[-1]) < layer_shape[-1] and (0 <= int(coords[-2]) < layer_shape[-2])):
            x_centered = int(coords[-1] - layer_shape[-1] / 2)
            y_centered = int(coords[-2] - layer_shape[-2] / 2)
            # Emit the centered coordinates and dimensions of the layer's data array
            self.signal_coordinates_clicked.emit(x_centered, y_centered, layer_shape[-1], layer_shape[-2])

    def set_live_configuration(self, live_configuration):
        self.live_configuration = live_configuration

    def updateContrastLimits(self, channel, min_val, max_val):
        self.contrastManager.update_limits(channel, min_val, max_val)
        if "Live View" in self.viewer.layers:
            self.viewer.layers["Live View"].contrast_limits = (min_val, max_val)

    def signalContrastLimits(self, event):
        layer = event.source
        min_val, max_val = map(float, layer.contrast_limits)
        self.contrastManager.update_limits(self.live_configuration.name, min_val, max_val)

    def getContrastLimits(self, dtype):
        return self.contrastManager.get_default_limits()

    def resetView(self):
        self.viewer.reset_view()

    def activate(self):
        print("ACTIVATING NAPARI LIVE WIDGET")
        self.viewer.window.activate()


class NapariMultiChannelWidget(QWidget):

    def __init__(self, objectiveStore, camera, contrastManager, grid_enabled=False, parent=None):
        super().__init__(parent)
        # Initialize placeholders for the acquisition parameters
        self.objectiveStore = objectiveStore
        self.camera = camera
        self.contrastManager = contrastManager
        self.image_width = 0
        self.image_height = 0
        self.dtype = np.uint8
        self.channels = set()
        self.pixel_size_um = 1
        self.dz_um = 1
        self.Nz = 1
        self.layers_initialized = False
        self.acquisition_initialized = False
        self.viewer_scale_initialized = False
        self.update_layer_count = 0
        self.grid_enabled = grid_enabled

        # Initialize a napari Viewer without showing its standalone window.
        self.initNapariViewer()

    def initNapariViewer(self):
        self.viewer = napari.Viewer(show=False)
        if self.grid_enabled:
            self.viewer.grid.enabled = True
        self.viewer.dims.axis_labels = ["Z-axis", "Y-axis", "X-axis"]
        self.viewerWidget = self.viewer.window._qt_window
        self.layout = QVBoxLayout()
        self.layout.addWidget(self.viewerWidget)
        self.setLayout(self.layout)
        self.customizeViewer()

    def customizeViewer(self):
        # # Hide the status bar (which includes the activity button)
        # if hasattr(self.viewer.window, "_status_bar"):
        #     self.viewer.window._status_bar.hide()

        # Disable napari's native menu bar so it doesn't take over macOS global menu bar
        if sys.platform == "darwin":
            self.viewer.window.main_menu.setNativeMenuBar(False)
        self.viewer.window.main_menu.hide()

        # Hide the layer buttons
        if hasattr(self.viewer.window._qt_viewer, "layerButtons"):
            self.viewer.window._qt_viewer.layerButtons.hide()

    def initLayersShape(self, Nz, dz):
        pixel_size_um = self.objectiveStore.get_pixel_size_factor() * self.camera.get_pixel_size_binned_um()
        if self.Nz != Nz or self.dz_um != dz or self.pixel_size_um != pixel_size_um:
            self.acquisition_initialized = False
            self.Nz = Nz
            self.dz_um = dz if Nz > 1 and dz != 0 else 1.0
            self.pixel_size_um = pixel_size_um

    def initChannels(self, channels):
        self.channels = set(channels)

    def extractWavelength(self, name):
        # Split the string and find the wavelength number immediately after "Fluorescence"
        parts = name.split()
        if "Fluorescence" in parts:
            index = parts.index("Fluorescence") + 1
            if index < len(parts):
                return parts[index].split()[0]  # Assuming '488 nm Ex' and taking '488'
        for color in ["R", "G", "B"]:
            if color in parts or f"full_{color}" in parts:
                return color
        return None

    def generateColormap(self, channel_info):
        """Convert a HEX value to a normalized RGB tuple."""
        positions = [0, 1]
        c0 = (0, 0, 0)
        c1 = (
            ((channel_info["hex"] >> 16) & 0xFF) / 255,  # Normalize the Red component
            ((channel_info["hex"] >> 8) & 0xFF) / 255,  # Normalize the Green component
            (channel_info["hex"] & 0xFF) / 255,
        )  # Normalize the Blue component
        return Colormap(colors=[c0, c1], controls=[0, 1], name=channel_info["name"])

    def initLayers(self, image_height, image_width, image_dtype):
        """Initializes the full canvas for each channel based on the acquisition parameters."""
        if self.acquisition_initialized:
            for layer in list(self.viewer.layers):
                if layer.name not in self.channels:
                    self.viewer.layers.remove(layer)
        else:
            self.viewer.layers.clear()
            self.acquisition_initialized = True
            if self.dtype != np.dtype(image_dtype) and not USE_NAPARI_FOR_LIVE_VIEW:
                self.contrastManager.scale_contrast_limits(image_dtype)

        self.image_width = image_width
        self.image_height = image_height
        self.dtype = np.dtype(image_dtype)
        self.layers_initialized = True
        self.update_layer_count = 0

    def updateLayers(self, image, x, y, k, channel_name):
        """Updates the appropriate slice of the canvas with the new image data."""
        rgb = len(image.shape) == 3

        # Check if the layer exists and has a different dtype
        if self.dtype != np.dtype(image.dtype):  # or self.viewer.layers[channel_name].data.dtype != image.dtype:
            # Remove the existing layer
            self.layers_initialized = False
            self.acquisition_initialized = False

        if not self.layers_initialized:
            self.initLayers(image.shape[0], image.shape[1], image.dtype)

        if channel_name not in self.viewer.layers:
            self.channels.add(channel_name)
            if rgb:
                color = None  # RGB images do not need a colormap
                canvas = np.zeros((self.Nz, self.image_height, self.image_width, 3), dtype=self.dtype)
            else:
                channel_info = CHANNEL_COLORS_MAP.get(
                    self.extractWavelength(channel_name), {"hex": 0xFFFFFF, "name": "gray"}
                )
                if channel_info["name"] in AVAILABLE_COLORMAPS:
                    color = AVAILABLE_COLORMAPS[channel_info["name"]]
                else:
                    color = self.generateColormap(channel_info)
                canvas = np.zeros((self.Nz, self.image_height, self.image_width), dtype=self.dtype)

            limits = self.getContrastLimits(self.dtype)
            layer = self.viewer.add_image(
                canvas,
                name=channel_name,
                visible=True,
                rgb=rgb,
                colormap=color,
                contrast_limits=limits,
                blending="additive",
                scale=(self.dz_um, self.pixel_size_um, self.pixel_size_um),
            )

            # print(f"multi channel - dz_um:{self.dz_um}, pixel_y_um:{self.pixel_size_um}, pixel_x_um:{self.pixel_size_um}")
            layer.contrast_limits = self.contrastManager.get_limits(channel_name)
            layer.events.contrast_limits.connect(self.signalContrastLimits)

            if not self.viewer_scale_initialized:
                self.resetView()
                self.viewer_scale_initialized = True
            else:
                layer.refresh()

        layer = self.viewer.layers[channel_name]
        layer.data[k] = image
        layer.contrast_limits = self.contrastManager.get_limits(channel_name)
        self.update_layer_count += 1
        if self.update_layer_count % len(self.channels) == 0:
            if self.Nz > 1:
                self.viewer.dims.set_point(0, k * self.dz_um)
            for layer in self.viewer.layers:
                layer.refresh()

    def signalContrastLimits(self, event):
        layer = event.source
        min_val, max_val = map(float, layer.contrast_limits)
        self.contrastManager.update_limits(layer.name, min_val, max_val)

    def getContrastLimits(self, dtype):
        return self.contrastManager.get_default_limits()

    def resetView(self):
        self.viewer.reset_view()
        for layer in self.viewer.layers:
            layer.refresh()

    def activate(self):
        self.viewer.window.activate()


class NapariMosaicDisplayWidget(QWidget):

    signal_coordinates_clicked = Signal(float, float)  # x, y in mm
    signal_clear_viewer = Signal()
    signal_layers_initialized = Signal()
    signal_shape_drawn = Signal(list)

    def __init__(self, objectiveStore, camera, contrastManager, parent=None):
        super().__init__(parent)
        self.objectiveStore = objectiveStore
        self.camera = camera
        self.contrastManager = contrastManager
        self.viewer = napari.Viewer(show=False)
        self.layout = QVBoxLayout()
        self.layout.addWidget(self.viewer.window._qt_window)
        self.layers_initialized = False
        self.shape_layer = None
        self.shapes_mm = []
        self.is_drawing_shape = False

        # add clear button
        self.clear_button = QPushButton("Clear Mosaic View")
        self.clear_button.clicked.connect(self.clearAllLayers)
        self.layout.addWidget(self.clear_button)

        self.setLayout(self.layout)
        self.customizeViewer()
        self.viewer_pixel_size_mm = 1
        self.dz_um = None
        self.Nz = None
        self.channels = set()
        self.viewer_extents = []  # [min_y, max_y, min_x, max_x]
        self.top_left_coordinate = None  # [y, x] in mm
        self.mosaic_dtype = None

    def customizeViewer(self):
        # # hide status bar
        # if hasattr(self.viewer.window, "_status_bar"):
        #     self.viewer.window._status_bar.hide()

        # Disable napari's native menu bar so it doesn't take over macOS global menu bar
        if sys.platform == "darwin":
            self.viewer.window.main_menu.setNativeMenuBar(False)
        self.viewer.window.main_menu.hide()

        self.viewer.bind_key("D", self.toggle_draw_mode)

    def toggle_draw_mode(self, viewer):
        self.is_drawing_shape = not self.is_drawing_shape

        if "Manual ROI" not in self.viewer.layers:
            self.shape_layer = self.viewer.add_shapes(
                name="Manual ROI", edge_width=40, edge_color="red", face_color="transparent"
            )
            self.shape_layer.events.data.connect(self.on_shape_change)
        else:
            self.shape_layer = self.viewer.layers["Manual ROI"]

        if self.is_drawing_shape:
            # if there are existing shapes, switch to vertex select mode
            if len(self.shape_layer.data) > 0:
                self.shape_layer.mode = "select"
                self.shape_layer.select_mode = "vertex"
            else:
                # if no shapes exist, switch to add polygon mode
                # start drawing a new polygon on click, add vertices with additional clicks, finish/close polygon with double-click
                self.shape_layer.mode = "add_polygon"
        else:
            # if no shapes exist, switch to pan/zoom mode
            self.shape_layer.mode = "pan_zoom"

        self.on_shape_change()

    def enable_shape_drawing(self, enable):
        if enable:
            self.toggle_draw_mode(self.viewer)
        else:
            self.is_drawing_shape = False
            if self.shape_layer is not None:
                self.shape_layer.mode = "pan_zoom"

    def on_shape_change(self, event=None):
        if self.shape_layer is not None and len(self.shape_layer.data) > 0:
            # Only convert shapes to mm if mosaic is initialized (has valid coordinate system)
            if self.layers_initialized and self.top_left_coordinate is not None:
                self.shapes_mm = [self.convert_shape_to_mm(shape) for shape in self.shape_layer.data]
            # else: keep existing shapes_mm (they're already in mm from before clear)
        else:
            self.shapes_mm = []
        self.signal_shape_drawn.emit(self.shapes_mm)

    def convert_shape_to_mm(self, shape_data):
        shape_data_mm = []
        # Scale factor: viewer uses um (mm * 1000), so data coords = world coords / (pixel_size_mm * 1000)
        scale = self.viewer_pixel_size_mm * 1000
        for point in shape_data:
            # Convert world coordinates (um) to data coordinates (pixels)
            y_data = point[0] / scale
            x_data = point[1] / scale
            # Convert data coordinates to mm
            x_mm = self.top_left_coordinate[1] + x_data * self.viewer_pixel_size_mm
            y_mm = self.top_left_coordinate[0] + y_data * self.viewer_pixel_size_mm
            shape_data_mm.append([x_mm, y_mm])
        return np.array(shape_data_mm)

    def convert_mm_to_viewer_shapes(self, shapes_mm):
        viewer_shapes = []
        # Scale factor: viewer uses um (mm * 1000), so world coords = data coords * (pixel_size_mm * 1000)
        scale = self.viewer_pixel_size_mm * 1000
        for shape_mm in shapes_mm:
            viewer_shape = []
            for point_mm in shape_mm:
                # Convert mm to data coordinates (pixels)
                x_data = (point_mm[0] - self.top_left_coordinate[1]) / self.viewer_pixel_size_mm
                y_data = (point_mm[1] - self.top_left_coordinate[0]) / self.viewer_pixel_size_mm
                # Convert data coordinates to world coordinates (um)
                world_coords = [y_data * scale, x_data * scale]
                viewer_shape.append(world_coords)
            viewer_shapes.append(viewer_shape)
        return viewer_shapes

    def update_shape_layer_position(self, prev_top_left, new_top_left):
        if self.shape_layer is None or len(self.shapes_mm) == 0:
            return
        try:
            # update top_left_coordinate
            self.top_left_coordinate = new_top_left

            # convert mm coordinates to viewer coordinates
            new_shapes = self.convert_mm_to_viewer_shapes(self.shapes_mm)

            # update shape layer data
            self.shape_layer.data = new_shapes
        except Exception as e:
            print(f"Error updating shape layer position: {e}")
            import traceback

            traceback.print_exc()

    def initChannels(self, channels):
        self.channels = set(channels)

    def initLayersShape(self, Nz, dz):
        self.Nz = 1
        self.dz_um = dz

    def extractWavelength(self, name):
        # extract wavelength from channel name
        parts = name.split()
        if "Fluorescence" in parts:
            index = parts.index("Fluorescence") + 1
            if index < len(parts):
                return parts[index].split()[0]
        for color in ["R", "G", "B"]:
            if color in parts or f"full_{color}" in parts:
                return color
        return None

    def generateColormap(self, channel_info):
        # generate colormap from hex value
        c0 = (0, 0, 0)
        c1 = (
            ((channel_info["hex"] >> 16) & 0xFF) / 255,
            ((channel_info["hex"] >> 8) & 0xFF) / 255,
            (channel_info["hex"] & 0xFF) / 255,
        )
        return Colormap(colors=[c0, c1], controls=[0, 1], name=channel_info["name"])

    def updateMosaic(self, image, x_mm, y_mm, k, channel_name):
        # NOTE: Check runtime flag to allow MCP to disable mosaic updates for RAM debugging.
        # This enables toggling mosaic view without restarting the application.
        if not control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY:
            return

        # calculate pixel size
        pixel_size_um = self.objectiveStore.get_pixel_size_factor() * self.camera.get_pixel_size_binned_um()
        downsample_factor = max(1, int(MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM / pixel_size_um))
        image_pixel_size_um = pixel_size_um * downsample_factor
        image_pixel_size_mm = image_pixel_size_um / 1000
        image_dtype = image.dtype

        # downsample image
        if downsample_factor != 1:
            image = cv2.resize(
                image,
                (image.shape[1] // downsample_factor, image.shape[0] // downsample_factor),
                interpolation=cv2.INTER_AREA,
            )

        # adjust image position
        x_mm -= (image.shape[1] * image_pixel_size_mm) / 2
        y_mm -= (image.shape[0] * image_pixel_size_mm) / 2

        if not self.layers_initialized:
            # initialize mosaic state for first image (or after clearAllLayers)
            self.layers_initialized = True
            self.signal_layers_initialized.emit()
            self.viewer_pixel_size_mm = image_pixel_size_mm
            self.viewer_extents = [
                y_mm,
                y_mm + image.shape[0] * image_pixel_size_mm,
                x_mm,
                x_mm + image.shape[1] * image_pixel_size_mm,
            ]
            self.top_left_coordinate = [y_mm, x_mm]
            self.mosaic_dtype = image_dtype

            # Update Manual ROI shapes to new coordinate system if they exist
            if self.shape_layer is not None and len(self.shapes_mm) > 0:
                new_shapes = self.convert_mm_to_viewer_shapes(self.shapes_mm)
                self.shape_layer.data = new_shapes
        else:
            # convert image dtype and scale if necessary
            image = self.convertImageDtype(image, self.mosaic_dtype)
            if image_pixel_size_mm != self.viewer_pixel_size_mm:
                scale_factor = image_pixel_size_mm / self.viewer_pixel_size_mm
                image = cv2.resize(
                    image,
                    (int(image.shape[1] * scale_factor), int(image.shape[0] * scale_factor)),
                    interpolation=cv2.INTER_LINEAR,
                )

        if channel_name not in self.viewer.layers:
            # create new layer for channel
            channel_info = CHANNEL_COLORS_MAP.get(
                self.extractWavelength(channel_name), {"hex": 0xFFFFFF, "name": "gray"}
            )
            if channel_info["name"] in AVAILABLE_COLORMAPS:
                color = AVAILABLE_COLORMAPS[channel_info["name"]]
            else:
                color = self.generateColormap(channel_info)

            layer = self.viewer.add_image(
                np.zeros_like(image),
                name=channel_name,
                rgb=len(image.shape) == 3,
                colormap=color,
                visible=True,
                blending="additive",
                scale=(self.viewer_pixel_size_mm * 1000, self.viewer_pixel_size_mm * 1000),
            )
            layer.mouse_double_click_callbacks.append(self.onDoubleClick)
            layer.events.contrast_limits.connect(self.signalContrastLimits)

        # get layer for channel
        layer = self.viewer.layers[channel_name]

        # update extents
        self.viewer_extents[0] = min(self.viewer_extents[0], y_mm)
        self.viewer_extents[1] = max(self.viewer_extents[1], y_mm + image.shape[0] * self.viewer_pixel_size_mm)
        self.viewer_extents[2] = min(self.viewer_extents[2], x_mm)
        self.viewer_extents[3] = max(self.viewer_extents[3], x_mm + image.shape[1] * self.viewer_pixel_size_mm)

        # store previous top-left coordinate
        prev_top_left = self.top_left_coordinate.copy() if self.top_left_coordinate else None
        self.top_left_coordinate = [self.viewer_extents[0], self.viewer_extents[2]]

        # update layer
        self.updateLayer(layer, image, x_mm, y_mm, k, prev_top_left)

        # update contrast limits
        min_val, max_val = self.contrastManager.get_limits(channel_name)
        scaled_min = self.convertValue(min_val, self.contrastManager.acquisition_dtype, self.mosaic_dtype)
        scaled_max = self.convertValue(max_val, self.contrastManager.acquisition_dtype, self.mosaic_dtype)
        layer.contrast_limits = (scaled_min, scaled_max)
        layer.refresh()

    def updateLayer(self, layer, image, x_mm, y_mm, k, prev_top_left):
        # calculate new mosaic size and position
        mosaic_height = int(math.ceil((self.viewer_extents[1] - self.viewer_extents[0]) / self.viewer_pixel_size_mm))
        mosaic_width = int(math.ceil((self.viewer_extents[3] - self.viewer_extents[2]) / self.viewer_pixel_size_mm))

        is_rgb = len(image.shape) == 3 and image.shape[2] == 3
        if layer.data.shape[:2] != (mosaic_height, mosaic_width):
            # calculate offsets for existing data
            y_offset = int(math.floor((prev_top_left[0] - self.top_left_coordinate[0]) / self.viewer_pixel_size_mm))
            x_offset = int(math.floor((prev_top_left[1] - self.top_left_coordinate[1]) / self.viewer_pixel_size_mm))

            for mosaic in self.viewer.layers:
                if mosaic.name != "Manual ROI":
                    if len(mosaic.data.shape) == 3 and mosaic.data.shape[2] == 3:
                        new_data = np.zeros((mosaic_height, mosaic_width, 3), dtype=mosaic.data.dtype)
                    else:
                        new_data = np.zeros((mosaic_height, mosaic_width), dtype=mosaic.data.dtype)

                    # ensure offsets don't exceed bounds
                    y_end = min(y_offset + mosaic.data.shape[0], new_data.shape[0])
                    x_end = min(x_offset + mosaic.data.shape[1], new_data.shape[1])

                    # shift existing data
                    if len(mosaic.data.shape) == 3 and mosaic.data.shape[2] == 3:
                        new_data[y_offset:y_end, x_offset:x_end, :] = mosaic.data[
                            : y_end - y_offset, : x_end - x_offset, :
                        ]
                    else:
                        new_data[y_offset:y_end, x_offset:x_end] = mosaic.data[: y_end - y_offset, : x_end - x_offset]
                    mosaic.data = new_data

            if "Manual ROI" in self.viewer.layers:
                self.update_shape_layer_position(prev_top_left, self.top_left_coordinate)

            self.resetView()

        # insert new image
        y_pos = int(math.floor((y_mm - self.top_left_coordinate[0]) / self.viewer_pixel_size_mm))
        x_pos = int(math.floor((x_mm - self.top_left_coordinate[1]) / self.viewer_pixel_size_mm))

        # ensure indices are within bounds
        y_end = min(y_pos + image.shape[0], layer.data.shape[0])
        x_end = min(x_pos + image.shape[1], layer.data.shape[1])

        # insert image data
        if is_rgb:
            layer.data[y_pos:y_end, x_pos:x_end, :] = image[: y_end - y_pos, : x_end - x_pos, :]
        else:
            layer.data[y_pos:y_end, x_pos:x_end] = image[: y_end - y_pos, : x_end - x_pos]
        layer.refresh()

    def convertImageDtype(self, image, target_dtype):
        # convert image to target dtype
        if image.dtype == target_dtype:
            return image

        # get full range of values for both dtypes
        if np.issubdtype(image.dtype, np.integer):
            input_info = np.iinfo(image.dtype)
            input_min, input_max = input_info.min, input_info.max
        else:
            input_min, input_max = np.min(image), np.max(image)

        if np.issubdtype(target_dtype, np.integer):
            output_info = np.iinfo(target_dtype)
            output_min, output_max = output_info.min, output_info.max
        else:
            output_min, output_max = 0.0, 1.0

        # normalize and scale image
        image_normalized = (image.astype(np.float64) - input_min) / (input_max - input_min)
        image_scaled = image_normalized * (output_max - output_min) + output_min

        return image_scaled.astype(target_dtype)

    def convertValue(self, value, from_dtype, to_dtype):
        # Convert value from one dtype range to another
        from_info = np.iinfo(from_dtype)
        to_info = np.iinfo(to_dtype)

        # Normalize the value to [0, 1] range
        normalized = (value - from_info.min) / (from_info.max - from_info.min)

        # Scale to the target dtype range
        return normalized * (to_info.max - to_info.min) + to_info.min

    def signalContrastLimits(self, event):
        layer = event.source
        min_val, max_val = map(float, layer.contrast_limits)

        # Convert the new limits from mosaic_dtype to acquisition_dtype
        acquisition_min = self.convertValue(min_val, self.mosaic_dtype, self.contrastManager.acquisition_dtype)
        acquisition_max = self.convertValue(max_val, self.mosaic_dtype, self.contrastManager.acquisition_dtype)

        # Update the ContrastManager with the new limits
        self.contrastManager.update_limits(layer.name, acquisition_min, acquisition_max)

    def getContrastLimits(self, dtype):
        return self.contrastManager.get_default_limits()

    def onDoubleClick(self, layer, event):
        coords = layer.world_to_data(event.position)
        if coords is not None:
            x_mm = self.top_left_coordinate[1] + coords[-1] * self.viewer_pixel_size_mm
            y_mm = self.top_left_coordinate[0] + coords[-2] * self.viewer_pixel_size_mm
            print(f"move from click: ({x_mm:.6f}, {y_mm:.6f})")
            self.signal_coordinates_clicked.emit(x_mm, y_mm)

    def resetView(self):
        self.viewer.reset_view()
        for layer in self.viewer.layers:
            layer.refresh()

    def clear_shape(self):
        if self.shape_layer is not None:
            self.viewer.layers.remove(self.shape_layer)
            self.shape_layer = None
            self.is_drawing_shape = False
            self.signal_shape_drawn.emit([])

    def clearAllLayers(self):
        # Remove all layers except Manual ROI to free memory and allow proper reinitialization
        layers_to_remove = [layer for layer in self.viewer.layers if layer.name != "Manual ROI"]
        for layer in layers_to_remove:
            self.viewer.layers.remove(layer)

        # Reset mosaic-related state so reinitialization logic can run cleanly
        self.channels = set()
        self.viewer_extents = None
        self.layers_initialized = False
        self.top_left_coordinate = None
        self.mosaic_dtype = None

        # Force garbage collection to return memory to OS
        gc.collect()

        self.signal_clear_viewer.emit()

    def activate(self):
        self.viewer.window.activate()

    def get_screenshot(self) -> Optional[np.ndarray]:
        """Capture the current mosaic view as a numpy array.

        Returns:
            RGB image array of the current view, or None if no layers exist.
        """
        if not self.layers_initialized:
            return None
        try:
            # Use napari's screenshot functionality
            return self.viewer.screenshot(canvas_only=True)
        except Exception:
            return None


class NapariPlateViewWidget(QWidget):
    """Widget for displaying downsampled plate view with multi-channel support.

    Similar to NapariMosaicDisplayWidget but specifically for plate-based acquisitions.
    Displays downsampled well images in a grid layout.
    """

    signal_well_fov_clicked = Signal(str, int)  # well_id, fov_index

    def __init__(self, contrastManager, parent=None):
        super().__init__(parent)
        self.contrastManager = contrastManager
        self.viewer = napari.Viewer(show=False)
        # Disable napari's native menu bar so it doesn't take over macOS global menu bar
        if sys.platform == "darwin":
            self.viewer.window.main_menu.setNativeMenuBar(False)
        self.viewer.window.main_menu.hide()
        self.layout = QVBoxLayout()
        self.layout.addWidget(self.viewer.window._qt_window)

        # Clear button
        self.clear_button = QPushButton("Clear Plate View")
        self.clear_button.clicked.connect(self.clearAllLayers)
        self.layout.addWidget(self.clear_button)

        self.setLayout(self.layout)

        # Plate layout info (set by initPlateLayout)
        self.num_rows = 0
        self.num_cols = 0
        self.well_slot_shape = (0, 0)  # (height, width) pixels per well
        self.fov_grid_shape = (1, 1)  # (ny, nx) FOVs per well
        self.channel_names = []
        self.plate_dtype = None
        self.layers_initialized = False

        # Zoom limits (updated in initPlateLayout based on plate size)
        self.min_zoom = 0.1  # Prevent zooming out too far
        self.max_zoom = None  # No max limit until plate size is known
        # Flag to prevent recursive zoom clamping. This is safe because Qt's event
        # loop processes events sequentially on the main thread - _custom_wheel_event
        # and _on_zoom_changed cannot run concurrently, so no lock is needed.
        self._clamping_zoom = False

        # Override wheel event on vispy canvas to enforce zoom limits
        canvas_widget = self.viewer.window._qt_viewer.canvas.native
        canvas_widget.wheelEvent = self._custom_wheel_event

        # Clamp zoom for programmatic changes (e.g., reset_view)
        self.viewer.camera.events.zoom.connect(self._on_zoom_changed)

    def initPlateLayout(self, num_rows, num_cols, well_slot_shape, fov_grid_shape=None, channel_names=None):
        """Initialize plate layout for click coordinate calculations.

        Args:
            num_rows: Number of rows in the plate
            num_cols: Number of columns in the plate
            well_slot_shape: (height, width) of each well slot in pixels
            fov_grid_shape: (ny, nx) FOVs per well for click mapping
            channel_names: List of channel names
        """
        self.num_rows = num_rows
        self.num_cols = num_cols
        self.well_slot_shape = well_slot_shape
        self.fov_grid_shape = fov_grid_shape or (1, 1)
        self.channel_names = channel_names or []
        self.layers_initialized = False

        # Calculate zoom limits based on plate size
        plate_height = num_rows * well_slot_shape[0]
        plate_width = num_cols * well_slot_shape[1]
        if plate_height > 0 and plate_width > 0:
            # Max zoom: ensure at least MIN_VISIBLE_PIXELS visible, capped at MAX_ZOOM_FACTOR
            min_plate_dim = min(plate_height, plate_width)
            self.max_zoom = min(
                max(1.0, min_plate_dim / PLATE_VIEW_MIN_VISIBLE_PIXELS),
                PLATE_VIEW_MAX_ZOOM_FACTOR,
            )

        # Draw plate boundaries
        self._draw_plate_boundaries()

        # Reset view to fit plate, then capture that zoom as the min (zoom out limit)
        self.viewer.reset_view()
        self.min_zoom = self.viewer.camera.zoom

    def _custom_wheel_event(self, event):
        """Custom wheel event handler that enforces zoom limits."""
        # Block ALL wheel events from reaching vispy - we handle zoom ourselves
        event.accept()

        delta = event.angleDelta().y()
        if delta == 0:
            return

        # Calculate new zoom with our own factor
        zoom = self.viewer.camera.zoom
        zoom_factor = 1.1 ** (delta / 120.0)  # Standard wheel: 120 units per notch
        new_zoom = zoom * zoom_factor

        # Clamp to limits
        new_zoom = max(self.min_zoom, new_zoom)
        if self.max_zoom is not None:
            new_zoom = min(self.max_zoom, new_zoom)

        # Apply clamped zoom
        if new_zoom != zoom:
            self._clamping_zoom = True
            self.viewer.camera.zoom = new_zoom
            self._clamping_zoom = False

    def _on_zoom_changed(self, event):
        """Clamp zoom to limits after any zoom change (e.g., reset_view)."""
        if self._clamping_zoom:
            return
        zoom = self.viewer.camera.zoom
        target_zoom = zoom
        if zoom < self.min_zoom:
            target_zoom = self.min_zoom
        elif self.max_zoom is not None and zoom > self.max_zoom:
            target_zoom = self.max_zoom
        if target_zoom != zoom:
            self._clamping_zoom = True
            self.viewer.camera.zoom = target_zoom
            self._clamping_zoom = False

    def _draw_plate_boundaries(self):
        """Draw grid lines to show well boundaries.

        Uses O(rows + cols) lines instead of O(rows * cols) rectangles for better
        performance with large plates (e.g., 1536-well).
        """
        if self.num_rows == 0 or self.num_cols == 0:
            return
        if self.well_slot_shape[0] == 0 or self.well_slot_shape[1] == 0:
            return

        # Remove existing boundary layer
        if "_plate_boundaries" in self.viewer.layers:
            self.viewer.layers.remove("_plate_boundaries")

        lines = []
        slot_h, slot_w = self.well_slot_shape
        plate_height = self.num_rows * slot_h
        plate_width = self.num_cols * slot_w

        # Horizontal lines (num_rows + 1 lines)
        for row in range(self.num_rows + 1):
            y = row * slot_h
            lines.append([[y, 0], [y, plate_width]])

        # Vertical lines (num_cols + 1 lines)
        for col in range(self.num_cols + 1):
            x = col * slot_w
            lines.append([[0, x], [plate_height, x]])

        if lines:
            self.viewer.add_shapes(
                lines,
                shape_type="line",
                edge_color="white",
                edge_width=2,
                name="_plate_boundaries",
            )
            # Make boundaries layer non-interactive so it doesn't intercept clicks
            boundaries_layer = self.viewer.layers["_plate_boundaries"]
            boundaries_layer.mouse_pan = False
            boundaries_layer.mouse_zoom = False
            # Move boundaries layer to bottom
            self.viewer.layers.move(len(self.viewer.layers) - 1, 0)
            # Ensure an image layer is selected, not the shapes layer
            for layer in reversed(self.viewer.layers):
                if layer.name != "_plate_boundaries":
                    self.viewer.layers.selection.active = layer
                    break

    def extractWavelength(self, name):
        """Extract wavelength from channel name for colormap selection."""
        parts = name.split()
        if "Fluorescence" in parts:
            index = parts.index("Fluorescence") + 1
            if index < len(parts):
                return parts[index].split()[0]
        for color in ["R", "G", "B"]:
            if color in parts or f"full_{color}" in parts:
                return color
        return None

    def generateColormap(self, channel_info):
        """Generate colormap from hex value."""
        c0 = (0, 0, 0)
        c1 = (
            ((channel_info["hex"] >> 16) & 0xFF) / 255,
            ((channel_info["hex"] >> 8) & 0xFF) / 255,
            (channel_info["hex"] & 0xFF) / 255,
        )
        return Colormap(colors=[c0, c1], controls=[0, 1], name=channel_info["name"])

    def updatePlateView(self, channel_idx, channel_name, plate_image):
        """Update a single channel's plate view.

        Args:
            channel_idx: Channel index (0-based)
            channel_name: Name of the channel
            plate_image: 2D numpy array with the channel's plate view
        """
        if plate_image is None:
            return

        if not self.layers_initialized:
            self.layers_initialized = True
            self.plate_dtype = plate_image.dtype

        if channel_name not in self.viewer.layers:
            # Create layer with appropriate colormap
            wavelength = self.extractWavelength(channel_name)
            channel_info = (
                CHANNEL_COLORS_MAP.get(wavelength, {"hex": 0xFFFFFF, "name": "gray"})
                if wavelength is not None
                else {"hex": 0xFFFFFF, "name": "gray"}
            )
            if channel_info["name"] in AVAILABLE_COLORMAPS:
                color = AVAILABLE_COLORMAPS[channel_info["name"]]
            else:
                color = self.generateColormap(channel_info)

            layer = self.viewer.add_image(
                plate_image,
                name=channel_name,
                colormap=color,
                visible=True,
                blending="additive",
            )
            layer.mouse_double_click_callbacks.append(self.onDoubleClick)
            layer.events.contrast_limits.connect(self.signalContrastLimits)
        else:
            self.viewer.layers[channel_name].data = plate_image

        # Apply contrast from contrastManager
        layer = self.viewer.layers[channel_name]
        min_val, max_val = self.contrastManager.get_limits(channel_name)
        layer.contrast_limits = (min_val, max_val)
        layer.refresh()

    def signalContrastLimits(self, event):
        """Handle contrast limit changes and propagate to contrastManager."""
        layer = event.source
        min_val, max_val = layer.contrast_limits
        self.contrastManager.update_limits(layer.name, min_val, max_val)

    def onDoubleClick(self, layer, event):
        """Handle double-click: calculate well_id and fov_index."""
        coords = layer.world_to_data(event.position)
        if coords is None or self.well_slot_shape[0] == 0 or self.well_slot_shape[1] == 0:
            return

        y, x = int(coords[-2]), int(coords[-1])

        # Calculate well position
        well_row = y // self.well_slot_shape[0]
        well_col = x // self.well_slot_shape[1]

        # Validate well position
        if well_row < 0 or well_row >= self.num_rows or well_col < 0 or well_col >= self.num_cols:
            print(f"Clicked outside plate bounds: row={well_row}, col={well_col}")
            return

        # Generate well ID using shared utility (inverse of parse_well_id)
        well_id = format_well_id(well_row, well_col)

        # Calculate FOV within well
        y_in_well = y % self.well_slot_shape[0]
        x_in_well = x % self.well_slot_shape[1]

        fov_ny, fov_nx = self.fov_grid_shape
        if fov_ny > 0 and fov_nx > 0:
            fov_height = self.well_slot_shape[0] // fov_ny
            fov_width = self.well_slot_shape[1] // fov_nx
            if fov_height > 0 and fov_width > 0:
                # Clamp to valid range to handle clicks at edge of well slot
                fov_row = min(y_in_well // fov_height, fov_ny - 1)
                fov_col = min(x_in_well // fov_width, fov_nx - 1)
                fov_index = fov_row * fov_nx + fov_col
            else:
                fov_index = 0
        else:
            fov_index = 0

        print(f"Clicked: Well {well_id}, FOV {fov_index}")
        self.signal_well_fov_clicked.emit(well_id, fov_index)

    def resetView(self):
        """Reset the viewer to fit all data."""
        self.viewer.reset_view()
        for layer in self.viewer.layers:
            layer.refresh()

    def clearAllLayers(self):
        """Clear all layers to free memory."""
        layers_to_remove = list(self.viewer.layers)
        for layer in layers_to_remove:
            self.viewer.layers.remove(layer)

        self.layers_initialized = False
        self.plate_dtype = None
        gc.collect()

    def activate(self):
        """Activate the viewer window."""
        self.viewer.window.activate()


class TrackingControllerWidget(QFrame):
    def __init__(
        self,
        trackingController: TrackingController,
        objectiveStore,
        show_configurations=True,
        main=None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.trackingController = trackingController
        self.objectiveStore = objectiveStore
        self.base_path_is_set = False
        self.add_components(show_configurations)
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

        self.trackingController.microcontroller.add_joystick_button_listener(
            lambda button_pressed: self.handle_button_state(button_pressed)
        )

    def add_components(self, show_configurations):
        self.btn_setSavingDir = QPushButton("Browse")
        self.btn_setSavingDir.setDefault(False)
        self.btn_setSavingDir.setIcon(QIcon("icon/folder.png"))
        self.lineEdit_savingDir = QLineEdit()
        self.lineEdit_savingDir.setReadOnly(True)
        self.lineEdit_savingDir.setText("Choose a base saving directory")
        self.lineEdit_savingDir.setText(DEFAULT_SAVING_PATH)
        self.trackingController.set_base_path(DEFAULT_SAVING_PATH)
        self.base_path_is_set = True

        self.lineEdit_experimentID = QLineEdit()

        # self.dropdown_objective = QComboBox()
        # self.dropdown_objective.addItems(list(OBJECTIVES.keys()))
        # self.dropdown_objective.setCurrentText(DEFAULT_OBJECTIVE)
        self.objectivesWidget = ObjectivesWidget(self.objectiveStore)

        self.dropdown_tracker = QComboBox()
        self.dropdown_tracker.addItems(TRACKERS)
        self.dropdown_tracker.setCurrentText(DEFAULT_TRACKER)

        self.entry_tracking_interval = QDoubleSpinBox()
        self.entry_tracking_interval.setKeyboardTracking(False)
        self.entry_tracking_interval.setMinimum(0)
        self.entry_tracking_interval.setMaximum(30)
        self.entry_tracking_interval.setSingleStep(0.5)
        self.entry_tracking_interval.setValue(0)

        self.list_configurations = QListWidget()
        for microscope_configuration in self.trackingController.liveController.get_channels(
            self.objectiveStore.current_objective
        ):
            self.list_configurations.addItems([microscope_configuration.name])
        self.list_configurations.setSelectionMode(
            QAbstractItemView.MultiSelection
        )  # ref: https://doc.qt.io/qt-5/qabstractitemview.html#SelectionMode-enum

        self.checkbox_withAutofocus = QCheckBox("With AF")
        self.checkbox_saveImages = QCheckBox("Save Images")
        self.btn_track = QPushButton("Start Tracking")
        self.btn_track.setCheckable(True)
        self.btn_track.setChecked(False)

        self.checkbox_enable_stage_tracking = QCheckBox(" Enable Stage Tracking")
        self.checkbox_enable_stage_tracking.setChecked(True)

        # layout
        grid_line0 = QGridLayout()
        tmp = QLabel("Saving Path")
        tmp.setFixedWidth(90)
        grid_line0.addWidget(tmp, 0, 0)
        grid_line0.addWidget(self.lineEdit_savingDir, 0, 1, 1, 2)
        grid_line0.addWidget(self.btn_setSavingDir, 0, 3)
        tmp = QLabel("Experiment ID")
        tmp.setFixedWidth(90)
        grid_line0.addWidget(tmp, 1, 0)
        grid_line0.addWidget(self.lineEdit_experimentID, 1, 1, 1, 1)
        tmp = QLabel("Objective")
        tmp.setFixedWidth(90)
        # grid_line0.addWidget(tmp,1,2)
        # grid_line0.addWidget(self.dropdown_objective, 1,3)
        grid_line0.addWidget(tmp, 1, 2)
        grid_line0.addWidget(self.objectivesWidget, 1, 3)

        grid_line3 = QHBoxLayout()
        tmp = QLabel("Configurations")
        tmp.setFixedWidth(90)
        grid_line3.addWidget(tmp)
        grid_line3.addWidget(self.list_configurations)

        grid_line1 = QHBoxLayout()
        tmp = QLabel("Tracker")
        grid_line1.addWidget(tmp)
        grid_line1.addWidget(self.dropdown_tracker)
        tmp = QLabel("Tracking Interval (s)")
        grid_line1.addWidget(tmp)
        grid_line1.addWidget(self.entry_tracking_interval)
        grid_line1.addWidget(self.checkbox_withAutofocus)
        grid_line1.addWidget(self.checkbox_saveImages)

        grid_line4 = QGridLayout()
        grid_line4.addWidget(self.btn_track, 0, 0, 1, 3)
        grid_line4.addWidget(self.checkbox_enable_stage_tracking, 0, 4)

        self.grid = QVBoxLayout()
        self.grid.addLayout(grid_line0)
        if show_configurations:
            self.grid.addLayout(grid_line3)
        else:
            self.list_configurations.setCurrentRow(0)  # select the first configuration
        self.grid.addLayout(grid_line1)
        self.grid.addLayout(grid_line4)
        self.grid.addStretch()
        self.setLayout(self.grid)

        # connections - buttons, checkboxes, entries
        self.checkbox_enable_stage_tracking.stateChanged.connect(self.trackingController.toggle_stage_tracking)
        self.checkbox_withAutofocus.stateChanged.connect(self.trackingController.toggel_enable_af)
        self.checkbox_saveImages.stateChanged.connect(self.trackingController.toggel_save_images)
        self.entry_tracking_interval.valueChanged.connect(self.trackingController.set_tracking_time_interval)
        self.btn_setSavingDir.clicked.connect(self.set_saving_dir)
        self.btn_track.clicked.connect(self.toggle_acquisition)
        # connections - selections and entries
        self.dropdown_tracker.currentIndexChanged.connect(self.update_tracker)
        # self.dropdown_objective.currentIndexChanged.connect(self.update_pixel_size)
        self.objectivesWidget.dropdown.currentIndexChanged.connect(self.update_pixel_size)
        # controller to widget
        self.trackingController.signal_tracking_stopped.connect(self.slot_tracking_stopped)

        # run initialization functions
        self.update_pixel_size()
        self.trackingController.update_image_resizing_factor(1)  # to add: image resizing slider

    # TODO(imo): This needs testing!
    def handle_button_pressed(self, button_state):
        QMetaObject.invokeMethod(self, "slot_joystick_button_pressed", Qt.AutoConnection, button_state)

    def slot_joystick_button_pressed(self, button_state):
        self.btn_track.setChecked(button_state)
        if self.btn_track.isChecked():
            if self.base_path_is_set == False:
                self.btn_track.setChecked(False)
                msg = QMessageBox()
                msg.setText("Please choose base saving directory first")
                msg.exec_()
                return
            self.setEnabled_all(False)
            self.trackingController.start_new_experiment(self.lineEdit_experimentID.text())
            self.trackingController.set_selected_configurations(
                (item.text() for item in self.list_configurations.selectedItems())
            )
            self.trackingController.start_tracking()
        else:
            self.trackingController.stop_tracking()

    def slot_tracking_stopped(self):
        self.btn_track.setChecked(False)
        self.setEnabled_all(True)
        print("tracking stopped")

    def set_saving_dir(self):
        dialog = QFileDialog()
        save_dir_base = dialog.getExistingDirectory(None, "Select Folder")
        self.trackingController.set_base_path(save_dir_base)
        self.lineEdit_savingDir.setText(save_dir_base)
        self.base_path_is_set = True

    def toggle_acquisition(self, pressed):
        if pressed:
            if self.base_path_is_set == False:
                self.btn_track.setChecked(False)
                msg = QMessageBox()
                msg.setText("Please choose base saving directory first")
                msg.exec_()
                return
            # @@@ to do: add a widgetManger to enable and disable widget
            # @@@ to do: emit signal to widgetManager to disable other widgets
            self.setEnabled_all(False)
            self.trackingController.start_new_experiment(self.lineEdit_experimentID.text())
            self.trackingController.set_selected_configurations(
                (item.text() for item in self.list_configurations.selectedItems())
            )
            self.trackingController.start_tracking()
        else:
            self.trackingController.stop_tracking()

    def setEnabled_all(self, enabled):
        self.btn_setSavingDir.setEnabled(enabled)
        self.lineEdit_savingDir.setEnabled(enabled)
        self.lineEdit_experimentID.setEnabled(enabled)
        # self.dropdown_tracker
        # self.dropdown_objective
        self.list_configurations.setEnabled(enabled)

    def update_tracker(self, index):
        self.trackingController.update_tracker_selection(self.dropdown_tracker.currentText())

    def update_pixel_size(self):
        objective = self.dropdown_objective.currentText()
        self.trackingController.objective = objective
        # self.internal_state.data['Objective'] = self.objective
        # TODO: these pixel size code needs to be updated.
        pixel_size_um = CAMERA_PIXEL_SIZE_UM[CAMERA_SENSOR] / (
            TUBE_LENS_MM / (OBJECTIVES[objective]["tube_lens_f_mm"] / OBJECTIVES[objective]["magnification"])
        )
        self.trackingController.update_pixel_size(pixel_size_um)
        print("pixel size is " + str(pixel_size_um) + " μm")

    def update_pixel_size(self):
        objective = self.objectiveStore.current_objective
        self.trackingController.objective = objective
        objective_info = self.objectiveStore.objectives_dict[objective]
        magnification = objective_info["magnification"]
        objective_tube_lens_mm = objective_info["tube_lens_f_mm"]
        tube_lens_mm = TUBE_LENS_MM
        # TODO: these pixel size code needs to be updated.
        pixel_size_um = CAMERA_PIXEL_SIZE_UM[CAMERA_SENSOR]
        pixel_size_xy = pixel_size_um / (magnification / (objective_tube_lens_mm / tube_lens_mm))
        self.trackingController.update_pixel_size(pixel_size_xy)
        print(f"pixel size is {pixel_size_xy:.2f} μm")


class PlateReaderAcquisitionWidget(QFrame):
    def __init__(
        self, plateReadingController, configurationManager=None, show_configurations=True, main=None, *args, **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.plateReadingController = plateReadingController
        self.configurationManager = configurationManager
        self.base_path_is_set = False
        self.add_components(show_configurations)
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def add_components(self, show_configurations):
        self.btn_setSavingDir = QPushButton("Browse")
        self.btn_setSavingDir.setDefault(False)
        self.btn_setSavingDir.setIcon(QIcon("icon/folder.png"))
        self.lineEdit_savingDir = QLineEdit()
        self.lineEdit_savingDir.setReadOnly(True)
        self.lineEdit_savingDir.setText("Choose a base saving directory")
        self.lineEdit_savingDir.setText(DEFAULT_SAVING_PATH)
        self.plateReadingController.set_base_path(DEFAULT_SAVING_PATH)
        self.base_path_is_set = True

        self.lineEdit_experimentID = QLineEdit()

        self.list_columns = QListWidget()
        for i in range(PLATE_READER.NUMBER_OF_COLUMNS):
            self.list_columns.addItems([str(i + 1)])
        self.list_columns.setSelectionMode(
            QAbstractItemView.MultiSelection
        )  # ref: https://doc.qt.io/qt-5/qabstractitemview.html#SelectionMode-enum

        self.list_configurations = QListWidget()
        for microscope_configuration in self.configurationManager.configurations:
            self.list_configurations.addItems([microscope_configuration.name])
        self.list_configurations.setSelectionMode(
            QAbstractItemView.MultiSelection
        )  # ref: https://doc.qt.io/qt-5/qabstractitemview.html#SelectionMode-enum

        self.checkbox_withAutofocus = QCheckBox("With AF")
        self.btn_startAcquisition = QPushButton("Start Acquisition")
        self.btn_startAcquisition.setCheckable(True)
        self.btn_startAcquisition.setChecked(False)

        self.btn_startAcquisition.setEnabled(False)

        # layout
        grid_line0 = QGridLayout()
        tmp = QLabel("Saving Path")
        tmp.setFixedWidth(90)
        grid_line0.addWidget(tmp)
        grid_line0.addWidget(self.lineEdit_savingDir, 0, 1)
        grid_line0.addWidget(self.btn_setSavingDir, 0, 2)

        grid_line1 = QGridLayout()
        tmp = QLabel("Sample ID")
        tmp.setFixedWidth(90)
        grid_line1.addWidget(tmp)
        grid_line1.addWidget(self.lineEdit_experimentID, 0, 1)

        grid_line2 = QGridLayout()
        tmp = QLabel("Columns")
        tmp.setFixedWidth(90)
        grid_line2.addWidget(tmp)
        grid_line2.addWidget(self.list_columns, 0, 1)

        grid_line3 = QHBoxLayout()
        tmp = QLabel("Configurations")
        tmp.setFixedWidth(90)
        grid_line3.addWidget(tmp)
        grid_line3.addWidget(self.list_configurations)
        # grid_line3.addWidget(self.checkbox_withAutofocus)

        self.grid = QGridLayout()
        self.grid.addLayout(grid_line0, 0, 0)
        self.grid.addLayout(grid_line1, 1, 0)
        self.grid.addLayout(grid_line2, 2, 0)
        if show_configurations:
            self.grid.addLayout(grid_line3, 3, 0)
        else:
            self.list_configurations.setCurrentRow(0)  # select the first configuration
        self.grid.addWidget(self.btn_startAcquisition, 4, 0)
        self.setLayout(self.grid)

        # add and display a timer - to be implemented
        # self.timer = QTimer()

        # connections
        self.checkbox_withAutofocus.stateChanged.connect(self.plateReadingController.set_af_flag)
        self.btn_setSavingDir.clicked.connect(self.set_saving_dir)
        self.btn_startAcquisition.clicked.connect(self.toggle_acquisition)
        self.plateReadingController.acquisitionFinished.connect(self.acquisition_is_finished)

    def set_saving_dir(self):
        dialog = QFileDialog()
        save_dir_base = dialog.getExistingDirectory(None, "Select Folder")
        self.plateReadingController.set_base_path(save_dir_base)
        self.lineEdit_savingDir.setText(save_dir_base)
        self.base_path_is_set = True

    def toggle_acquisition(self, pressed):
        if self.base_path_is_set == False:
            self.btn_startAcquisition.setChecked(False)
            msg = QMessageBox()
            msg.setText("Please choose base saving directory first")
            msg.exec_()
            return
        if pressed:
            # @@@ to do: add a widgetManger to enable and disable widget
            # @@@ to do: emit signal to widgetManager to disable other widgets
            self.setEnabled_all(False)
            self.plateReadingController.start_new_experiment(self.lineEdit_experimentID.text())
            self.plateReadingController.set_selected_configurations(
                (item.text() for item in self.list_configurations.selectedItems())
            )
            self.plateReadingController.set_selected_columns(
                list(map(int, [item.text() for item in self.list_columns.selectedItems()]))
            )
            self.plateReadingController.run_acquisition()
        else:
            self.plateReadingController.stop_acquisition()  # to implement
            pass

    def acquisition_is_finished(self):
        self.btn_startAcquisition.setChecked(False)
        self.setEnabled_all(True)

    def setEnabled_all(self, enabled, exclude_btn_startAcquisition=False):
        self.btn_setSavingDir.setEnabled(enabled)
        self.lineEdit_savingDir.setEnabled(enabled)
        self.lineEdit_experimentID.setEnabled(enabled)
        self.list_columns.setEnabled(enabled)
        self.list_configurations.setEnabled(enabled)
        self.checkbox_withAutofocus.setEnabled(enabled)
        self.checkbox_withReflectionAutofocus.setEnabled(enabled)
        if exclude_btn_startAcquisition is not True:
            self.btn_startAcquisition.setEnabled(enabled)

    def slot_homing_complete(self):
        self.btn_startAcquisition.setEnabled(True)


class PlateReaderNavigationWidget(QFrame):
    def __init__(self, plateReaderNavigationController, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        self.plateReaderNavigationController = plateReaderNavigationController

    def add_components(self):
        self.dropdown_column = QComboBox()
        self.dropdown_column.addItems([""])
        self.dropdown_column.addItems([str(i + 1) for i in range(PLATE_READER.NUMBER_OF_COLUMNS)])
        self.dropdown_row = QComboBox()
        self.dropdown_row.addItems([""])
        self.dropdown_row.addItems([chr(i) for i in range(ord("A"), ord("A") + PLATE_READER.NUMBER_OF_ROWS)])
        self.btn_moveto = QPushButton("Move To")
        self.btn_home = QPushButton("Home")
        self.label_current_location = QLabel()
        self.label_current_location.setFrameStyle(QFrame.Panel | QFrame.Sunken)
        self.label_current_location.setFixedWidth(50)

        self.dropdown_column.setEnabled(False)
        self.dropdown_row.setEnabled(False)
        self.btn_moveto.setEnabled(False)

        # layout
        grid_line0 = QHBoxLayout()
        # tmp = QLabel('Saving Path')
        # tmp.setFixedWidth(90)
        grid_line0.addWidget(self.btn_home)
        grid_line0.addWidget(QLabel("Column"))
        grid_line0.addWidget(self.dropdown_column)
        grid_line0.addWidget(QLabel("Row"))
        grid_line0.addWidget(self.dropdown_row)
        grid_line0.addWidget(self.btn_moveto)
        grid_line0.addStretch()
        grid_line0.addWidget(self.label_current_location)

        self.grid = QGridLayout()
        self.grid.addLayout(grid_line0, 0, 0)
        self.setLayout(self.grid)

        self.btn_home.clicked.connect(self.home)
        self.btn_moveto.clicked.connect(self.move)

    def home(self):
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Information)
        msg.setText("Confirm your action")
        msg.setInformativeText("Click OK to run homing")
        msg.setWindowTitle("Confirmation")
        msg.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        msg.setDefaultButton(QMessageBox.Cancel)
        retval = msg.exec_()
        if QMessageBox.Ok == retval:
            self.plateReaderNavigationController.home()

    def move(self):
        self.plateReaderNavigationController.moveto(self.dropdown_column.currentText(), self.dropdown_row.currentText())

    def slot_homing_complete(self):
        self.dropdown_column.setEnabled(True)
        self.dropdown_row.setEnabled(True)
        self.btn_moveto.setEnabled(True)

    def update_current_location(self, location_str):
        self.label_current_location.setText(location_str)
        row = location_str[0]
        column = location_str[1:]
        self.dropdown_row.setCurrentText(row)
        self.dropdown_column.setCurrentText(column)


class TriggerControlWidget(QFrame):
    # for synchronized trigger
    signal_toggle_live = Signal(bool)
    signal_trigger_mode = Signal(str)
    signal_trigger_fps = Signal(float)

    def __init__(self, microcontroller2):
        super().__init__()
        self.fps_trigger = 10
        self.fps_display = 10
        self.microcontroller2 = microcontroller2
        self.triggerMode = TriggerMode.SOFTWARE
        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def add_components(self):
        # line 0: trigger mode
        self.triggerMode = None
        self.dropdown_triggerManu = QComboBox()
        self.dropdown_triggerManu.addItems([TriggerMode.SOFTWARE, TriggerMode.HARDWARE])

        # line 1: fps
        self.entry_triggerFPS = QDoubleSpinBox()
        self.entry_triggerFPS.setKeyboardTracking(False)
        self.entry_triggerFPS.setMinimum(0.02)
        self.entry_triggerFPS.setMaximum(1000)
        self.entry_triggerFPS.setSingleStep(1)
        self.entry_triggerFPS.setValue(self.fps_trigger)

        self.btn_live = QPushButton("Live")
        self.btn_live.setCheckable(True)
        self.btn_live.setChecked(False)
        self.btn_live.setDefault(False)

        # connections
        self.dropdown_triggerManu.currentIndexChanged.connect(self.update_trigger_mode)
        self.btn_live.clicked.connect(self.toggle_live)
        self.entry_triggerFPS.valueChanged.connect(self.update_trigger_fps)

        # inititialization
        self.microcontroller2.set_camera_trigger_frequency(self.fps_trigger)

        # layout
        grid_line0 = QGridLayout()
        grid_line0.addWidget(QLabel("Trigger Mode"), 0, 0)
        grid_line0.addWidget(self.dropdown_triggerManu, 0, 1)
        grid_line0.addWidget(QLabel("Trigger FPS"), 0, 2)
        grid_line0.addWidget(self.entry_triggerFPS, 0, 3)
        grid_line0.addWidget(self.btn_live, 1, 0, 1, 4)
        self.setLayout(grid_line0)

    def toggle_live(self, pressed):
        self.signal_toggle_live.emit(pressed)
        if pressed:
            self.microcontroller2.start_camera_trigger()
        else:
            self.microcontroller2.stop_camera_trigger()

    def update_trigger_mode(self):
        self.signal_trigger_mode.emit(self.dropdown_triggerManu.currentText())

    def update_trigger_fps(self, fps):
        self.fps_trigger = fps
        self.signal_trigger_fps.emit(fps)
        self.microcontroller2.set_camera_trigger_frequency(self.fps_trigger)


class MultiCameraRecordingWidget(QFrame):
    def __init__(self, streamHandler, imageSaver, channels, main=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.imageSaver = imageSaver  # for saving path control
        self.streamHandler = streamHandler
        self.channels = channels
        self.base_path_is_set = False
        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def add_components(self):
        self.btn_setSavingDir = QPushButton("Browse")
        self.btn_setSavingDir.setDefault(False)
        self.btn_setSavingDir.setIcon(QIcon("icon/folder.png"))

        self.lineEdit_savingDir = QLineEdit()
        self.lineEdit_savingDir.setReadOnly(True)
        self.lineEdit_savingDir.setText("Choose a base saving directory")

        self.lineEdit_experimentID = QLineEdit()

        self.entry_saveFPS = QDoubleSpinBox()
        self.entry_saveFPS.setKeyboardTracking(False)
        self.entry_saveFPS.setMinimum(0.02)
        self.entry_saveFPS.setMaximum(1000)
        self.entry_saveFPS.setSingleStep(1)
        self.entry_saveFPS.setValue(1)
        for channel in self.channels:
            self.streamHandler[channel].set_save_fps(1)

        self.entry_timeLimit = QSpinBox()
        self.entry_timeLimit.setKeyboardTracking(False)
        self.entry_timeLimit.setMinimum(-1)
        self.entry_timeLimit.setMaximum(60 * 60 * 24 * 30)
        self.entry_timeLimit.setSingleStep(1)
        self.entry_timeLimit.setValue(-1)

        self.btn_record = QPushButton("Record")
        self.btn_record.setCheckable(True)
        self.btn_record.setChecked(False)
        self.btn_record.setDefault(False)

        grid_line1 = QGridLayout()
        grid_line1.addWidget(QLabel("Saving Path"))
        grid_line1.addWidget(self.lineEdit_savingDir, 0, 1)
        grid_line1.addWidget(self.btn_setSavingDir, 0, 2)

        grid_line2 = QGridLayout()
        grid_line2.addWidget(QLabel("Experiment ID"), 0, 0)
        grid_line2.addWidget(self.lineEdit_experimentID, 0, 1)

        grid_line3 = QGridLayout()
        grid_line3.addWidget(QLabel("Saving FPS"), 0, 0)
        grid_line3.addWidget(self.entry_saveFPS, 0, 1)
        grid_line3.addWidget(QLabel("Time Limit (s)"), 0, 2)
        grid_line3.addWidget(self.entry_timeLimit, 0, 3)
        grid_line3.addWidget(self.btn_record, 0, 4)

        self.grid = QGridLayout()
        self.grid.addLayout(grid_line1, 0, 0)
        self.grid.addLayout(grid_line2, 1, 0)
        self.grid.addLayout(grid_line3, 2, 0)
        self.setLayout(self.grid)

        # add and display a timer - to be implemented
        # self.timer = QTimer()

        # connections
        self.btn_setSavingDir.clicked.connect(self.set_saving_dir)
        self.btn_record.clicked.connect(self.toggle_recording)
        for channel in self.channels:
            self.entry_saveFPS.valueChanged.connect(self.streamHandler[channel].set_save_fps)
            self.entry_timeLimit.valueChanged.connect(self.imageSaver[channel].set_recording_time_limit)
            self.imageSaver[channel].stop_recording.connect(self.stop_recording)

    def set_saving_dir(self):
        dialog = QFileDialog()
        save_dir_base = dialog.getExistingDirectory(None, "Select Folder")
        for channel in self.channels:
            self.imageSaver[channel].set_base_path(save_dir_base)
        self.lineEdit_savingDir.setText(save_dir_base)
        self.save_dir_base = save_dir_base
        self.base_path_is_set = True

    def toggle_recording(self, pressed):
        if self.base_path_is_set == False:
            self.btn_record.setChecked(False)
            msg = QMessageBox()
            msg.setText("Please choose base saving directory first")
            msg.exec_()
            return
        if pressed:
            self.lineEdit_experimentID.setEnabled(False)
            self.btn_setSavingDir.setEnabled(False)
            experiment_ID = self.lineEdit_experimentID.text()
            experiment_ID = experiment_ID + "_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f")
            utils.ensure_directory_exists(os.path.join(self.save_dir_base, experiment_ID))
            for channel in self.channels:
                self.imageSaver[channel].start_new_experiment(os.path.join(experiment_ID, channel), add_timestamp=False)
                self.streamHandler[channel].start_recording()
        else:
            for channel in self.channels:
                self.streamHandler[channel].stop_recording()
            self.lineEdit_experimentID.setEnabled(True)
            self.btn_setSavingDir.setEnabled(True)

    # stop_recording can be called by imageSaver
    def stop_recording(self):
        self.lineEdit_experimentID.setEnabled(True)
        self.btn_record.setChecked(False)
        for channel in self.channels:
            self.streamHandler[channel].stop_recording()
        self.btn_setSavingDir.setEnabled(True)


class WaveformDisplay(QFrame):

    def __init__(self, N=1000, include_x=True, include_y=True, main=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.N = N
        self.include_x = include_x
        self.include_y = include_y
        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def add_components(self):
        self.plotWidget = {}
        self.plotWidget["X"] = PlotWidget("X", N=self.N, add_legend=True)
        self.plotWidget["Y"] = PlotWidget("X", N=self.N, add_legend=True)

        layout = QGridLayout()  # layout = QStackedLayout()
        if self.include_x:
            layout.addWidget(self.plotWidget["X"], 0, 0)
        if self.include_y:
            layout.addWidget(self.plotWidget["Y"], 1, 0)
        self.setLayout(layout)

    def plot(self, time, data):
        if self.include_x:
            self.plotWidget["X"].plot(time, data[0, :], "X", color=(255, 255, 255), clear=True)
        if self.include_y:
            self.plotWidget["Y"].plot(time, data[1, :], "Y", color=(255, 255, 255), clear=True)

    def update_N(self, N):
        self.N = N
        self.plotWidget["X"].update_N(N)
        self.plotWidget["Y"].update_N(N)


class PlotWidget(pg.GraphicsLayoutWidget):

    def __init__(self, title="", N=1000, parent=None, add_legend=False):
        super().__init__(parent)
        self.plotWidget = self.addPlot(title="", axisItems={"bottom": pg.DateAxisItem()})
        if add_legend:
            self.plotWidget.addLegend()
        self.N = N

    def plot(self, x, y, label, color, clear=False):
        self.plotWidget.plot(x[-self.N :], y[-self.N :], pen=pg.mkPen(color=color, width=4), name=label, clear=clear)

    def update_N(self, N):
        self.N = N


class DisplacementMeasurementWidget(QFrame):
    def __init__(self, displacementMeasurementController, waveformDisplay, main=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.displacementMeasurementController = displacementMeasurementController
        self.waveformDisplay = waveformDisplay
        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def add_components(self):
        self.entry_x_offset = QDoubleSpinBox()
        self.entry_x_offset.setMinimum(0)
        self.entry_x_offset.setMaximum(3000)
        self.entry_x_offset.setSingleStep(0.2)
        self.entry_x_offset.setDecimals(3)
        self.entry_x_offset.setValue(0)
        self.entry_x_offset.setKeyboardTracking(False)

        self.entry_y_offset = QDoubleSpinBox()
        self.entry_y_offset.setMinimum(0)
        self.entry_y_offset.setMaximum(3000)
        self.entry_y_offset.setSingleStep(0.2)
        self.entry_y_offset.setDecimals(3)
        self.entry_y_offset.setValue(0)
        self.entry_y_offset.setKeyboardTracking(False)

        self.entry_x_scaling = QDoubleSpinBox()
        self.entry_x_scaling.setMinimum(-100)
        self.entry_x_scaling.setMaximum(100)
        self.entry_x_scaling.setSingleStep(0.1)
        self.entry_x_scaling.setDecimals(3)
        self.entry_x_scaling.setValue(1)
        self.entry_x_scaling.setKeyboardTracking(False)

        self.entry_y_scaling = QDoubleSpinBox()
        self.entry_y_scaling.setMinimum(-100)
        self.entry_y_scaling.setMaximum(100)
        self.entry_y_scaling.setSingleStep(0.1)
        self.entry_y_scaling.setDecimals(3)
        self.entry_y_scaling.setValue(1)
        self.entry_y_scaling.setKeyboardTracking(False)

        self.entry_N_average = QSpinBox()
        self.entry_N_average.setMinimum(1)
        self.entry_N_average.setMaximum(25)
        self.entry_N_average.setSingleStep(1)
        self.entry_N_average.setValue(1)
        self.entry_N_average.setKeyboardTracking(False)

        self.entry_N = QSpinBox()
        self.entry_N.setMinimum(1)
        self.entry_N.setMaximum(5000)
        self.entry_N.setSingleStep(1)
        self.entry_N.setValue(1000)
        self.entry_N.setKeyboardTracking(False)

        self.reading_x = QLabel()
        self.reading_x.setNum(0)
        self.reading_x.setFrameStyle(QFrame.Panel | QFrame.Sunken)

        self.reading_y = QLabel()
        self.reading_y.setNum(0)
        self.reading_y.setFrameStyle(QFrame.Panel | QFrame.Sunken)

        # layout
        grid_line0 = QGridLayout()
        grid_line0.addWidget(QLabel("x offset"), 0, 0)
        grid_line0.addWidget(self.entry_x_offset, 0, 1)
        grid_line0.addWidget(QLabel("x scaling"), 0, 2)
        grid_line0.addWidget(self.entry_x_scaling, 0, 3)
        grid_line0.addWidget(QLabel("y offset"), 0, 4)
        grid_line0.addWidget(self.entry_y_offset, 0, 5)
        grid_line0.addWidget(QLabel("y scaling"), 0, 6)
        grid_line0.addWidget(self.entry_y_scaling, 0, 7)

        grid_line1 = QGridLayout()
        grid_line1.addWidget(QLabel("d from x"), 0, 0)
        grid_line1.addWidget(self.reading_x, 0, 1)
        grid_line1.addWidget(QLabel("d from y"), 0, 2)
        grid_line1.addWidget(self.reading_y, 0, 3)
        grid_line1.addWidget(QLabel("N average"), 0, 4)
        grid_line1.addWidget(self.entry_N_average, 0, 5)
        grid_line1.addWidget(QLabel("N"), 0, 6)
        grid_line1.addWidget(self.entry_N, 0, 7)

        self.grid = QGridLayout()
        self.grid.addLayout(grid_line0, 0, 0)
        self.grid.addLayout(grid_line1, 1, 0)
        self.setLayout(self.grid)

        # connections
        self.entry_x_offset.valueChanged.connect(self.update_settings)
        self.entry_y_offset.valueChanged.connect(self.update_settings)
        self.entry_x_scaling.valueChanged.connect(self.update_settings)
        self.entry_y_scaling.valueChanged.connect(self.update_settings)
        self.entry_N_average.valueChanged.connect(self.update_settings)
        self.entry_N.valueChanged.connect(self.update_settings)
        self.entry_N.valueChanged.connect(self.update_waveformDisplay_N)

    def update_settings(self, new_value):
        print("update settings")
        self.displacementMeasurementController.update_settings(
            self.entry_x_offset.value(),
            self.entry_y_offset.value(),
            self.entry_x_scaling.value(),
            self.entry_y_scaling.value(),
            self.entry_N_average.value(),
            self.entry_N.value(),
        )

    def update_waveformDisplay_N(self, N):
        self.waveformDisplay.update_N(N)

    def display_readings(self, readings):
        self.reading_x.setText("{:.2f}".format(readings[0]))
        self.reading_y.setText("{:.2f}".format(readings[1]))


class LaserAutofocusControlWidget(QFrame):
    def __init__(self, laserAutofocusController, liveController: LiveController, main=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.laserAutofocusController = laserAutofocusController
        self.liveController: LiveController = liveController
        self.add_components()
        self.update_init_state()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def add_components(self):
        self.btn_set_reference = QPushButton(" Set Reference ")
        self.btn_set_reference.setCheckable(False)
        self.btn_set_reference.setChecked(False)
        self.btn_set_reference.setDefault(False)
        if not self.laserAutofocusController.is_initialized:
            self.btn_set_reference.setEnabled(False)

        self.label_displacement = QLabel()
        self.label_displacement.setFrameStyle(QFrame.Panel | QFrame.Sunken)

        self.btn_measure_displacement = QPushButton("Measure Displacement")
        self.btn_measure_displacement.setCheckable(False)
        self.btn_measure_displacement.setChecked(False)
        self.btn_measure_displacement.setDefault(False)
        if not self.laserAutofocusController.is_initialized:
            self.btn_measure_displacement.setEnabled(False)

        self.entry_target = QDoubleSpinBox()
        self.entry_target.setMinimum(-100)
        self.entry_target.setMaximum(100)
        self.entry_target.setSingleStep(0.01)
        self.entry_target.setDecimals(2)
        self.entry_target.setValue(0)
        self.entry_target.setKeyboardTracking(False)

        self.btn_move_to_target = QPushButton("Move to Target")
        self.btn_move_to_target.setCheckable(False)
        self.btn_move_to_target.setChecked(False)
        self.btn_move_to_target.setDefault(False)
        if not self.laserAutofocusController.is_initialized:
            self.btn_move_to_target.setEnabled(False)

        self.grid = QGridLayout()

        self.grid.addWidget(self.btn_set_reference, 0, 0, 1, 4)

        self.grid.addWidget(QLabel("Displacement (um)"), 1, 0)
        self.grid.addWidget(self.label_displacement, 1, 1)
        self.grid.addWidget(self.btn_measure_displacement, 1, 2, 1, 2)

        self.grid.addWidget(QLabel("Target (um)"), 2, 0)
        self.grid.addWidget(self.entry_target, 2, 1)
        self.grid.addWidget(self.btn_move_to_target, 2, 2, 1, 2)
        self.setLayout(self.grid)

        # make connections
        self.btn_set_reference.clicked.connect(self.on_set_reference_clicked)
        self.btn_measure_displacement.clicked.connect(self.on_measure_displacement_clicked)
        self.btn_move_to_target.clicked.connect(self.move_to_target)
        self.laserAutofocusController.signal_displacement_um.connect(self.label_displacement.setNum)

    def update_init_state(self):
        self.btn_set_reference.setEnabled(self.laserAutofocusController.is_initialized)
        self.btn_measure_displacement.setEnabled(self.laserAutofocusController.laser_af_properties.has_reference)
        self.btn_move_to_target.setEnabled(self.laserAutofocusController.laser_af_properties.has_reference)

    def move_to_target(self):
        was_live = self.liveController.is_live
        if was_live:
            self.liveController.stop_live()
        self.laserAutofocusController.move_to_target(self.entry_target.value())
        if was_live:
            self.liveController.start_live()

    def on_set_reference_clicked(self):
        """Handle set reference button click"""
        was_live = self.liveController.is_live
        if was_live:
            self.liveController.stop_live()
        success = self.laserAutofocusController.set_reference()
        if success:
            self.btn_measure_displacement.setEnabled(True)
            self.btn_move_to_target.setEnabled(True)
        if was_live:
            self.liveController.start_live()

    def on_measure_displacement_clicked(self):
        was_live = self.liveController.is_live
        if was_live:
            self.liveController.stop_live()
        result = self.laserAutofocusController.measure_displacement()
        if math.isnan(result):
            QMessageBox.warning(
                self,
                "Measurement Failed",
                "Could not measure displacement. Please ensure the reference position is set.",
            )
        if was_live:
            self.liveController.start_live()


class WellplateFormatWidget(QWidget):

    signalWellplateSettings = Signal(str, float, float, int, int, float, float, int, int, int)

    def __init__(self, stage: AbstractStage, navigationViewer, streamHandler, liveController):
        super().__init__()
        self.stage = stage
        self.navigationViewer = navigationViewer
        self.streamHandler = streamHandler
        self.liveController = liveController
        self.wellplate_format = WELLPLATE_FORMAT
        self.csv_path = SAMPLE_FORMATS_CSV_PATH  # 'sample_formats.csv'
        self.initUI()

    def initUI(self):
        layout = QHBoxLayout(self)
        self.label = QLabel("Sample Format", self)
        self.comboBox = QComboBox(self)
        self.populate_combo_box()
        self.comboBox.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(self.label)
        layout.addWidget(self.comboBox)
        self.comboBox.currentIndexChanged.connect(self.wellplateChanged)
        index = self.comboBox.findData(self.wellplate_format)
        if index >= 0:
            self.comboBox.setCurrentIndex(index)

    def populate_combo_box(self):
        self.comboBox.clear()
        for format_, settings in WELLPLATE_FORMAT_SETTINGS.items():
            self.comboBox.addItem(format_, format_)

        # Add custom item and set its font to italic
        self.comboBox.addItem("calibrate format...", "custom")
        index = self.comboBox.count() - 1  # Get the index of the last item
        font = QFont()
        font.setItalic(True)
        self.comboBox.setItemData(index, font, Qt.FontRole)

    def wellplateChanged(self, index):
        self.wellplate_format = self.comboBox.itemData(index)
        if self.wellplate_format == "custom":
            calibration_dialog = WellplateCalibration(
                self, self.stage, self.navigationViewer, self.streamHandler, self.liveController
            )
            result = calibration_dialog.exec_()
            if result == QDialog.Rejected:
                # If the dialog was closed without adding a new format, revert to the previous selection
                prev_index = self.comboBox.findData(self.wellplate_format)
                self.comboBox.setCurrentIndex(prev_index)
        else:
            self.setWellplateSettings(self.wellplate_format)

    def setWellplateSettings(self, wellplate_format):
        if wellplate_format in WELLPLATE_FORMAT_SETTINGS:
            settings = WELLPLATE_FORMAT_SETTINGS[wellplate_format]
        elif wellplate_format == "glass slide":
            self.signalWellplateSettings.emit("glass slide", 0, 0, 0, 0, 0, 0, 0, 1, 1)
            return
        else:
            print(f"Wellplate format {wellplate_format} not recognized")
            return

        self.signalWellplateSettings.emit(
            wellplate_format,
            settings["a1_x_mm"],
            settings["a1_y_mm"],
            settings["a1_x_pixel"],
            settings["a1_y_pixel"],
            settings["well_size_mm"],
            settings["well_spacing_mm"],
            settings["number_of_skip"],
            settings["rows"],
            settings["cols"],
        )

    def getWellplateSettings(self, wellplate_format):
        if wellplate_format in WELLPLATE_FORMAT_SETTINGS:
            settings = WELLPLATE_FORMAT_SETTINGS[wellplate_format]
        elif wellplate_format == "glass slide":
            settings = {
                "format": "glass slide",
                "a1_x_mm": 0,
                "a1_y_mm": 0,
                "a1_x_pixel": 0,
                "a1_y_pixel": 0,
                "well_size_mm": 0,
                "well_spacing_mm": 0,
                "number_of_skip": 0,
                "rows": 1,
                "cols": 1,
            }
        else:
            return None
        return settings

    def add_custom_format(self, name, settings):
        WELLPLATE_FORMAT_SETTINGS[name] = settings
        self.populate_combo_box()
        index = self.comboBox.findData(name)
        if index >= 0:
            self.comboBox.setCurrentIndex(index)
        self.wellplateChanged(index)

    def save_formats_to_csv(self):
        cache_path = os.path.join("cache", self.csv_path)
        os.makedirs("cache", exist_ok=True)

        fieldnames = [
            "format",
            "a1_x_mm",
            "a1_y_mm",
            "a1_x_pixel",
            "a1_y_pixel",
            "well_size_mm",
            "well_spacing_mm",
            "number_of_skip",
            "rows",
            "cols",
        ]
        with open(cache_path, "w", newline="") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for format_, settings in WELLPLATE_FORMAT_SETTINGS.items():
                writer.writerow({**{"format": format_}, **settings})

    @staticmethod
    def parse_csv_row(row):
        return {
            "a1_x_mm": float(row["a1_x_mm"]),
            "a1_y_mm": float(row["a1_y_mm"]),
            "a1_x_pixel": int(row["a1_x_pixel"]),
            "a1_y_pixel": int(row["a1_y_pixel"]),
            "well_size_mm": float(row["well_size_mm"]),
            "well_spacing_mm": float(row["well_spacing_mm"]),
            "number_of_skip": int(row["number_of_skip"]),
            "rows": int(row["rows"]),
            "cols": int(row["cols"]),
        }


class WellplateCalibration(QDialog):

    def __init__(self, wellplateFormatWidget, stage: AbstractStage, navigationViewer, streamHandler, liveController):
        super().__init__()
        self.setWindowTitle("Well Plate Calibration")
        self.wellplateFormatWidget = wellplateFormatWidget
        self.stage = stage
        self.navigationViewer = navigationViewer
        self.streamHandler = streamHandler
        self.liveController: LiveController = liveController
        self.was_live = self.liveController.is_live
        self.corners = [None, None, None]
        self.show_virtual_joystick = True  # FLAG
        self.initUI()
        # Initially allow click-to-move and hide the joystick controls
        self.clickToMoveCheckbox.setChecked(True)
        self.toggleVirtualJoystick(False)

    def initUI(self):
        layout = QHBoxLayout(self)  # Change to QHBoxLayout to have two columns

        # Left column for existing controls
        left_layout = QVBoxLayout()

        # Add radio buttons for selecting mode
        self.mode_group = QButtonGroup(self)
        self.new_format_radio = QRadioButton("Add New Format")
        self.calibrate_format_radio = QRadioButton("Calibrate Existing Format")
        self.mode_group.addButton(self.new_format_radio)
        self.mode_group.addButton(self.calibrate_format_radio)
        self.new_format_radio.setChecked(True)

        left_layout.addWidget(self.new_format_radio)
        left_layout.addWidget(self.calibrate_format_radio)

        # Existing format selection (initially hidden)
        self.existing_format_combo = QComboBox(self)
        self.populate_existing_formats()
        self.existing_format_combo.hide()
        left_layout.addWidget(self.existing_format_combo)

        # Connect radio buttons to toggle visibility
        self.new_format_radio.toggled.connect(self.toggle_input_mode)
        self.calibrate_format_radio.toggled.connect(self.toggle_input_mode)

        self.form_layout = QFormLayout()

        self.nameInput = QLineEdit(self)
        self.nameInput.setPlaceholderText("custom well plate")
        self.form_layout.addRow("Sample Name:", self.nameInput)

        self.rowsInput = QSpinBox(self)
        self.rowsInput.setKeyboardTracking(False)
        self.rowsInput.setRange(1, 100)
        self.rowsInput.setValue(8)
        self.form_layout.addRow("# Rows:", self.rowsInput)

        self.colsInput = QSpinBox(self)
        self.colsInput.setKeyboardTracking(False)
        self.colsInput.setRange(1, 100)
        self.colsInput.setValue(12)
        self.form_layout.addRow("# Columns:", self.colsInput)

        # Add new inputs for plate dimensions
        self.plateWidthInput = QDoubleSpinBox(self)
        self.plateWidthInput.setKeyboardTracking(False)
        self.plateWidthInput.setRange(10, 500)  # Adjust range as needed
        self.plateWidthInput.setValue(127.76)  # Default value for a standard 96-well plate
        self.plateWidthInput.setSuffix(" mm")
        self.form_layout.addRow("Plate Width:", self.plateWidthInput)

        self.plateHeightInput = QDoubleSpinBox(self)
        self.plateHeightInput.setKeyboardTracking(False)
        self.plateHeightInput.setRange(10, 500)  # Adjust range as needed
        self.plateHeightInput.setValue(85.48)  # Default value for a standard 96-well plate
        self.plateHeightInput.setSuffix(" mm")
        self.form_layout.addRow("Plate Height:", self.plateHeightInput)

        self.wellSpacingInput = QDoubleSpinBox(self)
        self.wellSpacingInput.setKeyboardTracking(False)
        self.wellSpacingInput.setRange(0.1, 100)
        self.wellSpacingInput.setValue(9)
        self.wellSpacingInput.setSingleStep(0.1)
        self.wellSpacingInput.setDecimals(2)
        self.wellSpacingInput.setSuffix(" mm")
        self.form_layout.addRow("Well Spacing:", self.wellSpacingInput)

        left_layout.addLayout(self.form_layout)

        points_layout = QGridLayout()
        self.cornerLabels = []
        self.setPointButtons = []
        navigate_label = QLabel("Navigate to and Select\n3 Points on the Edge of Well A1")
        navigate_label.setAlignment(Qt.AlignCenter)
        # navigate_label.setStyleSheet("font-weight: bold;")
        points_layout.addWidget(navigate_label, 0, 0, 1, 2)
        for i in range(1, 4):
            label = QLabel(f"Point {i}: N/A")
            button = QPushButton("Set Point")
            button.setFixedWidth(button.sizeHint().width())
            button.clicked.connect(lambda checked, index=i - 1: self.setCorner(index))
            points_layout.addWidget(label, i, 0)
            points_layout.addWidget(button, i, 1)
            self.cornerLabels.append(label)
            self.setPointButtons.append(button)

        points_layout.setColumnStretch(0, 1)
        left_layout.addLayout(points_layout)

        # Add 'Click to Move' checkbox
        self.clickToMoveCheckbox = QCheckBox("Click to Move")
        self.clickToMoveCheckbox.stateChanged.connect(self.toggleClickToMove)
        left_layout.addWidget(self.clickToMoveCheckbox)

        # Add 'Show Virtual Joystick' checkbox
        self.showJoystickCheckbox = QCheckBox("Virtual Joystick")
        self.showJoystickCheckbox.stateChanged.connect(self.toggleVirtualJoystick)
        left_layout.addWidget(self.showJoystickCheckbox)

        self.calibrateButton = QPushButton("Calibrate")
        self.calibrateButton.clicked.connect(self.calibrate)
        self.calibrateButton.setEnabled(False)
        left_layout.addWidget(self.calibrateButton)

        # Add left column to main layout
        layout.addLayout(left_layout)

        self.live_viewer = CalibrationLiveViewer()
        self.streamHandler.image_to_display.connect(self.live_viewer.display_image)

        if not self.was_live:
            self.liveController.start_live()

        # when the dialog closes i want to # self.liveController.stop_live() if live was stopped before. . . if it was on before, leave it on
        layout.addWidget(self.live_viewer)

        # Right column for joystick and sensitivity controls
        self.right_layout = QVBoxLayout()
        self.right_layout.addStretch(1)

        self.joystick = Joystick(self)
        self.joystick.joystickMoved.connect(self.moveStage)
        self.right_layout.addWidget(self.joystick, 0, Qt.AlignTop | Qt.AlignHCenter)

        self.right_layout.addStretch(1)

        # Create a container widget for sensitivity label and slider
        sensitivity_layout = QVBoxLayout()

        sensitivityLabel = QLabel("Joystick Sensitivity")
        sensitivityLabel.setAlignment(Qt.AlignCenter)
        sensitivity_layout.addWidget(sensitivityLabel)

        self.sensitivitySlider = QSlider(Qt.Horizontal)
        self.sensitivitySlider.setMinimum(1)
        self.sensitivitySlider.setMaximum(100)
        self.sensitivitySlider.setValue(50)
        self.sensitivitySlider.setTickPosition(QSlider.TicksBelow)
        self.sensitivitySlider.setTickInterval(10)

        label_width = sensitivityLabel.sizeHint().width()
        self.sensitivitySlider.setFixedWidth(label_width)

        sensitivity_layout.addWidget(self.sensitivitySlider, 0, Qt.AlignHCenter)

        self.right_layout.addLayout(sensitivity_layout)

        layout.addLayout(self.right_layout)

        if not self.was_live:
            self.liveController.start_live()

    def toggleVirtualJoystick(self, state):
        if state:
            self.joystick.show()
            self.sensitivitySlider.show()
            self.right_layout.itemAt(self.right_layout.indexOf(self.joystick)).widget().show()
            self.right_layout.itemAt(self.right_layout.count() - 1).layout().itemAt(
                0
            ).widget().show()  # Show sensitivity label
            self.right_layout.itemAt(self.right_layout.count() - 1).layout().itemAt(
                1
            ).widget().show()  # Show sensitivity slider
        else:
            self.joystick.hide()
            self.sensitivitySlider.hide()
            self.right_layout.itemAt(self.right_layout.indexOf(self.joystick)).widget().hide()
            self.right_layout.itemAt(self.right_layout.count() - 1).layout().itemAt(
                0
            ).widget().hide()  # Hide sensitivity label
            self.right_layout.itemAt(self.right_layout.count() - 1).layout().itemAt(
                1
            ).widget().hide()  # Hide sensitivity slider

    def moveStage(self, x, y):
        sensitivity = self.sensitivitySlider.value() / 50.0  # Normalize to 0-2 range
        max_speed = 0.1 * sensitivity
        exponent = 2

        dx = math.copysign(max_speed * abs(x) ** exponent, x)
        dy = math.copysign(max_speed * abs(y) ** exponent, y)

        self.stage.move_x(dx)
        self.stage.move_y(dy)

    def toggleClickToMove(self, state):
        if state == Qt.Checked:
            self.live_viewer.signal_calibration_viewer_click.connect(self.viewerClicked)
        else:
            self.live_viewer.signal_calibration_viewer_click.disconnect(self.viewerClicked)

    def viewerClicked(self, x, y, width, height):
        pixel_size_um = (
            self.navigationViewer.objectiveStore.get_pixel_size_factor()
            * self.liveController.microscope.camera.get_pixel_size_binned_um()
        )

        pixel_sign_x = 1
        pixel_sign_y = 1 if INVERTED_OBJECTIVE else -1

        delta_x = pixel_sign_x * pixel_size_um * x / 1000.0
        delta_y = pixel_sign_y * pixel_size_um * y / 1000.0

        self.stage.move_x(delta_x)
        self.stage.move_y(delta_y)

    def setCorner(self, index):
        if self.corners[index] is None:
            pos = self.stage.get_pos()
            x = pos.x_mm
            y = pos.y_mm

            # Check if the new point is different from existing points
            if any(corner is not None and np.allclose([x, y], corner) for corner in self.corners):
                QMessageBox.warning(
                    self,
                    "Duplicate Point",
                    "This point is too close to an existing point. Please choose a different location.",
                )
                return

            self.corners[index] = (x, y)
            self.cornerLabels[index].setText(f"Point {index+1}: ({x:.2f}, {y:.2f})")
            self.setPointButtons[index].setText("Clear Point")
        else:
            self.corners[index] = None
            self.cornerLabels[index].setText(f"Point {index+1}: Not set")
            self.setPointButtons[index].setText("Set Point")

        self.calibrateButton.setEnabled(all(corner is not None for corner in self.corners))

    def populate_existing_formats(self):
        self.existing_format_combo.clear()
        for format_ in WELLPLATE_FORMAT_SETTINGS:
            self.existing_format_combo.addItem(f"{format_} well plate", format_)

    def toggle_input_mode(self):
        if self.new_format_radio.isChecked():
            self.existing_format_combo.hide()
            for i in range(self.form_layout.rowCount()):
                self.form_layout.itemAt(i, QFormLayout.FieldRole).widget().show()
                self.form_layout.itemAt(i, QFormLayout.LabelRole).widget().show()
        else:
            self.existing_format_combo.show()
            for i in range(self.form_layout.rowCount()):
                self.form_layout.itemAt(i, QFormLayout.FieldRole).widget().hide()
                self.form_layout.itemAt(i, QFormLayout.LabelRole).widget().hide()

    def calibrate(self):
        try:
            if self.new_format_radio.isChecked():
                if not self.nameInput.text() or not all(self.corners):
                    QMessageBox.warning(
                        self,
                        "Incomplete Information",
                        "Please fill in all fields and set 3 corner points before calibrating.",
                    )
                    return

                name = self.nameInput.text()
                rows = self.rowsInput.value()
                cols = self.colsInput.value()
                well_spacing_mm = self.wellSpacingInput.value()
                plate_width_mm = self.plateWidthInput.value()
                plate_height_mm = self.plateHeightInput.value()

                center, radius = self.calculate_circle(self.corners)
                well_size_mm = radius * 2
                a1_x_mm, a1_y_mm = center
                scale = 1 / 0.084665
                a1_x_pixel = round(a1_x_mm * scale)
                a1_y_pixel = round(a1_y_mm * scale)

                new_format = {
                    "a1_x_mm": a1_x_mm,
                    "a1_y_mm": a1_y_mm,
                    "a1_x_pixel": a1_x_pixel,
                    "a1_y_pixel": a1_y_pixel,
                    "well_size_mm": well_size_mm,
                    "well_spacing_mm": well_spacing_mm,
                    "number_of_skip": 0,
                    "rows": rows,
                    "cols": cols,
                }

                self.wellplateFormatWidget.add_custom_format(name, new_format)
                self.wellplateFormatWidget.save_formats_to_csv()
                self.create_wellplate_image(name, new_format, plate_width_mm, plate_height_mm)
                self.wellplateFormatWidget.setWellplateSettings(name)
                success_message = f"New format '{name}' has been successfully created and calibrated."

            else:
                selected_format = self.existing_format_combo.currentData()
                if not all(self.corners):
                    QMessageBox.warning(
                        self, "Incomplete Information", "Please set 3 corner points before calibrating."
                    )
                    return

                center, radius = self.calculate_circle(self.corners)
                well_size_mm = radius * 2
                a1_x_mm, a1_y_mm = center

                # Get the existing format settings
                existing_settings = WELLPLATE_FORMAT_SETTINGS[selected_format]

                print(f"Updating existing format {selected_format} well plate")
                print(
                    f"OLD: 'a1_x_mm': {existing_settings['a1_x_mm']}, 'a1_y_mm': {existing_settings['a1_y_mm']}, 'well_size_mm': {existing_settings['well_size_mm']}"
                )
                print(f"NEW: 'a1_x_mm': {a1_x_mm}, 'a1_y_mm': {a1_y_mm}, 'well_size_mm': {well_size_mm}")

                updated_settings = {
                    "a1_x_mm": a1_x_mm,
                    "a1_y_mm": a1_y_mm,
                    "well_size_mm": well_size_mm,
                }

                WELLPLATE_FORMAT_SETTINGS[selected_format].update(updated_settings)

                self.wellplateFormatWidget.save_formats_to_csv()
                self.wellplateFormatWidget.setWellplateSettings(selected_format)
                success_message = f"Format '{selected_format} well plate' has been successfully recalibrated."

            # Update the WellplateFormatWidget's combo box to reflect the newly calibrated format
            self.wellplateFormatWidget.populate_combo_box()
            index = self.wellplateFormatWidget.comboBox.findData(
                selected_format if self.calibrate_format_radio.isChecked() else name
            )
            if index >= 0:
                self.wellplateFormatWidget.comboBox.setCurrentIndex(index)

            # Display success message
            QMessageBox.information(self, "Calibration Successful", success_message)
            self.accept()

        except Exception as e:
            QMessageBox.critical(self, "Calibration Error", f"An error occurred during calibration: {str(e)}")

    def create_wellplate_image(self, name, format_data, plate_width_mm, plate_height_mm):

        scale = 1 / 0.084665

        def mm_to_px(mm):
            return round(mm * scale)

        width = mm_to_px(plate_width_mm)
        height = mm_to_px(plate_height_mm)
        image = Image.new("RGB", (width, height), color="white")
        draw = ImageDraw.Draw(image)

        rows, cols = format_data["rows"], format_data["cols"]
        well_spacing_mm = format_data["well_spacing_mm"]
        well_size_mm = format_data["well_size_mm"]
        a1_x_mm, a1_y_mm = format_data["a1_x_mm"], format_data["a1_y_mm"]

        def draw_left_slanted_rectangle(draw, xy, slant, width=4, outline="black", fill=None):
            x1, y1, x2, y2 = xy

            # Define the polygon points
            points = [
                (x1 + slant, y1),  # Top-left after slant
                (x2, y1),  # Top-right
                (x2, y2),  # Bottom-right
                (x1 + slant, y2),  # Bottom-left after slant
                (x1, y2 - slant),  # Bottom of left slant
                (x1, y1 + slant),  # Top of left slant
            ]

            # Draw the filled polygon with outline
            draw.polygon(points, fill=fill, outline=outline, width=width)

        # Draw the outer rectangle with rounded corners
        corner_radius = 20
        draw.rounded_rectangle(
            [0, 0, width - 1, height - 1], radius=corner_radius, outline="black", width=4, fill="grey"
        )

        # Draw the inner rectangle with left slanted corners
        margin = 20
        slant = 40
        draw_left_slanted_rectangle(
            draw, [margin, margin, width - margin, height - margin], slant, width=4, outline="black", fill="lightgrey"
        )

        # Function to draw a circle
        def draw_circle(x, y, diameter):
            radius = diameter / 2
            draw.ellipse([x - radius, y - radius, x + radius, y + radius], outline="black", width=4, fill="white")

        # Draw the wells
        for row in range(rows):
            for col in range(cols):
                x = mm_to_px(a1_x_mm + col * well_spacing_mm)
                y = mm_to_px(a1_y_mm + row * well_spacing_mm)
                draw_circle(x, y, mm_to_px(well_size_mm))

        # Load a default font
        font_size = 30
        font = ImageFont.load_default().font_variant(size=font_size)

        # Add column labels
        for col in range(cols):
            label = str(col + 1)
            x = mm_to_px(a1_x_mm + col * well_spacing_mm)
            y = mm_to_px((a1_y_mm - well_size_mm / 2) / 2)
            bbox = font.getbbox(label)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            draw.text((x - text_width / 2, y), label, fill="black", font=font)

        # Add row labels
        for row in range(rows):
            label = chr(65 + row) if row < 26 else chr(65 + row // 26 - 1) + chr(65 + row % 26)
            x = mm_to_px((a1_x_mm - well_size_mm / 2) / 2)
            y = mm_to_px(a1_y_mm + row * well_spacing_mm)
            bbox = font.getbbox(label)
            text_height = bbox[3] - bbox[1]
            text_width = bbox[2] - bbox[0]
            draw.text((x + 20 - text_width / 2, y - text_height + 1), label, fill="black", font=font)

        image_path = os.path.join("images", f'{name.replace(" ", "_")}.png')
        image.save(image_path)
        print(f"Wellplate image saved as {image_path}")
        return image_path

    @staticmethod
    def calculate_circle(points):
        # Convert points to numpy array
        points = np.array(points)

        # Calculate the center and radius of the circle
        A = np.array([points[1] - points[0], points[2] - points[0]])
        b = np.sum(A * (points[1:3] + points[0]) / 2, axis=1)
        center = np.linalg.solve(A, b)

        # Calculate the radius
        radius = np.mean(np.linalg.norm(points - center, axis=1))

        return center, radius

    def closeEvent(self, event):
        # Stop live view if it wasn't initially on
        if not self.was_live:
            self.liveController.stop_live()
        super().closeEvent(event)

    def accept(self):
        # Stop live view if it wasn't initially on
        if not self.was_live:
            self.liveController.stop_live()
        super().accept()

    def reject(self):
        # This method is called when the dialog is closed without accepting
        if not self.was_live:
            self.liveController.stop_live()
        sample = self.navigationViewer.sample

        # Convert sample string to format int
        if "glass slide" in sample:
            sample_format = "glass slide"
        else:
            try:
                sample_format = int(sample.split()[0])
            except (ValueError, IndexError):
                print(f"Unable to parse sample format from '{sample}'. Defaulting to 0.")
                sample_format = "glass slide"

        # Set dropdown to the current sample format
        index = self.wellplateFormatWidget.comboBox.findData(sample_format)
        if index >= 0:
            self.wellplateFormatWidget.comboBox.setCurrentIndex(index)

        # Update wellplate settings
        self.wellplateFormatWidget.setWellplateSettings(sample_format)

        super().reject()


class CalibrationLiveViewer(QWidget):

    signal_calibration_viewer_click = Signal(int, int, int, int)
    signal_mouse_moved = Signal(int, int)

    def __init__(self):
        super().__init__()
        self.initial_zoom_set = False
        self.initUI()

    def initUI(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.view = pg.GraphicsLayoutWidget()
        self.viewbox = self.view.addViewBox()
        self.viewbox.setAspectLocked(True)
        self.viewbox.invertY(True)

        self.viewbox.setMouseEnabled(x=False, y=False)  # Disable panning
        self.viewbox.setMenuEnabled(False)

        # Set appropriate panning limits based on the acquisition image or plate size
        xmax = int(CAMERA_CONFIG.CROP_WIDTH_UNBINNED)
        ymax = int(CAMERA_CONFIG.CROP_HEIGHT_UNBINNED)
        self.viewbox.setLimits(xMin=0, xMax=xmax, yMin=0, yMax=ymax)

        self.img_item = pg.ImageItem()
        self.viewbox.addItem(self.img_item)

        # Add fixed crosshair
        pen = QPen(QColor(255, 0, 0))  # Red color
        pen.setWidth(4)

        self.crosshair_h = pg.InfiniteLine(angle=0, movable=False, pen=pen)
        self.crosshair_v = pg.InfiniteLine(angle=90, movable=False, pen=pen)
        self.viewbox.addItem(self.crosshair_h)
        self.viewbox.addItem(self.crosshair_v)

        layout.addWidget(self.view)

        # Connect double-click event
        self.view.scene().sigMouseClicked.connect(self.onMouseClicked)

        # Set fixed size for the viewer
        self.setFixedSize(500, 500)

    def setCrosshairPosition(self):
        center = self.viewbox.viewRect().center()
        self.crosshair_h.setPos(center.y())
        self.crosshair_v.setPos(center.x())

    def display_image(self, image):
        # Step 1: Update the image
        self.img_item.setImage(image)

        # Step 2: Get the image dimensions
        image_width = image.shape[1]
        image_height = image.shape[0]

        # Step 3: Calculate the center of the image
        image_center_x = image_width / 2
        image_center_y = image_height / 2

        # Step 4: Calculate the current view range
        current_view_range = self.viewbox.viewRect()

        # Step 5: If it's the first image or initial zoom hasn't been set, center the image
        if not self.initial_zoom_set:
            self.viewbox.setRange(xRange=(0, image_width), yRange=(0, image_height), padding=0)
            self.initial_zoom_set = True  # Mark initial zoom as set

        # Step 6: Always center the view around the image center (for seamless transitions)
        else:
            self.viewbox.setRange(
                xRange=(
                    image_center_x - current_view_range.width() / 2,
                    image_center_x + current_view_range.width() / 2,
                ),
                yRange=(
                    image_center_y - current_view_range.height() / 2,
                    image_center_y + current_view_range.height() / 2,
                ),
                padding=0,
            )

        # Step 7: Ensure the crosshair is updated
        self.setCrosshairPosition()

    # def mouseMoveEvent(self, event):
    #     self.signal_mouse_moved.emit(event.x(), event.y())

    def onMouseClicked(self, event):
        # Map the scene position to view position
        if event.double():  # double click to move
            pos = event.pos()
            scene_pos = self.viewbox.mapSceneToView(pos)

            # Get the x, y coordinates
            x, y = int(scene_pos.x()), int(scene_pos.y())
            # Ensure the coordinates are within the image boundaries
            image_shape = self.img_item.image.shape
            if 0 <= x < image_shape[1] and 0 <= y < image_shape[0]:
                # Adjust the coordinates to be relative to the center of the image
                x_centered = x - image_shape[1] // 2
                y_centered = y - image_shape[0] // 2
                # Emit the signal with the clicked coordinates and image size
                self.signal_calibration_viewer_click.emit(x_centered, y_centered, image_shape[1], image_shape[0])
            else:
                print("click was outside the image bounds.")
        else:
            print("single click only detected")

    def wheelEvent(self, event):
        if event.angleDelta().y() > 0:
            scale_factor = 0.9
        else:
            scale_factor = 1.1

        # Get the center of the viewbox
        center = self.viewbox.viewRect().center()

        # Scale the view
        self.viewbox.scaleBy((scale_factor, scale_factor), center)

        # Update crosshair position after scaling
        self.setCrosshairPosition()

        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setCrosshairPosition()


class Joystick(QWidget):
    joystickMoved = Signal(float, float)  # Emits x and y values between -1 and 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(200, 200)
        self.inner_radius = 40
        self.max_distance = self.width() // 2 - self.inner_radius
        self.outer_radius = int(self.width() * 3 / 8)
        self.current_x = 0
        self.current_y = 0
        self.is_pressed = False
        self.timer = QTimer(self)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Calculate the painting area
        paint_rect = QRectF(0, 0, 200, 200)

        # Draw outer circle
        painter.setBrush(QColor(230, 230, 230))  # Light grey fill
        painter.setPen(QPen(QColor(100, 100, 100), 2))  # Dark grey outline
        painter.drawEllipse(paint_rect.center(), self.outer_radius, self.outer_radius)

        # Draw inner circle (joystick position)
        painter.setBrush(QColor(100, 100, 100))
        painter.setPen(Qt.NoPen)
        joystick_x = paint_rect.center().x() + self.current_x * self.max_distance
        joystick_y = paint_rect.center().y() + self.current_y * self.max_distance
        painter.drawEllipse(QPointF(joystick_x, joystick_y), self.inner_radius, self.inner_radius)

    def mousePressEvent(self, event):
        if QRectF(0, 0, 200, 200).contains(event.pos()):
            self.is_pressed = True
            self.updateJoystickPosition(event.pos())
            self.timer.timeout.connect(self.update_position)
            self.timer.start(10)

    def mouseMoveEvent(self, event):
        if self.is_pressed and QRectF(0, 0, 200, 200).contains(event.pos()):
            self.updateJoystickPosition(event.pos())

    def mouseReleaseEvent(self, event):
        self.is_pressed = False
        self.updateJoystickPosition(QPointF(100, 100))  # Center position
        self.timer.timeout.disconnect(self.update_position)
        self.joystickMoved.emit(0, 0)

    def update_position(self):
        if self.is_pressed:
            self.joystickMoved.emit(self.current_x, -self.current_y)

    def updateJoystickPosition(self, pos):
        center = QPointF(100, 100)
        dx = pos.x() - center.x()
        dy = pos.y() - center.y()
        distance = math.sqrt(dx**2 + dy**2)

        if distance > self.max_distance:
            dx = dx * self.max_distance / distance
            dy = dy * self.max_distance / distance

        self.current_x = dx / self.max_distance
        self.current_y = dy / self.max_distance
        self.update()


class Well1536SelectionWidget(QWidget):

    signal_wellSelected = Signal(bool)
    signal_wellSelectedPos = Signal(float, float)

    def __init__(self, wellplateFormatWidget):
        super().__init__()
        self.wellplateFormatWidget = wellplateFormatWidget
        self.format = "1536 well plate"
        self.selected_cells = {}  # Dictionary to keep track of selected cells and their colors
        self.current_cell = None  # To track the current (green) cell

        # defaults
        self.rows = 32
        self.columns = 48
        self.spacing_mm = 2.25
        self.number_of_skip = 0
        self.well_size_mm = 1.5
        self.a1_x_mm = 11.0  # measured stage position - to update
        self.a1_y_mm = 7.86  # measured stage position - to update
        self.a1_x_pixel = 144  # coordinate on the png - to update
        self.a1_y_pixel = 108  # coordinate on the png - to update

        if self.wellplateFormatWidget is not None:
            s = self.wellplateFormatWidget.getWellplateSettings(self.format)
            self.rows = s["rows"]
            self.columns = s["cols"]
            self.spacing_mm = s["well_spacing_mm"]
            self.number_of_skip = s["number_of_skip"]
            self.a1_x_mm = s["a1_x_mm"]
            self.a1_y_mm = s["a1_y_mm"]
            self.a1_x_pixel = s["a1_x_pixel"]
            self.a1_y_pixel = s["a1_y_pixel"]
            self.well_size_mm = s["well_size_mm"]

        self.initUI()

    def initUI(self):
        self.setWindowTitle("1536 Well Plate")
        self.setGeometry(100, 100, 750, 400)  # Increased width to accommodate controls

        self.a = 11
        image_width = 48 * self.a
        image_height = 32 * self.a

        self.image = QPixmap(image_width, image_height)
        self.image.fill(QColor("white"))
        self.label = QLabel()
        self.label.setPixmap(self.image)
        self.label.setFixedSize(image_width, image_height)
        self.label.setAlignment(Qt.AlignCenter)

        # Mouse interaction is handled on the widget that *displays* the pixmap (QLabel),
        # not on the QPixmap itself. We delay the single-click handler so that it can be
        # cancelled when a double-click arrives.
        self._pending_click_cell = None
        self._pending_click_modifiers = Qt.NoModifier
        self._click_token = 0
        self._press_pos = None
        self._press_button = None
        self._press_modifiers = Qt.NoModifier
        self._is_dragging = False
        self._drag_start_cell = None
        self._last_drag_rect = None  # (r0, r1, c0, c1)
        self._drag_mode = None  # "replace" | "add" | "remove"
        app = QApplication.instance()
        self._double_click_ms = app.doubleClickInterval() if app is not None else 250
        self.label.mousePressEvent = self._on_label_mouse_press
        self.label.mouseDoubleClickEvent = self._on_label_mouse_double_click
        self.label.mouseMoveEvent = self._on_label_mouse_move
        self.label.mouseReleaseEvent = self._on_label_mouse_release

        self.cell_input = QLineEdit(self)
        self.cell_input.setPlaceholderText("e.g. AE12 or B4")
        go_button = QPushButton("Go to well", self)
        go_button.clicked.connect(self.go_to_cell)
        self.selection_input = QLineEdit(self)
        self.selection_input.setPlaceholderText("e.g. A1:E48, X1, AC24, Z2:AF6, ...")
        self.selection_input.editingFinished.connect(self.select_cells)
        self.selection_input.returnPressed.connect(self.select_cells)

        # Create navigation buttons
        up_button = QPushButton("↑", self)
        left_button = QPushButton("←", self)
        right_button = QPushButton("→", self)
        down_button = QPushButton("↓", self)
        add_button = QPushButton("Select", self)

        # Connect navigation buttons to their respective functions
        up_button.clicked.connect(self.move_up)
        left_button.clicked.connect(self.move_left)
        right_button.clicked.connect(self.move_right)
        down_button.clicked.connect(self.move_down)
        add_button.clicked.connect(self.add_current_well)

        layout = QHBoxLayout()
        layout.addWidget(self.label)

        layout_controls = QVBoxLayout()
        layout_controls.addStretch(2)

        # Add navigation buttons in a + sign layout
        layout_move = QGridLayout()
        layout_move.addWidget(up_button, 0, 2)
        layout_move.addWidget(left_button, 1, 1)
        layout_move.addWidget(add_button, 1, 2)
        layout_move.addWidget(right_button, 1, 3)
        layout_move.addWidget(down_button, 2, 2)
        layout_move.setColumnStretch(0, 1)
        layout_move.setColumnStretch(4, 1)
        layout_controls.addLayout(layout_move)

        layout_controls.addStretch(1)

        layout_input = QGridLayout()
        layout_input.addWidget(QLabel("Well Navigation"), 0, 0)
        layout_input.addWidget(self.cell_input, 0, 1)
        layout_input.addWidget(go_button, 0, 2)
        layout_input.addWidget(QLabel("Well Selection"), 1, 0)
        layout_input.addWidget(self.selection_input, 1, 1, 1, 2)
        layout_controls.addLayout(layout_input)

        control_widget = QWidget()
        control_widget.setLayout(layout_controls)
        control_widget.setFixedHeight(image_height)  # Set the height of controls to match the image

        layout.addWidget(control_widget)
        self.setLayout(layout)

    def _cell_from_label_pos(self, pos: QPoint):
        """Map a click position in label pixel coords -> (row, col) or None."""
        col = int(pos.x() // self.a)
        row = int(pos.y() // self.a)
        if 0 <= row < self.rows and 0 <= col < self.columns:
            return (row, col)
        return None

    def _row_label(self, row: int) -> str:
        # A..Z, AA..AF for 32 rows
        if row < 26:
            return chr(65 + row)
        return chr(64 + (row // 26)) + chr(65 + (row % 26))

    def _cell_name(self, row: int, col: int) -> str:
        return f"{self._row_label(row)}{col + 1}"

    def _emit_selection_changed(self):
        """Refresh UI elements that depend on selected_cells and notify listeners."""
        self.redraw_wells()
        self._set_selection_input_from_selected_cells()
        self.signal_wellSelected.emit(bool(self.selected_cells))

    def _toggle_or_replace_selection(self, cell, *, additive: bool):
        """
        Selection semantics to match the table-based well selector:
        - additive=False: replace selection with only this cell
        - additive=True: toggle this cell without clearing others
        """
        if additive:
            if cell in self.selected_cells:
                self.selected_cells.pop(cell, None)
            else:
                self.selected_cells[cell] = "#1f77b4"
        else:
            self.selected_cells = {cell: "#1f77b4"}

    def _set_selection_input_from_selected_cells(self):
        """Render current selection into the textbox, compacted into per-row ranges."""
        if not self.selected_cells:
            self.selection_input.setText("")
            return

        rows_to_cols = {}
        for r, c in self.selected_cells.keys():
            rows_to_cols.setdefault(r, []).append(c)

        parts = []
        for r in sorted(rows_to_cols.keys()):
            cols = sorted(set(rows_to_cols[r]))
            start = prev = cols[0]
            for c in cols[1:]:
                if c == prev + 1:
                    prev = c
                    continue
                # flush run
                if start == prev:
                    parts.append(f"{self._row_label(r)}{start + 1}")
                else:
                    parts.append(f"{self._row_label(r)}{start + 1}:{self._row_label(r)}{prev + 1}")
                start = prev = c
            # flush last run
            if start == prev:
                parts.append(f"{self._row_label(r)}{start + 1}")
            else:
                parts.append(f"{self._row_label(r)}{start + 1}:{self._row_label(r)}{prev + 1}")

        self.selection_input.setText(", ".join(parts))

    def _commit_single_click(self, token: int):
        # If a double-click happened, the token will have changed -> ignore.
        if token != self._click_token:
            return
        if self._is_dragging:
            return
        cell = self._pending_click_cell
        mods = self._pending_click_modifiers
        self._pending_click_cell = None
        self._pending_click_modifiers = Qt.NoModifier
        if cell is None:
            return

        self.current_cell = cell
        self._toggle_or_replace_selection(cell, additive=bool(mods & Qt.ShiftModifier))

        # Update UI without navigating (no signal_wellSelectedPos here).
        row, col = cell
        self.cell_input.setText(self._cell_name(row, col))
        self._emit_selection_changed()

    def _on_label_mouse_press(self, event):
        if event.button() not in (Qt.LeftButton, Qt.RightButton):
            return

        cell = self._cell_from_label_pos(event.pos())
        if cell is None:
            return

        self._press_pos = QPoint(event.pos())
        self._press_button = event.button()
        self._press_modifiers = event.modifiers()
        self._is_dragging = False
        self._drag_start_cell = cell
        self._last_drag_rect = None
        self._drag_mode = None

        # Delay single-click action so we can cancel it if a double-click arrives.
        if event.button() == Qt.LeftButton:
            self._pending_click_cell = cell
            self._pending_click_modifiers = event.modifiers()
            self._click_token += 1
            token = self._click_token
            QTimer.singleShot(self._double_click_ms, lambda: self._commit_single_click(token))
        event.accept()

    def _apply_drag_rect(self, rect, mode: str):
        r0, r1, c0, c1 = rect
        if mode == "add":
            for r in range(r0, r1 + 1):
                for c in range(c0, c1 + 1):
                    self.selected_cells[(r, c)] = "#1f77b4"
        elif mode == "remove":
            for r in range(r0, r1 + 1):
                for c in range(c0, c1 + 1):
                    self.selected_cells.pop((r, c), None)

    def _on_label_mouse_move(self, event):
        if self._press_pos is None or self._drag_start_cell is None:
            return

        # Start drag if we moved far enough.
        if not self._is_dragging:
            threshold = QApplication.startDragDistance()
            if (event.pos() - self._press_pos).manhattanLength() < threshold:
                return

            # Cancel any pending single-click action.
            self._click_token += 1
            self._pending_click_cell = None
            self._pending_click_modifiers = Qt.NoModifier
            self._is_dragging = True

            # Determine drag mode:
            # - Left-drag: replace selection (unless Shift is held, then add)
            # - Right-drag: remove
            if self._press_button == Qt.RightButton:
                self._drag_mode = "remove"
            elif self._press_modifiers & Qt.ShiftModifier:
                self._drag_mode = "add"
            else:
                self._drag_mode = "replace"

        current_cell = self._cell_from_label_pos(event.pos())
        if current_cell is None:
            return

        r0 = min(self._drag_start_cell[0], current_cell[0])
        r1 = max(self._drag_start_cell[0], current_cell[0])
        c0 = min(self._drag_start_cell[1], current_cell[1])
        c1 = max(self._drag_start_cell[1], current_cell[1])
        rect = (r0, r1, c0, c1)
        if rect == self._last_drag_rect:
            return
        self._last_drag_rect = rect

        if self._drag_mode == "replace":
            self.selected_cells = {}
            self._apply_drag_rect(rect, "add")
        else:
            # add/remove
            self._apply_drag_rect(rect, self._drag_mode)
        self.current_cell = current_cell  # keep outline tracking cursor
        self.redraw_wells()
        event.accept()

    def _on_label_mouse_release(self, event):
        if self._press_pos is None:
            return

        if self._is_dragging:
            # Finalize drag selection: sync textbox + update navigation overlay.
            self._set_selection_input_from_selected_cells()
            self.signal_wellSelected.emit(bool(self.selected_cells))

        self._press_pos = None
        self._press_button = None
        self._press_modifiers = Qt.NoModifier
        self._is_dragging = False
        self._drag_start_cell = None
        self._last_drag_rect = None
        self._drag_mode = None
        event.accept()

    def _on_label_mouse_double_click(self, event):
        if event.button() != Qt.LeftButton:
            return

        cell = self._cell_from_label_pos(event.pos())
        if cell is None:
            return

        # Cancel any pending single-click action.
        self._click_token += 1
        self._pending_click_cell = None
        self._is_dragging = False

        # Double-click navigates to the cell AND selects it.
        self._toggle_or_replace_selection(cell, additive=bool(event.modifiers() & Qt.ShiftModifier))
        self._set_selection_input_from_selected_cells()
        self.signal_wellSelected.emit(bool(self.selected_cells))

        # Navigate to the cell (emits signal_wellSelectedPos).
        self.current_cell = cell
        self.update_current_cell()
        event.accept()

    def move_up(self):
        if self.current_cell:
            row, col = self.current_cell
            if row > 0:
                self.current_cell = (row - 1, col)
                self.update_current_cell()

    def move_left(self):
        if self.current_cell:
            row, col = self.current_cell
            if col > 0:
                self.current_cell = (row, col - 1)
                self.update_current_cell()

    def move_right(self):
        if self.current_cell:
            row, col = self.current_cell
            if col < self.columns - 1:
                self.current_cell = (row, col + 1)
                self.update_current_cell()

    def move_down(self):
        if self.current_cell:
            row, col = self.current_cell
            if row < self.rows - 1:
                self.current_cell = (row + 1, col)
                self.update_current_cell()

    def add_current_well(self):
        if self.current_cell:
            row, col = self.current_cell
            cell = (row, col)
            cell_name = self._cell_name(row, col)
            if cell in self.selected_cells:
                self.selected_cells.pop(cell, None)
                print(f"Removed well {cell_name}")
            else:
                self.selected_cells[cell] = "#1f77b4"
                print(f"Added well {cell_name}")
            # Redraw only (do not navigate on select/toggle).
            self._emit_selection_changed()

    def update_current_cell(self):
        self.redraw_wells()
        row, col = self.current_cell
        # Update cell_input with the correct label (e.g., A1, B2, AA1, etc.)
        self.cell_input.setText(self._cell_name(row, col))

        x_mm = col * self.spacing_mm + self.a1_x_mm + WELLPLATE_OFFSET_X_mm
        y_mm = row * self.spacing_mm + self.a1_y_mm + WELLPLATE_OFFSET_Y_mm
        self.signal_wellSelectedPos.emit(x_mm, y_mm)

    def redraw_wells(self):
        self.image.fill(QColor("white"))  # Clear the pixmap first
        painter = QPainter(self.image)
        painter.setPen(QColor("white"))
        # Draw selected cells (blue)
        for (row, col), color in self.selected_cells.items():
            painter.setBrush(QColor(color))
            painter.drawRect(col * self.a, row * self.a, self.a, self.a)
        # Draw current cell in green
        if self.current_cell:
            painter.setBrush(Qt.NoBrush)  # No fill
            painter.setPen(QPen(QColor("red"), 2))  # Red outline, 2 pixels wide
            row, col = self.current_cell
            painter.drawRect(col * self.a + 2, row * self.a + 2, self.a - 3, self.a - 3)
        painter.end()
        self.label.setPixmap(self.image)

    def go_to_cell(self):
        cell_desc = self.cell_input.text().strip()
        match = re.match(r"([A-Za-z]+)(\d+)", cell_desc)
        if match:
            row_part, col_part = match.groups()
            row_index = self.row_to_index(row_part)
            col_index = int(col_part) - 1
            self.current_cell = (row_index, col_index)  # Update the current cell
            self.update_current_cell()

    def select_cells(self):
        # first clear selection
        self.selected_cells = {}

        pattern = r"([A-Za-z]+)(\d+):?([A-Za-z]*)(\d*)"
        cell_descriptions = self.selection_input.text().split(",")
        for desc in cell_descriptions:
            match = re.match(pattern, desc.strip())
            if match:
                start_row, start_col, end_row, end_col = match.groups()
                start_row_index = self.row_to_index(start_row)
                start_col_index = int(start_col) - 1

                if end_row and end_col:  # It's a range
                    end_row_index = self.row_to_index(end_row)
                    end_col_index = int(end_col) - 1
                    for row in range(min(start_row_index, end_row_index), max(start_row_index, end_row_index) + 1):
                        for col in range(min(start_col_index, end_col_index), max(start_col_index, end_col_index) + 1):
                            self.selected_cells[(row, col)] = "#1f77b4"
                else:  # It's a single cell
                    self.selected_cells[(start_row_index, start_col_index)] = "#1f77b4"
        self.redraw_wells()
        self.signal_wellSelected.emit(bool(self.selected_cells))

    def row_to_index(self, row):
        index = 0
        for char in row:
            index = index * 26 + (ord(char.upper()) - ord("A") + 1)
        return index - 1

    def onSelectionChanged(self):
        self.get_selected_cells()

    def onWellplateChanged(self):
        """A placeholder to match the method in WellSelectionWidget"""
        pass

    def get_selected_cells(self):
        list_of_selected_cells = list(self.selected_cells.keys())
        return list_of_selected_cells


class LedMatrixSettingsDialog(QDialog):
    def __init__(self, led_array):
        self.led_array = led_array
        super().__init__()
        self.setWindowTitle("LED Matrix Settings")

        self.layout = QVBoxLayout()

        # Add QDoubleSpinBox for LED intensity (0-1)
        self.NA_spinbox = QDoubleSpinBox()
        self.NA_spinbox.setKeyboardTracking(False)
        self.NA_spinbox.setRange(0, 1)
        self.NA_spinbox.setSingleStep(0.01)
        self.NA_spinbox.setValue(self.led_array.NA)

        NA_layout = QHBoxLayout()
        NA_layout.addWidget(QLabel("NA"))
        NA_layout.addWidget(self.NA_spinbox)

        self.layout.addLayout(NA_layout)
        self.setLayout(self.layout)

        # add ok/cancel buttons
        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        self.layout.addWidget(self.button_box)

        self.button_box.accepted.connect(self.update_NA)

    def update_NA(self):
        self.led_array.set_NA(self.NA_spinbox.value())


class SampleSettingsWidget(QFrame):
    def __init__(self, ObjectivesWidget, WellplateFormatWidget, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.objectivesWidget = ObjectivesWidget
        self.wellplateFormatWidget = WellplateFormatWidget

        # Set up the layout
        top_row_layout = QGridLayout()
        top_row_layout.setSpacing(2)
        top_row_layout.setContentsMargins(0, 2, 0, 2)
        top_row_layout.addWidget(self.objectivesWidget, 0, 0)
        top_row_layout.addWidget(self.wellplateFormatWidget, 0, 1)
        self.setLayout(top_row_layout)
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

        # Connect signals for saving settings
        self.objectivesWidget.signal_objective_changed.connect(self.save_settings)
        self.wellplateFormatWidget.signalWellplateSettings.connect(lambda *args: self.save_settings())

    def save_settings(self):
        """Save current objective and wellplate format to cache"""
        os.makedirs("cache", exist_ok=True)
        data = {
            "objective": self.objectivesWidget.dropdown.currentText(),
            "wellplate_format": self.wellplateFormatWidget.wellplate_format,
        }

        with open("cache/objective_and_sample_format.txt", "w") as f:
            json.dump(data, f)


from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from mpl_toolkits.mplot3d import proj3d
from scipy.interpolate import griddata


class SurfacePlotWidget(QWidget):
    """
    A widget that displays a 3D surface plot of the coordinates.
    """

    signal_point_clicked = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._log = squid.logging.get_logger(__name__)

        # Setup canvas and figure
        self.fig = Figure()
        self.canvas = FigureCanvas(self.fig)
        self.ax = self.fig.add_subplot(111, projection="3d")

        layout = QVBoxLayout()
        layout.addWidget(self.canvas)
        self.setLayout(layout)

        self.selected_index = None
        self.plot_populated = False

        # Connect events
        self.canvas.mpl_connect("scroll_event", self.on_scroll)
        self.canvas.mpl_connect("button_press_event", self.on_click)

        self.x = list()
        self.y = list()
        self.z = list()
        self.regions = list()
        # Filtered coordinates for plotting (min Z at each unique X,Y)
        self.x_plot = np.array([])
        self.y_plot = np.array([])
        self.z_plot = np.array([])

    def clear(self):
        self.x.clear()
        self.y.clear()
        self.z.clear()
        self.regions.clear()
        self.x_plot = np.array([])
        self.y_plot = np.array([])
        self.z_plot = np.array([])
        # Reset plot state and clear the visual axes
        self.plot_populated = False
        self.ax.clear()
        self.canvas.draw()

    def add_point(self, x: float, y: float, z: float, region: int):
        self.x.append(x)
        self.y.append(y)
        self.z.append(z)
        self.regions.append(region)

    def plot(self) -> None:
        """
        Plot both surface and scatter points in 3D.

        For Z-stacks, uses the minimum Z at each unique X,Y location. This shows
        the bottom/focus surface of the sample and avoids interpolation artifacts
        that would occur if the surface passed through the middle of the stack.
        """
        try:
            # Clear previous plot
            self.ax.clear()

            if len(self.x) == 0:
                self._log.debug("No data to plot")
                self.canvas.draw()
                self.plot_populated = False
                return

            x = np.array(self.x).astype(float)
            y = np.array(self.y).astype(float)
            z = np.array(self.z).astype(float)
            regions = np.array(self.regions)

            # Filter to get minimum Z at each unique X,Y location (for Z-stacks)
            # Use vectorized approach for better performance with large datasets
            xy_precision = 4  # decimal places for grouping
            xy_keys = np.round(x, xy_precision) + 1j * np.round(y, xy_precision)

            # Find index of minimum Z for each unique (X, Y) using vectorized operations
            unique_xy, inverse = np.unique(xy_keys, return_inverse=True)

            # Sort by group (inverse) then by Z, so first in each group has minimum Z
            order = np.lexsort((z, inverse))
            grouped_inverse = inverse[order]

            # First occurrence of each group in sorted order corresponds to minimum Z
            _, first_indices = np.unique(grouped_inverse, return_index=True)
            min_z_indices = order[first_indices]

            # Store filtered coordinates using the min-Z indices (ensures x, y, z, region all match)
            self.x_plot = x[min_z_indices]
            self.y_plot = y[min_z_indices]
            self.z_plot = z[min_z_indices]
            regions_plot = regions[min_z_indices]

            # plot surface by region
            for r in np.unique(regions_plot):
                try:
                    mask = regions_plot == r
                    num_points = np.sum(mask)
                    if num_points >= 4:
                        # Check if points have sufficient spread in X and Y for surface interpolation
                        # griddata uses Delaunay triangulation which requires 2D spread in X-Y space
                        x_range = np.ptp(self.x_plot[mask])  # peak-to-peak (max - min)
                        y_range = np.ptp(self.y_plot[mask])
                        # Use practical threshold based on typical stage precision (~1 µm)
                        # Smaller spreads can lead to nearly collinear points and Qhull errors
                        min_spread = 1e-3  # minimum spread in mm (~1 µm)

                        if x_range < min_spread or y_range < min_spread:
                            # Single FOV or collinear points: skip surface, scatter plot will still show
                            self._log.debug(
                                f"Region {r}: insufficient X,Y spread for surface "
                                f"(x_range={x_range:.2e}, y_range={y_range:.2e}), showing scatter only"
                            )
                        else:
                            x_surface = self.x_plot[mask]
                            y_surface = self.y_plot[mask]
                            z_surface = self.z_plot[mask]

                            grid_x, grid_y = np.mgrid[
                                min(x_surface) : max(x_surface) : 10j, min(y_surface) : max(y_surface) : 10j
                            ]
                            grid_z = griddata((x_surface, y_surface), z_surface, (grid_x, grid_y), method="cubic")
                            self.ax.plot_surface(grid_x, grid_y, grid_z, cmap="viridis", edgecolor="none")
                    else:
                        self._log.debug(f"Region {r} has only {num_points} point(s), skipping surface interpolation")
                except Exception as e:
                    raise Exception(f"Cannot plot region {r}: {e}")

            # Create scatter plot using filtered coordinates (bottom Z only)
            self.colors = ["r"] * len(self.x_plot)
            self.scatter = self.ax.scatter(self.x_plot, self.y_plot, self.z_plot, c=self.colors, s=30)

            # Set labels
            self.ax.set_xlabel("X (mm)")
            self.ax.set_ylabel("Y (mm)")
            self.ax.set_zlabel("Z (um)")
            self.ax.set_title("Double-click a point to go to that position")

            # Force x and y to have same scale
            max_range = max(np.ptp(self.x_plot), np.ptp(self.y_plot))
            if max_range == 0:
                max_range = 1.0  # Default range for single point
            center_x = np.mean(self.x_plot)
            center_y = np.mean(self.y_plot)

            self.ax.set_xlim(center_x - max_range / 2, center_x + max_range / 2)
            self.ax.set_ylim(center_y - max_range / 2, center_y + max_range / 2)

            self.canvas.draw()
            self.plot_populated = True
        except Exception as e:
            self._log.error(f"Error plotting surface: {e}")

    def on_scroll(self, event):
        scale = 1.1 if event.button == "up" else 0.9

        def zoom(lim):
            center = (lim[0] + lim[1]) / 2
            half_range = (lim[1] - lim[0]) / 2 * scale
            return center - half_range, center + half_range

        self.ax.set_xlim(zoom(self.ax.get_xlim()))
        self.ax.set_ylim(zoom(self.ax.get_ylim()))
        self.ax.set_zlim(zoom(self.ax.get_zlim()))
        self.canvas.draw()

    def on_click(self, event):
        if not self.plot_populated:
            return
        if not event.dblclick or event.inaxes != self.ax:
            return

        # Cancel drag mode after double-click
        self.canvas.button_pressed = None  # FIX: Avoids AttributeError

        # Project 3D points to 2D screen space (use filtered plot coordinates)
        x2d, y2d, _ = proj3d.proj_transform(self.x_plot, self.y_plot, self.z_plot, self.ax.get_proj())
        dists = np.hypot(x2d - event.xdata, y2d - event.ydata)
        idx = np.argmin(dists)

        # Threshold in data coordinates
        display_thresh = 0.05 * max(
            self.ax.get_xlim()[1] - self.ax.get_xlim()[0], self.ax.get_ylim()[1] - self.ax.get_ylim()[0]
        )
        if dists[idx] > display_thresh:
            return

        # Change point color
        self.colors = ["r"] * len(self.x_plot)
        self.colors[idx] = "g"
        self.scatter.remove()
        self.scatter = self.ax.scatter(self.x_plot, self.y_plot, self.z_plot, c=self.colors, s=30)

        print(f"Clicked Point: x={self.x_plot[idx]:.3f}, y={self.y_plot[idx]:.3f}, z={self.z_plot[idx]:.3f}")
        self.canvas.draw()
        self.signal_point_clicked.emit(float(self.x_plot[idx]), float(self.y_plot[idx]))


class WavelengthWidget(QWidget):
    """Widget for wavelength field with checkbox to toggle between int and N/A."""

    def __init__(self, wavelength_nm=None, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(4)

        self.checkbox = QCheckBox()
        self.checkbox.setToolTip("Check to set wavelength, uncheck for N/A")
        self.checkbox.stateChanged.connect(self._on_checkbox_changed)
        layout.addWidget(self.checkbox)

        self.spinbox = QSpinBox()
        self.spinbox.setRange(200, 900)
        self.spinbox.setValue(405)
        layout.addWidget(self.spinbox)

        self.na_label = QLabel("N/A")
        self.na_label.setStyleSheet("color: gray;")
        layout.addWidget(self.na_label)

        # Set initial state
        if wavelength_nm is not None:
            self.checkbox.setChecked(True)
            self.spinbox.setValue(wavelength_nm)
            self.spinbox.setVisible(True)
            self.na_label.setVisible(False)
        else:
            self.checkbox.setChecked(False)
            self.spinbox.setVisible(False)
            self.na_label.setVisible(True)

    def _on_checkbox_changed(self, state):
        checked = state == Qt.Checked
        self.spinbox.setVisible(checked)
        self.na_label.setVisible(not checked)

    def get_wavelength(self):
        """Return wavelength value or None if N/A."""
        if self.checkbox.isChecked():
            return self.spinbox.value()
        return None

    def set_wavelength(self, wavelength_nm):
        """Set wavelength value or N/A."""
        if wavelength_nm is not None:
            self.checkbox.setChecked(True)
            self.spinbox.setValue(wavelength_nm)
        else:
            self.checkbox.setChecked(False)


class IlluminationChannelConfiguratorDialog(QDialog):
    """Dialog for editing illumination channel hardware configuration.

    This edits the machine_configs/illumination_channel_config.yaml file which defines
    the physical illumination hardware. User-facing acquisition settings (display color,
    enabled state, filter position) are configured separately in user profile configs.
    """

    signal_channels_updated = Signal()

    # Column indices for the channels table
    COL_NAME = 0
    COL_TYPE = 1
    COL_PORT = 2
    COL_WAVELENGTH = 3
    COL_CALIBRATION = 4

    def __init__(self, config_repo, parent=None):
        super().__init__(parent)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.config_repo = config_repo
        self.illumination_config = None
        self.setWindowTitle("Illumination Channel Configurator")
        self.setMinimumSize(900, 500)
        self._setup_ui()
        self._load_channels()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Warning label
        warning_label = QLabel(
            "Warning: Illumination channel configuration is hardware-specific. "
            "Modifying these settings may break existing acquisition configurations. "
            "Only change these settings when necessary."
        )
        warning_label.setWordWrap(True)
        warning_label.setStyleSheet("color: #CC0000; font-weight: bold;")
        layout.addWidget(warning_label)

        # Table for illumination channels
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Name", "Type", "Controller Port", "Wavelength (nm)", "Calibration File"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        layout.addWidget(self.table)

        # Buttons
        button_layout = QHBoxLayout()

        self.btn_add = QPushButton("Add Channel")
        self.btn_add.setAutoDefault(False)
        self.btn_add.setDefault(False)
        self.btn_add.clicked.connect(self._add_channel)
        button_layout.addWidget(self.btn_add)

        self.btn_remove = QPushButton("Remove Channel")
        self.btn_remove.setAutoDefault(False)
        self.btn_remove.setDefault(False)
        self.btn_remove.clicked.connect(self._remove_channel)
        button_layout.addWidget(self.btn_remove)

        self.btn_move_up = QPushButton("Move Up")
        self.btn_move_up.setAutoDefault(False)
        self.btn_move_up.clicked.connect(self._move_up)
        button_layout.addWidget(self.btn_move_up)

        self.btn_move_down = QPushButton("Move Down")
        self.btn_move_down.setAutoDefault(False)
        self.btn_move_down.clicked.connect(self._move_down)
        button_layout.addWidget(self.btn_move_down)

        self.btn_port_mapping = QPushButton("Port Mapping...")
        self.btn_port_mapping.setAutoDefault(False)
        self.btn_port_mapping.clicked.connect(self._open_port_mapping)
        button_layout.addWidget(self.btn_port_mapping)

        button_layout.addStretch()

        self.btn_save = QPushButton("Save")
        self.btn_save.setAutoDefault(False)
        self.btn_save.clicked.connect(self._save_changes)
        button_layout.addWidget(self.btn_save)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setAutoDefault(False)
        self.btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(self.btn_cancel)

        layout.addLayout(button_layout)

    def _get_calibration_full_path(self, filename):
        """Get full path for calibration file."""
        if not filename:
            return ""
        calib_dir = self.config_repo.machine_configs_path / "intensity_calibrations"
        return str(calib_dir / filename)

    def _load_channels(self):
        """Load illumination channels from YAML config into the table"""
        self.illumination_config = self.config_repo.get_illumination_config()
        if not self.illumination_config:
            return

        # Get available ports (only those with mappings)
        available_ports = self.illumination_config.get_available_ports()

        self.table.setRowCount(len(self.illumination_config.channels))

        for row, channel in enumerate(self.illumination_config.channels):
            # Name (editable)
            name_item = QTableWidgetItem(channel.name)
            self.table.setItem(row, self.COL_NAME, name_item)

            # Type (dropdown)
            type_combo = QComboBox()
            type_combo.addItems(["epi_illumination", "transillumination"])
            type_combo.setCurrentText(channel.type.value)
            type_combo.currentTextChanged.connect(lambda text, r=row: self._on_type_changed(r, text))
            self.table.setCellWidget(row, self.COL_TYPE, type_combo)

            # Controller Port (dropdown) - only ports with mappings
            port_combo = QComboBox()
            port_combo.addItems(available_ports)
            port_combo.setCurrentText(channel.controller_port)
            self.table.setCellWidget(row, self.COL_PORT, port_combo)

            # Wavelength (checkbox + spinbox, or N/A)
            wave_widget = WavelengthWidget(channel.wavelength_nm)
            self.table.setCellWidget(row, self.COL_WAVELENGTH, wave_widget)

            # Calibration file (full path)
            full_path = self._get_calibration_full_path(channel.intensity_calibration_file)
            calib_item = QTableWidgetItem(full_path)
            self.table.setItem(row, self.COL_CALIBRATION, calib_item)

    def _on_type_changed(self, row, new_type):
        """Handle type change - update wavelength default and controller port"""
        wave_widget = self.table.cellWidget(row, self.COL_WAVELENGTH)
        available_ports = self.illumination_config.get_available_ports()

        # Find first available USB and D ports
        first_usb = next((p for p in available_ports if p.startswith("USB")), None)
        first_d = next((p for p in available_ports if p.startswith("D")), None)

        if new_type == "epi_illumination":
            # Set wavelength to default 405nm for epi
            if isinstance(wave_widget, WavelengthWidget):
                wave_widget.set_wavelength(405)

            # Update controller port to first available laser port
            port_combo = self.table.cellWidget(row, self.COL_PORT)
            if port_combo and port_combo.currentText().startswith("USB") and first_d:
                port_combo.setCurrentText(first_d)
        else:
            # Set wavelength to N/A for transillumination
            if isinstance(wave_widget, WavelengthWidget):
                wave_widget.set_wavelength(None)

            # Update controller port to first available USB port
            port_combo = self.table.cellWidget(row, self.COL_PORT)
            if port_combo and port_combo.currentText().startswith("D") and first_usb:
                port_combo.setCurrentText(first_usb)

    def _add_channel(self):
        """Add a new illumination channel"""
        dialog = AddIlluminationChannelDialog(self.illumination_config, self)
        if dialog.exec_() == QDialog.Accepted:
            channel_data = dialog.get_channel_data()
            from control.models.illumination_config import IlluminationChannel

            new_channel = IlluminationChannel(**channel_data)
            self.illumination_config.channels.append(new_channel)
            self._load_channels()

    def _remove_channel(self):
        """Remove selected channel"""
        current_row = self.table.currentRow()
        if current_row < 0:
            return

        name_item = self.table.item(current_row, 0)
        if name_item:
            reply = QMessageBox.question(
                self, "Confirm Removal", f"Remove channel '{name_item.text()}'?", QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                del self.illumination_config.channels[current_row]
                self._load_channels()

    def _move_up(self):
        """Move selected channel up"""
        current_row = self.table.currentRow()
        if current_row <= 0:
            return

        channels = self.illumination_config.channels
        channels[current_row], channels[current_row - 1] = channels[current_row - 1], channels[current_row]
        self._load_channels()
        self.table.selectRow(current_row - 1)

    def _move_down(self):
        """Move selected channel down"""
        current_row = self.table.currentRow()
        if not self.illumination_config or current_row < 0 or current_row >= len(self.illumination_config.channels) - 1:
            return

        channels = self.illumination_config.channels
        channels[current_row], channels[current_row + 1] = channels[current_row + 1], channels[current_row]
        self._load_channels()
        self.table.selectRow(current_row + 1)

    def _open_port_mapping(self):
        """Open the controller port mapping dialog"""
        dialog = ControllerPortMappingDialog(self.config_repo, self)
        dialog.signal_mappings_updated.connect(self._load_channels)
        dialog.exec_()

    def _save_changes(self):
        """Save all changes to illumination channel config"""
        if not self.illumination_config:
            return

        # Confirmation dialog
        reply = QMessageBox.question(
            self,
            "Confirm Save",
            "Saving these changes will modify your hardware configuration.\n"
            "This may affect existing acquisition settings.\n\n"
            "Do you want to continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        from control.models.illumination_config import IlluminationType

        # Validate channel names before saving
        names = []
        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, self.COL_NAME)
            if name_item:
                name = name_item.text().strip()
                if not name:
                    QMessageBox.warning(
                        self,
                        "Validation Error",
                        f"Channel name at row {row + 1} cannot be empty.",
                    )
                    return
                if name in names:
                    QMessageBox.warning(
                        self,
                        "Validation Error",
                        f"Duplicate channel name '{name}' found.",
                    )
                    return
                names.append(name)

        # Update channels from table
        for row in range(self.table.rowCount()):
            channel = self.illumination_config.channels[row]

            # Name
            name_item = self.table.item(row, self.COL_NAME)
            if name_item:
                channel.name = name_item.text().strip()

            # Type
            type_widget = self.table.cellWidget(row, self.COL_TYPE)
            if isinstance(type_widget, QComboBox):
                channel.type = IlluminationType(type_widget.currentText())

            # Controller Port
            port_widget = self.table.cellWidget(row, self.COL_PORT)
            if isinstance(port_widget, QComboBox):
                channel.controller_port = port_widget.currentText()

            # Wavelength (checkbox + spinbox widget)
            wave_widget = self.table.cellWidget(row, self.COL_WAVELENGTH)
            if isinstance(wave_widget, WavelengthWidget):
                channel.wavelength_nm = wave_widget.get_wavelength()
            else:
                channel.wavelength_nm = None

            # Calibration file (extract filename from full path)
            calib_item = self.table.item(row, self.COL_CALIBRATION)
            if calib_item:
                calib_text = calib_item.text().strip()
                if calib_text:
                    # Extract just the filename from full path
                    channel.intensity_calibration_file = Path(calib_text).name
                else:
                    channel.intensity_calibration_file = None

        # Save to YAML file
        self.config_repo.save_illumination_config(self.illumination_config)
        self.signal_channels_updated.emit()
        self.accept()


# Keep old name as alias for backwards compatibility
ChannelEditorDialog = IlluminationChannelConfiguratorDialog


class AddIlluminationChannelDialog(QDialog):
    """Dialog for adding a new illumination channel"""

    def __init__(self, illumination_config, parent=None):
        super().__init__(parent)
        self.illumination_config = illumination_config
        self.setWindowTitle("Add Illumination Channel")
        self._setup_ui()

    def _setup_ui(self):
        layout = QFormLayout(self)

        # Channel type
        self.type_combo = QComboBox()
        self.type_combo.addItems(["epi_illumination", "transillumination"])
        self.type_combo.currentTextChanged.connect(self._on_type_changed)
        layout.addRow("Type:", self.type_combo)

        # Name
        self.name_edit = QLineEdit()
        layout.addRow("Name:", self.name_edit)

        # Controller port - only ports with mappings
        available_ports = self.illumination_config.get_available_ports() if self.illumination_config else []
        # Reorder: D ports first for epi_illumination default
        d_ports = [p for p in available_ports if p.startswith("D")]
        usb_ports = [p for p in available_ports if p.startswith("USB")]
        self.port_combo = QComboBox()
        self.port_combo.addItems(d_ports + usb_ports)
        layout.addRow("Controller Port:", self.port_combo)

        # Wavelength (for epi_illumination, optional for transillumination)
        self.wave_spin = QSpinBox()
        self.wave_spin.setRange(200, 900)
        self.wave_spin.setValue(405)
        self.wave_spin.setSpecialValueText("N/A")  # Show N/A when value is minimum
        self.wave_spin.setMinimum(0)  # Allow 0 to represent N/A
        layout.addRow("Wavelength (nm):", self.wave_spin)

        # Calibration file
        self.calib_edit = QLineEdit()
        self.calib_edit.setPlaceholderText("e.g., 405.csv")
        layout.addRow("Calibration File:", self.calib_edit)

        # Buttons
        button_layout = QHBoxLayout()
        self.btn_ok = QPushButton("Add")
        self.btn_ok.clicked.connect(self._validate_and_accept)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(self.btn_ok)
        button_layout.addWidget(self.btn_cancel)
        layout.addRow(button_layout)

    def _validate_and_accept(self):
        """Validate input before accepting"""
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Validation Error", "Channel name cannot be empty.")
            return

        # Check for duplicate names
        if self.illumination_config:
            existing_names = [ch.name for ch in self.illumination_config.channels]
            if name in existing_names:
                QMessageBox.warning(self, "Validation Error", f"Channel '{name}' already exists.")
                return

        self.accept()

    def _on_type_changed(self, type_str):
        is_epi = type_str == "epi_illumination"
        available_ports = self.illumination_config.get_available_ports() if self.illumination_config else []
        first_d = next((p for p in available_ports if p.startswith("D")), None)
        first_usb = next((p for p in available_ports if p.startswith("USB")), None)

        # Update port default based on type
        if is_epi:
            if first_d:
                self.port_combo.setCurrentText(first_d)
            self.wave_spin.setValue(405)
        else:
            if first_usb:
                self.port_combo.setCurrentText(first_usb)
            self.wave_spin.setValue(0)  # Shows as N/A

    def get_channel_data(self):
        from control.models.illumination_config import IlluminationType

        channel_type = IlluminationType(self.type_combo.currentText())
        wavelength = self.wave_spin.value()
        data = {
            "name": self.name_edit.text().strip(),
            "type": channel_type,
            "controller_port": self.port_combo.currentText(),
            "wavelength_nm": wavelength if wavelength > 0 else None,
        }

        calib_text = self.calib_edit.text().strip()
        data["intensity_calibration_file"] = calib_text if calib_text else None

        return data


# Keep old name as alias for backwards compatibility
AddChannelDialog = AddIlluminationChannelDialog


class ControllerPortMappingDialog(QDialog):
    """Dialog for editing controller port to source code mappings.

    Shows all available controller ports (USB1-USB8 for LED matrix, D1-D8 for lasers)
    and their corresponding illumination source codes.
    """

    signal_mappings_updated = Signal()

    def __init__(self, config_repo, parent=None):
        super().__init__(parent)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.config_repo = config_repo
        self.illumination_config = None
        self.setWindowTitle("Controller Port Mapping")
        self.setMinimumSize(400, 450)
        self._setup_ui()
        self._load_mappings()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Info label
        info_label = QLabel(
            "Map controller ports to illumination source codes. "
            "USB ports are for LED matrix patterns, D ports are for lasers."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: gray; font-style: italic;")
        layout.addWidget(info_label)

        # Table for port mappings
        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Controller Port", "Source Code"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.table)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.btn_save = QPushButton("Save")
        self.btn_save.clicked.connect(self._save_changes)
        button_layout.addWidget(self.btn_save)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(self.btn_cancel)

        layout.addLayout(button_layout)

    def _load_mappings(self):
        """Load current port mappings into the table"""
        from control.models.illumination_config import IlluminationChannelConfig

        self.illumination_config = self.config_repo.get_illumination_config()
        if not self.illumination_config:
            return

        port_mapping = self.illumination_config.controller_port_mapping
        all_ports = IlluminationChannelConfig.ALL_PORTS

        self.table.setRowCount(len(all_ports))

        for row, port in enumerate(all_ports):
            # Controller port (read-only)
            port_item = QTableWidgetItem(port)
            port_item.setFlags(port_item.flags() & ~Qt.ItemIsEditable)
            port_item.setBackground(QColor(240, 240, 240))
            self.table.setItem(row, 0, port_item)

            # Source code (editable spinbox with N/A option)
            source_code = port_mapping.get(port)
            source_widget = SourceCodeWidget(source_code)
            self.table.setCellWidget(row, 1, source_widget)

    def _save_changes(self):
        """Save changes to port mappings"""
        if not self.illumination_config:
            return

        # Confirmation dialog
        reply = QMessageBox.question(
            self,
            "Confirm Save",
            "Saving these changes will modify your controller port mappings.\n"
            "This may affect existing acquisition settings.\n\n"
            "Do you want to continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        # Update mappings from table
        new_mapping = {}
        for row in range(self.table.rowCount()):
            port_item = self.table.item(row, 0)
            if not port_item:
                continue

            port = port_item.text()
            source_widget = self.table.cellWidget(row, 1)

            if isinstance(source_widget, SourceCodeWidget):
                source_code = source_widget.get_source_code()
                if source_code is not None:
                    new_mapping[port] = source_code

        self.illumination_config.controller_port_mapping = new_mapping
        self.config_repo.save_illumination_config(self.illumination_config)
        self.signal_mappings_updated.emit()
        self.accept()


class SourceCodeWidget(QWidget):
    """Widget for source code field with checkbox to toggle between int and N/A."""

    def __init__(self, source_code=None, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(4)

        self.checkbox = QCheckBox()
        self.checkbox.setToolTip("Check to set source code, uncheck for N/A")
        self.checkbox.stateChanged.connect(self._on_checkbox_changed)
        layout.addWidget(self.checkbox)

        self.spinbox = QSpinBox()
        self.spinbox.setRange(0, 30)
        self.spinbox.setValue(0)
        layout.addWidget(self.spinbox)

        self.na_label = QLabel("N/A")
        self.na_label.setStyleSheet("color: gray;")
        layout.addWidget(self.na_label)

        # Set initial state
        if source_code is not None:
            self.checkbox.setChecked(True)
            self.spinbox.setValue(source_code)
            self.spinbox.setVisible(True)
            self.na_label.setVisible(False)
        else:
            self.checkbox.setChecked(False)
            self.spinbox.setVisible(False)
            self.na_label.setVisible(True)

    def _on_checkbox_changed(self, state):
        checked = state == Qt.Checked
        self.spinbox.setVisible(checked)
        self.na_label.setVisible(not checked)

    def get_source_code(self):
        """Return source code value or None if N/A."""
        if self.checkbox.isChecked():
            return self.spinbox.value()
        return None

    def set_source_code(self, source_code):
        """Set source code value or N/A."""
        if source_code is not None:
            self.checkbox.setChecked(True)
            self.spinbox.setValue(source_code)
        else:
            self.checkbox.setChecked(False)


# Keep old name as alias for backwards compatibility
AdvancedChannelMappingDialog = ControllerPortMappingDialog


class RAMMonitorWidget(QWidget):
    """Compact RAM monitor widget for status bar.

    Displays current RAM usage continuously when enabled. During acquisition,
    connects to MemoryMonitor for more detailed tracking.

    State Invariants:
        - When _memory_monitor is set, updates come via signals (timer is paused)
        - When _memory_monitor is None, updates come via timer

    Attributes:
        label_current: QLabel showing current RAM usage
        label_available: QLabel showing available system RAM
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._memory_monitor = None
        self._session_peak_mb = 0.0  # Track peak RAM usage across the session
        self._log = logging.getLogger("squid." + self.__class__.__name__)
        self._setup_ui()
        self._setup_timer()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(4)

        self.label_icon = QLabel("RAM usage:")
        self.label_icon.setStyleSheet("font-weight: bold;")

        # Value labels with fixed widths for stable layout
        fm = QFontMetrics(self.font())
        self.label_current = self._create_value_label(fm.horizontalAdvance("88.88 GB"))
        self.label_peak = self._create_value_label(fm.horizontalAdvance("88.88 GB"))
        self.label_available = self._create_value_label(fm.horizontalAdvance("888.8 GB"))

        # Separator and descriptor labels
        separator_style = "color: #666;"
        self.label_separator1 = QLabel("|")
        self.label_separator1.setStyleSheet(separator_style)
        self.label_peak_label = QLabel("peak:")
        self.label_peak_label.setStyleSheet(separator_style)
        self.label_separator2 = QLabel("|")
        self.label_separator2.setStyleSheet(separator_style)
        self.label_available_label = QLabel("available:")
        self.label_available_label.setStyleSheet(separator_style)

        layout.addWidget(self.label_icon)
        layout.addWidget(self.label_current)
        layout.addWidget(self.label_separator1)
        layout.addWidget(self.label_peak_label)
        layout.addWidget(self.label_peak)
        layout.addWidget(self.label_separator2)
        layout.addWidget(self.label_available_label)
        layout.addWidget(self.label_available)

    def _create_value_label(self, width: int) -> QLabel:
        """Create a left-aligned value label with fixed width."""
        label = QLabel("--")
        label.setFixedWidth(width)
        label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        return label

    def _setup_timer(self):
        """Setup timer for periodic memory updates when not connected to monitor."""
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self._update_memory_display)
        self._update_timer.setInterval(1000)  # Update every 1 second

    def start_monitoring(self, reset_peak: bool = True):
        """Start continuous memory monitoring.

        Args:
            reset_peak: If True, reset session peak tracking. Set to False when
                       resuming monitoring after disconnecting from an acquisition monitor.
        """
        if self._memory_monitor is not None:
            self._log.warning("Cannot start timer while connected to external monitor")
            return

        self._log.info("Starting continuous RAM monitoring timer")
        if reset_peak:
            self._session_peak_mb = 0.0
            self.label_peak.setText("--")
        self._update_memory_display()  # Initial update
        self._update_timer.start()

    def stop_monitoring(self):
        """Stop continuous memory monitoring."""
        self._update_timer.stop()
        self.label_current.setText("--")
        self.label_peak.setText("--")
        self.label_available.setText("--")

    def _update_memory_display(self):
        """Update memory display using direct measurement."""
        if self._memory_monitor is not None:
            # During acquisition, let the monitor signals handle updates
            return

        try:
            from control.core.memory_profiler import get_memory_footprint_mb

            # Get current process memory usage
            footprint_mb = get_memory_footprint_mb(os.getpid())
            self._log.debug(f"RAM monitor update: footprint={footprint_mb:.1f} MB")
            if footprint_mb > 0:
                self._session_peak_mb = max(self._session_peak_mb, footprint_mb)
                current_gb = footprint_mb / 1024
                peak_gb = self._session_peak_mb / 1024
                self.label_current.setText(f"{current_gb:.2f} GB")
                self.label_peak.setText(f"{peak_gb:.2f} GB")
            else:
                # Footprint unavailable on this platform/configuration
                self.label_current.setText("N/A")
                self.label_peak.setText("N/A")
                self._log.debug("Memory footprint unavailable (platform may not support this metric)")

            # Get system available memory
            mem_info = psutil.virtual_memory()
            available_gb = mem_info.available / (1024**3)
            self.label_available.setText(f"{available_gb:.1f} GB")
        except Exception as e:
            self._log.warning(f"RAM monitor update failed: {e}")

    def connect_monitor(self, memory_monitor: Optional["MemoryMonitor"]) -> None:
        """Connect to a MemoryMonitor's signals for live updates during acquisition.

        When connected, the timer-based updates are paused and updates come via signals.

        Args:
            memory_monitor: MemoryMonitor instance with signals attribute.
        """
        if memory_monitor is not None:
            self._update_timer.stop()  # Pause timer - signals will handle updates
        self._memory_monitor = memory_monitor
        if memory_monitor is not None and memory_monitor.signals is not None:
            memory_monitor.signals.footprint_updated.connect(self._on_footprint_updated)

    def disconnect_monitor(self) -> None:
        """Disconnect from acquisition monitor.

        Note: This method only disconnects from the monitor and clears the reference.
        It does NOT restart the timer - the caller is responsible for deciding whether
        to call start_monitoring() or stop_monitoring() based on the current settings.
        This avoids coupling the widget to control._def settings.
        """
        if self._memory_monitor is not None and self._memory_monitor.signals is not None:
            try:
                self._memory_monitor.signals.footprint_updated.disconnect(self._on_footprint_updated)
            except RuntimeError:
                # Already disconnected - this is expected
                self._log.debug("Signal already disconnected")
            except TypeError as e:
                # Unexpected - slot signature mismatch could indicate a bug
                self._log.warning(f"Signal disconnect type error (possible bug): {e}")
        self._memory_monitor = None
        # Timer is NOT started here - caller decides via start_monitoring()/stop_monitoring()

    def _on_footprint_updated(self, footprint_mb: float) -> None:
        """Handle footprint update signal from MemoryMonitor.

        Args:
            footprint_mb: Current memory footprint in megabytes.
        """
        # Track peak and display in GB for readability
        self._session_peak_mb = max(self._session_peak_mb, footprint_mb)
        current_gb = footprint_mb / 1024
        peak_gb = self._session_peak_mb / 1024
        self.label_current.setText(f"{current_gb:.2f} GB")
        self.label_peak.setText(f"{peak_gb:.2f} GB")

        # Also update available RAM
        try:
            mem_info = psutil.virtual_memory()
            available_gb = mem_info.available / (1024**3)
            self.label_available.setText(f"{available_gb:.1f} GB")
        except Exception as e:
            self._log.debug(f"Failed to read available RAM: {e}")
            self.label_available.setText("--")

    def closeEvent(self, event):
        """Ensure monitoring resources are cleaned up when the widget closes."""
        try:
            self.stop_monitoring()
        except Exception as e:
            self._log.debug(f"Error stopping monitoring on close: {e}")

        try:
            self.disconnect_monitor()
        except Exception as e:
            self._log.debug(f"Error disconnecting monitor on close: {e}")

        super().closeEvent(event)


class BackpressureMonitorWidget(QWidget):
    """Compact backpressure monitor widget for status bar.

    Displays pending jobs and bytes during acquisition when backpressure
    throttling is enabled. Shows a warning indicator when throttling is active.
    """

    # How long to keep [THROTTLED] visible after throttle releases (in update cycles)
    THROTTLE_STICKY_CYCLES = 4  # 4 cycles * 500ms = 2 seconds

    def __init__(self, parent=None):
        super().__init__(parent)
        self._controller = None
        self._log = logging.getLogger("squid." + self.__class__.__name__)
        self._throttle_sticky_counter = 0  # Countdown for sticky throttle indicator
        self._setup_ui()
        self._setup_timer()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(4)

        self.label_prefix = QLabel("Queue:")
        self.label_prefix.setStyleSheet("font-weight: bold;")

        # Value labels with fixed widths for stable layout
        fm = QFontMetrics(self.font())
        self.label_jobs = self._create_value_label(fm.horizontalAdvance("888/888 jobs"))
        self.label_bytes = self._create_value_label(fm.horizontalAdvance("8888.8/8888.8 MB"))

        self.label_separator = QLabel("|")
        self.label_separator.setStyleSheet("color: #666;")

        self.label_throttled = QLabel("")
        self.label_throttled.setStyleSheet("color: #e74c3c; font-weight: bold;")

        layout.addWidget(self.label_prefix)
        layout.addWidget(self.label_jobs)
        layout.addWidget(self.label_separator)
        layout.addWidget(self.label_bytes)
        layout.addWidget(self.label_throttled)

    def _create_value_label(self, width: int) -> QLabel:
        """Create a left-aligned value label with fixed width."""
        label = QLabel("--")
        label.setFixedWidth(width)
        label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        return label

    def _setup_timer(self):
        """Setup timer for periodic backpressure updates."""
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self._update_display)
        self._update_timer.setInterval(500)  # Update every 500ms

    def start_monitoring(self, controller: "BackpressureController") -> None:
        """Start monitoring backpressure stats.

        Args:
            controller: BackpressureController instance to monitor.
        """
        if controller is None:
            self._log.warning("start_monitoring called with None controller")
            return

        self._controller = controller
        self._throttle_sticky_counter = 0  # Reset state for clean start
        self._log.info("Starting backpressure monitoring")
        self._update_display()  # Initial update
        self._update_timer.start()

    def stop_monitoring(self) -> None:
        """Stop monitoring and reset display."""
        self._update_timer.stop()
        self._controller = None
        self._throttle_sticky_counter = 0
        self.label_jobs.setText("--")
        self.label_bytes.setText("--")
        self.label_throttled.setText("")

    def _update_display(self) -> None:
        """Update display with current backpressure stats."""
        if self._controller is None:
            return

        try:
            stats = self._controller.get_stats()

            self.label_jobs.setText(f"{stats.pending_jobs}/{stats.max_pending_jobs} jobs")
            self.label_bytes.setText(f"{stats.pending_bytes_mb:.1f}/{stats.max_pending_mb:.1f} MB")

            # Sticky throttle indicator: stays visible for THROTTLE_STICKY_CYCLES after release
            if stats.is_throttled:
                self._throttle_sticky_counter = self.THROTTLE_STICKY_CYCLES
                self.label_throttled.setText("[THROTTLED]")
            elif self._throttle_sticky_counter > 0:
                self._throttle_sticky_counter -= 1
                if self._throttle_sticky_counter == 0:
                    self.label_throttled.setText("")

        except (BrokenPipeError, EOFError) as e:
            # Multiprocessing communication ended - acquisition finished
            self._log.debug(f"Backpressure controller communication ended: {e}")
            self.stop_monitoring()
        except Exception as e:
            self._log.warning(f"Backpressure monitor update failed: {e}")
            self.stop_monitoring()

    def closeEvent(self, event):
        """Ensure monitoring resources are cleaned up when the widget closes."""
        try:
            self.stop_monitoring()
        except Exception as e:
            self._log.debug(f"Error stopping monitoring on close: {e}")

        super().closeEvent(event)


def _is_filter_wheel_enabled() -> bool:
    """Check if filter wheel is enabled in .ini configuration."""
    return getattr(control._def, "USE_EMISSION_FILTER_WHEEL", False)


def _populate_filter_positions_for_combo(
    combo: QComboBox,
    channel_wheel: Optional[str],
    config_repo,
    current_position: Optional[int] = None,
) -> None:
    """Populate filter position dropdown, auto-resolving wheel selection.

    Args:
        combo: The QComboBox to populate
        channel_wheel: Raw filter_wheel value from channel (None, "auto", or wheel name)
        config_repo: ConfigRepository instance
        current_position: Position to select (None for first position)
    """
    combo.clear()

    registry = config_repo.get_filter_wheel_registry()
    has_registry = registry and registry.filter_wheels

    # No filter wheel system at all
    if not has_registry and not _is_filter_wheel_enabled():
        combo.addItem("N/A", None)
        combo.setEnabled(False)
        return

    # Resolve wheel: explicit name, or auto-select first wheel
    wheel = None
    if channel_wheel and channel_wheel not in ("(None)", "auto"):
        # Explicit wheel name specified
        wheel = registry.get_wheel_by_name(channel_wheel) if registry else None
        if not wheel and registry:
            logger.warning(f"Filter wheel '{channel_wheel}' not found in registry")
    elif has_registry:
        # Auto-select first wheel (works for both single and multi-wheel systems)
        wheel = registry.get_first_wheel()

    if not wheel:
        # No wheel resolved - check if we should show default positions or N/A
        if has_registry or _is_filter_wheel_enabled():
            # Filter wheel enabled but no registry - show default positions
            combo.setEnabled(True)
            for pos in range(1, 9):
                combo.addItem(f"Position {pos}", pos)
        else:
            combo.addItem("N/A", None)
            combo.setEnabled(False)
            return
    else:
        # Populate from wheel's actual positions
        combo.setEnabled(True)
        for pos, filter_name in sorted(wheel.positions.items()):
            combo.addItem(f"{pos}: {filter_name}", pos)

    # Select current position, or default to first
    if current_position is not None:
        for i in range(combo.count()):
            if combo.itemData(i) == current_position:
                combo.setCurrentIndex(i)
                return
    combo.setCurrentIndex(0)


class AcquisitionChannelConfiguratorDialog(QDialog):
    """Dialog for editing acquisition channel configurations.

    Edits user_profiles/{profile}/channel_configs/general.yaml.
    Unlike IlluminationChannelConfiguratorDialog (hardware), this edits
    user-facing channel settings like enabled state, display color, camera,
    and filter wheel assignments.
    """

    signal_channels_updated = Signal()

    # Column indices for the channels table
    COL_ENABLED = 0
    COL_NAME = 1
    COL_ILLUMINATION = 2
    COL_CAMERA = 3
    COL_FILTER_WHEEL = 4
    COL_FILTER_POSITION = 5
    COL_DISPLAY_COLOR = 6

    def __init__(self, config_repo, parent=None):
        super().__init__(parent)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.config_repo = config_repo
        self.general_config = None
        self.illumination_config = None
        self.setWindowTitle("Acquisition Channel Configuration")
        self.setMinimumSize(700, 400)
        self._setup_ui()
        self._load_channels()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Info label
        info_label = QLabel(
            "Configure acquisition channels for the current profile. "
            "Changes affect how channels appear in the live view and acquisition panels."
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        # Table for acquisition channels
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["Enabled", "Name", "Illumination", "Camera", "Filter Wheel", "Filter", "Color"]
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_NAME, QHeaderView.Stretch)
        header.setSectionResizeMode(self.COL_DISPLAY_COLOR, QHeaderView.Fixed)
        self.table.setColumnWidth(self.COL_DISPLAY_COLOR, 60)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        layout.addWidget(self.table)

        # Buttons
        button_layout = QHBoxLayout()

        self.btn_add = QPushButton("Add Channel")
        self.btn_add.setAutoDefault(False)
        self.btn_add.setDefault(False)
        self.btn_add.clicked.connect(self._add_channel)
        button_layout.addWidget(self.btn_add)

        self.btn_remove = QPushButton("Remove Channel")
        self.btn_remove.setAutoDefault(False)
        self.btn_remove.setDefault(False)
        self.btn_remove.clicked.connect(self._remove_channel)
        button_layout.addWidget(self.btn_remove)

        self.btn_move_up = QPushButton("Move Up")
        self.btn_move_up.setAutoDefault(False)
        self.btn_move_up.clicked.connect(self._move_up)
        button_layout.addWidget(self.btn_move_up)

        self.btn_move_down = QPushButton("Move Down")
        self.btn_move_down.setAutoDefault(False)
        self.btn_move_down.clicked.connect(self._move_down)
        button_layout.addWidget(self.btn_move_down)

        button_layout.addSpacing(20)

        self.btn_export = QPushButton("Export...")
        self.btn_export.setAutoDefault(False)
        self.btn_export.clicked.connect(self._export_config)
        button_layout.addWidget(self.btn_export)

        self.btn_import = QPushButton("Import...")
        self.btn_import.setAutoDefault(False)
        self.btn_import.clicked.connect(self._import_config)
        button_layout.addWidget(self.btn_import)

        button_layout.addStretch()

        self.btn_save = QPushButton("Save")
        self.btn_save.setAutoDefault(False)
        self.btn_save.clicked.connect(self._save_changes)
        button_layout.addWidget(self.btn_save)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setAutoDefault(False)
        self.btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(self.btn_cancel)

        layout.addLayout(button_layout)

    def _set_buttons_enabled(self, enabled: bool):
        """Enable or disable action buttons based on config availability."""
        self.btn_add.setEnabled(enabled)
        self.btn_remove.setEnabled(enabled)
        self.btn_move_up.setEnabled(enabled)
        self.btn_move_down.setEnabled(enabled)
        self.btn_export.setEnabled(enabled)
        self.btn_save.setEnabled(enabled)
        # Import is always enabled since it can create a new config
        # Cancel is always enabled

    def _load_channels(self):
        """Load acquisition channels from general.yaml into the table."""
        self.general_config = self.config_repo.get_general_config()
        self.illumination_config = self.config_repo.get_illumination_config()

        if not self.general_config:
            self._log.warning("No general config found for current profile")
            QMessageBox.warning(
                self,
                "No Configuration",
                "No channel configuration found for the current profile.\n"
                "Please ensure a profile is selected and has been initialized.",
            )
            # Disable buttons when no config is loaded
            self._set_buttons_enabled(False)
            return

        # Enable buttons when config is loaded
        self._set_buttons_enabled(True)

        # Determine column visibility
        camera_names = self.config_repo.get_camera_names()
        wheel_names = self.config_repo.get_filter_wheel_names()
        has_any_wheel = wheel_names or _is_filter_wheel_enabled()

        # Hide Camera column if single camera (0 or 1)
        if len(camera_names) <= 1:
            self.table.setColumnHidden(self.COL_CAMERA, True)

        # Hide Filter Wheel column if single wheel (auto-assigned)
        if len(wheel_names) <= 1:
            self.table.setColumnHidden(self.COL_FILTER_WHEEL, True)

        # Hide Filter Position column only if NO wheels at all
        if not has_any_wheel:
            self.table.setColumnHidden(self.COL_FILTER_POSITION, True)

        self.table.setRowCount(len(self.general_config.channels))

        for row, channel in enumerate(self.general_config.channels):
            self._populate_row(row, channel)

    def _populate_row(self, row: int, channel):
        """Populate a table row with channel data."""
        from control.models import AcquisitionChannel

        # Enabled checkbox
        checkbox_widget = QWidget()
        checkbox_layout = QHBoxLayout(checkbox_widget)
        checkbox_layout.setContentsMargins(0, 0, 0, 0)
        checkbox_layout.setAlignment(Qt.AlignCenter)
        checkbox = QCheckBox()
        enabled = channel.enabled if hasattr(channel, "enabled") else True
        checkbox.setChecked(enabled)
        checkbox_layout.addWidget(checkbox)
        self.table.setCellWidget(row, self.COL_ENABLED, checkbox_widget)

        # Name (editable text)
        name_item = QTableWidgetItem(channel.name)
        self.table.setItem(row, self.COL_NAME, name_item)

        # Illumination dropdown
        illum_combo = QComboBox()
        if self.illumination_config:
            illum_names = [ch.name for ch in self.illumination_config.channels]
            illum_combo.addItems(illum_names)
            # Set current illumination
            current_illum = channel.illumination_settings.illumination_channel
            if current_illum and current_illum in illum_names:
                illum_combo.setCurrentText(current_illum)
        self.table.setCellWidget(row, self.COL_ILLUMINATION, illum_combo)

        # Camera dropdown
        camera_combo = QComboBox()
        camera_combo.addItem("(None)")
        camera_names = self.config_repo.get_camera_names()
        camera_combo.addItems(camera_names)
        if channel.camera and channel.camera in camera_names:
            camera_combo.setCurrentText(channel.camera)
        self.table.setCellWidget(row, self.COL_CAMERA, camera_combo)

        # Filter wheel dropdown
        wheel_combo = QComboBox()
        wheel_combo.addItem("(None)")
        wheel_names = self.config_repo.get_filter_wheel_names()
        wheel_combo.addItems(wheel_names)
        # Set selection if channel has explicit wheel name
        if channel.filter_wheel and channel.filter_wheel in wheel_names:
            wheel_combo.setCurrentText(channel.filter_wheel)
        wheel_combo.currentTextChanged.connect(lambda text, r=row: self._on_wheel_changed(r, text))
        self.table.setCellWidget(row, self.COL_FILTER_WHEEL, wheel_combo)

        # Filter position dropdown - function auto-resolves single-wheel systems
        position_combo = QComboBox()
        _populate_filter_positions_for_combo(
            position_combo, channel.filter_wheel, self.config_repo, channel.filter_position
        )
        self.table.setCellWidget(row, self.COL_FILTER_POSITION, position_combo)

        # Display color (color picker button - fills cell width)
        color = channel.display_color if hasattr(channel, "display_color") else "#FFFFFF"
        color_btn = QPushButton()
        color_btn.setStyleSheet(f"background-color: {color};")
        color_btn.setProperty("color", color)
        color_btn.clicked.connect(lambda _checked, r=row: self._pick_color(r))
        self.table.setCellWidget(row, self.COL_DISPLAY_COLOR, color_btn)

    def _on_wheel_changed(self, row: int, wheel_name: str):
        """Update filter position options when wheel selection changes."""
        position_combo = self.table.cellWidget(row, self.COL_FILTER_POSITION)
        if position_combo:
            _populate_filter_positions_for_combo(position_combo, wheel_name, self.config_repo)

    def _pick_color(self, row: int):
        """Open color picker for a row."""
        color_btn = self.table.cellWidget(row, self.COL_DISPLAY_COLOR)
        current_color = QColor(color_btn.property("color") if color_btn else "#FFFFFF")
        color = QColorDialog.getColor(current_color, self, "Select Display Color")
        if color.isValid():
            color_btn.setStyleSheet(f"background-color: {color.name()};")
            color_btn.setProperty("color", color.name())

    def _add_channel(self):
        """Add a new acquisition channel."""
        if self.general_config is None:
            QMessageBox.warning(self, "Error", "No configuration loaded. Cannot add channel.")
            return

        dialog = AddAcquisitionChannelDialog(self.config_repo, self)
        if dialog.exec_() == QDialog.Accepted:
            channel = dialog.get_channel()
            if channel:
                self.general_config.channels.append(channel)
                # Reload table
                self._load_channels()

    def _remove_channel(self):
        """Remove selected channel."""
        if self.general_config is None:
            return

        current_row = self.table.currentRow()
        if current_row < 0:
            return

        name_item = self.table.item(current_row, self.COL_NAME)
        if name_item:
            reply = QMessageBox.question(
                self,
                "Confirm Removal",
                f"Remove channel '{name_item.text()}'?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes and current_row < len(self.general_config.channels):
                del self.general_config.channels[current_row]
                self._load_channels()

    def _move_up(self):
        """Move selected channel up."""
        if self.general_config is None:
            return

        current_row = self.table.currentRow()
        if current_row <= 0:
            return

        channels = self.general_config.channels
        channels[current_row - 1], channels[current_row] = channels[current_row], channels[current_row - 1]
        self._load_channels()
        self.table.selectRow(current_row - 1)

    def _move_down(self):
        """Move selected channel down."""
        if self.general_config is None:
            return

        current_row = self.table.currentRow()
        if current_row < 0 or current_row >= len(self.general_config.channels) - 1:
            return

        channels = self.general_config.channels
        channels[current_row], channels[current_row + 1] = channels[current_row + 1], channels[current_row]
        self._load_channels()
        self.table.selectRow(current_row + 1)

    def _save_changes(self):
        """Save changes to general.yaml."""
        if self.general_config is None:
            QMessageBox.warning(self, "Error", "No configuration loaded. Cannot save.")
            return

        # Sync table data to config object
        self._sync_table_to_config()

        # Validate filter wheel/position consistency
        warnings = []
        for channel in self.general_config.channels:
            if channel.filter_wheel is not None and channel.filter_position is None:
                warnings.append(f"Channel '{channel.name}' has filter wheel but no position selected")
                self._log.warning(warnings[-1])

        if warnings:
            reply = QMessageBox.warning(
                self,
                "Configuration Warning",
                "Some channels have incomplete filter settings:\n\n" + "\n".join(warnings) + "\n\nSave anyway?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        # Save to YAML file
        try:
            self.config_repo.save_general_config(self.config_repo.current_profile, self.general_config)
        except (PermissionError, OSError) as e:
            self._log.error(f"Failed to save channel configuration: {e}")
            QMessageBox.critical(self, "Save Failed", f"Cannot write configuration file:\n{e}")
            return
        except Exception as e:
            self._log.error(f"Unexpected error saving channel configuration: {e}")
            QMessageBox.critical(self, "Save Failed", f"Failed to save configuration:\n{e}")
            return

        self.signal_channels_updated.emit()
        self.accept()

    def _export_config(self):
        """Export current channel configuration to a YAML file."""
        from control.models import GeneralChannelConfig
        import yaml

        # Get save file path
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Channel Configuration",
            "channel_config.yaml",
            "YAML Files (*.yaml *.yml);;All Files (*)",
        )
        if not file_path:
            return

        # Build current config from table (same logic as _save_changes but without saving)
        self._sync_table_to_config()

        if not self.general_config:
            QMessageBox.warning(self, "Export Failed", "No configuration loaded to export.")
            return

        # Export to YAML
        try:
            data = self.general_config.model_dump()
            with open(file_path, "w") as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=False)
            QMessageBox.information(self, "Export Successful", f"Configuration exported to:\n{file_path}")
        except (PermissionError, OSError) as e:
            self._log.warning(f"Failed to write export file {file_path}: {e}")
            QMessageBox.critical(self, "Export Failed", f"Cannot write to file:\n{e}")
        except Exception as e:
            self._log.error(f"Unexpected error during export: {e}")
            QMessageBox.critical(self, "Export Failed", f"Unexpected error:\n{e}")

    def _import_config(self):
        """Import channel configuration from a YAML file."""
        from pydantic import ValidationError
        from control.models import GeneralChannelConfig
        import yaml

        # Get file path
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Channel Configuration",
            "",
            "YAML Files (*.yaml *.yml);;All Files (*)",
        )
        if not file_path:
            return

        # Load and validate
        try:
            with open(file_path, "r") as f:
                data = yaml.safe_load(f)
            if data is None:
                raise ValueError("File is empty or contains no valid YAML content")
            imported_config = GeneralChannelConfig.model_validate(data)
        except (PermissionError, FileNotFoundError) as e:
            self._log.warning(f"Cannot read import file {file_path}: {e}")
            QMessageBox.critical(self, "Import Failed", f"Cannot read file:\n{e}")
            return
        except yaml.YAMLError as e:
            self._log.warning(f"Invalid YAML in {file_path}: {e}")
            QMessageBox.critical(self, "Import Failed", f"File contains invalid YAML:\n{e}")
            return
        except (ValidationError, ValueError) as e:
            self._log.warning(f"Config validation failed for {file_path}: {e}")
            QMessageBox.critical(self, "Import Failed", f"Configuration format error:\n{e}")
            return

        # Replace current config
        self.general_config = imported_config

        # Refresh the table
        self.table.setRowCount(0)
        self._load_channels()

        QMessageBox.information(
            self, "Import Successful", f"Imported {len(imported_config.channels)} channels from:\n{file_path}"
        )

    def _sync_table_to_config(self):
        """Sync table data back to self.general_config without saving to disk."""
        if self.general_config is None:
            return

        # Use bounds checking to handle potential table/config mismatch
        num_rows = min(self.table.rowCount(), len(self.general_config.channels))
        for row in range(num_rows):
            channel = self.general_config.channels[row]

            # Enabled
            checkbox_widget = self.table.cellWidget(row, self.COL_ENABLED)
            if checkbox_widget:
                checkbox = checkbox_widget.findChild(QCheckBox)
                if checkbox:
                    channel.enabled = checkbox.isChecked()

            # Name
            name_item = self.table.item(row, self.COL_NAME)
            if name_item:
                channel.name = name_item.text().strip()

            # Illumination
            illum_combo = self.table.cellWidget(row, self.COL_ILLUMINATION)
            if illum_combo and isinstance(illum_combo, QComboBox):
                channel.illumination_settings.illumination_channel = illum_combo.currentText()

            # Camera
            camera_combo = self.table.cellWidget(row, self.COL_CAMERA)
            if camera_combo and isinstance(camera_combo, QComboBox):
                camera_text = camera_combo.currentText()
                channel.camera = camera_text if camera_text != "(None)" else None

            # Filter wheel: None = no selection, else explicit wheel name
            wheel_combo = self.table.cellWidget(row, self.COL_FILTER_WHEEL)
            if wheel_combo and isinstance(wheel_combo, QComboBox):
                wheel_text = wheel_combo.currentText()
                channel.filter_wheel = wheel_text if wheel_text != "(None)" else None

            # Filter position
            position_combo = self.table.cellWidget(row, self.COL_FILTER_POSITION)
            if position_combo and isinstance(position_combo, QComboBox):
                channel.filter_position = position_combo.currentData()

            # Display color
            color_btn = self.table.cellWidget(row, self.COL_DISPLAY_COLOR)
            if color_btn:
                channel.display_color = color_btn.property("color") or "#FFFFFF"


class AddAcquisitionChannelDialog(QDialog):
    """Dialog for adding a new acquisition channel."""

    def __init__(self, config_repo, parent=None):
        super().__init__(parent)
        self.config_repo = config_repo
        self._display_color = "#FFFFFF"
        self.setWindowTitle("Add Acquisition Channel")
        self._setup_ui()

    def _setup_ui(self):
        layout = QFormLayout(self)

        # Name
        self.name_edit = QLineEdit()
        layout.addRow("Name:", self.name_edit)

        # Illumination source dropdown
        self.illumination_combo = QComboBox()
        illum_config = self.config_repo.get_illumination_config()
        if illum_config:
            self.illumination_combo.addItems([ch.name for ch in illum_config.channels])
        layout.addRow("Illumination:", self.illumination_combo)

        # Camera dropdown (hidden if single camera - 0 or 1 cameras)
        camera_names = self.config_repo.get_camera_names()
        if len(camera_names) > 1:
            self.camera_combo = QComboBox()
            self.camera_combo.addItem("(None)")
            self.camera_combo.addItems(camera_names)
            layout.addRow("Camera:", self.camera_combo)
        else:
            self.camera_combo = None

        # Filter wheel dropdown (hidden if single wheel - 0 or 1 wheels)
        wheel_names = self.config_repo.get_filter_wheel_names()
        has_any_wheel = wheel_names or _is_filter_wheel_enabled()

        # Show wheel dropdown only for multi-wheel systems
        if len(wheel_names) > 1:
            self.wheel_combo = QComboBox()
            self.wheel_combo.addItem("(None)")
            self.wheel_combo.addItems(wheel_names)
            self.wheel_combo.currentTextChanged.connect(self._on_wheel_changed)
            layout.addRow("Filter Wheel:", self.wheel_combo)
        else:
            self.wheel_combo = None

        # Filter position dropdown (shown if any filter wheels exist)
        if has_any_wheel:
            self.position_combo = QComboBox()
            # Populate positions - function auto-resolves single-wheel systems
            _populate_filter_positions_for_combo(self.position_combo, None, self.config_repo)
            layout.addRow("Filter Position:", self.position_combo)
        else:
            self.position_combo = None

        # Display color
        self.color_btn = QPushButton()
        self.color_btn.setFixedSize(60, 25)
        self.color_btn.setStyleSheet(f"background-color: {self._display_color}; border: 1px solid #888;")
        self.color_btn.clicked.connect(self._pick_color)
        layout.addRow("Display Color:", self.color_btn)

        # Buttons
        button_layout = QHBoxLayout()
        self.btn_ok = QPushButton("Add")
        self.btn_ok.clicked.connect(self._validate_and_accept)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(self.btn_ok)
        button_layout.addWidget(self.btn_cancel)
        layout.addRow(button_layout)

    def _on_wheel_changed(self, wheel_name: str):
        """Update filter position options when wheel selection changes."""
        if self.position_combo is not None:
            _populate_filter_positions_for_combo(self.position_combo, wheel_name, self.config_repo)

    def _pick_color(self):
        """Open color picker."""
        color = QColorDialog.getColor(QColor(self._display_color), self, "Select Display Color")
        if color.isValid():
            self._display_color = color.name()
            self.color_btn.setStyleSheet(f"background-color: {self._display_color}; border: 1px solid #888;")

    def _validate_and_accept(self):
        """Validate input before accepting."""
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Validation Error", "Channel name cannot be empty.")
            return

        # Check for duplicate names
        general_config = self.config_repo.get_general_config()
        if general_config:
            existing_names = [ch.name for ch in general_config.channels]
            if name in existing_names:
                QMessageBox.warning(self, "Validation Error", f"Channel '{name}' already exists.")
                return

        self.accept()

    def get_channel(self):
        """Build AcquisitionChannel from dialog inputs."""
        from control.models import (
            AcquisitionChannel,
            CameraSettings,
            IlluminationSettings,
        )

        name = self.name_edit.text().strip()
        illum_name = self.illumination_combo.currentText()

        # Camera
        camera = None
        if self.camera_combo:
            camera_text = self.camera_combo.currentText()
            camera = camera_text if camera_text != "(None)" else None

        # Filter wheel and position
        filter_wheel = None
        if self.wheel_combo:
            wheel_text = self.wheel_combo.currentText()
            filter_wheel = wheel_text if wheel_text != "(None)" else None
        filter_position = self.position_combo.currentData() if self.position_combo else None

        return AcquisitionChannel(
            name=name,
            enabled=True,
            display_color=self._display_color,
            camera=camera,
            filter_wheel=filter_wheel,
            filter_position=filter_position,
            illumination_settings=IlluminationSettings(
                illumination_channel=illum_name,
                intensity=20.0,
                z_offset_um=0.0,
            ),
            camera_settings=CameraSettings(
                exposure_time_ms=20.0,
                gain_mode=10.0,
                pixel_format=None,
            ),
        )


class FilterWheelConfiguratorDialog(QDialog):
    """Dialog for configuring filter wheel position names.

    Edits machine_configs/filter_wheels.yaml to define filter wheels
    and their position-to-name mappings.
    """

    signal_config_updated = Signal()

    def __init__(self, config_repo, parent=None):
        super().__init__(parent)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.config_repo = config_repo
        self.registry = None
        self.setWindowTitle("Filter Wheel Configuration")
        self.setMinimumSize(500, 400)
        self._setup_ui()
        self._load_config()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Instructions
        instructions = QLabel(
            "Configure filter wheel position names. Each position can have a descriptive name\n"
            "(e.g., 'DAPI emission', 'GFP emission') that will appear in channel configuration."
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions)

        # Wheel selector (hidden for single-wheel systems)
        self.wheel_layout = QHBoxLayout()
        self.wheel_label = QLabel("Filter Wheel:")
        self.wheel_layout.addWidget(self.wheel_label)
        self.wheel_combo = QComboBox()
        self.wheel_combo.currentIndexChanged.connect(self._on_wheel_selected)
        self.wheel_layout.addWidget(self.wheel_combo, 1)
        layout.addLayout(self.wheel_layout)

        # Positions table
        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Position", "Filter Name"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.table)

        # Save/Cancel buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.btn_save = QPushButton("Save")
        self.btn_save.clicked.connect(self._save_config)
        button_layout.addWidget(self.btn_save)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(self.btn_cancel)

        layout.addLayout(button_layout)

    def _load_config(self):
        """Load filter wheel registry from config."""
        from control.models.filter_wheel_config import FilterWheelRegistryConfig, FilterWheelDefinition, FilterWheelType

        self.registry = self.config_repo.get_filter_wheel_registry()

        # Check if filter wheel is enabled in .ini
        filter_wheel_enabled = getattr(control._def, "USE_EMISSION_FILTER_WHEEL", False)
        configured_indices = getattr(control._def, "EMISSION_FILTER_WHEEL_INDICES", [1])

        # If no registry exists but filter wheel is enabled, create one with wheels for all configured indices
        if self.registry is None:
            if filter_wheel_enabled:
                default_positions = {i: f"Position {i}" for i in range(1, 9)}
                wheels = []
                for wheel_id in configured_indices:
                    if len(configured_indices) == 1:
                        # Single wheel: no name/id needed
                        wheels.append(
                            FilterWheelDefinition(type=FilterWheelType.EMISSION, positions=default_positions.copy())
                        )
                    else:
                        # Multi-wheel: use id and name to distinguish
                        wheels.append(
                            FilterWheelDefinition(
                                id=wheel_id,
                                name=f"Wheel {wheel_id}",
                                type=FilterWheelType.EMISSION,
                                positions=default_positions.copy(),
                            )
                        )
                self.registry = FilterWheelRegistryConfig(filter_wheels=wheels)
            else:
                self.registry = FilterWheelRegistryConfig(filter_wheels=[])

        # Ensure registry has entries for all wheels configured in .ini
        # This handles the case where user updated .ini but didn't update filter_wheels.yaml
        if filter_wheel_enabled and len(configured_indices) > 1:
            existing_ids = {w.id for w in self.registry.filter_wheels if w.id is not None}
            default_positions = {i: f"Position {i}" for i in range(1, 9)}
            for wheel_id in configured_indices:
                if wheel_id not in existing_ids:
                    self._log.info(
                        f"Auto-creating filter wheel entry for wheel {wheel_id} (configured in .ini but missing in filter_wheels.yaml)"
                    )
                    self.registry.filter_wheels.append(
                        FilterWheelDefinition(
                            id=wheel_id,
                            name=f"Wheel {wheel_id}",
                            type=FilterWheelType.EMISSION,
                            positions=default_positions.copy(),
                        )
                    )

        # For single wheel systems: remove name if present (migrate from old "Emission" name)
        is_single_wheel = len(self.registry.filter_wheels) == 1
        if is_single_wheel:
            wheel = self.registry.filter_wheels[0]
            if wheel.name is not None or wheel.id is not None:
                self.registry.filter_wheels[0] = FilterWheelDefinition(type=wheel.type, positions=wheel.positions)

        # Hide wheel selector for single-wheel systems
        self.wheel_label.setVisible(not is_single_wheel)
        self.wheel_combo.setVisible(not is_single_wheel)

        # Populate wheel combo (for multi-wheel systems)
        self.wheel_combo.clear()
        for wheel in self.registry.filter_wheels:
            display_name = wheel.name or "(Unnamed)"
            self.wheel_combo.addItem(display_name, wheel)

        # Select first wheel and load its positions
        if self.wheel_combo.count() > 0:
            self.wheel_combo.setCurrentIndex(0)
            self._on_wheel_selected(0)
        else:
            self.table.setRowCount(0)

    def _on_wheel_selected(self, index):
        """Load positions for selected wheel into table."""
        self.table.setRowCount(0)

        if index < 0:
            return

        wheel = self.wheel_combo.itemData(index)
        if wheel is None:
            return

        # Populate table with positions
        for pos in sorted(wheel.positions.keys()):
            row = self.table.rowCount()
            self.table.insertRow(row)

            # Position number (read-only)
            pos_item = QTableWidgetItem(str(pos))
            pos_item.setFlags(pos_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 0, pos_item)

            # Filter name (editable)
            name_item = QTableWidgetItem(wheel.positions[pos])
            self.table.setItem(row, 1, name_item)

    def _save_config(self):
        """Save filter wheel configuration to YAML file."""
        import yaml

        # Sync table data back to current wheel
        index = self.wheel_combo.currentIndex()
        if index >= 0:
            wheel = self.wheel_combo.itemData(index)
            if wheel:
                wheel.positions.clear()
                for row in range(self.table.rowCount()):
                    pos_item = self.table.item(row, 0)
                    name_item = self.table.item(row, 1)
                    if pos_item and name_item:
                        pos = int(pos_item.text())
                        name = name_item.text().strip() or f"Position {pos}"
                        wheel.positions[pos] = name

        # Save to file using repository (ensures consistent serialization)
        try:
            self.config_repo.save_filter_wheel_registry(self.registry)
            self.signal_config_updated.emit()
            QMessageBox.information(self, "Saved", "Filter wheel configuration saved.")
            self.accept()
        except (PermissionError, OSError) as e:
            self._log.error(f"Failed to save filter wheel config: {e}")
            QMessageBox.critical(self, "Error", f"Cannot write configuration file:\n{e}")
        except yaml.YAMLError as e:
            self._log.error(f"Failed to serialize filter wheel config: {e}")
            QMessageBox.critical(self, "Error", f"Configuration data could not be serialized:\n{e}")
        except Exception as e:
            self._log.exception(f"Unexpected error saving filter wheel config: {e}")
            QMessageBox.critical(self, "Error", f"Failed to save configuration:\n{e}")


class _QtLogSignalHolder(QObject):
    """QObject that holds the signal for QtLoggingHandler.

    Defined at module level to avoid dynamic class creation.
    """

    message_logged = Signal(int, str, str)  # level, logger_name, message


class QtLoggingHandler(logging.Handler):
    """Logging handler that emits Qt signals for WARNING+ messages.

    Thread-safe: Qt signal system handles cross-thread delivery automatically.
    Used by WarningErrorWidget to display warnings/errors in the status bar.
    """

    def __init__(self, min_level: int = logging.WARNING):
        super().__init__()
        self.setLevel(min_level)
        self._signal_holder = _QtLogSignalHolder()
        self.setFormatter(logging.Formatter(fmt=squid.logging.LOG_FORMAT, datefmt=squid.logging.LOG_DATEFORMAT))
        # Intentionally reuse the private thread_id filter from squid.logging for consistent
        # formatting across all log handlers. This creates a controlled dependency on
        # squid.logging's internal API.
        self.addFilter(squid.logging._thread_id_filter)

    @property
    def signal_message_logged(self):
        return self._signal_holder.message_logged

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            self._signal_holder.message_logged.emit(record.levelno, record.name, msg)
        except Exception:
            self.handleError(record)


class WarningErrorWidget(QWidget):
    """Status bar widget displaying logged warnings and errors.

    Features:
    - Color-coded: yellow for warnings, red for errors
    - Shows timestamp for each message
    - Expandable popup showing all messages when multiple exist
    - Deduplication: repeated identical messages show count instead of duplicates
    - Rate limiting: max 10 messages per second to prevent GUI freeze
    """

    MAX_MESSAGES = 100  # Prevent unbounded memory growth
    RATE_LIMIT_WINDOW_MS = 1000  # 1 second window
    RATE_LIMIT_MAX_MESSAGES = 10  # Max messages per window

    def __init__(self, parent=None):
        super().__init__(parent)
        # List of dicts with keys: id, level, logger_name, message, count, datetime
        self._messages = []
        self._next_message_id = 0
        self._rate_limit_timestamps = []  # For rate limiting
        self._dropped_count = 0  # Track rate-limited messages
        self._popup = None
        self._setup_ui()

    def closeEvent(self, event):
        """Clean up popup when widget is closed."""
        self._cleanup_popup()
        super().closeEvent(event)

    def _cleanup_popup(self):
        """Safely clean up popup if it exists."""
        if self._popup is not None:
            try:
                self._popup.hide()
                self._popup.deleteLater()
            except RuntimeError:
                # Popup may already be deleted
                pass
            self._popup = None

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(6)

        # Level icon (warning/error indicator) - circular badge
        self.label_icon = QLabel()
        self.label_icon.setFixedSize(20, 20)
        self.label_icon.setAlignment(Qt.AlignCenter)

        # Message text
        self.label_text = QLabel()

        # Expand button (shows when multiple messages or dropped messages)
        self.btn_expand = QPushButton()
        self.btn_expand.setFixedHeight(18)
        self.btn_expand.setMinimumWidth(32)  # Allow width to grow for longer text
        self.btn_expand.setCursor(Qt.PointingHandCursor)
        self.btn_expand.setStyleSheet(
            "QPushButton { background-color: #666; color: white; border-radius: 9px; "
            "font-size: 11px; font-weight: bold; padding: 0px 6px; }"
            "QPushButton:hover { background-color: #444; }"
            "QPushButton:pressed { background-color: #222; }"
        )
        self.btn_expand.clicked.connect(self._on_expand_clicked)
        self.btn_expand.setVisible(False)

        # Dismiss button (X)
        self.btn_dismiss = QPushButton("✕")
        self.btn_dismiss.setFixedSize(18, 18)
        self.btn_dismiss.setCursor(Qt.PointingHandCursor)
        self.btn_dismiss.setStyleSheet(
            "QPushButton { background: transparent; border: none; color: #888; font-size: 14px; padding: 0px; }"
            "QPushButton:hover { color: #000; }"
        )
        self.btn_dismiss.clicked.connect(self.dismiss_current)

        layout.addWidget(self.label_icon)
        layout.addWidget(self.label_text)
        layout.addWidget(self.btn_expand)
        layout.addWidget(self.btn_dismiss)
        layout.addStretch()  # Push everything to the left

    def _on_expand_clicked(self):
        """Handle expand button click."""
        self._toggle_popup()

    def _toggle_popup(self):
        """Toggle the popup showing all messages."""
        if self._popup is not None and self._popup.isVisible():
            self._cleanup_popup()
            return
        self._show_popup()

    def _show_popup(self):
        """Show popup with scrollable list of all messages."""
        # Recreate popup each time to ensure fresh state
        self._cleanup_popup()

        self._popup = QFrame(self.window(), Qt.Popup | Qt.FramelessWindowHint)
        self._popup.setStyleSheet("QFrame { background-color: white; border: 1px solid #aaa; border-radius: 6px; }")

        popup_layout = QVBoxLayout(self._popup)
        popup_layout.setContentsMargins(0, 0, 0, 0)
        popup_layout.setSpacing(0)

        # Header with title and Clear All button
        header = QWidget()
        header.setStyleSheet("background-color: #f5f5f5; border-bottom: 1px solid #ddd;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 8, 12, 8)
        header_label = QLabel(f"<b>Warnings & Errors</b> ({len(self._messages)})")
        btn_clear = QPushButton("Clear All")
        btn_clear.setCursor(Qt.PointingHandCursor)
        btn_clear.setStyleSheet(
            "QPushButton { background-color: #e74c3c; color: white; border: none; "
            "border-radius: 4px; padding: 4px 12px; font-weight: bold; }"
            "QPushButton:hover { background-color: #c0392b; }"
        )
        btn_clear.clicked.connect(self._clear_all_from_popup)
        header_layout.addWidget(header_label)
        header_layout.addStretch()
        header_layout.addWidget(btn_clear)
        popup_layout.addWidget(header)

        # Scrollable list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet("QScrollArea { border: none; background: white; }")

        list_widget = QWidget()
        list_widget.setStyleSheet("background: white;")
        list_layout = QVBoxLayout(list_widget)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(0)

        # Add messages (newest first) - use message ID for dismiss callback
        for msg in reversed(self._messages):
            item_widget = self._create_popup_item(msg)
            list_layout.addWidget(item_widget)

        list_layout.addStretch()
        scroll.setWidget(list_widget)
        popup_layout.addWidget(scroll)

        # Size and position
        self._popup.setFixedWidth(550)
        self._popup.setMinimumHeight(100)
        self._popup.setMaximumHeight(350)

        # Position above this widget (popup appears above status bar)
        # with bounds checking to stay on screen
        global_pos = self.mapToGlobal(QPoint(0, 0))
        popup_height = min(350, 50 + len(self._messages) * 60)
        self._popup.setFixedHeight(popup_height)

        # Calculate position, ensuring popup stays on screen
        popup_x = global_pos.x()
        popup_y = global_pos.y() - popup_height - 5

        # Get available screen geometry
        from qtpy.QtWidgets import QApplication

        screen = QApplication.screenAt(global_pos)
        if screen is not None:
            screen_geo = screen.availableGeometry()
            # Ensure popup doesn't go above screen top
            if popup_y < screen_geo.top():
                # Show below the widget instead
                popup_y = global_pos.y() + self.height() + 5
            # Ensure popup doesn't go off right edge (and not past left edge on narrow screens)
            if popup_x + 550 > screen_geo.right():
                popup_x = max(screen_geo.left(), screen_geo.right() - 550)

        self._popup.move(popup_x, popup_y)
        self._popup.show()

    def _create_popup_item(self, msg: dict) -> QWidget:
        """Create a single item widget for the popup list."""
        level = msg["level"]
        message = msg["message"]
        count = msg["count"]
        dt = msg["datetime"]
        msg_id = msg["id"]

        item = QWidget()
        is_error = level >= logging.ERROR
        bg_color = "#fef2f2" if is_error else "#fefce8"
        item.setStyleSheet(f"background-color: {bg_color}; border-bottom: 1px solid #eee;")

        layout = QHBoxLayout(item)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        # Level indicator
        icon_label = QLabel("⬤")
        icon_color = "#dc2626" if is_error else "#ca8a04"
        icon_label.setStyleSheet(f"color: {icon_color}; font-size: 8px;")
        icon_label.setFixedWidth(14)
        icon_label.setAlignment(Qt.AlignCenter)

        # Date/Time - show full date and time
        time_str = dt.strftime("%m/%d %H:%M:%S")
        time_label = QLabel(time_str)
        time_label.setStyleSheet("color: #666; font-size: 11px; font-family: monospace;")
        time_label.setFixedWidth(90)

        # Message (allow wrapping)
        core_msg = self._extract_core_message(message)
        if count > 1:
            core_msg = f"{core_msg} <b style='color: #666;'>(×{count})</b>"
        msg_label = QLabel(core_msg)
        msg_label.setWordWrap(True)
        msg_label.setStyleSheet("font-size: 12px; color: #333;")
        msg_label.setTextFormat(Qt.RichText)

        # Dismiss button - use message ID for stable reference
        btn_dismiss = QPushButton("✕")
        btn_dismiss.setFixedSize(20, 20)
        btn_dismiss.setCursor(Qt.PointingHandCursor)
        btn_dismiss.setStyleSheet(
            "QPushButton { background: #ddd; border: none; color: #666; border-radius: 10px; font-size: 12px; }"
            "QPushButton:hover { background: #ccc; color: #333; }"
        )
        btn_dismiss.clicked.connect(lambda checked, mid=msg_id: self._dismiss_by_id(mid))

        layout.addWidget(icon_label, 0, Qt.AlignTop)
        layout.addWidget(time_label, 0, Qt.AlignTop)
        layout.addWidget(msg_label, 1)
        layout.addWidget(btn_dismiss, 0, Qt.AlignTop)

        return item

    def _dismiss_by_id(self, msg_id: int):
        """Dismiss a message by its unique ID."""
        for i, msg in enumerate(self._messages):
            if msg["id"] == msg_id:
                self._messages.pop(i)
                self._update_display()
                if self._popup is not None:
                    if self._messages:
                        # Refresh popup with updated list
                        self._cleanup_popup()
                        self._show_popup()
                    else:
                        self._cleanup_popup()
                return

    def _clear_all_from_popup(self):
        """Clear all messages and close popup."""
        self.clear_all()
        self._cleanup_popup()

    def add_message(self, level: int, logger_name: str, message: str):
        """Add a new warning/error message to the queue."""
        # Rate limiting - but never rate-limit ERROR or higher (they're too important to drop)
        now_ms = time.time() * 1000
        cutoff = now_ms - self.RATE_LIMIT_WINDOW_MS
        self._rate_limit_timestamps = [t for t in self._rate_limit_timestamps if t > cutoff]

        if level < logging.ERROR and len(self._rate_limit_timestamps) >= self.RATE_LIMIT_MAX_MESSAGES:
            self._dropped_count += 1
            self._update_display()  # Update to show dropped count
            return  # Rate limited

        # Extract datetime from message or use current time
        dt = self._extract_datetime(message)

        # Deduplication - check if identical message already exists
        # Note: duplicates don't consume rate limit slots since they don't create new entries
        core_msg = self._extract_core_message(message)
        for i, msg in enumerate(self._messages):
            if self._extract_core_message(msg["message"]) == core_msg and msg["level"] == level:
                # Update with new datetime and increment count
                msg["datetime"] = dt
                msg["count"] += 1
                msg["message"] = message  # Update to latest message text
                self._messages.append(self._messages.pop(i))  # Move to end
                self._update_display()
                return

        # New message - consume rate limit slot and assign unique ID
        self._rate_limit_timestamps.append(now_ms)
        if len(self._messages) >= self.MAX_MESSAGES:
            self._messages.pop(0)

        new_msg = {
            "id": self._next_message_id,
            "level": level,
            "logger_name": logger_name,
            "message": message,
            "count": 1,
            "datetime": dt,
        }
        self._next_message_id += 1
        self._messages.append(new_msg)
        self._update_display()

    def dismiss_current(self):
        """Dismiss the most recent message."""
        if self._messages:
            self._messages.pop()
            self._update_display()

    def clear_all(self):
        """Clear all messages and reset dropped count."""
        self._messages.clear()
        self._dropped_count = 0
        self._update_display()

    def get_dropped_count(self) -> int:
        """Return the number of messages dropped due to rate limiting."""
        return self._dropped_count

    def has_messages(self) -> bool:
        """Return True if there are pending messages."""
        return len(self._messages) > 0

    def _update_display(self):
        """Update the main widget display."""
        if not self._messages:
            self.setVisible(False)
            return

        self.setVisible(True)
        msg = self._messages[-1]
        level = msg["level"]
        message = msg["message"]
        count = msg["count"]
        dt = msg["datetime"]
        is_error = level >= logging.ERROR

        # Colors
        if is_error:
            bg_color = "#fef2f2"
            text_color = "#b91c1c"
            icon_text = "✕"
            icon_style = (
                "background-color: #dc2626; color: white; font-weight: bold; font-size: 12px; border-radius: 10px;"
            )
        else:
            bg_color = "#fefce8"
            text_color = "#a16207"
            icon_text = "!"
            icon_style = (
                "background-color: #eab308; color: white; font-weight: bold; font-size: 14px; border-radius: 10px;"
            )

        self.setStyleSheet(f"background-color: {bg_color}; border-radius: 4px;")
        self.label_icon.setText(icon_text)
        self.label_icon.setStyleSheet(icon_style)

        # Format message with compact time (HH:MM only)
        time_str = dt.strftime("%H:%M")
        display_msg = self._format_display_message(message)
        if count > 1:
            display_msg = f"[{time_str}] {display_msg} (×{count})"
        else:
            display_msg = f"[{time_str}] {display_msg}"
        self.label_text.setText(display_msg)
        self.label_text.setStyleSheet(f"color: {text_color}; font-weight: bold;")

        # Tooltip shows full message with date and dropped count if any
        full_time = dt.strftime("%Y-%m-%d %H:%M:%S")
        tooltip = f"{full_time}\n{self._extract_core_message(message)}"
        if self._dropped_count > 0:
            tooltip += f"\n\n⚠ {self._dropped_count} message(s) dropped due to rate limiting"
        self.setToolTip(tooltip)

        # Show expand button if multiple messages or dropped messages
        msg_count = len(self._messages)
        if msg_count > 1 or self._dropped_count > 0:
            if self._dropped_count > 0:
                # Show both additional messages and dropped count
                extra = msg_count - 1
                if extra > 0:
                    self.btn_expand.setText(f"+{extra} ({self._dropped_count}⚠)")
                else:
                    self.btn_expand.setText(f"({self._dropped_count}⚠)")
            else:
                self.btn_expand.setText(f"+{msg_count - 1}")
            self.btn_expand.setVisible(True)
        else:
            self.btn_expand.setVisible(False)

    def _extract_datetime(self, message: str) -> datetime:
        """Extract datetime from log message."""
        # Format: "2026-01-22 23:44:23.123 - ..."
        try:
            if " - " in message:
                datetime_part = message.split(" - ")[0]
                # Parse "2026-01-22 23:44:23.123"
                if "." in datetime_part:
                    datetime_part = datetime_part.rsplit(".", 1)[0]
                return datetime.strptime(datetime_part, "%Y-%m-%d %H:%M:%S")
        except (ValueError, IndexError):
            # Timestamp is optional - fall back to current time if parsing fails
            pass
        return datetime.now()

    # Pattern to match file location suffix like " (widgets.py:123)"
    _FILE_LOCATION_PATTERN = re.compile(r" \([^)]+:\d+\)$")

    def _extract_core_message(self, message: str) -> str:
        """Extract core message content (without timestamp/thread/location)."""
        for marker in [" - WARNING - ", " - ERROR - ", " - CRITICAL - "]:
            if marker in message:
                parts = message.split(marker, 1)
                if len(parts) > 1:
                    msg = parts[1]
                    # Strip file location suffix like " (widgets.py:123)" but not arbitrary parentheses
                    msg = self._FILE_LOCATION_PATTERN.sub("", msg)
                    return msg
        return message

    def _format_display_message(self, message: str) -> str:
        """Format message for single-line display."""
        msg = self._extract_core_message(message)
        if len(msg) > 60:
            msg = msg[:57] + "..."
        return msg
