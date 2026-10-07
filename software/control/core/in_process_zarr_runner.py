"""Zarr v3 saving in the acquisition process.

tensorstore compresses and writes on its own threads and hands back a future, so a save
subprocess buys nothing for Zarr and costs a copy of every image through a pipe. It also
cannot be fork()ed once this process has used tensorstore (the Record + Z-Stack recording
writer does), because tensorstore aborts such a child on its first use. This runner presents
the part of JobRunner's interface the workers use and runs SaveZarrJob here instead: dispatch
submits the write and returns; a drain thread waits for each write in order, keeps the
backpressure counters, and puts the JobResult on the output queue the worker polls.
"""

import queue
import threading
from typing import Any, Optional, Tuple

import squid.logging
from control.core.backpressure import BackpressureValues
from control.core.job_processing import AcquisitionInfo, JobResult, SaveZarrJob, ZarrWriterInfo, ZarrWriterRegistry

_SENTINEL = object()


class InProcessZarrRunner:
    runs_in_process = True

    def __init__(
        self,
        zarr_writer_info: Optional[ZarrWriterInfo] = None,
        bp_values: Optional[BackpressureValues] = None,
    ):
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._zarr_writer_info = zarr_writer_info
        self._bp_pending_jobs, self._bp_pending_bytes, self._bp_capacity_event = bp_values or (None, None, None)
        self.registry = ZarrWriterRegistry()
        self._submitted: "queue.Queue" = queue.Queue()  # (job, future, image_bytes, result), in submission order
        self._output: "queue.Queue" = queue.Queue()
        self._pending = 0
        self._pending_lock = threading.Lock()
        self._thread = threading.Thread(target=self._drain, name="InProcessZarrRunner", daemon=True)
        self._started = False
        self._finished = False
        self._killed = False

    # ---- lifecycle, as JobRunner has it

    def start(self) -> None:
        self._thread.start()
        self._started = True

    def is_ready(self) -> bool:
        return self._started and not self._finished

    def wait_ready(self, timeout_s: float = 5.0) -> bool:
        return self.is_ready()

    def is_alive(self) -> bool:
        return self._started and self._thread.is_alive()

    def set_acquisition_info(self, acquisition_info: Optional[AcquisitionInfo]) -> None:
        """Accepted for interface parity; SaveZarrJob does not use it."""

    def set_zarr_writer_info(self, zarr_writer_info: ZarrWriterInfo) -> None:
        self._zarr_writer_info = zarr_writer_info

    # ---- the hot path: called from the camera frame callback

    def dispatch(self, job: SaveZarrJob) -> bool:
        if self._zarr_writer_info is None:
            raise ValueError("Cannot dispatch SaveZarrJob: InProcessZarrRunner has no zarr_writer_info")
        job.zarr_writer_info = self._zarr_writer_info
        job.registry = self.registry
        image_bytes = job.capture_image.image_array.nbytes if job.capture_image.image_array is not None else 0
        self._count_dispatched(image_bytes)
        try:
            future, result = job.submit()
        except Exception as e:
            # Same outcome as a failed write: the worker sees the exception on the output queue.
            self._log.exception(f"Job {job.job_id} could not be submitted")
            self._submitted.put_nowait((job, _FailedFuture(e), image_bytes, None))
            return True
        self._submitted.put_nowait((job, future, image_bytes, result))
        return True

    def output_queue(self) -> "queue.Queue":
        return self._output

    def has_pending(self) -> bool:
        with self._pending_lock:
            return self._pending > 0

    def kill(self) -> None:
        """Stop tracking the outstanding writes; shutdown() then seals the stores as aborted."""
        self._killed = True

    def shutdown(self, timeout_s: float = 1.0, aborted: bool = False) -> None:
        """Wait for the outstanding writes, then seal every store.

        Complete when the acquisition ran to its end; `acquisition_complete: False, aborted: True`
        when it was aborted (by the user or by an error) or after kill(). Captured frames are
        written either way.
        """
        if not self._started or self._finished:
            return
        self._finished = True
        self._submitted.put_nowait(_SENTINEL)
        self._thread.join(timeout=max(timeout_s, 1.0))
        if self._thread.is_alive():
            self._log.warning("drain thread still waiting on a write after the shutdown timeout; sealing anyway")
        if self._killed or aborted:
            self.registry.abort_all()
        elif not self.registry.finalize_all():
            self._log.error("ZARR FINALIZATION INCOMPLETE - Some data may not be saved correctly")

    # ---- the drain thread

    def _drain(self) -> None:
        while True:
            item = self._submitted.get()
            if item is _SENTINEL:
                return
            job, future, image_bytes, result = item
            try:
                if future is not None:
                    future.result()
                self._output.put_nowait(JobResult(job_id=job.job_id, result=result, exception=None))
            except Exception as e:
                self._log.exception(f"Job {job.job_id} failed! Returning exception result.")
                self._output.put_nowait(JobResult(job_id=job.job_id, result=None, exception=e))
            finally:
                self._count_completed(image_bytes)

    def _count_dispatched(self, image_bytes: int) -> None:
        with self._pending_lock:
            self._pending += 1
        if self._bp_pending_jobs is not None:
            with self._bp_pending_jobs.get_lock():
                self._bp_pending_jobs.value += 1
            with self._bp_pending_bytes.get_lock():
                self._bp_pending_bytes.value += image_bytes

    def _count_completed(self, image_bytes: int) -> None:
        with self._pending_lock:
            self._pending -= 1
        if self._bp_pending_jobs is not None:
            with self._bp_pending_jobs.get_lock():
                self._bp_pending_jobs.value = max(0, self._bp_pending_jobs.value - 1)
            with self._bp_pending_bytes.get_lock():
                self._bp_pending_bytes.value = max(0, self._bp_pending_bytes.value - image_bytes)
            if self._bp_capacity_event is not None:
                self._bp_capacity_event.set()


class _FailedFuture:
    """A write that failed before it was submitted, reported through the same path as a failed write."""

    def __init__(self, error: Exception):
        self._error = error

    def result(self) -> Any:
        raise self._error
