# set QT_API environment variable
import os
import time
from queue import Queue
from threading import Lock, Thread
from typing import Optional, Tuple, Any, List

import cv2
import numpy as np

# qt libraries
os.environ["QT_API"] = "pyqt5"
import pyqtgraph as pg
import scipy.ndimage
from qtpy.QtCore import QObject, Qt, QTimer, Signal
from qtpy.QtGui import QCursor
from qtpy.QtWidgets import (
    QDesktopWidget,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from control._def import ENABLE_TRACKING, SpotDetectionMode
from control.core.configuration import ContrastManager
from control import utils
from control.core.display.live_controller import LiveController
import squid.logging


class ImageDisplay(QObject):
    image_to_display = Signal(np.ndarray)

    def __init__(self) -> None:
        QObject.__init__(self)
        self.queue: Queue = Queue(10)  # max 10 items in the queue
        self.image_lock: Lock = Lock()
        self.stop_signal_received: bool = False
        self.thread: Thread = Thread(target=self.process_queue, daemon=True)
        self.thread.start()

    def process_queue(self) -> None:
        while True:
            # stop the thread if stop signal is received
            if self.stop_signal_received:
                return
            # process the queue
            try:
                [image, frame_ID, timestamp] = self.queue.get(timeout=0.1)
                self.image_lock.acquire(True)
                self.image_to_display.emit(image)
                self.image_lock.release()
                self.queue.task_done()
            except Exception:
                pass
            time.sleep(0)

    # def enqueue(self,image,frame_ID,timestamp):
    def enqueue(self, image: np.ndarray) -> None:
        try:
            self.queue.put_nowait([image, None, None])
            # when using self.queue.put(str_) instead of try + nowait, program can be slowed down despite multithreading because of the block and the GIL
            pass
        except Exception:
            print("imageDisplay queue is full, image discarded")

    def emit_directly(self, image: np.ndarray) -> None:
        self.image_to_display.emit(image)

    def close(self) -> None:
        self.queue.join()
        self.stop_signal_received = True
        self.thread.join()


class ImageDisplayWindow(QMainWindow):
    image_click_coordinates = Signal(int, int, int, int)

    def __init__(
        self,
        liveController: Optional[LiveController] = None,
        contrastManager: Optional[ContrastManager] = None,
        window_title: str = "",
        show_LUT: bool = False,
        autoLevels: bool = False,
    ) -> None:
        super().__init__()
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.liveController: Optional[LiveController] = liveController
        self.contrastManager: Optional[ContrastManager] = contrastManager
        self.setWindowTitle(window_title)
        self.setWindowFlags(self.windowFlags() | Qt.CustomizeWindowHint)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowCloseButtonHint)
        self.widget: QWidget = QWidget()
        self.show_LUT: bool = show_LUT
        self.autoLevels: bool = autoLevels

        self.first_image: bool = True

        # Store last valid cursor position
        self.last_valid_x: int = 0
        self.last_valid_y: int = 0
        self.last_valid_value: Any = 0
        self.has_valid_position: bool = False

        # Line profiler state
        self.line_roi: Optional[Any] = None
        self.is_drawing_line: bool = False
        self.line_start_pos: Optional[Tuple[float, float]] = None
        self.line_end_pos: Optional[Tuple[float, float]] = None
        self.drawing_cursor: QCursor = QCursor(
            Qt.CrossCursor
        )  # Cross cursor for drawing mode
        self.normal_cursor: QCursor = QCursor(Qt.ArrowCursor)  # Normal cursor
        self.preview_line: Optional[Any] = None
        self.start_point_marker: Optional[Any] = None

        # Spot tracking state (for laser autofocus camera)
        self._spot_tracking_enabled: bool = False
        self._spot_tracking_params: Optional[dict] = None
        self._spot_tracking_mode: SpotDetectionMode = SpotDetectionMode.SINGLE
        self._spot_tracking_filter_sigma: Optional[int] = None
        self._last_spot_x: Optional[float] = None
        self._last_spot_y: Optional[float] = None

        # Create main layout
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Create status bar widget
        status_widget: QWidget = QWidget()
        status_layout: QHBoxLayout = QHBoxLayout()
        status_layout.setContentsMargins(5, 2, 5, 2)
        status_layout.setSpacing(10)

        # Create labels with minimum width to prevent jumping
        self.cursor_position_label: QLabel = QLabel()
        self.cursor_position_label.setMinimumWidth(150)
        self.pixel_value_label: QLabel = QLabel()
        self.pixel_value_label.setMinimumWidth(150)
        self.stage_position_label: QLabel = QLabel()
        self.stage_position_label.setMinimumWidth(200)
        self.piezo_position_label: QLabel = QLabel()
        self.piezo_position_label.setMinimumWidth(150)

        # Add line profiler toggle button
        self.btn_line_profiler: QPushButton = QPushButton("Line Profiler")
        self.btn_line_profiler.setCheckable(True)
        self.btn_line_profiler.setChecked(False)
        self.btn_line_profiler.setEnabled(False)
        self.btn_line_profiler.clicked.connect(self.toggle_line_profiler)

        # Add well selector toggle button
        self.btn_well_selector: QPushButton = QPushButton("Show Well Selector")
        self.btn_well_selector.setCheckable(False)

        # Add labels to status layout with spacing
        status_layout.addWidget(self.cursor_position_label)
        status_layout.addWidget(QLabel(" | "))  # Add separator
        status_layout.addWidget(self.pixel_value_label)
        status_layout.addWidget(QLabel(" | "))  # Add separator
        status_layout.addWidget(self.stage_position_label)
        status_layout.addWidget(QLabel(" | "))  # Add separator
        status_layout.addWidget(self.piezo_position_label)
        status_layout.addStretch()  # Push labels to the left
        status_layout.addWidget(self.btn_well_selector)  # Add well selector button
        status_layout.addWidget(QLabel(" | "))  # Add separator
        status_layout.addWidget(self.btn_line_profiler)  # Add line profiler button

        status_widget.setLayout(status_layout)

        # Initialize labels with default text
        self.cursor_position_label.setText("Position: (0, 0)")
        self.pixel_value_label.setText("Value: N/A")
        self.stage_position_label.setText("Stage: X: 0.00 mm, Y: 0.00 mm, Z: 0.00 mm")
        self.piezo_position_label.setText("Piezo: N/A")

        # interpret image data as row-major instead of col-major
        pg.setConfigOptions(imageAxisOrder="row-major")

        # Create a container widget for the image display
        self.image_container: QWidget = QWidget()
        image_layout: QVBoxLayout = QVBoxLayout()
        image_layout.setContentsMargins(0, 0, 0, 0)
        image_layout.setSpacing(0)

        self.graphics_widget: Any = pg.GraphicsLayoutWidget()
        self.graphics_widget.view = self.graphics_widget.addViewBox()
        self.graphics_widget.view.invertY()

        ## lock the aspect ratio so pixels are always square
        self.graphics_widget.view.setAspectLocked(True)

        ## Create image item
        if self.show_LUT:
            self.graphics_widget.view = pg.ImageView()
            self.graphics_widget.img = self.graphics_widget.view.getImageItem()
            self.graphics_widget.img.setBorder("w")
            self.graphics_widget.view.ui.roiBtn.hide()
            self.graphics_widget.view.ui.menuBtn.hide()
            self.LUTWidget: Any = self.graphics_widget.view.getHistogramWidget()
            self.LUTWidget.region.sigRegionChanged.connect(self.update_contrast_limits)
            self.LUTWidget.region.sigRegionChangeFinished.connect(
                self.update_contrast_limits
            )
        else:
            self.graphics_widget.img = pg.ImageItem(border="w")
            self.graphics_widget.view.addItem(self.graphics_widget.img)

        ## Create ROI
        self.roi_pos: Any = (500, 500)
        self.roi_size: Any = (500, 500)
        self.ROI: Any = pg.ROI(
            self.roi_pos, self.roi_size, scaleSnap=True, translateSnap=True
        )
        self.ROI.setZValue(10)
        self.ROI.addScaleHandle((0, 0), (1, 1))
        self.ROI.addScaleHandle((1, 1), (0, 0))
        self.graphics_widget.view.addItem(self.ROI)
        self.ROI.hide()
        self.ROI.sigRegionChanged.connect(self.update_ROI)
        self.roi_pos = self.ROI.pos()
        self.roi_size = self.ROI.size()

        ## Variables for annotating images
        self.draw_rectangle: bool = False
        self.ptRect1: Optional[Tuple[int, int]] = None
        self.ptRect2: Optional[Tuple[int, int]] = None
        self.DrawCirc: bool = False
        self.centroid: Optional[Any] = None
        self.image_offset: np.ndarray = np.array([0, 0])

        # Add image widget to container
        if self.show_LUT:
            image_layout.addWidget(self.graphics_widget.view)
        else:
            image_layout.addWidget(self.graphics_widget)
        self.image_container.setLayout(image_layout)

        # Create line profiler widget
        self.line_profiler_widget: Any = pg.GraphicsLayoutWidget()
        self.line_profiler_plot: Any = self.line_profiler_widget.addPlot()
        self.line_profiler_plot.setLabel("left", "Intensity")
        self.line_profiler_plot.setLabel("bottom", "Position")
        self.line_profiler_widget.hide()  # Initially hidden
        self.line_profiler_manual_range: bool = (
            False  # Flag to track if y-range is manually set
        )

        # Create splitter
        self.splitter = QSplitter(Qt.Vertical)
        self.splitter.addWidget(self.image_container)
        self.splitter.addWidget(self.line_profiler_widget)
        self.splitter.setStretchFactor(0, 1)  # Image container gets more space
        self.splitter.setStretchFactor(1, 0)  # Line profiler starts collapsed

        # Set initial sizes (80% image, 20% profiler)
        self.splitter.setSizes([800, 200])

        # Add splitter to main layout
        layout.addWidget(self.splitter)

        # Add status bar at the bottom
        layout.addWidget(status_widget)

        self.widget.setLayout(layout)
        self.setCentralWidget(self.widget)

        # set window size
        desktopWidget = QDesktopWidget()
        width = min(desktopWidget.height() * 0.9, 1000)
        height = width
        self.setFixedSize(int(width), int(height))

        # Connect mouse click handler
        if self.show_LUT:
            self.graphics_widget.view.getView().scene().sigMouseClicked.connect(
                self.handle_mouse_click
            )
            self.graphics_widget.view.getView().scene().sigMouseMoved.connect(
                self.handle_mouse_move
            )
        else:
            self.graphics_widget.view.scene().sigMouseClicked.connect(
                self.handle_mouse_click
            )
            self.graphics_widget.view.scene().sigMouseMoved.connect(
                self.handle_mouse_move
            )

        # Set up timer for updating stage and piezo positions
        self.update_timer: QTimer = QTimer()
        self.update_timer.timeout.connect(self.update_stage_piezo_positions)
        self.update_timer.start(100)  # Update every 100ms

    def update_stage_piezo_positions(self) -> None:
        try:
            if self.liveController and self.liveController.microscope:
                stage = self.liveController.microscope.stage
                if stage:
                    pos = stage.get_pos()
                    self.stage_position_label.setText(
                        f"Stage: X={pos.x_mm:.2f} mm, Y={pos.y_mm:.2f} mm, Z={pos.z_mm:.3f} mm"
                    )
                else:
                    self.stage_position_label.setText("Stage: N/A")

                piezo = self.liveController.microscope.addons.piezo_stage
                if piezo:
                    try:
                        piezo_pos = piezo.position
                        self.piezo_position_label.setText(f"Piezo: {piezo_pos:.1f} µm")
                        self.piezo_position_label.setVisible(True)
                    except Exception as e:
                        self._log.error(f"Error getting piezo position: {str(e)}")
                        self.piezo_position_label.setText("Piezo: Error")
                        self.piezo_position_label.setVisible(True)
                else:
                    self.piezo_position_label.setVisible(False)
            else:
                self.stage_position_label.setText("Stage: N/A")
                self.piezo_position_label.setVisible(False)
        except Exception as e:
            self._log.error(f"Error updating stage/piezo positions: {str(e)}")
            self.stage_position_label.setText("Stage: Error")
            self.piezo_position_label.setVisible(False)

    def closeEvent(self, event: Any) -> None:
        # Stop the timer when the window is closed
        self.update_timer.stop()
        super().closeEvent(event)

    def toggle_line_profiler(self) -> None:
        """Toggle the visibility of the line profiler widget."""
        if self.btn_line_profiler.isChecked():
            self.line_profiler_widget.show()
            if self.line_roi is None:
                # Start in drawing mode
                self.is_drawing_line = True
                self.line_start_pos = None
                self.line_end_pos = None
                # Set cross cursor
                if self.show_LUT:
                    self.graphics_widget.view.getView().setCursor(self.drawing_cursor)
                else:
                    self.graphics_widget.view.setCursor(self.drawing_cursor)
                self._log.info("Line profiler opened - ready to draw line")
            else:
                self.line_roi.show()
                self.update_line_profile()
        else:
            self.line_profiler_widget.hide()
            if self.line_roi is not None:
                self.line_roi.hide()
            # Reset cursor to normal
            if self.show_LUT:
                self.graphics_widget.view.getView().setCursor(self.normal_cursor)
            else:
                self.graphics_widget.view.setCursor(self.normal_cursor)

        # Connect to the view range changed signal to detect manual range changes
        self.line_profiler_plot.sigRangeChanged.connect(self._on_range_changed)

    def _on_range_changed(self, view_range: Any) -> None:
        """Handle manual range changes in the line profiler plot."""
        self.line_profiler_manual_range = True

    def create_line_roi(self) -> None:
        """Create a line ROI for intensity profiling."""
        if (
            self.line_roi is None
            and self.line_start_pos is not None
            and self.line_end_pos is not None
        ):
            try:
                # Convert coordinates to Point objects
                start_point = pg.Point(self.line_start_pos[0], self.line_start_pos[1])
                end_point = pg.Point(self.line_end_pos[0], self.line_end_pos[1])

                # Create the line ROI with width parameter
                self.line_roi = pg.LineROI(
                    pos1=start_point,
                    pos2=end_point,
                    width=5,  # Default width in pixels
                    pen=pg.mkPen("y", width=2),
                    hoverPen=pg.mkPen("y", width=2),
                    handlePen=pg.mkPen("y", width=2),
                    handleHoverPen=pg.mkPen("y", width=2),
                    movable=True,
                    rotatable=True,
                    resizable=True,
                )

                # Add the ROI to the view
                if self.show_LUT:
                    self.graphics_widget.view.getView().addItem(self.line_roi)
                else:
                    self.graphics_widget.view.addItem(self.line_roi)

                # Connect signal
                self.line_roi.sigRegionChanged.connect(self.update_line_profile)
                self.update_line_profile()
                self._log.info("Line ROI created successfully")
            except Exception as e:
                self._log.error(f"Error creating line ROI: {str(e)}")
                self.line_roi = None
                self.line_start_pos = None
                self.line_end_pos = None

    def update_line_profile(self) -> None:
        """Update the line profile plot based on the line ROI."""
        if not self.btn_line_profiler.isChecked() or self.line_roi is None:
            return

        try:
            if hasattr(self.graphics_widget.img, "image"):
                image = self.graphics_widget.img.image
                if image is not None:
                    # Get the line ROI state
                    state = self.line_roi.getState()
                    pos = state["pos"]
                    size = state["size"]
                    angle = state["angle"]
                    print(angle)
                    angle = np.radians(angle)

                    # Calculate start and end points
                    start = (pos[0], pos[1])
                    end = (
                        pos[0] + size[0] * np.cos(angle),
                        pos[1] + size[0] * np.sin(angle),
                    )

                    # Convert ROI coordinates to image coordinates
                    start_img = self.graphics_widget.img.mapFromView(
                        pg.Point(start[0], start[1])
                    )
                    end_img = self.graphics_widget.img.mapFromView(
                        pg.Point(end[0], end[1])
                    )

                    # Get the profile along the line
                    profile = self.get_line_profile(
                        image, start_img, end_img, size[1]
                    )  # size[1] is the width

                    # Clear previous plots
                    self.line_profiler_plot.clear()

                    # Calculate pixel distance for x-axis
                    pixel_distance = np.linspace(0, size[0], len(profile))

                    # Plot the profile
                    self.line_profiler_plot.plot(
                        pixel_distance, profile, pen="w", name="Intensity Profile"
                    )

                    # Set labels
                    self.line_profiler_plot.setLabel("left", "Intensity")
                    self.line_profiler_plot.setLabel("bottom", "Distance (pixels)")

                    # Add legend
                    self.line_profiler_plot.addLegend()

                    # Only auto-range if not manually set
                    if not self.line_profiler_manual_range:
                        self.line_profiler_plot.autoRange()
        except Exception as e:
            self._log.error(f"Error updating line profile: {str(e)}")

    def get_line_profile(
        self, image: np.ndarray, start: Any, end: Any, width: float = 1
    ) -> np.ndarray:
        """Get intensity profile along a line with specified width."""
        try:
            # Calculate the line vector
            line_vec = np.array([end.x() - start.x(), end.y() - start.y()])
            line_length = np.linalg.norm(line_vec)

            # Calculate the number of points along the line
            num_points = int(line_length)
            if num_points < 2:
                num_points = 2  # Ensure at least 2 points

            # Create coordinate arrays
            x = np.linspace(start.x(), end.x(), num_points)
            y = np.linspace(start.y(), end.y(), num_points)

            # Calculate perpendicular vector
            perp_vec = np.array([-line_vec[1], line_vec[0]]) / line_length

            # Create meshgrid for width sampling
            width_points = max(1, int(width))  # Ensure at least 1 point
            width_offsets = np.linspace(-width / 2, width / 2, width_points)

            # Initialize profile array
            profile = np.zeros(num_points)

            # Sample points along the width
            for w in width_offsets:
                x_offset = x + perp_vec[0] * w
                y_offset = y + perp_vec[1] * w

                # Get values at these points
                values = scipy.ndimage.map_coordinates(
                    image, [y_offset, x_offset], order=1
                )
                profile += values

            # Average the values
            profile /= width_points

            return profile

        except Exception as e:
            self._log.error(f"Error getting line profile: {str(e)}")
            return np.zeros(1)

    def handle_mouse_move(self, pos: Any) -> None:
        try:
            if self.show_LUT:
                view_coord = self.graphics_widget.view.getView().mapSceneToView(pos)
            else:
                view_coord = self.graphics_widget.view.mapSceneToView(pos)

            # Update preview line if we're drawing
            if (
                self.is_drawing_line
                and self.line_start_pos is not None
                and self.preview_line is not None
            ):
                self.preview_line.setData(
                    x=[self.line_start_pos[0], view_coord.x()],
                    y=[self.line_start_pos[1], view_coord.y()],
                )

            image_coord = self.graphics_widget.img.mapFromView(view_coord)

            if self.is_within_image(image_coord):
                x = int(image_coord.x())
                y = int(image_coord.y())
                self.last_valid_x = x
                self.last_valid_y = y
                self.has_valid_position = True

                self.cursor_position_label.setText(f"Position: ({x}, {y})")

                # Get pixel value
                image = self.graphics_widget.img.image
                if (
                    image is not None
                    and 0 <= y < image.shape[0]
                    and 0 <= x < image.shape[1]
                ):
                    pixel_value = image[y, x]
                    self.last_valid_value = pixel_value
                    self.pixel_value_label.setText(f"Value: {pixel_value}")
                else:
                    self.pixel_value_label.setText("Value:")
            else:
                self.cursor_position_label.setText("Position:")
                self.pixel_value_label.setText("Value:")
                self.has_valid_position = False
        except Exception:
            pass

    def handle_mouse_click(self, evt: Any) -> None:
        """Handle mouse clicks for both line drawing and other interactions."""
        if self.is_drawing_line:
            try:
                # Get the view that received the click
                if self.show_LUT:
                    view = self.graphics_widget.view.getView()
                else:
                    view = self.graphics_widget.view

                # Convert click position to view coordinates
                pos = evt.pos()
                view_coord = view.mapSceneToView(pos)

                if self.line_start_pos is None:
                    # First click - start drawing
                    self.line_start_pos = (view_coord.x(), view_coord.y())
                    self._log.info(f"Line start position set to: {self.line_start_pos}")

                    # Add a point marker at the start position
                    self.start_point_marker = pg.ScatterPlotItem(
                        pos=[(self.line_start_pos[0], self.line_start_pos[1])],
                        size=10,
                        symbol="o",
                        pen=pg.mkPen("y", width=2),
                        brush=pg.mkBrush("y"),
                    )
                    if self.show_LUT:
                        self.graphics_widget.view.getView().addItem(
                            self.start_point_marker
                        )
                    else:
                        self.graphics_widget.view.addItem(self.start_point_marker)

                    # Create preview line
                    self.preview_line = pg.PlotDataItem(
                        pen=pg.mkPen("y", width=2, style=Qt.DashLine)
                    )
                    if self.show_LUT:
                        self.graphics_widget.view.getView().addItem(self.preview_line)
                    else:
                        self.graphics_widget.view.addItem(self.preview_line)
                else:
                    # Second click - finish drawing
                    self.line_end_pos = (view_coord.x(), view_coord.y())
                    self._log.info(f"Line end position set to: {self.line_end_pos}")

                    # Remove preview line and start point marker
                    if self.preview_line is not None:
                        if self.show_LUT:
                            self.graphics_widget.view.getView().removeItem(
                                self.preview_line
                            )
                        else:
                            self.graphics_widget.view.removeItem(self.preview_line)
                        self.preview_line = None

                    if self.start_point_marker is not None:
                        if self.show_LUT:
                            self.graphics_widget.view.getView().removeItem(
                                self.start_point_marker
                            )
                        else:
                            self.graphics_widget.view.removeItem(
                                self.start_point_marker
                            )
                        self.start_point_marker = None

                    self.create_line_roi()
                    self.is_drawing_line = False
                    # Reset cursor to normal
                    view.setCursor(self.normal_cursor)
            except Exception as e:
                self._log.error(f"Error drawing line: {str(e)}")
                self.is_drawing_line = False
                self.line_start_pos = None
                self.line_end_pos = None
                # Clean up any remaining preview items
                if self.preview_line is not None:
                    if self.show_LUT:
                        self.graphics_widget.view.getView().removeItem(
                            self.preview_line
                        )
                    else:
                        self.graphics_widget.view.removeItem(self.preview_line)
                    self.preview_line = None
                if self.start_point_marker is not None:
                    if self.show_LUT:
                        self.graphics_widget.view.getView().removeItem(
                            self.start_point_marker
                        )
                    else:
                        self.graphics_widget.view.removeItem(self.start_point_marker)
                    self.start_point_marker = None
            return

        # Handle double clicks for other purposes
        if not evt.double():
            return

        try:
            pos = evt.pos()
            if self.show_LUT:
                view_coord = self.graphics_widget.view.getView().mapSceneToView(pos)
            else:
                view_coord = self.graphics_widget.view.mapSceneToView(pos)
            image_coord = self.graphics_widget.img.mapFromView(view_coord)
        except Exception:
            return

        if self.is_within_image(image_coord):
            x_pixel_centered = int(
                image_coord.x() - self.graphics_widget.img.width() / 2
            )
            y_pixel_centered = int(
                image_coord.y() - self.graphics_widget.img.height() / 2
            )
            self.image_click_coordinates.emit(
                x_pixel_centered,
                y_pixel_centered,
                self.graphics_widget.img.width(),
                self.graphics_widget.img.height(),
            )

    def is_within_image(self, coordinates: Any) -> bool:
        try:
            image_width = self.graphics_widget.img.width()
            image_height = self.graphics_widget.img.height()
            return (
                0 <= coordinates.x() < image_width
                and 0 <= coordinates.y() < image_height
            )
        except Exception:
            return False

    def display_image(self, image: np.ndarray) -> None:
        # enable the line profiler button after the first image is displayed
        if self.first_image:
            self.first_image = False
            self.btn_line_profiler.setEnabled(True)

        if ENABLE_TRACKING:
            image = np.copy(image)
            self.image_height, self.image_width = image.shape[:2]
            if self.draw_rectangle:
                cv2.rectangle(image, self.ptRect1, self.ptRect2, (255, 255, 255), 4)
                self.draw_rectangle = False

        # Apply spot tracking if enabled
        if self._spot_tracking_enabled:
            try:
                result = utils.find_spot_location(
                    image,
                    mode=self._spot_tracking_mode,
                    params=self._spot_tracking_params,
                    filter_sigma=self._spot_tracking_filter_sigma,
                    debug_plot=False,
                )
                if result is not None:
                    self._last_spot_x, self._last_spot_y = result
            except Exception:
                # Spot detection failed, keep last known position
                pass

            # Draw marker if we have a valid spot position
            if self._last_spot_x is not None and self._last_spot_y is not None:
                image = self._draw_spot_marker(image, self._last_spot_x, self._last_spot_y)

        info = (
            np.iinfo(image.dtype)
            if np.issubdtype(image.dtype, np.integer)
            else np.finfo(image.dtype)
        )
        min_val, max_val = info.min, info.max

        if self.liveController is not None and self.contrastManager is not None:
            channel_cfg = getattr(self.liveController, "currentConfiguration", None)
            channel_name = getattr(channel_cfg, "name", "Unknown")
            if (
                self.contrastManager.acquisition_dtype is not None
                and self.contrastManager.acquisition_dtype != np.dtype(image.dtype)
            ):
                self.contrastManager.scale_contrast_limits(np.dtype(image.dtype))
            min_val, max_val = self.contrastManager.get_limits(
                channel_name, image.dtype
            )

        self.graphics_widget.img.setImage(
            image, autoLevels=self.autoLevels, levels=(min_val, max_val)
        )

        if not self.autoLevels:
            if self.show_LUT:
                self.LUTWidget.setLevels(min_val, max_val)
                self.LUTWidget.setHistogramRange(info.min, info.max)
            else:
                self.graphics_widget.img.setLevels((min_val, max_val))

        self.graphics_widget.img.updateImage()

        # Update pixel value based on last valid position
        if self.has_valid_position:
            try:
                if (
                    0 <= self.last_valid_y < image.shape[0]
                    and 0 <= self.last_valid_x < image.shape[1]
                ):
                    pixel_value = image[self.last_valid_y, self.last_valid_x]
                    self.last_valid_value = pixel_value
                    self.cursor_position_label.setText(
                        f"Position: ({self.last_valid_x}, {self.last_valid_y})"
                    )
                    self.pixel_value_label.setText(f"Value: {pixel_value}")
            except Exception:
                # If there's an error, keep the last valid values
                self.cursor_position_label.setText(
                    f"Position: ({self.last_valid_x}, {self.last_valid_y})"
                )
                self.pixel_value_label.setText(f"Value: {self.last_valid_value}")

        if self.line_roi is not None and self.btn_line_profiler.isChecked():
            self.update_line_profile()

    def mark_spot(self, image: np.ndarray, x: float, y: float):
        """Mark the detected laserspot location on the image.

        Args:
            image: Image to mark
            x: x-coordinate of the spot
            y: y-coordinate of the spot

        Returns:
            Image with marked spot
        """
        # Draw a green crosshair at the specified x,y coordinates
        crosshair_size = 10  # Size of crosshair lines in pixels
        crosshair_color = (0, 255, 0)  # Green in BGR format
        crosshair_thickness = 1
        x = int(round(x))
        y = int(round(y))

        # Convert grayscale to BGR
        marked_image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)

        # Draw horizontal line
        cv2.line(
            marked_image,
            (x - crosshair_size, y),
            (x + crosshair_size, y),
            crosshair_color,
            crosshair_thickness,
        )

        # Draw vertical line
        cv2.line(
            marked_image,
            (x, y - crosshair_size),
            (x, y + crosshair_size),
            crosshair_color,
            crosshair_thickness,
        )

        self.display_image(marked_image)

    def set_spot_tracking(
        self,
        enabled: bool,
        mode: SpotDetectionMode = SpotDetectionMode.SINGLE,
        params: Optional[dict] = None,
        filter_sigma: Optional[int] = None,
    ) -> None:
        """Enable or disable continuous spot tracking on displayed images.

        Args:
            enabled: Whether to enable spot tracking
            mode: Spot detection mode
            params: Spot detection parameters (y_window, x_window, etc.)
            filter_sigma: Gaussian filter sigma for preprocessing
        """
        self._spot_tracking_enabled = enabled
        self._spot_tracking_mode = mode
        self._spot_tracking_params = params
        self._spot_tracking_filter_sigma = filter_sigma
        if not enabled:
            self._last_spot_x = None
            self._last_spot_y = None

    def _draw_spot_marker(self, image: np.ndarray, x: float, y: float) -> np.ndarray:
        """Draw a crosshair marker on the image at the given coordinates.

        Args:
            image: Image to draw on (will be modified in place if BGR, otherwise converted)
            x: x-coordinate of the spot
            y: y-coordinate of the spot

        Returns:
            Image with marker drawn
        """
        crosshair_size = 10
        crosshair_color = (0, 255, 0)  # Green in BGR
        crosshair_thickness = 1
        x_int = int(round(x))
        y_int = int(round(y))

        # Convert to BGR if grayscale
        if len(image.shape) == 2:
            marked_image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        else:
            marked_image = image.copy()

        # Draw horizontal line
        cv2.line(
            marked_image,
            (x_int - crosshair_size, y_int),
            (x_int + crosshair_size, y_int),
            crosshair_color,
            crosshair_thickness,
        )

        # Draw vertical line
        cv2.line(
            marked_image,
            (x_int, y_int - crosshair_size),
            (x_int, y_int + crosshair_size),
            crosshair_color,
            crosshair_thickness,
        )

        return marked_image

    def update_contrast_limits(self) -> None:
        if (
            self.show_LUT
            and self.contrastManager
            and self.contrastManager.acquisition_dtype
        ):
            min_val, max_val = self.LUTWidget.region.getRegion()
            cfg = getattr(self.liveController, "currentConfiguration", None)
            channel_name = getattr(cfg, "name", "Unknown")
            self.contrastManager.update_limits(channel_name, min_val, max_val)

    def update_ROI(self) -> None:
        self.roi_pos = self.ROI.pos()
        self.roi_size = self.ROI.size()

    def show_ROI_selector(self) -> None:
        self.ROI.show()

    def hide_ROI_selector(self) -> None:
        self.ROI.hide()

    def get_roi(self) -> Tuple[Any, Any]:
        return self.roi_pos, self.roi_size

    def update_bounding_box(self, pts: List[List[int]]) -> None:
        self.draw_rectangle = True
        self.ptRect1 = (pts[0][0], pts[0][1])
        self.ptRect2 = (pts[1][0], pts[1][1])

    def get_roi_bounding_box(self) -> np.ndarray:
        self.update_ROI()
        width = self.roi_size[0]
        height = self.roi_size[1]
        xmin = max(0, self.roi_pos[0])
        ymin = max(0, self.roi_pos[1])
        return np.array([xmin, ymin, width, height])

    def set_autolevel(self, enabled: bool) -> None:
        self.autoLevels = enabled
        self._log.info("set autolevel to " + str(enabled))


class ImageArrayDisplayWindow(QMainWindow):
    def __init__(self, window_title: str = "") -> None:
        super().__init__()
        self.setWindowTitle(window_title)
        self.setWindowFlags(self.windowFlags() | Qt.CustomizeWindowHint)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowCloseButtonHint)
        self.widget: QWidget = QWidget()

        # interpret image data as row-major instead of col-major
        pg.setConfigOptions(imageAxisOrder="row-major")

        self.graphics_widget_1: Any = pg.GraphicsLayoutWidget()
        self.graphics_widget_1.view = self.graphics_widget_1.addViewBox()
        self.graphics_widget_1.view.setAspectLocked(True)
        self.graphics_widget_1.img = pg.ImageItem(border="w")
        self.graphics_widget_1.view.addItem(self.graphics_widget_1.img)
        self.graphics_widget_1.view.invertY()

        self.graphics_widget_2: Any = pg.GraphicsLayoutWidget()
        self.graphics_widget_2.view = self.graphics_widget_2.addViewBox()
        self.graphics_widget_2.view.setAspectLocked(True)
        self.graphics_widget_2.img = pg.ImageItem(border="w")
        self.graphics_widget_2.view.addItem(self.graphics_widget_2.img)
        self.graphics_widget_2.view.invertY()

        self.graphics_widget_3: Any = pg.GraphicsLayoutWidget()
        self.graphics_widget_3.view = self.graphics_widget_3.addViewBox()
        self.graphics_widget_3.view.setAspectLocked(True)
        self.graphics_widget_3.img = pg.ImageItem(border="w")
        self.graphics_widget_3.view.addItem(self.graphics_widget_3.img)
        self.graphics_widget_3.view.invertY()

        self.graphics_widget_4: Any = pg.GraphicsLayoutWidget()
        self.graphics_widget_4.view = self.graphics_widget_4.addViewBox()
        self.graphics_widget_4.view.setAspectLocked(True)
        self.graphics_widget_4.img = pg.ImageItem(border="w")
        self.graphics_widget_4.view.addItem(self.graphics_widget_4.img)
        self.graphics_widget_4.view.invertY()
        ## Layout
        layout = QGridLayout()
        layout.addWidget(self.graphics_widget_1, 0, 0)
        layout.addWidget(self.graphics_widget_2, 0, 1)
        layout.addWidget(self.graphics_widget_3, 1, 0)
        layout.addWidget(self.graphics_widget_4, 1, 1)
        self.widget.setLayout(layout)
        self.setCentralWidget(self.widget)

        # set window size
        desktopWidget = QDesktopWidget()
        width = min(desktopWidget.height() * 0.9, 1000)  # @@@TO MOVE@@@#
        height = width
        self.setFixedSize(int(width), int(height))

    def display_image(self, image: np.ndarray, illumination_source: int) -> None:
        if illumination_source < 11:
            self.graphics_widget_1.img.setImage(image, autoLevels=False)
        elif illumination_source == 11:
            self.graphics_widget_2.img.setImage(image, autoLevels=False)
        elif illumination_source == 12:
            self.graphics_widget_3.img.setImage(image, autoLevels=False)
        elif illumination_source == 13:
            self.graphics_widget_4.img.setImage(image, autoLevels=False)
