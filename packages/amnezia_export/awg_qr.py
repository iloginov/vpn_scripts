"""AmneziaWG: .conf → PNG QR (chunked или raw)."""

from __future__ import annotations

from pathlib import Path

from amnezia_export.awg_conf import normalize_client_conf
from amnezia_export.qr_chunk import QrMethod, build_chunk_strings
from amnezia_export.qr_io import save_qr_png


def awg_qr_path(base: Path, index: int, total: int) -> Path:
    stem = base.stem
    if total <= 1:
        return base.with_name(f"{stem}_awg_qr.png")
    if index == 0:
        return base.with_name(f"{stem}_awg_qr.png")
    return base.with_name(f"{stem}_awg_qr{index + 1}.png")


def write_awg_qr_pngs(
    client_conf: Path,
    *,
    method: QrMethod = "raw",
    output_png: Path | None = None,
) -> list[Path]:
    normalized = normalize_client_conf(client_conf.read_text(encoding="utf-8"))
    if method == "raw":
        qr_texts = [normalized]
    else:
        qr_texts = build_chunk_strings(normalized.encode("utf-8"))

    if output_png is not None:
        if len(qr_texts) != 1:
            raise ValueError("--output допустим только при одном QR-коде")
        save_qr_png(qr_texts[0], output_png)
        return [output_png]

    paths: list[Path] = []
    for idx, text in enumerate(qr_texts):
        out = awg_qr_path(client_conf, idx, len(qr_texts))
        save_qr_png(text, out)
        paths.append(out)
    return paths
