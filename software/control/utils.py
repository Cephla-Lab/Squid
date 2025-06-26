import enum
import inspect
import pathlib
import sys
import shutil
import statistics
import time
from dataclasses import dataclass

import cv2
import git
from numpy import square, mean
import numpy as np
from scipy.ndimage import label, gaussian_filter
from scipy import signal
import os
from typing import Optional, Tuple, List

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
    """Find the location of a spot in an image.

    Args:
        image: Input grayscale image as numpy array
        mode: Which spot to detect when multiple spots are present
        params: Dictionary of parameters for spot detection. If None, default parameters will be used.
            Supported parameters:
            - y_window (int): Half-height of y-axis crop (default: 96)
            - x_window (int): Half-width of centroid window (default: 20)
            - peak_width (int): Minimum width of peaks (default: 10)
            - peak_distance (int): Minimum distance between peaks (default: 10)
            - peak_prominence (float): Minimum peak prominence (default: 100)
            - intensity_threshold (float): Threshold for intensity filtering (default: 0.1)
            - spot_spacing (int): Expected spacing between spots for multi-spot modes (default: 100)

    Returns:
        Optional[Tuple[float, float]]: (x,y) coordinates of selected spot, or None if detection fails

    Raises:
        ValueError: If image is invalid or mode is incompatible with detected spots
    """
    # Input validation
    if image is None or not isinstance(image, np.ndarray):
        raise ValueError("Invalid input image")

    # Default parameters
    default_params = {
        "y_window": LASER_AF_Y_WINDOW,  # Half-height of y-axis crop
        "x_window": LASER_AF_X_WINDOW,  # Half-width of centroid window
        "min_peak_width": LASER_AF_MIN_PEAK_WIDTH,  # Minimum width of peaks
        "min_peak_distance": LASER_AF_MIN_PEAK_DISTANCE,  # Minimum distance between peaks
        "min_peak_prominence": LASER_AF_MIN_PEAK_PROMINENCE,  # Minimum peak prominence
        "intensity_threshold": 0.1,  # Threshold for intensity filtering
        "spot_spacing": LASER_AF_SPOT_SPACING,  # Expected spacing between spots
    }

    if params is not None:
        default_params.update(params)
    p = default_params

    try:
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
        cropped_image = image[peak_y - p["y_window"] : peak_y + p["y_window"], :]

        # Get signal along x
        x_intensity_profile = np.sum(cropped_image, axis=0)

        # Normalize intensity profile
        x_intensity_profile = x_intensity_profile - np.min(x_intensity_profile)
        x_intensity_profile = x_intensity_profile / np.max(x_intensity_profile)

        # Find all peaks
        peaks = signal.find_peaks(
            x_intensity_profile,
            width=p["min_peak_width"],
            distance=p["min_peak_distance"],
            prominence=p["min_peak_prominence"],
        )
        peak_locations = peaks[0]
        peak_properties = peaks[1]

        if len(peak_locations) == 0:
            raise ValueError("No peaks detected")

        # Handle different spot detection modes
        if mode == SpotDetectionMode.SINGLE:
            if len(peak_locations) > 1:
                raise ValueError(f"Found {len(peak_locations)} peaks but expected single peak")
            peak_x = peak_locations[0]
        elif mode == SpotDetectionMode.DUAL_RIGHT:
            peak_x = peak_locations[-1]
        elif mode == SpotDetectionMode.DUAL_LEFT:
            peak_x = peak_locations[0]
        elif mode == SpotDetectionMode.MULTI_RIGHT:
            peak_x = peak_locations[-1]
        elif mode == SpotDetectionMode.MULTI_SECOND_RIGHT:
            raise NotImplementedError("MULTI_SECOND_RIGHT is not supported")
            # if len(peak_locations) < 2:
            #     raise ValueError("Not enough peaks for MULTI_SECOND_RIGHT mode")
            # peak_x = peak_locations[-2]
            # (peak_x, _) = _calculate_spot_centroid(cropped_image, peak_x, peak_y, p)
            # peak_x = peak_x - p["spot_spacing"]
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
            ax3.set_title(f"X Intensity Profile (Mode: {mode.name})")

            plt.tight_layout()
            plt.show()

        # Calculate centroid in window around selected peak
        return _calculate_spot_centroid(cropped_image, peak_x, peak_y, p)

    except (ValueError, NotImplementedError) as e:
        raise e
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

            return f"{self._name} (N={len(intervals)}): mean={mean(intervals)} [s], median={median(intervals)} [s], min/max={min_max(intervals)} [s]"

    def __init__(self, name):
        self._name = name
        self._timers = {}
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
