"""The Gallery tab lists the acquisition history, newest first, in a grid or a list, narrowed by status and text."""

import os
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest
import yaml
from qtpy.QtCore import Qt

import control._def
import control.widgets_gallery
from control.widgets_gallery import (
    STATUS_FILTERS,
    AcquisitionGalleryWidget,
    GalleryRow,
    GalleryView,
    build_rows,
    filter_counts,
    matches,
    status_label_for,
)
from squid.acquisition_history import HistoryEntry, HistoryStore

T0 = 1762596000.0


def _metadata(channels, objective="20x", image_format="TIFF images", sample="96 well plate"):
    return {
        "widget_type": "wellplate",
        "objective": objective,
        "magnification": 20.0,
        "wellplate_format": sample,
        "channels": list(channels),
        "num_regions": 2,
        "num_fovs": 8,
        "nz": 3,
        "nt": 2,
        "image_format": image_format,
    }


def _missing_drive_folder(tmp_path):
    """A folder on a drive that is not mounted (Windows), else a folder that is simply gone."""
    if os.name == "nt":
        for letter in "QZYXWVUTSRPONMLKJIHG":
            if not os.path.exists(f"{letter}:\\"):
                return f"{letter}:\\gone\\run", "Drive offline"
    return str(tmp_path / "gone" / "run"), "Folder missing"


def _write_png(store, name, shape=(48, 64, 3)):
    image = np.zeros(shape, dtype=np.uint8)
    image[:, :, 1] = 200
    assert cv2.imwrite(str(store.previews_dir / name), image)


@pytest.fixture
def store_with_entries(tmp_path):
    """Five entries: complete run with a preview, aborted at 38 %, running, offline, and a snap; newest first:
    running, snap, aborted, offline, complete."""
    store = HistoryStore(tmp_path / "history")
    folders = {}
    for name in ("complete", "aborted", "running", "snap"):
        folders[name] = tmp_path / "data" / name
        folders[name].mkdir(parents=True)
    offline_folder, offline_label = _missing_drive_folder(tmp_path)

    def started(id_, at, channels, kind="run", **extra):
        store.append(
            {
                "type": "started",
                "id": id_,
                "kind": kind,
                "name": id_,
                "folder": str(folders[id_]),
                "metadata": _metadata(channels, **extra),
                "at": at,
            }
        )

    def finished(id_, at, outcome, fraction):
        store.append(
            {"type": "finished", "id": id_, "outcome": outcome, "fraction_done": fraction, "ended_at": at, "at": at}
        )

    started("complete", T0, ["BF LED matrix full", "Fluorescence 488 nm Ex"])
    finished("complete", T0 + 100, "complete", 1.0)
    _write_png(store, "complete.png")
    store.append({"type": "preview", "id": "complete", "file": "complete.png", "channels": ["BF"], "at": T0 + 101})
    store.append(
        {
            "type": "imported",
            "id": "offline",
            "kind": "run",
            "name": "offline",
            "folder": offline_folder,
            "started_at": T0 + 1000,
            "outcome": "complete",
            "metadata": _metadata(["Fluorescence 561 nm Ex"], objective="10x"),
            "at": T0 + 1000,
        }
    )
    started("aborted", T0 + 2000, ["Fluorescence 638 nm Ex"], image_format="OME-Zarr")
    finished("aborted", T0 + 2100, "aborted", 0.38)
    started("snap", T0 + 3000, ["BF LED matrix full"], kind="snap")
    finished("snap", T0 + 3001, "complete", 1.0)
    started("running", T0 + 4000, ["Fluorescence 405 nm Ex"])
    _write_png(store, "running.png")
    store.append({"type": "preview", "id": "running", "file": "running.png", "channels": ["405"], "at": T0 + 4001})
    return SimpleNamespace(store=store, folders=folders, offline_label=offline_label)


@pytest.fixture
def gallery(qtbot, store_with_entries, tmp_path, monkeypatch):
    monkeypatch.setattr(
        control.widgets_gallery, "LAST_GALLERY_VIEW_CACHE", str(tmp_path / "cache" / "last_gallery_view.yaml")
    )
    monkeypatch.setattr(control._def, "RECORD_SNAPS_IN_HISTORY", True)
    widget = AcquisitionGalleryWidget(store_with_entries.store, import_root_provider=lambda: None)
    qtbot.addWidget(widget)
    widget.show()  # showEvent runs the first load
    qtbot.waitUntil(lambda: len(widget.rows()) == 5, timeout=10000)
    yield widget
    widget.shutdown()


def _reload_and_wait(widget, qtbot):
    widget.reload()
    qtbot.waitUntil(lambda: not widget._loading, timeout=10000)


def _ids(widget):
    return [row.entry.id for row in widget.rows()]


def _row(widget, entry_id):
    return next(row for row in widget.rows() if row.entry.id == entry_id)


# ---------------------------------------------------------------------------------------------------------
# Pure model
# ---------------------------------------------------------------------------------------------------------


def test_build_rows_labels_statuses(store_with_entries):
    rows = {row.entry.id: row for row in build_rows(store_with_entries.store.load(), running_fraction=0.38)}

    assert {k: r.status_label for k, r in rows.items()} == {
        "complete": "Complete",
        "aborted": "Aborted at 38%",
        "running": "Running 38%",
        "offline": store_with_entries.offline_label,
        "snap": "Snap · Complete",
    }
    assert rows["complete"].availability == "online"
    assert rows["offline"].availability in ("drive_offline", "folder_missing")
    assert "488" in rows["complete"].search_text and "20x" in rows["complete"].search_text
    assert "96 well plate" in rows["complete"].search_text and "tiff images" in rows["complete"].search_text
    assert build_rows(store_with_entries.store.load())[0].status_label == "Running"


def test_status_label_for_protocol_runs_and_unfinished_fractions():
    rounds = [HistoryEntry(id=f"r{i}", kind="run", name=f"r{i}", folder="x") for i in range(3)]
    protocol = HistoryEntry(id="p", kind="protocol_run", name="p", folder="x", outcome="running", rounds=rounds)
    assert status_label_for(protocol, "online", running_fraction=0.5) == "Running · round 3"
    aborted = HistoryEntry(id="a", kind="run", name="a", folder="x", outcome="aborted")
    assert status_label_for(aborted, "online") == "Aborted"
    assert status_label_for(aborted, "folder_missing") == "Folder missing"
    failed = HistoryEntry(id="f", kind="run", name="f", folder="x", outcome="failed")
    assert status_label_for(failed, "online") == "Failed"
    running = HistoryEntry(id="r", kind="run", name="r", folder="x", outcome="running")
    assert status_label_for(running, "drive_offline") == "Running"  # running wins over availability


def test_matches_filters_by_status_and_text():
    def row(id_, outcome, availability, channels, kind="run"):
        entry = HistoryEntry(id=id_, kind=kind, name=id_, folder="x", outcome=outcome, metadata={"channels": channels})
        return GalleryRow(
            entry=entry,
            availability=availability,
            status_label=status_label_for(entry, availability),
            search_text=" ".join([id_, *channels]).lower(),
        )

    online = row("plate", "complete", "online", ["Fluorescence 488 nm Ex"])
    gone = row("gone", "complete", "drive_offline", ["BF"])
    missing = row("missing", "aborted", "folder_missing", ["BF"])
    snap = row("snap", "complete", "online", ["BF"], kind="snap")

    assert [matches(r, "", "Offline") for r in (online, gone, missing, snap)] == [False, True, True, False]
    assert [matches(r, "", "Complete") for r in (online, gone, missing, snap)] == [True, False, False, True]
    assert not matches(missing, "", "Aborted")  # offline rows show under Offline only
    assert [matches(r, "", "Snaps") for r in (online, gone, missing, snap)] == [False, False, False, True]
    assert all(matches(r, "", "All") for r in (online, gone, missing, snap))
    assert matches(online, "488", "All") and not matches(gone, "488", "All")
    assert matches(online, "FLUORESCENCE", "Complete") and not matches(online, "488", "Failed")


def test_filter_counts_count_each_status(store_with_entries):
    counts = filter_counts(build_rows(store_with_entries.store.load()))

    assert counts == {"All": 5, "Running": 1, "Complete": 2, "Aborted": 1, "Failed": 0, "Offline": 1, "Snaps": 1}
    assert tuple(counts) == STATUS_FILTERS


# ---------------------------------------------------------------------------------------------------------
# The widget
# ---------------------------------------------------------------------------------------------------------


def test_set_running_progress_relabels_the_running_row(gallery):
    assert _row(gallery, "running").status_label == "Running"
    generation = gallery._generation

    gallery.set_running_progress(0.38)

    assert gallery._generation == generation  # no reload
    assert _row(gallery, "running").status_label == "Running 38%"
    assert gallery._table_status_items["running"].text() == "Running 38%"
    assert gallery._items["running"].toolTip().startswith("Running 38%")
    gallery.set_running_progress(None)
    assert _row(gallery, "running").status_label == "Running"


def test_gallery_lists_the_history_newest_first(gallery):
    assert _ids(gallery) == ["running", "snap", "aborted", "offline", "complete"]
    assert gallery.list_widget.count() == 5
    assert gallery.count_label.text() == "5"
    assert "5 acquisitions" in gallery.status_label.text()
    item = gallery.list_widget.item(4)
    assert item.text() == f"complete\n{datetime.fromtimestamp(T0):%Y-%m-%d %H:%M}"
    assert item.toolTip().startswith("Complete")
    assert "Channels (2): BF LED matrix full, Fluorescence 488 nm Ex" in item.toolTip()
    assert "Objective: 20x (20x)" in item.toolTip()


def test_a_protocol_run_tile_counts_its_rounds(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(control.widgets_gallery, "LAST_GALLERY_VIEW_CACHE", str(tmp_path / "view.yaml"))
    store = HistoryStore(tmp_path / "history")
    run_dir = tmp_path / "run"
    store.append({"type": "protocol_started", "id": "run", "name": "screen", "folder": str(run_dir), "at": T0})
    for i in range(2):
        store.append(
            {
                "type": "started",
                "id": f"round{i}",
                "name": f"round{i}",
                "folder": str(run_dir / f"round{i}"),
                "parent": "run",
                "at": T0 + i,
            }
        )
    widget = AcquisitionGalleryWidget(store, import_root_provider=lambda: None)
    qtbot.addWidget(widget)
    try:
        widget.reload()
        qtbot.waitUntil(lambda: len(widget.rows()) == 1, timeout=10000)
        assert widget.list_widget.item(0).text() == f"screen · 2 rounds\n{datetime.fromtimestamp(T0):%Y-%m-%d %H:%M}"
        assert widget.rows()[0].status_label == "Running · round 2"
    finally:
        widget.shutdown()


def test_grid_and_list_show_the_same_rows_and_the_view_is_remembered(gallery, tmp_path, monkeypatch):
    cache = tmp_path / "cache" / "last_gallery_view.yaml"
    assert gallery.view is GalleryView.GRID
    assert gallery._stack.currentWidget() is gallery.list_widget

    gallery.set_view(GalleryView.LIST)

    assert gallery.view is GalleryView.LIST
    assert gallery._stack.currentWidget() is gallery.table_widget
    table = gallery.table_widget
    assert table.rowCount() == len(gallery.rows()) == gallery.list_widget.count()
    headers = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    assert headers == ["Name", "Started", "Sample", "Objective", "Channels", "FOVs", "Nz / Nt", "Format", "Status"]
    assert [table.item(r, 0).text() for r in range(table.rowCount())] == _ids(gallery)
    assert table.isSortingEnabled()
    assert table.item(2, headers.index("Status")).text() == "Aborted at 38%"
    assert table.item(2, headers.index("Nz / Nt")).text() == "3 / 2"
    assert yaml.safe_load(cache.read_text()) == {"view": "list"}

    again = AcquisitionGalleryWidget(gallery._store, import_root_provider=lambda: None)
    try:
        assert again.view is GalleryView.LIST
    finally:
        again.shutdown()

    gallery.view_toggle.click()
    assert gallery.view is GalleryView.GRID
    assert yaml.safe_load(cache.read_text()) == {"view": "grid"}


def test_status_filter_chips_narrow_the_list(gallery):
    assert set(gallery.filter_buttons) == set(STATUS_FILTERS)
    assert gallery.filter_buttons["All"].text() == "All 5"
    assert gallery.filter_buttons["Aborted"].text() == "Aborted 1"
    assert gallery.filter_buttons["Failed"].text() == "Failed 0"

    gallery.filter_buttons["Aborted"].click()

    assert _ids(gallery) == ["aborted"]
    assert gallery.filter_buttons["Aborted"].isChecked() and not gallery.filter_buttons["All"].isChecked()
    assert gallery.list_widget.count() == 1 and gallery.table_widget.rowCount() == 1
    assert gallery.count_label.text() == "1 of 5"

    gallery.set_status_filter("Complete")
    assert _ids(gallery) == ["snap", "complete"]
    gallery.filter_edit.setText("488")
    assert _ids(gallery) == ["complete"]
    assert gallery.filter_buttons["Complete"].text() == "Complete 2"  # counts ignore the search text
    gallery.set_status_filter("All")
    gallery.filter_edit.setText("")
    assert len(gallery.rows()) == 5 and gallery.count_label.text() == "5"


def test_snaps_filter_only_when_enabled(gallery, qtbot, monkeypatch):
    assert not gallery.filter_buttons["Snaps"].isHidden()
    gallery.set_status_filter("Snaps")
    assert _ids(gallery) == ["snap"]

    monkeypatch.setattr(control._def, "RECORD_SNAPS_IN_HISTORY", False)
    _reload_and_wait(gallery, qtbot)

    assert gallery.filter_buttons["Snaps"].isHidden()
    assert gallery.filter_buttons["All"].isChecked()  # a hidden chip cannot stay selected
    assert len(gallery.rows()) == 5

    monkeypatch.setattr(control._def, "RECORD_SNAPS_IN_HISTORY", True)
    _reload_and_wait(gallery, qtbot)
    assert not gallery.filter_buttons["Snaps"].isHidden()


def test_thumbnails_load_from_the_preview_png(gallery, qtbot):
    qtbot.waitUntil(lambda: "complete" in gallery._thumbnail_cache, timeout=10000)

    path, mtime, pixmap = gallery._thumbnail_cache["complete"]
    assert path == str(gallery._store.previews_dir / "complete.png")
    assert mtime == os.stat(path).st_mtime
    assert max(pixmap.width(), pixmap.height()) == AcquisitionGalleryWidget.THUMBNAIL_SIZE
    assert not gallery._items["complete"].icon().isNull()
    assert not gallery._table_name_items["complete"].icon().isNull()
    assert "running" not in gallery._thumbnail_cache  # a running entry keeps the "Running…" placeholder
    assert "aborted" not in gallery._thumbnail_cache  # no preview: the placeholder with the format text
    assert not gallery._items["aborted"].icon().isNull()


def test_a_missing_or_broken_preview_keeps_its_format_placeholder(gallery, qtbot):
    store = gallery._store
    (store.previews_dir / "broken.png").write_bytes(b"not a png at all")
    for id_, file in (("broken", "broken.png"), ("missing", "missing.png")):
        store.append(
            {
                "type": "imported",
                "id": id_,
                "kind": "run",
                "name": id_,
                "folder": "x",
                "started_at": T0 - 100,
                "outcome": "complete",
                "metadata": {"image_format": "OME-Zarr"},
            }
        )
        store.append({"type": "preview", "id": id_, "file": file, "channels": []})
    _reload_and_wait(gallery, qtbot)
    qtbot.waitUntil(lambda: {"broken", "missing"} <= set(gallery._thumbnail_cache), timeout=10000)
    submitted = []
    real_submit = gallery._thumbnail_executor.submit
    gallery._thumbnail_executor.submit = lambda *args: submitted.append(args) or real_submit(*args)

    gallery.filter_edit.setText("o")  # re-populates both views from the cache

    size = AcquisitionGalleryWidget.THUMBNAIL_SIZE
    for id_ in ("broken", "missing"):
        entry = _row(gallery, id_).entry
        placeholder = gallery._format_placeholder(entry)
        assert gallery._thumbnail_cache[id_][2] is placeholder
        # Pixmap identity: offscreen Qt draws no text, so every placeholder has the same pixels.
        for item in (gallery._items[id_], gallery._table_name_items[id_]):
            tile = item.icon().pixmap(size, size).cacheKey()
            assert tile == placeholder.cacheKey()
            assert tile != gallery._placeholder_loading.cacheKey()
    _reload_and_wait(gallery, qtbot)
    assert submitted == []  # failures are remembered, not decoded again


def test_a_reload_keeps_the_users_table_sort(gallery, qtbot):
    table = gallery.table_widget
    table.sortItems(0, Qt.AscendingOrder)
    assert table.item(0, 0).text() == "aborted"
    gallery._store.append({"type": "started", "id": "mmm", "kind": "run", "name": "mmm", "folder": "x"})

    _reload_and_wait(gallery, qtbot)

    assert len(gallery.rows()) == 6
    header = table.horizontalHeader()
    assert (header.sortIndicatorSection(), header.sortIndicatorOrder()) == (0, Qt.AscendingOrder)
    names = [table.item(r, 0).text() for r in range(table.rowCount())]
    assert names == sorted(names) and names[0] == "aborted"


def test_reload_keeps_the_selection_and_picks_up_new_events(gallery, qtbot, store_with_entries):
    assert gallery.select_entry("aborted")
    assert gallery.selected_entry().id == "aborted"
    assert not gallery.select_entry("no-such-entry")
    folder = store_with_entries.folders["complete"].parent / "newest"
    folder.mkdir()
    gallery._store.append(
        {"type": "started", "id": "newest", "kind": "run", "name": "newest", "folder": str(folder), "metadata": {}}
    )

    gallery.reload()
    qtbot.waitUntil(lambda: len(gallery.rows()) == 6, timeout=10000)

    assert _ids(gallery)[0] == "newest"
    assert gallery.selected_entry().id == "aborted"
    assert gallery.list_widget.currentItem().text().startswith("aborted")
    selected_rows = gallery.table_widget.selectionModel().selectedRows()
    assert [gallery.table_widget.item(i.row(), 0).text() for i in selected_rows] == ["aborted"]


def test_history_file_change_triggers_a_reload(gallery, qtbot):
    assert gallery._timer.isActive()
    gallery._store.append({"type": "started", "id": "later", "kind": "run", "name": "later", "folder": "x"})

    qtbot.waitUntil(lambda: len(gallery.rows()) == 6, timeout=5000)

    gallery.hide()
    assert not gallery._timer.isActive()


def test_offline_entry_is_marked_and_listed(gallery, store_with_entries):
    row = _row(gallery, "offline")
    assert row.availability != "online"
    assert row.status_label == store_with_entries.offline_label
    assert gallery._items["offline"].toolTip().startswith(store_with_entries.offline_label)

    gallery.set_status_filter("Offline")

    assert _ids(gallery) == ["offline"]
    assert gallery.filter_buttons["Offline"].text() == "Offline 1"


def test_a_stale_load_result_is_ignored(gallery):
    """A reload started later always wins, whatever order the worker threads finish in."""
    gallery._on_loaded(gallery._generation - 1, ((0, 0), [Mock()]))
    assert len(gallery.rows()) == 5


def test_shutdown_drops_late_results(gallery):
    gallery.shutdown()
    assert not gallery._timer.isActive()
    gallery._emit(gallery._events.loaded, gallery._generation, ((0, 0), []))  # must not raise
    generation = gallery._generation
    gallery.reload()  # a no-op after shutdown
    assert gallery._generation == generation
