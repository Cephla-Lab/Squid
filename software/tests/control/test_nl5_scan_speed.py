"""Line speed <-> scan-limited frame rate (control.NL5._ScanSpeedMixin): the sweep scales with
FIELD_OF_VIEW_X / MAX_CAM_PIX_X, uncapped on the AION (measured), capped at 1 on the NL5."""

import pytest

from control.NL5 import _ScanSpeedMixin


class _Scanner(_ScanSpeedMixin):
    def __init__(self, is_aion, amplitude=69.0, fov_x=2700, max_cam_pix_x=2048):
        self.is_aion = is_aion
        self.scan_amplitude = amplitude
        self.fov_x = fov_x
        self.max_cam_pix_x = max_cam_pix_x


def test_aion_sweep_is_not_capped_at_the_full_field():
    # The AION reports MAX_CAM_PIX_X = 2048 for the 3200 px Aries; FIELD_OF_VIEW_X = 2700 at 69 mrad scans about
    # 2736 camera rows, i.e. 2700 / 2048 of the 2 x 69 mrad sweep.
    aion = _Scanner(is_aion=True)
    assert aion.scan_sweep_mrad() == pytest.approx(2 * 69.0 * 2700 / 2048)  # 181.9 mrad
    assert 1000 / aion.line_speed_to_fps(5000) == pytest.approx(36.4, abs=0.05)  # ms per scan
    assert aion.line_speed_to_fps(5000) == pytest.approx(27.5, abs=0.05)


def test_aion_bench_point():
    # Measured 2026-10-22: 73 mrad, FIELD_OF_VIEW_X 3200, 3000 mrad/s -> a 76 ms scan (sweep 228 mrad)
    aion = _Scanner(is_aion=True, amplitude=73.0, fov_x=3200)
    assert aion.scan_sweep_mrad() == pytest.approx(228.1, abs=0.1)
    assert 1000 / aion.line_speed_to_fps(3000) == pytest.approx(76.0, abs=0.1)


@pytest.mark.parametrize("is_aion", [True, False])
def test_a_field_inside_the_full_width_scales_the_sweep_down(is_aion):
    scanner = _Scanner(is_aion=is_aion, fov_x=1024)
    assert scanner.scan_sweep_mrad() == pytest.approx(69.0)  # half the 138 mrad full sweep


def test_nl5_keeps_the_cap_at_the_full_field():
    nl5 = _Scanner(is_aion=False)
    assert nl5.scan_sweep_mrad() == pytest.approx(2 * 69.0)


def test_unknown_camera_width_uses_the_full_sweep():
    assert _Scanner(is_aion=True, max_cam_pix_x=None).scan_sweep_mrad() == pytest.approx(2 * 69.0)


def test_frame_rate_mode_sets_the_line_speed_for_the_real_sweep():
    # Asking for 27.5 fps on the AION at FIELD_OF_VIEW_X 2700 needs ~5000 mrad/s (it used to set ~3795, i.e. 21 fps)
    aion = _Scanner(is_aion=True)
    assert aion.fps_to_line_speed(27.5) == pytest.approx(5003, abs=2)
    assert aion.line_speed_to_fps(aion.fps_to_line_speed(20.0)) == pytest.approx(20.0, abs=0.01)
