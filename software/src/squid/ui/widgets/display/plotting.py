# Plotting widgets (waveform, generic plot, 3D surface)
from typing import Any, Optional, List

import numpy as np
from mpl_toolkits.mplot3d import proj3d
from scipy.interpolate import griddata

import squid.core.logging
import pyqtgraph as pg
from matplotlib.figure import Figure
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas

from qtpy.QtCore import Signal
from qtpy.QtWidgets import QFrame, QWidget, QVBoxLayout, QGridLayout


class WaveformDisplay(QFrame):
    def __init__(
        self,
        N: int = 1000,
        include_x: bool = True,
        include_y: bool = True,
        main: QWidget = None,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.N = N
        self.include_x = include_x
        self.include_y = include_y
        self.add_components()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def add_components(self) -> None:
        self.plotWidget = {}
        self.plotWidget["X"] = PlotWidget("X", N=self.N, add_legend=True)
        self.plotWidget["Y"] = PlotWidget("X", N=self.N, add_legend=True)

        layout = QGridLayout()
        if self.include_x:
            layout.addWidget(self.plotWidget["X"], 0, 0)
        if self.include_y:
            layout.addWidget(self.plotWidget["Y"], 1, 0)
        self.setLayout(layout)

    def plot(self, time: np.ndarray, data: np.ndarray) -> None:
        if self.include_x:
            self.plotWidget["X"].plot(
                time, data[0, :], "X", color=(255, 255, 255), clear=True
            )
        if self.include_y:
            self.plotWidget["Y"].plot(
                time, data[1, :], "Y", color=(255, 255, 255), clear=True
            )

    def update_N(self, N: int) -> None:
        self.N = N
        self.plotWidget["X"].update_N(N)
        self.plotWidget["Y"].update_N(N)


class PlotWidget(pg.GraphicsLayoutWidget):
    def __init__(
        self,
        title: str = "",
        N: int = 1000,
        parent: QWidget = None,
        add_legend: bool = False,
    ) -> None:
        super().__init__(parent)
        self.plotWidget = self.addPlot(
            title="", axisItems={"bottom": pg.DateAxisItem()}
        )
        if add_legend:
            self.plotWidget.addLegend()
        self.N = N

    def plot(
        self,
        x: np.ndarray,
        y: np.ndarray,
        label: str,
        color: tuple,
        clear: bool = False,
    ) -> None:
        self.plotWidget.plot(
            x[-self.N :],
            y[-self.N :],
            pen=pg.mkPen(color=color, width=4),
            name=label,
            clear=clear,
        )

    def update_N(self, N: int) -> None:
        self.N = N


class SurfacePlotWidget(QWidget):
    """
    A widget that displays a 3D surface plot of the coordinates.
    """

    signal_point_clicked = Signal(float, float)

    def __init__(self, parent: QWidget = None) -> None:
        super().__init__(parent)
        self._log = squid.core.logging.get_logger(__name__)

        # Setup canvas and figure
        self.fig = Figure()
        self.canvas = FigureCanvas(self.fig)
        self.ax = self.fig.add_subplot(111, projection="3d")

        layout = QVBoxLayout()
        layout.addWidget(self.canvas)
        self.setLayout(layout)

        self.selected_index = None
        self.plot_populated = False

        # Connect events
        self.canvas.mpl_connect("scroll_event", self.on_scroll)
        self.canvas.mpl_connect("button_press_event", self.on_click)

        self._x_coords: list[float] = []
        self._y_coords: list[float] = []
        self._z_coords: list[float] = []
        self.regions: list[int] = []

    def clear(self) -> None:
        self._x_coords.clear()
        self._y_coords.clear()
        self._z_coords.clear()
        self.regions.clear()

    def add_point(self, x: float, y: float, z: float, region: int) -> None:
        self._x_coords.append(x)
        self._y_coords.append(y)
        self._z_coords.append(z)
        self.regions.append(region)

    def plot(self) -> None:
        """
        Plot both surface and scatter points in 3D.

        Args:
            x (np.array): X coordinates (1D array)
            y (np.array): Y coordinates (1D array)
            z (np.array): Z coordinates (1D array)
        """
        try:
            # Clear previous plot
            self.ax.clear()

            x = np.array(self._x_coords).astype(float)
            y = np.array(self._y_coords).astype(float)
            z = np.array(self._z_coords).astype(float)
            regions = np.array(self.regions)

            # plot surface by region
            for r in np.unique(regions):
                try:
                    mask = regions == r
                    num_points = np.sum(mask)
                    if num_points >= 4:
                        x_range = max(x[mask]) - min(x[mask])
                        y_range = max(y[mask]) - min(y[mask])
                        # Skip if points are collinear or have no spread in X or Y
                        if x_range < 1e-9 or y_range < 1e-9:
                            self._log.debug(
                                f"Region {r} has collinear or degenerate points "
                                f"(x_range={x_range:.2e}, y_range={y_range:.2e}), "
                                "skipping surface interpolation"
                            )
                            continue
                        grid_x, grid_y = np.mgrid[
                            min(x[mask]) : max(x[mask]) : 10j,
                            min(y[mask]) : max(y[mask]) : 10j,
                        ]  # type: ignore[misc]
                        grid_z = griddata(
                            (x[mask], y[mask]),
                            z[mask],
                            (grid_x, grid_y),
                            method="cubic",
                        )
                        self.ax.plot_surface(
                            grid_x, grid_y, grid_z, cmap="viridis", edgecolor="none"
                        )
                    else:
                        self._log.debug(
                            f"Region {r} has only {num_points} point(s), skipping surface interpolation"
                        )
                except Exception as e:
                    raise Exception(f"Cannot plot region {r}: {e}")

            # Create scatter plot using original coordinates
            self.colors = ["r"] * len(x)
            self.scatter = self.ax.scatter(x, y, z, c=self.colors, s=30)

            # Set labels
            self.ax.set_xlabel("X (mm)")
            self.ax.set_ylabel("Y (mm)")
            self.ax.set_zlabel("Z (um)")
            self.ax.set_title("Double-click a point to go to that position")

            # Force x and y to have same scale
            max_range = max(np.ptp(x), np.ptp(y))
            center_x = np.mean(x)
            center_y = np.mean(y)

            self.ax.set_xlim(center_x - max_range / 2, center_x + max_range / 2)
            self.ax.set_ylim(center_y - max_range / 2, center_y + max_range / 2)

            self.canvas.draw()
            self.plot_populated = True
        except Exception as e:
            self._log.error(f"Error plotting surface: {e}")

    def on_scroll(self, event: Any) -> None:
        scale = 1.1 if event.button == "up" else 0.9

        def zoom(lim):
            center = (lim[0] + lim[1]) / 2
            half_range = (lim[1] - lim[0]) / 2 * scale
            return center - half_range, center + half_range

        self.ax.set_xlim(zoom(self.ax.get_xlim()))
        self.ax.set_ylim(zoom(self.ax.get_ylim()))
        self.ax.set_zlim(zoom(self.ax.get_zlim()))
        self.canvas.draw()

    def on_click(self, event: Any) -> None:
        if not self.plot_populated:
            return
        if not event.dblclick or event.inaxes != self.ax:
            return

        # Cancel drag mode after double-click
        self.canvas.button_pressed = None  # FIX: Avoids AttributeError

        # Project 3D points to 2D screen space
        x2d, y2d, _ = proj3d.proj_transform(
            self._x_coords, self._y_coords, self._z_coords, self.ax.get_proj()
        )
        dists = np.hypot(x2d - event.xdata, y2d - event.ydata)
        idx = np.argmin(dists)

        # Threshold in data coordinates
        display_thresh = 0.05 * max(
            self.ax.get_xlim()[1] - self.ax.get_xlim()[0],
            self.ax.get_ylim()[1] - self.ax.get_ylim()[0],
        )
        if dists[idx] > display_thresh:
            return

        # Change point color
        self.colors = ["r"] * len(self._x_coords)
        self.colors[idx] = "g"
        self.scatter.remove()
        self.scatter = self.ax.scatter(
            self._x_coords, self._y_coords, self._z_coords, c=self.colors, s=30
        )

        print(
            f"Clicked Point: x={self._x_coords[idx]:.3f}, y={self._y_coords[idx]:.3f}, z={self._z_coords[idx]:.3f}"
        )
        self.canvas.draw()
        self.signal_point_clicked.emit(self._x_coords[idx], self._y_coords[idx])
