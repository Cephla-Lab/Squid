"""Integration tests for StageService with simulated stage."""

import pytest

import _def as _def
from squid.core.events import (
    EventBus,
    MoveStageCommand,
    HomeStageCommand,
    StagePositionChanged,
)
from squid.mcs.services import StageService


@pytest.mark.integration
def test_move_command_updates_position_and_emits_event(simulated_stage):
    bus = EventBus()
    service = StageService(simulated_stage, bus)

    # Start at the stage's minimum X so a small relative move is valid.
    x_min = simulated_stage.get_config().X_AXIS.MIN_POSITION
    simulated_stage.set_position(x_mm=x_min)

    position_events = []
    bus.subscribe(StagePositionChanged, lambda e: position_events.append(e))

    bus.publish(MoveStageCommand(axis="x", distance_mm=1.0))

    expected = x_min + 1.0
    assert simulated_stage.get_pos().x_mm == pytest.approx(expected)
    assert position_events and position_events[-1].x_mm == pytest.approx(expected)


@pytest.mark.integration
def test_home_command_resets_axes(simulated_stage):
    bus = EventBus()
    service = StageService(simulated_stage, bus)

    simulated_stage.set_position(x_mm=5.0, y_mm=3.0, z_mm=2.0)

    position_events = []
    bus.subscribe(StagePositionChanged, lambda e: position_events.append(e))

    bus.publish(HomeStageCommand(x=True, y=True, z=True, theta=False))

    pos = simulated_stage.get_pos()
    assert pos.x_mm == pytest.approx(0.0)
    assert pos.y_mm == pytest.approx(0.0)
    assert pos.z_mm == pytest.approx(0.0)
    assert position_events and position_events[-1].z_mm == pytest.approx(0.0)


@pytest.mark.integration
def test_move_to_loading_and_scanning_positions(simulated_stage):
    bus = EventBus()
    service = StageService(simulated_stage, bus)

    # Move to loading position (wellplate defaults)
    service.move_to_loading_position(blocking=True, is_wellplate=True)
    pos_loading = simulated_stage.get_pos()
    assert pos_loading.x_mm == pytest.approx(_def.SLIDE_POSITION.LOADING_X_MM)
    assert pos_loading.y_mm == pytest.approx(_def.SLIDE_POSITION.LOADING_Y_MM)

    # Move back to scanning position
    service.move_to_scanning_position(blocking=True, is_wellplate=True)
    pos_scan = simulated_stage.get_pos()
    assert pos_scan.x_mm == pytest.approx(_def.SLIDE_POSITION.SCANNING_X_MM)
    assert pos_scan.y_mm == pytest.approx(_def.SLIDE_POSITION.SCANNING_Y_MM)
