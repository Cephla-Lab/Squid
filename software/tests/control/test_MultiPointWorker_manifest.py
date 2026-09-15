"""Transfer-manifest wiring inside MultiPointWorker (large acquisition mode), on a worker built via __new__."""

import os
import queue
from types import SimpleNamespace

import control._def
from control.core.job_processing import JobResult, SaveResult, ZarrWriteResult
from control.core.multi_point_worker import MultiPointWorker
from control.core.transfer_manifest import CompletedUnit, UnitKey


class FakeManifest:
    def __init__(self):
        self.calls = []

    def start(self, **kw):
        self.calls.append(("start", kw))

    def complete(self, path, kind, nbytes, t, region, fov):
        self.calls.append(("complete", path, kind, nbytes, t, region, fov))

    def timepoint_done(self, t):
        self.calls.append(("timepoint_done", t))

    def end(self, reason):
        self.calls.append(("end", reason))


class FakeTracker:
    def __init__(self, incomplete=(), raise_on_feed=False):
        self.fed = []
        self._incomplete = list(incomplete)
        self._raise = raise_on_feed

    def feed(self, result):
        if self._raise:
            raise ValueError("boom")
        self.fed.append(result)

    def incomplete_units(self):
        return self._incomplete


def _make_worker(manifest=None, tracker=None):
    w = MultiPointWorker.__new__(MultiPointWorker)
    w._log = __import__("squid.logging").logging.get_logger("test-worker")
    w._manifest = manifest
    w._completion_tracker = tracker
    w._inline_results = queue.SimpleQueue()
    w._job_runners = []
    w._slack_notifier = None
    w._acquisition_error_count = 0
    w.NZ = 3
    w.selected_configurations = [object(), object()]
    w._region_fov_counts_by_id = {"A1": 4, "B2": 1}
    w.time_point = 1
    w.Nt = 5
    w.experiment_ID = "exp"
    return w


def _save_result(**kw):
    base = dict(time_point=1, region_id="A1", fov=0, z_index=0, channel_idx=0)
    base.update(kw)
    return SaveResult(**base)


def test_mode_off_helpers_are_no_ops():
    w = _make_worker(manifest=None, tracker=None)
    w._manifest_start()
    w._manifest_timepoint_done()
    w._manifest_end("completed")
    w._record_written_file("/nowhere")
    w._feed_completion(_save_result(immediate_paths=("/x",)))
    assert w._summarize_runner_outputs().had_results is False


def test_start_records_format_and_timepoints():
    m = FakeManifest()
    w = _make_worker(manifest=m, tracker=FakeTracker())
    w._manifest_start()
    assert m.calls == [
        ("start", {"experiment_id": "exp", "file_format": control._def.FILE_SAVING_OPTION.name, "nt": 5})
    ]


def test_expected_planes_for_fov_and_region_units():
    w = _make_worker()
    assert w._expected_planes(UnitKey(t=0, region="A1", fov=2)) == 3 * 2
    assert w._expected_planes(UnitKey(t=0, region="A1", fov=None)) == 4 * 3 * 2
    assert w._expected_planes(UnitKey(t=0, region="unknown", fov=None)) == 0


def test_feed_completion_routes_save_results_and_ignores_other_results():
    tracker = FakeTracker()
    w = _make_worker(manifest=FakeManifest(), tracker=tracker)
    save = _save_result(immediate_paths=("/exp/a.tiff",))
    zarr = ZarrWriteResult(fov=0, time_point=1, z_index=0, channel_name="BF")
    w._feed_completion(save)
    w._feed_completion(zarr)
    w._feed_completion(True)
    w._feed_completion(None)
    assert tracker.fed == [save, zarr]


def test_tracker_failure_disables_tracking_but_does_not_raise():
    w = _make_worker(manifest=FakeManifest(), tracker=FakeTracker(raise_on_feed=True))
    w._feed_completion(_save_result(unit_paths=("/exp/u",)))
    assert w._completion_tracker is None
    w._feed_completion(_save_result(unit_paths=("/exp/u",)))  # now a no-op


def test_unit_complete_writes_one_manifest_line_per_path():
    m = FakeManifest()
    w = _make_worker(manifest=m, tracker=FakeTracker())
    w._on_unit_complete(CompletedUnit(t=2, region="A1", fov=3, paths=("/exp/a",), kind="file", nbytes=11))
    w._on_unit_complete(CompletedUnit(t=2, region="A1", fov=None, paths=("/exp/b", "/exp/c"), kind="dir", nbytes=99))
    assert m.calls == [
        ("complete", "/exp/a", "file", 11, 2, "A1", 3),
        ("complete", "/exp/b", "dir", None, 2, "A1", None),
        ("complete", "/exp/c", "dir", None, 2, "A1", None),
    ]


def test_record_written_file_uses_the_file_size_and_current_timepoint(tmp_path):
    m = FakeManifest()
    w = _make_worker(manifest=m, tracker=FakeTracker())
    f = tmp_path / "coordinates.csv"
    f.write_text("x,y\n1,2\n")
    w._record_written_file(str(f))
    w._record_written_file(str(f), region_id=7, fov=2)
    w._record_written_file(str(tmp_path / "missing.bmp"))
    assert m.calls == [
        ("complete", str(f), "file", os.path.getsize(f), 1, None, None),
        ("complete", str(f), "file", os.path.getsize(f), 1, "7", 2),
        ("complete", str(tmp_path / "missing.bmp"), "file", None, 1, None, None),
    ]


def test_timepoint_done_and_end_report_incomplete_units():
    m = FakeManifest()
    w = _make_worker(manifest=m, tracker=FakeTracker(incomplete=[UnitKey(0, "A1", 0)]))
    w._manifest_timepoint_done()
    w._manifest_end("user_abort")
    assert m.calls == [("timepoint_done", 1), ("end", "user_abort")]


def test_inline_results_are_drained_only_when_tracking_is_on():
    tracker = FakeTracker()
    w = _make_worker(manifest=FakeManifest(), tracker=tracker)
    result = _save_result(immediate_paths=("/exp/a.tiff",))
    w._inline_results.put(JobResult(job_id="j1", result=result, exception=None))
    summary = w._summarize_runner_outputs()
    assert summary.had_results and summary.none_failed
    assert tracker.fed == [result]

    w2 = _make_worker(manifest=None, tracker=None)
    w2._inline_results.put(JobResult(job_id="j2", result=result, exception=None))
    assert w2._summarize_runner_outputs().had_results is False, "mode off never touches the inline queue"


def test_timepoint_drain_barrier_waits_for_pending_jobs_then_drains(monkeypatch):
    import itertools

    w = _make_worker(manifest=FakeManifest(), tracker=FakeTracker())
    pending = itertools.chain([2, 1, 0], itertools.repeat(0))
    w._backpressure = SimpleNamespace(get_pending_jobs=lambda: next(pending))
    w._wait_for_outstanding_callback_images = lambda: None
    w.abort_requested_fn = lambda: False
    w._sleep = lambda s: None
    drains = []
    w._summarize_runner_outputs = lambda drain_all=False: drains.append(drain_all)
    w._drain_results_for_timepoint(timeout_s=5.0)
    assert drains == [True, True, True], "one drain per wait iteration plus the final one"


def test_timepoint_drain_barrier_is_bounded_and_abort_aware():
    w = _make_worker(manifest=FakeManifest(), tracker=FakeTracker())
    w._backpressure = SimpleNamespace(get_pending_jobs=lambda: 99)
    w._wait_for_outstanding_callback_images = lambda: None
    w._sleep = lambda s: None
    w._summarize_runner_outputs = lambda drain_all=False: None
    w.abort_requested_fn = lambda: True
    w._drain_results_for_timepoint(timeout_s=5.0)  # returns immediately on abort

    w.abort_requested_fn = lambda: False
    import time as _time

    started = _time.monotonic()
    w._drain_results_for_timepoint(timeout_s=0.0)
    assert _time.monotonic() - started < 1.0


def test_timepoint_drain_barrier_is_a_no_op_when_mode_is_off():
    w = _make_worker(manifest=None, tracker=None)
    w._backpressure = SimpleNamespace(get_pending_jobs=lambda: (_ for _ in ()).throw(AssertionError("must not run")))
    w._drain_results_for_timepoint()
