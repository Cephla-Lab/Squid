"""Connected-component laser AF spot detection, spot-mode selection and the detection diagnostics.

All on synthetic frames; no Qt and no hardware.
"""

import numpy as np
import pytest

from control import utils
from control._def import SpotDetectionMode

FRAME_SHAPE = (256, 1024)
MIDDLE_ROW = FRAME_SHAPE[0] / 2

# The detector's defaults, spelled out so the tests do not move when a machine's defaults do.
PARAMS = {"threshold": 8, "min_area": 5, "max_area": 5000, "row_tolerance": 50, "max_aspect_ratio": 2.5}


def gaussian_spots(spots, shape=FRAME_SHAPE, sigma=4.0, background=0):
    """spots: (x, y, peak) for each Gaussian spot."""
    yy, xx = np.mgrid[: shape[0], : shape[1]]
    image = np.full(shape, float(background))
    for x, y, peak in spots:
        image += peak * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma**2))
    return np.clip(np.round(image), 0, 255).astype(np.uint8)


def block(center_x, center_y, width, height, value=200, shape=FRAME_SHAPE):
    """A uniform rectangle on black (odd width and height put its centroid on the given centre)."""
    image = np.zeros(shape, dtype=np.uint8)
    x0 = int(center_x - (width - 1) // 2)
    y0 = int(center_y - (height - 1) // 2)
    image[y0 : y0 + height, x0 : x0 + width] = value
    return image


# -- detection ---------------------------------------------------------------------------------


@pytest.mark.parametrize("filter_sigma", [None, 1])
def test_single_spot_centroid_is_subpixel(filter_sigma):
    image = gaussian_spots([(400.3, 130.6, 200)])

    x, y = utils.find_spot_location(image, SpotDetectionMode.SINGLE, PARAMS, filter_sigma=filter_sigma)

    assert x == pytest.approx(400.3, abs=0.15)
    assert y == pytest.approx(130.6, abs=0.15)


def test_blank_and_below_threshold_frames_raise():
    with pytest.raises(ValueError, match="below threshold"):
        utils.find_spot_location(np.zeros(FRAME_SHAPE, dtype=np.uint8), params=PARAMS)
    with pytest.raises(ValueError, match="below threshold"):
        utils.find_spot_location(gaussian_spots([(400, 128, 7)]), params=PARAMS)


@pytest.mark.parametrize(
    "image",
    [
        block(400, 128, 2, 2),  # 4 px: under min_area
        block(400, 128, 81, 81),  # 6561 px: over max_area
        block(400, 48, 11, 11),  # 80 rows from the middle: outside row_tolerance
        block(400, 128, 55, 11),  # 5:1: over max_aspect_ratio
    ],
    ids=["min_area", "max_area", "row_tolerance", "max_aspect_ratio"],
)
def test_each_filter_rejects(image):
    with pytest.raises(ValueError, match="No valid spots"):
        utils.find_spot_location(image, params=PARAMS)
    assert utils.find_all_spot_locations(image, params=PARAMS) == []


def test_filtered_out_blob_does_not_hide_the_real_spot():
    image = np.maximum(block(700, 128, 81, 81), gaussian_spots([(300, 128, 200)]))

    x, _ = utils.find_spot_location(image, SpotDetectionMode.SINGLE, PARAMS)

    assert x == pytest.approx(300, abs=0.2)


def test_unknown_parameter_is_an_error():
    with pytest.raises(ValueError, match="min_peak_width"):
        utils.find_spot_location(gaussian_spots([(400, 128, 200)]), params={"min_peak_width": 5})


def test_find_all_spot_locations_is_left_to_right_and_capped():
    image = gaussian_spots([(700, 128, 120), (200, 128, 200), (450, 128, 60)])

    candidates = utils.find_all_spot_locations(image, params=PARAMS)

    assert [round(c["x"]) for c in candidates] == [200, 450, 700]
    assert all("mask" not in c for c in candidates)  # the full-frame masks are not retained

    # capped to the brightest, still left to right
    brightest_two = utils.find_all_spot_locations(image, params=PARAMS, max_candidates=2)
    assert [round(c["x"]) for c in brightest_two] == [200, 700]


# -- spot mode selection -----------------------------------------------------------------------


def test_modes_on_two_spots():
    image = gaussian_spots([(300, 128, 200), (600, 128, 200)])

    assert utils.find_spot_location(image, SpotDetectionMode.DUAL_LEFT, PARAMS)[0] == pytest.approx(300, abs=0.2)
    assert utils.find_spot_location(image, SpotDetectionMode.DUAL_RIGHT, PARAMS)[0] == pytest.approx(600, abs=0.2)
    with pytest.raises(ValueError, match="expected single spot"):
        utils.find_spot_location(image, SpotDetectionMode.SINGLE, PARAMS)


def test_dual_modes_accept_a_single_spot():
    image = gaussian_spots([(300, 128, 200)])

    for mode in (SpotDetectionMode.SINGLE, SpotDetectionMode.DUAL_LEFT, SpotDetectionMode.DUAL_RIGHT):
        assert utils.find_spot_location(image, mode, PARAMS)[0] == pytest.approx(300, abs=0.2)


def test_select_spot_by_mode_is_positional():
    spots = [{"col": 10.0}, {"col": 20.0}, {"col": 30.0}]

    assert utils.select_spot_by_mode(spots, SpotDetectionMode.DUAL_LEFT) is spots[0]
    assert utils.select_spot_by_mode(spots, SpotDetectionMode.DUAL_RIGHT) is spots[-1]
    assert utils.select_spot_by_mode(spots[:1], SpotDetectionMode.SINGLE) is spots[0]
    with pytest.raises(ValueError):
        utils.select_spot_by_mode(spots, SpotDetectionMode.SINGLE)
    with pytest.raises(ValueError, match="Unknown spot detection mode"):
        utils.select_spot_by_mode(spots, "multi_right")


def test_only_three_modes_remain():
    assert {m.value for m in SpotDetectionMode} == {"single", "dual_left", "dual_right"}


# -- diagnostics -------------------------------------------------------------------------------


def _params_with(suggested):
    """PARAMS with a diagnosis' cc_* suggestions applied."""
    applied = dict(PARAMS)
    for field_name, value in suggested.items():
        applied[utils._CC_FIELD_TO_PARAM[field_name]] = value
    return applied


@pytest.mark.parametrize(
    "image, failing_field",
    [
        (block(400, 128, 2, 2), "cc_min_area"),
        (block(400, 128, 81, 81), "cc_max_area"),
        (block(400, 48, 11, 11), "cc_row_tolerance"),
        (block(400, 128, 55, 11), "cc_max_aspect_ratio"),
        (gaussian_spots([(400, 128, 7)], sigma=6.0), "cc_threshold"),
    ],
    ids=["min_area", "max_area", "row_tolerance", "max_aspect_ratio", "threshold"],
)
def test_diagnosis_names_the_failing_setting_and_its_suggestion_admits_the_spot(image, failing_field):
    with pytest.raises(ValueError):
        utils.find_spot_location(image, params=PARAMS)

    diagnosis = utils.diagnose_spot_detection(image, params=PARAMS)

    assert diagnosis.best is not None
    assert [c.name for c in diagnosis.best.failures] == [failing_field]
    suggested = diagnosis.suggested_params()
    assert set(suggested) == {failing_field}

    # the point of a suggestion: applying it makes the detector accept the blob it described
    x, _ = utils.find_spot_location(image, params=_params_with(suggested))
    assert x == pytest.approx(400, abs=1.0)


def test_diagnosis_suggests_every_failing_setting_at_once():
    # 3 px, and 80 rows off the middle: relaxing only one of the two still rejects it
    image = block(400, 48, 3, 1)

    diagnosis = utils.diagnose_spot_detection(image, params=PARAMS)
    suggested = diagnosis.suggested_params()

    assert {"cc_min_area", "cc_row_tolerance", "cc_max_aspect_ratio"} == set(suggested)
    assert suggested["cc_min_area"] <= 3
    assert suggested["cc_row_tolerance"] >= 80
    assert suggested["cc_max_aspect_ratio"] >= 3.0
    assert utils.find_spot_location(image, params=_params_with(suggested)) is not None


def test_diagnosis_of_a_uniform_frame_has_a_note_and_no_suggestion():
    diagnosis = utils.diagnose_spot_detection(np.full(FRAME_SHAPE, 3, dtype=np.uint8), params=PARAMS)

    assert diagnosis.best is None
    assert diagnosis.note
    assert diagnosis.suggested_params() == {}


def test_row_tolerance_suggestion_is_bounded_by_the_frame_height():
    # 400 rows off the middle of a tall frame: more than the static spinbox ceiling (200), but reachable
    # because the ceiling follows the crop height.
    tall = (1000, 1024)
    image = block(400, 100, 11, 11, shape=tall)

    diagnosis = utils.diagnose_spot_detection(image, params=PARAMS)
    suggested = diagnosis.suggested_params()

    assert 400 <= suggested["cc_row_tolerance"] <= utils._row_tolerance_ceiling(tall[0])
    assert utils.find_spot_location(image, params=_params_with(suggested)) is not None


def test_row_tolerance_ceiling():
    assert utils._row_tolerance_ceiling(None) == 200
    assert utils._row_tolerance_ceiling(256) == 200  # never below the static floor
    assert utils._row_tolerance_ceiling(2064) == 1032  # half the crop height


def test_analyze_frame_returns_candidates_and_only_diagnoses_on_request():
    image = gaussian_spots([(300, 128, 200), (600, 128, 200)])

    candidates, diagnosis = utils.analyze_frame(image, params=PARAMS)
    assert [round(c["x"]) for c in candidates] == [300, 600]
    assert diagnosis is None

    candidates, diagnosis = utils.analyze_frame(block(400, 128, 2, 2), params=PARAMS, diagnose=True)
    assert candidates == []
    assert diagnosis.suggested_params() == {"cc_min_area": 4}


def test_criteria_of_a_passing_spot_all_pass():
    criteria = utils.evaluate_spot_criteria(
        peak_intensity=200, area=120, row_deviation=3.0, aspect_ratio=1.1, params=PARAMS
    )

    assert {c.name for c in criteria} == set(utils._CC_FIELD_TO_PARAM)
    assert all(c.passed for c in criteria)


def test_aspect_ratio_suggestion_rounds_towards_admitting():
    # 2.63 rounded to nearest (2.6) would still reject the blob
    criteria = utils.evaluate_spot_criteria(
        peak_intensity=200, area=120, row_deviation=3.0, aspect_ratio=2.63, params=PARAMS
    )

    aspect = next(c for c in criteria if c.name == "cc_max_aspect_ratio")
    assert not aspect.passed
    assert aspect.suggested == pytest.approx(2.7)


# -- crop clamping -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "request_roi, expected",
    [
        ((100, 50, 512, 224), (96, 50, 512, 224)),  # snapped to the 8 x 2 grid
        ((2051, 100, 1536, 256), (1552, 100, 1536, 256)),  # runs off the right edge: pulled back in
        ((-30, -7, 512, 224), (0, 0, 512, 224)),  # negative offsets
        ((0, 0, 9999, 9999), (0, 0, 3088, 2064)),  # larger than the sensor
    ],
)
def test_clamp_roi_stays_on_the_sensor(request_roi, expected):
    roi = utils.clamp_roi(*request_roi, sensor_width=3088, sensor_height=2064)

    assert roi == expected
    x, y, width, height = roi
    assert 0 <= x and x + width <= 3088 and 0 <= y and y + height <= 2064
