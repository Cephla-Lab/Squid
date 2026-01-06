import sys
import json
import base64
import numpy as np
from PyQt5.QtWidgets import QApplication, QMainWindow, QLabel, QVBoxLayout, QWidget, QPushButton, QFileDialog
from PyQt5.QtCore import Qt
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg

try:
    import yaml

    HAS_YAML = True
except ImportError:
    HAS_YAML = False


class ImageWindow(QMainWindow):
    """Window to display a single reference image"""

    def __init__(self, image_array, file_path):
        super().__init__()
        self.setWindowTitle(f"Reference Image - {file_path.split('/')[-1]}")

        # Create central widget and layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # Create matplotlib figure
        self.figure, self.ax = plt.subplots()
        self.canvas = FigureCanvasQTAgg(self.figure)
        layout.addWidget(self.canvas)

        # Display image
        im = self.ax.imshow(image_array, cmap="gray")
        self.figure.colorbar(im)
        self.ax.set_title(f"Reference Image from {file_path.split('/')[-1]}")
        self.canvas.draw()

        # Add file path label
        path_label = QLabel(f"Source: {file_path}")
        path_label.setWordWrap(True)
        layout.addWidget(path_label)

        self.setMinimumSize(500, 500)


class ReferenceImageViewer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Laser AF Reference Image Viewer")
        self.setAcceptDrops(True)
        self.image_windows = []  # Keep track of open windows

        # Create central widget and layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # Add browse button
        browse_btn = QPushButton("Browse for laser_af_settings (JSON or YAML)")
        browse_btn.clicked.connect(self.browse_file)
        layout.addWidget(browse_btn)

        # Add drag-drop label
        self.drop_label = QLabel("Drag and drop laser_af_settings file here\n(JSON or YAML)")
        self.drop_label.setAlignment(Qt.AlignCenter)
        self.drop_label.setWordWrap(True)
        self.drop_label.setStyleSheet(
            """
            QLabel {
                border: 2px dashed #aaa;
                border-radius: 5px;
                padding: 20px;
                background: #f0f0f0;
                min-height: 100px;
            }
        """
        )
        layout.addWidget(self.drop_label)

        self.setMinimumSize(400, 200)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        files = [u.toLocalFile() for u in event.mimeData().urls()]
        for file_path in files:
            if file_path.endswith(".json") or file_path.endswith(".yaml") or file_path.endswith(".yml"):
                self.load_reference_image(file_path)

    def browse_file(self):
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select laser_af_settings file",
            "",
            "Config Files (*.json *.yaml *.yml);;JSON Files (*.json);;YAML Files (*.yaml *.yml);;All Files (*)",
        )
        for file_path in file_paths:
            self.load_reference_image(file_path)

    def load_reference_image(self, file_path):
        try:
            # Load file based on extension
            if file_path.endswith(".yaml") or file_path.endswith(".yml"):
                if not HAS_YAML:
                    raise ImportError("PyYAML is required to load YAML files. Install with: pip install pyyaml")
                with open(file_path, "r") as f:
                    data = yaml.safe_load(f)
            else:
                with open(file_path, "r") as f:
                    data = json.load(f)

            # Extract image data
            ref_image_b64 = data.get("reference_image")
            if not ref_image_b64:
                raise ValueError("No reference image found in file")

            shape = data.get("reference_image_shape")
            dtype = data.get("reference_image_dtype")

            # Decode image
            image_data = base64.b64decode(ref_image_b64)
            image_array = np.frombuffer(image_data, dtype=np.dtype(dtype)).reshape(shape)

            # Create new window for this image
            image_window = ImageWindow(image_array, file_path)
            image_window.show()
            self.image_windows.append(image_window)  # Keep reference to prevent garbage collection

            # Update status in drop area
            self.drop_label.setText(f"Loaded: {file_path}\n\nDrag another file here to view")

        except Exception as e:
            self.drop_label.setText(f"Error loading {file_path}: {str(e)}\n\nTry another file")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    viewer = ReferenceImageViewer()
    viewer.show()
    sys.exit(app.exec_())
