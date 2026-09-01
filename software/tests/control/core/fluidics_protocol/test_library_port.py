import logging

import pytest

pytest.importorskip("fluidics")

import squid.logging
from control.core.fluidics_protocol.library_port import LibraryFluidicsPort
from control.fluidics_system import FluidicsService
from control.models.fluidics_run import TecState
from tests.control.core.fluidics_protocol.fakes import wait_until

ROWS = [
    {"type": "flow_reagent", "fluidic_port": 1, "flow_rate": 500, "volume": 500},
    {"type": "flow_reagent", "fluidic_port": 2, "flow_rate": 500, "volume": 300},
]
SLOW_ROWS = [
    {"type": "flow_reagent", "fluidic_port": 1, "flow_rate": 500, "volume": 500, "incubation_time": 5},
    ROWS[1],
]


def _wait_outcome(ticket, tries=500):
    outcome = None
    for _ in range(tries):
        outcome = ticket.wait(0.02)
        if outcome:
            break
    return outcome


@pytest.fixture
def service(tmp_path, fluidics_config_path):
    svc = FluidicsService(default_config_path=fluidics_config_path, simulated=True)
    svc.initialize(report_dir=str(tmp_path), instant=True)
    yield svc
    assert svc.close() == []


@pytest.fixture
def tec_service(tmp_path):
    from tests.control.fluidics_test_config import TEC_CONFIG_YAML

    text = TEC_CONFIG_YAML
    config = tmp_path / "with_tec.yaml"
    config.write_text(text)
    svc = FluidicsService(default_config_path=str(config), simulated=True)
    svc.initialize(report_dir=str(tmp_path), instant=True)
    yield svc
    assert svc.close() == []


def test_validate_rejects_out_of_range_ports_and_unknown_keys(service):
    port = LibraryFluidicsPort(service.system)
    port.validate(ROWS)
    with pytest.raises(ValueError):
        port.validate([{**ROWS[0], "fluidic_port": 99}])
    port.validate([{**ROWS[0], "round": "R01"}])  # the pinned library understands round natively (phase 2)
    with pytest.raises(ValueError):
        port.validate([{**ROWS[0], "no_such_key": 1}])  # junk keys must still be rejected, not dropped


def test_run_finishes_with_reagent_totals_and_run_id(service):
    port = LibraryFluidicsPort(service.system)
    plan = port.plan(ROWS)
    assert len(plan) == 2 and all(e.duration_seconds >= 0 for e in plan)

    ticket = port.start(ROWS, plan=plan)
    outcome = _wait_outcome(ticket)

    assert outcome is not None and outcome.outcome == "finished" and outcome.position is None
    assert outcome.run_id and outcome.run_id == ticket.run_id
    assert ticket.position == 1  # the last sequence started was the 2nd of the plan
    assert outcome.reagent_used_ul.get(1, 0) > 0 and outcome.reagent_used_ul.get(2, 0) > 0
    assert service.system.busy is False


def test_abort_during_incubation_reports_stopped_with_position_and_the_tail_resumes(service):
    port = LibraryFluidicsPort(service.system)
    plan = port.plan(SLOW_ROWS)
    ticket = port.start(SLOW_ROWS, plan=plan)
    assert wait_until(lambda: service.system.busy, timeout=2)
    assert ticket.wait(0.2) is None  # incubating (5 min under a real clock)

    assert ticket.abort() is True
    outcome = _wait_outcome(ticket)
    assert outcome is not None and outcome.outcome == "stopped" and outcome.position == 0

    tail = port.start(SLOW_ROWS, plan=plan[1:])
    tail_outcome = _wait_outcome(tail)
    assert tail_outcome is not None and tail_outcome.outcome == "finished"


def test_pause_during_incubation_is_acknowledged_and_resume_continues(service):
    port = LibraryFluidicsPort(service.system)
    ticket = port.start(SLOW_ROWS)
    assert wait_until(lambda: service.system.busy, timeout=2)
    assert ticket.pause() is True
    assert wait_until(lambda: service.system.session.snapshot().paused, timeout=2)
    assert ticket.resume() is True
    assert ticket.abort() is True
    assert _wait_outcome(ticket) is not None


def test_tec_state_round_trips(tec_service):
    port = LibraryFluidicsPort(tec_service.system)
    assert port.tec_state() == TecState(targets=[10.0, 10.0], output_enabled=[False, False])
    port.restore_tec(TecState(targets=[37.0, 25.0], output_enabled=[True, False]))
    assert port.tec_state() == TecState(targets=[37.0, 25.0], output_enabled=[True, False])


def test_tec_state_is_none_without_a_temperature_controller(service):
    assert LibraryFluidicsPort(service.system).tec_state() is None


def test_library_run_log_lines_reach_squid_handlers(service):
    class Capture(logging.Handler):
        def __init__(self):
            super().__init__(level=logging.DEBUG)
            self.names = []

        def emit(self, record):
            self.names.append(record.name)

    capture = Capture()
    squid.logging.get_logger().addHandler(capture)
    try:
        ticket = LibraryFluidicsPort(service.system).start(ROWS)
        assert _wait_outcome(ticket) is not None
    finally:
        squid.logging.get_logger().removeHandler(capture)
    assert any(name.startswith("fluidics") for name in capture.names)
