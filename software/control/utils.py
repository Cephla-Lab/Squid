import collections
import enum
import math
import inspect
import pathlib
import sys
import shutil
import statistics
import time
import threading
from dataclasses import dataclass, field

import cv2
import git
from numpy import square, mean
import numpy as np
from scipy.ndimage import label, gaussian_filter
from scipy import signal
from skimage.registration import phase_cross_correlation
import os
from typing import Any, Dict, Optional, Tuple, List, Callable

from control._def import (
    LASER_AF_CC_THRESHOLD,
    LASER_AF_CC_MIN_AREA,
    LASER_AF_CC_MAX_AREA,
    LASER_AF_CC_ROW_TOLERANCE,
    LASER_AF_CC_MAX_ASPECT_RATIO,
    LASER_AF_DIAG_MAX_REJECTS,
    LASER_AF_DIAG_MAX_COMPONENTS,
    LASER_AF_DIAG_MIN_CONTRAST,
    LASER_AF_DIAG_NOISE_K,
    LASER_AF_DIAG_SEPARATION_MAX_AREA_FACTOR,
    SpotDetectionMode,
    FocusMeasureOperator,
)
import control._def
import squid.logging

_log = squid.logging.get_logger("control.utils")


def to_grayscale(image: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if image.ndim == 3 else image


def measure_translation_px(reference: np.ndarray, live: np.ndarray) -> Tuple[float, float]:
    """Displacement (dx, dy), in pixels of ``live``, of its content relative to ``reference``.

    ``reference`` is resampled to ``live``'s shape, so a reference acquired at a different binning works too.
    """
    reference = to_grayscale(reference)
    live = to_grayscale(live)
    if reference.shape != live.shape:
        height, width = live.shape
        reference = cv2.resize(reference, (width, height), interpolation=cv2.INTER_AREA)
    # skimage returns the (row, col) shift that registers ``live`` onto ``reference``
    shift_rows, shift_cols = phase_cross_correlation(reference, live)[0]
    return (-float(shift_cols), -float(shift_rows))


def image_delta_to_stage_delta_mm(delta_x_px: float, delta_y_px: float, pixel_size_um: float) -> Tuple[float, float]:
    """Stage move that shifts the displayed image content by (-delta_x_px, -delta_y_px).

    Used both to center a clicked point and to undo a measured displacement.
    """
    pixel_sign_y = 1 if control._def.INVERTED_OBJECTIVE else -1
    return (pixel_size_um * delta_x_px / 1000.0, pixel_sign_y * pixel_size_um * delta_y_px / 1000.0)


def crop_image(image, crop_width, crop_height):
    image_height = image.shape[0]
    image_width = image.shape[1]
    if crop_width is None:
        crop_width = image_width
    if crop_height is None:
        crop_height = image_height
    roi_left = int(max(image_width / 2 - crop_width / 2, 0))
    roi_right = int(min(image_width / 2 + crop_width / 2, image_width))
    roi_top = int(max(image_height / 2 - crop_height / 2, 0))
    roi_bottom = int(min(image_height / 2 + crop_height / 2, image_height))
    image_cropped = image[roi_top:roi_bottom, roi_left:roi_right]
    return image_cropped


def calculate_focus_measure(image, method=FocusMeasureOperator.LAPE):
    image = to_grayscale(image)
    if method == FocusMeasureOperator.LAPE:
        # float, not CV_16S: squaring an int16 Laplacian wraps around, which makes sharp 8-bit images score low
        lap = cv2.Laplacian(image, cv2.CV_32F)
        focus_measure = mean(square(lap, dtype=np.float64))
    elif method == FocusMeasureOperator.GLVA:
        focus_measure = np.std(image, axis=None)  # GLVA
    elif method == FocusMeasureOperator.TENENGRAD:
        sobelx = cv2.Sobel(image, cv2.CV_64F, 1, 0, ksize=3)
        sobely = cv2.Sobel(image, cv2.CV_64F, 0, 1, ksize=3)
        focus_measure = np.sum(cv2.magnitude(sobelx, sobely))
    else:
        raise ValueError(f"Invalid focus measure operator: {method}")
    return focus_measure


def unsigned_to_signed(unsigned_array, N):
    signed = 0
    for i in range(N):
        signed = signed + int(unsigned_array[i]) * (256 ** (N - 1 - i))
    signed = signed - (256**N) / 2
    return signed


class FlipVariant(enum.Enum):
    # The mixed case is a historical artifact.
    VERTICAL = "Vertical"
    HORIZONTAL = "Horizontal"
    BOTH = "Both"


def rotate_and_flip_image(image, rotate_image_angle: float, flip_image: Optional[FlipVariant]):
    """Returns a new C-contiguous array, never a view of `image` (callers may pass a view of driver memory).

    Flips are taken as views and materialized by a single copy at the end, so a flip costs one pass over the
    frame instead of a copy plus a flip (about 5 ms instead of 14 ms for a 3200x3200 16-bit frame).
    """
    ret_image = image
    if rotate_image_angle and rotate_image_angle != 0:
        """
        # ROTATE_90_CLOCKWISE
        # ROTATE_90_COUNTERCLOCKWISE
        """
        if rotate_image_angle == 90:
            ret_image = cv2.rotate(ret_image, cv2.ROTATE_90_CLOCKWISE)
        elif rotate_image_angle == -90:
            ret_image = cv2.rotate(ret_image, cv2.ROTATE_90_COUNTERCLOCKWISE)
        elif rotate_image_angle == 180:
            ret_image = cv2.rotate(ret_image, cv2.ROTATE_180)
        else:
            raise ValueError(f"Unhandled rotation: {rotate_image_angle}")

    if flip_image is not None:
        if flip_image == FlipVariant.VERTICAL:
            ret_image = ret_image[::-1]
        elif flip_image == FlipVariant.HORIZONTAL:
            ret_image = ret_image[:, ::-1]
        elif flip_image == FlipVariant.BOTH:
            ret_image = ret_image[::-1, ::-1]

    if np.may_share_memory(ret_image, image) or not ret_image.flags.c_contiguous:
        ret_image = ret_image.copy(order="C")
    return ret_image


def generate_dpc(im_left, im_right):
    # Normalize the images
    im_left = im_left.astype(float) / 255
    im_right = im_right.astype(float) / 255
    # differential phase contrast calculation
    im_dpc = 0.5 + np.divide(im_left - im_right, im_left + im_right)
    # take care of errors
    im_dpc[im_dpc < 0] = 0
    im_dpc[im_dpc > 1] = 1
    im_dpc[np.isnan(im_dpc)] = 0

    im_dpc = (im_dpc * 255).astype(np.uint8)

    return im_dpc


def colorize_mask(mask):
    # Label the detected objects
    labeled_mask, ___ = label(mask)
    # Color them
    colored_mask = np.array((labeled_mask * 83) % 255, dtype=np.uint8)
    colored_mask = cv2.applyColorMap(colored_mask, cv2.COLORMAP_HSV)
    # make sure background is black
    colored_mask[labeled_mask == 0] = 0
    return colored_mask


def colorize_mask_get_counts(mask):
    # Label the detected objects
    labeled_mask, no_cells = label(mask)
    # Color them
    colored_mask = np.array((labeled_mask * 83) % 255, dtype=np.uint8)
    colored_mask = cv2.applyColorMap(colored_mask, cv2.COLORMAP_HSV)
    # make sure background is black
    colored_mask[labeled_mask == 0] = 0
    return colored_mask, no_cells


def overlay_mask_dpc(color_mask, im_dpc):
    # Overlay the colored mask and DPC image
    # make DPC 3-channel
    im_dpc = np.stack([im_dpc] * 3, axis=2)
    return (0.75 * im_dpc + 0.25 * color_mask).astype(np.uint8)


def centerCrop(image, crop_sz):
    center = image.shape
    x = int(center[1] / 2 - crop_sz / 2)
    y = int(center[0] / 2 - crop_sz / 2)
    cropped = image[y : y + crop_sz, x : x + crop_sz]

    return cropped


def interpolate_plane(triple1, triple2, triple3, point):
    """
    Given 3 triples triple1-3 of coordinates (x,y,z)
    and a pair of coordinates (x,y), linearly interpolates
    the z-value at (x,y).
    """
    # Unpack points
    x1, y1, z1 = triple1
    x2, y2, z2 = triple2
    x3, y3, z3 = triple3

    x, y = point
    # Calculate barycentric coordinates
    detT = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
    if detT == 0:
        raise ValueError("Your 3 x-y coordinates are linear")
    alpha = ((y2 - y3) * (x - x3) + (x3 - x2) * (y - y3)) / detT
    beta = ((y3 - y1) * (x - x3) + (x1 - x3) * (y - y3)) / detT
    gamma = 1 - alpha - beta

    # Interpolate z-coordinate
    z = alpha * z1 + beta * z2 + gamma * z3

    return z


def create_done_file(path):
    with open(os.path.join(path, ".done"), "w") as file:
        pass  # This creates an empty file


def ensure_directory_exists(raw_string_path: str):
    path: pathlib.Path = pathlib.Path(raw_string_path)
    _log.debug(f"Making sure directory '{path}' exists.")
    path.mkdir(parents=True, exist_ok=True)


def sanitize_path_component(name: str, fallback: str = "channel") -> str:
    """Return *name* reduced to a filesystem-safe single path component.

    Keeps ``[A-Za-z0-9._-]``, collapses every other run of characters to a
    single ``_``, strips leading/trailing dots and underscores (so the result
    is never hidden or a ``..``), and falls back to *fallback* when nothing is
    left.  Used to turn user-facing channel names into output directory names.
    """
    import re

    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name)).strip("._")
    return cleaned or fallback


def serialize_for_yaml(obj):
    """Recursively convert objects into YAML/JSON-safe Python primitives.

    Handles Enum (→ .value), numpy scalars/arrays, dataclasses, Pydantic
    models, and the usual container types. Sets and frozensets become sorted
    lists. Returns the input unchanged for already-primitive types.
    """
    import dataclasses
    from enum import Enum

    import numpy as np

    if obj is None:
        return None
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, np.ndarray):
        return [serialize_for_yaml(item) for item in obj.tolist()]
    if isinstance(obj, np.generic):
        return obj.item()
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: serialize_for_yaml(v) for k, v in dataclasses.asdict(obj).items()}
    if hasattr(obj, "model_dump"):
        return serialize_for_yaml(obj.model_dump())
    if isinstance(obj, dict):
        return {str(k): serialize_for_yaml(v) for k, v in obj.items()}
    if isinstance(obj, (set, frozenset)):
        return sorted(serialize_for_yaml(item) for item in obj)
    if isinstance(obj, (list, tuple)):
        return [serialize_for_yaml(item) for item in obj]
    return obj


def _resolve_spot_detection_params(params: Optional[dict]) -> dict:
    """Merge caller-supplied spot detection params over the _def defaults."""
    resolved = {
        "threshold": LASER_AF_CC_THRESHOLD,
        "min_area": LASER_AF_CC_MIN_AREA,
        "max_area": LASER_AF_CC_MAX_AREA,
        "row_tolerance": LASER_AF_CC_ROW_TOLERANCE,
        "max_aspect_ratio": LASER_AF_CC_MAX_ASPECT_RATIO,
    }
    if params is not None:
        # A misspelled key would otherwise be ignored and its default used instead.
        unknown_keys = set(params) - set(resolved)
        if unknown_keys:
            raise ValueError(f"Unknown spot detection parameters: {sorted(unknown_keys)}")
        resolved.update(params)
    return resolved


def _build_working_image(image: np.ndarray, filter_sigma: Optional[int]) -> np.ndarray:
    """The frame every measurement is actually taken on: optionally Gaussian-filtered, uint8.

    Split out from _prepare_working_image so a diagnosis can look at a frame the detector would
    have refused. It is also the expensive step -- a float64 copy plus a Gaussian, tens of
    milliseconds on a full-sensor crop -- so a caller that needs both a detection and a diagnosis
    of the same frame must build this once and pass it to both.
    """
    working_image = image.copy()
    if filter_sigma is not None and filter_sigma > 0:
        filtered = gaussian_filter(working_image.astype(float), sigma=filter_sigma)
        working_image = np.clip(filtered, 0, 255).astype(np.uint8)
    return working_image


def _prepare_working_image(image: np.ndarray, filter_sigma: Optional[int], threshold: float) -> np.ndarray:
    """Optionally Gaussian-filter the frame, then reject it if nothing is above threshold.

    Raises:
        ValueError: if the brightest pixel is at or below the threshold.
    """
    working_image = _build_working_image(image, filter_sigma)

    # Quick check - if max intensity below threshold, no spot visible
    if working_image.max() <= threshold:
        raise ValueError("No spot detected: max intensity below threshold")

    return working_image


@dataclass
class _FrameComponents:
    """Per-component measurements for every connected component in one frame, in label order.

    Index j here is label j+1; label 0 is the background. Held as parallel numpy arrays rather
    than a list of dicts because the whole point is to test the filters without a Python loop --
    an over-exposed full-sensor frame yields tens of thousands of components, and the per-
    component `labels == i` comparison is a full-frame operation. Only the handful actually
    reported are ever materialized.
    """

    areas: np.ndarray  # int64, pixels
    row_deviations: np.ndarray  # float, |centroid row - expected row|
    aspect_ratios: np.ndarray  # float, always >= 1
    cols: np.ndarray  # float, centroid column
    rows: np.ndarray  # float, centroid row
    bboxes: np.ndarray  # int, (N, 4) of left, top, width, height -- lets a reject be inspected
    failure_counts: np.ndarray  # int64, how many cc_* filters each component fails
    reject_indices: np.ndarray  # int, the few worth describing, closest-to-passing first


def _collect_valid_spots(
    working_image: np.ndarray, p: dict, collect_rejects: bool = False
) -> Tuple[List[dict], np.ndarray, np.ndarray, int, float, Optional[_FrameComponents]]:
    """Binarize, label, and filter connected components down to plausible spot candidates.

    Returns (valid_spots sorted left-to-right by column, binary, labels, num_labels, expected_row,
    components). Each candidate carries its boolean `mask`, which callers that retain candidates
    should drop -- it is a full-frame array per candidate.

    `components` is None unless collect_rejects is set. It carries what every component measured
    and how many filters turned it away, which is what lets a failed frame name the setting to
    change rather than only reporting that it failed. Recording rejections here rather than in a
    separate pass is deliberate: one function then decides what a rejection is, so the live
    diagnosis cannot drift out of agreement with the detector it is explaining.

    The filters are evaluated as whole-array numpy predicates, and `labels == i` is run only for
    survivors. That is load-bearing, not style: it is a full-frame boolean comparison, and running
    it across the tens of thousands of components a noisy full-sensor frame produces would take
    minutes on the GUI thread.
    """
    # Binarize the image
    binary = (working_image > p["threshold"]).astype(np.uint8)

    # Find connected components
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(binary, connectivity=8)

    # Expected row position (center of image)
    expected_row = working_image.shape[0] / 2.0

    areas = stats[1:, cv2.CC_STAT_AREA].astype(np.int64)  # skip background (label 0)
    widths = stats[1:, cv2.CC_STAT_WIDTH].astype(np.float64)
    heights = stats[1:, cv2.CC_STAT_HEIGHT].astype(np.float64)
    cols = centroids[1:, 0].astype(np.float64)
    rows = centroids[1:, 1].astype(np.float64)

    # Aspect ratio (max of w/h or h/w, so always >= 1). A zero extent cannot occur for a real
    # label; treating it as infinite keeps a degenerate one out instead of raising.
    with np.errstate(divide="ignore", invalid="ignore"):
        aspect_ratios = np.maximum(widths / heights, heights / widths)
    aspect_ratios[~np.isfinite(aspect_ratios)] = np.inf

    row_deviations = np.abs(rows - expected_row)

    # Size, row position and aspect ratio filters, counted rather than short-circuited. The count
    # is what tells "one setting away" from "nothing like a spot", and a caller that relaxes a
    # setting needs every failure, not just the first one.
    failure_counts = (
        (areas < p["min_area"]).astype(np.int64)
        + (areas > p["max_area"])
        + (row_deviations > p["row_tolerance"])
        + (aspect_ratios > p["max_aspect_ratio"])
    )

    valid_spots = []
    for j in np.flatnonzero(failure_counts == 0):
        i = int(j) + 1
        # Calculate mean intensity of this component for sorting
        component_mask = labels == i
        intensity = working_image[component_mask].mean()

        valid_spots.append(
            {
                "label": i,
                "col": float(cols[j]),
                "row": float(rows[j]),
                "area": int(areas[j]),
                "intensity": intensity,
                "aspect_ratio": float(aspect_ratios[j]),
                "mask": component_mask,
            }
        )

    # Sort spots by x-coordinate (column) for mode-based selection
    valid_spots.sort(key=lambda s: s["col"])

    components = None
    if collect_rejects:
        rejected = np.flatnonzero(failure_counts > 0)
        # Fewest failures first, then largest: closest to passing is the one whose settings are
        # worth naming, and among equals the biggest blob is the one the operator can see.
        order = np.lexsort((-areas[rejected], failure_counts[rejected]))
        components = _FrameComponents(
            areas=areas,
            row_deviations=row_deviations,
            aspect_ratios=aspect_ratios,
            cols=cols,
            rows=rows,
            bboxes=stats[1:, [cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP, cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT]],
            failure_counts=failure_counts,
            reject_indices=rejected[order][:LASER_AF_DIAG_MAX_REJECTS],
        )

    return valid_spots, binary, labels, num_labels, expected_row, components


def select_spot_by_mode(sorted_spots: List[dict], mode: SpotDetectionMode) -> dict:
    """Pick which of several candidates a SpotDetectionMode selects.

    `sorted_spots` must already be ordered left-to-right. Selection is purely positional -- it
    never rejects a candidate on merit, so a mode cannot protect against a spurious reflection.

    Raises:
        ValueError: for SINGLE with more than one candidate, or an unknown mode.
    """
    if mode == SpotDetectionMode.SINGLE:
        if len(sorted_spots) > 1:
            raise ValueError(f"Found {len(sorted_spots)} spots but expected single spot")
        return sorted_spots[0]
    elif mode == SpotDetectionMode.DUAL_LEFT:
        return sorted_spots[0]  # Leftmost
    elif mode == SpotDetectionMode.DUAL_RIGHT:
        return sorted_spots[-1]  # Rightmost
    else:
        raise ValueError(f"Unknown spot detection mode: {mode}")


def _weighted_centroid(working_image: np.ndarray, component_mask: np.ndarray, spot: dict) -> Tuple[float, float]:
    """Intensity-weighted centroid of one component, for sub-pixel accuracy.

    Falls back to the component's geometric centroid when every pixel has the same intensity.
    """
    y_coords, x_coords = np.where(component_mask)
    intensities = working_image[component_mask].astype(float)

    # Subtract background (minimum intensity in component)
    intensities = intensities - intensities.min()

    sum_intensity = intensities.sum()
    if sum_intensity == 0:
        # Fall back to geometric centroid if all intensities are equal
        return spot["col"], spot["row"]
    return (x_coords * intensities).sum() / sum_intensity, (y_coords * intensities).sum() / sum_intensity


def find_all_spot_locations(
    image: np.ndarray,
    params: Optional[dict] = None,
    filter_sigma: Optional[int] = None,
    max_candidates: int = 32,
) -> List[dict]:
    """Every candidate that passes the cc_* filters, left to right -- not just the selected one.

    find_spot_location answers "where is the spot", which presupposes the answer. This answers
    "what is in frame", which is what you need to tell a real reflection from a static one: swept
    across z, the real spot traces a sloped line and a back-reflection traces a flat one.

    Unlike find_spot_location this never raises for an empty or spotless frame -- a z position
    where nothing is visible is ordinary data for a sweep, not an error. Returned dicts carry
    x, y (sub-pixel weighted centroid, in the frame's own coordinates), col, row, area, intensity,
    peak_intensity and aspect_ratio; the internal boolean mask is dropped, since retaining one
    full-frame array per candidate across a long sweep would be a real memory cost.

    Over-exposed frames can yield very many components, so the list is capped at max_candidates,
    keeping the brightest.
    """
    if image is None or not isinstance(image, np.ndarray) or image.size == 0:
        raise ValueError("Invalid input image")

    p = _resolve_spot_detection_params(params)
    return _candidates_from_working(_build_working_image(image, filter_sigma), p, max_candidates)


def _candidates_from_working(working_image: np.ndarray, p: dict, max_candidates: int = 32) -> List[dict]:
    """find_all_spot_locations once the working image already exists."""
    if working_image.max() <= p["threshold"]:
        return []
    valid_spots, _, _, _, _, _ = _collect_valid_spots(working_image, p)
    return _candidates_from_valid_spots(working_image, valid_spots, max_candidates)


def _candidates_from_valid_spots(working_image: np.ndarray, valid_spots: List[dict], max_candidates: int) -> List[dict]:
    """Turn surviving components into candidate dicts, dropping their full-frame masks.

    Split out so a caller that already ran _collect_valid_spots -- to get rejects out of the same
    pass -- does not run it a second time. Binarizing and labelling a full sensor is a couple of
    hundred milliseconds, and doing it twice per frame on the GUI thread is what makes Reset to
    Full Sensor feel broken.
    """
    if len(valid_spots) > max_candidates:
        valid_spots = sorted(valid_spots, key=lambda s: s["intensity"], reverse=True)[:max_candidates]
        valid_spots.sort(key=lambda s: s["col"])

    candidates = []
    for spot in valid_spots:
        centroid_x, centroid_y = _weighted_centroid(working_image, spot["mask"], spot)
        candidates.append(
            {
                "x": float(centroid_x),
                "y": float(centroid_y),
                "col": float(spot["col"]),
                "row": float(spot["row"]),
                "area": int(spot["area"]),
                "intensity": float(spot["intensity"]),
                # Peak as well as mean, because the threshold filter is a test on the peak: a
                # large dim blob and a small bright one have the same mean and behave completely
                # differently when cc_threshold moves.
                "peak_intensity": float(working_image[spot["mask"]].max()),
                "aspect_ratio": float(spot["aspect_ratio"]),
            }
        )
    return candidates


# The cc_* field a criterion names, and the parameter key the detector reads it under.
_CC_FIELD_TO_PARAM = {
    "cc_threshold": "threshold",
    "cc_min_area": "min_area",
    "cc_max_area": "max_area",
    "cc_row_tolerance": "row_tolerance",
    "cc_max_aspect_ratio": "max_aspect_ratio",
}

# Mirror of the spinbox ranges in LaserAutofocusSettingWidget.init_ui (the cc_* block in
# widgets.py). (minimum, maximum, decimals). A suggestion outside these cannot be typed in, so a
# suggestion that ignores them is worse than no suggestion at all: the operator clicks Relax,
# presses Apply, and nothing changes. Kept here rather than imported because utils must not depend
# on the GUI; if the spinbox ranges ever move, these move with them.
#
# cc_row_tolerance is the exception: its useful range depends on the crop, so the value here is
# only the floor. See _row_tolerance_ceiling.
_CC_SPINBOX_LIMITS = {
    "cc_threshold": (0.0, 255.0, 0),
    "cc_min_area": (1.0, 1000.0, 0),
    "cc_max_area": (100.0, 50000.0, 0),
    "cc_row_tolerance": (1.0, 200.0, 0),
    "cc_max_aspect_ratio": (1.0, 10.0, 1),
}


@dataclass
class SpotCriterion:
    """One filter verdict on one blob, with the value that would change the verdict.

    `suggested` is None when no value the spinbox can hold would admit the blob. That is not the
    same as "no advice": it means the answer is somewhere else entirely -- usually the crop -- and
    `alternative` says where. Offering a clamped value instead would be the worst outcome, since it
    looks like a fix and changes nothing.
    """

    name: str  # the config field, e.g. "cc_min_area", so advice can name the spinbox
    label: str  # what to call it in a table, e.g. "area"
    measured: float
    limit: float
    passed: bool
    suggested: Optional[float] = None
    alternative: Optional[str] = None


@dataclass
class SpotReject:
    """A blob the detector threw away, and every reason it did.

    All five criteria are always present, never short-circuited at the first failure. A record
    built by short-circuiting would break the one thing this exists for: relaxing a single setting
    and finding the blob still rejected by the next one.
    """

    x: float
    y: float
    area: int
    peak_intensity: float
    aspect_ratio: float
    row_deviation: float
    criteria: List[SpotCriterion] = field(default_factory=list)
    # Set when the blob had to be found below cc_threshold to be described at all, in which case
    # area and shape are measured at that lower threshold and overstate what the detector sees.
    measured_at_relaxed_threshold: Optional[float] = None

    @property
    def failures(self) -> List[SpotCriterion]:
        return [c for c in self.criteria if not c.passed]


@dataclass
class SpotDiagnosis:
    """Why a frame produced no usable spot, in terms of the settings that decide it."""

    frame_peak_intensity: float  # of the working image, i.e. AFTER the Gaussian filter
    frame_median: float
    rejects: List[SpotReject] = field(default_factory=list)  # closest to passing first
    also_admits: int = 0  # other blobs the best reject suggestion would also let in
    note: Optional[str] = None  # set when no per-blob answer applies; see diagnose_frame

    @property
    def best(self) -> Optional[SpotReject]:
        return self.rejects[0] if self.rejects else None

    def suggested_params(self) -> Dict[str, float]:
        """Every cc_* value that would admit the best reject, ready for the spinboxes.

        Empty when there is nothing to suggest, which is what disables the Relax button. All
        failing criteria at once, because fixing them one at a time does not work.
        """
        best = self.best
        if best is None:
            return {}
        return {c.name: c.suggested for c in best.failures if c.suggested is not None}


def _row_tolerance_ceiling(frame_height: Optional[float] = None) -> float:
    """The largest row tolerance worth offering for a frame this tall.

    Row deviation is measured from the middle of the crop, so it cannot exceed half the crop
    height: a tolerance set there accepts any blob the frame can contain. A fixed ceiling therefore
    means something different on every crop -- on the stock 256-tall crop it is already unreachable,
    while on a full sensor it cuts off at a fifth of what a blob can actually measure, so a spot
    plainly visible near the top of the frame could not be admitted at all.

    Never returns less than the static floor, so widening the crop can raise this but narrowing it
    can never silently clamp a value an objective already has saved.
    """
    static_max = _CC_SPINBOX_LIMITS["cc_row_tolerance"][1]
    if frame_height is None or not math.isfinite(frame_height) or frame_height <= 0:
        return static_max
    return max(static_max, float(math.ceil(frame_height / 2.0)))


def _round_to_admit(name: str, value: float, direction: int, high_override: Optional[float] = None) -> Optional[float]:
    """Round `value` onto the spinbox grid in the direction that admits the blob.

    direction +1 rounds up (for limits that must reach at least `value`), -1 rounds down. Returns
    None if the result falls outside the spinbox range, because a clamped value that still rejects
    the blob is worse than admitting there is no answer here.

    Rounding to nearest is the trap this exists to avoid: aspect ratio has one decimal, so a blob
    at 2.63 "suggested" as 2.6 is still rejected, and the operator clicks Relax, presses Apply, and
    sees no change at all.

    A value already on the grid is kept exactly, not pushed a further step. That matters at the
    ends of the ranges: a 1-pixel blob wants cc_min_area = 1, and a step past it lands outside the
    spinbox and turns a perfectly good suggestion into "no answer". The epsilon absorbs binary
    representation error only -- 2.5 held as 2.4999999996 must still round to 2.5, not to 2.6.

    Falling off one end of the range is fine and falling off the other is fatal, and which is which
    depends on the direction. A ceiling that must reach at least `value` is only ever helped by
    being larger, so a result under the spinbox minimum is raised to it -- cc_max_area cannot be
    set below 100, and 100 admits a 45-pixel blob perfectly well. A result past the maximum is the
    fatal one: no reachable value admits the blob, and saying so is the whole point of returning
    None rather than a number that will not work.
    """
    low, high, decimals = _CC_SPINBOX_LIMITS[name]
    if high_override is not None:
        high = high_override
    scale = 10.0**decimals
    scaled = value * scale
    stepped = math.ceil(scaled - 1e-9) if direction > 0 else math.floor(scaled + 1e-9)
    result = stepped / scale

    if direction > 0:
        if result > high:
            return None
        return max(result, low)
    if result < low:
        return None
    return min(result, high)


def evaluate_spot_criteria(
    *,
    peak_intensity: float,
    area: int,
    row_deviation: float,
    aspect_ratio: float,
    params: Optional[dict] = None,
    frame_median: float = 0.0,
    frame_height: Optional[float] = None,
    max_area_alternative: Optional[str] = None,
) -> List[SpotCriterion]:
    """The five filters applied to one blob measurements, each with its margin and its fix.

    Used for both halves of the live feedback: on a blob that passed it reports headroom, so a
    dropout is visible coming; on one that was rejected it reports which setting to change. One
    function, so the two readings cannot disagree.
    """
    p = _resolve_spot_detection_params(params)
    criteria: List[SpotCriterion] = []

    # -- cc_threshold. The test is strict (working > threshold), so a suggestion equal to the peak
    #    still rejects. Aim between the blob and the frame background rather than just under the
    #    peak, so ordinary frame-to-frame flicker does not immediately undo the fix.
    threshold_passed = peak_intensity > p["threshold"]
    suggested = alternative = None
    if not threshold_passed:
        ceiling = math.floor(peak_intensity) - 1.0
        if ceiling < _CC_SPINBOX_LIMITS["cc_threshold"][0]:
            alternative = (
                "This blob is barely above the noise floor. Raise the focus camera exposure or "
                "analog gain rather than lowering the threshold onto the noise."
            )
        else:
            target = min((peak_intensity + frame_median) / 2.0, ceiling)
            suggested = _round_to_admit("cc_threshold", target, -1)
            if suggested is None or suggested > ceiling:
                suggested = _round_to_admit("cc_threshold", ceiling, -1)
    criteria.append(
        SpotCriterion(
            name="cc_threshold",
            label="peak intensity",
            measured=float(peak_intensity),
            limit=float(p["threshold"]),
            passed=threshold_passed,
            suggested=suggested,
            alternative=alternative,
        )
    )

    # -- cc_min_area
    min_passed = area >= p["min_area"]
    suggested = alternative = None
    if not min_passed:
        suggested = _round_to_admit("cc_min_area", float(area), -1)
        if suggested is None:
            alternative = "This blob is smaller than the smallest area the detector can be set to accept."
    criteria.append(
        SpotCriterion(
            name="cc_min_area",
            label="area",
            measured=float(area),
            limit=float(p["min_area"]),
            passed=min_passed,
            suggested=suggested,
            alternative=alternative,
        )
    )

    # -- cc_max_area. Raising the ceiling is usually the wrong fix: a blob this large is normally
    #    the spot merged with a halo, and the right move is to raise the threshold until it
    #    separates. max_area_alternative carries that finding when the caller computed it.
    max_passed = area <= p["max_area"]
    suggested = alternative = None
    if not max_passed:
        suggested = _round_to_admit("cc_max_area", float(area), 1)
        alternative = max_area_alternative
        if suggested is None and alternative is None:
            alternative = (
                "This blob is larger than the largest area the detector can be set to accept -- it "
                "is a bright field, not a spot. Raise CC Threshold or cut exposure and gain."
            )
    criteria.append(
        SpotCriterion(
            name="cc_max_area",
            label="area",
            measured=float(area),
            limit=float(p["max_area"]),
            passed=max_passed,
            suggested=suggested,
            alternative=alternative,
        )
    )

    # -- cc_row_tolerance. Measured from the crop own centre row, so a large deviation is a
    #    statement about where the crop sits, not about the tolerance.
    row_passed = row_deviation <= p["row_tolerance"]
    suggested = alternative = None
    if not row_passed:
        suggested = _round_to_admit(
            "cc_row_tolerance", float(row_deviation), 1, high_override=_row_tolerance_ceiling(frame_height)
        )
        if suggested is None:
            alternative = (
                "This blob sits further from the centre row of the crop than the tolerance can "
                "reach. Move the crop instead: shift Crop Y Offset so the spot is centred, or use "
                "Center on Last Detection."
            )
    criteria.append(
        SpotCriterion(
            name="cc_row_tolerance",
            label="row offset",
            measured=float(row_deviation),
            limit=float(p["row_tolerance"]),
            passed=row_passed,
            suggested=suggested,
            alternative=alternative,
        )
    )

    # -- cc_max_aspect_ratio
    ar_passed = aspect_ratio <= p["max_aspect_ratio"]
    suggested = alternative = None
    if not ar_passed:
        suggested = _round_to_admit("cc_max_aspect_ratio", float(aspect_ratio), 1)
        if suggested is None:
            alternative = (
                "This is a streak, not a spot -- it is longer than the detector can be set to "
                "accept. Check for a specular reflection off the coverslip edge."
            )
    criteria.append(
        SpotCriterion(
            name="cc_max_aspect_ratio",
            label="aspect ratio",
            measured=float(aspect_ratio),
            limit=float(p["max_aspect_ratio"]),
            passed=ar_passed,
            suggested=suggested,
            alternative=alternative,
        )
    )

    return criteria


# Stride used when estimating the frame background. A median over a full sensor sorts 6.4 million
# values -- a couple of hundred milliseconds, on the GUI thread, for a number that is only ever a
# background level. Every 4th pixel in each axis is 16x cheaper and agrees to well inside one gray
# level on any frame that is mostly background, which is every frame this is asked about.
_BACKGROUND_SUBSAMPLE_STRIDE = 4


def _frame_background(working_image: np.ndarray) -> Tuple[float, float]:
    """(median, median absolute deviation) of the frame, from a strided subsample."""
    stride = _BACKGROUND_SUBSAMPLE_STRIDE
    sample = working_image[::stride, ::stride].astype(np.float32)
    median = float(np.median(sample))
    return median, float(np.median(np.abs(sample - median)))


# A frame with no contrast cannot be binarized into anything meaningful, and "there is no
# signal" is the true answer anyway. Shared so analyze_frame and diagnose_frame say it the same.
_UNIFORM_FRAME_NOTE = (
    "Frame is uniform (peak {:.0f}, median {:.0f}) -- there is no signal in the crop at all. "
    "Check that the AF laser is on, then exposure and analog gain."
)


def _threshold_that_separates(sub: np.ndarray, mask: np.ndarray, p: dict) -> Optional[str]:
    """For an over-large blob, find a threshold at which it breaks into something spot-sized.

    A blob failing cc_max_area is normally the spot merged with a halo or a bright background, and
    raising the ceiling to admit it is the wrong fix -- it admits the halo. Raising cc_threshold
    until the merge separates is the right one, and this is the one place a measurement at a
    different threshold is well defined, because it is confined to a single blob and asks what that
    blob becomes rather than what the frame contains.

    Bounded work: the slices are the blob bounding box, not the frame, and at most six candidate
    thresholds are tried.
    """
    values = sub[mask]
    if values.size == 0:
        return None

    for percentile in (50, 60, 70, 80, 90, 95):
        candidate = float(np.percentile(values, percentile))
        if candidate <= p["threshold"]:
            continue
        sub_binary = ((sub > candidate) & mask).astype(np.uint8)
        count, _, sub_stats, _ = cv2.connectedComponentsWithStats(sub_binary, connectivity=8)
        if count <= 1:
            continue
        largest = int(sub_stats[1:, cv2.CC_STAT_AREA].max())
        if p["min_area"] <= largest <= p["max_area"]:
            return (
                f"This blob is the spot merged with its surroundings. At CC Threshold "
                f"{math.ceil(candidate):.0f} it separates into {largest} px and passes -- raise the "
                f"threshold rather than the area ceiling, which would only admit the merge."
            )
    return None


def _diagnose_above_threshold(
    working_image: np.ndarray,
    p: dict,
    peak: float,
    median: float,
    x_reference: Optional[float],
    labels: np.ndarray,
    num_labels: int,
    components: _FrameComponents,
) -> "SpotDiagnosis":
    """The common case: components exist, so every measurement the table wants is already exact.

    No second threshold and no re-measurement are involved. Because a component exists only by
    having pixels above cc_threshold, every blob here passes the threshold criterion by
    construction, and the failures are all about size, position and shape.

    Takes the labelling rather than producing it, so the caller that just detected on this frame
    does not pay for a second binarize-and-label of the same pixels.
    """
    if num_labels - 1 > LASER_AF_DIAG_MAX_COMPONENTS:
        return SpotDiagnosis(
            frame_peak_intensity=peak,
            frame_median=median,
            note=(
                f"{num_labels - 1} components above CC Threshold {p['threshold']:.0f} -- this frame "
                f"is noise, not spots. Raise CC Threshold, or cut the focus camera exposure and "
                f"analog gain."
            ),
        )

    rejects: List[SpotReject] = []
    for index in components.reject_indices:
        j = int(index)
        label_id = j + 1
        left, top, width, height = (int(v) for v in components.bboxes[j])
        sub = working_image[top : top + height, left : left + width]
        mask = labels[top : top + height, left : left + width] == label_id
        blob_peak = float(sub[mask].max()) if mask.any() else float(p["threshold"])

        area = int(components.areas[j])
        # Only worth asking of a blob that plausibly IS the spot fused with something. Past that
        # the blob is the background, and walking it is a quarter-second of GUI thread for advice
        # that would be wrong anyway.
        separable = p["max_area"] < area <= LASER_AF_DIAG_SEPARATION_MAX_AREA_FACTOR * p["max_area"]
        alternative = _threshold_that_separates(sub, mask, p) if separable else None

        rejects.append(
            SpotReject(
                x=float(components.cols[j]),
                y=float(components.rows[j]),
                area=area,
                peak_intensity=blob_peak,
                aspect_ratio=float(components.aspect_ratios[j]),
                row_deviation=float(components.row_deviations[j]),
                criteria=evaluate_spot_criteria(
                    peak_intensity=blob_peak,
                    area=area,
                    row_deviation=float(components.row_deviations[j]),
                    aspect_ratio=float(components.aspect_ratios[j]),
                    params=p,
                    frame_median=median,
                    frame_height=working_image.shape[0],
                    max_area_alternative=alternative,
                ),
            )
        )

    # With a reference to hand, nearness to it is the strongest available prior on which of several
    # blobs is the sample reflection. Applied after the cap rather than before it, so the shortlist
    # is still the one closest to passing -- a blob near the reference that fails four filters is
    # not the one whose settings are worth naming.
    if x_reference is not None:
        rejects.sort(key=lambda r: (len(r.failures), abs(r.x - x_reference)))

    diagnosis = SpotDiagnosis(frame_peak_intensity=peak, frame_median=median, rejects=rejects)
    diagnosis.also_admits = _count_also_admitted(components, p, diagnosis.suggested_params())
    return diagnosis


def _count_also_admitted(components: _FrameComponents, p: dict, suggested: Dict[str, float]) -> int:
    """How many other blobs in this frame the suggested settings would also let through.

    Relaxing a filter until the blob you want appears is how a back-reflection gets locked onto,
    and the number that makes that visible is free: the same vectorized predicates, re-evaluated.
    A count of zero means the suggestion is surgical; a count in the dozens means the spot
    detection mode will simply fail differently.
    """
    if not suggested:
        return 0

    relaxed = dict(p)
    for field_name, value in suggested.items():
        relaxed[_CC_FIELD_TO_PARAM[field_name]] = value

    would_pass = (
        (components.areas >= relaxed["min_area"])
        & (components.areas <= relaxed["max_area"])
        & (components.row_deviations <= relaxed["row_tolerance"])
        & (components.aspect_ratios <= relaxed["max_aspect_ratio"])
    )
    already_valid = int((components.failure_counts == 0).sum())
    # Minus those already accepted, minus the blob the suggestion was built for.
    return max(int(would_pass.sum()) - already_valid - 1, 0)


def _diagnose_below_threshold(working_image: np.ndarray, p: dict, peak: float, median: float) -> "SpotDiagnosis":
    """Nothing cleared cc_threshold, so the threshold is the cause -- but where is the spot?

    This is the only case that needs to look below the configured threshold, and it needs one
    number rather than a second detector. The floor is taken from the frame noise statistics, never
    from the peak: a single saturated hot pixel would put a peak-derived floor above the real spot
    and hide exactly what we came to find.
    """
    ceiling = float(p["threshold"]) - 1.0
    if ceiling < 1.0:
        return SpotDiagnosis(
            frame_peak_intensity=peak,
            frame_median=median,
            note="CC Threshold is already at the floor and nothing cleared it. Raise exposure or analog gain.",
        )

    _, mad = _frame_background(working_image)
    relaxed_threshold = min(max(median + LASER_AF_DIAG_NOISE_K * mad, 1.0), ceiling)

    binary = (working_image > relaxed_threshold).astype(np.uint8)
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(binary, connectivity=8)

    if num_labels <= 1:
        return SpotDiagnosis(
            frame_peak_intensity=peak,
            frame_median=median,
            note=(
                "Nothing spot-like in the crop, even below CC Threshold. The spot is not in this "
                "crop at all -- no detection setting will bring it back. Use Reset to Full Sensor "
                "to find where it went, or check z."
            ),
        )
    if num_labels - 1 > LASER_AF_DIAG_MAX_COMPONENTS:
        return SpotDiagnosis(
            frame_peak_intensity=peak,
            frame_median=median,
            note=(
                f"Nothing cleared CC Threshold {p['threshold']:.0f}, and below it the frame breaks "
                f"into {num_labels - 1} components of noise. The spot is not distinguishable here: "
                f"raise exposure or analog gain rather than lowering the threshold."
            ),
        )

    areas = stats[1:, cv2.CC_STAT_AREA]
    j = int(np.argmax(areas))
    label_id = j + 1
    left, top, width, height = (
        int(stats[label_id, cv2.CC_STAT_LEFT]),
        int(stats[label_id, cv2.CC_STAT_TOP]),
        int(stats[label_id, cv2.CC_STAT_WIDTH]),
        int(stats[label_id, cv2.CC_STAT_HEIGHT]),
    )
    sub = working_image[top : top + height, left : left + width]
    mask = labels[top : top + height, left : left + width] == label_id
    blob_peak = float(sub[mask].max())

    aspect_ratio = max(width / height, height / width) if width > 0 and height > 0 else float("inf")
    row_deviation = abs(float(centroids[label_id, 1]) - working_image.shape[0] / 2.0)
    area = int(areas[j])

    reject = SpotReject(
        x=float(centroids[label_id, 0]),
        y=float(centroids[label_id, 1]),
        area=area,
        peak_intensity=blob_peak,
        aspect_ratio=float(aspect_ratio),
        row_deviation=row_deviation,
        criteria=evaluate_spot_criteria(
            peak_intensity=blob_peak,
            area=area,
            row_deviation=row_deviation,
            aspect_ratio=float(aspect_ratio),
            params=p,
            frame_median=median,
            frame_height=working_image.shape[0],
        ),
        measured_at_relaxed_threshold=relaxed_threshold,
    )
    return SpotDiagnosis(frame_peak_intensity=peak, frame_median=median, rejects=[reject])


def diagnose_frame(
    working_image: np.ndarray, params: Optional[dict] = None, x_reference: Optional[float] = None
) -> SpotDiagnosis:
    """Explain, in terms of the settings that decide it, why a frame yields no usable spot.

    Takes the working image rather than the raw frame, so a caller that has already built one for
    detection does not pay for a second Gaussian filter -- which is the dominant cost of the whole
    path on a full-sensor crop.

    Reads nothing back, moves nothing, and changes no configuration. It splits into two cases, and
    only the second needs to look below the configured threshold at all:

    - The frame peak clears cc_threshold, so components exist and every number is already exact.
    - It does not, so the threshold is the sole cause and the blob must be located below it.

    A frame with no contrast at all short-circuits both: binarizing one labels the whole sensor,
    and "there is no signal" is the true answer anyway.
    """
    p = _resolve_spot_detection_params(params)
    peak = float(working_image.max())
    median, _ = _frame_background(working_image)

    if peak - median < LASER_AF_DIAG_MIN_CONTRAST:
        return SpotDiagnosis(
            frame_peak_intensity=peak, frame_median=median, note=_UNIFORM_FRAME_NOTE.format(peak, median)
        )

    if peak > p["threshold"]:
        _, _, labels, num_labels, _, components = _collect_valid_spots(working_image, p, collect_rejects=True)
        return _diagnose_above_threshold(working_image, p, peak, median, x_reference, labels, num_labels, components)
    return _diagnose_below_threshold(working_image, p, peak, median)


def diagnose_spot_detection(
    image: np.ndarray,
    params: Optional[dict] = None,
    filter_sigma: Optional[int] = None,
    x_reference: Optional[float] = None,
) -> SpotDiagnosis:
    """diagnose_frame for a caller holding a raw frame rather than a working image."""
    if image is None or not isinstance(image, np.ndarray) or image.size == 0:
        raise ValueError("Invalid input image")
    return diagnose_frame(_build_working_image(image, filter_sigma), params=params, x_reference=x_reference)


def analyze_frame(
    image: np.ndarray,
    params: Optional[dict] = None,
    filter_sigma: Optional[int] = None,
    max_candidates: int = 32,
    diagnose: bool = False,
    x_reference: Optional[float] = None,
) -> Tuple[List[dict], Optional[SpotDiagnosis]]:
    """Every candidate in frame and, on request, why there were none -- from one working image.

    The one entry point a live overlay should use. Detection and diagnosis both need the
    Gaussian-filtered frame, and building it twice doubles the cost of the most expensive case
    (a full sensor, which is exactly when someone is diagnosing).

    `diagnose` is the cost control and belongs to the caller: the diagnosis walks the frame a
    second time, so it is worth running when detection just failed and wasteful otherwise.
    """
    if image is None or not isinstance(image, np.ndarray) or image.size == 0:
        raise ValueError("Invalid input image")

    p = _resolve_spot_detection_params(params)
    working_image = _build_working_image(image, filter_sigma)
    peak = float(working_image.max())

    # Nothing cleared the threshold: there are no candidates by definition, and the diagnosis has
    # its own way of finding the blob below it.
    if peak <= p["threshold"]:
        return [], (diagnose_frame(working_image, params=p, x_reference=x_reference) if diagnose else None)

    valid_spots, _, labels, num_labels, _, components = _collect_valid_spots(working_image, p, collect_rejects=diagnose)
    candidates = _candidates_from_valid_spots(working_image, valid_spots, max_candidates)

    diagnosis = None
    if diagnose:
        median, _ = _frame_background(working_image)
        if peak - median < LASER_AF_DIAG_MIN_CONTRAST:
            diagnosis = SpotDiagnosis(
                frame_peak_intensity=peak, frame_median=median, note=_UNIFORM_FRAME_NOTE.format(peak, median)
            )
        else:
            diagnosis = _diagnose_above_threshold(
                working_image, p, peak, median, x_reference, labels, num_labels, components
            )
    return candidates, diagnosis


def find_spot_location(
    image: np.ndarray,
    mode: SpotDetectionMode = SpotDetectionMode.SINGLE,
    params: Optional[dict] = None,
    filter_sigma: Optional[int] = None,
    debug_plot: bool = False,
) -> Optional[Tuple[float, float]]:
    """Find the location of a spot in an image using connected components analysis.

    Args:
        image: Input grayscale image as numpy array
        mode: Which spot to detect when multiple spots are present
        params: Dictionary of parameters for spot detection. If None, default parameters will be used.
            Supported parameters:
            - threshold (float): Intensity threshold for binarization (default: 8)
            - min_area (int): Minimum component area in pixels (default: 5)
            - max_area (int): Maximum component area in pixels (default: 5000)
            - row_tolerance (float): Allowed deviation from expected row in pixels (default: 50)
            - max_aspect_ratio (float): Maximum aspect ratio for valid spot (default: 2.5)
        filter_sigma: Sigma for Gaussian filter, or None to skip filtering
        debug_plot: If True, show debug plots

    Returns:
        Optional[Tuple[float, float]]: (x, y) coordinates of spot centroid, or None if detection fails.

    Raises:
        ValueError: If image is invalid, params has an unsupported key, or mode is incompatible with detected spots
    """
    # Input validation
    if image is None or not isinstance(image, np.ndarray):
        raise ValueError("Invalid input image")

    if image.size == 0:
        raise ValueError("Invalid input image")

    # Default parameters for connected component detection
    p = _resolve_spot_detection_params(params)

    try:
        working_image = _prepare_working_image(image, filter_sigma, p["threshold"])

        valid_spots, binary, labels, num_labels, expected_row, _ = _collect_valid_spots(working_image, p)

        if len(valid_spots) == 0:
            raise ValueError("No valid spots detected after filtering")

        selected_spot = select_spot_by_mode(valid_spots, mode)

        # Calculate intensity-weighted centroid for sub-pixel accuracy
        centroid_x, centroid_y = _weighted_centroid(working_image, selected_spot["mask"], selected_spot)

        if debug_plot:
            _show_connected_components_debug_plot(
                working_image,
                binary,
                labels,
                num_labels,
                valid_spots,
                selected_spot,
                centroid_x,
                centroid_y,
                expected_row,
                p,
                mode,
            )

        return (centroid_x, centroid_y)

    except (ValueError, NotImplementedError) as e:
        raise e
    except Exception:
        _log.exception("Error in spot detection")
        return None


def _show_connected_components_debug_plot(
    image: np.ndarray,
    binary: np.ndarray,
    labels: np.ndarray,
    num_labels: int,
    valid_spots: List[dict],
    selected_spot: dict,
    centroid_x: float,
    centroid_y: float,
    expected_row: float,
    params: dict,
    mode: SpotDetectionMode,
) -> None:
    """Show debug visualization for connected components spot detection."""
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    # Plot 1: Original image with centroid
    ax1 = axes[0, 0]
    ax1.imshow(image, cmap="gray")
    ax1.axhline(y=expected_row, color="cyan", linestyle="--", alpha=0.5, label="Expected row")
    ax1.axhline(y=expected_row - params["row_tolerance"], color="cyan", linestyle=":", alpha=0.3)
    ax1.axhline(y=expected_row + params["row_tolerance"], color="cyan", linestyle=":", alpha=0.3)
    ax1.plot(centroid_x, centroid_y, "r+", markersize=20, markeredgewidth=2, label="Detected centroid")
    ax1.legend(loc="upper right")
    ax1.set_title(f"Original Image (threshold={params['threshold']})")

    # Plot 2: Binary mask
    ax2 = axes[0, 1]
    ax2.imshow(binary, cmap="gray")
    ax2.set_title(f"Binary Mask (threshold > {params['threshold']})")

    # Plot 3: Connected components with labels
    ax3 = axes[1, 0]
    # Create colored label image
    colored_labels = np.zeros((*labels.shape, 3), dtype=np.uint8)
    colors = plt.cm.tab20(np.linspace(0, 1, max(num_labels, 20)))
    for i in range(1, num_labels):
        colored_labels[labels == i] = (colors[i % 20, :3] * 255).astype(np.uint8)
    ax3.imshow(colored_labels)
    # Mark valid spots
    for spot in valid_spots:
        ax3.plot(spot["col"], spot["row"], "go", markersize=8)
    # Mark selected spot
    ax3.plot(selected_spot["col"], selected_spot["row"], "r*", markersize=15, label="Selected")
    ax3.legend(loc="upper right")
    ax3.set_title(f"Connected Components ({num_labels-1} total, {len(valid_spots)} valid)")

    # Plot 4: Zoomed view around selected spot
    ax4 = axes[1, 1]
    zoom_size = 100
    x_center = int(centroid_x)
    y_center = int(centroid_y)
    x_start = max(0, x_center - zoom_size)
    x_end = min(image.shape[1], x_center + zoom_size)
    y_start = max(0, y_center - zoom_size)
    y_end = min(image.shape[0], y_center + zoom_size)
    zoomed = image[y_start:y_end, x_start:x_end]
    ax4.imshow(zoomed, cmap="gray")
    # Adjust centroid position for zoomed view
    local_cx = centroid_x - x_start
    local_cy = centroid_y - y_start
    ax4.plot(local_cx, local_cy, "r+", markersize=20, markeredgewidth=2)
    # Show component boundary
    zoomed_mask = selected_spot["mask"][y_start:y_end, x_start:x_end]
    ax4.contour(zoomed_mask, colors="yellow", linewidths=1)
    ax4.set_title(f"Zoomed View - Mode: {mode.name}\nCentroid: ({centroid_x:.2f}, {centroid_y:.2f})")

    # Add info text
    spot_coords = ", ".join([f"({s['col']:.1f}, {s['row']:.1f})" for s in valid_spots])
    info_text = (
        f"Selected spot: area={selected_spot['area']}, intensity={selected_spot['intensity']:.1f}\n"
        f"All valid spots: [{spot_coords}]"
    )
    fig.text(0.5, 0.02, info_text, ha="center", fontsize=9, family="monospace")

    plt.tight_layout()
    plt.subplots_adjust(bottom=0.1)
    plt.show()


def get_squid_repo_state_description() -> Optional[str]:
    # From here: https://stackoverflow.com/a/22881871
    def get_script_dir(follow_symlinks=True):
        if getattr(sys, "frozen", False):  # py2exe, PyInstaller, cx_Freeze
            path = os.path.abspath(sys.executable)
        else:
            path = inspect.getabsfile(get_script_dir)
        if follow_symlinks:
            path = os.path.realpath(path)
        return os.path.dirname(path)

    try:
        repo = git.Repo(get_script_dir(), search_parent_directories=True)
        return f"{repo.head.object.hexsha} (dirty={repo.is_dirty()})"
    except (git.GitError, ValueError, OSError) as e:
        # ValueError: a .git worktree pointer file whose gitdir does not exist on this
        # machine (e.g. a portable bundle built from a worktree). Never block startup on it.
        _log.warning(f"Failed to get script git repo info: {e}")
        return None


def truncate_to_interval(val, interval: int):
    return int(interval * (val // interval))


def clamp_roi(
    offset_x: float,
    offset_y: float,
    width: float,
    height: float,
    sensor_width: int,
    sensor_height: int,
    x_interval: int = 8,
    y_interval: int = 2,
    min_width: int = 8,
    min_height: int = 2,
) -> Tuple[int, int, int, int]:
    """Snap an ROI to the camera's alignment grid and clamp it inside the sensor.

    Cameras reject an ROI that runs off the sensor. Some backends raise (the Daheng
    backend in control/camera.py compares the readback and raises CameraError), others
    silently keep the previous ROI, which leaves the config and the delivered frames
    describing different regions. Callers should route any computed ROI through here so
    an out-of-range request never reaches the driver.

    Returns (offset_x, offset_y, width, height) as ints, guaranteed to satisfy
    0 <= offset and offset + size <= sensor.

    Order matters. Sizes are clamped and truncated first, then offsets are clamped
    against the resulting size; truncation only ever shrinks, so the result stays in
    bounds. Negative offsets are clamped to 0 *before* truncation because
    truncate_to_interval floors: truncate_to_interval(-3, 8) == -8, not 0.
    """
    width = truncate_to_interval(min(max(width, min_width), sensor_width), x_interval)
    height = truncate_to_interval(min(max(height, min_height), sensor_height), y_interval)

    offset_x = truncate_to_interval(min(max(offset_x, 0), sensor_width - width), x_interval)
    offset_y = truncate_to_interval(min(max(offset_y, 0), sensor_height - height), y_interval)

    return int(offset_x), int(offset_y), int(width), int(height)


def get_available_disk_space(directory: pathlib.Path) -> int:
    """
    Returns the available disk space, in bytes, for files created as children of the given directory.

    Raises: ValueError if directory is not a directory, or doesn't exist.  PermissionError if you do not have access.
    """
    if not isinstance(directory, pathlib.Path):
        directory = pathlib.Path(directory)

    if not directory.exists():
        raise ValueError(f"Cannot check for free space in '{directory}' because it does not exist.")

    if not directory.is_dir():
        raise ValueError(f"Path must be a directory, but '{directory}' is not.")

    (total, used, free) = shutil.disk_usage(directory)

    return free


def threaded_operation_helper(
    operation: Callable, callback: Optional[Callable[[bool, Optional[str]], None]] = None, **kwargs
):
    """
    Helper function to execute an operation in a separate thread, and notify the callback when done.

    Args:
        operation: The operation to execute.
        callback: The callback to notify when the operation is done.
    Returns:
        threading.Thread: The thread that is executing the operation.
    """
    method_name = operation.__name__

    def _threaded_operation():
        try:
            _log.info(f"Executing {method_name}...")
            operation(**kwargs)
            _log.info(f"Successfully executed {method_name}")
            if callback:
                callback(True, None)
        except NotImplementedError as e:
            error_msg = str(e)
            _log.warning(error_msg)
            if callback:
                callback(False, error_msg)
        except Exception as e:
            error_msg = f"Failed to execute {method_name}: {str(e)}"
            _log.error(error_msg)
            if callback:
                callback(False, error_msg)

    thread = threading.Thread(target=_threaded_operation, name=method_name)
    thread.daemon = True
    thread.start()
    return thread


def get_directory_disk_usage(directory: pathlib.Path) -> int:
    """
    Returns the total disk size used by the contents of this directory in bytes.

    Cribbed from the interwebs here: https://stackoverflow.com/a/1392549
    """
    total_size = 0
    if isinstance(directory, str):
        directory = pathlib.Path(directory)
    for dirpath, _, filenames in os.walk(directory.absolute()):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            # skip if it is symbolic link
            if not os.path.islink(fp):
                total_size += os.path.getsize(fp)

    return total_size


class TimingManager:
    @dataclass
    class TimingPair:
        start: float
        stop: float

        def elapsed(self):
            return self.stop - self.start

    class Timer:
        def __init__(self, name):
            self._log = squid.logging.get_logger(self.__class__.__name__)
            self._name = name
            self._timing_pairs: List[TimingManager.TimingPair] = []
            self._last_start: Optional[float] = None

        def __enter__(self):
            self.start()

        def __exit__(self, exc_type, exc_val, exc_tb):
            self.stop()

        def start(self):
            if self._last_start:
                self._log.warning(f"Double start detected for Timer={self._name}")
            self._log.debug(f"Starting name={self._name}")
            self._last_start = time.perf_counter()

        def stop(self):
            if not self._last_start:
                self._log.error(f"Timer={self._name} got stop() without start() first.")
                return
            this_pair = TimingManager.TimingPair(self._last_start, time.perf_counter())
            self._timing_pairs.append(this_pair)
            self._log.debug(f"Stopping name={self._name} with elapsed={this_pair.elapsed()} [s]")
            self._last_start = None

        def get_intervals(self):
            return [tp.elapsed() for tp in self._timing_pairs]

        def get_report(self):
            intervals = self.get_intervals()

            def mean(i):
                if not len(i):
                    return "N/A"
                return f"{statistics.mean(i):.4f}"

            def median(i):
                if not len(i):
                    return "N/A"
                return f"{statistics.median(i):.4f}"

            def min_max(i):
                if not len(i):
                    return "N/A"
                return f"{min(i):.4f}/{max(i):.4f}"

            def total_time(i):
                if not len(i):
                    return "N/A"
                return f"{sum(intervals):.4f}"

            return f"{self._name:>30}: (N={len(intervals)}, total={total_time(intervals)} [s]): mean={mean(intervals)} [s], median={median(intervals)} [s], min/max={min_max(intervals)} [s]"

    def __init__(self, name):
        self._name = name
        self._timers = collections.OrderedDict()
        self._log = squid.logging.get_logger(self.__class__.__name__)

    def get_timer(self, name) -> Timer:
        if name not in self._timers:
            self._log.debug(f"Creating timer={name} for manager={self._name}")
            self._timers[name] = TimingManager.Timer(name)

        return self._timers[name]

    def get_report(self) -> str:
        timer_names = sorted(self._timers.keys())
        report = f"Timings For {self._name}:\n"
        for name in timer_names:
            timer = self._timers[name]
            report += f"  {timer.get_report()}\n"

        return report

    def get_intervals(self, name) -> List[float]:
        return self.get_timer(name).get_intervals()


def parse_well_id(well_id: str) -> Tuple[str, str]:
    """Parse well ID to (row_letter, col_number) strings.

    Extracts alphabetic row identifier and numeric column identifier from
    a well ID string. Handles single and multi-letter rows.

    Note:
        Input is normalized to uppercase. Both "a1" and "A1" return ("A", "1").

    Args:
        well_id: Well identifier, e.g., "A1", "B12", "AA3" (case-insensitive)

    Returns:
        Tuple of (row_letters, col_digits), e.g., ("A", "1"), ("AA", "3")

    Examples:
        >>> parse_well_id("A1")
        ("A", "1")
        >>> parse_well_id("B12")
        ("B", "12")
        >>> parse_well_id("aa3")  # lowercase normalized to uppercase
        ("AA", "3")
    """
    well_id = str(well_id).upper()
    letter_part = ""
    number_part = ""
    for char in well_id:
        if char.isalpha():
            letter_part += char
        else:
            number_part += char
    return (letter_part, number_part)


def within_travel(x_mm: float, y_mm: float) -> bool:
    """Is this XY inside the software travel limits? Read live: the limits
    class is mutated at runtime (Preferences), and nothing below this layer
    clamps an XY move - the firmware truncates silently."""
    limits = control._def.SOFTWARE_POS_LIMIT
    return limits.X_NEGATIVE <= x_mm <= limits.X_POSITIVE and limits.Y_NEGATIVE <= y_mm <= limits.Y_POSITIVE


def row_to_index(row: str) -> int:
    """Convert a well row label to a 0-based row index.

    Row labels are bijective base-26 (A=0, ..., Z=25, AA=26, ..., AF=31 on a
    1536-well plate), so a leading "A" is worth 26 and must not be treated as a
    plain base-26 zero digit. Sorting rows by this index is NOT the same as
    sorting the labels as strings ("AA" < "B" lexicographically).

    Args:
        row: Row letters, e.g. "A", "Z", "AA" (case-insensitive)

    Returns:
        0-based row index
    """
    index = 0
    for char in row.upper():
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


# -----------------------------------------------------------------------------
# Zarr path building utilities
# -----------------------------------------------------------------------------


def build_hcs_zarr_fov_path(base_path: str, well_id: str, fov: int) -> str:
    """Build path for HCS (wellplate) zarr FOV group (OME-NGFF compliant).

    Returns the field GROUP path per OME-NGFF spec. The image array is at
    {group_path}/0 (resolution level 0).

    Args:
        base_path: Base experiment path (e.g., /data/experiment_001)
        well_id: Well identifier (e.g., "A1", "B12")
        fov: FOV index within the well

    Returns:
        Path to zarr group: {base_path}/plate.ome.zarr/{row}/{col}/{fov}
    """
    row_letter, col_num = parse_well_id(well_id)
    return os.path.join(base_path, "plate.ome.zarr", row_letter, col_num, str(fov))


def build_per_fov_zarr_path(base_path: str, region_id: str, fov: int) -> str:
    """Build path for non-HCS per-FOV zarr store.

    Args:
        base_path: Base experiment path (e.g., /data/experiment_001)
        region_id: Region identifier (e.g., "region_0", "scan_area_1")
        fov: FOV index within the region

    Returns:
        Path to zarr store: {base_path}/zarr/{region_id}/fov_{fov}.ome.zarr
    """
    return os.path.join(base_path, "zarr", str(region_id), f"fov_{fov}.ome.zarr")


def build_6d_zarr_path(base_path: str, region_id: str) -> str:
    """Build path for 6D (FOV as dimension) zarr store.

    Args:
        base_path: Base experiment path (e.g., /data/experiment_001)
        region_id: Region identifier (e.g., "region_0")

    Returns:
        Path to zarr store: {base_path}/zarr/{region_id}/acquisition.zarr
    """
    return os.path.join(base_path, "zarr", str(region_id), "acquisition.zarr")
