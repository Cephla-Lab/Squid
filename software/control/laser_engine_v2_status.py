"""Per-line readiness of laser engine v2 from one STAT? reply (+ the engine's own 560 nm source on DF). Pure, no I/O."""

import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Dict, Iterable, Optional, Tuple


class LineState(IntEnum):
    READY = 0
    STARTING = 1  # enabled and ramping / source starting / engine readbacks not up yet
    WARMING_UP = 2  # TEC not in window yet (TOK low)
    OFF = 3  # armed, line not enabled
    NOT_ARMED = 4
    PAUSED = 5  # cover open (the firmware resumes paused lines itself)
    SOURCE_OFF = 6  # 560 line enabled, its source is off
    NEEDS_KEY = 7  # 560 source: key must be turned OFF then ON
    NOT_CONFIGURED = 8  # 560 source not configured in this build / not found
    BLOCKED = 9  # TOK lost on this line: latched until FAULT:RESET
    FAULT = 10  # engine fault latched / 560 source fault or not responding
    UNUSED = 11  # nothing on this line in this variant


ERROR_STATES = frozenset({LineState.BLOCKED, LineState.FAULT})
REFUSE_STATES = ERROR_STATES | {LineState.NEEDS_KEY, LineState.NOT_CONFIGURED}
SOURCE_560_LINE = 3


@dataclass(frozen=True)
class SourceStatus:
    link_ok: bool
    ready: bool = False
    starting: bool = False
    off: bool = False
    needs_key: bool = False
    fault: bool = False
    detail: str = ""
    power_mw: float = 0.0
    settled: bool = True  # False while the source is still moving to the requested power (decided by the engine)


@dataclass(frozen=True)
class LineInfo:
    key: str
    line: int
    label: str
    kind: str
    state: LineState
    reason: str
    target: float
    now: float
    max: float
    gate: bool = False  # firmware gate held on without a TTL (LINE<n>:GATE 1)

    @property
    def is_ready(self) -> bool:
        return self.state == LineState.READY

    @property
    def is_error(self) -> bool:
        return self.state in ERROR_STATES

    @property
    def display_state(self) -> LineState:
        return self.state


@dataclass(frozen=True)
class EngineV2Status:
    channels: Dict[str, LineInfo]
    armed: bool
    suspended: bool
    interlock_ok: bool
    fault_names: Tuple[str, ...]
    last_event: str
    variant: str
    timestamp_s: float

    def is_ready_for(self, keys: Iterable[str]) -> bool:
        return all(k in self.channels and self.channels[k].is_ready for k in keys)

    def any_error(self) -> bool:
        return any(info.is_error for info in self.channels.values())


def is_560_line(stat: dict, line: int) -> bool:
    return stat.get("var") == "DF" and line == SOURCE_560_LINE


def _source_state(source: Optional[SourceStatus]) -> Tuple[LineState, str]:
    if source is None or not source.link_ok:
        return LineState.FAULT, "560 nm source not responding"
    if source.needs_key:
        return LineState.NEEDS_KEY, "turn the 560 key OFF then ON"
    if source.fault:
        return LineState.FAULT, f"560 nm source fault {source.detail}".strip()
    if source.ready:
        if not source.settled:
            return LineState.STARTING, "560 nm source settling to its set-point"
        return LineState.READY, ""
    if source.off:
        return LineState.SOURCE_OFF, "560 nm source off"
    return LineState.STARTING, f"560 nm source starting {source.detail}".strip()


def _line_state(stat: dict, i: int, ln: dict, system_faults, source, has_source) -> Tuple[LineState, str]:
    inputs = stat["in"]
    n = i + 1
    if ln["kind"] == "NONE":
        return LineState.UNUSED, ""
    if system_faults:
        return LineState.FAULT, "engine fault: " + ", ".join(system_faults) + " - FAULT:RESET"
    if ln["blocked"]:
        return LineState.BLOCKED, f"L{n} TEC left its window (TOK lost) - FAULT:RESET"
    if is_560_line(stat, n) and not has_source:
        return LineState.NOT_CONFIGURED, "560 nm source not configured"
    if not inputs.get("exp_ok", 1):
        return LineState.STARTING, "engine readbacks not available yet"
    if stat.get("suspended") or not inputs.get("interlock_ok", 1):
        return LineState.PAUSED, "cover open - lines resume when it closes"
    if not stat["armed"]:
        return LineState.NOT_ARMED, "not armed"
    if ln["st"] == "OFF":
        if is_560_line(stat, n):
            src_state, src_reason = _source_state(source)
            if src_state in REFUSE_STATES:  # key not cycled / source fault: say so before anyone enables line 3
                return src_state, src_reason
        if ln["tok_req"] and not inputs["tok"][i]:
            return LineState.WARMING_UP, f"L{n} TEC not in window yet"
        return LineState.OFF, "line not enabled"
    if ln["st"] == "RAMP":
        return LineState.STARTING, "ramping to set-point"
    if is_560_line(stat, n):
        return _source_state(source)
    return LineState.READY, ""


def parse_status(
    stat: dict, source: Optional[SourceStatus] = None, has_source: bool = False, timestamp_s: float = 0.0
) -> EngineV2Status:
    system_faults = [
        f for f in stat.get("fault_names", []) if f != "TOK_LOST"
    ]  # TOK_LOST is per line (the blocked flag)
    channels = {}
    for i, ln in enumerate(stat["lines"]):
        state, reason = _line_state(stat, i, ln, system_faults, source, has_source)
        key = f"L{i + 1}"
        channels[key] = LineInfo(
            key=key,
            line=i + 1,
            label=ln["label"],
            kind=ln["kind"],
            state=state,
            reason=reason,
            target=float(ln["target"] or 0.0),
            now=float(ln["now"] or 0.0),
            max=float(ln["max"] or 0.0),
            gate=bool(ln.get("gate", 0)),
        )
    return EngineV2Status(
        channels=channels,
        armed=bool(stat["armed"]),
        suspended=bool(stat.get("suspended")),
        interlock_ok=bool(stat["in"].get("interlock_ok", 1)),
        fault_names=tuple(stat.get("fault_names", [])),
        last_event=str(stat.get("last_event", "")),
        variant=str(stat.get("var", "")),
        timestamp_s=timestamp_s or time.time(),
    )
