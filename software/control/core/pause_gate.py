"""Single pause control point for a running acquisition.

Several parties may want an acquisition to pause: the disk-space guard when the save disk is nearly
full, or an operator pressing Pause. Each is a *holder* identified by a reason string. The acquisition
is paused while any holder is active, so releasing one reason never overrides another (an operator
Resume cannot override a full disk, and freed disk space cannot override an operator pause).

The acquisition thread parks itself in ``wait_while_paused`` at safe checkpoints (FOV and timepoint
boundaries). The wait is abort-aware through ``cancel_fn`` and runs an optional ``tick`` callback every
poll so the caller can keep heart-beating and re-evaluate its own hold.

This is the interim pause mechanism described in the acquisition-engine adjudication (AI-docs,
2026-07-19): a precursor of the control-plane seam, deliberately limited to hold / release / wait.
Stdlib only, no Qt, safe to call from any thread.
"""

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

import squid.logging

_log = squid.logging.get_logger(__name__)


@dataclass(frozen=True)
class PauseState:
    """Snapshot of the gate.

    Attributes:
        paused: True while at least one holder is active.
        reasons: Active holder reasons, sorted.
        since: Wall-clock time (``time.time()``) the current pause started, or None when not paused.
        paused_total_s: Total seconds spent paused over the gate's lifetime (completed pauses only).
    """

    paused: bool
    reasons: Tuple[str, ...]
    since: Optional[float]
    paused_total_s: float


class PauseGate:
    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._holders: set = set()
        self._since_wall: Optional[float] = None
        self._since_mono: Optional[float] = None
        self._paused_total_s: float = 0.0

    def hold(self, reason: str) -> None:
        """Request a pause for ``reason``. Idempotent per reason."""
        if not reason:
            raise ValueError("A pause reason is required")
        with self._cond:
            if reason in self._holders:
                return
            self._holders.add(reason)
            if self._since_mono is None:
                self._since_mono = time.monotonic()
                self._since_wall = time.time()
            _log.info(f"Pause requested: {reason} (active reasons: {sorted(self._holders)})")

    def release(self, reason: str) -> None:
        """Withdraw the pause request for ``reason``. No-op if it was not held."""
        with self._cond:
            if reason not in self._holders:
                return
            self._holders.discard(reason)
            if self._holders:
                _log.info(f"Pause released: {reason} (still paused by: {sorted(self._holders)})")
                return
            self._end_pause_locked()
            _log.info(f"Pause released: {reason} (acquisition may resume)")
            self._cond.notify_all()

    def clear(self) -> None:
        """Release every holder (acquisition teardown)."""
        with self._cond:
            if not self._holders:
                return
            self._holders.clear()
            self._end_pause_locked()
            self._cond.notify_all()

    def is_paused(self) -> bool:
        with self._cond:
            return bool(self._holders)

    def state(self) -> PauseState:
        with self._cond:
            return PauseState(
                paused=bool(self._holders),
                reasons=tuple(sorted(self._holders)),
                since=self._since_wall,
                paused_total_s=self._paused_total_s,
            )

    def wait_while_paused(
        self,
        cancel_fn: Callable[[], bool],
        tick: Optional[Callable[[], None]] = None,
        poll_s: float = 0.5,
    ) -> float:
        """Block the calling thread while the gate is paused.

        Returns the number of seconds spent blocked (0.0 if the gate was not paused). Returns as soon
        as all holders are released or ``cancel_fn()`` becomes true (checked at least every ``poll_s``).
        ``tick`` runs once per poll, outside the gate's lock, so it may hold/release the gate itself.
        Exceptions from ``tick`` propagate to the caller.
        """
        with self._cond:
            if not self._holders:
                return 0.0
        start = time.monotonic()
        while True:
            if cancel_fn():
                _log.info("Pause wait cancelled (abort requested)")
                break
            if tick is not None:
                tick()
            with self._cond:
                if not self._holders:
                    break
                self._cond.wait(timeout=poll_s)
        return time.monotonic() - start

    def _end_pause_locked(self) -> None:
        if self._since_mono is not None:
            self._paused_total_s += time.monotonic() - self._since_mono
        self._since_mono = None
        self._since_wall = None
