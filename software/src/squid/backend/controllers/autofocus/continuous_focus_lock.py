from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Deque, Optional, Tuple

import numpy as np

import squid.core.logging
from squid.backend.controllers.autofocus.laser_auto_focus_controller import (
    LaserAFResult,
    LaserAutofocusController,
)
from squid.backend.services.piezo_service import PiezoService
from squid.core.config.focus_lock import FocusLockConfig, FocusLockMode
from squid.core.events import (
    AdjustFocusLockTargetCommand,
    EventBus,
    FocusLockFrameUpdated,
    FocusLockMetricsUpdated,
    FocusLockModeChanged,
    FocusLockSearchProgress,
    FocusLockStatusChanged,
    FocusLockWarning,
    PauseFocusLockCommand,
    ReleaseFocusLockReferenceCommand,
    ResumeFocusLockCommand,
    SetFocusLockAutoSearchCommand,
    SetFocusLockModeCommand,
    SetFocusLockReferenceCommand,
    StartFocusLockCommand,
    StopFocusLockCommand,
)


class ContinuousFocusLockController:
    """Continuous closed-loop focus lock using laser autofocus."""

    def __init__(
        self,
        laser_af: LaserAutofocusController,
        piezo_service: PiezoService,
        event_bus: EventBus,
        config: Optional[FocusLockConfig] = None,
    ) -> None:
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self._laser_af = laser_af
        self._piezo_service = piezo_service
        self._event_bus = event_bus
        self._config = config or FocusLockConfig()

        self._mode: FocusLockMode = self._config.default_mode
        self._status: str = "disabled"
        self._laser_on = False
        self._paused = False
        self._running = False
        self._should_run = False
        self._target_um = 0.0

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._lock_buffer_fill = 0
        self._error_history: Deque[float] = deque(maxlen=max(10, self._config.buffer_length * 3))
        self._drift_history: Deque[tuple[float, float]] = deque(maxlen=30)
        self._smoothed_quality = 1.0  # Exponential moving average of lock quality (0-1)

        self._warning_debounce_s = 5.0
        self._warning_last_time: dict[str, float] = {}

        # Frame preview configuration - match laser AF camera aspect ratio
        self._preview_crop_size = (200, 160)  # (width, height) of cropped preview

        # Recovery state
        self._recovery_attempts_remaining = 0
        self._recovery_start_time: Optional[float] = None
        self._recovery_good_count = 0

        # Auto-search state (runtime toggle, can differ from config default)
        self._auto_search_enabled = self._config.auto_search_enabled
        self._search_phase: str = ""  # "last_position" or "sweep"
        self._search_position: float = 0.0

        # Reference piezo position when lock was set (for search recovery)
        self._locked_piezo_um: float = 0.0

        self._subscribe_to_bus()

    def _subscribe_to_bus(self) -> None:
        self._event_bus.subscribe(SetFocusLockModeCommand, self._on_set_mode_command)
        self._event_bus.subscribe(StartFocusLockCommand, self._on_start_command)
        self._event_bus.subscribe(StopFocusLockCommand, self._on_stop_command)
        self._event_bus.subscribe(PauseFocusLockCommand, self._on_pause_command)
        self._event_bus.subscribe(ResumeFocusLockCommand, self._on_resume_command)
        self._event_bus.subscribe(
            AdjustFocusLockTargetCommand, self._on_adjust_target_command
        )
        self._event_bus.subscribe(
            SetFocusLockReferenceCommand, self._on_set_reference_command
        )
        self._event_bus.subscribe(
            ReleaseFocusLockReferenceCommand, self._on_release_reference_command
        )
        self._event_bus.subscribe(
            SetFocusLockAutoSearchCommand, self._on_auto_search_command
        )

    @property
    def mode(self) -> FocusLockMode:
        with self._lock:
            return self._mode

    @property
    def status(self) -> str:
        with self._lock:
            return self._status

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    @property
    def is_active(self) -> bool:
        """True if focus lock was started and should be used (even if paused)."""
        with self._lock:
            return self._should_run

    def set_mode(self, mode: FocusLockMode) -> None:
        if mode not in ("off", "on"):
            raise ValueError(f"Invalid focus lock mode: {mode}")
        with self._lock:
            if mode == self._mode:
                return
            self._mode = mode
        self._event_bus.publish(FocusLockModeChanged(mode=mode))

        if mode == "off":
            self.stop()

    def start(self, target_um: float = 0.0) -> None:
        with self._lock:
            if self._running:
                return
            self._target_um = float(target_um)
            self._paused = False
            self._should_run = True
            self._stop_event.clear()
            self._reset_lock_state()
            self._status = "ready"
            self._running = True
            self._thread = threading.Thread(
                target=self._control_loop,
                name="ContinuousFocusLock",
                daemon=True,
            )
            self._turn_on_laser()
            self._thread.start()
        self._set_status("ready")

    def stop(self) -> None:
        thread = None
        with self._lock:
            if not self._running and self._status == "disabled":
                return
            self._paused = False
            self._running = False
            self._should_run = False
            self._stop_event.set()
            thread = self._thread
            self._thread = None
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        self._cleanup()
        self._set_status("disabled")

    def pause(self) -> None:
        """Pause focus corrections without stopping the control loop.

        The control loop continues running to monitor focus state, but no
        piezo corrections are applied. The laser stays on for sensing.
        Use this during image capture to prevent piezo jitter.
        """
        with self._lock:
            if not self._should_run:
                # Not started, nothing to pause
                return
            if self._paused:
                # Already paused
                return
            self._paused = True
        self._set_status("paused")

    def resume(self) -> None:
        """Resume focus corrections after a pause.

        Resumes piezo corrections without resetting lock state.
        The lock continues from where it was before pause.
        """
        with self._lock:
            if not self._should_run:
                self._log.debug("resume() called but lock is not running, ignoring")
                return
            if not self._paused:
                # Not paused, nothing to resume
                return
            self._paused = False
            # Restore status based on lock state
            if self._lock_buffer_fill >= self._config.buffer_length:
                new_status = "locked"
            elif self._lock_buffer_fill > 0:
                new_status = "ready"
            else:
                new_status = "ready"
        self._set_status(new_status)

    def shutdown(self) -> None:
        self.stop()

    def wait_for_lock(self, timeout_s: float = 5.0) -> bool:
        if not self.is_running:
            return False
        start = time.monotonic()
        while time.monotonic() - start < timeout_s:
            if self.status == "locked":
                return True
            if self._stop_event.wait(0.05):
                return False
        self._log.warning(f"wait_for_lock timed out after {timeout_s}s")
        return False

    def adjust_target(self, delta_um: float) -> None:
        with self._lock:
            self._target_um += float(delta_um)

    def _on_set_mode_command(self, cmd: SetFocusLockModeCommand) -> None:
        self.set_mode(cmd.mode)

    def _on_start_command(self, cmd: StartFocusLockCommand) -> None:
        self.start(target_um=cmd.target_um)

    def _on_stop_command(self, cmd: StopFocusLockCommand) -> None:
        self.stop()

    def _on_pause_command(self, cmd: PauseFocusLockCommand) -> None:
        self.pause()

    def _on_resume_command(self, cmd: ResumeFocusLockCommand) -> None:
        self.resume()

    def _on_adjust_target_command(self, cmd: AdjustFocusLockTargetCommand) -> None:
        self.adjust_target(cmd.delta_um)

    def _on_set_reference_command(self, cmd: SetFocusLockReferenceCommand) -> None:
        self._set_lock_reference()

    def _on_release_reference_command(self, cmd: ReleaseFocusLockReferenceCommand) -> None:
        self._release_lock_reference()

    def _on_auto_search_command(self, cmd: SetFocusLockAutoSearchCommand) -> None:
        with self._lock:
            self._auto_search_enabled = cmd.enabled
        self._log.info(f"Auto-search {'enabled' if cmd.enabled else 'disabled'}")

    def _set_lock_reference(self) -> None:
        """Set the lock reference at current position."""
        with self._lock:
            if not self._running:
                return
            self._locked_piezo_um = self._piezo_service.get_position()
            self._status = "locked"
            self._lock_buffer_fill = self._config.buffer_length
            self._log.info(f"Focus lock set at piezo={self._locked_piezo_um:.1f} um")
        self._set_status("locked")

    def _release_lock_reference(self) -> None:
        """Release the lock and return to ready state."""
        with self._lock:
            if not self._running:
                return
            self._lock_buffer_fill = 0
        self._set_status("ready")

    def _control_fn(self, error_um: float) -> float:
        sigma = 0.5
        dx = (error_um ** 2) / sigma
        scale = self._config.gain_max - self._config.gain
        p_term = self._config.gain_max - scale * math.exp(-dx)
        return -p_term * error_um

    def _control_loop(self) -> None:
        period = 1.0 / self._config.loop_rate_hz
        metrics_period = 1.0 / self._config.metrics_rate_hz
        last_metrics_time = 0.0

        try:
            while not self._stop_event.is_set():
                start = time.monotonic()

                # Check if paused - skip corrections but keep monitoring
                with self._lock:
                    is_paused = self._paused

                if is_paused:
                    # When paused: still measure and publish metrics for monitoring,
                    # but don't apply corrections or update lock state
                    result = self._laser_af.measure_displacement_continuous()
                    error_um = self._compute_error(result)
                    is_good = self._is_good_reading(result, error_um)

                    now = time.monotonic()
                    if now - last_metrics_time >= metrics_period:
                        self._publish_metrics(result, error_um, is_good)
                        last_metrics_time = now
                elif self._status == "searching":
                    # Handle search mode separately
                    self._search_step()
                else:
                    # Normal operation
                    result = self._laser_af.measure_displacement_continuous()

                    error_um = self._compute_error(result)
                    is_good = self._is_good_reading(result, error_um)

                    self._update_lock_state(is_good, error_um)

                    # Apply correction when locked or recovering - even if error is large
                    # This allows the piezo to catch up during rapid movements
                    if self._status in ("locked", "recovering") and not math.isnan(error_um):
                        correction = self._control_fn(error_um)
                        current_pos = self._piezo_service.get_position()
                        new_pos = self._clamp_to_range(current_pos + correction)
                        self._piezo_service.move_to_fast(new_pos)

                    now = time.monotonic()
                    if now - last_metrics_time >= metrics_period:
                        self._publish_metrics(result, error_um, is_good)
                        last_metrics_time = now

                    self._check_warnings(result, error_um)

                elapsed = time.monotonic() - start
                time.sleep(max(0.0, period - elapsed))
        except Exception:
            self._log.exception("Control loop crashed")
        finally:
            self._cleanup()

    def _compute_error(self, result: LaserAFResult) -> float:
        if math.isnan(result.displacement_um):
            return float("nan")
        return result.displacement_um - self._target_um

    def _is_good_reading(self, result: LaserAFResult, error_um: float) -> bool:
        if math.isnan(result.displacement_um):
            return False
        if math.isnan(result.spot_snr) or result.spot_snr < self._config.min_spot_snr:
            return False
        # Use hysteresis: tighter threshold to acquire lock, looser to maintain
        if self._status in ("locked", "recovering"):
            threshold_um = self._config.maintain_threshold_um
        else:
            threshold_um = self._config.acquire_threshold_um
        if math.isnan(error_um) or abs(error_um) > threshold_um:
            return False
        if result.correlation is not None:
            if math.isnan(result.correlation):
                return False
            threshold = self._laser_af.laser_af_properties.correlation_threshold
            if result.correlation < threshold:
                return False
        return True

    def _update_lock_state(self, is_good: bool, error_um: float) -> None:
        """Update lock state machine with recovery and search support."""
        new_status: Optional[str] = None

        if self._status == "locked":
            if is_good:
                # Good reading - maintain lock
                self._lock_buffer_fill = min(
                    self._lock_buffer_fill + 1, self._config.buffer_length
                )
            else:
                # Bad reading - enter recovery mode instead of immediate loss
                new_status = "recovering"
                self._recovery_attempts_remaining = self._config.recovery_attempts
                self._recovery_start_time = time.monotonic()
                self._recovery_good_count = 0
                self._log.info(
                    f"Entering recovery mode: {self._recovery_attempts_remaining} attempts, "
                    f"error={error_um:.3f}um"
                )

        elif self._status == "recovering":
            if is_good:
                # Good reading during recovery - count towards recovery
                self._recovery_good_count += 1
                if self._recovery_good_count >= self._config.recovery_window_readings:
                    # Successfully recovered!
                    new_status = "locked"
                    self._lock_buffer_fill = self._config.recovery_window_readings
                    self._log.info("Focus lock recovered")
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
                            new_status = "searching"
                        else:
                            new_status = "lost"
                            self._lock_buffer_fill = 0
                            self._log.warning("Focus lock lost - recovery attempts exhausted")
                    else:
                        # Reset timer for next attempt
                        self._recovery_start_time = time.monotonic()
                        self._log.debug(
                            f"Recovery attempt failed, {self._recovery_attempts_remaining} remaining"
                        )

        elif self._status == "ready":
            # Building up to lock
            if is_good:
                self._lock_buffer_fill = min(
                    self._lock_buffer_fill + 1, self._config.buffer_length
                )
                if self._lock_buffer_fill >= self._config.buffer_length:
                    new_status = "locked"
                    self._locked_piezo_um = self._piezo_service.get_position()
                    self._log.info(f"Focus lock achieved at piezo={self._locked_piezo_um:.1f}um")
            else:
                self._lock_buffer_fill = max(0, self._lock_buffer_fill - 1)

        elif self._status == "lost":
            # Try to recover from lost state
            if is_good:
                self._lock_buffer_fill += 1
                if self._lock_buffer_fill >= self._config.buffer_length:
                    new_status = "locked"
                    self._locked_piezo_um = self._piezo_service.get_position()
                    self._log.info("Focus lock re-acquired")
            else:
                self._lock_buffer_fill = 0

        if new_status is not None:
            self._set_status(new_status)

    def _start_search(self) -> None:
        """Start the piezo sweep search to re-find focus."""
        self._status = "searching"
        self._search_phase = "last_position"
        self._search_position = self._locked_piezo_um
        self._log.info(f"Starting search at last position: {self._search_position:.1f} um")

    def _get_search_bounds(self) -> tuple[float, float]:
        """Get the search bounds: ±search_range_um around last position, clamped to safe range."""
        min_um, max_um = self._piezo_service.get_range()
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
        """Perform one step of the piezo sweep search."""
        if self._status != "searching":
            return

        # Move piezo to current search position
        self._piezo_service.move_to(self._search_position)
        time.sleep(self._config.search_settle_ms / 1000.0)

        # Get a measurement and check if we found focus
        result = self._laser_af.measure_displacement_continuous()
        if self._is_good_search_reading(result):
            # Found it! Set lock at this position
            self._log.info(f"Focus found at {self._search_position:.1f} um")
            self._locked_piezo_um = self._search_position
            self._target_um = result.displacement_um
            self._status = "locked"
            self._lock_buffer_fill = self._config.buffer_length
            self._set_status("locked")
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
            self._log.info(f"Last position failed, starting sweep from {search_min:.1f} to {search_max:.1f} um")
        else:
            # Continue sweep
            self._search_position += self._config.search_step_um
            if self._search_position > search_max:
                # Search failed
                self._status = "lost"
                self._lock_buffer_fill = 0
                self._log.warning("Focus lock lost - search sweep completed without finding lock")
                self._set_status("lost")

    def _is_good_search_reading(self, result: LaserAFResult) -> bool:
        """Check if a laser AF result is good enough to establish lock during search.

        During search, we just need a valid spot with good SNR - we don't check against
        the old target since we're trying to find a new lock position.
        """
        if math.isnan(result.displacement_um):
            return False
        if math.isnan(result.spot_snr) or result.spot_snr < self._config.min_spot_snr:
            return False
        # Check correlation if available
        if result.correlation is not None:
            if math.isnan(result.correlation):
                return False
            threshold = self._laser_af.laser_af_properties.correlation_threshold
            if result.correlation < threshold:
                return False
        return True

    def _publish_metrics(self, result: LaserAFResult, error_um: float, is_good: bool) -> None:
        if not math.isnan(error_um):
            self._error_history.append(error_um)
            self._drift_history.append((time.monotonic(), error_um))

        z_error_rms = float("nan")
        if self._error_history:
            z_error_rms = math.sqrt(
                sum(err * err for err in self._error_history) / len(self._error_history)
            )

        drift_rate = 0.0
        if len(self._drift_history) >= 2:
            t0, e0 = self._drift_history[0]
            t1, e1 = self._drift_history[-1]
            if t1 > t0:
                drift_rate = (e1 - e0) / (t1 - t0)

        # Calculate RMS-based quality (0-1, higher is better)
        quality_threshold = self._config.offset_threshold_um
        if not math.isnan(z_error_rms):
            current_quality = max(0.0, 1.0 - z_error_rms / quality_threshold)
        else:
            current_quality = 1.0

        # Update smoothed quality with exponential moving average
        alpha = 0.1  # Smoothing factor (lower = smoother)
        self._smoothed_quality = alpha * current_quality + (1 - alpha) * self._smoothed_quality

        z_position = self._piezo_service.get_position()

        self._event_bus.publish(
            FocusLockMetricsUpdated(
                z_error_um=error_um,
                z_position_um=z_position,
                spot_snr=result.spot_snr,
                spot_intensity=result.spot_intensity,
                z_error_rms_um=z_error_rms,
                drift_rate_um_per_s=drift_rate,
                is_good_reading=is_good,
                correlation=result.correlation if result.correlation is not None else float("nan"),
                lock_buffer_fill=self._lock_buffer_fill,
                lock_buffer_length=self._config.buffer_length,
                lock_quality=self._smoothed_quality,
            )
        )

        # Publish frame preview
        self._publish_frame(result)

    def _publish_frame(self, result: LaserAFResult) -> None:
        """Crop and publish the AF spot frame for preview."""
        frame = getattr(self._laser_af, "image", None)
        if frame is None:
            return

        raw_spot_x = result.spot_x_px
        raw_spot_y = result.spot_y_px
        spot_valid = raw_spot_x is not None and raw_spot_y is not None
        if spot_valid and raw_spot_x is not None and raw_spot_y is not None:
            spot_x = float(raw_spot_x)
            spot_y = float(raw_spot_y)
        else:
            # Use center if no spot detected (still show frame, but no marker)
            h, w = frame.shape[:2]
            spot_x = w / 2.0
            spot_y = h / 2.0
            spot_valid = False

        cropped, crop_spot_x, crop_spot_y = self._crop_around_spot(
            frame, spot_x, spot_y
        )
        if cropped is None:
            return

        crop_h, crop_w = cropped.shape[:2]
        self._event_bus.publish(
            FocusLockFrameUpdated(
                frame=cropped,
                spot_x_px=crop_spot_x,
                spot_y_px=crop_spot_y,
                frame_width=crop_w,
                frame_height=crop_h,
                spot_valid=spot_valid,
            )
        )

    def _crop_around_spot(
        self, frame: np.ndarray, spot_x: float, spot_y: float
    ) -> Tuple[Optional[np.ndarray], float, float]:
        """Crop frame around spot position.

        Returns:
            Tuple of (cropped_frame, spot_x_in_crop, spot_y_in_crop).
            Returns (None, 0, 0) if frame is invalid.
        """
        if frame is None or frame.size == 0:
            return None, 0.0, 0.0

        h, w = frame.shape[:2]
        crop_w, crop_h = self._preview_crop_size

        # Calculate crop region centered on spot
        x1 = int(max(0, spot_x - crop_w // 2))
        y1 = int(max(0, spot_y - crop_h // 2))
        x2 = min(w, x1 + crop_w)
        y2 = min(h, y1 + crop_h)

        # Adjust if crop goes beyond bounds
        if x2 - x1 < crop_w:
            x1 = max(0, x2 - crop_w)
        if y2 - y1 < crop_h:
            y1 = max(0, y2 - crop_h)

        cropped = frame[y1:y2, x1:x2]

        # Calculate spot position in cropped frame
        crop_spot_x = spot_x - x1
        crop_spot_y = spot_y - y1

        return cropped, crop_spot_x, crop_spot_y

    def _check_warnings(self, result: LaserAFResult, error_um: float) -> None:
        min_um, max_um = self._piezo_service.get_range()
        position = self._piezo_service.get_position()
        margin = self._config.piezo_warning_margin_um

        if position <= min_um + margin:
            self._publish_warning("piezo_low", "Piezo approaching lower limit")
        elif position >= max_um - margin:
            self._publish_warning("piezo_high", "Piezo approaching upper limit")

        if self._status == "lost":
            self._publish_warning("signal_lost", "Focus lock signal lost")
        if result.spot_snr < self._config.min_spot_snr:
            self._publish_warning("snr_low", "Spot SNR below threshold")

    def _publish_warning(self, warning_type: str, message: str) -> None:
        now = time.monotonic()
        last_time = self._warning_last_time.get(warning_type)
        if last_time is not None and now - last_time < self._warning_debounce_s:
            return
        self._warning_last_time[warning_type] = now
        self._event_bus.publish(FocusLockWarning(warning_type=warning_type, message=message))

    def _clamp_to_range(self, position_um: float) -> float:
        min_um, max_um = self._piezo_service.get_range()
        return max(min_um, min(max_um, position_um))

    def _reset_lock_state(self) -> None:
        self._lock_buffer_fill = 0
        self._error_history.clear()
        self._drift_history.clear()
        # Reset recovery state
        self._recovery_attempts_remaining = 0
        self._recovery_start_time = None
        self._recovery_good_count = 0
        # Reset search state
        self._search_phase = ""
        self._search_position = 0.0

    def _set_status(self, status: str) -> None:
        with self._lock:
            if status == self._status:
                return
            self._status = status
            buffer_fill = self._lock_buffer_fill
            buffer_length = self._config.buffer_length
        self._event_bus.publish(
            FocusLockStatusChanged(
                is_locked=status == "locked",
                status=status,
                lock_buffer_fill=buffer_fill,
                lock_buffer_length=buffer_length,
            )
        )

    def _turn_on_laser(self) -> None:
        if not self._laser_on:
            self._laser_af.turn_on_laser(bypass_mode_gate=True)
            self._laser_on = True

    def _turn_off_laser(self) -> None:
        if self._laser_on:
            self._laser_af.turn_off_laser(bypass_mode_gate=True)
            self._laser_on = False

    def _cleanup(self) -> None:
        self._turn_off_laser()
