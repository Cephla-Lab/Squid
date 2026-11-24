"""_image_callback must end the run as an error when a frame's save job cannot be handed off.

The image is lost in each of these cases, so the run must stop (like the callback's other failures) and count
the error, so the end report does not read as if every image was saved.
"""

import threading
from unittest.mock import MagicMock

import numpy as np
import pytest

import control.utils as utils
from control.core.multi_point_worker import MultiPointWorker
from squid.abc import CameraFrame, CameraFrameFormat, CameraPixelFormat


class _Job:
    def __init__(self, run_raises: bool = False):
        self._run_raises = run_raises

    def run(self):
        if self._run_raises:
            raise RuntimeError("synchronous save failed")
        return None


def _worker(job_runner, job=None):
    w = MultiPointWorker.__new__(MultiPointWorker)
    w._log = MagicMock()
    w._timing = utils.TimingManager("test")
    w._ready_for_next_trigger = threading.Event()  # cleared: a trigger was sent, this frame is expected
    w._image_callback_idle = threading.Event()
    w._image_callback_idle.set()
    w._current_capture_info = MagicMock()
    w._timepoint_image_count = 0
    w.image_count = 0
    w._run_state_beat = MagicMock()
    w._first_job_dispatched = True
    w._job_runners = [(_Job, job_runner)]
    w._create_job = lambda job_class, info, image: job if job is not None else _Job()
    w._emit_plate_layout = MagicMock()
    w.callbacks = MagicMock()
    w._acquisition_error_count = 0
    w._abort_cause = None
    aborted = []
    w.request_abort_fn = lambda: aborted.append(True)
    w.abort_requested_fn = lambda: bool(aborted)
    w._run_state_fatal = False
    return w


def _frame():
    return CameraFrame(
        frame_id=1,
        timestamp=0.0,
        frame=np.zeros((4, 4), dtype=np.uint16),
        frame_format=CameraFrameFormat.RAW,
        frame_pixel_format=CameraPixelFormat.MONO16,
    )


def _assert_ended_as_error_with_lost_image(w):
    assert w.abort_requested_fn(), "the run must be aborted"
    assert w._abort_cause == "error"
    assert w._compute_end_reason() == "error"
    assert w._acquisition_error_count == 1, "the lost image must be counted as an error"
    assert w._image_callback_idle.is_set()
    w.callbacks.signal_new_image.assert_not_called()


def test_dispatch_that_raises_aborts_and_counts_the_lost_image():
    runner = MagicMock()
    runner.dispatch.side_effect = RuntimeError("queue full")
    w = _worker(runner)

    w._image_callback(_frame())  # must not raise out of the camera callback

    _assert_ended_as_error_with_lost_image(w)


def test_dispatch_that_returns_false_aborts_and_counts_the_lost_image():
    runner = MagicMock()
    runner.dispatch.return_value = False
    w = _worker(runner)

    w._image_callback(_frame())

    _assert_ended_as_error_with_lost_image(w)


def test_synchronous_job_run_that_raises_aborts_and_counts_the_lost_image():
    w = _worker(job_runner=None, job=_Job(run_raises=True))

    w._image_callback(_frame())

    _assert_ended_as_error_with_lost_image(w)


def test_successful_dispatch_does_not_abort_or_count():
    runner = MagicMock()
    runner.dispatch.return_value = True
    w = _worker(runner)

    w._image_callback(_frame())

    assert not w.abort_requested_fn()
    assert w._acquisition_error_count == 0
    assert w.image_count == 1
    w.callbacks.signal_new_image.assert_called_once()


@pytest.mark.parametrize("dispatch_raises", [True, False])
def test_later_runners_are_not_dispatched_after_a_failure(dispatch_raises):
    first = MagicMock()
    if dispatch_raises:
        first.dispatch.side_effect = RuntimeError("queue full")
    else:
        first.dispatch.return_value = False
    second = MagicMock()
    w = _worker(first)
    w._job_runners = [(_Job, first), (_Job, second)]

    w._image_callback(_frame())

    second.dispatch.assert_not_called()
    assert w._acquisition_error_count == 1
