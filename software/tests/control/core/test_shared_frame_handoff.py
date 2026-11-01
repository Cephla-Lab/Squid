"""Tests for handing frames to a JobRunner subprocess through shared memory (SharedFramePool).

dispatch() used to put the frame itself on the multiprocessing queue. Pickling it there held the GIL for
~8-27 ms per frame and froze the acquisition thread; the frame now goes through a shared-memory slot.
"""

import dataclasses
import pickle
import queue
import time
from dataclasses import dataclass
from multiprocessing import shared_memory

import numpy as np
import pytest

import squid.abc
from control import _def
from control.core.backpressure import create_backpressure_values
from control.core.job_processing import (
    SHARED_FRAME_POOL_SLOTS,
    CaptureInfo,
    Job,
    JobImage,
    JobRunner,
    SharedFramePool,
    _job_image_nbytes,
)
from control.models import AcquisitionChannel, CameraSettings, IlluminationSettings


def make_capture_info() -> CaptureInfo:
    return CaptureInfo(
        position=squid.abc.Pos(x_mm=0.0, y_mm=0.0, z_mm=0.0, theta_rad=None),
        z_index=0,
        capture_time=time.time(),
        configuration=AcquisitionChannel(
            name="BF LED matrix full",
            display_color="#FFFFFF",
            camera=1,
            illumination_settings=IlluminationSettings(illumination_channel="BF LED matrix full", intensity=50.0),
            camera_settings=CameraSettings(exposure_time_ms=10.0, gain_mode=1.0),
            z_offset_um=0.0,
        ),
        save_directory="/tmp/test",
        file_id="test_0_0",
        region_id="A1",
        fov=0,
        configuration_idx=0,
    )


def camera_like_frame(seed: int, raw_size: int = 260, crop: int = 200) -> np.ndarray:
    """A flipped, center-cropped view like the one the camera hands out (not C-contiguous)."""
    raw = np.random.default_rng(seed).integers(0, 4096, size=(raw_size, raw_size), dtype=np.uint16)
    flipped = raw[::-1, ::-1].copy()
    margin = (raw_size - crop) // 2
    frame = flipped[margin : margin + crop, margin : margin + crop]
    assert not frame.flags.c_contiguous
    return frame


@dataclass
class EchoImageJob(Job):
    """Returns the frame the job received, after an optional delay."""

    duration_s: float = 0.0

    def run(self):
        if self.duration_s:
            time.sleep(self.duration_s)
        return self.image_array()


def make_job(frame: np.ndarray, duration_s: float = 0.0) -> EchoImageJob:
    return EchoImageJob(
        capture_info=make_capture_info(), capture_image=JobImage(image_array=frame), duration_s=duration_s
    )


def collect_results(runner: JobRunner, n: int, timeout_s: float = 30.0) -> dict:
    results = {}
    deadline = time.time() + timeout_s
    while len(results) < n and time.time() < deadline:
        result = runner.output_queue().get(timeout=max(0.1, deadline - time.time()))
        results[result.job_id] = result
    return results


def wait_until_idle(runner: JobRunner, timeout_s: float = 10.0) -> None:
    deadline = time.time() + timeout_s
    while runner.has_pending() and time.time() < deadline:
        time.sleep(0.02)


@pytest.fixture
def started_runner():
    runners = []

    def start(**kwargs) -> JobRunner:
        runner = JobRunner(**kwargs)
        runner.daemon = True
        runner.start()
        assert runner.wait_ready(timeout_s=30.0)
        runners.append(runner)
        return runner

    yield start
    for runner in runners:
        runner.shutdown(timeout_s=5.0)


def test_queued_job_carries_a_reference_not_the_pixels():
    runner = JobRunner()
    try:
        frame = camera_like_frame(seed=1)
        job = make_job(frame)

        queued = runner._job_for_queue(job)

        assert queued.capture_image.image_array is None
        assert queued.capture_image.shared_frame is not None
        assert queued.capture_image.shared_frame.nbytes == frame.nbytes
        assert len(pickle.dumps(queued)) < frame.nbytes / 10
        # The caller's job and frame are untouched.
        assert job.capture_image.image_array is frame
        assert job.capture_image.shared_frame is None
    finally:
        runner._frame_pool.close()


def test_frames_arrive_intact_and_slots_are_reused(started_runner):
    runner = started_runner()
    frames = {}
    for i in range(3 * SHARED_FRAME_POOL_SLOTS):
        frame = camera_like_frame(seed=100 + i)
        job = make_job(frame)
        frames[job.job_id] = frame
        assert runner.dispatch(job)

    results = collect_results(runner, len(frames))

    assert set(results) == set(frames)
    for job_id, result in results.items():
        assert result.exception is None
        np.testing.assert_array_equal(result.result, frames[job_id])
    wait_until_idle(runner)
    assert runner._frame_pool.free_slot_count() == SHARED_FRAME_POOL_SLOTS


def test_every_frame_arrives_when_the_pool_runs_out(started_runner):
    # Slow jobs keep the queue deeper than the pool, so later frames fall back to pickling.
    runner = started_runner()
    frames = {}
    for i in range(SHARED_FRAME_POOL_SLOTS + 4):
        frame = camera_like_frame(seed=200 + i)
        job = make_job(frame, duration_s=0.05)
        frames[job.job_id] = frame
        assert runner.dispatch(job)

    results = collect_results(runner, len(frames))

    assert set(results) == set(frames)
    for job_id, result in results.items():
        assert result.exception is None
        np.testing.assert_array_equal(result.result, frames[job_id])
    wait_until_idle(runner)
    assert runner._frame_pool.free_slot_count() == SHARED_FRAME_POOL_SLOTS


def test_backpressure_counters_return_to_zero(started_runner):
    bp_jobs, bp_bytes, bp_event = create_backpressure_values()
    runner = started_runner(bp_pending_jobs=bp_jobs, bp_pending_bytes=bp_bytes, bp_capacity_event=bp_event)
    n = SHARED_FRAME_POOL_SLOTS + 2
    for i in range(n):
        assert runner.dispatch(make_job(camera_like_frame(seed=300 + i), duration_s=0.02))

    collect_results(runner, n)
    wait_until_idle(runner)

    assert bp_jobs.value == 0
    assert bp_bytes.value == 0


def test_full_pool_falls_back_to_a_contiguous_copy():
    runner = JobRunner()
    try:
        pool = runner._frame_pool
        for i in range(SHARED_FRAME_POOL_SLOTS):
            assert pool.put(camera_like_frame(seed=i)) is not None
        frame = camera_like_frame(seed=99)

        queued = runner._job_for_queue(make_job(frame))

        assert queued.capture_image.shared_frame is None
        assert queued.capture_image.image_array.flags.c_contiguous
        np.testing.assert_array_equal(queued.capture_image.image_array, frame)
    finally:
        runner._frame_pool.close()


def test_frame_larger_than_a_slot_is_not_put_in_the_pool():
    pool = SharedFramePool(n_slots=2)
    try:
        assert pool.put(camera_like_frame(seed=1, crop=100)) is not None
        assert pool.put(camera_like_frame(seed=2, crop=200)) is None
        assert pool.free_slot_count() == 1
    finally:
        pool.close()


def test_take_returns_the_frame_and_frees_the_slot():
    pool = SharedFramePool(n_slots=2)
    try:
        frame = camera_like_frame(seed=5)
        ref = pool.put(frame)
        assert pool.free_slot_count() == 1

        np.testing.assert_array_equal(pool.take(ref), frame)
        assert pool.free_slot_count() == 2
    finally:
        pool.close()


def test_take_frees_the_slot_even_when_it_fails():
    pool = SharedFramePool(n_slots=2)
    try:
        ref = pool.put(camera_like_frame(seed=6))
        bad_ref = dataclasses.replace(ref, shm_name="squid_test_no_such_frame_memory")

        with pytest.raises(FileNotFoundError):
            pool.take(bad_ref)
        assert pool.free_slot_count() == 2
    finally:
        pool.close()


def test_backpressure_bytes_are_known_while_the_frame_is_in_shared_memory():
    pool = SharedFramePool(n_slots=1)
    try:
        frame = camera_like_frame(seed=7)
        job = EchoImageJob(capture_info=make_capture_info(), capture_image=JobImage(None, shared_frame=pool.put(frame)))
        assert _job_image_nbytes(job) == frame.nbytes
    finally:
        pool.close()


def test_switch_off_restores_pickling(monkeypatch):
    monkeypatch.setattr(_def.Acquisition, "USE_SHARED_MEMORY_FRAME_HANDOFF", False)
    runner = JobRunner()
    frame = camera_like_frame(seed=8)

    queued = runner._job_for_queue(make_job(frame))

    assert runner._frame_pool is None
    assert queued.capture_image.shared_frame is None
    np.testing.assert_array_equal(queued.capture_image.image_array, frame)


def test_close_in_a_process_that_did_not_create_the_memory_does_not_unlink_it():
    # A forked JobRunner subprocess inherits the pool; its close() must leave the parent's memory alone.
    pool = SharedFramePool(n_slots=1)
    ref = pool.put(camera_like_frame(seed=9))
    keep = shared_memory.SharedMemory(name=ref.shm_name)  # on Windows the memory lives while any handle is open
    try:
        pool._owner_pid = -1
        pool.close()

        still_there = shared_memory.SharedMemory(name=ref.shm_name)
        still_there.close()
    finally:
        keep.close()
        keep.unlink()


def test_failure_preparing_the_frame_rolls_the_counters_back(monkeypatch):
    # Pool off, so the frame takes the contiguous-copy path, and that copy runs out of memory.
    monkeypatch.setattr(_def.Acquisition, "USE_SHARED_MEMORY_FRAME_HANDOFF", False)
    bp_jobs, bp_bytes, bp_event = create_backpressure_values()
    runner = JobRunner(bp_pending_jobs=bp_jobs, bp_pending_bytes=bp_bytes, bp_capacity_event=bp_event)

    def out_of_memory(*args, **kwargs):
        raise MemoryError("simulated")

    monkeypatch.setattr(np, "ascontiguousarray", out_of_memory)

    with pytest.raises(MemoryError):
        runner.dispatch(make_job(camera_like_frame(seed=10)))

    assert not runner.has_pending()
    assert bp_jobs.value == 0
    assert bp_bytes.value == 0


def test_failure_after_claiming_a_slot_frees_it(monkeypatch):
    runner = JobRunner()
    try:

        def queue_full(job):
            raise queue.Full

        monkeypatch.setattr(runner._input_queue, "put_nowait", queue_full)

        with pytest.raises(queue.Full):
            runner.dispatch(make_job(camera_like_frame(seed=11)))

        assert not runner.has_pending()
        assert runner._frame_pool.free_slot_count() == SHARED_FRAME_POOL_SLOTS
    finally:
        runner._frame_pool.close()
