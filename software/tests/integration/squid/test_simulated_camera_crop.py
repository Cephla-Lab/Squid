"""
Test to verify the fix for simulation mode image size calculation.
This test verifies that in simulation mode, the image size is calculated as:
crop_width_unbinned/binning_factor x crop_height_unbinned/binning_factor
instead of using hardcoded values.
"""

from squid.core.config import CameraConfig, CameraVariant
from squid.backend.drivers.cameras.camera_utils import get_camera


def test_simulated_camera_with_crop_dimensions():
    """Test that SimulatedCamera respects crop dimensions from config.

    Crop dimensions are clamped to the simulated sensor size (3088x2064).
    This test uses dimensions within those bounds to verify behavior.
    """
    # Use dimensions that fit within simulated sensor (3088x2064)
    config = CameraConfig(
        camera_type=CameraVariant.TOUPCAM,
        camera_model=None,  # No specific model needed for simulated camera
        crop_width=2400,
        crop_height=1800,
        default_binning=(2, 2),
        default_pixel_format="MONO12",
    )

    sim_cam = get_camera(config, simulated=True)

    # With binning (2, 2), the expected resolution should be:
    # width = 2400 / 2 = 1200
    # height = 1800 / 2 = 900
    expected_width = 1200
    expected_height = 900

    width, height = sim_cam.get_resolution()
    assert width == expected_width, f"Expected width {expected_width}, got {width}"
    assert height == expected_height, f"Expected height {expected_height}, got {height}"

    # Test changing binning
    sim_cam.set_binning(1, 1)
    width, height = sim_cam.get_resolution()
    assert width == 2400, f"Expected width 2400 with binning (1,1), got {width}"
    assert height == 1800, f"Expected height 1800 with binning (1,1), got {height}"

    sim_cam.set_binning(3, 3)
    width, height = sim_cam.get_resolution()
    # 2400 / 3 = 800
    # 1800 / 3 = 600
    assert width == 800, f"Expected width 800 with binning (3,3), got {width}"
    assert height == 600, f"Expected height 600 with binning (3,3), got {height}"


def test_simulated_camera_fallback_to_full_sensor():
    """Test that SimulatedCamera uses full sensor dimensions when crop dimensions are not set."""
    config = CameraConfig(
        camera_type=CameraVariant.TOUPCAM,
        camera_model=None,  # No specific model needed for simulated camera
        crop_width=None,  # No crop dimensions specified
        crop_height=None,
        default_binning=(2, 2),
        default_pixel_format="MONO12",
    )

    sim_cam = get_camera(config, simulated=True)

    # When no crop dimensions specified, use full sensor size (3088x2064) divided by binning
    # For (2, 2) binning: 3088/2 = 1544, 2064/2 = 1032
    width, height = sim_cam.get_resolution()
    assert width == 1544, f"Expected width 1544, got {width}"
    assert height == 1032, f"Expected height 1032, got {height}"


if __name__ == "__main__":
    test_simulated_camera_with_crop_dimensions()
    test_simulated_camera_fallback_to_full_sensor()
    print("All tests passed!")
