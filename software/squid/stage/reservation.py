"""Who drives the stage: one owner at a time.

An acquisition, a move to or from the loading position (and the stay there), a stage move from the control server and
a home each start from their own thread: the GUI's, a control-server client's, a worker's. Each one takes the
Microscope's StageMotionReservation before it moves anything and keeps it until the stage is done with it, so a second
one is refused at once, with what holds the stage, instead of driving the stage under the first. The take is one locked
step, so two owners starting at the same moment cannot both get it.

One observer (on_holder_changed) hears each change of owner: the Microscope locks the control panel through it while
anything holds the stage.

No Qt here: headless code takes it the same way.
"""

import threading
from typing import Callable, Optional, Union

import squid.logging

_log = squid.logging.get_logger(__name__)

# What holds the stage, as the refusal reads: "an acquisition is running". A callable is asked at each refusal, for an
# owner whose state changes while it holds the stage (a move to the loading position, the stay there, the move back).
Reason = Union[str, Callable[[], str]]


class StageMotionRefused(RuntimeError):
    """Something else holds the stage. reason says what; str(e) is "Refused: <reason>"."""

    def __init__(self, reason: str):
        super().__init__(f"Refused: {reason}")
        self.reason = reason


class StageMotionHold:
    """One owner's hold on the stage (StageMotionReservation.take, or take_next). release() hands the stage back (or
    leaves the queue, for a next holder not yet active); a second release, or one after the stage passed to the next
    owner, does nothing."""

    def __init__(self, reservation: "StageMotionReservation", reason: Reason):
        self._reservation = reservation
        self._reason = reason
        self._released = False

    @property
    def reason(self) -> str:
        return self._reason() if callable(self._reason) else self._reason

    @reason.setter
    def reason(self, reason: Reason) -> None:
        self._reason = reason

    @property
    def released(self) -> bool:
        return self._released

    @property
    def active(self) -> bool:
        """True while this hold is the one holding the stage (a next holder from take_next becomes active when the
        hold before it is released)."""
        return self._reservation._hold is self

    def release(self) -> None:
        self._reservation._release(self)


class StageMotionReservation:
    def __init__(self):
        self._lock = threading.Lock()
        self._hold: Optional[StageMotionHold] = None
        self._next: Optional[StageMotionHold] = None  # take_next: gets the stage straight from _hold's release
        # Called with the holder's reason after a take (and after a release that hands the stage to a next holder),
        # and with None after the release that frees the stage; not for a refused take or a stale release. It runs on the thread that took or released, outside the lock, before
        # take() returns: an owner moves nothing until it has run. An exception from it is logged, not raised. It must
        # not take or release the stage itself.
        self.on_holder_changed: Optional[Callable[[Optional[str]], None]] = None
        # Held across each change of holder and its observer call (a take that finds the stage free, the release that
        # frees it): the observer's calls come in the order of the changes, and the next owner's take() returns only
        # after the previous release's call. A take that finds the stage held is refused without waiting for it.
        self._transition_lock = threading.Lock()

    @property
    def holder(self) -> Optional[str]:
        """What holds the stage now, or None. Only a report: a later take may still be refused."""
        hold = self._hold
        return hold.reason if hold is not None else None

    def take(self, reason: Reason) -> StageMotionHold:
        """Hold the stage until the hold is released. Raises StageMotionRefused, with nothing taken, while another
        owner holds it."""
        self._refuse_if_held()
        with self._transition_lock:
            with self._lock:
                self._refuse_if_held()
                hold = self._hold = StageMotionHold(self, reason)
            self._tell_observer()
        return hold

    def take_next(self, reason: Reason) -> StageMotionHold:
        """Hold the stage now if it is free, else as soon as the owner holding it releases: that release hands the stage
        straight over, so it is never free in between (no other owner gets it, the control panel stays locked). The
        hold is active from then on (StageMotionHold.active). One next holder at most: raises StageMotionRefused while
        one is queued already."""
        with self._transition_lock:
            with self._lock:
                if self._hold is not None:
                    if self._next is not None:
                        raise StageMotionRefused(self._hold.reason)
                    self._next = StageMotionHold(self, reason)
                    return self._next
                hold = self._hold = StageMotionHold(self, reason)
            self._tell_observer()
        return hold

    def stop_observing(self) -> None:
        """No observer call from now on: waits for one under way to end. (Microscope.close(), before its final
        unlock of the control panel.)"""
        with self._transition_lock:
            self.on_holder_changed = None

    def _refuse_if_held(self) -> None:
        hold = self._hold
        if hold is not None:
            raise StageMotionRefused(hold.reason)

    def _release(self, hold: StageMotionHold) -> None:
        with self._transition_lock:
            with self._lock:
                hold._released = True
                if self._next is hold:  # a next holder not yet active leaves the queue: the holder is unchanged
                    self._next = None
                    return
                if self._hold is not hold:
                    return
                self._hold, self._next = self._next, None
            self._tell_observer()

    def _tell_observer(self) -> None:
        """Under _transition_lock: tell the observer who holds the stage now."""
        observer = self.on_holder_changed
        if observer is None:
            return
        holder = self.holder
        try:
            observer(holder)
        except Exception:
            _log.exception(f"The stage-motion observer failed (holder: {holder})")
