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
    """Calculate shard shape for per-z-level sharding.

    Each shard contains one complete z-slice with all channels.
    This allows efficient finalization as soon as all channels for a z-level complete.

    Args:
        config: Zarr acquisition configuration

    Returns:
        Shard shape as (T, C, Z, Y, X) for 5D or (FOV, T, C, Z, Y, X) for 6D
    """
    if config.ndim == 5:
        return (1, config.c_size, 1, config.y_size, config.x_size)
    else:  # 6D: (FOV, T, C, Z, Y, X) - shard contains all channels for one (fov, t, z)
        return (1, 1, config.c_size, 1, config.y_size, config.x_size)


def _get_compression_codec(compression: ZarrCompression) -> Dict[str, Any]:
    """Get blosc codec configuration for compression preset.

    Args:
        compression: Compression preset enum

    Returns:
        Codec configuration dict for TensorStore
    """
    if compression == ZarrCompression.FAST:
        return {
            "name": "blosc",
            "configuration": {
                "cname": "lz4",
                "clevel": 5,
                "shuffle": "bitshuffle",
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


class ZarrWriterManager:
    """Manages TensorStore-based Zarr v3 saving.

    This class handles the lifecycle of a Zarr v3 dataset during acquisition:
    - Initialization: Creates the zarr structure with sharding configuration
    - Writing: Async frame writes using TensorStore futures
    - Finalization: Completes pending writes and writes OME-NGFF metadata

    The writer uses per-z-level sharding to efficiently stream frames
    while maintaining good read performance for visualization tools.

    Usage:
        config = ZarrAcquisitionConfig(...)
        manager = ZarrWriterManager(config)
        await manager.initialize()

        # During acquisition:
        await manager.write_frame(image, t=0, c=0, z=0)

        # At end:
        await manager.finalize()
    """

    def __init__(self, config: ZarrAcquisitionConfig):
        """Initialize the writer manager.

        Args:
            config: Zarr acquisition configuration
        """
        self.config = config
        self._dataset = None
        self._pending_futures: List[Any] = []
        self._initialized = False
        self._finalized = False

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def is_finalized(self) -> bool:
        return self._finalized

    async def initialize(self) -> None:
        """Initialize the zarr dataset with TensorStore.

        Creates the zarr v3 structure with sharding and compression configuration.
        """
        if self._initialized:
            log.warning("ZarrWriterManager already initialized")
            return

        ts = _get_tensorstore()

        # Ensure output directory exists
        os.makedirs(os.path.dirname(self.config.output_path), exist_ok=True)

        # Build TensorStore spec for zarr v3 with sharding
        chunk_shape = _get_chunk_shape(self.config)
        shard_shape = _get_shard_shape(self.config)
        compression_codec = _get_compression_codec(self.config.compression)

        # Dimension names and transpose order depend on 5D vs 6D
        if self.config.ndim == 5:
            dimension_names = ["t", "c", "z", "y", "x"]
            transpose_order = [4, 3, 2, 1, 0]  # Reverse order for C-contiguous layout
        else:  # 6D: FOV, T, C, Z, Y, X
            dimension_names = ["fov", "t", "c", "z", "y", "x"]
            transpose_order = [5, 4, 3, 2, 1, 0]  # Reverse order for C-contiguous layout

        # Determine if we need sharding (when chunk != shard)
        use_sharding = chunk_shape != shard_shape

        if use_sharding:
            codecs = [
                {
                    "name": "sharding_indexed",
                    "configuration": {
                        "chunk_shape": list(chunk_shape),
                        "codecs": [
                            {"name": "transpose", "configuration": {"order": transpose_order}},
                            {"name": "bytes", "configuration": {"endian": "little"}},
                            compression_codec,
                        ],
                        "index_codecs": [
                            {"name": "bytes", "configuration": {"endian": "little"}},
                            {"name": "crc32c"},
                        ],
                    },
                }
            ]
            chunk_config = list(shard_shape)
        else:
            codecs = [
                {"name": "transpose", "configuration": {"order": transpose_order}},
                {"name": "bytes", "configuration": {"endian": "little"}},
                compression_codec,
            ]
            chunk_config = list(chunk_shape)

        spec = {
            "driver": "zarr3",
            "kvstore": {"driver": "file", "path": self.config.output_path},
            "metadata": {
                "shape": list(self.config.shape),
                "chunk_grid": {"name": "regular", "configuration": {"chunk_shape": chunk_config}},
                "chunk_key_encoding": {"name": "default"},
                "data_type": _dtype_to_zarr(self.config.dtype),
                "codecs": codecs,
                "dimension_names": dimension_names,
            },
        }

        log.info(
            f"Initializing Zarr v3 dataset: {self.config.output_path}, "
            f"shape={self.config.shape}, chunks={chunk_shape}, shards={shard_shape}, "
            f"compression={self.config.compression.value}"
        )

        try:
            self._dataset = await ts.open(spec, create=True, delete_existing=True)
            self._initialized = True
            log.info(f"Zarr v3 dataset initialized successfully")
        except Exception as e:
            log.error(f"Failed to initialize Zarr v3 dataset: {e}")
            raise

        # Write zarr.json and initial metadata
        await self._write_zarr_metadata()

    async def _write_zarr_metadata(self) -> None:
        """Write OME-NGFF compliant metadata to .zattrs."""
        # Build axes based on dimensionality
        if self.config.ndim == 5:
            # Standard 5D: T, C, Z, Y, X
            axes = [
                {"name": "t", "type": "time", "unit": "second"},
                {"name": "c", "type": "channel"},
                {"name": "z", "type": "space", "unit": "micrometer"},
                {"name": "y", "type": "space", "unit": "micrometer"},
                {"name": "x", "type": "space", "unit": "micrometer"},
            ]
            scale = [
                self.config.time_increment_s or 1.0,
                1.0,  # channel has no physical scale
                self.config.z_step_um or 1.0,
                self.config.pixel_size_um,
                self.config.pixel_size_um,
            ]
        else:
            # 6D with FOV first: FOV, T, C, Z, Y, X
            axes = [
                {"name": "fov", "type": "index"},  # FOV dimension first (index type)
                {"name": "t", "type": "time", "unit": "second"},
                {"name": "c", "type": "channel"},
                {"name": "z", "type": "space", "unit": "micrometer"},
                {"name": "y", "type": "space", "unit": "micrometer"},
                {"name": "x", "type": "space", "unit": "micrometer"},
            ]
            scale = [
                1.0,  # fov has no physical scale
                self.config.time_increment_s or 1.0,
                1.0,  # channel has no physical scale
                self.config.z_step_um or 1.0,
                self.config.pixel_size_um,
                self.config.pixel_size_um,
            ]

        zattrs = {
            "multiscales": [
                {
                    "version": "0.5",
                    "name": "default",
                    "axes": axes,
                    "datasets": [
                        {
                            "path": ".",
                            "coordinateTransformations": [
                                {
                                    "type": "scale",
                                    "scale": scale,
                                }
                            ],
                        }
                    ],
                }
            ],
        }

        # Add omero metadata for channel visualization
        if self.config.channel_names:
            zattrs["omero"] = {"channels": [{"label": name, "active": True} for name in self.config.channel_names]}

        # Write .zattrs file
        zattrs_path = os.path.join(self.config.output_path, ".zattrs")
        with open(zattrs_path, "w") as f:
            json.dump(zattrs, f, indent=2)

        log.debug(f"Wrote OME-NGFF metadata to {zattrs_path}")

    async def write_frame(self, image: np.ndarray, t: int, c: int, z: int, fov: Optional[int] = None) -> None:
        """Write a single frame to the zarr dataset.

        Args:
            image: 2D image array (Y, X)
            t: Time point index
            c: Channel index
            z: Z-slice index
            fov: FOV index (required for 6D datasets, ignored for 5D)
        """
        if not self._initialized:
            raise RuntimeError("ZarrWriterManager not initialized. Call initialize() first.")
        if self._finalized:
            raise RuntimeError("ZarrWriterManager already finalized.")

        ts = _get_tensorstore()

        # Validate indices
        if not (0 <= t < self.config.t_size):
            raise ValueError(f"Time index {t} out of range [0, {self.config.t_size})")
        if not (0 <= c < self.config.c_size):
            raise ValueError(f"Channel index {c} out of range [0, {self.config.c_size})")
        if not (0 <= z < self.config.z_size):
            raise ValueError(f"Z index {z} out of range [0, {self.config.z_size})")

        # Validate FOV index for 6D datasets
        if self.config.ndim == 6:
            if fov is None:
                raise ValueError("FOV index required for 6D dataset")
            if not (0 <= fov < self.config.fov_size):
                raise ValueError(f"FOV index {fov} out of range [0, {self.config.fov_size})")

        # Ensure image is correct dtype
        if image.dtype != self.config.dtype:
            image = image.astype(self.config.dtype)

        # Write frame asynchronously
        if self.config.ndim == 5:
            # 5D: [t, c, z, y, x]
            future = self._dataset[t, c, z, :, :].write(image)
            log.debug(f"Queued write for frame t={t}, c={c}, z={z}")
        else:
            # 6D: [fov, t, c, z, y, x] - FOV is first dimension
            future = self._dataset[fov, t, c, z, :, :].write(image)
            log.debug(f"Queued write for frame fov={fov}, t={t}, c={c}, z={z}")

        self._pending_futures.append(future)

    async def wait_for_pending(self, timeout_s: Optional[float] = None) -> int:
        """Wait for all pending writes to complete.

        Args:
            timeout_s: Optional timeout in seconds

        Returns:
            Number of writes completed
        """
        ts = _get_tensorstore()

        if not self._pending_futures:
            return 0

        count = len(self._pending_futures)
        log.debug(f"Waiting for {count} pending writes...")

        try:
            # Await all pending futures
            await asyncio.gather(*self._pending_futures)
            self._pending_futures.clear()
            log.debug(f"Completed {count} pending writes")
            return count
        except Exception as e:
            log.error(f"Error during pending writes: {e}")
            raise

    @property
    def pending_write_count(self) -> int:
        """Number of writes currently pending."""
        return len(self._pending_futures)

    async def finalize(self) -> None:
        """Finalize the zarr dataset.

        Waits for all pending writes to complete and writes final metadata.
        """
        if self._finalized:
            log.warning("ZarrWriterManager already finalized")
            return

        log.info("Finalizing Zarr v3 dataset...")

        # Wait for all pending writes
        await self.wait_for_pending()

        # Update metadata with completion status
        zattrs_path = os.path.join(self.config.output_path, ".zattrs")
        if os.path.exists(zattrs_path):
            with open(zattrs_path, "r") as f:
                zattrs = json.load(f)
        else:
            zattrs = {}

        zattrs["_squid_metadata"] = {
            "acquisition_complete": True,
            "shape": list(self.config.shape),
            "dtype": str(self.config.dtype),
        }

        with open(zattrs_path, "w") as f:
            json.dump(zattrs, f, indent=2)

        self._finalized = True
        log.info(f"Zarr v3 dataset finalized: {self.config.output_path}")

    async def abort(self) -> None:
        """Abort the acquisition and clean up.

        Cancels pending writes and marks metadata as incomplete.
        """
        log.warning("Aborting Zarr writer...")

        # Clear pending futures (don't wait for them)
        self._pending_futures.clear()

        # Update metadata to indicate incomplete acquisition
        if self._initialized:
            zattrs_path = os.path.join(self.config.output_path, ".zattrs")
            if os.path.exists(zattrs_path):
                try:
                    with open(zattrs_path, "r") as f:
                        zattrs = json.load(f)
                    zattrs["_squid_metadata"] = {
                        "acquisition_complete": False,
                        "aborted": True,
                    }
                    with open(zattrs_path, "w") as f:
                        json.dump(zattrs, f, indent=2)
                except Exception as e:
                    log.error(f"Failed to update metadata on abort: {e}")

        self._finalized = True
        log.info("Zarr writer aborted")


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


def write_well_metadata(
    well_path: str,
    fields: List[int],
) -> None:
    """Write OME-NGFF HCS well metadata.

    Creates the well-level .zattrs with field references.

    Args:
        well_path: Path to well directory (e.g., plate.zarr/A/1)
        fields: List of field indices (FOVs) in this well
    """
    well_metadata = {
        "well": {
            "version": "0.5",
            "images": [{"path": str(f)} for f in fields],
        }
    }

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


# Synchronous wrapper for use in job processing


class SyncZarrWriter:
    """Synchronous wrapper around ZarrWriterManager for use in job processing.

    Provides a blocking interface for write operations, suitable for use
    in the synchronous job processing architecture.
    """

    def __init__(self, config: ZarrAcquisitionConfig):
        """Initialize the synchronous writer.

        Args:
            config: Zarr acquisition configuration
        """
        self._manager = ZarrWriterManager(config)
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        """Get or create the event loop."""
        if self._loop is None or self._loop.is_closed():
            try:
                self._loop = asyncio.get_event_loop()
            except RuntimeError:
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
        return self._loop

    def initialize(self) -> None:
        """Initialize the zarr dataset (blocking)."""
        loop = self._get_loop()
        loop.run_until_complete(self._manager.initialize())

    def write_frame(self, image: np.ndarray, t: int, c: int, z: int, fov: Optional[int] = None) -> None:
        """Write a single frame (blocking).

        Args:
            image: 2D image array (Y, X)
            t: Time point index
            c: Channel index
            z: Z-slice index
            fov: FOV index (required for 6D datasets, ignored for 5D)
        """
        loop = self._get_loop()
        loop.run_until_complete(self._manager.write_frame(image, t, c, z, fov))

    def wait_for_pending(self, timeout_s: Optional[float] = None) -> int:
        """Wait for pending writes (blocking).

        Args:
            timeout_s: Optional timeout in seconds

        Returns:
            Number of writes completed
        """
        loop = self._get_loop()
        return loop.run_until_complete(self._manager.wait_for_pending(timeout_s))

    @property
    def pending_write_count(self) -> int:
        """Number of writes currently pending."""
        return self._manager.pending_write_count

    def finalize(self) -> None:
        """Finalize the dataset (blocking)."""
        loop = self._get_loop()
        loop.run_until_complete(self._manager.finalize())

    def abort(self) -> None:
        """Abort and clean up (blocking)."""
        loop = self._get_loop()
        loop.run_until_complete(self._manager.abort())

    @property
    def is_initialized(self) -> bool:
        return self._manager.is_initialized

    @property
    def is_finalized(self) -> bool:
        return self._manager.is_finalized

    @property
    def config(self) -> ZarrAcquisitionConfig:
        return self._manager.config
