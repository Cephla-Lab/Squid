"""Colour composite previews of an acquisition: from the mosaic overview, or from the first field of view."""

import os
import shutil

import cv2
import numpy as np
import pytest
import tifffile

from control.gallery_previews import (
    PREVIEW_MAX_SIDE,
    array_to_pixmap,
    channel_rgb,
    composite,
    find_preview_source,
    load_channel_planes,
    load_thumbnail_array,
    stretch_to_unit,
    write_preview_png,
)
from tests.control.gallery_fixtures import saving_root, write_acquisition  # noqa: F401  (saving_root is a fixture)

BF = "BF LED matrix full"
F488 = "Fluorescence 488 nm Ex"
F638 = "Fluorescence 638 nm Ex"


def test_channel_rgb_uses_the_channel_colour_map():
    assert channel_rgb(F488) == (0x1F, 0xFF, 0x00)
    assert channel_rgb(BF) == (255, 255, 255)


def test_find_preview_source_prefers_the_latest_overview(saving_root):
    folder = write_acquisition(saving_root, "exp", nt=2, with_overview=True)
    src = find_preview_source(str(folder))
    assert src.kind == "overview"
    assert src.overview_path.endswith(os.path.join("1", "mosaic_view", "mosaic_plate_5um.ome.tiff"))
    assert src.channels == [BF, F488]  # from the sidecar yaml


def test_find_preview_source_reads_channel_names_from_the_ome_metadata_then_numbers_them(saving_root):
    folder = write_acquisition(saving_root, "exp", nt=1, with_overview=True)
    mosaic = folder / "0" / "mosaic_view"
    os.remove(mosaic / "mosaic_plate_5um.yaml")
    stack = np.zeros((2, 10, 10), np.uint16)
    tifffile.imwrite(
        mosaic / "mosaic_plate_5um.ome.tiff", stack, metadata={"axes": "CYX", "Channel": {"Name": ["a", "b"]}}
    )
    assert find_preview_source(str(folder)).channels == ["a", "b"]
    tifffile.imwrite(mosaic / "mosaic_plate_5um.ome.tiff", stack, ome=False)
    assert find_preview_source(str(folder)).channels == ["channel 1", "channel 2"]


def test_find_preview_source_falls_back_to_first_fov_images(saving_root):
    folder = write_acquisition(saving_root, "exp", nt=2)
    src = find_preview_source(str(folder))
    assert src.kind == "images" and set(src.image_paths) == {BF, F488}
    assert all(os.path.basename(p).startswith("A1_0_0_") for p in src.image_paths.values())
    assert sorted(src.channels) == sorted([BF, F488])


def test_find_preview_source_reads_the_first_fov_of_a_multipage_stack(saving_root, tmp_path):
    folder = write_acquisition(saving_root, "exp", images="multipage", nz=3)
    src = find_preview_source(str(folder))
    assert src.kind == "stack" and src.channels == [BF, F488]
    assert os.path.basename(src.stack_path) == "A1_0_stack.tiff"

    planes = load_channel_planes(src, PREVIEW_MAX_SIDE)
    assert list(planes) == [BF, F488]
    assert all(plane.shape == (48, 64) and plane.any() for plane in planes.values())  # z=0, not the black z>0
    assert write_preview_png(str(folder), str(tmp_path / "p.png")) == [BF, F488]


def test_find_preview_source_reads_t0_z0_of_an_ome_tiff(saving_root, tmp_path):
    folder = write_acquisition(saving_root, "exp", images="ome", nz=3, nt=2)
    src = find_preview_source(str(folder))
    assert src.kind == "ome" and src.channels == [BF, F488]
    assert src.ome_path.endswith(os.path.join("ome_tiff", "A1_0.ome.tiff"))

    planes = load_channel_planes(src, PREVIEW_MAX_SIDE)
    assert list(planes) == [BF, F488]
    assert all(plane.shape == (48, 64) and plane.any() for plane in planes.values())  # only t=0, z=0 is lit
    assert write_preview_png(str(folder), str(tmp_path / "p.png")) == [BF, F488]


def test_find_preview_source_finds_an_ome_tiff_inside_the_first_timepoint(saving_root):
    folder = write_acquisition(saving_root, "exp", images="ome", nz=1, nt=1)
    os.makedirs(folder / "0" / "ome_tiff")
    os.replace(folder / "ome_tiff" / "B2_0.ome.tiff", folder / "0" / "ome_tiff" / "B2_0.ome.tiff")
    shutil.rmtree(folder / "ome_tiff")
    src = find_preview_source(str(folder))
    assert src.kind == "ome" and src.ome_path.endswith(os.path.join("0", "ome_tiff", "B2_0.ome.tiff"))
    assert list(load_channel_planes(src, PREVIEW_MAX_SIDE)) == [BF, F488]


def test_find_preview_source_none_for_zarr_without_overview(saving_root):
    folder = write_acquisition(saving_root, "exp", images="zarr")
    src = find_preview_source(str(folder))
    assert src.kind == "none" and src.image_paths == {} and src.channels == []


def test_composite_colours_channels_and_clips():
    # Both channels span their range, so the stretch maps 0 -> 0 and 4000 -> 1 (a flat channel would map by dtype range instead).
    planes = {F488: np.array([[0, 4000]], np.uint16), F638: np.array([[4000, 0]], np.uint16)}
    rgb = composite(planes)
    assert rgb.shape == (1, 2, 3)
    assert rgb[0, 0].tolist() == [255, 0, 0]  # 638 only
    assert rgb[0, 1].tolist() == [0x1F, 0xFF, 0x00]  # 488 only, its map colour
    assert composite(planes, visible={F488})[0, 0].tolist() == [0, 0, 0]
    both = composite({F488: np.array([[0, 4000]], np.uint16), F638: np.array([[0, 4000]], np.uint16)})
    assert both[0, 1].tolist() == [255, 255, 0]  # additive, clipped


def test_composite_keeps_a_flat_channel_dark_and_handles_rgb_planes():
    flat = composite({F488: np.zeros((4, 4), np.uint16)})
    assert flat.max() == 0
    assert stretch_to_unit(np.full((2, 2), 32768, np.uint16)).max() == np.float32(32768 / 65535)
    assert composite({}).shape == (1, 1, 3) and composite({}).max() == 0
    colour = np.zeros((4, 4, 3), np.uint8)
    colour[..., 0] = 255
    assert composite({BF: colour}).shape == (4, 4, 3)


def test_write_preview_png_writes_a_bounded_rgb_png(saving_root, tmp_path):
    folder = write_acquisition(saving_root, "exp", nt=1, with_overview=True)
    mosaic = folder / "0" / "mosaic_view" / "mosaic_plate_5um.ome.tiff"
    stack = np.zeros((2, 400, 600), np.uint16)
    stack[0, :200] = 4000
    stack[1, 200:] = 4000
    tifffile.imwrite(mosaic, stack, metadata={"axes": "CYX"})
    out = str(tmp_path / "preview.png")

    channels = write_preview_png(str(folder), out)

    assert channels == [BF, F488]
    bgr = cv2.imread(out, cv2.IMREAD_UNCHANGED)
    assert bgr.shape == (341, 512, 3)
    assert bgr[0, 0, ::-1].tolist() == [255, 255, 255]  # BF lights the top half
    assert bgr[-1, 0, ::-1].tolist() == [0x1F, 0xFF, 0x00]  # 488 lights the bottom half


def test_write_preview_png_replaces_the_file_whole(saving_root, tmp_path, monkeypatch):
    """A preview being upgraded is never seen half-written: it is written beside, then moved over the old one."""
    folder = write_acquisition(saving_root, "exp", nt=1, with_overview=True)
    out = tmp_path / "preview.png"
    out.write_bytes(b"old preview")
    moves = []
    real_replace = os.replace

    def replace(src, dst):
        moves.append((str(src), str(dst), out.read_bytes()))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace)

    assert write_preview_png(str(folder), str(out)) is not None

    assert moves == [(str(out) + ".tmp", str(out), b"old preview")]  # the old file is intact until the move
    assert cv2.imread(str(out)) is not None
    assert not os.path.exists(str(out) + ".tmp")


def test_write_preview_png_returns_none_without_a_source(saving_root, tmp_path):
    folder = write_acquisition(saving_root, "exp", images="zarr")
    out = str(tmp_path / "preview.png")
    assert write_preview_png(str(folder), out) is None
    assert not os.path.exists(out)


def test_write_preview_png_survives_an_unreadable_image(saving_root, tmp_path):
    folder = write_acquisition(saving_root, "exp", nt=1)
    junk = folder / "0" / "A1_0_0_Fluorescence_488_nm_Ex.tiff"
    junk.write_bytes(b"not a tiff")
    out = str(tmp_path / "preview.png")

    channels = write_preview_png(str(folder), out)

    assert channels is not None and os.path.exists(out)
    bgr = cv2.imread(out)
    assert bgr is not None and bgr[..., 1].max() > 0  # BF (white) still drawn


def test_load_channel_planes_downsamples_but_never_upsamples(saving_root):
    folder = write_acquisition(saving_root, "exp", nt=1, with_overview=True)
    src = find_preview_source(str(folder))
    small = load_channel_planes(src, 30)
    assert small[BF].shape == (20, 30) and small[BF].dtype == np.uint16
    big = load_channel_planes(src, 512)
    assert big[BF].shape == (40, 60)


def test_load_thumbnail_array_stretches_16bit_and_fits_the_size(tmp_path):
    image = np.full((400, 800), 1000, dtype=np.uint16)
    image[:, 400:] = 1200  # a dim image: 200 counts of contrast out of 65535
    path = tmp_path / "dim.tiff"
    tifffile.imwrite(path, image)

    thumb = load_thumbnail_array(str(path), 100)

    assert thumb.dtype == np.uint8
    assert thumb.shape == (50, 100)
    assert thumb[:, :45].max() < 10 and thumb[:, 55:].min() > 245


def test_load_thumbnail_array_max_projects_a_small_overview(tmp_path):
    stack = np.zeros((2, 40, 60), dtype=np.uint16)
    stack[0, :20, 10:50] = 4000  # channel 0 lights the top of a well, channel 1 the bottom
    stack[1, 20:, 10:50] = 4000
    path = tmp_path / "mosaic_plate_5um.ome.tiff"
    tifffile.imwrite(path, stack, metadata={"axes": "CYX"})

    thumb = load_thumbnail_array(str(path), 60)

    assert thumb.shape == (40, 60)
    assert thumb[:, 10:50].min() > 200  # both halves lit: the channels were combined
    assert thumb[:, :10].max() < 10 and thumb[:, 50:].max() < 10


def test_load_thumbnail_array_keeps_a_flat_image_at_its_own_level(tmp_path):
    """No contrast to stretch: an empty frame stays black, a lit one shows grey instead of black."""
    tifffile.imwrite(tmp_path / "dark.tiff", np.zeros((20, 20), dtype=np.uint16))
    tifffile.imwrite(tmp_path / "lit.tiff", np.full((20, 20), 32768, dtype=np.uint16))

    assert load_thumbnail_array(str(tmp_path / "dark.tiff"), 100).max() == 0
    lit = load_thumbnail_array(str(tmp_path / "lit.tiff"), 100)
    assert 120 <= lit.min() <= lit.max() <= 135


def test_load_thumbnail_array_reads_color_images_and_rejects_junk(tmp_path):
    import cv2

    color = np.zeros((30, 40, 3), dtype=np.uint8)
    color[..., 2] = 255  # BGR red
    cv2.imwrite(str(tmp_path / "c.png"), color)
    (tmp_path / "junk.tiff").write_bytes(b"not a tiff")

    thumb = load_thumbnail_array(str(tmp_path / "c.png"), 40)
    assert thumb.shape == (30, 40, 3)
    assert thumb[0, 0].tolist() == [255, 0, 0]  # RGB
    assert load_thumbnail_array(str(tmp_path / "junk.tiff"), 100) is None
    assert load_thumbnail_array(str(tmp_path / "missing.tiff"), 100) is None


def test_array_to_pixmap(qtbot):
    gray = array_to_pixmap(np.zeros((20, 30), dtype=np.uint8))
    rgb = array_to_pixmap(np.zeros((20, 30, 3), dtype=np.uint8))
    assert (gray.width(), gray.height()) == (30, 20)
    assert (rgb.width(), rgb.height()) == (30, 20)


@pytest.mark.parametrize("with_yaml", [True, False])
def test_first_fov_images_accept_underscores_in_the_region_name(saving_root, with_yaml):
    """Flexible scans take ids such as sample_left; the two numbers, not the first underscore, split the name."""
    folder = write_acquisition(
        saving_root, "flex", widget_type="flexible", regions=(("sample_left", 2),), with_yaml=with_yaml
    )
    src = find_preview_source(str(folder))
    assert src.kind == "images" and sorted(src.channels) == sorted([BF, F488])
    assert all(os.path.basename(p).startswith("sample_left_0_0_") for p in src.image_paths.values())


def test_a_channel_name_starting_with_digits_is_told_apart_by_the_run_s_channel_list(saving_root):
    folder = write_acquisition(saving_root, "digits", channels=("488 nm", BF))
    src = find_preview_source(str(folder))
    assert sorted(src.channels) == ["488 nm", BF]
    assert os.path.basename(src.image_paths["488 nm"]) == "A1_0_0_488_nm.tiff"
    assert os.path.basename(src.image_paths[BF]) == "A1_0_0_BF_LED_matrix_full.tiff"
