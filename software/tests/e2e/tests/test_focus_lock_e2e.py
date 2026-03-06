"""E2E tests for focus lock module.

Tests the FocusLockSimulator with real EventBus, simulated laser AF and piezo,
verifying status transitions, event publishing, crash safety, pause/resume,
search, and parameter wiring.
"""

from __future__ import annotations

import math
import threading
import time

import pytest

from squid.core.config.focus_lock import FocusLockConfig
from squid.core.events import (
    FocusLockModeChanged,
    FocusLockStatusChanged,
    FocusLockWarning,
    SetFocusLockParamsCommand,
    SetFocusLockReferenceCommand,
)

from ..harness.focus_lock_context import FocusLockTestContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wait_until(predicate, timeout_s: float = 5.0, poll_s: float = 0.02) -> bool:
    """Poll ``predicate()`` until it returns True or timeout."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(poll_s)
    return False


def _start_and_lock(ctx: FocusLockTestContext, timeout_s: float = 5.0) -> None:
    """Start the simulator and explicitly engage the lock reference."""
    ctx.simulator.start()
    assert ctx.wait_for_status("ready", timeout_s=3.0)
    ctx.simulator.set_lock_reference()
    assert ctx.wait_for_status("locked", timeout_s=timeout_s)


# ---------------------------------------------------------------------------
# Start / Lock lifecycle
# ---------------------------------------------------------------------------


class TestStartAndLock:
    """Tests for basic start → ready → locked lifecycle."""

    def test_start_emits_ready_event(self):
        """Start lock, assert FocusLockStatusChanged(status='ready') published."""
        with FocusLockTestContext() as ctx:
            ctx.initialize()
            ctx.simulator.start()

            assert ctx.wait_for_status("ready", timeout_s=3.0)

            ctx.simulator.stop()

    def test_lock_achieved_emits_locked_event(self):
        """Wait for buffer to fill → assert 'locked' event published."""
        with FocusLockTestContext() as ctx:
            ctx.initialize()
            _start_and_lock(ctx)

            ctx.simulator.stop()

    def test_manual_lock_emits_locked_and_sets_target(self):
        """Send set_lock() before buffer fills, assert 'locked' + target set."""
        config = FocusLockConfig(
            loop_rate_hz=50,
            metrics_rate_hz=50,
            buffer_length=100,  # Very high so natural lock won't happen
            recovery_delay_s=0.05,
            recovery_attempts=2,
            recovery_window_readings=2,
        )
        with FocusLockTestContext(config=config) as ctx:
            ctx.initialize()
            # Set a known displacement before starting
            ctx.laser_af.set_displacement(1.5)
            ctx.simulator.start()
            assert ctx.wait_for_status("ready", timeout_s=3.0)

            # Manual lock should bypass buffer requirement
            ctx.simulator.set_lock()
            assert ctx.wait_for_status("locked", timeout_s=3.0)

            # Target displacement should be set to current displacement
            assert ctx.simulator._target_displacement_um == pytest.approx(1.5, abs=0.5)

            ctx.simulator.stop()


# ---------------------------------------------------------------------------
# Mode events
# ---------------------------------------------------------------------------


class TestModeEvents:
    """Tests for FocusLockModeChanged events on start/stop."""

    def test_mode_reflects_start_stop(self):
        """Start → mode='on'; stop → mode='off'."""
        with FocusLockTestContext() as ctx:
            ctx.initialize()
            ctx.simulator.start()

            # Wait for mode=on
            mode_on = ctx.collector.wait_for(
                FocusLockModeChanged,
                predicate=lambda e: e.mode == "on",
                timeout_s=3.0,
            )
            assert mode_on is not None, "Expected FocusLockModeChanged(mode='on') after start()"

            ctx.simulator.stop()
            ctx.event_bus.drain()

            # Wait for mode=off
            mode_off = ctx.collector.wait_for(
                FocusLockModeChanged,
                predicate=lambda e: e.mode == "off",
                timeout_s=3.0,
            )
            assert mode_off is not None, "Expected FocusLockModeChanged(mode='off') after stop()"


# ---------------------------------------------------------------------------
# Crash safety
# ---------------------------------------------------------------------------


class TestCrashSafety:
    """Tests for crash-fails-safe behavior."""

    def test_crash_fails_safe(self):
        """Inject exception → assert controller transitions to disabled."""
        with FocusLockTestContext() as ctx:
            ctx.initialize()
            ctx.simulator.start()
            assert ctx.wait_for_status("ready", timeout_s=3.0)

            # Inject failure
            ctx.laser_af.set_should_fail(True)

            # Should transition to disabled
            assert ctx.wait_for_status("disabled", timeout_s=5.0)

            # Flags should be cleared
            assert ctx.simulator._is_running is False
            assert ctx.simulator._should_run is False


# ---------------------------------------------------------------------------
# Recovery sequence
# ---------------------------------------------------------------------------


class TestRecovery:
    """Tests for locked → recovering → locked / lost sequence."""

    def test_signal_loss_recovery_sequence(self):
        """Degrade SNR to trigger recovery, restore to recover."""
        config = FocusLockConfig(
            loop_rate_hz=50,
            metrics_rate_hz=50,
            buffer_length=3,
            recovery_delay_s=0.05,
            recovery_attempts=5,  # Generous attempts
            recovery_window_readings=2,
        )
        with FocusLockTestContext(config=config) as ctx:
            ctx.initialize()
            _start_and_lock(ctx)

            # Remove the spot signal to trigger recovery.
            ctx.laser_af.set_signal_present(False)
            assert ctx.wait_for_status("recovering", timeout_s=5.0)

            # Restore the signal and nominal displacement.
            ctx.laser_af.set_signal_present(True)
            ctx.laser_af.set_displacement(0.0)
            assert ctx.wait_for_status("locked", timeout_s=5.0)

            ctx.simulator.stop()

    def test_recovery_exhausted_goes_to_lost(self):
        """Exhaust recovery attempts → assert 'lost' status."""
        config = FocusLockConfig(
            loop_rate_hz=50,
            metrics_rate_hz=50,
            buffer_length=3,
            recovery_delay_s=0.01,
            recovery_attempts=1,
            recovery_window_readings=2,
            auto_search_enabled=False,  # Don't enter search
        )
        with FocusLockTestContext(config=config) as ctx:
            ctx.initialize()
            _start_and_lock(ctx)

            # Remove the spot to trigger recovery failure.
            ctx.laser_af.set_signal_present(False)

            # Should exhaust recovery and go to lost
            assert ctx.wait_for_status("lost", timeout_s=5.0)

            ctx.simulator.stop()


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class TestSearch:
    """Tests for auto-search behavior."""

    def test_search_sequence_events(self):
        """Force locked → recovering → searching, assert ordered events."""
        config = FocusLockConfig(
            loop_rate_hz=50,
            metrics_rate_hz=50,
            buffer_length=3,
            recovery_delay_s=0.01,
            recovery_attempts=1,
            recovery_window_readings=2,
            auto_search_enabled=True,
            search_step_um=50.0,  # Large steps so search finishes fast
            search_settle_ms=1,  # Fast settle for tests
        )
        with FocusLockTestContext(config=config) as ctx:
            ctx.initialize()
            _start_and_lock(ctx)

            # Break the lock
            ctx.laser_af.set_signal_present(False)

            # Should enter searching after recovery exhausts
            assert ctx.wait_for_status("searching", timeout_s=5.0)

            ctx.simulator.stop()

            # Verify the status sequence includes the expected transitions
            statuses = ctx.get_statuses()
            assert "ready" in statuses
            assert "locked" in statuses
            assert "recovering" in statuses
            assert "searching" in statuses


# ---------------------------------------------------------------------------
# Pause / Resume
# ---------------------------------------------------------------------------


class TestPauseResume:
    """Tests for pause/resume behavior."""

    def test_pause_resume_preserves_lock(self):
        """Lock → pause → resume, assert lock restored without re-acquisition."""
        with FocusLockTestContext() as ctx:
            ctx.initialize()
            _start_and_lock(ctx)

            # Pause
            ctx.simulator.pause()
            assert ctx.wait_for_status("paused", timeout_s=3.0)
            assert ctx.simulator._lock_buffer_fill == ctx.config.buffer_length

            # Resume
            ctx.simulator.resume()
            assert ctx.wait_for_status("locked", timeout_s=3.0)
            # Buffer should be preserved (no re-acquisition)
            assert ctx.simulator._lock_buffer_fill >= ctx.config.buffer_length

            ctx.simulator.stop()

    def test_pause_resume_race_stress(self):
        """100 rapid pause/resume cycles, assert no crash + valid final state."""
        with FocusLockTestContext() as ctx:
            ctx.initialize()
            _start_and_lock(ctx)

            errors = []

            def stress():
                for _ in range(100):
                    try:
                        ctx.simulator.pause()
                        time.sleep(0.001)
                        ctx.simulator.resume()
                        time.sleep(0.001)
                    except Exception as e:
                        errors.append(e)

            t = threading.Thread(target=stress, daemon=True)
            t.start()
            t.join(timeout=30.0)
            assert not t.is_alive(), "Stress thread timed out"

            assert len(errors) == 0, f"Stress thread raised: {errors}"

            # Final state should be valid
            status = ctx.simulator.status
            assert status in ("ready", "locked", "recovering", "paused", "disabled")

            ctx.simulator.stop()

    def test_pause_when_not_started(self):
        """Pause on a not-started simulator does nothing."""
        with FocusLockTestContext() as ctx:
            ctx.initialize()
            ctx.simulator.pause()
            assert ctx.simulator.status == "disabled"

    def test_resume_when_not_paused(self):
        """Resume on a running but not paused simulator does nothing."""
        with FocusLockTestContext() as ctx:
            ctx.initialize()
            _start_and_lock(ctx)

            ctx.collector.clear()
            ctx.simulator.resume()
            ctx.event_bus.drain()

            # No extra status events should have been published
            status_events = ctx.collector.get(FocusLockStatusChanged)
            assert len(status_events) == 0

            ctx.simulator.stop()


# ---------------------------------------------------------------------------
# Piezo warnings
# ---------------------------------------------------------------------------


class TestPiezoWarnings:
    """Tests for piezo limit warnings."""

    def test_piezo_limit_warnings(self):
        """Drive piezo near limits, assert FocusLockWarning published."""
        with FocusLockTestContext() as ctx:
            ctx.initialize()
            _start_and_lock(ctx)

            # Move piezo near lower limit
            ctx.piezo.move_to(1.0)  # Well within warning margin of 0.0

            # Wait for metrics cycle to pick up the position and publish warning
            warning = ctx.collector.wait_for(
                FocusLockWarning,
                predicate=lambda e: e.warning_type == "piezo_low",
                timeout_s=5.0,
            )
            assert warning is not None, "Expected piezo_low warning"

            ctx.simulator.stop()


# ---------------------------------------------------------------------------
# Multiple start/stop cycles
# ---------------------------------------------------------------------------


class TestLifecycle:
    """Tests for repeated start/stop without leaks."""

    def test_multiple_start_stop_no_leaks(self):
        """10 start/stop cycles, assert no dangling threads, clean state."""
        with FocusLockTestContext() as ctx:
            ctx.initialize()

            for i in range(10):
                ctx.simulator.start()
                assert ctx.wait_for_status("ready", timeout_s=3.0), f"Cycle {i}: no ready"
                ctx.simulator.stop()
                assert ctx.wait_for_status("disabled", timeout_s=3.0), f"Cycle {i}: no disabled"

            # After all cycles, should be cleanly stopped
            assert ctx.simulator.status == "disabled"
            assert ctx.simulator._is_running is False
            assert ctx.simulator._should_run is False

            # No lingering FocusLockSimulator threads
            live_threads = [t for t in threading.enumerate() if "FocusLockSimulator" in t.name]
            assert len(live_threads) == 0, f"Dangling threads: {live_threads}"


# ---------------------------------------------------------------------------
# State reset
# ---------------------------------------------------------------------------


class TestStateReset:
    """Tests for clean state reset across start/stop cycles."""

    def test_state_reset_clears_all_histories(self):
        """Start → lock → stop → start, assert histories are clean."""
        with FocusLockTestContext() as ctx:
            ctx.initialize()

            # First cycle: get locked
            _start_and_lock(ctx)

            # Let some error history accumulate
            time.sleep(0.2)

            ctx.simulator.stop()
            assert ctx.wait_for_status("disabled", timeout_s=3.0)

            # Second cycle: should start clean
            ctx.simulator.start()
            assert ctx.wait_for_status("ready", timeout_s=3.0)

            # Verify clean state
            assert ctx.simulator._lock_buffer_fill == 0
            assert ctx.simulator._recovery_attempts_remaining == 0
            assert ctx.simulator._recovery_good_count == 0
            assert ctx.simulator._smoothed_quality == 1.0

            ctx.simulator.stop()


# ---------------------------------------------------------------------------
# Focus lock params flow
# ---------------------------------------------------------------------------


class TestParamsFlow:
    """Tests for SetFocusLockParamsCommand wiring."""

    def test_focus_lock_params_flow(self):
        """Publish SetFocusLockParamsCommand, assert controller config updated."""
        with FocusLockTestContext() as ctx:
            ctx.initialize()
            ctx.simulator.start()
            assert ctx.wait_for_status("ready", timeout_s=3.0)

            # Publish a params command
            ctx.event_bus.publish(SetFocusLockParamsCommand(
                buffer_length=10,
                min_spot_snr=5.0,
            ))
            ctx.event_bus.drain()

            # Give a moment for the handler to execute
            time.sleep(0.1)

            assert ctx.simulator._config.buffer_length == 10
            assert ctx.simulator._config.min_spot_snr == 5.0

            ctx.simulator.stop()

    def test_params_update_partial(self):
        """Only specified fields are updated, others remain at defaults."""
        with FocusLockTestContext() as ctx:
            ctx.initialize()
            original_snr = ctx.simulator._config.min_spot_snr

            ctx.simulator.start()
            assert ctx.wait_for_status("ready", timeout_s=3.0)

            ctx.event_bus.publish(SetFocusLockParamsCommand(
                buffer_length=20,
            ))
            ctx.event_bus.drain()
            time.sleep(0.1)

            assert ctx.simulator._config.buffer_length == 20
            # SNR should be unchanged
            assert ctx.simulator._config.min_spot_snr == original_snr

            ctx.simulator.stop()


# ---------------------------------------------------------------------------
# Not-initialized guard
# ---------------------------------------------------------------------------


class TestStartGuards:
    """Tests for start() preconditions."""

    def test_start_without_initialization_is_noop(self):
        """Start without laser AF initialized does nothing."""
        with FocusLockTestContext() as ctx:
            # Don't call ctx.initialize()
            ctx.simulator.start()

            # Should not have started
            assert ctx.simulator.status == "disabled"
            assert ctx.simulator._is_running is False

    def test_start_after_initialization_works(self):
        """Start after initialization works normally."""
        with FocusLockTestContext() as ctx:
            ctx.initialize()
            ctx.simulator.start()
            assert ctx.wait_for_status("ready", timeout_s=3.0)
            ctx.simulator.stop()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Miscellaneous edge case tests."""

    def test_stop_when_not_started(self):
        """Stop on a not-started simulator does not crash."""
        with FocusLockTestContext() as ctx:
            ctx.initialize()
            ctx.simulator.stop()  # Should not raise
            assert ctx.simulator.status == "disabled"

    def test_double_start_is_noop(self):
        """Calling start() twice does not create extra threads."""
        with FocusLockTestContext() as ctx:
            ctx.initialize()
            ctx.simulator.start()
            assert ctx.wait_for_status("ready", timeout_s=3.0)

            # Second start should be a no-op
            ctx.simulator.start()

            # Only one thread should exist
            threads = [t for t in threading.enumerate() if "FocusLockSimulator" in t.name]
            assert len(threads) == 1

            ctx.simulator.stop()

    def test_set_lock_when_not_running(self):
        """set_lock() when not running does nothing."""
        with FocusLockTestContext() as ctx:
            ctx.initialize()
            ctx.simulator.set_lock()
            assert ctx.simulator.status == "disabled"

    def test_release_lock_when_not_running(self):
        """release_lock() when not running does nothing."""
        with FocusLockTestContext() as ctx:
            ctx.initialize()
            ctx.simulator.release_lock()
            assert ctx.simulator.status == "disabled"

    def test_adjust_target_when_not_locked(self):
        """adjust_target() when not locked logs warning and does nothing."""
        with FocusLockTestContext() as ctx:
            ctx.initialize()
            ctx.simulator.start()
            assert ctx.wait_for_status("ready", timeout_s=3.0)

            # Not locked yet (high buffer), adjust should be ignored
            original_target = ctx.simulator._target_displacement_um
            ctx.simulator.adjust_target(1.0)
            assert ctx.simulator._target_displacement_um == original_target

            ctx.simulator.stop()

    def test_context_manager_cleans_up_on_exception(self):
        """FocusLockTestContext cleans up even when exception occurs."""
        try:
            with FocusLockTestContext() as ctx:
                ctx.initialize()
                ctx.simulator.start()
                assert ctx.wait_for_status("ready", timeout_s=3.0)
                raise ValueError("test exception")
        except ValueError:
            pass

        # After context exit, simulator should be stopped
        # (no dangling threads)
        time.sleep(0.2)
        threads = [t for t in threading.enumerate() if "FocusLockSimulator" in t.name]
        assert len(threads) == 0, f"Dangling threads after exception: {threads}"
