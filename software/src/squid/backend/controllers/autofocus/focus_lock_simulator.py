from __future__ import annotations

import math
import random
import threading
import time
from collections import deque
from typing import Callable, Optional, Tuple, TYPE_CHECKING

import numpy as np

from squid.core.config.focus_lock import FocusLockConfig, FocusLockMode
from squid.backend.controllers.base import BaseController
from squid.core.events import (
    EventBus,
    FocusLockFrameUpdated,
    FocusLockMetricsUpdated,
    FocusLockModeChanged,
    FocusLockSearchProgress,
    FocusLockStatus,
    FocusLockStatusChanged,
    FocusLockWarning,
    AdjustFocusLockTargetCommand,
    LaserAFInitialized,
    PauseFocusLockCommand,
    PiezoPositionChanged,
    ReleaseFocusLockReferenceCommand,
    ResumeFocusLockCommand,
    SetFocusLockAutoSearchCommand,
    SetFocusLockModeCommand,
    SetFocusLockParamsCommand,
    SetFocusLockReferenceCommand,
    StartFocusLockCommand,
    StopFocusLockCommand,
    handles,
)

if TYPE_CHECKING:
    from squid.backend.controllers.autofocus import LaserAutofocusController
    from squid.backend.services.piezo_service import PiezoService


class FocusLockSimulator(BaseController):
    """Focus lock controller that uses the laser AF camera.

    Requires laser autofocus to be initialized before it can lock.
    Uses the same camera feed as the "Laser Based Focus" display.
    """

    def __init__(
        self,
        event_bus: EventBus,
        config: Optional[FocusLockConfig] = None,
        laser_autofocus: Optional["LaserAutofocusController"] = None,
        piezo_service: Optional["PiezoService"] = None,
        noise_level_um: float = 0.1,
        drift_rate_um_per_s: float = 0.05,
        snr_range: Tuple[float, float] = (8.0, 15.0),
    ) -> None:
        super().__init__(event_bus)
        self._config = config or FocusLockConfig()
        self._laser_af = laser_autofocus
        self._piezo_service = piezo_service
        self._laser_af_initialized = False

        self._buffer_length = self._config.buffer_length
        self._noise_level_um = float(noise_level_um)
        self._drift_rate_um_per_s = float(drift_rate_um_per_s)
        self._snr_range = snr_range

        self._mode: FocusLockMode = self._config.default_mode
        self._status: FocusLockStatus = "disabled"

        self._keep_running = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._is_running = False
        self._should_run = False  # True when started (even if paused)
        self._paused = False  # True when paused (loop keeps running, corrections skipped)
        self._lock_reference_active = False

        self._lock = threading.RLock()
        self._lock_buffer_fill = 0
        self._lock_loss_until: Optional[float] = None

        self._lock_loss_chance_per_s = 0.02
        self._lock_loss_duration_s = (0.5, 1.5)

        self._piezo_range_um = (100.0, 200.0)
        self._target_z_um = sum(self._piezo_range_um) / 2.0
        self._z_position_um = self._target_z_um
        self._drift_offset_um = 0.0

        self._z_error_um = 0.0
        self._spot_snr = 0.0
        self._spot_intensity = 0.0
        self._correlation = math.nan
        self._is_good_reading = False
        self._error_history = deque(maxlen=max(10, self._buffer_length))
        self._smoothed_quality = 1.0  # Exponential moving average of lock quality (0-1)

        # Target displacement to maintain when locked (set by set_lock())
        self._target_displacement_um = 0.0

        # Reference values saved when lock is set (for UI display)
        self._locked_spot_x: float = 0.0  # Spot X position when locked
        self._locked_piezo_um: float = 0.0  # Piezo position when locked

        self._last_published_status: Optional[FocusLockStatus] = None

        self._warning_debounce_s = 5.0
        self._last_warning_time: dict[str, float] = {}

        self._preview_handler: Optional[Callable] = None

        # Frame from laser AF camera (updated by _on_laser_af_frame)
        self._latest_frame: Optional[np.ndarray] = None
        self._latest_spot_x: float = 0.0
        self._latest_spot_y: float = 0.0
        self._preview_publish_period_s = 0.2  # Limit preview updates to 5 Hz.
        self._last_preview_publish_time = 0.0

        # Recovery state
        self._recovery_attempts_remaining = 0
        self._recovery_start_time: Optional[float] = None
        self._recovery_good_count = 0

        # Auto-search state (runtime toggle, can differ from config default)
        self._auto_search_enabled = self._config.auto_search_enabled
        self._search_phase: str = ""  # "last_position" or "sweep"
        self._search_position: float = 0.0
        self._search_start_time: Optional[float] = None

    @handles(LaserAFInitialized)
    def _on_laser_af_initialized(self, event: LaserAFInitialized) -> None:
        """Track laser AF initialization state."""
        self._laser_af_initialized = event.is_initialized and event.success

    @handles(PiezoPositionChanged)
    def _on_piezo_position_changed(self, event: PiezoPositionChanged) -> None:
        """Track piezo position from actual hardware/simulation."""
        with self._lock:
            self._z_position_um = event.position_um

    def start(self) -> None:
        """Start the focus lock loop.

        Requires laser AF to be initialized first.
        """
        if not self._laser_af_initialized:
            self._log.warning("Cannot start focus lock: laser AF not initialized")
            return

        publish_mode_on = False
        with self._lock:
            if self._is_running:
                return
            self._reset_lock_state()
            self._paused = False
            self._status = "ready"
            if self._mode != "on":
                self._mode = "on"
                publish_mode_on = True
            self._keep_running.set()
            self._thread = threading.Thread(
                target=self._simulation_loop,
                name="FocusLockSimulator",
                daemon=True,
            )
            self._is_running = True
            self._should_run = True
            self._thread.start()
        if publish_mode_on:
            self._event_bus.publish(FocusLockModeChanged(mode="on"))
        self._publish_status_if_needed()

    def stop(self) -> None:
        """Stop the simulator loop."""
        thread = None
        publish_mode_off = False
        with self._lock:
            self._keep_running.clear()
            thread = self._thread
            self._thread = None
            self._is_running = False
            self._should_run = False
            self._paused = False
            self._status = "disabled"
            if self._mode != "off":
                self._mode = "off"
                publish_mode_off = True
            self._reset_lock_state()
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        if publish_mode_off:
            self._event_bus.publish(FocusLockModeChanged(mode="off"))
        self._publish_status_if_needed()

    def pause(self) -> None:
        """Pause focus corrections without stopping the control loop.

        The control loop continues running to monitor focus state, but no
        piezo corrections are applied. Use this during image capture to
        prevent piezo jitter.
        """
        with self._lock:
            if not self._should_run:
                # Not started, nothing to pause
                return
            if self._paused:
                # Already paused
                return
            self._paused = True
            self._status = "paused"
        self._publish_status_if_needed()

    def resume(self) -> None:
        """Resume focus corrections after a pause.

        Resumes piezo corrections without resetting lock state.
        The lock continues from where it was before pause.
        """
        with self._lock:
            if not self._should_run:
                return
            if not self._paused:
                # Not paused, nothing to resume
                return
            self._paused = False
            # Restore status based on lock state
            if self._lock_buffer_fill >= self._buffer_length:
                self._status = "locked"
            elif self._lock_buffer_fill > 0:
                self._status = "ready"
            else:
                self._status = "ready"
        self._publish_status_if_needed()

    def shutdown(self) -> None:
        """Shutdown simulator and stop background thread."""
        self.stop()
        super().shutdown()

    def set_mode(self, mode: FocusLockMode) -> None:
        """Set focus lock operating mode."""
        if mode not in ("off", "on"):
            raise ValueError(f"Invalid focus lock mode: {mode}")
        should_stop = False
        with self._lock:
            if mode == self._mode:
                return
            self._mode = mode
            if mode == "off":
                should_stop = True
        self._event_bus.publish(FocusLockModeChanged(mode=mode))
        if should_stop:
            self.stop()

    def adjust_target(self, delta_um: float) -> None:
        """Adjust focus lock target displacement by a relative offset.

        This changes the target spot position that the feedback loop tries to maintain.
        A positive delta moves the target "up" (higher displacement value).
        """
        with self._lock:
            if self._status != "locked":
                self._log.warning("Cannot adjust target: not locked")
                return
            self._target_displacement_um += float(delta_um)

    def set_lock_reference(self) -> None:
        """Engage lock at the current displacement reference."""
        self.set_lock()

    def release_lock_reference(self) -> None:
        """Release active lock reference and return to ready state."""
        self.release_lock()

    def wait_for_lock(self, timeout_s: float = 5.0) -> bool:
        """Wait for lock to be achieved."""
        if not self.is_running:
            return False
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.status == "locked":
                return True
            if not self.is_running:
                return False
            time.sleep(0.02)
        return False

    def set_preview_handler(self, handler: Callable) -> None:
        """Set preview handler (no-op for simulator)."""
        self._preview_handler = handler

    @property
    def mode(self) -> str:
        """Current mode: off | on."""
        with self._lock:
            return self._mode

    @property
    def is_running(self) -> bool:
        """Whether the lock loop is active."""
        with self._lock:
            return self._is_running

    @property
    def is_active(self) -> bool:
        """True if focus lock was started and should be used (even if paused)."""
        with self._lock:
            return self._should_run

    @property
    def status(self) -> str:
        """Current status: disabled | searching | locked | lost | paused."""
        with self._lock:
            return self._status

    @handles(SetFocusLockModeCommand)
    def _on_set_mode_command(self, cmd: SetFocusLockModeCommand) -> None:
        self.set_mode(cmd.mode)

    @handles(StartFocusLockCommand)
    def _on_start_command(self, cmd: StartFocusLockCommand) -> None:
        self.start()

    @handles(StopFocusLockCommand)
    def _on_stop_command(self, cmd: StopFocusLockCommand) -> None:
        self.stop()

    @handles(PauseFocusLockCommand)
    def _on_pause_command(self, cmd: PauseFocusLockCommand) -> None:
        self.pause()

    @handles(ResumeFocusLockCommand)
    def _on_resume_command(self, cmd: ResumeFocusLockCommand) -> None:
        self.resume()

    @handles(AdjustFocusLockTargetCommand)
    def _on_adjust_target_command(self, cmd: AdjustFocusLockTargetCommand) -> None:
        self.adjust_target(cmd.delta_um)

    @handles(SetFocusLockReferenceCommand)
    def _on_set_reference_command(self, cmd: SetFocusLockReferenceCommand) -> None:
        self.set_lock_reference()

    @handles(ReleaseFocusLockReferenceCommand)
    def _on_release_reference_command(self, cmd: ReleaseFocusLockReferenceCommand) -> None:
        self.release_lock_reference()

    @handles(SetFocusLockAutoSearchCommand)
    def _on_auto_search_command(self, cmd: SetFocusLockAutoSearchCommand) -> None:
        with self._lock:
            self._auto_search_enabled = cmd.enabled

    @handles(SetFocusLockParamsCommand)
    def _on_set_params(self, cmd: SetFocusLockParamsCommand) -> None:
        updates = {}
        if cmd.buffer_length is not None:
            updates["buffer_length"] = cmd.buffer_length
        if cmd.recovery_attempts is not None:
            updates["recovery_attempts"] = cmd.recovery_attempts
        if cmd.min_spot_snr is not None:
            updates["min_spot_snr"] = cmd.min_spot_snr
        if cmd.acquire_threshold_um is not None:
            updates["acquire_threshold_um"] = cmd.acquire_threshold_um
        if cmd.maintain_threshold_um is not None:
            updates["maintain_threshold_um"] = cmd.maintain_threshold_um
        if not updates:
            return

        with self._lock:
            self._config = self._config.model_copy(update=updates)
            self._buffer_length = self._config.buffer_length
            self._lock_buffer_fill = min(self._lock_buffer_fill, self._buffer_length)
            self._error_history = deque(
                self._error_history,
                maxlen=max(10, self._buffer_length),
            )
            # Force a status publish so UI gets updated lock buffer length.
            self._last_published_status = None
        self._publish_status_if_needed()

    def set_lock(self) -> None:
        """Lock at current position.

        Saves the current displacement as the target to maintain.
        The feedback loop will move the piezo to keep displacement at this value.
        """
        # Snapshot one measurement so lock reference isn't taken from stale zeros
        # if user presses Lock immediately after Start.
        result = None
        if self._laser_af is not None:
            try:
                result = self._laser_af.measure_displacement_continuous()
            except Exception:
                result = None

        # Get current piezo position
        if self._piezo_service is not None:
            current_piezo = self._piezo_service.get_position()
        else:
            current_piezo = self._z_position_um

        with self._lock:
            if not self._is_running:
                return
            self._lock_reference_active = True
            if result is not None:
                if result.spot_x_px is not None and result.spot_y_px is not None:
                    self._latest_spot_x = result.spot_x_px
                    self._latest_spot_y = result.spot_y_px
                if not math.isnan(result.displacement_um):
                    self._z_error_um = result.displacement_um
            self._status = "locked"
            self._lock_buffer_fill = self._buffer_length
            # Save current displacement as target to maintain
            self._target_displacement_um = self._z_error_um
            # Save reference values for UI display
            self._locked_spot_x = self._latest_spot_x
            self._locked_piezo_um = current_piezo
        self._publish_status_if_needed()

    def release_lock(self) -> None:
        """Release the lock and return to ready state."""
        with self._lock:
            if not self._is_running:
                return
            self._lock_reference_active = False
            self._status = "ready"
            self._lock_buffer_fill = 0
        self._publish_status_if_needed()

    def _reset_lock_state(self) -> None:
        self._lock_reference_active = False
        self._lock_buffer_fill = 0
        self._lock_loss_until = None
        self._target_displacement_um = 0.0
        # Reset recovery state
        self._recovery_attempts_remaining = 0
        self._recovery_start_time = None
        self._recovery_good_count = 0
        # Reset search state
        self._search_phase = ""
        self._search_position = 0.0
        self._search_start_time = None

    def _start_search(self) -> None:
        """Start the piezo sweep search to re-find focus."""
        self._status = "searching"
        self._search_phase = "last_position"
        self._search_position = self._locked_piezo_um
        self._search_start_time = time.monotonic()

    def _get_piezo_range(self) -> Tuple[float, float]:
        """Get the piezo range from service or defaults."""
        if self._piezo_service is not None:
            return self._piezo_service.get_range()
        return self._piezo_range_um

    def _get_search_bounds(self) -> Tuple[float, float]:
        """Get the search bounds: ±search_range_um around last position, clamped to safe range."""
        min_um, max_um = self._get_piezo_range()
        # Safety clamp based on percentage of piezo range
        safe_min = min_um + (max_um - min_um) * self._config.search_min_percent / 100.0
        safe_max = min_um + (max_um - min_um) * self._config.search_max_percent / 100.0
        # Local search around last position
        local_min = self._locked_piezo_um - self._config.search_range_um
        local_max = self._locked_piezo_um + self._config.search_range_um
        # Clamp to safe range
        search_min = max(safe_min, local_min)
        search_max = min(safe_max, local_max)
        return search_min, search_max

    def _search_step(self) -> None:
        """Perform one step of the piezo sweep search.

        Called from the simulation loop when status is "searching".
        """
        if self._status != "searching":
            return

        if self._search_start_time is not None:
            elapsed = time.monotonic() - self._search_start_time
            if elapsed >= self._config.search_timeout_s:
                self._log.warning("Focus search timed out after %.1fs", elapsed)
                if self._piezo_service is not None:
                    self._piezo_service.move_to(self._locked_piezo_um)
                self._lock_buffer_fill = 0
                self._status = "lost"
                self._publish_status_if_needed()
                return

        if self._piezo_service is None:
            # Can't search without piezo control
            self._status = "lost"
            self._log.warning("Search aborted - no piezo service")
            self._publish_status_if_needed()
            return

        # Move piezo to current search position (piezo service adds 10ms settle time)
        self._piezo_service.move_to(self._search_position)

        # Additional settle time for measurement stability
        settle_time_s = self._config.search_settle_ms / 1000.0
        time.sleep(settle_time_s)

        # Get a measurement and check if we found focus
        if self._laser_af is not None:
            result = self._laser_af.measure_displacement_continuous()

            # Check if this is a good enough reading to lock
            if self._is_good_search_reading(result):
                self.set_lock()
                return

        # Get search bounds
        search_min, search_max = self._get_search_bounds()

        # Publish search progress
        self._event_bus.publish(
            FocusLockSearchProgress(
                phase=self._search_phase,
                current_position_um=self._search_position,
                search_min_um=search_min,
                search_max_um=search_max,
            )
        )

        if self._search_phase == "last_position":
            # Last position didn't work, start local sweep
            self._search_phase = "sweep"
            self._search_position = search_min
        else:
            # Continue sweep
            self._search_position += self._config.search_step_um
            if self._search_position > search_max:
                # Search failed — restore piezo to last known good position
                if self._piezo_service is not None:
                    self._piezo_service.move_to(self._locked_piezo_um)
                self._status = "lost"
                self._lock_buffer_fill = 0
                self._log.warning("Focus lock lost - search sweep completed without finding lock")
                self._publish_status_if_needed()

    def _is_good_search_reading(self, result) -> bool:
        """Check if a laser AF result is good enough to establish lock during search.

        During search, we just need a valid spot with good SNR - we don't check against
        the old target since we're trying to find a new lock position.
        """
        if result.spot_x_px is None:
            return False
        if math.isnan(result.displacement_um):
            return False
        snr = result.spot_snr if result.spot_snr else 0.0
        if snr < self._config.min_spot_snr:
            return False
        return True

    def _control_fn(self, error_um: float) -> float:
        """Proportional control with variable gain.

        Uses same control function as ContinuousFocusLockController.
        Gain increases for small errors (fine adjustment) and decreases for large errors.
        """
        dx = (error_um ** 2) / self._config.gain_sigma
        scale = self._config.gain_max - self._config.gain
        p_term = self._config.gain + scale * math.exp(-dx)
        return -p_term * error_um

    def _has_laser_reference(self) -> bool:
        if self._laser_af is None:
            return False
        props = getattr(self._laser_af, "laser_af_properties", None)
        if props is None:
            return False
        has_reference = bool(getattr(props, "has_reference", False))
        x_reference = getattr(props, "x_reference", None)
        return has_reference and x_reference is not None

    def _pixel_to_um(self) -> float:
        if self._laser_af is None:
            return 0.2
        props = getattr(self._laser_af, "laser_af_properties", None)
        if props is None:
            return 0.2
        value = float(getattr(props, "pixel_to_um", 0.2))
        return value if value > 0 else 0.2

    def _apply_piezo_correction(self) -> None:
        """Apply piezo correction to maintain target displacement.

        Applies correction when locked or recovering (to help recovery succeed).
        """
        if self._piezo_service is None:
            return

        with self._lock:
            if self._status not in ("locked", "recovering"):
                return
            # Use the same lock error used by state/metrics logic.
            error_um = self._z_error_um - self._target_displacement_um

        # Only correct if error is significant
        if abs(error_um) < 0.01:  # 10nm deadband
            return

        # Compute correction
        correction = self._control_fn(error_um)

        # Get current position and compute new position
        current_pos = self._piezo_service.get_position()
        new_pos = current_pos + correction

        # Clamp to piezo range
        min_um, max_um = self._piezo_service.get_range()
        new_pos = max(min_um, min(max_um, new_pos))

        # Apply correction
        self._piezo_service.move_to_fast(new_pos)

    def _simulation_loop(self) -> None:
        """Main loop that gets frames from the laser AF camera."""
        period = 1.0 / self._config.loop_rate_hz
        metrics_period = 1.0 / self._config.metrics_rate_hz
        last_metrics_time = 0.0

        try:
            while self._keep_running.is_set():
                start = time.monotonic()

                # Check if paused - skip corrections but keep monitoring
                with self._lock:
                    is_paused = self._paused

                if is_paused:
                    # When paused: still measure for monitoring,
                    # but don't apply corrections or update lock state
                    if self._laser_af is not None:
                        result = self._laser_af.measure_displacement_continuous()
                        # Update spot position and frame for display only
                        with self._lock:
                            if result.spot_x_px is not None and result.spot_y_px is not None:
                                self._latest_spot_x = result.spot_x_px
                                self._latest_spot_y = result.spot_y_px
                            self._spot_snr = result.spot_snr if result.spot_snr else 0.0
                            self._spot_intensity = result.spot_intensity if result.spot_intensity else 0.0
                            self._latest_frame = result.image
                elif self._status == "searching":
                    # Handle search mode separately
                    self._search_step()
                elif self._laser_af is not None:
                    # Normal operation: get measurement from laser AF
                    result = self._laser_af.measure_displacement_continuous()
                    self._update_from_laser_af_result(result)

                    # Apply piezo correction when locked or recovering
                    self._apply_piezo_correction()

                # Publish metrics at configured rate (always, regardless of pause state)
                now = time.monotonic()
                if now - last_metrics_time >= metrics_period:
                    self._publish_metrics()
                    last_metrics_time = now

                elapsed = time.monotonic() - start
                time.sleep(max(0.0, period - elapsed))
        except Exception:
            self._log.exception("Focus lock simulator loop crashed; disabling lock")
        finally:
            self._keep_running.clear()
            with self._lock:
                self._is_running = False
                self._should_run = False
                self._paused = False
                self._status = "disabled"
            self._publish_status_if_needed()

    def _update_from_laser_af_result(self, result) -> None:
        """Update internal state from laser AF measurement result."""
        with self._lock:
            if result.spot_x_px is not None and result.spot_y_px is not None:
                self._latest_spot_x = result.spot_x_px
                self._latest_spot_y = result.spot_y_px
            self._spot_snr = result.spot_snr if result.spot_snr else 0.0
            self._spot_intensity = result.spot_intensity if result.spot_intensity else 0.0
            self._correlation = result.correlation if result.correlation is not None else math.nan
            self._latest_frame = result.image

            # Determine which displacement model to use:
            # 1) referenced displacement from laser AF, or
            # 2) spot-offset fallback (sim mode without AF reference).
            has_reference = self._has_laser_reference()
            if has_reference:
                if not math.isnan(result.displacement_um):
                    self._z_error_um = result.displacement_um
            elif (
                self._status in ("locked", "recovering")
                and result.spot_x_px is not None
            ):
                # No AF reference: track offset relative to lock spot.
                spot_offset_um = (result.spot_x_px - self._locked_spot_x) * self._pixel_to_um()
                self._z_error_um = self._target_displacement_um + spot_offset_um
            elif self._status in ("ready", "lost"):
                # During acquisition without reference, treat current reading as baseline.
                self._z_error_um = 0.0

            # Store lock error (not raw displacement) for RMS calculation
            if self._status in ("locked", "recovering"):
                lock_error = self._z_error_um - self._target_displacement_um
            else:
                lock_error = self._z_error_um
            self._error_history.append(lock_error)

            # Use hysteresis thresholds: tighter to acquire, looser to maintain
            if self._status in ("locked", "recovering"):
                threshold_um = self._config.maintain_threshold_um
            else:
                threshold_um = self._config.acquire_threshold_um

            # Update reading quality
            error_for_quality = lock_error
            if result.spot_x_px is None or math.isnan(result.displacement_um):
                self._is_good_reading = False
            elif abs(error_for_quality) > threshold_um:
                self._is_good_reading = False
            elif self._status in ("locked", "recovering"):
                # Keep lock based on displacement stability; report low-SNR as warning.
                self._is_good_reading = True
            else:
                # Acquire/reacquire: match real controller criteria.
                if math.isnan(self._spot_snr) or self._spot_snr < self._config.min_spot_snr:
                    self._is_good_reading = False
                elif math.isnan(self._correlation):
                    self._is_good_reading = True
                else:
                    corr_threshold = 0.7
                    try:
                        corr_threshold = float(
                            self._laser_af.laser_af_properties.correlation_threshold
                        )
                    except Exception:
                        pass
                    self._is_good_reading = self._correlation >= corr_threshold

            status_changed = False

            # State machine for lock management
            if not self._lock_reference_active:
                self._lock_buffer_fill = 0
                if self._status in ("locked", "recovering", "lost", "searching"):
                    self._status = "ready"
                    status_changed = True
            elif self._status == "locked":
                if self._is_good_reading:
                    # Good reading - maintain lock
                    self._lock_buffer_fill = min(self._lock_buffer_fill + 1, self._buffer_length)
                else:
                    # Bad reading - enter recovery mode instead of immediate loss
                    self._status = "recovering"
                    self._recovery_attempts_remaining = self._config.recovery_attempts
                    self._recovery_start_time = time.monotonic()
                    self._recovery_good_count = 0
                    status_changed = True

            elif self._status == "recovering":
                if self._is_good_reading:
                    # Good reading during recovery - count towards recovery
                    self._recovery_good_count += 1
                    if self._recovery_good_count >= self._config.recovery_window_readings:
                        # Successfully recovered!
                        self._status = "locked"
                        self._lock_buffer_fill = self._config.recovery_window_readings
                        status_changed = True
                else:
                    # Bad reading during recovery - reset good count
                    self._recovery_good_count = 0
                    if self._recovery_start_time is None:
                        self._recovery_start_time = time.monotonic()
                    elapsed = time.monotonic() - self._recovery_start_time
                    if elapsed >= self._config.recovery_delay_s:
                        # Recovery delay elapsed, try next attempt
                        self._recovery_attempts_remaining -= 1
                        if self._recovery_attempts_remaining <= 0:
                            # All recovery attempts exhausted
                            if self._auto_search_enabled:
                                self._start_search()
                                status_changed = True
                            else:
                                self._status = "lost"
                                self._lock_buffer_fill = 0
                                status_changed = True
                        else:
                            # Reset timer for next attempt
                            self._recovery_start_time = time.monotonic()

            elif self._status == "ready":
                if self._is_good_reading:
                    self._lock_buffer_fill = min(self._lock_buffer_fill + 1, self._buffer_length)
                    if self._lock_buffer_fill >= self._buffer_length:
                        self._status = "locked"
                        self._locked_spot_x = self._latest_spot_x
                        if self._piezo_service is not None:
                            self._locked_piezo_um = self._piezo_service.get_position()
                        else:
                            self._locked_piezo_um = self._z_position_um
                        status_changed = True
                else:
                    self._lock_buffer_fill = max(0, self._lock_buffer_fill - 1)

            elif self._status == "lost":
                if self._is_good_reading:
                    self._lock_buffer_fill = min(self._lock_buffer_fill + 1, self._buffer_length)
                    if self._lock_buffer_fill >= self._buffer_length:
                        self._status = "locked"
                        self._locked_spot_x = self._latest_spot_x
                        if self._piezo_service is not None:
                            self._locked_piezo_um = self._piezo_service.get_position()
                        else:
                            self._locked_piezo_um = self._z_position_um
                        status_changed = True
                else:
                    self._lock_buffer_fill = 0

        # Publish status change outside of lock
        if status_changed:
            self._publish_status_if_needed()

    def _publish_status_if_needed(self) -> None:
        event = None
        with self._lock:
            if self._status == self._last_published_status:
                return
            self._last_published_status = self._status
            event = FocusLockStatusChanged(
                is_locked=self._status == "locked",
                status=self._status,
                lock_buffer_fill=self._lock_buffer_fill,
                lock_buffer_length=self._buffer_length,
            )
        self._event_bus.publish(event)

    def _publish_metrics(self) -> None:
        # Get piezo position from service if available
        if self._piezo_service is not None:
            z_position = self._piezo_service.get_position()
        else:
            z_position = self._z_position_um

        with self._lock:
            if not self._error_history:
                z_error_rms = abs(self._z_error_um)
            else:
                z_error_rms = math.sqrt(
                    sum(err * err for err in self._error_history) / len(self._error_history)
                )
            drift_rate = self._drift_rate_um_per_s + random.uniform(-0.01, 0.01)

            # Calculate RMS-based quality (0-1, higher is better)
            # Quality = 1 when RMS = 0, Quality = 0 when RMS >= threshold
            quality_threshold = self._config.offset_threshold_um
            current_quality = max(0.0, 1.0 - z_error_rms / quality_threshold)

            # Keep quality aligned with lock state so UI doesn't report
            # excellent quality while searching/lost.
            if self._status in ("lost", "searching"):
                current_quality = 0.0
            elif self._status == "recovering":
                current_quality = min(current_quality, 0.4)
            elif not self._is_good_reading:
                current_quality = min(current_quality, 0.2)

            # Update smoothed quality with exponential moving average, but force
            # immediate drop when lock is explicitly lost/searching.
            if self._status in ("lost", "searching"):
                self._smoothed_quality = 0.0
            else:
                alpha = 0.3  # Smoothing factor (lower = smoother, higher = faster response)
                self._smoothed_quality = alpha * current_quality + (1 - alpha) * self._smoothed_quality

            # Calculate offsets from lock reference (only meaningful when locked)
            if self._status == "locked":
                spot_offset_px = self._latest_spot_x - self._locked_spot_x
                piezo_delta_um = z_position - self._locked_piezo_um
                # Error is displacement minus target - what feedback loop minimizes
                lock_error_um = self._z_error_um - self._target_displacement_um
            else:
                spot_offset_px = math.nan
                piezo_delta_um = math.nan
                lock_error_um = self._z_error_um  # Raw displacement when not locked

            metrics = FocusLockMetricsUpdated(
                z_error_um=lock_error_um,  # Error from target (should be ~0 when locked)
                z_position_um=z_position,
                spot_snr=self._spot_snr,
                spot_intensity=self._spot_intensity,
                z_error_rms_um=z_error_rms,
                drift_rate_um_per_s=drift_rate,
                is_good_reading=self._is_good_reading,
                correlation=self._correlation,
                spot_offset_px=spot_offset_px,
                piezo_delta_um=piezo_delta_um,
                lock_buffer_fill=self._lock_buffer_fill,
                lock_buffer_length=self._buffer_length,
                lock_quality=self._smoothed_quality,
            )

        self._event_bus.publish(metrics)
        self._publish_frame()
        self._maybe_publish_warnings(z_position)

    def _publish_frame(self) -> None:
        """Publish the laser AF camera frame with spot position and simulated noise."""
        now = time.monotonic()
        if now - self._last_preview_publish_time < self._preview_publish_period_s:
            return

        # Get latest frame from measurement result
        frame = self._latest_frame
        if frame is None:
            return

        # Use spot position from latest measurement result
        with self._lock:
            spot_x = self._latest_spot_x
            spot_y = self._latest_spot_y

        # Check if spot detection was valid
        h, w = frame.shape[:2]
        spot_valid = spot_x != 0.0 or spot_y != 0.0
        if not spot_valid:
            # Use center of frame if no spot detected
            spot_x = w / 2.0
            spot_y = h / 2.0
        else:
            # Add jitter to spot position (simulating measurement noise)
            spot_jitter_px = 1.5  # pixels of jitter
            spot_x += random.gauss(0, spot_jitter_px)
            spot_y += random.gauss(0, spot_jitter_px * 0.5)  # Less vertical jitter

        # Add noise to the frame
        frame = self._add_frame_noise(frame)

        self._last_preview_publish_time = now
        frame_h, frame_w = frame.shape[:2]
        self._event_bus.publish(
            FocusLockFrameUpdated(
                frame=frame,
                spot_x_px=float(spot_x),
                spot_y_px=float(spot_y),
                frame_width=frame_w,
                frame_height=frame_h,
                spot_valid=spot_valid,
            )
        )

    def _add_frame_noise(self, frame: np.ndarray) -> np.ndarray:
        """Add simulated noise to the frame for more realistic display."""
        # Make a copy to avoid modifying the original
        frame = frame.copy()

        # Add Gaussian noise (simulating camera read noise)
        noise_std = 8.0  # Standard deviation of noise
        noise = np.random.normal(0, noise_std, frame.shape).astype(np.float32)
        frame = np.clip(frame.astype(np.float32) + noise, 0, 255).astype(np.uint8)

        return frame

    def _maybe_publish_warnings(self, z_position_um: float) -> None:
        # Get range from piezo service if available, otherwise use defaults
        if self._piezo_service is not None:
            low_limit, high_limit = self._piezo_service.get_range()
        else:
            low_limit, high_limit = self._piezo_range_um

        margin = self._config.piezo_warning_margin_um
        if z_position_um <= low_limit + margin:
            self._publish_warning("piezo_low", "Piezo approaching lower limit")
        elif z_position_um >= high_limit - margin:
            self._publish_warning("piezo_high", "Piezo approaching upper limit")

        if self.status == "lost":
            self._publish_warning("signal_lost", "Focus lock signal lost")

    def _publish_warning(self, warning_type: str, message: str) -> None:
        now = time.monotonic()
        last_time = self._last_warning_time.get(warning_type)
        if last_time is not None and now - last_time < self._warning_debounce_s:
            return
        self._last_warning_time[warning_type] = now
        self._event_bus.publish(FocusLockWarning(warning_type=warning_type, message=message))
