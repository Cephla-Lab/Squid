"""Zarr v3 saving in the acquisition process.

tensorstore compresses and writes on its own threads and hands back a future, so a save
subprocess buys nothing for Zarr and costs a copy of every image through a pipe. It also
cannot be fork()ed once this process has used tensorstore (the NDViewer does; a recording
writer will), because tensorstore aborts such a child on its first use. This runner presents
the part of JobRunner's interface the workers use and runs SaveZarrJob here instead: dispatch
submits the write and returns; a drain thread waits for each write in order, keeps the
backpressure counters, and puts the JobResult on the output queue the worker polls.
"""

import concurrent.futures
import queue
import threading
from typing import Optional

import squid.logging
from control.core.backpressure import BackpressureValues, note_job_completed, note_job_dispatched
from control.core.job_processing import AcquisitionInfo, JobResult, SaveZarrJob, ZarrWriterInfo, ZarrWriterRegistry

_SENTINEL = object()


class InProcessZarrRunner:
    def __init__(
        self,
        zarr_writer_info: Optional[ZarrWriterInfo] = None,
        bp_values: Optional[BackpressureValues] = None,
    ):
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._zarr_writer_info = zarr_writer_info
        self._bp_pending_jobs, self._bp_pending_bytes, self._bp_capacity_event = bp_values or (None, None, None)
        self.registry = ZarrWriterRegistry()
        self._submitted: "queue.Queue" = queue.Queue()  # (job_id, future, image_bytes, result), in submission order
        self._output: "queue.Queue" = queue.Queue()
        # Counted up before the submit, as JobRunner does, so has_pending() never says False mid-flight.
        self._pending = 0
        self._pending_lock = threading.Lock()
        # dispatch and shutdown exclude each other, so no job lands behind the drain thread's sentinel
        self._state_lock = threading.Lock()
        self._thread = threading.Thread(target=self._drain, name="InProcessZarrRunner", daemon=True)
        self._started = False
        self._finished = False
        self._aborted = False

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
        """Submit the write and return True; return False, leaving the job untouched, once shutdown() has run."""
        with self._state_lock:
            if self._finished:
                self._log.warning(f"Job {job.job_id} dispatched after shutdown; refusing it")
                return False
            if self._zarr_writer_info is None:
                raise ValueError("Cannot dispatch SaveZarrJob: InProcessZarrRunner has no zarr_writer_info")
            job.zarr_writer_info = self._zarr_writer_info
            job.registry = self.registry
            image_bytes = job.capture_image.image_array.nbytes if job.capture_image.image_array is not None else 0
            with self._pending_lock:
                self._pending += 1
            note_job_dispatched(self._bp_pending_jobs, self._bp_pending_bytes, image_bytes)
            try:
                future, result = job.submit()
            except Exception as e:
                # Same outcome as a failed write: the worker sees the exception on the output queue.
                self._log.exception(f"Job {job.job_id} could not be submitted")
                future, result = concurrent.futures.Future(), None
                future.set_exception(e)
            # Only the id goes to the drain thread: tensorstore has copied the frame, so nothing keeps it alive.
            self._submitted.put_nowait((job.job_id, future, image_bytes, result))
            return True

    def output_queue(self) -> "queue.Queue":
        return self._output

    def has_pending(self) -> bool:
        with self._pending_lock:
            return self._pending > 0

    def kill(self) -> None:
        """Mark the run aborted; shutdown() then seals the stores as aborted."""
        self._aborted = True

    def shutdown(self, timeout_s: float = 1.0, aborted: bool = False) -> bool:
        """Wait for the outstanding writes, then seal every store. Returns False if a write was still in flight.

        Complete when the acquisition ran to its end; `acquisition_complete: False, aborted: True`
        when it was aborted (by the user or by an error), after kill(), or when the writes did not
        finish within the timeout (a store is never stamped complete over a write still in flight).
        Captured frames are written either way.
        """
        with self._state_lock:
            if not self._started or self._finished:
                return True
            self._finished = True
            self._submitted.put_nowait(_SENTINEL)
        self._thread.join(timeout=max(timeout_s, 1.0))
        drained = not self._thread.is_alive()
        if not drained:
            self._log.error("a Zarr write is still in flight after the shutdown timeout; sealing the stores as aborted")
        if self._aborted or aborted or not drained:
            self.registry.abort_all()
        elif not self.registry.finalize_all():
            self._log.error("ZARR FINALIZATION INCOMPLETE - Some data may not be saved correctly")
        return drained

    # ---- the drain thread

    def _drain(self) -> None:
        while True:
            item = self._submitted.get()
            if item is _SENTINEL:
                return
            job_id, future, image_bytes, result = item
            try:
                if future is not None:
                    future.result()
                self._output.put_nowait(JobResult(job_id=job_id, result=result, exception=None))
            except Exception as e:
                self._log.exception(f"Job {job_id} failed! Returning exception result.")
                self._output.put_nowait(JobResult(job_id=job_id, result=None, exception=e))
            finally:
                with self._pending_lock:
                    self._pending -= 1
                note_job_completed(self._bp_pending_jobs, self._bp_pending_bytes, self._bp_capacity_event, image_bytes)
