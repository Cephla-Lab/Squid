"""The Laser Engine tab's 560 settings in Squid's cache folder. Every test uses a temporary directory, never cache/."""

from pathlib import Path

import pytest
import yaml

import control.laser_engine_v2_settings as settings
from control.laser_engine_v2 import EngineOptions, options_from_cache
from control.laser_engine_v2_settings import (
    LaserEngineV2Settings,
    load_settings,
    save_idle_off_560_min,
    save_power_560_mw,
)


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """The default cache path, pointed at a temporary directory that does not exist yet."""
    path = tmp_path / "cache" / "laser_engine_v2.yaml"
    monkeypatch.setattr(settings, "DEFAULT_CACHE_PATH", path)
    return path


def test_the_default_file_is_in_squids_cache_folder():
    assert settings.DEFAULT_CACHE_PATH == Path("cache/laser_engine_v2.yaml")


def test_defaults_when_there_is_no_file(cache):
    assert load_settings() == LaserEngineV2Settings(power_560_mw=None, idle_off_560_min=30.0)
    assert options_from_cache() == EngineOptions()  # the source's minimum, 30 min idle-off
    assert not cache.exists()  # loading writes nothing


def test_round_trip_creates_the_folder_and_keeps_the_other_key(cache):
    assert save_power_560_mw(600) is True
    assert save_idle_off_560_min(0) is True  # 0 = 24 h
    assert yaml.safe_load(cache.read_text()) == {"power_560_mw": 600.0, "idle_off_560_min": 0.0}
    assert load_settings() == LaserEngineV2Settings(power_560_mw=600.0, idle_off_560_min=0.0)
    assert options_from_cache() == EngineOptions(source_idle_off_min=0, source_power_mw=600.0)
    assert save_power_560_mw(612.5) is True  # overwritten, not duplicated
    assert load_settings() == LaserEngineV2Settings(power_560_mw=612.5, idle_off_560_min=0.0)


def test_an_explicit_path_is_used(tmp_path, cache):
    other = tmp_path / "elsewhere.yaml"
    assert save_idle_off_560_min(45, cache_path=other) is True
    assert load_settings(other).idle_off_560_min == 45.0 and not cache.exists()


@pytest.mark.parametrize(
    "text",
    [
        "power_560_mw: [unclosed\n",  # not YAML
        "- 600\n- 30\n",  # not a mapping
        "",  # empty
    ],
)
def test_defaults_when_the_file_is_unreadable(cache, text):
    cache.parent.mkdir(parents=True)
    cache.write_text(text)
    assert load_settings() == LaserEngineV2Settings()


def test_a_bad_value_falls_back_to_its_default_only(cache):
    cache.parent.mkdir(parents=True)
    cache.write_text("power_560_mw: lots\nidle_off_560_min: 45\n")
    assert load_settings() == LaserEngineV2Settings(power_560_mw=None, idle_off_560_min=45.0)
    cache.write_text("power_560_mw: 700\nidle_off_560_min: -5\n")
    assert load_settings() == LaserEngineV2Settings(power_560_mw=700.0, idle_off_560_min=30.0)
    cache.write_text("power_560_mw: true\nidle_off_560_min: .nan\n")
    assert load_settings() == LaserEngineV2Settings()


def test_a_corrupt_file_is_replaced_by_the_next_save(cache):
    cache.parent.mkdir(parents=True)
    cache.write_text("power_560_mw: [unclosed\n")
    assert save_idle_off_560_min(10) is True
    assert load_settings() == LaserEngineV2Settings(power_560_mw=None, idle_off_560_min=10.0)


def test_save_failure_returns_false_and_never_raises(tmp_path):
    blocker = tmp_path / "cache"
    blocker.write_text("a file where the folder should be")
    path = blocker / "laser_engine_v2.yaml"
    assert save_power_560_mw(600, cache_path=path) is False
    assert save_idle_off_560_min(30, cache_path=path) is False
    assert save_power_560_mw(float("nan"), cache_path=tmp_path / "x.yaml") is False
    assert save_idle_off_560_min(-1, cache_path=tmp_path / "x.yaml") is False
    assert save_power_560_mw("lots", cache_path=tmp_path / "x.yaml") is False
    assert not (tmp_path / "x.yaml").exists()
