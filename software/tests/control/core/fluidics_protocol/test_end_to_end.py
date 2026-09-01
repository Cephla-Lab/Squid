import pytest

pytest.importorskip("fluidics")

from control.core.fluidics_protocol import manifest as manifest_io
from control.core.fluidics_protocol.events import RunnerState
from control.core.fluidics_protocol.library_port import LibraryFluidicsPort
from control.core.fluidics_protocol.resolve import resolve_protocol
from control.core.fluidics_protocol.runner import ProtocolRunner
from control.fluidics_system import FluidicsService
from control.models.fluidics_protocol import ProtocolFile, expand_rounds, load_protocol
from tests.control.core.fluidics_protocol.fakes import FakeImagingPort


def _protocol():
    base = ProtocolFile(
        name="merfish_3r",
        imaging={
            "settings": {"cur": {"channels": ["A"]}},
            "coordinates": {"cur": {"regions": [{"name": "A1", "fovs": [[1.0, 2.0, 3.0]]}]}},
        },
        sequences=[
            {
                "type": "flow_reagent",
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
                "name": "buffer",
                "fluidic_port": 27,
                "flow_rate": 5000,
                "volume": 300,
            },
            {
                "type": "imaging",
                "round": "R01",
                "name": "image",
                "folder": "R01_image",
                "settings": "cur",
                "coordinates": "cur",
            },
            {
                "type": "flow_reagent",
                "round": "R01",
                "name": "rinse",
                "fluidic_port": 25,
                "flow_rate": 10000,
                "volume": 500,
                "repeat": 2,
            },
            {
                "type": "flow_reagent",
                "round": "final",
                "name": "cleanup",
                "fluidic_port": 28,
                "flow_rate": 10000,
                "volume": 500,
            },
        ],
    )
    return expand_rounds(base, "R01", 2, port_row_name="probe", ports=[2, 3])


def test_three_round_protocol_runs_on_the_simulated_fluidics_system(tmp_path, fluidics_config_path):
    service = FluidicsService(default_config_path=fluidics_config_path, simulated=True)
    service.initialize(report_dir=str(tmp_path / "reports"), instant=True)
    try:
        port = LibraryFluidicsPort(service.system)
        resolved = resolve_protocol(_protocol(), tmp_path, fluidics=port)
        assert resolved.fluidics_estimate_s is not None
        run_dir = manifest_io.create_run_dir(tmp_path, "liver")
        imaging = FakeImagingPort()
        runner = ProtocolRunner(resolved, run_dir, imaging, port, run_name="liver", poll_s=0.01)

        runner.start()
        assert runner.wait(60), runner.snapshot()

        assert runner.state == RunnerState.ENDED and runner.outcome == "finished"
        man = manifest_io.read_manifest(run_dir)
        assert man.status == "finished"
        # setup | R01 fluidics | R01 imaging | R01 rinse | R02 ... | R03 ... | final
        assert [s.kind for s in man.steps] == [
            "fluidics",
            "fluidics",
            "imaging",
            "fluidics",
            "fluidics",
            "imaging",
            "fluidics",
            "fluidics",
            "imaging",
            "fluidics",
            "fluidics",
        ]
        fluidics_attempts = [s.attempts[0] for s in man.steps if s.kind == "fluidics"]
        assert all(a.outcome == "finished" and a.fluidics_run_id for a in fluidics_attempts)
        assert fluidics_attempts[1].reagent_used_ul.get("1", 0) > 0  # R01 probe drew from port 1
        assert sorted(r.folder for r in imaging.requests) == ["R01_image", "R02_image", "R03_image"]
        copy = load_protocol(str(run_dir / "protocol.yaml"))
        assert copy.name == "merfish_3r" and len(copy.sequences) == 6 + 2 * 4
        assert (run_dir / "run.log").stat().st_size > 0
    finally:
        assert service.close() == []
