"""Transfer-manifest wiring inside MultiPointWorker (large acquisition mode), on a worker built via __new__."""

import os
import queue
import time
from types import SimpleNamespace

import control._def
from control.core.job_processing import JobResult, SaveResult, ZarrWriteResult
from control.core.multi_point_worker import MultiPointWorker
from control.core.pause_gate import PauseGate
from control.core.pending_outputs import FinishedOutputs
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
    w._outstanding_save_job_ids = set()
    w.callbacks = SimpleNamespace(
        wait_for_pending_outputs=lambda t, s: FinishedOutputs((), True),
        when_pending_outputs_settle=lambda fn: fn(FinishedOutputs((), True)),
    )
    w._run_state = SimpleNamespace(beat=lambda progress=None, force=False: None, set_status=lambda s: None)
    w._timepoint_fov_count = 0
    w.image_count = 0
    w._inline_results = queue.SimpleQueue()
    w._job_runners = []
    w._slack_notifier = None
    w._acquisition_error_count = 0
    w.NZ = 3
    w.selected_configurations = [object(), object()]
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


def test_expected_planes_is_z_levels_times_channels():
    w = _make_worker()
    assert w._expected_planes(UnitKey(t=0, region="A1", fov=2)) == 3 * 2


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


def test_unit_complete_lists_files_with_their_on_disk_size_and_expands_directories(tmp_path):
    m = FakeManifest()
    w = _make_worker(manifest=m, tracker=FakeTracker())
    stack = tmp_path / "A1_0003_stack.tiff"
    stack.write_bytes(b"header" + b"\0" * 100)  # a multi-plane file is bigger than its summed pixel bytes
    chunk_dir = tmp_path / "fov_0.ome.zarr" / "0" / "c" / "2"
    (chunk_dir / "0" / "0").mkdir(parents=True)
    (chunk_dir / "1" / "0").mkdir(parents=True)
    (chunk_dir / "0" / "0" / "0").write_bytes(b"\0" * 7)
    (chunk_dir / "1" / "0" / "0").write_bytes(b"\0" * 9)
    w._on_unit_complete(CompletedUnit(t=2, region="A1", fov=3, paths=(str(stack),), kind="file", nbytes=100))
    w._on_unit_complete(CompletedUnit(t=2, region="A1", fov=0, paths=(str(chunk_dir),), kind="dir", nbytes=99))
    w._on_unit_complete(CompletedUnit(t=2, region="A1", fov=4, paths=(str(tmp_path / "gone"),), kind="file", nbytes=5))
    assert m.calls == [
        ("complete", str(stack), "file", 106, 2, "A1", 3),
        # a chunk directory is listed file by file: the manifest is the inventory verify checks
        ("complete", str(chunk_dir / "0" / "0" / "0"), "file", 7, 2, "A1", 0),
        ("complete", str(chunk_dir / "1" / "0" / "0"), "file", 9, 2, "A1", 0),
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


def test_timepoint_drain_barrier_waits_until_every_save_result_has_been_drained():
    w = _make_worker(manifest=FakeManifest(), tracker=FakeTracker())
    w._wait_for_outstanding_callback_images = lambda: None
    w.abort_requested_fn = lambda: False
    w._sleep = lambda s: None
    w._outstanding_save_job_ids = {"a", "b"}
    deliveries = [[], ["a"], [], ["b"]]  # results cross the multiprocessing queue late and one at a time
    drains = []

    def summarize(drain_all=False):
        drains.append(drain_all)
        for job_id in deliveries.pop(0) if deliveries else []:
            w._outstanding_save_job_ids.discard(job_id)
        return SimpleNamespace(none_failed=True, had_results=True)

    w._summarize_runner_outputs = summarize
    assert w._drain_results_for_timepoint(timeout_s=5.0) is True
    assert drains == [True] * 5, "four polls until both results arrived, plus the final drain"


def test_timepoint_drain_barrier_is_bounded_and_abort_aware():
    w = _make_worker(manifest=FakeManifest(), tracker=FakeTracker())
    w._outstanding_save_job_ids = {"never-delivered"}
    w._wait_for_outstanding_callback_images = lambda: None
    w._sleep = lambda s: None
    w._summarize_runner_outputs = lambda drain_all=False: SimpleNamespace(none_failed=True, had_results=False)
    w.abort_requested_fn = lambda: True
    assert w._drain_results_for_timepoint(timeout_s=5.0) is False  # returns immediately on abort

    w.abort_requested_fn = lambda: False
    import time as _time

    started = _time.monotonic()
    assert w._drain_results_for_timepoint(timeout_s=0.0) is False
    assert _time.monotonic() - started < 1.0


def test_a_result_still_in_the_queue_keeps_the_timepoint_open_even_with_no_pending_jobs():
    """The subprocess drops its pending-job counter before the result has crossed the queue. Going
    through the real summarizer: the barrier ends only once that result has actually been drained."""
    tracker = FakeTracker()
    w = _make_worker(manifest=FakeManifest(), tracker=tracker)
    w._wait_for_outstanding_callback_images = lambda: None
    w.abort_requested_fn = lambda: False
    runner = _QueueRunner([])
    w._job_runners = [(object, runner)]
    w._outstanding_save_job_ids = {"s1"}
    polls = []

    def sleep(_s):  # the result shows up in the queue only after a few polls
        polls.append(1)
        if len(polls) == 3:
            runner.output_queue().put(_ok("s1", "/exp/a"))

    w._sleep = sleep
    assert w._drain_results_for_timepoint(timeout_s=5.0) is True
    assert [r.immediate_paths[0] for r in tracker.fed] == ["/exp/a"]
    assert len(polls) >= 3


def test_timepoint_drain_barrier_is_a_no_op_when_mode_is_off():
    w = _make_worker(manifest=None, tracker=None)
    w._drain_results_for_timepoint()


def test_timepoint_drain_applies_the_abort_on_failed_job_policy():
    w = _make_worker(manifest=FakeManifest(), tracker=FakeTracker())
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


def test_timepoint_done_is_emitted_only_when_the_barrier_completed():
    m = FakeManifest()
    w = _make_worker(manifest=m, tracker=FakeTracker())
    w._wait_for_outstanding_callback_images = lambda: None
    w._sleep = lambda s: None
    w._summarize_runner_outputs = lambda drain_all=False: SimpleNamespace(none_failed=True, had_results=True)
    w.abort_requested_fn = lambda: False

    assert w._drain_results_for_timepoint(timeout_s=1.0) is True

    w._outstanding_save_job_ids = {"x", "y", "z"}
    assert w._drain_results_for_timepoint(timeout_s=0.0) is False, "saves still pending: no marker"

    w2 = _make_worker(manifest=None, tracker=None)
    assert w2._drain_results_for_timepoint() is False


def test_manifest_end_waits_for_leftover_outputs_and_lists_them_under_their_own_timepoint(tmp_path):
    m = FakeManifest()
    w = _make_worker(manifest=m, tracker=FakeTracker(incomplete=[]))
    w.time_point = 4  # the run ended at timepoint 4; the leftover mosaic belongs to timepoint 2
    mosaic_dir = tmp_path / "2" / "mosaic_view"
    mosaic_dir.mkdir(parents=True)
    (mosaic_dir / "mosaic_BF_10um.ome.tiff").write_bytes(b"\0" * 12)
    (mosaic_dir / "mosaic_BF_10um.yaml").write_bytes(b"a: 1\n")
    waits = []

    def wait_for_pending_outputs(time_point, timeout_s):
        waits.append((time_point, timeout_s))
        return FinishedOutputs(((2, str(mosaic_dir)),), True)

    w.callbacks = SimpleNamespace(wait_for_pending_outputs=wait_for_pending_outputs)
    w._manifest_end("completed")

    assert waits == [(None, w._PENDING_OUTPUTS_TIMEOUT_S)]
    assert m.calls == [
        ("complete", str(mosaic_dir / "mosaic_BF_10um.ome.tiff"), "file", 12, 2, None, None),
        ("complete", str(mosaic_dir / "mosaic_BF_10um.yaml"), "file", 5, 2, None, None),
        ("end", "completed"),
    ], "outputs are listed before the end record, with the timepoint they belong to"


def test_a_failing_outputs_callback_defers_end_and_never_raises():
    def boom(*args):
        raise RuntimeError("gui gone")

    # The wait fails, so completion cannot be established: end goes through the deferred path.
    m = FakeManifest()
    w = _make_worker(manifest=m, tracker=FakeTracker())
    w.callbacks = SimpleNamespace(
        wait_for_pending_outputs=boom, when_pending_outputs_settle=lambda fn: fn(FinishedOutputs((), True))
    )
    w._manifest_end("completed")
    assert m.calls == [("end", "completed")]

    # If even that fails the manifest stays open: the mover then never sweeps unlisted files (safe side).
    m2 = FakeManifest()
    w2 = _make_worker(manifest=m2, tracker=FakeTracker())
    w2.callbacks = SimpleNamespace(wait_for_pending_outputs=boom, when_pending_outputs_settle=boom)
    w2._manifest_end("completed")
    assert m2.calls == []


def test_list_pending_outputs_reports_whether_the_timepoint_is_fully_listed(tmp_path):
    m = FakeManifest()
    w = _make_worker(manifest=m, tracker=FakeTracker())
    out = tmp_path / "1" / "mosaic_view"
    out.mkdir(parents=True)
    (out / "mosaic.yaml").write_bytes(b"ok\n")
    w.callbacks = SimpleNamespace(wait_for_pending_outputs=lambda t, s: FinishedOutputs(((1, str(out)),), True))
    assert w._list_pending_outputs(1, 5.0) is True
    assert m.calls == [("complete", str(out / "mosaic.yaml"), "file", 3, 1, None, None)]

    w.callbacks = SimpleNamespace(wait_for_pending_outputs=lambda t, s: FinishedOutputs((), False))
    assert w._list_pending_outputs(1, 5.0) is False, "an unanswered or unfinished writer: no timepoint_done"

    w_off = _make_worker(manifest=None, tracker=None)
    w_off.callbacks = SimpleNamespace(
        wait_for_pending_outputs=lambda t, s: (_ for _ in ()).throw(AssertionError("mode off must never wait"))
    )
    assert w_off._list_pending_outputs(1, 5.0) is False


def test_end_is_deferred_while_an_outside_writer_is_still_running(tmp_path):
    m = FakeManifest()
    w = _make_worker(manifest=m, tracker=FakeTracker(incomplete=[]))
    out = tmp_path / "3" / "mosaic_view"
    deferred = []
    w.callbacks = SimpleNamespace(
        wait_for_pending_outputs=lambda t, s: FinishedOutputs((), False),  # the 60 s wait ran out
        when_pending_outputs_settle=deferred.append,
    )
    w._manifest_end("completed")
    assert m.calls == [], "no end record while a mosaic save may still be writing"
    assert len(deferred) == 1

    out.mkdir(parents=True)
    (out / "mosaic.yaml").write_bytes(b"late\n")
    deferred[0](FinishedOutputs(((3, str(out)),), True))  # the writer finally finished
    assert m.calls == [
        ("complete", str(out / "mosaic.yaml"), "file", 5, 3, None, None),
        ("end", "completed"),
    ]


def test_pause_tick_lists_outputs_that_finished_late_without_blocking(tmp_path):
    m = FakeManifest()
    w = _make_worker(manifest=m, tracker=FakeTracker())
    w._pause_gate = PauseGate()
    w._disk_guard = None
    w._last_disk_check_mono = time.monotonic()
    w._summarize_runner_outputs = lambda drain_all=False: SimpleNamespace(none_failed=True, had_results=False)
    w.abort_requested_fn = lambda: False
    out = tmp_path / "0" / "mosaic_view"
    out.mkdir(parents=True)
    (out / "mosaic.yaml").write_bytes(b"ok\n")
    asked = []

    def wait_for_pending_outputs(time_point, timeout_s):
        asked.append((time_point, timeout_s))
        return FinishedOutputs(((0, str(out)),), True)

    w.callbacks = SimpleNamespace(wait_for_pending_outputs=wait_for_pending_outputs)
    w._pause_tick()
    assert asked == [(None, 0.0)], "a non-blocking sweep of everything left"
    assert m.calls == [("complete", str(out / "mosaic.yaml"), "file", 3, 0, None, None)]
