import json
import os
from types import SimpleNamespace

import pytest

from control.core.fluidics_protocol import manifest as manifest_io
from control.core.fluidics_protocol.events import HoldAction, RunnerState
from control.core.fluidics_protocol.resolve import ProtocolProblems, resolve_protocol
from control.core.fluidics_protocol.runner import ProtocolRunner
from control.models.fluidics_protocol import ProtocolFile, ReleaseAndSettle, expand_rounds
from control.models.fluidics_run import TecState
from tests.control.core.fluidics_protocol.fakes import FakeFluidicsPort, FakeImagingPort, wait_until

SETTINGS = {"channels": ["A"], "z_stack": {"nz": 1}}
COORDS = {"regions": [{"name": "A1", "fovs": [[1.0, 2.0, 3.0]]}]}


def _protocol(rounds=1):
    p = ProtocolFile(
        name="demo",
        imaging={"settings": {"cur": SETTINGS}, "coordinates": {"cur": COORDS}},
        sequences=[
            {
                "type": "priming",
                "round": "setup",
                "name": "prime",
                "fluidic_port": 25,
                "flow_rate": 5000,
                "volume": 800,
            },
            {
                "type": "flow_reagent",
                "round": "R01",
                "name": "probe",
                "fluidic_port": 1,
                "flow_rate": 2000,
                "volume": 500,
            },
            {
                "type": "flow_reagent",
                "round": "R01",
                "name": "wash",
                "fluidic_port": 25,
                "flow_rate": 5000,
                "volume": 1000,
            },
            {
                "type": "imaging",
                "round": "R01",
                "name": "image",
                "folder": "image",
                "settings": "cur",
                "coordinates": "cur",
            },
            {
                "type": "flow_reagent",
                "round": "R01",
                "name": "cleave",
                "fluidic_port": 26,
                "flow_rate": 5000,
                "volume": 2000,
            },
        ],
    )
    if rounds > 1:
        p = expand_rounds(p, "R01", rounds - 1, port_row_name="probe", ports=list(range(2, rounds + 1)))
    return p


def _runner(tmp_path, fluidics=None, imaging=None, rounds=1, events=None):
    fluidics = fluidics or FakeFluidicsPort()
    imaging = imaging or FakeImagingPort()
    resolved = resolve_protocol(_protocol(rounds), tmp_path, fluidics=fluidics)
    run_dir = manifest_io.create_run_dir(tmp_path, "liver")
    listener = events.append if events is not None else None
    runner = ProtocolRunner(resolved, run_dir, imaging, fluidics, run_name="liver", listener=listener, poll_s=0.01)
    return runner, fluidics, imaging, run_dir


def _held(runner, attempt=None):
    def held():
        # Read the hold once, and before the state: after a hold is acted on the runner clears it while the
        # state is still HELD, until the manifest is written.
        hold = runner.hold
        return runner.state == RunnerState.HELD and hold is not None and attempt in (None, hold.attempt)

    return wait_until(held)


def test_happy_path_runs_every_step_and_leaves_the_documented_run_folder(tmp_path):
    events = []
    runner, fluidics, imaging, run_dir = _runner(tmp_path, events=events)

    runner.start()
    assert runner.wait(10)

    assert runner.state == RunnerState.ENDED and runner.outcome == "finished"
    assert (run_dir / "protocol.yaml").exists() and (run_dir / "run_manifest.json").exists()
    assert (run_dir / "run.log").exists()
    assert (run_dir / "R01_image" / "protocol_step.json").exists()
    man = manifest_io.read_manifest(run_dir)
    assert man.status == "finished" and man.protocol_sha256 == manifest_io.sha256_of_file(run_dir / "protocol.yaml")
    assert [s.kind for s in man.steps] == ["fluidics", "fluidics", "imaging", "fluidics"]
    assert [s.attempts[0].outcome for s in man.steps] == ["finished", "finished", "completed", "finished"]
    assert man.steps[0].attempts[0].reagent_used_ul == {"1": 500.0} and man.steps[2].attempts[0].images == 7
    assert len(fluidics.starts) == 3 and imaging.requests[0].protocol["round"] == "R01"
    assert imaging.requests[0].settings.channels == ["A"] and imaging.requests[0].coordinates.fov_count == 1
    kinds = [type(e).__name__ for e in events]
    assert kinds[0] == "StateChanged" and kinds[-1] == "RunFinished" and kinds.count("StepStarted") == 4


def test_progress_fraction_prices_elapsed_over_the_estimate(tmp_path):
    # the snapshot owns "how far through the run" so it's testable without the GUI: elapsed over
    # the rough total estimate, held just under full until the run ends, None when there's no estimate.
    runner, *_ = _runner(tmp_path)
    runner._resolved = SimpleNamespace(total_estimate_s=200.0)
    runner._state = RunnerState.RUNNING
    assert runner._progress_fraction(0.0) == 0.0
    assert runner._progress_fraction(100.0) == 0.5
    assert runner._progress_fraction(1000.0) == 0.99  # capped until the run actually ends
    runner._state = RunnerState.ENDED
    assert runner._progress_fraction(0.0) == 1.0
    runner._resolved = SimpleNamespace(total_estimate_s=None)  # an unpriced plan
    runner._state = RunnerState.RUNNING
    assert runner._progress_fraction(50.0) is None


def test_24_rounds_complete_in_seconds(tmp_path):
    runner, fluidics, imaging, run_dir = _runner(tmp_path, rounds=24)
    runner.start()
    assert runner.wait(20)
    assert runner.outcome == "finished"
    folders = sorted(p.name for p in run_dir.iterdir() if p.name.endswith("_image"))
    assert folders == [f"R{n:02d}_image" for n in range(1, 25)]
    assert fluidics.starts[3]["rows"][0]["fluidic_port"] == 2  # R02's probe port
    assert all(row["round"] == "R02" for row in fluidics.starts[3]["rows"])  # round passes through


def test_abort_step_during_fluidics_holds_and_resume_runs_the_plan_tail(tmp_path):
    fluidics = FakeFluidicsPort(script=[("finished",), ("hold",), ("finished",)])
    runner, fluidics, imaging, run_dir = _runner(tmp_path, fluidics=fluidics)
    runner.start()
    assert wait_until(lambda: len(fluidics.starts) == 2)

    assert wait_until(lambda: manifest_io.read_manifest(run_dir).cursor.sequence == 1)  # 2nd sequence in flight

    runner.abort_step()
    assert _held(runner)
    hold = runner.hold
    assert hold.kind == "fluidics" and hold.reason == "stopped" and hold.resume_position == 1 and hold.can_resume
    assert manifest_io.read_manifest(run_dir).status == "held"

    runner.hold_action(HoldAction.RESUME)
    assert runner.wait(10) and runner.outcome == "finished"
    assert len(fluidics.starts[2]["plan"]) == 1  # tail from position 1 of the 2-row step
    man = manifest_io.read_manifest(run_dir)
    assert [a.outcome for a in man.steps[1].attempts] == ["stopped", "finished"]


def test_fluidics_failure_offers_restart_skip_and_end(tmp_path):
    fluidics = FakeFluidicsPort(
        script=[("failed", 0, "Flow fault on syringe_draw"), ("failed", 0, "again"), ("finished",)]
    )
    runner, fluidics, imaging, run_dir = _runner(tmp_path, fluidics=fluidics)
    runner.start()
    assert _held(runner)
    assert runner.hold.reason == "failed" and "Flow fault" in runner.hold.message

    runner.hold_action(HoldAction.RESTART)
    assert _held(runner, attempt=2)
    assert len(fluidics.starts[1]["plan"]) == 1  # full plan again

    runner.hold_action(HoldAction.SKIP)
    assert runner.wait(10) and runner.outcome == "finished"
    man = manifest_io.read_manifest(run_dir)
    assert man.steps[0].skipped is True and len(man.steps[0].attempts) == 2
    assert len(fluidics.starts) == 4  # setup x2 (failed twice), R01, cleave


def test_end_run_from_hold_stops_the_run(tmp_path):
    fluidics = FakeFluidicsPort(script=[("failed", 0, "boom")])
    runner, fluidics, imaging, run_dir = _runner(tmp_path, fluidics=fluidics)
    runner.start()
    assert _held(runner)
    runner.hold_action(HoldAction.END)
    assert runner.wait(10)
    assert runner.outcome == "stopped" and manifest_io.read_manifest(run_dir).status == "stopped"
    assert imaging.requests == []


def test_imaging_abort_writes_the_marker_and_restart_uses_attempt2(tmp_path):
    imaging = FakeImagingPort(script=["hold", "completed"])
    runner, fluidics, imaging, run_dir = _runner(tmp_path, imaging=imaging)
    runner.start()
    assert wait_until(lambda: len(imaging.requests) == 1)

    runner.abort_step()
    assert _held(runner)
    assert runner.hold.kind == "imaging" and runner.hold.reason == "user_abort" and not runner.hold.can_resume
    assert json.loads((run_dir / "R01_image" / "aborted.json").read_text())["reason"] == "user_abort"
    assert fluidics.make_safe_calls == 1

    runner.hold_action(HoldAction.RESTART)
    assert runner.wait(10) and runner.outcome == "finished"
    assert imaging.requests[1].folder == "R01_image_attempt2" and (run_dir / "R01_image_attempt2").is_dir()
    man = manifest_io.read_manifest(run_dir)
    assert [a.folder for a in man.steps[2].attempts] == ["R01_image", "R01_image_attempt2"]
    assert [a.outcome for a in man.steps[2].attempts] == ["user_abort", "completed"]


def test_imaging_failed_to_start_holds(tmp_path):
    imaging = FakeImagingPort(script=["raise", "completed"])
    runner, fluidics, imaging, run_dir = _runner(tmp_path, imaging=imaging)
    runner.start()
    assert _held(runner)
    assert runner.hold.reason == "failed_to_start" and "Laser AF" in runner.hold.message
    runner.hold_action(HoldAction.RESTART)
    assert runner.wait(10) and runner.outcome == "finished"
    assert imaging.requests[1].folder == "R01_image_attempt2"  # every attempt gets its own folder name


def test_completed_with_errors_holds_and_accept_continues(tmp_path):
    imaging = FakeImagingPort(script=["completed_with_errors"])
    runner, fluidics, imaging, run_dir = _runner(tmp_path, imaging=imaging)
    runner.start()
    assert _held(runner)
    assert runner.hold.reason == "completed_with_errors" and runner.hold.can_accept
    runner.hold_action(HoldAction.ACCEPT)
    assert runner.wait(10) and runner.outcome == "finished"
    assert len(imaging.requests) == 1


def test_pause_during_fluidics_reaches_paused_and_resume_continues(tmp_path):
    fluidics = FakeFluidicsPort(script=[("hold",)])
    runner, fluidics, imaging, run_dir = _runner(tmp_path, fluidics=fluidics)
    runner.start()
    assert wait_until(lambda: len(fluidics.starts) == 1)

    runner.pause()
    assert wait_until(lambda: runner.state == RunnerState.PAUSED)
    assert manifest_io.read_manifest(run_dir).status == "paused"

    runner.resume()
    assert runner.wait(10) and runner.outcome == "finished"


def test_pause_during_imaging_parks_at_the_boundary(tmp_path):
    imaging = FakeImagingPort(script=["hold"])
    runner, fluidics, imaging, run_dir = _runner(tmp_path, imaging=imaging)
    runner.start()
    assert wait_until(lambda: len(imaging.requests) == 1)

    runner.pause()
    assert runner.state == RunnerState.RUNNING  # no engine pause during imaging
    imaging.handles[0].release()
    assert wait_until(lambda: runner.state == RunnerState.PAUSED)
    assert len(fluidics.starts) == 2  # the cleave step has not started
    boundary = manifest_io.read_manifest(run_dir)
    assert boundary.cursor.step == 3 and boundary.cursor.attempt == 0  # the finished step is not offered for resume

    runner.resume()
    assert runner.wait(10) and runner.outcome == "finished" and len(fluidics.starts) == 3


def test_abort_run_during_fluidics_ends_without_a_hold(tmp_path):
    fluidics = FakeFluidicsPort(script=[("hold",)])
    runner, fluidics, imaging, run_dir = _runner(tmp_path, fluidics=fluidics)
    runner.start()
    assert wait_until(lambda: len(fluidics.starts) == 1)
    runner.abort_run()
    assert runner.wait(10)
    assert runner.outcome == "stopped" and runner.hold is None
    assert manifest_io.read_manifest(run_dir).steps[0].attempts[0].outcome == "stopped"


def test_restore_tec_on_resume(tmp_path):
    tec = TecState(targets=[37.0, 37.0], output_enabled=[True, True])
    fluidics = FakeFluidicsPort(script=[("failed", 0, "temperature timeout")], tec=tec)
    runner, fluidics, imaging, run_dir = _runner(tmp_path, fluidics=fluidics)
    runner.start()
    assert _held(runner)
    assert runner.hold.tec_before == tec
    runner.hold_action(HoldAction.RESTART, restore_tec=True)
    assert runner.wait(10) and fluidics.restored == [tec]


def test_crash_recovery_starts_held_at_the_cursor_and_resumes_the_tail(tmp_path):
    # A first run "crashes" mid-R01 fluidics: reproduce its manifest with a dead pid.
    runner, fluidics, imaging, run_dir = _runner(tmp_path)
    runner.start()
    assert runner.wait(10)
    crashed = manifest_io.read_manifest(run_dir)
    crashed.status = "running"
    crashed.pid = 2**22 - 7
    crashed.cursor.step, crashed.cursor.attempt, crashed.cursor.sequence = 1, 1, 1
    crashed.steps[1].attempts[0].outcome = None
    manifest_io.write_manifest(run_dir, crashed)
    assert [os.path.basename(x.run_dir) for x in manifest_io.find_unfinished_runs(tmp_path)] == [run_dir.name]

    fluidics2, imaging2 = FakeFluidicsPort(), FakeImagingPort()
    resolved = resolve_protocol(_protocol(), tmp_path, fluidics=fluidics2)
    os.rename(run_dir / "R01_image", run_dir / "R01_image_old")  # the imaging step re-runs into R01_image
    recovered = ProtocolRunner(resolved, run_dir, imaging2, fluidics2, run_name="liver", manifest=crashed, poll_s=0.01)
    recovered.start()
    assert _held(recovered)
    assert recovered.hold.reason == "recovered" and recovered.hold.step_index == 1
    assert recovered.hold.resume_position == 1 and recovered.hold.can_resume
    assert fluidics2.make_safe_calls == 1

    recovered.hold_action(HoldAction.RESUME)
    assert recovered.wait(10) and recovered.outcome == "finished"
    assert len(fluidics2.starts[0]["plan"]) == 1  # tail from sequence 1 of the 2-row R01 step
    man = manifest_io.read_manifest(run_dir)
    assert man.status == "finished" and man.steps[0].attempts[0].outcome == "finished"  # untouched history
    assert man.steps[1].attempts[0].outcome == "error" and man.steps[1].attempts[1].outcome == "finished"
    assert len(imaging2.requests) == 1 and len(fluidics2.starts) == 2


def test_abort_run_while_held_ends_the_run(tmp_path):
    fluidics = FakeFluidicsPort(script=[("failed", 0, "boom")])
    runner, fluidics, imaging, run_dir = _runner(tmp_path, fluidics=fluidics)
    runner.start()
    assert _held(runner)
    runner.abort_run()
    assert runner.wait(10) and runner.outcome == "stopped"


def test_refused_pause_parks_at_the_step_boundary(tmp_path):
    fluidics = FakeFluidicsPort(script=[("nopause",)])
    runner, fluidics, imaging, run_dir = _runner(tmp_path, fluidics=fluidics)
    runner.start()
    assert wait_until(lambda: len(fluidics.starts) == 1)

    runner.pause()  # the library has no gate left: pause() returns False
    assert wait_until(lambda: len(fluidics.starts) == 1 and runner.state == RunnerState.RUNNING)
    fluidics.tickets[0].release()
    assert wait_until(lambda: runner.state == RunnerState.PAUSED)
    assert len(fluidics.starts) == 1  # the next step waits for Resume

    runner.resume()
    assert runner.wait(10) and runner.outcome == "finished"


def test_fluidics_start_refusal_holds_as_failed_to_start(tmp_path):
    fluidics = FakeFluidicsPort(script=[("raise",), ("finished",)])
    runner, fluidics, imaging, run_dir = _runner(tmp_path, fluidics=fluidics)
    runner.start()
    assert _held(runner)
    assert runner.hold.reason == "failed_to_start" and "busy" in runner.hold.message
    man = manifest_io.read_manifest(run_dir)
    assert man.steps[0].attempts[0].outcome == "failed_to_start"

    runner.hold_action(HoldAction.RESTART)
    assert runner.wait(10) and runner.outcome == "finished"


def test_recovery_refuses_a_manifest_that_does_not_match_the_protocol(tmp_path):
    runner, fluidics, imaging, run_dir = _runner(tmp_path)
    runner.start()
    assert runner.wait(10)
    crashed = manifest_io.read_manifest(run_dir)
    crashed.status = "running"
    crashed.cursor.step = 1

    other = resolve_protocol(_protocol(rounds=2), tmp_path, fluidics=FakeFluidicsPort())
    with pytest.raises(ValueError, match="steps"):
        ProtocolRunner(other, run_dir, FakeImagingPort(), FakeFluidicsPort(), run_name="liver", manifest=crashed)

    (run_dir / "protocol.yaml").write_text("version: 1\nsequences: []\n")
    same = resolve_protocol(_protocol(), tmp_path, fluidics=FakeFluidicsPort())
    with pytest.raises(ValueError, match="protocol.yaml"):
        ProtocolRunner(same, run_dir, FakeImagingPort(), FakeFluidicsPort(), run_name="liver", manifest=crashed)


def test_a_crash_while_polling_aborts_the_step_and_makes_the_system_safe(tmp_path):
    fluidics = FakeFluidicsPort(script=[("explode",)])
    runner, fluidics, imaging, run_dir = _runner(tmp_path, fluidics=fluidics)
    runner.start()
    assert runner.wait(10)

    assert runner.outcome == "failed"
    assert fluidics.tickets[0]._aborted is True
    assert fluidics.make_safe_calls == 1
    man = manifest_io.read_manifest(run_dir)
    assert man.status == "failed" and "device fell off the bus" in man.hold_message


def test_recovery_refuses_a_missing_or_different_protocol_copy(tmp_path):
    runner, fluidics, imaging, run_dir = _runner(tmp_path)
    runner.start()
    assert runner.wait(10)
    crashed = manifest_io.read_manifest(run_dir)
    crashed.status = "running"
    crashed.cursor.step = 1
    crashed.protocol_sha256 = None  # get past the hash check to the content check

    from control.models.fluidics_protocol import save_protocol

    changed = _protocol()
    changed.sequences[1]["volume"] = 9999  # same steps, different instructions
    save_protocol(changed, str(run_dir / "protocol.yaml"))
    same = resolve_protocol(_protocol(), tmp_path, fluidics=FakeFluidicsPort())
    with pytest.raises(ValueError, match="differs from protocol.yaml"):
        ProtocolRunner(same, run_dir, FakeImagingPort(), FakeFluidicsPort(), run_name="liver", manifest=crashed)

    (run_dir / "protocol.yaml").unlink()
    with pytest.raises(ValueError, match="missing"):
        ProtocolRunner(same, run_dir, FakeImagingPort(), FakeFluidicsPort(), run_name="liver", manifest=crashed)


# ---- release-and-settle before an imaging row (imaging.release_and_settle) ----

FAST_RELEASE = {"release_port": 30, "alternate_port": 15, "window_s": 0.05, "timeout_s": 0.4, "fixed_wait_s": 0.05}


@pytest.fixture
def fast_sampling(monkeypatch):
    from control.core.fluidics_protocol import release_settle

    monkeypatch.setattr(release_settle, "SAMPLE_INTERVAL_S", 0.005)


def _release_runner(tmp_path, release, focus=None, fluidics=None, imaging_script=None):
    fluidics = fluidics or FakeFluidicsPort()
    imaging = FakeImagingPort(imaging_script, focus=focus)
    protocol = _protocol()
    protocol.imaging.release_and_settle = ReleaseAndSettle.model_validate(release)
    resolved = resolve_protocol(protocol, tmp_path, fluidics=fluidics)
    run_dir = manifest_io.create_run_dir(tmp_path, "liver")
    runner = ProtocolRunner(resolved, run_dir, imaging, fluidics, run_name="liver", poll_s=0.01)
    return runner, fluidics, imaging, run_dir


def _springing_back(fluidics, depth=-12.0, seconds=0.06):
    """Pulled in `depth` um until the valve moves, back to 0 over `seconds`, then still."""

    def focus():
        since = fluidics.since_open()
        return depth if since is None else depth * max(0.0, 1.0 - since / seconds)

    return focus


def test_without_release_and_settle_the_valve_is_not_touched_before_imaging(tmp_path):
    runner, fluidics, imaging, run_dir = _runner(tmp_path)
    runner.start()
    assert runner.wait(10) and runner.outcome == "finished"
    assert fluidics.opened == [] and imaging.focus_reads == 0
    attempt = manifest_io.read_manifest(run_dir).steps[2].attempts[0]
    assert attempt.release_and_settle is None
    assert "release_and_settle" not in json.loads((run_dir / "R01_image" / "protocol_step.json").read_text())


def test_release_and_settle_moves_the_valve_waits_for_the_coverslip_and_records_it(tmp_path, fast_sampling):
    fluidics = FakeFluidicsPort()
    fluidics.port = 25
    fluidics.flow_ul_min = lambda since: 0.0 if since is None else 600.0  # 600 uL/min = 10 uL/s after the move
    runner, fluidics, imaging, run_dir = _release_runner(
        tmp_path, FAST_RELEASE, focus=_springing_back(fluidics), fluidics=fluidics
    )
    runner.start()
    assert runner.wait(10) and runner.outcome == "finished"

    assert fluidics.opened == [30] and len(imaging.requests) == 1
    record = manifest_io.read_manifest(run_dir).steps[2].attempts[0].release_and_settle
    assert record.mode == "laser_af" and record.settled is True and record.port_before == 25
    assert record.rise_um == pytest.approx(12.0, abs=0.01) and record.baseline_um == pytest.approx(-12.0)
    assert 0.1 <= record.settle_s < 0.4 and record.inflow_ul > 0
    step_info = json.loads((run_dir / "R01_image" / "protocol_step.json").read_text())
    assert step_info["release_and_settle"]["settled"] is True and len(step_info["release_and_settle"]["series"]) > 10
    # acquisition.yaml's protocol: section carries the summary, not the reading series
    assert imaging.requests[0].protocol["release_and_settle"]["rise_um"] == pytest.approx(12.0, abs=0.01)
    assert "series" not in imaging.requests[0].protocol["release_and_settle"]
    log = (run_dir / "run.log").read_text()
    assert "release and settle: valve 25 -> 30, rise +12.00 um" in log and "settled in" in log


def test_a_coverslip_that_does_not_settle_holds_the_step_and_restart_checks_again(tmp_path, fast_sampling):
    fluidics = FakeFluidicsPort()
    drifting = {"on": True}
    focus = lambda: (fluidics.since_open() or 0.0) * 50.0 if drifting["on"] else 0.0  # 50 um/s while drifting
    runner, fluidics, imaging, run_dir = _release_runner(tmp_path, FAST_RELEASE, focus=focus, fluidics=fluidics)
    runner.start()
    assert _held(runner)

    hold = runner.hold
    assert hold.kind == "imaging" and hold.reason == "not_settled" and not hold.can_resume and not hold.can_accept
    assert "did not settle after the valve moved to port 30" in hold.message
    assert imaging.requests == [] and fluidics.make_safe_calls >= 1
    attempt = manifest_io.read_manifest(run_dir).steps[2].attempts[0]
    assert attempt.outcome == "not_settled" and attempt.release_and_settle.settled is False
    assert "NOT settled" in (run_dir / "run.log").read_text()

    drifting["on"] = False
    runner.hold_action(HoldAction.RESTART)
    assert runner.wait(10) and runner.outcome == "finished"
    # the valve sat on the release port after attempt 1, so attempt 2 moves it to the alternate port
    assert fluidics.opened == [30, 15] and [r.folder for r in imaging.requests] == ["R01_image_attempt2"]
    assert manifest_io.read_manifest(run_dir).steps[2].attempts[1].release_and_settle.settled is True


def test_on_timeout_warn_logs_and_images_anyway(tmp_path, fast_sampling):
    fluidics = FakeFluidicsPort()
    focus = lambda: (fluidics.since_open() or 0.0) * 50.0
    runner, fluidics, imaging, run_dir = _release_runner(
        tmp_path, {**FAST_RELEASE, "on_timeout": "warn"}, focus=focus, fluidics=fluidics
    )
    runner.start()
    assert runner.wait(10) and runner.outcome == "finished"
    assert len(imaging.requests) == 1
    attempt = manifest_io.read_manifest(run_dir).steps[2].attempts[0]
    assert attempt.outcome == "completed" and attempt.release_and_settle.settled is False
    assert "WARNING" in next(l for l in (run_dir / "run.log").read_text().splitlines() if "NOT settled" in l)


def test_without_laser_af_the_step_waits_a_fixed_time_after_the_valve_move(tmp_path, fast_sampling):
    runner, fluidics, imaging, run_dir = _release_runner(tmp_path, FAST_RELEASE, focus=None)
    runner.start()
    assert runner.wait(10) and runner.outcome == "finished"
    record = manifest_io.read_manifest(run_dir).steps[2].attempts[0].release_and_settle
    assert fluidics.opened == [30] and record.mode == "fixed_wait" and record.settled is None
    assert record.waited_s >= 0.05 and record.message == "laser AF not available"
    assert len(imaging.requests) == 1


def test_a_failed_release_valve_move_holds_the_step(tmp_path, fast_sampling):
    fluidics = FakeFluidicsPort()
    fluidics.open_error = "the rig is busy: a manual is in progress"
    runner, fluidics, imaging, run_dir = _release_runner(tmp_path, FAST_RELEASE, focus=lambda: 0.0, fluidics=fluidics)
    runner.start()
    assert _held(runner)
    assert runner.hold.reason == "release_failed" and "the rig is busy" in runner.hold.message
    assert imaging.requests == []
    runner.hold_action(HoldAction.END)
    assert runner.wait(10) and runner.outcome == "stopped"


def test_abort_step_during_the_settle_wait_holds_as_user_abort(tmp_path, fast_sampling):
    fluidics = FakeFluidicsPort()
    slow = {**FAST_RELEASE, "timeout_s": 30.0}
    runner, fluidics, imaging, run_dir = _release_runner(
        tmp_path, slow, focus=lambda: (fluidics.since_open() or 0.0) * 50.0, fluidics=fluidics
    )
    runner.start()
    assert wait_until(lambda: fluidics.opened == [30])
    runner.abort_step()
    assert _held(runner)
    assert runner.hold.reason == "user_abort" and "release-and-settle" in runner.hold.message
    assert imaging.requests == []
    runner.hold_action(HoldAction.SKIP)
    assert runner.wait(10) and runner.outcome == "finished"


def test_the_resolver_refuses_release_ports_the_rig_does_not_offer(tmp_path):
    fluidics = FakeFluidicsPort()
    fluidics.ports = tuple(range(1, 29))
    protocol = _protocol()
    protocol.imaging.release_and_settle = ReleaseAndSettle(release_port=30, alternate_port=15)
    with pytest.raises(ProtocolProblems) as e:
        resolve_protocol(protocol, tmp_path, fluidics=fluidics)
    assert e.value.problems == [
        "imaging.release_and_settle: release_port 30 is not a port of this fluidics system (it offers 1..28)"
    ]
    fluidics.ports = (1, 2, 3, 30)
    with pytest.raises(ProtocolProblems, match="alternate_port 15 is not a port"):
        resolve_protocol(protocol, tmp_path, fluidics=fluidics)
