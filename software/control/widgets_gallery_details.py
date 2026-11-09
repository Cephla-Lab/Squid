"""The Gallery tab's details pane: a large preview with channel chips, the run's metadata, its acquisition.yaml,
the action buttons and, for a protocol run, a table of its rounds with their own open buttons.

The pure helpers at the top (metadata table, sizes, which actions are allowed) need no Qt. The pane itself only
shows what it is given; AcquisitionGalleryWidget (control.widgets_gallery) decides what is selected and what each
action does. Reading the overview planes and the YAML happens on the gallery's thumbnail executor; results come
back through a Qt signal tagged with a token, so a newer selection always wins.
"""

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
from qtpy.QtCore import QObject, Qt, Signal
from qtpy.QtGui import QPixmap
from qtpy.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import squid.logging
from control.gallery_import import RECORDED_METADATA_KEYS
from control.gallery_previews import (
    array_to_pixmap,
    channel_rgb,
    composite,
    load_preview_planes,
    load_thumbnail_array,
)
from squid.acquisition_history import HistoryEntry

_log = squid.logging.get_logger(__name__)

PREVIEW_SIDE = 360  # longest side of the large preview, in pixels
YAML_FILE = "acquisition.yaml"
_MAX_YAML_BYTES = 2 * 1024 * 1024
UNKNOWN = "–"
RUNNING_REASON = "Wait for the acquisition to finish"

# ---------------------------------------------------------------------------------------------------------
# Metadata table (pure)
# ---------------------------------------------------------------------------------------------------------

_KEY_LABELS = {
    "experiment_id": "Experiment",
    "widget_type": "Mode",
    "xy_mode": "XY mode",
    "objective": "Objective",
    "magnification": "Magnification",
    "pixel_size_um": "Pixel size (µm)",
    "wellplate_format": "Sample",
    "channels": "Channels",
    "num_regions": "Regions",
    "num_fovs": "FOVs",
    "nz": "Nz",
    "dz_um": "Δz (µm)",
    "nt": "Nt",
    "dt_s": "Δt (s)",
    "z_stacking_config": "Z stacking",
    "use_piezo": "Use piezo",
    "contrast_af": "Contrast AF",
    "laser_af": "Laser AF",
    "laser_af_per_stack": "Laser AF per stack",
    "start_time": "Start time",
    "image_format": "Format",
    "protocol": "Protocol",
    "machine": "Machine",
    "software_version": "Software version",
    "file_format": "File format",
    "expected_images": "Expected images",
}


def format_size(num_bytes: Optional[int]) -> Optional[str]:
    """ "2.3 GB", or MB under 1 GB; None when the size is not known."""
    if num_bytes is None:
        return None
    if num_bytes >= 1e9:
        return f"{num_bytes / 1e9:.1f} GB"
    return f"{num_bytes / 1e6:.1f} MB"


def format_value(value) -> str:
    """A metadata value as table text: lists joined, None as a dash, booleans as yes/no."""
    if value is None:
        return UNKNOWN
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, (list, tuple)):
        return ", ".join(format_value(v) for v in value) if value else UNKNOWN
    if isinstance(value, dict):
        parts = [f"{k}: {format_value(v)}" for k, v in value.items() if v is not None]
        return ", ".join(parts) if parts else UNKNOWN
    return str(value)


def _format_time(value) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        try:
            return f"{datetime.fromtimestamp(value):%Y-%m-%d %H:%M:%S}"
        except (OverflowError, OSError, ValueError):
            pass
    return format_value(value)


def key_label(key: str) -> str:
    return _KEY_LABELS.get(key) or str(key).replace("_", " ").capitalize()


def _metadata_value(key: str, value) -> str:
    return _format_time(value) if key == "start_time" else format_value(value)


def metadata_rows(entry: HistoryEntry, status_label: str) -> List[Tuple[str, str]]:
    """(label, value) rows for the details table: a protocol run's identity first; then every metadata key in
    RECORDED_METADATA_KEYS order, then the extra keys; then the size on disk when known and the status."""
    rows: List[Tuple[str, str]] = []
    if entry.kind == "protocol_run":
        protocol = entry.protocol if isinstance(entry.protocol, dict) else {}
        rows += [
            ("Protocol", format_value(protocol.get("protocol"))),
            ("Run name", format_value(protocol.get("run_name") or entry.name)),
            ("Run folder", entry.folder or UNKNOWN),
            ("Outcome", entry.outcome.capitalize()),
            ("Rounds", str(len(entry.rounds))),
        ]
    md = entry.metadata or {}
    ordered = [k for k in RECORDED_METADATA_KEYS if k in md] + [k for k in md if k not in RECORDED_METADATA_KEYS]
    rows += [(key_label(k), _metadata_value(k, md[k])) for k in ordered]
    size = format_size(entry.bytes_on_disk)
    if size is not None:
        rows.append(("Size", size))
    rows.append(("Status", f"{status_label} — {entry.reason}" if entry.reason else status_label))
    return rows


def _escape(text) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def metadata_html(entry: HistoryEntry, status_label: str) -> str:
    """The name, the folder and the metadata rows as a two-column HTML table."""
    # A zero-width space after each separator lets the label wrap a long path at folder boundaries.
    wrappable = _escape(entry.folder).replace(os.sep, os.sep + "​")
    cells = "".join(
        f"<tr><td style='padding-right:8px; color:gray'>{_escape(k)}</td><td>{_escape(v)}</td></tr>"
        for k, v in metadata_rows(entry, status_label)
    )
    return f"<b>{_escape(entry.name)}</b><br><small>{wrappable}</small><table>{cells}</table>"


def round_cells(entry: HistoryEntry, status_label: str) -> List[str]:
    """A round's Round, Step, Attempt, Started and Outcome cells."""
    protocol = entry.protocol if isinstance(entry.protocol, dict) else entry.metadata.get("protocol")
    protocol = protocol if isinstance(protocol, dict) else {}
    started = f"{datetime.fromtimestamp(entry.started_at):%Y-%m-%d %H:%M}" if entry.started_at else ""
    return [
        "" if protocol.get("round") is None else str(protocol["round"]),
        "" if protocol.get("step") is None else str(protocol["step"]),
        "" if protocol.get("attempt") is None else str(protocol["attempt"]),
        started,
        status_label,
    ]


# ---------------------------------------------------------------------------------------------------------
# What each action may do (pure)
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionState:
    enabled: bool
    reason: str = ""  # why not, shown as the tooltip and in the status line when tried anyway


ACTIONS = ("open_in_squid", "open_in_window", "show_folder", "load_settings", "copy_path", "locate", "remove")
NO_SELECTION = {name: ActionState(False, "Select an acquisition first") for name in ACTIONS}


def open_target(entry: HistoryEntry) -> HistoryEntry:
    """What the open and load actions act on: a protocol run's latest round, else the entry itself."""
    return entry.rounds[-1] if entry.kind == "protocol_run" and entry.rounds else entry


def _offline_reason(availability: str) -> str:
    if availability == "drive_offline":
        return "The drive holding this acquisition is not connected"
    return "The folder is missing; use Locate… if it was moved"


def action_states(
    entry: HistoryEntry,
    availability: str,
    *,
    target_availability: Optional[str] = None,
    has_settings: bool = False,
    squid: Tuple[bool, str] = (True, ""),
    window: Tuple[bool, str] = (True, ""),
) -> Dict[str, ActionState]:
    """Which actions the entry allows, and why not. availability is the entry's folder's; target_availability that
    of open_target(entry)'s folder (the same folder unless it is a protocol run); has_settings whether that folder
    holds an acquisition.yaml; squid / window whether those viewers can open anything at all, and why not."""
    target = open_target(entry)
    target_availability = availability if target_availability is None else target_availability
    running = entry.is_running or target.is_running

    def opener(viewer: Tuple[bool, str]) -> ActionState:
        if running:
            return ActionState(False, RUNNING_REASON)
        if target_availability != "online":
            return ActionState(False, _offline_reason(target_availability))
        return ActionState(bool(viewer[0]), "" if viewer[0] else viewer[1])

    if running:
        settings = ActionState(False, RUNNING_REASON)
    elif target_availability != "online":
        settings = ActionState(False, _offline_reason(target_availability))
    elif not has_settings:
        settings = ActionState(False, f"This folder has no {YAML_FILE}")
    else:
        settings = ActionState(True)
    online = availability == "online"
    return {
        "open_in_squid": opener(squid),
        "open_in_window": opener(window),
        "show_folder": ActionState(online, "" if online else _offline_reason(availability)),
        "load_settings": settings,
        "copy_path": ActionState(bool(entry.folder), "" if entry.folder else "No folder recorded"),
        "locate": ActionState(not running, RUNNING_REASON if running else ""),
        "remove": ActionState(not running, RUNNING_REASON if running else ""),
    }


# ---------------------------------------------------------------------------------------------------------
# The pane
# ---------------------------------------------------------------------------------------------------------


def _read_yaml_text(folder: str) -> Optional[str]:
    path = os.path.join(folder, YAML_FILE)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(_MAX_YAML_BYTES)
    except OSError:
        return None


def _scaled(pixmap: QPixmap) -> QPixmap:
    return pixmap.scaled(PREVIEW_SIDE, PREVIEW_SIDE, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def _chip_style(rgb: Tuple[int, int, int]) -> str:
    r, g, b = rgb
    return (
        f"QPushButton {{ border: 2px solid rgb({r},{g},{b}); border-radius: 8px; padding: 1px 8px; }}"
        f"QPushButton:checked {{ background-color: rgba({r},{g},{b},110); }}"
        "QPushButton:disabled { color: gray; }"
    )


class _DetailsEvents(QObject):
    loaded = Signal(int, str, object)  # token, "png" | "yaml" | "planes", payload


class GalleryDetailsPane(QWidget):
    """The details of one history entry. submit(fn, *args) runs fn on a worker thread; emit(signal, *args) hands a
    worker's result to the GUI thread (and drops it once the gallery is closed)."""

    round_open_in_squid = Signal(str)  # a round's folder
    round_open_in_window = Signal(str)

    ROUND_COLUMNS = ("Round", "Step", "Attempt", "Started", "Outcome", "Open")
    _COL_OPEN = 5

    def __init__(self, submit: Callable, emit: Callable, parent=None):
        super().__init__(parent)
        self._submit = submit
        self._emit = emit
        self._token = 0
        self._key = None
        self._planes: Dict[str, np.ndarray] = {}
        self._placeholder: Optional[QPixmap] = None
        self.preview_source = "none"  # "none" | "placeholder" | "png" | "planes"
        self.preview_array: Optional[np.ndarray] = None  # the composite shown, when built from planes
        self.yaml_loaded = False
        self.channel_chips: List[QPushButton] = []
        self._events = _DetailsEvents()
        self._events.loaded.connect(self._on_loaded)
        self._build_ui()
        self.clear()

    def _build_ui(self) -> None:
        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(PREVIEW_SIDE // 2, PREVIEW_SIDE // 2)
        self.preview_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._chips_layout = QHBoxLayout()
        self._chips_layout.setContentsMargins(0, 0, 0, 0)
        self._chips_layout.addStretch(1)

        self.metadata_label = QLabel()
        self.metadata_label.setWordWrap(True)
        self.metadata_label.setTextFormat(Qt.RichText)
        self.metadata_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.metadata_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        self.rounds_table = QTableWidget(0, len(self.ROUND_COLUMNS))
        self.rounds_table.setHorizontalHeaderLabels(list(self.ROUND_COLUMNS))
        self.rounds_table.verticalHeader().setVisible(False)
        self.rounds_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.rounds_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.rounds_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.rounds_table.setMinimumHeight(120)

        self.details_toggle = QPushButton("Details")
        self.details_toggle.setCheckable(True)
        self.details_toggle.setToolTip(f"Show the run's {YAML_FILE}")
        self.raw_yaml_text = QPlainTextEdit()
        self.raw_yaml_text.setReadOnly(True)
        self.raw_yaml_text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.raw_yaml_text.setMinimumHeight(160)
        self.raw_yaml_text.setHidden(True)
        self.details_toggle.toggled.connect(lambda checked: self.raw_yaml_text.setHidden(not checked))

        self.btn_open_in_squid = QPushButton("Open in Squid")
        self.btn_open_in_window = QPushButton("Open in window")
        self.btn_show_folder = QPushButton("Show folder")
        self.btn_load_settings = QPushButton("Load settings")
        self.btn_load_settings.setStatusTip("Load the acquisition's settings into the acquisition tab")
        open_row = QHBoxLayout()
        open_row.addWidget(self.btn_open_in_squid)
        open_row.addWidget(self.btn_open_in_window)
        other_row = QHBoxLayout()
        other_row.addWidget(self.btn_show_folder)
        other_row.addWidget(self.btn_load_settings)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.addWidget(self.preview_label)
        layout.addLayout(self._chips_layout)
        layout.addLayout(open_row)
        layout.addLayout(other_row)
        layout.addWidget(self.metadata_label)
        layout.addWidget(self.rounds_table)
        layout.addWidget(self.details_toggle)
        layout.addWidget(self.raw_yaml_text)
        layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    # ----- showing an entry -----

    def clear(self) -> None:
        self._token += 1
        self._key = None
        self._planes = {}
        self.preview_array = None
        self.preview_source = "none"
        self.preview_label.clear()
        self._set_chips([], enabled=False)
        self.metadata_label.setText("Select an acquisition.")
        self.rounds_table.setRowCount(0)
        self.rounds_table.setHidden(True)
        self.raw_yaml_text.setPlainText("")
        self.raw_yaml_text.setPlaceholderText("")
        self.yaml_loaded = False
        self.set_actions(NO_SELECTION)

    def show_entry(
        self,
        entry: HistoryEntry,
        status_label: str,
        *,
        placeholder: QPixmap,
        preview_png: Optional[str],
        data_folder: Optional[str],
        planes_allowed: bool,
        preview_mtime: Optional[float] = None,
    ) -> None:
        """Show the entry: its metadata now; the preview PNG, the YAML and (when planes_allowed) the per-channel
        planes of data_folder's overview from a worker. data_folder is None when the folder cannot be read.
        preview_mtime (read by the loading worker) makes a preview replaced under the same name show again."""
        self.metadata_label.setText(metadata_html(entry, status_label))
        key = (entry.id, entry.folder, data_folder, entry.preview, planes_allowed, preview_mtime)
        if key == self._key:
            return  # same images and files: keep what is shown (and the chips the user set)
        self._key = key
        self._token += 1
        token = self._token
        self._planes = {}
        self.preview_array = None
        self._placeholder = placeholder
        self.preview_label.setPixmap(_scaled(placeholder))
        self.preview_source = "placeholder"
        self._set_chips(list(entry.preview_channels) if entry.preview else [], enabled=False)
        self.raw_yaml_text.setPlainText("")
        if data_folder is None:
            self.raw_yaml_text.setPlaceholderText(f"No {YAML_FILE}: the folder is offline or missing.")
            self.yaml_loaded = True
        else:
            self.raw_yaml_text.setPlaceholderText(f"Reading {YAML_FILE}…")
            self.yaml_loaded = False
        if preview_png is None and data_folder is None:
            return
        self._submit(self._load_in_background, token, preview_png, data_folder, data_folder if planes_allowed else None)

    def _load_in_background(
        self, token: int, preview_png: Optional[str], yaml_folder: Optional[str], planes_folder: Optional[str]
    ) -> None:
        if token != self._token:
            return
        if preview_png is not None:
            array = load_thumbnail_array(preview_png, PREVIEW_SIDE)
            if array is not None:
                self._emit(self._events.loaded, token, "png", array)
        if yaml_folder is not None and token == self._token:
            self._emit(self._events.loaded, token, "yaml", _read_yaml_text(yaml_folder))
        if planes_folder is not None and token == self._token:
            try:
                planes = load_preview_planes(planes_folder, PREVIEW_SIDE)[1]
            except Exception:
                _log.exception(f"Reading the preview planes of {planes_folder} failed")
                planes = {}
            if planes:
                self._emit(self._events.loaded, token, "planes", planes)

    def _on_loaded(self, token: int, kind: str, payload) -> None:
        if token != self._token:
            return  # a newer selection (or a cleared pane) wins
        if kind == "png":
            if self.preview_source != "planes":
                self.preview_label.setPixmap(_scaled(array_to_pixmap(payload)))
                self.preview_source = "png"
        elif kind == "yaml":
            self.raw_yaml_text.setPlainText(payload or "")
            if payload is None:
                self.raw_yaml_text.setPlaceholderText(f"This folder has no {YAML_FILE}.")
            self.yaml_loaded = True
        elif kind == "planes" and payload:
            self._planes = dict(payload)
            self._set_chips(list(self._planes), enabled=True)
            self._recomposite()

    # ----- channel chips -----

    def _set_chips(self, channels: Sequence[str], *, enabled: bool) -> None:
        for chip in self.channel_chips:
            self._chips_layout.removeWidget(chip)
            chip.deleteLater()
        self.channel_chips = []
        for i, name in enumerate(channels):
            chip = QPushButton(str(name).replace("&", "&&"))
            chip.setProperty("channel", str(name))
            chip.setCheckable(True)
            chip.setChecked(True)
            chip.setEnabled(enabled)
            chip.setStyleSheet(_chip_style(channel_rgb(str(name))))
            chip.setToolTip(
                "Show or hide this channel" if enabled else "Channels can be toggled once the overview is read"
            )
            chip.toggled.connect(lambda _checked: self._recomposite())
            self._chips_layout.insertWidget(i, chip)
            self.channel_chips.append(chip)

    def _recomposite(self) -> None:
        if not self._planes:
            return
        visible = {chip.property("channel") for chip in self.channel_chips if chip.isChecked()}
        self.preview_array = composite(self._planes, visible=visible)
        self.preview_label.setPixmap(_scaled(array_to_pixmap(self.preview_array)))
        self.preview_source = "planes"

    # ----- actions and rounds -----

    @staticmethod
    def _apply(button: QPushButton, state: ActionState) -> None:
        button.setEnabled(state.enabled)
        button.setToolTip("" if state.enabled else state.reason)

    def set_actions(self, states: Dict[str, ActionState], *, squid_hidden: bool = False) -> None:
        self._apply(self.btn_open_in_squid, states["open_in_squid"])
        self._apply(self.btn_open_in_window, states["open_in_window"])
        self._apply(self.btn_show_folder, states["show_folder"])
        self._apply(self.btn_load_settings, states["load_settings"])
        self.btn_open_in_squid.setHidden(squid_hidden)

    def set_rounds(
        self,
        rounds: Optional[List[Tuple[HistoryEntry, str, ActionState, ActionState]]],
        *,
        squid_hidden: bool = False,
    ) -> None:
        """(round, status label, in-Squid state, in-window state) per round; None hides the table."""
        table = self.rounds_table
        table.setRowCount(0)
        table.setHidden(rounds is None)
        if rounds is None:
            return
        table.setRowCount(len(rounds))
        for r, (entry, label, squid, window) in enumerate(rounds):
            for c, text in enumerate(round_cells(entry, label)):
                item = QTableWidgetItem(text)
                item.setToolTip(entry.folder)
                table.setItem(r, c, item)
            cell = QWidget()
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(2, 0, 2, 0)
            for name, text, state, signal in (
                ("in_squid", "in Squid", squid, self.round_open_in_squid),
                ("in_window", "in window", window, self.round_open_in_window),
            ):
                button = QPushButton(text)
                button.setObjectName(name)
                self._apply(button, state)
                button.clicked.connect(lambda _checked=False, s=signal, f=entry.folder: s.emit(f))
                cell_layout.addWidget(button)
                if name == "in_squid":
                    button.setHidden(squid_hidden)
            table.setCellWidget(r, self._COL_OPEN, cell)
