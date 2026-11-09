"""Preview images for the acquisition gallery: a colour composite of an acquisition's mosaic overview or,
without one, of its first field of view, plus the single-image thumbnail helpers the gallery list uses."""

import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

import cv2
import numpy as np
import tifffile
import yaml
from qtpy.QtGui import QImage, QPixmap

import squid.logging
from control._def import CHANNEL_COLORS_MAP
from control.utils_channel import extract_wavelength_from_config_name

_log = squid.logging.get_logger(__name__)

PREVIEW_MAX_SIDE = 512

# Largest overview whose channels are all read for a thumbnail; above it only the first channel is read.
_MAX_OVERVIEW_BYTES_FOR_MIP = 64 * 1024 * 1024

# <region>_<fov>_<z>_<channel name with underscores>.<extension>. The region may itself contain underscores (a
# flexible-scan id such as sample_left), so it is matched greedily and the two numbers anchor the split; a channel
# name that starts with digits is told apart by the run's own channel list (_parse_image_name).
_IMAGE_EXTENSIONS = ("tiff", "tif", "bmp", "png", "jpg", "jpeg")
_IMAGE_NAME = re.compile(
    r"^(?P<region>.+)_(?P<fov>\d+)_(?P<z>\d+)_(?P<channel>.+)\.(" + "|".join(_IMAGE_EXTENSIONS) + r")$", re.IGNORECASE
)
_IMAGE_STEM = re.compile(r"^(?P<region>.+)_(?P<fov>\d+)_(?P<z>\d+)$")
_OVERVIEW_NAME = re.compile(r"^mosaic_.*\.ome\.tiff?$", re.IGNORECASE)
# MULTI_PAGE_TIFF: <timepoint>/<region>_<fov>_stack.tiff, one page per (z, channel)
_STACK_NAME = re.compile(r"^(?P<region>.+)_(?P<fov>\d+)_stack\.tiff?$", re.IGNORECASE)
# OME_TIFF: ome_tiff/<region>_<fov>.ome.tiff, one TZCYX series per FOV
_OME_NAME = re.compile(r"^(?P<region>.+)_(?P<fov>\d+)\.ome\.tiff?$", re.IGNORECASE)
_TIFF_PAGE_NAME = 285


@dataclass
class PreviewSource:
    kind: str = "none"  # "overview" | "images" | "stack" | "ome" | "none"
    overview_path: Optional[str] = None
    image_paths: Dict[str, str] = field(default_factory=dict)  # channel name -> first-FOV image file
    channels: List[str] = field(default_factory=list)
    stack_path: Optional[str] = None  # the first FOV's multi-page TIFF
    stack_pages: Dict[str, int] = field(default_factory=dict)  # channel name -> page index (lowest z)
    ome_path: Optional[str] = None  # the first FOV's OME-TIFF


def _timepoint_dirs(folder: str) -> List[Tuple[int, str]]:
    """(timepoint, path) of the numbered subfolders, ascending."""
    found = []
    try:
        with os.scandir(folder) as it:
            for entry in it:
                if entry.name.isdigit() and entry.is_dir():
                    found.append((int(entry.name), entry.path))
    except OSError:
        return []
    return sorted(found)


def _overview_in(timepoint_path: str) -> Optional[str]:
    mosaic_dir = os.path.join(timepoint_path, "mosaic_view")
    try:
        names = sorted(n for n in os.listdir(mosaic_dir) if _OVERVIEW_NAME.match(n))
    except OSError:
        return None
    return os.path.join(mosaic_dir, names[0]) if names else None


def _overview_plane_count(path: str) -> int:
    try:
        with tifffile.TiffFile(path) as tif:
            shape = tif.series[0].shape
            return int(shape[0]) if len(shape) == 3 else 1
    except Exception:
        _log.exception(f"Cannot read the overview {path}")
        return 0


def _ome_channel_names(path: str) -> List[str]:
    """The Name of every OME Channel element in the file; empty when any is unnamed or there is no OME XML."""
    try:
        with tifffile.TiffFile(path) as tif:
            xml_text = tif.ome_metadata
        if xml_text:
            root = ET.fromstring(xml_text)
            names = [el.attrib.get("Name") for el in root.iter() if el.tag.rsplit("}", 1)[-1] == "Channel"]
            if names and all(names):
                return names
    except Exception:
        pass
    return []


def _overview_channel_names(path: str) -> List[str]:
    """Sidecar yaml channel_names, else the OME Channel names, else channel 1..N."""
    stem = re.sub(r"\.ome\.tiff?$", "", path, flags=re.IGNORECASE)
    try:
        with open(stem + ".yaml", "r", encoding="utf-8") as f:
            names = (yaml.safe_load(f) or {}).get("channel_names")
        if isinstance(names, list) and names:
            return [str(n) for n in names]
    except Exception:
        pass
    names = _ome_channel_names(path)
    if names:
        return names
    return [f"channel {i + 1}" for i in range(_overview_plane_count(path))]


def _acquisition_channel_names(folder: str) -> List[str]:
    """The channel names the run recorded, from acquisition.yaml or, for older runs, acquisition_channels.yaml; []
    when neither names any (oldest data) or they are unreadable."""
    for file_name in ("acquisition.yaml", "acquisition_channels.yaml"):
        try:
            with open(os.path.join(folder, file_name), encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except (OSError, yaml.YAMLError):
            continue
        channels = data.get("channels") if isinstance(data, dict) else None
        if isinstance(channels, list):
            names = [str(c["name"]) for c in channels if isinstance(c, dict) and c.get("name")]
            if names:
                return names
    return []


def _channel_suffixes(channel_names: Sequence[str]) -> List[Tuple[str, str]]:
    """(file-name suffix, channel) for each known channel, longest suffix first, as the saver writes the names."""
    return sorted(((f"_{c.replace(' ', '_')}", c) for c in channel_names), key=lambda s: -len(s[0]))


def _parse_image_name(name: str, suffixes: Sequence[Tuple[str, str]] = ()) -> Optional[Tuple[str, int, int, str]]:
    """(region, fov, z, channel) of an individual image file name, or None. A known channel (_channel_suffixes) is
    matched as the file's suffix first, which settles names whose region and channel both contain underscores or
    digits; the pattern alone decides files of channels the run did not list (and would mis-split a channel name
    that starts with digits, such as "488 nm", when the run recorded no channel names at all)."""
    stem, dot, ext = name.rpartition(".")
    if not dot or ext.lower() not in _IMAGE_EXTENSIONS:
        return None
    for suffix, channel in suffixes:
        if stem.endswith(suffix):
            m = _IMAGE_STEM.match(stem[: -len(suffix)])
            if m:
                return m["region"], int(m["fov"]), int(m["z"]), channel
    m = _IMAGE_NAME.match(name)
    if m:
        return m["region"], int(m["fov"]), int(m["z"]), m["channel"].replace("_", " ")
    return None


def _first_fov_images(timepoint_path: str, channel_names: Sequence[str] = ()) -> Dict[str, str]:
    """Channel name -> file, for the smallest (region, fov) found and the smallest z of each channel."""
    suffixes = _channel_suffixes(channel_names)
    candidates = []  # (region, fov, z, channel, path)
    try:
        with os.scandir(timepoint_path) as it:
            for entry in it:
                parsed = _parse_image_name(entry.name, suffixes)
                if parsed and entry.is_file():
                    candidates.append((*parsed, entry.path))
    except OSError:
        return {}
    if not candidates:
        return {}
    region, fov = min((c[0], c[1]) for c in candidates)
    best: Dict[str, Tuple[int, str]] = {}
    for r, f, z, channel, path in candidates:
        if (r, f) == (region, fov) and (channel not in best or z < best[channel][0]):
            best[channel] = (z, path)
    return {channel: best[channel][1] for channel in sorted(best)}


def _first_fov_file(directory: str, pattern) -> Optional[str]:
    """The file in directory matching pattern with the smallest (region, fov); None when there is none."""
    candidates = []
    try:
        with os.scandir(directory) as it:
            for entry in it:
                m = pattern.match(entry.name)
                if m and entry.is_file():
                    candidates.append((m["region"], int(m["fov"]), entry.path))
    except OSError:
        return None
    return min(candidates)[2] if candidates else None


def _stack_channel_pages(path: str) -> Dict[str, int]:
    """Channel name -> index of its lowest-z page in a multi-page TIFF, channels in acquisition order. The writer
    stores the channel in each page's PageName tag and channel / z_level in its description JSON. A stack whose
    pages name no channel gives its first page as "channel 1"."""
    best: Dict[str, Tuple[int, int]] = {}  # channel -> (z, page index)
    try:
        with tifffile.TiffFile(path) as tif:
            for i, page in enumerate(tif.pages):
                try:
                    info = json.loads(page.description) if page.description else {}
                except ValueError:
                    info = {}
                info = info if isinstance(info, dict) else {}
                tag = page.tags.get(_TIFF_PAGE_NAME)
                name = tag.value if tag is not None and isinstance(tag.value, str) and tag.value else None
                name = name or (str(info["channel"]) if info.get("channel") else None)
                if name is None:
                    continue
                z = info.get("z_level")
                z = z if isinstance(z, int) and not isinstance(z, bool) else i
                if name not in best or z < best[name][0]:
                    best[name] = (z, i)
            if not best and len(tif.pages):
                return {"channel 1": 0}
    except Exception:
        _log.exception(f"Cannot read the stack {path}")
        return {}
    return {name: page for name, (_, page) in best.items()}


def _ome_plane_count(path: str) -> int:
    try:
        with tifffile.TiffFile(path) as tif:
            series = tif.series[0]
            axes, shape = series.axes, series.shape
    except Exception:
        _log.exception(f"Cannot read the OME-TIFF {path}")
        return 0
    return int(shape[axes.index("C")]) if "C" in axes else 1


def _preview_sources(folder: str) -> Iterator[PreviewSource]:
    """The folder's preview sources, best first: each timepoint's mosaic overview from the latest back (skipping one
    whose file does not open, as a save that failed part-way leaves it), then the first FOV of the first timepoint as
    channel images, as a multi-page TIFF stack, or as an OME-TIFF."""
    timepoints = _timepoint_dirs(folder)
    for _, path in reversed(timepoints):
        overview = _overview_in(path)
        if overview and _overview_plane_count(overview):
            yield PreviewSource("overview", overview_path=overview, channels=_overview_channel_names(overview))
    if timepoints:
        first = timepoints[0][1]
        images = _first_fov_images(first, _acquisition_channel_names(folder))
        if images:
            yield PreviewSource("images", image_paths=images, channels=list(images))
        stack = _first_fov_file(first, _STACK_NAME)
        if stack:
            pages = _stack_channel_pages(stack)
            if pages:
                yield PreviewSource("stack", stack_path=stack, stack_pages=pages, channels=list(pages))
    ome_dirs = [os.path.join(folder, "ome_tiff")]
    if timepoints:
        ome_dirs += [os.path.join(timepoints[0][1], "ome_tiff"), timepoints[0][1]]
    for directory in ome_dirs:
        ome = _first_fov_file(directory, _OME_NAME)
        if ome:
            count = _ome_plane_count(ome)
            if count:
                names = _ome_channel_names(ome)
                if len(names) != count:
                    names = [f"channel {i + 1}" for i in range(count)]
                yield PreviewSource("ome", ome_path=ome, channels=names)


def find_preview_source(folder: str) -> PreviewSource:
    """The best preview source whose file opens: the latest timepoint's mosaic overview if any timepoint has one, else
    the first FOV of the first timepoint (its channel images, its multi-page TIFF stack, or its OME-TIFF)."""
    return next(_preview_sources(folder), PreviewSource())


def load_preview_planes(folder: str, max_side: int) -> Tuple[PreviewSource, Dict[str, np.ndarray]]:
    """The first preview source with readable planes, and those planes (as load_channel_planes gives them): an
    overview whose file opens but whose pages do not decode gives way to the next source. (PreviewSource(), {}) when
    no source has a readable plane."""
    for source in _preview_sources(folder):
        planes = load_channel_planes(source, max_side)
        if planes:
            return source, planes
        _log.warning(f"No readable plane in the {source.kind} preview source of {folder}; trying the next source")
    return PreviewSource(), {}


def _downsample(plane: np.ndarray, max_side: int) -> np.ndarray:
    h, w = plane.shape[:2]
    scale = max_side / max(h, w, 1)
    if scale >= 1.0:
        return plane
    size = (max(1, int(round(w * scale))), max(1, int(round(h * scale))))
    return cv2.resize(plane, size, interpolation=cv2.INTER_AREA)


def _to_plane(array: np.ndarray, max_side: int) -> Optional[np.ndarray]:
    """A downsampled 2D plane from a read array; colour is converted to grayscale."""
    array = np.squeeze(array)
    if array.ndim == 3 and array.shape[-1] in (3, 4):
        array = cv2.cvtColor(np.ascontiguousarray(array[:, :, :3]), cv2.COLOR_RGB2GRAY)
    if array.ndim != 2:
        return None
    if array.dtype == bool:
        array = array.astype(np.uint8) * 255
    return _downsample(np.ascontiguousarray(array), max_side)


def _read_page_plane(page, max_side: int) -> Optional[np.ndarray]:
    """A TIFF page as a plane no longer than max_side on a side. A page far larger than that which can be memory-mapped
    (uncompressed and contiguous, as the mosaic writer saves overviews) is sampled every `step` pixels straight from
    the file first, so a plate-wide overview of several GB is never loaded whole; any other page is read in full."""
    step = max(1, max(page.shape[:2]) // (2 * max_side))
    if step > 1 and page.is_memmappable:
        mapped = page.asarray(out="memmap")
        try:
            return _to_plane(np.array(mapped[::step, ::step]), max_side)
        finally:
            del mapped
    return _to_plane(page.asarray(), max_side)


def _read_overview_planes(source: PreviewSource, max_side: int) -> Dict[str, np.ndarray]:
    planes: Dict[str, np.ndarray] = {}
    with tifffile.TiffFile(source.overview_path) as tif:
        series = tif.series[0]
        count = series.shape[0] if len(series.shape) == 3 else 1
        for i in range(count):
            name = source.channels[i] if i < len(source.channels) else f"channel {i + 1}"
            try:
                plane = _read_page_plane(series.pages[i], max_side)
            except Exception:
                _log.exception(f"Cannot read plane {i} of {source.overview_path}")
                continue
            if plane is not None:
                planes[name] = plane
    return planes


def _read_stack_planes(source: PreviewSource, max_side: int) -> Dict[str, np.ndarray]:
    planes: Dict[str, np.ndarray] = {}
    with tifffile.TiffFile(source.stack_path) as tif:
        for channel, index in source.stack_pages.items():
            try:
                plane = _to_plane(tif.pages[index].asarray(), max_side)
            except Exception:
                _log.exception(f"Cannot read page {index} of {source.stack_path}")
                continue
            if plane is not None:
                planes[channel] = plane
    return planes


def _read_ome_planes(source: PreviewSource, max_side: int) -> Dict[str, np.ndarray]:
    """t=0, z=0 (index 0 on every axis but C, Y, X and samples) of the first series, one plane per channel."""
    planes: Dict[str, np.ndarray] = {}
    with tifffile.TiffFile(source.ome_path) as tif:
        series = tif.series[0]
        outer = [(axis, size) for axis, size in zip(series.axes, series.shape) if axis not in "YXS"]
        sizes = tuple(size for _, size in outer)
        count = dict(outer).get("C", 1)
        for c in range(count):
            name = source.channels[c] if c < len(source.channels) else f"channel {c + 1}"
            index = tuple(c if axis == "C" else 0 for axis, _ in outer)
            try:
                page = series.pages[int(np.ravel_multi_index(index, sizes)) if sizes else 0]
                plane = _to_plane(page.asarray(), max_side) if page is not None else None
            except Exception:
                _log.exception(f"Cannot read channel {c} of {source.ome_path}")
                continue
            if plane is not None:
                planes[name] = plane
    return planes


def _read_single_image(path: str, max_side: int) -> Optional[np.ndarray]:
    if os.path.splitext(path)[1].lower() in (".tif", ".tiff"):
        array = tifffile.imread(path)
    else:
        array = cv2.imread(path, cv2.IMREAD_UNCHANGED)
        if array is None:
            raise ValueError("unreadable image")
        if array.ndim == 3 and array.shape[2] >= 3:
            array = cv2.cvtColor(np.ascontiguousarray(array[:, :, :3]), cv2.COLOR_BGR2RGB)
    return _to_plane(array, max_side)


def load_channel_planes(source: PreviewSource, max_side: int) -> Dict[str, np.ndarray]:
    """One 2D array per channel, longer side at most max_side, dtype as read. Unreadable files are left out."""
    if source.kind == "overview" and source.overview_path:
        try:
            return _read_overview_planes(source, max_side)
        except Exception:
            _log.exception(f"Cannot read the overview {source.overview_path}")
            return {}
    if source.kind == "stack" and source.stack_path:
        try:
            return _read_stack_planes(source, max_side)
        except Exception:
            _log.exception(f"Cannot read the stack {source.stack_path}")
            return {}
    if source.kind == "ome" and source.ome_path:
        try:
            return _read_ome_planes(source, max_side)
        except Exception:
            _log.exception(f"Cannot read the OME-TIFF {source.ome_path}")
            return {}
    planes: Dict[str, np.ndarray] = {}
    if source.kind == "images":
        for channel, path in source.image_paths.items():
            try:
                plane = _read_single_image(path, max_side)
            except Exception:
                _log.exception(f"Cannot read {path}")
                continue
            if plane is not None:
                planes[channel] = plane
    return planes


def channel_rgb(channel_name: str) -> Tuple[int, int, int]:
    """The map colour of the channel's wavelength; white for anything else (brightfield included)."""
    wavelength = extract_wavelength_from_config_name(channel_name)
    entry = CHANNEL_COLORS_MAP.get(wavelength) if wavelength is not None else None
    if not entry:
        return (255, 255, 255)
    hex_value = int(entry["hex"])
    return ((hex_value >> 16) & 0xFF, (hex_value >> 8) & 0xFF, hex_value & 0xFF)


def stretch_to_unit(plane: np.ndarray) -> np.ndarray:
    """float32 in 0..1 from the 0.5/99.5 percentiles; a flat plane maps by its dtype's range."""
    data = plane.astype(np.float32)
    finite = data[np.isfinite(data)]
    if finite.size == 0:
        return np.zeros(plane.shape, dtype=np.float32)
    lo, hi = np.percentile(finite, (0.5, 99.5))
    if hi <= lo:
        full_scale = float(np.iinfo(plane.dtype).max) if np.issubdtype(plane.dtype, np.integer) else None
        unit = data / full_scale if full_scale else np.zeros_like(data)
    else:
        unit = (data - lo) / (hi - lo)
    return np.clip(np.nan_to_num(unit), 0.0, 1.0).astype(np.float32)


def _to_gray(plane: np.ndarray) -> np.ndarray:
    if plane.ndim == 3:
        return cv2.cvtColor(np.ascontiguousarray(plane[:, :, :3]), cv2.COLOR_RGB2GRAY)
    return plane


def composite(planes: Dict[str, np.ndarray], visible: Optional[set] = None) -> np.ndarray:
    """(H, W, 3) uint8: the stretched planes tinted with their channel colours and added together."""
    if not planes:
        return np.zeros((1, 1, 3), dtype=np.uint8)
    planes = {name: _to_gray(plane) for name, plane in planes.items()}
    shape = next(iter(planes.values())).shape
    total = np.zeros((*shape, 3), dtype=np.float32)
    for name, plane in planes.items():
        if visible is not None and name not in visible:
            continue
        if plane.shape != shape:
            plane = cv2.resize(plane, (shape[1], shape[0]), interpolation=cv2.INTER_AREA)
        total += stretch_to_unit(plane)[..., None] * np.array(channel_rgb(name), dtype=np.float32)
    return np.clip(total, 0, 255).astype(np.uint8)


def write_preview_png(folder: str, out_path: str, max_side: int = PREVIEW_MAX_SIDE) -> Optional[List[str]]:
    """Write the folder's composite preview to out_path; the channel list, or None when there is nothing to show."""
    try:
        source, planes = load_preview_planes(folder, max_side)
        if not planes:
            return None
        rgb = composite(planes)
        out_dir = os.path.dirname(out_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        ok, encoded = cv2.imencode(".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        if not ok:
            _log.error(f"Could not encode the preview {out_path}")
            return None
        # Written beside and moved over the old one, so a reader never sees a half-written preview.
        tmp_path = out_path + ".tmp"
        with open(tmp_path, "wb") as f:
            f.write(encoded.tobytes())
        os.replace(tmp_path, out_path)
        return list(source.channels)
    except Exception:
        _log.exception(f"Could not make a preview of {folder}")
        return None


# ---------------------------------------------------------------------------------------------------------
# Single-image thumbnails
# ---------------------------------------------------------------------------------------------------------


def _read_image_for_thumbnail(source: str) -> Optional[np.ndarray]:
    """The 2D (or H,W,3 color) array to thumbnail, reading as little of the file as makes sense."""
    ext = os.path.splitext(source)[1].lower()
    if ext in (".tif", ".tiff"):
        with tifffile.TiffFile(source) as tif:
            pages = tif.pages
            first = pages[0].asarray()
            if first.ndim == 2 and len(pages) > 1 and first.nbytes * len(pages) <= _MAX_OVERVIEW_BYTES_FOR_MIP:
                # A small multi-channel overview: a max projection over its channels shows every stain.
                mip = first
                for page in pages[1:]:
                    plane = page.asarray()
                    if plane.shape == first.shape:
                        mip = np.maximum(mip, plane)
                return mip
            return first
    image = cv2.imread(source, cv2.IMREAD_UNCHANGED)
    if image is None:
        return None
    if image.ndim == 3 and image.shape[2] >= 3:
        image = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2RGB)
    return image


def _fit_within(image: np.ndarray, max_size: int) -> np.ndarray:
    """Scale so the longer side is max_size: large frames shrink, a small overview grows to fill its tile."""
    h, w = image.shape[:2]
    scale = max_size / max(h, w, 1)
    if scale == 1.0:
        return image
    size = (max(1, int(round(w * scale))), max(1, int(round(h * scale))))
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_NEAREST
    return cv2.resize(image, size, interpolation=interpolation)


def _stretch_to_uint8(image: np.ndarray) -> np.ndarray:
    """Percentile contrast stretch: a dim 16-bit image still shows its structure in a thumbnail."""
    if image.dtype == np.uint8:
        return image
    data = image.astype(np.float32)
    finite = data[np.isfinite(data)]
    if finite.size == 0:
        return np.zeros(image.shape[:2], dtype=np.uint8)
    lo, hi = np.percentile(finite, (0.5, 99.5))
    if hi <= lo:
        # A flat image: scale by the dtype's range, so an empty frame stays black and a lit one shows grey.
        full_scale = float(np.iinfo(image.dtype).max) if np.issubdtype(image.dtype, np.integer) else 1.0
        return np.clip(data * (255.0 / full_scale), 0, 255).astype(np.uint8)
    return np.clip((data - lo) * (255.0 / (hi - lo)), 0, 255).astype(np.uint8)


def load_thumbnail_array(source: str, max_size: int) -> Optional[np.ndarray]:
    """A uint8 grayscale (H,W) or color (H,W,3) thumbnail of an image file, no side longer than max_size.

    None when the file cannot be read or is not an image plane.
    """
    try:
        image = _read_image_for_thumbnail(source)
    except Exception:
        return None
    if image is None:
        return None
    image = np.squeeze(image)
    if image.ndim == 3 and image.shape[-1] not in (3, 4):
        image = image[0]  # a (C,Y,X) stack whose first page was already a stack: first plane
    if image.ndim == 3:
        image = image[:, :, :3]
    if image.ndim != 2 and image.ndim != 3:
        return None
    if image.ndim == 2 and image.dtype == bool:
        image = image.astype(np.uint8) * 255
    image = _fit_within(np.ascontiguousarray(image), max_size)
    return np.ascontiguousarray(_stretch_to_uint8(image))


def array_to_pixmap(array: np.ndarray) -> QPixmap:
    """A QPixmap of a uint8 grayscale (H,W) or RGB (H,W,3) array (the data is copied)."""
    array = np.ascontiguousarray(array, dtype=np.uint8)
    h, w = array.shape[:2]
    if array.ndim == 2:
        image = QImage(array.data, w, h, w, QImage.Format_Grayscale8)
    else:
        image = QImage(array.data, w, h, 3 * w, QImage.Format_RGB888)
    return QPixmap.fromImage(image.copy())
