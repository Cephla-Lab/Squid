"""Tests for squid.camera.settings_cache module."""

import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from squid.camera.settings_cache import (
    CachedCameraSettings,
    DEFAULT_BINNING,
    load_camera_settings,
    save_camera_settings,
)
from squid.config import CameraPixelFormat


class TestCachedCameraSettings:
    """Tests for CachedCameraSettings dataclass validation."""

    def test_valid_settings(self):
        settings = CachedCameraSettings(binning=(2, 2), pixel_format="MONO8")
        assert settings.binning == (2, 2)
        assert settings.pixel_format == "MONO8"

    def test_valid_settings_no_pixel_format(self):
        settings = CachedCameraSettings(binning=(1, 1), pixel_format=None)
        assert settings.binning == (1, 1)
        assert settings.pixel_format is None

    def test_invalid_binning_length(self):
        with pytest.raises(ValueError, match="must be a 2-tuple"):
            CachedCameraSettings(binning=(1,), pixel_format=None)

    def test_invalid_binning_too_long(self):
        with pytest.raises(ValueError, match="must be a 2-tuple"):
            CachedCameraSettings(binning=(1, 2, 3), pixel_format=None)

    def test_invalid_binning_zero(self):
        with pytest.raises(ValueError, match="must be positive"):
            CachedCameraSettings(binning=(0, 1), pixel_format=None)

    def test_invalid_binning_negative(self):
        with pytest.raises(ValueError, match="must be positive"):
            CachedCameraSettings(binning=(1, -1), pixel_format=None)

    def test_frozen_dataclass(self):
        settings = CachedCameraSettings(binning=(2, 2), pixel_format="MONO8")
        with pytest.raises(Exception):  # FrozenInstanceError
            settings.binning = (1, 1)


class TestSaveCameraSettings:
    """Tests for save_camera_settings function."""

    def test_save_settings(self):
        """Test saving camera settings to a file."""
        mock_camera = Mock()
        mock_camera.get_binning.return_value = (2, 2)
        mock_camera.get_pixel_format.return_value = CameraPixelFormat.MONO8

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "camera_settings.yaml"
            save_camera_settings(mock_camera, cache_path)

            assert cache_path.exists()
            with open(cache_path, "r") as f:
                data = yaml.safe_load(f)

            assert data["binning"] == [2, 2]
            assert data["pixel_format"] == "MONO8"

    def test_save_settings_no_pixel_format(self):
        """Test saving when pixel format is None."""
        mock_camera = Mock()
        mock_camera.get_binning.return_value = (1, 1)
        mock_camera.get_pixel_format.return_value = None

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "camera_settings.yaml"
            save_camera_settings(mock_camera, cache_path)

            with open(cache_path, "r") as f:
                data = yaml.safe_load(f)

            assert data["binning"] == [1, 1]
            assert data["pixel_format"] is None

    def test_save_creates_parent_directories(self):
        """Test that save creates parent directories if needed."""
        mock_camera = Mock()
        mock_camera.get_binning.return_value = (1, 1)
        mock_camera.get_pixel_format.return_value = None

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "nested" / "dir" / "camera_settings.yaml"
            save_camera_settings(mock_camera, cache_path)

            assert cache_path.exists()

    def test_save_handles_camera_error(self):
        """Test that save handles camera errors gracefully."""
        mock_camera = Mock()
        mock_camera.get_binning.side_effect = RuntimeError("Camera disconnected")

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "camera_settings.yaml"
            # Should not raise, just log error
            save_camera_settings(mock_camera, cache_path)
            assert not cache_path.exists()


class TestLoadCameraSettings:
    """Tests for load_camera_settings function."""

    def test_load_settings(self):
        """Test loading valid camera settings."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "camera_settings.yaml"
            with open(cache_path, "w") as f:
                yaml.safe_dump({"binning": [2, 2], "pixel_format": "MONO8"}, f)

            settings = load_camera_settings(cache_path)

            assert settings is not None
            assert settings.binning == (2, 2)
            assert settings.pixel_format == "MONO8"

    def test_load_settings_no_pixel_format(self):
        """Test loading when pixel format is null."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "camera_settings.yaml"
            with open(cache_path, "w") as f:
                yaml.safe_dump({"binning": [4, 4], "pixel_format": None}, f)

            settings = load_camera_settings(cache_path)

            assert settings is not None
            assert settings.binning == (4, 4)
            assert settings.pixel_format is None

    def test_load_missing_file(self):
        """Test loading when file doesn't exist returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "nonexistent.yaml"
            settings = load_camera_settings(cache_path)
            assert settings is None

    def test_load_corrupted_yaml(self):
        """Test loading corrupted YAML returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "camera_settings.yaml"
            with open(cache_path, "w") as f:
                f.write("not: valid: yaml: {{{\n  - broken")

            settings = load_camera_settings(cache_path)
            assert settings is None

    def test_load_missing_binning_uses_default(self):
        """Test loading with missing binning key uses default."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "camera_settings.yaml"
            with open(cache_path, "w") as f:
                yaml.safe_dump({"pixel_format": "MONO8"}, f)

            settings = load_camera_settings(cache_path)

            assert settings is not None
            assert settings.binning == DEFAULT_BINNING
            assert settings.pixel_format == "MONO8"

    def test_load_invalid_binning_format_uses_default(self):
        """Test loading with invalid binning format uses default."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "camera_settings.yaml"
            with open(cache_path, "w") as f:
                yaml.safe_dump({"binning": "invalid", "pixel_format": "MONO8"}, f)

            settings = load_camera_settings(cache_path)

            assert settings is not None
            assert settings.binning == DEFAULT_BINNING

    def test_load_binning_wrong_length_uses_default(self):
        """Test loading with wrong binning length uses default."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "camera_settings.yaml"
            with open(cache_path, "w") as f:
                yaml.safe_dump({"binning": [1, 2, 3], "pixel_format": None}, f)

            settings = load_camera_settings(cache_path)

            assert settings is not None
            assert settings.binning == DEFAULT_BINNING

    def test_load_binning_negative_returns_none(self):
        """Test loading with negative binning values returns None due to validation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "camera_settings.yaml"
            with open(cache_path, "w") as f:
                yaml.safe_dump({"binning": [-1, 2], "pixel_format": None}, f)

            settings = load_camera_settings(cache_path)
            # Should return None because CachedCameraSettings validation fails
            assert settings is None


class TestRoundTrip:
    """Tests for save/load round-trip."""

    def test_round_trip(self):
        """Test that settings survive a save/load round-trip."""
        mock_camera = Mock()
        mock_camera.get_binning.return_value = (4, 4)
        mock_camera.get_pixel_format.return_value = CameraPixelFormat.MONO12

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "camera_settings.yaml"

            save_camera_settings(mock_camera, cache_path)
            settings = load_camera_settings(cache_path)

            assert settings is not None
            assert settings.binning == (4, 4)
            assert settings.pixel_format == "MONO12"

    def test_round_trip_no_pixel_format(self):
        """Test round-trip with None pixel format."""
        mock_camera = Mock()
        mock_camera.get_binning.return_value = (2, 2)
        mock_camera.get_pixel_format.return_value = None

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "camera_settings.yaml"

            save_camera_settings(mock_camera, cache_path)
            settings = load_camera_settings(cache_path)

            assert settings is not None
            assert settings.binning == (2, 2)
            assert settings.pixel_format is None
