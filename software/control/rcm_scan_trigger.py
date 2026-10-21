"""Hardware trigger through a Confocal.nl unit (AION / NL5), with a timeout.

With NL5_USE_DOUT the unit, not the microcontroller, triggers the camera: every image is one
RCM startAcquisition(), which waits for the camera's ready signal, scans and sends the camera its
level trigger. If the camera never reports ready, startAcquisition() never returns. Called from the
camera's hardware trigger function, that held the camera's trigger lock forever, so live view and
acquisitions stalled ("Not ready for trigger") until the trigger mode was toggled.

RcmScanTrigger runs the scan on a worker thread and, if it has not finished in time, aborts it with
stopContinuousAcquisition() - which the RCM API documents as interrupting a regular acquisition too -
so a camera that misses its ready signal costs one dropped frame instead of a stall.

On AION a Tucsen left idle in hardware trigger mode for a while stops signalling ready (live view
then never comes back); re-applying its trigger mode re-arms it. on_timeout is called for that,
on its own thread, once the stalled trigger call has returned and released the camera.
"""

import threading
from typing import Callable, Optional

import squid.logging

log = squid.logging.get_logger(__name__)

ABORT_JOIN_TIMEOUT_S = 1.0
# How long recovery waits for the aborted scan to leave startAcquisition() before giving up.
RECOVERY_WAIT_S = 10.0


class RcmScanTrigger:
    def __init__(self, timeout_s: float, on_timeout: Optional[Callable[[], None]] = None):
        self._timeout_s = timeout_s
        self._on_timeout = on_timeout
        self._scan: Optional[threading.Thread] = None
        self._recovery: Optional[threading.Thread] = None
        self._error: Optional[BaseException] = None

    def trigger(self, unit, frame_time_s: float = 0.0) -> bool:
        """Scan one image on `unit`; False if the trigger was dropped (timed out, busy or recovering)."""
        if self._recovery is not None and self._recovery.is_alive():
            # A scan started now could take the ready signal the re-arm is about to give the camera.
            log.debug("The camera is being re-armed after a stalled confocal scan; dropping this trigger")
            return False
        if self._scan is not None and self._scan.is_alive():
            # Never call into the RCM DLL again while an earlier startAcquisition() is stuck in it.
            log.warning("The previous confocal scan has still not returned; dropping this trigger")
            return False

        self._error = None
        self._scan = threading.Thread(target=self._run, args=(unit,), name="RCM startAcquisition", daemon=True)
        self._scan.start()
        timeout_s = self._timeout_s + 3 * frame_time_s
        self._scan.join(timeout_s)
        if self._scan.is_alive():
            log.warning(
                f"Confocal scan did not finish within {timeout_s:.1f} s (the camera never reported ready?); aborting it"
            )
            unit.stop_continuous_acquisition()
            self._scan.join(ABORT_JOIN_TIMEOUT_S)
            self._start_recovery(self._scan)
            return False
        if self._error is not None:
            raise self._error
        return True

    def _start_recovery(self, stalled_scan: threading.Thread):
        # One recovery at a time, so a camera that stays dead isn't restarted on every trigger.
        if self._on_timeout is None or (self._recovery is not None and self._recovery.is_alive()):
            return
        self._recovery = threading.Thread(
            target=self._recover, args=(stalled_scan,), name="Confocal scan recovery", daemon=True
        )
        self._recovery.start()

    def _recover(self, stalled_scan: threading.Thread):
        # Re-arming gives the camera its ready signal back. While the aborted scan is still inside
        # startAcquisition(), that could let it go ahead and deliver a late frame after the acquisition
        # has moved on, so only re-arm once it has exited.
        stalled_scan.join(RECOVERY_WAIT_S)
        if stalled_scan.is_alive():
            log.error("The aborted confocal scan never returned; not re-arming the camera")
            return
        self._on_timeout()

    def _run(self, unit):
        try:
            unit.start_acquisition()
        except BaseException as e:
            self._error = e
