"""Crash guard for the Vortran VersaLase: turns laser emission off when Squid's process ends without
shutting the lasers down - a force quit, a crash, or its console window being closed.

VersaLase.initialize() starts it as a separate, detached process:

    python -m control.versalase_guard <squid pid>

It waits for that process to end, then sends LE=0 to every Vortran head. On a clean shutdown,
VersaLase.shut_down() turns the heads off itself and then stops the guard, so the guard never acts
after a normal exit (or on a Squid that was restarted right away).

It cannot act if it is killed too ("End process tree" on Squid) or the PC loses power. On AION the
light is then still gated by the NL5 TTL lines, which stop when the microcontroller watchdog trips.
"""

import os
import subprocess
import sys
import time
from typing import List, Optional

import hid
import psutil

import squid.logging
from control.vortran_hid import VORTRAN_PID, VORTRAN_VID, StradusHead

log = squid.logging.get_logger("control.versalase_guard")  # not __name__: it is "__main__" under -m

SOFTWARE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE_NAME = "versalase_guard.log"
ATTEMPTS = 3
RETRY_DELAY_S = 0.5


def start(squid_pid: int) -> subprocess.Popen:
    """Start a guard for process `squid_pid`, detached so that it outlives that process."""
    command = [sys.executable, "-m", "control.versalase_guard", str(squid_pid)]
    kwargs = dict(cwd=SOFTWARE_DIR, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if sys.platform != "win32":
        return subprocess.Popen(command, start_new_session=True, **kwargs)

    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        # Out of the console's job too, where a terminal kills its whole job when its window closes.
        return subprocess.Popen(command, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kwargs)
    except OSError:
        # The job does not allow breaking away.
        return subprocess.Popen(command, creationflags=flags, **kwargs)


def turn_off_all_heads(vid: int = VORTRAN_VID, pid: int = VORTRAN_PID) -> List[str]:
    """Send LE=0 to every Vortran head; return the ones that confirmed emission is off."""
    turned_off = []
    for info in hid.enumerate(vid, pid):
        path = info["path"]
        for attempt in range(1, ATTEMPTS + 1):
            device = hid.device()
            try:
                device.open_path(path)
                device.set_nonblocking(0)
                head = StradusHead(device, path)
                if not head.set_flag("LE", False):
                    turned_off.append(head.transact("?LW") + " nm")
                    break
                log.warning(f"Vortran head {path} still reports emission on (attempt {attempt})")
            except Exception as e:
                log.warning(f"Could not turn off Vortran head {path} (attempt {attempt}): {e}")
            finally:
                device.close()
            time.sleep(RETRY_DELAY_S)
        else:
            log.error(f"Gave up turning off Vortran head {path}")
    return turned_off


def main(argv: Optional[List[str]] = None) -> int:
    squid_pid = int((sys.argv[1:] if argv is None else argv)[0])
    squid.logging.add_file_logging(os.path.join(squid.logging.get_default_log_directory(), LOG_FILE_NAME))
    log.info(f"Guarding the VersaLase for Squid process {squid_pid}")
    try:
        psutil.Process(squid_pid).wait()
    except psutil.NoSuchProcess:
        pass
    log.warning(f"Squid process {squid_pid} ended without shutting the VersaLase down; turning emission off")
    turned_off = turn_off_all_heads()
    log.info(f"Emission off on {len(turned_off)} head(s): {', '.join(turned_off) or 'none'}")
    return 0 if turned_off else 1


if __name__ == "__main__":
    sys.exit(main())
