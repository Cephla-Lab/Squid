import control.channel_sequence as cs
from qtpy.QtCore import QEvent, QPointF, QRect, Qt
from qtpy.QtGui import QMouseEvent, QPainter, QPixmap
from qtpy.QtTest import QTest
from qtpy.QtWidgets import QAbstractItemView, QListWidget, QStyleOptionViewItem

from control.channel_sequence import ChannelOrderDelegate, enable_channel_sequence


class TestPureOrdering:
    def test_display_order_included_first_then_config_rest(self):
        assert cs.display_order(["c", "a"], ["a", "b", "c", "d"]) == ["c", "a", "b", "d"]

    def test_display_order_filters_unknown_included(self):
        assert cs.display_order(["x", "b"], ["a", "b", "c"]) == ["b", "a", "c"]

    def test_display_order_empty_included_is_config_order(self):
        assert cs.display_order([], ["a", "b"]) == ["a", "b"]

    def test_reconcile_included_drops_missing_keeps_order(self):
        assert cs.reconcile_included(["c", "x", "a"], ["a", "b", "c"]) == ["c", "a"]


class TestCacheIO:
    def test_save_then_load_roundtrip(self, tmp_path):
        path = str(tmp_path / "channel_sequence.yaml")
        cs.save_cached_order("flexible", ["c", "a"], path=path)
        assert cs.load_cached_order("flexible", path=path) == ["c", "a"]

    def test_load_missing_file_returns_empty(self, tmp_path):
        assert cs.load_cached_order("flexible", path=str(tmp_path / "nope.yaml")) == []

    def test_save_merges_keys(self, tmp_path):
        path = str(tmp_path / "channel_sequence.yaml")
        cs.save_cached_order("flexible", ["a"], path=path)
        cs.save_cached_order("wellplate", ["b"], path=path)
        assert cs.load_cached_order("flexible", path=path) == ["a"]
        assert cs.load_cached_order("wellplate", path=path) == ["b"]


def _list(qtbot, names, selected_rows):
    lw = QListWidget()
    qtbot.addWidget(lw)
    lw.addItems(names)
    lw.setSelectionMode(QAbstractItemView.MultiSelection)
    for r in selected_rows:
        lw.item(r).setSelected(True)
    return lw


def _badge(delegate, lw, row):
    return delegate.badge_for_index(lw.model().index(row, 0))


class TestDelegate:
    def test_badge_numbers_selected_rows_top_to_bottom(self, qtbot):
        lw = _list(qtbot, ["a", "b", "c", "d"], selected_rows=[0, 1, 2])
        delegate = ChannelOrderDelegate(lw)
        assert _badge(delegate, lw, 0) == "[1]"
        assert _badge(delegate, lw, 1) == "[2]"
        assert _badge(delegate, lw, 2) == "[3]"
        assert _badge(delegate, lw, 3) is None

    def test_sizehint_reserves_gutter(self, qtbot):
        lw = _list(qtbot, ["a"], selected_rows=[])
        delegate = ChannelOrderDelegate(lw)
        opt = QStyleOptionViewItem()
        idx = lw.model().index(0, 0)
        delegate.initStyleOption(opt, idx)
        assert delegate.sizeHint(opt, idx).width() > 0

    def test_paint_runs_without_error(self, qtbot):
        lw = _list(qtbot, ["a", "b", "c"], selected_rows=[0, 1])
        delegate = ChannelOrderDelegate(lw)
        pixmap = QPixmap(220, 22)
        painter = QPainter(pixmap)
        try:
            for row in range(3):
                opt = QStyleOptionViewItem()
                opt.rect = QRect(0, 0, 220, 22)
                idx = lw.model().index(row, 0)
                delegate.initStyleOption(opt, idx)
                delegate.paint(painter, opt, idx)
        finally:
            painter.end()


def _controller(qtbot, names, cache_key="flexible", path=None):
    lw = QListWidget()
    qtbot.addWidget(lw)
    kwargs = {} if path is None else {"cache_path": path}
    ctrl = enable_channel_sequence(lw, lambda: list(names), cache_key, **kwargs)
    return lw, ctrl


def _rows(lw):
    return [lw.item(i).text() for i in range(lw.count())]


def _row_of(lw, name):
    return next(i for i in range(lw.count()) if lw.item(i).text() == name)


class TestController:
    def test_initial_population_is_config_order_nothing_selected(self, qtbot, tmp_path):
        lw, ctrl = _controller(qtbot, ["a", "b", "c"], path=str(tmp_path / "c.yaml"))
        assert _rows(lw) == ["a", "b", "c"]
        assert ctrl.ordered_selected_names() == []

    def test_selecting_floats_to_top_block_in_selection_order(self, qtbot, tmp_path):
        lw, ctrl = _controller(qtbot, ["a", "b", "c", "d"], path=str(tmp_path / "c.yaml"))
        lw.item(2).setSelected(True)  # c
        lw.item(0).setSelected(True)  # a
        assert ctrl.ordered_selected_names() == ["c", "a"]
        # the visual regroup is deferred to the event loop; wait for it
        qtbot.waitUntil(lambda: _rows(lw)[:2] == ["c", "a"])

    def test_deselect_drops_out_of_block(self, qtbot, tmp_path):
        lw, ctrl = _controller(qtbot, ["a", "b", "c"], path=str(tmp_path / "c.yaml"))
        lw.item(0).setSelected(True)
        lw.item(1).setSelected(True)
        lw.item(0).setSelected(False)
        assert ctrl.ordered_selected_names() == ["b"]

    def test_set_included_order_restores_order_and_highlight(self, qtbot, tmp_path):
        lw, ctrl = _controller(qtbot, ["a", "b", "c"], path=str(tmp_path / "c.yaml"))
        ctrl.set_included_order(["c", "a"])
        assert ctrl.ordered_selected_names() == ["c", "a"]
        assert _rows(lw)[:2] == ["c", "a"]
        assert sorted(i.text() for i in lw.selectedItems()) == ["a", "c"]

    def test_set_included_order_filters_unknown_names(self, qtbot, tmp_path):
        lw, ctrl = _controller(qtbot, ["a", "b"], path=str(tmp_path / "c.yaml"))
        ctrl.set_included_order(["x", "b"])
        assert ctrl.ordered_selected_names() == ["b"]

    def test_move_up_reorders_within_block(self, qtbot, tmp_path):
        lw, ctrl = _controller(qtbot, ["a", "b", "c", "d"], path=str(tmp_path / "c.yaml"))
        ctrl.set_included_order(["a", "b", "c"])
        ctrl.move_up("b")
        assert ctrl.ordered_selected_names() == ["b", "a", "c"]
        assert _rows(lw)[:3] == ["b", "a", "c"]

    def test_move_down_reorders_within_block(self, qtbot, tmp_path):
        lw, ctrl = _controller(qtbot, ["a", "b", "c", "d"], path=str(tmp_path / "c.yaml"))
        ctrl.set_included_order(["a", "b", "c"])
        ctrl.move_down("b")
        assert ctrl.ordered_selected_names() == ["a", "c", "b"]

    def test_move_up_at_top_is_noop(self, qtbot, tmp_path):
        lw, ctrl = _controller(qtbot, ["a", "b", "c"], path=str(tmp_path / "c.yaml"))
        ctrl.set_included_order(["a", "b"])
        ctrl.move_up("a")
        assert ctrl.ordered_selected_names() == ["a", "b"]

    def test_move_down_at_bottom_is_noop(self, qtbot, tmp_path):
        lw, ctrl = _controller(qtbot, ["a", "b", "c"], path=str(tmp_path / "c.yaml"))
        ctrl.set_included_order(["a", "b"])
        ctrl.move_down("b")
        assert ctrl.ordered_selected_names() == ["a", "b"]

    def test_move_unincluded_is_noop(self, qtbot, tmp_path):
        lw, ctrl = _controller(qtbot, ["a", "b", "c"], path=str(tmp_path / "c.yaml"))
        ctrl.set_included_order(["a"])
        ctrl.move_down("b")  # b is not included
        assert ctrl.ordered_selected_names() == ["a"]

    def test_repeated_move_up(self, qtbot, tmp_path):
        lw, ctrl = _controller(qtbot, ["a", "b", "c"], path=str(tmp_path / "c.yaml"))
        ctrl.set_included_order(["a", "b", "c"])
        ctrl.move_up("c")
        ctrl.move_up("c")
        assert ctrl.ordered_selected_names() == ["c", "a", "b"]

    def test_refresh_drops_missing_and_appends_new(self, qtbot, tmp_path):
        names = ["a", "b", "c"]
        lw, ctrl = _controller(qtbot, names, path=str(tmp_path / "c.yaml"))
        ctrl.set_included_order(["c", "a"])
        names[:] = ["a", "c", "e"]
        ctrl.refresh()
        assert ctrl.ordered_selected_names() == ["c", "a"]
        assert _rows(lw) == ["c", "a", "e"]

    def test_persistence_restored_on_new_controller(self, qtbot, tmp_path):
        path = str(tmp_path / "c.yaml")
        lw1, ctrl1 = _controller(qtbot, ["a", "b", "c"], path=path)
        ctrl1.set_included_order(["c", "a"])
        lw2, ctrl2 = _controller(qtbot, ["a", "b", "c"], path=path)
        assert ctrl2.ordered_selected_names() == ["c", "a"]

    def test_rebuild_does_not_emit_spurious_selection_signals(self, qtbot, tmp_path):
        lw, ctrl = _controller(qtbot, ["a", "b", "c"], path=str(tmp_path / "c.yaml"))
        fired = []
        lw.itemSelectionChanged.connect(lambda: fired.append(1))
        ctrl.set_included_order(["b"])
        assert fired == []


def _release_at(point):
    return QMouseEvent(
        QEvent.MouseButtonRelease,
        QPointF(point),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )


class TestArrows:
    def test_arrow_rects_right_aligned_and_disjoint(self):
        up, down = ChannelOrderDelegate._arrow_rects(QRect(0, 0, 200, 20))
        assert up.left() >= 0
        assert up.right() <= down.left()  # up sits left of down, no overlap
        assert down.right() <= 200

    def test_up_arrow_click_moves_channel_earlier(self, qtbot, tmp_path):
        lw, ctrl = _controller(qtbot, ["a", "b", "c"], path=str(tmp_path / "c.yaml"))
        ctrl.set_included_order(["a", "b", "c"])
        delegate = lw.itemDelegate()
        opt = QStyleOptionViewItem()
        opt.rect = QRect(0, 0, 200, 20)
        index = lw.model().index(_row_of(lw, "b"), 0)
        up_rect, _ = ChannelOrderDelegate._arrow_rects(opt.rect)

        handled = delegate.editorEvent(_release_at(up_rect.center()), lw.model(), opt, index)
        assert handled
        assert ctrl.ordered_selected_names() == ["b", "a", "c"]

    def test_down_arrow_click_moves_channel_later(self, qtbot, tmp_path):
        lw, ctrl = _controller(qtbot, ["a", "b", "c"], path=str(tmp_path / "c.yaml"))
        ctrl.set_included_order(["a", "b", "c"])
        delegate = lw.itemDelegate()
        opt = QStyleOptionViewItem()
        opt.rect = QRect(0, 0, 200, 20)
        index = lw.model().index(_row_of(lw, "b"), 0)
        _, down_rect = ChannelOrderDelegate._arrow_rects(opt.rect)

        handled = delegate.editorEvent(_release_at(down_rect.center()), lw.model(), opt, index)
        assert handled
        assert ctrl.ordered_selected_names() == ["a", "c", "b"]

    def test_click_outside_arrows_is_not_consumed(self, qtbot, tmp_path):
        lw, ctrl = _controller(qtbot, ["a", "b", "c"], path=str(tmp_path / "c.yaml"))
        ctrl.set_included_order(["a", "b", "c"])
        delegate = lw.itemDelegate()
        opt = QStyleOptionViewItem()
        opt.rect = QRect(0, 0, 200, 20)
        index = lw.model().index(_row_of(lw, "b"), 0)

        handled = delegate.editorEvent(_release_at(QPointF(10, 10)), lw.model(), opt, index)
        assert not handled  # falls through to default handling (selection toggle)
        assert ctrl.ordered_selected_names() == ["a", "b", "c"]


class TestArrowClickIntegration:
    """End-to-end: a real mouse click on an arrow (press+release through the
    view) must reorder WITHOUT toggling the row's selection off. The unit tests
    above call editorEvent(release) directly and so cannot catch a regression in
    the press-consumption that prevents the selection toggle."""

    def test_real_click_on_arrow_moves_without_deselecting(self, qtbot, tmp_path):
        lw, ctrl = _controller(qtbot, ["a", "b", "c"], path=str(tmp_path / "c.yaml"))
        ctrl.set_included_order(["a", "b", "c"])
        lw.resize(360, 240)
        with qtbot.waitExposed(lw):
            lw.show()

        rect = lw.visualItemRect(lw.item(_row_of(lw, "a")))
        _, down_rect = ChannelOrderDelegate._arrow_rects(rect)
        QTest.mouseClick(lw.viewport(), Qt.LeftButton, Qt.NoModifier, down_rect.center())

        assert ctrl.ordered_selected_names() == ["b", "a", "c"]  # "a" moved later
        selected = [lw.item(i).text() for i in range(lw.count()) if lw.item(i).isSelected()]
        assert "a" in selected  # the arrow click must NOT have deselected "a"
