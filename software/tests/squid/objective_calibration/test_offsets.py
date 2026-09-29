import dataclasses
import math
import re

import numpy as np
import pytest

from squid.objective_calibration import offsets
from squid.objective_calibration.engine import CalibrationView
from squid.objective_calibration.offsets import OffsetsError, register_offsets
from squid.objective_calibration.registration import MAX_SELF_SIMILARITY, Match, SelfSimilarity
from squid.objective_calibration.synthetic import FakeCalibrationHardware, FakeGridPatchScene, FakeObjective, FakeScene

ORDERED = ["4x", "10x", "20x"]
PIXEL_UM = {"4x": 1.6, "10x": 0.64, "20x": 0.32}
PARCENTRIC = {"4x": (0.0, 0.0), "10x": (12.0, -7.0), "20x": (-15.0, 9.0)}


def _rot(deg):
    t = math.radians(deg)
    return np.array([[math.cos(t), -math.sin(t)], [math.sin(t), math.cos(t)]])


ORIENTATIONS = {"inverted": np.eye(2), "upright": np.diag([1.0, -1.0]), "rotated": _rot(0.5)}


def _machine(orientation=np.eye(2), parcentric=PARCENTRIC, scene=None, shape=(192, 256)):
    objectives = {
        name: FakeObjective(
            name, mag, na, matrix_um_per_px=PIXEL_UM[name] * orientation, parcentric_um=parcentric[name]
        )
        for name, mag, na in (("4x", 4, 0.13), ("10x", 10, 0.3), ("20x", 20, 0.8))
    }
    return FakeCalibrationHardware(
        objectives, scene or FakeScene.random(), shape=shape, start_objective="4x", microstep_um=0.0
    )


def _images(hw):
    images = {}
    for name in ORDERED:
        hw.switch_objective(name)
        images[name] = hw.snap(name, "BF")
    return images


def _true_view(hw):
    return CalibrationView({name: obj.matrix_um_per_px for name, obj in hw.objectives.items()})


@pytest.mark.parametrize("name", list(ORIENTATIONS))
def test_recovers_the_injected_xy_offsets_for_every_orientation(name):
    hw = _machine(ORIENTATIONS[name])
    result = register_offsets(_images(hw), _true_view(hw), ORDERED)
    assert result.reference == "4x" and result.offsets_um["4x"] == (0.0, 0.0)
    for objective in ("10x", "20x"):
        # ground truth in stage coordinates, independent of any conversion helper: parcentric_k - parcentric_ref
        assert result.offsets_um[objective] == pytest.approx(PARCENTRIC[objective], abs=0.5)
    assert [(p.lower, p.higher, p.measurable) for p in result.pairs] == [
        ("4x", "10x", True),
        ("4x", "20x", True),
        ("10x", "20x", True),
    ]
    assert all(p.score > 0.5 and p.runner_up_ratio < 0.8 for p in result.pairs)
    assert max(result.closure_error_um.values()) < 1.0
    assert result.pairs[2].closure_error_um == result.closure_error_um["20x"]  # the one triple, per pair too
    assert result.warnings == []


def test_a_closure_pair_whose_template_does_not_fit_is_reported_not_measurable():
    # 10x and 20x 160 um apart: both fit the 4x frame (410 um wide) but 20x's view is outside 10x's (164 um)
    hw = _machine(parcentric={"4x": (0.0, 0.0), "10x": (80.0, 0.0), "20x": (-80.0, 0.0)})
    result = register_offsets(_images(hw), _true_view(hw), ORDERED)
    assert result.offsets_um["10x"] == pytest.approx((80.0, 0.0), abs=0.5)
    assert result.offsets_um["20x"] == pytest.approx((-80.0, 0.0), abs=0.5)
    closure = next(p for p in result.pairs if (p.lower, p.higher) == ("10x", "20x"))
    assert not closure.measurable and "not measurable" in closure.message
    assert result.closure_error_um == {}


def test_a_wrong_matrix_shows_up_as_a_closure_warning_not_a_failure():
    hw = _machine(parcentric={"4x": (0.0, 0.0), "10x": (10.0, 0.0), "20x": (-10.0, 5.0)})
    matrices = {name: obj.matrix_um_per_px for name, obj in hw.objectives.items()}
    matrices["10x"] = (
        _rot(20.0) @ matrices["10x"]
    )  # same scale, but the 10x-20x pair converts with a 20° wrong rotation
    result = register_offsets(_images(hw), CalibrationView(matrices), ORDERED)
    assert result.offsets_um["20x"] == pytest.approx((-10.0, 5.0), abs=0.5)  # reference pairs use M_4x only
    assert len(result.warnings) == 1 and "closure error" in result.warnings[0]
    assert result.closure_error_um["20x"] > 2.0


def test_a_periodic_sample_is_refused():
    # A 16 um grating: it also resembles itself all along its lines, the shortest repeat the message names
    hw = _machine(scene=FakeScene.periodic(period_um=16.0))
    with pytest.raises(
        OffsetsError,
        match=r"^Cannot uniquely match \S+ and \S+: the sample remains too similar after a ~\d+ µm shift \(self-similarity 1\.00; limit 0\.4\)",
    ):
        register_offsets(_images(hw), _true_view(hw), ORDERED)


def test_a_featureless_sample_does_not_match():
    hw = _machine(scene=FakeScene.flat())
    with pytest.raises(OffsetsError, match=r"Images of 4x and 10x do not match \(match score 0\.\d\d, need 0\.5\)"):
        register_offsets(_images(hw), _true_view(hw), ORDERED)


def _pair(px, offset_um, scene=None, matrix=np.eye(2), **kw):
    """Two objectives, px = {name: um/px} in ascending magnification; the second is offset_um away. Both
    share the camera's orientation (and anisotropy), `matrix`."""
    (low, px_low), (high, px_high) = px.items()
    objectives = {
        low: FakeObjective(low, 10, 0.3, matrix_um_per_px=px_low * matrix),
        high: FakeObjective(high, 20, 0.8, matrix_um_per_px=px_high * matrix, parcentric_um=offset_um),
    }
    hw = FakeCalibrationHardware(objectives, scene or FakeScene.random(), start_objective=low, microstep_um=0.0, **kw)
    images = {}
    for name in (low, high):
        hw.switch_objective(name)
        images[name] = hw.snap(name, "BF")
    return images, _true_view(hw), [low, high]


def test_equal_magnification_objectives_are_registered():
    # The external review's case: two 20x objectives (NA 0.8 and 1.0) with the same pixel size
    images, view, ordered = _pair({"20x-air": 0.32, "20x-water": 0.32}, (2.0, -1.0))
    result = register_offsets(images, view, ordered)
    assert result.offsets_um["20x-water"] == pytest.approx((2.0, -1.0), abs=0.1)


LOWER = [("20x-air", 0.32), ("16x", 0.4), ("10x", 0.64)]  # equal, near-equal and 2:1 against a 20x


@pytest.mark.parametrize("period", [8.0, 12.0, 16.0, 20.0, 24.0, 28.0, 40.0])
@pytest.mark.parametrize("low, px_low", LOWER)
def test_a_grid_is_refused_whatever_its_period_against_the_search_area(low, px_low, period):
    # The 20x is one period away, so a match one period short is as good as the true one. On this 256 x 192
    # frame the search holds ±16.5 x ±12.3 um for two 20x objectives, ±20.6 x ±15.4 um for 16x/20x and
    # ±41 x ±31 um for 10x/20x: the periods lie below, near and beyond it. From 20 um on, the two 20x
    # objectives used to report dx ~ 0 with no warning (the external review's P1).
    images, view, ordered = _pair({low: px_low, "20x": 0.32}, (period, 0.0), scene=FakeScene.grid(period_um=period))
    with pytest.raises(OffsetsError) as refused:
        register_offsets(images, view, ordered)
    message = str(refused.value)
    assert re.fullmatch(
        rf"Cannot uniquely match {low} and 20x: the sample remains too similar after a ~(\d+) µm shift "
        r"\(self-similarity [01]\.\d\d; limit 0\.4\)\. Use a sample with varied texture in both directions\.",
        message,
    ), message
    assert float(re.search(r"~(\d+) µm", message).group(1)) == pytest.approx(period, rel=0.06)


def _weak_grid(period_um, amplitude, seed=0):
    """A grid of the given amplitude (a grid alone has 1) on the textured specimen."""
    grid = FakeScene.grid(period_um=period_um)
    return dataclasses.replace(grid, amplitudes=amplitude * grid.amplitudes, octaves=FakeScene.random(seed).octaves)


@pytest.mark.parametrize("amplitude", [0.05, 0.1, 0.2, 0.3, 0.5, 1.0])
@pytest.mark.parametrize("offset", [(20.0, 0.0), (5.0, -3.0)], ids=["a period away, beyond the search", "within it"])
@pytest.mark.parametrize("seed", range(2))
def test_a_weak_grid_on_a_textured_sample_is_refused_or_measured_correctly(offset, amplitude, seed):
    # A match one period away scores about as much as the sample resembles itself one period away, so an
    # alias that passes the match gate (0.5) is on a sample the self-similarity gate (0.4) refuses.
    images, view, ordered = _pair(
        {"20x-air": 0.32, "20x-water": 0.32}, offset, scene=_weak_grid(20.0, amplitude, seed), seed=seed
    )
    try:
        result = register_offsets(images, view, ordered)
    except OffsetsError:
        return
    assert result.offsets_um["20x-water"] == pytest.approx(offset, abs=0.5)


def test_a_faint_grid_is_measured_and_a_strong_one_refused():
    images, view, ordered = _pair({"20x-air": 0.32, "20x-water": 0.32}, (5.0, -3.0), scene=_weak_grid(20.0, 0.1))
    result = register_offsets(images, view, ordered)
    assert result.offsets_um["20x-water"] == pytest.approx((5.0, -3.0), abs=0.1)
    assert result.pairs[0].self_similarity < MAX_SELF_SIMILARITY
    images, view, ordered = _pair({"20x-air": 0.32, "20x-water": 0.32}, (5.0, -3.0), scene=_weak_grid(20.0, 0.5))
    with pytest.raises(
        OffsetsError,
        match=r"^Cannot uniquely match \S+ and \S+: the sample remains too similar after a ~(19|20|21) µm shift \(self-similarity 0\.\d\d",
    ):
        register_offsets(images, view, ordered)


@pytest.mark.parametrize("seed", range(3))
@pytest.mark.parametrize("low, px_low", [("4x", 1.6), ("10x", 0.64), ("16x", 0.4), ("20x-air", 0.32)])
def test_textured_samples_register_at_every_magnification_ratio(low, px_low, seed):
    images, view, ordered = _pair(
        {low: px_low, "20x": 0.32}, (6.0, -4.0), FakeScene.random(seed), seed=seed, noise=0.01, vignetting=0.3
    )
    result = register_offsets(images, view, ordered)
    assert result.offsets_um["20x"] == pytest.approx((6.0, -4.0), abs=0.5)
    [pair] = result.pairs
    assert pair.self_similarity < MAX_SELF_SIMILARITY - 0.1  # measured at most 0.24 over 20 seeds
    frame_um = (256 * px_low, 192 * px_low)
    assert pair.range_um[0] < frame_um[0] / 2 and pair.range_um[1] < frame_um[1] / 2


@pytest.mark.parametrize("noise", [0.0, 0.002])
@pytest.mark.parametrize("low, px_low", [("20x-air", 0.32), ("10x", 0.64)])
@pytest.mark.parametrize(
    "period, x_fraction",
    [(None, 1.0)] + [(p, f) for p in (12.0, 20.0, 24.0, 40.0) for f in (0.5, 0.7, 0.9, 0.99)],
)
def test_directional_samples_are_refused(period, x_fraction, low, px_low, noise):
    # Stripes along x, with or without a repeat along y, the second objective a period away (20 um without
    # one). Along y the pair cannot be told apart from a period, or any, shift away. On these 512 x 192
    # frames the search holds ±12.3 um (1:1) and ±31 um (10x/20x) along y. Revision 3 passed the 20 um
    # repeat on a 0.7 ridge with dy ~ 0 (the external review's P1).
    images, view, ordered = _pair(
        {low: px_low, "20x": 0.32},
        (0.0, period or 20.0),
        FakeScene.stripes(period, x_fraction, seed=1),
        shape=(192, 512),
        noise=noise,
    )
    with pytest.raises(OffsetsError, match="^Cannot uniquely match|^The sample's texture is too directional"):
        register_offsets(images, view, ordered)


def test_a_texture_too_directional_to_isolate_its_peak_is_refused():
    # Stripes tilted by 1 degree: the lobe along y does not close within the shifts tested
    images, view, ordered = _pair(
        {"20x-air": 0.32, "20x-water": 0.32}, (0.0, 0.0), FakeScene.stripes(None), matrix=_rot(1.0), shape=(192, 512)
    )
    with pytest.raises(
        OffsetsError,
        match=r"^The sample's texture is too directional to establish a unique match between 20x-air and 20x-water: "
        r"along y its autocorrelation does not fall to zero or a minimum within the shifts tested \(still 0\.\d\d at "
        r"±30\.7 µm\); use a sample textured in every direction\.$",
    ):
        register_offsets(images, view, ordered)


@pytest.mark.parametrize("stretch", [2.0, 4.0])
def test_a_moderately_directional_texture_still_registers(stretch):
    # The textured specimen stretched 2 and 4 times along y (anisotropic pixels on both objectives): its
    # autocorrelation still falls to zero along both axes, and the pair is measured
    matrix = np.diag([1.0, stretch]) / math.sqrt(stretch)
    images, view, ordered = _pair({"20x-air": 0.32, "20x-water": 0.32}, (4.0, -3.0), matrix=matrix)
    result = register_offsets(images, view, ordered)
    assert result.offsets_um["20x-water"] == pytest.approx((4.0, -3.0), abs=0.1)
    assert result.pairs[0].self_similarity < MAX_SELF_SIMILARITY - 0.1


def test_a_match_at_the_edge_of_the_search_area_is_refused():
    # Equal magnifications measure offsets up to 20% of the field (12.3 um on this 192-row frame)
    images, view, ordered = _pair({"20x-air": 0.32, "20x-water": 0.32}, (0.0, 13.0))
    with pytest.raises(
        OffsetsError,
        match=r"^The match between 20x-air and 20x-water is at the edge of the search area \(match score 0\.\d\d\): "
        r"their offset may be larger than it can measure \(±16\.5 µm in x, ±12\.3 µm in y on this frame\)",
    ):
        register_offsets(images, view, ordered)


def test_a_match_with_nothing_to_compare_is_refused_not_called_unique(monkeypatch):
    unique = SelfSimilarity(0.1, (5, 0))
    monkeypatch.setattr(
        offsets, "match_template", lambda *args: Match(1.0, 2.0, 0.95, None, False, unique, None, (9.0, 9.0))
    )
    hw = _machine()
    with pytest.raises(OffsetsError, match="Cannot establish a unique match between 4x and 10x .*: the search area"):
        register_offsets(_images(hw), _true_view(hw), ORDERED)


def test_frames_with_nothing_outside_their_main_lobe_are_refused_not_called_unique():
    # A 140 um grid on an 82 um frame, high-passed: the frames resemble themselves at every shift tested
    images, view, ordered = _pair({"20x-air": 0.32, "20x-water": 0.32}, (0.0, 0.0), FakeScene.grid(period_um=140.0))
    with pytest.raises(
        OffsetsError, match="Cannot establish a unique match between 20x-air and 20x-water .*: their frames"
    ):
        register_offsets(images, view, ordered)


def test_an_objective_without_a_matrix_in_this_cycle_fails():
    hw = _machine()
    view = CalibrationView({"4x": hw.objectives["4x"].matrix_um_per_px, "20x": hw.objectives["20x"].matrix_um_per_px})
    with pytest.raises(OffsetsError, match="no pixel calibration for 10x in this cycle"):
        register_offsets(_images(hw), view, ORDERED)


@pytest.mark.parametrize("period", [20.0, 24.0])
@pytest.mark.parametrize("grid_amp", [0.5, 1.0])
@pytest.mark.parametrize("noise", [0.0, 0.002])
def test_a_periodic_patch_is_never_measured_wrong_at_equal_magnification(period, grid_amp, noise):
    """The final review of C1: a grid patch that fills the matched template but not the whole frame, the
    second objective one period away, beyond the ±20% search of an equal-magnification pair. The frames'
    self-similarity is diluted by the texture around the patch; the matched template's is not."""
    offset = (period, 0.0)
    scene = FakeGridPatchScene(period, (-30.0, 50.0, -25.0, 25.0), grid_amp=grid_amp)
    images, view, ordered = _pair({"20x-a": 0.32, "20x-b": 0.32}, offset, scene=scene, noise=noise)
    try:
        result = register_offsets(images, view, ordered)
    except OffsetsError:
        return  # refused: acceptable
    assert result.offsets_um["20x-b"] == pytest.approx(offset, abs=0.5)  # never a wrong offset
