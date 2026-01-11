"""
Image content validation for acquisition tests.

This module provides tools for validating captured images beyond just counting them,
including dimension checks, data type validation, content uniqueness, and z-stack
focus variation checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from tests.harness.core.backend_context import BackendContext


@dataclass
class ImageSpec:
    """Expected properties for an image."""

    height: int
    width: int
    dtype: np.dtype = field(default_factory=lambda: np.dtype(np.uint16))
    min_value: int = 0
    max_value: int = 65535
    channel_name: Optional[str] = None
    z_index: Optional[int] = None


class ImageValidator:
    """
    Validates captured images against specifications.

    This class collects images during acquisition and provides methods
    to validate their properties, ensuring data integrity beyond simple
    image counts.

    Usage:
        with BackendContext() as ctx:
            validator = ImageValidator(ctx)

            # Run acquisition...

            # Validate captured images
            images = validator.get_captured_images()
            for img in images:
                assert validator.validate_dimensions(img, expected_spec)
                assert validator.validate_not_blank(img)
    """

    def __init__(self, ctx: "BackendContext"):
        """
        Initialize the image validator.

        Args:
            ctx: BackendContext instance for accessing camera config
        """
        self._ctx = ctx
        self._captured_images: List[np.ndarray] = []
        self._image_metadata: List[dict] = []
        self._subscribed = False

    def subscribe_to_frames(self) -> None:
        """
        Subscribe to frame capture events to collect images.

        Note: This requires the StreamHandler to publish frames via EventBus,
        which may not be enabled in all test configurations.
        """
        # For now, images must be collected via alternative means
        # (e.g., reading from disk or intercepting the image callback)
        self._subscribed = True

    def add_image(self, image: np.ndarray, metadata: Optional[dict] = None) -> None:
        """
        Manually add an image for validation.

        Args:
            image: The captured image as numpy array
            metadata: Optional metadata (channel, z, coordinates, etc.)
        """
        self._captured_images.append(image.copy())
        self._image_metadata.append(metadata or {})

    def get_captured_images(self) -> List[np.ndarray]:
        """Get all captured images."""
        return self._captured_images

    def get_image_metadata(self) -> List[dict]:
        """Get metadata for all captured images."""
        return self._image_metadata

    def clear(self) -> None:
        """Clear all captured images and metadata."""
        self._captured_images = []
        self._image_metadata = []

    # =========================================================================
    # Validation Methods
    # =========================================================================

    def validate_dimensions(self, image: np.ndarray, spec: ImageSpec) -> bool:
        """
        Check if image dimensions match the expected spec.

        Args:
            image: Image to validate
            spec: Expected image properties

        Returns:
            True if dimensions match
        """
        if len(image.shape) == 2:
            return image.shape == (spec.height, spec.width)
        elif len(image.shape) == 3:
            # RGB or multi-channel image
            return image.shape[:2] == (spec.height, spec.width)
        return False

    def validate_dtype(self, image: np.ndarray, spec: ImageSpec) -> bool:
        """
        Check if image data type matches the expected spec.

        Args:
            image: Image to validate
            spec: Expected image properties

        Returns:
            True if dtype matches
        """
        return image.dtype == spec.dtype

    def validate_value_range(self, image: np.ndarray, spec: ImageSpec) -> bool:
        """
        Check if image pixel values are within expected range.

        Args:
            image: Image to validate
            spec: Expected image properties

        Returns:
            True if values are in range
        """
        return image.min() >= spec.min_value and image.max() <= spec.max_value

    def validate_not_blank(
        self,
        image: np.ndarray,
        std_threshold: float = 1.0,
    ) -> bool:
        """
        Check if image has non-trivial content (not blank).

        An image is considered blank if the standard deviation of pixel
        values is below the threshold, indicating uniform content.

        Args:
            image: Image to validate
            std_threshold: Minimum standard deviation required

        Returns:
            True if image has content
        """
        return float(np.std(image)) > std_threshold

    def validate_not_saturated(
        self,
        image: np.ndarray,
        saturation_threshold: float = 0.1,
    ) -> bool:
        """
        Check if image is not oversaturated.

        An image is considered saturated if more than the threshold
        fraction of pixels are at the maximum value.

        Args:
            image: Image to validate
            saturation_threshold: Maximum fraction of saturated pixels

        Returns:
            True if image is not saturated
        """
        if image.dtype == np.uint8:
            max_val = 255
        elif image.dtype == np.uint16:
            max_val = 65535
        else:
            max_val = np.iinfo(image.dtype).max if np.issubdtype(image.dtype, np.integer) else 1.0

        saturated_fraction = np.sum(image >= max_val) / image.size
        return bool(saturated_fraction < saturation_threshold)

    def validate_unique_content(self, images: List[np.ndarray]) -> bool:
        """
        Verify that images are not identical.

        This is useful for checking that different channels or z-planes
        actually captured different content.

        Args:
            images: List of images to compare

        Returns:
            True if all images have unique content
        """
        for i, img1 in enumerate(images):
            for img2 in images[i + 1 :]:
                if np.array_equal(img1, img2):
                    return False
        return True

    def validate_z_progression(
        self,
        z_stack: List[np.ndarray],
        min_variation_ratio: float = 1.05,
    ) -> bool:
        """
        Verify that z-stack planes show focus variation.

        A valid z-stack should have planes with different focus quality,
        indicating the z-position actually changed.

        Args:
            z_stack: List of images from different z-planes
            min_variation_ratio: Minimum ratio between best and worst focus

        Returns:
            True if z-stack shows expected focus variation
        """
        if len(z_stack) < 2:
            return True  # Single plane, nothing to validate

        focus_scores = [self._compute_focus_metric(img) for img in z_stack]

        if min(focus_scores) == 0:
            return False  # Blank images

        ratio = max(focus_scores) / max(min(focus_scores), 1e-10)
        return ratio >= min_variation_ratio

    def _compute_focus_metric(self, image: np.ndarray) -> float:
        """
        Compute a focus quality metric for an image.

        Uses the variance of the Laplacian as a focus measure.
        Higher values indicate sharper (more in-focus) images.

        Args:
            image: Input image

        Returns:
            Focus quality score
        """
        if len(image.shape) == 3:
            # Convert to grayscale
            image = np.mean(image, axis=2).astype(image.dtype)

        # Laplacian kernel
        laplacian = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]])

        # Convolve with Laplacian (simple implementation)
        from scipy import ndimage

        try:
            lap = ndimage.convolve(image.astype(float), laplacian)
            return float(np.var(lap))
        except ImportError:
            # Fallback if scipy not available
            return float(np.std(image))

    # =========================================================================
    # Batch Validation
    # =========================================================================

    def validate_all(self, spec: ImageSpec) -> List[str]:
        """
        Validate all captured images against a specification.

        Args:
            spec: Expected image properties

        Returns:
            List of validation error messages (empty if all pass)
        """
        errors = []

        for i, img in enumerate(self._captured_images):
            if not self.validate_dimensions(img, spec):
                errors.append(
                    f"Image {i}: dimensions {img.shape} don't match "
                    f"expected ({spec.height}, {spec.width})"
                )

            if not self.validate_dtype(img, spec):
                errors.append(
                    f"Image {i}: dtype {img.dtype} doesn't match expected {spec.dtype}"
                )

            if not self.validate_value_range(img, spec):
                errors.append(
                    f"Image {i}: values [{img.min()}, {img.max()}] outside "
                    f"expected range [{spec.min_value}, {spec.max_value}]"
                )

            if not self.validate_not_blank(img):
                errors.append(f"Image {i}: appears to be blank")

        return errors

    def assert_all_valid(self, spec: ImageSpec) -> None:
        """
        Assert that all captured images are valid.

        Args:
            spec: Expected image properties

        Raises:
            AssertionError: If any validation fails
        """
        errors = self.validate_all(spec)
        if errors:
            raise AssertionError(
                f"Image validation failed with {len(errors)} errors:\n"
                + "\n".join(f"  - {e}" for e in errors)
            )
