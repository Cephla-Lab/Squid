"""Transfer-manifest wiring inside MultiPointWorker (large acquisition mode), on a worker built via __new__."""

import os
import queue
import time
from types import SimpleNamespace

import control._def
from control.core.job_processing import JobResult, SaveResult, ZarrWriteResult
from control.core.multi_point_worker import MultiPointWorker
from control.core.pause_gate import PauseGate
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
    w._large_acquisition_mode = tracker is not None
    w._abort_on_failed_job = True
    w._run_state = SimpleNamespace(beat=lambda progress=None, force=False: None, set_status=lambda s: None)
    w._timepoint_fov_count = 0
    w.image_count = 0
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


def test_unit_complete_writes_one_manifest_line_per_path_with_the_on_disk_size(tmp_path):
    m = FakeManifest()
    w = _make_worker(manifest=m, tracker=FakeTracker())
    stack = tmp_path / "A1_0003_stack.tiff"
    stack.write_bytes(b"header" + b"\0" * 100)  # a multi-plane file is bigger than its summed pixel bytes
    w._on_unit_complete(CompletedUnit(t=2, region="A1", fov=3, paths=(str(stack),), kind="file", nbytes=100))
    w._on_unit_complete(CompletedUnit(t=2, region="A1", fov=None, paths=("/exp/b", "/exp/c"), kind="dir", nbytes=99))
    w._on_unit_complete(CompletedUnit(t=2, region="A1", fov=4, paths=(str(tmp_path / "gone"),), kind="file", nbytes=5))
    assert m.calls == [
        ("complete", str(stack), "file", 106, 2, "A1", 3),
        ("complete", "/exp/b", "dir", None, 2, "A1", None),
        ("complete", "/exp/c", "dir", None, 2, "A1", None),
        ("complete", str(tmp_path / "gone"), "file", None, 2, "A1", 4),
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
    w._summarize_runner_outputs = lambda drain_all=False: (
        drains.append(drain_all),
        SimpleNamespace(none_failed=True, had_results=True),
    )[1]
    w._drain_results_for_timepoint(timeout_s=5.0)
    assert drains == [True, True, True], "one drain per wait iteration plus the final one"


def test_timepoint_drain_barrier_is_bounded_and_abort_aware():
    w = _make_worker(manifest=FakeManifest(), tracker=FakeTracker())
    w._backpressure = SimpleNamespace(get_pending_jobs=lambda: 99)
    w._wait_for_outstanding_callback_images = lambda: None
    w._sleep = lambda s: None
    w._summarize_runner_outputs = lambda drain_all=False: SimpleNamespace(none_failed=True, had_results=False)
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


def test_timepoint_drain_applies_the_abort_on_failed_job_policy():
    w = _make_worker(manifest=FakeManifest(), tracker=FakeTracker())
    w._backpressure = SimpleNamespace(get_pending_jobs=lambda: 0)
    w._wait_for_outstanding_callback_images = lambda: None
    w._summarize_runner_outputs = lambda drain_all=False: SimpleNamespace(none_failed=False, had_results=True)
    aborts = []
    w._abort_due_to_error = lambda: aborts.append(1)
    w.abort_requested_fn = lambda: False

    w._drain_results_for_timepoint(timeout_s=1.0)
    assert aborts == [1], "a save failure drained at the timepoint barrier must abort like one seen in the FOV loop"

    aborts.clear()
    w._abort_on_failed_job = False
    w._drain_results_for_timepoint(timeout_s=1.0)
    assert aborts == []


def test_pause_tick_drains_results_every_tick_and_applies_the_abort_policy():
    w = _make_worker(manifest=FakeManifest(), tracker=FakeTracker())
    w._pause_gate = PauseGate()
    w._disk_guard = None
    w._last_disk_check_mono = time.monotonic()  # the disk re-check is not due; draining must not depend on it
    outcomes = iter(
        [SimpleNamespace(none_failed=True, had_results=True), SimpleNamespace(none_failed=False, had_results=True)]
    )
    drains = []
    w._summarize_runner_outputs = lambda drain_all=False: (drains.append(drain_all), next(outcomes))[1]
    aborts = []
    w._abort_due_to_error = lambda: aborts.append(1)
    w.abort_requested_fn = lambda: False

    w._pause_tick()
    assert drains == [True] and aborts == []
    w._pause_tick()
    assert drains == [True, True] and aborts == [1]


def test_pause_tick_never_drains_when_mode_is_off():
    w = _make_worker(manifest=None, tracker=None)
    w._disk_guard = None
    w._summarize_runner_outputs = lambda drain_all=False: (_ for _ in ()).throw(AssertionError("must not drain"))
    w._pause_tick()


class _QueueRunner:
    """Stand-in for a JobRunner: only output_queue() is used by the drain."""

    def __init__(self, results):
        self._queue = queue.Queue()
        for r in results:
            self._queue.put(r)

    def output_queue(self):
        return self._queue


def _failed(job_id):
    return JobResult(job_id=job_id, result=None, exception=RuntimeError("disk error"))


def _ok(job_id, path):
    return JobResult(job_id=job_id, result=_save_result(immediate_paths=(path,), bytes_written=1), exception=None)


def test_a_failed_result_does_not_hide_the_successes_behind_it_in_the_runner_queue():
    tracker = FakeTracker()
    w = _make_worker(manifest=FakeManifest(), tracker=tracker)
    w._job_runners = [
        (object, _QueueRunner([_failed("f1"), _ok("s1", "/exp/a"), _ok("s2", "/exp/b"), _ok("s3", "/exp/c")]))
    ]

    summary = w._summarize_runner_outputs(drain_all=True)

    assert summary.had_results and summary.none_failed is False
    assert [r.immediate_paths[0] for r in tracker.fed] == ["/exp/a", "/exp/b", "/exp/c"], "every result is processed"
    assert w._acquisition_error_count == 1
    assert w._job_runners[0][1].output_queue().empty()


def test_a_failed_result_does_not_hide_the_successes_behind_it_in_the_inline_queue():
    tracker = FakeTracker()
    w = _make_worker(manifest=FakeManifest(), tracker=tracker)
    for item in (_failed("f1"), _ok("s1", "/exp/a"), _ok("s2", "/exp/b")):
        w._inline_results.put(item)

    summary = w._summarize_runner_outputs()

    assert summary.none_failed is False
    assert [r.immediate_paths[0] for r in tracker.fed] == ["/exp/a", "/exp/b"]
    assert w._acquisition_error_count == 1
