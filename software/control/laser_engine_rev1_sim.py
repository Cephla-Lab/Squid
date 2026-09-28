"""Simulated rev 1 laser engine and its 560 nm source, for tests and Squid's simulation mode (no vendor protocol here)."""

import json
import time as _time
from typing import List, Optional

from control.laser_engine_rev1_status import SourceStatus

# DF variant table (laser-engine-firmware firmware/src/variant.h): (label, kind, max, tok_required)
_DF_LINES = [
    ("L1 slot1 -2L", "WLD", 0.541, True),
    ("L2 slot2 -3L", "WLD", 2.045, True),
    ("L3 AOM analog 0-5V", "VOLT", 5.0, False),
    ("L4 slot3 -2L", "WLD", 1.196, True),
    ("L5 slot4 -2L", "WLD", 1.308, True),
]


class FakeEngine:
    """Answers the engine text protocol from in-memory state. Deterministic: time advances one step per STAT?."""

    def __init__(self, tok_delay_polls: int = 2):
        self.sent: List[str] = []
        self._out: List[bytes] = []
        self.key_on = True
        self.cover_ok = True
        self.latch_ok = False  # the real board sets its fault latch at every power-up
        self.armed = False
        self.suspended = False
        self.faults: List[str] = []
        self.last_event = ""
        self.t_ms = 8_000  # engine uptime: a few seconds after its power-up
        self.reset_refusal: Optional[str] = None
        self.host_timeout_ms = 5000
        self.tok_delay_polls = tok_delay_polls
        self.shut_src = "MCU"
        self.shut_open = False
        self._shut_resume = False
        self.i2c_fail_count = 0
        self.arm_refusals: List[str] = []
        self._unplugged = False
        self._emit_s = [0.0] * 5  # hour meter per line, seconds (STAT? "hours"; stays 0 here, as TAP3 does on DF)
        self.lines = [
            dict(
                label=lb,
                kind=k,
                max=mx,
                tok_req=tr,
                st="OFF",
                target=0.0,
                now=0.0,
                blocked=0,
                gate=0,
                mod_ext=0,
                resume=0,
            )
            for lb, k, mx, tr in _DF_LINES
        ]
        self.tok = [False] * 5
        self._tok_countdown: List[Optional[int]] = [None] * 5

    # ---- serial-port surface -----------------------------------------------------------------------------------------
    def write(self, data: bytes) -> None:
        if self._unplugged:
            return
        for line in data.decode().splitlines():
            if line.strip():
                self.sent.append(line.strip())
                self._out.append((self._reply(line.strip()) + "\r\n").encode())

    def readline(self) -> bytes:
        return b"" if self._unplugged or not self._out else self._out.pop(0)

    def reset_input_buffer(self) -> None:
        self._out.clear()

    def close(self) -> None:
        pass

    # ---- test hooks --------------------------------------------------------------------------------------------------
    def set_tok(self, line: int, ok: bool) -> None:
        i = line - 1
        self.tok[i] = ok
        if not ok and self.lines[i]["st"] != "OFF" and self.lines[i]["tok_req"]:
            self._line_off(i)
            self.lines[i]["blocked"] = 1
            if "TOK_LOST" not in self.faults:
                self.faults.append("TOK_LOST")
            self.last_event = "TOK_LOST"

    def open_cover(self) -> None:
        self.cover_ok = False
        self._maybe_suspend()

    def close_cover(self) -> None:
        self.cover_ok = True
        if self.suspended:
            self.suspended = False
            for ln in self.lines:
                if ln["resume"]:
                    ln["resume"], ln["st"] = 0, "RAMP"
            self.shut_open = self._shut_resume and self.lines[2]["st"] != "OFF"
            self._shut_resume = False

    def unplug(self) -> None:
        self._unplugged = True

    def queue_stale_reply(self, text: str) -> None:
        self._out.append((text + "\r\n").encode())

    # ---- firmware behaviour ------------------------------------------------------------------------------------------
    def _line_off(self, i: int) -> None:
        self.lines[i]["st"], self.lines[i]["now"] = "OFF", 0.0
        if i == 2:
            self.shut_open = False  # PERMIT3_DF needs FW_EN3: line 3 off closes the shutter

    def _any_on(self) -> bool:
        return self.shut_open or any(ln["st"] != "OFF" for ln in self.lines)

    def _maybe_suspend(self) -> None:
        if (
            self.armed and not self.cover_ok and self._any_on()
        ):  # every tick, like firmware checks() -> raise(EV_COVER_OPEN)
            self._shut_resume = self.shut_open
            for i, ln in enumerate(self.lines):
                ln["resume"] = int(ln["st"] != "OFF")
                self._line_off(i)
            self.suspended = True
            self.last_event = "COVER_OPEN"

    def _tick(self) -> None:
        for i in range(5):
            if self._tok_countdown[i] is not None:
                self._tok_countdown[i] -= 1
                if self._tok_countdown[i] <= 0:
                    self.tok[i], self._tok_countdown[i] = True, None
        self._maybe_suspend()  # an EN accepted while the cover is open is paused at the next tick, as in the firmware
        for ln in self.lines:
            if ln["st"] == "RAMP":
                ln["st"], ln["now"] = "ON", ln["target"]

    def _stat(self) -> str:
        self._tick()
        lines = [
            dict(
                ln,
                fw_en=int(ln["st"] != "OFF"),
                imon_v=None,
                imon_a=None,
                pd_v=None,
                tap=0,
                hours=round(self._emit_s[i] / 3600.0, 3),
            )
            for i, ln in enumerate(self.lines)
        ]
        inputs = dict(
            exp_ok=1,
            tok=[int(t) for t in self.tok],
            v12_ok=1,
            pg_bucks=1,
            wdog_5v=1,
            overtemp_n=1,
            ilock_rb=1,
            interlock_ok=int(self.cover_ok),
            latch_ok=int(self.latch_ok),
            arm_ok=int(self.armed and self.key_on),
        )
        return json.dumps(
            {
                "t": self.t_ms,
                "fw": "sim",
                "var": "DF",
                "armed": int(self.armed),
                "suspended": int(self.suspended),
                "faults": len(self.faults),
                "fault_names": list(self.faults),
                "last_event": self.last_event,
                "host_timeout_ms": self.host_timeout_ms,
                "chips": dict(mcp=1, dac=1, adc_imon=1, adc_pd=1, fan_a=1, fan_b=1),
                "in": inputs,
                "lines": lines,
                "shutter": dict(present=1, src=self.shut_src, open=int(self.shut_open), fb_v=0.0),
            }
        )

    def _reply(self, cmd: str) -> str:
        head, _, arg = cmd.partition(" ")
        h = head.upper()
        word = h.split(":")[0]
        n = int("".join(c for c in word if c.isdigit()) or 0)
        key = "".join(c for c in word if not c.isdigit()) + (":" + h.split(":", 1)[1] if ":" in h else "")
        arg = arg.strip()
        if key == "*IDN?":
            return "Cephla,LaserEngineCarrier-rev1,sim"
        if key == "VAR?":
            return "DF"
        if key == "STAT?":
            return self._stat()
        if key == "HOST:TIMEOUT":
            self.host_timeout_ms = int(float(arg) * 1000)
            return "OK"
        if key == "FAULT:RESET":
            if self.reset_refusal:
                return "ERR " + self.reset_refusal
            self.latch_ok, self.faults = True, []
            for ln in self.lines:
                ln["blocked"] = 0
            return "OK"
        if key == "ARM":
            if self.arm_refusals:
                return "ERR " + self.arm_refusals.pop(0)
            if self.faults:
                return "ERR firmware fault latched - FAULT:RESET"
            if not self.cover_ok:
                return "ERR cover interlock open (INTERLOCK_OK low)"
            if not self.latch_ok:
                return "ERR hardware fault latch set - FAULT:RESET"
            if not self.key_on:
                return "ERR ARM_OK stayed low - key switch off?"
            self.armed = True
            return "OK"
        if key in ("DISARM", "OFF"):
            for i in range(5):
                self._line_off(i)
                self.lines[i]["resume"] = 0
            self.shut_open = self._shut_resume = self.suspended = False  # allOff() forgets a pending cover resume
            self.armed = self.armed and key == "OFF"
            return "OK"
        if key == "LINE:EN":
            i = n - 1
            if arg in ("0", "OFF"):
                self._line_off(i)
                self.lines[i]["resume"] = 0  # an explicit OFF also cancels a pending resume
                return "OK"
            if not self.armed:
                return "ERR not armed"
            if self.lines[i]["blocked"]:
                return "ERR line blocked by a latched fault - FAULT:RESET"
            if self.lines[i]["tok_req"] and not self.tok[i]:
                return "ERR TOK low - TEC not in window"
            if self.lines[i]["st"] == "OFF":
                self.lines[i]["st"] = "RAMP"
            return "OK"
        if key == "LINE:SET":
            i = n - 1
            v = max(0.0, min(float(arg), self.lines[i]["max"]))
            self.lines[i]["target"] = v
            if self.lines[i]["st"] != "OFF":
                self.lines[i]["st"] = "RAMP"
            return "OK %.4f" % v
        if key in ("LINE:MOD", "LINE:GATE"):
            if self.i2c_fail_count > 0:
                self.i2c_fail_count -= 1
                return "ERR I2C write failed"
            field, value = ("mod_ext", arg.upper() == "EXT") if key == "LINE:MOD" else ("gate", arg in ("1", "ON"))
            self.lines[n - 1][field] = int(value)
            return "OK"
        if key == "SHUT:SRC":
            self.shut_src = arg.upper()
            return "OK"
        if key == "SHUT:OPEN":
            want = arg in ("1", "ON")
            if want and not self.armed:
                return "ERR not armed"
            if want and self.lines[2]["st"] == "OFF":
                return "ERR LINE3:EN 1 first (PERMIT3_DF needs FW_EN3)"
            if not want:
                self._shut_resume = False
            self.shut_open = want
            return "OK"
        if key == "TEC:OUT":
            if not self.lines[n - 1]["tok_req"]:
                return "ERR timeout "
            if arg == "1" and not self.tok[n - 1]:
                self._tok_countdown[n - 1] = self.tok_delay_polls
            return "OK CMD:REPLY=1@%d" % n
        return "ERR unknown command"


class FakeSource:
    """Vendor-neutral stand-in for the engine's 560 nm source: off -> (enable) starting for 2 polls -> ready."""

    def __init__(self, max_power_mw: float = 1000.0, min_power_mw: float = 200.0):
        self.max_power_mw = max_power_mw
        self.min_power_mw = min_power_mw
        self.enabled = False
        self.power_mw = 0.0
        self.needs_key = False
        self.fault = False
        self.silent = False
        self.poll_delay_s = 0.0
        self.fail_enable = False
        self.fail_disable = 0  # the number of upcoming disable() calls that raise
        self.calls: List[str] = []
        self._starting_polls = 0

    def poll(self) -> SourceStatus:
        if self.poll_delay_s:
            _time.sleep(self.poll_delay_s)
        if self.silent:
            return SourceStatus(link_ok=False)
        if self.needs_key:
            return SourceStatus(link_ok=True, needs_key=True)
        if self.fault:
            return SourceStatus(link_ok=True, fault=True, detail="simulated")
        if not self.enabled:
            return SourceStatus(link_ok=True, off=True)
        if self._starting_polls > 0:
            self._starting_polls -= 1
            return SourceStatus(link_ok=True, starting=True)
        return SourceStatus(link_ok=True, ready=True, power_mw=self.power_mw)

    def set_power_mw(self, mw: float) -> None:
        self.power_mw = max(self.min_power_mw, min(mw, self.max_power_mw))
        self.calls.append("power %.1f" % mw)

    def enable(self) -> None:
        self.calls.append("enable")
        if self.fail_enable:
            raise RuntimeError("enable refused")
        if not self.needs_key:
            self.enabled, self._starting_polls = True, 2

    def disable(self) -> None:
        self.calls.append("disable")
        if self.fail_disable > 0:
            self.fail_disable -= 1
            raise RuntimeError("disable refused")
        self.enabled = False

    def close(self) -> None:
        self.calls.append("close")


def build_simulated_engine(options=None):
    from control.laser_engine_rev1 import LaserEngineRev1
    from control.laser_engine_rev1_link import EngineLink

    fake, source = FakeEngine(tok_delay_polls=3), FakeSource()
    engine = LaserEngineRev1(link_factory=lambda: EngineLink(fake), source_factory=lambda: source, options=options)
    engine.sim_engine, engine.sim_source = fake, source  # test / demo access
    return engine
