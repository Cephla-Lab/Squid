"""Pure functions for laser spot detection and processing.

These are stateless, pure functions that can be easily tested independently.
They provide a cleaner interface to the spot detection algorithms and can be
used by both the LaserAutofocusController and ContinuousFocusLockController.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import cv2
import numpy as np

import squid.core.utils.hardware_utils as utils


@dataclass
class SpotDetectionResult:
    """Result of laser spot detection in a single frame."""

    x: float
    y: float
    intensity: float
    snr: float
    background: float

    @property
    def is_valid(self) -> bool:
        """Check if the detection result is valid (positive SNR)."""
        return self.snr > 0


@dataclass
class DisplacementResult:
    """Result of displacement measurement from reference."""

    displacement_um: float
    spot_x: float
    spot_y: float
    snr: float
    intensity: float
    correlation: Optional[float] = None

    @property
    def is_valid(self) -> bool:
        """Check if the measurement is valid."""
        return not np.isnan(self.displacement_um) and self.snr > 0


def remove_background(image: np.ndarray, kernel_size: int = 50) -> np.ndarray:
    """Remove background from image using morphological top-hat transform.

    Args:
        image: Input grayscale image
        kernel_size: Size of the morphological kernel (default 50)

    Returns:
        Image with background removed.
    """
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    return cv2.morphologyEx(image, cv2.MORPH_TOPHAT, kernel)


def detect_spot(
    image: np.ndarray,
    params: Dict[str, Any],
    mode: str = "Peak Detect",
    filter_sigma: float = 0.0,
    remove_bg: bool = False,
    center_crop: Optional[Tuple[int, int]] = None,
) -> Optional[SpotDetectionResult]:
    """Detect laser spot in an image and return detection result.

    Args:
        image: Input grayscale image
        params: Detection parameters dict containing:
            - y_window: Y window size for peak detection
            - x_window: X window size for peak detection
            - min_peak_width: Minimum peak width
            - min_peak_distance: Minimum distance between peaks
            - min_peak_prominence: Minimum peak prominence
            - spot_spacing: Expected spot spacing
        mode: Detection mode ("Peak Detect" or other supported modes)
        filter_sigma: Gaussian filter sigma for pre-filtering
        remove_bg: Whether to remove background
        center_crop: Optional (width, height) to crop from center before detection

    Returns:
        SpotDetectionResult with spot location and metrics, or None if detection fails.
    """
    full_height, full_width = image.shape[:2]
    processed = image.copy()

    # Apply center crop if specified
    if center_crop is not None:
        crop_w, crop_h = center_crop
        processed = utils.crop_image(processed, crop_w, crop_h)

    # Remove background if requested
    if remove_bg:
        processed = remove_background(processed)

    # Detect spot location
    try:
        result = utils.find_spot_location(
            processed,
            mode=mode,
            params=params,
            filter_sigma=filter_sigma,
        )
    except Exception:
        return None

    if result is None:
        return None

    spot_x, spot_y = result

    # Adjust coordinates if center crop was used
    if center_crop is not None:
        spot_x = spot_x + (full_width - center_crop[0]) // 2
        spot_y = spot_y + (full_height - center_crop[1]) // 2

    # Extract spot metrics from original image
    metrics = utils.extract_spot_metrics(image, int(spot_x), int(spot_y))
    if metrics is None:
        return SpotDetectionResult(
            x=spot_x, y=spot_y, intensity=0.0, snr=0.0, background=0.0
        )

    snr, intensity, background = metrics
    return SpotDetectionResult(
        x=spot_x, y=spot_y, intensity=intensity, snr=snr, background=background
    )


def compute_displacement(
    spot_x: float, reference_x: float, pixel_to_um: float
) -> float:
    """Compute displacement in micrometers from reference position.

    Args:
        spot_x: Current X position of spot in pixels
        reference_x: Reference X position in pixels
        pixel_to_um: Conversion factor from pixels to micrometers

    Returns:
        Displacement in micrometers.
    """
    return (spot_x - reference_x) * pixel_to_um


def extract_spot_crop(
    image: np.ndarray,
    center_x: float,
    center_y: float,
    crop_size: int,
) -> Tuple[np.ndarray, Tuple[int, int, int, int]]:
    """Extract a square crop around the spot location.

    Args:
        image: Input image
        center_x: X coordinate of crop center
        center_y: Y coordinate of crop center
        crop_size: Size of the square crop

    Returns:
        Tuple of (cropped image, (x_start, y_start, x_end, y_end) bounds).
    """
    height, width = image.shape[:2]
    half_size = crop_size // 2

    x_start = max(0, int(center_x) - half_size)
    x_end = min(width, int(center_x) + half_size)
    y_start = max(0, int(center_y) - half_size)
    y_end = min(height, int(center_y) + half_size)

    crop = image[y_start:y_end, x_start:x_end]
    return crop, (x_start, y_start, x_end, y_end)


def compute_correlation(
    current_crop: np.ndarray, reference_crop: np.ndarray
) -> Optional[float]:
    """Compute normalized correlation between current and reference crops.

    Args:
        current_crop: Current image crop
        reference_crop: Reference image crop (must be same shape)

    Returns:
        Correlation coefficient between -1 and 1, or None if computation fails.
    """
    if current_crop.size == 0 or reference_crop.size == 0:
        return None

    if current_crop.shape != reference_crop.shape:
        return None

    current_float = current_crop.astype(np.float32)
    max_val = float(np.max(current_float))
    if max_val == 0:
        return None

    current_norm = (current_float - np.mean(current_float)) / max_val

    try:
        correlation = float(
            np.corrcoef(current_norm.ravel(), reference_crop.ravel())[0, 1]
        )
    except Exception:
        return None

    return correlation


def normalize_crop_for_reference(crop: np.ndarray) -> Optional[np.ndarray]:
    """Normalize a crop for use as a correlation reference.

    Args:
        crop: Image crop to normalize

    Returns:
        Normalized crop as float32, or None if normalization fails.
    """
    if crop.size == 0:
        return None

    crop_float = crop.astype(np.float32)
    max_val = float(np.max(crop_float))
    if max_val == 0:
        return None

    return (crop_float - np.mean(crop_float)) / max_val


def is_spot_in_range(
    spot_x: float,
    reference_x: float,
    pixel_to_um: float,
    max_range_um: float,
) -> bool:
    """Check if detected spot is within acceptable range from reference.

    Args:
        spot_x: Detected X position in pixels
        reference_x: Reference X position in pixels
        pixel_to_um: Conversion factor from pixels to micrometers
        max_range_um: Maximum allowed displacement in micrometers

    Returns:
        True if spot is within range, False otherwise.
    """
    displacement_um = abs(spot_x - reference_x) * pixel_to_um
    return displacement_um <= max_range_um
