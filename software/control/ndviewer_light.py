"""
Lightweight NDV-based viewer

Supports: OME-TIFF and single-TIFF acquisitions with lazy loading via dask (specify how it is faster).
"""

import re
import sys
from pathlib import Path
from typing import Dict, List, Optional


import numpy as np


from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QMainWindow, QPushButton, QFileDialog, QApplication
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QPalette, QColor

# NDV viewer
try:
    import ndv

    NDV_AVAILABLE = True
    # Monkeypatch superqt slider to respect full ranges (self-contained)
    try:
        from superqt.sliders import QLabeledSlider

        if not getattr(QLabeledSlider, "_ndv_range_patch", False):
            _orig_setRange = QLabeledSlider.setRange

            def _patched_setRange(self, a, b):
                _orig_setRange(self, a, b)
                if hasattr(self, "_slider"):
                    self._slider.setMinimum(a)
                    self._slider.setMaximum(b)
                if hasattr(self, "_label"):
                    try:
                        self._label.setRange(a, b)
                    except Exception:
                        pass

            QLabeledSlider.setRange = _patched_setRange
            QLabeledSlider._ndv_range_patch = True
    except Exception:
        pass
except ImportError:
    NDV_AVAILABLE = False

# Lazy loading
try:
    import tifffile as tf
    import xarray as xr
    import dask.array as da
    from dask import delayed
    from functools import lru_cache

    LAZY_LOADING_AVAILABLE = True
except ImportError:
    LAZY_LOADING_AVAILABLE = False

# Filename patterns (from common.py)
FPATTERN = re.compile(r"(?P<r>[^_]+)_(?P<f>\d+)_(?P<z>\d+)_(?P<c>.+)\.tiff?", re.IGNORECASE)
FPATTERN_OME = re.compile(r"(?P<r>[^_]+)_(?P<f>\d+)\.ome\.tiff?", re.IGNORECASE)


# Helper functions
def extract_wavelength(channel_str: str):
    """Extract wavelength (nm) from channel string; None if unknown."""
    if not channel_str:
        return None
    lower = channel_str.lower()
    if re.fullmatch(r"ch\d+", lower):
        return None
    # Direct wavelength pattern
    if m := re.search(r"(\d{3,4})[ _]*nm", channel_str, re.IGNORECASE):
        return int(m.group(1))

    # Common fluorophores
    fluor_map = {
        "dapi": 405,
        "hoechst": 405,
        "gfp": 488,
        "fitc": 488,
        "alexa488": 488,
        "tritc": 561,
        "cy3": 561,
        "mcherry": 561,
        "cy5": 640,
        "alexa647": 640,
        "cy7": 730,
    }
    channel_lower = channel_str.lower()
    for fluor, wl in fluor_map.items():
        if fluor in channel_lower:
            return wl

    # Fallback
    numbers = re.findall(r"\d{3,4}", channel_str)
    if numbers:
        # Prefer the last 3-4 digit group (likely wavelength)
        val = int(numbers[-1])
        return val if val > 0 else None
    return None


def detect_format(base_path: Path) -> str:
    """Detect OME-TIFF vs single-TIFF format."""
    ome_dir = base_path / "ome_tiff"
    if ome_dir.exists():
        if any(".ome" in f.name for f in ome_dir.glob("*.tif*")):
            return "ome_tiff"

    first_tp = next((d for d in base_path.iterdir() if d.is_dir() and d.name.isdigit()), None)
    if first_tp:
        if any(".ome" in f.name for f in first_tp.glob("*.tif*")):
            return "ome_tiff"
    return "single_tiff"


def wavelength_to_colormap(wavelength: int, index: int = 0) -> str:
    """Map wavelength to NDV colormap."""
    if wavelength is None or wavelength == 0:
        return "gray"
    if wavelength <= 420:
        return "blue"
    elif 470 <= wavelength <= 510:
        return "green"
    elif 540 <= wavelength <= 590:
        return "yellow"
    elif 620 <= wavelength <= 660:
        return "red"
    elif wavelength >= 700:
        return "magenta"
    return "gray"


class LauncherWindow(QMainWindow):
    """Separate launcher window with dropbox for dataset selection."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("NDViewer Lightweight - Open Dataset")
        self.setGeometry(100, 100, 400, 300)  # 4:3 aspect, narrower
        self._set_dark_theme()

        central = QWidget()
        layout = QVBoxLayout()

        # Drop zone / Open button
        self.drop_label = QLabel("Drop folder here\nor click to open")
        self.drop_label.setAlignment(Qt.AlignCenter)
        self.drop_label.setStyleSheet(
            """
            QLabel {
                border: 2px dashed #666;
                border-radius: 10px;
                padding: 40px;
                background: #2a2a2a;
                color: #aaa;
                font-size: 14px;
            }
            QLabel:hover {
                border-color: #888;
                background: #333;
            }
        """
        )
        self.drop_label.setMinimumHeight(150)
        self.drop_label.mousePressEvent = lambda e: self._open_folder_dialog()
        layout.addWidget(self.drop_label)

        # Status
        self.status_label = QLabel("No dataset loaded")
        self.status_label.setStyleSheet("color: #888; padding: 5px;")
        self.status_label.setAlignment(Qt.AlignCenter)
        # layout.addWidget(self.status_label) # hide status label

        central.setLayout(layout)
        self.setCentralWidget(central)
        self.setAcceptDrops(True)

        self.viewer_window = None

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            self._launch_viewer(path)

    def _open_folder_dialog(self):
        path = QFileDialog.getExistingDirectory(self, "Select Dataset Folder")
        if path:
            self._launch_viewer(path)

    def _launch_viewer(self, path: str):
        """Launch main viewer window with dataset."""
        self.status_label.setText(f"Opening: {Path(path).name}...")
        QApplication.processEvents()

        # Keep launcher open; allow multiple drops without restarting.
        if self.viewer_window:
            try:
                self.viewer_window.close()
            except Exception:
                pass
        self.viewer_window = LightweightMainWindow(path)
        self.viewer_window.show()

    def _set_dark_theme(self):
        from PyQt5.QtWidgets import QStyleFactory

        self.setStyle(QStyleFactory.create("Fusion"))

        p = self.palette()
        p.setColor(QPalette.Window, QColor(53, 53, 53))
        p.setColor(QPalette.WindowText, QColor(255, 255, 255))
        p.setColor(QPalette.Base, QColor(35, 35, 35))
        p.setColor(QPalette.AlternateBase, QColor(53, 53, 53))
        p.setColor(QPalette.Text, QColor(255, 255, 255))
        p.setColor(QPalette.Button, QColor(53, 53, 53))
        p.setColor(QPalette.ButtonText, QColor(255, 255, 255))
        p.setColor(QPalette.Highlight, QColor(42, 130, 218))
        p.setColor(QPalette.HighlightedText, QColor(35, 35, 35))
        self.setPalette(p)


class LightweightViewer(QWidget):
    """Minimal NDV-based viewer."""

    def __init__(self, dataset_path: str):
        super().__init__()
        self.dataset_path = dataset_path
        self.ndv_viewer = None
        self._xarray_data = None  # Store for external access
        self._open_handles = []  # Keep tif handles alive when mmap is used
        self._last_sig = None
        self._refresh_timer = None
        self._setup_ui()
        self.load_dataset(dataset_path)
        self._setup_live_refresh()

    def _setup_ui(self):
        layout = QVBoxLayout()

        # Status
        self.status_label = QLabel("Loading dataset...")
        self.status_label.setStyleSheet("color: #888; padding: 5px;")
        # layout.addWidget(self.status_label)

        # NDV placeholder
        if NDV_AVAILABLE:
            dummy = np.zeros((1, 100, 100), dtype=np.uint16)
            self.ndv_viewer = ndv.ArrayViewer(dummy, channel_axis=0, channel_mode="composite", visible_axes=(-2, -1))
            layout.addWidget(self.ndv_viewer.widget(), 1)
        else:
            placeholder = QLabel("NDV not available.\npip install ndv[vispy,pyqt]")
            placeholder.setAlignment(Qt.AlignCenter)
            layout.addWidget(placeholder, 1)

        self.setLayout(layout)

    def _setup_live_refresh(self):
        """Poll the dataset folder periodically to pick up new timepoints during acquisition."""
        # Only enable when lazy loading + NDV are available; otherwise refresh does nothing useful.
        if not (LAZY_LOADING_AVAILABLE and NDV_AVAILABLE and self.ndv_viewer):
            return
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(750)  # ms; keep light to avoid IO churn
        self._refresh_timer.timeout.connect(self._maybe_refresh)
        self._refresh_timer.start()

    def _close_open_handles(self):
        """Close mmap TiffFile handles (OME path) from the previously loaded dataset."""
        for h in getattr(self, "_open_handles", []) or []:
            try:
                h.close()
            except Exception:
                pass
        self._open_handles = []

    def _force_refresh(self):
        self._last_sig = None
        self._maybe_refresh()

    def _dataset_signature(self) -> tuple:
        """Return a cheap signature that changes when new data likely arrived."""
        base = Path(self.dataset_path)
        fmt = detect_format(base)

        if fmt == "single_tiff":
            tp_dirs = [d for d in base.iterdir() if d.is_dir() and d.name.isdigit()]
            if not tp_dirs:
                return (fmt, -1, 0)

            t_vals = sorted(int(d.name) for d in tp_dirs)
            first_tp = base / str(t_vals[0])

            # FOVs are assumed to only appear in the first timepoint during acquisition.
            fov_set = set()
            try:
                if first_tp.exists():
                    for f in first_tp.iterdir():
                        if f.suffix.lower() not in [".tif", ".tiff"]:
                            continue
                        m = FPATTERN.search(f.name)
                        if m:
                            fov_set.add((m.group("r"), int(m.group("f"))))
            except Exception:
                pass

            return (fmt, max(t_vals), len(fov_set))

        # ome_tiff
        ome_dir = base / "ome_tiff"
        if not ome_dir.exists():
            ome_dir = next((d for d in base.iterdir() if d.is_dir() and d.name.isdigit()), base)

        ome_files = sorted(ome_dir.glob("*.ome.tif*"))
        n_ome = len(ome_files)
        t_len = -1
        st = None
        if ome_files:
            try:
                st = ome_files[0].stat()
            except Exception:
                st = None
            try:
                with tf.TiffFile(str(ome_files[0])) as tif:
                    series = tif.series[0]
                    axes = series.axes
                    shape = series.shape
                    if "T" in axes:
                        t_len = int(shape[axes.index("T")])
                    else:
                        t_len = 1
            except Exception:
                # File may be mid-write; fall back on size/mtime if available
                pass

        if st is None:
            return (fmt, n_ome, t_len)
        return (fmt, n_ome, t_len, st.st_size, getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))

    def _try_inplace_ndv_update(self, data: "xr.DataArray") -> bool:
        """Best-effort no-flicker data swap for ndv, depending on installed ndv version."""
        v = self.ndv_viewer
        if v is None:
            return False

        # Try common APIs across versions.
        candidates = [
            ("set_data", True),
            ("setData", True),
            ("set_array", True),
            ("setArray", True),
        ]
        for name, is_call in candidates:
            attr = getattr(v, name, None)
            if callable(attr):
                try:
                    attr(data)
                    return True
                except Exception:
                    pass

        # Try common attribute assignment patterns.
        for prop in ["data", "array"]:
            if hasattr(v, prop):
                try:
                    setattr(v, prop, data)
                    return True
                except Exception:
                    pass

        # Some viewers tuck the model under .viewer or .model
        for inner_name in ["viewer", "model"]:
            inner = getattr(v, inner_name, None)
            if inner is None:
                continue
            for name in ["set_data", "setData", "set_array", "setArray"]:
                fn = getattr(inner, name, None)
                if callable(fn):
                    try:
                        fn(data)
                        return True
                    except Exception:
                        pass
            for prop in ["data", "array"]:
                if hasattr(inner, prop):
                    try:
                        setattr(inner, prop, data)
                        return True
                    except Exception:
                        pass

        return False

    def _maybe_refresh(self):
        if not LAZY_LOADING_AVAILABLE:
            return

        try:
            sig = self._dataset_signature()
        except Exception:
            return
        if sig == self._last_sig:
            return
        self._last_sig = sig

        data = self._create_lazy_array(Path(self.dataset_path))
        if data is None:
            return

        # Swap dataset, keeping OME handles alive for the new data
        old_handles = getattr(self, "_open_handles", [])
        self._xarray_data = data
        self._open_handles = data.attrs.get("_open_tifs", [])

        # Prefer in-place update to avoid visible refresh.
        if self._try_inplace_ndv_update(data):
            # Close old handles after successful swap.
            for h in old_handles or []:
                try:
                    h.close()
                except Exception:
                    pass
            return

        # Fallback: rebuild widget (may be visible on some platforms). Reduce flicker a bit.
        try:
            self.setUpdatesEnabled(False)
            self._set_ndv_data(data)
        finally:
            self.setUpdatesEnabled(True)
            # Close old handles regardless.
            for h in old_handles or []:
                try:
                    h.close()
                except Exception:
                    pass

    def load_dataset(self, path: str):
        """Load dataset and display in NDV."""
        self.dataset_path = path
        self.status_label.setText(f"Loading: {Path(path).name}...")
        QApplication.processEvents()

        try:
            data = self._create_lazy_array(Path(path))
            if data is not None:
                self._xarray_data = data  # Store for profiling
                self._open_handles = data.attrs.get("_open_tifs", [])
                self._set_ndv_data(data)

                # Update status (keep it stable during live acquisition; avoid printing dims like time=...)
                self.status_label.setText(f"Loaded: {Path(path).name}")
            else:
                self.status_label.setText("Failed to load dataset")
        except Exception as e:
            self.status_label.setText(f"Error: {e}")
            import traceback

            traceback.print_exc()

    def _create_lazy_array(self, base_path: Path) -> Optional[xr.DataArray]:
        """Create lazy xarray from dataset - auto-detects format."""
        if not LAZY_LOADING_AVAILABLE:
            return None

        fmt = detect_format(base_path)
        fovs = self._discover_fovs(base_path, fmt)

        if not fovs:
            print("No FOVs found")
            return None

        # print(f"Format: {fmt}, FOVs: {len(fovs)}")  # Disabled for profiling

        if fmt == "ome_tiff":
            return self._load_ome_tiff(base_path, fovs)
        else:
            return self._load_single_tiff(base_path, fovs)

    def _discover_fovs(self, base_path: Path, fmt: str) -> List[Dict]:
        """Discover all FOVs (region, fov) pairs."""
        fov_set = set()

        if fmt == "ome_tiff":
            ome_dir = base_path / "ome_tiff"
            if not ome_dir.exists():
                ome_dir = next((d for d in base_path.iterdir() if d.is_dir() and d.name.isdigit()), base_path)
            for f in ome_dir.glob("*.ome.tif*"):
                if m := FPATTERN_OME.search(f.name):
                    fov_set.add((m.group("r"), int(m.group("f"))))
        else:
            first_tp = next((d for d in base_path.iterdir() if d.is_dir() and d.name.isdigit()), None)
            if first_tp:
                for f in first_tp.glob("*.tiff"):
                    if m := FPATTERN.search(f.name):
                        fov_set.add((m.group("r"), int(m.group("f"))))

        return [{"region": r, "fov": f} for r, f in sorted(fov_set)]

    def _load_ome_tiff(self, base_path: Path, fovs: List[Dict]) -> Optional[xr.DataArray]:
        """Fast OME-TIFF: mmap via tifffile.aszarr, small chunks, no big graphs."""
        try:
            ome_dir = base_path / "ome_tiff"
            if not ome_dir.exists():
                ome_dir = next((d for d in base_path.iterdir() if d.is_dir() and d.name.isdigit()), base_path)

            file_index = {}
            for f in ome_dir.glob("*.ome.tif*"):
                if m := FPATTERN_OME.search(f.name):
                    file_index[(m.group("r"), int(m.group("f")))] = str(f)
            if not file_index:
                return None

            first_file = next(iter(file_index.values()))
            with tf.TiffFile(first_file) as tif:
                series = tif.series[0]
                axes = series.axes
                shape = series.shape
                shape_dict = dict(zip(axes, shape))
                n_t = shape_dict.get("T", 1)
                n_c = shape_dict.get("C", 1)
                n_z = shape_dict.get("Z", 1)
                height = shape_dict.get("Y", shape[-2])
                width = shape_dict.get("X", shape[-1])
                channel_names = []
                try:
                    if tif.ome_metadata:
                        import xml.etree.ElementTree as ET

                        root = ET.fromstring(tif.ome_metadata)
                        ns = {"ome": "http://www.openmicroscopy.org/Schemas/OME/2016-06"}
                        for ch in root.findall(".//ome:Channel", ns):
                            name = ch.get("Name") or ch.get("ID", "")
                            if name:
                                channel_names.append(name)
                except:
                    pass

            axis_map = {"T": "time", "Z": "z_level", "C": "channel", "Y": "y", "X": "x"}
            dims_base = [axis_map.get(ax, f"ax_{ax}") for ax in axes]
            coords_base = {axis_map.get(ax, f"ax_{ax}"): list(range(dim)) for ax, dim in zip(axes, shape)}

            # Per-axis chunking: 1 for non-spatial, full for spatial
            chunks = []
            for ax, dim in zip(axes, shape):
                if ax in ("X", "Y"):
                    chunks.append(dim)
                else:
                    chunks.append(1)

            luts = {
                i: wavelength_to_colormap(
                    extract_wavelength(channel_names[i] if i < len(channel_names) else f"Ch{i}"), i
                )
                for i in range(n_c)
            }
            n_fov = len(fovs)

            def open_zarr(path: str):
                tif = tf.TiffFile(path)
                zarr_store = tif.series[0].aszarr()
                return tif, zarr_store

            # One dask array per FOV, chunked per plane for fast single-slice reads
            fov_arrays = []
            tifs_kept = []
            for fov_idx in range(n_fov):
                region, fov = fovs[fov_idx]["region"], fovs[fov_idx]["fov"]
                filepath = file_index.get((region, fov))
                if not filepath:
                    fov_arrays.append(da.zeros((n_t, n_z, n_c, height, width), dtype=np.uint16))
                    continue
                tif, zarr_store = open_zarr(filepath)
                tifs_kept.append(tif)
                arr = da.from_zarr(zarr_store, chunks=tuple(chunks))
                # keep tif open to support mmap; rely on Python GC after viewer closes
                fov_arrays.append(arr)

            # Insert fov axis immediately after time if present, else at front
            if "time" in dims_base:
                fov_axis = dims_base.index("time") + 1
            else:
                fov_axis = 0
            full_array = da.stack(fov_arrays, axis=fov_axis)

            dims_full = dims_base[:fov_axis] + ["fov"] + dims_base[fov_axis:]
            coords_full = coords_base.copy()
            coords_full["fov"] = list(range(n_fov))

            xarr = xr.DataArray(full_array, dims=dims_full, coords=coords_full)
            # Ensure standard dims exist with singleton axes if missing
            for ax in ["time", "fov", "z_level", "channel", "y", "x"]:
                if ax not in xarr.dims:
                    xarr = xarr.expand_dims({ax: [0]})
            xarr = xarr.transpose("time", "fov", "z_level", "channel", "y", "x")
            xarr.attrs["luts"] = luts
            xarr.attrs["_open_tifs"] = tifs_kept
            return xarr
        except Exception as e:
            print(f"OME-TIFF load error: {e}")
            import traceback

            traceback.print_exc()
            return None

    def _load_single_tiff(self, base_path: Path, fovs: List[Dict]) -> Optional[xr.DataArray]:
        """Fast single-TIFF: per-plane on-demand loads with tiny LRU cache."""
        try:
            file_index = {}  # (t, region, fov, z, channel) -> filepath
            t_set, z_set, c_set = set(), set(), set()

            for tp_dir in sorted(base_path.iterdir()):
                if not (tp_dir.is_dir() and tp_dir.name.isdigit()):
                    continue
                t = int(tp_dir.name)
                t_set.add(t)
                for f in tp_dir.iterdir():
                    if f.suffix.lower() not in [".tiff", ".tif"]:
                        continue
                    if m := FPATTERN.search(f.name):
                        region, fov = m.group("r"), int(m.group("f"))
                        z, channel = int(m.group("z")), m.group("c")
                        z_set.add(z)
                        c_set.add(channel)
                        file_index[(t, region, fov, z, channel)] = str(f)

            if not file_index:
                return None

            times = sorted(t_set)
            z_levels = sorted(z_set)
            channels = sorted(c_set)
            n_t, n_fov, n_z, n_c = len(times), len(fovs), len(z_levels), len(channels)

            sample = next((p for p in file_index.values() if p.lower().endswith((".tif", ".tiff"))), None)
            if sample is None:
                return None
            try:
                with tf.TiffFile(sample) as tif:
                    height, width = tif.pages[0].shape[-2:]
            except Exception:
                return None

            luts = {i: wavelength_to_colormap(extract_wavelength(c), i) for i, c in enumerate(channels)}

            @lru_cache(maxsize=128)
            def load_plane(t, region, fov, z, channel):
                filepath = file_index.get((t, region, fov, z, channel))
                if not filepath:
                    return np.zeros((height, width), dtype=np.uint16)
                try:
                    ext = Path(filepath).suffix.lower()
                    if ext in [".tif", ".tiff"]:
                        with tf.TiffFile(filepath) as tif:
                            return tif.pages[0].asarray()
                except Exception:
                    pass
                return np.zeros((height, width), dtype=np.uint16)

            # Build on-demand loader via map_blocks over a dummy array
            chunks = (
                (1,) * n_t,
                (1,) * n_fov,
                (1,) * n_z,
                (1,) * n_c,
                (height,),
                (width,),
            )

            def _block_loader(block, block_info=None):
                loc = block_info[None]["chunk-location"]
                t_idx, f_idx, z_idx, c_idx = loc[0], loc[1], loc[2], loc[3]
                t = times[t_idx]
                region, fov = fovs[f_idx]["region"], fovs[f_idx]["fov"]
                z = z_levels[z_idx]
                channel = channels[c_idx]
                plane = load_plane(t, region, fov, z, channel)
                return plane.reshape(1, 1, 1, 1, height, width)

            dummy = da.zeros((n_t, n_fov, n_z, n_c, height, width), chunks=chunks, dtype=np.uint16)
            stacked = da.map_blocks(_block_loader, dummy, dtype=np.uint16, chunks=chunks)

            xarr = xr.DataArray(
                stacked,
                dims=["time", "fov", "z_level", "channel", "y", "x"],
                coords={"time": times, "fov": list(range(n_fov)), "z_level": z_levels, "channel": channels},
            )
            xarr.attrs["luts"] = luts
            return xarr
        except Exception as e:
            print(f"Single-TIFF load error: {e}")
            import traceback

            traceback.print_exc()
            return None

    def _set_ndv_data(self, data: xr.DataArray):
        """Update NDV viewer with lazy array."""
        if not NDV_AVAILABLE or not self.ndv_viewer:
            return

        luts = data.attrs.get("luts", {})
        channel_axis = data.dims.index("channel") if "channel" in data.dims else None

        # Recreate viewer with proper dimensions
        old_widget = self.ndv_viewer.widget()
        layout = self.layout()

        self.ndv_viewer = ndv.ArrayViewer(
            data,
            channel_axis=channel_axis,
            channel_mode="composite",
            luts=luts,
            visible_axes=("y", "x"),  # 2D display, sliders for rest
        )

        # Replace widget
        idx = layout.indexOf(old_widget)
        layout.removeWidget(old_widget)
        old_widget.deleteLater()
        layout.insertWidget(idx, self.ndv_viewer.widget(), 1)


class LightweightMainWindow(QMainWindow):
    """Main window with dark theme."""

    def __init__(self, dataset_path: str):
        super().__init__()
        self.setWindowTitle(f"NDViewer Lightweight - {Path(dataset_path).name}")
        self.setGeometry(100, 100, 720, 540)  # 4:3 aspect, smaller
        self._set_dark_theme()

        self.viewer = LightweightViewer(dataset_path)
        self.setCentralWidget(self.viewer)

    def _set_dark_theme(self):
        from PyQt5.QtWidgets import QStyleFactory

        self.setStyle(QStyleFactory.create("Fusion"))

        p = self.palette()
        p.setColor(QPalette.Window, QColor(53, 53, 53))
        p.setColor(QPalette.WindowText, QColor(255, 255, 255))
        p.setColor(QPalette.Base, QColor(35, 35, 35))
        p.setColor(QPalette.AlternateBase, QColor(53, 53, 53))
        p.setColor(QPalette.Text, QColor(255, 255, 255))
        p.setColor(QPalette.Button, QColor(53, 53, 53))
        p.setColor(QPalette.ButtonText, QColor(255, 255, 255))
        p.setColor(QPalette.Highlight, QColor(42, 130, 218))
        p.setColor(QPalette.HighlightedText, QColor(35, 35, 35))
        self.setPalette(p)


def main(dataset_path: str = None):
    """Launch lightweight viewer."""
    import sys

    app = QApplication(sys.argv)

    if dataset_path:
        # Direct launch with dataset
        window = LightweightMainWindow(dataset_path)
        window.show()
    else:
        # Show launcher window first
        launcher = LauncherWindow()
        launcher.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else None
    main(path)
