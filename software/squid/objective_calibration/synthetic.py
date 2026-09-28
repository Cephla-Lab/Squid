"""A synthetic microscope for the calibration engine's tests and for --simulation.

Geometry (all in um): the sample is fixed to the stage. With the stage at S, objective k images
the sample point u = (-S_actual + parcentric_k) + A_k @ p at pixel offset p = (col, row) from the
image centre, where A_k = -M_k and M_k maps a pixel offset to the stage move that centres it.
A mosaic-consistent camera has M = +px * I. Defocus blurs the scene with a Gaussian of
sigma = sqrt(sigma_opt^2 + (0.5 * NA * dz)^2).

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


@dataclass
class FakeScene:
    amplitudes: np.ndarray = field(default_factory=lambda: np.zeros(0))
    wavevectors: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))
    phases: np.ndarray = field(default_factory=lambda: np.zeros(0))
    octaves: Optional[Tuple[np.ndarray, ...]] = None  # broadband specimen, octave k at TEXEL0_UM * 2**k

    @classmethod
    def random(cls, seed=0):
        return cls(octaves=_octaves(seed))

    @classmethod
    def flat(cls):
        return cls()

    @classmethod
    def periodic(cls, period_um=20.0):
        return cls(np.array([1.0]), np.array([[2 * np.pi / period_um, 0.0]]), np.array([0.0]))

    def render(self, ux: np.ndarray, uy: np.ndarray, sigma_um: float, px_um: float) -> np.ndarray:
        out = np.zeros_like(ux)
        for a, (kx, ky), phase in zip(self.amplitudes, self.wavevectors, self.phases):
            attenuation = np.exp(-0.5 * (sigma_um**2) * (kx * kx + ky * ky))
            out += a * attenuation * np.sin(kx * ux + ky * uy + phase)
        if self.octaves is not None:
            specimen = np.zeros(ux.shape, dtype=np.float32)
            for k, octave in enumerate(self.octaves):
                texel = TEXEL0_UM * 2**k
                if texel < MIN_TEXEL_PX * px_um:
                    continue
                mapx = ((ux / texel) % OCTAVE_TEXELS).astype(np.float32)
                mapy = ((uy / texel) % OCTAVE_TEXELS).astype(np.float32)
                specimen += cv2.remap(octave, mapx, mapy, cv2.INTER_CUBIC, borderMode=cv2.BORDER_WRAP)
            sigma_px = sigma_um / px_um
            if sigma_px > 0.05:
                specimen = cv2.GaussianBlur(specimen, (0, 0), sigma_px)
            # One fixed scale for every magnification (the same specimen); 0.3 keeps uint16 unsaturated.
            out = out + 0.3 * specimen / math.sqrt(len(self.octaves))
        return out


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
        sigma = float(np.hypot(sigma_opt, 0.5 * obj.na * (self._z - obj.z_focus_um)))
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
