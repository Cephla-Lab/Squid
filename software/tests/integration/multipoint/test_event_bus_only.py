"""
EventBus-only integration tests for multipoint acquisition workflows.
"""

from __future__ import annotations

import uuid
from typing import Callable, Tuple

import pytest

import _def
from tests.harness import BackendContext, AcquisitionSimulator
from squid.core.events import (
    AcquisitionStateChanged,
    AcquisitionWorkerFinished,
    RequestScanCoordinatesSnapshotCommand,
    ScanCoordinatesSnapshot,
    SetAcquisitionChannelsCommand,
    SetAcquisitionParametersCommand,
    SetAcquisitionPathCommand,
    SetFluidicsRoundsCommand,
    StartAcquisitionCommand,
)


@pytest.fixture
def backend_ctx():
    """Provide a simulated backend context."""
    with BackendContext(simulation=True) as ctx:
        yield ctx


@pytest.fixture
def bus_only_sim(backend_ctx: BackendContext) -> AcquisitionSimulator:
    """Provide a bus-only acquisition simulator."""
    return AcquisitionSimulator(backend_ctx, bus_only=True)


@pytest.fixture
def center(backend_ctx: BackendContext) -> Tuple[float, float, float]:
    """Provide the stage center position."""
    return backend_ctx.get_stage_center()


@pytest.fixture
def channels(backend_ctx: BackendContext):
    """Provide available channel names."""
    return backend_ctx.get_available_channels()


def _request_scan_snapshot(sim: AcquisitionSimulator) -> ScanCoordinatesSnapshot:
    request_id = f"snapshot_{uuid.uuid4().hex[:8]}"
    sim.publish(RequestScanCoordinatesSnapshotCommand(request_id=request_id))
    snapshot = sim.wait_for(
        ScanCoordinatesSnapshot,
        timeout_s=2.0,
        predicate=lambda e: e.request_id == request_id,
    )
    assert snapshot is not None, "ScanCoordinatesSnapshot not received"
    return snapshot


def _count_fovs(snapshot: ScanCoordinatesSnapshot) -> int:
    return sum(len(coords) for coords in snapshot.region_fov_coordinates.values())


def _manual_half_width(sim: AcquisitionSimulator) -> float:
    fov_width_mm, _fov_height_mm = sim.ctx.scan_coordinates._get_current_fov_dimensions()
    return max(0.2, float(fov_width_mm) * 1.2)


def _manual_half_height(sim: AcquisitionSimulator) -> float:
    _fov_width_mm, fov_height_mm = sim.ctx.scan_coordinates._get_current_fov_dimensions()
    return max(0.2, float(fov_height_mm) * 1.2)


def test_set_acquisition_path_command_sets_base_path(bus_only_sim: AcquisitionSimulator, tmp_path):
    """Verify SetAcquisitionPathCommand updates controller base_path."""
    bus_only_sim.publish(SetAcquisitionPathCommand(base_path=str(tmp_path)))
    bus_only_sim.drain(timeout_s=0.5)

    controller = bus_only_sim.ctx.multipoint_controller
    assert controller.base_path == str(tmp_path)


def test_start_requires_base_path(backend_ctx: BackendContext, channels):
    """Verify StartAcquisitionCommand does not proceed without base_path."""
    sim = AcquisitionSimulator(backend_ctx, bus_only=True, auto_set_base_path=False)
    sim.monitor.clear()

    sim.set_channels(channels[:1])
    sim.publish(
        StartAcquisitionCommand(
            experiment_id="missing_base_path",
            acquire_current_fov=True,
            xy_mode="Current Position",
        )
    )
    sim.drain(timeout_s=0.5)

    running = sim.wait_for(
        AcquisitionStateChanged,
        timeout_s=1.0,
        predicate=lambda e: e.in_progress,
    )
    finished = sim.wait_for(AcquisitionWorkerFinished, timeout_s=1.0)

    controller = backend_ctx.multipoint_controller
    assert controller.base_path is None
    assert controller.experiment_ID is None
    assert running is None
    assert finished is None


def test_set_acquisition_parameters_command_applies_fields(bus_only_sim: AcquisitionSimulator):
    """Verify SetAcquisitionParametersCommand applies extended fields."""
    focus_map = {"z_offsets": [0.0, 0.5, 1.0]}
    # Note: gen_focus_map and use_manual_focus_map cannot both be True (validation rule)
    bus_only_sim.publish(
        SetAcquisitionParametersCommand(
            n_x=3,
            n_y=4,
            delta_x_mm=0.5,
            delta_y_mm=0.6,
            z_range=(1.0, 2.0),
            gen_focus_map=False,
            use_manual_focus_map=True,
            focus_map=focus_map,
            use_fluidics=True,
            z_stacking_config=2,
        )
    )
    bus_only_sim.drain(timeout_s=0.5)

    controller = bus_only_sim.ctx.multipoint_controller
    assert controller.NX == 3
    assert controller.NY == 4
    assert controller.deltaX == 0.5
    assert controller.deltaY == 0.6
    assert controller.z_range == (1.0, 2.0)
    assert controller.gen_focus_map is False
    assert controller.use_manual_focus_map is True
    assert controller.focus_map == focus_map
    assert controller.use_fluidics is True
    assert controller.z_stacking_config == _def.Z_STACKING_CONFIG_MAP[2]


def test_set_acquisition_channels_command_updates_controller(
    bus_only_sim: AcquisitionSimulator, channels
):
    """Verify SetAcquisitionChannelsCommand updates selected configurations."""
    expected = channels[:1]
    bus_only_sim.publish(SetAcquisitionChannelsCommand(channel_names=expected))
    bus_only_sim.drain(timeout_s=0.5)

    controller = bus_only_sim.ctx.multipoint_controller
    actual = [cfg.name for cfg in controller.selected_configurations]
    assert actual == expected


def test_set_fluidics_rounds_command_calls_service(bus_only_sim: AcquisitionSimulator):
    """Verify SetFluidicsRoundsCommand is wired to the fluidics service."""
    class _StubFluidicsService:
        def __init__(self):
            self.rounds = None

        def set_rounds(self, rounds):
            self.rounds = rounds

    stub = _StubFluidicsService()
    bus_only_sim.ctx.multipoint_controller._fluidics_service = stub

    bus_only_sim.publish(SetFluidicsRoundsCommand(rounds=[0, 2, 4]))
    bus_only_sim.drain(timeout_s=0.5)

    assert stub.rounds == [0, 2, 4]


@pytest.mark.parametrize(
    "xy_mode, setup_fn",
    [
        ("Current Position", lambda sim, center: sim.set_current_position_scan(
            x=center[0], y=center[1], scan_size_mm=0.5, overlap_pct=0.0
        )),
        ("Select Wells", lambda sim, _center: sim.select_wells(
            ["A1", "A2"], scan_size_mm=0.5, overlap_pct=0.0
        )),
        ("Load Coordinates", lambda sim, center: sim.load_coordinates(
            {"region_A": [(center[0], center[1], center[2])]}
        )),
        ("Manual", lambda sim, center: sim.set_manual_scan(
            ((
                (
                    center[0] - _manual_half_width(sim),
                    center[1] - _manual_half_height(sim),
                ),
                (
                    center[0] + _manual_half_width(sim),
                    center[1] - _manual_half_height(sim),
                ),
                (
                    center[0] + _manual_half_width(sim),
                    center[1] + _manual_half_height(sim),
                ),
                (
                    center[0] - _manual_half_width(sim),
                    center[1] + _manual_half_height(sim),
                ),
            ),),
            overlap_pct=0.0,
        )),
    ],
)
def test_bus_only_xy_modes(
    bus_only_sim: AcquisitionSimulator,
    center: Tuple[float, float, float],
    channels,
    xy_mode: str,
    setup_fn: Callable[[AcquisitionSimulator, Tuple[float, float, float]], None],
):
    """Verify EventBus-only workflows across xy_mode variants."""
    bus_only_sim.set_channels(channels[:1])
    setup_fn(bus_only_sim, center)
    bus_only_sim.drain(timeout_s=0.5)

    snapshot = _request_scan_snapshot(bus_only_sim)
    expected_fovs = _count_fovs(snapshot)
    assert expected_fovs > 0

    result = bus_only_sim.run_and_wait(xy_mode=xy_mode, timeout_s=120.0)

    assert result.success, f"Acquisition failed: {result.error}"
    assert result.total_fovs == expected_fovs
    assert result.total_images == expected_fovs
