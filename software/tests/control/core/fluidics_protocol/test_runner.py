import csv
import json
import os
from types import SimpleNamespace

import pytest

from control.core.fluidics_protocol import manifest as manifest_io
from control.core.fluidics_protocol.events import HoldAction, RunnerState
from control.core.fluidics_protocol.resolve import resolve_protocol
from control.core.fluidics_protocol.runner import ProtocolRunner
from control.models.fluidics_protocol import ProtocolFile, expand_rounds
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


def _runner(tmp_path, fluidics=None, imaging=None, rounds=1, events=None, focus_log=False):
    fluidics = fluidics or FakeFluidicsPort()
    imaging = imaging or FakeImagingPort()
    protocol = _protocol(rounds)
    if focus_log:
        protocol.imaging.focus_log = True
    resolved = resolve_protocol(protocol, tmp_path, fluidics=fluidics)
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


# ---- imaging.focus_log: a laser AF reading before each fluidics step and after each sequence ----


def _focus_rows(run_dir):
    with open(run_dir / manifest_io.FOCUS_LOG_NAME, newline="") as f:
        return list(csv.DictReader(f))


def test_focus_log_is_off_by_default(tmp_path):
    imaging = FakeImagingPort(focus=lambda: 1.0)
    runner, fluidics, imaging, run_dir = _runner(tmp_path, imaging=imaging)
    runner.start()
    assert runner.wait(10) and runner.outcome == "finished"
    assert not (run_dir / manifest_io.FOCUS_LOG_NAME).exists()
    assert imaging.focus_reads == 0
    assert all(start["after_sequence"] is None for start in fluidics.starts)


def test_focus_log_reads_before_each_fluidics_step_and_after_each_sequence(tmp_path):
    heights = iter(range(100))  # every reading a new height: 3 readings per row
    position = {"x_mm": 12.5, "y_mm": 30.25, "z_um": 4321.0, "piezo_um": 150.0}
    imaging = FakeImagingPort(focus=lambda: float(next(heights)), position=position)
    runner, fluidics, imaging, run_dir = _runner(tmp_path, imaging=imaging, focus_log=True)
    runner.start()
    assert runner.wait(10) and runner.outcome == "finished"

    rows = _focus_rows(run_dir)
    assert [(r["step"], r["round"], r["point"], r["sequence"], r["name"]) for r in rows] == [
        ("0", "setup", "before", "", ""),
        ("0", "setup", "after", "1/1", "prime"),
        ("1", "R01", "before", "", ""),
        ("1", "R01", "after", "1/2", "probe"),
        ("1", "R01", "after", "2/2", "wash"),
        ("3", "R01", "before", "", ""),
        ("3", "R01", "after", "1/1", "cleave"),
    ]
    probe = rows[3]
    assert (probe["type"], probe["port"], probe["volume_ul"], probe["flow_rate_ul_min"]) == (
        "flow_reagent",
        "1",
        "500",
        "2000",
    )
    assert [float(r["displacement_um"]) for r in rows] == [1.0 + 3 * i for i in range(7)]  # mean of 3 readings
    assert all(r["spread_um"] == "2.000" and r["readings_ok"] == "3" and r["ok"] == "true" for r in rows)
    assert (probe["x_mm"], probe["y_mm"], probe["z_um"], probe["piezo_um"]) == (
        "12.5000",
        "30.2500",
        "4321.00",
        "150.00",
    )
    assert all(r["attempt"] == "1" and r["time"] and float(r["elapsed_s"]) >= 0 for r in rows)
    # every reading was taken on the runner thread, the engine's thread only waited for it
    assert imaging.focus_reads == 21 and set(imaging.focus_threads) == {"fluidics-protocol"}
    assert all(start["after_sequence"] is not None for start in fluidics.starts)
    assert "Focus log: step 1 (R01) after 2/2 wash: 13.000 um" in (run_dir / "run.log").read_text()
    # measurement only: the same runs started as without the log
    assert [len(start["plan"]) for start in fluidics.starts] == [1, 2, 1]


@pytest.mark.parametrize(
    "focus, message",
    [
        (None, "laser AF not available"),
        (lambda: float("nan"), "no laser AF spot"),
        (lambda: (_ for _ in ()).throw(TimeoutError("focus camera")), "reading failed: focus camera"),
    ],
)
def test_focus_log_never_changes_the_run_when_there_is_no_reading(tmp_path, focus, message):
    def no_position():
        raise RuntimeError("stage busy")

    imaging = FakeImagingPort(focus=focus, position=no_position)
    runner, fluidics, imaging, run_dir = _runner(tmp_path, imaging=imaging, focus_log=True)
    runner.start()
    assert runner.wait(10) and runner.outcome == "finished"
    rows = _focus_rows(run_dir)
    assert len(rows) == 7
    assert all(r["ok"] == "false" and message in r["message"] and r["displacement_um"] == "" for r in rows)
    assert all(r["x_mm"] == "" for r in rows)
    if focus is None:
        assert imaging.focus_reads == 0  # laser AF not usable: not asked again within a row
    man = manifest_io.read_manifest(run_dir)
    assert [s.attempts[0].outcome for s in man.steps] == ["finished", "finished", "completed", "finished"]


def test_focus_log_names_the_sequences_of_a_resumed_plan_tail(tmp_path):
    fluidics = FakeFluidicsPort(script=[("finished",), ("failed", 1, "Flow fault on syringe_draw"), ("finished",)])
    imaging = FakeImagingPort(focus=lambda: 2.0)
    runner, fluidics, imaging, run_dir = _runner(tmp_path, fluidics=fluidics, imaging=imaging, focus_log=True)
    runner.start()
    assert _held(runner) and runner.hold.resume_position == 1
    runner.hold_action(HoldAction.RESUME)
    assert runner.wait(10) and runner.outcome == "finished"

    rows = [r for r in _focus_rows(run_dir) if r["step"] == "1"]
    assert [(r["attempt"], r["point"], r["sequence"], r["name"]) for r in rows] == [
        ("1", "before", "", ""),
        ("1", "after", "1/2", "probe"),  # completed before the fault in the 2nd sequence
        ("2", "before", "", ""),
        ("2", "after", "2/2", "wash"),  # the tail's 1st entry is the step's 2nd sequence
    ]


def test_an_abort_during_a_focus_log_reading_stops_the_readings(tmp_path):
    box = {}

    def reading():
        box["runner"].abort_run()  # the operator presses Abort run while the first reading is being taken
        return 1.0

    imaging = FakeImagingPort(focus=reading)
    runner, fluidics, imaging, run_dir = _runner(tmp_path, imaging=imaging, focus_log=True)
    box["runner"] = runner
    runner.start()
    assert runner.wait(10)
    assert imaging.focus_reads == 1  # the other readings of that row and of later rows never started
    rows = _focus_rows(run_dir)
    assert "stopped after 1 of 3 readings: abort requested" in rows[0]["message"]
    assert fluidics.starts == []  # the abort came before the launch: no pump or valve moved
    assert runner.outcome == "stopped" and runner.hold is None
    assert manifest_io.read_manifest(run_dir).steps[0].attempts[0].outcome == "stopped"


def test_a_step_abort_during_the_before_step_reading_holds_without_launching_the_fluidics(tmp_path):
    box = {"aborted": False}

    def reading():
        if not box["aborted"]:
            box["aborted"] = True
            box["runner"].abort_step()  # the operator presses Abort step while the before-step reading is taken
        return 1.0

    imaging = FakeImagingPort(focus=reading)
    runner, fluidics, imaging, run_dir = _runner(tmp_path, imaging=imaging, focus_log=True)
    box["runner"] = runner
    runner.start()
    assert _held(runner)
    assert fluidics.starts == []  # held like an abort during the fluidics, but nothing was launched
    hold = runner.hold
    assert hold.kind == "fluidics" and hold.reason == "stopped" and hold.resume_position == 0 and hold.can_resume
    attempt = manifest_io.read_manifest(run_dir).steps[0].attempts[0]
    assert attempt.outcome == "stopped" and attempt.ended_position == 0

    runner.hold_action(HoldAction.RESUME)
    assert runner.wait(10) and runner.outcome == "finished"
    assert len(fluidics.starts[0]["plan"]) == len(fluidics.plan(fluidics.starts[0]["rows"]))  # the whole step
