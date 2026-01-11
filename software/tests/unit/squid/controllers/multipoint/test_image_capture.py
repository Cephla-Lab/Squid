"""Unit tests for CaptureContext and build_capture_info."""

from __future__ import annotations

from dataclasses import dataclass
import time

import pytest

from squid.backend.controllers.multipoint.image_capture import (
    CaptureContext,
    build_capture_info,
)


@dataclass
class FakePos:
    """Fake position for testing."""

    x_mm: float = 1.0
    y_mm: float = 2.0
    z_mm: float = 0.05


@dataclass
class FakeChannelMode:
    """Fake channel mode for testing."""

    name: str = "BF"
    exposure_time: float = 10.0
    analog_gain: float = 1.0
    illumination_source: int = 0
    illumination_intensity: float = 50.0


class TestCaptureContext:
    """Tests for CaptureContext dataclass."""

    def test_create_context(self):
        """Test creating a CaptureContext."""
        config = FakeChannelMode()
        context = CaptureContext(
            config=config,
            file_id="region_0_0_0_0_BF",
            save_directory="/path/to/data",
            z_index=0,
            region_id="region_0",
            fov=1,
            config_idx=0,
            time_point=0,
            z_piezo_um=50.0,
            pixel_size_um=0.325,
        )

        assert context.config == config
        assert context.file_id == "region_0_0_0_0_BF"
        assert context.save_directory == "/path/to/data"
        assert context.z_index == 0
        assert context.region_id == "region_0"
        assert context.fov == 1
        assert context.config_idx == 0
        assert context.time_point == 0
        assert context.z_piezo_um == 50.0
        assert context.pixel_size_um == 0.325

    def test_context_defaults(self):
        """Test CaptureContext default values."""
        context = CaptureContext(
            config=FakeChannelMode(),
            file_id="test",
            save_directory="/path",
            z_index=0,
            region_id="region_0",
            fov=0,
            config_idx=0,
            time_point=0,
        )

        assert context.z_piezo_um is None
        assert context.pixel_size_um is None


class TestBuildCaptureInfo:
    """Tests for build_capture_info function."""

    def test_builds_capture_info(self):
        """Test that build_capture_info creates CaptureInfo correctly."""
        config = FakeChannelMode()
        context = CaptureContext(
            config=config,
            file_id="test_file",
            save_directory="/path/to/data",
            z_index=2,
            region_id="region_0",
            fov=5,
            config_idx=1,
            time_point=3,
            z_piezo_um=25.0,
            pixel_size_um=0.5,
        )
        pos = FakePos(x_mm=1.0, y_mm=2.0, z_mm=0.05)
        capture_time = 12345.0

        info = build_capture_info(context, pos, capture_time)

        assert info.position == pos
        assert info.z_index == 2
        assert info.capture_time == 12345.0
        assert info.z_piezo_um == 25.0
        assert info.configuration == config
        assert info.save_directory == "/path/to/data"
        assert info.file_id == "test_file"
        assert info.region_id == "region_0"
        assert info.fov == 5
        assert info.configuration_idx == 1
        assert info.time_point == 3
        assert info.pixel_size_um == 0.5

    def test_uses_current_time_if_not_provided(self):
        """Test that capture_time defaults to current time."""
        context = CaptureContext(
            config=FakeChannelMode(),
            file_id="test",
            save_directory="/path",
            z_index=0,
            region_id="region_0",
            fov=0,
            config_idx=0,
            time_point=0,
        )
        pos = FakePos()

        before = time.time()
        info = build_capture_info(context, pos)
        after = time.time()

        assert before <= info.capture_time <= after
