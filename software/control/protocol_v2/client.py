"""Protocol v2 host client.

Client.request() frames a REQUEST, writes it to a Transport, reads the matching
RESPONSE, and returns it. Phase B tests drive this over an in-memory transport;
a live pyserial/simulator loopback lands with Phase C.
"""

from . import frames


class Timeout(Exception):
    """No matching response arrived within the deadline."""


class Transport:
    """Byte transport for framed protocol-v2 traffic."""

    def write(self, data: bytes) -> None:
        raise NotImplementedError

    def read_frame(self, timeout: float) -> bytes:
        """Return one wire frame (COBS bytes + 0x00), or raise Timeout."""
        raise NotImplementedError


class Response:
    def __init__(self, ftype: int, cmd_id: int, cmd_type: int, flags: int, payload: bytes):
        self.type = ftype
        self.cmd_id = cmd_id
        self.cmd_type = cmd_type
        self.flags = flags
        self.payload = payload

    @property
    def status(self):
        return self.payload[0] if len(self.payload) >= 1 else None

    @property
    def error_code(self):
        return self.payload[1] if len(self.payload) >= 2 else None

    def __repr__(self):
        return (
            f"Response(cmd_id={self.cmd_id}, cmd_type=0x{self.cmd_type:02X}, "
            f"status={self.status}, error_code={self.error_code}, len={len(self.payload)})"
        )


class Client:
    def __init__(self, transport: Transport):
        self._transport = transport
        self._next_id = 1

    def _alloc_cmd_id(self) -> int:
        cmd_id = self._next_id
        self._next_id += 1
        if self._next_id > 255:
            self._next_id = 1  # cmd_id 0 reserved as "unassigned"
        return cmd_id

    def request(
        self,
        cmd_type: int,
        payload: bytes = b"",
        retry: bool = False,
        timeout: float = 1.0,
        cmd_id: int = None,
    ) -> Response:
        """Send a REQUEST and return the correlated RESPONSE.

        Raises Timeout if no response with the request's cmd_id arrives before
        ``timeout``; skips any frame whose cmd_id does not match.
        """
        if cmd_id is None:
            cmd_id = self._alloc_cmd_id()
        flags = frames.FLAG_RETRY if retry else 0
        wire = frames.encode_frame(frames.REQUEST, cmd_id, cmd_type, flags, payload)
        self._transport.write(wire)

        while True:
            resp_wire = self._transport.read_frame(timeout)  # raises Timeout
            rtype, rid, rct, rflags, rpayload = frames.decode_frame(resp_wire)
            if rid == cmd_id:
                return Response(rtype, rid, rct, rflags, rpayload)
            # Not ours (a stale/other response); keep waiting.
