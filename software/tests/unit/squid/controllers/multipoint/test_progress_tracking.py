"""Unit tests for ProgressTracker and CoordinateTracker."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List
from unittest.mock import MagicMock, patch
import tempfile
import os

import pandas as pd
import pytest

from squid.backend.controllers.multipoint.progress_tracking import (
    ProgressTracker,
    ProgressState,
    CoordinateTracker,
)


@dataclass
class FakePos:
    """Fake position for testing."""

    x_mm: float = 1.0
    y_mm: float = 2.0
    z_mm: float = 0.05  # 50um


class FakeEventBus:
    """Fake EventBus that captures published events."""

    def __init__(self):
        self.published_events: List[Any] = []

    def publish(self, event: Any) -> None:
        self.published_events.append(event)


class TestProgressTracker:
    """Tests for ProgressTracker class."""

    def test_init(self):
        """Test ProgressTracker initialization."""
        event_bus = FakeEventBus()
        tracker = ProgressTracker(event_bus, "exp_123")

        assert tracker.experiment_id == "exp_123"
        assert tracker.start_time is None
        assert tracker.af_fov_count == 0

    def test_start_publishes_acquisition_started(self):
        """Test that start() publishes AcquisitionStarted event."""
        event_bus = FakeEventBus()
        tracker = ProgressTracker(event_bus, "exp_123")

        tracker.start()

        assert len(event_bus.published_events) == 1
        event = event_bus.published_events[0]
        assert event.experiment_id == "exp_123"
        assert tracker.start_time is not None

    def test_start_with_no_event_bus(self):
        """Test that start() works without event bus."""
        tracker = ProgressTracker(None, "exp_123")

        tracker.start()

        assert tracker.start_time is not None

    def test_finish_publishes_acquisition_worker_finished(self):
        """Test that finish() publishes AcquisitionWorkerFinished event."""
        event_bus = FakeEventBus()
        tracker = ProgressTracker(event_bus, "exp_123")
        tracker.af_fov_count = 42

        tracker.finish(success=True)

        assert len(event_bus.published_events) == 1
        event = event_bus.published_events[0]
        assert event.experiment_id == "exp_123"
        assert event.success is True
        assert event.error is None
        assert event.final_fov_count == 42

    def test_finish_with_error(self):
        """Test that finish() includes error message."""
        event_bus = FakeEventBus()
        tracker = ProgressTracker(event_bus, "exp_123")
        error = ValueError("Test error")

        tracker.finish(success=False, error=error)

        event = event_bus.published_events[0]
        assert event.success is False
        assert event.error == "Test error"

    def test_finish_with_no_event_bus(self):
        """Test that finish() works without event bus."""
        tracker = ProgressTracker(None, "exp_123")

        # Should not raise
        tracker.finish(success=True)

    def test_update_returns_progress_state(self):
        """Test that update() returns ProgressState with correct values."""
        tracker = ProgressTracker(None, "exp_123")

        state = tracker.update(
            current_fov=5,
            total_fovs=10,
            current_region=2,
            total_regions=4,
            current_timepoint=1,
            total_timepoints=5,
            current_channel="DAPI",
        )

        assert isinstance(state, ProgressState)
        assert state.current_fov == 5
        assert state.total_fovs == 10
        assert state.current_region == 2
        assert state.total_regions == 4
        assert state.current_timepoint == 1
        assert state.total_timepoints == 5
        assert state.current_channel == "DAPI"

    def test_update_calculates_progress_percent(self):
        """Test that update() calculates progress percentage correctly."""
        tracker = ProgressTracker(None, "exp_123")

        # Region 2 of 4, FOV 5 of 10
        # region_progress = (2-1)/4 = 0.25
        # fov_progress = 5/10 = 0.5
        # total = (0.25 + 0.5/4) * 100 = (0.25 + 0.125) * 100 = 37.5
        state = tracker.update(
            current_fov=5,
            total_fovs=10,
            current_region=2,
            total_regions=4,
            current_timepoint=1,
            total_timepoints=1,
        )

        assert state.progress_percent == pytest.approx(37.5)

    def test_update_handles_zero_totals(self):
        """Test that update() handles zero totals gracefully."""
        tracker = ProgressTracker(None, "exp_123")

        state = tracker.update(
            current_fov=0,
            total_fovs=0,
            current_region=0,
            total_regions=0,
            current_timepoint=1,
            total_timepoints=1,
        )

        assert state.progress_percent == 0.0

    def test_update_publishes_events(self):
        """Test that update() publishes progress events."""
        event_bus = FakeEventBus()
        tracker = ProgressTracker(event_bus, "exp_123")

        tracker.update(
            current_fov=1,
            total_fovs=10,
            current_region=1,
            total_regions=2,
            current_timepoint=1,
            total_timepoints=1,
        )

        # Should publish both AcquisitionProgress and AcquisitionWorkerProgress
        assert len(event_bus.published_events) == 2

    def test_update_calculates_eta_after_start(self):
        """Test that ETA is calculated after start() is called."""
        tracker = ProgressTracker(None, "exp_123")
        tracker.start()

        # Wait a tiny bit for time to pass
        import time
        time.sleep(0.01)

        state = tracker.update(
            current_fov=5,
            total_fovs=10,
            current_region=1,
            total_regions=1,
            current_timepoint=1,
            total_timepoints=1,
            current_channel="BF",
        )

        # At 50% progress, ETA should be approximately the elapsed time
        assert state.eta_seconds is not None
        assert state.eta_seconds >= 0

    def test_register_fov_publishes_event(self):
        """Test that register_fov() publishes CurrentFOVRegistered event."""
        event_bus = FakeEventBus()
        tracker = ProgressTracker(event_bus, "exp_123")

        tracker.register_fov(x_mm=1.0, y_mm=2.0, fov_width_mm=0.5, fov_height_mm=0.4)

        assert len(event_bus.published_events) == 1
        event = event_bus.published_events[0]
        assert event.x_mm == 1.0
        assert event.y_mm == 2.0
        assert event.fov_width_mm == 0.5
        assert event.fov_height_mm == 0.4

    def test_register_fov_with_no_event_bus(self):
        """Test that register_fov() works without event bus."""
        tracker = ProgressTracker(None, "exp_123")

        # Should not raise
        tracker.register_fov(x_mm=1.0, y_mm=2.0)

    def test_af_fov_count_property(self):
        """Test af_fov_count getter/setter."""
        tracker = ProgressTracker(None, "exp_123")

        tracker.af_fov_count = 10
        assert tracker.af_fov_count == 10

        tracker.af_fov_count = 20
        assert tracker.af_fov_count == 20


class TestCoordinateTracker:
    """Tests for CoordinateTracker class."""

    def test_init(self):
        """Test CoordinateTracker initialization."""
        tracker = CoordinateTracker(use_piezo=True)

        assert tracker.dataframe is None
        assert tracker._use_piezo is True

    def test_initialize_creates_dataframe(self):
        """Test that initialize() creates DataFrame with correct columns."""
        tracker = CoordinateTracker(use_piezo=False)
        tracker.initialize()

        df = tracker.dataframe
        assert df is not None
        assert list(df.columns) == ["region", "fov", "z_level", "x (mm)", "y (mm)", "z (um)", "time"]

    def test_initialize_includes_piezo_column_when_enabled(self):
        """Test that initialize() includes piezo column when enabled."""
        tracker = CoordinateTracker(use_piezo=True)
        tracker.initialize()

        df = tracker.dataframe
        assert "z_piezo (um)" in df.columns

    def test_record_adds_row(self):
        """Test that record() adds a row to the DataFrame."""
        tracker = CoordinateTracker(use_piezo=False)
        tracker.initialize()

        pos = FakePos(x_mm=1.0, y_mm=2.0, z_mm=0.05)
        tracker.record(region_id="region_0", z_level=0, pos=pos, fov=1)

        df = tracker.dataframe
        assert len(df) == 1
        assert df.iloc[0]["region"] == "region_0"
        assert df.iloc[0]["fov"] == 1
        assert df.iloc[0]["z_level"] == 0
        assert df.iloc[0]["x (mm)"] == 1.0
        assert df.iloc[0]["y (mm)"] == 2.0
        assert df.iloc[0]["z (um)"] == 50.0  # 0.05mm * 1000

    def test_record_includes_piezo_when_enabled(self):
        """Test that record() includes piezo position when enabled."""
        tracker = CoordinateTracker(use_piezo=True)
        tracker.initialize()

        pos = FakePos()
        tracker.record(region_id="region_0", z_level=0, pos=pos, fov=1, z_piezo_um=25.5)

        df = tracker.dataframe
        assert df.iloc[0]["z_piezo (um)"] == 25.5

    def test_record_without_initialize(self):
        """Test that record() auto-initializes if needed."""
        tracker = CoordinateTracker(use_piezo=False)

        pos = FakePos()
        tracker.record(region_id="region_0", z_level=0, pos=pos, fov=1)

        assert tracker.dataframe is not None
        assert len(tracker.dataframe) == 1

    def test_record_multiple_rows(self):
        """Test recording multiple positions."""
        tracker = CoordinateTracker(use_piezo=False)

        pos1 = FakePos(x_mm=1.0, y_mm=2.0, z_mm=0.05)
        pos2 = FakePos(x_mm=3.0, y_mm=4.0, z_mm=0.06)

        tracker.record(region_id="region_0", z_level=0, pos=pos1, fov=0)
        tracker.record(region_id="region_0", z_level=1, pos=pos2, fov=0)

        df = tracker.dataframe
        assert len(df) == 2
        assert df.iloc[0]["x (mm)"] == 1.0
        assert df.iloc[1]["x (mm)"] == 3.0

    def test_record_last_z_position(self):
        """Test recording last z positions."""
        tracker = CoordinateTracker()

        tracker.record_last_z_position("region_0", 1, z_mm=0.05)
        tracker.record_last_z_position("region_0", 2, z_mm=0.06)

        assert tracker.get_last_z_position("region_0", 1) == 0.05
        assert tracker.get_last_z_position("region_0", 2) == 0.06

    def test_get_last_z_position_returns_none_for_unrecorded(self):
        """Test that get_last_z_position returns None for unrecorded FOVs."""
        tracker = CoordinateTracker()

        assert tracker.get_last_z_position("region_0", 1) is None

    def test_has_last_z_position(self):
        """Test has_last_z_position check."""
        tracker = CoordinateTracker()

        assert tracker.has_last_z_position("region_0", 1) is False

        tracker.record_last_z_position("region_0", 1, z_mm=0.05)

        assert tracker.has_last_z_position("region_0", 1) is True

    def test_clear_last_z_positions(self):
        """Test clearing last z positions."""
        tracker = CoordinateTracker()

        tracker.record_last_z_position("region_0", 1, z_mm=0.05)
        tracker.record_last_z_position("region_0", 2, z_mm=0.06)

        tracker.clear_last_z_positions()

        assert tracker.get_last_z_position("region_0", 1) is None
        assert tracker.get_last_z_position("region_0", 2) is None

    def test_save_to_csv(self):
        """Test saving coordinates to CSV file."""
        tracker = CoordinateTracker(use_piezo=False)

        pos = FakePos(x_mm=1.0, y_mm=2.0, z_mm=0.05)
        tracker.record(region_id="region_0", z_level=0, pos=pos, fov=1)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "coordinates.csv")
            tracker.save(path)

            # Verify file was created and has correct content
            assert os.path.exists(path)
            loaded_df = pd.read_csv(path)
            assert len(loaded_df) == 1
            assert loaded_df.iloc[0]["region"] == "region_0"

    def test_save_to_directory(self):
        """Test saving coordinates to directory with default filename."""
        tracker = CoordinateTracker(use_piezo=False)

        pos = FakePos()
        tracker.record(region_id="region_0", z_level=0, pos=pos, fov=1)

        with tempfile.TemporaryDirectory() as tmpdir:
            tracker.save_to_directory(tmpdir)

            assert os.path.exists(os.path.join(tmpdir, "coordinates.csv"))

    def test_save_with_custom_filename(self):
        """Test saving with custom filename."""
        tracker = CoordinateTracker(use_piezo=False)

        pos = FakePos()
        tracker.record(region_id="region_0", z_level=0, pos=pos, fov=1)

        with tempfile.TemporaryDirectory() as tmpdir:
            tracker.save_to_directory(tmpdir, filename="my_coords.csv")

            assert os.path.exists(os.path.join(tmpdir, "my_coords.csv"))

    def test_save_without_data(self):
        """Test that save() handles empty data gracefully."""
        tracker = CoordinateTracker()

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "coordinates.csv")

            # Should not raise, just log warning
            tracker.save(path)

            # File should not be created
            assert not os.path.exists(path)


class TestProgressState:
    """Tests for ProgressState dataclass."""

    def test_progress_state_defaults(self):
        """Test ProgressState default values."""
        state = ProgressState(
            current_fov=1,
            total_fovs=10,
            current_region=1,
            total_regions=2,
            current_timepoint=1,
            total_timepoints=5,
        )

        assert state.current_channel == ""
        assert state.progress_percent == 0.0
        assert state.eta_seconds is None

    def test_progress_state_all_values(self):
        """Test ProgressState with all values set."""
        state = ProgressState(
            current_fov=5,
            total_fovs=10,
            current_region=2,
            total_regions=4,
            current_timepoint=3,
            total_timepoints=10,
            current_channel="DAPI",
            progress_percent=50.0,
            eta_seconds=120.5,
        )

        assert state.current_fov == 5
        assert state.total_fovs == 10
        assert state.current_region == 2
        assert state.total_regions == 4
        assert state.current_timepoint == 3
        assert state.total_timepoints == 10
        assert state.current_channel == "DAPI"
        assert state.progress_percent == 50.0
        assert state.eta_seconds == 120.5
