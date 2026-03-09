"""Unit tests for ExperimentRunner."""

import threading
from unittest.mock import MagicMock, patch

import pytest

from squid.core.protocol import (
    ExperimentProtocol,
    Round,
    ImagingStep,
    ImagingProtocol,
    FluidicsStep,
)
from squid.core.utils.cancel_token import CancelToken
from squid.backend.controllers.orchestrator.experiment_runner import ExperimentRunner
from squid.backend.controllers.orchestrator.state import ExperimentProgress, RoundProgress, StepOutcome


def _make_runner(
    *,
    protocol: ExperimentProtocol | None = None,
    imaging_executor=None,
    fluidics_controller=None,
    scan_coordinates=None,
    start_from_fov: int = 0,
    on_checkpoint=None,
):
    protocol = protocol or ExperimentProtocol(
        name="test_protocol",
        imaging_protocols={"standard": ImagingProtocol(channels=["DAPI"])},
        rounds=[Round(name="round_1", steps=[ImagingStep(protocol="standard")])],
    )
    progress = ExperimentProgress()
    progress.current_round_index = 0
    progress.current_step_index = 0
    return ExperimentRunner(
        protocol=protocol,
        experiment_path="/tmp",
        experiment_id="exp1",
        cancel_token=CancelToken(),
        event_bus=MagicMock(),
        progress=progress,
        progress_lock=threading.RLock(),
        imaging_executor=imaging_executor,
        fluidics_controller=fluidics_controller,
        scan_coordinates=scan_coordinates,
        experiment_manager=object(),
        experiment_context=object(),
        protocol_path=None,
        on_operation_change=lambda _op: None,
        on_progress=lambda: None,
        on_checkpoint=on_checkpoint or (lambda: None),
        on_round_started=lambda *_: None,
        on_round_completed=lambda *_: None,
        on_transition=lambda *_: None,
        on_pause=lambda: True,
        on_add_warning=lambda **_: False,
        intervention_acknowledged=threading.Event(),
        start_from_fov=start_from_fov,
        step_time_estimates={(0, 0): 10.0},
        total_estimated_seconds=10.0,
    )


def test_compute_eta_ignores_pause_time():
    runner = _make_runner()
    runner._step_start_time = 100.0

    with patch(
        "squid.backend.controllers.orchestrator.experiment_runner.time.monotonic"
    ) as mono:
        mono.return_value = 105.0
        eta_before = runner.compute_eta()
        assert eta_before == pytest.approx(5.0)

        mono.return_value = 106.0
        eta_at_pause = runner.compute_eta()
        runner.notify_pause()

        mono.return_value = 110.0
        eta_during_pause = runner.compute_eta()
        assert eta_during_pause == pytest.approx(eta_at_pause)

        mono.return_value = 112.0
        runner.notify_resume()

        mono.return_value = 113.0
        eta_after = runner.compute_eta()
        assert eta_after == pytest.approx(3.0)


def test_request_skip_to_round_rejects_invalid_targets():
    protocol = ExperimentProtocol(
        name="skip_test",
        imaging_protocols={"standard": ImagingProtocol(channels=["DAPI"])},
        rounds=[
            Round(name="r0", steps=[ImagingStep(protocol="standard")]),
            Round(name="r1", steps=[ImagingStep(protocol="standard")]),
        ],
    )
    runner = _make_runner(protocol=protocol)
    with runner._progress_lock:
        runner._progress.current_round = RoundProgress(round_index=0, round_name="r0")
        runner._progress.current_round_index = 0

    assert runner.request_skip_to_round(-1) is False
    assert runner.request_skip_to_round(0) is False
    assert runner.request_skip_to_round(2) is False
    assert runner.request_skip_to_round(1) is True


def test_execute_fluidics_step_without_controller_fails():
    protocol = ExperimentProtocol(
        name="fluidics_test",
        rounds=[Round(name="r0", steps=[FluidicsStep(protocol="wash")])],
    )
    runner = _make_runner(protocol=protocol, fluidics_controller=None)
    with runner._progress_lock:
        runner._progress.current_round = RoundProgress(round_index=0, round_name="r0")
        runner._progress.current_round_index = 0

    result = runner._execute_fluidics_step(0, FluidicsStep(protocol="wash"))
    assert result.outcome == StepOutcome.FAILED
    assert result.error_message == "No fluidics controller configured"


def test_execute_imaging_step_without_executor_fails():
    protocol = ExperimentProtocol(
        name="imaging_test",
        imaging_protocols={"standard": ImagingProtocol(channels=["DAPI"])},
        rounds=[Round(name="r0", steps=[ImagingStep(protocol="standard")])],
    )
    runner = _make_runner(protocol=protocol, imaging_executor=None)
    with runner._progress_lock:
        runner._progress.current_round = RoundProgress(round_index=0, round_name="r0")
        runner._progress.current_round_index = 0

    result = runner._execute_imaging_step(0, ImagingStep(protocol="standard"))
    assert result.outcome == StepOutcome.FAILED
    assert result.error_message == "No imaging executor configured"


def test_execute_imaging_step_rejects_resume_fov_out_of_bounds():
    protocol = ExperimentProtocol(
        name="imaging_test",
        imaging_protocols={"standard": ImagingProtocol(channels=["DAPI"])},
        rounds=[Round(name="r0", steps=[ImagingStep(protocol="standard")])],
    )
    imaging_executor = MagicMock()
    scan_coordinates = MagicMock()
    scan_coordinates.region_fov_coordinates = {"region_1": [(0.0, 0.0, 0.0)]}
    runner = _make_runner(
        protocol=protocol,
        imaging_executor=imaging_executor,
        scan_coordinates=scan_coordinates,
    )
    with runner._progress_lock:
        runner._progress.current_round = RoundProgress(round_index=0, round_name="r0")
        runner._progress.current_round_index = 0

    result = runner._execute_imaging_step(0, ImagingStep(protocol="standard"), resume_fov=3)
    assert result.outcome == StepOutcome.FAILED
    assert "start_from_fov out of bounds" in (result.error_message or "")
    imaging_executor.execute_with_config.assert_not_called()


def test_run_saves_checkpoint_after_successful_step():
    protocol = ExperimentProtocol(
        name="checkpoint_test",
        imaging_protocols={"standard": ImagingProtocol(channels=["DAPI"])},
        rounds=[Round(name="r0", steps=[ImagingStep(protocol="standard")])],
    )
    imaging_executor = MagicMock()
    imaging_executor.execute_with_config.return_value = True
    on_checkpoint = MagicMock()
    runner = _make_runner(
        protocol=protocol,
        imaging_executor=imaging_executor,
        on_checkpoint=on_checkpoint,
    )

    result = runner.run()
    assert result.outcome == StepOutcome.SUCCESS
    # At least one step-start checkpoint + one post-success checkpoint.
    assert on_checkpoint.call_count >= 2


def test_run_passes_start_from_fov_to_imaging_executor():
    protocol = ExperimentProtocol(
        name="resume_fov_test",
        imaging_protocols={"standard": ImagingProtocol(channels=["DAPI"])},
        rounds=[Round(name="r0", steps=[ImagingStep(protocol="standard")])],
    )
    imaging_executor = MagicMock()
    imaging_executor.execute_with_config.return_value = True
    runner = _make_runner(
        protocol=protocol,
        imaging_executor=imaging_executor,
        start_from_fov=2,
    )

    result = runner.run()
    assert result.outcome == StepOutcome.SUCCESS
    kwargs = imaging_executor.execute_with_config.call_args.kwargs
    assert kwargs["resume_fov_index"] == 2


def test_execute_imaging_step_surfaces_executor_error_message():
    protocol = ExperimentProtocol(
        name="imaging_test",
        imaging_protocols={"standard": ImagingProtocol(channels=["DAPI"])},
        rounds=[Round(name="r0", steps=[ImagingStep(protocol="standard")])],
    )
    imaging_executor = MagicMock()
    imaging_executor.execute_with_config.return_value = False
    imaging_executor.last_error = "Focus lock verification failed before FOV capture"
    runner = _make_runner(protocol=protocol, imaging_executor=imaging_executor)
    with runner._progress_lock:
        runner._progress.current_round = RoundProgress(round_index=0, round_name="r0")
        runner._progress.current_round_index = 0

    result = runner._execute_imaging_step(0, ImagingStep(protocol="standard"))

    assert result.outcome == StepOutcome.FAILED
    assert result.error_message == "Focus lock verification failed before FOV capture"
