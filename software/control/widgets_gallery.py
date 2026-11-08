"""Gallery tab: every acquisition in the per-machine history, newest first, in a grid or a list.

The history (squid.acquisition_history.HistoryStore) records each acquisition as it runs, plus the ones imported
from folders, with a PNG preview per run. The gallery folds it into rows (pure functions below, testable without
Qt), shows them as thumbnail tiles or as a sortable table, and narrows them with status chips and a search box.
It never opens a dataset itself: it emits the chosen acquisition folder, and the GUI routes it on.

Loading the history and decoding previews run on worker threads; results reach the widget through Qt signals,
tagged with a generation so a reload started later always wins. While the tab is visible a timer reloads it
whenever the history file (or the previews folder) changed.
"""

import enum
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

import yaml
from qtpy.QtCore import QObject, QSize, Qt, QTimer, QUrl, Signal
from qtpy.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPixmap
from qtpy.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import control._def
import squid.logging
from control.gallery_import import ImportResult, import_folder, scan_acquisitions
from control.gallery_previews import array_to_pixmap, load_thumbnail_array, write_preview_png
from control.widgets_gallery_details import (
    NO_SELECTION,
    PREVIEW_SIDE,
    YAML_FILE,
    ActionState,
    GalleryDetailsPane,
    action_states,
    open_target,
)
from squid.acquisition_history import PREVIEWS_DIR_NAME, HistoryEntry, HistoryStore, folder_availability

# ---------------------------------------------------------------------------------------------------------
# Rows: the history entries as the gallery shows them (pure, no Qt)
# ---------------------------------------------------------------------------------------------------------

STATUS_FILTERS = ("All", "Running", "Complete", "Aborted", "Failed", "Offline", "Snaps")

_AVAILABILITY_LABELS = {"drive_offline": "Drive offline", "folder_missing": "Folder missing"}


@dataclass
class GalleryRow:
    entry: HistoryEntry
    availability: str  # "online" | "drive_offline" | "folder_missing"
    status_label: str
    search_text: str


def _percent(fraction: float) -> int:
    return int(fraction * 100 + 1e-9)  # floor, but 0.29 * 100 == 28.999… still reads 29


def status_label_for(entry: HistoryEntry, availability: str, running_fraction: Optional[float] = None) -> str:
    """The status column / tooltip text: running first, then a gone folder, then how the run ended."""
    if entry.is_running:
        if entry.kind == "protocol_run":
            label = f"Running · round {len(entry.rounds)}" if entry.rounds else "Running"
        elif running_fraction is not None:
            label = f"Running {_percent(running_fraction)}%"
        else:
            label = "Running"
    elif availability in _AVAILABILITY_LABELS:
        label = _AVAILABILITY_LABELS[availability]
    elif entry.outcome == "complete":
        label = "Complete"
    elif entry.outcome == "aborted":
        label = "Aborted" if entry.fraction_done is None else f"Aborted at {_percent(entry.fraction_done)}%"
    elif entry.outcome == "failed":
        label = "Failed"
    else:
        label = "Unknown"
    return f"Snap · {label}" if entry.kind == "snap" else label


def _channels(entry: HistoryEntry) -> List[str]:
    channels = entry.metadata.get("channels") or []
    return [str(c) for c in channels] if isinstance(channels, (list, tuple)) else []


def _protocol_names(entry: HistoryEntry) -> List[str]:
    names = []
    for protocol in (entry.protocol, entry.metadata.get("protocol")):
        if isinstance(protocol, dict):
            names += [str(protocol[k]) for k in ("protocol", "run_name") if protocol.get(k)]
    return names


def _search_text(entry: HistoryEntry) -> str:
    md = entry.metadata
    parts = [entry.name, md.get("wellplate_format"), *_channels(entry), md.get("objective"), md.get("image_format")]
    parts += _protocol_names(entry)
    return " ".join(str(p) for p in parts if p).lower()


def build_rows(entries: List[HistoryEntry], running_fraction: Optional[float] = None) -> List[GalleryRow]:
    """One row per top-level history entry, in the given order (the store's newest first)."""
    rows = []
    for entry in entries:
        availability = folder_availability(entry.folder)
        rows.append(
            GalleryRow(
                entry=entry,
                availability=availability,
                status_label=status_label_for(entry, availability, running_fraction),
                search_text=_search_text(entry),
            )
        )
    return rows


def _in_filter(row: GalleryRow, status_filter: str) -> bool:
    entry = row.entry
    if status_filter == "All":
        return True
    if status_filter == "Running":
        return entry.is_running
    if status_filter in ("Complete", "Aborted", "Failed"):
        return entry.outcome == status_filter.lower() and row.availability == "online"
    if status_filter == "Offline":
        return row.availability != "online"
    if status_filter == "Snaps":
        return entry.kind == "snap"
    return False


def matches(row: GalleryRow, needle: str, status_filter: str) -> bool:
    """Whether the row passes the status chip and contains the search text (case-insensitive)."""
    needle = needle.strip().lower()
    if needle and needle not in row.search_text:
        return False
    return _in_filter(row, status_filter)


def filter_counts(rows: List[GalleryRow]) -> Dict[str, int]:
    """How many rows each status chip would show, ignoring the search text."""
    return {name: sum(1 for row in rows if _in_filter(row, name)) for name in STATUS_FILTERS}


def summary_lines(entry: HistoryEntry) -> List[str]:
    """Short facts for the tooltip and the details pane, only the ones known."""
    md = entry.metadata
    lines = []
    if entry.started_at:
        lines.append(f"Started: {datetime.fromtimestamp(entry.started_at):%Y-%m-%d %H:%M:%S}")
    if md.get("objective"):
        mag = f" ({md['magnification']:g}x)" if isinstance(md.get("magnification"), (int, float)) else ""
        lines.append(f"Objective: {md['objective']}{mag}")
    if md.get("wellplate_format"):
        lines.append(f"Sample: {md['wellplate_format']}")
    if md.get("widget_type"):
        lines.append(f"Mode: {md['widget_type']} multipoint")
    channels = _channels(entry)
    if channels:
        lines.append(f"Channels ({len(channels)}): " + ", ".join(channels))
    dims = []
    for key, word in (("num_regions", "region"), ("num_fovs", "FOV")):
        n = md.get(key)
        if n is not None:
            dims.append(f"{n} {word}{'s' if n != 1 else ''}")
    for key, word in (("nz", "Nz"), ("nt", "Nt")):
        if md.get(key) is not None:
            dims.append(f"{word}={md[key]}")
    if dims:
        lines.append("Scan: " + ", ".join(dims))
    if entry.rounds:
        lines.append(f"Rounds: {len(entry.rounds)}")
    if md.get("image_format"):
        lines.append(f"Format: {md['image_format']}")
    return lines


# ---------------------------------------------------------------------------------------------------------
# Remembered view (the widgets_mosaic cache/ pattern)
# ---------------------------------------------------------------------------------------------------------


class GalleryView(enum.Enum):
    GRID = "grid"
    LIST = "list"


LAST_GALLERY_VIEW_CACHE = "cache/last_gallery_view.yaml"


def _load_last_gallery_view() -> GalleryView:
    """Read the persisted gallery view from cache/, or default to GRID."""
    try:
        with open(LAST_GALLERY_VIEW_CACHE, "r") as f:
            data = yaml.safe_load(f) or {}
        return GalleryView(data.get("view"))
    except (OSError, ValueError, AttributeError, yaml.YAMLError):
        return GalleryView.GRID


def _save_last_gallery_view(view: GalleryView) -> None:
    """Persist the gallery view so the next session opens in the same one."""
    try:
        directory = os.path.dirname(LAST_GALLERY_VIEW_CACHE)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(LAST_GALLERY_VIEW_CACHE, "w") as f:
            yaml.safe_dump({"view": view.value}, f)
    except OSError:
        # Cache write is best-effort; nothing fatal if the disk is read-only.
        squid.logging.get_logger(__name__).debug("Failed to persist the gallery view", exc_info=True)


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


IMPORT_OFFER_MARKER = "cache/gallery_import_offered.txt"  # exists once the first-launch import was offered


def import_status_text(folder: str, result: ImportResult) -> str:
    """The status line after importing a folder."""
    n = len(result.added)
    text = (
        f"Added {n} acquisition{'s' if n != 1 else ''} from {folder} "
        f"({result.skipped_existing} already in the history, {result.skipped_snaps} snaps skipped)"
    )
    if result.errors:
        k = len(result.errors)
        text += f"; {k} error{'s' if k != 1 else ''}, see the log"
    return text


def _folder_bytes(folder: str) -> Optional[int]:
    total = 0
    try:
        for dirpath, _dirs, files in os.walk(folder):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(dirpath, name))
                except OSError:
                    pass
    except OSError:
        return None
    return total


class _GalleryEvents(QObject):
    """Signals the worker threads use to hand results to the GUI thread."""

    loaded = Signal(int, object)  # generation, (history stamp, List[GalleryRow])
    thumbnail_ready = Signal(int, str, object)  # generation, entry id, (preview path, mtime, Optional[np.ndarray])
    imported = Signal(int, str, object)  # import number, folder, ImportResult or an error message (str)


class _SortItem(QTableWidgetItem):
    """A table cell that sorts by a key stored beside its text (dates and counts sort as numbers)."""

    _ROLE_KEY = Qt.UserRole + 1

    def __lt__(self, other):
        mine, theirs = self.data(self._ROLE_KEY), other.data(self._ROLE_KEY)
        if mine is not None and theirs is not None:
            return mine < theirs
        return self.text().lower() < other.text().lower()


class AcquisitionGalleryWidget(QWidget):
    """The Gallery tab: the acquisition history as thumbnail tiles or a table, to open or reuse.

    import_root_provider gives the folder acquisitions are saved under (the starting point for importing).
    """

    signal_open_in_squid = Signal(str)  # an acquisition folder to show in Squid's viewer tab
    signal_open_in_window = Signal(str)  # an acquisition folder to show in a standalone viewer window
    signal_load_settings = Signal(str)  # an acquisition folder whose acquisition.yaml to load
    signal_shown = Signal()

    THUMBNAIL_SIZE = 180
    TABLE_ICON_SIZE = 56
    RELOAD_INTERVAL_MS = 2000
    COLUMNS = ("Name", "Started", "Sample", "Objective", "Channels", "FOVs", "Nz / Nt", "Format", "Status")
    _COL_STARTED = 1
    _COL_STATUS = 8
    _ROLE_ID = Qt.UserRole
    _NOT_LOADED = object()

    def __init__(self, store: HistoryStore, *, import_root_provider: Callable[[], Optional[str]], parent=None):
        super().__init__(parent)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._store = store
        self._import_root_provider = (
            import_root_provider  # where importing starts, and what the first-launch offer scans
        )
        self._generation = 0
        self._import_serial = 0
        self._importing = 0
        self._loading = False
        self._sort_initialised = False
        self._seen_stamp = self._NOT_LOADED
        self._running_fraction: Optional[float] = None
        self._status_filter = "All"
        self._all_rows: List[GalleryRow] = []
        self._shown: List[GalleryRow] = []
        self._selected_id: Optional[str] = None
        self._syncing_selection = False
        self._items: Dict[str, QListWidgetItem] = {}
        self._table_name_items: Dict[str, QTableWidgetItem] = {}
        self._table_status_items: Dict[str, QTableWidgetItem] = {}
        # entry id -> (preview path, preview mtime, pixmap); reused across reloads. A preview that is missing
        # (mtime None) or does not decode is cached with its format placeholder, so it is not retried until it changes.
        self._thumbnail_cache: Dict[str, Tuple[str, Optional[float], QPixmap]] = {}
        self._closed = False
        self._lock = threading.Lock()
        # Which viewers can open a dataset: (available, reason); the in-Squid one can also be hidden altogether.
        self._squid: Tuple[bool, str] = (True, "")
        self._squid_hidden = False
        self._window: Tuple[bool, str] = (True, "")
        self._action_states: Dict[str, ActionState] = NO_SELECTION
        self._preview_placeholders: Dict[str, QPixmap] = {}
        self._status_after_load: Optional[str] = None  # an action's message, kept over the reload's own

        self._events = _GalleryEvents()
        self._events.loaded.connect(self._on_loaded)
        self._events.thumbnail_ready.connect(self._on_thumbnail_ready)
        self._events.imported.connect(self._on_imported)
        self._scan_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gallery-scan")
        self._thumbnail_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="gallery-thumb")

        self._placeholder_loading = make_placeholder_pixmap("Loading…", self.THUMBNAIL_SIZE)
        self._placeholder_running = make_placeholder_pixmap("Running…", self.THUMBNAIL_SIZE)
        self._placeholders: Dict[str, QPixmap] = {}

        self._timer = QTimer(self)
        self._timer.setInterval(self.RELOAD_INTERVAL_MS)
        self._timer.timeout.connect(self._reload_if_changed)

        self._build_ui()
        self._view = _load_last_gallery_view()
        self._apply_view()
        self._update_chips()

    # ----- UI -----

    def _build_ui(self) -> None:
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Search name, sample, channel, objective…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_buttons: Dict[str, QPushButton] = {}
        top = QHBoxLayout()
        top.addWidget(self.filter_edit, 2)
        for name in STATUS_FILTERS:
            button = QPushButton(name)
            button.setCheckable(True)
            button.setFlat(True)
            button.clicked.connect(lambda _checked=False, name=name: self.set_status_filter(name))
            self.filter_buttons[name] = button
            top.addWidget(button)
        self.count_label = QLabel("")
        self.view_toggle = QPushButton()
        self.view_toggle.setCheckable(True)  # checked = list view
        self.btn_refresh = QPushButton("Refresh")
        self.btn_import = QPushButton("Add folder to history")
        top.addStretch(1)
        top.addWidget(self.count_label)
        top.addWidget(self.view_toggle)
        top.addWidget(self.btn_import)
        top.addWidget(self.btn_refresh)

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

        self.table_widget = QTableWidget(0, len(self.COLUMNS))
        self.table_widget.setHorizontalHeaderLabels(list(self.COLUMNS))
        self.table_widget.setIconSize(QSize(self.TABLE_ICON_SIZE, self.TABLE_ICON_SIZE))
        self.table_widget.verticalHeader().setVisible(False)
        self.table_widget.verticalHeader().setDefaultSectionSize(self.TABLE_ICON_SIZE + 4)
        self.table_widget.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table_widget.horizontalHeader().setStretchLastSection(True)
        self.table_widget.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table_widget.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table_widget.setSortingEnabled(True)

        self._stack = QStackedWidget()
        self._stack.addWidget(self.list_widget)
        self._stack.addWidget(self.table_widget)

        self.details = GalleryDetailsPane(self._submit_detail_load, self._emit)
        self.details.setMinimumWidth(PREVIEW_SIDE + 40)
        pane = self.details
        self.preview_label = pane.preview_label
        self.metadata_label = pane.metadata_label
        self.rounds_table = pane.rounds_table
        self.details_toggle = pane.details_toggle
        self.raw_yaml_text = pane.raw_yaml_text
        self.btn_open_in_squid = pane.btn_open_in_squid
        self.btn_open_in_window = pane.btn_open_in_window
        self.btn_show_folder = pane.btn_show_folder
        self.btn_load_settings = pane.btn_load_settings

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._stack)
        splitter.addWidget(self.details)
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

        self.filter_edit.textChanged.connect(lambda _text: self._populate())
        self.view_toggle.clicked.connect(
            lambda checked: self.set_view(GalleryView.LIST if checked else GalleryView.GRID)
        )
        self.btn_refresh.clicked.connect(self.reload)
        self.btn_import.clicked.connect(self._choose_folder_to_import)
        self.list_widget.itemSelectionChanged.connect(self._on_grid_selection_changed)
        # activated comes for a double-click and for Enter (also connecting doubleClicked would open twice).
        self.list_widget.itemActivated.connect(lambda _item: self.open_selected())
        self.list_widget.customContextMenuRequested.connect(
            lambda pos: self._show_context_menu(self.list_widget, self.list_widget.itemAt(pos), pos)
        )
        self.table_widget.itemSelectionChanged.connect(self._on_table_selection_changed)
        self.table_widget.itemActivated.connect(lambda _item: self.open_selected())
        self.table_widget.customContextMenuRequested.connect(
            lambda pos: self._show_context_menu(self.table_widget, self.table_widget.itemAt(pos), pos)
        )
        self.btn_open_in_squid.clicked.connect(self.open_selected_in_squid)
        self.btn_open_in_window.clicked.connect(self.open_selected_in_window)
        self.btn_show_folder.clicked.connect(self.show_selected_in_file_browser)
        self.btn_load_settings.clicked.connect(self.load_selected_settings)
        self.details.round_open_in_squid.connect(lambda folder: self._open_folder(self.signal_open_in_squid, folder))
        self.details.round_open_in_window.connect(lambda folder: self._open_folder(self.signal_open_in_window, folder))

    # ----- public state -----

    def rows(self) -> List[GalleryRow]:
        """The rows shown, in display order (newest first), after the status chip and the search text."""
        return list(self._shown)

    @property
    def view(self) -> GalleryView:
        return self._view

    def set_view(self, view: GalleryView) -> None:
        self._view = GalleryView(view)
        self._apply_view()
        _save_last_gallery_view(self._view)

    def _apply_view(self) -> None:
        is_list = self._view is GalleryView.LIST
        self._stack.setCurrentWidget(self.table_widget if is_list else self.list_widget)
        self.view_toggle.setChecked(is_list)
        self.view_toggle.setText("Grid view" if is_list else "List view")
        self.view_toggle.setToolTip("Show the acquisitions as " + ("thumbnails" if is_list else "a table"))

    def set_status_filter(self, name: str) -> None:
        if name not in STATUS_FILTERS:
            raise ValueError(f"Unknown gallery filter {name!r}; expected one of {STATUS_FILTERS}")
        self._status_filter = name
        self._populate()

    def set_running_progress(self, fraction: Optional[float]) -> None:
        """Relabel the running row(s) with the acquisition's progress, without reloading the history."""
        self._running_fraction = fraction
        for row in self._all_rows:
            if row.entry.is_running:
                row.status_label = status_label_for(row.entry, row.availability, fraction)
                self._show_row_status(row)
                if row.entry.id == self._selected_id:
                    self._show_details()

    @property
    def channel_chips(self) -> List[QPushButton]:
        """The details pane's channel toggles, one per channel of the preview shown."""
        return self.details.channel_chips

    def set_open_in_squid(self, available: bool, reason: str = "", *, hidden: bool = False) -> None:
        """Whether Squid's viewer tab can open acquisitions (reason says why not); hidden removes the action."""
        self._squid = (bool(available), reason)
        self._squid_hidden = bool(hidden)
        self._show_details()

    def set_open_in_window(self, available: bool, reason: str = "") -> None:
        """Whether a standalone viewer window can open acquisitions (reason says why not)."""
        self._window = (bool(available), reason)
        self._show_details()

    def select_entry(self, entry_id: str) -> bool:
        if not any(row.entry.id == entry_id for row in self._shown):
            return False
        self._set_selected(entry_id)
        return True

    def selected_entry(self) -> Optional[HistoryEntry]:
        row = self._shown_row(self._selected_id)
        return row.entry if row is not None else None

    # ----- importing -----

    def _choose_folder_to_import(self) -> None:
        start = self._import_root_provider()
        if not start or not os.path.isdir(start):
            start = os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder of acquisitions", start)
        if folder:
            self.import_path(folder)

    def import_path(self, folder: str) -> None:
        """Add the acquisitions under folder (or folder itself) to the history, on the scan worker thread."""
        if self._closed:
            return
        self._import_serial += 1
        self._importing += 1
        self.btn_import.setEnabled(False)
        self.status_label.setText(f"Adding acquisitions from {folder} …")
        self._scan_executor.submit(self._import_in_background, self._import_serial, folder)

    def _import_in_background(self, serial: int, folder: str) -> None:
        try:
            result = import_folder(
                self._store,
                folder,
                include_snaps=control._def.RECORD_SNAPS_IN_HISTORY,
                make_preview=self._make_preview,
            )
        except Exception as e:
            self._log.exception(f"Importing {folder} into the acquisition history failed")
            result = str(e) or type(e).__name__
        self._emit(self._events.imported, serial, folder, result)

    def _make_preview(self, folder: str, entry_id: str) -> None:
        """Write the entry's preview PNG and record it in the history (called by the importer for each addition)."""
        channels = write_preview_png(folder, str(self._store.previews_dir / f"{entry_id}.png"))
        if channels:
            self._store.append(
                {
                    "type": "preview",
                    "id": entry_id,
                    "file": f"{entry_id}.png",
                    "channels": list(channels),
                    "bytes_on_disk": _folder_bytes(folder),
                    "at": time.time(),
                }
            )

    def _on_imported(self, serial: int, folder: str, result) -> None:
        if self._closed:
            return
        self._importing = max(0, self._importing - 1)
        if self._importing == 0:
            self.btn_import.setEnabled(True)
        if isinstance(result, ImportResult):
            for error in result.errors:
                self._log.warning(f"Importing {folder}: {error}")
            message = import_status_text(folder, result)
        else:
            message = f"Importing {folder} failed: {result}"
        self.status_label.setText(message)
        self._status_after_load = message
        self.reload()

    def offer_initial_import(self) -> None:
        """At first launch, with an empty history and acquisitions in the saving folder, offer once to add them."""
        marker = IMPORT_OFFER_MARKER
        try:
            if self._store.load() or os.path.exists(marker):
                return
            root = self._import_root_provider()
            if not root or not os.path.isdir(root) or not scan_acquisitions(root):
                return
        except Exception:
            self._log.exception("Checking whether to offer the history import failed")
            return
        answer = QMessageBox.question(
            self,
            "Add existing acquisitions?",
            f"Add the acquisitions already in\n{root}\nto the history?",
            QMessageBox.Yes | QMessageBox.No,
        )
        try:
            os.makedirs(os.path.dirname(os.path.abspath(marker)), exist_ok=True)
            with open(marker, "w", encoding="utf-8") as f:
                f.write(f"offered {datetime.now().isoformat()}\n")
        except OSError:
            self._log.exception(f"Could not write {marker}")
        if answer == QMessageBox.Yes:
            self.import_path(root)

    # ----- loading -----

    def reload(self) -> None:
        """Re-read the history on a worker thread; both views update when it lands."""
        if self._closed:
            return
        self._generation += 1
        self._loading = True
        if not self._all_rows:
            self.status_label.setText("Loading the acquisition history…")
        self._scan_executor.submit(self._load_in_background, self._generation, self._running_fraction)

    def _history_stamp(self):
        stamp = []
        for path in (self._store.path, self._store.path.parent / PREVIEWS_DIR_NAME):
            try:
                st = os.stat(path)
                stamp.append((st.st_mtime_ns, st.st_size))
            except OSError:
                stamp.append(None)
        return tuple(stamp)

    def _reload_if_changed(self) -> None:
        if self._closed or self._loading or not self.isVisible():
            return
        if self._history_stamp() != self._seen_stamp:
            self.reload()

    def _load_in_background(self, generation: int, running_fraction: Optional[float]) -> None:
        if generation != self._generation or self._closed:
            return
        stamp = self._history_stamp()  # taken first, so a write during the read triggers another reload
        try:
            rows = build_rows(self._store.load(), running_fraction)
        except Exception:
            self._log.exception(f"Loading the acquisition history {self._store.path} failed")
            rows = []
        self._emit(self._events.loaded, generation, (stamp, rows))

    def _emit(self, signal, *args) -> None:
        """Emit from a worker thread unless the widget is already gone."""
        with self._lock:
            if self._closed:
                return
            try:
                signal.emit(*args)
            except RuntimeError:  # the Qt object was deleted under us
                pass

    def _on_loaded(self, generation: int, result) -> None:
        if generation != self._generation or self._closed:
            return
        stamp, rows = result
        self._loading = False
        self._seen_stamp = stamp
        self._all_rows = rows
        self._populate(reset_sort=not self._sort_initialised)  # later reloads keep the user's table sort
        self._sort_initialised = True
        n = len(rows)
        message, self._status_after_load = self._status_after_load, None
        self.status_label.setText(message or f"{n} acquisition{'s' if n != 1 else ''} in the history")
        for row in rows:
            self._request_thumbnail(generation, row)

    # ----- populating the views -----

    def _update_chips(self) -> None:
        if not control._def.RECORD_SNAPS_IN_HISTORY and self._status_filter == "Snaps":
            self._status_filter = "All"
        counts = filter_counts(self._all_rows)
        for name, button in self.filter_buttons.items():
            button.setText(f"{name} {counts[name]}")
            button.setChecked(name == self._status_filter)
        self.filter_buttons["Snaps"].setHidden(not control._def.RECORD_SNAPS_IN_HISTORY)

    def _populate(self, reset_sort: bool = False) -> None:
        self._update_chips()
        needle = self.filter_edit.text()
        self._shown = [row for row in self._all_rows if matches(row, needle, self._status_filter)]
        self._syncing_selection = True
        try:
            self._populate_grid()
            self._populate_table(reset_sort)
        finally:
            self._syncing_selection = False
        total, shown = len(self._all_rows), len(self._shown)
        self.count_label.setText(f"{shown} of {total}" if shown != total else f"{total}")
        selected = self._selected_id if self._shown_row(self._selected_id) is not None else None
        self._set_selected(selected)

    def _populate_grid(self) -> None:
        self.list_widget.clear()
        self._items = {}
        for row in self._shown:
            item = QListWidgetItem(self._tile_text(row.entry))
            item.setData(self._ROLE_ID, row.entry.id)
            item.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            item.setSizeHint(QSize(self.THUMBNAIL_SIZE + 20, self.THUMBNAIL_SIZE + 48))
            item.setIcon(QIcon(self._cached_or_placeholder(row.entry)))
            self.list_widget.addItem(item)
            self._items[row.entry.id] = item
            self._show_row_status(row)

    def _populate_table(self, reset_sort: bool) -> None:
        table = self.table_widget
        table.setSortingEnabled(False)
        table.clearContents()
        table.setRowCount(len(self._shown))
        self._table_name_items = {}
        self._table_status_items = {}
        n = len(self._shown)
        for r, row in enumerate(self._shown):
            entry, md = row.entry, row.entry.metadata
            started = f"{datetime.fromtimestamp(entry.started_at):%Y-%m-%d %H:%M}" if entry.started_at else ""
            nz_nt = " / ".join("" if md.get(k) is None else str(md[k]) for k in ("nz", "nt"))
            cells = [
                (self._tile_text(entry).split("\n")[0], None),
                (started, n - r),  # sorts as the store's order: newest first when descending
                (md.get("wellplate_format") or "", None),
                (md.get("objective") or "", None),
                (", ".join(_channels(entry)), None),
                ("" if md.get("num_fovs") is None else str(md["num_fovs"]), md.get("num_fovs")),
                (nz_nt if nz_nt != " / " else "", None),
                (md.get("image_format") or "", None),
                (row.status_label, None),
            ]
            for c, (text, key) in enumerate(cells):
                item = _SortItem(text)
                if key is not None:
                    item.setData(_SortItem._ROLE_KEY, key)
                item.setData(self._ROLE_ID, entry.id)
                table.setItem(r, c, item)
            name_item = table.item(r, 0)
            name_item.setIcon(QIcon(self._cached_or_placeholder(entry)))
            self._table_name_items[entry.id] = name_item
            self._table_status_items[entry.id] = table.item(r, self._COL_STATUS)
            self._show_row_status(row)
        if reset_sort:
            table.horizontalHeader().setSortIndicator(self._COL_STARTED, Qt.DescendingOrder)
        table.setSortingEnabled(True)

    def _show_row_status(self, row: GalleryRow) -> None:
        tooltip = "\n".join([row.status_label, row.entry.folder, *summary_lines(row.entry)])
        item = self._items.get(row.entry.id)
        if item is not None:
            item.setToolTip(tooltip)
        status_item = self._table_status_items.get(row.entry.id)
        if status_item is not None:
            status_item.setText(row.status_label)
        name_item = self._table_name_items.get(row.entry.id)
        if name_item is not None:
            name_item.setToolTip(tooltip)

    @staticmethod
    def _tile_text(entry: HistoryEntry) -> str:
        name = f"{entry.name} · {len(entry.rounds)} rounds" if entry.kind == "protocol_run" else entry.name
        return f"{name}\n{datetime.fromtimestamp(entry.started_at):%Y-%m-%d %H:%M}" if entry.started_at else name

    # ----- selection -----

    def _shown_row(self, entry_id: Optional[str]) -> Optional[GalleryRow]:
        return next((row for row in self._shown if row.entry.id == entry_id), None) if entry_id else None

    def _set_selected(self, entry_id: Optional[str]) -> None:
        """Select the entry in both views (None clears the selection)."""
        self._selected_id = entry_id
        self._syncing_selection = True
        try:
            item = self._items.get(entry_id) if entry_id else None
            if item is not None:
                self.list_widget.setCurrentItem(item)
            else:
                self.list_widget.clearSelection()
            name_item = self._table_name_items.get(entry_id) if entry_id else None
            if name_item is not None:
                self.table_widget.selectRow(name_item.row())
            else:
                self.table_widget.clearSelection()
        finally:
            self._syncing_selection = False
        self._show_details()

    def _on_grid_selection_changed(self) -> None:
        if not self._syncing_selection:
            items = self.list_widget.selectedItems()
            self._set_selected(items[0].data(self._ROLE_ID) if items else None)

    def _on_table_selection_changed(self) -> None:
        if not self._syncing_selection:
            items = self.table_widget.selectedItems()
            self._set_selected(items[0].data(self._ROLE_ID) if items else None)

    def _show_details(self) -> None:
        row = self._shown_row(self._selected_id)
        if row is None:
            self._action_states = NO_SELECTION
            self.details.clear()
            return
        entry = row.entry
        target = open_target(entry)
        target_availability = row.availability if target is entry else folder_availability(target.folder)
        target_online = target_availability == "online"
        self._action_states = action_states(
            entry,
            row.availability,
            target_availability=target_availability,
            has_settings=target_online and self._has_settings(target),
            squid=self._squid,
            window=self._window,
        )
        self.details.show_entry(
            entry,
            row.status_label,
            placeholder=self._preview_placeholder(entry),
            preview_png=self._preview_path(entry),
            data_folder=target.folder if target_online and target.folder else None,
            planes_allowed=not target.is_running,
        )
        self.details.set_actions(self._action_states, squid_hidden=self._squid_hidden)
        rounds = None
        if entry.kind == "protocol_run":
            rounds = []
            for r in entry.rounds:
                availability = folder_availability(r.folder)
                states = action_states(r, availability, squid=self._squid, window=self._window)
                label = status_label_for(r, availability, self._running_fraction if r.is_running else None)
                rounds.append((r, label, states["open_in_squid"], states["open_in_window"]))
        self.details.set_rounds(rounds, squid_hidden=self._squid_hidden)

    def _submit_detail_load(self, fn, *args) -> None:
        if self._closed:
            return
        try:
            self._thumbnail_executor.submit(fn, *args)
        except RuntimeError:  # the executor was shut down under us
            pass

    def _preview_placeholder(self, entry: HistoryEntry) -> QPixmap:
        text = "Running…" if entry.is_running else (entry.metadata.get("image_format") or "No preview")
        if text not in self._preview_placeholders:
            self._preview_placeholders[text] = make_placeholder_pixmap(text, PREVIEW_SIDE)
        return self._preview_placeholders[text]

    # ----- actions -----

    @staticmethod
    def _has_settings(entry: HistoryEntry) -> bool:
        return bool(entry.folder) and os.path.isfile(os.path.join(entry.folder, YAML_FILE))

    def _allowed(self, action: str) -> Optional[HistoryEntry]:
        """The selected entry when the action is allowed on it; otherwise None, with the reason in the status line."""
        entry = self.selected_entry()
        if entry is None:
            return None
        state = self._action_states.get(action, NO_SELECTION[action])
        if not state.enabled:
            self.status_label.setText(state.reason)
            return None
        return entry

    def _open_folder(self, signal, folder: str) -> None:
        where = "Squid" if signal is self.signal_open_in_squid else "a viewer window"
        self.status_label.setText(f"Opening {os.path.basename(os.path.normpath(folder))} in {where}")
        signal.emit(folder)

    def open_selected_in_squid(self) -> None:
        if self._squid_hidden:
            return
        entry = self._allowed("open_in_squid")
        if entry is not None:
            self._open_folder(self.signal_open_in_squid, open_target(entry).folder)

    def open_selected_in_window(self) -> None:
        entry = self._allowed("open_in_window")
        if entry is not None:
            self._open_folder(self.signal_open_in_window, open_target(entry).folder)

    def open_selected(self) -> None:
        """Open in Squid when it can, else in a viewer window, else show the folder (double-click and Enter)."""
        if self.selected_entry() is None:
            return
        states = self._action_states
        if not self._squid_hidden and states["open_in_squid"].enabled:
            self.open_selected_in_squid()
        elif states["open_in_window"].enabled:
            self.open_selected_in_window()
        else:
            self.show_selected_in_file_browser()

    def show_selected_in_file_browser(self) -> None:
        entry = self._allowed("show_folder")
        if entry is not None and not QDesktopServices.openUrl(QUrl.fromLocalFile(entry.folder)):
            self._log.warning(f"Could not open {entry.folder} in the file browser")
            self.status_label.setText(f"Could not open {entry.folder} in the file browser")

    def load_selected_settings(self) -> None:
        entry = self._allowed("load_settings")
        if entry is not None:
            self.signal_load_settings.emit(open_target(entry).folder)

    def copy_selected_path(self) -> None:
        entry = self._allowed("copy_path")
        if entry is not None:
            QApplication.clipboard().setText(entry.folder)
            self.status_label.setText(f"Copied {entry.folder}")

    def remove_selected(self) -> None:
        """Drop the entry from the history (an append-only `removed` event); nothing on disk is touched."""
        entry = self._allowed("remove")
        if entry is None:
            return
        self._store.append({"type": "removed", "id": entry.id})
        message = f"Removed {entry.name} from the history (data untouched)"
        self.status_label.setText(message)
        self._status_after_load = message
        self.reload()

    def locate_selected(self) -> None:
        """Point the entry at the folder it was moved to (same folder name); a protocol run's rounds follow."""
        entry = self._allowed("locate")
        if entry is None:
            return
        old = os.path.normpath(entry.folder) if entry.folder else ""
        start = os.path.dirname(old) if old and os.path.isdir(os.path.dirname(old)) else os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(self, f"Locate {entry.name}", start)
        if not chosen:
            return
        new = os.path.normpath(os.path.abspath(chosen))
        expected, got = os.path.basename(old), os.path.basename(new)
        if os.path.normcase(expected) != os.path.normcase(got):
            self.status_label.setText(f"Not relocated: the folder must be named {expected}, not {got}")
            return
        self._store.append({"type": "relocated", "id": entry.id, "folder": new})
        missing = []
        for r in entry.rounds:
            name = os.path.basename(os.path.normpath(r.folder)) if r.folder else ""
            candidate = os.path.join(new, name)
            if name and os.path.isdir(candidate):
                self._store.append({"type": "relocated", "id": r.id, "folder": candidate})
            else:
                missing.append(name or r.name)
        message = f"Located {entry.name} at {new}"
        if missing:
            message += "; not found there: " + ", ".join(missing)
        self.status_label.setText(message)
        self._status_after_load = message
        self.reload()

    def _build_context_menu(self) -> QMenu:
        """The selected entry's actions, in the order of the buttons, then the history ones."""
        menu = QMenu(self)
        entries = [
            ("Open in Squid", self.open_selected_in_squid, "open_in_squid"),
            ("Open in window", self.open_selected_in_window, "open_in_window"),
            ("Show folder", self.show_selected_in_file_browser, "show_folder"),
            ("Load settings", self.load_selected_settings, "load_settings"),
            None,
            ("Copy path", self.copy_selected_path, "copy_path"),
            ("Locate…", self.locate_selected, "locate"),
            ("Remove from history", self.remove_selected, "remove"),
        ]
        for spec in entries:
            if spec is None:
                menu.addSeparator()
                continue
            text, slot, action = spec
            if action == "open_in_squid" and self._squid_hidden:
                continue
            state = self._action_states.get(action, NO_SELECTION[action])
            menu_action = menu.addAction(text, slot)
            menu_action.setEnabled(state.enabled)
            menu_action.setToolTip(state.reason)
        menu.setToolTipsVisible(True)
        return menu

    def _show_context_menu(self, view: QWidget, item, pos) -> None:
        if item is None:
            return
        self._set_selected(item.data(self._ROLE_ID))
        if self.selected_entry() is None:
            return
        self._build_context_menu().exec_(view.viewport().mapToGlobal(pos))

    # ----- thumbnails -----

    def _preview_path(self, entry: HistoryEntry) -> Optional[str]:
        return str(self._store.previews_dir / entry.preview) if entry.preview else None

    def _format_placeholder(self, entry: HistoryEntry) -> QPixmap:
        text = entry.metadata.get("image_format") or "No preview"
        if text not in self._placeholders:
            self._placeholders[text] = make_placeholder_pixmap(text, self.THUMBNAIL_SIZE)
        return self._placeholders[text]

    def _cached_or_placeholder(self, entry: HistoryEntry) -> QPixmap:
        if entry.is_running:
            return self._placeholder_running
        path = self._preview_path(entry)
        if path is None:
            return self._format_placeholder(entry)
        cached = self._thumbnail_cache.get(entry.id)
        if cached is not None and cached[0] == path:
            return cached[2]
        return self._placeholder_loading

    def _request_thumbnail(self, generation: int, row: GalleryRow) -> None:
        entry = row.entry
        path = self._preview_path(entry)
        if entry.is_running or path is None:
            return
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            mtime = None
        cached = self._thumbnail_cache.get(entry.id)
        if cached is not None and cached[0] == path and cached[1] == mtime:
            return  # the views already show it
        if mtime is None:
            self._cache_failed_preview(entry, path, mtime)
            return
        self._thumbnail_executor.submit(self._load_thumbnail_in_background, generation, entry.id, path, mtime)

    def _load_thumbnail_in_background(self, generation: int, entry_id: str, path: str, mtime: float) -> None:
        if generation != self._generation or self._closed:
            return
        array = load_thumbnail_array(path, self.THUMBNAIL_SIZE)
        self._emit(self._events.thumbnail_ready, generation, entry_id, (path, mtime, array))

    def _on_thumbnail_ready(self, generation: int, entry_id: str, result) -> None:
        if generation != self._generation or self._closed:
            return
        path, mtime, array = result
        row = next((r for r in self._all_rows if r.entry.id == entry_id), None)
        if row is None:
            return
        if array is None:
            self._cache_failed_preview(row.entry, path, mtime)
            return
        pixmap = square_tile(array_to_pixmap(array), self.THUMBNAIL_SIZE)
        self._thumbnail_cache[entry_id] = (path, mtime, pixmap)
        self._set_thumbnail(entry_id, pixmap)

    def _cache_failed_preview(self, entry: HistoryEntry, path: str, mtime: Optional[float]) -> None:
        pixmap = self._format_placeholder(entry)
        self._thumbnail_cache[entry.id] = (path, mtime, pixmap)
        self._set_thumbnail(entry.id, pixmap)

    def _set_thumbnail(self, entry_id: str, pixmap: QPixmap) -> None:
        icon = QIcon(pixmap)
        for item in (self._items.get(entry_id), self._table_name_items.get(entry_id)):
            if item is not None:
                item.setIcon(icon)

    # ----- Qt events -----

    def showEvent(self, event):  # noqa: N802 (Qt naming)
        super().showEvent(event)
        if not self._closed:
            self._timer.start()
            self._reload_if_changed()
        self.signal_shown.emit()

    def hideEvent(self, event):  # noqa: N802 (Qt naming)
        self._timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event):  # noqa: N802 (Qt naming)
        self.shutdown()
        super().closeEvent(event)

    def shutdown(self) -> None:
        """Stop the timer and the worker threads; late results are dropped."""
        with self._lock:
            self._closed = True
        self._timer.stop()
        self._scan_executor.shutdown(wait=False, cancel_futures=True)
        self._thumbnail_executor.shutdown(wait=False, cancel_futures=True)


def _escape(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
