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


def _sim_with_serial(serial):
    import squid.config
    from squid.camera.utils import SimulatedCamera

    config = squid.config.get_camera_config().model_copy(update={"serial_number": serial})
    return SimulatedCamera(config, hw_trigger_fn=None, hw_set_strobe_delay_ms_fn=None)


def test_multi_camera_round_trip(tmp_path):
    from squid.camera.settings_cache import load_camera_settings, save_all_camera_settings

    cache = tmp_path / "camera_settings.yaml"
    cam1, cam2 = _sim_with_serial("SN1"), _sim_with_serial("SN2")
    cam1.set_binning(2, 2)
    cam2.set_binning(1, 1)
    save_all_camera_settings({1: cam1, 2: cam2}, cache_path=cache)

    s1 = load_camera_settings(serial="SN1", cache_path=cache)
    s2 = load_camera_settings(serial="SN2", cache_path=cache)
    assert s1.binning == (2, 2)
    assert s2.binning == (1, 1)
    assert load_camera_settings(serial="SN-UNKNOWN", cache_path=cache) is None


def test_legacy_flat_file_readable_for_any_serial(tmp_path):
    from squid.camera.settings_cache import load_camera_settings

    cache = tmp_path / "camera_settings.yaml"
    cache.write_text("binning: [3, 3]\npixel_format: MONO16\n")
    settings = load_camera_settings(serial="SN1", cache_path=cache)
    assert settings.binning == (3, 3)
    assert settings.pixel_format == "MONO16"
    # And with no serial (single-camera call sites)
    assert load_camera_settings(cache_path=cache).binning == (3, 3)


def test_none_serial_reads_default_key(tmp_path):
    from squid.camera.settings_cache import load_camera_settings, save_all_camera_settings

    cache = tmp_path / "camera_settings.yaml"
    cam = _sim_with_serial(None)  # INI-only camera without serial
    cam.set_binning(2, 2)
    save_all_camera_settings({1: cam}, cache_path=cache)
    assert load_camera_settings(cache_path=cache).binning == (2, 2)


def test_none_serial_falls_back_to_sole_v2_entry(tmp_path):
    """A serial-less camera adopts the only cached entry, even under another key."""
    from squid.camera.settings_cache import load_camera_settings

    cache = tmp_path / "camera_settings.yaml"
    with open(cache, "w") as f:
        yaml.safe_dump({"version": 2, "cameras": {"SN1": {"binning": [2, 2], "pixel_format": "MONO12"}}}, f)

    settings = load_camera_settings(cache_path=cache)

    assert settings is not None
    assert settings.binning == (2, 2)
    assert settings.pixel_format == "MONO12"


def test_save_all_isolates_a_camera_that_raises(tmp_path, monkeypatch):
    """One broken camera must not cost the healthy cameras their settings.

    save_all_camera_settings runs from closeEvent; anything escaping it aborts every
    later shutdown step (camera close, Z retract, microcontroller close).
    """
    from squid.camera.settings_cache import load_camera_settings, save_all_camera_settings

    def _raise():
        raise OSError("usb gone")

    cache = tmp_path / "camera_settings.yaml"
    healthy, broken = _sim_with_serial("SN-OK"), _sim_with_serial("SN-BAD")
    healthy.set_binning(2, 2)
    monkeypatch.setattr(broken, "get_binning", _raise)

    save_all_camera_settings({1: healthy, 2: broken}, cache_path=cache)

    with open(cache, "r") as f:
        data = yaml.safe_load(f)
    assert set(data["cameras"]) == {"SN-OK"}
    assert load_camera_settings(serial="SN-OK", cache_path=cache).binning == (2, 2)
    assert load_camera_settings(serial="SN-BAD", cache_path=cache) is None


def test_save_all_leaves_cache_untouched_when_no_camera_readable(tmp_path, monkeypatch):
    """Nothing readable means keep the last good cache rather than truncating it."""
    from squid.camera.settings_cache import save_all_camera_settings

    def _raise():
        raise RuntimeError("Camera disconnected")

    cache = tmp_path / "camera_settings.yaml"
    cache.write_text("binning: [3, 3]\npixel_format: MONO16\n")
    original = cache.read_text()

    broken = _sim_with_serial("SN-BAD")
    monkeypatch.setattr(broken, "get_binning", _raise)

    save_all_camera_settings({1: broken}, cache_path=cache)

    assert cache.read_text() == original


# ---------------------------------------------------------------------------
# White balance gains
# ---------------------------------------------------------------------------


def test_white_balance_gains_round_trip(tmp_path, monkeypatch):
    """A colour camera's gains survive a save/load cycle."""
    from squid.camera.settings_cache import load_camera_settings, save_all_camera_settings

    cache = tmp_path / "camera_settings.yaml"
    camera = _sim_with_serial("SN-COLOR")
    monkeypatch.setattr(camera, "get_white_balance_gains", lambda: (25, -10, 40))

    save_all_camera_settings({1: camera}, cache_path=cache)

    assert load_camera_settings(serial="SN-COLOR", cache_path=cache).white_balance_gains == (25.0, -10.0, 40.0)


def test_camera_without_white_balance_still_caches_other_settings(tmp_path, monkeypatch):
    """A mono camera raises on white balance; binning and pixel format must survive."""
    from squid.camera.settings_cache import load_camera_settings, save_all_camera_settings

    def _raise():
        raise RuntimeError("Not implemented")

    cache = tmp_path / "camera_settings.yaml"
    camera = _sim_with_serial("SN-MONO")
    camera.set_binning(2, 2)
    monkeypatch.setattr(camera, "get_white_balance_gains", _raise)

    save_all_camera_settings({1: camera}, cache_path=cache)

    settings = load_camera_settings(serial="SN-MONO", cache_path=cache)
    assert settings.binning == (2, 2)
    assert settings.white_balance_gains is None
    assert "white_balance_gains" not in yaml.safe_load(cache.read_text())["cameras"]["SN-MONO"]


def test_cache_without_white_balance_key_loads_as_none(tmp_path):
    """Entries written before white balance was cached must still load."""
    from squid.camera.settings_cache import load_camera_settings

    cache = tmp_path / "camera_settings.yaml"
    cache.write_text("version: 2\ncameras:\n  SN1: {binning: [2, 2], pixel_format: MONO16}\n")

    settings = load_camera_settings(serial="SN1", cache_path=cache)
    assert settings.binning == (2, 2)
    assert settings.white_balance_gains is None


def test_malformed_white_balance_gains_are_ignored(tmp_path):
    """Bad gains must not throw away the rest of the entry."""
    from squid.camera.settings_cache import load_camera_settings

    cache = tmp_path / "camera_settings.yaml"
    cache.write_text("version: 2\ncameras:\n  SN1: {binning: [2, 2], white_balance_gains: [1, 2]}\n")

    settings = load_camera_settings(serial="SN1", cache_path=cache)
    assert settings is not None
    assert settings.binning == (2, 2)
    assert settings.white_balance_gains is None


def test_cached_settings_rejects_wrong_length_gains():
    from squid.camera.settings_cache import CachedCameraSettings

    with pytest.raises(ValueError):
        CachedCameraSettings(binning=(1, 1), pixel_format=None, white_balance_gains=(1.0, 2.0))


def test_default_path_saves_never_touch_the_real_cache():
    """The autouse isolation fixture must keep default-path writes off the machine's file.

    Goes through the module attribute rather than an imported name, because that is how
    the application calls it (and how the fixture's redirect is reached). Without the
    fixture this test rewrites the developer's own cache/camera_settings.yaml.
    """
    import squid.camera.settings_cache as settings_cache

    real_path = settings_cache._DEFAULT_CACHE_PATH
    before = real_path.read_text() if real_path.exists() else None

    camera = _sim_with_serial("SN-LEAK-CHECK")
    settings_cache.save_all_camera_settings({1: camera})  # no cache_path -> the default

    after = real_path.read_text() if real_path.exists() else None
    assert after == before, "a default-path save reached the machine's real camera settings cache"

    # ...and the save still worked, just somewhere harmless.
    assert settings_cache.load_camera_settings(serial="SN-LEAK-CHECK") is not None
