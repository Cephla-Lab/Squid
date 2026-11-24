import csv
import math
import threading
import time

from control.core.fluidics_protocol.focus_log import COLUMNS, FocusLog, read_focus, sequence_context
from tests.control.core.fluidics_protocol.fakes import FakeImagingPort, FakePlanEntry, wait_until


def _readings(*values):
    it = iter(values)
    return FakeImagingPort(focus=lambda: next(it))


def test_read_focus_averages_the_readings_that_found_the_spot():
    reading = read_focus(_readings(1.0, float("nan"), 2.0), readings=3)
    assert reading.ok and reading.readings_ok == 2
    assert math.isclose(reading.displacement_um, 1.5) and math.isclose(reading.spread_um, 1.0)

    assert read_focus(_readings(float("nan"), float("nan")), readings=2).message == "no laser AF spot"
    none = FakeImagingPort(focus=None)
    reading = read_focus(none)
    assert not reading.ok and "not available" in reading.message and none.focus_reads == 0


def test_sequence_context_names_the_row_and_the_repeat():
    rows = [{"type": "flow_reagent", "name": "rinse", "fluidic_port": 25, "volume": 500, "flow_rate": 10000}]
    entry = FakePlanEntry(0)
    entry.repeat, entry.repeats = 2, 3
    assert sequence_context(rows, entry, 4, 6) == {
        "sequence": "5/6",
        "name": "rinse",
        "type": "flow_reagent",
        "port": 25,
        "volume_ul": 500,
        "flow_rate_ul_min": 10000,
        "repeat": "2/3",
    }


def test_a_sequence_boundary_waits_for_the_runner_thread_and_then_goes_on(tmp_path):
    log = FocusLog(tmp_path / "focus_log.csv", FakeImagingPort(focus=lambda: 0.5), started_at=time.time())
    result = {}
    engine = threading.Thread(target=lambda: result.setdefault("ok", log.request({"step": 0, "point": "after"})))
    engine.start()
    assert wait_until(lambda: log._request is not None)
    assert engine.is_alive()  # the next sequence waits for the reading
    log.serve()
    engine.join(5)
    assert result["ok"] is True
    with open(tmp_path / "focus_log.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0].keys()) == list(COLUMNS) and rows[0]["displacement_um"] == "0.500"


def test_a_sequence_boundary_stops_waiting_when_nobody_takes_the_reading(tmp_path):
    imaging = FakeImagingPort(focus=lambda: 0.5)
    log = FocusLog(tmp_path / "focus_log.csv", imaging, started_at=time.time(), wait_s=0.05)
    result = {}
    engine = threading.Thread(target=lambda: result.setdefault("ok", log.request({"step": 0, "sequence": "1/2"})))
    engine.start()
    engine.join(5)
    assert result["ok"] is False and log._request is None
    log.serve()  # nothing left to take
    assert imaging.focus_reads == 0 and not (tmp_path / "focus_log.csv").exists()


def test_a_request_on_the_runner_thread_is_recorded_at_once_and_a_write_failure_does_not_raise(tmp_path):
    log = FocusLog(tmp_path / "missing" / "focus_log.csv", FakeImagingPort(focus=lambda: 0.5), started_at=0.0)
    assert log.request({"step": 0, "point": "before"}) is True
    assert not (tmp_path / "missing").exists()
