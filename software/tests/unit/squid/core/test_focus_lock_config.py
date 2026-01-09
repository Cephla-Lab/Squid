"""Tests for FocusLockConfig."""

import pytest
from pydantic import ValidationError

from squid.core.config.focus_lock import FocusLockConfig


class TestFocusLockConfig:
    """Tests for FocusLockConfig."""

    def test_defaults_from_def(self):
        """Config should use defaults from _def.py."""
        config = FocusLockConfig()
        assert config.gain == 0.5
        assert config.gain_max == 0.7
        assert config.buffer_length == 5
        assert config.offset_threshold_um == 0.5
        assert config.min_spot_snr == 5.0
        assert config.loop_rate_hz == 30
        assert config.metrics_rate_hz == 10
        assert config.piezo_warning_margin_um == 20.0
        assert config.default_mode == "off"

    def test_custom_values(self):
        """Config should accept custom values."""
        config = FocusLockConfig(
            gain=0.6,
            gain_max=0.8,
            buffer_length=7,
            default_mode="on",
        )
        assert config.gain == 0.6
        assert config.gain_max == 0.8
        assert config.buffer_length == 7
        assert config.default_mode == "on"

    def test_invalid_mode_rejected(self):
        """Invalid mode should raise ValidationError."""
        with pytest.raises(ValidationError):
            FocusLockConfig(default_mode="invalid")

    def test_valid_modes(self):
        """All valid modes should be accepted."""
        for mode in ["off", "on"]:
            config = FocusLockConfig(default_mode=mode)
            assert config.default_mode == mode

    def test_gain_positive(self):
        """Gain must be positive."""
        with pytest.raises(ValidationError):
            FocusLockConfig(gain=0)
        with pytest.raises(ValidationError):
            FocusLockConfig(gain=-0.5)

    def test_buffer_length_positive(self):
        """Buffer length must be >= 1."""
        with pytest.raises(ValidationError):
            FocusLockConfig(buffer_length=0)
        with pytest.raises(ValidationError):
            FocusLockConfig(buffer_length=-1)

    def test_rate_positive(self):
        """Rates must be > 0."""
        with pytest.raises(ValidationError):
            FocusLockConfig(loop_rate_hz=0)
        with pytest.raises(ValidationError):
            FocusLockConfig(metrics_rate_hz=-1)

    def test_config_is_frozen(self):
        """Config should be immutable."""
        config = FocusLockConfig()
        with pytest.raises(ValidationError):
            config.gain = 0.9
