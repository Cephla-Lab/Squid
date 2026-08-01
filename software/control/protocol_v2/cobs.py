"""COBS codec — Python mirror of firmware/controller/src/protocol/cobs.

Standard Consistent Overhead Byte Stuffing (Cheshire & Baker). Encoded output
never contains 0x00, so 0x00 can serve as an unambiguous frame delimiter.
"""


def cobs_encode(data: bytes) -> bytes:
    """Encode ``data``; the result contains no 0x00 byte."""
    out = bytearray()
    code_idx = len(out)
    out.append(0)  # placeholder for the running code byte
    code = 1
    for b in data:
        if b == 0:
            out[code_idx] = code
            code_idx = len(out)
            out.append(0)
            code = 1
        else:
            out.append(b)
            code += 1
            if code == 0xFF:  # block full (254 data bytes)
                out[code_idx] = code
                code_idx = len(out)
                out.append(0)
                code = 1
    out[code_idx] = code
    return bytes(out)


def cobs_decode(data: bytes) -> bytes:
    """Decode COBS ``data``; raise ValueError on malformed input."""
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        code = data[i]
        if code == 0:
            raise ValueError("embedded 0x00 in COBS stream")
        i += 1
        for _ in range(1, code):
            if i >= n:
                raise ValueError("truncated COBS block")
            b = data[i]
            i += 1
            if b == 0:
                raise ValueError("embedded 0x00 inside COBS block")
            out.append(b)
        if code != 0xFF and i < n:
            out.append(0)
    return bytes(out)
