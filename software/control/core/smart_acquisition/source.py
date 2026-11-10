"""Read a completed Squid acquisition back into FOV records and images.

Positions always come from the per-timepoint ``coordinates.csv``, never from
image metadata. That file is written unconditionally by
``MultiPointWorker.run_single_time_point`` regardless of the save mode, and it
holds the *actual* post-autofocus stage position. The OME-TIFF's PositionX/Y
plane metadata is not a substitute: it is staged in a temp-directory JSON
sidecar and only patched into the file once the whole stack is complete.

Note the unit trap: the per-timepoint ``coordinates.csv`` records ``z (um)``
while the top-level one records ``z (mm)``. We read the per-timepoint file
because only it carries the ``fov`` column needed to join to images.
"""

import glob
import json
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

import squid.logging
from control.acquisition_yaml_loader import parse_acquisition_yaml

from .targets import FovRecord

_log = squid.logging.get_logger(__name__)

#: Save modes we can read. ZARR_V3 has three distinct on-disk layouts and is
#: deliberately unsupported rather than half-supported.
SUPPORTED_MODES = ("INDIVIDUAL_IMAGES", "MULTI_PAGE_TIFF", "OME_TIFF")


class AcquisitionNotReady(RuntimeError):
    """Raised when an acquisition folder exists but is not finished being written."""


class UnsupportedLayout(NotImplementedError):
    """Raised for save modes this reader cannot handle."""


@dataclass
class AcquisitionSource:
    """A finished acquisition on disk, ready to be analysed."""

    path: str
    pixel_size_um: float
    channel_names: List[str]
    nz: int
    timepoints: List[int]
    save_mode: str
    objective_name: Optional[str] = None
    magnification: Optional[float] = None

    def __post_init__(self):
        self._shape_cache: Dict[Tuple[str, int], Tuple[int, int]] = {}


def _timepoint_dirs(acq_dir: str) -> List[int]:
    out = []
    for entry in os.listdir(acq_dir):
        if entry.isdigit() and os.path.isdir(os.path.join(acq_dir, entry)):
            out.append(int(entry))
    return sorted(out)


def _detect_save_mode(acq_dir: str, timepoints: List[int]) -> str:
    """Infer how images were written by looking at what is actually on disk."""
    if os.path.isdir(os.path.join(acq_dir, "ome_tiff")):
        return "OME_TIFF"
    if os.path.isdir(os.path.join(acq_dir, "plate.ome.zarr")) or os.path.isdir(os.path.join(acq_dir, "zarr")):
        return "ZARR_V3"
    for t in timepoints:
        tdir = os.path.join(acq_dir, str(t))
        if glob.glob(os.path.join(tdir, "*_stack.tif*")):
            return "MULTI_PAGE_TIFF"
        if glob.glob(os.path.join(tdir, "*.tif*")) or glob.glob(os.path.join(tdir, "*.bmp")):
            return "INDIVIDUAL_IMAGES"
    return "INDIVIDUAL_IMAGES"


def _require_done(acq_dir: str, timepoints: List[int], save_mode: str, require_complete: bool) -> None:
    """Refuse to analyse an acquisition that is still being written.

    This matters most for OME_TIFF: the stack file is pre-allocated zero-filled
    up-front, so reading it mid-run yields blank frames and the analyzer
    silently 'finds nothing' instead of failing.
    """
    if not require_complete:
        return
    if not timepoints:
        raise AcquisitionNotReady(f"No timepoint folders in {acq_dir}")
    # An OME-TIFF stack spans every timepoint, so it is only safe once the last
    # timepoint is done. For per-timepoint layouts, each folder stands alone.
    needed = [max(timepoints)] if save_mode == "OME_TIFF" else timepoints
    missing = [t for t in needed if not os.path.isfile(os.path.join(acq_dir, str(t), ".done"))]
    if missing:
        raise AcquisitionNotReady(
            f"Acquisition {acq_dir} is incomplete: no .done sentinel for timepoint(s) {missing}. "
            "Wait for the acquisition to finish, or pass require_complete=False to override."
        )


def open_acquisition(acq_dir: str, require_complete: bool = True) -> AcquisitionSource:
    """Open an acquisition folder and read its settings."""
    if not os.path.isdir(acq_dir):
        raise FileNotFoundError(f"Not a directory: {acq_dir}")

    parsed = parse_acquisition_yaml(acq_dir)
    with open(os.path.join(acq_dir, "acquisition.yaml"), "r", encoding="utf-8") as f:
        import yaml

        raw = yaml.safe_load(f) or {}

    pixel_size_um = parsed.objective_pixel_size_um
    if not pixel_size_um:
        raise ValueError(
            f"{acq_dir}/acquisition.yaml has no objective.pixel_size_um; cannot convert pixels to stage mm."
        )

    timepoints = _timepoint_dirs(acq_dir)
    save_mode = _detect_save_mode(acq_dir, timepoints)
    if save_mode not in SUPPORTED_MODES:
        raise UnsupportedLayout(
            f"Save mode {save_mode} is not supported by smart acquisition yet "
            f"(supported: {', '.join(SUPPORTED_MODES)})."
        )
    _require_done(acq_dir, timepoints, save_mode, require_complete)

    return AcquisitionSource(
        path=acq_dir,
        pixel_size_um=float(pixel_size_um),
        channel_names=list(parsed.channel_names),
        nz=int(parsed.nz or 1),
        timepoints=timepoints,
        save_mode=save_mode,
        objective_name=parsed.objective_name,
        magnification=(raw.get("objective") or {}).get("magnification"),
    )


def _read_timepoint_coordinates(acq_dir: str, t: int) -> pd.DataFrame:
    path = os.path.join(acq_dir, str(t), "coordinates.csv")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Missing {path}; cannot recover stage positions for timepoint {t}.")
    df = pd.read_csv(path)
    required = ("region", "fov", "z_level", "x (mm)", "y (mm)")
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"{path} is missing column(s) {missing}")
    return df


def _z_mm_from_row(row) -> Optional[float]:
    """Per-timepoint coordinates.csv stores microns; piezo offset is a separate column."""
    z_um = row.get("z (um)")
    if z_um is None or pd.isna(z_um):
        return None
    total_um = float(z_um)
    piezo = row.get("z_piezo (um)")
    if piezo is not None and not pd.isna(piezo):
        total_um += float(piezo)
    return total_um / 1000.0


def _pick_z_level(group: pd.DataFrame, nz: int) -> int:
    """Choose which plane of a z-stack to analyse: the middle one."""
    levels = sorted(int(z) for z in group["z_level"].unique())
    if not levels:
        return 0
    return levels[len(levels) // 2]


def list_fovs(
    source: AcquisitionSource,
    time_point: Optional[int] = None,
    z_level: Optional[int] = None,
    all_z: bool = False,
) -> List[FovRecord]:
    """Enumerate the FOVs to analyse.

    Defaults to the first timepoint and the middle z-level of each FOV, which is
    the right choice for a survey scan. Pass ``all_z`` to get one record per
    plane instead -- that is what a best-focus sweep needs. Image dimensions are
    read lazily on the first :func:`load_image` call and back-filled, so this
    stays cheap.
    """
    t = source.timepoints[0] if time_point is None else time_point
    if t not in source.timepoints:
        raise ValueError(f"Timepoint {t} not in {source.timepoints}")

    df = _read_timepoint_coordinates(source.path, t)
    records: List[FovRecord] = []
    for (region, fov), group in df.groupby(["region", "fov"], sort=False):
        if all_z:
            wanted = sorted(int(z) for z in group["z_level"].unique())
        else:
            chosen = z_level if z_level is not None else _pick_z_level(group, source.nz)
            wanted = [int(chosen)]

        for level in wanted:
            rows = group[group["z_level"] == level]
            if rows.empty:
                rows = group
            row = rows.iloc[0]
            w, h = _image_shape(source, str(region), int(fov), t, level)
            records.append(
                FovRecord(
                    region=str(region),
                    fov=int(fov),
                    time_point=t,
                    z_level=level,
                    x_mm=float(row["x (mm)"]),
                    y_mm=float(row["y (mm)"]),
                    z_mm=_z_mm_from_row(row),
                    pixel_size_um=source.pixel_size_um,
                    width_px=w,
                    height_px=h,
                )
            )
    return records


def _channel_filename(channel: str) -> str:
    """Mirror utils_acquisition's channel-name sanitisation."""
    return re.sub(r'[\s/\\:*?"<>|]+', "_", channel)


def _individual_image_path(
    source: AcquisitionSource, region: str, fov: int, t: int, z: int, channel: str
) -> Optional[str]:
    tdir = os.path.join(source.path, str(t))
    stem = f"{region}_{fov}_{z}_{_channel_filename(channel)}"
    for ext in ("tiff", "tif", "bmp", "png"):
        candidate = os.path.join(tdir, f"{stem}.{ext}")
        if os.path.isfile(candidate):
            return candidate
    # Fallback for channel names whose on-disk sanitisation differs from ours.
    # The caller has already checked that `channel` is one this acquisition has.
    matches = sorted(glob.glob(os.path.join(tdir, f"{region}_{fov}_{z}_*")))
    if matches:
        _log.warning(f"No file named {stem}.*; falling back to {os.path.basename(matches[0])}")
        return matches[0]
    return None


def _axis_positions(axes: str, t_index: int, z_level: int, c: int) -> Dict[str, int]:
    return {"T": t_index, "Z": z_level, "C": c}


def _plane_index(axes: str, shape, t_index: int, z_level: int, c: int, path: str) -> tuple:
    """Index tuple selecting one 2D plane, driven by the series' own axis order.

    tifffile squeezes singleton dimensions, so a single-channel single-timepoint
    stack comes back as 'ZYX' rather than 'TZCYX'. Blindly padding to five
    dimensions would silently index the wrong axis, so map by axis letter.
    """
    wanted = _axis_positions(axes, t_index, z_level, c)
    index = []
    for axis, size in zip(axes, shape):
        if axis in ("Y", "X"):
            index.append(slice(None))
            continue
        position = wanted.get(axis, 0)
        if position >= size:
            raise IndexError(f"{path}: axis {axis} has size {size}, asked for {position} (axes={axes})")
        index.append(position)
    return tuple(index)


def _page_number(axes: str, shape, t_index: int, z_level: int, c: int, path: str) -> int:
    """Flat page number of one plane, for files that cannot be memory-mapped."""
    wanted = _axis_positions(axes, t_index, z_level, c)
    outer = [(axis, size) for axis, size in zip(axes, shape) if axis not in ("Y", "X")]
    page = 0
    for position, (axis, size) in enumerate(outer):
        value = wanted.get(axis, 0)
        if value >= size:
            raise IndexError(f"{path}: axis {axis} has size {size}, asked for {value} (axes={axes})")
        stride = 1
        for _later_axis, later_size in outer[position + 1 :]:
            stride *= later_size
        page += value * stride
    return page


def _ome_path(source: AcquisitionSource, region: str, fov: int) -> str:
    return os.path.join(source.path, "ome_tiff", f"{region}_{fov}.ome.tiff")


def _channel_index(source: AcquisitionSource, channel: Optional[str]) -> int:
    if channel is None:
        return 0
    if channel in source.channel_names:
        return source.channel_names.index(channel)
    raise ValueError(f"Channel {channel!r} not in acquisition channels {source.channel_names}")


def _image_shape(source: AcquisitionSource, region: str, fov: int, t: int, z: int) -> Tuple[int, int]:
    """(width, height) without decoding the whole frame where possible.

    Cached per (region, fov): every plane of a stack shares a frame size, and
    without this a 33-plane sweep would re-open the file 33 times and, for a
    missing FOV, log the same warning 33 times.
    """
    cache_key = (region, fov)
    if cache_key in source._shape_cache:
        return source._shape_cache[cache_key]
    shape = _read_image_shape(source, region, fov, t, z)
    source._shape_cache[cache_key] = shape
    return shape


def _read_image_shape(source: AcquisitionSource, region: str, fov: int, t: int, z: int) -> Tuple[int, int]:
    try:
        if source.save_mode == "OME_TIFF":
            import tifffile

            with tifffile.TiffFile(_ome_path(source, region, fov)) as tf:
                shape = tf.series[0].shape  # TZCYX
                return int(shape[-1]), int(shape[-2])
        img = load_image(source, region, fov, t, z)
        return int(img.shape[1]), int(img.shape[0])
    except Exception as e:
        _log.warning(f"Could not determine image size for {region}_{fov}: {e}")
        return 0, 0


def load_image(
    source: AcquisitionSource,
    region: str,
    fov: int,
    time_point: int,
    z_level: int,
    channel: Optional[str] = None,
) -> np.ndarray:
    """Load one 2D frame, dispatching on the acquisition's save mode."""
    import tifffile

    if source.save_mode == "OME_TIFF":
        path = _ome_path(source, region, fov)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Missing OME-TIFF stack {path}")
        c = _channel_index(source, channel)
        t_index = source.timepoints.index(time_point)

        with tifffile.TiffFile(path) as tf:
            axes = tf.series[0].axes
            shape = tf.series[0].shape

        # One OME-TIFF holds every T, Z and C for this FOV and can be hundreds
        # of megabytes. Read only the plane asked for -- decoding the whole
        # series per request would re-read the entire stack for every plane of
        # a z sweep. Squid pre-allocates these files uncompressed, so memmap
        # works and touches only the bytes of the requested plane.
        try:
            stack = tifffile.memmap(path, mode="r")
            return np.array(stack[_plane_index(axes, shape, t_index, z_level, c, path)])
        except (ValueError, MemoryError, IndexError) as e:
            _log.debug(f"memmap unavailable for {path} ({e}); falling back to per-page read")

        with tifffile.TiffFile(path) as tf:
            series = tf.series[0]
            page = _page_number(axes, shape, t_index, z_level, c, path)
            if page >= len(series.pages):
                raise IndexError(
                    f"{path}: no plane for t={time_point} z={z_level} c={c} " f"(page {page} of {len(series.pages)})"
                )
            return np.asarray(series.pages[page].asarray())

    if source.save_mode == "MULTI_PAGE_TIFF":
        path = os.path.join(source.path, str(time_point), f"{region}_{fov}_stack.tiff")
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Missing multi-page stack {path}")
        c = _channel_index(source, channel)
        page = z_level * max(len(source.channel_names), 1) + c
        with tifffile.TiffFile(path) as tf:
            return np.asarray(tf.pages[page].asarray())

    # Validate an explicitly requested channel before touching the disk. The
    # filename fallback below matches on region/fov/z only, so an unknown
    # channel would otherwise silently return a *different* channel's image.
    if channel is not None:
        _channel_index(source, channel)
    chan = channel or (source.channel_names[0] if source.channel_names else "")
    path = _individual_image_path(source, region, fov, time_point, z_level, chan)
    if path is None:
        raise FileNotFoundError(
            f"No image for region={region} fov={fov} t={time_point} z={z_level} channel={chan!r} "
            f"under {os.path.join(source.path, str(time_point))}"
        )
    import cv2

    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise IOError(f"Could not decode {path}")
    return img


def load_fov_image(source: AcquisitionSource, fov: FovRecord, channel: Optional[str] = None) -> np.ndarray:
    return load_image(source, fov.region, fov.fov, fov.time_point, fov.z_level, channel)
