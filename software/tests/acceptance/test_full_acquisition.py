"""
End-to-end simulation-mode acquisition acceptance tests.

These drive a full MultiPointController acquisition against simulated hardware
(no GUI, no QApplication) and assert on observable artifacts only: files on
disk, their counts/shapes, coordinate CSVs, the acquisition-parameters JSON,
``.done`` markers, and the per-acquisition log. They intentionally pin CURRENT
master behavior; where the product writes something surprising the assertion
documents it with a ``# pins current behavior:`` note rather than "fixing" it.

Ordering note: ``acquisition parameters.json`` is written by
``start_new_experiment()`` (i.e. inside ``harness.new_experiment``), so any NZ /
Nt / channel / region configuration that must appear in that JSON has to be set
*before* ``new_experiment`` is called. The helpers below configure the
controller first, then create the experiment folder, then run.
"""

import glob
import json

import pytest

import tifffile

from control._def import FileSavingOption
from tests.acceptance.conftest import set_file_saving_option
from tests.acceptance.harness import (
    list_image_files,
    make_harness,
    read_coordinates_csv,
    timepoint_dir,
)

pytestmark = pytest.mark.acceptance


def _open_zarr3(array_path):
    """Open a Zarr v3 array written by the product's TensorStore writer.

    The store is true Zarr v3 (zarr.json + ``c/`` chunk keys); zarr-python
    2.15.0 cannot read that layout, so we use TensorStore -- the same library
    the product uses to write it.
    """
    import tensorstore as ts

    return ts.open({"driver": "zarr3", "kvstore": {"driver": "file", "path": str(array_path)}}).result()


def _plane_count_and_shape(full_shape):
    """Split an (..., Y, X) shape into (num_planes, (Y, X))."""
    plane_shape = tuple(full_shape[-2:])
    num_planes = 1
    for dim in full_shape[:-2]:
        num_planes *= dim
    return num_planes, plane_shape


def test_full_small_acquisition(tmp_path, acquisition_defaults):
    """Scenario 1: 2x2 FOVs x 2 channels x NZ=3, Nt=1, individual images."""
    h = make_harness()
    try:
        # Configure fully BEFORE new_experiment so the params JSON captures NZ/Nt.
        h.add_fov_grid("region0", 2, 2)
        h.select_channels(2)
        h.mpc.set_NZ(3)
        h.mpc.set_deltaZ(1.0)  # micrometers
        h.mpc.set_Nt(1)
        h.mpc.set_deltat(0.0)
        h.new_experiment(tmp_path / "exp", "small")

        h.run_and_wait(timeout_s=300)

        ed = h.experiment_dir

        # Image counts: 4 FOVs x 3 z x 2 channels.
        assert h.mpc.get_acquisition_image_count() == 24
        assert h.tracker.image_count == 24

        # Timepoint 0 holds exactly 24 individual image files.
        tp0 = timepoint_dir(ed, 0)
        assert len(list_image_files(tp0)) == 24

        # Per-timepoint coordinates.csv: one row per region x fov x z_level = 4x3.
        inner = read_coordinates_csv(tp0 / "coordinates.csv")
        assert len(inner) == 12
        for col in ("region", "fov", "z_level"):
            assert col in inner[0]

        # Top-level coordinates.csv: one row per FOV.
        top = read_coordinates_csv(ed / "coordinates.csv")
        assert len(top) == 4

        # acquisition parameters.json (literal space in the filename).
        with open(ed / "acquisition parameters.json") as f:
            params = json.load(f)
        assert params["Nz"] == 3
        assert params["Nt"] == 1
        assert params["dz(um)"] == 1.0  # set_deltaZ(1.0) is micrometers

        # .done markers at experiment root and in the timepoint dir.
        assert (ed / ".done").exists()
        assert (tp0 / ".done").exists()

        # Per-acquisition log exists and is clean of ERROR-level records.
        log_text = (ed / "acquisition.log").read_text()
        error_lines = [ln for ln in log_text.splitlines() if " - ERROR - " in ln]
        assert error_lines == [], f"unexpected ERROR log lines: {error_lines[:5]}"
    finally:
        h.close()


def test_format_parity_ome_tiff_vs_zarr(tmp_path, acquisition_defaults):
    """Scenario 2: same geometry (2x2 FOVs x 2 ch x NZ=2, Nt=1) in OME-TIFF and
    Zarr v3 represents identical logical content (16 planes, same per-plane
    shape and dtype)."""
    # The product's ZARR_V3 writer requires tensorstore (and so does reading
    # the store back) — skip where it isn't installed, like test_zarr_writer.py.
    pytest.importorskip("tensorstore")

    def _run(option, subdir):
        set_file_saving_option(acquisition_defaults, option)
        h = make_harness()
        try:
            h.add_fov_grid("region0", 2, 2)
            h.select_channels(2)
            h.mpc.set_NZ(2)
            h.mpc.set_deltaZ(1.0)
            h.mpc.set_Nt(1)
            h.mpc.set_deltat(0.0)
            h.new_experiment(tmp_path / subdir, subdir)
            h.run_and_wait(timeout_s=300)
            return h.experiment_dir
        finally:
            h.close()

    # --- OME-TIFF ---
    ome_dir = _run(FileSavingOption.OME_TIFF, "ome")
    # pins current behavior: OME-TIFF writes one file per FOV under
    # {experiment}/ome_tiff/, NOT under the timepoint dir.
    ome_files = sorted(glob.glob(str(ome_dir / "**" / "*.ome.tiff"), recursive=True))
    assert len(ome_files) == 4, f"expected 4 per-FOV OME-TIFF files, found {ome_files}"
    ome_total_planes = 0
    ome_plane_shapes = set()
    ome_dtypes = set()
    for path in ome_files:
        with tifffile.TiffFile(path) as tf:
            series = tf.series[0]
            # pins current behavior: per-FOV series is ZCYX (Z=2, C=2).
            planes, plane_shape = _plane_count_and_shape(series.shape)
            ome_total_planes += planes
            ome_plane_shapes.add(plane_shape)
            ome_dtypes.add(series.dtype)
    assert ome_total_planes == 16  # 4 FOVs x 2 z x 2 ch
    assert len(ome_plane_shapes) == 1
    (ome_plane_shape,) = ome_plane_shapes
    assert len(ome_dtypes) == 1
    (ome_dtype,) = ome_dtypes

    # --- Zarr v3 ---
    zarr_dir = _run(FileSavingOption.ZARR_V3, "zarr")
    # pins current behavior: non-well flexible region writes per-FOV stores at
    # {experiment}/zarr/{region_id}/fov_{n}.ome.zarr/0 (experiment ROOT, not
    # under the timepoint dir as ZarrWriterInfo's docstring path suggests).
    zarr_arrays = sorted(glob.glob(str(zarr_dir / "**" / "fov_*.ome.zarr" / "0"), recursive=True))
    assert len(zarr_arrays) == 4, f"expected 4 per-FOV zarr stores, found {zarr_arrays}"
    zarr_total_planes = 0
    zarr_plane_shapes = set()
    zarr_dtypes = set()
    for array_path in zarr_arrays:
        arr = _open_zarr3(array_path)
        # pins current behavior: per-FOV array is 5D TCZYX (T=1, C=2, Z=2).
        planes, plane_shape = _plane_count_and_shape(tuple(arr.shape))
        zarr_total_planes += planes
        zarr_plane_shapes.add(plane_shape)
        zarr_dtypes.add(arr.dtype.numpy_dtype)
    assert zarr_total_planes == 16  # 4 FOVs x 2 z x 2 ch
    assert len(zarr_plane_shapes) == 1
    (zarr_plane_shape,) = zarr_plane_shapes
    assert len(zarr_dtypes) == 1
    (zarr_dtype,) = zarr_dtypes

    # --- Parity between formats ---
    assert ome_total_planes == zarr_total_planes == 16
    assert ome_plane_shape == zarr_plane_shape
    assert ome_dtype == zarr_dtype


def test_multi_timepoint_smoke(tmp_path, acquisition_defaults):
    """Scenario 5: Nt=2, 2x1 FOVs, 1 channel, NZ=1, individual images."""
    h = make_harness()
    try:
        h.add_fov_grid("region0", 2, 1)
        h.select_channels(1)
        h.mpc.set_NZ(1)
        h.mpc.set_Nt(2)
        h.mpc.set_deltat(0.0)
        h.new_experiment(tmp_path / "exp", "multi_t")

        h.run_and_wait(timeout_s=300)

        ed = h.experiment_dir
        assert h.tracker.image_count == 4  # 2 FOVs x 1 z x 1 ch x 2 timepoints

        for t in (0, 1):
            tp = timepoint_dir(ed, t)
            assert tp.is_dir(), f"timepoint dir {t} missing"
            assert len(list_image_files(tp)) == 2
            rows = read_coordinates_csv(tp / "coordinates.csv")
            assert len(rows) == 2
            assert (tp / ".done").exists()

        assert (ed / ".done").exists()
    finally:
        h.close()
