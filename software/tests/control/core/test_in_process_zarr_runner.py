"""InProcessZarrRunner: SaveZarrJob in this process, with JobRunner's interface and counters."""

import json
import os
import queue
import time

import numpy as np
import pytest

import squid.abc
from control.core.backpressure import create_backpressure_values
from control.core.job_processing import CaptureInfo, JobImage, JobResult, SaveZarrJob, ZarrWriteResult, ZarrWriterInfo
from control.models import AcquisitionChannel, CameraSettings, IlluminationSettings

pytest.importorskip("tensorstore")


def _info(tmp_path, t_size=1, z_size=2):
    return ZarrWriterInfo(base_path=str(tmp_path / "acq"), t_size=t_size, c_size=1, z_size=z_size)


def _job(z, value=1):
    return SaveZarrJob(
        capture_info=CaptureInfo(
            position=squid.abc.Pos(x_mm=0.0, y_mm=0.0, z_mm=0.0, theta_rad=None),
            z_index=z,
            capture_time=time.time(),
            configuration=AcquisitionChannel(
                name="BF",
                illumination_settings=IlluminationSettings(illumination_channel="LED", intensity=50.0),
                camera_settings=CameraSettings(exposure_time_ms=10.0, gain_mode=1.0),
            ),
            save_directory="/tmp/unused",
            file_id=f"f_{z}",
            region_id="A1",
            fov=0,
            configuration_idx=0,
            time_point=0,
        ),
        capture_image=JobImage(image_array=np.full((8, 8), value, dtype=np.uint16)),
    )


def _results(runner, n, timeout=10.0):
    out, deadline = [], time.monotonic() + timeout
    while len(out) < n and time.monotonic() < deadline:
        try:
            out.append(runner.output_queue().get(timeout=0.1))
        except queue.Empty:
            pass
    return out


def _wait_settled(runner, bp_values=None, timeout=5.0):
    # The drain thread queues the JobResult before its finally lowers the counters and sets capacity.
    def settled():
        if runner.has_pending():
            return False
        if bp_values is None:
            return True
        pending_jobs, pending_bytes, capacity = bp_values
        return pending_jobs.value == 0 and pending_bytes.value == 0 and capacity.is_set()

    deadline = time.monotonic() + timeout
    while not settled() and time.monotonic() < deadline:
        time.sleep(0.01)


def _squid_attrs(info):
    # The OME group metadata (with _squid) lives in the group above the "0" array (ZarrWriter._get_metadata_zarr_json_path).
    group_json = os.path.join(os.path.dirname(info.get_output_path("A1", 0)), "zarr.json")
    with open(group_json) as f:
        return json.load(f)["attributes"]["_squid"]


def test_dispatch_writes_and_reports_a_result(tmp_path):
    from control.core.in_process_zarr_runner import InProcessZarrRunner

    info = _info(tmp_path)
    runner = InProcessZarrRunner(zarr_writer_info=info)
    runner.start()
    assert runner.is_ready() and runner.wait_ready(timeout_s=1.0) and runner.is_alive()
    assert runner.dispatch(_job(0, value=7)) is True
    (result,) = _results(runner, 1)
    assert isinstance(result, JobResult) and result.exception is None
    assert isinstance(result.result, ZarrWriteResult) and result.result.z_index == 0
    runner.shutdown()
    assert _squid_attrs(info)["acquisition_complete"] is True
    import tensorstore as ts

    written = ts.open(
        {"driver": "zarr3", "kvstore": {"driver": "file", "path": info.get_output_path("A1", 0)}}
    ).result()
    assert int(written[0, 0, 0, 0, 0].read().result()) == 7


def test_counters_follow_jobrunner_contract(tmp_path):
    from control.core.in_process_zarr_runner import InProcessZarrRunner

    pending_jobs, pending_bytes, capacity = create_backpressure_values()
    runner = InProcessZarrRunner(zarr_writer_info=_info(tmp_path), bp_values=(pending_jobs, pending_bytes, capacity))
    runner.start()
    capacity.clear()
    runner.dispatch(_job(0))
    runner.dispatch(_job(1))
    _results(runner, 2)
    _wait_settled(runner, (pending_jobs, pending_bytes, capacity))
    assert not runner.has_pending()
    assert pending_jobs.value == 0 and pending_bytes.value == 0
    assert capacity.is_set()
    runner.shutdown()


def test_dispatch_injects_zarr_writer_info_and_registry(tmp_path):
    from control.core.in_process_zarr_runner import InProcessZarrRunner

    runner = InProcessZarrRunner()
    runner.set_zarr_writer_info(_info(tmp_path))
    runner.set_acquisition_info(None)  # accepted and ignored, as the workers call it
    runner.start()
    job = _job(0)
    runner.dispatch(job)
    assert job.zarr_writer_info is not None and job.registry is runner.registry
    assert SaveZarrJob.default_registry.writers == {}
    _results(runner, 1)
    runner.shutdown()


def test_failed_write_reaches_output_queue_as_exception(tmp_path, monkeypatch):
    from control.core.in_process_zarr_runner import InProcessZarrRunner

    class _FailingFuture:
        def result(self):
            raise RuntimeError("disk full")

    monkeypatch.setattr(SaveZarrJob, "submit", lambda self: (_FailingFuture(), ZarrWriteResult(0, 0, 0, "BF", 0)))
    pending_jobs, pending_bytes, capacity = create_backpressure_values()
    runner = InProcessZarrRunner(zarr_writer_info=_info(tmp_path), bp_values=(pending_jobs, pending_bytes, capacity))
    runner.start()
    capacity.clear()
    runner.dispatch(_job(0))
    (result,) = _results(runner, 1)
    assert isinstance(result.exception, RuntimeError) and result.result is None
    _wait_settled(runner, (pending_jobs, pending_bytes, capacity))
    assert pending_jobs.value == 0 and pending_bytes.value == 0
    runner.shutdown()


def test_submit_failure_is_reported_not_raised(tmp_path, monkeypatch):
    from control.core.in_process_zarr_runner import InProcessZarrRunner

    def boom(self):
        raise OSError("cannot create store")

    monkeypatch.setattr(SaveZarrJob, "submit", boom)
    runner = InProcessZarrRunner(zarr_writer_info=_info(tmp_path))
    runner.start()
    assert runner.dispatch(_job(0)) is True
    (result,) = _results(runner, 1)
    assert isinstance(result.exception, OSError)
    _wait_settled(runner)
    assert not runner.has_pending()
    runner.shutdown()


def test_kill_then_shutdown_aborts_open_stores(tmp_path):
    from control.core.in_process_zarr_runner import InProcessZarrRunner

    info = _info(tmp_path)
    runner = InProcessZarrRunner(zarr_writer_info=info)
    runner.start()
    runner.dispatch(_job(0))
    _results(runner, 1)
    runner.kill()
    runner.shutdown()
    attrs = _squid_attrs(info)
    assert attrs["acquisition_complete"] is False and attrs["aborted"] is True


def test_shutdown_aborted_waits_for_writes_then_seals_aborted(tmp_path):
    """A user abort keeps the frames already captured and marks the store aborted, not complete."""
    from control.core.in_process_zarr_runner import InProcessZarrRunner

    info = _info(tmp_path)
    runner = InProcessZarrRunner(zarr_writer_info=info)
    runner.start()
    runner.dispatch(_job(0, value=5))
    runner.shutdown(aborted=True)
    attrs = _squid_attrs(info)
    assert attrs["acquisition_complete"] is False and attrs["aborted"] is True
    import tensorstore as ts

    written = ts.open(
        {"driver": "zarr3", "kvstore": {"driver": "file", "path": info.get_output_path("A1", 0)}}
    ).result()
    assert int(written[0, 0, 0, 0, 0].read().result()) == 5


def test_shutdown_is_idempotent_and_before_start_is_safe(tmp_path):
    from control.core.in_process_zarr_runner import InProcessZarrRunner

    runner = InProcessZarrRunner(zarr_writer_info=_info(tmp_path))
    runner.shutdown()  # never started: nothing to do
    runner.start()
    runner.shutdown()
    runner.shutdown()
    assert not runner.is_alive()


def test_dispatch_after_shutdown_is_refused(tmp_path):
    """A frame after the stores are sealed must not reopen a writer or raise the counters."""
    from control.core.in_process_zarr_runner import InProcessZarrRunner

    info = _info(tmp_path)
    pending_jobs, pending_bytes, capacity = create_backpressure_values()
    runner = InProcessZarrRunner(zarr_writer_info=info, bp_values=(pending_jobs, pending_bytes, capacity))
    runner.start()
    runner.shutdown()
    job = _job(0)
    assert runner.dispatch(job) is False
    assert job.zarr_writer_info is None and job.registry is None
    assert not runner.has_pending()
    assert pending_jobs.value == 0 and pending_bytes.value == 0
    assert runner.registry.writers == {}
    assert not os.path.exists(info.base_path)
