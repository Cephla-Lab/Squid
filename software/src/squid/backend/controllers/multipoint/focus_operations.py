"""
Autofocus execution for multipoint acquisitions.

This module provides:
- AutofocusExecutor: Autofocus decision logic and execution

This class encapsulates autofocus logic previously embedded in
MultiPointWorker.
"""

from typing import Any, Callable, Optional, TYPE_CHECKING, Dict, Tuple

import squid.core.logging
from _def import Acquisition, MULTIPOINT_AUTOFOCUS_CHANNEL

if TYPE_CHECKING:
    from squid.backend.controllers.autofocus import AutoFocusController, LaserAutofocusController
    from squid.backend.managers import ChannelConfigurationManager, ObjectiveStore


_log = squid.core.logging.get_logger(__name__)


class AutofocusExecutor:
    """
    Executes autofocus during multipoint acquisitions.

    Handles decision logic for when to perform autofocus and delegates
    to the appropriate autofocus controller (contrast-based or laser AF).

    Usage:
        executor = AutofocusExecutor(
            autofocus_controller=af_controller,
            laser_af_controller=laser_af,
            channel_config_manager=config_manager,
            objective_store=objectives,
        )

        # Configure autofocus mode
        executor.configure(
            do_autofocus=True,
            do_reflection_af=False,
            nz=1,
            z_stacking_config="FROM CENTER",
        )

        # Perform autofocus if needed
        success = executor.perform_autofocus(region_id="region_0", fov=0)
    """

    def __init__(
        self,
        autofocus_controller: Optional["AutoFocusController"] = None,
        laser_af_controller: Optional["LaserAutofocusController"] = None,
        focus_lock_controller: Optional[Any] = None,
        channel_config_manager: Optional["ChannelConfigurationManager"] = None,
        objective_store: Optional["ObjectiveStore"] = None,
    ):
        """
        Initialize the autofocus executor.

        Args:
            autofocus_controller: Contrast-based autofocus controller
            laser_af_controller: Laser reflection autofocus controller
            focus_lock_controller: Focus lock controller (optional)
            channel_config_manager: Channel configuration manager
            objective_store: Objective store for current objective
        """
        self._autofocus = autofocus_controller
        self._laser_af = laser_af_controller
        self._focus_lock = focus_lock_controller
        self._channel_config = channel_config_manager
        self._objectives = objective_store

        # Configuration
        self._do_autofocus = False
        self._do_reflection_af = False
        self._nz = 1
        self._z_stacking_config = "FROM BOTTOM"
        self._af_fov_count = 0
        self._fovs_per_af = Acquisition.NUMBER_OF_FOVS_PER_AF

        # Callback for applying channel configuration
        self._apply_config_callback: Optional[Callable] = None

    @property
    def af_fov_count(self) -> int:
        """Get/set the autofocus FOV counter."""
        return self._af_fov_count

    @af_fov_count.setter
    def af_fov_count(self, value: int) -> None:
        self._af_fov_count = value

    def configure(
        self,
        do_autofocus: bool = False,
        do_reflection_af: bool = False,
        nz: int = 1,
        z_stacking_config: str = "FROM BOTTOM",
        fovs_per_af: Optional[int] = None,
    ) -> None:
        """
        Configure autofocus behavior.

        Args:
            do_autofocus: Enable contrast-based autofocus
            do_reflection_af: Enable laser reflection autofocus
            nz: Number of z-levels
            z_stacking_config: Z-stacking configuration
            fovs_per_af: Number of FOVs between autofocus (None = use default)
        """
        self._do_autofocus = do_autofocus
        self._do_reflection_af = do_reflection_af
        self._nz = nz
        self._z_stacking_config = z_stacking_config
        if fovs_per_af is not None:
            self._fovs_per_af = fovs_per_af

    def set_apply_config_callback(self, callback: Callable) -> None:
        """
        Set callback for applying channel configuration.

        The callback should take a ChannelMode as argument.
        """
        self._apply_config_callback = callback

    def is_focus_lock_active(self) -> bool:
        """
        Check if continuous focus lock is in use (started, possibly paused).

        When focus lock is active, it handles focus continuously and
        traditional per-FOV autofocus should be skipped.

        Returns:
            True if focus lock is active
        """
        return (
            self._focus_lock is not None
            and getattr(self._focus_lock, "is_active", False)
        )

    def should_perform_autofocus(self) -> bool:
        """
        Check if autofocus should be performed based on current state.

        Returns:
            True if autofocus should be performed
        """
        # Focus lock handles focus continuously - skip per-FOV AF
        if self.is_focus_lock_active():
            return False

        if self._do_reflection_af:
            return True

        # Contrast-based AF conditions
        if not self._do_autofocus:
            return False

        # Only AF when not taking z-stack or doing z-stack from center
        if not (self._nz == 1 or self._z_stacking_config == "FROM CENTER"):
            return False

        # Check FOV interval
        if self._af_fov_count % self._fovs_per_af != 0:
            return False

        return True

    def perform_autofocus(
        self,
        region_id: str = "",  # noqa: ARG002 - kept for future error logging
        fov: int = 0,  # noqa: ARG002 - kept for future error logging
        timeout_s: Optional[float] = None,
    ) -> bool:
        """
        Perform autofocus if conditions are met.

        Args:
            region_id: Region identifier (reserved for future error logging)
            fov: FOV index (reserved for future error logging)
            timeout_s: Timeout for autofocus operation

        Returns:
            True if autofocus succeeded or was skipped, False if failed
        """
        # Mark as intentionally unused (reserved for future error context)
        _ = (region_id, fov)

        if not self.should_perform_autofocus():
            return True

        if self._do_reflection_af:
            return self._perform_laser_af()
        else:
            return self._perform_contrast_af(timeout_s)

    def generate_focus_map_for_acquisition(
        self,
        scan_bounds: Dict[str, Tuple[float, float]],
        dx_mm: float,
        dy_mm: float,
    ) -> Optional[Tuple[float, float]]:
        """Generate autofocus map for the acquisition region.

        Args:
            scan_bounds: Bounds dict with "x" and "y" min/max entries.
            dx_mm: X spacing used for estimating focus map grid.
            dy_mm: Y spacing used for estimating focus map grid.

        Returns:
            (x_center, y_center) of the scan region if map generated, else None.
        """
        if self._autofocus is None:
            _log.warning("Autofocus controller not available for focus map generation")
            return None
        if not scan_bounds:
            return None

        x_min, x_max = scan_bounds["x"]
        y_min, y_max = scan_bounds["y"]

        # Calculate scan dimensions and center
        x_span = abs(x_max - x_min)
        y_span = abs(y_max - y_min)
        x_center = (x_max + x_min) / 2
        y_center = (y_max + y_min) / 2

        # Determine grid size based on scan dimensions
        if x_span < dx_mm:
            fmap_nx = 2
            fmap_dx = dx_mm  # Force spacing for small scans
        else:
            fmap_nx = min(4, max(2, int(x_span / dx_mm) + 1))
            fmap_dx = max(dx_mm, x_span / (fmap_nx - 1))

        if y_span < dy_mm:
            fmap_ny = 2
            fmap_dy = dy_mm  # Force spacing for small scans
        else:
            fmap_ny = min(4, max(2, int(y_span / dy_mm) + 1))
            fmap_dy = max(dy_mm, y_span / (fmap_ny - 1))

        # Calculate starting corner position (top-left of the AF map grid)
        starting_x_mm = x_center - (fmap_nx - 1) * fmap_dx / 2
        starting_y_mm = y_center - (fmap_ny - 1) * fmap_dy / 2

        coord1 = (starting_x_mm, starting_y_mm)  # Starting corner
        coord2 = (
            starting_x_mm + (fmap_nx - 1) * fmap_dx,
            starting_y_mm,
        )  # X-axis corner
        coord3 = (
            starting_x_mm,
            starting_y_mm + (fmap_ny - 1) * fmap_dy,
        )  # Y-axis corner

        x_positions = [starting_x_mm + j * fmap_dx for j in range(fmap_nx)]
        y_positions = [starting_y_mm + i * fmap_dy for i in range(fmap_ny)]
        focus_coords = [coord1, coord2, coord3]
        for y in y_positions:
            for x in x_positions:
                candidate = (x, y)
                if candidate not in focus_coords:
                    focus_coords.append(candidate)

        _log.info("Generating AF Map: Nx=%s, Ny=%s", fmap_nx, fmap_ny)
        _log.info("Spacing: dx=%.3fmm, dy=%.3fmm", fmap_dx, fmap_dy)
        _log.info("Center:  x=(%.3fmm), y=(%.3fmm)", x_center, y_center)

        self._autofocus.sample_focus_map_points(focus_coords)
        if len(self._autofocus.focus_map_coords) >= 4:
            try:
                from squid.backend.managers.focus_map import FocusMap

                focus_map = FocusMap()
                focus_map.fit({"global": self._autofocus.focus_map_coords})
                self._autofocus.set_focus_map_surface(focus_map)
            except Exception:
                _log.exception("Failed to fit focus map surface; falling back to plane interpolation")
                self._autofocus.set_focus_map_surface(None)
        else:
            self._autofocus.set_focus_map_surface(None)
        self._autofocus.set_focus_map_use(True)
        return x_center, y_center

    def _perform_contrast_af(self, timeout_s: Optional[float] = None) -> bool:
        """
        Perform contrast-based autofocus.

        Args:
            timeout_s: Timeout for autofocus operation

        Returns:
            True if autofocus succeeded
        """
        if self._autofocus is None:
            _log.warning("Contrast autofocus controller not available")
            return False

        # Get and apply AF channel configuration
        if self._channel_config is not None and self._objectives is not None:
            config_af = self._channel_config.get_channel_configuration_by_name(
                self._objectives.current_objective,
                MULTIPOINT_AUTOFOCUS_CHANNEL,
            )
            if config_af is not None and self._apply_config_callback is not None:
                self._apply_config_callback(config_af)

        # Perform autofocus
        self._autofocus.autofocus()

        if not self._autofocus.wait_till_autofocus_has_completed(timeout_s=timeout_s):
            _log.warning("Autofocus timed out; continuing acquisition")
            return False

        return True

    def _perform_laser_af(self) -> bool:
        """
        Perform traditional per-FOV laser reflection autofocus.

        Note: Focus lock is handled separately at the worker level.
        This method is only called when focus lock is NOT active.

        Returns:
            True if autofocus succeeded
        """
        _log.info("Performing laser reflection AF")

        if self._laser_af is None:
            _log.warning("Laser autofocus controller not available")
            return False

        try:
            self._laser_af.move_to_target(0)
            return True
        except Exception as exc:
            _log.error(f"Laser AF failed: {exc}")
            return False

    # Focus lock helper methods for MultiPointWorker

    def wait_for_focus_lock(self, timeout_s: float = 5.0) -> bool:
        """
        Wait for continuous focus lock to achieve lock.

        Args:
            timeout_s: Maximum time to wait for lock

        Returns:
            True if locked, False if timeout or not active
        """
        if self._focus_lock is None or not self.is_focus_lock_active():
            return False

        return self._focus_lock.wait_for_lock(timeout_s=timeout_s)

    def pause_focus_lock(self) -> bool:
        """
        Pause focus lock for image capture.

        Call this before capturing images to prevent piezo corrections
        during exposure.

        Returns:
            True if paused (caller should resume), False if not active
        """
        if self._focus_lock is None or not self.is_focus_lock_active():
            return False

        try:
            self._focus_lock.pause()
            return True
        except Exception:
            _log.exception("Failed to pause focus lock")
            return False

    def resume_focus_lock(self) -> None:
        """
        Resume focus lock after image capture.

        Call this after capturing images to restart focus corrections.
        """
        if self._focus_lock is not None:
            try:
                self._focus_lock.resume()
            except Exception:
                _log.exception("Failed to resume focus lock")

    def increment_fov_count(self) -> None:
        """Increment the FOV counter for AF interval tracking."""
        self._af_fov_count += 1

    def reset_fov_count(self) -> None:
        """Reset the FOV counter."""
        self._af_fov_count = 0
