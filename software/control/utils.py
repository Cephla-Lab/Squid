import collections
import enum
import inspect
import pathlib
import sys
import shutil
import statistics
import time
import threading
from dataclasses import dataclass

import cv2
import git
from numpy import square, mean
import numpy as np
from scipy.ndimage import label, gaussian_filter
from scipy import signal
from skimage.registration import phase_cross_correlation
import os
from typing import NamedTuple, Optional, Tuple, List, Callable

from control._def import (
    LASER_AF_Y_WINDOW,
    LASER_AF_X_WINDOW,
    LASER_AF_MIN_PEAK_WIDTH,
    LASER_AF_MIN_PEAK_DISTANCE,
    LASER_AF_MIN_PEAK_PROMINENCE,
    LASER_AF_SPOT_SPACING,
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


def center_crop_origin(image_width: int, image_height: int, crop_width: int, crop_height: int) -> Tuple[int, int]:
    """Top-left corner (left, top) of the centred crop that crop_image takes; a crop larger than the image
    starts at the image's corner, so positions found in the crop map back by adding this origin."""
    left = int(max(image_width / 2 - crop_width / 2, 0))
    top = int(max(image_height / 2 - crop_height / 2, 0))
    return left, top


def crop_image(image, crop_width, crop_height):
    image_height = image.shape[0]
    image_width = image.shape[1]
    if crop_width is None:
        crop_width = image_width
    if crop_height is None:
        crop_height = image_height
    roi_left, roi_top = center_crop_origin(image_width, image_height, crop_width, crop_height)
    roi_right = int(min(image_width / 2 + crop_width / 2, image_width))
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


class _ProfilePeaks(NamedTuple):
    """One pass of the spot search: the row band that holds the spots and every peak along it."""

    image: np.ndarray  # after the optional Gaussian filter
    peak_y: int
    y_intensity_profile: np.ndarray
    band: np.ndarray  # the rows around peak_y that the x profile is summed over
    x_intensity_profile: np.ndarray  # normalised to 0-1
    locations: np.ndarray  # peak x positions, ascending
    properties: dict


def _spot_detection_params(params: Optional[dict]) -> dict:
    """The detection parameters with defaults filled in; a misspelled key would otherwise be ignored."""
    p = {
        "y_window": LASER_AF_Y_WINDOW,  # Half-height of y-axis crop
        "x_window": LASER_AF_X_WINDOW,  # Half-width of centroid window
        "min_peak_width": LASER_AF_MIN_PEAK_WIDTH,  # Minimum width of peaks
        "min_peak_distance": LASER_AF_MIN_PEAK_DISTANCE,  # Minimum distance between peaks
        "min_peak_prominence": LASER_AF_MIN_PEAK_PROMINENCE,  # Minimum peak prominence
        "intensity_threshold": 0.1,  # Threshold for intensity filtering
        "spot_spacing": LASER_AF_SPOT_SPACING,  # Expected spacing between spots
    }
    if params is not None:
        unknown_keys = set(params) - set(p)
        if unknown_keys:
            raise ValueError(f"Unknown spot detection parameters: {sorted(unknown_keys)}")
        p.update(params)
    return p


def _find_profile_peaks(image: np.ndarray, p: dict, filter_sigma: Optional[int]) -> _ProfilePeaks:
    """Locate the row band holding the spots and every peak along it (raises ValueError when there is none)."""
    if image is None or not isinstance(image, np.ndarray):
        raise ValueError("Invalid input image")

    # Apply Gaussian filter if requested
    if filter_sigma is not None and filter_sigma > 0:
        filtered = gaussian_filter(image.astype(float), sigma=filter_sigma)
        image = np.clip(filtered, 0, 255).astype(np.uint8)

    # Get the y position of the spots
    y_intensity_profile = np.sum(image, axis=1)
    if np.all(y_intensity_profile == 0):
        raise ValueError("No spots detected in image")

    peak_y = np.argmax(y_intensity_profile)

    # Validate peak_y location
    if peak_y < p["y_window"] or peak_y > image.shape[0] - p["y_window"]:
        raise ValueError("Spot too close to image edge")

    # Crop along the y axis
    band = image[peak_y - p["y_window"] : peak_y + p["y_window"], :]

    # Get signal along x
    x_intensity_profile = np.sum(band, axis=0)

    # Normalize intensity profile
    x_intensity_profile = x_intensity_profile - np.min(x_intensity_profile)
    x_intensity_profile = x_intensity_profile / np.max(x_intensity_profile)

    # Find all peaks
    locations, properties = signal.find_peaks(
        x_intensity_profile,
        width=p["min_peak_width"],
        distance=p["min_peak_distance"],
        prominence=p["min_peak_prominence"],
    )
    if len(locations) == 0:
        raise ValueError("No peaks detected")

    return _ProfilePeaks(image, peak_y, y_intensity_profile, band, x_intensity_profile, locations, properties)


def find_spot_peaks(image: np.ndarray, params: Optional[dict] = None, filter_sigma: Optional[int] = None) -> np.ndarray:
    """The x position (pixels, ascending) of every spot find_spot_location would rank, with the same parameters.

    Raises:
        ValueError: If image is invalid, params has an unsupported key, or no spot is found
    """
    return _find_profile_peaks(image, _spot_detection_params(params), filter_sigma).locations


def find_spot_location(
    image: np.ndarray,
    mode: SpotDetectionMode = SpotDetectionMode.SINGLE,
    spot_index: int = 1,
    params: Optional[dict] = None,
    filter_sigma: Optional[int] = None,
    debug_plot: bool = False,
) -> Optional[Tuple[float, float]]:
    """Find the location of a spot in an image.

    Args:
        image: Input grayscale image as numpy array
        mode: Which spot to detect when multiple spots are present
        spot_index: For the NTH_FROM_* modes, which spot counted from that side (1 = outermost)
        params: Dictionary of parameters for spot detection. If None, default parameters will be used.
            Supported parameters:
            - y_window (int): Half-height of y-axis crop (default: 96)
            - x_window (int): Half-width of centroid window (default: 20)
            - min_peak_width (int): Minimum width of peaks (default: 10)
            - min_peak_distance (int): Minimum distance between peaks (default: 10)
            - min_peak_prominence (float): Minimum peak prominence, on the normalized 0-1 profile (default: 0.2)
            - intensity_threshold (float): Threshold for intensity filtering (default: 0.1)
            - spot_spacing (int): Expected spacing between spots for multi-spot modes (default: 100)

    Returns:
        Optional[Tuple[float, float]]: (x,y) coordinates of selected spot, or None if detection fails

    Raises:
        ValueError: If image is invalid, params has an unsupported key, or mode is incompatible with detected spots
    """
    p = _spot_detection_params(params)

    try:
        found = _find_profile_peaks(image, p, filter_sigma)
        image, peak_y, y_intensity_profile = found.image, found.peak_y, found.y_intensity_profile
        cropped_image, x_intensity_profile = found.band, found.x_intensity_profile
        peak_locations, peak_properties = found.locations, found.properties

        # Handle different spot detection modes
        if mode == SpotDetectionMode.SINGLE:
            if len(peak_locations) > 1:
                raise ValueError(f"Found {len(peak_locations)} peaks but expected single peak")
            peak_x = peak_locations[0]
        elif mode in (SpotDetectionMode.NTH_FROM_LEFT, SpotDetectionMode.NTH_FROM_RIGHT):
            # find_peaks returns the peaks in ascending x, so the leftmost is first
            side = "left" if mode == SpotDetectionMode.NTH_FROM_LEFT else "right"
            if spot_index < 1:
                raise ValueError(f"spot_index counts from 1, got {spot_index}")
            if len(peak_locations) < spot_index:
                raise ValueError(
                    f"Found {len(peak_locations)} peaks but spot {spot_index} from the {side} was requested"
                )
            peak_x = peak_locations[spot_index - 1] if side == "left" else peak_locations[-spot_index]
        else:
            raise ValueError(f"Unknown spot detection mode: {mode}")

        if debug_plot:
            import matplotlib.pyplot as plt

            fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(10, 8))

            # Plot original image
            ax1.imshow(image, cmap="gray")
            ax1.axhline(y=peak_y, color="r", linestyle="--", label="Peak Y")
            ax1.axhline(y=peak_y - p["y_window"], color="g", linestyle="--", label="Crop Window")
            ax1.axhline(y=peak_y + p["y_window"], color="g", linestyle="--")
            ax1.legend()
            ax1.set_title("Original Image with Y-crop Lines")

            # Plot Y intensity profile
            ax2.plot(y_intensity_profile)
            ax2.axvline(x=peak_y, color="r", linestyle="--", label="Peak Y")
            ax2.axvline(x=peak_y - p["y_window"], color="g", linestyle="--", label="Crop Window")
            ax2.axvline(x=peak_y + p["y_window"], color="g", linestyle="--")
            ax2.legend()
            ax2.set_title("Y Intensity Profile")

            # Plot X intensity profile and detected peaks
            ax3.plot(x_intensity_profile, label="Intensity Profile")
            ax3.plot(peak_locations, x_intensity_profile[peak_locations], "x", color="r", label="All Peaks")

            # Plot prominence for all peaks
            for peak_idx, prominence in zip(peak_locations, peak_properties["prominences"]):
                ax3.vlines(
                    x=peak_idx,
                    ymin=x_intensity_profile[peak_idx] - prominence,
                    ymax=x_intensity_profile[peak_idx],
                    color="g",
                )

            # Highlight selected peak
            ax3.plot(peak_x, x_intensity_profile[peak_x], "o", color="yellow", markersize=10, label="Selected Peak")
            ax3.axvline(x=peak_x, color="yellow", linestyle="--", alpha=0.5)

            ax3.legend()
            ax3.set_title(f"X Intensity Profile (Mode: {mode.name}, N={spot_index})")

            plt.tight_layout()
            plt.show()

        # Calculate centroid in window around selected peak
        return _calculate_spot_centroid(cropped_image, peak_x, peak_y, p)

    except ValueError:
        raise
    except Exception:
        # TODO: this should not be a blank Exception catch, we should jsut return None above if we have a valid "no spots"
        # case, and let exceptions raise otherwise.
        _log.exception(f"Error in spot detection")
        return None


def _calculate_spot_centroid(cropped_image: np.ndarray, peak_x: int, peak_y: int, params: dict) -> Tuple[float, float]:
    """Calculate precise centroid location in window around peak."""
    h, w = cropped_image.shape
    x, y = np.meshgrid(range(w), range(h))

    # Crop region around the peak
    intensity_window = cropped_image[:, peak_x - params["x_window"] : peak_x + params["x_window"]]
    x_coords = x[:, peak_x - params["x_window"] : peak_x + params["x_window"]]
    y_coords = y[:, peak_x - params["x_window"] : peak_x + params["x_window"]]

    # Process intensity values
    intensity_window = intensity_window.astype(float)
    intensity_window = intensity_window - np.amin(intensity_window)
    if np.amax(intensity_window) > 0:  # Avoid division by zero
        intensity_window[intensity_window / np.amax(intensity_window) < params["intensity_threshold"]] = 0

    # Calculate centroid
    sum_intensity = np.sum(intensity_window)
    if sum_intensity == 0:
        raise ValueError("No significant intensity in centroid window")

    centroid_x = np.sum(x_coords * intensity_window) / sum_intensity
    centroid_y = np.sum(y_coords * intensity_window) / sum_intensity

    # Convert back to original image coordinates
    centroid_y = peak_y - params["y_window"] + centroid_y

    return (centroid_x, centroid_y)


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
    except git.GitError as e:
        _log.warning(f"Failed to get script git repo info: {e}")
        return None


def truncate_to_interval(val, interval: int):
    return int(interval * (val // interval))


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
