"""AmneziaVPN: .vpn → PNG QR (1–2 чанка по 850 байт сжатых данных)."""

from __future__ import annotations

import base64
from pathlib import Path

from amnezia_export.qr_chunk import build_chunk_strings
from amnezia_export.qr_io import save_qr_png


def _read_compressed(vpn_path: Path) -> bytes:
    text = vpn_path.read_text(encoding="utf-8").strip()
    if not text.startswith("vpn://"):
        raise ValueError(f"{vpn_path.name}: нет префикса vpn://")
    enc = text[6:]
    pad = "=" * ((4 - len(enc) % 4) % 4)
    return base64.urlsafe_b64decode(enc + pad)


def _vpn_qr_path(vpn_path: Path, index: int, total: int) -> Path:
    stem = vpn_path.stem
    if total <= 1:
        return vpn_path.with_name(f"{stem}_qr.png")
    if index == 0:
        return vpn_path.with_name(f"{stem}_qr.png")
    return vpn_path.with_name(f"{stem}_qr{index + 1}.png")


def write_vpn_qr_pngs(vpn_path: Path) -> list[Path]:
    chunks = build_chunk_strings(_read_compressed(vpn_path))
    out_paths: list[Path] = []
    for idx, chunk in enumerate(chunks):
        out = _vpn_qr_path(vpn_path, idx, len(chunks))
        save_qr_png(chunk, out)
        out_paths.append(out)
    return out_paths
