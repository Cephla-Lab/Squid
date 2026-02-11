"""Focused tests for MultiPointWorker focus-lock behavior."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from squid.backend.controllers.multipoint.multi_point_worker import MultiPointWorker
from squid.core.events import AutofocusMode


class _FakeAutofocusExecutor:
    def __init__(self, *, focus_lock_active: bool, wait_for_lock_result: bool):
        self._focus_lock_active = focus_lock_active
        self._wait_for_lock_result = wait_for_lock_result
        self.pause_called = False
        self.resume_called = False

    def is_focus_lock_active(self) -> bool:
        return self._focus_lock_active

    def wait_for_focus_lock(self, timeout_s: float = 5.0) -> bool:  # noqa: ARG002
        return self._wait_for_lock_result

    def pause_focus_lock(self) -> bool:
        self.pause_called = True
        return False

    def resume_focus_lock(self) -> None:
        self.resume_called = True


def _build_worker_for_acquire_test(
    *,
    autofocus_mode: AutofocusMode,
    focus_lock_active: bool,
    wait_for_lock_result: bool,
) -> MultiPointWorker:
    worker = MultiPointWorker.__new__(MultiPointWorker)

    worker.autofocus_mode = autofocus_mode
    worker.perform_autofocus = lambda region_id, fov: True
    worker._stage_service = SimpleNamespace(get_position=lambda: SimpleNamespace(z_mm=1.234))
    worker._log = MagicMock()
    worker._event_bus = None
    worker._autofocus_executor = _FakeAutofocusExecutor(
        focus_lock_active=focus_lock_active,
        wait_for_lock_result=wait_for_lock_result,
    )
    worker.NZ = 1
    worker.use_piezo = False
    worker._piezo_service = None
    worker._zstack_executor = SimpleNamespace(z_piezo_um=0.0)
    worker._acquire_channel_first = MagicMock()
    worker._acquire_z_first = MagicMock()
    worker.acquisition_order = "channel_first"
    worker.af_fov_count = 0
    worker._progress_tracker = SimpleNamespace(af_fov_count=0)
    worker.time_point = 0
    worker.prepare_z_stack = MagicMock()
    worker.move_z_back_after_stack = MagicMock()

    return worker


def test_acquire_at_position_aborts_when_focus_lock_mode_cannot_lock() -> None:
    """Focus-lock AF mode must fail fast when lock verification fails."""
    worker = _build_worker_for_acquire_test(
        autofocus_mode=AutofocusMode.FOCUS_LOCK,
        focus_lock_active=True,
        wait_for_lock_result=False,
    )

    with pytest.raises(RuntimeError, match="Focus lock verification failed"):
        worker.acquire_at_position(region_id="A1", current_path="/tmp", fov=0)

    worker._acquire_channel_first.assert_not_called()


def test_acquire_at_position_non_focus_lock_mode_continues_on_lock_warning() -> None:
    """Non-focus-lock AF modes should not hard-fail on focus-lock warning."""
    worker = _build_worker_for_acquire_test(
        autofocus_mode=AutofocusMode.LASER_REFLECTION,
        focus_lock_active=True,
        wait_for_lock_result=False,
    )

    worker.acquire_at_position(region_id="A1", current_path="/tmp", fov=0)

    worker._acquire_channel_first.assert_called_once()
