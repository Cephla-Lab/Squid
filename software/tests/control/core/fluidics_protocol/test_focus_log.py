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


def _slow_readings(seconds, value=1.0):
    def reading():
        time.sleep(seconds)
        return value

    return FakeImagingPort(focus=reading)


def _rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def test_a_reading_the_boundary_gave_up_on_is_marked_late_not_written_under_its_label(tmp_path):
    # Three 0.2 s readings, but the sequence boundary waits only 0.3 s: the next sequence starts during the reading.
    imaging = _slow_readings(0.2)
    log = FocusLog(tmp_path / "focus_log.csv", imaging, started_at=time.time(), wait_s=0.3)
    result = {}
    engine = threading.Thread(target=lambda: result.setdefault("ok", log.request({"step": 0, "sequence": "1/2"})))
    engine.start()
    assert wait_until(lambda: log._request is not None)
    log.serve()
    engine.join(5)
    assert result["ok"] is False
    assert imaging.focus_reads < 3  # no reading started after the boundary's deadline
    (row,) = _rows(tmp_path / "focus_log.csv")
    assert row["sequence"] == "1/2" and row["ok"] == "false" and row["displacement_um"] == ""
    assert "late" in row["message"]


def test_a_reading_that_ends_in_time_is_kept(tmp_path):
    imaging = _slow_readings(0.01, value=2.0)
    log = FocusLog(tmp_path / "focus_log.csv", imaging, started_at=time.time(), wait_s=5.0)
    result = {}
    engine = threading.Thread(target=lambda: result.setdefault("ok", log.request({"step": 0, "sequence": "1/2"})))
    engine.start()
    assert wait_until(lambda: log._request is not None)
    log.serve()
    engine.join(5)
    assert result["ok"] is True
    (row,) = _rows(tmp_path / "focus_log.csv")
    assert row["ok"] == "true" and row["displacement_um"] == "2.000" and row["readings_ok"] == "3"


def test_an_abort_stops_the_reading_between_two_readings(tmp_path):
    stop = threading.Event()

    def reading():
        stop.set()  # the operator presses abort while the first reading is being taken
        time.sleep(0.05)
        return 1.0

    imaging = FakeImagingPort(focus=reading)
    log = FocusLog(tmp_path / "focus_log.csv", imaging, started_at=time.time(), should_stop=stop.is_set)
    result = {}
    engine = threading.Thread(target=lambda: result.setdefault("ok", log.request({"step": 0, "sequence": "1/2"})))
    engine.start()
    assert wait_until(lambda: log._request is not None)
    log.serve()
    engine.join(5)
    assert imaging.focus_reads == 1  # the abort is seen after one reading, not after all of them
    (row,) = _rows(tmp_path / "focus_log.csv")
    assert row["readings_ok"] == "1" and "stopped" in row["message"]

    log.record({"step": 1, "point": "before"})  # abort already requested: no reading at all
    assert imaging.focus_reads == 1
    assert _rows(tmp_path / "focus_log.csv")[-1]["ok"] == "false"


def test_the_before_step_reading_is_bounded_too(tmp_path):
    imaging = _slow_readings(0.2)
    log = FocusLog(tmp_path / "focus_log.csv", imaging, started_at=time.time(), wait_s=0.3)
    log.record({"step": 0, "point": "before"})
    assert 1 <= imaging.focus_reads < 3  # no reading started after wait_s
    (row,) = _rows(tmp_path / "focus_log.csv")
    assert row["ok"] == "true" and row["readings_ok"] == str(imaging.focus_reads)
    assert "time limit 0.3 s" in row["message"]
