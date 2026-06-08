import os

import yaml
from qtpy.QtCore import QEvent, QItemSelectionModel, QObject, QRect, Qt, QTimer
from qtpy.QtGui import QColor, QPalette
from qtpy.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)

import squid.logging

_log = squid.logging.get_logger(__name__)

_CACHE_PATH = "cache/channel_sequence.yaml"


def display_order(included_order, config_order):
    """Included channels first (in their order, filtered to those present in
    config_order), then the remaining config channels in config order."""
    config_set = set(config_order)
    included = [name for name in included_order if name in config_set]
    included_set = set(included)
    rest = [name for name in config_order if name not in included_set]
    return included + rest


def reconcile_included(included_order, config_order):
    """Drop names no longer present in config_order; preserve relative order."""
    config_set = set(config_order)
    return [name for name in included_order if name in config_set]


def load_cached_order(cache_key, path=_CACHE_PATH):
    """Return the cached list of included channel names for `cache_key`, or []."""
    try:
        if not os.path.exists(path):
            return []
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}
        value = data.get(cache_key, [])
        return list(value) if isinstance(value, list) else []
    except Exception as e:
        _log.warning(f"Failed to load channel sequence cache: {e}")
        return []


def save_cached_order(cache_key, names, path=_CACHE_PATH):
    """Persist included channel names for `cache_key`, merging with other keys."""
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        data = {}
        if os.path.exists(path):
            with open(path, "r") as f:
                data = yaml.safe_load(f) or {}
        data[cache_key] = list(names)
        with open(path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    except Exception as e:
        _log.warning(f"Failed to save channel sequence cache: {e}")


class ChannelOrderDelegate(QStyledItemDelegate):
    """Renders each channel row: a "[N]" acquisition-position badge in a fixed
    left gutter, the channel name at a fixed x-offset (names stay aligned), and
    for *selected* rows a pair of up/down (▲/▼) reorder controls at the right.
    A divider line is drawn under the last selected row. Clicks on the ▲/▼
    controls are routed to `controller.move_up/move_down(name)` via editorEvent;
    item data is never modified.
    """

    _GUTTER_SAMPLE = "[99]  "  # widest expected badge (assumes <= 99 selected channels)

    def __init__(self, list_widget, controller=None):
        super().__init__(list_widget)
        self._controller = controller

    def _position_and_count(self, index):
        """(1-based position of `index` among selected rows or None, total
        selected count)."""
        selected_rows = sorted(i.row() for i in self.parent().selectedIndexes())
        row = index.row()
        position = selected_rows.index(row) + 1 if row in selected_rows else None
        return position, len(selected_rows)

    def badge_for_index(self, index):
        position, _ = self._position_and_count(index)
        return None if position is None else f"[{position}]"

    @staticmethod
    def _arrow_color(palette, enabled):
        # Faded highlighted-text: reads as gray on the selection background and
        # adapts to the theme; dimmer when the move is disabled (sequence ends).
        color = QColor(palette.color(QPalette.HighlightedText))
        color.setAlpha(180 if enabled else 90)
        return color

    def _draw_arrow(self, painter, palette, rect, glyph, enabled):
        painter.setPen(self._arrow_color(palette, enabled))
        painter.drawText(rect, Qt.AlignCenter, glyph)

    def _is_last_selected_row(self, index):
        selected_rows = [i.row() for i in self.parent().selectedIndexes()]
        return bool(selected_rows) and index.row() == max(selected_rows)

    @classmethod
    def _gutter_width(cls, font_metrics):
        s = cls._GUTTER_SAMPLE
        if hasattr(font_metrics, "horizontalAdvance"):  # Qt >= 5.11
            return font_metrics.horizontalAdvance(s)
        return font_metrics.width(s)

    @staticmethod
    def _arrow_rects(rect):
        """The (up, down) clickable arrow rects at the right of a row rect."""
        w = rect.height()  # square cells, one per arrow
        up = QRect(rect.right() - 2 * w, rect.top(), w, rect.height())
        down = QRect(rect.right() - w, rect.top(), w, rect.height())
        return up, down

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setWidth(size.width() + self._gutter_width(option.fontMetrics))
        return size

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        name = opt.text

        opt.text = ""
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, widget)

        text_rect = style.subElementRect(QStyle.SE_ItemViewItemText, opt, widget)
        fm = opt.fontMetrics
        gutter = self._gutter_width(fm)
        alignment = Qt.AlignVCenter | Qt.AlignLeft

        painter.save()
        selected = bool(opt.state & QStyle.State_Selected)
        text_pen = opt.palette.color(QPalette.HighlightedText if selected else QPalette.Text)
        painter.setPen(text_pen)
        position, count = self._position_and_count(index)
        name_rect = QRect(text_rect)
        name_rect.setLeft(text_rect.left() + gutter)
        if position is not None:
            painter.drawText(text_rect, alignment, f"[{position}]")
            # reserve room on the right for the up/down arrows; gray them out at
            # the ends of the sequence (no up for the first, no down for the last)
            up_rect, down_rect = self._arrow_rects(option.rect)
            name_rect.setRight(up_rect.left() - 4)
            self._draw_arrow(painter, opt.palette, up_rect, "▲", enabled=position > 1)
            self._draw_arrow(painter, opt.palette, down_rect, "▼", enabled=position < count)
            painter.setPen(text_pen)  # restore pen for the channel name
        painter.drawText(name_rect, alignment, fm.elidedText(name, Qt.ElideRight, max(0, name_rect.width())))
        painter.restore()

        if self._is_last_selected_row(index):
            painter.save()
            painter.setPen(opt.palette.color(QPalette.Mid))
            r = option.rect
            painter.drawLine(r.left(), r.bottom(), r.right(), r.bottom())
            painter.restore()

    def editorEvent(self, event, model, option, index):
        # Route clicks on the per-row ▲/▼ controls (selected rows only) to the
        # controller, consuming press AND release so the click does not also
        # toggle the row's selection.
        if self._controller is not None and self.badge_for_index(index) is not None:
            if event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
                pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                up_rect, down_rect = self._arrow_rects(option.rect)
                if up_rect.contains(pos) or down_rect.contains(pos):
                    if event.type() == QEvent.MouseButtonRelease:
                        name = index.data()
                        if up_rect.contains(pos):
                            self._controller.move_up(name)
                        else:
                            self._controller.move_down(name)
                    return True
        return super().editorEvent(event, model, option, index)


class ChannelSequenceController(QObject):
    """Manages a multipoint widget's channel QListWidget as an ordered
    acquisition sequence: selected channels form an ordered block at the top,
    reordered with the per-row up/down controls drawn by ChannelOrderDelegate
    (which call move_up/move_down); `included_order` is the single source of
    truth. Persists to a per-widget cache. Parent it to the list widget."""

    def __init__(self, list_widget, get_names, cache_key, cache_path=_CACHE_PATH):
        super().__init__(list_widget)
        self._list = list_widget
        self._get_names = get_names
        self._cache_key = cache_key
        self._cache_path = cache_path
        self._suppress = False
        self._pending_rebuild = False

        list_widget.setSelectionMode(QAbstractItemView.MultiSelection)
        list_widget.setItemDelegate(ChannelOrderDelegate(list_widget, controller=self))

        self._included_order = reconcile_included(load_cached_order(cache_key, self._cache_path), self._config_order())
        self._rebuild()

        list_widget.itemSelectionChanged.connect(self._on_selection_changed)
        # Disable drag-to-select (see eventFilter): clicks toggle channels, but
        # dragging across rows must not rubber-band a whole range. Cache the
        # viewport so eventFilter compares by identity and never dereferences
        # the (possibly torn-down) list.
        self._viewport = list_widget.viewport()
        self._viewport.installEventFilter(self)

    def eventFilter(self, obj, event):
        # Swallow mouse-move events over the list viewport while a button is
        # held, so a click-drag does not extend the selection across rows.
        # Discrete clicks (press/release) and the per-row arrow clicks are
        # unaffected.
        if obj is self._viewport and event.type() == QEvent.MouseMove and event.buttons():
            return True
        return super().eventFilter(obj, event)

    def _config_order(self):
        return list(self._get_names())

    def ordered_selected_names(self):
        # Pure read: `_included_order` is updated synchronously on selection, so
        # this is always correct even while a visual rebuild is still pending.
        config_set = set(self._config_order())
        return [n for n in self._included_order if n in config_set]

    def set_included_order(self, names):
        self._included_order = reconcile_included(list(names), self._config_order())
        self._rebuild()
        self._save()

    def refresh(self):
        self._included_order = reconcile_included(self._included_order, self._config_order())
        self._rebuild()
        self._save()

    def _rebuild(self):
        self._suppress = True
        # Save/restore the prior blockSignals state (blockSignals is not
        # ref-counted), so set_included_order()/refresh() are safe to call from a
        # context that has already blocked the list's signals (e.g. YAML drop).
        was_blocked = self._list.blockSignals(True)
        current = self._list.currentItem()
        current_name = current.text() if current is not None else None
        try:
            config_order = self._config_order()
            order = display_order(self._included_order, config_order)
            included_set = {n for n in self._included_order if n in set(config_order)}
            self._list.clear()
            for name in order:
                self._list.addItem(name)
            for i in range(self._list.count()):
                item = self._list.item(i)
                if item.text() in included_set:
                    item.setSelected(True)
            # Restore the focused (current) row without disturbing the selection,
            # so repeated up/down presses keep acting on the same channel.
            if current_name is not None:
                for i in range(self._list.count()):
                    if self._list.item(i).text() == current_name:
                        self._list.setCurrentRow(i, QItemSelectionModel.NoUpdate)
                        break
        finally:
            self._list.blockSignals(was_blocked)
            self._suppress = False
        self._list.viewport().update()

    def _on_selection_changed(self):
        if self._suppress:
            return
        # Update _included_order synchronously (so ordered_selected_names() is
        # always correct), but defer the visual rebuild to the next event-loop
        # turn: restructuring the list inside its own itemSelectionChanged
        # handler (during mouse processing) is unsafe, and deferring also
        # coalesces rapid selections.
        selected = [i.text() for i in self._list.selectedItems()]
        selected_set = set(selected)
        new_order = [n for n in self._included_order if n in selected_set]
        existing = set(new_order)
        for n in selected:
            if n not in existing:
                new_order.append(n)
                existing.add(n)
        self._included_order = new_order
        self._save()
        if not self._pending_rebuild:
            self._pending_rebuild = True
            QTimer.singleShot(0, self._flush_selection_rebuild)

    def _flush_selection_rebuild(self):
        self._pending_rebuild = False
        self._rebuild()

    def move_up(self, name):
        """Move `name` one position earlier in the acquisition sequence."""
        self._move(name, -1)

    def move_down(self, name):
        """Move `name` one position later in the acquisition sequence."""
        self._move(name, 1)

    def _move(self, name, delta):
        if name not in self._included_order:
            return
        i = self._included_order.index(name)
        j = i + delta
        if not (0 <= j < len(self._included_order)):
            return
        self._included_order[i], self._included_order[j] = (
            self._included_order[j],
            self._included_order[i],
        )
        self._rebuild()
        self._save()

    def _save(self):
        save_cached_order(self._cache_key, self._included_order, self._cache_path)


def enable_channel_sequence(list_widget, get_names, cache_key, cache_path=_CACHE_PATH):
    """Turn a channel QListWidget into an ordered acquisition sequence with
    per-row up/down reorder controls. `get_names` returns the channel names in
    config order. Returns the controller (store it on the widget; read
    ordered_selected_names() for acquisition, call set_included_order() to
    restore a saved sequence)."""
    return ChannelSequenceController(list_widget, get_names, cache_key, cache_path)
