"""Чанки QR (magic 1984) — как qrCodeUtils в amnezia-client."""

from __future__ import annotations

import base64
import math
import struct
from typing import Literal

QR_MAGIC = 0x07C0
CHUNK_SIZE = 850

QrMethod = Literal["chunked", "raw"]


def chunk_b64(data: bytes, index: int, total: int) -> str:
    buf = bytearray()
    buf += QR_MAGIC.to_bytes(2, "big")
    buf.append(total & 0xFF)
    buf.append(index & 0xFF)
    buf += len(data).to_bytes(4, "big")
    buf += data
    return base64.urlsafe_b64encode(buf).decode("utf-8").rstrip("=")


def split_chunks(data: bytes, chunk_size: int = CHUNK_SIZE) -> list[bytes]:
    n = math.ceil(len(data) / chunk_size) if data else 1
    if not data:
        return [b""]
    return [data[i * chunk_size : (i + 1) * chunk_size] for i in range(n)]


def build_chunk_strings(data: bytes, chunk_size: int = CHUNK_SIZE) -> list[str]:
    parts = split_chunks(data, chunk_size)
    total = len(parts)
    return [chunk_b64(part, idx, total) for idx, part in enumerate(parts)]


def _b64url_decode(text: str) -> bytes:
    pad = "=" * ((4 - len(text) % 4) % 4)
    return base64.urlsafe_b64decode(text + pad)


def is_chunked_qr_text(text: str) -> bool:
    try:
        raw = _b64url_decode(text)
    except Exception:
        return False
    if len(raw) < 2:
        return False
    return struct.unpack(">h", raw[0:2])[0] == QR_MAGIC


def extract_chunk(text: str) -> tuple[int, int, bytes]:
    raw = _b64url_decode(text)
    if len(raw) < 8:
        raise RuntimeError("extract_chunk: слишком короткий payload")
    magic = struct.unpack(">h", raw[0:2])[0]
    if magic != QR_MAGIC:
        raise RuntimeError(f"extract_chunk: magic {magic}, ожидался {QR_MAGIC}")
    total, index = raw[2], raw[3]
    blen = struct.unpack(">I", raw[4:8])[0]
    return total, index, raw[8 : 8 + blen]
