"""Tests for z-stack mode helpers in widgets.py."""

import logging
from unittest.mock import MagicMock, Mock

import pytest

from control.widgets import (
    ZStackMode,
    calculate_z_range,
    update_autofocus_checkboxes,
    log_af_restriction_warnings,
)


class TestZStackModeEnum:
    """Tests for ZStackMode enum properties."""

    def test_from_bottom_allows_laser_af(self):
        assert ZStackMode.FROM_BOTTOM.allows_laser_af is True

    def test_from_bottom_disallows_contrast_af(self):
        assert ZStackMode.FROM_BOTTOM.allows_contrast_af is False

    def test_from_center_allows_contrast_af(self):
        assert ZStackMode.FROM_CENTER.allows_contrast_af is True

    def test_from_center_disallows_laser_af(self):
        assert ZStackMode.FROM_CENTER.allows_laser_af is False

    def test_from_top_disallows_both_af(self):
        assert ZStackMode.FROM_TOP.allows_contrast_af is False
        assert ZStackMode.FROM_TOP.allows_laser_af is False

    def test_set_range_disallows_both_af(self):
        assert ZStackMode.SET_RANGE.allows_contrast_af is False
        assert ZStackMode.SET_RANGE.allows_laser_af is False

    def test_worker_config_index_from_bottom(self):
        assert ZStackMode.FROM_BOTTOM.worker_config_index == 0

    def test_worker_config_index_from_center(self):
        assert ZStackMode.FROM_CENTER.worker_config_index == 1

    def test_worker_config_index_from_top(self):
        assert ZStackMode.FROM_TOP.worker_config_index == 2

    def test_worker_config_index_set_range_uses_from_bottom(self):
        """SET_RANGE should use FROM_BOTTOM config for worker."""
        assert ZStackMode.SET_RANGE.worker_config_index == ZStackMode.FROM_BOTTOM.value
        assert ZStackMode.SET_RANGE.worker_config_index == 0

    def test_int_enum_values(self):
        """Verify enum values match Z_STACKING_CONFIG_MAP indices."""
        assert int(ZStackMode.FROM_BOTTOM) == 0
        assert int(ZStackMode.FROM_CENTER) == 1
        assert int(ZStackMode.FROM_TOP) == 2
        assert int(ZStackMode.SET_RANGE) == 3


class TestCalculateZRange:
    """Tests for calculate_z_range() function."""

    def test_from_bottom_z_range(self):
        """FROM_BOTTOM: z_range starts at current position, goes up."""
        current_z = 1.0  # mm
        dz = 10.0  # μm
        nz = 5

        min_z, max_z = calculate_z_range(current_z, dz, nz, ZStackMode.FROM_BOTTOM)

        # Total travel = 10μm * (5-1) = 40μm = 0.04mm
        assert min_z == 1.0  # Start at current
        assert max_z == pytest.approx(1.04)  # End at current + total

    def test_from_center_z_range(self):
        """FROM_CENTER: z_range centered around current position."""
        current_z = 1.0  # mm
        dz = 10.0  # μm
        nz = 5

        min_z, max_z = calculate_z_range(current_z, dz, nz, ZStackMode.FROM_CENTER)

        # Total travel = 40μm = 0.04mm, half = 0.02mm
        assert min_z == pytest.approx(0.98)  # current - half
        assert max_z == pytest.approx(1.02)  # current + half

    def test_from_top_z_range(self):
        """FROM_TOP: z_range ends at current position, starts below."""
        current_z = 1.0  # mm
        dz = 10.0  # μm
        nz = 5

        min_z, max_z = calculate_z_range(current_z, dz, nz, ZStackMode.FROM_TOP)

        # Total travel = 40μm = 0.04mm
        assert min_z == pytest.approx(0.96)  # current - total
        assert max_z == 1.0  # End at current

    def test_single_z_slice(self):
        """With nz=1, total travel is 0."""
        current_z = 1.0
        dz = 10.0
        nz = 1

        min_z, max_z = calculate_z_range(current_z, dz, nz, ZStackMode.FROM_BOTTOM)

        assert min_z == 1.0
        assert max_z == 1.0

    def test_large_z_stack(self):
        """Test with larger z-stack."""
        current_z = 2.0  # mm
        dz = 1.0  # μm
        nz = 101  # 100 steps

        min_z, max_z = calculate_z_range(current_z, dz, nz, ZStackMode.FROM_CENTER)

        # Total travel = 1μm * 100 = 100μm = 0.1mm, half = 0.05mm
        assert min_z == pytest.approx(1.95)
        assert max_z == pytest.approx(2.05)


class TestUpdateAutofocusCheckboxes:
    """Tests for update_autofocus_checkboxes() function."""

    def create_mock_checkbox(self, checked=False, enabled=True):
        """Create a mock QCheckBox."""
        checkbox = MagicMock()
        checkbox.isChecked.return_value = checked
        checkbox.isEnabled.return_value = enabled
        return checkbox

    def test_both_allowed(self):
        """When both AF types allowed, both checkboxes enabled."""
        contrast_cb = self.create_mock_checkbox()
        laser_cb = self.create_mock_checkbox()

        update_autofocus_checkboxes(
            contrast_af_allowed=True,
            laser_af_allowed=True,
            contrast_af_checkbox=contrast_cb,
            laser_af_checkbox=laser_cb,
        )

        contrast_cb.setEnabled.assert_called_with(True)
        laser_cb.setEnabled.assert_called_with(True)

    def test_neither_allowed(self):
        """When neither AF type allowed, both checkboxes disabled."""
        contrast_cb = self.create_mock_checkbox()
        laser_cb = self.create_mock_checkbox()

        update_autofocus_checkboxes(
            contrast_af_allowed=False,
            laser_af_allowed=False,
            contrast_af_checkbox=contrast_cb,
            laser_af_checkbox=laser_cb,
        )

        contrast_cb.setEnabled.assert_called_with(False)
        laser_cb.setEnabled.assert_called_with(False)

    def test_contrast_only(self):
        """FROM_CENTER mode: contrast allowed, laser disabled."""
        contrast_cb = self.create_mock_checkbox()
        laser_cb = self.create_mock_checkbox()

        update_autofocus_checkboxes(
            contrast_af_allowed=True,
            laser_af_allowed=False,
            contrast_af_checkbox=contrast_cb,
            laser_af_checkbox=laser_cb,
        )

        contrast_cb.setEnabled.assert_called_with(True)
        laser_cb.setEnabled.assert_called_with(False)

    def test_laser_only(self):
        """FROM_BOTTOM mode: laser allowed, contrast disabled."""
        contrast_cb = self.create_mock_checkbox()
        laser_cb = self.create_mock_checkbox()

        update_autofocus_checkboxes(
            contrast_af_allowed=False,
            laser_af_allowed=True,
            contrast_af_checkbox=contrast_cb,
            laser_af_checkbox=laser_cb,
        )

        contrast_cb.setEnabled.assert_called_with(False)
        laser_cb.setEnabled.assert_called_with(True)

    def test_uncheck_when_disabled_contrast(self):
        """Contrast checkbox should be unchecked when disabled."""
        contrast_cb = self.create_mock_checkbox(checked=True)
        laser_cb = self.create_mock_checkbox()

        update_autofocus_checkboxes(
            contrast_af_allowed=False,
            laser_af_allowed=True,
            contrast_af_checkbox=contrast_cb,
            laser_af_checkbox=laser_cb,
        )

        contrast_cb.setChecked.assert_called_with(False)

    def test_uncheck_when_disabled_laser(self):
        """Laser checkbox should be unchecked when disabled."""
        contrast_cb = self.create_mock_checkbox()
        laser_cb = self.create_mock_checkbox(checked=True)

        update_autofocus_checkboxes(
            contrast_af_allowed=True,
            laser_af_allowed=False,
            contrast_af_checkbox=contrast_cb,
            laser_af_checkbox=laser_cb,
        )

        laser_cb.setChecked.assert_called_with(False)

    def test_no_uncheck_when_not_checked(self):
        """Should not call setChecked if checkbox wasn't checked."""
        contrast_cb = self.create_mock_checkbox(checked=False)
        laser_cb = self.create_mock_checkbox(checked=False)

        update_autofocus_checkboxes(
            contrast_af_allowed=False,
            laser_af_allowed=False,
            contrast_af_checkbox=contrast_cb,
            laser_af_checkbox=laser_cb,
        )

        contrast_cb.setChecked.assert_not_called()
        laser_cb.setChecked.assert_not_called()


class TestLogAfRestrictionWarnings:
    """Tests for log_af_restriction_warnings() function."""

    def test_no_warnings_when_unchanged(self):
        """No warnings when AF settings match."""
        log = MagicMock()

        log_af_restriction_warnings(
            yaml_contrast_af=True,
            yaml_laser_af=False,
            actual_contrast_af=True,
            actual_laser_af=False,
            log=log,
        )

        log.warning.assert_not_called()

    def test_warning_when_contrast_af_disabled(self):
        """Warning logged when contrast AF was disabled."""
        log = MagicMock()

        log_af_restriction_warnings(
            yaml_contrast_af=True,
            yaml_laser_af=False,
            actual_contrast_af=False,
            actual_laser_af=False,
            log=log,
        )

        log.warning.assert_called_once()
        call_args = log.warning.call_args[0][0]
        assert "Contrast AF was disabled" in call_args
        assert "From Center" in call_args

    def test_warning_when_laser_af_disabled(self):
        """Warning logged when laser AF was disabled."""
        log = MagicMock()

        log_af_restriction_warnings(
            yaml_contrast_af=False,
            yaml_laser_af=True,
            actual_contrast_af=False,
            actual_laser_af=False,
            log=log,
        )

        log.warning.assert_called_once()
        call_args = log.warning.call_args[0][0]
        assert "Laser AF was disabled" in call_args
        assert "From Bottom" in call_args

    def test_warning_when_both_disabled(self):
        """Warning includes both AF types when both disabled."""
        log = MagicMock()

        log_af_restriction_warnings(
            yaml_contrast_af=True,
            yaml_laser_af=True,
            actual_contrast_af=False,
            actual_laser_af=False,
            log=log,
        )

        log.warning.assert_called_once()
        call_args = log.warning.call_args[0][0]
        assert "Contrast AF was disabled" in call_args
        assert "Laser AF was disabled" in call_args

    def test_no_warning_when_yaml_af_was_false(self):
        """No warning if YAML didn't request AF in the first place."""
        log = MagicMock()

        log_af_restriction_warnings(
            yaml_contrast_af=False,
            yaml_laser_af=False,
            actual_contrast_af=False,
            actual_laser_af=False,
            log=log,
        )

        log.warning.assert_not_called()
