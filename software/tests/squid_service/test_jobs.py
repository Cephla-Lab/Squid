from pathlib import Path

from squid_service.jobs import JobOutcome, JobResult, JobState, JobStore


def test_job_lifecycle():
    store = JobStore()
    job = store.create(experiment_id="exp1", expected_total_images=10)
    assert job.state == JobState.ACCEPTED
    assert store.active.job_id == job.job_id
    store.mark_running(job.job_id)
    assert store.get(job.job_id).state == JobState.RUNNING
    assert store.get(job.job_id).started_at is not None
    store.update_progress(job.job_id, images_acquired=4, elapsed_s=2.0)
    prog = store.get(job.job_id).progress
    assert prog.images_acquired == 4
    assert prog.total_images == 10
    done = store.complete(job.job_id, JobOutcome.SUCCESS, JobResult(image_count_written=10))
    assert done.state == JobState.COMPLETED
    assert done.outcome == JobOutcome.SUCCESS
    assert done.completed_at is not None
    assert store.active is None
    assert store.last.job_id == job.job_id


def test_wait_returns_true_after_complete():
    store = JobStore()
    job = store.create(experiment_id=None)
    assert store.wait(job.job_id, timeout_s=0.05) is False
    store.complete(job.job_id, JobOutcome.ABORTED, JobResult())
    assert store.wait(job.job_id, timeout_s=0.05) is True


def test_last_job_persists_across_stores(tmp_path: Path):
    path = tmp_path / "last_job.json"
    store = JobStore(persist_path=path)
    job = store.create(experiment_id="exp2")
    store.complete(job.job_id, JobOutcome.FAILURE, JobResult(errors_encountered=3))
    reloaded = JobStore(persist_path=path)
    assert reloaded.last is not None
    assert reloaded.last.job_id == job.job_id
    assert reloaded.last.outcome == JobOutcome.FAILURE


def test_estimated_remaining_computed():
    store = JobStore()
    job = store.create(experiment_id="e", expected_total_images=100)
    store.mark_running(job.job_id)
    store.update_progress(job.job_id, images_acquired=25, elapsed_s=50.0)
    est = store.get(job.job_id).progress.estimated_remaining_s
    assert est is not None and 149.0 < est < 151.0
