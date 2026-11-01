import errno
import os
import sys
from configparser import ConfigParser

import control.utils
import tests.tools
import pathlib
import tempfile
import threading
import time


def test_squid_repo_info():
    # At least make sure we get something and that it calls without issue.
    assert control.utils.get_squid_repo_state_description()


import cv2
import numpy as np
import pytest
from control._def import FocusMeasureOperator
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

    # Test unknown parameter
    with pytest.raises(ValueError, match="peak_width"):
        find_spot_location(create_test_image([(320, 240)]), params={"peak_width": 5})


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


def _binary_amplitude_image(dtype, shape=(256, 256), seed=0):
    """Random (non-periodic) 0/dtype-max image: every pixel edge at full contrast, so maximally sharp."""
    rng = np.random.default_rng(seed)
    return ((rng.random(shape) > 0.5) * np.iinfo(dtype).max).astype(dtype)


@pytest.mark.parametrize("sigma", [1, 2])
@pytest.mark.parametrize("method", list(FocusMeasureOperator))
@pytest.mark.parametrize("dtype", [np.uint8, np.uint16])
def test_focus_measure_sharp_scores_higher_than_blurred(dtype, method, sigma):
    sharp = _binary_amplitude_image(dtype)
    blurred = cv2.GaussianBlur(sharp, (0, 0), sigma)

    assert control.utils.calculate_focus_measure(sharp, method) > control.utils.calculate_focus_measure(blurred, method)


def test_focus_measure_lape_non_negative_for_uint8():
    sharp = _binary_amplitude_image(np.uint8)

    assert control.utils.calculate_focus_measure(sharp, FocusMeasureOperator.LAPE) >= 0
    assert control.utils.calculate_focus_measure(cv2.GaussianBlur(sharp, (0, 0), 1), FocusMeasureOperator.LAPE) >= 0


def test_focus_measure_lape_independent_of_dtype_for_identical_values():
    as_uint8 = _binary_amplitude_image(np.uint8)
    as_uint16 = as_uint8.astype(np.uint16)

    assert control.utils.calculate_focus_measure(as_uint8, FocusMeasureOperator.LAPE) == pytest.approx(
        control.utils.calculate_focus_measure(as_uint16, FocusMeasureOperator.LAPE), rel=1e-9
    )


@pytest.mark.parametrize("rotate", [None, 0, 90, -90, 180])
@pytest.mark.parametrize("flip", [None, *control.utils.FlipVariant])
@pytest.mark.parametrize("shape", [(6, 4), (6, 4, 3)])
def test_rotate_and_flip_image_matches_opencv_and_never_aliases_the_input(rotate, flip, shape):
    image = np.arange(np.prod(shape), dtype=np.uint16).reshape(shape)
    original = image.copy()
    expected = image.copy()
    if rotate:
        expected = cv2.rotate(
            expected, {90: cv2.ROTATE_90_CLOCKWISE, -90: cv2.ROTATE_90_COUNTERCLOCKWISE, 180: cv2.ROTATE_180}[rotate]
        )
    if flip is not None:
        expected = cv2.flip(expected, {"Vertical": 0, "Horizontal": 1, "Both": -1}[flip.value])

    result = control.utils.rotate_and_flip_image(image, rotate_image_angle=rotate, flip_image=flip)

    np.testing.assert_array_equal(result, expected)
    assert result.flags.c_contiguous
    assert not np.may_share_memory(result, image)  # callers pass views of driver memory
    np.testing.assert_array_equal(image, original)


def _ini(**general):
    config = ConfigParser()
    config["GENERAL"] = general
    return config


def _read_general(path):
    saved = ConfigParser()
    saved.read(path)
    return dict(saved["GENERAL"])


def test_write_ini_atomically_replaces_the_file_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "machine.ini"
    path.write_text("[GENERAL]\nold = 1\n")
    control.utils.write_ini_atomically(_ini(new="2"), str(path))
    assert _read_general(path) == {"new": "2"}
    assert os.listdir(tmp_path) == ["machine.ini"]


def test_a_failed_ini_write_leaves_the_file_as_it_was(tmp_path):
    path = tmp_path / "machine.ini"
    path.write_text("[GENERAL]\nold = 1\n")

    class DiskFull(ConfigParser):
        def write(self, fp, space_around_delimiters=True):
            fp.write("[GENERAL]\n")  # cut off part way
            raise OSError(errno.ENOSPC, "No space left on device")

    with pytest.raises(OSError):
        control.utils.write_ini_atomically(DiskFull(), str(path))
    assert path.read_text() == "[GENERAL]\nold = 1\n"
    assert os.listdir(tmp_path) == ["machine.ini"]


def test_a_save_made_while_another_is_part_way_publishes_only_its_own_text(tmp_path):
    # A shared ini saved from another user or host while this save is part way, which then runs out of space.
    path = tmp_path / "machine.ini"
    path.write_text("[GENERAL]\nold = 1\n")

    class DiskFullAfterAnotherSave(ConfigParser):
        def write(self, fp, space_around_delimiters=True):
            fp.write("[GENERAL]\n")
            control.utils.write_ini_atomically(_ini(other="2"), str(path))
            raise OSError(errno.ENOSPC, "No space left on device")

    with pytest.raises(OSError):
        control.utils.write_ini_atomically(DiskFullAfterAnotherSave(), str(path))
    assert path.read_text() == "[GENERAL]\nother = 2\n\n"  # the other save, whole
    assert os.listdir(tmp_path) == ["machine.ini"]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows files have no group/other permission bits")
def test_a_new_ini_gets_the_usual_permissions(tmp_path):
    # Not the owner-only 0600 of a tempfile.mkstemp file, which would shut other users out of a shared ini.
    plain = tmp_path / "plain.txt"
    plain.write_text("")
    path = tmp_path / "machine.ini"
    control.utils.write_ini_atomically(_ini(new="2"), str(path))
    assert path.stat().st_mode & 0o777 == plain.stat().st_mode & 0o777


def test_a_shared_ini_keeps_its_group(tmp_path, monkeypatch):
    # Written in place, the ini kept its group; the replacement is given it too, before it replaces the ini.
    path = tmp_path / "machine.ini"
    path.write_text("[GENERAL]\nold = 1\n")
    chowned = []

    def chown(new_path, uid, gid):
        assert _read_general(path) == {"old": "1"}
        chowned.append((os.path.basename(new_path), uid, gid))

    monkeypatch.setattr(os, "chown", chown, raising=False)  # also on Windows, which has no os.chown
    control.utils.write_ini_atomically(_ini(new="2"), str(path))
    [(name, uid, gid)] = chowned
    assert name.startswith("machine.ini.") and name.endswith(".tmp")
    assert (uid, gid) == (-1, path.stat().st_gid)
    assert _read_general(path) == {"new": "2"}


def test_an_ini_whose_group_this_user_cannot_set_is_still_saved(tmp_path, monkeypatch):
    path = tmp_path / "machine.ini"
    path.write_text("[GENERAL]\nold = 1\n")

    def chown(new_path, uid, gid):
        raise PermissionError(errno.EPERM, "Operation not permitted", new_path)  # not a member of that group

    monkeypatch.setattr(os, "chown", chown, raising=False)
    control.utils.write_ini_atomically(_ini(new="2"), str(path))
    assert _read_general(path) == {"new": "2"}
    assert os.listdir(tmp_path) == ["machine.ini"]


def test_a_read_only_ini_is_not_replaced(tmp_path):
    path = tmp_path / "machine.ini"
    path.write_text("[GENERAL]\nold = 1\n")
    os.chmod(path, 0o444)
    if os.access(path, os.W_OK):
        os.chmod(path, 0o644)
        pytest.skip("running as root, which may write a read-only file")
    try:
        with pytest.raises(PermissionError):
            control.utils.write_ini_atomically(_ini(new="2"), str(path))
        assert path.read_text() == "[GENERAL]\nold = 1\n"
    finally:
        os.chmod(path, 0o644)


def test_an_ini_reached_through_a_symlink_keeps_the_link(tmp_path):
    target = tmp_path / "machine.ini"
    target.write_text("[GENERAL]\nold = 1\n")
    link = tmp_path / "current.ini"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("this account can't create symlinks (Windows without developer mode)")
    control.utils.write_ini_atomically(_ini(new="2"), str(link))
    assert link.is_symlink()
    assert _read_general(target) == {"new": "2"}
