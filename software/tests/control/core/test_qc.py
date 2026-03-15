import time

import numpy as np
import pytest

from control.core.qc import FOVIdentifier, FOVMetrics, QCConfig, QCPolicyConfig, calculate_focus_score


class TestFOVIdentifier:
    def test_create(self):
        fov_id = FOVIdentifier(region_id="A1", fov_index=3)
        assert fov_id.region_id == "A1"
        assert fov_id.fov_index == 3

    def test_hashable_as_dict_key(self):
        a = FOVIdentifier(region_id="A1", fov_index=0)
        b = FOVIdentifier(region_id="A1", fov_index=0)
        assert a == b
        assert hash(a) == hash(b)
        assert {a: "val"}[b] == "val"

    def test_different_fovs_not_equal(self):
        assert FOVIdentifier("A1", 0) != FOVIdentifier("A1", 1)


class TestFOVMetrics:
    def test_required_fields_only(self):
        m = FOVMetrics(fov_id=FOVIdentifier("A1", 0), timestamp=1000.0, z_position_um=100.0)
        assert m.focus_score is None
        assert m.laser_af_displacement_um is None
        assert m.z_diff_from_last_timepoint_um is None

    def test_all_fields(self):
        m = FOVMetrics(
            fov_id=FOVIdentifier("B2", 5),
            timestamp=1000.0,
            z_position_um=150.0,
            focus_score=42.5,
            laser_af_displacement_um=0.3,
            z_diff_from_last_timepoint_um=-1.2,
        )
        assert m.focus_score == 42.5
        assert m.laser_af_displacement_um == 0.3
        assert m.z_diff_from_last_timepoint_um == -1.2


class TestQCConfig:
    def test_defaults(self):
        c = QCConfig()
        assert c.enabled is False
        assert c.calculate_focus_score is True
        assert c.record_laser_af_displacement is False
        assert c.calculate_z_diff_from_last_timepoint is False
        assert c.focus_score_method == "laplacian_variance"


class TestQCPolicyConfig:
    def test_defaults(self):
        c = QCPolicyConfig()
        assert c.enabled is False
        assert c.check_after_timepoint is True
        assert c.focus_score_min is None
        assert c.z_drift_max_um is None
        assert c.detect_outliers is False
        assert c.outlier_metric == "focus_score"
        assert c.outlier_std_threshold == 2.0
        assert c.pause_if_any_flagged is True


class TestCalculateFocusScore:
    def _sharp_image(self):
        img = np.zeros((100, 100), dtype=np.uint8)
        img[::2, :] = 255
        return img

    def _uniform_image(self):
        return np.ones((100, 100), dtype=np.uint8) * 128

    def test_laplacian_variance_positive_for_sharp(self):
        assert calculate_focus_score(self._sharp_image(), method="laplacian_variance") > 0

    def test_laplacian_variance_near_zero_for_uniform(self):
        assert calculate_focus_score(self._uniform_image(), method="laplacian_variance") < 1.0

    def test_normalized_variance(self):
        assert calculate_focus_score(self._sharp_image(), method="normalized_variance") > 0

    def test_normalized_variance_zero_mean_returns_zero(self):
        assert calculate_focus_score(np.zeros((100, 100), dtype=np.uint8), method="normalized_variance") == 0.0

    def test_gradient_magnitude(self):
        assert calculate_focus_score(self._sharp_image(), method="gradient_magnitude") > 0

    def test_fft_high_freq(self):
        assert calculate_focus_score(self._sharp_image(), method="fft_high_freq") > 0

    def test_unknown_method_raises(self):
        with pytest.raises(ValueError, match="Unknown focus method"):
            calculate_focus_score(np.zeros((10, 10), dtype=np.uint8), method="nonexistent")

    def test_sharp_scores_higher_than_uniform(self):
        assert calculate_focus_score(self._sharp_image()) > calculate_focus_score(self._uniform_image())

    def test_multichannel_uses_first_channel(self):
        rgb = np.zeros((100, 100, 3), dtype=np.uint8)
        rgb[::2, :, 0] = 255
        score = calculate_focus_score(rgb)
        assert score > 0
