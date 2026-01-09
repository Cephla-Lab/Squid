"""Tests for MCP context helpers."""

import pytest
from unittest.mock import MagicMock

from squid.mcp.context import check_mode_gate, format_position


class TestCheckModeGate:
    """Tests for check_mode_gate helper."""

    def test_allows_when_not_blocked(self):
        """Should not raise when mode gate allows commands."""
        mode_gate = MagicMock()
        mode_gate.is_blocked_for_commands.return_value = False

        # Should not raise
        check_mode_gate(mode_gate, "test operation")

    def test_raises_when_blocked(self):
        """Should raise RuntimeError when mode gate blocks commands."""
        mode_gate = MagicMock()
        mode_gate.is_blocked_for_commands.return_value = True
        mode_gate.get_current_mode.return_value = "ACQUIRING"

        with pytest.raises(RuntimeError) as exc_info:
            check_mode_gate(mode_gate, "move stage")

        assert "Cannot move stage" in str(exc_info.value)
        assert "ACQUIRING" in str(exc_info.value)


class TestFormatPosition:
    """Tests for format_position helper."""

    def test_formats_position(self):
        """Should format position with 4 decimal places."""
        result = format_position(1.23456789, 2.3456789, 3.456789)

        assert result == {
            "x_mm": 1.2346,
            "y_mm": 2.3457,
            "z_mm": 3.4568,
        }

    def test_handles_zeros(self):
        """Should handle zero values."""
        result = format_position(0.0, 0.0, 0.0)

        assert result == {"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0}

    def test_handles_negative(self):
        """Should handle negative values."""
        result = format_position(-10.5, -20.5, -30.5)

        assert result == {"x_mm": -10.5, "y_mm": -20.5, "z_mm": -30.5}
