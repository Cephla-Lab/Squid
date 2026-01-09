"""Tests for LaserAFResult."""

from squid.backend.controllers.autofocus.laser_auto_focus_controller import LaserAFResult


def test_laser_af_result_fields():
    result = LaserAFResult(
        displacement_um=1.5,
        spot_intensity=120.0,
        spot_snr=6.5,
        correlation=None,
        spot_x_px=10.0,
        spot_y_px=12.0,
        timestamp=123.456,
    )

    assert result.displacement_um == 1.5
    assert result.spot_intensity == 120.0
    assert result.spot_snr == 6.5
    assert result.correlation is None
    assert result.spot_x_px == 10.0
    assert result.spot_y_px == 12.0
    assert result.timestamp == 123.456
