import os
from typing import Callable, Dict, List, Tuple

import cv2
import numpy as np
import pyqtgraph as pg
from qtpy.QtCore import Qt, QVariant, Signal
from qtpy.QtWidgets import QFrame, QPushButton, QVBoxLayout
from scipy.interpolate import RBFInterpolator, SmoothBivariateSpline

from control._def import (
    A1_X_MM,
    A1_X_PIXEL,
    A1_Y_MM,
    A1_Y_PIXEL,
    INVERTED_OBJECTIVE,
    IS_HCS,
    NUMBER_OF_SKIP,
    WELL_SIZE_MM,
    WELL_SPACING_MM,
)
from control.core.scan_coordinates import ScanCoordinates
import squid.abc
import squid.logging


class FocusMap:
    """Handles fitting and interpolation of slide surfaces through measured focus points"""

    def __init__(self, smoothing_factor=0.1):
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.smoothing_factor = smoothing_factor
        self.method = "spline"  # can be 'spline' or 'rbf' or 'constant'
        self.global_surface_fit = None
        self.global_method = None
        self.global_errors = None
        self.region_surface_fits = {}
        self.region_methods = {}
        self.region_errors = {}
        self.fit_by_region = False
        self.focus_points = {}
        self.is_fitted = False

    def generate_grid_coordinates(
        self, scanCoordinates: ScanCoordinates, rows: int = 4, cols: int = 4, add_margin: bool = False
    ) -> Dict[str, List[Tuple[float, float]]]:
        """
        Generate focus point grid coordinates for each scan region

        Args:
            scanCoordinates: ScanCoordinates instance containing regions
            rows: Number of rows in focus grid
            cols: Number of columns in focus grid
            add_margin: If True, adds margin to avoid points at region borders

        Returns:
            Dictionary with region_id as key and list of (x,y) coordinate tuples as value
        """
        if rows <= 0 or cols <= 0:
            raise ValueError("Number of rows and columns must be greater than 0")

        # Dictionary to store focus points by region
        focus_coords = {}

        # Generate focus points for each region
        for region_id, region_coords in scanCoordinates.region_fov_coordinates.items():
            # Get region bounds
            bounds = scanCoordinates.get_region_bounds(region_id)
            if not bounds:
                continue

            region_focus_coords = []
            x_min, x_max = bounds["min_x"], bounds["max_x"]
            y_min, y_max = bounds["min_y"], bounds["max_y"]

            # For add_margin we are using one more row and col, taking the middle points on the grid so that the
            # focus points are not located at the edges of the scaning grid.
            # TODO: set a value for margin from user input
            # Calculate x and y positions
            if add_margin:
                # With margin, divide the area into equal cells and use cell centers
                x_step = (x_max - x_min) / cols
                y_step = (y_max - y_min) / rows

                x_positions = [x_min + (j + 0.5) * x_step for j in range(cols)]
                y_positions = [y_min + (i + 0.5) * y_step for i in range(rows)]
            else:
                # Without margin, handle special cases for rows=1 or cols=1
                if rows == 1:
                    y_positions = [y_min + (y_max - y_min) / 2]  # Center point
                else:
                    y_step = (y_max - y_min) / (rows - 1)
                    y_positions = [y_min + i * y_step for i in range(rows)]

                if cols == 1:
                    x_positions = [x_min + (x_max - x_min) / 2]  # Center point
                else:
                    x_step = (x_max - x_min) / (cols - 1)
                    x_positions = [x_min + j * x_step for j in range(cols)]

            # Generate grid points by combining x and y positions
            for y in y_positions:
                for x in x_positions:
                    # Check if point is within region bounds
                    if scanCoordinates.validate_coordinates(x, y) and scanCoordinates.region_contains_coordinate(
                        region_id, x, y
                    ):
                        region_focus_coords.append((x, y))

            focus_coords[region_id] = region_focus_coords

        return focus_coords

    def set_method(self, method: str):
        """Set interpolation method

        Args:
            method (str): Either 'spline' or 'rbf' (Radial Basis Function)
        """
        if method not in ["spline", "rbf", "constant"]:
            raise ValueError("Method must be either 'spline' or 'rbf' or 'constant'")
        self.method = method
        self.is_fitted = False
        self.region_surface_fits = {}  # Reset region fits when method changes

    def set_fit_by_region(self, fit_by_region: bool):
        """Set if the surface fit should be done by region or globally

        Args:
            fit_by_region (bool): If True, fitting functions will be bounded by region
        """
        self.fit_by_region = fit_by_region

    def fit(self, points: Dict[str, List[Tuple[float, float, float]]]):
        """Fit surface through provided focus points

        Args:
            points: A dictionary with region_id as key and list of (x,y,z) tuples as value

        Returns:
            If by_region=False: tuple (mean_error, std_error) in mm
            If by_region=True: dict with region_id as key and (mean_error, std_error) as value
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
                    raise ValueError("Use 1 point for constant plane, or at least 4 points for surface fitting")
                self.region_surface_fits[region_id], self.region_methods[region_id], self.region_errors[region_id] = (
                    self._fit_surface(region_points)
                )
            if self.method == "constant":
                mean_error = 0
                std_error = 0
            else:
                all_errors = np.concatenate([errors for errors in self.region_errors.values()])
                mean_error = np.mean(all_errors)
                std_error = np.std(all_errors)
        else:
            all_points = []
            for region_points in points.values():
                all_points.extend(region_points)
            if len(all_points) < 4:
                raise ValueError("Use 1 point for constant plane, or at least 4 points for surface fitting")

            self.global_surface_fit, self.global_method, self.global_errors = self._fit_surface(all_points)
            mean_error = np.mean(self.global_errors)
            std_error = np.std(self.global_errors)

        self.is_fitted = True

        return mean_error, std_error

    def _fit_surface(self, points: List[Tuple[float, float, float]]) -> Tuple[Callable, str, np.ndarray]:
        """Fit surface through provided focus points for a specific region or globally

        Args:
            points (list): List of (x,y,z) tuples

        Returns:
            tuple: (surface_fit, method, errors)
        """
        points_array = np.array(points)
        x = points_array[:, 0]
        y = points_array[:, 1]
        z = points_array[:, 2]

        if len(points) == 1:
            # For single point, create a flat plane at that z-height
            if self.method != "constant":
                self._log.warning("One point can only be used for constant plane, falling back to constant")
            z_value = z[0]
            surface_fit = self._fit_constant_plane(z_value)
            method = "constant"

            self.is_fitted = True
            errors = None  # No error for a single point
        else:
            if self.method == "spline":
                try:
                    surface_fit = SmoothBivariateSpline(
                        x, y, z, kx=3, ky=3, s=self.smoothing_factor  # cubic spline in x  # cubic spline in y
                    )
                    method = self.method
                except Exception as e:
                    self._log.warning(f"Spline fitting failed: {str(e)}, falling back to RBF")
                    surface_fit = self._fit_rbf(x, y, z)
                    method = "rbf"
            elif self.method == "constant":
                self._log.warning("Constant method cannot be used for multiple points, falling back to RBF")
                surface_fit = self._fit_rbf(x, y, z)
                method = "rbf"
            else:
                surface_fit = self._fit_rbf(x, y, z)
                method = "rbf"

            self.is_fitted = True
            errors = self._calculate_fitting_errors(points, surface_fit, method)

        return surface_fit, method, errors

    def _fit_rbf(self, x, y, z):
        """Fit using Radial Basis Function interpolation"""
        xy = np.column_stack((x, y))
        return RBFInterpolator(xy, z, kernel="thin_plate_spline", epsilon=self.smoothing_factor)

    def _fit_constant_plane(self, z_value):
        """Create a constant height plane"""

        def constant_plane(x, y):
            if isinstance(x, np.ndarray):
                return np.full_like(x, z_value)
            else:
                return z_value

        return constant_plane

    def interpolate(self, x, y, region_id=None):
        """Get interpolated Z value at given (x,y) coordinates

        Args:
            x (float or array): X coordinate(s)
            y (float or array): Y coordinate(s)
            region_id: Region identifier for region-specific interpolation

        Returns:
            float or array: Interpolated Z value(s)
        """
        if not self.is_fitted and not self.region_surface_fits:
            raise RuntimeError("Must fit surface before interpolating")

        # If fit_by_region is True and region_id is provided, use region-specific surface
        if self.fit_by_region:
            if region_id is None or region_id not in self.region_surface_fits:
                raise ValueError(f"Region {region_id} not found")
            surface_fit = self.region_surface_fits[region_id]
            method = self.region_methods[region_id]
        else:
            surface_fit = self.global_surface_fit
            method = self.global_method

        return self._interpolate_helper(x, y, surface_fit, method)

    def _interpolate_helper(self, x, y, surface_fit, method):
        if np.isscalar(x) and np.isscalar(y):
            if method == "spline":
                return float(surface_fit.ev(x, y))
            elif method == "constant":
                return surface_fit(x, y)
            else:  # rbf
                return float(surface_fit([[x, y]]))
        else:
            x = np.asarray(x)
            y = np.asarray(y)
            if method == "spline":
                return surface_fit.ev(x, y)
            elif method == "constant":
                return surface_fit(x, y)
            else:  # rbf
                xy = np.column_stack((x.ravel(), y.ravel()))
                z = surface_fit(xy)
                return z.reshape(x.shape)

    def _calculate_fitting_errors(
        self, points: List[Tuple[float, float, float]], surface_fit: Callable, method: str
    ) -> np.ndarray:
        """Calculate absolute errors at measured points"""
        errors = []
        for x, y, z_measured in points:
            z_fit = self._interpolate_helper(x, y, surface_fit, method)
            errors.append(abs(z_fit - z_measured))
        return np.array(errors)

    def get_surface_grid(self, x_range, y_range, num_points=50, region_id=None):
        """Generate grid of interpolated Z values for visualization

        Args:
            x_range (tuple): (min_x, max_x)
            y_range (tuple): (min_y, max_y)
            num_points (int): Number of points per dimension
            region_id: Region identifier for region-specific visualization

        Returns:
            tuple: (X grid, Y grid, Z grid)
        """
        if not self.is_fitted:
            raise RuntimeError("Must fit surface before generating grid")

        x = np.linspace(x_range[0], x_range[1], num_points)
        y = np.linspace(y_range[0], y_range[1], num_points)
        X, Y = np.meshgrid(x, y)
        Z = self.interpolate(X, Y, region_id)

        return X, Y, Z


class NavigationViewer(QFrame):
    signal_coordinates_clicked = Signal(float, float)  # Will emit x_mm, y_mm when clicked

    def __init__(self, objectivestore, camera, sample="glass slide", invertX=False, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)
        self.sample = sample
        self.objectiveStore = objectivestore
        self.camera = camera
        self.well_size_mm = WELL_SIZE_MM
        self.well_spacing_mm = WELL_SPACING_MM
        self.number_of_skip = NUMBER_OF_SKIP
        self.a1_x_mm = A1_X_MM
        self.a1_y_mm = A1_Y_MM
        self.a1_x_pixel = A1_X_PIXEL
        self.a1_y_pixel = A1_Y_PIXEL
        self.location_update_threshold_mm = 0.2
        self.box_color = (255, 0, 0)
        self.box_line_thickness = 2
        self.x_mm = None
        self.y_mm = None
        self.image_paths = {
            "glass slide": "images/slide carrier_828x662.png",
            "4 glass slide": "images/4 slide carrier_1509x1010.png",
            "6 well plate": "images/6 well plate_1509x1010.png",
            "12 well plate": "images/12 well plate_1509x1010.png",
            "24 well plate": "images/24 well plate_1509x1010.png",
            "96 well plate": "images/96 well plate_1509x1010.png",
            "384 well plate": "images/384 well plate_1509x1010.png",
            "1536 well plate": "images/1536 well plate_1509x1010.png",
        }

        print("navigation viewer:", sample)
        self.init_ui(invertX)

        self.load_background_image(self.image_paths.get(sample, "images/4 slide carrier_1509x1010.png"))
        self.create_layers()
        self.update_display_properties(sample)
        # self.update_display()

    def init_ui(self, invertX):
        # interpret image data as row-major instead of col-major
        pg.setConfigOptions(imageAxisOrder="row-major")
        self.graphics_widget = pg.GraphicsLayoutWidget()
        self.graphics_widget.setBackground("w")

        self.view = self.graphics_widget.addViewBox(invertX=not INVERTED_OBJECTIVE, invertY=True)
        self.view.setAspectLocked(True)

        # Create Clear Coordinates button with seamless styling
        self.btn_clear_coordinates = QPushButton("Clear Scan Grid", self.graphics_widget)
        self.btn_clear_coordinates.clicked.connect(self.clear_slide)
        self.btn_clear_coordinates.setCursor(Qt.PointingHandCursor)
        # Position button
        self.btn_clear_coordinates.adjustSize()
        self._position_button()

        self.grid = QVBoxLayout()
        self.grid.addWidget(self.graphics_widget)
        self.setLayout(self.grid)
        # Connect double-click handler
        self.view.scene().sigMouseClicked.connect(self.handle_mouse_click)

    def _position_button(self):
        """Position the clear button at the bottom-right corner of the graphics widget"""
        margin = 10  # Margin from edges
        button_width = self.btn_clear_coordinates.sizeHint().width()
        button_height = self.btn_clear_coordinates.sizeHint().height()

        x = self.graphics_widget.width() - button_width - margin
        y = self.graphics_widget.height() - button_height - margin
        self.btn_clear_coordinates.move(x, y)
        self.btn_clear_coordinates.raise_()

    def resizeEvent(self, event):
        """Reposition button when widget is resized"""
        super().resizeEvent(event)
        if hasattr(self, "btn_clear_coordinates"):
            self._position_button()

    def load_background_image(self, image_path):
        self.view.clear()
        self.background_image = cv2.imread(image_path)
        if self.background_image is None:
            # raise ValueError(f"Failed to load image from {image_path}")
            self.background_image = cv2.imread(self.image_paths.get("glass slide"))

        if len(self.background_image.shape) == 2:  # Grayscale image
            self.background_image = cv2.cvtColor(self.background_image, cv2.COLOR_GRAY2RGBA)
        elif self.background_image.shape[2] == 3:  # BGR image
            self.background_image = cv2.cvtColor(self.background_image, cv2.COLOR_BGR2RGBA)
        elif self.background_image.shape[2] == 4:  # BGRA image
            self.background_image = cv2.cvtColor(self.background_image, cv2.COLOR_BGRA2RGBA)

        self.background_image_copy = self.background_image.copy()
        self.image_height, self.image_width = self.background_image.shape[:2]
        self.background_item = pg.ImageItem(self.background_image)
        self.view.addItem(self.background_item)

    def create_layers(self):
        self.scan_overlay = np.zeros((self.image_height, self.image_width, 4), dtype=np.uint8)
        self.fov_overlay = np.zeros((self.image_height, self.image_width, 4), dtype=np.uint8)
        self.focus_point_overlay = np.zeros((self.image_height, self.image_width, 4), dtype=np.uint8)

        self.scan_overlay_item = pg.ImageItem()
        self.fov_overlay_item = pg.ImageItem()
        self.focus_point_overlay_item = pg.ImageItem()

        self.view.addItem(self.scan_overlay_item)
        self.view.addItem(self.fov_overlay_item)
        self.view.addItem(self.focus_point_overlay_item)

        self.background_item.setZValue(-1)  # Background layer at the bottom
        self.scan_overlay_item.setZValue(0)  # Scan overlay in the middle
        self.focus_point_overlay_item.setZValue(1)  # # Focus points next
        self.fov_overlay_item.setZValue(2)  # FOV overlay on top

    def update_display_properties(self, sample):
        if sample == "glass slide":
            self.location_update_threshold_mm = 0.2
            self.mm_per_pixel = 0.1453
            self.origin_x_pixel = 200
            self.origin_y_pixel = 120
        elif sample == "4 glass slide":
            self.location_update_threshold_mm = 0.2
            self.mm_per_pixel = 0.084665
            self.origin_x_pixel = 50
            self.origin_y_pixel = 0
        else:
            self.location_update_threshold_mm = 0.05
            self.mm_per_pixel = 0.084665
            self.origin_x_pixel = self.a1_x_pixel - (self.a1_x_mm) / self.mm_per_pixel
            self.origin_y_pixel = self.a1_y_pixel - (self.a1_y_mm) / self.mm_per_pixel
        self.update_fov_size()

    def update_fov_size(self):
        self.fov_size_mm = self.camera.get_fov_size_mm() * self.objectiveStore.get_pixel_size_factor()

    def redraw_fov(self):
        self.clear_overlay()
        self.update_fov_size()
        self.draw_current_fov(self.x_mm, self.y_mm)

    def update_wellplate_settings(
        self,
        sample_format,
        a1_x_mm,
        a1_y_mm,
        a1_x_pixel,
        a1_y_pixel,
        well_size_mm,
        well_spacing_mm,
        number_of_skip,
        rows,
        cols,
    ):
        if isinstance(sample_format, QVariant):
            sample_format = sample_format.value()

        if sample_format == "glass slide":
            if IS_HCS:
                sample = "4 glass slide"
            else:
                sample = "glass slide"
        else:
            sample = sample_format

        self.sample = sample
        self.a1_x_mm = a1_x_mm
        self.a1_y_mm = a1_y_mm
        self.a1_x_pixel = a1_x_pixel
        self.a1_y_pixel = a1_y_pixel
        self.well_size_mm = well_size_mm
        self.well_spacing_mm = well_spacing_mm
        self.number_of_skip = number_of_skip
        self.rows = rows
        self.cols = cols

        # Try to find the image for the wellplate
        image_path = self.image_paths.get(sample)
        if image_path is None or not os.path.exists(image_path):
            # Look for a custom wellplate image
            custom_image_path = os.path.join("images", self.sample + ".png")
            self._log.info(custom_image_path)
            if os.path.exists(custom_image_path):
                image_path = custom_image_path
            else:
                self._log.warning(f"Image not found for {sample}. Using default image.")
                image_path = self.image_paths.get("glass slide")  # Use a default image

        self.load_background_image(image_path)
        self.create_layers()
        self.update_display_properties(sample)
        self.draw_current_fov(self.x_mm, self.y_mm)

    def draw_fov_current_location(self, pos: squid.abc.Pos):
        if not pos:
            if self.x_mm is None and self.y_mm is None:
                return
            self.draw_current_fov(self.x_mm, self.y_mm)
        else:
            x_mm = pos.x_mm
            y_mm = pos.y_mm
            self.draw_current_fov(x_mm, y_mm)
            self.x_mm = x_mm
            self.y_mm = y_mm

    def get_FOV_pixel_coordinates(self, x_mm, y_mm):
        if self.sample == "glass slide":
            current_FOV_top_left = (
                round(self.origin_x_pixel + x_mm / self.mm_per_pixel - self.fov_size_mm / 2 / self.mm_per_pixel),
                round(
                    self.image_height
                    - (self.origin_y_pixel + y_mm / self.mm_per_pixel)
                    - self.fov_size_mm / 2 / self.mm_per_pixel
                ),
            )
            current_FOV_bottom_right = (
                round(self.origin_x_pixel + x_mm / self.mm_per_pixel + self.fov_size_mm / 2 / self.mm_per_pixel),
                round(
                    self.image_height
                    - (self.origin_y_pixel + y_mm / self.mm_per_pixel)
                    + self.fov_size_mm / 2 / self.mm_per_pixel
                ),
            )
        else:
            current_FOV_top_left = (
                round(self.origin_x_pixel + x_mm / self.mm_per_pixel - self.fov_size_mm / 2 / self.mm_per_pixel),
                round((self.origin_y_pixel + y_mm / self.mm_per_pixel) - self.fov_size_mm / 2 / self.mm_per_pixel),
            )
            current_FOV_bottom_right = (
                round(self.origin_x_pixel + x_mm / self.mm_per_pixel + self.fov_size_mm / 2 / self.mm_per_pixel),
                round((self.origin_y_pixel + y_mm / self.mm_per_pixel) + self.fov_size_mm / 2 / self.mm_per_pixel),
            )
        return current_FOV_top_left, current_FOV_bottom_right

    def draw_current_fov(self, x_mm, y_mm):
        self.fov_overlay.fill(0)
        current_FOV_top_left, current_FOV_bottom_right = self.get_FOV_pixel_coordinates(x_mm, y_mm)
        cv2.rectangle(
            self.fov_overlay, current_FOV_top_left, current_FOV_bottom_right, (255, 0, 0, 255), self.box_line_thickness
        )
        self.fov_overlay_item.setImage(self.fov_overlay)

    def register_fov(self, x_mm, y_mm):
        color = (0, 0, 255, 255)  # Blue RGBA
        current_FOV_top_left, current_FOV_bottom_right = self.get_FOV_pixel_coordinates(x_mm, y_mm)
        cv2.rectangle(
            self.background_image, current_FOV_top_left, current_FOV_bottom_right, color, self.box_line_thickness
        )
        self.background_item.setImage(self.background_image)

    def register_fovs_to_image(self, fov_list):
        """
        Register FOVs to image with single display update.

        Args:
            fov_list: List of tuples (x_mm, y_mm) or (x_mm, y_mm, z_mm), or list of FovCenter objects
        """
        if not fov_list:
            return

        color = (252, 174, 30, 128)  # Yellow RGBA
        for fov in fov_list:
            # Handle tuple (2D or 3D) and FovCenter object formats
            if isinstance(fov, tuple):
                x_mm = fov[0]
                y_mm = fov[1]
            else:
                x_mm = fov.x_mm
                y_mm = fov.y_mm
            current_FOV_top_left, current_FOV_bottom_right = self.get_FOV_pixel_coordinates(x_mm, y_mm)
            cv2.rectangle(
                self.scan_overlay, current_FOV_top_left, current_FOV_bottom_right, color, self.box_line_thickness
            )
        # Single update after all rectangles are drawn
        self.scan_overlay_item.setImage(self.scan_overlay)

    def deregister_fovs_from_image(self, fov_list):
        """
        Deregister FOVs from image with single display update.

        Args:
            fov_list: List of tuples (x_mm, y_mm) or (x_mm, y_mm, z_mm), or list of FovCenter objects
        """
        if not fov_list:
            return

        for fov in fov_list:
            # Handle tuple (2D or 3D) and FovCenter object formats
            if isinstance(fov, tuple):
                x_mm = fov[0]
                y_mm = fov[1]
            else:
                x_mm = fov.x_mm
                y_mm = fov.y_mm
            current_FOV_top_left, current_FOV_bottom_right = self.get_FOV_pixel_coordinates(x_mm, y_mm)
            cv2.rectangle(
                self.scan_overlay, current_FOV_top_left, current_FOV_bottom_right, (0, 0, 0, 0), self.box_line_thickness
            )
        # Single update after all rectangles are cleared
        self.scan_overlay_item.setImage(self.scan_overlay)

    def register_focus_point(self, x_mm, y_mm):
        """Draw focus point marker as filled circle centered on the FOV"""
        color = (0, 255, 0, 255)  # Green RGBA
        # Get FOV corner coordinates, then calculate FOV center pixel coordinates
        current_FOV_top_left, current_FOV_bottom_right = self.get_FOV_pixel_coordinates(x_mm, y_mm)
        center_x = (current_FOV_top_left[0] + current_FOV_bottom_right[0]) // 2
        center_y = (current_FOV_top_left[1] + current_FOV_bottom_right[1]) // 2
        # Draw a filled circle at the center
        radius = 5  # Radius of circle in pixels
        cv2.circle(self.focus_point_overlay, (center_x, center_y), radius, color, -1)  # -1 thickness means filled
        self.focus_point_overlay_item.setImage(self.focus_point_overlay)

    def clear_focus_points(self):
        """Clear just the focus point overlay"""
        self.focus_point_overlay = np.zeros((self.image_height, self.image_width, 4), dtype=np.uint8)
        self.focus_point_overlay_item.setImage(self.focus_point_overlay)

    def clear_slide(self):
        self.background_image = self.background_image_copy.copy()
        self.background_item.setImage(self.background_image)
        self.draw_current_fov(self.x_mm, self.y_mm)

    def clear_overlay(self):
        self.scan_overlay.fill(0)
        self.scan_overlay_item.setImage(self.scan_overlay)
        self.focus_point_overlay.fill(0)
        self.focus_point_overlay_item.setImage(self.focus_point_overlay)

    def handle_mouse_click(self, evt):
        if not evt.double():
            return
        try:
            # Get mouse position in image coordinates (independent of zoom)
            mouse_point = self.background_item.mapFromScene(evt.scenePos())

            # Subtract origin offset before converting to mm
            x_mm = (mouse_point.x() - self.origin_x_pixel) * self.mm_per_pixel
            y_mm = (mouse_point.y() - self.origin_y_pixel) * self.mm_per_pixel

            self._log.debug(f"Got double click at (x_mm, y_mm) = {x_mm, y_mm}")
            self.signal_coordinates_clicked.emit(x_mm, y_mm)

        except Exception as e:
            print(f"Error processing navigation click: {e}")
            return
