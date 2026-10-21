"""Hardware trigger through a Confocal.nl unit (AION / NL5), with a timeout.

With NL5_USE_DOUT the unit, not the microcontroller, triggers the camera: every image is one
RCM startAcquisition(), which waits for the camera's ready signal, scans and sends the camera its
level trigger. If the camera never reports ready, startAcquisition() never returns. Called from the
camera's hardware trigger function, that held the camera's trigger lock forever, so live view and
acquisitions stalled ("Not ready for trigger") until the trigger mode was toggled.

RcmScanTrigger runs the scan on a worker thread and, if it has not finished in time, aborts it with
stopContinuousAcquisition() - which the RCM API documents as interrupting a regular acquisition too.

On AION a Tucsen left idle in hardware trigger mode for 50-55 s or more stops signalling ready;
re-applying its trigger mode re-arms it. After a stalled scan has been aborted and has exited,
trigger() calls rearm() and retries the scan once, so the image is still taken. Both happen on the
calling thread, which holds the camera's trigger lock (send_trigger), so nothing else can change the
camera's settings in between. A stall is therefore a bounded pause: at most two scan timeouts, two
abort waits and one re-arm (on AION about 2-4 s was observed).

No retry, and no re-arm, when:
- the aborted scan is still inside startAcquisition(): re-arming would give it the ready signal it
  waits for, and it could deliver a late frame after the acquisition had moved on. Triggers are
  dropped (logged once) until it returns; the next trigger then re-arms first.
- the camera produced a frame during the aborted scan (it fired just before the abort): that frame
  is the image, and a retry would add a second one that could be filed under the next channel.
"""

import threading
from typing import Callable, Optional

from squid.abc import CameraError
import squid.logging

log = squid.logging.get_logger(__name__)

ABORT_JOIN_TIMEOUT_S = 1.0


class ConfocalScanFailedError(CameraError):
    """The confocal unit could not take this image: its scan stalled and re-arming did not help."""


class RcmScanTrigger:
    def __init__(
        self,
        timeout_s: float,
        rearm: Optional[Callable[[], None]] = None,
        frame_id: Optional[Callable[[], int]] = None,
    ):
        self._timeout_s = timeout_s
        self._rearm = rearm
        self._frame_id = frame_id
        self._scan: Optional[threading.Thread] = None
        self._error: Optional[BaseException] = None
        self._stuck = False

    def trigger(self, unit, frame_time_s: float = 0.0) -> bool:
        """Scan one image on `unit`; False if it was not taken (stalled again after the retry, or a scan is stuck)."""
        if self._scan is not None and self._scan.is_alive():
            # Never call into the RCM DLL again while an earlier startAcquisition() is stuck in it.
            if not self._stuck:
                log.error("An aborted confocal scan is still stuck in startAcquisition(); dropping triggers")
                self._stuck = True
            return False
        if self._stuck:
            # The camera was never re-armed after that stall.
            log.warning("The stuck confocal scan has returned; re-arming the camera")
            self._stuck = False
            self._call_rearm()

        timeout_s = self._timeout_s + 3 * frame_time_s
        frame_id_before = self._frame_id() if self._frame_id else None
        if self._scan_once(unit, timeout_s):
            return True
        if self._scan.is_alive() or self._rearm is None:
            return False
        if frame_id_before is not None and self._frame_id() != frame_id_before:
            log.warning("The aborted confocal scan had already exposed the camera; not retrying")
            return True
        log.warning("Re-arming the camera and retrying the scan once")
        self._call_rearm()
        return self._scan_once(unit, timeout_s)

    def _scan_once(self, unit, timeout_s: float) -> bool:
        self._error = None
        self._scan = threading.Thread(target=self._run, args=(unit,), name="RCM startAcquisition", daemon=True)
        self._scan.start()
        self._scan.join(timeout_s)
        if self._scan.is_alive():
            log.warning(
                f"Confocal scan did not finish within {timeout_s:.1f} s (the camera never reported ready?); aborting it"
            )
            unit.stop_continuous_acquisition()
            self._scan.join(ABORT_JOIN_TIMEOUT_S)
            return False
        if self._error is not None:
            raise self._error
        return True

    def _call_rearm(self):
        if self._rearm is not None:
            self._rearm()

    def _run(self, unit):
        try:
            unit.start_acquisition()
        except BaseException as e:
            self._error = e
