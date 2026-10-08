"""Распаковка vpn:// (qCompress + base64url)."""

from __future__ import annotations

import base64
import zlib
from pathlib import Path


def read_vpn_payload(vpn: Path) -> bytes:
    text = vpn.read_text(encoding="utf-8").strip()
    if not text.startswith("vpn://"):
        raise ValueError(f"{vpn.name}: нет префикса vpn://")
    enc = text[6:]
    pad = "=" * ((4 - len(enc) % 4) % 4)
    blob = base64.urlsafe_b64decode(enc + pad)
    return zlib.decompress(blob[4:])
