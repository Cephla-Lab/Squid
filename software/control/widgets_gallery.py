"""Gallery tab: the acquisitions saved under the saving directory, newest first, each opened in a click.

An acquisition is the folder MultiPointController.start_new_experiment creates, <base_path>/<experiment_ID>,
holding acquisition.yaml and "acquisition parameters.json" beside one numbered folder per timepoint (or a zarr
store). The gallery lists those folders with a thumbnail (the saved mosaic overview when there is one, else
the first image) and a summary read from the metadata files. It never opens a dataset itself: it emits the
chosen folder, and the GUI routes it to the NDViewer tab, to the file browser, or, for its settings, to the
multipoint tab the acquisition was run from.

Directory scans and thumbnail decoding run on worker threads; results reach the widget through Qt signals,
tagged with a generation so a refresh started later always wins.
"""

import csv
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np
import tifffile
import yaml

from qtpy.QtCore import QObject, QSize, Qt, QUrl, Signal
from qtpy.QtGui import QColor, QDesktopServices, QFont, QIcon, QImage, QPainter, QPixmap
from qtpy.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

import squid.logging
from control.gallery_previews import array_to_pixmap, load_thumbnail_array

# Any one of these marks a folder as a saved acquisition (older versions wrote only the last two).
METADATA_FILES = ("acquisition.yaml", "acquisition parameters.json", "acquisition_channels.yaml", "configurations.xml")
IMAGE_EXTENSIONS = (".tiff", ".tif", ".bmp", ".png", ".jpg", ".jpeg")
NO_IMAGES = "No images"

# MultiPointController names an experiment folder "<name>_YYYY-MM-DD_HH-MM-SS.ffffff".
_EXPERIMENT_NAME = re.compile(
    r"^(?P<label>.*?)_?(?P<stamp>\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})(?P<fraction>\.\d+)?$",
)


@dataclass
class AcquisitionEntry:
    """What the gallery knows about one saved acquisition folder."""

    path: str
    name: str  # the folder name
    label: str  # the experiment name the user typed, without the timestamp suffix
    started: Optional[datetime] = None
    modified: float = 0.0  # folder mtime, the sort key when no start time is known
    widget_type: Optional[str] = None  # "wellplate" or "flexible"
    objective: Optional[str] = None
    magnification: Optional[float] = None
    wellplate_format: Optional[str] = None
    channels: List[str] = field(default_factory=list)
    nz: Optional[int] = None
    nt: Optional[int] = None
    num_regions: Optional[int] = None
    num_fovs: Optional[int] = None
    image_format: str = NO_IMAGES
    thumbnail_source: Optional[str] = None  # an image file to make the thumbnail from
    has_settings: bool = False  # acquisition.yaml exists, so the settings can be loaded again

    @property
    def sort_time(self) -> float:
        return self.started.timestamp() if self.started else self.modified

    def summary_lines(self) -> List[str]:
        """Short facts for the tooltip and the details pane, only the ones known."""
        lines = []
        if self.started:
            lines.append(f"Started: {self.started:%Y-%m-%d %H:%M:%S}")
        if self.objective:
            mag = f" ({self.magnification:g}x)" if self.magnification else ""
            lines.append(f"Objective: {self.objective}{mag}")
        if self.wellplate_format:
            lines.append(f"Sample: {self.wellplate_format}")
        if self.widget_type:
            lines.append(f"Mode: {self.widget_type} multipoint")
        if self.channels:
            lines.append(f"Channels ({len(self.channels)}): " + ", ".join(self.channels))
        dims = []
        if self.num_regions is not None:
            dims.append(f"{self.num_regions} region{'s' if self.num_regions != 1 else ''}")
        if self.num_fovs is not None:
            dims.append(f"{self.num_fovs} FOV{'s' if self.num_fovs != 1 else ''}")
        if self.nz is not None:
            dims.append(f"Nz={self.nz}")
        if self.nt is not None:
            dims.append(f"Nt={self.nt}")
        if dims:
            lines.append("Scan: " + ", ".join(dims))
        lines.append(f"Format: {self.image_format}")
        return lines


# ---------------------------------------------------------------------------------------------------------
# Reading acquisition folders (pure functions, no Qt)
# ---------------------------------------------------------------------------------------------------------


def split_experiment_name(folder_name: str) -> Tuple[str, Optional[datetime]]:
    """The experiment label and start time encoded in a folder name; the whole name and None otherwise."""
    m = _EXPERIMENT_NAME.match(folder_name)
    if not m:
        return folder_name, None
    try:
        started = datetime.strptime(m.group("stamp"), "%Y-%m-%d_%H-%M-%S")
    except ValueError:
        return folder_name, None
    fraction = m.group("fraction")
    if fraction:
        try:
            started = started.replace(microsecond=int(round(float(fraction) * 1_000_000)))
        except ValueError:
            pass
    return m.group("label") or folder_name, started


def is_acquisition_folder(path: str) -> bool:
    return os.path.isdir(path) and any(os.path.isfile(os.path.join(path, name)) for name in METADATA_FILES)


def _load_yaml(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _load_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _as_int(value) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_float(value) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _channel_names(channels) -> List[str]:
    if not isinstance(channels, list):
        return []
    return [str(ch["name"]) for ch in channels if isinstance(ch, dict) and ch.get("name")]


def _count_csv_rows(path: str) -> Optional[int]:
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            rows = sum(1 for row in csv.reader(f) if row)
    except (OSError, UnicodeDecodeError, csv.Error):
        return None
    return max(rows - 1, 0)  # minus the header


def _timepoint_folders(path: str) -> List[str]:
    """The numbered per-timepoint folders, in acquisition order."""
    try:
        with os.scandir(path) as it:
            names = [d.name for d in it if d.is_dir(follow_symlinks=False) and d.name.isdigit()]
    except OSError:
        return []
    return [os.path.join(path, name) for name in sorted(names, key=int)]


def _first_image_file(folder: str) -> Optional[str]:
    """The first image file in a folder, by name; None when there is none. Reads only one directory."""
    try:
        with os.scandir(folder) as it:
            names = [e.name for e in it if e.is_file() and e.name.lower().endswith(IMAGE_EXTENSIONS)]
    except OSError:
        return None
    return os.path.join(folder, min(names)) if names else None


def _overview_file(timepoint_folder: str) -> Optional[str]:
    """The mosaic overview UnifiedMosaicWidget saves under <timepoint>/mosaic_view, if any."""
    mosaic_dir = os.path.join(timepoint_folder, "mosaic_view")
    try:
        with os.scandir(mosaic_dir) as it:
            names = [
                e.name
                for e in it
                if e.is_file() and e.name.startswith("mosaic_") and e.name.lower().endswith((".ome.tiff", ".ome.tif"))
            ]
    except OSError:
        return None
    return os.path.join(mosaic_dir, min(names)) if names else None


def _has_zarr_store(path: str) -> bool:
    if any(os.path.isdir(os.path.join(path, name)) for name in ("plate.ome.zarr", "plate.zarr")):
        return True
    zarr_dir = os.path.join(path, "zarr")
    try:
        with os.scandir(zarr_dir) as regions:
            for region in regions:
                if not region.is_dir(follow_symlinks=False):
                    continue
                with os.scandir(region.path) as stores:
                    if any(s.is_dir() and s.name.endswith(".zarr") for s in stores):
                        return True
    except OSError:
        pass
    return False


def inspect_images(path: str) -> Tuple[str, Optional[str]]:
    """How an acquisition's images are stored and which file to draw its thumbnail from.

    The thumbnail comes from the saved mosaic overview when there is one, else the first image of the first
    timepoint. Zarr stores get no thumbnail unless an overview was saved.
    """
    timepoints = _timepoint_folders(path)
    overview = next((f for f in (_overview_file(tp) for tp in timepoints) if f), None)

    if _has_zarr_store(path):
        return "OME-Zarr", overview

    ome_dir = os.path.join(path, "ome_tiff")
    ome_file = _first_image_file(ome_dir) if os.path.isdir(ome_dir) else None
    if ome_file and ".ome." in os.path.basename(ome_file).lower():
        return "OME-TIFF", overview or ome_file

    for tp in timepoints:
        image = _first_image_file(tp)
        if image is None:
            continue
        name = os.path.basename(image).lower()
        if ".ome." in name:
            return "OME-TIFF", overview or image
        if name.endswith("_stack.tiff") or name.endswith("_stack.tif"):
            return "Multi-page TIFF", overview or image
        ext = os.path.splitext(name)[1].lstrip(".").upper()
        return f"{ext} images", overview or image

    return NO_IMAGES, overview


def read_acquisition_entry(path: str) -> AcquisitionEntry:
    """Everything the gallery shows for one acquisition folder, from whichever metadata files it has.

    Each file is optional and read leniently: a folder with a broken acquisition.yaml still lists, with less
    said about it.
    """
    path = os.path.normpath(path)
    name = os.path.basename(path)
    label, started = split_experiment_name(name)
    entry = AcquisitionEntry(path=path, name=name, label=label, started=started)
    try:
        entry.modified = os.stat(path).st_mtime
    except OSError:
        pass

    yaml_path = os.path.join(path, "acquisition.yaml")
    entry.has_settings = os.path.isfile(yaml_path)
    data = _load_yaml(yaml_path) if entry.has_settings else None
    if data:
        acq = data.get("acquisition") or {}
        entry.widget_type = acq.get("widget_type") or None
        start_time = _as_float(acq.get("start_time"))
        if start_time and start_time > 0:
            try:
                entry.started = datetime.fromtimestamp(start_time)
            except (OverflowError, OSError, ValueError):
                pass
        objective = data.get("objective") or {}
        entry.objective = objective.get("name") or None
        entry.magnification = _as_float(objective.get("magnification"))
        entry.wellplate_format = (data.get("sample") or {}).get("wellplate_format") or None
        entry.nz = _as_int((data.get("z_stack") or {}).get("nz"))
        entry.nt = _as_int((data.get("time_series") or {}).get("nt"))
        entry.channels = _channel_names(data.get("channels"))
        regions = (data.get("wellplate_scan") or {}).get("regions") or (data.get("flexible_scan") or {}).get(
            "positions"
        )
        if isinstance(regions, list) and regions:
            entry.num_regions = len(regions)
            fovs = sum(len(r.get("fovs") or []) for r in regions if isinstance(r, dict))
            if fovs:
                entry.num_fovs = fovs

    params = _load_json(os.path.join(path, "acquisition parameters.json"))
    if params:
        if entry.nz is None:
            entry.nz = _as_int(params.get("Nz"))
        if entry.nt is None:
            entry.nt = _as_int(params.get("Nt"))
        objective = params.get("objective") or {}
        if isinstance(objective, dict):
            if entry.objective is None:
                entry.objective = objective.get("name") or None
            if entry.magnification is None:
                entry.magnification = _as_float(objective.get("magnification"))

    if not entry.channels or entry.objective is None:
        channels_data = _load_yaml(os.path.join(path, "acquisition_channels.yaml"))
        if channels_data:
            if not entry.channels:
                entry.channels = _channel_names(channels_data.get("channels"))
            if entry.objective is None:
                entry.objective = channels_data.get("objective") or None

    if entry.num_fovs is None:
        rows = _count_csv_rows(os.path.join(path, "coordinates.csv"))
        if rows:
            entry.num_fovs = rows

    entry.image_format, entry.thumbnail_source = inspect_images(path)
    return entry


def scan_acquisitions(root: str) -> List[AcquisitionEntry]:
    """The acquisition folders directly under root, newest first. An unreadable root gives an empty list."""
    entries = []
    try:
        with os.scandir(root) as it:
            for d in it:
                try:
                    if not d.is_dir(follow_symlinks=False) or d.name.startswith("."):
                        continue
                except OSError:
                    continue
                if is_acquisition_folder(d.path):
                    entries.append(read_acquisition_entry(d.path))
    except OSError:
        return []
    entries.sort(key=lambda e: e.sort_time, reverse=True)
    return entries


# ---------------------------------------------------------------------------------------------------------
# Thumbnails
# ---------------------------------------------------------------------------------------------------------


def square_tile(pixmap: QPixmap, size: int) -> QPixmap:
    """The pixmap centred on a size x size tile, so every gallery item has the same footprint."""
    tile = QPixmap(size, size)
    tile.fill(QColor(60, 60, 60))
    painter = QPainter(tile)
    try:
        painter.drawPixmap((size - pixmap.width()) // 2, (size - pixmap.height()) // 2, pixmap)
    finally:
        painter.end()
    return tile


def make_placeholder_pixmap(text: str, size: int) -> QPixmap:
    """A neutral tile carrying a short text, for entries without a loadable image."""
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(60, 60, 60))
    painter = QPainter(pixmap)
    try:
        painter.setPen(QColor(190, 190, 190))
        font = QFont()
        font.setPointSize(10)
        painter.setFont(font)
        painter.drawText(pixmap.rect().adjusted(6, 6, -6, -6), Qt.AlignCenter | Qt.TextWordWrap, text)
    finally:
        painter.end()
    return pixmap


# ---------------------------------------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------------------------------------


class _GalleryEvents(QObject):
    """Signals the worker threads use to hand results to the GUI thread."""

    scanned = Signal(int, str, object)  # generation, root, List[AcquisitionEntry]
    thumbnail_ready = Signal(int, str, object)  # generation, acquisition path, Optional[np.ndarray]


class AcquisitionGalleryWidget(QWidget):
    """The Gallery tab: saved acquisitions under the saving directory, with thumbnails, to open or reuse.

    default_root_provider gives the folder acquisitions are currently saved under; the gallery follows it
    until the user browses to another folder, and "Saving folder" brings it back.
    """

    signal_open_acquisition = Signal(str)  # an acquisition folder to show in the viewer
    signal_load_settings = Signal(str)  # an acquisition folder whose acquisition.yaml to load

    THUMBNAIL_SIZE = 180
    _ROLE_PATH = Qt.UserRole

    def __init__(self, default_root_provider: Callable[[], Optional[str]], parent=None):
        super().__init__(parent)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._default_root_provider = default_root_provider
        self._root: Optional[str] = None
        self._root_pinned = False  # True once the user browsed away from the saving folder
        self._generation = 0
        self._scanned_once = False
        self._viewer_available = True
        self._viewer_hint = ""
        self._entries: Dict[str, AcquisitionEntry] = {}
        self._items: Dict[str, QListWidgetItem] = {}
        # acquisition path -> (thumbnail source, source mtime, pixmap); reused across refreshes
        self._thumbnail_cache: Dict[str, Tuple[str, float, QPixmap]] = {}
        self._closed = False
        self._lock = threading.Lock()

        self._events = _GalleryEvents()
        self._events.scanned.connect(self._on_scanned)
        self._events.thumbnail_ready.connect(self._on_thumbnail_ready)
        self._scan_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gallery-scan")
        self._thumbnail_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="gallery-thumb")

        self._placeholder_loading = make_placeholder_pixmap("Loading…", self.THUMBNAIL_SIZE)

        self._build_ui()

    # ----- UI -----

    def _build_ui(self) -> None:
        self.root_edit = QLineEdit()
        self.root_edit.setReadOnly(True)
        self.root_edit.setPlaceholderText("No saving folder")
        self.btn_browse = QPushButton("Browse…")
        self.btn_browse.setToolTip("Show the acquisitions saved under another folder")
        self.btn_saving_folder = QPushButton("Saving folder")
        self.btn_saving_folder.setToolTip("Back to the folder acquisitions are currently saved under")
        self.btn_refresh = QPushButton("Refresh")
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter by name, channel, objective…")
        self.filter_edit.setClearButtonEnabled(True)
        self.count_label = QLabel("")

        top = QHBoxLayout()
        top.addWidget(QLabel("Folder:"))
        top.addWidget(self.root_edit, 3)
        top.addWidget(self.btn_browse)
        top.addWidget(self.btn_saving_folder)
        top.addWidget(self.btn_refresh)
        top.addSpacing(12)
        top.addWidget(self.filter_edit, 2)
        top.addWidget(self.count_label)

        self.list_widget = QListWidget()
        self.list_widget.setViewMode(QListWidget.IconMode)
        self.list_widget.setIconSize(QSize(self.THUMBNAIL_SIZE, self.THUMBNAIL_SIZE))
        self.list_widget.setGridSize(QSize(self.THUMBNAIL_SIZE + 24, self.THUMBNAIL_SIZE + 52))
        self.list_widget.setResizeMode(QListWidget.Adjust)
        self.list_widget.setMovement(QListWidget.Static)
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.setWordWrap(True)
        self.list_widget.setSpacing(6)
        self.list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.details_label = QLabel("Select an acquisition.")
        self.details_label.setWordWrap(True)
        self.details_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.details_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.btn_open = QPushButton("Open in NDViewer")
        self.btn_show_folder = QPushButton("Show in file browser")
        self.btn_load_settings = QPushButton("Load settings into acquisition tab")
        self.btn_load_settings.setToolTip(
            "Fill the multipoint tab this acquisition was run from with its saved settings (acquisition.yaml)"
        )
        for button in (self.btn_open, self.btn_show_folder, self.btn_load_settings):
            button.setEnabled(False)

        details = QWidget()
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(6, 0, 0, 0)
        details_layout.addWidget(self.details_label, 1)
        details_layout.addWidget(self.btn_open)
        details_layout.addWidget(self.btn_show_folder)
        details_layout.addWidget(self.btn_load_settings)
        details.setMinimumWidth(240)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.list_widget)
        splitter.addWidget(details)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        splitter.setCollapsible(0, False)

        self.status_label = QLabel("")
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(top)
        layout.addWidget(splitter, 1)
        layout.addWidget(self.status_label)

        self.btn_browse.clicked.connect(self._browse)
        self.btn_saving_folder.clicked.connect(self.follow_saving_folder)
        self.btn_refresh.clicked.connect(self.refresh)
        self.filter_edit.textChanged.connect(self._apply_filter)
        self.list_widget.itemSelectionChanged.connect(self._on_selection_changed)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self.open_selected())
        self.list_widget.itemActivated.connect(lambda _item: self.open_selected())
        self.list_widget.customContextMenuRequested.connect(self._show_context_menu)
        self.btn_open.clicked.connect(self.open_selected)
        self.btn_show_folder.clicked.connect(self.show_selected_in_file_browser)
        self.btn_load_settings.clicked.connect(self.load_selected_settings)

    # ----- public state -----

    @property
    def root(self) -> Optional[str]:
        return self._root

    def count(self) -> int:
        return self.list_widget.count()

    def entries(self) -> List[AcquisitionEntry]:
        """The listed acquisitions, in display order."""
        return [self._entries[self.list_widget.item(i).data(self._ROLE_PATH)] for i in range(self.count())]

    def selected_entry(self) -> Optional[AcquisitionEntry]:
        items = self.list_widget.selectedItems()
        return self._entries.get(items[0].data(self._ROLE_PATH)) if items else None

    def select_path(self, path: str) -> bool:
        item = self._items.get(os.path.normpath(path))
        if item is None:
            return False
        self.list_widget.setCurrentItem(item)
        return True

    def set_viewer_available(self, available: bool, hint: str = "") -> None:
        """Whether a viewer takes signal_open_acquisition; without one, opening shows the folder instead."""
        self._viewer_available = available
        self._viewer_hint = hint
        if available:
            self.btn_open.setText("Open in NDViewer")
            self.btn_open.setToolTip("Show this acquisition in the NDViewer tab")
        else:
            self.btn_open.setText("Open (NDViewer unavailable)")
            self.btn_open.setToolTip(hint or "The NDViewer tab is not available; opening shows the folder instead")
        self._on_selection_changed()

    def set_root(self, root: Optional[str], pinned: bool = True) -> None:
        """List the acquisitions under root; pinned=False means it is the saving folder being followed."""
        self._root = os.path.normpath(root) if root else None
        self._root_pinned = pinned and self._root is not None
        self.root_edit.setText(self._root or "")
        self.refresh()

    def follow_saving_folder(self) -> None:
        self.set_root(self._default_root(), pinned=False)

    def refresh(self) -> None:
        """Re-read the root folder on a worker thread; the list updates when the scan lands."""
        if self._closed:
            return
        self._generation += 1
        generation = self._generation
        root = self._root
        if not root:
            self._show_entries([], root)
            self.status_label.setText("No saving folder is set.")
            return
        if not os.path.isdir(root):
            self._show_entries([], root)
            self.status_label.setText(f"Folder not found: {root}")
            return
        self.status_label.setText(f"Scanning {root} …")
        self._scan_executor.submit(self._scan_in_background, generation, root)

    def refresh_if_needed(self) -> None:
        """Follow a changed saving folder, or do the first scan; otherwise leave the list as it is."""
        if not self._root_pinned:
            default_root = self._default_root()
            if default_root != self._root:
                self.set_root(default_root, pinned=False)
                return
        if not self._scanned_once:
            self.refresh()

    # ----- actions -----

    def open_selected(self) -> None:
        entry = self.selected_entry()
        if entry is None:
            return
        if self._viewer_available:
            self.status_label.setText(f"Opening {entry.name} in NDViewer")
            self.signal_open_acquisition.emit(entry.path)
        else:
            self.status_label.setText(self._viewer_hint or "NDViewer is not available; showing the folder instead.")
            self._open_in_file_browser(entry.path)

    def show_selected_in_file_browser(self) -> None:
        entry = self.selected_entry()
        if entry is not None:
            self._open_in_file_browser(entry.path)

    def load_selected_settings(self) -> None:
        entry = self.selected_entry()
        if entry is None or not entry.has_settings:
            return
        self.signal_load_settings.emit(entry.path)

    def _open_in_file_browser(self, path: str) -> None:
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            self._log.warning(f"Could not open {path} in the file browser")
            self.status_label.setText(f"Could not open {path} in the file browser")

    def _browse(self) -> None:
        start = self._root if self._root and os.path.isdir(self._root) else (self._default_root() or "")
        picked = QFileDialog.getExistingDirectory(self, "Choose a folder of acquisitions", start)
        if picked:
            self.set_root(picked, pinned=True)

    def _show_context_menu(self, pos) -> None:
        item = self.list_widget.itemAt(pos)
        if item is None:
            return
        self.list_widget.setCurrentItem(item)
        entry = self.selected_entry()
        if entry is None:
            return
        menu = QMenu(self)
        menu.addAction(self.btn_open.text(), self.open_selected)
        menu.addAction("Show in file browser", self.show_selected_in_file_browser)
        load_action = menu.addAction("Load settings into acquisition tab", self.load_selected_settings)
        load_action.setEnabled(entry.has_settings)
        menu.exec_(self.list_widget.viewport().mapToGlobal(pos))

    # ----- scanning -----

    def _default_root(self) -> Optional[str]:
        try:
            root = self._default_root_provider()
        except Exception:
            self._log.exception("Saving folder provider failed")
            return None
        return os.path.normpath(root) if root else None

    def _scan_in_background(self, generation: int, root: str) -> None:
        try:
            entries = scan_acquisitions(root)
        except Exception:
            self._log.exception(f"Scanning {root} failed")
            entries = []
        self._emit(self._events.scanned, generation, root, entries)

    def _emit(self, signal, *args) -> None:
        """Emit from a worker thread unless the widget is already gone."""
        with self._lock:
            if self._closed:
                return
            try:
                signal.emit(*args)
            except RuntimeError:  # the Qt object was deleted under us
                pass

    def _on_scanned(self, generation: int, root: str, entries: List[AcquisitionEntry]) -> None:
        if generation != self._generation or self._closed:
            return
        self._scanned_once = True
        self._show_entries(entries, root)
        n = len(entries)
        self.status_label.setText(f"{n} acquisition{'s' if n != 1 else ''} under {root}")
        for entry in entries:
            self._request_thumbnail(generation, entry)

    def _show_entries(self, entries: List[AcquisitionEntry], root: Optional[str]) -> None:
        previously_selected = self.selected_entry()
        self.list_widget.clear()
        self._entries = {}
        self._items = {}
        for entry in entries:
            item = QListWidgetItem(self._item_text(entry))
            item.setData(self._ROLE_PATH, entry.path)
            item.setToolTip(entry.path + "\n" + "\n".join(entry.summary_lines()))
            item.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            item.setSizeHint(QSize(self.THUMBNAIL_SIZE + 20, self.THUMBNAIL_SIZE + 48))
            item.setIcon(QIcon(self._cached_or_placeholder(entry)))
            self.list_widget.addItem(item)
            self._entries[entry.path] = entry
            self._items[entry.path] = item
        self._apply_filter()
        if previously_selected is not None and previously_selected.path in self._items:
            self.list_widget.setCurrentItem(self._items[previously_selected.path])
        self._on_selection_changed()

    @staticmethod
    def _item_text(entry: AcquisitionEntry) -> str:
        when = f"{entry.started:%Y-%m-%d %H:%M}" if entry.started else ""
        return f"{entry.label}\n{when}" if when else entry.label

    def _apply_filter(self) -> None:
        needle = self.filter_edit.text().strip().lower()
        shown = 0
        for path, item in self._items.items():
            entry = self._entries[path]
            haystack = " ".join([entry.name, entry.objective or "", entry.image_format, *entry.channels]).lower()
            hidden = bool(needle) and needle not in haystack
            item.setHidden(hidden)
            shown += not hidden
        total = len(self._items)
        self.count_label.setText(f"{shown} of {total}" if needle else f"{total}")

    def _on_selection_changed(self) -> None:
        entry = self.selected_entry()
        if entry is None:
            self.details_label.setText("Select an acquisition.")
            for button in (self.btn_open, self.btn_show_folder, self.btn_load_settings):
                button.setEnabled(False)
            return
        # A zero-width space after each separator lets the label wrap a long path at folder boundaries.
        wrappable_path = _escape(entry.path).replace(os.sep, os.sep + "​")
        lines = [f"<b>{_escape(entry.label)}</b>", f"<small>{wrappable_path}</small>", ""]
        lines += [_escape(line) for line in entry.summary_lines()]
        if not entry.has_settings:
            lines.append("<i>No acquisition.yaml: settings cannot be loaded.</i>")
        self.details_label.setText("<br>".join(lines))
        self.btn_open.setEnabled(True)
        self.btn_show_folder.setEnabled(True)
        self.btn_load_settings.setEnabled(entry.has_settings)

    # ----- thumbnails -----

    def _cached_or_placeholder(self, entry: AcquisitionEntry) -> QPixmap:
        cached = self._thumbnail_cache.get(entry.path)
        if cached is not None and entry.thumbnail_source and cached[0] == entry.thumbnail_source:
            return cached[2]
        if entry.thumbnail_source:
            return self._placeholder_loading
        return make_placeholder_pixmap(entry.image_format, self.THUMBNAIL_SIZE)

    def _request_thumbnail(self, generation: int, entry: AcquisitionEntry) -> None:
        source = entry.thumbnail_source
        if not source:
            return
        try:
            mtime = os.stat(source).st_mtime
        except OSError:
            self._set_thumbnail(entry.path, None)
            return
        cached = self._thumbnail_cache.get(entry.path)
        if cached is not None and cached[0] == source and cached[1] == mtime:
            return  # the list already shows it
        self._thumbnail_executor.submit(self._load_thumbnail_in_background, generation, entry.path, source, mtime)

    def _load_thumbnail_in_background(self, generation: int, path: str, source: str, mtime: float) -> None:
        if generation != self._generation or self._closed:
            return
        array = load_thumbnail_array(source, self.THUMBNAIL_SIZE)
        self._emit(self._events.thumbnail_ready, generation, path, (source, mtime, array))

    def _on_thumbnail_ready(self, generation: int, path: str, result) -> None:
        if generation != self._generation or self._closed:
            return
        source, mtime, array = result
        if array is None:
            self._set_thumbnail(path, None)
            return
        pixmap = square_tile(array_to_pixmap(array), self.THUMBNAIL_SIZE)
        self._thumbnail_cache[path] = (source, mtime, pixmap)
        self._set_thumbnail(path, pixmap)

    def _set_thumbnail(self, path: str, pixmap: Optional[QPixmap]) -> None:
        item = self._items.get(path)
        entry = self._entries.get(path)
        if item is None or entry is None:
            return
        if pixmap is None:
            pixmap = make_placeholder_pixmap(entry.image_format, self.THUMBNAIL_SIZE)
        item.setIcon(QIcon(pixmap))

    # ----- Qt events -----

    def showEvent(self, event):  # noqa: N802 (Qt naming)
        super().showEvent(event)
        self.refresh_if_needed()

    def closeEvent(self, event):  # noqa: N802 (Qt naming)
        self.shutdown()
        super().closeEvent(event)

    def shutdown(self) -> None:
        """Stop the worker threads; late results are dropped."""
        with self._lock:
            self._closed = True
        self._scan_executor.shutdown(wait=False, cancel_futures=True)
        self._thumbnail_executor.shutdown(wait=False, cancel_futures=True)


def _escape(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
