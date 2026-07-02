"""Acquisition job records and store (spec §9)."""

import json
import threading
import uuid
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

import squid.logging
from squid_service.faults import Fault
from squid_service.timeutil import utc_now_iso


class JobState(str, Enum):
    ACCEPTED = "ACCEPTED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"


class JobOutcome(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    ABORTED = "ABORTED"
    PARTIAL = "PARTIAL"


class JobProgress(BaseModel):
    images_acquired: int = 0
    total_images: int = 0
    current_region: int = 0
    total_regions: int = 0
    current_timepoint: int = 0
    total_timepoints: int = 0
    elapsed_s: float = 0.0
    estimated_remaining_s: Optional[float] = None


class JobResult(BaseModel):
    output_dir: Optional[str] = None
    image_count_written: int = 0
    partial_write: bool = False
    errors_encountered: int = 0
    end_reason: Optional[str] = None
    skipped_fovs: List[Dict[str, Any]] = Field(default_factory=list)


class JobRecord(BaseModel):
    job_id: str
    kind: str = "acquisition"
    experiment_id: Optional[str] = None
    origin: str = "api"  # "api" or "gui"
    state: JobState
    accepted_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    outcome: Optional[JobOutcome] = None
    progress: JobProgress = Field(default_factory=JobProgress)
    result: Optional[JobResult] = None
    fault: Optional[Fault] = None


class JobStore:
    """Thread-safe store for acquisition jobs.

    Keeps every job of the current process in memory; persists the most recently
    completed job to disk so GET /v1/jobs/last is durable across restarts.
    """

    def __init__(self, persist_path: Optional[Path] = None):
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self._lock = threading.Lock()
        self._jobs: Dict[str, JobRecord] = {}
        self._active_id: Optional[str] = None
        self._last_id: Optional[str] = None
        self._done_events: Dict[str, threading.Event] = {}
        self._persist_path = persist_path
        self._persisted_last: Optional[JobRecord] = None
        if persist_path is not None and persist_path.exists():
            try:
                self._persisted_last = JobRecord.model_validate_json(persist_path.read_text())
            except Exception as e:
                self._log.warning(f"Could not load persisted last job: {e}")

    def create(
        self,
        experiment_id: Optional[str],
        origin: str = "api",
        expected_total_images: int = 0,
        expected_total_regions: int = 0,
        expected_total_timepoints: int = 0,
    ) -> JobRecord:
        job = JobRecord(
            job_id=uuid.uuid4().hex[:12],
            experiment_id=experiment_id,
            origin=origin,
            state=JobState.ACCEPTED,
            accepted_at=utc_now_iso(),
            progress=JobProgress(
                total_images=expected_total_images,
                total_regions=expected_total_regions,
                total_timepoints=expected_total_timepoints,
            ),
        )
        with self._lock:
            self._jobs[job.job_id] = job
            self._active_id = job.job_id
            self._done_events[job.job_id] = threading.Event()
        return job

    def get(self, job_id: str) -> Optional[JobRecord]:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None and self._persisted_last is not None and self._persisted_last.job_id == job_id:
            return self._persisted_last
        return job

    @property
    def active(self) -> Optional[JobRecord]:
        with self._lock:
            return self._jobs.get(self._active_id) if self._active_id else None

    @property
    def last(self) -> Optional[JobRecord]:
        with self._lock:
            if self._last_id:
                return self._jobs[self._last_id]
        return self._persisted_last

    def mark_running(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.state = JobState.RUNNING
            job.started_at = utc_now_iso()

    def update_progress(self, job_id: str, **fields) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.state == JobState.COMPLETED:
                return
            progress = job.progress.model_copy(update=fields)
            if progress.total_images and progress.images_acquired and progress.elapsed_s:
                fraction = progress.images_acquired / progress.total_images
                if 0 < fraction < 1:
                    progress.estimated_remaining_s = progress.elapsed_s * (1 - fraction) / fraction
                elif fraction >= 1:
                    progress.estimated_remaining_s = 0.0
            job.progress = progress

    def complete(
        self,
        job_id: str,
        outcome: JobOutcome,
        result: JobResult,
        fault: Optional[Fault] = None,
    ) -> JobRecord:
        with self._lock:
            job = self._jobs[job_id]
            job.state = JobState.COMPLETED
            job.completed_at = utc_now_iso()
            job.outcome = outcome
            job.result = result
            job.fault = fault
            if self._active_id == job_id:
                self._active_id = None
            self._last_id = job_id
            done = self._done_events.get(job_id)
        self._persist(job)
        if done:
            done.set()
        return job

    def wait(self, job_id: str, timeout_s: float) -> bool:
        with self._lock:
            done = self._done_events.get(job_id)
        if done is None:
            return self.get(job_id) is not None
        return done.wait(timeout=timeout_s)

    def _persist(self, job: JobRecord) -> None:
        if self._persist_path is None:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            self._persist_path.write_text(job.model_dump_json(indent=2))
        except Exception as e:
            self._log.warning(f"Could not persist last job: {e}")
