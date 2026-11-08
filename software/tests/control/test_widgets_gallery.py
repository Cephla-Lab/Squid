"""The Gallery tab lists the acquisition history, newest first, in a grid or a list, narrowed by status and text."""

import os
import shutil
import threading
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest
import yaml
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QApplication

import control._def
import control.widgets_gallery
from control.gallery_import import import_folder
from control.widgets_gallery import (
    SNAP_BADGE_COLOUR,
    STATUS_FILTERS,
    AcquisitionGalleryWidget,
    GalleryRow,
    GalleryView,
    build_rows,
    filter_counts,
    image_format_of,
    matches,
    status_badge_colour,
    status_label_for,
    summary_lines,
    tile_text,
)
from control.widgets_gallery_details import format_size, format_value, metadata_rows
from squid.acquisition_history import HistoryEntry, HistoryStore
from tests.control.gallery_fixtures import saving_root, write_acquisition, write_protocol_run  # noqa: F401

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


def test_an_entry_with_only_a_file_format_still_shows_its_format(monkeypatch):
    # Entries recorded before image_format was stored carry only the saving option.
    md = {k: v for k, v in _metadata(["BF"]).items() if k != "image_format"}
    zarr = HistoryEntry(id="z", kind="run", name="z", folder="x", metadata={**md, "file_format": "ZARR_V3"})
    monkeypatch.setattr(control._def.Acquisition, "IMAGE_FORMAT", "tiff")
    images = HistoryEntry(id="i", kind="run", name="i", folder="x", metadata={**md, "file_format": "INDIVIDUAL_IMAGES"})

    rows = {row.entry.id: row for row in build_rows([zarr, images])}

    assert "ome-zarr" in rows["z"].search_text and "Format: OME-Zarr" in summary_lines(zarr)
    assert matches(rows["z"], "zarr", "All")
    assert "Format: TIFF images" in summary_lines(images)
    assert image_format_of(zarr) == "OME-Zarr" and image_format_of(images) == "TIFF images"
    unknown = HistoryEntry(id="u", kind="run", name="u", folder="x", metadata={"file_format": "SOMETHING_NEW"})
    assert image_format_of(unknown) is None


def test_the_format_column_falls_back_to_the_file_format(gallery, store_with_entries, qtbot):
    store_with_entries.store.append(
        {
            "type": "imported",
            "id": "old",
            "kind": "run",
            "name": "old",
            "folder": str(store_with_entries.folders["complete"]),
            "started_at": T0 + 5000,
            "outcome": "complete",
            "metadata": {"file_format": "OME_TIFF"},
            "at": T0 + 5000,
        }
    )
    _reload_and_wait(gallery, qtbot)
    gallery.set_view(GalleryView.LIST)
    table = gallery.table_widget
    headers = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    row = next(r for r in range(table.rowCount()) if table.item(r, 0).text() == "old")
    assert table.item(row, headers.index("Format")).text() == "OME-TIFF"


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
    assert item.text() == (
        f"complete · {datetime.fromtimestamp(T0):%Y-%m-%d %H:%M}\n20x · 96 well plate\n2 channels · 8 FOVs · Nz 3 · Nt 2"
    )
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
        assert widget.list_widget.item(0).text() == f"screen · 2 rounds · {datetime.fromtimestamp(T0):%Y-%m-%d %H:%M}"
        assert widget._table_name_items["run"].text() == "screen · 2 rounds"
        assert widget.rows()[0].status_label == "Running · round 2"
    finally:
        widget.shutdown()


def test_tile_text_shows_what_is_known():
    entry = HistoryEntry(id="a", kind="run", name="a", folder="x", started_at=T0, metadata=_metadata(["BF"]))
    date = f"{datetime.fromtimestamp(T0):%Y-%m-%d %H:%M}"
    assert tile_text(entry) == f"a · {date}\n20x · 96 well plate\n1 channel · 8 FOVs · Nz 3 · Nt 2"
    entry.metadata = {"objective": "10x", "num_fovs": 1, "nz": 1, "nt": 5}
    assert tile_text(entry) == f"a · {date}\n10x\n1 FOV · Nt 5"
    assert tile_text(HistoryEntry(id="b", kind="run", name="b", folder="x")) == "b"


def _icon_pixel(item, x, y, size=180):
    return item.icon().pixmap(size, size).toImage().pixelColor(x, y)


def test_tiles_carry_a_status_badge_coloured_by_status(gallery, qtbot):
    qtbot.waitUntil(lambda: gallery._thumbnail_cache.get("complete") is not None, timeout=10000)
    complete, aborted, snap = (gallery._items[i] for i in ("complete", "aborted", "snap"))
    assert _icon_pixel(complete, 6, 9) == status_badge_colour("Complete")
    assert _icon_pixel(aborted, 6, 9) == status_badge_colour("Aborted at 38%")
    assert status_badge_colour("Complete") != status_badge_colour("Aborted at 38%")
    assert complete.icon().pixmap(180, 180).cacheKey() != aborted.icon().pixmap(180, 180).cacheKey()
    # The snap tag sits in the top-right corner; other tiles have none there.
    assert _icon_pixel(snap, 173, 9) == SNAP_BADGE_COLOUR
    assert _icon_pixel(complete, 173, 9) != SNAP_BADGE_COLOUR
    # The table shows the same badged tile.
    table_icon = gallery._table_name_items["aborted"].icon()
    assert table_icon.pixmap(180, 180).toImage().pixelColor(6, 9) == status_badge_colour("Aborted")
    # A progress tick repaints the running tile's badge.
    gallery.set_running_progress(0.5)
    assert _icon_pixel(gallery._items["running"], 6, 9) == status_badge_colour("Running 50%")


def test_selection_and_progress_never_touch_the_data_folders(gallery, store_with_entries):
    calls = []
    main = threading.main_thread()

    def spy(name, original):
        def wrapper(*args, **kwargs):
            if threading.current_thread() is main:
                calls.append((name, args))
            return original(*args, **kwargs)

        return wrapper

    with pytest.MonkeyPatch.context() as mp:
        for name in ("exists", "isfile", "isdir"):
            mp.setattr(os.path, name, spy(name, getattr(os.path, name)))
        mp.setattr(control.widgets_gallery, "folder_availability", spy("availability", lambda f: "online"))
        for entry_id in ("complete", "offline", "running", "aborted"):
            gallery.select_entry(entry_id)
            gallery.set_running_progress(0.25)
    data = str(store_with_entries.folders["complete"].parent)
    assert [c for c in calls if c[0] == "availability" or any(data in str(a) for a in c[1])] == []


def test_a_reload_is_not_held_up_by_a_running_import(gallery, qtbot, saving_root, monkeypatch):
    write_acquisition(saving_root, "slow", start_time=T0 + 10)
    entered, release = threading.Event(), threading.Event()

    def blocking_preview(folder, out_path):
        entered.set()
        release.wait(20)
        return None

    monkeypatch.setattr(control.widgets_gallery, "write_preview_png", blocking_preview)
    try:
        gallery.import_path(str(saving_root))
        assert entered.wait(10)
        gallery._store.append({"type": "started", "id": "new", "kind": "run", "name": "new", "folder": "x", "at": T0})
        gallery.reload()
        qtbot.waitUntil(lambda: "new" in _ids(gallery), timeout=5000)
        assert not release.is_set()
    finally:
        release.set()
    qtbot.waitUntil(lambda: "slow" in _ids_by_name(gallery) and gallery.btn_import.isEnabled(), timeout=10000)


def test_the_import_button_waits_for_a_running_acquisition(gallery, qtbot, saving_root):
    gallery.set_acquiring(True)
    assert not gallery.btn_import.isEnabled()
    assert gallery.btn_import.toolTip() == "Wait for the running acquisition to finish"
    write_acquisition(saving_root, "imp", start_time=T0 + 10)
    gallery.import_path(str(saving_root))  # an import already asked for still finishes, but leaves the button off
    qtbot.waitUntil(lambda: "imp" in _ids_by_name(gallery), timeout=10000)
    assert not gallery.btn_import.isEnabled()
    gallery.set_acquiring(False)
    assert gallery.btn_import.isEnabled() and gallery.btn_import.toolTip() == ""


def test_the_status_line_is_plain_text(gallery):
    assert gallery.status_label.textFormat() == Qt.PlainText


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
        # Pixmap identity: offscreen Qt draws no text, so every placeholder has the same pixels. The tile is the
        # placeholder with its status badge, shared by the grid and the table.
        badged = gallery._badged_tile(_row(gallery, id_), placeholder).cacheKey()
        loading = gallery._badged_tile(_row(gallery, id_), gallery._placeholder_loading).cacheKey()
        for item in (gallery._items[id_], gallery._table_name_items[id_]):
            tile = item.icon().pixmap(size, size).cacheKey()
            assert tile == badged
            assert tile != loading
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


# ---------------------------------------------------------------------------------------------------------
# Details pane, actions and context menu
# ---------------------------------------------------------------------------------------------------------


def _signals(widget):
    """Record every folder the widget emits, per signal."""
    seen = {"squid": [], "window": [], "settings": []}
    widget.signal_open_in_squid.connect(seen["squid"].append)
    widget.signal_open_in_window.connect(seen["window"].append)
    widget.signal_load_settings.connect(seen["settings"].append)
    return seen


@pytest.fixture
def opened_urls(monkeypatch):
    urls = []
    monkeypatch.setattr(
        control.widgets_gallery.QDesktopServices, "openUrl", lambda url: urls.append(url.toLocalFile()) or True
    )
    return urls


def _menu_entries(widget):
    menu = widget._build_context_menu()
    return [("-" if a.isSeparator() else a.text(), a.isEnabled()) for a in menu.actions()]


def _double_click_selected(widget, qtbot):
    item = widget._items[widget.selected_entry().id]
    widget.list_widget.scrollToItem(item)
    center = widget.list_widget.visualItemRect(item).center()
    # A real double-click: QTest's mouseDClick alone skips the first press/release the item view expects.
    qtbot.mouseClick(widget.list_widget.viewport(), Qt.LeftButton, pos=center)
    qtbot.mouseDClick(widget.list_widget.viewport(), Qt.LeftButton, pos=center)


def _round_button(widget, row, name):
    return widget.rounds_table.cellWidget(row, 5).findChild(control.widgets_gallery.QPushButton, name)


def test_format_size_and_metadata_rows_are_pure():
    assert format_size(2_345_000_000) == "2.3 GB"
    assert format_size(12_300_000) == "12.3 MB"
    assert format_size(None) is None
    assert format_value(["a", "b"]) == "a, b" and format_value(None) == "–"
    assert format_value(True) == "yes" and format_value(False) == "no"
    entry = HistoryEntry(
        id="e",
        kind="run",
        name="e",
        folder="x",
        reason="user abort",
        bytes_on_disk=3_000_000_000,
        metadata={"machine": "scope-2", "nz": 3, "objective": "20x", "channels": ["BF", "488"], "use_piezo": False},
    )
    rows = metadata_rows(entry, "Aborted")
    keys = [k for k, _ in rows]
    # RECORDED_METADATA_KEYS order (objective, channels, nz, use_piezo), then the extra keys, then size and status.
    assert keys == ["Objective", "Channels", "Nz", "Use piezo", "Machine", "Size", "Status"]
    assert dict(rows)["Channels"] == "BF, 488" and dict(rows)["Use piezo"] == "no"
    assert dict(rows)["Size"] == "3.0 GB"
    assert dict(rows)["Status"] == "Aborted — user abort"


def test_selecting_an_entry_fills_the_details_pane(gallery, qtbot):
    assert gallery.select_entry("complete")

    html = gallery.metadata_label.text()
    for text in (
        "Objective",
        "20x",
        "Sample",
        "96 well plate",
        "Channels",
        "BF LED matrix full, Fluorescence 488 nm Ex",
    ):
        assert text in html
    assert "Nz" in html and "Status" in html and "Complete" in html
    assert html.index("Objective") < html.index("Channels") < html.index("Format")  # RECORDED_METADATA_KEYS order
    assert [chip.text() for chip in gallery.channel_chips] == ["BF"]  # the preview's channels
    assert all(chip.isCheckable() for chip in gallery.channel_chips)
    qtbot.waitUntil(lambda: gallery.details.preview_source == "png", timeout=10000)
    pixmap = gallery.preview_label.pixmap()
    assert max(pixmap.width(), pixmap.height()) == 360
    assert not any(chip.isEnabled() for chip in gallery.channel_chips)  # only the PNG: nothing to recomposite
    assert gallery.raw_yaml_text.isReadOnly() and gallery.raw_yaml_text.isHidden()
    gallery.details_toggle.click()
    assert not gallery.raw_yaml_text.isHidden()
    gallery.details_toggle.click()
    assert gallery.raw_yaml_text.isHidden()

    gallery.select_entry("aborted")  # no preview: the format placeholder
    assert gallery.details.preview_source == "placeholder"
    assert gallery.channel_chips == []
    assert "OME-Zarr" in gallery.metadata_label.text()


def test_double_click_prefers_squid_then_window_then_folder(gallery, qtbot, opened_urls, store_with_entries):
    seen = _signals(gallery)
    folder = str(store_with_entries.folders["complete"])
    gallery.select_entry("complete")

    _double_click_selected(gallery, qtbot)
    assert (seen["squid"], seen["window"], opened_urls) == ([folder], [], [])  # exactly once, not twice

    gallery.set_open_in_squid(False, "No NDViewer")
    _double_click_selected(gallery, qtbot)
    assert (seen["squid"], seen["window"], opened_urls) == ([folder], [folder], [])

    gallery.set_open_in_window(False, "No ndviewer_light")
    _double_click_selected(gallery, qtbot)
    assert (seen["squid"], seen["window"]) == ([folder], [folder])
    assert [os.path.normcase(os.path.normpath(u)) for u in opened_urls] == [os.path.normcase(folder)]


def test_enter_on_a_table_row_opens_it(gallery, qtbot):
    seen = _signals(gallery)
    gallery.set_view(GalleryView.LIST)
    gallery.select_entry("complete")
    gallery.table_widget.setFocus()
    qtbot.keyClick(gallery.table_widget, Qt.Key_Return)
    assert len(seen["squid"]) == 1


def test_running_entry_only_shows_the_folder(gallery, qtbot, opened_urls, store_with_entries):
    seen = _signals(gallery)
    gallery.select_entry("running")

    for button in (gallery.btn_open_in_squid, gallery.btn_open_in_window):
        assert not button.isEnabled()
        assert button.toolTip() == "Wait for the acquisition to finish"
    assert gallery.btn_show_folder.isEnabled() and not gallery.btn_load_settings.isEnabled()
    gallery.open_selected()

    assert seen["squid"] == seen["window"] == []
    assert len(opened_urls) == 1 and os.path.samefile(opened_urls[0], store_with_entries.folders["running"])
    gallery.open_selected_in_squid()
    assert seen["squid"] == [] and "Wait for the acquisition to finish" in gallery.status_label.text()
    entries = dict(_menu_entries(gallery))
    assert entries["Open in Squid"] is False and entries["Open in window"] is False
    assert entries["Show folder"] is True
    assert entries["Remove from history"] is False and entries["Locate…"] is False
    n_events = len(gallery._store.events())
    gallery.remove_selected()
    assert len(gallery._store.events()) == n_events
    assert "Wait for the acquisition to finish" in gallery.status_label.text()


def test_offline_entry_allows_only_copy_locate_and_remove(gallery):
    gallery.select_entry("offline")
    buttons = (
        gallery.btn_open_in_squid,
        gallery.btn_open_in_window,
        gallery.btn_show_folder,
        gallery.btn_load_settings,
    )
    assert not any(b.isEnabled() for b in buttons)
    assert _menu_entries(gallery) == [
        ("Open in Squid", False),
        ("Open in window", False),
        ("Show folder", False),
        ("Load settings", False),
        ("-", True),
        ("Copy path", True),
        ("Locate…", True),
        ("Remove from history", True),
    ]


def test_load_settings_requires_an_online_yaml(gallery, qtbot, store_with_entries):
    seen = _signals(gallery)
    gallery.select_entry("complete")
    assert not gallery.btn_load_settings.isEnabled()  # no acquisition.yaml in the folder
    qtbot.waitUntil(lambda: gallery.details.yaml_loaded, timeout=10000)
    assert gallery.raw_yaml_text.toPlainText() == ""
    assert "acquisition.yaml" in gallery.raw_yaml_text.placeholderText()
    gallery.load_selected_settings()
    assert seen["settings"] == []

    folder = store_with_entries.folders["complete"]
    (folder / "acquisition.yaml").write_text("acquisition:\n  experiment_id: complete\n", encoding="utf-8")
    gallery.select_entry("aborted")
    gallery.select_entry("complete")
    assert not gallery.btn_load_settings.isEnabled()  # folder states are refreshed by a reload, not by selecting
    _reload_and_wait(gallery, qtbot)

    assert gallery.btn_load_settings.isEnabled()
    qtbot.waitUntil(lambda: "experiment_id: complete" in gallery.raw_yaml_text.toPlainText(), timeout=10000)
    gallery.btn_load_settings.click()
    assert seen["settings"] == [str(folder)]

    gallery.select_entry("offline")
    assert not gallery.btn_load_settings.isEnabled()


def test_remove_appends_a_removed_event_and_drops_the_row(gallery, qtbot, store_with_entries):
    gallery.select_entry("aborted")

    gallery.remove_selected()
    qtbot.waitUntil(lambda: len(gallery.rows()) == 4, timeout=10000)

    last = gallery._store.events()[-1]
    assert last["type"] == "removed" and last["id"] == "aborted" and "at" in last
    assert "aborted" not in _ids(gallery)
    assert gallery.status_label.text() == "Removed aborted from the history (data untouched)"
    assert store_with_entries.folders["aborted"].is_dir()  # nothing on disk is touched


def test_locate_rejects_a_different_folder_name_and_accepts_the_same(gallery, qtbot, monkeypatch, tmp_path):
    gallery.select_entry("offline")
    offline_name = os.path.basename(gallery.selected_entry().folder)
    wrong = tmp_path / "elsewhere" / "other_name"
    right = tmp_path / "elsewhere" / offline_name
    wrong.mkdir(parents=True)
    right.mkdir(parents=True)
    picked = [str(wrong)]
    starts = []

    def get_dir(parent, caption, directory):
        starts.append(directory)
        return picked[0]

    monkeypatch.setattr(control.widgets_gallery.QFileDialog, "getExistingDirectory", get_dir)
    n_events = len(gallery._store.events())

    gallery.locate_selected()

    assert len(gallery._store.events()) == n_events  # refused
    assert offline_name in gallery.status_label.text() and "other_name" in gallery.status_label.text()

    picked[0] = str(right)
    gallery.locate_selected()
    qtbot.waitUntil(lambda: _row(gallery, "offline").availability == "online", timeout=10000)

    last = gallery._store.events()[-1]
    assert (last["type"], last["id"], last["folder"]) == ("relocated", "offline", str(right))
    assert gallery.selected_entry().folder == str(right)
    assert gallery._store.entry("offline").folder == str(right)
    assert len(starts) == 2
    picked[0] = ""  # a cancelled dialog changes nothing
    gallery.locate_selected()
    assert len(gallery._store.events()) == n_events + 1


def _import_protocol_run(gallery, qtbot, root, run_name, rounds):
    run = write_protocol_run(root, run_name, rounds)
    result = import_folder(gallery._store, str(run), include_snaps=False)
    assert not result.errors
    _reload_and_wait(gallery, qtbot)
    entry = next(row.entry for row in gallery.rows() if row.entry.kind == "protocol_run" and row.entry.name == run_name)
    return run, entry


def test_locating_a_moved_protocol_run_moves_its_rounds_too(gallery, qtbot, monkeypatch, tmp_path):
    old_root, new_root = tmp_path / "old_root", tmp_path / "new_root"
    old_root.mkdir()
    new_root.mkdir()
    run, entry = _import_protocol_run(gallery, qtbot, old_root, "run_7", 2)
    shutil.copytree(run, new_root / "run_7", ignore=shutil.ignore_patterns("round_2_image"))
    shutil.rmtree(run)
    _reload_and_wait(gallery, qtbot)
    monkeypatch.setattr(control.widgets_gallery.QFileDialog, "getExistingDirectory", lambda *a: str(new_root / "run_7"))
    gallery.select_entry(entry.id)

    gallery.locate_selected()
    qtbot.waitUntil(lambda: gallery.selected_entry().folder == str(new_root / "run_7"), timeout=10000)

    parent = gallery.selected_entry()
    assert parent.rounds[0].folder == str(new_root / "run_7" / "round_1_image")
    assert parent.rounds[1].folder == str(run / "round_2_image")  # not found there: unchanged
    assert "round_2_image" in gallery.status_label.text()
    seen = _signals(gallery)
    window_button = _round_button(gallery, 0, "in_window")
    assert window_button.isEnabled()
    assert not _round_button(gallery, 1, "in_window").isEnabled()  # still at the old place, which is gone
    window_button.click()
    assert seen["window"] == [str(new_root / "run_7" / "round_1_image")]


def test_copy_path_puts_the_folder_on_the_clipboard(gallery, store_with_entries):
    gallery.select_entry("complete")
    gallery.copy_selected_path()
    assert QApplication.clipboard().text() == str(store_with_entries.folders["complete"])
    assert "Copied" in gallery.status_label.text()


def test_channel_chips_toggle_the_composite(gallery, qtbot, saving_root):
    folder = write_acquisition(saving_root, "plate", with_overview=True)
    gallery._store.append(
        {
            "type": "imported",
            "id": "plate",
            "kind": "run",
            "name": "plate",
            "folder": str(folder),
            "started_at": T0 + 9000,
            "outcome": "complete",
            "metadata": {"channels": ["BF LED matrix full", "Fluorescence 488 nm Ex"]},
        }
    )
    _reload_and_wait(gallery, qtbot)
    gallery.select_entry("plate")
    qtbot.waitUntil(lambda: gallery.details.preview_source == "planes", timeout=10000)

    chips = {chip.text(): chip for chip in gallery.channel_chips}
    assert list(chips) == ["BF LED matrix full", "Fluorescence 488 nm Ex"]
    assert all(chip.isEnabled() and chip.isChecked() for chip in chips.values())
    before = gallery.details.preview_array.copy()
    key_before = gallery.preview_label.pixmap().cacheKey()
    assert before[0, 0].any() and before[-1, -1].any()  # top lit by BF, bottom by 488
    assert max(gallery.preview_label.pixmap().width(), gallery.preview_label.pixmap().height()) == 360

    chips["BF LED matrix full"].click()

    after = gallery.details.preview_array
    assert not after[0, 0].any()  # BF off: the top half goes dark
    assert np.array_equal(after[-1, -1], before[-1, -1])
    assert gallery.preview_label.pixmap().cacheKey() != key_before


def test_a_newer_selection_wins_over_a_late_preview(gallery, qtbot):
    gallery.select_entry("complete")
    token = gallery.details._token
    gallery.select_entry("aborted")
    gallery.details._on_loaded(token, "planes", {"BF": np.ones((4, 4), dtype=np.uint16)})
    assert gallery.details.preview_source == "placeholder"


def test_protocol_run_lists_its_rounds_with_their_own_open_buttons(gallery, qtbot, tmp_path):
    root = tmp_path / "runs"
    root.mkdir()
    run, entry = _import_protocol_run(gallery, qtbot, root, "run_3", [(1, 1), (2, 1), (2, 2)])
    seen = _signals(gallery)

    gallery.select_entry(entry.id)

    html = gallery.metadata_label.text()
    assert "stain" in html and "run_3" in html and str(run) in html and "Complete" in html
    table = gallery.rounds_table
    assert not table.isHidden()
    headers = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    assert headers == ["Round", "Step", "Attempt", "Started", "Outcome", "Open"]
    assert table.rowCount() == 3
    assert [table.item(r, 0).text() for r in range(3)] == ["1", "2", "2"]
    assert [table.item(r, 2).text() for r in range(3)] == ["1", "1", "2"]
    assert table.item(0, 1).text() == "image" and table.item(0, 4).text() == "Complete"
    for r in range(3):
        assert _round_button(gallery, r, "in_squid").isEnabled()
        assert _round_button(gallery, r, "in_window").isEnabled()
    assert gallery.btn_open_in_squid.isEnabled() and gallery.btn_open_in_window.isEnabled()

    _round_button(gallery, 0, "in_squid").click()
    gallery.btn_open_in_squid.click()  # the entry-level buttons open the latest round
    gallery.btn_open_in_window.click()
    latest = str(run / "round_2_image_attempt2")
    assert seen["squid"] == [str(run / "round_1_image"), latest]
    assert seen["window"] == [latest]

    gallery.set_open_in_squid(False, "No NDViewer")
    assert not _round_button(gallery, 0, "in_squid").isEnabled()
    assert _round_button(gallery, 0, "in_squid").toolTip() == "No NDViewer"
    gallery.select_entry("complete")
    assert gallery.rounds_table.isHidden()


def test_unavailable_viewer_disables_the_button_with_the_reason(gallery):
    gallery.select_entry("complete")
    assert gallery.btn_open_in_squid.isEnabled()

    gallery.set_open_in_squid(False, "The NDViewer tab failed to load")
    gallery.set_open_in_window(False, "ndviewer_light was not found")

    assert not gallery.btn_open_in_squid.isEnabled()
    assert gallery.btn_open_in_squid.toolTip() == "The NDViewer tab failed to load"
    assert not gallery.btn_open_in_window.isEnabled()
    assert gallery.btn_open_in_window.toolTip() == "ndviewer_light was not found"
    gallery.open_selected_in_window()
    assert gallery.status_label.text() == "ndviewer_light was not found"
    entries = dict(_menu_entries(gallery))
    assert entries["Open in Squid"] is False and entries["Open in window"] is False

    gallery.set_open_in_squid(True)
    assert gallery.btn_open_in_squid.isEnabled() and gallery.btn_open_in_squid.toolTip() == ""


def test_hidden_viewer_removes_the_button_and_menu_entry(gallery, qtbot):
    seen = _signals(gallery)
    gallery.select_entry("complete")

    gallery.set_open_in_squid(False, hidden=True)

    assert gallery.btn_open_in_squid.isHidden()
    assert [text for text, _ in _menu_entries(gallery)] == [
        "Open in window",
        "Show folder",
        "Load settings",
        "-",
        "Copy path",
        "Locate…",
        "Remove from history",
    ]
    _double_click_selected(gallery, qtbot)
    assert seen["squid"] == [] and len(seen["window"]) == 1


# ----- importing folders and the first-launch offer -----


def _pick_directory(monkeypatch, folder, starts=None):
    def get_dir(parent, caption, directory):
        if starts is not None:
            starts.append(directory)
        return str(folder)

    monkeypatch.setattr(control.widgets_gallery.QFileDialog, "getExistingDirectory", get_dir)


def test_import_button_adds_the_folders_and_previews(gallery, qtbot, saving_root, monkeypatch):
    write_acquisition(saving_root, "imp_a", start_time=T0 + 10)
    write_acquisition(saving_root, "imp_b", start_time=T0 + 20)
    starts = []
    _pick_directory(monkeypatch, saving_root, starts)
    gallery._import_root_provider = lambda: str(saving_root)

    gallery.btn_import.click()

    qtbot.waitUntil(lambda: len(gallery.rows()) == 7 and not gallery._loading, timeout=10000)
    assert starts == [str(saving_root)]
    assert gallery.btn_import.isEnabled()
    assert {"imp_a", "imp_b"} <= set(_ids_by_name(gallery))
    previews = sorted(p.name for p in gallery._store.previews_dir.glob("*.png"))
    added = [row.entry.id for row in gallery.rows() if row.entry.name in ("imp_a", "imp_b")]
    assert all(f"{entry_id}.png" in previews for entry_id in added)
    preview_events = [e for e in gallery._store.events() if e["type"] == "preview" and e["id"] in added]
    assert len(preview_events) == 2 and all(e["bytes_on_disk"] is None for e in preview_events)  # no size walk
    assert gallery.status_label.text().startswith("Added 2 acquisitions from ")
    assert "(0 already in the history, 0 snaps skipped)" in gallery.status_label.text()

    gallery.btn_import.click()  # the same folder again adds nothing
    qtbot.waitUntil(lambda: "Added 0 acquisitions" in gallery.status_label.text(), timeout=10000)
    assert "(2 already in the history" in gallery.status_label.text()
    assert len(gallery.rows()) == 7


def _ids_by_name(widget):
    return [row.entry.name for row in widget.rows()]


def test_import_button_does_nothing_when_the_dialog_is_cancelled(gallery, monkeypatch):
    _pick_directory(monkeypatch, "")
    n_events = len(gallery._store.events())
    gallery.btn_import.click()
    assert len(gallery._store.events()) == n_events
    assert gallery.btn_import.isEnabled()


@pytest.fixture
def marker(tmp_path, monkeypatch):
    path = tmp_path / "cache" / "gallery_import_offered.txt"
    monkeypatch.setattr(control.widgets_gallery, "IMPORT_OFFER_MARKER", str(path))
    monkeypatch.setattr(control._def, "RECORD_SNAPS_IN_HISTORY", True)
    return path


def _new_gallery(qtbot, tmp_path, root, name="history"):
    widget = AcquisitionGalleryWidget(HistoryStore(tmp_path / name), import_root_provider=lambda: str(root))
    qtbot.addWidget(widget)
    return widget


def _offer(widget, qtbot):
    """Make the first-launch offer and wait for its folder check (on the scan worker) to come back."""
    widget.offer_initial_import()
    qtbot.waitUntil(lambda: not widget._offer_pending, timeout=10000)


def test_initial_offer_asks_once_and_imports_on_yes(qtbot, tmp_path, saving_root, marker, monkeypatch):
    write_acquisition(saving_root, "first", start_time=T0 + 10)
    write_acquisition(saving_root, "second", start_time=T0 + 20)
    asked = []

    def question(parent, title, text, *args):
        asked.append((title, text))
        return control.widgets_gallery.QMessageBox.Yes

    monkeypatch.setattr(control.widgets_gallery.QMessageBox, "question", question)
    widget = _new_gallery(qtbot, tmp_path, saving_root)
    checked_on = []
    real_check = control.widgets_gallery.is_acquisition_folder
    monkeypatch.setattr(
        control.widgets_gallery,
        "is_acquisition_folder",
        lambda path: checked_on.append(threading.current_thread()) or real_check(path),
    )

    _offer(widget, qtbot)

    assert checked_on and threading.main_thread() not in checked_on  # the saving folder is read off the GUI thread
    assert len(asked) == 1 and str(saving_root) in asked[0][1]
    assert marker.exists()
    qtbot.waitUntil(lambda: len(widget.rows()) == 2, timeout=10000)
    widget.shutdown()

    other = _new_gallery(qtbot, tmp_path, saving_root, name="history2")
    _offer(other, qtbot)
    assert len(asked) == 1  # the marker stops a second question
    assert other._store.load() == []
    other.shutdown()


def test_initial_offer_writes_the_marker_on_no_and_imports_nothing(qtbot, tmp_path, saving_root, marker, monkeypatch):
    write_acquisition(saving_root, "first", start_time=T0 + 10)
    monkeypatch.setattr(
        control.widgets_gallery.QMessageBox, "question", lambda *a: control.widgets_gallery.QMessageBox.No
    )
    widget = _new_gallery(qtbot, tmp_path, saving_root)

    _offer(widget, qtbot)

    assert marker.exists()
    assert widget._store.load() == []
    widget.shutdown()


def test_initial_offer_accepted_during_an_acquisition_imports_once_it_is_over(
    qtbot, tmp_path, saving_root, marker, monkeypatch
):
    write_acquisition(saving_root, "first", start_time=T0 + 10)
    monkeypatch.setattr(
        control.widgets_gallery.QMessageBox, "question", lambda *a: control.widgets_gallery.QMessageBox.Yes
    )
    widget = _new_gallery(qtbot, tmp_path, saving_root)
    widget.set_acquiring(True)

    _offer(widget, qtbot)
    qtbot.wait(300)

    assert marker.exists() and widget._store.load() == []
    widget.set_acquiring(False)
    qtbot.waitUntil(lambda: len(widget.rows()) == 1, timeout=10000)
    widget.shutdown()


def test_initial_offer_is_silent_when_the_history_is_not_empty_or_the_folder_has_no_runs(
    qtbot, tmp_path, saving_root, marker, monkeypatch
):
    question = Mock(return_value=control.widgets_gallery.QMessageBox.Yes)
    monkeypatch.setattr(control.widgets_gallery.QMessageBox, "question", question)

    # an empty saving folder
    widget = _new_gallery(qtbot, tmp_path, saving_root)
    _offer(widget, qtbot)
    # a folder that does not exist
    missing = _new_gallery(qtbot, tmp_path, tmp_path / "nowhere", name="h2")
    _offer(missing, qtbot)
    # a history that already has entries
    write_acquisition(saving_root, "first", start_time=T0 + 10)
    busy = _new_gallery(qtbot, tmp_path, saving_root, name="h3")
    busy._store.append({"type": "removed", "id": "x"})
    busy._store.append(
        {"type": "imported", "id": "e", "kind": "run", "name": "e", "folder": str(saving_root / "first"), "at": T0}
    )
    _offer(busy, qtbot)
    # no provider answer
    none = AcquisitionGalleryWidget(HistoryStore(tmp_path / "h4"), import_root_provider=lambda: None)
    qtbot.addWidget(none)
    _offer(none, qtbot)

    question.assert_not_called()
    assert not marker.exists()  # nothing was asked, so a later launch can still offer
    for w in (widget, missing, busy, none):
        w.shutdown()
