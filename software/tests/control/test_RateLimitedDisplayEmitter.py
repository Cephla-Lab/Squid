"""Tests for RateLimitedDisplayEmitter class."""

import time
from unittest.mock import MagicMock, patch
import numpy as np
import pytest
from pytestqt.qtbot import QtBot

from control.gui_hcs import RateLimitedDisplayEmitter


class MockCaptureInfo:
    """Mock CaptureInfo for testing."""

    def __init__(self, channel_name="BF LED matrix full"):
        self.configuration = MagicMock()
        self.configuration.name = channel_name
        self.configuration.illumination_source = 0
        self.position = MagicMock()
        self.position.x_mm = 10.0
        self.position.y_mm = 20.0
        self.position.z_mm = 1.0
        self.z_index = 0
        self.z_piezo_um = None
        self.region_id = 0


class TestRateLimitedDisplayEmitter:
    """Test cases for RateLimitedDisplayEmitter."""

    def test_init_default_interval(self, qtbot: QtBot):
        """Test default emit interval is 200ms."""
        emitter = RateLimitedDisplayEmitter()
        assert emitter._emit_interval_ms == 200

    def test_init_custom_interval(self, qtbot: QtBot):
        """Test custom emit interval."""
        emitter = RateLimitedDisplayEmitter(emit_interval_ms=100)
        assert emitter._emit_interval_ms == 100

    def test_set_emit_interval(self, qtbot: QtBot):
        """Test changing emit interval."""
        emitter = RateLimitedDisplayEmitter(emit_interval_ms=200)
        emitter.set_emit_interval(500)
        assert emitter._emit_interval_ms == 500

    def test_add_callback(self, qtbot: QtBot):
        """Test adding emit callbacks."""
        emitter = RateLimitedDisplayEmitter()
        callback = MagicMock()
        emitter.add_emit_callback(callback)
        assert len(emitter._emit_callbacks) == 1

    def test_clear_callbacks(self, qtbot: QtBot):
        """Test clearing emit callbacks."""
        emitter = RateLimitedDisplayEmitter()
        emitter.add_emit_callback(MagicMock())
        emitter.add_emit_callback(MagicMock())
        emitter.clear_callbacks()
        assert len(emitter._emit_callbacks) == 0

    def test_queue_frame_immediate_when_interval_zero(self, qtbot: QtBot):
        """Test that frames are emitted immediately when interval is 0."""
        emitter = RateLimitedDisplayEmitter(emit_interval_ms=0)
        callback = MagicMock()
        emitter.add_emit_callback(callback)

        frame = np.zeros((100, 100), dtype=np.uint16)
        info = MockCaptureInfo()

        emitter.queue_frame(frame, info)

        # Should be called immediately
        callback.assert_called_once()
        np.testing.assert_array_equal(callback.call_args[0][0], frame)

    def test_queue_frame_buffered_when_interval_positive(self, qtbot: QtBot):
        """Test that frames are buffered when interval > 0."""
        emitter = RateLimitedDisplayEmitter(emit_interval_ms=100)
        callback = MagicMock()
        emitter.add_emit_callback(callback)

        frame = np.zeros((100, 100), dtype=np.uint16)
        info = MockCaptureInfo()

        emitter.queue_frame(frame, info)

        # Should NOT be called immediately
        assert callback.call_count == 0
        # Frame should be buffered
        assert len(emitter._latest_frames) == 1
        # Timer should be started
        assert emitter._timer.isActive()

    def test_only_latest_frame_per_channel_kept(self, qtbot: QtBot):
        """Test that only the latest frame per channel is kept."""
        emitter = RateLimitedDisplayEmitter(emit_interval_ms=1000)
        callback = MagicMock()
        emitter.add_emit_callback(callback)

        # Queue multiple frames for same channel
        for i in range(5):
            frame = np.full((100, 100), i, dtype=np.uint16)
            info = MockCaptureInfo(channel_name="BF")
            emitter.queue_frame(frame, info)

        # Should only have 1 frame buffered (the latest)
        assert len(emitter._latest_frames) == 1
        # The buffered frame should be the last one (value=4)
        buffered_frame = emitter._latest_frames["BF"][0]
        assert buffered_frame[0, 0] == 4

    def test_multiple_channels_buffered_separately(self, qtbot: QtBot):
        """Test that different channels are buffered separately."""
        emitter = RateLimitedDisplayEmitter(emit_interval_ms=1000)
        callback = MagicMock()
        emitter.add_emit_callback(callback)

        # Queue frames for different channels
        for channel in ["BF", "Fluorescence 488", "Fluorescence 561"]:
            frame = np.zeros((100, 100), dtype=np.uint16)
            info = MockCaptureInfo(channel_name=channel)
            emitter.queue_frame(frame, info)

        # Should have 3 channels buffered
        assert len(emitter._latest_frames) == 3
        assert "BF" in emitter._latest_frames
        assert "Fluorescence 488" in emitter._latest_frames
        assert "Fluorescence 561" in emitter._latest_frames

    def test_emit_buffered_clears_buffer(self, qtbot: QtBot):
        """Test that _emit_buffered clears the buffer after emitting."""
        emitter = RateLimitedDisplayEmitter(emit_interval_ms=100)
        callback = MagicMock()
        emitter.add_emit_callback(callback)

        frame = np.zeros((100, 100), dtype=np.uint16)
        info = MockCaptureInfo()
        emitter.queue_frame(frame, info)

        # Manually trigger emit
        emitter._emit_buffered()

        # Buffer should be cleared
        assert len(emitter._latest_frames) == 0
        # Callback should have been called
        callback.assert_called_once()

    def test_stop_clears_buffer_and_stops_timer(self, qtbot: QtBot):
        """Test that stop() clears buffer and stops timer."""
        emitter = RateLimitedDisplayEmitter(emit_interval_ms=100)
        callback = MagicMock()
        emitter.add_emit_callback(callback)

        frame = np.zeros((100, 100), dtype=np.uint16)
        info = MockCaptureInfo()
        emitter.queue_frame(frame, info)

        emitter.stop()

        assert len(emitter._latest_frames) == 0
        assert not emitter._timer.isActive()
        # Callback should NOT have been called (stop doesn't emit)
        callback.assert_not_called()

    def test_flush_emits_and_stops(self, qtbot: QtBot):
        """Test that flush() emits buffered frames then stops."""
        emitter = RateLimitedDisplayEmitter(emit_interval_ms=1000)
        callback = MagicMock()
        emitter.add_emit_callback(callback)

        frame = np.zeros((100, 100), dtype=np.uint16)
        info = MockCaptureInfo()
        emitter.queue_frame(frame, info)

        emitter.flush()

        # Callback should have been called
        callback.assert_called_once()
        # Buffer should be cleared
        assert len(emitter._latest_frames) == 0
        # Timer should be stopped
        assert not emitter._timer.isActive()

    def test_disabled_emitter_does_nothing(self, qtbot: QtBot):
        """Test that disabled emitter doesn't queue or emit."""
        emitter = RateLimitedDisplayEmitter(emit_interval_ms=100)
        callback = MagicMock()
        emitter.add_emit_callback(callback)

        emitter.set_enabled(False)

        frame = np.zeros((100, 100), dtype=np.uint16)
        info = MockCaptureInfo()
        emitter.queue_frame(frame, info)

        # Nothing should happen
        assert len(emitter._latest_frames) == 0
        assert not emitter._timer.isActive()
        callback.assert_not_called()

    def test_timer_emits_at_interval(self, qtbot: QtBot):
        """Test that timer fires and emits at the specified interval."""
        emitter = RateLimitedDisplayEmitter(emit_interval_ms=50)
        callback = MagicMock()
        emitter.add_emit_callback(callback)

        frame = np.zeros((100, 100), dtype=np.uint16)
        info = MockCaptureInfo()
        emitter.queue_frame(frame, info)

        # Wait for timer to fire
        qtbot.wait(100)

        # Callback should have been called
        assert callback.call_count >= 1

    def test_frame_copy_prevents_reference_issues(self, qtbot: QtBot):
        """Test that frames are copied to prevent reference issues."""
        emitter = RateLimitedDisplayEmitter(emit_interval_ms=1000)
        callback = MagicMock()
        emitter.add_emit_callback(callback)

        # Create frame and queue it
        frame = np.zeros((100, 100), dtype=np.uint16)
        info = MockCaptureInfo()
        emitter.queue_frame(frame, info)

        # Modify original frame
        frame[0, 0] = 999

        # Buffered frame should still be 0 (was copied)
        buffered_frame = emitter._latest_frames["BF LED matrix full"][0]
        assert buffered_frame[0, 0] == 0
