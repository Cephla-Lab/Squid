"""A plate-sipper protocol through Squid's engine on the library's simulated sipper rig: rounds made
by Add rounds, the pre-flight volume gate over the whole protocol, and the runner driving it."""

import pytest

pytest.importorskip("fluidics")

from control.core.fluidics_protocol import manifest as manifest_io
from control.core.fluidics_protocol.events import RunnerState
from control.core.fluidics_protocol.library_port import LibraryFluidicsPort
from control.core.fluidics_protocol.resolve import ProtocolProblems, resolve_protocol
from control.core.fluidics_protocol.runner import ProtocolRunner
from control.fluidics_system import FluidicsService
from control.models.fluidics_protocol import ProtocolFile, expand_rounds, next_wells, strip_for_library
from tests.control.core.fluidics_protocol.fakes import FakeImagingPort
from tests.control.fluidics_test_config import SIPPER_ROWS

IMAGING = {
    "settings": {"cur": {"channels": ["A"]}},
    "coordinates": {"cur": {"regions": [{"name": "C4", "fovs": [[1.0, 2.0, 3.0]]}]}},
}


def _protocol(rounds: int, wells=True) -> ProtocolFile:
    """setup priming, then `rounds` rounds of hyb / push / ssct / dapi / image. Incubations are left
    out: the library times them on a real clock, even on an instant simulated rig."""
    rows = [{k: v for k, v in row.items() if k != "incubation_time"} for row in SIPPER_ROWS]
    rows.append({"type": "imaging", "round": "R01", "name": "image", "folder": "image"})
    rows[-1].update(settings="cur", coordinates="cur")
    base = ProtocolFile(name="seqfish", imaging=IMAGING, sequences=rows)
    return expand_rounds(base, "R01", rounds - 1, wells=next_wells("A1", rounds - 1) if wells else None)


@pytest.fixture
def service(tmp_path, sipper_config_path):
    svc = FluidicsService(default_config_path=sipper_config_path, simulated=True)
    svc.initialize(report_dir=str(tmp_path / "reports"), instant=True)
    yield svc
    assert svc.close() == []


def test_a_two_round_sipper_protocol_runs_on_the_simulated_rig(service, tmp_path):
    port = LibraryFluidicsPort(service.system)
    resolved = resolve_protocol(_protocol(rounds=2), tmp_path, fluidics=port)
    # the pre-flight's projection: each round's hyb takes 100 of a probes well's 350, and its ssct
    # well gives the 710 push and the 1000 wash of its 1800
    left = {key: remaining for key, _dispatched, remaining in resolved.reservoirs}
    assert (left["probes/A1"], left["probes/A2"]) == (250, 250)
    assert (left["ssct/A1"], left["ssct/A2"]) == (90, 90)

    run_dir = manifest_io.create_run_dir(tmp_path, "sipper")
    imaging = FakeImagingPort()
    runner = ProtocolRunner(resolved, run_dir, imaging, port, run_name="sipper", poll_s=0.01)
    runner.start()
    assert runner.wait(120), runner.snapshot()

    assert runner.state == RunnerState.ENDED and runner.outcome == "finished", runner.snapshot()
    man = manifest_io.read_manifest(run_dir)
    assert [s.label for s in man.steps] == ["setup", "R01", "image", "R02", "image"]
    r01 = man.steps[1].attempts[0]
    assert r01.reagent_used_ul["1"] == 100 + 710 + 1000  # every well draw counts under the sipper's port
    assert r01.reagent_used_ul["4"] == 300
    assert sorted(r.folder for r in imaging.requests) == ["R01_image", "R02_image"]
    tracker = service.system.devices.reservoirs
    assert tracker.remaining("probes/A2") == 250 and tracker.remaining("ssct/A2") == 90  # the projection held
    assert service.system.devices.robot.current_well_key() == "ssct/A2"  # the tip rests in R02's last well


def test_the_preflight_refuses_a_protocol_that_would_empty_a_well_rounds_in(service, tmp_path):
    port = LibraryFluidicsPort(service.system)
    protocol = _protocol(rounds=2, wells=False)  # R02 draws from R01's wells again
    r01 = strip_for_library([row for row in protocol.sequences if row.get("round") == "R01"])
    assert port.check_reservoirs(port.plan(r01))  # each round alone fits, so the library's own gate passes it

    with pytest.raises(ProtocolProblems) as refused:
        resolve_protocol(protocol, tmp_path, fluidics=port)
    (problem,) = refused.value.problems
    assert problem.startswith("reservoirs: ") and "ssct/A1" in problem

    # a recovered run's finished steps already drew: there each step is gated as it starts instead
    resolve_protocol(protocol, tmp_path, fluidics=port, check_reservoirs=False)


def test_undeclared_volumes_name_the_reservoirs_tab(service, tmp_path):
    tracker = service.system.devices.reservoirs
    tracker.restore({"undeclared": ["probes/A1"]})  # as a fresh volume tracking file brings them up
    port = LibraryFluidicsPort(service.system)
    with pytest.raises(ProtocolProblems) as refused:
        resolve_protocol(_protocol(rounds=1), tmp_path, fluidics=port)
    (problem,) = refused.value.problems
    assert "probes/A1" in problem and "Reservoirs tab" in problem

    rows = strip_for_library(_protocol(rounds=1).sequences)
    with pytest.raises(Exception, match="Reservoirs tab"):  # the same words when a step's start is refused
        port.start(rows)
