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

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional, Tuple

from qtpy.QtCore import QObject, QSize, Qt, QUrl, Signal
from qtpy.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPixmap
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
from control.gallery_import import (  # noqa: F401  (re-exported: the folder readers live in gallery_import)
    IMAGE_EXTENSIONS,
    METADATA_FILES,
    NO_IMAGES,
    AcquisitionEntry,
    inspect_images,
    is_acquisition_folder,
    read_acquisition_entry,
    scan_acquisitions,
    split_experiment_name,
)
from control.gallery_previews import array_to_pixmap, load_thumbnail_array

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
