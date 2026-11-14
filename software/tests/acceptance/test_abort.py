"""
Acceptance scenario 3: abort mid-acquisition, then re-acquire in the same process.

Drives MultiPointController directly against a simulated microscope (no GUI). A
multi-FOV / multi-channel / multi-Z acquisition is started with enough runway
that a mid-flight abort lands after the first image but before completion. We
then assert:

  * abort stops the run cleanly (finished callback fires, acquisition_in_progress
    drops, fewer than the full image count were produced),
  * the same process/harness can immediately run a second, smaller acquisition to
    completion, and
  * once the harness is closed, no orphaned JobRunner subprocesses linger.

Context (PR #582): the app previously leaked orphaned JobRunner ``spawn_main``
subprocesses because ``os._exit()`` skipped multiprocessing's atexit hook.
Per-acquisition cleanup now shuts runners down in non-blocking daemon threads;
this test pins that no JobRunner survives ``close()`` at the acceptance level.
"""

import multiprocessing
import time

import pytest

from control.core.job_processing import JobRunner

from tests.acceptance.harness import make_harness, timepoint_dir, list_image_files

pytestmark = pytest.mark.acceptance


def _wait_until(predicate, timeout_s: float, interval_s: float = 0.2) -> bool:
    """Poll ``predicate`` until it is truthy or the deadline passes."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return predicate()


def test_abort_mid_acquisition_then_reacquire(tmp_path, acquisition_defaults):
    h = make_harness()
    tracker = h.tracker
    try:
        # ---- Run 1: large enough to guarantee abort lands mid-flight ----------
        # 3x3 FOVs x 2 channels x NZ=2 = 36 images of runway.
        h.new_experiment(tmp_path, "abort_run")
        h.add_fov_grid("region0", nx=3, ny=3)
        h.select_channels(2)
        h.mpc.set_NZ(2)
        expected_full = 3 * 3 * 2 * 2
        assert expected_full == 36

        h.mpc.run_acquisition()

        assert tracker.started_event.wait(60), "run 1 did not start"
        assert tracker.first_image_event.wait(120), "run 1 produced no image before abort"

        # Abort mid-flight (real API misspelling: request_abort_aquisition).
        h.mpc.request_abort_aquisition()

        assert tracker.finished_event.wait(120), "abort did not fire finished callback"
        # acquisition_in_progress() reflects the worker thread and may briefly lag
        # the finished callback, so poll it down rather than asserting immediately.
        assert _wait_until(
            lambda: not h.mpc.acquisition_in_progress(), timeout_s=30
        ), "acquisition_in_progress stayed True after abort"

        aborted_count = tracker.image_count
        assert aborted_count < expected_full, (
            f"abort produced {aborted_count} images; expected fewer than {expected_full} "
            "(abort did not land mid-flight -- increase the grid)"
        )
        assert aborted_count >= 1, "expected at least one image before abort"

        # Partial artifacts are allowed; only pin what is stable: the first
        # timepoint directory was created for the partial run.
        assert timepoint_dir(h.experiment_dir, 0).is_dir(), "run 1 timepoint dir 0/ was not created"

        # ---- Run 2: second experiment in the same process/harness -------------
        # The tracker events are latched from run 1; clear them and remember the
        # run-1 image count so we can assert run 2 emits exactly 2 new callbacks.
        tracker.started_event.clear()
        tracker.finished_event.clear()
        tracker.first_image_event.clear()
        count_before_run2 = tracker.image_count

        h.mpc.scanCoordinates.clear_regions()
        h.new_experiment(tmp_path, "after_abort")
        h.add_fov_grid("region0", nx=2, ny=1)
        h.select_channels(1)
        h.mpc.set_NZ(1)

        h.run_and_wait(timeout_s=300)

        new_callbacks = tracker.image_count - count_before_run2
        assert new_callbacks == 2, (
            f"run 2 emitted {new_callbacks} new-image callbacks, expected 2 " "(2 FOVs x 1 channel x NZ=1)"
        )

        run2_tp0 = timepoint_dir(h.experiment_dir, 0)
        run2_images = list_image_files(run2_tp0)
        assert len(run2_images) == 2, f"run 2 timepoint 0/ has {run2_images}, expected 2 files"
        assert (h.experiment_dir / ".done").exists(), "run 2 .done marker missing"

    finally:
        h.close()

    # ---- Orphan assertion (after close) --------------------------------------
    # Formulation: type-based (isinstance(child, JobRunner)) rather than a raw
    # active_children() count. JobRunner is a multiprocessing.Process subclass, so
    # in the parent process a leaked runner shows up as a JobRunner instance in
    # active_children(). This is more robust than a count baseline because:
    #   (a) the MultiPointController PRE-WARMS a JobRunner at construction and
    #       after each acquisition start, so a live runner is EXPECTED while the
    #       harness is open -- a count taken "before the test" would need careful
    #       timing to exclude it, whereas after close() the invariant is simply
    #       "zero JobRunners", and
    #   (b) type-matching ignores unrelated children the test framework or other
    #       fixtures might spawn, targeting exactly the leak PR #582 is about.
    def _no_job_runners() -> bool:
        return not [c for c in multiprocessing.active_children() if isinstance(c, JobRunner)]

    assert _wait_until(_no_job_runners, timeout_s=15), (
        "JobRunner subprocess(es) still alive after close(): "
        f"{[c for c in multiprocessing.active_children() if isinstance(c, JobRunner)]}"
    )
