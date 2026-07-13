"""Acceptance test pinning the backpressure byte-release deadlock class.

This test guards against a historical PERMANENT DEADLOCK in acquisition
throttling (see project CLAUDE.md, "Backpressure" design note):

    Backpressure tracks bytes in the MAIN PROCESS queue. Bytes are incremented
    when a job is dispatched and decremented when a job completes. For
    DownsampledViewJob, bytes are released *immediately on job completion*
    (not when the well/region completes), because the image data moves to
    subprocess memory when processed.

    Why this matters: if bytes were only released at well completion, a z-stack
    acquisition (FOVs x z-levels x channels) could accumulate more pending bytes
    than the byte limit *before any well finishes*. The acquisition would then
    block forever in wait_for_capacity() waiting for capacity that only well
    completion could free -- a permanent deadlock.

The scenario below drives a small z-stack under a deliberately tight byte limit
(~1.5 camera frames) so that the region's total pending bytes vastly exceed the
limit before the single region completes. Under correct per-job byte release the
acquisition throttles but drains and finishes; under the regressed
release-at-well-completion behavior it would hang. Completion within the timeout
is therefore the regression assertion.
"""

import logging

import pytest

import control._def
from tests.acceptance.harness import (
    make_harness,
    list_image_files,
    timepoint_dir,
)

pytestmark = pytest.mark.acceptance

# Empirically measured simulated-camera frame: 4168 x 4168 uint16 = 34,744,448
# bytes = 33.135 MiB (backpressure accounts in binary MiB, 1 MiB = 1024*1024).
_IMAGE_SIZE_MIB = 33.135


def test_zstack_completes_under_tight_byte_limit(tmp_path, acquisition_defaults, caplog):
    """A z-stack whose total bytes far exceed the byte limit still completes.

    Pins the DownsampledViewJob byte-release-on-job-completion design: if bytes
    were only released at region completion, the 12-image z-stack (~8x the byte
    limit) would deadlock before the single region finished. Completion is the
    regression assertion; a hang manifests as the run_and_wait timeout.
    """
    monkeypatch = acquisition_defaults

    # Byte limit ~1.5 frames: the second dispatched frame already exceeds it, so
    # throttling must engage well before the region's 12 frames are all in flight.
    byte_limit_mib = 1.5 * _IMAGE_SIZE_MIB  # ~49.7 MiB
    monkeypatch.setattr(control._def, "ACQUISITION_MAX_PENDING_MB", byte_limit_mib)
    monkeypatch.setattr(control._def, "ACQUISITION_MAX_PENDING_JOBS", 3)
    monkeypatch.setattr(control._def, "ACQUISITION_THROTTLING_ENABLED", True)

    harness = make_harness()
    try:
        harness.new_experiment(tmp_path / "backpressure", "zstack_tight_limit")
        harness.add_fov_grid("region0", nx=2, ny=2)  # 4 FOVs
        harness.select_channels(1)  # 1 channel

        harness.mpc.set_Nt(1)
        harness.mpc.set_NZ(3)  # 4 FOVs * 1 channel * 3 z = 12 images
        harness.mpc.set_deltaZ(1.0)
        harness.mpc.set_af_flag(False)
        harness.mpc.set_reflection_af_flag(False)

        # 12 frames * 33.135 MiB = ~397.6 MiB pending if none released,
        # ~8x the ~49.7 MiB byte limit -- the historical deadlock trigger.
        with caplog.at_level(logging.INFO):
            # Deadlock (regression) manifests as this timeout.
            harness.run_and_wait(timeout_s=300)

        # Completion assertions: all 12 images landed and the region drained.
        tp0 = timepoint_dir(harness.experiment_dir, 0)
        images = list_image_files(tp0)
        assert len(images) == 12, f"expected 12 image files in {tp0}, found {len(images)}: {images}"

        # Guard the premise, not just the outcome: the deadlock path is only
        # exercised while total acquisition bytes far exceed the byte limit.
        # If the simulated frame ever shrinks, fail loudly instead of going
        # green without testing anything. (Uncompressed TIFF size ~= frame
        # bytes, so on-disk size is a faithful proxy.)
        total_bytes = sum(p.stat().st_size for p in images)
        limit_bytes = byte_limit_mib * 1024 * 1024
        assert total_bytes > 5 * limit_bytes, (
            f"acquisition totalled {total_bytes / 1024 / 1024:.1f} MiB, not >5x the "
            f"{byte_limit_mib:.1f} MiB byte limit — the simulated frame size changed and "
            "this test no longer exercises the backpressure deadlock path; re-derive "
            "_IMAGE_SIZE_MIB and the limit"
        )
        assert harness.tracker.image_count == 12, f"tracker saw {harness.tracker.image_count} images, expected 12"

        # Root acquisition-complete marker.
        done_marker = harness.experiment_dir / ".done"
        assert done_marker.exists(), f"expected .done marker at {done_marker}"

        # Informational: confirm throttling actually engaged (not asserted --
        # timing-dependent on CI). Reported by the runner via captured logs.
        throttled = any("Backpressure throttling" in r.getMessage() for r in caplog.records)
        logging.getLogger(__name__).info(
            "backpressure throttling engaged during acquisition: %s (byte limit=%.1f MiB, image=%.3f MiB)",
            throttled,
            byte_limit_mib,
            _IMAGE_SIZE_MIB,
        )
    finally:
        harness.close()
