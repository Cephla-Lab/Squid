"""Who drives the stage: one owner at a time.

An acquisition, a move to or from the loading position (and the stay there), a stage move from the control server and
a home each start from their own thread: the GUI's, a control-server client's, a worker's. Each one takes the
Microscope's StageMotionReservation before it moves anything and keeps it until the stage is done with it, so a second
one is refused at once, with what holds the stage, instead of driving the stage under the first. The take is one locked
step, so two owners starting at the same moment cannot both get it.

No Qt here: headless code takes it the same way.
"""

import threading
from typing import Callable, Optional, Union

# What holds the stage, as the refusal reads: "an acquisition is running". A callable is asked at each refusal, for an
# owner whose state changes while it holds the stage (a move to the loading position, the stay there, the move back).
Reason = Union[str, Callable[[], str]]


class StageMotionRefused(RuntimeError):
    """Something else holds the stage. reason says what; str(e) is "Refused: <reason>"."""

    def __init__(self, reason: str):
        super().__init__(f"Refused: {reason}")
        self.reason = reason


class StageMotionHold:
    """One owner's hold on the stage (StageMotionReservation.take). release() hands the stage back; a second release,
    or one after the stage passed to the next owner, does nothing."""

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

    def release(self) -> None:
        self._reservation._release(self)


class StageMotionReservation:
    def __init__(self):
        self._lock = threading.Lock()
        self._hold: Optional[StageMotionHold] = None

    @property
    def holder(self) -> Optional[str]:
        """What holds the stage now, or None. Only a report: a later take may still be refused."""
        hold = self._hold
        return hold.reason if hold is not None else None

    def take(self, reason: Reason) -> StageMotionHold:
        """Hold the stage until the hold is released. Raises StageMotionRefused, with nothing taken, while another
        owner holds it."""
        with self._lock:
            if self._hold is not None:
                raise StageMotionRefused(self._hold.reason)
            self._hold = StageMotionHold(self, reason)
            return self._hold

    def _release(self, hold: StageMotionHold) -> None:
        with self._lock:
            hold._released = True
            if self._hold is hold:
                self._hold = None
