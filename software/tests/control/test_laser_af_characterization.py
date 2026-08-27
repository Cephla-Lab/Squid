"""Tests for the laser AF characterization module (metrics, report writers, runner)."""

import json
import math
import threading
import time
from pathlib import Path

import cv2
import imageio as iio
import numpy as np
import pytest
import yaml

import control._def
import control.microscope
import control.core.config as config_repository
import tests.control.test_stubs as ts
import tests.tools
from control.models.laser_af_config import LaserAFConfig
from control.core import laser_af_characterization as lac


def test_sweep_metrics_recover_slope_and_usable_range():
    commanded = np.linspace(-100.0, 100.0, 41)
    rng = np.random.default_rng(0)
    measured = 0.98 * commanded + rng.normal(0.0, 0.05, commanded.shape)
    measured[np.abs(commanded) > 80.0] = float("nan")

    m = lac.compute_sweep_metrics(commanded, measured)

    assert m.n_total == 41
    assert m.n_valid == int(np.sum(np.abs(commanded) <= 80.0))
    assert m.slope_um_per_um == pytest.approx(0.98, abs=0.01)
    assert m.r_squared > 0.99
    assert m.residual_rms_um < 0.2
    assert m.usable_range_neg_um == pytest.approx(-80.0)
    assert m.usable_range_pos_um == pytest.approx(80.0)


def test_sweep_metrics_all_nan_returns_nan_metrics():
    commanded = np.linspace(-10.0, 10.0, 5)
    measured = np.full_like(commanded, float("nan"))

    m = lac.compute_sweep_metrics(commanded, measured)

    assert m.n_valid == 0
    assert m.n_total == 5
    assert math.isnan(m.slope_um_per_um)
    assert math.isnan(m.r_squared)
    assert math.isnan(m.residual_rms_um)
    assert math.isnan(m.usable_range_neg_um)
    assert math.isnan(m.usable_range_pos_um)


def test_repeatability_metrics_use_successful_cycles_only():
    residuals = np.array([0.1, -0.2, 5.0])
    successes = np.array([True, True, False])

    m = lac.compute_repeatability_metrics(residuals, successes)

    assert m.n_cycles == 3
    assert m.success_rate == pytest.approx(2.0 / 3.0)
    assert m.rms_residual_um == pytest.approx(math.sqrt((0.1**2 + 0.2**2) / 2))
    assert m.max_abs_residual_um == pytest.approx(0.2)


def test_stability_metrics_separate_drift_from_noise():
    t = np.arange(0.0, 30.0, 0.5)
    disp = 0.05 + 0.02 * t  # pure drift, no noise

    m = lac.compute_stability_metrics(t, disp)

    assert m.n_samples == t.size
    assert m.duration_s == pytest.approx(29.5)
    assert m.drift_um_per_min == pytest.approx(0.02 * 60.0, abs=1e-6)
    assert m.sigma_um == pytest.approx(0.0, abs=1e-9)


def _fake_results(tmp_path) -> "lac.LaserAFTestResults":
    records = [
        lac.MeasurementRecord("sweep", 0, -10.0, 1.0, -9.8, 100.0, 50.0, 0.1),
        lac.MeasurementRecord("sweep", 1, 10.0, 1.02, 9.9, 150.0, 50.0, 0.4),
        lac.MeasurementRecord("repeatability", 0, 25.0, 1.01, 0.05, 125.0, 50.0, 1.0, move_success=True),
        lac.MeasurementRecord("stability", 0, 0.0, 1.01, 0.01, 125.0, 50.0, 2.0),
    ]
    return lac.LaserAFTestResults(
        objective="20x",
        output_dir=str(tmp_path),
        params=lac.LaserAFTestParams(),
        records=records,
        sweep=lac.SweepMetrics(1.0, 0.999, 0.05, -80.0, 80.0, 2, 2),
        repeatability=lac.RepeatabilityMetrics(0.1, 0.2, 1.0, 1),
        stability=lac.StabilityMetrics(0.02, 0.1, 2.0, 1),
    )


def test_write_report_creates_all_files(tmp_path):
    results = _fake_results(tmp_path)
    config = LaserAFConfig()

    with tests.tools.NonInteractiveMatplotlib():
        lac.write_report(results, config, tmp_path)

    header = (tmp_path / "measurements.csv").read_text().splitlines()[0]
    assert (
        header
        == "routine,index,commanded_dz_um,stage_z_mm,measured_displacement_um,spot_x_px,spot_y_px,timestamp_s,move_success"
    )

    summary = json.loads((tmp_path / "summary.json").read_text())
    for key in (
        "objective",
        "params",
        "sweep",
        "repeatability",
        "stability",
        "pixel_to_um",
        "squid_repo_state",
        "aborted",
        "error",
    ):
        assert key in summary
    assert summary["objective"] == "20x"
    assert summary["sweep"]["slope_um_per_um"] == pytest.approx(1.0)

    assert (tmp_path / "summary.txt").read_text().strip()

    snapshot = yaml.safe_load((tmp_path / "laser_af_config.yaml").read_text())
    assert "pixel_to_um" in snapshot
    assert not any(k.startswith("reference_image") for k in snapshot)

    for plot in ("sweep.png", "repeatability.png", "stability.png"):
        assert (tmp_path / plot).exists()


def test_write_report_skips_plots_for_missing_phases(tmp_path):
    results = _fake_results(tmp_path)
    results = lac.LaserAFTestResults(
        objective=results.objective,
        output_dir=results.output_dir,
        params=results.params,
        records=[r for r in results.records if r.routine == "sweep"],
        sweep=results.sweep,
        repeatability=None,
        stability=None,
    )

    with tests.tools.NonInteractiveMatplotlib():
        lac.write_report(results, LaserAFConfig(), tmp_path)

    assert (tmp_path / "sweep.png").exists()
    assert not (tmp_path / "repeatability.png").exists()
    assert not (tmp_path / "stability.png").exists()
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["repeatability"] is None


def test_report_builds_without_matplotlib36_subplots_kwargs(monkeypatch, tmp_path):
    """Microscope machines may run matplotlib < 3.6, where Figure.subplots() does not accept
    height_ratios/width_ratios directly — the builders must pass them via gridspec_kw."""
    from matplotlib.figure import Figure

    original_subplots = Figure.subplots

    def strict_subplots(self, *args, **kwargs):
        for kw in ("height_ratios", "width_ratios"):
            if kw in kwargs:
                raise TypeError(f"subplots() got an unexpected keyword argument '{kw}'")
        return original_subplots(self, *args, **kwargs)

    monkeypatch.setattr(Figure, "subplots", strict_subplots)

    results = _fake_results(tmp_path)
    with tests.tools.NonInteractiveMatplotlib():
        lac.write_report(results, LaserAFConfig(), tmp_path)

    assert (tmp_path / "sweep.png").exists()
    assert (tmp_path / "repeatability.png").exists()
    assert (tmp_path / "stability.png").exists()
    assert (tmp_path / "report.pdf").exists()


def test_write_report_generates_pdf(tmp_path):
    results = _fake_results(tmp_path)

    with tests.tools.NonInteractiveMatplotlib():
        lac.write_report(results, LaserAFConfig(), tmp_path)

    pdf = tmp_path / "report.pdf"
    assert pdf.exists()
    assert pdf.stat().st_size > 5000  # summary page + three plot pages, not an empty shell
    # Without saved spot images there is nothing to assemble into videos.
    for routine in ("sweep", "repeatability", "stability"):
        assert not (tmp_path / f"{routine}.mp4").exists()


def _frame_count(path) -> int:
    cap = cv2.VideoCapture(str(path))
    try:
        return int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    finally:
        cap.release()


def test_write_report_builds_one_video_per_routine(tmp_path):
    results = _fake_results(tmp_path)
    images_dir = tmp_path / "spot_images"
    images_dir.mkdir()
    rng = np.random.default_rng(1)
    for r in results.records:
        frame = rng.integers(0, 255, size=(64, 96), dtype=np.uint8)
        iio.imwrite(images_dir / f"{r.routine}_{r.index:03d}.bmp", frame)

    with tests.tools.NonInteractiveMatplotlib():
        lac.write_report(results, LaserAFConfig(), tmp_path)

    # _fake_results has 2 sweep, 1 repeatability, 1 stability record.
    assert _frame_count(tmp_path / "sweep.mp4") == 2
    assert _frame_count(tmp_path / "repeatability.mp4") == 1
    assert _frame_count(tmp_path / "stability.mp4") == 1
    assert not (tmp_path / "spot_video.mp4").exists()

    cap = cv2.VideoCapture(str(tmp_path / "sweep.mp4"))
    try:
        assert int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) == 96
        assert int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) == 64
    finally:
        cap.release()


def test_create_output_dir_uses_runtime_saving_path(tmp_path, monkeypatch):
    monkeypatch.setattr(control._def, "DEFAULT_SAVING_PATH", str(tmp_path))

    out = lac.create_output_dir("20x (dry)")

    assert out.parent == tmp_path / "laser_af_tests"
    assert out.is_dir()
    assert out.name.startswith("20x__dry_")


@pytest.fixture
def sim_controller(monkeypatch, tmp_path):
    monkeypatch.setattr(control._def, "SUPPORT_LASER_AUTOFOCUS", True)
    monkeypatch.setattr(control._def, "DEFAULT_SAVING_PATH", str(tmp_path))
    scope = control.microscope.Microscope.build_from_global_config(True)
    # Keep per-objective laser AF configs (written by set_reference) out of the repo tree.
    scope.config_repo = config_repository.ConfigRepository(base_path=tmp_path)
    # The simulated stage boots at z=0.0, below the Z soft-limit minimum; absolute moves clamp
    # to the soft limits, so start mid-range like a focused stage on real hardware.
    z_config = scope.stage.get_config().Z_AXIS
    scope.stage.move_z_to((z_config.MIN_POSITION + z_config.MAX_POSITION) / 2.0)
    controller = ts.get_test_laser_autofocus_controller(scope)
    yield controller
    scope.close()


def _referenced(controller):
    assert controller.initialize_auto()
    assert controller.set_reference()
    return controller


def test_runner_end_to_end_simulated(sim_controller):
    controller = _referenced(sim_controller)
    start_z_mm = controller.stage.get_pos().z_mm

    params = lac.LaserAFTestParams(
        sweep_range_um=20.0,
        sweep_n_steps=9,
        repeatability_cycles=3,
        repeatability_offset_um=10.0,
        stability_n_samples=5,
        stability_interval_s=0.05,
        save_spot_images=True,
    )
    progress = []
    finished = []
    runner = lac.LaserAFCharacterizationRunner(
        controller, progress_fn=lambda phase, i, n: progress.append((phase, i, n)), finished_fn=finished.append
    )

    with tests.tools.NonInteractiveMatplotlib():
        results = runner.run_blocking(params)

    assert results.error is None
    assert not results.aborted
    assert results.objective == controller.objectiveStore.current_objective
    assert finished == [results]
    assert progress

    assert results.sweep is not None
    assert results.sweep.slope_um_per_um == pytest.approx(1.0, abs=0.1)
    assert results.sweep.r_squared > 0.98
    assert results.repeatability is not None and results.repeatability.n_cycles == 3
    assert results.stability is not None and results.stability.n_samples == 5

    assert abs(controller.stage.get_pos().z_mm - start_z_mm) * 1000.0 < 1.0  # restored within 1 um

    out = Path(results.output_dir)
    for name in (
        "measurements.csv",
        "summary.json",
        "summary.txt",
        "laser_af_config.yaml",
        "sweep.png",
        "report.pdf",
        "sweep.mp4",
        "repeatability.mp4",
        "stability.mp4",
    ):
        assert (out / name).exists(), name
    assert any((out / "spot_images").iterdir())


def test_stability_collects_n_samples_each_with_laser_cycling(sim_controller, monkeypatch):
    controller = _referenced(sim_controller)
    microcontroller = controller.microcontroller

    counts = {"on": 0, "off": 0}

    def counting(fn, key):
        def wrapper(*args, **kwargs):
            counts[key] += 1
            return fn(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(microcontroller, "turn_on_AF_laser", counting(microcontroller.turn_on_AF_laser, "on"))
    monkeypatch.setattr(microcontroller, "turn_off_AF_laser", counting(microcontroller.turn_off_AF_laser, "off"))

    params = lac.LaserAFTestParams(
        run_sweep=False,
        run_repeatability=False,
        stability_n_samples=4,
        stability_interval_s=0.05,
        save_spot_images=True,
    )
    runner = lac.LaserAFCharacterizationRunner(controller, progress_fn=lambda *a: None, finished_fn=lambda r: None)

    with tests.tools.NonInteractiveMatplotlib():
        results = runner.run_blocking(params)

    assert results.error is None
    stability_records = [r for r in results.records if r.routine == "stability"]
    assert len(stability_records) == 4
    # Every stability sample saves its frame...
    saved = sorted(p.name for p in (Path(results.output_dir) / "spot_images").iterdir())
    assert saved == ["stability_000.bmp", "stability_001.bmp", "stability_002.bmp", "stability_003.bmp"]
    # ...and each sample is an independent laser-gated capture: on before, off after, never held on.
    assert counts["on"] == 4
    assert counts["off"] >= 4  # the runner's cleanup adds one belt-and-braces off


def test_runner_abort_restores_stage_and_reports_partial(sim_controller):
    controller = _referenced(sim_controller)
    start_z_mm = controller.stage.get_pos().z_mm

    holder = {}
    finished = []

    def abort_on_first_progress(phase, i, n):
        holder["runner"].abort()

    runner = lac.LaserAFCharacterizationRunner(
        controller, progress_fn=abort_on_first_progress, finished_fn=finished.append
    )
    holder["runner"] = runner

    with tests.tools.NonInteractiveMatplotlib():
        results = runner.run_blocking(lac.LaserAFTestParams(sweep_range_um=20.0, sweep_n_steps=9))

    assert results.aborted
    assert len(finished) == 1
    assert abs(controller.stage.get_pos().z_mm - start_z_mm) * 1000.0 < 1.0
    assert (Path(results.output_dir) / "measurements.csv").exists()


def test_runner_reports_error_without_reference(sim_controller):
    controller = sim_controller
    assert controller.initialize_auto()  # clears any reference

    finished = []
    runner = lac.LaserAFCharacterizationRunner(controller, progress_fn=lambda *a: None, finished_fn=finished.append)
    results = runner.run_blocking(lac.LaserAFTestParams())

    assert results.error
    assert results.records == []
    assert results.output_dir == ""
    assert len(finished) == 1


def test_runner_start_runs_in_thread_and_rejects_double_start(sim_controller):
    controller = _referenced(sim_controller)

    done = threading.Event()
    results_box = []

    def finished(r):
        results_box.append(r)
        done.set()

    params = lac.LaserAFTestParams(
        sweep_range_um=10.0, sweep_n_steps=3, run_repeatability=False, run_stability=False, save_spot_images=False
    )
    runner = lac.LaserAFCharacterizationRunner(controller, progress_fn=lambda *a: None, finished_fn=finished)

    with tests.tools.NonInteractiveMatplotlib():
        runner.start(params)
        with pytest.raises(RuntimeError):
            runner.start(params)
        assert done.wait(timeout=30)
        for _ in range(100):
            if not runner.is_running():
                break
            time.sleep(0.01)

    assert not runner.is_running()
    assert results_box and results_box[0].sweep is not None
