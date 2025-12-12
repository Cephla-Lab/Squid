"""Tests for EventBus utility."""

import threading
import time
from dataclasses import dataclass

import pytest

from squid.core.events import Event, EventBus


@dataclass
class TestEvent(Event):
    """Test event for unit tests."""

    message: str


@dataclass
class OtherEvent(Event):
    """Another test event."""

    value: int


class TestEventBus:
    """Test suite for EventBus (synchronous drain mode for testing)."""

    def test_subscribe_and_publish(self):
        """Subscribers should receive published events."""
        bus = EventBus()
        received = []

        def handler(event):
            received.append(event)

        bus.subscribe(TestEvent, handler)
        bus.publish(TestEvent(message="hello"))
        bus.drain()  # Process queued events

        assert len(received) == 1
        assert received[0].message == "hello"

    def test_multiple_subscribers(self):
        """Multiple subscribers should all receive events."""
        bus = EventBus()
        received_a = []
        received_b = []

        bus.subscribe(TestEvent, lambda e: received_a.append(e))
        bus.subscribe(TestEvent, lambda e: received_b.append(e))

        bus.publish(TestEvent(message="test"))
        bus.drain()

        assert len(received_a) == 1
        assert len(received_b) == 1

    def test_different_event_types(self):
        """Subscribers only receive their event type."""
        bus = EventBus()
        test_events = []
        other_events = []

        bus.subscribe(TestEvent, lambda e: test_events.append(e))
        bus.subscribe(OtherEvent, lambda e: other_events.append(e))

        bus.publish(TestEvent(message="test"))
        bus.publish(OtherEvent(value=42))
        bus.drain()

        assert len(test_events) == 1
        assert len(other_events) == 1
        assert test_events[0].message == "test"
        assert other_events[0].value == 42

    def test_unsubscribe(self):
        """Unsubscribed handlers should not receive events."""
        bus = EventBus()
        received = []

        def handler(event):
            received.append(event)

        bus.subscribe(TestEvent, handler)
        bus.publish(TestEvent(message="first"))
        bus.drain()

        bus.unsubscribe(TestEvent, handler)
        bus.publish(TestEvent(message="second"))
        bus.drain()

        assert len(received) == 1
        assert received[0].message == "first"

    def test_handler_exception_doesnt_crash(self):
        """Exception in handler should not crash bus."""
        bus = EventBus()
        received = []

        def bad_handler(event):
            raise RuntimeError("handler error")

        def good_handler(event):
            received.append(event)

        bus.subscribe(TestEvent, bad_handler)
        bus.subscribe(TestEvent, good_handler)

        # Should not raise
        bus.publish(TestEvent(message="test"))
        bus.drain()

        # Good handler should still receive event
        assert len(received) == 1

    def test_clear(self):
        """clear() should remove all subscriptions."""
        bus = EventBus()
        received = []

        bus.subscribe(TestEvent, lambda e: received.append(e))
        bus.clear()
        bus.publish(TestEvent(message="test"))
        bus.drain()

        assert len(received) == 0


class TestEventBusQueued:
    """Tests for queued dispatch functionality."""

    def test_start_stop_lifecycle(self):
        """EventBus should start and stop cleanly."""
        bus = EventBus()
        assert not bus.is_running

        bus.start()
        assert bus.is_running

        bus.stop()
        assert not bus.is_running

    def test_start_is_idempotent(self):
        """Multiple start() calls should be safe."""
        bus = EventBus()
        bus.start()
        bus.start()  # Should not raise
        assert bus.is_running
        bus.stop()

    def test_stop_is_idempotent(self):
        """Multiple stop() calls should be safe."""
        bus = EventBus()
        bus.start()
        bus.stop()
        bus.stop()  # Should not raise
        assert not bus.is_running

    def test_queued_dispatch(self):
        """Events should be dispatched via background thread."""
        bus = EventBus()
        received = []
        dispatch_thread_name = []

        def handler(event):
            received.append(event)
            dispatch_thread_name.append(threading.current_thread().name)

        bus.subscribe(TestEvent, handler)
        bus.start()

        bus.publish(TestEvent(message="async"))

        # Wait for dispatch
        timeout = 1.0
        start_time = time.time()
        while len(received) == 0 and time.time() - start_time < timeout:
            time.sleep(0.01)

        bus.stop()

        assert len(received) == 1
        assert received[0].message == "async"
        assert dispatch_thread_name[0] == "EventBus-Dispatch"

    def test_queued_dispatch_ordering(self):
        """Events should be dispatched in order."""
        bus = EventBus()
        received = []

        def handler(event):
            received.append(event.message)

        bus.subscribe(TestEvent, handler)
        bus.start()

        for i in range(10):
            bus.publish(TestEvent(message=f"msg_{i}"))

        # Wait for all events
        timeout = 1.0
        start_time = time.time()
        while len(received) < 10 and time.time() - start_time < timeout:
            time.sleep(0.01)

        bus.stop()

        assert received == [f"msg_{i}" for i in range(10)]

    def test_publish_before_start_queues(self):
        """Events published before start() should be queued."""
        bus = EventBus()
        received = []

        def handler(event):
            received.append(event)

        bus.subscribe(TestEvent, handler)

        # Publish before start
        bus.publish(TestEvent(message="queued"))
        assert len(received) == 0  # Not dispatched yet

        bus.start()

        # Wait for dispatch
        timeout = 1.0
        start_time = time.time()
        while len(received) == 0 and time.time() - start_time < timeout:
            time.sleep(0.01)

        bus.stop()

        assert len(received) == 1
        assert received[0].message == "queued"

    def test_drain_processes_pending_events(self):
        """drain() should process all pending events synchronously."""
        bus = EventBus()
        received = []

        def handler(event):
            received.append(event)

        bus.subscribe(TestEvent, handler)

        # Publish without starting
        bus.publish(TestEvent(message="one"))
        bus.publish(TestEvent(message="two"))
        bus.publish(TestEvent(message="three"))

        # Drain synchronously
        count = bus.drain()

        assert count == 3
        assert len(received) == 3
        assert [e.message for e in received] == ["one", "two", "three"]


def test_trigger_events_are_dataclasses():
    """Trigger events should be proper dataclasses."""
    from dataclasses import fields

    from squid.core.events import (
        SetTriggerFPSCommand,
        SetTriggerModeCommand,
        TriggerFPSChanged,
        TriggerModeChanged,
    )

    # Commands have required fields
    assert "mode" in [f.name for f in fields(SetTriggerModeCommand)]
    assert "fps" in [f.name for f in fields(SetTriggerFPSCommand)]

    # State events have required fields
    assert "mode" in [f.name for f in fields(TriggerModeChanged)]
    assert "fps" in [f.name for f in fields(TriggerFPSChanged)]


def test_microscope_mode_events():
    """Microscope mode events should have required fields."""
    from squid.core.events import MicroscopeModeChanged, SetMicroscopeModeCommand

    cmd = SetMicroscopeModeCommand(configuration_name="GFP", objective="20x")
    assert cmd.configuration_name == "GFP"
    assert cmd.objective == "20x"

    evt = MicroscopeModeChanged(configuration_name="GFP")
    assert evt.configuration_name == "GFP"


# ============================================================================
# Tests for new Phase 2 events
# ============================================================================


class TestNewPeripheralEvents:
    """Test new peripheral events."""

    def test_set_filter_position_command(self):
        from squid.core.events import SetFilterPositionCommand

        cmd = SetFilterPositionCommand(position=3, wheel_index=0)
        assert cmd.position == 3
        assert cmd.wheel_index == 0

    def test_set_objective_command(self):
        from squid.core.events import SetObjectiveCommand

        cmd = SetObjectiveCommand(position=1)
        assert cmd.position == 1

    def test_set_spinning_disk_position_command(self):
        from squid.core.events import SetSpinningDiskPositionCommand

        cmd = SetSpinningDiskPositionCommand(in_beam=True)
        assert cmd.in_beam is True

    def test_set_spinning_disk_spinning_command(self):
        from squid.core.events import SetSpinningDiskSpinningCommand

        cmd = SetSpinningDiskSpinningCommand(spinning=True)
        assert cmd.spinning is True

    def test_set_disk_dichroic_command(self):
        from squid.core.events import SetDiskDichroicCommand

        cmd = SetDiskDichroicCommand(position=2)
        assert cmd.position == 2

    def test_set_disk_emission_filter_command(self):
        from squid.core.events import SetDiskEmissionFilterCommand

        cmd = SetDiskEmissionFilterCommand(position=3)
        assert cmd.position == 3

    def test_set_piezo_position_command(self):
        from squid.core.events import SetPiezoPositionCommand

        cmd = SetPiezoPositionCommand(position_um=50.0)
        assert cmd.position_um == 50.0

    def test_move_piezo_relative_command(self):
        from squid.core.events import MovePiezoRelativeCommand

        cmd = MovePiezoRelativeCommand(delta_um=10.5)
        assert cmd.delta_um == 10.5

    def test_filter_position_changed(self):
        from squid.core.events import FilterPositionChanged

        event = FilterPositionChanged(position=2, wheel_index=1)
        assert event.position == 2
        assert event.wheel_index == 1

    def test_objective_changed(self):
        from squid.core.events import ObjectiveChanged

        event = ObjectiveChanged(position=0, objective_name="20x", magnification=20.0)
        assert event.position == 0
        assert event.objective_name == "20x"
        assert event.magnification == 20.0

    def test_pixel_size_changed(self):
        from squid.core.events import PixelSizeChanged

        event = PixelSizeChanged(pixel_size_um=0.325)
        assert event.pixel_size_um == 0.325

    def test_spinning_disk_state_changed(self):
        from squid.core.events import SpinningDiskStateChanged

        event = SpinningDiskStateChanged(
            is_disk_in=True,
            is_spinning=True,
            motor_speed=5000,
            dichroic=0,
            emission_filter=1,
        )
        assert event.is_disk_in is True
        assert event.is_spinning is True
        assert event.motor_speed == 5000
        assert event.dichroic == 0
        assert event.emission_filter == 1

    def test_piezo_position_changed(self):
        from squid.core.events import PiezoPositionChanged

        event = PiezoPositionChanged(position_um=75.5)
        assert event.position_um == 75.5


class TestNewAcquisitionEvents:
    """Test new acquisition events."""

    def test_start_acquisition_command(self):
        from squid.core.events import StartAcquisitionCommand

        cmd = StartAcquisitionCommand(experiment_id="exp_001")
        assert cmd.experiment_id == "exp_001"

    def test_start_acquisition_command_default(self):
        from squid.core.events import StartAcquisitionCommand

        cmd = StartAcquisitionCommand()
        assert cmd.experiment_id is None

    def test_stop_acquisition_command(self):
        from squid.core.events import StopAcquisitionCommand

        cmd = StopAcquisitionCommand()
        assert cmd is not None

    def test_pause_acquisition_command(self):
        from squid.core.events import PauseAcquisitionCommand

        cmd = PauseAcquisitionCommand()
        assert cmd is not None

    def test_resume_acquisition_command(self):
        from squid.core.events import ResumeAcquisitionCommand

        cmd = ResumeAcquisitionCommand()
        assert cmd is not None

    def test_acquisition_progress(self):
        from squid.core.events import AcquisitionProgress

        event = AcquisitionProgress(
            current_fov=5,
            total_fovs=100,
            current_round=1,
            total_rounds=3,
            current_channel="DAPI",
            progress_percent=5.0,
            eta_seconds=3600.0,
            experiment_id="exp_default",
        )
        assert event.current_fov == 5
        assert event.total_fovs == 100
        assert event.current_round == 1
        assert event.total_rounds == 3
        assert event.current_channel == "DAPI"
        assert event.progress_percent == 5.0
        assert event.eta_seconds == 3600.0
        assert event.experiment_id == "exp_default"

    def test_acquisition_progress_with_experiment_id(self):
        from squid.core.events import AcquisitionProgress

        event = AcquisitionProgress(
            current_fov=5,
            total_fovs=100,
            current_round=1,
            total_rounds=3,
            current_channel="DAPI",
            progress_percent=5.0,
            experiment_id="exp_123",
        )
        assert event.experiment_id == "exp_123"

    def test_acquisition_state_changed_with_experiment_id(self):
        from squid.core.events import AcquisitionStateChanged

        event = AcquisitionStateChanged(
            in_progress=True,
            experiment_id="exp_456",
            is_aborting=False,
        )
        assert event.in_progress is True
        assert event.experiment_id == "exp_456"
        assert event.is_aborting is False

    def test_acquisition_region_progress_with_experiment_id(self):
        from squid.core.events import AcquisitionRegionProgress

        event = AcquisitionRegionProgress(
            current_region=2,
            total_regions=10,
            experiment_id="exp_789",
        )
        assert event.current_region == 2
        assert event.total_regions == 10
        assert event.experiment_id == "exp_789"

    def test_acquisition_finished_with_experiment_id(self):
        from squid.core.events import AcquisitionFinished

        event = AcquisitionFinished(
            success=True,
            experiment_id="exp_done",
        )
        assert event.success is True
        assert event.experiment_id == "exp_done"
        assert event.error is None

    def test_acquisition_paused(self):
        from squid.core.events import AcquisitionPaused

        event = AcquisitionPaused()
        assert event is not None

    def test_acquisition_resumed(self):
        from squid.core.events import AcquisitionResumed

        event = AcquisitionResumed()
        assert event is not None


class TestNewAutofocusEvents:
    """Test new autofocus events."""

    def test_start_autofocus_command(self):
        from squid.core.events import StartAutofocusCommand

        cmd = StartAutofocusCommand()
        assert cmd is not None

    def test_stop_autofocus_command(self):
        from squid.core.events import StopAutofocusCommand

        cmd = StopAutofocusCommand()
        assert cmd is not None

    def test_set_autofocus_params_command(self):
        from squid.core.events import SetAutofocusParamsCommand

        cmd = SetAutofocusParamsCommand(n_planes=10, delta_z_um=0.5, focus_metric="brenner")
        assert cmd.n_planes == 10
        assert cmd.delta_z_um == 0.5
        assert cmd.focus_metric == "brenner"

    def test_set_autofocus_params_command_defaults(self):
        from squid.core.events import SetAutofocusParamsCommand

        cmd = SetAutofocusParamsCommand()
        assert cmd.n_planes is None
        assert cmd.delta_z_um is None
        assert cmd.focus_metric is None

    def test_autofocus_progress(self):
        from squid.core.events import AutofocusProgress

        event = AutofocusProgress(
            current_step=3,
            total_steps=10,
            current_z=1.5,
            best_z=1.2,
            best_score=0.95,
        )
        assert event.current_step == 3
        assert event.total_steps == 10
        assert event.current_z == 1.5
        assert event.best_z == 1.2
        assert event.best_score == 0.95

    def test_autofocus_completed_success(self):
        from squid.core.events import AutofocusCompleted

        event = AutofocusCompleted(
            success=True,
            z_position=1.25,
            score=0.98,
            error=None,
        )
        assert event.success is True
        assert event.z_position == 1.25
        assert event.score == 0.98
        assert event.error is None

    def test_autofocus_completed_failure(self):
        from squid.core.events import AutofocusCompleted

        event = AutofocusCompleted(
            success=False,
            z_position=None,
            score=None,
            error="Could not find focus",
        )
        assert event.success is False
        assert event.z_position is None
        assert event.error == "Could not find focus"

    def test_focus_changed(self):
        from squid.core.events import FocusChanged

        event = FocusChanged(z_mm=1.5, source="autofocus")
        assert event.z_mm == 1.5
        assert event.source == "autofocus"


# ============================================================================
# Tests for Phase 3 Coordinator Events
# ============================================================================


class TestCoordinatorEvents:
    """Test resource coordinator events."""

    def test_global_mode_changed(self):
        from squid.core.events import GlobalModeChanged

        event = GlobalModeChanged(old_mode="IDLE", new_mode="LIVE")
        assert event.old_mode == "IDLE"
        assert event.new_mode == "LIVE"

    def test_lease_acquired(self):
        from squid.core.events import LeaseAcquired

        event = LeaseAcquired(
            lease_id="lease-123",
            owner="LiveController",
            resources=["CAMERA_CONTROL", "ILLUMINATION_CONTROL"],
        )
        assert event.lease_id == "lease-123"
        assert event.owner == "LiveController"
        assert len(event.resources) == 2
        assert "CAMERA_CONTROL" in event.resources
        assert "ILLUMINATION_CONTROL" in event.resources

    def test_lease_released(self):
        from squid.core.events import LeaseReleased

        event = LeaseReleased(lease_id="lease-456", owner="AcquisitionController")
        assert event.lease_id == "lease-456"
        assert event.owner == "AcquisitionController"

    def test_lease_revoked(self):
        from squid.core.events import LeaseRevoked

        event = LeaseRevoked(
            lease_id="lease-789",
            owner="StaleController",
            reason="expired",
        )
        assert event.lease_id == "lease-789"
        assert event.owner == "StaleController"
        assert event.reason == "expired"

    def test_acquisition_worker_finished_success(self):
        from squid.core.events import AcquisitionWorkerFinished

        event = AcquisitionWorkerFinished(
            experiment_id="exp-001",
            success=True,
            final_fov_count=100,
        )
        assert event.experiment_id == "exp-001"
        assert event.success is True
        assert event.error is None
        assert event.final_fov_count == 100

    def test_acquisition_worker_finished_error(self):
        from squid.core.events import AcquisitionWorkerFinished

        event = AcquisitionWorkerFinished(
            experiment_id="exp-002",
            success=False,
            error="Camera timeout",
            final_fov_count=42,
        )
        assert event.experiment_id == "exp-002"
        assert event.success is False
        assert event.error == "Camera timeout"
        assert event.final_fov_count == 42

    def test_acquisition_worker_progress(self):
        from squid.core.events import AcquisitionWorkerProgress

        event = AcquisitionWorkerProgress(
            experiment_id="exp-003",
            current_region=2,
            total_regions=5,
            current_fov=10,
            total_fovs=100,
            current_timepoint=1,
            total_timepoints=3,
        )
        assert event.experiment_id == "exp-003"
        assert event.current_region == 2
        assert event.total_regions == 5
        assert event.current_fov == 10
        assert event.total_fovs == 100
        assert event.current_timepoint == 1
        assert event.total_timepoints == 3


class TestUIStateEvents:
    """Test suite for UI state events."""

    def test_acquisition_ui_state_changed(self):
        """AcquisitionUIStateChanged should contain all UI-relevant fields."""
        from squid.core.events import AcquisitionUIStateChanged

        event = AcquisitionUIStateChanged(
            experiment_id="exp-ui-001",
            is_running=True,
            is_aborting=False,
            current_region=2,
            total_regions=5,
            current_fov=10,
            total_fovs=100,
            progress_percent=25.5,
        )
        assert event.experiment_id == "exp-ui-001"
        assert event.is_running is True
        assert event.is_aborting is False
        assert event.current_region == 2
        assert event.total_regions == 5
        assert event.current_fov == 10
        assert event.total_fovs == 100
        assert event.progress_percent == 25.5

    def test_acquisition_ui_state_changed_defaults(self):
        """AcquisitionUIStateChanged should have sensible defaults."""
        from squid.core.events import AcquisitionUIStateChanged

        event = AcquisitionUIStateChanged(
            experiment_id="exp-ui-002",
            is_running=False,
        )
        assert event.is_aborting is False
        assert event.current_region == 0
        assert event.total_regions == 0
        assert event.progress_percent == 0.0

    def test_live_ui_state_changed(self):
        """LiveUIStateChanged should contain all live view state."""
        from squid.core.events import LiveUIStateChanged

        event = LiveUIStateChanged(
            is_live=True,
            current_configuration="BF",
            exposure_time_ms=50.0,
            trigger_mode="Software",
        )
        assert event.is_live is True
        assert event.current_configuration == "BF"
        assert event.exposure_time_ms == 50.0
        assert event.trigger_mode == "Software"

    def test_live_ui_state_changed_defaults(self):
        """LiveUIStateChanged should allow None for optional fields."""
        from squid.core.events import LiveUIStateChanged

        event = LiveUIStateChanged(is_live=False)
        assert event.is_live is False
        assert event.current_configuration is None
        assert event.exposure_time_ms is None
        assert event.trigger_mode is None

    def test_navigation_viewer_state_changed(self):
        """NavigationViewerStateChanged should contain navigation state."""
        from squid.core.events import NavigationViewerStateChanged

        event = NavigationViewerStateChanged(
            x_mm=10.5,
            y_mm=20.3,
            fov_width_mm=0.5,
            fov_height_mm=0.4,
            wellplate_format="96",
        )
        assert event.x_mm == 10.5
        assert event.y_mm == 20.3
        assert event.fov_width_mm == 0.5
        assert event.fov_height_mm == 0.4
        assert event.wellplate_format == "96"

    def test_navigation_viewer_state_changed_no_wellplate(self):
        """NavigationViewerStateChanged should allow None wellplate."""
        from squid.core.events import NavigationViewerStateChanged

        event = NavigationViewerStateChanged(
            x_mm=0.0,
            y_mm=0.0,
            fov_width_mm=1.0,
            fov_height_mm=1.0,
        )
        assert event.wellplate_format is None

    def test_scan_coordinates_updated(self):
        """ScanCoordinatesUpdated should contain region info."""
        from squid.core.events import ScanCoordinatesUpdated

        event = ScanCoordinatesUpdated(
            total_regions=3,
            total_fovs=150,
            region_ids=("region_0", "region_1", "region_2"),
        )
        assert event.total_regions == 3
        assert event.total_fovs == 150
        assert event.region_ids == ("region_0", "region_1", "region_2")

    def test_scan_coordinates_updated_empty(self):
        """ScanCoordinatesUpdated should handle empty regions."""
        from squid.core.events import ScanCoordinatesUpdated

        event = ScanCoordinatesUpdated(
            total_regions=0,
            total_fovs=0,
            region_ids=(),
        )
        assert event.total_regions == 0
        assert event.total_fovs == 0
        assert event.region_ids == ()
