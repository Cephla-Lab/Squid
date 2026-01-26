"""Tests for Zarr v3 saving using TensorStore.

These tests verify the ZarrWriter and related functionality
for Zarr v3 saving during acquisition.
"""

import json
import os
import tempfile
import time
from unittest.mock import patch

import numpy as np
import pytest

import squid.abc
from control._def import ZarrChunkMode, ZarrCompression
from control.core.job_processing import (
    CaptureInfo,
    JobImage,
    JobRunner,
    ZarrWriteResult,
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


class TestZarrWriter:
    """Tests for ZarrWriter."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for test outputs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_sync_lifecycle(self, temp_dir):
        from control.core.zarr_writer import ZarrAcquisitionConfig, ZarrWriter

        output_path = os.path.join(temp_dir, "test.zarr")
        config = ZarrAcquisitionConfig(
            output_path=output_path,
            shape=(1, 1, 1, 32, 32),
            dtype=np.uint16,
            pixel_size_um=1.0,
        )

        writer = ZarrWriter(config)
        writer.initialize()

        assert writer.is_initialized
        assert not writer.is_finalized

        test_image = np.random.randint(0, 65535, (32, 32), dtype=np.uint16)
        writer.write_frame(test_image, t=0, c=0, z=0)

        writer.finalize()
        assert writer.is_finalized

    def test_sync_writer_omero_channel_metadata(self, temp_dir):
        """Test that channel metadata (colors, wavelengths) is written to zattrs."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, ZarrWriter

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

        writer = ZarrWriter(config)
        writer.initialize()
        writer.finalize()

        # Check zarr.json attributes contain OME-NGFF 0.5 structure with omero metadata
        zarr_json_path = os.path.join(output_path, "zarr.json")
        with open(zarr_json_path) as f:
            zarr_json = json.load(f)

        # Verify Zarr v3 structure
        assert zarr_json["zarr_format"] == 3
        assert "attributes" in zarr_json
        attrs = zarr_json["attributes"]

        # Verify OME-NGFF 0.5 namespace structure
        assert "ome" in attrs
        assert attrs["ome"]["version"] == "0.5"
        assert "multiscales" in attrs["ome"]
        assert attrs["ome"]["multiscales"][0]["version"] == "0.5"
        assert "omero" in attrs["ome"]
        assert attrs["ome"]["omero"]["version"] == "0.5"

        # Verify _squid structure field
        assert "_squid" in attrs
        assert attrs["_squid"]["structure"] == "5D-TCZYX"

        channels = attrs["ome"]["omero"]["channels"]
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

        # Check zarr.json with OME-NGFF 0.5 namespace in attributes
        zarr_json_path = os.path.join(plate_path, "zarr.json")
        assert os.path.exists(zarr_json_path)

        with open(zarr_json_path) as f:
            zarr_json = json.load(f)

        # Verify Zarr v3 structure
        assert zarr_json["zarr_format"] == 3
        assert zarr_json["node_type"] == "group"
        assert "attributes" in zarr_json

        attrs = zarr_json["attributes"]

        # Verify OME-NGFF 0.5 namespace structure
        assert "ome" in attrs
        assert attrs["ome"]["version"] == "0.5"
        assert "plate" in attrs["ome"]
        assert attrs["ome"]["plate"]["version"] == "0.5"
        assert attrs["ome"]["plate"]["name"] == "test_plate"
        assert len(attrs["ome"]["plate"]["wells"]) == 2

    def test_write_well_metadata(self, temp_dir):
        from control.core.zarr_writer import write_well_metadata

        well_path = os.path.join(temp_dir, "plate.zarr", "A", "1")
        write_well_metadata(
            well_path=well_path,
            fields=[0, 1, 2],
        )

        # Check zarr.json with OME-NGFF 0.5 namespace in attributes
        zarr_json_path = os.path.join(well_path, "zarr.json")
        assert os.path.exists(zarr_json_path)

        with open(zarr_json_path) as f:
            zarr_json = json.load(f)

        # Verify Zarr v3 structure
        assert zarr_json["zarr_format"] == 3
        assert zarr_json["node_type"] == "group"
        assert "attributes" in zarr_json

        attrs = zarr_json["attributes"]

        # Verify OME-NGFF 0.5 namespace structure
        assert "ome" in attrs
        assert attrs["ome"]["version"] == "0.5"
        assert "well" in attrs["ome"]
        assert attrs["ome"]["well"]["version"] == "0.5"
        assert len(attrs["ome"]["well"]["images"]) == 3


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
        assert path == "/tmp/experiment/plate.ome.zarr/A/1/0/0"

        path = info.get_output_path("A1", 2)
        assert path == "/tmp/experiment/plate.ome.zarr/A/1/2/0"

        # Test multi-digit column
        path = info.get_output_path("B12", 2)
        assert path == "/tmp/experiment/plate.ome.zarr/B/12/2/0"

        # Test double-letter row (e.g., AA, AB)
        path = info.get_output_path("AA3", 0)
        assert path == "/tmp/experiment/plate.ome.zarr/AA/3/0/0"

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

        # Each FOV gets its own zarr file - get_output_path returns ARRAY path (group + /0)
        assert info.get_output_path("region_1", 0) == "/tmp/experiment/zarr/region_1/fov_0.ome.zarr/0"
        assert info.get_output_path("region_1", 1) == "/tmp/experiment/zarr/region_1/fov_1.ome.zarr/0"
        assert info.get_output_path("region_1", 2) == "/tmp/experiment/zarr/region_1/fov_2.ome.zarr/0"

        # Different region
        assert info.get_output_path("region_2", 0) == "/tmp/experiment/zarr/region_2/fov_0.ome.zarr/0"

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
            assert isinstance(result, ZarrWriteResult)

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
                        assert isinstance(result, ZarrWriteResult)

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
        result = SaveZarrJob.finalize_all_writers()
        assert result is True  # No writers = success


class TestZarrWriterErrorHandling:
    """Tests for error handling in ZarrWriter."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for test outputs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_write_before_initialize_raises(self, temp_dir):
        """Test that writing before initialization raises an error."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, ZarrWriter

        config = ZarrAcquisitionConfig(
            output_path=os.path.join(temp_dir, "test.zarr"),
            shape=(1, 1, 1, 32, 32),
            dtype=np.uint16,
            pixel_size_um=1.0,
        )

        writer = ZarrWriter(config)
        test_image = np.ones((32, 32), dtype=np.uint16)

        with pytest.raises(RuntimeError, match="not initialized"):
            writer.write_frame(test_image, t=0, c=0, z=0)

    def test_write_after_finalize_raises(self, temp_dir):
        """Test that writing after finalization raises an error."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, ZarrWriter

        config = ZarrAcquisitionConfig(
            output_path=os.path.join(temp_dir, "test.zarr"),
            shape=(1, 1, 1, 32, 32),
            dtype=np.uint16,
            pixel_size_um=1.0,
        )

        writer = ZarrWriter(config)
        writer.initialize()
        writer.finalize()

        test_image = np.ones((32, 32), dtype=np.uint16)
        with pytest.raises(RuntimeError, match="finalized"):
            writer.write_frame(test_image, t=0, c=0, z=0)

    def test_double_initialize_warning(self, temp_dir):
        """Test that double initialization logs a warning but doesn't fail."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, ZarrWriter

        config = ZarrAcquisitionConfig(
            output_path=os.path.join(temp_dir, "test.zarr"),
            shape=(1, 1, 1, 32, 32),
            dtype=np.uint16,
            pixel_size_um=1.0,
        )

        writer = ZarrWriter(config)
        writer.initialize()
        writer.initialize()  # Should warn but not fail
        assert writer.is_initialized

    def test_double_finalize_warning(self, temp_dir):
        """Test that double finalization logs a warning but doesn't fail."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, ZarrWriter

        config = ZarrAcquisitionConfig(
            output_path=os.path.join(temp_dir, "test.zarr"),
            shape=(1, 1, 1, 32, 32),
            dtype=np.uint16,
            pixel_size_um=1.0,
        )

        writer = ZarrWriter(config)
        writer.initialize()
        writer.finalize()
        writer.finalize()  # Should warn but not fail
        assert writer.is_finalized


class TestZarrWriterIndexValidation:
    """Tests for index validation in ZarrWriter."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for test outputs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_invalid_time_index(self, temp_dir):
        """Test that out-of-range time index raises an error."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, ZarrWriter

        config = ZarrAcquisitionConfig(
            output_path=os.path.join(temp_dir, "test.zarr"),
            shape=(2, 1, 1, 32, 32),  # t_size=2
            dtype=np.uint16,
            pixel_size_um=1.0,
        )

        writer = ZarrWriter(config)
        writer.initialize()

        test_image = np.ones((32, 32), dtype=np.uint16)

        with pytest.raises(ValueError, match="Time index"):
            writer.write_frame(test_image, t=5, c=0, z=0)  # t=5 is out of range

    def test_invalid_channel_index(self, temp_dir):
        """Test that out-of-range channel index raises an error."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, ZarrWriter

        config = ZarrAcquisitionConfig(
            output_path=os.path.join(temp_dir, "test.zarr"),
            shape=(1, 3, 1, 32, 32),  # c_size=3
            dtype=np.uint16,
            pixel_size_um=1.0,
        )

        writer = ZarrWriter(config)
        writer.initialize()

        test_image = np.ones((32, 32), dtype=np.uint16)

        with pytest.raises(ValueError, match="Channel index"):
            writer.write_frame(test_image, t=0, c=10, z=0)  # c=10 is out of range

    def test_invalid_z_index(self, temp_dir):
        """Test that out-of-range z index raises an error."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, ZarrWriter

        config = ZarrAcquisitionConfig(
            output_path=os.path.join(temp_dir, "test.zarr"),
            shape=(1, 1, 5, 32, 32),  # z_size=5
            dtype=np.uint16,
            pixel_size_um=1.0,
        )

        writer = ZarrWriter(config)
        writer.initialize()

        test_image = np.ones((32, 32), dtype=np.uint16)

        with pytest.raises(ValueError, match="Z index"):
            writer.write_frame(test_image, t=0, c=0, z=10)  # z=10 is out of range


class TestZarrWriterMultipleFrames:
    """Tests for writing multiple frames."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for test outputs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_write_multiple_frames(self, temp_dir):
        """Test writing multiple frames to a dataset."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, ZarrWriter

        output_path = os.path.join(temp_dir, "test.zarr")
        config = ZarrAcquisitionConfig(
            output_path=output_path,
            shape=(2, 2, 3, 32, 32),  # 2 timepoints, 2 channels, 3 z-levels
            dtype=np.uint16,
            pixel_size_um=1.0,
        )

        writer = ZarrWriter(config)
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

        from control.core.zarr_writer import ZarrAcquisitionConfig, ZarrWriter

        output_path = os.path.join(temp_dir, "test.zarr")
        config = ZarrAcquisitionConfig(
            output_path=output_path,
            shape=(1, 1, 1, 32, 32),
            dtype=np.uint16,
            pixel_size_um=1.0,
        )

        writer = ZarrWriter(config)
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
        from control.core.zarr_writer import ZarrAcquisitionConfig, ZarrWriter

        output_path = os.path.join(temp_dir, "test_6d.zarr")
        config = ZarrAcquisitionConfig(
            output_path=output_path,
            shape=(4, 1, 2, 3, 32, 32),  # FOV, T, C, Z, Y, X
            dtype=np.uint16,
            pixel_size_um=1.0,
            is_hcs=False,
        )

        writer = ZarrWriter(config)
        writer.initialize()

        assert writer.is_initialized
        assert os.path.exists(output_path)

        # Check zarr.json attributes contain OME-NGFF 0.5 metadata with 6 axes
        zarr_json_path = os.path.join(output_path, "zarr.json")
        with open(zarr_json_path) as f:
            zarr_json = json.load(f)

        # Verify Zarr v3 structure
        assert zarr_json["zarr_format"] == 3
        assert "attributes" in zarr_json
        attrs = zarr_json["attributes"]

        # Verify OME-NGFF 0.5 namespace structure
        assert "ome" in attrs
        assert attrs["ome"]["version"] == "0.5"

        axes = attrs["ome"]["multiscales"][0]["axes"]
        assert len(axes) == 6
        axis_names = [a["name"] for a in axes]
        assert axis_names == ["fov", "t", "c", "z", "y", "x"]

        # Verify _squid structure field for 6D
        assert attrs["_squid"]["structure"] == "6D-FTCZYX"

        writer.finalize()

    def test_6d_write_multiple_fovs(self, temp_dir):
        """Test writing to multiple FOV indices in a 6D dataset."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, ZarrWriter

        output_path = os.path.join(temp_dir, "test_6d.zarr")
        config = ZarrAcquisitionConfig(
            output_path=output_path,
            shape=(4, 1, 2, 3, 32, 32),  # 4 FOVs, T, C, Z, Y, X
            dtype=np.uint16,
            pixel_size_um=1.0,
            is_hcs=False,
        )

        writer = ZarrWriter(config)
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

        from control.core.zarr_writer import ZarrAcquisitionConfig, ZarrWriter

        output_path = os.path.join(temp_dir, "test_6d.zarr")
        config = ZarrAcquisitionConfig(
            output_path=output_path,
            shape=(3, 1, 1, 1, 32, 32),  # 3 FOVs, T, C, Z, Y, X
            dtype=np.uint16,
            pixel_size_um=1.0,
            is_hcs=False,
        )

        writer = ZarrWriter(config)
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
        from control.core.zarr_writer import ZarrAcquisitionConfig, ZarrWriter

        output_path = os.path.join(temp_dir, "test_6d.zarr")
        config = ZarrAcquisitionConfig(
            output_path=output_path,
            shape=(4, 1, 1, 1, 32, 32),  # FOV, T, C, Z, Y, X
            dtype=np.uint16,
            pixel_size_um=1.0,
            is_hcs=False,
        )

        writer = ZarrWriter(config)
        writer.initialize()

        test_image = np.ones((32, 32), dtype=np.uint16)

        with pytest.raises(ValueError, match="FOV index required"):
            writer.write_frame(test_image, t=0, c=0, z=0)  # Missing fov

    def test_6d_invalid_fov_index_raises_error(self, temp_dir):
        """Test that out-of-range FOV index raises an error."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, ZarrWriter

        output_path = os.path.join(temp_dir, "test_6d.zarr")
        config = ZarrAcquisitionConfig(
            output_path=output_path,
            shape=(4, 1, 1, 1, 32, 32),  # 4 FOVs, T, C, Z, Y, X
            dtype=np.uint16,
            pixel_size_um=1.0,
            is_hcs=False,
        )

        writer = ZarrWriter(config)
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


class TestZarrWriterIOErrorHandling:
    """Tests for I/O error handling in zarr writer."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for test outputs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_finalize_handles_corrupted_zarr_json(self, temp_dir):
        """finalize() should handle corrupted zarr.json gracefully."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, ZarrWriter

        output_path = os.path.join(temp_dir, "test.zarr")
        config = ZarrAcquisitionConfig(
            output_path=output_path,
            shape=(1, 1, 1, 32, 32),
            dtype=np.uint16,
            pixel_size_um=1.0,
        )

        writer = ZarrWriter(config)
        writer.initialize()

        # Corrupt the zarr.json file with invalid JSON
        zarr_json_path = os.path.join(output_path, "zarr.json")
        with open(zarr_json_path, "w") as f:
            f.write("not valid json {{{")

        # finalize() should handle the error gracefully (log error, don't crash)
        writer.finalize()  # Should not raise
        assert writer.is_finalized

    def test_abort_handles_corrupted_zarr_json(self, temp_dir):
        """abort() should handle corrupted zarr.json gracefully."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, ZarrWriter

        output_path = os.path.join(temp_dir, "test.zarr")
        config = ZarrAcquisitionConfig(
            output_path=output_path,
            shape=(1, 1, 1, 32, 32),
            dtype=np.uint16,
            pixel_size_um=1.0,
        )

        writer = ZarrWriter(config)
        writer.initialize()

        # Corrupt the zarr.json file with invalid JSON
        zarr_json_path = os.path.join(output_path, "zarr.json")
        with open(zarr_json_path, "w") as f:
            f.write("not valid json {{{")

        # abort() should handle the error gracefully (log error, don't crash)
        writer.abort()  # Should not raise
        assert writer.is_finalized

    def test_abort_handles_missing_zarr_json(self, temp_dir):
        """abort() should handle missing zarr.json gracefully."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, ZarrWriter

        output_path = os.path.join(temp_dir, "test.zarr")
        config = ZarrAcquisitionConfig(
            output_path=output_path,
            shape=(1, 1, 1, 32, 32),
            dtype=np.uint16,
            pixel_size_um=1.0,
        )

        writer = ZarrWriter(config)
        writer.initialize()

        # Delete the zarr.json file
        zarr_json_path = os.path.join(output_path, "zarr.json")
        os.remove(zarr_json_path)

        # abort() should handle the missing file gracefully
        writer.abort()  # Should not raise
        assert writer.is_finalized


class TestZarrWriterEmptyDataset:
    """Tests for empty dataset handling."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for test outputs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_finalize_empty_dataset(self, temp_dir):
        """Finalizing a dataset with zero frames written should work."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, ZarrWriter

        output_path = os.path.join(temp_dir, "test.zarr")
        config = ZarrAcquisitionConfig(
            output_path=output_path,
            shape=(1, 1, 1, 32, 32),
            dtype=np.uint16,
            pixel_size_um=1.0,
        )

        writer = ZarrWriter(config)
        writer.initialize()

        # Finalize without writing any frames
        writer.finalize()

        assert writer.is_finalized

        # Verify metadata exists and is valid in zarr.json attributes
        zarr_json_path = os.path.join(output_path, "zarr.json")
        assert os.path.exists(zarr_json_path)

        with open(zarr_json_path) as f:
            zarr_json = json.load(f)

        assert "attributes" in zarr_json
        attrs = zarr_json["attributes"]
        assert "_squid" in attrs
        assert attrs["_squid"]["acquisition_complete"] is True


class TestZarrWriterDtypeAutoConversion:
    """Tests for automatic dtype conversion in write_frame."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for test outputs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.mark.parametrize("source_dtype", [np.uint8, np.float32, np.int32])
    def test_dtype_auto_conversion_to_uint16(self, temp_dir, source_dtype):
        """write_frame should auto-convert dtypes to target dtype."""
        import tensorstore as ts

        from control.core.zarr_writer import ZarrAcquisitionConfig, ZarrWriter

        output_path = os.path.join(temp_dir, "test.zarr")
        target_dtype = np.uint16
        config = ZarrAcquisitionConfig(
            output_path=output_path,
            shape=(1, 1, 1, 32, 32),
            dtype=np.dtype(target_dtype),
            pixel_size_um=0.5,
        )

        writer = ZarrWriter(config)
        writer.initialize()

        # Write image with different dtype
        image = np.ones((32, 32), dtype=source_dtype) * 100
        writer.write_frame(image, t=0, c=0, z=0)
        writer.wait_for_pending()
        writer.finalize()

        # Read back and verify dtype
        spec = {
            "driver": "zarr3",
            "kvstore": {"driver": "file", "path": output_path},
        }
        dataset = ts.open(spec).result()
        read_data = dataset[0, 0, 0, :, :].read().result()

        assert read_data.dtype == target_dtype
        np.testing.assert_array_equal(read_data, np.ones((32, 32), dtype=target_dtype) * 100)


class TestZarrWriterMetadataErrorHandling:
    """Tests for metadata write error handling."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for test outputs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_write_metadata_raises_on_permission_error(self, temp_dir):
        """_write_zarr_metadata should raise RuntimeError on permission errors."""
        from control.core.zarr_writer import ZarrAcquisitionConfig, ZarrWriter

        # Create a read-only directory
        readonly_dir = os.path.join(temp_dir, "readonly")
        os.makedirs(readonly_dir)
        output_path = os.path.join(readonly_dir, "test.zarr")

        config = ZarrAcquisitionConfig(
            output_path=output_path,
            shape=(1, 1, 1, 32, 32),
            dtype=np.uint16,
            pixel_size_um=1.0,
        )

        writer = ZarrWriter(config)

        # Make the directory read-only after creating the writer
        # Note: This test may be platform-specific
        # We'll use mock to simulate the permission error instead
        from unittest.mock import patch

        with patch("builtins.open", side_effect=PermissionError("Permission denied")):
            with pytest.raises(RuntimeError, match="Failed to write zarr metadata"):
                writer._write_zarr_metadata()


class TestHCSWorkflowIntegration:
    """Integration tests for full HCS (High Content Screening) workflow.

    These tests verify the complete plate/well/FOV hierarchy is correctly
    created when using SaveZarrJob with HCS mode enabled.
    """

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for test outputs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_hcs_full_workflow(self, temp_dir):
        """Test complete HCS workflow: multiple wells, FOVs, channels, z-levels.

        Verifies:
        1. Plate metadata is written with correct well references
        2. Well metadata is written with correct field references
        3. Data arrays exist and have correct shapes
        4. Data can be read back correctly
        """
        import tensorstore as ts

        # Clear any state from previous tests
        SaveZarrJob.clear_writers()

        # Configure a 2x2 well plate with 2 FOVs per well
        wells = ["A1", "A2", "B1", "B2"]
        fovs_per_well = 2
        t_size, c_size, z_size = 2, 2, 3
        image_shape = (32, 32)

        zarr_info = ZarrWriterInfo(
            base_path=temp_dir,
            t_size=t_size,
            c_size=c_size,
            z_size=z_size,
            is_hcs=True,
            region_fov_counts={well: fovs_per_well for well in wells},
            channel_names=["DAPI", "GFP"],
            channel_colors=["#0000FF", "#00FF00"],
            pixel_size_um=0.5,
        )

        # Simulate acquisition: write frames for all wells/FOVs/t/c/z
        for well_id in wells:
            for fov in range(fovs_per_well):
                for t in range(t_size):
                    for c in range(c_size):
                        for z in range(z_size):
                            # Create unique image pattern for verification
                            well_idx = wells.index(well_id)
                            pattern_value = well_idx * 10000 + fov * 1000 + t * 100 + c * 10 + z
                            image = np.ones(image_shape, dtype=np.uint16) * pattern_value

                            capture_info = make_test_capture_info(
                                region_id=well_id,
                                fov=fov,
                                z_index=z,
                                config_idx=c,
                                time_point=t,
                            )

                            job = SaveZarrJob(
                                capture_info=capture_info,
                                capture_image=JobImage(image_array=image),
                            )
                            job.zarr_writer_info = zarr_info
                            job.run()

        # Finalize all writers
        success = SaveZarrJob.finalize_all_writers()
        assert success, "Failed to finalize all zarr writers"

        # Verify plate structure
        plate_path = os.path.join(temp_dir, "plate.ome.zarr")
        assert os.path.exists(plate_path), "plate.ome.zarr should exist"

        # Verify plate metadata
        plate_zarr_json = os.path.join(plate_path, "zarr.json")
        assert os.path.exists(plate_zarr_json), "plate zarr.json should exist"

        with open(plate_zarr_json) as f:
            plate_meta = json.load(f)

        assert "attributes" in plate_meta
        attrs = plate_meta["attributes"]
        assert "ome" in attrs, "Plate metadata should have 'ome' namespace"
        assert attrs["ome"]["version"] == "0.5"
        assert "plate" in attrs["ome"]

        plate_info = attrs["ome"]["plate"]
        assert plate_info["version"] == "0.5"
        assert len(plate_info["rows"]) == 2  # A, B
        assert len(plate_info["columns"]) == 2  # 1, 2
        assert len(plate_info["wells"]) == 4  # A1, A2, B1, B2

        # Verify each well
        for well_id in wells:
            row = well_id[0]  # A or B
            col = well_id[1:]  # 1 or 2

            well_path = os.path.join(plate_path, row, col)
            assert os.path.exists(well_path), f"Well {well_id} directory should exist"

            # Verify well metadata
            well_zarr_json = os.path.join(well_path, "zarr.json")
            assert os.path.exists(well_zarr_json), f"Well {well_id} zarr.json should exist"

            with open(well_zarr_json) as f:
                well_meta = json.load(f)

            assert "attributes" in well_meta
            well_attrs = well_meta["attributes"]
            assert "ome" in well_attrs, f"Well {well_id} should have 'ome' namespace"
            assert well_attrs["ome"]["version"] == "0.5"
            assert "well" in well_attrs["ome"]

            well_info = well_attrs["ome"]["well"]
            assert well_info["version"] == "0.5"
            assert len(well_info["images"]) == fovs_per_well

            # Verify each FOV data array and metadata location
            for fov in range(fovs_per_well):
                fov_group_path = os.path.join(well_path, str(fov))
                fov_array_path = os.path.join(fov_group_path, "0")
                assert os.path.exists(fov_array_path), f"FOV {fov} data path should exist"

                # Verify OME metadata is at GROUP level ({fov}/zarr.json), not array level ({fov}/0/zarr.json)
                fov_group_zarr_json = os.path.join(fov_group_path, "zarr.json")
                assert os.path.exists(fov_group_zarr_json), f"FOV {fov} group zarr.json should exist"

                with open(fov_group_zarr_json) as f:
                    fov_group_meta = json.load(f)

                assert "attributes" in fov_group_meta
                fov_attrs = fov_group_meta["attributes"]
                assert "ome" in fov_attrs, "FOV group should have 'ome' namespace with multiscales/omero"
                assert "multiscales" in fov_attrs["ome"], "FOV group should have multiscales"
                assert "omero" in fov_attrs["ome"], "FOV group should have omero"
                assert "_squid" in fov_attrs, "FOV group should have _squid metadata"

                # Verify array-level zarr.json does NOT have OME attributes (TensorStore creates this)
                fov_array_zarr_json = os.path.join(fov_array_path, "zarr.json")
                if os.path.exists(fov_array_zarr_json):
                    with open(fov_array_zarr_json) as f:
                        array_meta = json.load(f)
                    # Array should have zarr format info but not OME attributes
                    array_attrs = array_meta.get("attributes", {})
                    assert "ome" not in array_attrs, "Array-level zarr.json should not have OME metadata"

                # Read data back using tensorstore
                spec = {
                    "driver": "zarr3",
                    "kvstore": {"driver": "file", "path": fov_array_path},
                }
                dataset = ts.open(spec).result()

                # Verify shape: (T, C, Z, Y, X)
                expected_shape = (t_size, c_size, z_size, *image_shape)
                assert (
                    dataset.shape == expected_shape
                ), f"Well {well_id} FOV {fov} should have shape {expected_shape}, got {dataset.shape}"

                # Verify data content for one frame
                well_idx = wells.index(well_id)
                expected_value = well_idx * 10000 + fov * 1000 + 0 * 100 + 0 * 10 + 0
                read_data = dataset[0, 0, 0, :, :].read().result()
                assert (
                    read_data[0, 0] == expected_value
                ), f"Well {well_id} FOV {fov} data mismatch: expected {expected_value}, got {read_data[0, 0]}"

    def test_hcs_metadata_written_once(self, temp_dir):
        """Test that plate/well metadata is written only once even with multiple FOVs."""
        # Clear any state from previous tests
        SaveZarrJob.clear_writers()

        zarr_info = ZarrWriterInfo(
            base_path=temp_dir,
            t_size=1,
            c_size=1,
            z_size=1,
            is_hcs=True,
            region_fov_counts={"A1": 3},  # 3 FOVs in one well
            channel_names=["DAPI"],
            pixel_size_um=0.5,
        )

        # Track log messages to verify metadata is only written once
        plate_writes = []
        well_writes = []

        original_info = SaveZarrJob._log.info

        def tracking_info(msg, *args, **kwargs):
            if "Wrote HCS plate metadata" in msg:
                plate_writes.append(msg)
            if "Wrote HCS well metadata" in msg:
                well_writes.append(msg)
            return original_info(msg, *args, **kwargs)

        with patch.object(SaveZarrJob._log, "info", tracking_info):
            # Write 3 FOVs for the same well
            for fov in range(3):
                image = np.ones((32, 32), dtype=np.uint16) * fov
                capture_info = make_test_capture_info(region_id="A1", fov=fov)

                job = SaveZarrJob(
                    capture_info=capture_info,
                    capture_image=JobImage(image_array=image),
                )
                job.zarr_writer_info = zarr_info
                job.run()

        SaveZarrJob.finalize_all_writers()

        # Plate metadata should be written exactly once
        assert len(plate_writes) == 1, f"Plate metadata should be written once, got {len(plate_writes)}"

        # Well metadata should be written exactly once (even with 3 FOVs)
        # The tracking set should have exactly one entry for this well
        assert (
            len(SaveZarrJob._hcs_wells_written) == 1
        ), f"Well metadata should be tracked once, got {len(SaveZarrJob._hcs_wells_written)}"

        # Verify the actual file exists
        plate_path = os.path.join(temp_dir, "plate.ome.zarr")
        well_path = os.path.join(plate_path, "A", "1")
        assert os.path.exists(os.path.join(well_path, "zarr.json"))

        # Clean up tracking sets for next test
        SaveZarrJob.clear_writers()

    def test_hcs_single_well_single_fov(self, temp_dir):
        """Test minimal HCS case: single well, single FOV."""
        # Clear any state from previous tests
        SaveZarrJob.clear_writers()

        zarr_info = ZarrWriterInfo(
            base_path=temp_dir,
            t_size=1,
            c_size=1,
            z_size=1,
            is_hcs=True,
            region_fov_counts={"C3": 1},
            channel_names=["BF"],
            pixel_size_um=1.0,
        )

        image = np.ones((64, 64), dtype=np.uint16) * 42
        capture_info = make_test_capture_info(region_id="C3", fov=0)

        job = SaveZarrJob(
            capture_info=capture_info,
            capture_image=JobImage(image_array=image),
        )
        job.zarr_writer_info = zarr_info
        job.run()

        SaveZarrJob.finalize_all_writers()

        # Verify structure
        plate_path = os.path.join(temp_dir, "plate.ome.zarr")
        assert os.path.exists(plate_path)

        # Verify plate has only C row and column 3
        with open(os.path.join(plate_path, "zarr.json")) as f:
            plate_meta = json.load(f)

        plate_info = plate_meta["attributes"]["ome"]["plate"]
        assert len(plate_info["rows"]) == 1
        assert plate_info["rows"][0]["name"] == "C"
        assert len(plate_info["columns"]) == 1
        assert plate_info["columns"][0]["name"] == "3"
        assert len(plate_info["wells"]) == 1

        # Verify well exists
        well_path = os.path.join(plate_path, "C", "3")
        assert os.path.exists(well_path)

        # Verify data
        import tensorstore as ts

        fov_path = os.path.join(well_path, "0", "0")
        spec = {"driver": "zarr3", "kvstore": {"driver": "file", "path": fov_path}}
        dataset = ts.open(spec).result()
        assert dataset.shape == (1, 1, 1, 64, 64)

    def test_hcs_multirow_well_ids(self, temp_dir):
        """Test HCS with multi-character row IDs (e.g., AA1, AB2)."""
        # Clear any state from previous tests
        SaveZarrJob.clear_writers()

        # 384-well plates can have rows like AA, AB, etc.
        wells = ["A1", "AA1", "AB2"]

        zarr_info = ZarrWriterInfo(
            base_path=temp_dir,
            t_size=1,
            c_size=1,
            z_size=1,
            is_hcs=True,
            region_fov_counts={w: 1 for w in wells},
            channel_names=["DAPI"],
            pixel_size_um=0.5,
        )

        for well_id in wells:
            image = np.ones((32, 32), dtype=np.uint16)
            capture_info = make_test_capture_info(region_id=well_id, fov=0)

            job = SaveZarrJob(
                capture_info=capture_info,
                capture_image=JobImage(image_array=image),
            )
            job.zarr_writer_info = zarr_info
            job.run()

        SaveZarrJob.finalize_all_writers()

        # Verify plate structure
        plate_path = os.path.join(temp_dir, "plate.ome.zarr")

        with open(os.path.join(plate_path, "zarr.json")) as f:
            plate_meta = json.load(f)

        plate_info = plate_meta["attributes"]["ome"]["plate"]

        # Should have rows A, AA, AB
        row_names = [r["name"] for r in plate_info["rows"]]
        assert "A" in row_names
        assert "AA" in row_names
        assert "AB" in row_names

        # Verify well directories exist
        assert os.path.exists(os.path.join(plate_path, "A", "1"))
        assert os.path.exists(os.path.join(plate_path, "AA", "1"))
        assert os.path.exists(os.path.join(plate_path, "AB", "2"))

    def test_hcs_plate_metadata_write_failure_propagates(self, temp_dir):
        """Test that plate metadata write failure propagates as RuntimeError."""
        # Clear any state from previous tests
        SaveZarrJob.clear_writers()

        zarr_info = ZarrWriterInfo(
            base_path=temp_dir,
            t_size=1,
            c_size=1,
            z_size=1,
            is_hcs=True,
            region_fov_counts={"A1": 1},
            channel_names=["DAPI"],
            pixel_size_um=0.5,
        )

        image = np.ones((32, 32), dtype=np.uint16)
        capture_info = make_test_capture_info(region_id="A1", fov=0)

        job = SaveZarrJob(
            capture_info=capture_info,
            capture_image=JobImage(image_array=image),
        )
        job.zarr_writer_info = zarr_info

        # Mock write_plate_metadata to raise RuntimeError
        with patch(
            "control.core.zarr_writer.write_plate_metadata",
            side_effect=RuntimeError("Simulated plate metadata write failure"),
        ):
            with pytest.raises(RuntimeError, match="Simulated plate metadata write failure"):
                job.run()

        # Clean up
        SaveZarrJob.clear_writers()

    def test_hcs_well_metadata_write_failure_propagates(self, temp_dir):
        """Test that well metadata write failure propagates as RuntimeError."""
        # Clear any state from previous tests
        SaveZarrJob.clear_writers()

        zarr_info = ZarrWriterInfo(
            base_path=temp_dir,
            t_size=1,
            c_size=1,
            z_size=1,
            is_hcs=True,
            region_fov_counts={"A1": 1},
            channel_names=["DAPI"],
            pixel_size_um=0.5,
        )

        image = np.ones((32, 32), dtype=np.uint16)
        capture_info = make_test_capture_info(region_id="A1", fov=0)

        job = SaveZarrJob(
            capture_info=capture_info,
            capture_image=JobImage(image_array=image),
        )
        job.zarr_writer_info = zarr_info

        # Mock write_well_metadata to raise RuntimeError (plate metadata succeeds)
        with patch(
            "control.core.zarr_writer.write_well_metadata",
            side_effect=RuntimeError("Simulated well metadata write failure"),
        ):
            with pytest.raises(RuntimeError, match="Simulated well metadata write failure"):
                job.run()

        # Clean up
        SaveZarrJob.clear_writers()


class TestZarrPathConsistency:
    """Tests that NDViewer paths match writer paths.

    Both gui_hcs and ZarrWriterInfo now use shared utility functions from
    control.utils, ensuring path consistency. These tests verify the
    utility functions produce correct paths.
    """

    def test_hcs_paths_use_shared_utility(self):
        """Verify HCS mode paths follow OME-NGFF structure.

        build_hcs_zarr_fov_path returns the GROUP path (field level).
        ZarrWriterInfo.get_output_path returns the ARRAY path (appends /0).
        """
        from control.utils import build_hcs_zarr_fov_path

        base_path = "/tmp/test_acquisition"

        # Test various well IDs and FOV indices
        # Format: (well_id, fov, expected_group_path, expected_array_path)
        test_cases = [
            ("A1", 0, "/tmp/test_acquisition/plate.ome.zarr/A/1/0", "/tmp/test_acquisition/plate.ome.zarr/A/1/0/0"),
            ("A1", 1, "/tmp/test_acquisition/plate.ome.zarr/A/1/1", "/tmp/test_acquisition/plate.ome.zarr/A/1/1/0"),
            ("B2", 0, "/tmp/test_acquisition/plate.ome.zarr/B/2/0", "/tmp/test_acquisition/plate.ome.zarr/B/2/0/0"),
            ("B12", 2, "/tmp/test_acquisition/plate.ome.zarr/B/12/2", "/tmp/test_acquisition/plate.ome.zarr/B/12/2/0"),
            ("AA1", 0, "/tmp/test_acquisition/plate.ome.zarr/AA/1/0", "/tmp/test_acquisition/plate.ome.zarr/AA/1/0/0"),
        ]

        for well_id, fov, expected_group_path, expected_array_path in test_cases:
            # Utility returns GROUP path (for NDViewer/readers)
            group_path = build_hcs_zarr_fov_path(base_path, well_id, fov)
            assert group_path == expected_group_path, (
                f"Group path mismatch for well={well_id}, fov={fov}: "
                f"got={group_path}, expected={expected_group_path}"
            )

            # Writer returns ARRAY path (group + /0)
            zarr_info = ZarrWriterInfo(
                base_path=base_path,
                t_size=1,
                c_size=3,
                z_size=1,
                is_hcs=True,
            )
            writer_path = zarr_info.get_output_path(well_id, fov)
            assert writer_path == expected_array_path, (
                f"Writer path mismatch for well={well_id}, fov={fov}: "
                f"writer={writer_path}, expected={expected_array_path}"
            )

    def test_non_hcs_per_fov_paths_use_shared_utility(self):
        """Verify non-HCS per-FOV mode paths follow OME-NGFF structure.

        build_per_fov_zarr_path returns the GROUP path.
        ZarrWriterInfo.get_output_path returns the ARRAY path (appends /0).
        """
        from control.utils import build_per_fov_zarr_path

        base_path = "/tmp/test_acquisition"

        # Format: (region_id, fov, expected_group_path, expected_array_path)
        test_cases = [
            (
                "region_0",
                0,
                "/tmp/test_acquisition/zarr/region_0/fov_0.ome.zarr",
                "/tmp/test_acquisition/zarr/region_0/fov_0.ome.zarr/0",
            ),
            (
                "region_0",
                1,
                "/tmp/test_acquisition/zarr/region_0/fov_1.ome.zarr",
                "/tmp/test_acquisition/zarr/region_0/fov_1.ome.zarr/0",
            ),
            (
                "scan_area_1",
                0,
                "/tmp/test_acquisition/zarr/scan_area_1/fov_0.ome.zarr",
                "/tmp/test_acquisition/zarr/scan_area_1/fov_0.ome.zarr/0",
            ),
            (
                "custom_name",
                5,
                "/tmp/test_acquisition/zarr/custom_name/fov_5.ome.zarr",
                "/tmp/test_acquisition/zarr/custom_name/fov_5.ome.zarr/0",
            ),
        ]

        for region_id, fov, expected_group_path, expected_array_path in test_cases:
            # Utility returns GROUP path (for NDViewer/readers)
            group_path = build_per_fov_zarr_path(base_path, region_id, fov)
            assert group_path == expected_group_path, (
                f"Group path mismatch for region={region_id}, fov={fov}: "
                f"got={group_path}, expected={expected_group_path}"
            )

            # Writer returns ARRAY path (group + /0)
            zarr_info = ZarrWriterInfo(
                base_path=base_path,
                t_size=1,
                c_size=3,
                z_size=1,
                is_hcs=False,
                use_6d_fov=False,
            )
            writer_path = zarr_info.get_output_path(region_id, fov)
            assert writer_path == expected_array_path, (
                f"Writer path mismatch for region={region_id}, fov={fov}: "
                f"writer={writer_path}, expected={expected_array_path}"
            )

    def test_6d_paths_use_shared_utility(self):
        """Verify 6D mode paths use shared utility."""
        from control.utils import build_6d_zarr_path

        base_path = "/tmp/test_acquisition"

        test_cases = [
            ("region_0", "/tmp/test_acquisition/zarr/region_0/acquisition.zarr"),
            ("scan_area_1", "/tmp/test_acquisition/zarr/scan_area_1/acquisition.zarr"),
        ]

        for region_id, expected_path in test_cases:
            # Test utility function directly
            util_path = build_6d_zarr_path(base_path, region_id)
            assert util_path == expected_path, (
                f"Utility path mismatch for region={region_id}: " f"got={util_path}, expected={expected_path}"
            )

            # Verify ZarrWriterInfo uses the same path
            zarr_info = ZarrWriterInfo(
                base_path=base_path,
                t_size=1,
                c_size=3,
                z_size=1,
                is_hcs=False,
                use_6d_fov=True,
            )
            writer_path = zarr_info.get_output_path(region_id, 0)
            assert writer_path == util_path, (
                f"Writer path doesn't match utility for region={region_id}: " f"writer={writer_path}, util={util_path}"
            )

    def test_well_id_parsing(self):
        """Verify well ID parsing utility works correctly."""
        from control.utils import parse_well_id

        test_cases = [
            ("A1", ("A", "1")),
            ("B12", ("B", "12")),
            ("C3", ("C", "3")),
            ("AA1", ("AA", "1")),
            ("AB12", ("AB", "12")),
            ("H8", ("H", "8")),
            ("a1", ("A", "1")),  # lowercase
        ]

        for well_id, expected in test_cases:
            result = parse_well_id(well_id)
            assert result == expected, f"Failed for {well_id}: got {result}, expected {expected}"
