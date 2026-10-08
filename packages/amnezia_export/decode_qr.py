"""AmneziaVPN QR PNG → <name>.qr.decoded."""

from __future__ import annotations

import zlib
from collections import defaultdict
from pathlib import Path

from amnezia_export.decode_output import decoded_output_path, strip_qr_stem
from amnezia_export.qr_chunk import extract_chunk
from amnezia_export.qr_io import decode_qr_png


def decode_vpn_qr_pngs(
    images: list[Path],
    *,
    output: Path | None = None,
) -> list[Path]:
    groups: dict[tuple[Path, str], list[Path]] = defaultdict(list)
    for png in images:
        base = strip_qr_stem(png.stem)
        groups[(png.parent, base)].append(png)

    if output is not None and len(groups) != 1:
        raise ValueError("--output допустим только для одной группы QR")

    written: list[Path] = []
    for (directory, _base), pngs in sorted(groups.items()):
        parts: dict[int, bytes] = {}
        total = 0
        for png in sorted(pngs, key=lambda p: p.name):
            text = decode_qr_png(png)
            total, index, data = extract_chunk(text)
            parts[index] = data

        if len(parts) != total:
            raise RuntimeError(
                f"склейка: чанков {len(parts)}, нужно {total}, индексы {sorted(parts)}"
            )
        blob = b"".join(parts[i] for i in range(total))
        out = decoded_output_path(
            pngs,
            strip_stem=strip_qr_stem,
            name_suffix=".qr.decoded",
            output=output,
        )
        out.write_bytes(zlib.decompress(blob[4:]))
        written.append(out)
    return written
