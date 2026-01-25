"""Zarr v3 saving using TensorStore.

This module provides Zarr v3 saving during acquisition
with sharding support, enabling direct zarr output without post-acquisition
conversion.

Key features:
- TensorStore-based async writes for high throughput
- Per-z-level sharding for efficient memory usage
- OME-NGFF HCS plate hierarchy support
- Blosc compression with LZ4/Zstd codecs
"""

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

import squid.logging
from control._def import ZarrChunkMode, ZarrCompression

log = squid.logging.get_logger(__name__)

# TensorStore is an optional dependency - import lazily to allow module import
# even when tensorstore is not installed
_tensorstore = None


def _get_tensorstore():
    """Lazily import tensorstore to avoid import errors when not installed."""
    global _tensorstore
    if _tensorstore is None:
        try:
            import tensorstore as ts

            _tensorstore = ts
        except ImportError:
            raise ImportError("TensorStore is required for Zarr v3 saving. " "Install it with: pip install tensorstore")
    return _tensorstore


@dataclass
class ZarrAcquisitionConfig:
    """Configuration for Zarr v3 saving during acquisition.

    Attributes:
        output_path: Base path for zarr output (e.g., /path/to/experiment.zarr)
        shape: Full dataset shape as (T, C, Z, Y, X) for 5D or (FOV, T, C, Z, Y, X) for 6D
        dtype: NumPy dtype for the data
        pixel_size_um: Physical pixel size in micrometers
        z_step_um: Z step size in micrometers (optional)
        time_increment_s: Time between timepoints in seconds (optional)
        channel_names: List of channel names for metadata
        chunk_mode: Chunk size mode (FULL_FRAME, TILED_512, TILED_256)
        compression: Compression preset (FAST, BALANCED, BEST)
        is_hcs: Whether this is an HCS (5D) or non-HCS (6D with FOV) dataset
        plate_name: Name for HCS plate (if is_hcs)
    """

    output_path: str
    shape: Tuple[int, ...]  # T, C, Z, Y, X (5D) or FOV, T, C, Z, Y, X (6D)
    dtype: np.dtype
    pixel_size_um: float
    z_step_um: Optional[float] = None
    time_increment_s: Optional[float] = None
    channel_names: List[str] = field(default_factory=list)
    channel_colors: List[str] = field(default_factory=list)  # Hex colors (e.g., "#FF0000")
    channel_wavelengths: List[Optional[int]] = field(default_factory=list)  # Wavelength in nm (None for BF)
    chunk_mode: ZarrChunkMode = ZarrChunkMode.FULL_FRAME
    compression: ZarrCompression = ZarrCompression.FAST
    is_hcs: bool = True  # Default to HCS (5D); non-HCS uses 6D with FOV dimension
    plate_name: str = "plate"

    @property
    def ndim(self) -> int:
        return len(self.shape)

    @property
    def fov_size(self) -> int:
        """FOV count for 6D datasets. Returns 1 for 5D datasets."""
        if self.ndim == 6:
            return self.shape[0]  # FOV is first dimension in 6D
        return 1

    @property
    def t_size(self) -> int:
        if self.ndim == 6:
            return self.shape[1]  # T is second dimension in 6D
        return self.shape[0]

    @property
    def c_size(self) -> int:
        if self.ndim == 6:
            return self.shape[2]  # C is third dimension in 6D
        return self.shape[1]

    @property
    def z_size(self) -> int:
        if self.ndim == 6:
            return self.shape[3]  # Z is fourth dimension in 6D
        return self.shape[2]

    @property
    def y_size(self) -> int:
        return self.shape[-2]

    @property
    def x_size(self) -> int:
        return self.shape[-1]


def _get_chunk_shape(config: ZarrAcquisitionConfig) -> Tuple[int, ...]:
    """Calculate chunk shape based on chunk mode.

    Args:
        config: Zarr acquisition configuration

    Returns:
        Chunk shape as (T, C, Z, Y, X) for 5D or (FOV, T, C, Z, Y, X) for 6D
    """
    if config.chunk_mode == ZarrChunkMode.FULL_FRAME:
        # Each chunk is a full image plane
        if config.ndim == 5:
            return (1, 1, 1, config.y_size, config.x_size)
        else:  # 6D: (FOV, T, C, Z, Y, X)
            return (1, 1, 1, 1, config.y_size, config.x_size)
    elif config.chunk_mode == ZarrChunkMode.TILED_512:
        if config.ndim == 5:
            return (1, 1, 1, 512, 512)
        else:
            return (1, 1, 1, 1, 512, 512)
    elif config.chunk_mode == ZarrChunkMode.TILED_256:
        if config.ndim == 5:
            return (1, 1, 1, 256, 256)
        else:
            return (1, 1, 1, 1, 256, 256)
    else:
        # Default to full frame
        if config.ndim == 5:
            return (1, 1, 1, config.y_size, config.x_size)
        else:
            return (1, 1, 1, 1, config.y_size, config.x_size)


def _get_shard_shape(config: ZarrAcquisitionConfig) -> Tuple[int, ...]:
    """Calculate shard shape for sharding configuration.

    For FAST compression: no sharding (shard_shape = chunk_shape) for maximum write speed.
    For BALANCED/BEST: per-z-level sharding for better file organization.

    Args:
        config: Zarr acquisition configuration

    Returns:
        Shard shape as (T, C, Z, Y, X) for 5D or (FOV, T, C, Z, Y, X) for 6D
    """
    # NONE/FAST mode: skip sharding for maximum write speed
    # Each chunk is its own file, eliminating shard coordination overhead
    if config.compression in (ZarrCompression.NONE, ZarrCompression.FAST):
        return _get_chunk_shape(config)

    # BALANCED/BEST: use per-z-level sharding for better file organization
    if config.ndim == 5:
        return (1, config.c_size, 1, config.y_size, config.x_size)
    else:  # 6D: (FOV, T, C, Z, Y, X) - shard contains all channels for one (fov, t, z)
        return (1, 1, config.c_size, 1, config.y_size, config.x_size)


def _get_compression_codec(compression: ZarrCompression) -> Optional[Dict[str, Any]]:
    """Get blosc codec configuration for compression preset.

    Args:
        compression: Compression preset enum

    Returns:
        Codec configuration dict for TensorStore, or None for no compression
    """
    if compression == ZarrCompression.NONE:
        return None
    elif compression == ZarrCompression.FAST:
        # LZ4 with minimal compression level and byte shuffle (faster than bitshuffle)
        # for maximum write throughput at ~800-1200 MB/s
        return {
            "name": "blosc",
            "configuration": {
                "cname": "lz4",
                "clevel": 1,
                "shuffle": "shuffle",
            },
        }
    elif compression == ZarrCompression.BALANCED:
        return {
            "name": "blosc",
            "configuration": {
                "cname": "zstd",
                "clevel": 3,
                "shuffle": "bitshuffle",
            },
        }
    elif compression == ZarrCompression.BEST:
        return {
            "name": "blosc",
            "configuration": {
                "cname": "zstd",
                "clevel": 9,
                "shuffle": "bitshuffle",
            },
        }
    else:
        # Default to fast
        return {
            "name": "blosc",
            "configuration": {
                "cname": "lz4",
                "clevel": 5,
                "shuffle": "bitshuffle",
            },
        }


def _dtype_to_zarr(dtype: np.dtype) -> str:
    """Convert numpy dtype to zarr v3 dtype string.

    Args:
        dtype: NumPy dtype

    Returns:
        Zarr v3 dtype string
    """
    dtype = np.dtype(dtype)
    # Map numpy dtypes to zarr v3 format
    dtype_map = {
        np.dtype("uint8"): "uint8",
        np.dtype("uint16"): "uint16",
        np.dtype("uint32"): "uint32",
        np.dtype("uint64"): "uint64",
        np.dtype("int8"): "int8",
        np.dtype("int16"): "int16",
        np.dtype("int32"): "int32",
        np.dtype("int64"): "int64",
        np.dtype("float32"): "float32",
        np.dtype("float64"): "float64",
    }
    if dtype in dtype_map:
        return dtype_map[dtype]
    raise ValueError(f"Unsupported dtype for zarr: {dtype}")


# HCS Plate Metadata Functions


def write_plate_metadata(
    plate_path: str,
    rows: List[str],
    cols: List[int],
    wells: List[Tuple[str, int]],
    plate_name: str = "plate",
) -> None:
    """Write OME-NGFF HCS plate metadata.

    Creates the plate-level .zattrs with well references.

    Args:
        plate_path: Path to plate.zarr directory
        rows: List of row names (e.g., ["A", "B", "C"])
        cols: List of column numbers (e.g., [1, 2, 3])
        wells: List of (row, col) tuples for wells with data
        plate_name: Name for the plate

    Raises:
        RuntimeError: If metadata files cannot be written
    """
    # Build well paths
    well_entries = []
    for row, col in wells:
        well_entries.append(
            {
                "path": f"{row}/{col}",
                "rowIndex": rows.index(row),
                "columnIndex": cols.index(col),
            }
        )

    plate_metadata = {
        "plate": {
            "version": "0.5",
            "name": plate_name,
            "rows": [{"name": r} for r in rows],
            "columns": [{"name": str(c)} for c in cols],
            "wells": well_entries,
        }
    }

    try:
        os.makedirs(plate_path, exist_ok=True)
        zattrs_path = os.path.join(plate_path, ".zattrs")
        with open(zattrs_path, "w") as f:
            json.dump(plate_metadata, f, indent=2)

        # Write zarr.json for v3
        zarr_json = {"zarr_format": 3, "node_type": "group"}
        zarr_json_path = os.path.join(plate_path, "zarr.json")
        with open(zarr_json_path, "w") as f:
            json.dump(zarr_json, f, indent=2)

        log.debug(f"Wrote plate metadata to {zattrs_path}")
    except OSError as e:
        log.error(f"Failed to write plate metadata to {plate_path}: {e}")
        raise RuntimeError(f"Failed to write plate metadata: {e}") from e


def write_well_metadata(
    well_path: str,
    fields: List[int],
) -> None:
    """Write OME-NGFF HCS well metadata.

    Creates the well-level .zattrs with field references.

    Args:
        well_path: Path to well directory (e.g., plate.zarr/A/1)
        fields: List of field indices (FOVs) in this well

    Raises:
        RuntimeError: If metadata files cannot be written
    """
    well_metadata = {
        "well": {
            "version": "0.5",
            "images": [{"path": str(f)} for f in fields],
        }
    }

    try:
        os.makedirs(well_path, exist_ok=True)
        zattrs_path = os.path.join(well_path, ".zattrs")
        with open(zattrs_path, "w") as f:
            json.dump(well_metadata, f, indent=2)

        # Write zarr.json for v3
        zarr_json = {"zarr_format": 3, "node_type": "group"}
        zarr_json_path = os.path.join(well_path, "zarr.json")
        with open(zarr_json_path, "w") as f:
            json.dump(zarr_json, f, indent=2)

        log.debug(f"Wrote well metadata to {zattrs_path}")
    except OSError as e:
        log.error(f"Failed to write well metadata to {well_path}: {e}")
        raise RuntimeError(f"Failed to write well metadata: {e}") from e


# Synchronous wrapper for use in job processing


class ZarrWriter:
    """Zarr v3 writer for use in job processing.

    Directly uses TensorStore without asyncio overhead for write operations.
    Only uses asyncio for initialization and finalization where it's unavoidable.
    """

    def __init__(self, config: ZarrAcquisitionConfig):
        """Initialize the synchronous writer.

        Args:
            config: Zarr acquisition configuration
        """
        self._config = config
        self._dataset = None
        self._pending_futures: List[Any] = []
        self._initialized = False
        self._finalized = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._owns_loop = False  # True if we created the loop ourselves

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        """Get or create the event loop (only used for init/finalize)."""
        if self._loop is None or self._loop.is_closed():
            try:
                self._loop = asyncio.get_event_loop()
                self._owns_loop = False  # Using existing loop
            except RuntimeError:
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
                self._owns_loop = True  # We created this loop
        return self._loop

    def initialize(self) -> None:
        """Initialize the zarr dataset (blocking).

        Uses asyncio only for TensorStore's async open operation.
        """
        if self._initialized:
            log.warning("Writer already initialized")
            return

        ts = _get_tensorstore()
        config = self._config

        # Build TensorStore spec
        os.makedirs(os.path.dirname(config.output_path), exist_ok=True)

        chunk_shape = _get_chunk_shape(config)
        shard_shape = _get_shard_shape(config)
        compression_codec = _get_compression_codec(config.compression)

        # Dimension names and transpose order depend on 5D vs 6D
        if config.ndim == 5:
            transpose_order = [4, 3, 2, 1, 0]  # Reverse order for C-contiguous layout
        else:
            transpose_order = [5, 4, 3, 2, 1, 0]  # Reverse order for C-contiguous layout

        # Determine if we need sharding (when chunk != shard)
        use_sharding = chunk_shape != shard_shape

        # Build inner codec chain (with or without compression)
        inner_codecs = [
            {"name": "transpose", "configuration": {"order": transpose_order}},
            {"name": "bytes", "configuration": {"endian": "little"}},
        ]
        if compression_codec is not None:
            inner_codecs.append(compression_codec)

        if use_sharding:
            codecs = [
                {
                    "name": "sharding_indexed",
                    "configuration": {
                        "chunk_shape": list(chunk_shape),
                        "codecs": inner_codecs,
                        "index_codecs": [
                            {"name": "bytes", "configuration": {"endian": "little"}},
                            {"name": "crc32c"},
                        ],
                    },
                }
            ]
            chunk_config = list(shard_shape)
        else:
            codecs = inner_codecs
            chunk_config = list(chunk_shape)

        spec = {
            "driver": "zarr3",
            "kvstore": {"driver": "file", "path": config.output_path},
            "metadata": {
                "shape": list(config.shape),
                "chunk_grid": {"name": "regular", "configuration": {"chunk_shape": chunk_config}},
                "chunk_key_encoding": {"name": "default"},
                "data_type": _dtype_to_zarr(config.dtype),
                "codecs": codecs,
                "fill_value": 0,
            },
        }

        log.info(
            f"Initializing Zarr v3 dataset: {config.output_path}, "
            f"shape={config.shape}, chunks={chunk_shape}, shards={shard_shape}, "
            f"compression={config.compression.value}"
        )

        # Use asyncio only for the TensorStore open operation
        async def _open():
            return await ts.open(spec, create=True, delete_existing=True)

        loop = self._get_loop()
        self._dataset = loop.run_until_complete(_open())
        self._initialized = True
        log.info("Zarr v3 dataset initialized successfully")

        # Write metadata synchronously (just file I/O)
        self._write_zarr_metadata()

    def _write_zarr_metadata(self) -> None:
        """Write OME-NGFF compliant metadata to .zattrs (synchronous file I/O)."""
        config = self._config

        # Build axes based on dimensionality
        if config.ndim == 5:
            axes = [
                {"name": "t", "type": "time", "unit": "second"},
                {"name": "c", "type": "channel"},
                {"name": "z", "type": "space", "unit": "micrometer"},
                {"name": "y", "type": "space", "unit": "micrometer"},
                {"name": "x", "type": "space", "unit": "micrometer"},
            ]
            coordinate_transforms = [
                {
                    "type": "scale",
                    "scale": [
                        config.time_increment_s or 1.0,
                        1.0,
                        config.z_step_um or 1.0,
                        config.pixel_size_um,
                        config.pixel_size_um,
                    ],
                }
            ]
        else:
            axes = [
                {"name": "fov", "type": "fov"},
                {"name": "t", "type": "time", "unit": "second"},
                {"name": "c", "type": "channel"},
                {"name": "z", "type": "space", "unit": "micrometer"},
                {"name": "y", "type": "space", "unit": "micrometer"},
                {"name": "x", "type": "space", "unit": "micrometer"},
            ]
            coordinate_transforms = [
                {
                    "type": "scale",
                    "scale": [
                        1.0,
                        config.time_increment_s or 1.0,
                        1.0,
                        config.z_step_um or 1.0,
                        config.pixel_size_um,
                        config.pixel_size_um,
                    ],
                }
            ]

        # Build channel metadata (omero)
        channels_meta = []
        for i, name in enumerate(config.channel_names or []):
            channel_info: Dict[str, Any] = {
                "label": name,
                "active": True,
            }
            if config.channel_colors and i < len(config.channel_colors):
                color = config.channel_colors[i]
                if color.startswith("#"):
                    color = color[1:]
                channel_info["color"] = color
            if config.channel_wavelengths and i < len(config.channel_wavelengths):
                wavelength = config.channel_wavelengths[i]
                if wavelength is not None:
                    channel_info["emission_wavelength"] = {
                        "value": wavelength,
                        "unit": "nanometer",
                    }
            # Add display window based on dtype
            dtype = np.dtype(config.dtype)
            if np.issubdtype(dtype, np.integer):
                info = np.iinfo(dtype)
                channel_info["window"] = {
                    "start": 0,
                    "end": info.max,
                    "min": 0,
                    "max": info.max,
                }
            elif np.issubdtype(dtype, np.floating):
                channel_info["window"] = {
                    "start": 0.0,
                    "end": 1.0,
                    "min": 0.0,
                    "max": 1.0,
                }
            channels_meta.append(channel_info)

        zattrs = {
            "multiscales": [
                {
                    "version": "0.4",
                    "name": os.path.basename(config.output_path),
                    "axes": axes,
                    "datasets": [
                        {
                            "path": "0",
                            "coordinateTransformations": coordinate_transforms,
                        }
                    ],
                    "coordinateTransformations": [{"type": "identity"}],
                }
            ],
            "omero": {
                "name": os.path.basename(config.output_path),
                "version": "0.4",
                "channels": channels_meta,
            },
            "_squid": {
                "pixel_size_um": config.pixel_size_um,
                "z_step_um": config.z_step_um,
                "time_increment_s": config.time_increment_s,
                "chunk_mode": config.chunk_mode.value,
                "compression": config.compression.value,
                "shape": list(config.shape),
                "dtype": str(config.dtype),
                "is_hcs": config.is_hcs,
                "acquisition_complete": False,
            },
        }

        zattrs_path = os.path.join(config.output_path, ".zattrs")
        try:
            os.makedirs(os.path.dirname(zattrs_path), exist_ok=True)
            with open(zattrs_path, "w") as f:
                json.dump(zattrs, f, indent=2)
            log.debug(f"Wrote OME-NGFF metadata to {zattrs_path}")
        except OSError as e:
            log.error(f"Failed to write zarr metadata to {zattrs_path}: {e}")
            raise RuntimeError(f"Failed to write zarr metadata: {e}") from e

    def write_frame(self, image: np.ndarray, t: int, c: int, z: int, fov: Optional[int] = None) -> None:
        """Write a single frame (non-blocking, queues for async write).

        This method directly uses TensorStore's write API without asyncio overhead.
        The write is queued and will complete asynchronously. Call wait_for_pending()
        or finalize() to ensure all writes are complete.

        Args:
            image: 2D image array (Y, X)
            t: Time point index
            c: Channel index
            z: Z-slice index
            fov: FOV index (required for 6D datasets, ignored for 5D)
        """
        if not self._initialized:
            raise RuntimeError("Writer not initialized. Call initialize() first.")
        if self._finalized:
            raise RuntimeError("Writer already finalized.")

        config = self._config

        # Validate indices
        if t < 0 or t >= config.shape[0 if config.ndim == 5 else 1]:
            raise ValueError(f"Time index {t} out of range")
        if c < 0 or c >= config.shape[1 if config.ndim == 5 else 2]:
            raise ValueError(f"Channel index {c} out of range")
        if z < 0 or z >= config.shape[2 if config.ndim == 5 else 3]:
            raise ValueError(f"Z index {z} out of range")

        if config.ndim == 6:
            if fov is None:
                raise ValueError("FOV index required for 6D dataset")
            if not (0 <= fov < config.fov_size):
                raise ValueError(f"FOV index {fov} out of range [0, {config.fov_size})")

        # Ensure image is correct dtype
        if image.dtype != config.dtype:
            image = image.astype(config.dtype)

        # Queue write directly using TensorStore (no asyncio overhead)
        if config.ndim == 5:
            future = self._dataset[t, c, z, :, :].write(image)
            log.debug(f"Queued write for frame t={t}, c={c}, z={z}")
        else:
            future = self._dataset[fov, t, c, z, :, :].write(image)
            log.debug(f"Queued write for frame fov={fov}, t={t}, c={c}, z={z}")

        self._pending_futures.append(future)

    def wait_for_pending(self, timeout_s: Optional[float] = None) -> int:
        """Wait for pending writes (blocking).

        Args:
            timeout_s: Optional timeout in seconds (not currently enforced)

        Returns:
            Number of writes completed
        """
        if not self._pending_futures:
            return 0

        count = len(self._pending_futures)
        log.debug(f"Waiting for {count} pending writes...")

        # Wait for all TensorStore futures in parallel using asyncio.gather
        # This is more efficient than waiting sequentially
        async def _wait_all():
            await asyncio.gather(*self._pending_futures)

        loop = self._get_loop()
        loop.run_until_complete(_wait_all())

        self._pending_futures.clear()
        log.debug(f"Completed {count} pending writes")
        return count

    @property
    def pending_write_count(self) -> int:
        """Number of writes currently pending."""
        return len(self._pending_futures)

    def finalize(self) -> None:
        """Finalize the dataset (blocking)."""
        if self._finalized:
            log.warning("Writer already finalized")
            return

        log.info("Finalizing Zarr v3 dataset...")

        # Wait for all pending writes
        self.wait_for_pending()

        # Update metadata with completion status
        zattrs_path = os.path.join(self._config.output_path, ".zattrs")
        try:
            if os.path.exists(zattrs_path):
                with open(zattrs_path, "r") as f:
                    zattrs = json.load(f)
                if "_squid" in zattrs:
                    zattrs["_squid"]["acquisition_complete"] = True
                with open(zattrs_path, "w") as f:
                    json.dump(zattrs, f, indent=2)
        except (OSError, json.JSONDecodeError) as e:
            log.error(f"Failed to finalize zarr metadata at {zattrs_path}: {e}")
            # Don't raise - data is already written, just log the metadata issue

        self._finalized = True
        self._cleanup_event_loop()
        log.info(f"Zarr v3 dataset finalized: {self._config.output_path}")

    def abort(self) -> None:
        """Abort and clean up (blocking)."""
        log.warning("Aborting Zarr writer...")

        # Clear pending futures (don't wait for them)
        self._pending_futures.clear()

        # Mark as incomplete in metadata
        zattrs_path = os.path.join(self._config.output_path, ".zattrs")
        try:
            if os.path.exists(zattrs_path):
                with open(zattrs_path, "r") as f:
                    zattrs = json.load(f)
                if "_squid" in zattrs:
                    zattrs["_squid"]["acquisition_complete"] = False
                    zattrs["_squid"]["aborted"] = True
                with open(zattrs_path, "w") as f:
                    json.dump(zattrs, f, indent=2)
        except (OSError, json.JSONDecodeError) as e:
            log.error(f"Failed to update abort metadata at {zattrs_path}: {e}")

        self._finalized = True
        self._cleanup_event_loop()
        log.warning(f"Zarr writer aborted: {self._config.output_path}")

    def _cleanup_event_loop(self) -> None:
        """Clean up the event loop to prevent resource leaks.

        Only closes the loop if we created it ourselves (via new_event_loop).
        Loops obtained from get_event_loop() are shared and should not be closed.
        """
        if self._loop is not None and self._owns_loop and not self._loop.is_closed():
            try:
                self._loop.close()
            except Exception as e:
                log.warning(f"Error closing event loop: {e}")
        self._loop = None
        self._owns_loop = False

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def is_finalized(self) -> bool:
        return self._finalized

    @property
    def config(self) -> ZarrAcquisitionConfig:
        return self._config
