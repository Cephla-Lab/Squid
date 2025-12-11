from squid.events import (
    EventBus,
    StagePositionChanged,
    StageMovementStopped,
    PiezoPositionChanged,
)
from squid.services.movement_service import MovementService
from squid.abc import Pos, StageStage


class FakeStage:
    def __init__(self, pos: Pos, busy: bool = False):
        self._pos = pos
        self._busy = busy

    def get_pos(self):
        return self._pos

    def get_state(self):
        return StageStage(busy=self._busy)


class FakePiezo:
    def __init__(self, position: float):
        self.position = position


def test_initial_poll_emits_position():
    bus = EventBus()
    captured = []
    bus.subscribe(StagePositionChanged, captured.append)

    stage = FakeStage(Pos(x_mm=1.0, y_mm=2.0, z_mm=3.0, theta_rad=None))
    service = MovementService(stage, None, bus, poll_interval_ms=10)

    service._poll_once()

    assert len(captured) == 1
    assert captured[0].x_mm == 1.0
    assert captured[0].y_mm == 2.0
    assert captured[0].z_mm == 3.0


def test_piezo_initial_poll_emits_position():
    bus = EventBus()
    captured = []
    bus.subscribe(PiezoPositionChanged, captured.append)

    stage = FakeStage(Pos(x_mm=0.0, y_mm=0.0, z_mm=0.0, theta_rad=None))
    piezo = FakePiezo(position=5.0)
    service = MovementService(stage, piezo, bus, poll_interval_ms=10)

    service._poll_once()

    assert captured and captured[0].position_um == 5.0


def test_movement_stopped_emits_once():
    bus = EventBus()
    positions = []
    stopped = []
    bus.subscribe(StagePositionChanged, positions.append)
    bus.subscribe(StageMovementStopped, stopped.append)

    stage = FakeStage(Pos(x_mm=0.0, y_mm=0.0, z_mm=0.0, theta_rad=None), busy=False)
    service = MovementService(stage, None, bus, poll_interval_ms=10, movement_threshold_mm=0.01)

    # Seed initial position
    service._poll_once()
    positions.clear()

    # Movement detected
    stage._pos = Pos(x_mm=0.5, y_mm=0.0, z_mm=0.0, theta_rad=None)
    service._poll_once()

    # Movement stops
    service._poll_once()

    # Additional steady-state poll should not emit another stopped event
    service._poll_once()

    assert len(stopped) == 1
    assert stopped[0].x_mm == 0.5
    assert len(positions) >= 3  # emitted for move + subsequent polls
