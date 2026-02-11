"""Integration tests for sequential acquisition runs via GUI-equivalent EventBus commands."""

from __future__ import annotations

import uuid
from typing import List, Tuple

import pytest

from squid.core.events import (
    AcquisitionWorkerFinished,
    AutofocusMode,
    SetAcquisitionChannelsCommand,
    SetAcquisitionParametersCommand,
    SetAcquisitionPathCommand,
    StartAcquisitionCommand,
    StartNewExperimentCommand,
)
from tests.harness import AcquisitionSimulator, BackendContext


@pytest.fixture
def backend_ctx():
    """Provide a simulated backend context."""
    with BackendContext(simulation=True) as ctx:
        yield ctx


@pytest.fixture
def sim(backend_ctx: BackendContext) -> AcquisitionSimulator:
    """Provide an acquisition simulator."""
    return AcquisitionSimulator(backend_ctx)


@pytest.fixture
def center(backend_ctx: BackendContext) -> Tuple[float, float, float]:
    """Provide the stage center position."""
    return backend_ctx.get_stage_center()


@pytest.fixture
def channels(backend_ctx: BackendContext) -> List[str]:
    """Provide available channel names."""
    return backend_ctx.get_available_channels()


def _run_quick_scan_and_wait(
    sim: AcquisitionSimulator,
    center: Tuple[float, float, float],
    channels: List[str],
    *,
    nx: int = 2,
    ny: int = 2,
    overlap: float = 10.0,
    timeout_s: float = 60.0,
) -> AcquisitionWorkerFinished:
    """Run one quick-scan style acquisition using GUI-equivalent command sequence."""
    assert channels, "At least one imaging channel is required"

    x_mm, y_mm, z_mm = center
    exp_id = f"quick_scan_{uuid.uuid4().hex[:8]}"

    sim.monitor.clear()
    sim.publish(
        SetAcquisitionParametersCommand(
            skip_saving=True,
            n_z=1,
            autofocus_mode=AutofocusMode.NONE,
            autofocus_interval_fovs=1,
            widget_type="setup",
        )
    )
    sim.publish(SetAcquisitionChannelsCommand(channel_names=[channels[0]]))
    sim.publish(SetAcquisitionPathCommand(base_path=sim.ctx.base_path))
    sim.publish(StartNewExperimentCommand(experiment_id=exp_id))
    sim.sleep(0.2)
    sim.publish(
        StartAcquisitionCommand(
            experiment_id=exp_id,
            xy_mode="Current Position",
            quick_scan_center=(x_mm, y_mm, z_mm),
            quick_scan_nx=nx,
            quick_scan_ny=ny,
            quick_scan_overlap=overlap,
        )
    )

    finish = sim.wait_for(
        AcquisitionWorkerFinished,
        timeout_s=timeout_s,
        predicate=lambda e: e.experiment_id.startswith(exp_id),
    )
    assert finish is not None, "Timed out waiting for quick scan to finish"
    return finish


@pytest.mark.integration
def test_loaded_coordinates_start_acquisition_can_run_twice_without_saving(
    sim: AcquisitionSimulator,
    center: Tuple[float, float, float],
    channels: List[str],
) -> None:
    """Load coordinates once, then run regular acquisition twice with skip_saving=True."""
    assert channels, "At least one channel must be available for acquisition"
    x_mm, y_mm, z_mm = center

    sim.load_coordinates(
        {
            "region_0": [
                (x_mm, y_mm, z_mm),
                (x_mm + 0.5, y_mm, z_mm),
            ]
        }
    )
    sim.set_channels([channels[0]])
    sim.set_skip_saving(True)

    first = sim.run_and_wait(
        experiment_id="sequential_start_1",
        xy_mode="Load Coordinates",
        timeout_s=90.0,
    )
    assert first.success, f"First acquisition failed: {first.error}"
    assert first.total_images == 2
    assert first.total_fovs == 2

    second = sim.run_and_wait(
        experiment_id="sequential_start_2",
        xy_mode="Load Coordinates",
        timeout_s=90.0,
    )
    assert second.success, f"Second acquisition failed: {second.error}"
    assert second.total_images == 2
    assert second.total_fovs == 2


@pytest.mark.integration
def test_quick_scan_then_regular_start_acquisition_without_saving(
    sim: AcquisitionSimulator,
    center: Tuple[float, float, float],
    channels: List[str],
) -> None:
    """Run quick-scan command path, then run regular loaded-coordinate acquisition."""
    quick_finish = _run_quick_scan_and_wait(sim, center, channels, timeout_s=90.0)
    assert quick_finish.success, f"Quick scan failed: {quick_finish.error}"

    x_mm, y_mm, z_mm = center
    sim.load_coordinates(
        {
            "region_1": [
                (x_mm, y_mm, z_mm),
                (x_mm, y_mm + 0.5, z_mm),
            ]
        }
    )
    sim.set_channels([channels[0]])
    sim.set_skip_saving(True)

    post_quick = sim.run_and_wait(
        experiment_id="start_after_quick_scan",
        xy_mode="Load Coordinates",
        timeout_s=90.0,
    )
    assert post_quick.success, f"Acquisition after quick scan failed: {post_quick.error}"
    assert post_quick.total_images == 2
    assert post_quick.total_fovs == 2
