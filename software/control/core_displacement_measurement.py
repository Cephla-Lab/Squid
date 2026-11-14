# set QT_API environment variable
import os

os.environ["QT_API"] = "pyqt5"
import qtpy

# qt libraries
from qtpy.QtCore import *
from qtpy.QtWidgets import *
from qtpy.QtGui import *

import control.utils as utils
from control._def import *

import time
import numpy as np
import cv2


class DisplacementMeasurementController(QObject):

    signal_readings = Signal(list)
    signal_plots = Signal(np.ndarray, np.ndarray)

    def __init__(self, x_offset=0, y_offset=0, x_scaling=1, y_scaling=1, N_average=1, N=10000):

        QObject.__init__(self)
        self.x_offset = x_offset
        self.y_offset = y_offset
        self.x_scaling = x_scaling
        self.y_scaling = y_scaling
        self.N_average = N_average
        self.N = N  # length of array to emit
        self.t_array = np.array([])
        self.x_array = np.array([])
        self.y_array = np.array([])
        # Cache the coordinate grids across frames of the same size (see update_measurement).
        self._grid_shape = None
        self._xgrid = None
        self._ygrid = None

    def update_measurement(self, image):

        t = time.time()

        if len(image.shape) == 3:
            image = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)

        h, w = image.shape
        # Recomputing the meshgrid every frame is the bulk of the per-frame CPU cost; on the main
        # (UI) thread this is what froze the autofocus live view. Cache it across same-size frames.
        if self._grid_shape != (h, w):
            self._xgrid, self._ygrid = np.meshgrid(np.arange(w), np.arange(h))
            self._grid_shape = (h, w)

        I = image.astype(float)
        I = I - np.amin(I)
        peak = np.amax(I)
        if peak > 0:
            I[I / peak < 0.2] = 0
        total = np.sum(I)
        if total > 0:
            x = np.sum(self._xgrid * I) / total
            y = np.sum(self._ygrid * I) / total
        else:
            x = y = 0.0  # blank/uniform frame: no spot -> avoid 0/0 nan spam

        x = x - self.x_offset
        y = y - self.y_offset
        x = x * self.x_scaling
        y = y * self.y_scaling

        # Keep only the last N samples. The original appended without trimming, so the arrays (and
        # the plot data emitted every frame) grew without bound -> the UI thread eventually froze.
        self.t_array = np.append(self.t_array, t)[-self.N :]
        self.x_array = np.append(self.x_array, x)[-self.N :]
        self.y_array = np.append(self.y_array, y)[-self.N :]

        self.signal_plots.emit(self.t_array, np.vstack((self.x_array, self.y_array)))
        self.signal_readings.emit([np.mean(self.x_array[-self.N_average :]), np.mean(self.y_array[-self.N_average :])])

    def update_settings(self, x_offset, y_offset, x_scaling, y_scaling, N_average, N):
        self.N = N
        self.N_average = N_average
        self.x_offset = x_offset
        self.y_offset = y_offset
        self.x_scaling = x_scaling
        self.y_scaling = y_scaling
