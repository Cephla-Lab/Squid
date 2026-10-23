"""The FTDI latency timer of a Windows COM port, and a start-up warning when it is high.

The FTDI driver holds a device's short replies for up to its latency timer (16 ms by default) before passing them
to the PC. For devices that talk in small request/response exchanges, that timer, not the baud rate, sets the cost
of each command. On an AION with the PI C-414 (every query is followed by an ERR? round trip) it made a 1 um Z step
take ~250 ms; at 1 ms it takes ~45 ms. Linux sets it with a udev rule (drivers and libraries/pi/udev/
99-pi-ftdi-latency.rules); Windows keeps it with the device, set in Device Manager, so a new PC or a reinstalled
driver goes back to 16 ms.
"""

import sys
from typing import Optional

RECOMMENDED_LATENCY_TIMER_MS = 1
MAX_LATENCY_TIMER_MS = 2  # warn above this

_FTDIBUS_KEY = r"SYSTEM\CurrentControlSet\Enum\FTDIBUS"


def windows_latency_timer_ms(port: str) -> Optional[int]:
    """The FTDI latency timer of COM port `port` (e.g. "COM4"), in ms.

    None when not on Windows, when `port` is not an FTDI port, or when the registry can't be read.
    Read-only: needs no admin rights.
    """
    if sys.platform != "win32" or not port:
        return None
    import winreg

    try:
        ftdibus = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _FTDIBUS_KEY)
    except OSError:
        return None
    with ftdibus:
        for device in _subkeys(winreg, ftdibus):
            try:
                device_key = winreg.OpenKey(ftdibus, device)
            except OSError:
                continue
            with device_key:
                for instance in _subkeys(winreg, device_key):
                    try:
                        with winreg.OpenKey(device_key, instance + r"\Device Parameters") as params:
                            if str(winreg.QueryValueEx(params, "PortName")[0]).upper() != port.upper():
                                continue
                            return int(winreg.QueryValueEx(params, "LatencyTimer")[0])
                    except (OSError, ValueError):
                        continue
    return None


def warn_if_latency_timer_high(port: str, device_name: str, log) -> Optional[int]:
    """Log a warning, with how to fix it, when `port`'s FTDI latency timer is above MAX_LATENCY_TIMER_MS.

    Returns the latency timer (None when it couldn't be read, see windows_latency_timer_ms). Never raises.
    """
    try:
        latency_ms = windows_latency_timer_ms(port)
    except Exception as e:  # a start-up check must not stop a device from connecting
        log.debug(f"Could not read the FTDI latency timer of {port}: {e}")
        return None
    if latency_ms is not None and latency_ms > MAX_LATENCY_TIMER_MS:
        log.warning(
            f"{device_name} on {port}: the FTDI latency timer is {latency_ms} ms, which slows every command to it "
            f"(recommended: {RECOMMENDED_LATENCY_TIMER_MS} ms). To fix, close Squid, then in Device Manager open "
            f"{port} > Properties > Port Settings > Advanced, set Latency Timer to {RECOMMENDED_LATENCY_TIMER_MS}, "
            f"and unplug and replug the device (or power-cycle it) if it does not reappear."
        )
    return latency_ms


def _subkeys(winreg, key):
    i = 0
    while True:
        try:
            yield winreg.EnumKey(key, i)
        except OSError:
            return
        i += 1
