"""THROWAWAY SPIKE: run an unmodified Squid on a Zmotion-controlled well-plate scanner with no
Cephla microcontroller.

This is NOT the product integration (that design lives in AI-docs and uses proper seams). It exists
to put Squid on the instrument within an hour: a fake `Microcontroller` object answers the calls the
app makes (trigger, AF laser, no-op illumination, waits), and a `ZmotionStage` replaces
`CephlaStage`. `install()` monkeypatches both into `control.microcontroller` / `control.microscope`
before the app builds the `Microscope`; `main_zmotion_spike.py` does that and then runs `main_hcs`.

Everything the controller program needs was measured on the bench 2026-10-31 (squid-bench
zmotion/zmc_bench.py): variables `pPosX/Y/Z` in counts, tasks `MoveX/Y/Z` on 1/2/3, `HomeX/Y/Z`,
`?pMoveX` done flags, `?MPOS(ch)`, `?pRes*`, `pSP*` + `RUNTASK 0,SP*`, `CtrlBL` on task 6 with
`pCamExpo` and `pBLCnt=1` (re-sent every shot), `OP(OLaser, OLaserOn/Off)`. Minimum effective Z
step 0.3 um; relative targets accumulate from the commanded target.

Without the vendor library (e.g. on a Mac, or with --simulation) a tiny in-memory controller stands
in so the whole app path can be exercised.
"""

from __future__ import annotations

import ctypes
import logging
import os
import pathlib
import sys
import threading
import time
from typing import Dict, Optional

import squid.logging
from squid.abc import AbstractStage, Pos, StageStage
from squid.config import StageConfig

_log = squid.logging.get_logger("zmotion_spike")

TRIGGER_TASK = 6
AXIS = {
    #  name: (channel query, res query, travel query, maxspd query, move task, move name, move flag, home task, home name, home flag, speed var, speed name, pos var)
    "x": (
        "?AxisX",
        "?pResX",
        "?pTRAX",
        "?pMaxSPDX",
        1,
        "MoveX",
        "?pMoveX",
        1,
        "HomeX",
        "?pHomeX",
        "pSPX",
        "SPX",
        "pPosX",
    ),
    "y": (
        "?AxisY",
        "?pResY",
        "?pTRAY",
        "?pMaxSPDY",
        2,
        "MoveY",
        "?pMoveY",
        2,
        "HomeY",
        "?pHomeY",
        "pSPY",
        "SPY",
        "pPosY",
    ),
    "z": (
        "?AxisZ",
        "?pResZ",
        "?pTRAZ",
        "?pMaxSPDZ",
        3,
        "MoveZ",
        "?pMoveZ",
        3,
        "HomeZ",
        "?pHomeZ",
        "pSPZ",
        "SPZ",
        "pPosZ",
    ),
}
MIN_Z_STEP_MM = 0.0003  # bench: 1- and 2-count Z targets are accepted but do not move
# Bench: XY moves land exactly; single Z steps within 1-2 counts, but back-to-back 5 um wheel steps
# ended 4 counts (0.4 um) short once (run 9), so Z gets a 1 um tolerance.
MOVE_TOLERANCE_COUNTS = {"x": 3, "y": 3, "z": 10}
# Bench run 1 (user: "x and y crossed"): Squid's X moves the controller's Y axis (channel 1, 120 mm)
# and Squid's Y the controller's X (channel 0, 80 mm nominal). ZMOTION_SWAP_XY=0 turns the swap off.
SWAP_XY = os.environ.get("ZMOTION_SWAP_XY", "1") == "1"
# Bench run 3 (user: "0,0 seems to be at the center"): the controller's power-on homing puts 0 at the
# CENTRE of XY travel (forward sensors at +42.25 on the 80 mm axis and +63.0 on the 120 mm axis, reverse
# inputs inactive at 0). Squid wants 0 at a corner, so the shim shifts each XY axis by half its nominal
# travel: squid_mm = ctrl_mm + travel/2. ZMOTION_<X|Y>_OFFSET_MM (controller axis names) overrides the
# shift; ZMOTION_CENTER_ZERO=0 turns it off.
CENTER_ZERO = os.environ.get("ZMOTION_CENTER_ZERO", "1") == "1"


def _controller_axis_for(squid_axis: str) -> str:
    if SWAP_XY and squid_axis in ("x", "y"):
        return "y" if squid_axis == "x" else "x"
    return squid_axis


AXISSTATUS_BITS = {
    4: "forward hard limit",
    5: "reverse hard limit",
    8: "following-error fault",
    9: "forward soft limit",
    10: "reverse soft limit",
    12: "pulse rate over MAX_SPEED",
    18: "power fault",
    22: "alarm input",
}


class ZmotionError(RuntimeError):
    pass


# ----------------------------------------------------------------------------------------------
# Link: vendor library or an in-memory stand-in
# ----------------------------------------------------------------------------------------------


class _NativeLink:
    def __init__(self, ip: str, lib_path: pathlib.Path, timeout_ms: int = 2000):
        if sys.platform == "win32":
            self._dll = ctypes.WinDLL(str(lib_path))
        else:
            self._dll = ctypes.CDLL(str(lib_path))
            init = getattr(self._dll, "ZMC_LinuxLibInit", None)
            if init is not None:
                init.restype = ctypes.c_int32
                init()
        d = self._dll
        d.ZMC_OpenEth.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
        d.ZMC_OpenEth.restype = ctypes.c_int32
        d.ZMC_Close.argtypes = [ctypes.c_void_p]
        d.ZMC_Close.restype = ctypes.c_int32
        d.ZMC_SetTimeOut.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        d.ZMC_SetTimeOut.restype = ctypes.c_int32
        d.ZMC_DirectCommand.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32]
        d.ZMC_DirectCommand.restype = ctypes.c_int32
        d.ZMC_Execute.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32]
        d.ZMC_Execute.restype = ctypes.c_int32
        d.ZMC_ExecuteNoAck.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        d.ZMC_ExecuteNoAck.restype = ctypes.c_int32
        self._handle = ctypes.c_void_p()
        rc = d.ZMC_OpenEth(ip.encode("ascii"), ctypes.byref(self._handle))
        if rc != 0 or not self._handle.value:
            raise ZmotionError(f"ZMC_OpenEth({ip}) failed with code {rc} (20008 = network open failed)")
        d.ZMC_SetTimeOut(self._handle, timeout_ms)
        _log.info(f"Zmotion controller connected at {ip} via {lib_path}")

    def direct(self, cmd: str) -> str:
        buf = ctypes.create_string_buffer(2048)
        rc = self._dll.ZMC_DirectCommand(self._handle, cmd.encode("ascii"), buf, len(buf))
        if rc != 0:
            raise ZmotionError(f"DirectCommand({cmd!r}) failed: {rc}")
        return buf.value.decode("ascii", errors="replace").strip()

    def execute(self, cmd: str) -> str:
        buf = ctypes.create_string_buffer(2048)
        rc = self._dll.ZMC_Execute(self._handle, cmd.encode("ascii"), 100, buf, len(buf))
        if rc != 0:
            raise ZmotionError(f"Execute({cmd!r}) failed: {rc}")
        return buf.value.decode("ascii", errors="replace").strip()

    def no_ack(self, cmd: str) -> None:
        rc = self._dll.ZMC_ExecuteNoAck(self._handle, cmd.encode("ascii"), 100)
        if rc != 0:
            raise ZmotionError(f"ExecuteNoAck({cmd!r}) failed: {rc}")

    def close(self) -> None:
        if self._handle.value:
            self._dll.ZMC_Close(self._handle)
            self._handle = ctypes.c_void_p()


class _SimLink:
    """In-memory stand-in: the few commands the spike uses, with instantaneous motion."""

    def __init__(self):
        self.vars: Dict[str, float] = {
            "pErr": 0,
            "pHome": 10,
            "pCtrlBL": 0,
            "pBLCnt": 0,
            "pCamExpo": 0,
            "pLen": 0,
            "pBLV": 10,
            "pFLCS": 0,
        }
        self.axes = {"x": (0, 2519.69, 80.0, 30.0), "y": (1, 2519.69, 120.0, 30.0), "z": (2, 10000.0, 6.0, 3.0)}
        self.mpos = {0: 0.0, 1: 0.0, 2: 0.0}
        for a, (ch, res, tra, spd) in self.axes.items():
            A = a.upper()
            self.vars.update(
                {
                    f"Axis{A}": ch,
                    f"pRes{A}": res,
                    f"pTRA{A}": tra,
                    f"pMaxSPD{A}": spd,
                    f"pMove{A}": 0,
                    f"pHome{A}": 1,
                    f"pPos{A}": 0,
                    f"pSP{A}": 1,
                }
            )
        self.outputs: Dict[int, int] = {}

    def direct(self, cmd: str) -> str:
        c = cmd.strip()
        if c.startswith("?"):
            q = c[1:]
            if q.upper().startswith("MPOS("):
                return format(self.mpos[int(q[5:-1])], ".4f")
            if q.lower().startswith("idle("):
                return "-1"
            if q.lower().startswith("axisstatus("):
                return "0"
            if q.upper().startswith("PROC_STATUS("):
                return "0"
            for k, v in self.vars.items():
                if k.lower() == q.lower():
                    return format(v, "g")
            raise ZmotionError(f"sim: unknown variable {q}")
        if "=" in c:
            k, v = c.split("=", 1)
            for name in list(self.vars):
                if name.lower() == k.strip().lower():
                    self.vars[name] = float(v)
                    return ""
            raise ZmotionError(f"sim: unknown variable {k}")
        raise ZmotionError(f"sim: unsupported direct command {c!r}")

    def execute(self, cmd: str) -> str:
        if cmd.upper().startswith("OP("):
            return ""
        raise ZmotionError(f"sim: unsupported execute {cmd!r}")

    def no_ack(self, cmd: str) -> None:
        c = cmd.strip().lower()
        if c.startswith("stoptask"):
            return
        if c.startswith("runtask"):
            name = c.split(",", 1)[1].strip()
            for a, (ch, *_rest) in self.axes.items():
                if name == f"move{a}":
                    self.mpos[ch] = self.vars[f"pPos{a.upper()}"]
                    return
                if name == f"home{a}":
                    self.mpos[ch] = 0.0
                    return
                if name == f"sp{a}":
                    return
            if name == "ctrlbl":
                return
        raise ZmotionError(f"sim: unsupported task command {cmd!r}")

    def close(self) -> None:
        pass


class ZmotionLink:
    """One connection, one lock, typed helpers."""

    def __init__(self, ip: str, lib_path: Optional[str], simulate: bool):
        self.lock = threading.RLock()
        self.closed = False  # Microscope.close() closes the link while GUI timers may still poll
        if simulate:
            self._impl = _SimLink()
            _log.warning("Zmotion spike: SIMULATED controller (no vendor library used)")
        else:
            self._impl = _NativeLink(ip, _find_library(lib_path))

    def _check_open(self) -> None:
        if self.closed:
            raise ZmotionError("Zmotion link is closed")

    def direct(self, cmd: str) -> str:
        with self.lock:
            self._check_open()
            return self._impl.direct(cmd)

    def execute(self, cmd: str) -> str:
        with self.lock:
            self._check_open()
            return self._impl.execute(cmd)

    def run_task(self, task: int, name: str) -> None:
        with self.lock:
            self._check_open()
            self._impl.no_ack(f"runtask {task},{name}")

    def stop_task(self, task: int) -> None:
        with self.lock:
            self._check_open()
            self._impl.no_ack(f"stoptask {task}")

    def query_float(self, cmd: str) -> float:
        r = self.direct(cmd)
        return float(r.split()[0])

    def query_int(self, cmd: str) -> int:
        return int(round(self.query_float(cmd)))

    def set_var(self, name: str, value) -> None:
        text = format(value, ".10g") if isinstance(value, float) else str(value)
        self.direct(f"{name}={text}")

    def close(self) -> None:
        with self.lock:
            if not self.closed:
                self.closed = True
                self._impl.close()


def _find_library(explicit: Optional[str]) -> pathlib.Path:
    here = pathlib.Path(__file__).resolve().parent.parent  # software/
    cands = []
    if explicit:
        cands.append(pathlib.Path(explicit))
    if os.environ.get("ZMOTION_LIB"):
        cands.append(pathlib.Path(os.environ["ZMOTION_LIB"]))
    base = here / "drivers and libraries" / "zmotion"
    if sys.platform == "win32":
        cands += [base / "windows" / "x64" / "zmotion.dll", here / "zmotion.dll"]
    else:
        cands += [base / "linux" / "x64" / "libzmotion.so", here / "libzmotion.so"]
    for c in cands:
        if c.exists():
            return c
    raise ZmotionError(
        "Zmotion library not found; set ZMOTION_LIB or copy zmotion.dll next to main_hcs.py. Tried: "
        + ", ".join(map(str, cands))
    )


# ----------------------------------------------------------------------------------------------
# Stage
# ----------------------------------------------------------------------------------------------


class _Axis:
    def __init__(self, name: str, ctrl_name: str, link: ZmotionLink, sign: int, max_speed_cfg: float):
        self.name = name  # Squid's axis name
        self.ctrl_name = ctrl_name  # the controller program's axis name (may differ: SWAP_XY)
        (
            chq,
            resq,
            traq,
            spdq,
            self.move_task,
            self.move_name,
            self.move_flag,
            self.home_task,
            self.home_name,
            self.home_flag,
            self.speed_var,
            self.speed_name,
            self.pos_var,
        ) = AXIS[ctrl_name]
        self.channel = link.query_int(chq)
        self.res = link.query_float(resq)
        # Bench run 2: both XY axes (open-loop steppers, no encoder) hit their forward limit sensors at
        # ~52.5 % of the program's nominal travel, so the program's counts/mm is suspect. Until a ruler
        # test settles it, ZMOTION_<X|Y|Z>_COUNTS_PER_MM in the environment overrides the value read.
        override = os.environ.get(f"ZMOTION_{ctrl_name.upper()}_COUNTS_PER_MM")
        if override:
            _log.warning(f"Zmotion axis {name}: counts/mm overridden {self.res:g} -> {float(override):g} (environment)")
            self.res = float(override)
        self.travel = link.query_float(traq)
        self.max_speed = link.query_float(spdq)
        if self.channel < 0 or self.res <= 0:
            raise ZmotionError(
                f"axis {name} (controller {ctrl_name}) not configured on the controller (channel {self.channel}, res {self.res})"
            )
        self.sign = sign
        self.offset_mm = 0.0  # squid frame = controller frame + offset (before the sign mirror)
        if CENTER_ZERO and ctrl_name in ("x", "y"):
            env = os.environ.get(f"ZMOTION_{ctrl_name.upper()}_OFFSET_MM")
            self.offset_mm = float(env) if env else self.travel / 2.0
            _log.info(
                f"Zmotion axis {name}: controller 0 is the centre of travel; Squid 0 = controller {-self.offset_mm:g} mm"
            )
        self.commanded_mm: Optional[float] = None  # controller frame, mm
        self.min_mm: Optional[float] = None
        self.max_mm: Optional[float] = None
        speed = min(max_speed_cfg, self.max_speed) if max_speed_cfg and max_speed_cfg > 0 else self.max_speed
        link.set_var(self.speed_var, float(speed))
        link.run_task(0, self.speed_name)
        _log.info(
            f"Zmotion axis {name} -> controller {ctrl_name}: channel {self.channel}, {self.res:g} counts/mm, travel {self.travel:g} mm, speed {speed:g} mm/s (max {self.max_speed:g})"
        )


class ZmotionStage(AbstractStage):
    """Same constructor shape as CephlaStage(microcontroller, stage_config): the first argument is
    the ZmotionMcuShim, which carries the link. Positions are the controller's frame scaled by
    MOVEMENT_SIGN from the stage config."""

    def __init__(self, microcontroller, stage_config: StageConfig):
        super().__init__(stage_config)
        self.link: ZmotionLink = microcontroller.link
        microcontroller.stage = self
        cfg = stage_config
        self._axes = {
            "x": _Axis("x", _controller_axis_for("x"), self.link, int(cfg.X_AXIS.MOVEMENT_SIGN), cfg.X_AXIS.MAX_SPEED),
            "y": _Axis("y", _controller_axis_for("y"), self.link, int(cfg.Y_AXIS.MOVEMENT_SIGN), cfg.Y_AXIS.MAX_SPEED),
            "z": _Axis("z", _controller_axis_for("z"), self.link, int(cfg.Z_AXIS.MOVEMENT_SIGN), cfg.Z_AXIS.MAX_SPEED),
        }
        self._busy = 0
        self._busy_lock = threading.Lock()
        self._theta_rad = 0.0
        self._last_pos: Optional[Pos] = None
        self._warned_closed = False

    # ---- conversions (squid mm <-> controller mm) ------------------------------------------

    # The controller frame is 0..travel. MOVEMENT_SIGN = -1 flips the direction while keeping Squid's
    # frame at 0..travel too (squid = travel - controller), so the software limits stay meaningful.
    def _to_ctrl(self, axis: _Axis, squid_mm: float) -> float:
        shifted = squid_mm if axis.sign > 0 else axis.travel - squid_mm
        return shifted - axis.offset_mm

    def _to_squid(self, axis: _Axis, ctrl_mm: float) -> float:
        shifted = ctrl_mm + axis.offset_mm
        return shifted if axis.sign > 0 else axis.travel - shifted

    def _mpos_mm(self, axis: _Axis) -> float:
        return self.link.query_float(f"?MPOS({axis.channel})") / axis.res

    # ---- motion ----------------------------------------------------------------------------

    def _set_busy(self, delta: int) -> None:
        with self._busy_lock:
            self._busy += delta

    def _check_limits(self, axis: _Axis, squid_mm: float) -> None:
        if axis.min_mm is not None and squid_mm < axis.min_mm - 1e-9:
            raise ZmotionError(f"{axis.name} target {squid_mm:.4f} mm below the software limit {axis.min_mm}")
        if axis.max_mm is not None and squid_mm > axis.max_mm + 1e-9:
            raise ZmotionError(f"{axis.name} target {squid_mm:.4f} mm above the software limit {axis.max_mm}")

    def _move_to(self, name: str, squid_mm: float, blocking: bool) -> None:
        axis = self._axes[name]
        self._check_limits(axis, squid_mm)
        target_ctrl = self._to_ctrl(axis, squid_mm)
        counts = int(round(target_ctrl * axis.res))

        def run():
            try:
                with self.link.lock:
                    self._wait_flag(axis, timeout_s=60)  # a previous move on this axis
                    self.link.set_var(axis.pos_var, counts)
                    self.link.run_task(axis.move_task, axis.move_name)
                    axis.commanded_mm = counts / axis.res
                self._wait_move_done(axis, counts)
            finally:
                self._set_busy(-1)

        self._set_busy(+1)
        if blocking:
            run()
        else:
            threading.Thread(target=run, name=f"zmotion-move-{name}", daemon=True).start()

    def _wait_flag(self, axis: _Axis, timeout_s: float) -> None:
        t0 = time.monotonic()
        while self.link.query_int(axis.move_flag) != 0:
            if time.monotonic() - t0 > timeout_s:
                raise ZmotionError(f"{axis.name}: previous move did not finish within {timeout_s} s")
            time.sleep(0.002)

    def _wait_move_done(self, axis: _Axis, counts: int) -> None:
        # bench: the flag reads 1 within ~1 ms of RUNTASK; distance/speed + 0.2 s + margin
        dist_mm = abs(counts / axis.res - (self._mpos_mm(axis)))
        timeout_s = dist_mm / max(axis.max_speed, 0.1) + 2.0 + 0.2
        t0 = time.monotonic()
        seen_running = False
        while True:
            flag = self.link.query_int(axis.move_flag)
            if flag == 1:
                seen_running = True
            elif seen_running or time.monotonic() - t0 > 0.2:
                if self.link.query_int(f"?idle({axis.channel})") == -1:
                    break
            if time.monotonic() - t0 > timeout_s:
                self.link.stop_task(axis.move_task)
                raise ZmotionError(f"{axis.name}: move to {counts} counts timed out after {timeout_s:.1f} s")
            time.sleep(0.002)
        err = self.link.query_int("?pErr")
        if err != 0:
            raise ZmotionError(f"{axis.name}: controller error {err} after move")
        # Bench run 1: X stopped on its forward hard-limit switch at 42.25 mm with idle=-1 and pErr=0,
        # and the spike reported the move done. Limit/alarm bits and the reached position are checked.
        status = self.link.query_int(f"?axisstatus({axis.channel})")
        bad = [name for bit, name in AXISSTATUS_BITS.items() if status & (1 << bit)]
        if bad:
            raise ZmotionError(f"{axis.name}: axis status after move: {', '.join(bad)} (axisstatus={status})")
        reached = self.link.query_float(f"?MPOS({axis.channel})")
        if abs(reached - counts) > MOVE_TOLERANCE_COUNTS[axis.name]:
            raise ZmotionError(
                f"{axis.name}: move ended {reached / axis.res:.4f} mm instead of {counts / axis.res:.4f} mm "
                f"({abs(reached - counts):.0f} counts off)"
            )

    def _move_by(self, name: str, rel_mm: float, blocking: bool) -> None:
        axis = self._axes[name]
        if name == "z" and 0 < abs(rel_mm) < MIN_Z_STEP_MM:
            _log.warning(
                f"Zmotion: Z step {rel_mm * 1e3:+.2f} um is below the controller's minimum effective step (0.3 um); not sent"
            )
            return
        with self.link.lock:
            fresh = self._to_squid(axis, self._mpos_mm(axis))
            base = fresh
            if axis.commanded_mm is not None:
                commanded_squid = self._to_squid(axis, axis.commanded_mm)
                if abs(commanded_squid - fresh) <= 1.5 / axis.res:
                    base = commanded_squid
            self._move_to(name, base + rel_mm, blocking)

    def move_x(self, rel_mm: float, blocking: bool = True):
        self._move_by("x", rel_mm, blocking)

    def move_y(self, rel_mm: float, blocking: bool = True):
        self._move_by("y", rel_mm, blocking)

    def move_z(self, rel_mm: float, blocking: bool = True):
        self._move_by("z", rel_mm, blocking)

    def move_x_to(self, abs_mm: float, blocking: bool = True):
        self._move_to("x", abs_mm, blocking)

    def move_y_to(self, abs_mm: float, blocking: bool = True):
        self._move_to("y", abs_mm, blocking)

    def move_z_to(self, abs_mm: float, blocking: bool = True):
        self._move_to("z", abs_mm, blocking)

    def get_pos(self) -> Pos:
        # Bench run 6: after Microscope.close() (the settings restart path) the GUI's position timers and
        # the final cleanup still call get_pos(); answer from the last reading instead of raising 100x.
        if self.link.closed:
            if not self._warned_closed:
                self._warned_closed = True
                _log.warning("Zmotion: link closed; get_pos() returns the last known position from now on")
            return self._last_pos or Pos(x_mm=0.0, y_mm=0.0, z_mm=0.0, theta_rad=self._theta_rad)
        with self.link.lock:
            x = self._to_squid(self._axes["x"], self._mpos_mm(self._axes["x"]))
            y = self._to_squid(self._axes["y"], self._mpos_mm(self._axes["y"]))
            z = self._to_squid(self._axes["z"], self._mpos_mm(self._axes["z"]))
        self._last_pos = Pos(x_mm=x, y_mm=y, z_mm=z, theta_rad=self._theta_rad)
        return self._last_pos

    def get_state(self) -> StageStage:
        with self._busy_lock:
            return StageStage(busy=self._busy > 0)

    def home(self, x: bool, y: bool, z: bool, theta: bool, blocking: bool = True):
        # The controller auto-homes at power-on (?pHome == 10); re-homing an already-homed axis
        # costs 8 to 11.5 s on the bench, so only axes whose home flag is not set are homed.
        for name, wanted in (("z", z), ("x", x), ("y", y)):
            if not wanted:
                continue
            axis = self._axes[name]
            with self.link.lock:
                if (
                    self.link.query_int("?pHome") == 10
                    and self.link.query_int(axis.home_flag) == 1
                    and self.link.query_int("?pErr") == 0
                ):
                    _log.info(f"Zmotion: {name} already homed by the controller, skipping")
                    axis.commanded_mm = None
                    continue
                _log.info(f"Zmotion: homing {name}")
                self._set_busy(+1)
                try:
                    self.link.stop_task(axis.home_task)
                    self.link.run_task(axis.home_task, axis.home_name)
                    t0 = time.monotonic()
                    while self.link.query_int(axis.home_flag) != 1:
                        if time.monotonic() - t0 > 120:
                            raise ZmotionError(f"{name}: homing did not finish within 120 s")
                        time.sleep(0.01)
                    err = self.link.query_int("?pErr")
                    if err != 0:
                        raise ZmotionError(f"{name}: controller error {err} after homing")
                    axis.commanded_mm = None
                finally:
                    self._set_busy(-1)

    # The navigation widget calls these (CephlaStage has them); controller counts are the unit.
    def x_mm_to_usteps(self, mm: float) -> int:
        return int(round(mm * self._axes["x"].res))

    def y_mm_to_usteps(self, mm: float) -> int:
        return int(round(mm * self._axes["y"].res))

    def z_mm_to_usteps(self, mm: float) -> int:
        return int(round(mm * self._axes["z"].res))

    def zero(self, x: bool, y: bool, z: bool, theta: bool, blocking: bool = True):
        _log.warning("Zmotion: zero() is not supported (the frame is the controller's homed frame); ignored")

    def set_limits(
        self,
        x_pos_mm=None,
        x_neg_mm=None,
        y_pos_mm=None,
        y_neg_mm=None,
        z_pos_mm=None,
        z_neg_mm=None,
        theta_pos_rad=None,
        theta_neg_rad=None,
    ):
        for name, lo, hi in (("x", x_neg_mm, x_pos_mm), ("y", y_neg_mm, y_pos_mm), ("z", z_neg_mm, z_pos_mm)):
            axis = self._axes[name]
            if lo is not None:
                axis.min_mm = lo
            if hi is not None:
                axis.max_mm = hi

    def close(self):
        pass


# ----------------------------------------------------------------------------------------------
# Fake microcontroller
# ----------------------------------------------------------------------------------------------


class ZmotionMcuShim:
    """Answers the Microcontroller calls Squid makes. Real: camera trigger, AF laser. No-op:
    illumination (the light is always on), DAC, joystick, PID, watchdog. Unknown attributes log
    once and return a no-op, which is acceptable only because this is a throwaway spike."""

    firmware_version = (1, 0)  # below 1.1: Microscope skips the illumination watchdog
    last_command_aborted_error = None

    def __init__(self, link: ZmotionLink):
        self.link = link
        self.stage: Optional[ZmotionStage] = None
        self._cam_expo_us: Optional[int] = None
        # Light: spec 3.2.10 makes pBLV (0 off, 1..100) an input of the CtrlBL task. Bench 2026-10-31
        # (camera as detector): pBLV=0 + CtrlBL switches the light OFF and it stays off; 5/50/100 give the
        # same brightness (no dimming on this unit). So Squid's turn_on/turn_off are real, the slider value
        # is still sent as pBLV (if the vendor ever maps it to brightness it works unchanged), and every
        # CtrlBL run (on, off, trigger) also emits the ~1 ms OP(2) camera pulse.
        self._blv: Optional[int] = None  # slider %, None = never set (light left as found)
        self._blv_sent: Optional[int] = None  # last pBLV written
        self._light_wanted = True  # turn_off_illumination() -> False
        self._laser_on = False
        self._warned = set()

    # ---- camera trigger (bench-verified sequence) -----------------------------------------

    def send_hardware_trigger(
        self, control_illumination: bool = False, illumination_on_time_us: int = 0, trigger_output_ch: int = 0
    ):
        us = int(illumination_on_time_us) if illumination_on_time_us else 0
        with self.link.lock:
            if us and us != self._cam_expo_us:
                self.link.set_var("pCamExpo", us)
                self._cam_expo_us = us
            self._send_blv_if_changed()
            self.link.set_var("pBLCnt", 1)  # not sticky on the bench: re-sent every shot
            self.link.run_task(TRIGGER_TASK, "CtrlBL")

    def _wanted_blv(self) -> Optional[int]:
        if not self._light_wanted:
            return 0
        return self._blv  # None until the slider has been seen: leave pBLV as found

    def _send_blv_if_changed(self) -> None:
        wanted = self._wanted_blv()
        if wanted is not None and wanted != self._blv_sent:
            self.link.set_var("pFLCS", 0)
            self.link.set_var("pBLV", int(wanted))
            self._blv_sent = wanted
            _log.info(f"Zmotion: pBLV={wanted} ({'light off' if wanted == 0 else 'light on'})")

    def _run_ctrlbl_once(self) -> None:
        """Apply pBLV through one single-shot CtrlBL run (also pulses OP(2): a stray camera trigger)."""
        with self.link.lock:
            self._send_blv_if_changed()
            self.link.set_var("pBLCnt", 1)
            self.link.run_task(TRIGGER_TASK, "CtrlBL")

    def _set_brightness_percent(self, pct: float) -> None:
        self._blv = max(0, min(100, int(round(pct))))

    def set_strobe_delay_us(self, us: int):
        pass

    def set_trigger_mode(self, mode):
        pass

    # ---- AF laser ---------------------------------------------------------------------------

    def turn_on_AF_laser(self):
        self.link.execute("OP(OLaser, OLaserOn)")
        self._laser_on = True

    def turn_off_AF_laser(self):
        self.link.execute("OP(OLaser, OLaserOff)")
        self._laser_on = False

    def set_pin_level(self, pin: int, level: int):
        import control._def as _def

        if pin == _def.MCU_PINS.AF_LASER:
            self.turn_on_AF_laser() if level else self.turn_off_AF_laser()

    # ---- illumination: the light is always on; nothing to do -------------------------------

    def turn_on_illumination(self):
        # LiveController / MultiPointWorker call this in software-trigger mode (never per frame in
        # hardware mode, where the trigger itself carries pBLV). A changed value is applied through one
        # CtrlBL run whose OP(2) pulse a software-triggered camera ignores.
        self._light_wanted = True
        if self._wanted_blv() is not None and self._wanted_blv() != self._blv_sent:
            self._run_ctrlbl_once()

    def turn_off_illumination(self):
        # Real on this unit: pBLV=0 through CtrlBL switches the light off and it stays off. Called at
        # stop_live and between software-triggered frames; in hardware mode only at stop_live.
        self._light_wanted = False
        if self._blv_sent != 0:
            self._run_ctrlbl_once()

    def set_illumination(self, source, intensity, *args, **kwargs):
        self._set_brightness_percent(float(intensity))

    def set_illumination_led_matrix(self, source, r, g, b):
        import control._def as _def

        factor = max(_def.LED_MATRIX_R_FACTOR, _def.LED_MATRIX_G_FACTOR, _def.LED_MATRIX_B_FACTOR) or 1.0
        self._set_brightness_percent(100.0 * max(r, g, b) / factor)

    def supports_multi_port(self) -> bool:
        return False

    # ---- sync -------------------------------------------------------------------------------

    def is_busy(self) -> bool:
        return self.stage.get_state().busy if self.stage is not None else False

    def wait_till_operation_is_completed(self, timeout_limit_s: float = 5, *args, **kwargs):
        if self.stage is not None:
            t0 = time.monotonic()
            while self.stage.get_state().busy:
                if time.monotonic() - t0 > timeout_limit_s:
                    raise TimeoutError("Zmotion: stage still busy")
                time.sleep(0.002)

    # ---- no-ops -----------------------------------------------------------------------------

    def enable_joystick(self, enabled: bool):
        pass

    def add_joystick_button_listener(self, fn):
        pass

    def analog_write_onboard_DAC(self, dac, value):
        pass

    def configure_dac80508_refdiv_and_gain(self, div, gains):
        pass

    def set_piezo_um(self, um):
        raise ZmotionError("no piezo on this instrument")

    def reset(self):
        pass

    def turn_off_all_pid(self):
        pass

    def unlock_panel(self):
        pass

    def lock_panel(self):
        pass

    def acknowledge_aborted_command(self):
        pass

    def set_watchdog_timeout(self, s):
        pass

    def start_heartbeat(self, s):
        pass

    def close(self):
        try:
            if self._laser_on:
                self.turn_off_AF_laser()
        finally:
            self.link.close()

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if name not in self._warned:
            self._warned.add(name)
            _log.warning(f"Zmotion spike: Microcontroller.{name} called; answering with a no-op")

        def _noop(*args, **kwargs):
            return None

        return _noop


# ----------------------------------------------------------------------------------------------
# Install
# ----------------------------------------------------------------------------------------------


def install(ip: str = "10.0.0.100", lib_path: Optional[str] = None, simulate: bool = False) -> ZmotionLink:
    """Monkeypatch the microcontroller and stage factories before Microscope.build_from_global_config."""
    import control.microcontroller
    import control.microscope

    link = ZmotionLink(ip, lib_path, simulate)

    def _no_serial(*args, **kwargs):
        return None

    def _make_shim(serial_device=None, reset_and_initialize=True, **kwargs):
        return ZmotionMcuShim(link)

    def _make_stage(microcontroller, stage_config):
        return ZmotionStage(microcontroller, stage_config)

    control.microcontroller.get_microcontroller_serial_device = _no_serial
    control.microcontroller.Microcontroller = _make_shim
    control.microscope.CephlaStage = _make_stage
    _log.warning("Zmotion spike installed: fake microcontroller + ZmotionStage (THROWAWAY, not the product path)")
    return link
