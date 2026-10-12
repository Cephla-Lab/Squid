"""Tests for control.core.disk_space.DiskSpaceGuard — the free-space watchdog of a large acquisition."""

import pathlib
import time

import pytest

import control._def
import control.utils
from control.core.disk_space import DiskSpaceGuard, DiskStatus
from control.core.pause_gate import PauseGate

_BYTES_PER_GB = 1024**3


def _gb(n_bytes: int) -> float:
    """Express a byte count as GB so it round-trips exactly through ``int(gb * 2**30)``."""
    return n_bytes / _BYTES_PER_GB


@pytest.fixture(autouse=True)
def default_settings(monkeypatch):
    """Deterministic settings for every test: small reserve, no simulated capacity, fast polling."""
    monkeypatch.setattr(control._def, "DISK_SPACE_RESERVE_GB", _gb(100_000), raising=False)
    monkeypatch.setattr(control._def, "DISK_SPACE_POLL_INTERVAL_S", 5.0, raising=False)
    monkeypatch.setattr(control._def, "SIMULATED_DISK_CAPACITY_GB", 0.0, raising=False)


def _guard(tmp_path, free_bytes, pending_bytes=0, frame_bytes=10_000, planes_per_fov=1, **kwargs):
    """Guard whose free space is whatever ``free_bytes`` (int or callable) says."""
    free_fn = free_bytes if callable(free_bytes) else (lambda _directory: free_bytes)
    return DiskSpaceGuard(
        directory=tmp_path,
        pending_bytes_fn=lambda: pending_bytes,
        frame_bytes_fn=lambda: frame_bytes,
        planes_per_fov=planes_per_fov,
        free_bytes_fn=free_fn,
        **kwargs,
    )


def test_required_bytes_is_reserve_plus_pending_plus_two_fovs(tmp_path, monkeypatch):
    monkeypatch.setattr(control._def, "DISK_SPACE_RESERVE_GB", _gb(1_000_000), raising=False)
    guard = _guard(tmp_path, free_bytes=0, pending_bytes=250_000, frame_bytes=7_000, planes_per_fov=5)

    fov_bytes = 5 * 7_000
    assert guard.required_bytes() == 1_000_000 + 250_000 + 2 * fov_bytes


def test_required_bytes_reads_reserve_live(tmp_path, monkeypatch):
    guard = _guard(tmp_path, free_bytes=0, frame_bytes=0)
    assert guard.required_bytes() == 100_000

    monkeypatch.setattr(control._def, "DISK_SPACE_RESERVE_GB", _gb(500_000), raising=False)
    assert guard.required_bytes() == 500_000


def test_status_reports_components_and_ok(tmp_path):
    guard = _guard(tmp_path, free_bytes=10_000_000, pending_bytes=1_000, frame_bytes=2_000, planes_per_fov=3)
    status = guard.status()

    assert isinstance(status, DiskStatus)
    assert status.free_bytes == 10_000_000
    assert status.reserve_bytes == 100_000
    assert status.pending_bytes == 1_000
    assert status.fov_bytes == 6_000
    assert status.required_bytes == 100_000 + 1_000 + 12_000
    assert status.ok is True
    assert status.holding is False


def test_status_not_ok_when_free_below_required(tmp_path):
    guard = _guard(tmp_path, free_bytes=50_000)
    assert guard.status().ok is False


def test_status_ok_when_free_equals_required(tmp_path):
    guard = _guard(tmp_path, free_bytes=120_000, frame_bytes=10_000, planes_per_fov=1)
    status = guard.status()
    assert status.required_bytes == 120_000
    assert status.ok is True


def test_status_does_not_change_hold_state(tmp_path):
    guard = _guard(tmp_path, free_bytes=0)
    gate = PauseGate()

    guard.status()

    assert gate.is_paused() is False
    assert guard.last_status is None


def test_update_holds_gate_when_below_threshold(tmp_path):
    guard = _guard(tmp_path, free_bytes=50_000)
    gate = PauseGate()

    status = guard.update(gate)

    assert gate.is_paused() is True
    assert gate.state().reasons == ("disk_space",)
    assert status.holding is True
    assert status.ok is False


def test_update_does_not_hold_when_above_threshold(tmp_path):
    guard = _guard(tmp_path, free_bytes=10_000_000)
    gate = PauseGate()

    status = guard.update(gate)

    assert gate.is_paused() is False
    assert status.holding is False


def test_update_stays_held_until_one_fov_of_headroom(tmp_path):
    free = {"value": 50_000}
    guard = _guard(tmp_path, free_bytes=lambda _d: free["value"], frame_bytes=10_000, planes_per_fov=1)
    gate = PauseGate()

    guard.update(gate)
    assert gate.is_paused() is True

    # required == 100_000 + 0 + 2 * 10_000 == 120_000; hysteresis needs required + fov == 130_000.
    free["value"] = 120_000
    status = guard.update(gate)
    assert gate.is_paused() is True, "free == required must not release the hold"
    assert status.holding is True
    assert status.ok is True

    free["value"] = 129_999
    assert guard.update(gate).holding is True
    assert gate.is_paused() is True

    free["value"] = 130_000
    status = guard.update(gate)
    assert gate.is_paused() is False
    assert status.holding is False
    assert gate.state().reasons == ()


def test_update_is_idempotent(tmp_path):
    free = {"value": 50_000}
    guard = _guard(tmp_path, free_bytes=lambda _d: free["value"])
    gate = PauseGate()
    holds = []
    releases = []
    gate_hold, gate_release = gate.hold, gate.release
    gate.hold = lambda reason: (holds.append(reason), gate_hold(reason))[1]
    gate.release = lambda reason: (releases.append(reason), gate_release(reason))[1]

    for _ in range(3):
        guard.update(gate)
    assert holds == ["disk_space"]
    assert gate.is_paused() is True

    free["value"] = 10_000_000
    for _ in range(3):
        guard.update(gate)
    assert releases == ["disk_space"]
    assert gate.is_paused() is False


def test_update_does_not_release_other_holders(tmp_path):
    guard = _guard(tmp_path, free_bytes=10_000_000)
    gate = PauseGate()
    gate.hold("operator")

    guard.update(gate)

    assert gate.is_paused() is True
    assert gate.state().reasons == ("operator",)


def test_last_status_reflects_latest_update(tmp_path):
    free = {"value": 50_000}
    guard = _guard(tmp_path, free_bytes=lambda _d: free["value"])
    gate = PauseGate()

    assert guard.last_status is None

    guard.update(gate)
    assert guard.last_status.free_bytes == 50_000
    assert guard.last_status.holding is True

    free["value"] = 10_000_000
    returned = guard.update(gate)
    assert guard.last_status is returned
    assert guard.last_status.free_bytes == 10_000_000
    assert guard.last_status.holding is False


def test_pending_bytes_raise_propagates(tmp_path):
    def _boom():
        raise RuntimeError("pending is gone")

    guard = DiskSpaceGuard(
        directory=tmp_path,
        pending_bytes_fn=_boom,
        frame_bytes_fn=lambda: 10_000,
        planes_per_fov=1,
        free_bytes_fn=lambda _d: 0,
    )
    with pytest.raises(RuntimeError, match="pending is gone"):
        guard.update(PauseGate())


def test_frame_bytes_raise_propagates(tmp_path):
    def _boom():
        raise RuntimeError("no camera")

    guard = DiskSpaceGuard(
        directory=tmp_path,
        pending_bytes_fn=lambda: 0,
        frame_bytes_fn=_boom,
        planes_per_fov=1,
        free_bytes_fn=lambda _d: 0,
    )
    with pytest.raises(RuntimeError, match="no camera"):
        guard.required_bytes()


def test_real_path_uses_injected_free_bytes_fn(tmp_path):
    seen = []

    def _free(directory):
        seen.append(directory)
        return 4_242_424

    guard = _guard(tmp_path, free_bytes=_free)

    assert guard.free_bytes() == 4_242_424
    assert [pathlib.Path(d) for d in seen] == [tmp_path]


def test_real_path_falls_back_to_nearest_existing_parent(tmp_path):
    missing = tmp_path / "not" / "created" / "yet"
    seen = []

    def _free(directory):
        seen.append(pathlib.Path(directory))
        return 7_000_000

    guard = DiskSpaceGuard(
        directory=missing,
        pending_bytes_fn=lambda: 0,
        frame_bytes_fn=lambda: 10_000,
        planes_per_fov=1,
        free_bytes_fn=_free,
    )

    assert guard.free_bytes() == 7_000_000
    assert seen == [tmp_path]


def test_real_path_uses_directory_once_it_exists(tmp_path):
    target = tmp_path / "acquisition"
    seen = []

    def _free(directory):
        seen.append(pathlib.Path(directory))
        return 7_000_000

    guard = DiskSpaceGuard(
        directory=target,
        pending_bytes_fn=lambda: 0,
        frame_bytes_fn=lambda: 10_000,
        planes_per_fov=1,
        free_bytes_fn=_free,
    )
    guard.free_bytes()
    target.mkdir()
    guard.free_bytes()

    assert seen == [tmp_path, target]


def test_default_free_bytes_fn_reads_the_real_disk(tmp_path):
    guard = DiskSpaceGuard(
        directory=tmp_path,
        pending_bytes_fn=lambda: 0,
        frame_bytes_fn=lambda: 10_000,
        planes_per_fov=1,
    )
    assert guard.free_bytes() > 0


def _write_file(path: pathlib.Path, n_bytes: int) -> None:
    path.write_bytes(b"\0" * n_bytes)


def test_simulated_capacity_holds_as_files_fill_the_disk_and_releases_when_deleted(tmp_path, monkeypatch):
    # Capacity 1 MiB; required == 100_000 reserve + 2 * 10_000 FOV == 120_000 bytes.
    monkeypatch.setattr(control._def, "SIMULATED_DISK_CAPACITY_GB", _gb(1_048_576), raising=False)
    guard = _guard(
        tmp_path, free_bytes=lambda _d: pytest.fail("real disk must not be consulted"), refresh_interval_s=0.0
    )
    gate = PauseGate()

    assert guard.free_bytes() == 1_048_576
    assert guard.update(gate).holding is False

    big = tmp_path / "images.bin"
    _write_file(big, 1_000_000)
    status = guard.update(gate)
    assert status.free_bytes == 48_576
    assert status.holding is True
    assert gate.is_paused() is True

    big.unlink()
    status = guard.update(gate)
    assert status.free_bytes == 1_048_576
    assert status.holding is False
    assert gate.is_paused() is False


def test_simulated_capacity_clamps_free_at_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(control._def, "SIMULATED_DISK_CAPACITY_GB", _gb(1_000), raising=False)
    _write_file(tmp_path / "big.bin", 50_000)
    guard = _guard(tmp_path, free_bytes=0, refresh_interval_s=0.0)

    assert guard.free_bytes() == 0


def test_simulated_capacity_counts_nested_files(tmp_path, monkeypatch):
    monkeypatch.setattr(control._def, "SIMULATED_DISK_CAPACITY_GB", _gb(100_000), raising=False)
    nested = tmp_path / "A1" / "0"
    nested.mkdir(parents=True)
    _write_file(nested / "img.tiff", 40_000)
    guard = _guard(tmp_path, free_bytes=0, refresh_interval_s=0.0)

    assert guard.free_bytes() == 60_000


def test_simulated_capacity_treats_missing_directory_as_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(control._def, "SIMULATED_DISK_CAPACITY_GB", _gb(100_000), raising=False)
    guard = DiskSpaceGuard(
        directory=tmp_path / "does" / "not" / "exist",
        pending_bytes_fn=lambda: 0,
        frame_bytes_fn=lambda: 10_000,
        planes_per_fov=1,
        refresh_interval_s=0.0,
    )

    assert guard.free_bytes() == 100_000


def test_simulated_capacity_caches_usage_between_polls(tmp_path, monkeypatch):
    monkeypatch.setattr(control._def, "SIMULATED_DISK_CAPACITY_GB", _gb(100_000), raising=False)
    guard = _guard(tmp_path, free_bytes=0, refresh_interval_s=60.0)

    assert guard.free_bytes() == 100_000

    _write_file(tmp_path / "img.bin", 40_000)
    assert guard.free_bytes() == 100_000, "usage must be cached until the poll interval elapses"


def test_simulated_capacity_refreshes_after_the_poll_interval(tmp_path, monkeypatch):
    monkeypatch.setattr(control._def, "SIMULATED_DISK_CAPACITY_GB", _gb(100_000), raising=False)
    guard = _guard(tmp_path, free_bytes=0, refresh_interval_s=0.02)

    assert guard.free_bytes() == 100_000
    _write_file(tmp_path / "img.bin", 40_000)
    time.sleep(0.05)

    assert guard.free_bytes() == 60_000


def test_simulated_capacity_poll_interval_defaults_to_setting(tmp_path, monkeypatch):
    monkeypatch.setattr(control._def, "SIMULATED_DISK_CAPACITY_GB", _gb(100_000), raising=False)
    monkeypatch.setattr(control._def, "DISK_SPACE_POLL_INTERVAL_S", 0.0, raising=False)
    guard = _guard(tmp_path, free_bytes=0)

    assert guard.free_bytes() == 100_000
    _write_file(tmp_path / "img.bin", 40_000)
    assert guard.free_bytes() == 60_000


# --- pre-flight check shared by the GUI dialog and the TCP run commands ----------------------------


class _EstimateOnlyController:
    def __init__(self, estimate_bytes, image_count=12):
        self._estimate = estimate_bytes
        self._count = image_count

    def get_estimated_acquisition_disk_storage(self):
        return self._estimate

    def get_acquisition_image_count(self):
        return self._count


def test_preflight_check_applies_the_safety_factor_and_reports_fit(tmp_path, monkeypatch):
    from control.core.disk_space import preflight_disk_check

    monkeypatch.setattr(control.utils, "get_available_disk_space", lambda d: 1030)
    fits = preflight_disk_check(_EstimateOnlyController(1000), str(tmp_path))
    assert fits.fits and fits.required_bytes == 1030 and fits.available_bytes == 1030 and fits.image_count == 12

    too_big = preflight_disk_check(_EstimateOnlyController(1001), str(tmp_path))
    assert not too_big.fits


def test_preflight_check_measures_the_nearest_existing_folder(tmp_path, monkeypatch):
    from control.core.disk_space import preflight_disk_check

    asked = []
    monkeypatch.setattr(control.utils, "get_available_disk_space", lambda d: asked.append(str(d)) or 10**9)
    # The TCP commands check before the experiment folder (or even the base path) has been created.
    check = preflight_disk_check(_EstimateOnlyController(1), str(tmp_path / "not" / "created" / "yet"))
    assert asked == [str(tmp_path)]
    assert check.save_directory == str(tmp_path / "not" / "created" / "yet")


def test_preflight_check_describes_the_shortfall(tmp_path, monkeypatch):
    from control.core.disk_space import preflight_disk_check

    monkeypatch.setattr(control.utils, "get_available_disk_space", lambda d: 5 * 1024 * 1024)
    check = preflight_disk_check(_EstimateOnlyController(2000 * 1024 * 1024, image_count=1234), str(tmp_path))
    text = check.describe()
    assert "1,234 images" in text and "2,060 [MB]" in text and "5 [MB] available" in text and str(tmp_path) in text
