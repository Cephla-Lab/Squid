"""Same-objective image registration for the pixel-size measurement.

Sign convention (pinned by tests): phase_shift(ref, img) returns (dx, dy), the displacement of
img's content relative to ref in (column, row) pixels. Content that moved right or down is
positive. This is cv2.phaseCorrelate(ref, img)'s order. skimage.registration.phase_cross_correlation,
used elsewhere in the repo (control/utils.py), returns the opposite sign; do not mix them.
"""

from dataclasses import dataclass
from typing import Optional, Tuple

import cv2
import numpy as np

_PEAK_EXCLUSION_PX = 5


@dataclass(frozen=True)
class Shift:
    dx: float
    dy: float
    response: float
    peak_ratio: float


def high_pass(image, sigma_px: Optional[float] = None) -> np.ndarray:
    """The image minus a wide Gaussian blur: removes vignetting and illumination gradients."""
    img = np.asarray(image, dtype=np.float32)
    if img.ndim == 3:
        img = img.mean(axis=2)
    sigma = sigma_px if sigma_px is not None else max(3.0, min(img.shape) / 16)
    return img - cv2.GaussianBlur(img, (0, 0), sigma)


def _peak_ratio(a: np.ndarray, b: np.ndarray) -> float:
    cross = np.fft.fft2(a) * np.conj(np.fft.fft2(b))
    cross /= np.abs(cross) + 1e-12
    surface = np.abs(np.fft.ifft2(cross))
    iy, ix = np.unravel_index(int(np.argmax(surface)), surface.shape)
    h, w = surface.shape
    dy = np.abs(np.arange(h) - iy)
    dx = np.abs(np.arange(w) - ix)
    near = (np.minimum(dy, h - dy)[:, None] <= _PEAK_EXCLUSION_PX) & (
        np.minimum(dx, w - dx)[None, :] <= _PEAK_EXCLUSION_PX
    )
    runner_up = float(np.where(near, 0.0, surface).max())
    return float(surface[iy, ix] / max(runner_up, 1e-12))


def phase_shift(ref, img) -> Shift:
    a = high_pass(ref)
    b = high_pass(img)
    window = cv2.createHanningWindow((a.shape[1], a.shape[0]), cv2.CV_32F)
    (dx, dy), response = cv2.phaseCorrelate(a, b, window)
    return Shift(float(dx), float(dy), float(response), _peak_ratio(a * window, b * window))


def overlap_crops(ref, img, shift_px: Tuple[int, int]):
    """Crop ref and img to the region they share when img's content is displaced by shift_px (col, row)."""
    qx, qy = int(shift_px[0]), int(shift_px[1])
    h, w = np.asarray(ref).shape[:2]
    ref_crop = ref[max(0, -qy) : h - max(0, qy), max(0, -qx) : w - max(0, qx)]
    img_crop = img[max(0, qy) : h - max(0, -qy), max(0, qx) : w - max(0, -qx)]
    return ref_crop, img_crop


# Between objectives of equal or nearly equal magnification the resampled view is about as large as the
# reference frame. It is cropped about its centre so this fraction of the reference frame stays free on
# every side: such a pair measures offsets up to SEARCH_MARGIN of the reference's field of view per axis
# (Match.search_px). The limit in um follows from the frame: 2048 px at 0.376 um/px, for example, gives
# ±154 um. A best match on the border of the search area is refused (Match.at_edge); an offset beyond it
# whose match is not on the border is refused by the other gates, a periodic sample by its self-similarity.
SEARCH_MARGIN = 0.2
# A frame whose autocorrelation has a local maximum this high outside the central peak's lobe repeats
# itself at that shift: an offset cannot be told apart from one a period away, whatever the search area
# (spec C §6.4). The gate is in offsets.match_failure.
MAX_SELF_SIMILARITY = 0.4
_MIN_OVERLAP = 0.5  # the autocorrelation is taken over the shifts that keep half the frame overlapping
_TEMPLATE_HIGH_PASS_FRACTION = 8


@dataclass(frozen=True)
class SelfSimilarity:
    value: float  # the highest local maximum of the frame's normalized autocorrelation outside its main lobe
    shift_px: Tuple[int, int]  # (x, y): the shortest shift with a local maximum within 0.1 of it (a period)


@dataclass(frozen=True)
class OpenLobe:
    """An axis along which the central peak's lobe does not close: the autocorrelation neither falls to
    zero nor reaches a minimum within the shifts tested, so the peak cannot be isolated."""

    shift_px: Tuple[int, int]  # (x, y): the limit of the shifts tested along that axis
    value: float  # the autocorrelation there


@dataclass(frozen=True)
class Match:
    dx: float  # shift of the template's centre from the image's centre, in image pixels (column)
    dy: float  # (row)
    score: float  # TM_CCOEFF_NORMED at the peak
    # The best distinct peak outside the peak's own lobe, over the peak. None when the search area holds
    # no other peak to compare with: uniqueness is then not established (it is never reported as 0).
    runner_up_ratio: Optional[float]
    at_edge: bool  # the peak is on the border of the search area: the true match may lie beyond it
    # The higher of the image's and the resampled view's self-similarity, in the image's pixels. It sees
    # repeats beyond the search area. None when neither frame has a local maximum outside its main lobe;
    # when only one has none, the other's is used.
    self_similarity: Optional[SelfSimilarity]
    open_lobe: Optional[OpenLobe]  # the image's, else the view's; None when both central peaks are isolated
    search_px: Tuple[float, float]  # the largest |dx| and |dy| the search area holds (on its border)


def _quadratic_peak(surface: np.ndarray, iy: int, ix: int) -> Tuple[float, float]:
    """The vertex of a 2-D quadratic through the 3x3 neighbourhood of the integer peak, or the integer
    peak itself when it lies on the border, the fit is not concave, or the vertex leaves the 3x3."""
    h, w = surface.shape
    if not (1 <= iy < h - 1 and 1 <= ix < w - 1):
        return float(ix), float(iy)
    patch = surface[iy - 1 : iy + 2, ix - 1 : ix + 2].astype(float).ravel()
    ys, xs = (g.ravel().astype(float) for g in np.mgrid[-1:2, -1:2])
    design = np.stack([np.ones(9), xs, ys, xs * xs, ys * ys, xs * ys], axis=1)
    _, b, c, d, e, f = np.linalg.lstsq(design, patch, rcond=None)[0]
    hessian = np.array([[2 * d, f], [f, 2 * e]])
    if not (d < 0 and e < 0 and np.linalg.det(hessian) > 0):
        return float(ix), float(iy)
    vx, vy = np.linalg.solve(hessian, [-b, -c])
    if abs(vx) > 1 or abs(vy) > 1:
        return float(ix), float(iy)
    return ix + float(vx), iy + float(vy)


def template_shape(image_shape, template_image_shape, scale: float) -> Tuple[int, int]:
    """(rows, columns) of the template in the image's pixels: the view resampled by `scale`, cropped
    about its centre to at most (1 - 2 * SEARCH_MARGIN) of the image on each axis."""
    (h, w), (rows, cols) = image_shape[:2], template_image_shape[:2]
    return (
        max(2, min(int(round(rows * scale)), int((1 - 2 * SEARCH_MARGIN) * h))),
        max(2, min(int(round(cols * scale)), int((1 - 2 * SEARCH_MARGIN) * w))),
    )


def _resample_template(template: np.ndarray, scale: float, shape: Tuple[int, int]) -> np.ndarray:
    """The template at exactly `scale` times its size, sampled about its centre, `shape` pixels large
    (a central crop when smaller than the full view). cv2.resize would sample at the rounded size's
    ratio instead (38/192 for a 192-row template at 0.2), which moves the resized content's centre by
    a fraction of a pixel and biases every match by that much."""
    h, w = template.shape
    th, tw = shape
    xs = ((np.arange(tw) - (tw - 1) / 2) / scale + (w - 1) / 2).astype(np.float32)
    ys = ((np.arange(th) - (th - 1) / 2) / scale + (h - 1) / 2).astype(np.float32)
    mapx, mapy = np.meshgrid(xs, ys)
    smoothed = cv2.GaussianBlur(template, (0, 0), 0.5 / scale) if scale < 1 else template  # anti-alias
    return cv2.remap(smoothed, mapx, mapy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101)


def _lobe_extent(profile: np.ndarray) -> int:
    """Shifts from zero to the end of the main lobe: the first shift where the autocorrelation stops
    falling or reaches zero (a plateau ends it at once: the match is not unique along that axis)."""
    for shift in range(1, len(profile)):
        if profile[shift] <= 0 or profile[shift] >= profile[shift - 1]:
            return shift
    return len(profile)


def lobe_px(template_hp: np.ndarray) -> Tuple[int, int]:
    """Half-widths (x, y), in pixels, of the main lobe of the template's own normalized autocorrelation,
    out to its first zero or minimum. A match's correlation peak has this shape, so this is what the
    runner-up search excludes: a few pixels on a fine texture, a quarter to half a period on a
    periodic one, whatever the template's size."""
    th, tw = template_hp.shape
    my, mx = th // 4, tw // 4
    auto = cv2.matchTemplate(template_hp, template_hp[my : th - my, mx : tw - mx], cv2.TM_CCOEFF_NORMED)
    return _lobe(auto, my, mx)


def _lobe(auto: np.ndarray, cy: int, cx: int) -> Tuple[int, int]:
    """Half-widths (x, y) of the peak of `auto` at (cy, cx), each the longer of its two directions."""
    rx = max(_lobe_extent(auto[cy, cx:]), _lobe_extent(auto[cy, cx::-1]))
    ry = max(_lobe_extent(auto[cy:, cx]), _lobe_extent(auto[cy::-1, cx]))
    return rx, ry


def runner_up_ratio(surface: np.ndarray, iy: int, ix: int, lobe: Tuple[int, int]) -> Optional[float]:
    """The highest distinct local maximum of `surface` outside the lobe (x, y half-widths) around the
    peak (iy, ix), over the peak. None when there is no such maximum: nothing was compared with the
    peak, so the match is not called unique."""
    rx, ry = lobe
    sy, sx = surface.shape
    # A plateau counts as a maximum (a grating matches all along its lines); beyond the border counts
    # as lower (dilate's default border), so a rise towards a peak outside the area is compared too.
    local_max = surface >= cv2.dilate(surface, np.ones((3, 3), np.uint8))
    near = (np.abs(np.arange(sy) - iy)[:, None] <= ry) & (np.abs(np.arange(sx) - ix)[None, :] <= rx)
    competing = surface[local_max & ~near]
    if competing.size == 0:
        return None
    return float(competing.max()) / float(surface[iy, ix])


def _summed_area(a: np.ndarray) -> np.ndarray:
    table = np.zeros((a.shape[0] + 1, a.shape[1] + 1))
    table[1:, 1:] = a.cumsum(axis=0).cumsum(axis=1)
    return table


def autocorrelation(image_hp: np.ndarray) -> np.ndarray:
    """The normalized correlation of the frame with itself shifted by (u, v), taken over their overlap
    only, for |u| <= w // 2 and |v| <= h // 2, at [v + h // 2, u + w // 2]. The sums of products come
    from one zero-padded FFT (nothing wraps around), the overlap's sums from summed-area tables."""
    f = np.asarray(image_hp, dtype=np.float64)
    f = f - f.mean()
    h, w = f.shape
    vs, us = np.arange(-(h // 2), h // 2 + 1), np.arange(-(w // 2), w // 2 + 1)
    spectrum = np.fft.rfft2(f, s=(2 * h, 2 * w))
    products = np.fft.irfft2(spectrum * np.conj(spectrum), s=(2 * h, 2 * w))[np.ix_(vs % (2 * h), us % (2 * w))]
    r0, r1 = np.maximum(0, -vs), h - np.maximum(0, vs)  # the overlap in the unshifted frame
    c0, c1 = np.maximum(0, -us), w - np.maximum(0, us)
    n = np.outer(r1 - r0, c1 - c0)
    sums = []
    for table in (_summed_area(f), _summed_area(f * f)):
        for dv, du in ((0, 0), (vs, us)):  # the overlap, then the same overlap in the shifted frame
            a, b, c, d = r0 + dv, r1 + dv, c0 + du, c1 + du
            sums.append(table[np.ix_(b, d)] - table[np.ix_(a, d)] - table[np.ix_(b, c)] + table[np.ix_(a, c)])
    s1, s2, q1, q2 = sums
    covariance = products - s1 * s2 / n
    spread = np.sqrt(np.maximum(q1 - s1 * s1 / n, 0.0) * np.maximum(q2 - s2 * s2 / n, 0.0))
    # A flat overlap has no correlation to measure: 0, not a ratio of rounding errors.
    rho = np.divide(covariance, spread, out=np.zeros_like(covariance), where=spread > 1e-12 * float((f * f).sum()))
    return np.clip(rho, -1.0, 1.0).astype(np.float32)


def self_similarity(image_hp: np.ndarray) -> Tuple[Optional[SelfSimilarity], Optional[OpenLobe]]:
    """How closely the frame resembles itself shifted, from its autocorrelation over the shifts that keep
    at least half the frame overlapping (up to half the frame along each axis). The central peak's lobe
    is lobe_px's, taken on this autocorrelation: along each axis out to its first zero or minimum. The
    self-similarity is the highest local maximum outside it (a plateau counts, and beyond the tested
    shifts counts as lower, so a rise towards a period just beyond them is seen too); None when there is
    none. A ridge that rises to a repeat is caught at the repeat. The second value is where the lobe does
    not close (the peak cannot be isolated), or None."""
    rho = autocorrelation(image_hp)
    h, w = np.asarray(image_hp).shape[:2]
    cy, cx = h // 2, w // 2
    rx, ry = _lobe(rho, cy, cx)
    # _lobe_extent returns the whole profile when the autocorrelation never stops falling above zero
    open_lobe = OpenLobe((cx, 0), float(rho[cy, -1])) if rx > cx else None
    open_lobe = OpenLobe((0, cy), float(rho[-1, cx])) if ry > cy else open_lobe
    v = np.abs(np.arange(-cy, cy + 1))[:, None]
    u = np.abs(np.arange(-cx, cx + 1))[None, :]
    tested = (1 - u / w) * (1 - v / h) >= _MIN_OVERLAP
    rho = np.where(tested, rho, -1.0).astype(np.float32)
    peaks = tested & ((u > rx) | (v > ry)) & (rho >= cv2.dilate(rho, np.ones((3, 3), np.uint8)))
    if not peaks.any():
        return None, open_lobe
    best = float(rho[peaks].max())
    rows, cols = np.nonzero(peaks & (rho >= best - 0.1))  # a grid's period, not its diagonal
    nearest = int(np.argmin(np.hypot(rows - cy, cols - cx)))
    return SelfSimilarity(best, (int(cols[nearest] - cx), int(rows[nearest] - cy))), open_lobe


def match_template(image, template_image, scale: float) -> Match:
    """Where the higher-magnification `template_image` appears in `image` (spec C §6.4).

    `scale = px_template / px_image` resamples the template to the image's pixel size; a view too large
    to leave SEARCH_MARGIN free on every side (equal or near-equal magnifications) is cropped about its
    centre. Both are then high-passed with the same sigma, so vignetting and illumination differences
    between the objectives drop out. The whole image is searched with TM_CCOEFF_NORMED. The peak is
    refined by a 2-D quadratic on its 3x3 neighbourhood (good to about 0.15 px on the synthetic
    specimen; the tests pin it). `runner_up_ratio` compares the peak with the best distinct peak
    outside its own lobe: near 1 for a periodic or featureless scene, None when there is none. The
    search sees only the translations it holds, so `self_similarity` measures, on the whole image and
    the whole resampled view, whether the sample repeats at any translation up to half their size (a
    cropped template can only raise it past the limit), and `open_lobe` whether their central peak can
    be isolated at all.
    """
    img = np.asarray(image, dtype=np.float32)
    view = np.asarray(template_image, dtype=np.float32)
    img = img.mean(axis=2) if img.ndim == 3 else img
    view = view.mean(axis=2) if view.ndim == 3 else view
    tpl = _resample_template(view, scale, template_shape(img.shape, view.shape, scale))
    h, w = img.shape
    th, tw = tpl.shape
    sigma = max(3.0, min(th, tw) / _TEMPLATE_HIGH_PASS_FRACTION)
    img_hp, tpl_hp = high_pass(img, sigma), high_pass(tpl, sigma)
    surface = cv2.matchTemplate(img_hp, tpl_hp, cv2.TM_CCOEFF_NORMED)
    iy, ix = (int(i) for i in np.unravel_index(int(np.argmax(surface)), surface.shape))
    score = float(surface[iy, ix])
    px, py = _quadratic_peak(surface, iy, ix)
    # No correlation at all is as ambiguous as it gets (the weak-match gate refuses it first).
    ratio = runner_up_ratio(surface, iy, ix, lobe_px(tpl_hp)) if score > 0 else 1.0
    sy, sx = surface.shape
    at_edge = iy in (0, sy - 1) or ix in (0, sx - 1)
    whole_view = (max(2, int(round(view.shape[0] * scale))), max(2, int(round(view.shape[1] * scale))))
    frames = [self_similarity(f) for f in (img_hp, high_pass(_resample_template(view, scale, whole_view), sigma))]
    repeat = max((s for s, _ in frames if s is not None), key=lambda s: s.value, default=None)
    if (th, tw) != whole_view and repeat is not None and repeat.value < MAX_SELF_SIMILARITY:
        # A cropped template (equal or near-equal magnifications) can only escalate the frames' verdict: a
        # periodic patch that fills it but not the whole frames is diluted in the frames' self-similarity
        # (the final review of C1). The frames' better-sampled result stays whenever it already decides.
        template_repeat = self_similarity(tpl_hp)[0]
        if template_repeat is not None and template_repeat.value >= MAX_SELF_SIMILARITY:
            repeat = template_repeat
    open_lobe = frames[0][1] or frames[1][1]
    dx = px + (tw - 1) / 2 - (w - 1) / 2
    dy = py + (th - 1) / 2 - (h - 1) / 2
    return Match(float(dx), float(dy), score, ratio, at_edge, repeat, open_lobe, ((w - tw) / 2, (h - th) / 2))
