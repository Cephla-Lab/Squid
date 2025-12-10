"""Tests for EventBus utility."""

from dataclasses import dataclass
from squid.events import Event, EventBus


@dataclass
class TestEvent(Event):
    """Test event for unit tests."""

    message: str


@dataclass
class OtherEvent(Event):
    """Another test event."""

    value: int


class TestEventBus:
    """Test suite for EventBus."""

    def test_subscribe_and_publish(self):
        """Subscribers should receive published events."""
        bus = EventBus()
        received = []

        def handler(event):
            received.append(event)

        bus.subscribe(TestEvent, handler)
        bus.publish(TestEvent(message="hello"))

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

        bus.unsubscribe(TestEvent, handler)
        bus.publish(TestEvent(message="second"))

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

        # Good handler should still receive event
        assert len(received) == 1

    def test_clear(self):
        """clear() should remove all subscriptions."""
        bus = EventBus()
        received = []

        bus.subscribe(TestEvent, lambda e: received.append(e))
        bus.clear()
        bus.publish(TestEvent(message="test"))

        assert len(received) == 0


def test_trigger_events_are_dataclasses():
    """Trigger events should be proper dataclasses."""
    from dataclasses import fields

    from squid.events import (
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
    from squid.events import MicroscopeModeChanged, SetMicroscopeModeCommand

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
        from squid.events import SetFilterPositionCommand

        cmd = SetFilterPositionCommand(position=3, wheel_index=0)
        assert cmd.position == 3
        assert cmd.wheel_index == 0

    def test_set_objective_command(self):
        from squid.events import SetObjectiveCommand

        cmd = SetObjectiveCommand(position=1)
        assert cmd.position == 1

    def test_set_spinning_disk_position_command(self):
        from squid.events import SetSpinningDiskPositionCommand

        cmd = SetSpinningDiskPositionCommand(in_beam=True)
        assert cmd.in_beam is True

    def test_set_spinning_disk_spinning_command(self):
        from squid.events import SetSpinningDiskSpinningCommand

        cmd = SetSpinningDiskSpinningCommand(spinning=True)
        assert cmd.spinning is True

    def test_set_disk_dichroic_command(self):
        from squid.events import SetDiskDichroicCommand

        cmd = SetDiskDichroicCommand(position=2)
        assert cmd.position == 2

    def test_set_disk_emission_filter_command(self):
        from squid.events import SetDiskEmissionFilterCommand

        cmd = SetDiskEmissionFilterCommand(position=3)
        assert cmd.position == 3

    def test_set_piezo_position_command(self):
        from squid.events import SetPiezoPositionCommand

        cmd = SetPiezoPositionCommand(position_um=50.0)
        assert cmd.position_um == 50.0

    def test_move_piezo_relative_command(self):
        from squid.events import MovePiezoRelativeCommand

        cmd = MovePiezoRelativeCommand(delta_um=10.5)
        assert cmd.delta_um == 10.5

    def test_filter_position_changed(self):
        from squid.events import FilterPositionChanged

        event = FilterPositionChanged(position=2, wheel_index=1)
        assert event.position == 2
        assert event.wheel_index == 1

    def test_objective_changed(self):
        from squid.events import ObjectiveChanged

        event = ObjectiveChanged(position=0, objective_name="20x", magnification=20.0)
        assert event.position == 0
        assert event.objective_name == "20x"
        assert event.magnification == 20.0

    def test_pixel_size_changed(self):
        from squid.events import PixelSizeChanged

        event = PixelSizeChanged(pixel_size_um=0.325)
        assert event.pixel_size_um == 0.325

    def test_spinning_disk_state_changed(self):
        from squid.events import SpinningDiskStateChanged

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
        from squid.events import PiezoPositionChanged

        event = PiezoPositionChanged(position_um=75.5)
        assert event.position_um == 75.5


class TestNewAcquisitionEvents:
    """Test new acquisition events."""

    def test_start_acquisition_command(self):
        from squid.events import StartAcquisitionCommand

        cmd = StartAcquisitionCommand(experiment_id="exp_001")
        assert cmd.experiment_id == "exp_001"

    def test_start_acquisition_command_default(self):
        from squid.events import StartAcquisitionCommand

        cmd = StartAcquisitionCommand()
        assert cmd.experiment_id is None

    def test_stop_acquisition_command(self):
        from squid.events import StopAcquisitionCommand

        cmd = StopAcquisitionCommand()
        assert cmd is not None

    def test_pause_acquisition_command(self):
        from squid.events import PauseAcquisitionCommand

        cmd = PauseAcquisitionCommand()
        assert cmd is not None

    def test_resume_acquisition_command(self):
        from squid.events import ResumeAcquisitionCommand

        cmd = ResumeAcquisitionCommand()
        assert cmd is not None

    def test_acquisition_progress(self):
        from squid.events import AcquisitionProgress

        event = AcquisitionProgress(
            current_fov=5,
            total_fovs=100,
            current_round=1,
            total_rounds=3,
            current_channel="DAPI",
            progress_percent=5.0,
            eta_seconds=3600.0,
        )
        assert event.current_fov == 5
        assert event.total_fovs == 100
        assert event.current_round == 1
        assert event.total_rounds == 3
        assert event.current_channel == "DAPI"
        assert event.progress_percent == 5.0
        assert event.eta_seconds == 3600.0

    def test_acquisition_paused(self):
        from squid.events import AcquisitionPaused

        event = AcquisitionPaused()
        assert event is not None

    def test_acquisition_resumed(self):
        from squid.events import AcquisitionResumed

        event = AcquisitionResumed()
        assert event is not None


class TestNewAutofocusEvents:
    """Test new autofocus events."""

    def test_start_autofocus_command(self):
        from squid.events import StartAutofocusCommand

        cmd = StartAutofocusCommand()
        assert cmd is not None

    def test_stop_autofocus_command(self):
        from squid.events import StopAutofocusCommand

        cmd = StopAutofocusCommand()
        assert cmd is not None

    def test_set_autofocus_params_command(self):
        from squid.events import SetAutofocusParamsCommand

        cmd = SetAutofocusParamsCommand(n_planes=10, delta_z_um=0.5, focus_metric="brenner")
        assert cmd.n_planes == 10
        assert cmd.delta_z_um == 0.5
        assert cmd.focus_metric == "brenner"

    def test_set_autofocus_params_command_defaults(self):
        from squid.events import SetAutofocusParamsCommand

        cmd = SetAutofocusParamsCommand()
        assert cmd.n_planes is None
        assert cmd.delta_z_um is None
        assert cmd.focus_metric is None

    def test_autofocus_progress(self):
        from squid.events import AutofocusProgress

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
        from squid.events import AutofocusCompleted

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
        from squid.events import AutofocusCompleted

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
        from squid.events import FocusChanged

        event = FocusChanged(z_mm=1.5, source="autofocus")
        assert event.z_mm == 1.5
        assert event.source == "autofocus"
