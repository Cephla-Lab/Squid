import threading
import time
from pathlib import Path

import pytest


def _wait_until(predicate, *, timeout_s: float = 3.0, interval_s: float = 0.01) -> None:
    from squid.core.events import event_bus

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        event_bus.drain(timeout_s=0.05)
        if predicate():
            return
        time.sleep(interval_s)
    raise AssertionError("Timed out waiting for condition")


@pytest.fixture(autouse=True)
def _fast_multipoint(monkeypatch):
    import squid.backend.controllers.multipoint.multi_point_worker as mpw

    # Keep integration tests fast/deterministic by eliminating stabilization sleeps.
    monkeypatch.setattr(mpw.MultiPointWorker, "_sleep", lambda _self, _sec: None)
    for name in (
        "SCAN_STABILIZATION_TIME_MS_X",
        "SCAN_STABILIZATION_TIME_MS_Y",
        "SCAN_STABILIZATION_TIME_MS_Z",
    ):
        if hasattr(mpw, name):
            monkeypatch.setattr(mpw, name, 0)

    # Avoid spawning job runner processes in test runs.
    if hasattr(mpw, "Acquisition") and hasattr(mpw.Acquisition, "USE_MULTIPROCESSING"):
        monkeypatch.setattr(mpw.Acquisition, "USE_MULTIPROCESSING", False)


def _move_stage_into_cacheable_range(ctx) -> None:
    stage = ctx.microscope.stage
    config = stage.get_config()
    stage.move_x_to(config.X_AXIS.MIN_POSITION, blocking=True)
    stage.move_y_to(config.Y_AXIS.MIN_POSITION, blocking=True)
    stage.move_z_to(config.Z_AXIS.MIN_POSITION, blocking=True)


def _pick_channel_name(ctx) -> str:
    from squid.backend.managers.channel_configuration_manager import ChannelConfigurationManager
    import _def

    manager: ChannelConfigurationManager = ctx.controllers.channel_config_manager
    objective = getattr(ctx.controllers.objective_store, "current_objective", None) or "20x"

    configs = manager.get_configurations(objective)
    if not configs:
        manager.set_profile_path(Path(_def.PROFILE_PATH))
        manager.load_configurations(objective)
        configs = manager.get_configurations(objective)

    assert configs, "No channel configurations available"
    return configs[0].name


def test_rapid_start_stop_live_sequences(simulated_application_context):
    from squid.core.events import event_bus, StartLiveCommand, StopLiveCommand

    ctx = simulated_application_context
    _move_stage_into_cacheable_range(ctx)

    for _ in range(10):
        event_bus.publish(StartLiveCommand(configuration=None))
        event_bus.publish(StopLiveCommand())
        event_bus.drain(timeout_s=0.5)

    _wait_until(lambda: ctx.controllers.live.observable_state.is_live is False, timeout_s=2.0)
    assert ctx.mode_gate.get_mode().name == "IDLE"


def test_abort_during_acquisition_startup(simulated_application_context, tmp_path):
    from squid.core.events import (
        event_bus,
        SetAcquisitionPathCommand,
        StartNewExperimentCommand,
        SetAcquisitionChannelsCommand,
        SetAcquisitionParametersCommand,
        StartAcquisitionCommand,
        StopAcquisitionCommand,
        AcquisitionStateChanged,
        AcquisitionWorkerFinished,
    )

    ctx = simulated_application_context
    _move_stage_into_cacheable_range(ctx)
    channel = _pick_channel_name(ctx)

    states: list[AcquisitionStateChanged] = []
    finished: list[AcquisitionWorkerFinished] = []
    event_bus.subscribe(AcquisitionStateChanged, states.append)
    event_bus.subscribe(AcquisitionWorkerFinished, finished.append)

    event_bus.publish(SetAcquisitionPathCommand(base_path=str(tmp_path)))
    event_bus.publish(SetAcquisitionChannelsCommand(channel_names=[channel]))
    event_bus.publish(StartNewExperimentCommand(experiment_id="race_abort_startup"))
    event_bus.drain(timeout_s=0.5)
    exp_id = ctx.controllers.multipoint.experiment_ID
    assert exp_id is not None
    event_bus.publish(
        SetAcquisitionParametersCommand(
            n_z=1,
            n_t=1,
            use_autofocus=False,
            use_reflection_af=False,
            use_piezo=False,
            use_fluidics=False,
        )
    )

    # Queue an immediate abort right after start; stop should be processed after the start handler returns.
    event_bus.publish(StartAcquisitionCommand(experiment_id=None, acquire_current_fov=True))
    event_bus.publish(StopAcquisitionCommand())

    _wait_until(lambda: any(s.experiment_id == exp_id and s.in_progress for s in states), timeout_s=3.0)
    _wait_until(lambda: any(s.experiment_id == exp_id and (not s.in_progress) for s in states), timeout_s=6.0)

    event_bus.drain(timeout_s=0.5)
    assert any(evt.experiment_id == exp_id for evt in finished)
    assert ctx.controllers.multipoint.state.name == "IDLE"
    assert ctx.mode_gate.get_mode().name == "IDLE"


def test_back_to_back_acquisitions(simulated_application_context, tmp_path):
    from squid.core.events import (
        event_bus,
        SetAcquisitionPathCommand,
        StartNewExperimentCommand,
        SetAcquisitionChannelsCommand,
        SetAcquisitionParametersCommand,
        StartAcquisitionCommand,
        AcquisitionStateChanged,
        AcquisitionWorkerFinished,
    )

    ctx = simulated_application_context
    _move_stage_into_cacheable_range(ctx)
    channel = _pick_channel_name(ctx)

    states: list[AcquisitionStateChanged] = []
    finished: list[AcquisitionWorkerFinished] = []
    event_bus.subscribe(AcquisitionStateChanged, states.append)
    event_bus.subscribe(AcquisitionWorkerFinished, finished.append)

    event_bus.publish(SetAcquisitionPathCommand(base_path=str(tmp_path)))
    event_bus.publish(SetAcquisitionChannelsCommand(channel_names=[channel]))
    event_bus.publish(
        SetAcquisitionParametersCommand(
            n_z=1,
            n_t=1,
            use_autofocus=False,
            use_reflection_af=False,
            use_piezo=False,
            use_fluidics=False,
        )
    )
    event_bus.drain(timeout_s=0.5)

    for label in ("race_back_to_back_1", "race_back_to_back_2"):
        event_bus.publish(StartNewExperimentCommand(experiment_id=label))
        event_bus.drain(timeout_s=0.5)
        exp_id = ctx.controllers.multipoint.experiment_ID
        assert exp_id is not None

        event_bus.publish(StartAcquisitionCommand(experiment_id=None, acquire_current_fov=True))

        _wait_until(
            lambda: any(s.experiment_id == exp_id and (not s.in_progress) for s in states),
            timeout_s=10.0,
        )
        event_bus.drain(timeout_s=0.5)
        assert any(evt.experiment_id == exp_id for evt in finished)
        assert ctx.controllers.multipoint.state.name == "IDLE"
        assert ctx.mode_gate.get_mode().name == "IDLE"


def test_move_stage_command_blocked_during_acquisition(simulated_application_context, tmp_path, monkeypatch):
    from squid.core.events import (
        event_bus,
        MoveStageCommand,
        SetAcquisitionPathCommand,
        StartNewExperimentCommand,
        SetAcquisitionChannelsCommand,
        SetAcquisitionParametersCommand,
        StartAcquisitionCommand,
        AcquisitionStateChanged,
    )
    import squid.backend.controllers.multipoint.multi_point_worker as mpw

    ctx = simulated_application_context
    _move_stage_into_cacheable_range(ctx)
    channel = _pick_channel_name(ctx)

    stage = ctx.microscope.stage
    x_before = stage.get_pos().x_mm

    allow_finish = threading.Event()
    original_acquire_at_position = mpw.MultiPointWorker.acquire_at_position

    def _gated_acquire_at_position(self, region_id, current_path, fov):  # type: ignore[no-untyped-def]
        allow_finish.wait(timeout=2.0)
        return original_acquire_at_position(self, region_id, current_path, fov)

    monkeypatch.setattr(mpw.MultiPointWorker, "acquire_at_position", _gated_acquire_at_position)

    states: list[AcquisitionStateChanged] = []
    event_bus.subscribe(AcquisitionStateChanged, states.append)

    event_bus.publish(SetAcquisitionPathCommand(base_path=str(tmp_path)))
    event_bus.publish(SetAcquisitionChannelsCommand(channel_names=[channel]))
    event_bus.publish(StartNewExperimentCommand(experiment_id="race_stage_blocked"))
    event_bus.drain(timeout_s=0.5)
    exp_id = ctx.controllers.multipoint.experiment_ID
    assert exp_id is not None
    event_bus.publish(
        SetAcquisitionParametersCommand(
            n_z=1,
            n_t=1,
            use_autofocus=False,
            use_reflection_af=False,
            use_piezo=False,
            use_fluidics=False,
        )
    )

    # Ensure the stage-move command is processed while mode gate is ACQUIRING (FIFO vs worker-finished event).
    event_bus.publish(StartAcquisitionCommand(experiment_id=None, acquire_current_fov=True))
    _wait_until(lambda: ctx.mode_gate.get_mode().name == "ACQUIRING", timeout_s=3.0)

    event_bus.publish(MoveStageCommand(axis="x", distance_mm=5.0))
    event_bus.drain(timeout_s=0.5)
    assert stage.get_pos().x_mm == pytest.approx(x_before)

    allow_finish.set()

    _wait_until(lambda: any(s.experiment_id == exp_id and (not s.in_progress) for s in states), timeout_s=10.0)
    event_bus.drain(timeout_s=0.5)

    assert stage.get_pos().x_mm == pytest.approx(x_before)
    assert ctx.controllers.multipoint.state.name == "IDLE"
    assert ctx.mode_gate.get_mode().name == "IDLE"
