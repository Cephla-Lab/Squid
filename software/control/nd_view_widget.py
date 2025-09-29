import os
import glob
from typing import Dict, Optional, Tuple

import imageio
import napari
import numpy as np
import tifffile
import dask.array as da
from dask import delayed
from napari.utils.colormaps import Colormap, AVAILABLE_COLORMAPS
from qtpy.QtWidgets import QVBoxLayout, QWidget

from control._def import CHANNEL_COLORS_MAP, FILE_ID_PADDING, FILE_SAVING_OPTION, FileSavingOption


class NapariNDViewWidget(QWidget):
    """Napari widget for viewing N-D acquisitions (time, position, z) lazily from disk."""

    def __init__(self, objectiveStore, camera, contrastManager, grid_enabled: bool = False, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.objectiveStore = objectiveStore
        self.camera = camera
        self.contrastManager = contrastManager
        self.grid_enabled = grid_enabled

        self.image_width = 0
        self.image_height = 0
        self.dtype = np.uint8
        self.pixel_size_um = 1.0
        self.dz_um = 1.0
        self.Nz = 1

        self.layers_initialized = False
        self.viewer_scale_initialized = False
        self.dims_initialized = False

        self.channels = set()
        self.channel_layers: Dict[str, napari.layers.Image] = {}
        self.channel_rgb: Dict[str, bool] = {}
        self.channel_dtype: Dict[str, np.dtype] = {}
        self.channel_config_index: Dict[str, int] = {}
        self.channel_shape: Dict[str, Tuple[int, ...]] = {}

        self.position_map: Dict[Tuple[int, int], int] = {}
        self.position_metadata: Dict[int, Dict[str, float]] = {}
        self.acquisition_store: Dict[Tuple[str, int, int], Dict[str, Dict]] = {}

        self.available_time_indices = set()
        self.available_position_indices = set()
        self.max_time_index = -1
        self.max_position_index = -1

        self.initNapariViewer()

    def initNapariViewer(self):
        self.viewer = napari.Viewer(show=False)
        if self.grid_enabled:
            self.viewer.grid.enabled = True
        self.viewer.dims.axis_labels = ["time", "position", "z", "y", "x"]
        self.layout = QVBoxLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(self.viewer.window._qt_window)
        self.setLayout(self.layout)
        self._customize_viewer()

    def _customize_viewer(self):
        if hasattr(self.viewer.window._qt_viewer, "layerButtons"):
            self.viewer.window._qt_viewer.layerButtons.hide()

    # --- Public API used by acquisition code -------------------------------------------------

    def initChannels(self, channels):
        self.channels = set(channels)

    def initLayersShape(self, Nz, dz):
        pixel_size_um = self.objectiveStore.get_pixel_size_factor() * self.camera.get_pixel_size_binned_um()
        if Nz <= 0:
            Nz = 1
        self.Nz = Nz
        self.dz_um = dz if Nz > 1 and dz not in (0, None) else 1.0
        self.pixel_size_um = pixel_size_um

    def initLayers(self, image_height, image_width, image_dtype):
        self.viewer.layers.clear()
        self.layers_initialized = True
        self.viewer_scale_initialized = False
        self.dims_initialized = False

        self.image_height = image_height
        self.image_width = image_width
        self.dtype = np.dtype(image_dtype)

        self.channel_layers.clear()
        self.channel_rgb.clear()
        self.channel_dtype.clear()
        self.channel_config_index.clear()
        self.channel_shape.clear()
        self.position_map.clear()
        self.position_metadata.clear()
        self.acquisition_store.clear()
        self.available_time_indices.clear()
        self.available_position_indices.clear()
        self.max_time_index = -1
        self.max_position_index = -1

        self.viewer.dims.axis_labels = ["time", "position", "z", "y", "x"]
        self.viewer.dims.set_range(0, (0, 0, 1))
        self.viewer.dims.set_range(1, (0, 0, 1))
        self.viewer.dims.set_range(2, (0, max(self.Nz - 1, 0), 1))

    def updateLayers(self, image, x_mm, y_mm, k, channel_name, info):
        if not self.layers_initialized:
            self.initLayers(image.shape[0], image.shape[1], image.dtype)

        time_idx = info.time_point if info.time_point is not None else 0
        position_idx = self._get_position_index(info, x_mm, y_mm)
        rgb = len(image.shape) == 3

        self.available_time_indices.add(time_idx)
        self.available_position_indices.add(position_idx)
        self.max_time_index = max(self.max_time_index, time_idx)
        self.max_position_index = max(self.max_position_index, position_idx)

        self.channel_rgb[channel_name] = rgb
        self.channel_dtype[channel_name] = image.dtype
        self.channel_config_index[channel_name] = info.configuration_idx
        self.channel_shape[channel_name] = image.shape
        self._record_capture(channel_name, time_idx, position_idx, k, info, image)

        layer = self._ensure_channel_layer(channel_name, image)
        self._refresh_channel_layer_data(channel_name)
        layer.contrast_limits = self.contrastManager.get_limits(channel_name)

        if self.Nz > 1:
            try:
                self.viewer.dims.set_point(2, k)
            except Exception:
                pass

        if not self.dims_initialized:
            try:
                self.viewer.dims.set_point(0, time_idx)
                self.viewer.dims.set_point(1, position_idx)
            except Exception:
                pass
            self.dims_initialized = True

        self._update_dims_metadata()

        if not self.viewer_scale_initialized:
            self.resetView()
            self.viewer_scale_initialized = True

    # --- Helper methods ----------------------------------------------------------------------

    def _get_position_index(self, info, x_mm: float, y_mm: float) -> int:
        key = (info.region_id, info.fov)
        if key not in self.position_map:
            index = len(self.position_map)
            self.position_map[key] = index
            self.position_metadata[index] = {
                "region": info.region_id,
                "fov": info.fov,
                "x_mm": x_mm,
                "y_mm": y_mm,
            }
            print(f"[NapariNDViewWidget] new position index={index} for region={info.region_id} fov={info.fov}")
        return self.position_map[key]

    def _record_capture(self, channel_name: str, time_idx: int, position_idx: int, z_idx: int, info, image: np.ndarray) -> None:
        key = (channel_name, time_idx, position_idx)
        entry = self.acquisition_store.setdefault(
            key,
            {
                "paths": {},
                "stack_path": None,
                "config_name": info.configuration.name,
                "meta": {
                    "save_directory": info.save_directory,
                    "file_id": info.file_id,
                    "config_token": info.configuration.name.replace(" ", "_"),
                    "region_id": getattr(info, "region_id", 0),
                    "fov": getattr(info, "fov", 0),
                    "experiment_path": getattr(info, "experiment_path", info.save_directory),
                    "time_point": getattr(info, "time_point", 0),
                },
            },
        )
        entry["config_name"] = info.configuration.name

        path = self._infer_capture_path(info)
        if FILE_SAVING_OPTION == FileSavingOption.INDIVIDUAL_IMAGES:
            if path:
                entry["paths"][z_idx] = path
                print(
                    f"[NapariNDViewWidget] map file channel={channel_name} t={time_idx} p={position_idx} z={z_idx} -> {path}"
                )
        else:
            if path:
                entry["stack_path"] = path

    def _infer_capture_path(self, info) -> Optional[str]:
        if FILE_SAVING_OPTION == FileSavingOption.INDIVIDUAL_IMAGES:
            channel_token = info.configuration.name.replace(" ", "_")
            prefix = f"{info.file_id}_{channel_token}"
            pattern = os.path.join(info.save_directory, prefix + ".*")
            matches = glob.glob(pattern)
            if matches:
                latest = max(matches, key=os.path.getmtime)
                return latest
            return None
        if FILE_SAVING_OPTION == FileSavingOption.MULTI_PAGE_TIFF:
            return os.path.join(info.save_directory, f"{info.region_id}_{info.fov:0{FILE_ID_PADDING}}_stack.tiff")
        if FILE_SAVING_OPTION == FileSavingOption.OME_TIFF:
            from control.core import ome_tiff_writer

            return os.path.join(
                ome_tiff_writer.ome_output_folder(info),
                ome_tiff_writer.ome_base_name(info) + ".ome.tiff",
            )
        return None

    def _ensure_channel_layer(self, channel_name: str, image: np.ndarray):
        if channel_name in self.channel_layers:
            return self.channel_layers[channel_name]

        self.channels.add(channel_name)
        rgb = self.channel_rgb.get(channel_name, len(image.shape) == 3)

        if rgb:
            color = None
        else:
            channel_info = CHANNEL_COLORS_MAP.get(
                self.extractWavelength(channel_name), {"hex": 0xFFFFFF, "name": "gray"}
            )
            if channel_info["name"] in AVAILABLE_COLORMAPS:
                color = AVAILABLE_COLORMAPS[channel_info["name"]]
            else:
                color = self._generate_colormap(channel_info)

        data = self._build_channel_dask(channel_name)
        limits = self.getContrastLimits(self.dtype)
        scale = (1, 1, self.dz_um, self.pixel_size_um, self.pixel_size_um)

        layer = self.viewer.add_image(
            data,
            name=channel_name,
            visible=True,
            rgb=rgb,
            colormap=color,
            contrast_limits=limits,
            blending="additive",
            scale=scale,
        )
        layer.events.contrast_limits.connect(self.signalContrastLimits)
        self.channel_layers[channel_name] = layer
        return layer

    def _refresh_channel_layer_data(self, channel_name: str) -> None:
        layer = self.channel_layers.get(channel_name)
        if not layer:
            return
        layer.data = self._build_channel_dask(channel_name)
        layer.refresh()

    def _update_dims_metadata(self) -> None:
        labels = ["time", "position", "z", "y", "x"]
        try:
            if list(self.viewer.dims.axis_labels) != labels:
                self.viewer.dims.axis_labels = labels
        except Exception:
            pass
        time_stop = max(self.max_time_index, 0)
        position_stop = max(self.max_position_index, 0)
        z_stop = max(self.Nz - 1, 0)
        self.viewer.dims.set_range(0, (0, time_stop, 1))
        self.viewer.dims.set_range(1, (0, position_stop, 1))
        self.viewer.dims.set_range(2, (0, z_stop, 1))

    def _build_channel_dask(self, channel_name: str):
        dtype = self.channel_dtype.get(channel_name, self.dtype)
        stack_shape = self._stack_shape(channel_name)
        time_count = max(self.max_time_index + 1, 1)
        position_count = max(self.max_position_index + 1, 1)

        blocks = []
        for t_idx in range(time_count):
            row = []
            for p_idx in range(position_count):
                entry = self.acquisition_store.get((channel_name, t_idx, p_idx))
                print(
                    f"[NapariNDViewWidget] chunk request channel={channel_name} t={t_idx} p={p_idx} has_entry={entry is not None}"
                )
                if entry:
                    arr = self._load_stack_from_entry(channel_name, entry, dtype, t_idx)
                else:
                    arr = np.zeros(stack_shape, dtype=dtype)

                if len(stack_shape) == 4:
                    arr = arr[np.newaxis, np.newaxis, ...]
                else:
                    arr = arr[np.newaxis, np.newaxis, ...]
                row.append(arr)
            row_stacked = np.concatenate(row, axis=1)
            blocks.append(row_stacked)
        full = np.concatenate(blocks, axis=0)
        return da.from_array(full, chunks=full.shape)

    def _stack_shape(self, channel_name: str):
        nz = max(1, self.Nz or 1)
        height = self.image_height
        width = self.image_width
        if self.channel_rgb.get(channel_name, False):
            channels = (
                self.channel_shape[channel_name][2]
                if self.channel_shape.get(channel_name) and len(self.channel_shape[channel_name]) >= 3
                else 3
            )
            return (nz, height, width, channels)
        return (nz, height, width)

    def _empty_stack(self, channel_name: str, dtype):
        return np.zeros(self._stack_shape(channel_name), dtype=dtype)

    def _load_stack_from_entry(self, channel_name: str, entry: dict, dtype, time_idx: int) -> np.ndarray:
        if FILE_SAVING_OPTION == FileSavingOption.INDIVIDUAL_IMAGES:
            return self._load_from_individual_images(channel_name, entry, dtype)
        if FILE_SAVING_OPTION == FileSavingOption.MULTI_PAGE_TIFF:
            return self._load_from_multi_page_tiff(channel_name, entry, dtype)
        if FILE_SAVING_OPTION == FileSavingOption.OME_TIFF:
            channel_idx = self.channel_config_index.get(channel_name, 0)
            return self._load_from_ome_tiff(channel_name, entry, channel_idx, dtype, time_idx)
        return np.zeros(self._stack_shape(channel_name), dtype=dtype)

    def _load_from_individual_images(self, channel_name: str, entry: dict, dtype) -> np.ndarray:
        stack = self._empty_stack(channel_name, dtype)
        for z_idx in range(stack.shape[0]):
            path = entry["paths"].get(z_idx)
            if not path or not os.path.exists(path):
                path = self._resolve_individual_path(entry, z_idx)
            exists = path is not None and os.path.exists(path)
            # print(
            #     f"[NapariNDViewWidget] load individual image channel={channel_name} z={z_idx} path={path} exists={exists}"
            # )
            if exists:
                try:
                    frame = imageio.imread(path)
                    stack[z_idx] = np.asarray(frame, dtype=dtype)
                    entry["paths"][z_idx] = path
                    continue
                except Exception as exc:
                    print(f"[NapariNDViewWidget] failed to load {path}: {exc}")
            print(f"[NapariNDViewWidget] no image data for channel={channel_name} z={z_idx}; returning zeros")
            stack[z_idx] = np.zeros(stack[z_idx].shape, dtype=dtype)
        return stack

    def _resolve_individual_path(self, entry: dict, z_idx: int) -> Optional[str]:
        meta = entry.get("meta")
        if not meta:
            return None
        base = os.path.join(meta["save_directory"], f"{meta['file_id']}_{meta['config_token']}")
        candidates = [base + ext for ext in (".tiff", ".tif", ".png", ".bmp", ".jpg")]
        for candidate in candidates:
            if os.path.exists(candidate):
                entry["paths"][z_idx] = candidate
                return candidate
        glob_matches = glob.glob(base + ".*")
        if glob_matches:
            latest = max(glob_matches, key=os.path.getmtime)
            entry["paths"][z_idx] = latest
            return latest
        return None

    def _resolve_multipage_path(self, entry: dict) -> Optional[str]:
        meta = entry.get("meta")
        if not meta:
            return entry.get("stack_path")
        path = entry.get("stack_path")
        if path and os.path.exists(path):
            return path
        base = os.path.join(
            meta["save_directory"],
            f"{meta['region_id']}_{int(meta['fov']):0{FILE_ID_PADDING}}_stack.tiff",
        )
        entry["stack_path"] = base
        return base

    def _resolve_ome_path(self, entry: dict) -> Optional[str]:
        meta = entry.get("meta")
        if not meta:
            return entry.get("stack_path")
        path = entry.get("stack_path")
        if path and os.path.exists(path):
            return path
        folder = os.path.join(meta["experiment_path"], "ome_tiff")
        base = os.path.join(folder, f"{meta['region_id']}_{int(meta['fov']):0{FILE_ID_PADDING}}.ome.tiff")
        entry["stack_path"] = base
        return base

    def _load_from_multi_page_tiff(self, channel_name: str, entry: dict, dtype) -> np.ndarray:
        stack = self._empty_stack(channel_name, dtype)
        path = self._resolve_multipage_path(entry)
        if not path or not os.path.exists(path):
            print(
                f"[NapariNDViewWidget] missing multi-page TIFF for channel={channel_name} path={path}; returning zeros"
            )
            return stack
        try:
            with tifffile.TiffFile(path) as tif:
                for page in tif.pages:
                    description = page.tags.get("ImageDescription")
                    metadata = {}
                    if description is not None:
                        try:
                            metadata = json.loads(description.value)
                        except Exception:
                            metadata = {}
                    if metadata.get("channel") != entry.get("config_name"):
                        continue
                    z_level = metadata.get("z_level")
                    if z_level is None or z_level >= stack.shape[0]:
                        continue
                    try:
                        stack[z_level] = page.asarray().astype(dtype, copy=False)
                    except Exception as exc:
                        print(
                            f"[NapariNDViewWidget] failed reading multi-page TIFF channel={channel_name} z={z_level}: {exc}"
                        )
        except Exception as exc:
            print(f"[NapariNDViewWidget] error opening TIFF {path}: {exc}")
            return stack
        return stack

    def _load_from_ome_tiff(self, channel_name: str, entry: dict, channel_idx: int, dtype, time_idx: int) -> np.ndarray:
        stack = self._empty_stack(channel_name, dtype)
        path = self._resolve_ome_path(entry)
        if not path or not os.path.exists(path):
            print(f"[NapariNDViewWidget] missing OME-TIFF for channel={channel_name} path={path}; returning zeros")
            return stack
        try:
            with tifffile.TiffFile(path) as tif:
                series = tif.series[0]
                axes = getattr(series, "axes", "")
                shape = series.shape
                axis_index = {ax: i for i, ax in enumerate(axes)}
                z_dim = axis_index.get("Z")
                max_z = stack.shape[0]
                if z_dim is not None:
                    max_z = min(stack.shape[0], shape[z_dim])
                for z_idx in range(max_z):
                    try:
                        key = {}
                        if "T" in axis_index:
                            if time_idx >= shape[axis_index["T"]]:
                                continue
                            key["T"] = time_idx
                        if "Z" in axis_index:
                            key["Z"] = z_idx
                        if "C" in axis_index:
                            if channel_idx >= shape[axis_index["C"]]:
                                continue
                            key["C"] = channel_idx
                        plane = series.asarray(key)
                        plane = np.asarray(plane, dtype=dtype)
                        plane = np.squeeze(plane)
                        stack[z_idx] = plane.copy()
                    except Exception as exc:
                        print(
                            f"[NapariNDViewWidget] failed reading OME-TIFF channel={channel_name} t={time_idx} z={z_idx}: {exc}"
                        )
        except Exception as exc:
            print(f"[NapariNDViewWidget] error opening OME-TIFF {path}: {exc}")
        return stack

    # --- Utilities ---------------------------------------------------------------------------

    def extractWavelength(self, name):
        parts = name.split()
        if "Fluorescence" in parts:
            index = parts.index("Fluorescence") + 1
            if index < len(parts):
                return parts[index].split()[0]
        for color in ["R", "G", "B"]:
            if color in parts or f"full_{color}" in parts:
                return color
        return None

    def _generate_colormap(self, channel_info):
        c0 = (0, 0, 0)
        c1 = (
            ((channel_info["hex"] >> 16) & 0xFF) / 255,
            ((channel_info["hex"] >> 8) & 0xFF) / 255,
            (channel_info["hex"] & 0xFF) / 255,
        )
        return Colormap(colors=[c0, c1], controls=[0, 1], name=channel_info["name"])

    def getContrastLimits(self, dtype):
        return self.contrastManager.get_default_limits()

    def signalContrastLimits(self, event):
        layer = event.source
        min_val, max_val = map(float, layer.contrast_limits)
        self.contrastManager.update_limits(layer.name, min_val, max_val)

    def resetView(self):
        self.viewer.reset_view()
        for layer in self.viewer.layers:
            layer.refresh()

    def activate(self):
        self.viewer.window.activate()
