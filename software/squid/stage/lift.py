"""
The Z lift of an upright system whose focus Z is a PI V-308 (see squid.stage.pi).

The Cephla Z stepper sits under the XY stage and lifts it, with the sample, toward the objective. The V-308 is the
focus Z the rest of Squid uses (CombinedStage routes Z to it), so the lift is a separate device: ``LiftStage`` moves
the stepper with its own travel limits, ``LiftPresets`` keeps the named heights edited in the Lift window, and
``FocusWheelRouter`` hands the control panel's focus wheel to the V-308 (firmware >= 1.8).

Lift Z grows upward (toward the objective). Homing drives it down to the bottom sensor, which becomes 0.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

import control._def as _def
import control.microcontroller
import squid.logging
from squid.config import AxisConfig

_log = squid.logging.get_logger(__name__)


class LiftStage:
    """The Cephla Z stepper used as a lift. Moves are clamped to [min_mm, max_mm], set as firmware Z limits too.

    Thread-safe: moves come from the GUI thread (the Lift window) and from worker threads (homing, the loading
    position, an objective swap), and each one is a command plus a wait on the shared microcontroller.
    """

    _BACKLASH_COMPENSATION_DISTANCE_MM = 0.06  # as CephlaStage: come to rest moving up

    def __init__(
        self,
        microcontroller: control.microcontroller.Microcontroller,
        z_axis_config: AxisConfig,
        min_mm: float,
        max_mm: float,
        lowered_mm: float = 0.0,
    ):
        if not min_mm < max_mm:
            raise ValueError(f"Z lift travel must have min < max, got [{min_mm}, {max_mm}]")
        self._microcontroller = microcontroller
        self._axis = z_axis_config.model_copy(update={"MIN_POSITION": min_mm, "MAX_POSITION": max_mm})
        self._lowered_mm = self.clamp(lowered_mm)
        self._lock = threading.RLock()

    @property
    def min_mm(self) -> float:
        return self._axis.MIN_POSITION

    @property
    def max_mm(self) -> float:
        return self._axis.MAX_POSITION

    @property
    def lowered_mm(self) -> float:
        return self._lowered_mm

    def clamp(self, mm: float) -> float:
        return self._axis.clamp_position(mm)

    def get_position_mm(self) -> float:
        return self._axis.convert_to_real_units(self._microcontroller.get_pos()[2])

    def is_lowered(self, tolerance_mm: float = 0.005) -> bool:
        return self.get_position_mm() <= self._lowered_mm + tolerance_mm

    def limit_switches(self) -> Tuple[bool, bool]:
        """(bottom, top) triggered. Z homes on its RIGHT input, the bottom sensor; LEFT is the top one.

        Firmware older than 1.7 does not report them and both read False.
        """
        return self._microcontroller.z_limit_right_triggered, self._microcontroller.z_limit_left_triggered

    def is_busy(self) -> bool:
        return self._microcontroller.is_busy()

    def apply_limits(self):
        """Program the firmware Z soft limits to the lift travel."""
        sign = self._axis.MOVEMENT_SIGN
        neg_code, pos_code = (
            (_def.LIMIT_CODE.Z_NEGATIVE, _def.LIMIT_CODE.Z_POSITIVE)
            if sign == 1
            else (_def.LIMIT_CODE.Z_POSITIVE, _def.LIMIT_CODE.Z_NEGATIVE)
        )
        with self._lock:
            self._microcontroller.set_lim(pos_code, self._axis.convert_real_units_to_ustep(self.max_mm))
            self._microcontroller.set_lim(neg_code, self._axis.convert_real_units_to_ustep(self.min_mm))

    def _timeout_s(self, distance_mm: float, speed_mm_s: Optional[float] = None) -> float:
        speed = speed_mm_s if speed_mm_s is not None else self._axis.MAX_SPEED
        return max(3.0, 3.0 * abs(distance_mm) / max(0.1, abs(speed)))

    def _move_to_usteps_and_wait(self, mm: float, blocking: bool):
        distance = mm - self.get_position_mm()
        self._microcontroller.move_z_to_usteps(self._axis.convert_real_units_to_ustep(mm))
        if blocking:
            self._microcontroller.wait_till_operation_is_completed(self._timeout_s(distance))

    def move_to(self, mm: float, blocking: bool = True):
        target = self.clamp(mm)
        if target != mm:
            _log.info(f"Z lift target {mm} mm clamped to {target} mm (travel [{self.min_mm}, {self.max_mm}]).")
        with self._lock:
            # Like CephlaStage Z: a downward move goes past the target and comes back up to it.
            if blocking and target < self.get_position_mm():
                self._move_to_usteps_and_wait(max(target - self._BACKLASH_COMPENSATION_DISTANCE_MM, self.min_mm), True)
            self._move_to_usteps_and_wait(target, blocking)

    def move_relative(self, delta_mm: float, blocking: bool = True):
        with self._lock:
            self.move_to(self.get_position_mm() + delta_mm, blocking)

    def home(self):
        """Drive down to the bottom sensor; it becomes 0. Blocking."""
        homing_direction = control.microcontroller.movement_sign_to_homing_direction(self._axis.MOVEMENT_SIGN)
        with self._lock:
            _log.info("Homing the Z lift (down to its bottom sensor).")
            self._microcontroller.home_z(homing_direction=homing_direction)
            self._microcontroller.wait_till_operation_is_completed(
                self._timeout_s(self.max_mm - self.min_mm, self._axis.MAX_SPEED / 5.0)
            )

    def lower(self):
        """Move down to the lowered position. Blocking. Returns the height it left, for restoring later."""
        with self._lock:
            before = self.get_position_mm()
            if not self.is_lowered():
                _log.info(f"Lowering the Z lift from {before:.3f} mm to {self._lowered_mm:.3f} mm.")
                self.move_to(self._lowered_mm)
            return before


@dataclass
class LiftPreset:
    name: str
    height_mm: float


class LiftPresets:
    """Named lift heights, kept in a JSON file and edited in the Lift window.

    ``startup`` names the preset the lift rises to after homing; None (the default) leaves it lowered.
    A missing or unreadable file gives an empty list: the presets are a convenience, never a reason not to start.
    """

    def __init__(self, path: str):
        self._path = path
        self.presets: List[LiftPreset] = []
        self.startup: Optional[str] = None
        self._load()

    def _load(self):
        if not os.path.isfile(self._path):
            return
        try:
            with open(self._path, "r") as f:
                data = json.load(f)
            presets = [LiftPreset(str(p["name"]), float(p["height_mm"])) for p in data.get("presets", [])]
            if not all(math.isfinite(p.height_mm) for p in presets):
                raise ValueError("non-finite height")
            startup = data.get("startup")
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            _log.warning(f"Z lift presets file '{self._path}' could not be read ({e}); starting with no presets.")
            return
        self.presets = presets
        self.startup = startup if any(p.name == startup for p in presets) else None

    def save(self):
        data = {
            "presets": [{"name": p.name, "height_mm": p.height_mm} for p in self.presets],
            "startup": self.startup,
        }
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        tmp_path = f"{self._path}.tmp"
        with open(tmp_path, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, self._path)

    def get(self, name: str) -> Optional[LiftPreset]:
        return next((p for p in self.presets if p.name == name), None)

    def startup_preset(self) -> Optional[LiftPreset]:
        return self.get(self.startup) if self.startup else None


class FocusWheelRouter:
    """Moves the V-308 by the control panel's focus wheel, which the firmware reports instead of moving the lift.

    A polling thread reads Microcontroller.focus_wheel_count and turns each change into a non-blocking absolute
    V-308 move. Consecutive ticks add to the last commanded target rather than to the (still moving) position, so
    a fast turn is not lost; after a pause the target restarts from the measured position. Ticks that arrive while
    ``enabled()`` is False (an acquisition, autofocus, an objective swap with the servo off) are dropped, not saved
    for later.
    """

    _POLL_S = 0.03
    _RESYNC_AFTER_S = 0.5

    def __init__(
        self,
        microcontroller: control.microcontroller.Microcontroller,
        focus_stage,
        mm_per_tick: float,
        enabled: Optional[Callable[[], bool]] = None,
    ):
        self._microcontroller = microcontroller
        self._focus = focus_stage
        self._mm_per_tick = mm_per_tick
        self._enabled = enabled or (lambda: True)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_count: Optional[int] = None
        self._target_mm: Optional[float] = None
        self._range_mm: Optional[Tuple[float, float]] = None
        self._last_tick_time = 0.0

    def set_enabled_check(self, enabled: Callable[[], bool]):
        self._enabled = enabled

    def start(self):
        self._microcontroller.set_focus_wheel_mode(_def.FOCUS_WHEEL_MODE.TO_HOST)
        self._microcontroller.wait_till_operation_is_completed()
        self._thread = threading.Thread(target=self._run, daemon=True, name="focus-wheel-router")
        self._thread.start()
        _log.info(f"Focus wheel drives the V-308 ({self._mm_per_tick * 1000:.4f} um per tick).")

    def stop(self, restore_wheel_to_z: bool = True):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if restore_wheel_to_z:
            self._microcontroller.set_focus_wheel_mode(_def.FOCUS_WHEEL_MODE.DRIVES_Z)
            self._microcontroller.wait_till_operation_is_completed()

    def _run(self):
        while not self._stop.wait(self._POLL_S):
            try:
                self.poll()
            except Exception:
                _log.exception("Focus wheel: V-308 move failed")
                self._target_mm = None

    @staticmethod
    def _wrapped_delta(count: int, last: int) -> int:
        return ((count - last + 2**31) % 2**32) - 2**31

    def poll(self):
        count = self._microcontroller.focus_wheel_count
        if count is None:
            return
        if self._last_count is None:
            self._last_count = count
            return
        ticks = self._wrapped_delta(count, self._last_count)
        self._last_count = count
        if ticks == 0:
            return
        if not self._enabled():
            self._target_mm = None
            return
        now = time.monotonic()
        if self._target_mm is None or now - self._last_tick_time > self._RESYNC_AFTER_S:
            self._target_mm = self._focus.get_pos().z_mm
            # Read with the position: the range can be fenced at runtime (set_limits).
            z_range = getattr(self._focus, "z_range_mm", None)
            self._range_mm = z_range() if z_range is not None else None
        self._last_tick_time = now
        self._target_mm += ticks * self._mm_per_tick
        if self._range_mm is not None:
            # Keep the target reachable: the stage clamps the move anyway, and an unclamped target would make
            # the wheel turned back do nothing until the extra ticks were unwound.
            self._target_mm = min(max(self._target_mm, self._range_mm[0]), self._range_mm[1])
        self._focus.move_z_to(self._target_mm, blocking=False)


def default_focus_wheel_mm_per_tick(z_axis_config: AxisConfig) -> float:
    """PI_FOCUS_WHEEL_UM_PER_TICK, or (when 0) the distance one tick moved the Z stepper, signed by
    PI_FOCUS_WHEEL_INVERT and the Z movement sign so that turning the wheel focuses the same way it did."""
    if _def.PI_FOCUS_WHEEL_UM_PER_TICK:
        mm = _def.PI_FOCUS_WHEEL_UM_PER_TICK / 1000.0
    else:
        mm = abs(z_axis_config.convert_to_real_units(1))
    mm *= z_axis_config.MOVEMENT_SIGN
    return -mm if _def.PI_FOCUS_WHEEL_INVERT else mm
