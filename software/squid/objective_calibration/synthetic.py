"""A synthetic microscope for the calibration engine's tests and for --simulation.

Geometry (all in um): the sample is fixed to the stage. With the stage at S, objective k images
the sample point u = (-S_actual + parcentric_k) + A_k @ p at pixel offset p = (col, row) from the
image centre, where A_k = -M_k and M_k maps a pixel offset to the stage move that centres it.
A mosaic-consistent camera has M = +px * I. Defocus blurs the scene with a Gaussian of
sigma = sqrt(sigma_opt^2 + (0.5 * NA * dz)^2), where dz = z - z_focus_k - height(u): the specimen's
topography (a tilt, a step) is a property of the specimen, independent of the objectives' offsets.

Ground truth for the offsets (spec C §4): objective k centres sample point u with the stage at
parcentric_k - u, so offset_k - offset_ref = parcentric_k - parcentric_ref, and its raw focus
difference is z_focus_k - z_focus_ref. A changer that parks Z per objective (changer_z_um, the
Xeryon frame) moves Z by the difference of its two positions on every switch, like the real one.

The default scene is a broadband random specimen, like a real one: a sum of a few sinusoids has a
line spectrum that phase correlation (which whitens the spectrum) cannot register. It is built from
octaves, periodic noise textures at texel sizes 0.05 um * 2**k, so one physical specimen has detail
at every magnification from 2x to 60x. A pixel sees only the octaves at least MIN_TEXEL_PX of its
size; finer ones would average away inside it.
"""

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, Optional, Tuple

import math

import cv2
import numpy as np

from squid.objective_calibration.hardware import RunCancelled, check_xy_target, check_z_target

TEXEL0_UM = 0.05  # octave k has texels of TEXEL0_UM * 2**k: 0.05 um to 12.8 um
OCTAVES = 9
OCTAVE_TEXELS = 1024  # per side; octave k repeats every OCTAVE_TEXELS texels, >= 716 px wherever it is seen
MIN_TEXEL_PX = 0.7  # an octave is seen by pixels up to 1/0.7 of its texel
_BLUR_LEVELS = 12  # spatially varying defocus is rendered with this many blur levels per frame


@lru_cache(maxsize=2)
def _octaves(seed: int) -> Tuple[np.ndarray, ...]:
    """Independent periodic noise textures, one per octave, each low-passed to ~2-texel features
    (so sampling at >= MIN_TEXEL_PX texels per pixel does not alias) and normalised to unit std."""
    rng = np.random.default_rng(seed)
    f = np.fft.fftfreq(OCTAVE_TEXELS)
    lowpass = np.exp(-2 * np.pi**2 * (f[None, :] ** 2 + f[:, None] ** 2))
    octaves = []
    for _ in range(OCTAVES):
        noise = rng.normal(0.0, 1.0, (OCTAVE_TEXELS, OCTAVE_TEXELS))
        layer = np.real(np.fft.ifft2(np.fft.fft2(noise) * lowpass))
        octaves.append((layer / layer.std()).astype(np.float32))
    return tuple(octaves)


@dataclass
class FakeObjective:
    name: str
    magnification: float
    na: float
    matrix_um_per_px: Optional[np.ndarray] = None
    z_focus_um: float = 0.0
    parcentric_um: Tuple[float, float] = (0.0, 0.0)
    pixel_um: float = 0.5

    def __post_init__(self):
        if self.matrix_um_per_px is None:
            self.matrix_um_per_px = self.pixel_um * np.eye(2)
        self.matrix_um_per_px = np.asarray(self.matrix_um_per_px, dtype=float)


@dataclass(frozen=True)
class FakeTopography:
    """The specimen's height above the focal plane (um) at a sample point: the point is in focus
    at z_focus_k + height. Independent of the objectives' offsets."""

    slope: Tuple[float, float] = (0.0, 0.0)  # dz/dx, dz/dy; 0.01 is a 1% tilt
    step_um: float = 0.0  # the specimen is step_um higher wherever x > step_x_um
    step_x_um: float = 0.0

    def height_um(self, ux: np.ndarray, uy: np.ndarray) -> np.ndarray:
        height = self.slope[0] * ux + self.slope[1] * uy
        if self.step_um:
            height = height + np.where(ux > self.step_x_um, self.step_um, 0.0)
        return height

    @property
    def is_flat(self) -> bool:
        return self.slope == (0.0, 0.0) and self.step_um == 0.0


@dataclass(frozen=True)
class FakeFeature:
    """A strong texture feature: the specimen's amplitude is multiplied by 1 + gain at centre_um,
    falling off as a Gaussian of radius_um. Makes the texture non-uniform, so a contrast metric's
    peak follows the feature's height, not the region centre's."""

    centre_um: Tuple[float, float]
    radius_um: float
    gain: float


@dataclass
class FakeScene:
    amplitudes: np.ndarray = field(default_factory=lambda: np.zeros(0))
    wavevectors: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))
    phases: np.ndarray = field(default_factory=lambda: np.zeros(0))
    octaves: Optional[Tuple[np.ndarray, ...]] = None  # broadband specimen, octave k at TEXEL0_UM * 2**k
    feature: Optional[FakeFeature] = None
    stripe_profile: Optional[np.ndarray] = None  # along x, one sample per STRIPE_TEXEL_UM, the same at every y
    STRIPE_TEXEL_UM = 0.32

    @classmethod
    def random(cls, seed=0, feature: Optional[FakeFeature] = None):
        return cls(octaves=_octaves(seed), feature=feature)

    @classmethod
    def flat(cls):
        return cls()

    @classmethod
    def periodic(cls, period_um=20.0):
        return cls(np.array([1.0]), np.array([[2 * np.pi / period_um, 0.0]]), np.array([0.0]))

    @classmethod
    def grid(cls, period_um=12.0):
        """A two-dimensional grid: gratings along x and y with the same period."""
        k = 2 * np.pi / period_um
        return cls(np.ones(2), np.array([[k, 0.0], [0.0, k]]), np.zeros(2))

    @classmethod
    def stripes(cls, period_um: Optional[float] = 20.0, x_fraction: float = 0.7, seed: int = 0):
        """A directional specimen (the external review's, 2026-09-28): a smooth random profile along x, the
        same at every y, like stripes or fibres, plus a sinusoid along y with period_um (none when None).
        x_fraction of the variance is the profile's. The profile repeats every 4096 samples (1311 um)."""
        f = np.fft.fftfreq(4096)
        noise = np.random.default_rng(seed).normal(size=4096)
        profile = np.real(np.fft.ifft(np.fft.fft(noise) * np.exp(-2 * np.pi**2 * f**2)))  # 1-sample blur
        profile = 0.25 * profile / profile.std()
        if period_um is None:
            return cls(stripe_profile=profile)
        y = np.array([0.25 * math.sqrt(2 * (1 - x_fraction))])  # the sinusoid's variance is (1 - x_fraction) / 16
        return cls(
            y, np.array([[0.0, 2 * np.pi / period_um]]), np.zeros(1), stripe_profile=math.sqrt(x_fraction) * profile
        )

    def _sample(self, ux: np.ndarray, uy: np.ndarray, px_um: float) -> np.ndarray:
        """The unblurred specimen at these sample points, at one pixel scale."""
        specimen = np.zeros(ux.shape, dtype=np.float32)
        for k, octave in enumerate(self.octaves):
            texel = TEXEL0_UM * 2**k
            if texel < MIN_TEXEL_PX * px_um:
                continue
            mapx = ((ux / texel) % OCTAVE_TEXELS).astype(np.float32)
            mapy = ((uy / texel) % OCTAVE_TEXELS).astype(np.float32)
            specimen += cv2.remap(octave, mapx, mapy, cv2.INTER_CUBIC, borderMode=cv2.BORDER_WRAP)
        if self.feature is not None:
            cx, cy = self.feature.centre_um
            r2 = (ux - cx) ** 2 + (uy - cy) ** 2
            specimen = specimen * (1.0 + self.feature.gain * np.exp(-0.5 * r2 / self.feature.radius_um**2))
        return specimen

    @staticmethod
    def _blur(image: np.ndarray, sigma_px: np.ndarray) -> np.ndarray:
        """Gaussian defocus; a spatially varying sigma is rendered with _BLUR_LEVELS blur levels."""
        sigma_px = np.asarray(sigma_px, dtype=float)
        if sigma_px.ndim == 0 or sigma_px.max() - sigma_px.min() < 0.05:
            sigma = float(sigma_px.mean())
            return cv2.GaussianBlur(image, (0, 0), sigma) if sigma > 0.05 else image
        levels = np.linspace(sigma_px.min(), sigma_px.max(), _BLUR_LEVELS)
        index = np.rint((sigma_px - levels[0]) / (levels[-1] - levels[0]) * (_BLUR_LEVELS - 1)).astype(int)
        out = np.empty_like(image)
        for i, sigma in enumerate(levels):
            mask = index == i
            if mask.any():
                blurred = cv2.GaussianBlur(image, (0, 0), float(sigma)) if sigma > 0.05 else image
                out[mask] = blurred[mask]
        return out

    def render(self, ux: np.ndarray, uy: np.ndarray, sigma_um, px_um: float) -> np.ndarray:
        """sigma_um is a scalar, or an array over the frame for spatially varying defocus."""
        sigma_um = np.asarray(sigma_um, dtype=float)
        mean_sigma = float(sigma_um.mean())
        out = np.zeros_like(ux)
        for a, (kx, ky), phase in zip(self.amplitudes, self.wavevectors, self.phases):
            attenuation = np.exp(-0.5 * (mean_sigma**2) * (kx * kx + ky * ky))
            out += a * attenuation * np.sin(kx * ux + ky * uy + phase)
        if self.octaves is not None:
            specimen = self._blur(self._sample(ux, uy, px_um), sigma_um / px_um)
            # One fixed scale for every magnification (the same specimen); 0.3 keeps uint16 unsaturated.
            out = out + 0.3 * specimen / math.sqrt(len(self.octaves))
        if self.stripe_profile is not None:
            n = len(self.stripe_profile)
            profile = np.interp(ux / self.STRIPE_TEXEL_UM, np.arange(n), self.stripe_profile, period=n)
            out = out + self._blur(profile.astype(np.float32), sigma_um / px_um)
        return out


def _inside(u: np.ndarray, low: float, high: float) -> np.ndarray:
    """1 inside [low, high], 0 outside, with 1 um soft edges."""
    return 1.0 / (1.0 + np.exp(low - u)) / (1.0 + np.exp(u - high))


class FakeGridPatchScene(FakeScene):
    """The broadband specimen with a grid patch (the final review of C1): inside rect_um = (x0, x1, y0, y1),
    in sample um, a two-dimensional grid of period_um replaces the texture. A periodic region that fills
    the matched template but not the whole frame, so the frame's own self-similarity is diluted.
    texture_amp adds that much of the texture everywhere, over the grid too (the review of 2dbc53d0): fine
    detail to focus on that leaves the grid's alias in place."""

    def __init__(
        self,
        period_um: float,
        rect_um: Tuple[float, float, float, float],
        grid_amp: float = 1.0,
        seed: int = 0,
        texture_amp: float = 0.0,
    ):
        super().__init__(octaves=_octaves(seed))
        self._grid = FakeScene.grid(period_um)
        self._rect = rect_um
        self._grid_amp = grid_amp
        self._texture_amp = texture_amp

    def render(self, ux: np.ndarray, uy: np.ndarray, sigma_um, px_um: float) -> np.ndarray:
        texture = FakeScene(octaves=self.octaves).render(ux, uy, sigma_um, px_um)
        grid = 0.1 * self._grid.render(ux, uy, sigma_um, px_um)
        x0, x1, y0, y1 = self._rect
        patch = _inside(ux, x0, x1) * _inside(uy, y0, y1)
        return texture * (1 - patch) + self._grid_amp * grid * patch + self._texture_amp * texture


class FakeCalibrationHardware:
    def __init__(
        self,
        objectives: Dict[str, FakeObjective],
        scene: Optional[FakeScene] = None,
        *,
        shape=(192, 256),
        start_objective: Optional[str] = None,
        start_xy_um=(0.0, 0.0),
        start_z_um=0.0,
        z_limits_um=(-5000.0, 5000.0),
        xy_limits_um=((-50000.0, 50000.0), (-50000.0, 50000.0)),
        backlash_um=0.0,
        positioning_noise_um=0.0,
        microstep_um=0.1,
        z_microstep_um=0.01,
        drift_um_per_op=(0.0, 0.0),
        noise=0.002,
        vignetting=0.0,
        seed=0,
        binned_px_um=6.5,
        topography: Optional[FakeTopography] = None,
        changer_z_um: Optional[Dict[str, float]] = None,
        roi_px: Optional[Tuple[int, int, int, int]] = None,
    ):
        self.objectives = dict(objectives)
        self.scene = scene if scene is not None else FakeScene.random(seed)
        self.shape = tuple(shape)
        self._objective = start_objective or next(iter(self.objectives))
        self._cmd = np.array(start_xy_um, dtype=float)
        self._last_dir = np.ones(2)
        self._drift = np.zeros(2)
        self._z = float(start_z_um)
        self._z_limits = tuple(z_limits_um)
        self._xy_limits = tuple(tuple(v) for v in xy_limits_um)
        self.backlash_um = backlash_um
        self.positioning_noise_um = positioning_noise_um
        self.microstep_um = microstep_um
        self.z_microstep_um = z_microstep_um
        self.drift_um_per_op = np.array(drift_um_per_op, dtype=float)
        self.noise = noise
        self.vignetting = vignetting
        self._rng = np.random.default_rng(seed)
        self._jitter = np.zeros(2)
        self.binned_px_um = binned_px_um
        self.topography = topography
        self.changer_z_um = dict(changer_z_um) if changer_z_um else {}  # Z each objective's position parks at
        h, w = self.shape
        self._roi = tuple(int(v) for v in roi_px) if roi_px is not None else (0, 0, w, h)
        self.fail_snap_at: Optional[int] = None
        self.fail_switch_to: Optional[str] = None
        self.refuse_switch = False
        self.snaps = 0
        self.moves = 0

    # --- objectives ---
    def current_objective(self) -> Optional[str]:
        return self._objective

    def switch_objective(self, name: str) -> None:
        if self.refuse_switch:
            raise RunCancelled("Objective switch declined")
        if name == self.fail_switch_to:
            raise RuntimeError(f"changer fault switching to {name}")
        if name not in self.objectives:
            raise KeyError(name)
        # The changer's own mechanical Z (the Xeryon parks position 2 lower): relative, unchecked, like stage.move_z
        self._z += self.changer_z_um.get(name, 0.0) - self.changer_z_um.get(self._objective, 0.0)
        self._objective = name

    # --- stage ---
    @staticmethod
    def _quantize(value, step):
        if not step:
            return value
        # round twice: n * 0.1 is not exact in floating point (e.g. 100 * 0.1 = 10.000000000000002)
        return np.round(np.round(np.asarray(value) / step) * step, 9)

    def get_xy_um(self):
        return (float(self._cmd[0]), float(self._cmd[1]))

    def move_xy_to_um(self, x_um, y_um):
        check_xy_target(self, x_um, y_um)
        target = self._quantize(np.array([x_um, y_um], dtype=float), self.microstep_um)
        direction = np.sign(target - self._cmd)
        self._last_dir = np.where(direction != 0, direction, self._last_dir)
        self._cmd = target
        self._jitter = self._rng.normal(0.0, self.positioning_noise_um, 2) if self.positioning_noise_um else np.zeros(2)
        self._drift += self.drift_um_per_op
        self.moves += 1

    def _actual_xy(self):
        return self._cmd - self._last_dir * self.backlash_um / 2 + self._jitter + self._drift

    def get_z_um(self):
        return float(self._z)

    def move_z_to_um(self, z_um):
        check_z_target(self, z_um)
        self._z = float(self._quantize(z_um, self.z_microstep_um))
        self.moves += 1

    def z_limits_um(self):
        return self._z_limits

    def xy_limits_um(self):
        return self._xy_limits

    def xy_microstep_um(self):
        return self.microstep_um

    # --- camera ---
    def snap(self, objective, channel):
        self.snaps += 1
        if self.fail_snap_at is not None and self.snaps == self.fail_snap_at:
            raise RuntimeError("camera fault")
        assert objective == self._objective, f"snap for {objective} while {self._objective} is in place"
        obj = self.objectives[objective]
        h, w = self.shape
        rows, cols = np.mgrid[0:h, 0:w].astype(float)
        p = np.stack([cols - (w - 1) / 2, rows - (h - 1) / 2])  # (2, h, w) as (col, row)
        a = -obj.matrix_um_per_px
        centre = -self._actual_xy() + np.asarray(obj.parcentric_um, dtype=float)
        ux = centre[0] + a[0, 0] * p[0] + a[0, 1] * p[1]
        uy = centre[1] + a[1, 0] * p[0] + a[1, 1] * p[1]
        sigma_opt = 0.25 * 0.55 / obj.na
        dz = self._z - obj.z_focus_um
        if self.topography is not None and not self.topography.is_flat:
            dz = dz - self.topography.height_um(ux, uy)
        sigma = np.hypot(sigma_opt, 0.5 * obj.na * dz)
        px_um = float(np.sqrt(abs(np.linalg.det(obj.matrix_um_per_px))))
        image = 0.5 + 0.35 * self.scene.render(ux, uy, sigma, px_um)
        if self.vignetting:
            r2 = (p[0] / (w / 2)) ** 2 + (p[1] / (h / 2)) ** 2
            image = image * (1 - self.vignetting * r2 / 2)
        if self.noise:
            image = image + self._rng.normal(0.0, self.noise, image.shape)
        self._drift += self.drift_um_per_op
        return (np.clip(image, 0, 1) * 65535).astype(np.uint16)

    def frame_shape(self, channel):
        return self.shape

    def binning(self):
        return (1, 1)

    def binned_sensor_pixel_um(self):
        return self.binned_px_um

    def unbinned_sensor_pixel_um(self):
        return self.binned_px_um

    def camera_key(self):
        return "fake/camera/0"

    def image_transform(self):
        return (None, None)

    def roi(self) -> Tuple[int, int, int, int]:
        """The camera ROI (x, y, width, height): the whole frame unless a test moves it."""
        return self._roi

    def roi_centre_px(self) -> Optional[Tuple[float, float]]:
        """The ROI centre in unbinned sensor pixels (spec C §5): the fake's ROI units are unbinned."""
        x, y, width, height = self._roi
        return (x + width / 2, y + height / 2)

    def set_roi(self, x: int, y: int, width: int, height: int) -> None:
        self._roi = (int(x), int(y), int(width), int(height))
