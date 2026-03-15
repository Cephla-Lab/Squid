import time

import numpy as np
import pytest

import squid.abc
from control.core.job_processing import CaptureInfo, JobImage
from control.core.multi_point_utils import MultiPointControllerFunctions
from control.core.qc import (
    FOVIdentifier,
    FOVMetrics,
    FocusScoreMethod,
    PolicyDecision,
    QCConfig,
    QCJob,
    QCMetricField,
    QCPolicy,
    QCPolicyConfig,
    QCResult,
    TimepointMetricsStore,
    calculate_focus_score,
)
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
        assert c.focus_score_method == FocusScoreMethod.LAPLACIAN_VARIANCE
        assert c.qc_channel_index == 0


class TestQCPolicyConfig:
    def test_defaults(self):
        c = QCPolicyConfig()
        assert c.enabled is False
        assert c.check_after_timepoint is True
        assert c.focus_score_min is None
        assert c.z_drift_max_um is None
        assert c.detect_outliers is False
        assert c.outlier_metric == QCMetricField.FOCUS_SCORE
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
        with pytest.raises(ValueError):
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


def _make_metrics(region_id="A1", fov_index=0, focus_score=100.0, z_um=1000.0, z_diff=None):
    return FOVMetrics(
        fov_id=FOVIdentifier(region_id=region_id, fov_index=fov_index),
        timestamp=time.time(),
        z_position_um=z_um,
        focus_score=focus_score,
        z_diff_from_last_timepoint_um=z_diff,
    )


class TestTimepointMetricsStore:
    def test_add_and_get(self):
        store = TimepointMetricsStore(timepoint_index=0)
        m = _make_metrics("A1", 0)
        store.add(m)
        assert store.get(FOVIdentifier("A1", 0)) is m

    def test_get_missing_returns_none(self):
        store = TimepointMetricsStore(timepoint_index=0)
        assert store.get(FOVIdentifier("A1", 99)) is None

    def test_get_all(self):
        store = TimepointMetricsStore(timepoint_index=0)
        m1 = _make_metrics("A1", 0)
        m2 = _make_metrics("A1", 1)
        store.add(m1)
        store.add(m2)
        all_m = store.get_all()
        assert len(all_m) == 2
        assert m1 in all_m and m2 in all_m

    def test_get_metric_values_skips_none(self):
        store = TimepointMetricsStore(timepoint_index=0)
        store.add(_make_metrics("A1", 0, focus_score=100.0))
        store.add(_make_metrics("A1", 1, focus_score=200.0))
        store.add(_make_metrics("A1", 2, focus_score=None))
        values = store.get_metric_values("focus_score")
        assert len(values) == 2
        assert values[FOVIdentifier("A1", 0)] == 100.0
        assert values[FOVIdentifier("A1", 1)] == 200.0

    def test_overwrite_on_duplicate_fov(self):
        store = TimepointMetricsStore(timepoint_index=0)
        store.add(_make_metrics("A1", 0, focus_score=100.0))
        store.add(_make_metrics("A1", 0, focus_score=200.0))
        assert store.get(FOVIdentifier("A1", 0)).focus_score == 200.0
        assert len(store.get_all()) == 1

    def test_save_csv(self, tmp_path):
        import csv

        store = TimepointMetricsStore(timepoint_index=0)
        store.add(_make_metrics("A1", 0, focus_score=100.0, z_um=1500.0))
        store.add(_make_metrics("A1", 1, focus_score=200.0, z_um=1510.0))
        csv_path = str(tmp_path / "qc_metrics.csv")
        store.save(csv_path)

        with open(csv_path) as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2
        assert set(rows[0].keys()) >= {"region_id", "fov_index", "focus_score", "z_position_um"}


class TestQCPolicy:
    def _store_with(self, metrics_list):
        store = TimepointMetricsStore(timepoint_index=0)
        for m in metrics_list:
            store.add(m)
        return store

    def test_no_rules_no_flags(self):
        policy = QCPolicy(QCPolicyConfig(enabled=True))
        decision = policy.check_timepoint(
            self._store_with(
                [
                    _make_metrics("A1", 0, focus_score=50.0),
                    _make_metrics("A1", 1, focus_score=100.0),
                ]
            )
        )
        assert decision.flagged_fovs == []
        assert decision.should_pause is False

    def test_focus_score_threshold(self):
        policy = QCPolicy(QCPolicyConfig(enabled=True, focus_score_min=80.0))
        decision = policy.check_timepoint(
            self._store_with(
                [
                    _make_metrics("A1", 0, focus_score=50.0),
                    _make_metrics("A1", 1, focus_score=100.0),
                    _make_metrics("A1", 2, focus_score=79.9),
                ]
            )
        )
        assert len(decision.flagged_fovs) == 2
        assert FOVIdentifier("A1", 0) in decision.flagged_fovs
        assert FOVIdentifier("A1", 2) in decision.flagged_fovs
        assert decision.should_pause is True

    def test_z_drift_threshold(self):
        policy = QCPolicy(QCPolicyConfig(enabled=True, z_drift_max_um=5.0))
        decision = policy.check_timepoint(
            self._store_with(
                [
                    _make_metrics("A1", 0, z_diff=2.0),
                    _make_metrics("A1", 1, z_diff=-6.0),
                    _make_metrics("A1", 2, z_diff=None),
                ]
            )
        )
        assert decision.flagged_fovs == [FOVIdentifier("A1", 1)]

    def test_outlier_detection(self):
        policy = QCPolicy(
            QCPolicyConfig(
                enabled=True,
                detect_outliers=True,
                outlier_metric="focus_score",
                outlier_std_threshold=2.0,
            )
        )
        metrics = [_make_metrics("A1", i, focus_score=100.0) for i in range(9)]
        metrics.append(_make_metrics("A1", 9, focus_score=10.0))
        decision = policy.check_timepoint(self._store_with(metrics))
        assert FOVIdentifier("A1", 9) in decision.flagged_fovs

    def test_outlier_needs_minimum_3_fovs(self):
        policy = QCPolicy(QCPolicyConfig(enabled=True, detect_outliers=True))
        decision = policy.check_timepoint(
            self._store_with(
                [
                    _make_metrics("A1", 0, focus_score=100.0),
                    _make_metrics("A1", 1, focus_score=10.0),
                ]
            )
        )
        assert decision.flagged_fovs == []

    def test_pause_if_any_flagged_false(self):
        policy = QCPolicy(QCPolicyConfig(enabled=True, focus_score_min=80.0, pause_if_any_flagged=False))
        decision = policy.check_timepoint(
            self._store_with(
                [
                    _make_metrics("A1", 0, focus_score=50.0),
                ]
            )
        )
        assert len(decision.flagged_fovs) == 1
        assert decision.should_pause is False

    def test_flag_reasons_populated(self):
        policy = QCPolicy(QCPolicyConfig(enabled=True, focus_score_min=80.0, z_drift_max_um=5.0))
        decision = policy.check_timepoint(
            self._store_with(
                [
                    _make_metrics("A1", 0, focus_score=50.0, z_diff=10.0),
                ]
            )
        )
        reasons = decision.flag_reasons[FOVIdentifier("A1", 0)]
        assert len(reasons) == 2
        assert any("focus_score" in r for r in reasons)
        assert any("z_drift" in r for r in reasons)

    def test_fov_not_duplicated_across_rules(self):
        """An FOV failing multiple rules should appear once in flagged_fovs."""
        policy = QCPolicy(QCPolicyConfig(enabled=True, focus_score_min=80.0, z_drift_max_um=5.0))
        decision = policy.check_timepoint(
            self._store_with(
                [
                    _make_metrics("A1", 0, focus_score=50.0, z_diff=10.0),
                ]
            )
        )
        assert decision.flagged_fovs.count(FOVIdentifier("A1", 0)) == 1


class TestQCSignals:
    def test_qc_signals_have_noop_defaults(self):
        """New QC signals must default to no-ops so existing callers don't break."""
        callbacks = MultiPointControllerFunctions(
            signal_acquisition_start=lambda *a, **kw: None,
            signal_acquisition_finished=lambda *a, **kw: None,
            signal_new_image=lambda *a, **kw: None,
            signal_current_configuration=lambda *a, **kw: None,
            signal_current_fov=lambda *a, **kw: None,
            signal_overall_progress=lambda *a, **kw: None,
            signal_region_progress=lambda *a, **kw: None,
        )
        # Should be callable without error
        m = FOVMetrics(fov_id=FOVIdentifier("A1", 0), timestamp=0.0, z_position_um=0.0)
        callbacks.signal_qc_metrics_updated(m)

        d = PolicyDecision(flagged_fovs=[], flag_reasons={}, should_pause=False)
        callbacks.signal_qc_policy_decision(d)
