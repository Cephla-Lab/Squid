"""Tests for check_ram_available_with_error_dialog function."""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

import pytest

import _def
from squid.ui.widgets.base import check_ram_available_with_error_dialog


class _FakeMultiPointController:
    """Fake MultiPointController for testing RAM check."""

    def __init__(self, ram_estimate: int = 0) -> None:
        self._ram_estimate = ram_estimate

    def get_estimated_mosaic_ram_bytes(self) -> int:
        return self._ram_estimate


class TestCheckRamAvailableWithErrorDialog:
    """Test suite for check_ram_available_with_error_dialog function."""

    def test_performance_mode_bypasses_check(self) -> None:
        """Test that RAM check is skipped when performance mode is enabled."""
        controller = _FakeMultiPointController(ram_estimate=1_000_000_000)  # 1GB
        logger = logging.getLogger("test")

        # When performance mode is enabled, should always return True (skip check)
        result = check_ram_available_with_error_dialog(
            controller, logger, performance_mode=True
        )
        assert result is True

    def test_sufficient_ram_passes(self) -> None:
        """Test that check passes when sufficient RAM is available."""
        # Small RAM requirement
        controller = _FakeMultiPointController(ram_estimate=1024)  # 1KB
        logger = logging.getLogger("test")

        # Mock psutil to return plenty of RAM
        mock_vmem = MagicMock()
        mock_vmem.available = 8 * 1024 * 1024 * 1024  # 8GB

        with patch("psutil.virtual_memory", return_value=mock_vmem):
            result = check_ram_available_with_error_dialog(
                controller, logger, performance_mode=False
            )
            assert result is True

    def test_insufficient_ram_fails(self) -> None:
        """Test that check fails when insufficient RAM is available."""
        # Large RAM requirement
        controller = _FakeMultiPointController(ram_estimate=10 * 1024 * 1024 * 1024)  # 10GB
        logger = logging.getLogger("test")

        # Mock psutil to return low available RAM
        mock_vmem = MagicMock()
        mock_vmem.available = 1024  # Only 1KB available

        with patch("psutil.virtual_memory", return_value=mock_vmem):
            with patch("squid.ui.widgets.base.error_dialog"):  # Mock dialog to avoid GUI
                result = check_ram_available_with_error_dialog(
                    controller, logger, performance_mode=False
                )
                assert result is False

    def test_zero_estimate_passes(self) -> None:
        """Test that check passes when RAM estimate is 0 (no regions or napari disabled)."""
        controller = _FakeMultiPointController(ram_estimate=0)
        logger = logging.getLogger("test")

        # Mock psutil to return low available RAM
        mock_vmem = MagicMock()
        mock_vmem.available = 1024  # Only 1KB available

        with patch("psutil.virtual_memory", return_value=mock_vmem):
            # Should pass since 0 bytes required (0 * 1.15 = 0 < 1024)
            result = check_ram_available_with_error_dialog(
                controller, logger, performance_mode=False
            )
            assert result is True

    def test_factor_of_safety_is_applied(self) -> None:
        """Test that factor of safety is applied to RAM estimate."""
        base_estimate = 1000
        controller = _FakeMultiPointController(ram_estimate=base_estimate)
        logger = logging.getLogger("test")

        # Mock psutil to return RAM that's exactly equal to base estimate
        # With factor_of_safety > 1, this should fail
        mock_vmem = MagicMock()
        mock_vmem.available = base_estimate  # Exactly equal to base estimate

        with patch("psutil.virtual_memory", return_value=mock_vmem):
            with patch("squid.ui.widgets.base.error_dialog"):
                # With default factor_of_safety=1.15, should fail (needs 15% more)
                result = check_ram_available_with_error_dialog(
                    controller, logger, factor_of_safety=1.15, performance_mode=False
                )
                assert result is False

                # With factor_of_safety=1.0, should pass (exact match)
                mock_vmem.available = base_estimate
                result = check_ram_available_with_error_dialog(
                    controller, logger, factor_of_safety=1.0, performance_mode=False
                )
                assert result is True

    def test_error_dialog_called_on_failure(self) -> None:
        """Test that error_dialog is called when RAM check fails."""
        controller = _FakeMultiPointController(ram_estimate=10 * 1024 * 1024 * 1024)  # 10GB
        logger = logging.getLogger("test")

        mock_vmem = MagicMock()
        mock_vmem.available = 1024  # Only 1KB available

        with patch("psutil.virtual_memory", return_value=mock_vmem):
            with patch("squid.ui.widgets.base.error_dialog") as mock_error_dialog:
                result = check_ram_available_with_error_dialog(
                    controller, logger, performance_mode=False
                )
                assert result is False
                # Verify error_dialog was called
                mock_error_dialog.assert_called_once()
                # Verify the title contains "RAM"
                call_args = mock_error_dialog.call_args
                assert "RAM" in call_args.kwargs.get("title", "") or "RAM" in str(call_args)
