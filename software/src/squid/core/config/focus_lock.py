"""
Focus lock configuration model.

Provides validated, immutable configuration for continuous focus lock system.

Usage:
    from squid.core.config.focus_lock import FocusLockConfig

    # Use defaults from _def.py
    config = FocusLockConfig()

    # Override specific values
    config = FocusLockConfig(gain=0.6, buffer_length=7)
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

import _def

# Type alias for focus lock modes
FocusLockMode = Literal["off", "on"]


class FocusLockConfig(BaseModel):
    """Configuration for continuous focus lock system.

    Attributes:
        gain: Base proportional gain for feedback loop.
        gain_max: Maximum gain (used in gain scheduling).
        buffer_length: Number of consecutive good readings required for lock.
        offset_threshold_um: Maximum displacement error to count as "good" reading.
        min_spot_snr: Minimum spot SNR to count as valid measurement.
        loop_rate_hz: Control loop rate (Hz).
        metrics_rate_hz: Rate to publish metrics events (Hz).
        piezo_warning_margin_um: Distance from piezo limits to trigger warning.
        default_mode: Initial operating mode.

        Recovery parameters:
        recovery_attempts: Number of retry cycles before declaring lost.
        recovery_delay_s: Delay between retry attempts.
        recovery_window_readings: Good readings needed to recover.

        Hysteresis thresholds:
        acquire_threshold_um: Tighter threshold to acquire lock.
        maintain_threshold_um: Looser threshold to maintain lock.

        Auto-search parameters:
        auto_search_enabled: Enable auto-search on lock loss.
        search_range_um: Search range ±um around last known position.
        search_min_percent: Safety clamp - lower limit as % of piezo range (0-100).
        search_max_percent: Safety clamp - upper limit as % of piezo range (0-100).
        search_step_um: Step size during sweep.
        search_settle_ms: Settling time per step.
    """

    model_config = ConfigDict(frozen=True)

    # Core parameters
    gain: float = _def.FOCUS_LOCK_GAIN
    gain_max: float = _def.FOCUS_LOCK_GAIN_MAX
    gain_sigma: float = _def.FOCUS_LOCK_GAIN_SIGMA
    buffer_length: int = _def.FOCUS_LOCK_BUFFER_LENGTH
    offset_threshold_um: float = _def.FOCUS_LOCK_OFFSET_THRESHOLD_UM
    min_spot_snr: float = _def.FOCUS_LOCK_MIN_SPOT_SNR
    loop_rate_hz: float = _def.FOCUS_LOCK_LOOP_RATE_HZ
    metrics_rate_hz: float = _def.FOCUS_LOCK_METRICS_RATE_HZ
    piezo_warning_margin_um: float = _def.FOCUS_LOCK_PIEZO_WARNING_MARGIN_UM
    piezo_critical_margin_um: float = _def.FOCUS_LOCK_PIEZO_CRITICAL_MARGIN_UM
    default_mode: FocusLockMode = _def.FOCUS_LOCK_DEFAULT_MODE

    # PI controller parameters
    ki: float = _def.FOCUS_LOCK_KI
    integral_limit_um: float = _def.FOCUS_LOCK_INTEGRAL_LIMIT_UM

    # NaN holdover parameters
    max_nan_holdover_cycles: int = _def.FOCUS_LOCK_MAX_NAN_HOLDOVER_CYCLES
    nan_holdover_decay: float = _def.FOCUS_LOCK_NAN_HOLDOVER_DECAY

    # Recovery parameters
    recovery_attempts: int = _def.FOCUS_LOCK_RECOVERY_ATTEMPTS
    recovery_delay_s: float = _def.FOCUS_LOCK_RECOVERY_DELAY_S
    recovery_window_readings: int = _def.FOCUS_LOCK_RECOVERY_WINDOW_READINGS

    # Hysteresis thresholds
    acquire_threshold_um: float = _def.FOCUS_LOCK_ACQUIRE_THRESHOLD_UM
    maintain_threshold_um: float = _def.FOCUS_LOCK_MAINTAIN_THRESHOLD_UM

    # Auto-search parameters
    auto_search_enabled: bool = _def.FOCUS_LOCK_AUTO_SEARCH_ENABLED
    search_range_um: float = _def.FOCUS_LOCK_SEARCH_RANGE_UM
    search_min_percent: float = _def.FOCUS_LOCK_SEARCH_MIN_PERCENT
    search_max_percent: float = _def.FOCUS_LOCK_SEARCH_MAX_PERCENT
    search_step_um: float = _def.FOCUS_LOCK_SEARCH_STEP_UM
    search_settle_ms: float = _def.FOCUS_LOCK_SEARCH_SETTLE_MS
    search_timeout_s: float = _def.FOCUS_LOCK_SEARCH_TIMEOUT_S

    @field_validator("default_mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        """Validate that mode is one of the allowed values."""
        if v not in ("off", "on"):
            raise ValueError(f"Invalid mode: {v}. Must be 'off' or 'on'")
        return v

    @field_validator("gain", "gain_max", "gain_sigma")
    @classmethod
    def validate_gain(cls, v: float) -> float:
        """Validate gain is positive."""
        if v <= 0:
            raise ValueError(f"Gain must be positive, got {v}")
        return v

    @field_validator("buffer_length")
    @classmethod
    def validate_buffer_length(cls, v: int) -> int:
        """Validate buffer length is positive."""
        if v < 1:
            raise ValueError(f"buffer_length must be >= 1, got {v}")
        return v

    @field_validator("loop_rate_hz", "metrics_rate_hz")
    @classmethod
    def validate_rate(cls, v: float) -> float:
        """Validate rate is positive."""
        if v <= 0:
            raise ValueError(f"Rate must be > 0, got {v}")
        return v

    @field_validator("recovery_attempts", "recovery_window_readings")
    @classmethod
    def validate_recovery_counts(cls, v: int) -> int:
        """Validate recovery counts are positive."""
        if v < 1:
            raise ValueError(f"Value must be >= 1, got {v}")
        return v

    @field_validator("search_range_um")
    @classmethod
    def validate_search_range(cls, v: float) -> float:
        """Validate search range is positive."""
        if v <= 0:
            raise ValueError(f"search_range_um must be > 0, got {v}")
        return v

    @field_validator("search_step_um")
    @classmethod
    def validate_search_step(cls, v: float) -> float:
        """Validate search step is positive."""
        if v <= 0:
            raise ValueError(f"search_step_um must be > 0, got {v}")
        return v

    @field_validator("search_min_percent", "search_max_percent")
    @classmethod
    def validate_search_percent(cls, v: float) -> float:
        """Validate search percentages are in range 0-100."""
        if v < 0 or v > 100:
            raise ValueError(f"search percent must be between 0 and 100, got {v}")
        return v

    @model_validator(mode="after")
    def _validate_cross_fields(self) -> "FocusLockConfig":
        if self.gain > self.gain_max:
            raise ValueError(f"gain ({self.gain}) must be <= gain_max ({self.gain_max})")
        if self.acquire_threshold_um <= 0:
            raise ValueError(f"acquire_threshold_um must be positive, got {self.acquire_threshold_um}")
        if self.acquire_threshold_um > self.maintain_threshold_um:
            raise ValueError(
                f"acquire_threshold_um ({self.acquire_threshold_um}) must be <= "
                f"maintain_threshold_um ({self.maintain_threshold_um})"
            )
        if self.search_min_percent >= self.search_max_percent:
            raise ValueError(
                f"search_min_percent ({self.search_min_percent}) must be < "
                f"search_max_percent ({self.search_max_percent})"
            )
        if self.recovery_delay_s < 0:
            raise ValueError(f"recovery_delay_s must be non-negative, got {self.recovery_delay_s}")
        if self.piezo_critical_margin_um <= 0:
            raise ValueError(
                f"piezo_critical_margin_um must be positive, got {self.piezo_critical_margin_um}"
            )
        if self.piezo_critical_margin_um >= self.piezo_warning_margin_um:
            raise ValueError(
                f"piezo_critical_margin_um ({self.piezo_critical_margin_um}) must be < "
                f"piezo_warning_margin_um ({self.piezo_warning_margin_um})"
            )
        if self.ki < 0:
            raise ValueError(f"ki must be non-negative, got {self.ki}")
        if self.integral_limit_um <= 0:
            raise ValueError(f"integral_limit_um must be positive, got {self.integral_limit_um}")
        if self.nan_holdover_decay < 0 or self.nan_holdover_decay > 1:
            raise ValueError(f"nan_holdover_decay must be in [0, 1], got {self.nan_holdover_decay}")
        if self.max_nan_holdover_cycles < 0:
            raise ValueError(
                f"max_nan_holdover_cycles must be non-negative, got {self.max_nan_holdover_cycles}"
            )
        if self.search_timeout_s <= 0:
            raise ValueError(f"search_timeout_s must be positive, got {self.search_timeout_s}")
        return self
