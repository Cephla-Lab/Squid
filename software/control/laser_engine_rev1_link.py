"""USB-serial link to the Cephla laser engine, carrier rev 1 (Teensy 4.1 text protocol, laser-engine-firmware).

One text line in, exactly one line back: "OK [value]", "ERR <reason>", a bare value, or one JSON object.
"""

import json
import threading
import time
from typing import Optional

import serial
from serial.tools import list_ports

import squid.logging


class EngineLinkError(RuntimeError):
    """The link is broken: no reply, garbled reply, or a serial error."""


class EngineCommandError(EngineLinkError):
    """The engine understood the command and refused it ("ERR <reason>")."""

    def __init__(self, command: str, reason: str):
        super().__init__(f"{command!r} refused: {reason}")
        self.command = command
        self.reason = reason


class EngineLink:
    BAUDRATE = 115200  # USB CDC: the Teensy ignores it, pyserial needs a value
    RESYNC_WAIT_S = 0.2

    def __init__(self, port):
        self._port = port
        self._lock = threading.Lock()
        self._log = squid.logging.get_logger(self.__class__.__name__)

    @classmethod
    def open(cls, sn: Optional[str] = None, device: Optional[str] = None, timeout_s: float = 3.0) -> "EngineLink":
        path = device
        if sn is not None:
            sn = str(sn)  # the .ini reader turns an all-digit serial number into an int
            path = next((p.device for p in list_ports.comports() if p.serial_number == sn), None)
            if path is None:
                raise EngineLinkError(f"laser engine: no USB device with serial number {sn!r}")
        if path is None:
            raise EngineLinkError("laser engine: set LASER_ENGINE_REV1_SN (or give a device path)")
        return cls(serial.Serial(path, baudrate=cls.BAUDRATE, timeout=timeout_s, write_timeout=timeout_s))

    def resync(self) -> None:
        """Terminate any half-sent line a previous session left in the engine, then drop everything queued."""
        with self._lock:
            try:
                self._port.write(b"\n")
                time.sleep(self.RESYNC_WAIT_S)
                self._port.reset_input_buffer()
            except (serial.SerialException, OSError) as e:
                raise EngineLinkError(f"serial error during resync: {e}") from e

    def query(self, line: str) -> str:
        with self._lock:
            try:
                self._port.write((line.strip() + "\n").encode())
                raw = self._port.readline()
            except (serial.SerialException, OSError) as e:
                raise EngineLinkError(f"serial error on {line!r}: {e}") from e
            if not raw:
                try:
                    self._port.reset_input_buffer()  # a late reply must not become the answer to the next query
                except (serial.SerialException, OSError):
                    pass
                raise EngineLinkError(f"no reply to {line!r}")
        return raw.decode(errors="replace").strip()

    def command(self, line: str) -> str:
        reply = self.query(line)
        if not reply.startswith(("OK", "ERR")):  # out of step (a late reply to an earlier query): resync, ask once more
            self.resync()
            reply = self.query(line)  # every engine command is idempotent (ARM, EN, SET, TEC:OUT, FAULT:RESET ...)
        if reply == "OK":
            return ""
        if reply.startswith("OK "):
            return reply[3:]
        if reply.startswith("ERR"):
            raise EngineCommandError(line, reply[3:].strip())
        raise EngineLinkError(f"unexpected reply to {line!r}: {reply!r}")

    def status(self) -> dict:
        for attempt in (1, 2):
            reply = self.query("STAT?")
            try:
                return json.loads(reply)
            except ValueError as e:
                if attempt == 2:
                    raise EngineLinkError(f"STAT? reply is not JSON: {reply[:80]!r}") from e
                self.resync()  # out of step: resync and ask once more

    def close(self) -> None:
        with self._lock:
            try:
                self._port.close()
            except Exception:
                self._log.exception("closing the laser engine port")
