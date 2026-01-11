"""Unit tests for AutofocusExecutor."""

from __future__ import annotations

from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from squid.backend.controllers.multipoint.focus_operations import (
    AutofocusExecutor,
)


class FakeAutofocusController:
    """Fake AutoFocusController for testing."""

    def __init__(self):
        self.autofocus_called = False
        self.autofocus_completed = True

    def autofocus(self) -> None:
        self.autofocus_called = True

    def wait_till_autofocus_has_completed(self, timeout_s: Optional[float] = None) -> bool:
        return self.autofocus_completed


class FakeLaserAFController:
    """Fake LaserAutofocusController for testing."""

    def __init__(self):
        self.move_to_target_calls = []
        self.should_fail = False

    def move_to_target(self, target: float) -> None:
        if self.should_fail:
            raise Exception("Laser AF failed")
        self.move_to_target_calls.append(target)


class FakeFocusLockController:
    """Fake FocusLockController for testing."""

    def __init__(self):
        self.mode = "off"
        self.is_running = False
        self.is_active = False  # New: tracks if started (even if paused)
        self.wait_result = True
        self.pause_called = False
        self.resume_called = False
        self.should_fail_pause = False

    def wait_for_lock(self, timeout_s: float = 5.0) -> bool:
        return self.wait_result

    def pause(self) -> None:
        if self.should_fail_pause:
            raise Exception("Pause failed")
        self.pause_called = True

    def resume(self) -> None:
        self.resume_called = True


class FakeChannelConfigManager:
    """Fake ChannelConfigurationManager for testing."""

    def __init__(self):
        self.returned_config = MagicMock()

    def get_channel_configuration_by_name(
        self, objective: str, channel_name: str
    ) -> Any:
        return self.returned_config


class FakeObjectiveStore:
    """Fake ObjectiveStore for testing."""

    def __init__(self):
        self.current_objective = "10x"


class TestAutofocusExecutor:
    """Tests for AutofocusExecutor class."""

    def test_init(self):
        """Test AutofocusExecutor initialization."""
        executor = AutofocusExecutor()

        assert executor._do_autofocus is False
        assert executor._do_reflection_af is False
        assert executor._nz == 1

    def test_configure(self):
        """Test configuring autofocus behavior."""
        executor = AutofocusExecutor()

        executor.configure(
            do_autofocus=True,
            do_reflection_af=False,
            nz=5,
            z_stacking_config="FROM CENTER",
            fovs_per_af=10,
        )

        assert executor._do_autofocus is True
        assert executor._do_reflection_af is False
        assert executor._nz == 5
        assert executor._z_stacking_config == "FROM CENTER"
        assert executor._fovs_per_af == 10

    def test_should_perform_autofocus_reflection_af(self):
        """Test that reflection AF always returns True."""
        executor = AutofocusExecutor()
        executor.configure(do_reflection_af=True)

        assert executor.should_perform_autofocus() is True

    def test_should_perform_autofocus_contrast_af(self):
        """Test contrast AF with single z-level."""
        executor = AutofocusExecutor()
        executor.configure(do_autofocus=True, nz=1)

        assert executor.should_perform_autofocus() is True

    def test_should_perform_autofocus_z_stack_from_center(self):
        """Test contrast AF with z-stack from center."""
        executor = AutofocusExecutor()
        executor.configure(
            do_autofocus=True, nz=5, z_stacking_config="FROM CENTER"
        )

        assert executor.should_perform_autofocus() is True

    def test_should_perform_autofocus_z_stack_from_bottom(self):
        """Test contrast AF with z-stack from bottom returns False."""
        executor = AutofocusExecutor()
        executor.configure(
            do_autofocus=True, nz=5, z_stacking_config="FROM BOTTOM"
        )

        assert executor.should_perform_autofocus() is False

    def test_should_perform_autofocus_fov_interval(self):
        """Test AF respects FOV interval."""
        executor = AutofocusExecutor()
        executor.configure(do_autofocus=True, nz=1, fovs_per_af=3)

        # FOV 0 should trigger AF
        executor.af_fov_count = 0
        assert executor.should_perform_autofocus() is True

        # FOV 1 should not
        executor.af_fov_count = 1
        assert executor.should_perform_autofocus() is False

        # FOV 3 should trigger AF
        executor.af_fov_count = 3
        assert executor.should_perform_autofocus() is True

    def test_perform_contrast_autofocus(self):
        """Test performing contrast-based autofocus."""
        af = FakeAutofocusController()
        executor = AutofocusExecutor(autofocus_controller=af)
        executor.configure(do_autofocus=True, nz=1)

        result = executor.perform_autofocus()

        assert result is True
        assert af.autofocus_called is True

    def test_perform_contrast_autofocus_timeout(self):
        """Test contrast autofocus timeout returns False."""
        af = FakeAutofocusController()
        af.autofocus_completed = False
        executor = AutofocusExecutor(autofocus_controller=af)
        executor.configure(do_autofocus=True, nz=1)

        result = executor.perform_autofocus(timeout_s=1.0)

        assert result is False

    def test_perform_laser_autofocus(self):
        """Test performing laser reflection autofocus."""
        laser_af = FakeLaserAFController()
        executor = AutofocusExecutor(laser_af_controller=laser_af)
        executor.configure(do_reflection_af=True)

        result = executor.perform_autofocus()

        assert result is True
        assert 0 in laser_af.move_to_target_calls

    def test_perform_laser_autofocus_failure(self):
        """Test laser AF failure returns False."""
        laser_af = FakeLaserAFController()
        laser_af.should_fail = True
        executor = AutofocusExecutor(laser_af_controller=laser_af)
        executor.configure(do_reflection_af=True)

        result = executor.perform_autofocus()

        assert result is False

    def test_is_focus_lock_active_when_active(self):
        """Test is_focus_lock_active returns True when focus lock is active."""
        focus_lock = FakeFocusLockController()
        focus_lock.is_active = True

        executor = AutofocusExecutor(focus_lock_controller=focus_lock)

        assert executor.is_focus_lock_active() is True

    def test_is_focus_lock_active_when_inactive(self):
        """Test is_focus_lock_active returns False when focus lock is not active."""
        focus_lock = FakeFocusLockController()
        focus_lock.is_active = False

        executor = AutofocusExecutor(focus_lock_controller=focus_lock)

        assert executor.is_focus_lock_active() is False

    def test_is_focus_lock_active_no_controller(self):
        """Test is_focus_lock_active returns False when no controller."""
        executor = AutofocusExecutor()

        assert executor.is_focus_lock_active() is False

    def test_should_perform_autofocus_skips_when_focus_lock_active(self):
        """Test that autofocus is skipped when focus lock is active."""
        focus_lock = FakeFocusLockController()
        focus_lock.is_active = True

        executor = AutofocusExecutor(focus_lock_controller=focus_lock)
        executor.configure(do_reflection_af=True)

        # Focus lock is active, so per-FOV AF should be skipped
        assert executor.should_perform_autofocus() is False

    def test_perform_autofocus_skips_when_focus_lock_active(self):
        """Test perform_autofocus returns True (skip) when focus lock is active."""
        focus_lock = FakeFocusLockController()
        focus_lock.is_active = True
        laser_af = FakeLaserAFController()

        executor = AutofocusExecutor(
            focus_lock_controller=focus_lock,
            laser_af_controller=laser_af,
        )
        executor.configure(do_reflection_af=True)

        # Should return True (skipped) without calling laser AF
        result = executor.perform_autofocus()

        assert result is True
        assert len(laser_af.move_to_target_calls) == 0

    def test_wait_for_focus_lock_success(self):
        """Test waiting for focus lock to achieve lock."""
        focus_lock = FakeFocusLockController()
        focus_lock.is_active = True
        focus_lock.wait_result = True

        executor = AutofocusExecutor(focus_lock_controller=focus_lock)

        result = executor.wait_for_focus_lock(timeout_s=5.0)

        assert result is True

    def test_wait_for_focus_lock_timeout(self):
        """Test wait_for_focus_lock returns False on timeout."""
        focus_lock = FakeFocusLockController()
        focus_lock.is_active = True
        focus_lock.wait_result = False

        executor = AutofocusExecutor(focus_lock_controller=focus_lock)

        result = executor.wait_for_focus_lock(timeout_s=1.0)

        assert result is False

    def test_wait_for_focus_lock_not_active(self):
        """Test wait_for_focus_lock returns False when not active."""
        focus_lock = FakeFocusLockController()
        focus_lock.is_active = False

        executor = AutofocusExecutor(focus_lock_controller=focus_lock)

        result = executor.wait_for_focus_lock()

        assert result is False

    def test_pause_focus_lock_success(self):
        """Test pausing focus lock."""
        focus_lock = FakeFocusLockController()
        focus_lock.is_active = True

        executor = AutofocusExecutor(focus_lock_controller=focus_lock)

        result = executor.pause_focus_lock()

        assert result is True
        assert focus_lock.pause_called is True

    def test_pause_focus_lock_not_active(self):
        """Test pause_focus_lock returns False when not active."""
        focus_lock = FakeFocusLockController()
        focus_lock.is_active = False

        executor = AutofocusExecutor(focus_lock_controller=focus_lock)

        result = executor.pause_focus_lock()

        assert result is False
        assert focus_lock.pause_called is False

    def test_pause_focus_lock_failure(self):
        """Test pause_focus_lock returns False on exception."""
        focus_lock = FakeFocusLockController()
        focus_lock.is_active = True
        focus_lock.should_fail_pause = True

        executor = AutofocusExecutor(focus_lock_controller=focus_lock)

        result = executor.pause_focus_lock()

        assert result is False

    def test_resume_focus_lock(self):
        """Test resuming focus lock."""
        focus_lock = FakeFocusLockController()
        focus_lock.is_active = True

        executor = AutofocusExecutor(focus_lock_controller=focus_lock)

        executor.resume_focus_lock()

        assert focus_lock.resume_called is True

    def test_resume_focus_lock_no_controller(self):
        """Test resume_focus_lock handles no controller gracefully."""
        executor = AutofocusExecutor()

        # Should not raise
        executor.resume_focus_lock()

    def test_increment_fov_count(self):
        """Test incrementing FOV counter."""
        executor = AutofocusExecutor()

        assert executor.af_fov_count == 0
        executor.increment_fov_count()
        assert executor.af_fov_count == 1
        executor.increment_fov_count()
        assert executor.af_fov_count == 2

    def test_reset_fov_count(self):
        """Test resetting FOV counter."""
        executor = AutofocusExecutor()
        executor.af_fov_count = 10

        executor.reset_fov_count()

        assert executor.af_fov_count == 0

    def test_apply_config_callback(self):
        """Test apply config callback is called for contrast AF."""
        af = FakeAutofocusController()
        channel_config = FakeChannelConfigManager()
        objectives = FakeObjectiveStore()
        callback_received = []

        def mock_callback(config):
            callback_received.append(config)

        executor = AutofocusExecutor(
            autofocus_controller=af,
            channel_config_manager=channel_config,
            objective_store=objectives,
        )
        executor.configure(do_autofocus=True, nz=1)
        executor.set_apply_config_callback(mock_callback)

        executor.perform_autofocus()

        assert len(callback_received) == 1
        assert callback_received[0] == channel_config.returned_config
