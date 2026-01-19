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
import os
from typing import Optional, Tuple, List, Callable

from control._def import (
    LASER_AF_CC_THRESHOLD,
    LASER_AF_CC_MIN_AREA,
    LASER_AF_CC_MAX_AREA,
    LASER_AF_CC_ROW_TOLERANCE,
    LASER_AF_CC_MAX_ASPECT_RATIO,
    SpotDetectionMode,
    FocusMeasureOperator,
)
import squid.logging

_log = squid.logging.get_logger("control.utils")


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
    if len(image.shape) == 3:
        image = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)  # optional
    if method == FocusMeasureOperator.LAPE:
        if image.dtype == np.uint16:
            lap = cv2.Laplacian(image, cv2.CV_32F)
        else:
            lap = cv2.Laplacian(image, cv2.CV_16S)
        focus_measure = mean(square(lap))
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
    ret_image = image.copy()
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
            ret_image = cv2.flip(ret_image, 0)
        elif flip_image == FlipVariant.HORIZONTAL:
            ret_image = cv2.flip(ret_image, 1)
        elif flip_image == FlipVariant.BOTH:
            ret_image = cv2.flip(ret_image, -1)

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
        ValueError: If image is invalid or mode is incompatible with detected spots
    """
    # Input validation
    if image is None or not isinstance(image, np.ndarray):
        raise ValueError("Invalid input image")

    if image.size == 0:
        raise ValueError("Invalid input image")

    # Default parameters for connected component detection
    default_params = {
        "threshold": LASER_AF_CC_THRESHOLD,
        "min_area": LASER_AF_CC_MIN_AREA,
        "max_area": LASER_AF_CC_MAX_AREA,
        "row_tolerance": LASER_AF_CC_ROW_TOLERANCE,
        "max_aspect_ratio": LASER_AF_CC_MAX_ASPECT_RATIO,
    }

    if params is not None:
        default_params.update(params)
    p = default_params

    try:
        # Apply Gaussian filter if requested
        working_image = image.copy()
        if filter_sigma is not None and filter_sigma > 0:
            filtered = gaussian_filter(working_image.astype(float), sigma=filter_sigma)
            working_image = np.clip(filtered, 0, 255).astype(np.uint8)

        # Quick check - if max intensity below threshold, no spot visible
        if working_image.max() <= p["threshold"]:
            raise ValueError("No spot detected: max intensity below threshold")

        # Binarize the image
        binary = (working_image > p["threshold"]).astype(np.uint8)

        # Find connected components
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(binary, connectivity=8)

        # Expected row position (center of image)
        expected_row = working_image.shape[0] / 2.0

        # Filter valid components and collect spot candidates
        valid_spots = []
        for i in range(1, num_labels):  # Skip background (label 0)
            area = stats[i, cv2.CC_STAT_AREA]
            cx, cy = centroids[i]
            width = stats[i, cv2.CC_STAT_WIDTH]
            height = stats[i, cv2.CC_STAT_HEIGHT]

            # Size filter
            if area < p["min_area"] or area > p["max_area"]:
                continue

            # Row position filter
            if abs(cy - expected_row) > p["row_tolerance"]:
                continue

            # Aspect ratio filter (max of w/h or h/w, so always >= 1)
            aspect_ratio = max(width / height, height / width) if height > 0 and width > 0 else float("inf")
            if aspect_ratio > p["max_aspect_ratio"]:
                continue

            # Calculate mean intensity of this component for sorting
            component_mask = labels == i
            intensity = working_image[component_mask].mean()

            valid_spots.append(
                {
                    "label": i,
                    "col": cx,
                    "row": cy,
                    "area": area,
                    "intensity": intensity,
                    "mask": component_mask,
                }
            )

        if len(valid_spots) == 0:
            raise ValueError("No valid spots detected after filtering")

        # Sort spots by x-coordinate (column) for mode-based selection
        valid_spots.sort(key=lambda s: s["col"])

        # Handle different spot detection modes
        if mode == SpotDetectionMode.SINGLE:
            if len(valid_spots) > 1:
                raise ValueError(f"Found {len(valid_spots)} spots but expected single spot")
            selected_spot = valid_spots[0]
        elif mode == SpotDetectionMode.DUAL_LEFT:
            selected_spot = valid_spots[0]  # Leftmost
        elif mode == SpotDetectionMode.DUAL_RIGHT:
            selected_spot = valid_spots[-1]  # Rightmost
        elif mode == SpotDetectionMode.MULTI_RIGHT:
            selected_spot = valid_spots[-1]  # Rightmost
        elif mode == SpotDetectionMode.MULTI_SECOND_RIGHT:
            raise NotImplementedError("MULTI_SECOND_RIGHT is not supported")
        else:
            raise ValueError(f"Unknown spot detection mode: {mode}")

        # Calculate intensity-weighted centroid for sub-pixel accuracy
        component_mask = selected_spot["mask"]
        y_coords, x_coords = np.where(component_mask)
        intensities = working_image[component_mask].astype(float)

        # Subtract background (minimum intensity in component)
        intensities = intensities - intensities.min()

        sum_intensity = intensities.sum()
        if sum_intensity == 0:
            # Fall back to geometric centroid if all intensities are equal
            centroid_x = selected_spot["col"]
            centroid_y = selected_spot["row"]
        else:
            centroid_x = (x_coords * intensities).sum() / sum_intensity
            centroid_y = (y_coords * intensities).sum() / sum_intensity

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
