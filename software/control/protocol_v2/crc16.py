"""CRC-16/CCITT-FALSE — Python mirror of firmware/controller/src/protocol/crc16.

Polynomial 0x1021, initial value 0xFFFF, no reflection, no final XOR. The table
is computed at import and is byte-identical to the C lookup table.
"""


def _build_table() -> list:
    table = []
    for i in range(256):
        crc = i << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if (crc & 0x8000) else (crc << 1)
            crc &= 0xFFFF
        table.append(crc)
    return table


TABLE = _build_table()


def crc16_ccitt(data: bytes) -> int:
    """CRC-16/CCITT-FALSE over ``data`` (0xFFFF for an empty buffer)."""
    crc = 0xFFFF
    for b in data:
        crc = ((crc << 8) ^ TABLE[((crc >> 8) ^ b) & 0xFF]) & 0xFFFF
    return crc
