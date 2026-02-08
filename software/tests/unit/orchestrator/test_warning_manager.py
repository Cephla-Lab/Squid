"""Unit tests for WarningManager."""

import threading
import time
from unittest.mock import MagicMock, call

import pytest

from squid.backend.controllers.orchestrator.warnings import (
    AcquisitionWarning,
    WarningCategory,
    WarningSeverity,
    WarningThresholds,
    DEFAULT_THRESHOLDS,
)
from squid.backend.controllers.orchestrator.warning_manager import (
    WarningManager,
    WarningStats,
)
from squid.backend.controllers.orchestrator.state import (
    WarningRaised,
    WarningThresholdReached,
    WarningsCleared,
)


class TestAcquisitionWarning:
    """Tests for AcquisitionWarning dataclass."""

    def test_create_warning(self):
        """Test creating a warning with factory method."""
        warning = AcquisitionWarning.create(
            category=WarningCategory.FOCUS,
            severity=WarningSeverity.MEDIUM,
            message="Autofocus failed",
            round_index=2,
            round_name="Round 3",
            fov_id="A1_0005",
        )

        assert warning.category == WarningCategory.FOCUS
        assert warning.severity == WarningSeverity.MEDIUM
        assert warning.message == "Autofocus failed"
        assert warning.round_index == 2
        assert warning.round_name == "Round 3"
        assert warning.fov_id == "A1_0005"
        assert warning.timestamp is not None

    def test_warning_with_context(self):
        """Test warning with context dict."""
        warning = AcquisitionWarning.create(
            category=WarningCategory.IMAGE_QUALITY,
            severity=WarningSeverity.LOW,
            message="Low signal",
            context={"intensity": 100, "threshold": 500},
        )

        context = warning.get_context()
        assert context["intensity"] == 100
        assert context["threshold"] == 500

    def test_warning_is_frozen(self):
        """Test that warning is immutable."""
        warning = AcquisitionWarning.create(
            category=WarningCategory.HARDWARE,
            severity=WarningSeverity.HIGH,
            message="Stage error",
        )

        with pytest.raises(AttributeError):
            warning.message = "Changed"


class TestWarningThresholds:
    """Tests for WarningThresholds configuration."""

    def test_default_thresholds(self):
        """Test default threshold values."""
        thresholds = DEFAULT_THRESHOLDS

        assert thresholds.pause_after_count is None
        assert WarningSeverity.CRITICAL in thresholds.pause_on_severity
        assert thresholds.max_stored_warnings == 1000

    def test_should_pause_on_critical(self):
        """Test that CRITICAL severity triggers pause."""
        thresholds = WarningThresholds(
            pause_on_severity=(WarningSeverity.CRITICAL,),
        )

        warning = AcquisitionWarning.create(
            category=WarningCategory.HARDWARE,
            severity=WarningSeverity.CRITICAL,
            message="Critical error",
        )

        assert thresholds.should_pause_on_warning(warning) is True

    def test_should_not_pause_on_low(self):
        """Test that LOW severity does not trigger pause by default."""
        thresholds = DEFAULT_THRESHOLDS

        warning = AcquisitionWarning.create(
            category=WarningCategory.FOCUS,
            severity=WarningSeverity.LOW,
            message="Minor issue",
        )

        assert thresholds.should_pause_on_warning(warning) is False

    def test_pause_on_category(self):
        """Test category-based pause trigger."""
        thresholds = WarningThresholds(
            pause_on_categories=(WarningCategory.HARDWARE,),
        )

        warning = AcquisitionWarning.create(
            category=WarningCategory.HARDWARE,
            severity=WarningSeverity.LOW,
            message="Hardware issue",
        )

        assert thresholds.should_pause_on_warning(warning) is True

    def test_category_threshold(self):
        """Test per-category threshold lookup."""
        thresholds = WarningThresholds(
            category_thresholds=(
                (WarningCategory.FOCUS, 5),
                (WarningCategory.IMAGE_QUALITY, 10),
            ),
        )

        assert thresholds.get_category_threshold(WarningCategory.FOCUS) == 5
        assert thresholds.get_category_threshold(WarningCategory.IMAGE_QUALITY) == 10
        assert thresholds.get_category_threshold(WarningCategory.HARDWARE) is None


class TestWarningManager:
    """Tests for WarningManager class."""

    def test_add_warning(self):
        """Test adding a warning."""
        manager = WarningManager(experiment_id="test_exp")

        should_pause = manager.add_warning(
            category=WarningCategory.FOCUS,
            severity=WarningSeverity.MEDIUM,
            message="Autofocus drift",
            fov_id="A1_0001",
        )

        assert should_pause is False
        assert len(manager) == 1

    def test_add_warning_publishes_event(self):
        """Test that adding a warning publishes WarningRaised event."""
        event_bus = MagicMock()
        manager = WarningManager(event_bus=event_bus, experiment_id="test_exp")

        manager.add_warning(
            category=WarningCategory.FOCUS,
            severity=WarningSeverity.MEDIUM,
            message="Test warning",
            round_index=1,
            round_name="Round 2",
            fov_id="A1_0005",
        )

        event_bus.publish.assert_called()
        call_args = event_bus.publish.call_args[0][0]
        assert isinstance(call_args, WarningRaised)
        assert call_args.experiment_id == "test_exp"
        assert call_args.category == "FOCUS"
        assert call_args.severity == "MEDIUM"
        assert call_args.total_warnings == 1

    def test_warning_threshold_pause(self):
        """Test that reaching threshold returns True."""
        thresholds = WarningThresholds(pause_after_count=3)
        manager = WarningManager(thresholds=thresholds, experiment_id="test_exp")

        # Add warnings below threshold
        assert manager.add_warning(
            WarningCategory.FOCUS, WarningSeverity.LOW, "Warning 1"
        ) is False
        assert manager.add_warning(
            WarningCategory.FOCUS, WarningSeverity.LOW, "Warning 2"
        ) is False

        # Third warning should trigger threshold
        assert manager.add_warning(
            WarningCategory.FOCUS, WarningSeverity.LOW, "Warning 3"
        ) is True

    def test_critical_warning_triggers_pause(self):
        """Test that CRITICAL severity triggers immediate pause."""
        manager = WarningManager(experiment_id="test_exp")

        should_pause = manager.add_warning(
            category=WarningCategory.HARDWARE,
            severity=WarningSeverity.CRITICAL,
            message="Critical hardware failure",
        )

        assert should_pause is True

    def test_threshold_publishes_event(self):
        """Test that reaching threshold publishes WarningThresholdReached."""
        event_bus = MagicMock()
        thresholds = WarningThresholds(pause_after_count=2)
        manager = WarningManager(
            event_bus=event_bus, thresholds=thresholds, experiment_id="test_exp"
        )

        manager.add_warning(WarningCategory.FOCUS, WarningSeverity.LOW, "Warning 1")
        manager.add_warning(WarningCategory.FOCUS, WarningSeverity.LOW, "Warning 2")

        # Check that WarningThresholdReached was published
        calls = event_bus.publish.call_args_list
        threshold_events = [
            c for c in calls if isinstance(c[0][0], WarningThresholdReached)
        ]
        assert len(threshold_events) == 1
        assert threshold_events[0][0][0].should_pause is True

    def test_filter_by_category(self):
        """Test filtering warnings by category."""
        manager = WarningManager(experiment_id="test_exp")

        manager.add_warning(WarningCategory.FOCUS, WarningSeverity.LOW, "Focus 1")
        manager.add_warning(WarningCategory.HARDWARE, WarningSeverity.LOW, "Hardware 1")
        manager.add_warning(WarningCategory.FOCUS, WarningSeverity.LOW, "Focus 2")

        focus_warnings = manager.get_warnings(category=WarningCategory.FOCUS)
        assert len(focus_warnings) == 2

        hardware_warnings = manager.get_warnings(category=WarningCategory.HARDWARE)
        assert len(hardware_warnings) == 1

    def test_filter_by_severity(self):
        """Test filtering warnings by severity."""
        manager = WarningManager(experiment_id="test_exp")

        manager.add_warning(WarningCategory.FOCUS, WarningSeverity.LOW, "Low 1")
        manager.add_warning(WarningCategory.FOCUS, WarningSeverity.HIGH, "High 1")
        manager.add_warning(WarningCategory.FOCUS, WarningSeverity.LOW, "Low 2")

        high_warnings = manager.get_warnings(severity=WarningSeverity.HIGH)
        assert len(high_warnings) == 1

    def test_filter_by_fov_id(self):
        """Test filtering warnings by FOV ID."""
        manager = WarningManager(experiment_id="test_exp")

        manager.add_warning(
            WarningCategory.FOCUS, WarningSeverity.LOW, "Warning 1", fov_id="A1_0001"
        )
        manager.add_warning(
            WarningCategory.FOCUS, WarningSeverity.LOW, "Warning 2", fov_id="A1_0002"
        )
        manager.add_warning(
            WarningCategory.FOCUS, WarningSeverity.LOW, "Warning 3", fov_id="A1_0001"
        )

        fov_warnings = manager.get_warnings(fov_id="A1_0001")
        assert len(fov_warnings) == 2

    def test_filter_by_round_index(self):
        """Test filtering warnings by round index."""
        manager = WarningManager(experiment_id="test_exp")

        manager.add_warning(
            WarningCategory.FOCUS, WarningSeverity.LOW, "Round 0", round_index=0
        )
        manager.add_warning(
            WarningCategory.FOCUS, WarningSeverity.LOW, "Round 1", round_index=1
        )
        manager.add_warning(
            WarningCategory.FOCUS, WarningSeverity.LOW, "Round 0 again", round_index=0
        )

        round_warnings = manager.get_warnings(round_index=0)
        assert len(round_warnings) == 2

    def test_clear_all(self):
        """Test clearing all warnings."""
        event_bus = MagicMock()
        manager = WarningManager(event_bus=event_bus, experiment_id="test_exp")

        manager.add_warning(WarningCategory.FOCUS, WarningSeverity.LOW, "Warning 1")
        manager.add_warning(WarningCategory.HARDWARE, WarningSeverity.LOW, "Warning 2")
        manager.add_warning(WarningCategory.FOCUS, WarningSeverity.LOW, "Warning 3")

        cleared = manager.clear()

        assert cleared == 3
        assert len(manager) == 0

        # Check WarningsCleared event was published
        calls = event_bus.publish.call_args_list
        cleared_events = [c for c in calls if isinstance(c[0][0], WarningsCleared)]
        assert len(cleared_events) == 1
        assert cleared_events[0][0][0].cleared_count == 3

    def test_clear_by_category(self):
        """Test clearing warnings by category."""
        manager = WarningManager(experiment_id="test_exp")

        manager.add_warning(WarningCategory.FOCUS, WarningSeverity.LOW, "Focus 1")
        manager.add_warning(WarningCategory.HARDWARE, WarningSeverity.LOW, "Hardware 1")
        manager.add_warning(WarningCategory.FOCUS, WarningSeverity.LOW, "Focus 2")

        cleared = manager.clear(categories=(WarningCategory.FOCUS,))

        assert cleared == 2
        assert len(manager) == 1
        assert manager.get_warnings()[0].category == WarningCategory.HARDWARE

    def test_get_stats(self):
        """Test getting warning statistics."""
        manager = WarningManager(experiment_id="test_exp")

        manager.add_warning(
            WarningCategory.FOCUS, WarningSeverity.LOW, "Warning 1", fov_id="A1_0001"
        )
        manager.add_warning(
            WarningCategory.FOCUS, WarningSeverity.HIGH, "Warning 2", fov_id="A1_0001"
        )
        manager.add_warning(
            WarningCategory.HARDWARE, WarningSeverity.LOW, "Warning 3", fov_id="A1_0002"
        )

        stats = manager.get_stats()

        assert stats.total_count == 3
        assert stats.by_category[WarningCategory.FOCUS] == 2
        assert stats.by_category[WarningCategory.HARDWARE] == 1
        assert stats.by_severity[WarningSeverity.LOW] == 2
        assert stats.by_severity[WarningSeverity.HIGH] == 1
        assert stats.by_fov["A1_0001"] == 2
        assert stats.by_fov["A1_0002"] == 1

    def test_max_stored_warnings(self):
        """Test that old warnings are removed when max is reached."""
        thresholds = WarningThresholds(max_stored_warnings=5)
        manager = WarningManager(thresholds=thresholds, experiment_id="test_exp")

        # Add 7 warnings
        for i in range(7):
            manager.add_warning(
                WarningCategory.FOCUS, WarningSeverity.LOW, f"Warning {i}"
            )

        # Only 5 should be stored
        assert len(manager) == 5
        assert manager.get_stats().total_count == 5

        # The first two should have been removed
        warnings = manager.get_warnings()
        assert "Warning 2" in warnings[0].message
        assert "Warning 6" in warnings[-1].message

    def test_thread_safety(self):
        """Test that WarningManager is thread-safe."""
        manager = WarningManager(experiment_id="test_exp")
        errors = []

        def add_warnings(thread_id: int):
            try:
                for i in range(100):
                    manager.add_warning(
                        WarningCategory.FOCUS,
                        WarningSeverity.LOW,
                        f"Thread {thread_id} warning {i}",
                    )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=add_warnings, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(manager) == 500

    def test_iteration(self):
        """Test iterating over warnings."""
        manager = WarningManager(experiment_id="test_exp")

        manager.add_warning(WarningCategory.FOCUS, WarningSeverity.LOW, "Warning 1")
        manager.add_warning(WarningCategory.FOCUS, WarningSeverity.LOW, "Warning 2")

        warnings = list(manager)
        assert len(warnings) == 2

    def test_set_thresholds(self):
        """Test updating thresholds."""
        manager = WarningManager(experiment_id="test_exp")

        # Initially, 3 warnings shouldn't trigger pause
        manager.add_warning(WarningCategory.FOCUS, WarningSeverity.LOW, "Warning 1")
        manager.add_warning(WarningCategory.FOCUS, WarningSeverity.LOW, "Warning 2")
        assert manager.add_warning(
            WarningCategory.FOCUS, WarningSeverity.LOW, "Warning 3"
        ) is False

        # Update thresholds
        manager.set_thresholds(WarningThresholds(pause_after_count=4))

        # 4th warning should now trigger pause
        assert manager.add_warning(
            WarningCategory.FOCUS, WarningSeverity.LOW, "Warning 4"
        ) is True

    def test_experiment_id_property(self):
        """Test experiment_id getter/setter."""
        manager = WarningManager(experiment_id="exp1")
        assert manager.experiment_id == "exp1"

        manager.experiment_id = "exp2"
        assert manager.experiment_id == "exp2"

    def test_category_threshold_pause(self):
        """Test that category-specific threshold triggers pause."""
        thresholds = WarningThresholds(
            category_thresholds=((WarningCategory.FOCUS, 3),),
        )
        manager = WarningManager(thresholds=thresholds, experiment_id="test_exp")

        # Add focus warnings
        assert manager.add_warning(
            WarningCategory.FOCUS, WarningSeverity.LOW, "Focus 1"
        ) is False
        assert manager.add_warning(
            WarningCategory.FOCUS, WarningSeverity.LOW, "Focus 2"
        ) is False
        assert manager.add_warning(
            WarningCategory.FOCUS, WarningSeverity.LOW, "Focus 3"
        ) is True  # Should trigger

        # Hardware warnings shouldn't trigger (no threshold set)
        manager.clear()
        for i in range(5):
            result = manager.add_warning(
                WarningCategory.HARDWARE, WarningSeverity.LOW, f"Hardware {i}"
            )
        assert result is False
