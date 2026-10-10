"""The hardware seam of the objective calibration engine. Every quantity is in micrometres."""

from typing import Optional, Protocol, Tuple

import numpy as np

LIMIT_MESSAGE = "Calibration moves would leave the stage limits; start closer to the centre of travel."


class CalibrationError(Exception):
    """A calibration step failed a quality gate or a limit; the message is shown to the user."""


class LimitError(CalibrationError):
    """A target lies outside the stage limits. Raised before moving; targets are never clamped."""


class RestoreError(Exception):
    """Restoring the objective, XY or Z after a cycle failed; the run must stop."""


class RunCancelled(Exception):
    """The operator cancelled (for example, declined a manual objective switch). Not a quality gate."""


class CalibrationHardware(Protocol):
    def current_objective(self) -> Optional[str]:
        """The objective in place; None while a switch is unconfirmed (a switch that failed partway),
        so the restore always switches back."""
        ...

    def switch_objective(self, name: str) -> None: ...

    def get_z_um(self) -> float: ...

    def move_z_to_um(self, z_um: float) -> None: ...

    def get_xy_um(self) -> Tuple[float, float]: ...

    def move_xy_to_um(self, x_um: float, y_um: float) -> None: ...

    def z_limits_um(self) -> Tuple[float, float]: ...

    def xy_limits_um(self) -> Tuple[Tuple[float, float], Tuple[float, float]]: ...

    def xy_microstep_um(self) -> float: ...

    def snap(self, objective: str, channel: str) -> np.ndarray: ...

    def frame_shape(self, channel: str) -> Tuple[int, int]:
        """(height, width) of the frames snap() returns, after the camera's rotation, flip and crop."""
        ...

    def binning(self) -> Tuple[int, int]: ...

    def binned_sensor_pixel_um(self) -> float: ...

    def unbinned_sensor_pixel_um(self) -> float: ...

    def camera_key(self) -> str: ...

    def image_transform(self) -> Tuple[Optional[float], Optional[str]]: ...

    def roi(self) -> Tuple[int, int, int, int]:
        """The camera ROI (x, y, width, height) exactly as the camera driver reports it."""
        ...

    def roi_centre_px(self) -> Optional[Tuple[float, float]]:
        """The ROI centre (x, y) in unbinned sensor pixels, or None when the driver's ROI units are not
        verified: with roi() and binning(), the offset calibration's XY key (spec C §5)."""
        ...


def check_z_target(hw: CalibrationHardware, z_um: float) -> None:
    low, high = hw.z_limits_um()
    if not low <= z_um <= high:
        raise LimitError(LIMIT_MESSAGE)


def check_xy_target(hw: CalibrationHardware, x_um: float, y_um: float) -> None:
    (x_low, x_high), (y_low, y_high) = hw.xy_limits_um()
    if not (x_low <= x_um <= x_high and y_low <= y_um <= y_high):
        raise LimitError(LIMIT_MESSAGE)
