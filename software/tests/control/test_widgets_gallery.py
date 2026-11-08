"""The Gallery tab lists the acquisition folders under the saving directory and opens the chosen one."""

import json
import os
import time
from datetime import datetime
from unittest.mock import Mock

import numpy as np
import pytest
import tifffile
import yaml
from qtpy.QtCore import Qt
from qtpy.QtGui import QDesktopServices

from control.widgets_gallery import (
    NO_IMAGES,
    AcquisitionGalleryWidget,
    inspect_images,
    is_acquisition_folder,
    read_acquisition_entry,
    scan_acquisitions,
    split_experiment_name,
)

# ---------------------------------------------------------------------------------------------------------
# Fixtures: saved acquisition folders as MultiPointController and the writers lay them out
# ---------------------------------------------------------------------------------------------------------


def write_acquisition(
    root,
    name,
    *,
    start_time=None,
    widget_type="wellplate",
    channels=("BF LED matrix full", "Fluorescence 488 nm Ex"),
    nz=3,
    nt=2,
    regions=(("A1", 4), ("B2", 4)),
    images="tiff",
    with_yaml=True,
    with_overview=False,
):
    """A folder like <base_path>/<experiment_ID>: metadata files and a first-timepoint folder of images."""
    folder = root / name
    folder.mkdir()
    params = {"Nz": nz, "Nt": nt, "objective": {"name": "20x", "magnification": 20.0}}
    (folder / "acquisition parameters.json").write_text(json.dumps(params))
    if with_yaml:
        data = {
            "acquisition": {"experiment_id": name, "start_time": start_time, "widget_type": widget_type},
            "objective": {"name": "20x", "magnification": 20.0, "pixel_size_um": 0.325},
            "sample": {"wellplate_format": "96 well plate"},
            "z_stack": {"nz": nz, "delta_z_mm": 0.001},
            "time_series": {"nt": nt, "delta_t_s": 0.0},
            "channels": [{"name": c} for c in channels],
            "wellplate_scan" if widget_type == "wellplate" else "flexible_scan": {
                "regions" if widget_type == "wellplate" else "positions": [
                    {"name": r, "center_mm": [1.0, 2.0], "fovs": [[1.0, 2.0]] * n} for r, n in regions
                ]
            },
        }
        with open(folder / "acquisition.yaml", "w", encoding="utf-8") as f:
            f.write(f"# Acquisition Parameters - {name}\n\n")
            yaml.dump(data, f, sort_keys=False)
    with open(folder / "coordinates.csv", "w") as f:
        f.write("region,x (mm),y (mm),z (mm)\n")
        for region, n in regions:
            for _ in range(n):
                f.write(f"{region},1.0,2.0,3.0\n")
    tp = folder / "0"
    tp.mkdir()
    image = (np.arange(64 * 48, dtype=np.uint16).reshape(48, 64) * 100).astype(np.uint16)
    if images == "tiff":
        for region, n in regions:
            for fov in range(n):
                for ch in channels:
                    safe = ch.replace(" ", "_")
                    tifffile.imwrite(tp / f"{region}_{fov}_0_{safe}.tiff", image)
    elif images == "multipage":
        tifffile.imwrite(tp / "A1_0_stack.tiff", np.stack([image, image]))
    elif images == "ome":
        ome = folder / "ome_tiff"
        ome.mkdir()
        tifffile.imwrite(ome / "A1_0.ome.tiff", np.stack([image, image]), metadata={"axes": "CYX"})
    elif images == "zarr":
        (folder / "zarr" / "A1" / "fov_0.ome.zarr").mkdir(parents=True)
        (folder / "zarr" / "A1" / "fov_0.ome.zarr" / "zarr.json").write_text("{}")
    elif images == "bmp":
        import cv2

        cv2.imwrite(str(tp / "A1_0_0_BF_LED_matrix_full.bmp"), (image >> 8).astype(np.uint8))
    for t in range(1, 2 if nt > 1 else 1):  # a second timepoint folder with its own images
        later = folder / str(t)
        later.mkdir()
        if images == "tiff":
            for region, n in regions:
                for fov in range(n):
                    for ch in channels:
                        tifffile.imwrite(later / f"{region}_{fov}_0_{ch.replace(' ', '_')}.tiff", image)
    if with_overview:
        for t in range(2 if nt > 1 else 1):
            mosaic = folder / str(t) / "mosaic_view"
            mosaic.mkdir()
            stack = np.zeros((2, 40, 60), dtype=np.uint16)
            stack[0, :20, :] = 4000  # top half lit in channel 0
            stack[1, 20:, :] = 4000  # bottom half lit in channel 1
            tifffile.imwrite(mosaic / "mosaic_plate_5um.ome.tiff", stack, metadata={"axes": "CYX"})
            with open(mosaic / "mosaic_plate_5um.yaml", "w", encoding="utf-8") as f:
                yaml.dump({"channel_names": list(channels)}, f)
    return folder


@pytest.fixture
def saving_root(tmp_path):
    root = tmp_path / "saved"
    root.mkdir()
    return root


# ---------------------------------------------------------------------------------------------------------
# Folder names and metadata
# ---------------------------------------------------------------------------------------------------------


def test_split_experiment_name_reads_the_controller_timestamp_suffix():
    label, started = split_experiment_name("plate_3_2026-11-07_14-05-09.123456")
    assert label == "plate_3"
    assert started == datetime(2026, 11, 7, 14, 5, 9, 123456)


def test_split_experiment_name_without_a_timestamp_keeps_the_name():
    assert split_experiment_name("my_experiment") == ("my_experiment", None)


def test_split_experiment_name_with_only_a_timestamp_uses_it_as_the_label():
    label, started = split_experiment_name("2026-11-07_14-05-09.5")
    assert label == "2026-11-07_14-05-09.5"
    assert started == datetime(2026, 11, 7, 14, 5, 9, 500000)


def test_is_acquisition_folder_needs_a_metadata_file(tmp_path):
    assert not is_acquisition_folder(str(tmp_path))
    (tmp_path / "configurations.xml").write_text("<modes/>")  # what older versions wrote
    assert is_acquisition_folder(str(tmp_path))
    assert not is_acquisition_folder(str(tmp_path / "configurations.xml"))


def test_read_acquisition_entry_from_yaml(saving_root):
    started = datetime(2026, 11, 7, 9, 30, 0)
    folder = write_acquisition(
        saving_root, "plate_3_2026-11-07_09-30-00.000000", start_time=started.timestamp(), with_overview=True
    )

    entry = read_acquisition_entry(str(folder))

    assert entry.label == "plate_3"
    assert entry.started == started
    assert entry.widget_type == "wellplate"
    assert entry.objective == "20x"
    assert entry.magnification == 20.0
    assert entry.wellplate_format == "96 well plate"
    assert entry.channels == ["BF LED matrix full", "Fluorescence 488 nm Ex"]
    assert (entry.nz, entry.nt) == (3, 2)
    assert (entry.num_regions, entry.num_fovs) == (2, 8)
    assert entry.image_format == "TIFF images"
    assert entry.has_settings
    assert entry.thumbnail_source == os.path.join(str(folder), "0", "mosaic_view", "mosaic_plate_5um.ome.tiff")
    assert "Channels (2): BF LED matrix full, Fluorescence 488 nm Ex" in entry.summary_lines()


def test_read_acquisition_entry_falls_back_to_the_json_and_csv(saving_root):
    """Folders from before acquisition.yaml existed still list, dated from the folder name."""
    folder = write_acquisition(saving_root, "old_2026-05-05_13-02-12.243959", with_yaml=False, nz=200, nt=1)

    entry = read_acquisition_entry(str(folder))

    assert entry.started == datetime(2026, 5, 5, 13, 2, 12, 243959)
    assert entry.objective == "20x"
    assert (entry.nz, entry.nt) == (200, 1)
    assert entry.num_fovs == 8  # the coordinates.csv rows
    assert entry.num_regions is None
    assert not entry.has_settings
    assert entry.thumbnail_source.endswith(".tiff")


def test_read_acquisition_entry_survives_a_broken_yaml(saving_root):
    folder = write_acquisition(saving_root, "broken_2026-01-01_00-00-00.000000")
    (folder / "acquisition.yaml").write_text("acquisition: [unclosed")

    entry = read_acquisition_entry(str(folder))

    assert entry.label == "broken"
    assert entry.nz == 3  # from the json
    assert entry.has_settings  # the file is there; loading it reports the error


@pytest.mark.parametrize(
    "images, expected",
    [
        ("tiff", "TIFF images"),
        ("bmp", "BMP images"),
        ("multipage", "Multi-page TIFF"),
        ("ome", "OME-TIFF"),
        ("zarr", "OME-Zarr"),
        ("none", NO_IMAGES),
    ],
)
def test_inspect_images_names_the_storage_format(saving_root, images, expected):
    folder = write_acquisition(saving_root, f"fmt_{images}", images=images)

    fmt, source = inspect_images(str(folder))

    assert fmt == expected
    if images in ("zarr", "none"):
        assert source is None
    else:
        assert source and os.path.isfile(source)


def test_inspect_images_prefers_the_mosaic_overview(saving_root):
    folder = write_acquisition(saving_root, "zarr_with_overview", images="zarr", with_overview=True)

    fmt, source = inspect_images(str(folder))

    assert fmt == "OME-Zarr"
    assert source.endswith("mosaic_plate_5um.ome.tiff")


def test_scan_acquisitions_lists_newest_first_and_skips_other_folders(saving_root):
    write_acquisition(saving_root, "older_2026-11-01_08-00-00.000000", start_time=datetime(2026, 11, 1, 8).timestamp())
    write_acquisition(saving_root, "newer_2026-11-06_08-00-00.000000", start_time=datetime(2026, 11, 6, 8).timestamp())
    (saving_root / "not_an_acquisition").mkdir()
    (saving_root / "stray.txt").write_text("x")
    undated = saving_root / "undated"
    undated.mkdir()
    (undated / "acquisition parameters.json").write_text("{}")
    os.utime(undated, (0, datetime(2026, 11, 3, 8).timestamp()))  # sorted by mtime when nothing else is known

    names = [e.label for e in scan_acquisitions(str(saving_root))]

    assert names == ["newer", "undated", "older"]


def test_scan_acquisitions_of_a_missing_root_is_empty(tmp_path):
    assert scan_acquisitions(str(tmp_path / "nowhere")) == []


# ---------------------------------------------------------------------------------------------------------
# The widget
# ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def two_acquisitions(saving_root):
    write_acquisition(
        saving_root,
        "older_2026-11-01_08-00-00.000000",
        start_time=datetime(2026, 11, 1, 8).timestamp(),
        channels=("BF LED matrix full",),
    )
    write_acquisition(
        saving_root,
        "newer_2026-11-06_08-00-00.000000",
        start_time=datetime(2026, 11, 6, 8).timestamp(),
        widget_type="flexible",
        with_overview=True,
    )
    return saving_root


@pytest.fixture
def gallery(qtbot, two_acquisitions):
    widget = AcquisitionGalleryWidget(lambda: str(two_acquisitions))
    qtbot.addWidget(widget)
    widget.show()  # showEvent follows the saving folder and runs the first scan
    qtbot.waitUntil(lambda: widget.count() == 2, timeout=10000)
    yield widget
    widget.shutdown()


def wait_for_thumbnails(qtbot, widget):
    expected = {e.path for e in widget.entries() if e.thumbnail_source}
    qtbot.waitUntil(lambda: set(widget._thumbnail_cache) >= expected, timeout=10000)


def test_gallery_lists_the_saving_folder_newest_first(gallery, two_acquisitions):
    assert gallery.root == os.path.normpath(str(two_acquisitions))
    assert gallery.root_edit.text() == gallery.root
    assert [e.label for e in gallery.entries()] == ["newer", "older"]
    assert gallery.count_label.text() == "2"
    assert "2 acquisitions" in gallery.status_label.text()
    item = gallery.list_widget.item(0)
    assert item.text() == "newer\n2026-11-06 08:00"
    assert "Channels (2)" in item.toolTip()


def test_gallery_loads_thumbnails_in_the_background(gallery, qtbot):
    wait_for_thumbnails(qtbot, gallery)
    for entry in gallery.entries():
        source, _mtime, pixmap = gallery._thumbnail_cache[entry.path]
        assert source == entry.thumbnail_source
        assert max(pixmap.width(), pixmap.height()) <= AcquisitionGalleryWidget.THUMBNAIL_SIZE
        assert not gallery._items[entry.path].icon().isNull()


def test_double_click_opens_the_selected_acquisition_in_the_viewer(gallery, qtbot):
    gallery.select_path(gallery.entries()[1].path)
    with qtbot.waitSignal(gallery.signal_open_acquisition, timeout=1000) as blocker:
        gallery.list_widget.itemDoubleClicked.emit(gallery.list_widget.currentItem())
    assert blocker.args == [gallery.entries()[1].path]
    assert gallery.btn_open.isEnabled()
    assert "older" in gallery.details_label.text()


def test_without_a_viewer_opening_shows_the_folder_instead(gallery, qtbot, monkeypatch):
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()) or True)
    gallery.set_viewer_available(False, hint="NDViewer is off")
    gallery.select_path(gallery.entries()[0].path)

    with qtbot.assertNotEmitted(gallery.signal_open_acquisition):
        gallery.open_selected()

    assert os.path.normpath(opened[0]) == gallery.entries()[0].path
    assert gallery.status_label.text() == "NDViewer is off"
    assert gallery.btn_open.text() == "Open (NDViewer unavailable)"


def test_show_in_file_browser_opens_the_folder(gallery, monkeypatch):
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()) or True)
    gallery.select_path(gallery.entries()[0].path)
    gallery.show_selected_in_file_browser()
    assert os.path.normpath(opened[0]) == gallery.entries()[0].path


def test_load_settings_emits_the_folder_only_when_it_has_an_acquisition_yaml(gallery, qtbot, saving_root):
    gallery.select_path(gallery.entries()[0].path)
    assert gallery.btn_load_settings.isEnabled()
    with qtbot.waitSignal(gallery.signal_load_settings, timeout=1000) as blocker:
        gallery.load_selected_settings()
    assert blocker.args == [gallery.entries()[0].path]

    write_acquisition(saving_root, "legacy_2026-11-07_08-00-00.000000", with_yaml=False)
    gallery.refresh()
    qtbot.waitUntil(lambda: gallery.count() == 3, timeout=10000)
    legacy = gallery.entries()[0]
    assert not legacy.has_settings
    gallery.select_path(legacy.path)
    assert not gallery.btn_load_settings.isEnabled()
    with qtbot.assertNotEmitted(gallery.signal_load_settings):
        gallery.load_selected_settings()
    assert "settings cannot be loaded" in gallery.details_label.text()


def test_filter_hides_non_matching_acquisitions(gallery):
    gallery.filter_edit.setText("488")
    hidden = {e.label: gallery._items[e.path].isHidden() for e in gallery.entries()}
    assert hidden == {"newer": False, "older": True}
    assert gallery.count_label.text() == "1 of 2"

    gallery.filter_edit.setText("")
    assert not any(gallery._items[e.path].isHidden() for e in gallery.entries())


def test_refresh_keeps_the_selection_and_picks_up_new_folders(gallery, qtbot, saving_root):
    selected = gallery.entries()[1].path
    gallery.select_path(selected)
    write_acquisition(saving_root, "newest_2026-11-07_08-00-00.000000", start_time=datetime(2026, 11, 7, 8).timestamp())

    gallery.refresh()
    qtbot.waitUntil(lambda: gallery.count() == 3, timeout=10000)

    assert gallery.entries()[0].label == "newest"
    assert gallery.selected_entry().path == selected


def test_browse_pins_the_root_and_saving_folder_returns(gallery, qtbot, tmp_path, two_acquisitions):
    other = tmp_path / "elsewhere"
    other.mkdir()
    write_acquisition(other, "there_2026-11-02_08-00-00.000000")

    gallery.set_root(str(other), pinned=True)
    qtbot.waitUntil(lambda: gallery.count() == 1, timeout=10000)
    assert gallery.entries()[0].label == "there"
    gallery.refresh_if_needed()  # pinned: showing the tab again does not snap back to the saving folder
    assert gallery.root == os.path.normpath(str(other))

    gallery.follow_saving_folder()
    qtbot.waitUntil(lambda: gallery.count() == 2, timeout=10000)
    assert gallery.root == os.path.normpath(str(two_acquisitions))


def test_gallery_follows_a_changed_saving_folder_when_shown(qtbot, tmp_path):
    first = tmp_path / "first"
    first.mkdir()
    write_acquisition(first, "a_2026-11-02_08-00-00.000000")
    second = tmp_path / "second"
    second.mkdir()
    write_acquisition(second, "b_2026-11-02_08-00-00.000000")
    write_acquisition(second, "c_2026-11-03_08-00-00.000000")
    current = {"root": str(first)}
    widget = AcquisitionGalleryWidget(lambda: current["root"])
    qtbot.addWidget(widget)
    widget.show()
    qtbot.waitUntil(lambda: widget.count() == 1, timeout=10000)

    current["root"] = str(second)
    widget.refresh_if_needed()
    qtbot.waitUntil(lambda: widget.count() == 2, timeout=10000)
    assert widget.root == os.path.normpath(str(second))
    widget.shutdown()


def test_gallery_without_a_saving_folder_says_so(qtbot):
    widget = AcquisitionGalleryWidget(lambda: None)
    qtbot.addWidget(widget)
    widget.refresh()
    assert widget.count() == 0
    assert widget.status_label.text() == "No saving folder is set."
    widget.set_root(str("Z:/definitely/not/here"), pinned=True)
    assert "Folder not found" in widget.status_label.text()
    widget.shutdown()


def test_a_stale_scan_result_is_ignored(gallery, qtbot, tmp_path):
    """A refresh started later always wins, whatever order the worker threads finish in."""
    empty = tmp_path / "empty"
    empty.mkdir()
    gallery.set_root(str(empty), pinned=True)
    qtbot.waitUntil(lambda: gallery.count() == 0 and "0 acquisitions" in gallery.status_label.text(), timeout=10000)
    stale_generation = gallery._generation - 1
    gallery._on_scanned(stale_generation, "old-root", [Mock()])
    assert gallery.count() == 0


def test_shutdown_drops_late_results(gallery):
    gallery.shutdown()
    gallery._emit(gallery._events.scanned, gallery._generation, gallery.root, [])  # must not raise
    gallery.refresh()  # a no-op after shutdown
