import control.utils
import tests.tools
import pathlib
import tempfile
import threading
import time


def test_squid_repo_info():
    # At least make sure we get something and that it calls without issue.
    assert control.utils.get_squid_repo_state_description()


import numpy as np
import pytest
from control.utils import find_spot_location, SpotDetectionMode, get_available_disk_space, threaded_operation_helper


def create_test_image(spot_positions, image_size=(480, 640), spot_size=20):
    """Create a test image with Gaussian spots at specified positions.

    Args:
        spot_positions: List of (x, y) coordinates for spot centers
        image_size: Tuple of (height, width) for the image
        spot_size: Approximate diameter of each spot
    """
    image = np.zeros(image_size)
    y, x = np.ogrid[: image_size[0], : image_size[1]]

    for pos_x, pos_y in spot_positions:
        spot = np.exp(-((x - pos_x) ** 2 + (y - pos_y) ** 2) / (2 * (spot_size / 4) ** 2))
        image += spot

    # Normalize and convert to uint8
    image = (image * 255 / np.max(image)).astype(np.uint8)
    return image


def test_single_spot_detection():
    # Create test image with single spot
    spot_x, spot_y = 320, 240
    image = create_test_image([(spot_x, spot_y)])

    # Test detection
    result = find_spot_location(image, mode=SpotDetectionMode.SINGLE)

    assert result is not None
    detected_x, detected_y = result
    assert abs(detected_x - spot_x) < 5
    assert abs(detected_y - spot_y) < 5


def test_dual_spot_detection():
    # Create test image with two spots
    spots = [(280, 240), (360, 240)]
    image = create_test_image(spots)

    # Test right spot detection
    result = find_spot_location(image, mode=SpotDetectionMode.DUAL_RIGHT)
    assert result is not None
    detected_x, detected_y = result
    assert abs(detected_x - spots[1][0]) < 5

    # Test left spot detection
    result = find_spot_location(image, mode=SpotDetectionMode.DUAL_LEFT)
    assert result is not None
    detected_x, detected_y = result
    assert abs(detected_x - spots[0][0]) < 5


def test_multi_spot_detection():
    # Create test image with multiple spots
    spots = [(200, 240), (280, 240), (360, 240)]
    image = create_test_image(spots)

    # Test rightmost spot detection
    result = find_spot_location(image, mode=SpotDetectionMode.MULTI_RIGHT)
    assert result
    detected_x, detected_y = result
    assert abs(detected_x - spots[2][0]) < 5

    # Test second from right spot detection
    with pytest.raises(NotImplementedError):
        result = find_spot_location(image, mode=SpotDetectionMode.MULTI_SECOND_RIGHT)


def test_invalid_inputs():
    # Test empty image
    with pytest.raises(ValueError):
        find_spot_location(np.zeros((0, 0), dtype=np.uint8))

    # Test None image
    with pytest.raises(ValueError):
        find_spot_location(None)

    # Test invalid mode
    with pytest.raises(ValueError):
        find_spot_location(np.zeros((480, 640), dtype=np.uint8), mode="invalid")


def test_spot_detection_parameters():
    # Create test image with single spot
    image = create_test_image([(320, 240)])

    # Test with custom parameters
    params = {
        "y_window": 50,
        "x_window": 15,
        "min_peak_width": 5,
        "min_peak_distance": 5,
        "min_peak_prominence": 0.25,
    }

    result = find_spot_location(image, params=params)
    assert result is not None


@pytest.mark.skip("Test debug plot backend conflicts with qt.")
def test_debug_plot(tmp_path):
    """Test that debug plotting doesn't error."""
    with tests.tools.NonInteractiveMatplotlib():
        image = create_test_image([(320, 240)])
        # This should create plots but not raise any errors
        find_spot_location(image, debug_plot=True)


def test_get_available_disk_space():
    temp_dir = pathlib.Path(tempfile.mkdtemp())

    assert get_available_disk_space(temp_dir) > 0

    some_non_dir_file = temp_dir / "test_file"
    some_non_dir_file.touch()

    with pytest.raises(ValueError):
        get_available_disk_space(some_non_dir_file)

    some_non_dir_file.unlink(missing_ok=True)
    temp_dir.rmdir()
    with pytest.raises(ValueError):
        get_available_disk_space(temp_dir)


def test_timing_manager():
    tma = control.utils.TimingManager("a")
    tmb = control.utils.TimingManager("b")

    ta1 = tma.get_timer("a 1")
    ta1.start()

    ta2 = tma.get_timer("a 2")
    ta1.stop()
    ta2.start()

    tb1 = tmb.get_timer("b 1")
    tb1.start()

    tb2 = tmb.get_timer("b 2")
    tb2.start()
    ta2.stop()
    tb1.stop()
    tb2.stop()

    ta2.start()
    ta2.stop()
    tb1.start()
    tb2.start()
    tb2.stop()
    tb1.stop()

    tb1.start()
    tb1.stop()

    assert len(tma.get_report())
    assert len(tma.get_report())
    assert len(ta1.get_intervals()) == 1
    assert len(ta2.get_intervals()) == 2
    assert len(tb1.get_intervals()) == 3
    assert len(tb2.get_intervals()) == 2

    assert not len(tma.get_timer("t1").get_intervals())
    with tma.get_timer("t1"):
        pass
    assert len(tma.get_timer("t1").get_intervals()) == 1

    with tma.get_timer("t2"):
        pass
    assert len(tma.get_timer("t2").get_intervals()) == 1


def test_threaded_operation_helper():
    """Test threaded operation with callback and arguments."""
    operation_result = []
    callback_result = []

    def test_operation(arg1, arg2):
        operation_result.append((arg1, arg2))
        time.sleep(0.05)

    def test_callback(success, error_msg):
        callback_result.append((success, error_msg))

    # Execute
    thread = threaded_operation_helper(test_operation, callback=test_callback, arg1="value1", arg2="value2")

    # Verify thread properties and wait for completion
    assert isinstance(thread, threading.Thread)
    assert thread.daemon is True
    assert thread.name == "test_operation"
    thread.join(timeout=1.0)

    # Verify results
    assert operation_result == [("value1", "value2")]
    assert callback_result == [(True, None)]
