"""Controlled preview frames and private wire helpers; no browser at import.

``REAL_JPEG`` is a genuine 64x48 browser-encoded JPEG used to exercise the
receiver's SOF/EOI checks without Playwright. ``jpeg`` builds structurally valid
SOF samples for dimension/boundary cases; it is not meant to decode as an image.
"""

from __future__ import annotations

import base64
import json
import struct
from datetime import datetime, timezone

# Generated once with headless Chromium canvas.toBlob('image/jpeg', 0.6).
REAL_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/4gHYSUNDX1BST0ZJTEUAAQEAAAHIAAAAAAQwAABtbnRyUkdCIFhZWiAH"
    "4AABAAEAAAAAAABhY3NwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAA9tYAAQAAAADTLQAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAlkZXNjAAAA8AAAACRyWFlaAAAB"
    "FAAAABRnWFlaAAABKAAAABRiWFlaAAABPAAAABR3dHB0AAABUAAAABRyVFJDAAABZAAAAChnVFJDAAABZAAA"
    "AChiVFJDAAABZAAAAChjcHJ0AAABjAAAADxtbHVjAAAAAAAAAAEAAAAMZW5VUwAAAAgAAAAcAHMAUgBHAEJY"
    "WVogAAAAAAAAb6IAADj1AAADkFhZWiAAAAAAAABimQAAt4UAABjaWFlaIAAAAAAAACSgAAAPhAAAts9YWVog"
    "AAAAAAAA9tYAAQAAAADTLXBhcmEAAAAAAAQAAAACZmYAAPKnAAANWQAAE9AAAApbAAAAAAAAAABtbHVjAAAA"
    "AAAAAAEAAAAMZW5VUwAAACAAAAAcAEcAbwBvAGcAbABlACAASQBuAGMALgAgADIAMAAxADb/2wBDAA0JCgsK"
    "CA0LCgsODg0PEyAVExISEyccHhcgLikxMC4pLSwzOko+MzZGNywtQFdBRkxOUlNSMj5aYVpQYEpRUk//2wBD"
    "AQ4ODhMREyYVFSZPNS01T09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09P"
    "T0//wAARCAAwAEADASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAH/xAAUEAEAAAAAAAAAAAAAAAAA"
    "AAAA/8QAFgEBAQEAAAAAAAAAAAAAAAAAAAQF/8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAwDAQACEQMRAD8A"
    "gCdgAAAAAAAAAAAAAAAAAAAAAAP/2Q=="
)
REAL_WIDTH, REAL_HEIGHT = 64, 48

SOI, EOI = b"\xff\xd8", b"\xff\xd9"


def jpeg(width: int, height: int, *, comment: bytes | None = None, sof: int = 0xC0) -> bytes:
    """Structurally valid JPEG markers with a declared SOF size (not decodable)."""
    parts = [
        SOI,
        b"\xff\xe0" + struct.pack("!H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00",
    ]
    if comment is not None:
        parts.append(b"\xff\xfe" + struct.pack("!H", len(comment) + 2) + comment)
    parts.append(
        bytes([0xFF, sof]) + struct.pack("!H", 17) + bytes([8])
        + struct.pack("!HH", height, width) + bytes([3]) + b"\x01\x11\x00" * 3
    )
    parts.append(b"\xff\xda" + struct.pack("!H", 12) + b"\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00")
    parts.append(EOI)
    return b"".join(parts)


def packet(header: dict, data: bytes = b"") -> bytes:
    encoded = json.dumps(header, separators=(",", ":")).encode()
    return struct.pack("!I", len(encoded)) + encoded + data


def raw_packet(header: bytes, data: bytes = b"") -> bytes:
    """Pre-encoded JSON, for duplicate-key and malformed-header checks."""
    return struct.pack("!I", len(header)) + header + data


def frame_header(
    run_id: str,
    token: str,
    *,
    sequence: int = 1,
    width: int = REAL_WIDTH,
    height: int = REAL_HEIGHT,
    captured_at: datetime | None = None,
    size: int | None = None,
    **extra,
) -> dict:
    header = {
        "type": "frame", "run_id": run_id, "token": token, "sequence": sequence,
        "captured_at": (captured_at or datetime.now(timezone.utc)).isoformat(),
        "width": width, "height": height, "format": "jpeg",
        "size": len(REAL_JPEG) if size is None else size,
    }
    header.update(extra)
    return header


def status_header(run_id: str, token: str, state: str, diagnostic=None) -> dict:
    return {"type": "status", "run_id": run_id, "token": token,
            "state": state, "diagnostic": diagnostic}


def heartbeat_header(run_id: str, token: str) -> dict:
    return {"type": "heartbeat", "run_id": run_id, "token": token}
