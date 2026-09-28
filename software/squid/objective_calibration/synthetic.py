"""A synthetic microscope for the calibration engine's tests and for --simulation.

Geometry (all in um): the sample is fixed to the stage. With the stage at S, objective k images
the sample point u = (-S_actual + parcentric_k) + A_k @ p at pixel offset p = (col, row) from the
image centre, where A_k = -M_k and M_k maps a pixel offset to the stage move that centres it.
A mosaic-consistent camera has M = +px * I. Defocus blurs the scene with a Gaussian of
sigma = sqrt(sigma_opt^2 + (0.5 * NA * dz)^2).

The default scene is a broadband random texture, like a real specimen: a sum of a few sinusoids
has a line spectrum that phase correlation (which whitens the spectrum) cannot register.
"""

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, Optional, Tuple

import cv2
import numpy as np

from squid.objective_calibration.hardware import RunCancelled, check_xy_target, check_z_target

TEXTURE_RES_UM = 0.5
TEXTURE_PERIOD_UM = 1024.0  # at least twice the widest simulated field of view, so no frame sees a repeat


@lru_cache(maxsize=2)
def _broadband_texture(seed: int, size: int) -> np.ndarray:
    """A periodic, unit-variance random field with energy from 2 to ~50 texels (six octaves),
    built in Fourier space so it wraps seamlessly."""
    rng = np.random.default_rng(seed)
    spectrum = np.fft.fft2(rng.normal(0.0, 1.0, (size, size)))
    f = np.fft.fftfreq(size)
    f2 = f[None, :] ** 2 + f[:, None] ** 2
    texture = np.zeros((size, size))
    for sigma in (0.7, 1.4, 2.8, 5.6, 11.2, 22.4):
        layer = np.real(np.fft.ifft2(spectrum * np.exp(-2 * np.pi**2 * sigma**2 * f2)))
        texture += layer / layer.std()
    return (texture / texture.std()).astype(np.float32)


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
    texture: Optional[np.ndarray] = None  # broadband random field, TEXTURE_RES_UM per texel, periodic

    def __post_init__(self):
        self._prepared: Dict[float, np.ndarray] = {}

    @classmethod
    def random(cls, seed=0):
        return cls(texture=_broadband_texture(seed, int(round(TEXTURE_PERIOD_UM / TEXTURE_RES_UM))))

    @classmethod
    def flat(cls):
        return cls()

    @classmethod
    def periodic(cls, period_um=20.0):
        return cls(np.array([1.0]), np.array([[2 * np.pi / period_um, 0.0]]), np.array([0.0]))

    def _texture_for(self, px_um: float) -> np.ndarray:
        """The texture anti-aliased for one pixel scale (half a pixel of blur), computed once."""
        key = round(px_um, 6)
        if key not in self._prepared:
            sigma = 0.5 * px_um / TEXTURE_RES_UM
            self._prepared[key] = cv2.GaussianBlur(self.texture, (0, 0), sigma) if sigma > 0.3 else self.texture
        return self._prepared[key]

    def render(self, ux: np.ndarray, uy: np.ndarray, sigma_um: float, px_um: float) -> np.ndarray:
        out = np.zeros_like(ux)
        for a, (kx, ky), phase in zip(self.amplitudes, self.wavevectors, self.phases):
            attenuation = np.exp(-0.5 * (sigma_um**2) * (kx * kx + ky * ky))
            out += a * attenuation * np.sin(kx * ux + ky * uy + phase)
        if self.texture is not None:
            n = self.texture.shape[0]
            mapx = ((ux / TEXTURE_RES_UM) % n).astype(np.float32)
            mapy = ((uy / TEXTURE_RES_UM) % n).astype(np.float32)
            sampled = cv2.remap(self._texture_for(px_um), mapx, mapy, cv2.INTER_CUBIC, borderMode=cv2.BORDER_WRAP)
            sigma_px = sigma_um / px_um
            if sigma_px > 0.05:
                sampled = cv2.GaussianBlur(sampled, (0, 0), sigma_px)
            out = out + 0.3 * sampled  # 0.3 keeps the uint16 image unsaturated
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
