"""The calibration engine's hardware seam on the running application (AI-docs pixel-size design §4.2).

MicroscopeCalibrationHardware implements squid.objective_calibration.hardware.CalibrationHardware on
the app's Microscope (stage, camera, live controller, objective store) and objective changer, in
micrometres. Under --simulation the dialog uses simulation_hardware() instead: the simulated
camera's frames do not move with the stage, so only the synthetic microscope can be calibrated.
"""

import time
from typing import List, Optional, Tuple

import numpy as np

import control._def
from squid.objective_calibration.engine import ObjectiveSpec
from squid.objective_calibration.hardware import CalibrationError, check_xy_target, check_z_target
from squid.objective_calibration.synthetic import FakeCalibrationHardware, FakeObjective, FakeScene

SETTLE_S = 0.15  # spec B §5.1: settle after the last XY move, before the snap


def camera_key(camera_config) -> str:
    """Spec B §4.2: type/model/serial from the configured camera (the camera API has no model or serial getter)."""
    model = camera_config.camera_model.value if camera_config.camera_model is not None else "unknown"
    return f"{camera_config.camera_type.value}/{model}/{camera_config.serial_number or 'unknown'}"


def image_transform(camera_config) -> Tuple[Optional[float], Optional[str]]:
    """The rotation and flip _process_raw_frame applies to every frame."""
    flip = camera_config.flip.value if camera_config.flip is not None else None
    return (camera_config.rotate_image_angle, flip)


def _microstep_um(axis) -> float:
    return 1000.0 * axis.SCREW_PITCH / (axis.MICROSTEPS_PER_STEP * axis.FULL_STEPS_PER_REV)


class MicroscopeCalibrationHardware:
    """Runs in the calibration worker thread; every call blocks until the hardware is done."""

    def __init__(self, microscope, *, camera_config, objective_changer=None):
        self._microscope = microscope
        self._stage = microscope.stage
        self._camera = microscope.camera
        self._live = microscope.live_controller
        self._objective_store = microscope.objective_store
        self._microcontroller = microscope.low_level_drivers.microcontroller
        self._camera_config = camera_config
        self._changer = objective_changer
        self._objective: Optional[str] = microscope.objective_store.current_objective
        self._start_mode = self._live.currentConfiguration
        self._mode_key: Optional[Tuple[str, str]] = None  # (objective, channel) whose settings are applied
        self._frame_shape: Optional[Tuple[int, int]] = None
        self._settle_pending = False

    @property
    def has_changer(self) -> bool:
        return self._changer is not None

    # --- objectives ---
    def current_objective(self) -> Optional[str]:
        return self._objective

    def switch_objective(self, name: str) -> None:
        if name == self._objective:
            return
        # Unknown until the changer confirms: a switch that fails partway (the turret rotated but its
        # Z restore failed) must not look like the old objective, or the restore would skip switching back.
        self._objective = None
        self._mode_key = None
        if self._changer is not None:
            self._changer.move_to_objective(name)
        # Without a changer the operator has switched by hand: the dialog prompts before this call.
        # The store changes without signal_objective_changed (spec C §6.2 step 2.1); the restore puts
        # it back on the start objective before the dialog closes.
        self._objective_store.set_current_objective(name)
        self._objective = name

    # --- stage ---
    def get_z_um(self) -> float:
        return self._stage.get_pos().z_mm * 1000.0

    def move_z_to_um(self, z_um: float) -> None:
        check_z_target(self, z_um)
        self._stage.move_z_to(z_um / 1000.0, blocking=True)

    def get_xy_um(self) -> Tuple[float, float]:
        pos = self._stage.get_pos()
        return (pos.x_mm * 1000.0, pos.y_mm * 1000.0)

    def move_xy_to_um(self, x_um: float, y_um: float) -> None:
        check_xy_target(self, x_um, y_um)
        self._stage.move_x_to(x_um / 1000.0, blocking=True)
        self._stage.move_y_to(y_um / 1000.0, blocking=True)
        self._settle_pending = True

    def z_limits_um(self) -> Tuple[float, float]:
        axis = self._stage.get_config().Z_AXIS
        return (axis.MIN_POSITION * 1000.0, axis.MAX_POSITION * 1000.0)

    def xy_limits_um(self) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        config = self._stage.get_config()
        return tuple((a.MIN_POSITION * 1000.0, a.MAX_POSITION * 1000.0) for a in (config.X_AXIS, config.Y_AXIS))

    def xy_microstep_um(self) -> float:
        """The coarser of the X and Y microsteps: the gates that use it stay conservative on both axes."""
        config = self._stage.get_config()
        return max(_microstep_um(config.X_AXIS), _microstep_um(config.Y_AXIS))

    # --- camera ---
    def snap(self, objective: str, channel: str) -> np.ndarray:
        if objective != self._objective:
            raise RuntimeError(f"snap for {objective} while {self._objective} is in place")
        if self._mode_key != (objective, channel):
            config = self._live.get_channel_by_name(objective, channel)
            if config is None:
                raise CalibrationError(
                    f"Channel '{channel}' is not enabled for {objective}; enable it or pick another channel."
                )
            if control._def.ENABLE_NL5 and control._def.NL5_USE_DOUT and "Fluorescence" in config.name:
                raise CalibrationError(
                    "NL5-triggered channels are not supported for calibration; pick a brightfield channel."
                )
            self._live.set_microscope_mode(config)
            # set_microscope_mode sends MCU commands without waiting; the MCU tracks one pending command.
            self._microcontroller.wait_till_operation_is_completed()
            self._mode_key = (objective, channel)
        if not self._camera.get_is_streaming():
            self._camera.start_streaming()
        if self._settle_pending:
            time.sleep(SETTLE_S)
            self._settle_pending = False
        image = self._microscope.acquire_image()  # raises RuntimeError when the camera returns no frame
        return image.mean(axis=2) if image.ndim == 3 else image

    def frame_shape(self, channel: str) -> Tuple[int, int]:
        """Snaps one frame at the current objective the first time: before "Cycle 1" is logged."""
        if self._frame_shape is None:
            self._frame_shape = tuple(self.snap(self._objective, channel).shape[:2])
        return self._frame_shape

    def binning(self) -> Tuple[int, int]:
        return tuple(self._camera.get_binning())

    def binned_sensor_pixel_um(self) -> float:
        return self._camera.get_pixel_size_binned_um()

    def unbinned_sensor_pixel_um(self) -> float:
        return self._camera.get_pixel_size_unbinned_um()

    def camera_key(self) -> str:
        return camera_key(self._camera_config)

    def image_transform(self) -> Tuple[Optional[float], Optional[str]]:
        return image_transform(self._camera_config)

    def restore_mode(self) -> None:
        """Put the live controller back on the channel it had before the run, and forget which channel
        this adapter applied, so the next run in the same dialog applies its channel again."""
        self._mode_key = None
        if self._start_mode is not None:
            self._live.set_microscope_mode(self._start_mode)
            self._microcontroller.wait_till_operation_is_completed()


def simulation_hardware(
    specs: List[ObjectiveSpec], start_objective: str, binned_px_um: float, seed: int = 0
) -> FakeCalibrationHardware:
    """A synthetic microscope for --simulation, with a Squid+-like stage (0.79 um XY microstep,
    0.1 um positioning error) and a 480x640 frame. Each objective's true pixel size is off nominal
    by 0.5-3% and its focus sits at its own Z, so a run has something to measure. Every catalog
    objective, 2x to 60x, passes every gate (28/28 objective-cycles over four seeds, worst error
    0.3%); a smaller frame makes the high-magnification moves too short for the stage error."""
    rng = np.random.default_rng(seed)
    objectives = {}
    for spec in specs:
        error = rng.uniform(0.005, 0.03) * rng.choice([-1.0, 1.0])
        objectives[spec.name] = FakeObjective(
            spec.name,
            spec.magnification,
            spec.na,
            pixel_um=spec.nominal_px_um * (1.0 + error),
            z_focus_um=float(rng.uniform(-20.0, 20.0)),
        )
    return FakeCalibrationHardware(
        objectives,
        FakeScene.random(seed),
        shape=(480, 640),
        start_objective=start_objective,
        positioning_noise_um=0.1,
        microstep_um=0.79,
        binned_px_um=binned_px_um,
    )
