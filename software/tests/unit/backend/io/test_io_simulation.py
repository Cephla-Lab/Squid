"""Tests for simulated disk I/O module."""

import time
from io import BytesIO
from unittest.mock import patch, MagicMock

import numpy as np
import pytest

# Import module under test
from squid.backend.io import io_simulation


class TestIsSimulationEnabled:
    """Test is_simulation_enabled function."""

    def test_returns_false_by_default(self):
        """When SIMULATED_DISK_IO_ENABLED is not set, returns False."""
        with patch.object(io_simulation, "_def") as mock_def:
            delattr(mock_def, "SIMULATED_DISK_IO_ENABLED")
            result = io_simulation.is_simulation_enabled()
        assert result is False

    def test_returns_true_when_enabled(self):
        """When SIMULATED_DISK_IO_ENABLED is True, returns True."""
        with patch.object(io_simulation, "_def") as mock_def:
            mock_def.SIMULATED_DISK_IO_ENABLED = True
            mock_def.SIMULATION_FORCE_SAVE_IMAGES = False
            result = io_simulation.is_simulation_enabled()
        assert result is True

    def test_returns_false_when_disabled(self):
        """When SIMULATED_DISK_IO_ENABLED is False, returns False."""
        with patch.object(io_simulation, "_def") as mock_def:
            mock_def.SIMULATED_DISK_IO_ENABLED = False
            result = io_simulation.is_simulation_enabled()
        assert result is False


class TestGetSimulatedSpeedMbs:
    """Test get_simulated_speed_mb_s function."""

    def test_returns_default_200(self):
        """When SIMULATED_DISK_IO_SPEED_MB_S is not set, returns 200.0."""
        with patch.object(io_simulation, "_def") as mock_def:
            delattr(mock_def, "SIMULATED_DISK_IO_SPEED_MB_S")
            result = io_simulation.get_simulated_speed_mb_s()
        assert result == 200.0

    def test_returns_configured_value(self):
        """Returns configured speed value."""
        with patch.object(io_simulation, "_def") as mock_def:
            mock_def.SIMULATED_DISK_IO_SPEED_MB_S = 500.0
            result = io_simulation.get_simulated_speed_mb_s()
        assert result == 500.0


class TestGetSimulatedCompression:
    """Test get_simulated_compression function."""

    def test_returns_default_true(self):
        """When SIMULATED_DISK_IO_COMPRESSION is not set, returns True."""
        with patch.object(io_simulation, "_def") as mock_def:
            delattr(mock_def, "SIMULATED_DISK_IO_COMPRESSION")
            result = io_simulation.get_simulated_compression()
        assert result is True

    def test_returns_configured_value(self):
        """Returns configured compression value."""
        with patch.object(io_simulation, "_def") as mock_def:
            mock_def.SIMULATED_DISK_IO_COMPRESSION = False
            result = io_simulation.get_simulated_compression()
        assert result is False


class TestThrottleForSpeed:
    """Test throttle_for_speed function."""

    def test_zero_speed_no_delay(self):
        """When speed is 0, no delay is applied."""
        delay = io_simulation.throttle_for_speed(1_000_000, 0.0)
        assert delay == 0.0

    def test_negative_speed_no_delay(self):
        """When speed is negative, no delay is applied."""
        delay = io_simulation.throttle_for_speed(1_000_000, -10.0)
        assert delay == 0.0

    def test_negative_bytes_treated_as_zero(self):
        """When bytes_count is negative, treats as zero and logs warning."""
        delay = io_simulation.throttle_for_speed(-1000, 200.0)
        assert delay == 0.0

    def test_calculates_correct_delay(self):
        """Delay is calculated correctly based on bytes and speed."""
        # 1 MB at 100 MB/s should take ~0.01 seconds
        bytes_count = 1024 * 1024  # 1 MB
        speed = 100.0  # MB/s
        expected_delay = 0.01  # seconds

        start = time.time()
        delay = io_simulation.throttle_for_speed(bytes_count, speed)
        elapsed = time.time() - start

        assert abs(delay - expected_delay) < 0.001
        assert elapsed >= delay * 0.9  # Allow some timing variance


class TestSimulatedTiffWrite:
    """Test simulated_tiff_write function."""

    @pytest.fixture
    def sample_image(self):
        """Create a sample grayscale image."""
        return np.random.randint(0, 65535, size=(512, 512), dtype=np.uint16)

    def test_returns_bytes_written(self, sample_image):
        """Returns the number of bytes that would be written."""
        with patch.object(io_simulation, "get_simulated_speed_mb_s", return_value=10000.0):
            with patch.object(io_simulation, "get_simulated_compression", return_value=False):
                bytes_written = io_simulation.simulated_tiff_write(sample_image)

        assert bytes_written > 0
        # Uncompressed 512x512 uint16 should be around 512 KB + headers
        assert bytes_written >= 512 * 512 * 2

    def test_uses_compression_when_enabled(self):
        """Uses LZW compression when enabled on compressible data."""
        # Create highly compressible data (repeated patterns)
        compressible_image = np.zeros((512, 512), dtype=np.uint16)
        compressible_image[::2, :] = 65535  # Alternating rows

        with patch.object(io_simulation, "get_simulated_speed_mb_s", return_value=10000.0):
            with patch.object(io_simulation, "get_simulated_compression", return_value=True):
                compressed = io_simulation.simulated_tiff_write(compressible_image)

            with patch.object(io_simulation, "get_simulated_compression", return_value=False):
                uncompressed = io_simulation.simulated_tiff_write(compressible_image)

        # Compressed should be smaller than uncompressed for compressible data
        assert compressed < uncompressed

    def test_applies_throttle(self, sample_image):
        """Applies throttle based on configured speed."""
        with patch.object(io_simulation, "throttle_for_speed") as mock_throttle:
            mock_throttle.return_value = 0.0
            with patch.object(io_simulation, "get_simulated_speed_mb_s", return_value=200.0):
                with patch.object(io_simulation, "get_simulated_compression", return_value=False):
                    bytes_written = io_simulation.simulated_tiff_write(sample_image)

        mock_throttle.assert_called_once()
        call_args = mock_throttle.call_args[0]
        assert call_args[0] == bytes_written
        assert call_args[1] == 200.0


class TestSimulatedOmeTiffWrite:
    """Test simulated_ome_tiff_write function."""

    @pytest.fixture
    def sample_image(self):
        """Create a sample grayscale image."""
        return np.random.randint(0, 65535, size=(256, 256), dtype=np.uint16)

    def test_initializes_stack_on_first_plane(self, sample_image):
        """First plane initializes stack tracking."""
        io_simulation.reset_simulated_stacks()

        with patch.object(io_simulation, "get_simulated_speed_mb_s", return_value=10000.0):
            with patch.object(io_simulation, "get_simulated_compression", return_value=False):
                bytes_written = io_simulation.simulated_ome_tiff_write(
                    image=sample_image,
                    stack_key="test_stack",
                    shape=(1, 1, 1, 256, 256),  # T, Z, C, Y, X
                    time_point=0,
                    z_index=0,
                    channel_index=0,
                )

        assert bytes_written > 0

    def test_tracks_planes_correctly(self, sample_image):
        """Tracks written planes and cleans up when complete."""
        io_simulation.reset_simulated_stacks()

        shape = (2, 2, 1, 256, 256)  # 2 timepoints, 2 z, 1 channel = 4 planes

        with patch.object(io_simulation, "get_simulated_speed_mb_s", return_value=10000.0):
            with patch.object(io_simulation, "get_simulated_compression", return_value=False):
                # Write 4 planes
                for t in range(2):
                    for z in range(2):
                        io_simulation.simulated_ome_tiff_write(
                            image=sample_image,
                            stack_key="test_stack",
                            shape=shape,
                            time_point=t,
                            z_index=z,
                            channel_index=0,
                        )

        # Stack should be cleaned up after completion
        assert "test_stack" not in io_simulation._simulated_ome_stacks

    def test_returns_bytes_for_each_plane(self, sample_image):
        """Returns bytes written for each plane."""
        io_simulation.reset_simulated_stacks()

        with patch.object(io_simulation, "get_simulated_speed_mb_s", return_value=10000.0):
            with patch.object(io_simulation, "get_simulated_compression", return_value=False):
                bytes_1 = io_simulation.simulated_ome_tiff_write(
                    image=sample_image,
                    stack_key="test_stack",
                    shape=(2, 1, 1, 256, 256),
                    time_point=0,
                    z_index=0,
                    channel_index=0,
                )
                bytes_2 = io_simulation.simulated_ome_tiff_write(
                    image=sample_image,
                    stack_key="test_stack",
                    shape=(2, 1, 1, 256, 256),
                    time_point=1,
                    z_index=0,
                    channel_index=0,
                )

        # Both should return similar byte counts
        assert bytes_1 > 0
        assert bytes_2 > 0
        assert abs(bytes_1 - bytes_2) < 1000  # Small variance allowed


class TestResetSimulatedStacks:
    """Test reset_simulated_stacks function."""

    def test_clears_stack_tracking(self):
        """Clears all tracked stacks."""
        # Add some entries
        io_simulation._simulated_ome_stacks["stack1"] = {"shape": (1, 1, 1, 256, 256)}
        io_simulation._simulated_ome_stacks["stack2"] = {"shape": (2, 2, 1, 256, 256)}

        io_simulation.reset_simulated_stacks()

        assert len(io_simulation._simulated_ome_stacks) == 0
