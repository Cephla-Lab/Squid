"""
Test to verify the fix for simulation mode image size calculation.
This test verifies that in simulation mode, the image size is calculated as:
crop_width_unbinned/binning_factor x crop_height_unbinned/binning_factor
instead of using hardcoded values.
"""

import squid.camera.utils
from squid.config import CameraConfig, CameraVariant


def test_simulated_camera_with_crop_dimensions():
    """Test that SimulatedCamera respects crop dimensions from config."""
    # Example: ITR3CMOS26000KMA configuration
    # Assume crop_width_unbinned = 5320, crop_height_unbinned = 4600, binning = 2
    config = CameraConfig(
        camera_type=CameraVariant.TOUPCAM,
        camera_model="ITR3CMOS26000KMA",
        crop_width=5320,
        crop_height=4600,
        default_binning=(2, 2),
        default_pixel_format="MONO12",
    )

    sim_cam = squid.camera.utils.get_camera(config, simulated=True)

    # With binning (2, 2), the expected resolution should be:
    # width = 5320 / 2 = 2660
    # height = 4600 / 2 = 2300
    expected_width = 2660
    expected_height = 2300

    width, height = sim_cam.get_resolution()
    assert width == expected_width, f"Expected width {expected_width}, got {width}"
    assert height == expected_height, f"Expected height {expected_height}, got {height}"

    # Test changing binning
    sim_cam.set_binning(1, 1)
    width, height = sim_cam.get_resolution()
    assert width == 5320, f"Expected width 5320 with binning (1,1), got {width}"
    assert height == 4600, f"Expected height 4600 with binning (1,1), got {height}"

    sim_cam.set_binning(3, 3)
    width, height = sim_cam.get_resolution()
    # 5320 / 3 = 1773.33 -> 1773
    # 4600 / 3 = 1533.33 -> 1533
    assert width == 1773, f"Expected width 1773 with binning (3,3), got {width}"
    assert height == 1533, f"Expected height 1533 with binning (3,3), got {height}"


def test_simulated_camera_fallback_to_hardcoded():
    """Test that SimulatedCamera falls back to hardcoded values when crop dimensions are not set."""
    config = CameraConfig(
        camera_type=CameraVariant.TOUPCAM,
        camera_model="ITR3CMOS26000KMA",  # Use a valid camera model
        crop_width=None,  # No crop dimensions specified
        crop_height=None,
        default_binning=(2, 2),
        default_pixel_format="MONO12",
    )

    sim_cam = squid.camera.utils.get_camera(config, simulated=True)

    # Should fall back to hardcoded BINNING_TO_RESOLUTION
    # For (2, 2) binning, the hardcoded value is (960, 540)
    width, height = sim_cam.get_resolution()
    assert width == 960, f"Expected width 960, got {width}"
    assert height == 540, f"Expected height 540, got {height}"


if __name__ == "__main__":
    test_simulated_camera_with_crop_dimensions()
    test_simulated_camera_fallback_to_hardcoded()
    print("All tests passed!")
