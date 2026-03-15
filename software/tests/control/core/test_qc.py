import time

import numpy as np
import pytest

import squid.abc
from control.core.job_processing import CaptureInfo, JobImage
from control.core.qc import FOVIdentifier, FOVMetrics, QCConfig, QCJob, QCPolicyConfig, QCResult, calculate_focus_score
from control.models import AcquisitionChannel, CameraSettings, IlluminationSettings


def make_test_capture_info(region_id="A1", fov=0, z_mm=1.0, z_piezo_um=None) -> CaptureInfo:
    return CaptureInfo(
        position=squid.abc.Pos(x_mm=0.0, y_mm=0.0, z_mm=z_mm, theta_rad=None),
        z_index=0,
        capture_time=time.time(),
        configuration=AcquisitionChannel(
            name="BF LED matrix full",
            display_color="#FFFFFF",
            camera=1,
            illumination_settings=IlluminationSettings(
                illumination_channel="BF LED matrix full",
                intensity=50.0,
            ),
            camera_settings=CameraSettings(exposure_time_ms=10.0, gain_mode=1.0),
            z_offset_um=0.0,
        ),
        save_directory="/tmp/test",
        file_id="test_0_0",
        region_id=region_id,
        fov=fov,
        configuration_idx=0,
        z_piezo_um=z_piezo_um,
    )


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


class TestQCJob:
    def test_run_calculates_focus_score(self):
        image = np.zeros((100, 100), dtype=np.uint8)
        image[::2, :] = 255
        job = QCJob(
            capture_info=make_test_capture_info(region_id="A1", fov=3, z_mm=1.5),
            capture_image=JobImage(image_array=image),
            qc_config=QCConfig(enabled=True, calculate_focus_score=True),
        )
        result = job.run()
        assert isinstance(result, QCResult)
        assert result.metrics.fov_id == FOVIdentifier(region_id="A1", fov_index=3)
        assert result.metrics.z_position_um == 1500.0
        assert result.metrics.focus_score > 0
        assert result.error is None

    def test_run_without_focus_score(self):
        job = QCJob(
            capture_info=make_test_capture_info(),
            capture_image=JobImage(image_array=np.zeros((10, 10), dtype=np.uint8)),
            qc_config=QCConfig(enabled=True, calculate_focus_score=False),
        )
        assert job.run().metrics.focus_score is None

    def test_run_records_laser_af_displacement(self):
        job = QCJob(
            capture_info=make_test_capture_info(z_piezo_um=2.5),
            capture_image=JobImage(image_array=np.zeros((10, 10), dtype=np.uint8)),
            qc_config=QCConfig(enabled=True, record_laser_af_displacement=True, calculate_focus_score=False),
        )
        assert job.run().metrics.laser_af_displacement_um == 2.5

    def test_run_calculates_z_diff(self):
        job = QCJob(
            capture_info=make_test_capture_info(z_mm=1.5),
            capture_image=JobImage(image_array=np.zeros((10, 10), dtype=np.uint8)),
            qc_config=QCConfig(enabled=True, calculate_focus_score=False),
            previous_timepoint_z=1490.0,
        )
        assert job.run().metrics.z_diff_from_last_timepoint_um == pytest.approx(10.0)

    def test_run_no_z_diff_without_previous(self):
        job = QCJob(
            capture_info=make_test_capture_info(z_mm=1.5),
            capture_image=JobImage(image_array=np.zeros((10, 10), dtype=np.uint8)),
            qc_config=QCConfig(enabled=True, calculate_focus_score=False),
        )
        assert job.run().metrics.z_diff_from_last_timepoint_um is None

    def test_runs_in_job_runner(self):
        """QCJob must work through JobRunner subprocess (picklable)."""
        from control.core.job_processing import JobRunner

        image = np.zeros((50, 50), dtype=np.uint8)
        image[::2, :] = 255
        job = QCJob(
            capture_info=make_test_capture_info(),
            capture_image=JobImage(image_array=image),
            qc_config=QCConfig(enabled=True),
        )
        runner = JobRunner()
        runner.daemon = True
        runner.start()
        assert runner.wait_ready(timeout_s=5.0)
        runner.dispatch(job)
        result = runner.output_queue().get(timeout=5.0)
        runner.shutdown(timeout_s=2.0)
        assert result.exception is None
        assert result.result.metrics.focus_score > 0
