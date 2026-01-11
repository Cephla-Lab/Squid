"""
Focus map generation and autofocus execution for multipoint acquisitions.

This module provides:
- FocusMapGenerator: Focus map generation with save/restore context management
- AutofocusExecutor: Autofocus decision logic and execution
- FocusMapConfig: Configuration for focus map generation

These classes encapsulate focus-related logic previously embedded in
MultiPointController and MultiPointWorker.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple, TYPE_CHECKING

import squid.core.logging
from _def import Acquisition, MULTIPOINT_AUTOFOCUS_CHANNEL

if TYPE_CHECKING:
    from squid.backend.controllers.autofocus import AutoFocusController, LaserAutofocusController
    from squid.backend.services import StageService
    from squid.backend.managers import ChannelConfigurationManager, ObjectiveStore, ScanCoordinates
    from squid.core.utils.config_utils import ChannelMode


_log = squid.core.logging.get_logger(__name__)


@dataclass
class FocusMapConfig:
    """Configuration for focus map generation."""

    delta_x_mm: float
    delta_y_mm: float
    max_grid_points: int = 4
    min_grid_points: int = 2


@dataclass
class FocusMapState:
    """State of focus map before/after generation."""

    coords: List[Tuple[float, float, float]]
    use_focus_map: bool


class FocusMapGenerator:
    """
    Generates focus maps for multipoint acquisitions.

    Handles:
    - Grid-based focus map generation from scan bounds
    - Save/restore of existing focus map state
    - Focus surface interpolation for z-positions

    Usage:
        generator = FocusMapGenerator(
            autofocus_controller=af_controller,
            stage_service=stage,
            config=FocusMapConfig(delta_x_mm=1.0, delta_y_mm=1.0),
        )

        # Generate focus map from scan bounds
        with generator.focus_map_context():
            generator.generate_from_bounds(bounds)

        # Or use existing focus surface
        generator.interpolate_z_positions(scan_coordinates, focus_map)
    """

    def __init__(
        self,
        autofocus_controller: "AutoFocusController",
        stage_service: "StageService",
        config: FocusMapConfig,
    ):
        """
        Initialize the focus map generator.

        Args:
            autofocus_controller: AutoFocus controller for focus map operations
            stage_service: Stage service for movement during generation
            config: Focus map configuration
        """
        self._autofocus = autofocus_controller
        self._stage = stage_service
        self._config = config
        self._saved_state: Optional[FocusMapState] = None

    @contextmanager
    def focus_map_context(self) -> Iterator[None]:
        """
        Context manager that saves and restores focus map state.

        Saves the current focus map state on entry, and restores it
        on exit (whether by normal completion or exception).

        Usage:
            with generator.focus_map_context():
                generator.generate_from_bounds(bounds)
                # ... use generated focus map ...
            # Original focus map is restored here
        """
        self._save_focus_map_state()
        try:
            yield
        finally:
            self._restore_focus_map_state()

    def generate_from_bounds(
        self,
        bounds: Dict[str, Tuple[float, float]],
        return_to_center: bool = True,
    ) -> bool:
        """
        Generate a focus map from scan bounds.

        Args:
            bounds: Dictionary with "x" and "y" keys containing (min, max) tuples
            return_to_center: Whether to return stage to center after generation

        Returns:
            True if focus map was generated successfully
        """
        if not bounds:
            _log.error("Cannot generate focus map: no bounds provided")
            return False

        x_min, x_max = bounds["x"]
        y_min, y_max = bounds["y"]

        # Calculate scan dimensions and center
        x_span = abs(x_max - x_min)
        y_span = abs(y_max - y_min)
        x_center = (x_max + x_min) / 2
        y_center = (y_max + y_min) / 2

        # Calculate grid parameters
        grid_nx, grid_dx = self._calculate_grid_params(
            x_span, self._config.delta_x_mm
        )
        grid_ny, grid_dy = self._calculate_grid_params(
            y_span, self._config.delta_y_mm
        )

        # Calculate starting corner position (top-left of the AF map grid)
        starting_x_mm = x_center - (grid_nx - 1) * grid_dx / 2
        starting_y_mm = y_center - (grid_ny - 1) * grid_dy / 2

        _log.info(f"Generating AF Map: Nx={grid_nx}, Ny={grid_ny}")
        _log.info(f"Spacing: dx={grid_dx:.3f}mm, dy={grid_dy:.3f}mm")
        _log.info(f"Center: x={x_center:.3f}mm, y={y_center:.3f}mm")

        # Define grid corners for AF map
        coord1 = (starting_x_mm, starting_y_mm)  # Starting corner
        coord2 = (
            starting_x_mm + (grid_nx - 1) * grid_dx,
            starting_y_mm,
        )  # X-axis corner
        coord3 = (
            starting_x_mm,
            starting_y_mm + (grid_ny - 1) * grid_dy,
        )  # Y-axis corner

        try:
            # Generate and enable the AF map
            self._autofocus.gen_focus_map(coord1, coord2, coord3)
            self._autofocus.set_focus_map_use(True)

            # Return to center position
            if return_to_center:
                self._stage.move_x_to(x_center)
                self._stage.move_y_to(y_center)

            return True

        except ValueError as exc:
            _log.error(f"Invalid coordinates for autofocus plane: {exc}")
            return False

    def interpolate_z_positions(
        self,
        scan_coordinates: "ScanCoordinates",
        focus_map: Any,
    ) -> None:
        """
        Interpolate z-positions for all FOVs using a focus surface.

        Args:
            scan_coordinates: Scan coordinates to update
            focus_map: Focus map/surface with interpolate method
        """
        if focus_map is None:
            return

        _log.info("Using focus surface for Z interpolation")

        for region_id in scan_coordinates.region_fov_coordinates.keys():
            region_fov_coords = scan_coordinates.region_fov_coordinates.get(region_id, [])

            for i, coord in enumerate(region_fov_coords):
                x, y = coord[0], coord[1]
                try:
                    z = focus_map.interpolate(x, y, region_id)
                    # Update coordinate with interpolated z
                    region_fov_coords[i] = (x, y, z)
                    scan_coordinates.update_fov_z_level(region_id, i, z)
                except Exception as exc:
                    _log.warning(f"Failed to interpolate z for ({x}, {y}): {exc}")

    def clear_focus_map(self) -> None:
        """Clear the current focus map."""
        self._autofocus.clear_focus_map()

    def _calculate_grid_params(
        self,
        span: float,
        delta: float,
    ) -> Tuple[int, float]:
        """
        Calculate grid parameters for focus map.

        Args:
            span: Total span in mm
            delta: Step size in mm

        Returns:
            Tuple of (num_points, spacing)
        """
        min_points = self._config.min_grid_points
        max_points = self._config.max_grid_points

        if span < delta:
            return min_points, delta

        num_points = min(max_points, max(min_points, int(span / delta) + 1))
        spacing = max(delta, span / (num_points - 1))
        return num_points, spacing

    def _save_focus_map_state(self) -> None:
        """Save current focus map state."""
        coords = []
        for x, y, z in self._autofocus.focus_map_coords:
            coords.append((x, y, z))

        self._saved_state = FocusMapState(
            coords=coords,
            use_focus_map=self._autofocus.use_focus_map,
        )
        _log.debug(f"Saved focus map state: {len(coords)} coordinates")

    def _restore_focus_map_state(self) -> None:
        """Restore previously saved focus map state."""
        if self._saved_state is None:
            return

        self._autofocus.clear_focus_map()
        for x, y, z in self._saved_state.coords:
            self._autofocus.focus_map_coords.append((x, y, z))
        self._autofocus.use_focus_map = self._saved_state.use_focus_map

        _log.debug(f"Restored focus map state: {len(self._saved_state.coords)} coordinates")
        self._saved_state = None


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

    def should_perform_autofocus(self) -> bool:
        """
        Check if autofocus should be performed based on current state.

        Returns:
            True if autofocus should be performed
        """
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
        Perform laser reflection autofocus.

        Returns:
            True if autofocus succeeded
        """
        _log.info("Performing laser reflection AF")

        # Check if focus lock is active
        if (
            self._focus_lock is not None
            and getattr(self._focus_lock, "mode", "off") != "off"
            and getattr(self._focus_lock, "is_running", False)
        ):
            return self._focus_lock.wait_for_lock(timeout_s=5.0)

        # Use laser AF controller
        if self._laser_af is None:
            _log.warning("Laser autofocus controller not available")
            return False

        try:
            self._laser_af.move_to_target(0)
            return True
        except Exception as exc:
            _log.error(f"Laser AF failed: {exc}")
            return False

    def increment_fov_count(self) -> None:
        """Increment the FOV counter for AF interval tracking."""
        self._af_fov_count += 1

    def reset_fov_count(self) -> None:
        """Reset the FOV counter."""
        self._af_fov_count = 0
