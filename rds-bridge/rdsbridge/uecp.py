"""Minimal UECP (EBU SPB 490) frame builder for RadioText and PS.

Frame:  STA(FE) | ADD(2) SQC(1) MFL(1) MSG(MFL) CRC(2) | STP(FF)
The part between STA and STP is byte-stuffed (FD->FD 00, FE->FD 01, FF->FD 02).
CRC is CRC-16/CCITT (poly 0x1021, init 0xFFFF) over ADD..MSG, inverted.
ADD = site address (10 bits) << 6 | encoder address (6 bits); 0/0 = all.
"""

from __future__ import annotations

MEC_PS = 0x02
MEC_RT = 0x0A
UECP_FIELDS = ("rt", "ps")


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc ^ 0xFFFF


def stuff(data: bytes) -> bytes:
    out = bytearray()
    for b in data:
        if b >= 0xFD:
            out += bytes((0xFD, b - 0xFD))
        else:
            out.append(b)
    return bytes(out)


def frame(message: bytes, site: int = 0, encoder: int = 0, sequence: int = 0) -> bytes:
    if len(message) > 255:
        raise ValueError("UECP message too long")
    addr = ((site & 0x3FF) << 6) | (encoder & 0x3F)
    body = bytes((addr >> 8, addr & 0xFF, sequence & 0xFF, len(message))) + message
    crc = crc16(body)
    return b"\xFE" + stuff(body + bytes((crc >> 8, crc & 0xFF))) + b"\xFF"


def _rds_bytes(text: str) -> bytes:
    # Printable ASCII is identical in the RDS (EBU Latin) character table.
    return text.encode("ascii", "replace")


def ps_message(text: str, dsn: int = 0, psn: int = 0) -> bytes:
    return bytes((MEC_PS, dsn, psn)) + _rds_bytes(text)[:8].ljust(8, b" ")


def rt_message(text: str, dsn: int = 0, psn: int = 0, transmissions: int = 0,
               ab_flag: int = 0, buffer_config: int = 0) -> bytes:
    data = _rds_bytes(text)[:64]
    if not data:
        return bytes((MEC_RT, dsn, psn, 0))  # MEL 0 clears the RT buffer
    cfg = ((buffer_config & 0x03) << 5) | ((transmissions & 0x0F) << 1) | (ab_flag & 0x01)
    return bytes((MEC_RT, dsn, psn, len(data) + 1, cfg)) + data


def hexdump(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)
