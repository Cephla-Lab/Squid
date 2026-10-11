"""Custom plate PNGs: one filename convention on write and read.

create_wellplate_image used to save "my custom plate" as
images/my_custom_plate.png while NavigationViewer looked up
images/my custom plate.png - so every custom format named with a space
(including the dialog's own placeholder text) silently fell back to the
slide-carrier image. Writes now preserve spaces; reads accept the legacy
underscore files.
"""

import os

import control.widgets


def test_create_wellplate_image_preserves_spaces(qtbot, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("images")

    format_data = {
        "rows": 2,
        "cols": 4,
        "well_spacing_mm": 12.0,
        "well_size_mm": 9.0,
        "a1_x_mm": 15.0,
        "a1_y_mm": 12.0,
    }
    # create_wellplate_image never touches self, so no dialog is constructed.
    path = control.widgets.WellplateCalibration.create_wellplate_image(
        None, "my custom plate", format_data, 127.76, 85.48
    )

    assert path == os.path.join("images", "my custom plate.png")
    assert os.path.exists(path)


def _viewer_lookup(monkeypatch, sample):
    """Run the REAL NavigationViewer.update_wellplate_settings lookup on a
    minimal instance, capturing what it tries to load."""
    import control.core.core as core_module

    viewer = core_module.NavigationViewer.__new__(core_module.NavigationViewer)
    viewer._log = __import__("logging").getLogger("test")
    viewer.image_paths = {"glass slide": "images/slide carrier_828x662.png"}
    viewer.x_mm = 0.0
    viewer.y_mm = 0.0

    loaded = []
    monkeypatch.setattr(viewer, "load_background_image", lambda p: loaded.append(p), raising=False)
    monkeypatch.setattr(viewer, "create_layers", lambda: None, raising=False)
    monkeypatch.setattr(viewer, "update_display_properties", lambda s: None, raising=False)
    monkeypatch.setattr(viewer, "draw_current_fov", lambda x, y: None, raising=False)

    from control.core.plate_transform import WellplateSettings

    viewer.update_wellplate_settings(WellplateSettings(sample, 15.0, 12.0, 177, 142, 9.0, 12.0, 0, 2, 4))
    assert len(loaded) == 1
    return loaded[0]


def test_viewer_prefers_raw_name_and_accepts_legacy(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("images")
    raw = os.path.join("images", "my custom plate.png")
    legacy = os.path.join("images", "my_custom_plate.png")

    # Only the legacy underscore file exists (image from an older build) -> found.
    open(legacy, "w").close()
    assert _viewer_lookup(monkeypatch, "my custom plate") == legacy

    # Both exist -> the raw-name file wins.
    open(raw, "w").close()
    assert _viewer_lookup(monkeypatch, "my custom plate") == raw


def test_viewer_falls_back_to_default_with_warning(tmp_path, monkeypatch, caplog):
    import logging

    monkeypatch.chdir(tmp_path)
    os.makedirs("images")

    with caplog.at_level(logging.WARNING):
        path = _viewer_lookup(monkeypatch, "nonexistent plate")

    assert path == "images/slide carrier_828x662.png"
    assert any("nonexistent plate" in r.getMessage() for r in caplog.records)


def test_plate_image_ignores_wellplate_offset(qtbot, tmp_path, monkeypatch):
    """The PNG is a plate-frame display asset: WELLPLATE_OFFSET must never leak
    into it, or the drawn wells shear away from the a1_x_pixel registration."""
    import control.widgets

    monkeypatch.chdir(tmp_path)
    os.makedirs("images")
    format_data = {
        "rows": 2,
        "cols": 3,
        "well_spacing_mm": 9.0,
        "well_size_mm": 6.2,
        "a1_x_mm": 11.31,
        "a1_y_mm": 10.75,
    }

    monkeypatch.setattr(control.widgets, "WELLPLATE_OFFSET_X_mm", 0.0)
    monkeypatch.setattr(control.widgets, "WELLPLATE_OFFSET_Y_mm", 0.0)
    control.widgets.WellplateCalibration.create_wellplate_image(None, "offset zero", format_data, 127.76, 85.48)

    monkeypatch.setattr(control.widgets, "WELLPLATE_OFFSET_X_mm", 25.0)
    monkeypatch.setattr(control.widgets, "WELLPLATE_OFFSET_Y_mm", -25.0)
    control.widgets.WellplateCalibration.create_wellplate_image(None, "offset huge", format_data, 127.76, 85.48)

    zero = open(os.path.join("images", "offset zero.png"), "rb").read()
    huge = open(os.path.join("images", "offset huge.png"), "rb").read()
    assert zero == huge
