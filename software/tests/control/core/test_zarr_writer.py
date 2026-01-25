"""Tests for Zarr v3 saving using TensorStore.

These tests verify the ZarrWriterManager and related functionality
for Zarr v3 saving during acquisition.
"""

import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import pytest

import squid.abc
from control._def import ZarrChunkMode, ZarrCompression
from control.core.job_processing import (
    CaptureInfo,
    JobImage,
    JobRunner,
    ZarrWriterInfo,
    SaveZarrJob,
)
from control.models import AcquisitionChannel, CameraSettings, IlluminationSettings


# Skip all tests if tensorstore is not installed
pytest.importorskip("tensorstore")


def make_test_capture_info(
    region_id: str = "A1",
    fov: int = 0,
    z_index: int = 0,
    config_idx: int = 0,
    time_point: int = 0,
) -> CaptureInfo:
    """Create a minimal CaptureInfo for testing."""
    return CaptureInfo(
        position=squid.abc.Pos(x_mm=0.0, y_mm=0.0, z_mm=0.0, theta_rad=None),
        z_index=z_index,
        capture_time=time.time(),
        configuration=AcquisitionChannel(
            name="BF LED matrix full",
            illumination_settings=IlluminationSettings(
                illumination_channels=["LED"],
                intensity={"LED": 50.0},
                z_offset_um=0.0,
            ),
            camera_settings={
                "main": CameraSettings(
                    exposure_time_ms=10.0,
                    gain_mode=1.0,
                )
            },
        ),
        save_directory="/tmp/test",
        file_id=f"test_{fov}_{z_index}",
        region_id=region_id,
        fov=fov,
        configuration_idx=config_idx,
        time_point=time_point,
    )


class TestZarrAcquisitionConfig:
    """Tests for ZarrAcquisitionConfig dataclass."""

    def test_config_creation(self):
        from control.core.zarr_writer import ZarrAcquisitionConfig

        config = ZarrAcquisitionConfig(
            output_path="/tmp/test.zarr",
            shape=(2, 3, 4, 100, 100),
            dtype=np.uint16,
            pixel_size_um=0.5,
        )

        assert config.t_size == 2
        assert config.c_size == 3
        assert config.z_size == 4
        assert config.y_size == 100
        assert config.x_size == 100
        assert config.pixel_size_um == 0.5

    def test_config_with_channel_names(self):
        from control.core.zarr_writer import ZarrAcquisitionConfig

        config = ZarrAcquisitionConfig(
            output_path="/tmp/test.zarr",
            shape=(1, 2, 1, 50, 50),
            dtype=np.uint16,
            pixel_size_um=1.0,
            channel_names=["DAPI", "GFP"],
        )

        assert config.channel_names == ["DAPI", "GFP"]

    def test_config_with_channel_metadata(self):
        """Test config with full channel metadata (colors and wavelengths)."""
        from control.core.zarr_writer import ZarrAcquisitionConfig

        config = ZarrAcquisitionConfig(
            output_path="/tmp/test.zarr",
            shape=(1, 3, 1, 50, 50),
            dtype=np.uint16,
            pixel_size_um=1.0,
            channel_names=["DAPI", "GFP", "Brightfield"],
            channel_colors=["#20ADF8", "#1FFF00", "#FFFFFF"],
            channel_wavelengths=[405, 488, None],  # None for brightfield
        )

        assert config.channel_names == ["DAPI", "GFP", "Brightfield"]
        assert config.channel_colors == ["#20ADF8", "#1FFF00", "#FFFFFF"]
        assert config.channel_wavelengths == [405, 488, None]

    def test_config_compression_presets(self):
        from control.core.zarr_writer import ZarrAcquisitionConfig

        for preset in [ZarrCompression.FAST, ZarrCompression.BALANCED, ZarrCompression.BEST]:
            config = ZarrAcquisitionConfig(
                output_path="/tmp/test.zarr",
                shape=(1, 1, 1, 50, 50),
                dtype=np.uint16,
                pixel_size_um=1.0,
                compression=preset,
            )
            assert config.compression == preset


class TestChunkShapeCalculation:
    """Tests for chunk shape calculation functions."""

    def test_full_frame_chunks(self):
        from control.core.zarr_writer import ZarrAcquisitionConfig, _get_chunk_shape

        config = ZarrAcquisitionConfig(
            output_path="/tmp/test.zarr",
            shape=(2, 3, 4, 2048, 2048),
            dtype=np.uint16,
            pixel_size_um=0.5,
            chunk_mode=ZarrChunkMode.FULL_FRAME,
        )

        chunk_shape = _get_chunk_shape(config)
        assert chunk_shape == (1, 1, 1, 2048, 2048)

    def test_tiled_512_chunks(self):
        from control.core.zarr_writer import ZarrAcquisitionConfig, _get_chunk_shape

        config = ZarrAcquisitionConfig(
            output_path="/tmp/test.zarr",
            shape=(2, 3, 4, 2048, 2048),
            dtype=np.uint16,
            pixel_size_um=0.5,
            chunk_mode=ZarrChunkMode.TILED_512,
        )

        chunk_shape = _get_chunk_shape(config)
        assert chunk_shape == (1, 1, 1, 512, 512)

    def test_tiled_256_chunks(self):
        from control.core.zarr_writer import ZarrAcquisitionConfig, _get_chunk_shape

        config = ZarrAcquisitionConfig(
            output_path="/tmp/test.zarr",
            shape=(2, 3, 4, 2048, 2048),
            dtype=np.uint16,
            pixel_size_um=0.5,
            chunk_mode=ZarrChunkMode.TILED_256,
        )

        chunk_shape = _get_chunk_shape(config)
        assert chunk_shape == (1, 1, 1, 256, 256)

    def test_shard_shape_per_z_level(self):
        """Test shard shape for BALANCED/BEST modes (per-z-level sharding)."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, _get_shard_shape

        # Use BALANCED compression to get actual sharding (FAST skips sharding)
        config = ZarrAcquisitionConfig(
            output_path="/tmp/test.zarr",
            shape=(2, 4, 10, 2048, 2048),
            dtype=np.uint16,
            pixel_size_um=0.5,
            compression=ZarrCompression.BALANCED,
        )

        shard_shape = _get_shard_shape(config)
        # Shard contains all channels for one z-level
        assert shard_shape == (1, 4, 1, 2048, 2048)

    def test_fast_mode_no_sharding(self):
        """Test that FAST mode skips sharding for maximum write speed."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, _get_shard_shape, _get_chunk_shape

        config = ZarrAcquisitionConfig(
            output_path="/tmp/test.zarr",
            shape=(2, 4, 10, 2048, 2048),
            dtype=np.uint16,
            pixel_size_um=0.5,
            compression=ZarrCompression.FAST,
        )

        chunk_shape = _get_chunk_shape(config)
        shard_shape = _get_shard_shape(config)
        # FAST mode: shard_shape == chunk_shape (no internal sharding)
        assert shard_shape == chunk_shape


class TestCompressionCodecs:
    """Tests for compression codec configuration."""

    def test_none_compression(self):
        """Test that NONE compression returns None (no codec)."""
        from control.core.zarr_writer import _get_compression_codec

        codec = _get_compression_codec(ZarrCompression.NONE)
        assert codec is None

    def test_none_compression_no_sharding(self):
        """Test that NONE compression skips sharding for maximum speed."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, _get_shard_shape, _get_chunk_shape

        config = ZarrAcquisitionConfig(
            output_path="/tmp/test.zarr",
            shape=(2, 4, 10, 2048, 2048),
            dtype=np.uint16,
            pixel_size_um=0.5,
            compression=ZarrCompression.NONE,
        )

        chunk_shape = _get_chunk_shape(config)
        shard_shape = _get_shard_shape(config)
        # NONE mode: shard_shape == chunk_shape (no internal sharding)
        assert shard_shape == chunk_shape

    def test_fast_compression(self):
        from control.core.zarr_writer import _get_compression_codec

        codec = _get_compression_codec(ZarrCompression.FAST)
        assert codec["name"] == "blosc"
        assert codec["configuration"]["cname"] == "lz4"
        assert codec["configuration"]["clevel"] == 1  # Minimal compression for speed
        assert codec["configuration"]["shuffle"] == "shuffle"  # Byte shuffle (faster than bitshuffle)

    def test_balanced_compression(self):
        from control.core.zarr_writer import _get_compression_codec

        codec = _get_compression_codec(ZarrCompression.BALANCED)
        assert codec["name"] == "blosc"
        assert codec["configuration"]["cname"] == "zstd"
        assert codec["configuration"]["clevel"] == 3

    def test_best_compression(self):
        from control.core.zarr_writer import _get_compression_codec

        codec = _get_compression_codec(ZarrCompression.BEST)
        assert codec["name"] == "blosc"
        assert codec["configuration"]["cname"] == "zstd"
        assert codec["configuration"]["clevel"] == 9


class TestDtypeConversion:
    """Tests for dtype to zarr conversion."""

    def test_common_dtypes(self):
        from control.core.zarr_writer import _dtype_to_zarr

        assert _dtype_to_zarr(np.dtype("uint8")) == "uint8"
        assert _dtype_to_zarr(np.dtype("uint16")) == "uint16"
        assert _dtype_to_zarr(np.dtype("float32")) == "float32"
        assert _dtype_to_zarr(np.dtype("float64")) == "float64"

    def test_unsupported_dtype(self):
        from control.core.zarr_writer import _dtype_to_zarr

        with pytest.raises(ValueError, match="Unsupported dtype"):
            _dtype_to_zarr(np.dtype("complex64"))


class TestZarrWriterManager:
    """Tests for ZarrWriterManager lifecycle."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for test outputs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.mark.asyncio
    async def test_initialize_creates_dataset(self, temp_dir):
        from control.core.zarr_writer import ZarrAcquisitionConfig, ZarrWriterManager

        output_path = os.path.join(temp_dir, "test.zarr")
        config = ZarrAcquisitionConfig(
            output_path=output_path,
            shape=(1, 2, 1, 64, 64),
            dtype=np.uint16,
            pixel_size_um=1.0,
            channel_names=["DAPI", "GFP"],
        )

        manager = ZarrWriterManager(config)
        await manager.initialize()

        assert manager.is_initialized
        assert not manager.is_finalized
        assert os.path.exists(output_path)

        # Check .zattrs contains OME-NGFF metadata
        zattrs_path = os.path.join(output_path, ".zattrs")
        assert os.path.exists(zattrs_path)

        with open(zattrs_path) as f:
            zattrs = json.load(f)

        assert "multiscales" in zattrs
        assert zattrs["multiscales"][0]["version"] == "0.5"

    @pytest.mark.asyncio
    async def test_write_frame(self, temp_dir):
        from control.core.zarr_writer import ZarrAcquisitionConfig, ZarrWriterManager

        output_path = os.path.join(temp_dir, "test.zarr")
        config = ZarrAcquisitionConfig(
            output_path=output_path,
            shape=(1, 1, 1, 32, 32),
            dtype=np.uint16,
            pixel_size_um=1.0,
        )

        manager = ZarrWriterManager(config)
        await manager.initialize()

        # Write a test frame
        test_image = np.random.randint(0, 65535, (32, 32), dtype=np.uint16)
        await manager.write_frame(test_image, t=0, c=0, z=0)

        # Wait for write to complete
        await manager.wait_for_pending()
        assert manager.pending_write_count == 0

    @pytest.mark.asyncio
    async def test_finalize(self, temp_dir):
        from control.core.zarr_writer import ZarrAcquisitionConfig, ZarrWriterManager

        output_path = os.path.join(temp_dir, "test.zarr")
        config = ZarrAcquisitionConfig(
            output_path=output_path,
            shape=(1, 1, 1, 32, 32),
            dtype=np.uint16,
            pixel_size_um=1.0,
        )

        manager = ZarrWriterManager(config)
        await manager.initialize()

        test_image = np.ones((32, 32), dtype=np.uint16) * 100
        await manager.write_frame(test_image, t=0, c=0, z=0)

        await manager.finalize()

        assert manager.is_finalized

        # Check metadata updated with completion status
        zattrs_path = os.path.join(output_path, ".zattrs")
        with open(zattrs_path) as f:
            zattrs = json.load(f)

        assert "_squid_metadata" in zattrs
        assert zattrs["_squid_metadata"]["acquisition_complete"] is True

    @pytest.mark.asyncio
    async def test_abort(self, temp_dir):
        from control.core.zarr_writer import ZarrAcquisitionConfig, ZarrWriterManager

        output_path = os.path.join(temp_dir, "test.zarr")
        config = ZarrAcquisitionConfig(
            output_path=output_path,
            shape=(1, 1, 1, 32, 32),
            dtype=np.uint16,
            pixel_size_um=1.0,
        )

        manager = ZarrWriterManager(config)
        await manager.initialize()

        # Write a frame but abort before completion
        test_image = np.ones((32, 32), dtype=np.uint16)
        await manager.write_frame(test_image, t=0, c=0, z=0)

        await manager.abort()

        assert manager.is_finalized

        # Check metadata indicates aborted state
        zattrs_path = os.path.join(output_path, ".zattrs")
        with open(zattrs_path) as f:
            zattrs = json.load(f)

        assert "_squid_metadata" in zattrs
        assert zattrs["_squid_metadata"]["aborted"] is True


class TestSyncZarrWriter:
    """Tests for synchronous ZarrWriter wrapper."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for test outputs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_sync_lifecycle(self, temp_dir):
        from control.core.zarr_writer import ZarrAcquisitionConfig, SyncZarrWriter

        output_path = os.path.join(temp_dir, "test.zarr")
        config = ZarrAcquisitionConfig(
            output_path=output_path,
            shape=(1, 1, 1, 32, 32),
            dtype=np.uint16,
            pixel_size_um=1.0,
        )

        writer = SyncZarrWriter(config)
        writer.initialize()

        assert writer.is_initialized
        assert not writer.is_finalized

        test_image = np.random.randint(0, 65535, (32, 32), dtype=np.uint16)
        writer.write_frame(test_image, t=0, c=0, z=0)

        writer.finalize()
        assert writer.is_finalized

    def test_sync_writer_omero_channel_metadata(self, temp_dir):
        """Test that channel metadata (colors, wavelengths) is written to zattrs."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, SyncZarrWriter

        output_path = os.path.join(temp_dir, "test.zarr")
        config = ZarrAcquisitionConfig(
            output_path=output_path,
            shape=(1, 3, 1, 32, 32),
            dtype=np.uint16,
            pixel_size_um=0.5,
            channel_names=["DAPI", "GFP", "Brightfield"],
            channel_colors=["#20ADF8", "#1FFF00", "#FFFFFF"],
            channel_wavelengths=[405, 488, None],  # None for brightfield
        )

        writer = SyncZarrWriter(config)
        writer.initialize()
        writer.finalize()

        # Check .zattrs contains omero metadata with colors and wavelengths
        zattrs_path = os.path.join(output_path, ".zattrs")
        with open(zattrs_path) as f:
            zattrs = json.load(f)

        assert "omero" in zattrs
        channels = zattrs["omero"]["channels"]
        assert len(channels) == 3

        # Check DAPI channel
        assert channels[0]["label"] == "DAPI"
        assert channels[0]["active"] is True
        assert "color" in channels[0]
        assert channels[0]["emission_wavelength"]["value"] == 405
        assert channels[0]["emission_wavelength"]["unit"] == "nanometer"
        assert "window" in channels[0]

        # Check GFP channel
        assert channels[1]["label"] == "GFP"
        assert channels[1]["emission_wavelength"]["value"] == 488

        # Check Brightfield channel (no wavelength)
        assert channels[2]["label"] == "Brightfield"
        assert "emission_wavelength" not in channels[2]  # No wavelength for BF


class TestHCSMetadata:
    """Tests for HCS plate metadata functions."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for test outputs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_write_plate_metadata(self, temp_dir):
        from control.core.zarr_writer import write_plate_metadata

        plate_path = os.path.join(temp_dir, "plate.zarr")
        write_plate_metadata(
            plate_path=plate_path,
            rows=["A", "B", "C"],
            cols=[1, 2, 3],
            wells=[("A", 1), ("B", 2)],
            plate_name="test_plate",
        )

        # Check .zattrs
        zattrs_path = os.path.join(plate_path, ".zattrs")
        assert os.path.exists(zattrs_path)

        with open(zattrs_path) as f:
            zattrs = json.load(f)

        assert "plate" in zattrs
        assert zattrs["plate"]["name"] == "test_plate"
        assert len(zattrs["plate"]["wells"]) == 2

        # Check zarr.json
        zarr_json_path = os.path.join(plate_path, "zarr.json")
        assert os.path.exists(zarr_json_path)

        with open(zarr_json_path) as f:
            zarr_json = json.load(f)

        assert zarr_json["zarr_format"] == 3

    def test_write_well_metadata(self, temp_dir):
        from control.core.zarr_writer import write_well_metadata

        well_path = os.path.join(temp_dir, "plate.zarr", "A", "1")
        write_well_metadata(
            well_path=well_path,
            fields=[0, 1, 2],
        )

        # Check .zattrs
        zattrs_path = os.path.join(well_path, ".zattrs")
        assert os.path.exists(zattrs_path)

        with open(zattrs_path) as f:
            zattrs = json.load(f)

        assert "well" in zattrs
        assert len(zattrs["well"]["images"]) == 3


class TestZarrWriterInfo:
    """Tests for ZarrWriterInfo dataclass."""

    def test_zarr_writer_info_creation(self):
        info = ZarrWriterInfo(
            base_path="/tmp/experiment",
            t_size=5,
            c_size=3,
            z_size=10,
        )

        assert info.base_path == "/tmp/experiment"
        assert info.t_size == 5
        assert info.c_size == 3
        assert info.z_size == 10
        assert info.is_hcs is False  # Default

    def test_zarr_writer_info_hcs_output_path(self):
        """Test HCS mode output paths use plate hierarchy."""
        info = ZarrWriterInfo(
            base_path="/tmp/experiment",
            t_size=1,
            c_size=2,
            z_size=3,
            is_hcs=True,
            region_fov_counts={"A1": 4, "B12": 4},
        )

        # Test single-letter row
        path = info.get_output_path("A1", 0)
        assert path == "/tmp/experiment/plate.zarr/A/1/0/0"

        path = info.get_output_path("A1", 2)
        assert path == "/tmp/experiment/plate.zarr/A/1/2/0"

        # Test multi-digit column
        path = info.get_output_path("B12", 2)
        assert path == "/tmp/experiment/plate.zarr/B/12/2/0"

        # Test double-letter row (e.g., AA, AB)
        path = info.get_output_path("AA3", 0)
        assert path == "/tmp/experiment/plate.zarr/AA/3/0/0"

    def test_zarr_writer_info_non_hcs_per_fov_output_path(self):
        """Test non-HCS default: per-FOV zarr files (OME-NGFF compliant)."""
        info = ZarrWriterInfo(
            base_path="/tmp/experiment",
            t_size=1,
            c_size=2,
            z_size=3,
            is_hcs=False,
            use_6d_fov=False,  # Default
            region_fov_counts={"region_1": 4, "region_2": 2},
        )

        # Each FOV gets its own zarr file
        assert info.get_output_path("region_1", 0) == "/tmp/experiment/zarr/region_1/fov_0.zarr"
        assert info.get_output_path("region_1", 1) == "/tmp/experiment/zarr/region_1/fov_1.zarr"
        assert info.get_output_path("region_1", 2) == "/tmp/experiment/zarr/region_1/fov_2.zarr"

        # Different region
        assert info.get_output_path("region_2", 0) == "/tmp/experiment/zarr/region_2/fov_0.zarr"

    def test_zarr_writer_info_non_hcs_6d_output_path(self):
        """Test non-HCS with 6D mode: single zarr per region (non-standard)."""
        info = ZarrWriterInfo(
            base_path="/tmp/experiment",
            t_size=1,
            c_size=2,
            z_size=3,
            is_hcs=False,
            use_6d_fov=True,  # 6D mode
            region_fov_counts={"region_1": 4, "region_2": 2},
        )

        # All FOVs go to same zarr file per region
        path_fov0 = info.get_output_path("region_1", 0)
        path_fov1 = info.get_output_path("region_1", 1)

        assert path_fov0 == "/tmp/experiment/zarr/region_1/acquisition.zarr"
        assert path_fov1 == "/tmp/experiment/zarr/region_1/acquisition.zarr"

        # Different region
        path_region2 = info.get_output_path("region_2", 0)
        assert path_region2 == "/tmp/experiment/zarr/region_2/acquisition.zarr"

    def test_zarr_writer_info_get_fov_count(self):
        """Test get_fov_count returns correct counts for regions."""
        info = ZarrWriterInfo(
            base_path="/tmp/experiment",
            t_size=1,
            c_size=2,
            z_size=3,
            is_hcs=False,
            region_fov_counts={"region_1": 4, "region_2": 9},
        )

        assert info.get_fov_count("region_1") == 4
        assert info.get_fov_count("region_2") == 9
        assert info.get_fov_count("unknown") == 1  # Default

    def test_zarr_writer_info_with_metadata(self):
        """Test ZarrWriterInfo with optional metadata fields."""
        info = ZarrWriterInfo(
            base_path="/tmp/experiment",
            t_size=10,
            c_size=4,
            z_size=20,
            is_hcs=True,
            pixel_size_um=0.5,
            z_step_um=1.0,
            time_increment_s=60.0,
            channel_names=["DAPI", "GFP", "RFP", "CY5"],
            channel_colors=["#20ADF8", "#1FFF00", "#FF0000", "#770000"],
            channel_wavelengths=[405, 488, 561, 638],
        )

        assert info.pixel_size_um == 0.5
        assert info.z_step_um == 1.0
        assert info.time_increment_s == 60.0
        assert info.channel_names == ["DAPI", "GFP", "RFP", "CY5"]
        assert info.channel_colors == ["#20ADF8", "#1FFF00", "#FF0000", "#770000"]
        assert info.channel_wavelengths == [405, 488, 561, 638]
        assert info.is_hcs is True


class TestSaveZarrJobWithSimulation:
    """Tests for SaveZarrJob with simulated I/O."""

    def test_save_zarr_job_simulated(self):
        """Test SaveZarrJob with simulated disk I/O."""
        import control._def

        # Enable simulated I/O
        original_enabled = control._def.SIMULATED_DISK_IO_ENABLED
        original_compression = control._def.SIMULATED_DISK_IO_COMPRESSION

        try:
            control._def.SIMULATED_DISK_IO_ENABLED = True
            control._def.SIMULATED_DISK_IO_COMPRESSION = True

            info = make_test_capture_info(region_id="A1", fov=0, time_point=0, z_index=0, config_idx=0)
            image = np.random.randint(0, 65535, (64, 64), dtype=np.uint16)

            job = SaveZarrJob(
                capture_info=info,
                capture_image=JobImage(image_array=image),
            )

            # Inject zarr writer info
            job.zarr_writer_info = ZarrWriterInfo(
                base_path="/tmp/test_experiment",
                t_size=1,
                c_size=1,
                z_size=1,
            )

            # Run should complete without error (simulated write)
            result = job.run()
            assert result is True

        finally:
            control._def.SIMULATED_DISK_IO_ENABLED = original_enabled
            control._def.SIMULATED_DISK_IO_COMPRESSION = original_compression

    def test_save_zarr_job_multiple_regions_fovs(self):
        """Test SaveZarrJob writes to separate paths for different regions/FOVs."""
        import control._def

        original_enabled = control._def.SIMULATED_DISK_IO_ENABLED
        original_compression = control._def.SIMULATED_DISK_IO_COMPRESSION
        original_speed = control._def.SIMULATED_DISK_IO_SPEED_MB_S

        try:
            control._def.SIMULATED_DISK_IO_ENABLED = True
            control._def.SIMULATED_DISK_IO_COMPRESSION = True
            control._def.SIMULATED_DISK_IO_SPEED_MB_S = 10000.0

            zarr_info = ZarrWriterInfo(
                base_path="/tmp/multi_region_test",
                t_size=1,
                c_size=2,
                z_size=3,
            )

            # Simulate writing to multiple regions and FOVs
            regions_fovs = [("A1", 0), ("A1", 1), ("A2", 0), ("B1", 0)]

            for region_id, fov in regions_fovs:
                for c in range(2):
                    for z in range(3):
                        info = make_test_capture_info(
                            region_id=region_id,
                            fov=fov,
                            time_point=0,
                            z_index=z,
                            config_idx=c,
                        )
                        image = np.random.randint(0, 65535, (32, 32), dtype=np.uint16)

                        job = SaveZarrJob(
                            capture_info=info,
                            capture_image=JobImage(image_array=image),
                        )
                        job.zarr_writer_info = zarr_info

                        result = job.run()
                        assert result is True

        finally:
            control._def.SIMULATED_DISK_IO_ENABLED = original_enabled
            control._def.SIMULATED_DISK_IO_COMPRESSION = original_compression
            control._def.SIMULATED_DISK_IO_SPEED_MB_S = original_speed

    def test_save_zarr_job_missing_info(self):
        """Test SaveZarrJob raises error when zarr_writer_info is missing."""
        info = make_test_capture_info()
        image = np.random.randint(0, 65535, (64, 64), dtype=np.uint16)

        job = SaveZarrJob(
            capture_info=info,
            capture_image=JobImage(image_array=image),
        )

        with pytest.raises(ValueError, match="zarr_writer_info"):
            job.run()


class TestSimulatedZarrWrite:
    """Tests for simulated zarr write function."""

    def test_simulated_write_basic(self):
        """Test basic simulated zarr write."""
        import control._def
        from control.core.io_simulation import simulated_zarr_write

        # Enable simulated I/O with fast speed
        original_enabled = control._def.SIMULATED_DISK_IO_ENABLED
        original_speed = control._def.SIMULATED_DISK_IO_SPEED_MB_S
        original_compression = control._def.SIMULATED_DISK_IO_COMPRESSION

        try:
            control._def.SIMULATED_DISK_IO_ENABLED = True
            control._def.SIMULATED_DISK_IO_SPEED_MB_S = 10000.0  # Very fast for tests
            control._def.SIMULATED_DISK_IO_COMPRESSION = True

            image = np.random.randint(0, 65535, (32, 32), dtype=np.uint16)

            bytes_written = simulated_zarr_write(
                image=image,
                stack_key="/tmp/test_sim.zarr",
                shape=(1, 1, 1, 32, 32),
                time_point=0,
                z_index=0,
                channel_index=0,
            )

            # Should return some bytes written (compressed)
            assert bytes_written > 0
            assert bytes_written < image.nbytes  # Compressed should be smaller

        finally:
            control._def.SIMULATED_DISK_IO_ENABLED = original_enabled
            control._def.SIMULATED_DISK_IO_SPEED_MB_S = original_speed
            control._def.SIMULATED_DISK_IO_COMPRESSION = original_compression


class TestEnumConversions:
    """Tests for enum conversion functions."""

    def test_zarr_chunk_mode_convert_from_string(self):
        assert ZarrChunkMode.convert_to_enum("full_frame") == ZarrChunkMode.FULL_FRAME
        assert ZarrChunkMode.convert_to_enum("tiled_512") == ZarrChunkMode.TILED_512
        assert ZarrChunkMode.convert_to_enum("tiled_256") == ZarrChunkMode.TILED_256

    def test_zarr_chunk_mode_convert_case_insensitive(self):
        assert ZarrChunkMode.convert_to_enum("FULL_FRAME") == ZarrChunkMode.FULL_FRAME
        assert ZarrChunkMode.convert_to_enum("Full_Frame") == ZarrChunkMode.FULL_FRAME

    def test_zarr_chunk_mode_convert_from_enum(self):
        assert ZarrChunkMode.convert_to_enum(ZarrChunkMode.FULL_FRAME) == ZarrChunkMode.FULL_FRAME

    def test_zarr_chunk_mode_invalid(self):
        with pytest.raises(ValueError, match="Invalid zarr chunk mode"):
            ZarrChunkMode.convert_to_enum("invalid_mode")

    def test_zarr_compression_convert_from_string(self):
        assert ZarrCompression.convert_to_enum("fast") == ZarrCompression.FAST
        assert ZarrCompression.convert_to_enum("balanced") == ZarrCompression.BALANCED
        assert ZarrCompression.convert_to_enum("best") == ZarrCompression.BEST

    def test_zarr_compression_convert_case_insensitive(self):
        assert ZarrCompression.convert_to_enum("FAST") == ZarrCompression.FAST
        assert ZarrCompression.convert_to_enum("Fast") == ZarrCompression.FAST

    def test_zarr_compression_convert_from_enum(self):
        assert ZarrCompression.convert_to_enum(ZarrCompression.FAST) == ZarrCompression.FAST

    def test_zarr_compression_invalid(self):
        with pytest.raises(ValueError, match="Invalid zarr compression"):
            ZarrCompression.convert_to_enum("invalid_compression")


class TestSaveZarrJobClassMethods:
    """Tests for SaveZarrJob class-level writer management."""

    def test_clear_writers_empty(self):
        """Test clearing writers when none exist."""
        SaveZarrJob.clear_writers()
        # Should not raise

    def test_finalize_all_writers_empty(self):
        """Test finalizing writers when none exist."""
        SaveZarrJob.finalize_all_writers()
        # Should not raise


class TestSyncZarrWriterErrorHandling:
    """Tests for error handling in SyncZarrWriter."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for test outputs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_write_before_initialize_raises(self, temp_dir):
        """Test that writing before initialization raises an error."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, SyncZarrWriter

        config = ZarrAcquisitionConfig(
            output_path=os.path.join(temp_dir, "test.zarr"),
            shape=(1, 1, 1, 32, 32),
            dtype=np.uint16,
            pixel_size_um=1.0,
        )

        writer = SyncZarrWriter(config)
        test_image = np.ones((32, 32), dtype=np.uint16)

        with pytest.raises(RuntimeError, match="not initialized"):
            writer.write_frame(test_image, t=0, c=0, z=0)

    def test_write_after_finalize_raises(self, temp_dir):
        """Test that writing after finalization raises an error."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, SyncZarrWriter

        config = ZarrAcquisitionConfig(
            output_path=os.path.join(temp_dir, "test.zarr"),
            shape=(1, 1, 1, 32, 32),
            dtype=np.uint16,
            pixel_size_um=1.0,
        )

        writer = SyncZarrWriter(config)
        writer.initialize()
        writer.finalize()

        test_image = np.ones((32, 32), dtype=np.uint16)
        with pytest.raises(RuntimeError, match="finalized"):
            writer.write_frame(test_image, t=0, c=0, z=0)

    def test_double_initialize_warning(self, temp_dir):
        """Test that double initialization logs a warning but doesn't fail."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, SyncZarrWriter

        config = ZarrAcquisitionConfig(
            output_path=os.path.join(temp_dir, "test.zarr"),
            shape=(1, 1, 1, 32, 32),
            dtype=np.uint16,
            pixel_size_um=1.0,
        )

        writer = SyncZarrWriter(config)
        writer.initialize()
        writer.initialize()  # Should warn but not fail
        assert writer.is_initialized

    def test_double_finalize_warning(self, temp_dir):
        """Test that double finalization logs a warning but doesn't fail."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, SyncZarrWriter

        config = ZarrAcquisitionConfig(
            output_path=os.path.join(temp_dir, "test.zarr"),
            shape=(1, 1, 1, 32, 32),
            dtype=np.uint16,
            pixel_size_um=1.0,
        )

        writer = SyncZarrWriter(config)
        writer.initialize()
        writer.finalize()
        writer.finalize()  # Should warn but not fail
        assert writer.is_finalized


class TestSyncZarrWriterIndexValidation:
    """Tests for index validation in SyncZarrWriter."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for test outputs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_invalid_time_index(self, temp_dir):
        """Test that out-of-range time index raises an error."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, SyncZarrWriter

        config = ZarrAcquisitionConfig(
            output_path=os.path.join(temp_dir, "test.zarr"),
            shape=(2, 1, 1, 32, 32),  # t_size=2
            dtype=np.uint16,
            pixel_size_um=1.0,
        )

        writer = SyncZarrWriter(config)
        writer.initialize()

        test_image = np.ones((32, 32), dtype=np.uint16)

        with pytest.raises(ValueError, match="Time index"):
            writer.write_frame(test_image, t=5, c=0, z=0)  # t=5 is out of range

    def test_invalid_channel_index(self, temp_dir):
        """Test that out-of-range channel index raises an error."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, SyncZarrWriter

        config = ZarrAcquisitionConfig(
            output_path=os.path.join(temp_dir, "test.zarr"),
            shape=(1, 3, 1, 32, 32),  # c_size=3
            dtype=np.uint16,
            pixel_size_um=1.0,
        )

        writer = SyncZarrWriter(config)
        writer.initialize()

        test_image = np.ones((32, 32), dtype=np.uint16)

        with pytest.raises(ValueError, match="Channel index"):
            writer.write_frame(test_image, t=0, c=10, z=0)  # c=10 is out of range

    def test_invalid_z_index(self, temp_dir):
        """Test that out-of-range z index raises an error."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, SyncZarrWriter

        config = ZarrAcquisitionConfig(
            output_path=os.path.join(temp_dir, "test.zarr"),
            shape=(1, 1, 5, 32, 32),  # z_size=5
            dtype=np.uint16,
            pixel_size_um=1.0,
        )

        writer = SyncZarrWriter(config)
        writer.initialize()

        test_image = np.ones((32, 32), dtype=np.uint16)

        with pytest.raises(ValueError, match="Z index"):
            writer.write_frame(test_image, t=0, c=0, z=10)  # z=10 is out of range


class TestSyncZarrWriterMultipleFrames:
    """Tests for writing multiple frames."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for test outputs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_write_multiple_frames(self, temp_dir):
        """Test writing multiple frames to a dataset."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, SyncZarrWriter

        output_path = os.path.join(temp_dir, "test.zarr")
        config = ZarrAcquisitionConfig(
            output_path=output_path,
            shape=(2, 2, 3, 32, 32),  # 2 timepoints, 2 channels, 3 z-levels
            dtype=np.uint16,
            pixel_size_um=1.0,
        )

        writer = SyncZarrWriter(config)
        writer.initialize()

        # Write all frames
        for t in range(2):
            for c in range(2):
                for z in range(3):
                    test_image = np.ones((32, 32), dtype=np.uint16) * (t * 100 + c * 10 + z)
                    writer.write_frame(test_image, t=t, c=c, z=z)

        # Wait for all writes
        completed = writer.wait_for_pending()
        assert completed >= 0

        writer.finalize()
        assert writer.is_finalized

    def test_write_and_verify_data(self, temp_dir):
        """Test that written data can be read back correctly."""
        import tensorstore as ts

        from control.core.zarr_writer import ZarrAcquisitionConfig, SyncZarrWriter

        output_path = os.path.join(temp_dir, "test.zarr")
        config = ZarrAcquisitionConfig(
            output_path=output_path,
            shape=(1, 1, 1, 32, 32),
            dtype=np.uint16,
            pixel_size_um=1.0,
        )

        writer = SyncZarrWriter(config)
        writer.initialize()

        # Write a known pattern
        test_image = np.arange(32 * 32, dtype=np.uint16).reshape((32, 32))
        writer.write_frame(test_image, t=0, c=0, z=0)
        writer.wait_for_pending()
        writer.finalize()

        # Read back and verify
        spec = {
            "driver": "zarr3",
            "kvstore": {"driver": "file", "path": output_path},
        }
        dataset = ts.open(spec).result()
        read_data = dataset[0, 0, 0, :, :].read().result()

        np.testing.assert_array_equal(read_data, test_image)


class TestSixDimensionalSupport:
    """Tests for 6D (FOV, T, C, Z, Y, X) dataset support."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for test outputs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_6d_config_properties(self):
        """Test 6D config exposes correct dimension properties."""
        from control.core.zarr_writer import ZarrAcquisitionConfig

        config = ZarrAcquisitionConfig(
            output_path="/tmp/test.zarr",
            shape=(5, 2, 3, 4, 100, 100),  # FOV, T, C, Z, Y, X
            dtype=np.uint16,
            pixel_size_um=1.0,
            is_hcs=False,  # non-HCS uses 6D
        )

        assert config.ndim == 6
        assert config.fov_size == 5
        assert config.t_size == 2
        assert config.c_size == 3
        assert config.z_size == 4
        assert config.y_size == 100
        assert config.x_size == 100

    def test_5d_config_fov_size_is_one(self):
        """Test 5D config returns fov_size=1."""
        from control.core.zarr_writer import ZarrAcquisitionConfig

        config = ZarrAcquisitionConfig(
            output_path="/tmp/test.zarr",
            shape=(2, 3, 4, 100, 100),  # T, C, Z, Y, X
            dtype=np.uint16,
            pixel_size_um=1.0,
            is_hcs=True,
        )

        assert config.ndim == 5
        assert config.fov_size == 1

    def test_6d_chunk_shape(self):
        """Test chunk shape calculation for 6D datasets."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, _get_chunk_shape

        config = ZarrAcquisitionConfig(
            output_path="/tmp/test.zarr",
            shape=(5, 2, 3, 4, 2048, 2048),  # FOV, T, C, Z, Y, X
            dtype=np.uint16,
            pixel_size_um=0.5,
            chunk_mode=ZarrChunkMode.FULL_FRAME,
            is_hcs=False,
        )

        chunk_shape = _get_chunk_shape(config)
        assert chunk_shape == (1, 1, 1, 1, 2048, 2048)

    def test_6d_shard_shape(self):
        """Test shard shape calculation for 6D datasets with BALANCED compression."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, _get_shard_shape

        # Use BALANCED compression to get actual sharding (FAST skips sharding)
        config = ZarrAcquisitionConfig(
            output_path="/tmp/test.zarr",
            shape=(5, 2, 4, 10, 2048, 2048),  # FOV, T, C, Z, Y, X
            dtype=np.uint16,
            pixel_size_um=0.5,
            is_hcs=False,
            compression=ZarrCompression.BALANCED,
        )

        shard_shape = _get_shard_shape(config)
        # Shard contains all channels for one (fov, t, z) combination
        assert shard_shape == (1, 1, 4, 1, 2048, 2048)

    def test_6d_fast_mode_no_sharding(self):
        """Test that FAST mode skips sharding for 6D datasets."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, _get_shard_shape, _get_chunk_shape

        config = ZarrAcquisitionConfig(
            output_path="/tmp/test.zarr",
            shape=(5, 2, 4, 10, 2048, 2048),  # FOV, T, C, Z, Y, X
            dtype=np.uint16,
            pixel_size_um=0.5,
            is_hcs=False,
            compression=ZarrCompression.FAST,
        )

        chunk_shape = _get_chunk_shape(config)
        shard_shape = _get_shard_shape(config)
        # FAST mode: shard_shape == chunk_shape (no internal sharding)
        assert shard_shape == chunk_shape

    def test_6d_writer_initialization(self, temp_dir):
        """Test 6D writer initializes correctly."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, SyncZarrWriter

        output_path = os.path.join(temp_dir, "test_6d.zarr")
        config = ZarrAcquisitionConfig(
            output_path=output_path,
            shape=(4, 1, 2, 3, 32, 32),  # FOV, T, C, Z, Y, X
            dtype=np.uint16,
            pixel_size_um=1.0,
            is_hcs=False,
        )

        writer = SyncZarrWriter(config)
        writer.initialize()

        assert writer.is_initialized
        assert os.path.exists(output_path)

        # Check metadata has 6 axes with FOV first
        zattrs_path = os.path.join(output_path, ".zattrs")
        with open(zattrs_path) as f:
            zattrs = json.load(f)

        axes = zattrs["multiscales"][0]["axes"]
        assert len(axes) == 6
        axis_names = [a["name"] for a in axes]
        assert axis_names == ["fov", "t", "c", "z", "y", "x"]

        writer.finalize()

    def test_6d_write_multiple_fovs(self, temp_dir):
        """Test writing to multiple FOV indices in a 6D dataset."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, SyncZarrWriter

        output_path = os.path.join(temp_dir, "test_6d.zarr")
        config = ZarrAcquisitionConfig(
            output_path=output_path,
            shape=(4, 1, 2, 3, 32, 32),  # 4 FOVs, T, C, Z, Y, X
            dtype=np.uint16,
            pixel_size_um=1.0,
            is_hcs=False,
        )

        writer = SyncZarrWriter(config)
        writer.initialize()

        # Write to different FOV indices
        for fov in range(4):
            test_image = np.ones((32, 32), dtype=np.uint16) * (fov + 1) * 100
            writer.write_frame(test_image, t=0, c=0, z=0, fov=fov)

        writer.wait_for_pending()
        writer.finalize()
        assert writer.is_finalized

    def test_6d_write_and_verify_data(self, temp_dir):
        """Test 6D data can be written and read back correctly."""
        import tensorstore as ts

        from control.core.zarr_writer import ZarrAcquisitionConfig, SyncZarrWriter

        output_path = os.path.join(temp_dir, "test_6d.zarr")
        config = ZarrAcquisitionConfig(
            output_path=output_path,
            shape=(3, 1, 1, 1, 32, 32),  # 3 FOVs, T, C, Z, Y, X
            dtype=np.uint16,
            pixel_size_um=1.0,
            is_hcs=False,
        )

        writer = SyncZarrWriter(config)
        writer.initialize()

        # Write different patterns to each FOV
        test_images = []
        for fov in range(3):
            test_image = np.arange(32 * 32, dtype=np.uint16).reshape((32, 32)) + (fov * 1000)
            test_images.append(test_image)
            writer.write_frame(test_image, t=0, c=0, z=0, fov=fov)

        writer.wait_for_pending()
        writer.finalize()

        # Read back and verify each FOV - 6D indexing: [fov, t, c, z, y, x]
        spec = {
            "driver": "zarr3",
            "kvstore": {"driver": "file", "path": output_path},
        }
        dataset = ts.open(spec).result()

        for fov in range(3):
            read_data = dataset[fov, 0, 0, 0, :, :].read().result()
            np.testing.assert_array_equal(read_data, test_images[fov])

    def test_6d_missing_fov_raises_error(self, temp_dir):
        """Test that writing to 6D dataset without FOV index raises an error."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, SyncZarrWriter

        output_path = os.path.join(temp_dir, "test_6d.zarr")
        config = ZarrAcquisitionConfig(
            output_path=output_path,
            shape=(4, 1, 1, 1, 32, 32),  # FOV, T, C, Z, Y, X
            dtype=np.uint16,
            pixel_size_um=1.0,
            is_hcs=False,
        )

        writer = SyncZarrWriter(config)
        writer.initialize()

        test_image = np.ones((32, 32), dtype=np.uint16)

        with pytest.raises(ValueError, match="FOV index required"):
            writer.write_frame(test_image, t=0, c=0, z=0)  # Missing fov

    def test_6d_invalid_fov_index_raises_error(self, temp_dir):
        """Test that out-of-range FOV index raises an error."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, SyncZarrWriter

        output_path = os.path.join(temp_dir, "test_6d.zarr")
        config = ZarrAcquisitionConfig(
            output_path=output_path,
            shape=(4, 1, 1, 1, 32, 32),  # 4 FOVs, T, C, Z, Y, X
            dtype=np.uint16,
            pixel_size_um=1.0,
            is_hcs=False,
        )

        writer = SyncZarrWriter(config)
        writer.initialize()

        test_image = np.ones((32, 32), dtype=np.uint16)

        with pytest.raises(ValueError, match="FOV index.*out of range"):
            writer.write_frame(test_image, t=0, c=0, z=0, fov=10)  # Invalid FOV


class TestJobRunnerZarrDispatch:
    """Tests for JobRunner dispatch integration with SaveZarrJob.

    Note: These tests only test the dispatch() method's injection logic,
    not the full subprocess execution. The runner is not started.
    """

    def test_dispatch_injects_zarr_writer_info(self):
        """Test that JobRunner.dispatch() injects zarr_writer_info into SaveZarrJob."""
        zarr_info = ZarrWriterInfo(
            base_path="/tmp/test_acquisition",
            t_size=1,
            c_size=1,
            z_size=1,
        )

        # Create JobRunner with zarr_writer_info (not started - just testing dispatch logic)
        runner = JobRunner(zarr_writer_info=zarr_info)

        # Create a SaveZarrJob without zarr_writer_info
        info = make_test_capture_info(region_id="A1", fov=0)
        image = np.zeros((32, 32), dtype=np.uint16)
        job = SaveZarrJob(capture_info=info, capture_image=JobImage(image_array=image))

        # Verify job doesn't have zarr_writer_info yet
        assert job.zarr_writer_info is None

        # Dispatch the job (this should inject zarr_writer_info)
        runner.dispatch(job)

        # Verify zarr_writer_info was injected
        assert job.zarr_writer_info is not None
        assert job.zarr_writer_info.base_path == "/tmp/test_acquisition"

    def test_dispatch_without_zarr_info_raises(self):
        """Test that dispatching SaveZarrJob without zarr_writer_info raises an error."""
        # Create JobRunner WITHOUT zarr_writer_info (not started)
        runner = JobRunner()

        # Create a SaveZarrJob
        info = make_test_capture_info(region_id="A1", fov=0)
        image = np.zeros((32, 32), dtype=np.uint16)
        job = SaveZarrJob(capture_info=info, capture_image=JobImage(image_array=image))

        # Dispatching should raise because JobRunner has no zarr_writer_info
        with pytest.raises(ValueError, match="Cannot dispatch SaveZarrJob.*zarr_writer_info"):
            runner.dispatch(job)
