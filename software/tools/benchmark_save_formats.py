#!/usr/bin/env python3
"""Benchmark different save formats to compare performance.

Usage:
    cd software
    python tools/benchmark_save_formats.py
"""

import os
import sys
import tempfile
import time

import numpy as np

# Add software dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from control.core.zarr_writer import ZarrWriter, ZarrAcquisitionConfig
from control._def import ZarrChunkMode, ZarrCompression


def benchmark_zarr_write(num_frames: int, image_shape: tuple, detailed: bool = False) -> float:
    """Benchmark Zarr v3 writes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "test.zarr")

        # Simulate a simple acquisition: 1 timepoint, num_frames channels, 1 z
        config = ZarrAcquisitionConfig(
            output_path=output_path,
            shape=(1, num_frames, 1, image_shape[0], image_shape[1]),
            dtype=np.uint16,
            pixel_size_um=1.0,
            chunk_mode=ZarrChunkMode.FULL_FRAME,
            compression=ZarrCompression.FAST,
        )

        writer = ZarrWriter(config)

        init_start = time.perf_counter()
        writer.initialize()
        init_time = time.perf_counter() - init_start

        # Generate test image
        image = np.random.randint(0, 65535, image_shape, dtype=np.uint16)

        queue_start = time.perf_counter()
        for c in range(num_frames):
            writer.write_frame(image, t=0, c=c, z=0)
        queue_time = time.perf_counter() - queue_start

        # Wait for pending writes
        wait_start = time.perf_counter()
        writer.wait_for_pending()
        wait_time = time.perf_counter() - wait_start

        finalize_start = time.perf_counter()
        writer.finalize()
        finalize_time = time.perf_counter() - finalize_start

        total_time = init_time + queue_time + wait_time + finalize_time

        if detailed:
            print(
                f"    Init: {init_time*1000:.1f}ms, Queue: {queue_time*1000:.1f}ms, "
                f"Wait: {wait_time*1000:.1f}ms, Finalize: {finalize_time*1000:.1f}ms"
            )

        return total_time


def benchmark_tiff_write(num_frames: int, image_shape: tuple) -> float:
    """Benchmark TIFF writes (similar to SaveImageJob)."""
    import tifffile

    with tempfile.TemporaryDirectory() as tmpdir:
        # Generate test image
        image = np.random.randint(0, 65535, image_shape, dtype=np.uint16)

        start = time.perf_counter()
        for i in range(num_frames):
            output_path = os.path.join(tmpdir, f"image_{i:04d}.tiff")
            tifffile.imwrite(output_path, image)

        elapsed = time.perf_counter() - start
        return elapsed


def benchmark_zarr_no_compression(num_frames: int, image_shape: tuple, detailed: bool = False) -> float:
    """Benchmark Zarr v3 writes with NONE compression (no compression)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "test.zarr")

        # Use the actual NONE compression mode
        config = ZarrAcquisitionConfig(
            output_path=output_path,
            shape=(1, num_frames, 1, image_shape[0], image_shape[1]),
            dtype=np.uint16,
            pixel_size_um=1.0,
            chunk_mode=ZarrChunkMode.FULL_FRAME,
            compression=ZarrCompression.NONE,
        )

        writer = ZarrWriter(config)

        init_start = time.perf_counter()
        writer.initialize()
        init_time = time.perf_counter() - init_start

        image = np.random.randint(0, 65535, image_shape, dtype=np.uint16)

        queue_start = time.perf_counter()
        for c in range(num_frames):
            writer.write_frame(image, t=0, c=c, z=0)
        queue_time = time.perf_counter() - queue_start

        wait_start = time.perf_counter()
        writer.wait_for_pending()
        wait_time = time.perf_counter() - wait_start

        finalize_start = time.perf_counter()
        writer.finalize()
        finalize_time = time.perf_counter() - finalize_start

        total_time = init_time + queue_time + wait_time + finalize_time

        if detailed:
            print(
                f"    Init: {init_time*1000:.1f}ms, Queue: {queue_time*1000:.1f}ms, "
                f"Wait: {wait_time*1000:.1f}ms, Finalize: {finalize_time*1000:.1f}ms"
            )

        return total_time


def benchmark_zarr_no_sharding(num_frames: int, image_shape: tuple) -> float:
    """Benchmark Zarr v3 writes with compression but no sharding."""
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "test.zarr")

        from control.core.zarr_writer import ZarrWriter
        import tensorstore as ts
        import asyncio

        config = ZarrAcquisitionConfig(
            output_path=output_path,
            shape=(1, num_frames, 1, image_shape[0], image_shape[1]),
            dtype=np.uint16,
            pixel_size_um=1.0,
            chunk_mode=ZarrChunkMode.FULL_FRAME,
            compression=ZarrCompression.FAST,
        )

        writer = ZarrWriter(config)

        # Use compression but no sharding (each chunk is also a shard)
        spec = {
            "driver": "zarr3",
            "kvstore": {"driver": "file", "path": output_path},
            "metadata": {
                "shape": list(config.shape),
                "chunk_grid": {
                    "name": "regular",
                    "configuration": {"chunk_shape": [1, 1, 1, image_shape[0], image_shape[1]]},
                },
                "data_type": "uint16",
                "codecs": [
                    {"name": "transpose", "configuration": {"order": [4, 3, 2, 1, 0]}},
                    {"name": "bytes", "configuration": {"endian": "little"}},
                    {"name": "blosc", "configuration": {"cname": "lz4", "clevel": 1, "shuffle": "shuffle"}},
                ],
                "fill_value": 0,
            },
        }

        async def _open():
            return await ts.open(spec, create=True, delete_existing=True)

        loop = asyncio.new_event_loop()
        writer._dataset = loop.run_until_complete(_open())
        writer._initialized = True
        writer._loop = loop

        image = np.random.randint(0, 65535, image_shape, dtype=np.uint16)

        start = time.perf_counter()
        for c in range(num_frames):
            writer.write_frame(image, t=0, c=c, z=0)
        writer.wait_for_pending()
        elapsed = time.perf_counter() - start
        return elapsed


def main():
    print("Save Format Benchmark")
    print("=" * 50)

    # Test parameters
    num_frames = 20
    image_shape = (2048, 2048)

    print(f"Parameters: {num_frames} frames, {image_shape[0]}x{image_shape[1]} uint16")
    print()

    # Warm up
    print("Warming up...")
    benchmark_tiff_write(2, (512, 512))
    benchmark_zarr_write(2, (512, 512))

    # Run benchmarks
    print(f"\nBenchmarking TIFF writes ({num_frames} frames)...")
    tiff_time = benchmark_tiff_write(num_frames, image_shape)
    print(f"  TIFF: {tiff_time:.3f}s ({num_frames/tiff_time:.1f} fps)")

    print(f"\nBenchmarking Zarr v3 writes ({num_frames} frames)...")
    zarr_time = benchmark_zarr_write(num_frames, image_shape, detailed=True)
    print(f"  Zarr: {zarr_time:.3f}s ({num_frames/zarr_time:.1f} fps)")

    print(f"\nBenchmarking Zarr v3 NO compression ({num_frames} frames)...")
    zarr_nc_time = benchmark_zarr_no_compression(num_frames, image_shape, detailed=True)
    print(f"  Zarr (no comp): {zarr_nc_time:.3f}s ({num_frames/zarr_nc_time:.1f} fps)")

    print(f"\nBenchmarking Zarr v3 NO sharding ({num_frames} frames)...")
    zarr_ns_time = benchmark_zarr_no_sharding(num_frames, image_shape)
    print(f"  Zarr (no shard): {zarr_ns_time:.3f}s ({num_frames/zarr_ns_time:.1f} fps)")

    print()
    print(f"Ratio (Zarr/TIFF): {zarr_time/tiff_time:.2f}x")
    print(f"Ratio (Zarr-nc/TIFF): {zarr_nc_time/tiff_time:.2f}x")
    print(f"Ratio (Zarr-ns/TIFF): {zarr_ns_time/tiff_time:.2f}x")

    if zarr_time > tiff_time * 1.5:
        print("\n⚠️  Zarr is significantly slower than TIFF")
    elif zarr_time < tiff_time:
        print("\n✓ Zarr is faster than TIFF")
    else:
        print("\n~ Performance is comparable")


if __name__ == "__main__":
    main()
