from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import numpy as np
from scipy.interpolate import RBFInterpolator, SmoothBivariateSpline

import squid.core.logging


class FocusMap:
    """Handles fitting and interpolation of slide surfaces through measured focus points"""

    def __init__(self, smoothing_factor: float = 0.1) -> None:
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self.smoothing_factor: float = smoothing_factor
        self.method: str = "spline"  # can be 'spline' or 'rbf' or 'constant'
        self.global_surface_fit: Optional[
            Union[SmoothBivariateSpline, RBFInterpolator, Callable]
        ] = None
        self.global_method: Optional[str] = None
        self.global_errors: Optional[np.ndarray] = None
        self.region_surface_fits: Dict[
            str, Union[SmoothBivariateSpline, RBFInterpolator, Callable]
        ] = {}
        self.region_methods: Dict[str, str] = {}
        self.region_errors: Dict[str, np.ndarray] = {}
        self.fit_by_region: bool = False
        self.focus_points: Dict[str, List[Tuple[float, float, float]]] = {}
        self.is_fitted: bool = False

    def generate_grid_coordinates(
        self,
        region_fov_coordinates: Dict[str, List[Tuple[float, ...]]],
        rows: int = 4,
        cols: int = 4,
        add_margin: bool = False,
    ) -> Dict[str, List[Tuple[float, float]]]:
        """Generate focus point grid coordinates for each scan region.

        This method is intentionally decoupled from ScanCoordinates so UI widgets can
        operate from snapshots (`ScanCoordinatesSnapshot`) instead of holding backend
        objects directly.

        Args:
            region_fov_coordinates: Mapping of region_id -> list of (x_mm, y_mm[, z_mm]) tuples.
            rows: Number of rows in focus grid.
            cols: Number of columns in focus grid.
            add_margin: If True, uses cell centers to avoid borders.

        Returns:
            Dictionary with region_id as key and list of (x_mm, y_mm) tuples as value.
        """
        if rows <= 0 or cols <= 0:
            raise ValueError("Number of rows and columns must be greater than 0")

        focus_coords: Dict[str, List[Tuple[float, float]]] = {}

        for region_id, region_coords in region_fov_coordinates.items():
            if not region_coords:
                continue

            region_focus_coords: List[Tuple[float, float]] = []
            xs = [float(c[0]) for c in region_coords]
            ys = [float(c[1]) for c in region_coords]
            x_min, x_max = min(xs), max(xs)
            y_min, y_max = min(ys), max(ys)

            if add_margin:
                x_step = (x_max - x_min) / cols
                y_step = (y_max - y_min) / rows
                x_positions = [x_min + (j + 0.5) * x_step for j in range(cols)]
                y_positions = [y_min + (i + 0.5) * y_step for i in range(rows)]
            else:
                if rows == 1:
                    y_positions = [y_min + (y_max - y_min) / 2]
                else:
                    y_step = (y_max - y_min) / (rows - 1)
                    y_positions = [y_min + i * y_step for i in range(rows)]

                if cols == 1:
                    x_positions = [x_min + (x_max - x_min) / 2]
                else:
                    x_step = (x_max - x_min) / (cols - 1)
                    x_positions = [x_min + j * x_step for j in range(cols)]

            for y in y_positions:
                for x in x_positions:
                    region_focus_coords.append((float(x), float(y)))

            focus_coords[region_id] = region_focus_coords

        return focus_coords

    def set_method(self, method: str) -> None:
        """Set interpolation method.

        Args:
            method: Either 'spline' or 'rbf' (Radial Basis Function) or 'constant'
        """
        if method not in ["spline", "rbf", "constant"]:
            raise ValueError("Method must be either 'spline' or 'rbf' or 'constant'")
        self.method = method
        self.is_fitted = False
        self.region_surface_fits = {}

    def set_fit_by_region(self, fit_by_region: bool) -> None:
        """Set if the surface fit should be done by region or globally."""
        self.fit_by_region = fit_by_region

    def fit(self, points: Dict[str, List[Tuple[float, float, float]]]) -> Tuple[float, float]:
        """Fit surface through provided focus points.

        Args:
            points: region_id -> list of (x,y,z) tuples.

        Returns:
            (mean_error_mm, std_error_mm)
        """
        if not hasattr(self, "fit_by_region"):
            raise ValueError("fit_by_region must be set before fitting")

        self.focus_points = points

        if self.fit_by_region:
            self.region_surface_fits = {}
            self.region_methods = {}
            self.region_errors = {}
            for region_id, region_points in points.items():
                if len(region_points) in [0, 2, 3]:
                    raise ValueError(
                        "Use 1 point for constant plane, or at least 4 points for surface fitting"
                    )
                (
                    self.region_surface_fits[region_id],
                    self.region_methods[region_id],
                    self.region_errors[region_id],
                ) = self._fit_surface(region_points)
            if self.method == "constant":
                mean_error = 0.0
                std_error = 0.0
            else:
                all_errors = np.concatenate([errors for errors in self.region_errors.values()])
                mean_error = float(np.mean(all_errors))
                std_error = float(np.std(all_errors))
        else:
            all_points: List[Tuple[float, float, float]] = []
            for region_points in points.values():
                all_points.extend(region_points)
            if len(all_points) < 4:
                raise ValueError(
                    "Use 1 point for constant plane, or at least 4 points for surface fitting"
                )
            self.global_surface_fit, self.global_method, self.global_errors = self._fit_surface(all_points)
            mean_error = float(np.mean(self.global_errors))
            std_error = float(np.std(self.global_errors))

        self.is_fitted = True
        return mean_error, std_error

    def _fit_surface(
        self, points: List[Tuple[float, float, float]]
    ) -> Tuple[Union[SmoothBivariateSpline, RBFInterpolator, Callable], str, Optional[np.ndarray]]:
        points_array = np.array(points)
        x = points_array[:, 0]
        y = points_array[:, 1]
        z = points_array[:, 2]

        if len(points) == 1:
            if self.method != "constant":
                self._log.warning("One point can only be used for constant plane, falling back to constant")
            z_value = z[0]
            surface_fit = self._fit_constant_plane(z_value)
            method = "constant"
            self.is_fitted = True
            errors = None
        else:
            if self.method == "spline":
                surface_fit = SmoothBivariateSpline(x, y, z, s=self.smoothing_factor)
                method = "spline"
            elif self.method == "rbf":
                xy = np.column_stack((x, y))
                surface_fit = RBFInterpolator(xy, z)
                method = "rbf"
            elif self.method == "constant":
                z_value = float(np.mean(z))
                surface_fit = self._fit_constant_plane(z_value)
                method = "constant"
            else:  # pragma: no cover
                raise ValueError(f"Unknown method: {self.method}")

            errors = self._calculate_errors(points, surface_fit, method)

        return surface_fit, method, errors

    @staticmethod
    def _fit_constant_plane(z_value: float) -> Callable:
        return lambda *_args, **_kwargs: float(z_value)

    def interpolate(self, x: np.ndarray | float, y: np.ndarray | float, region_id: Optional[str] = None) -> Any:
        if not self.is_fitted:
            raise RuntimeError("Must fit surface before interpolating")

        if self.fit_by_region and region_id is not None:
            if region_id not in self.region_surface_fits:
                raise ValueError(f"Region {region_id} not found in region fits")
            return self._interpolate_helper(
                x, y, self.region_surface_fits[region_id], self.region_methods[region_id]
            )

        if self.global_surface_fit is None or self.global_method is None:
            raise RuntimeError("Global surface fit not available")
        return self._interpolate_helper(x, y, self.global_surface_fit, self.global_method)

    def _interpolate_helper(
        self,
        x: np.ndarray | float,
        y: np.ndarray | float,
        surface_fit: Union[SmoothBivariateSpline, RBFInterpolator, Callable],
        method: str,
    ):
        if method == "spline":
            return surface_fit(x, y, grid=False)  # type: ignore[misc]
        if method == "rbf":
            xy = np.column_stack((np.array(x).flatten(), np.array(y).flatten()))
            z = surface_fit(xy)  # type: ignore[misc]
            if isinstance(x, np.ndarray):
                return z.reshape(np.array(x).shape)
            return float(z[0])
        if method == "constant":
            return surface_fit(x, y)  # type: ignore[misc]
        raise ValueError(f"Unknown method: {method}")

    def _calculate_errors(
        self,
        points: List[Tuple[float, float, float]],
        surface_fit: Union[SmoothBivariateSpline, RBFInterpolator, Callable],
        method: str,
    ) -> np.ndarray:
        if method == "constant":
            return np.zeros(len(points))

        errors = []
        for x, y, z_measured in points:
            z_fit = self._interpolate_helper(x, y, surface_fit, method)
            errors.append(abs(float(z_fit) - float(z_measured)))
        return np.array(errors)

    def get_surface_grid(
        self,
        x_range: Tuple[float, float],
        y_range: Tuple[float, float],
        num_points: int = 50,
        region_id: Optional[str] = None,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        if not self.is_fitted:
            raise RuntimeError("Must fit surface before generating grid")

        x = np.linspace(x_range[0], x_range[1], num_points)
        y = np.linspace(y_range[0], y_range[1], num_points)
        X, Y = np.meshgrid(x, y)
        Z = self.interpolate(X, Y, region_id)
        return X, Y, Z
