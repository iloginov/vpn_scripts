"""AmneziaWG QR PNG → <name>.awg_qr.decoded (chunked/raw, автоопределение)."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from amnezia_export.decode_output import decoded_output_path, strip_awg_qr_stem
from amnezia_export.qr_chunk import extract_chunk, is_chunked_qr_text
from amnezia_export.qr_io import decode_qr_png


def _decode_raw_text(text: str) -> bytes:
    return text.encode("utf-8")


def decode_awg_qr_pngs(
    images: list[Path],
    *,
    output: Path | None = None,
) -> list[Path]:
    chunked_groups: dict[tuple[Path, str], list[Path]] = defaultdict(list)
    raw_by_key: dict[tuple[Path, str], tuple[Path, bytes]] = {}

    for png in images:
        text = decode_qr_png(png)
        base = strip_awg_qr_stem(png.stem)
        key = (png.parent, base)
        if is_chunked_qr_text(text):
            chunked_groups[key].append(png)
        else:
            if key in raw_by_key:
                raise RuntimeError(
                    f"несколько raw QR для одной группы {base!r} в {png.parent}"
                )
            raw_by_key[key] = (png, _decode_raw_text(text))

    group_count = len(chunked_groups) + len(raw_by_key)
    if output is not None and group_count != 1:
        raise ValueError("--output допустим только для одной группы QR")

    written: list[Path] = []

    for (_directory, _base), pngs in sorted(chunked_groups.items()):
        parts: dict[int, bytes] = {}
        total = 0
        for png in sorted(pngs, key=lambda p: p.name):
            text = decode_qr_png(png)
            total, index, data = extract_chunk(text)
            parts[index] = data

        if len(parts) != total:
            raise RuntimeError(
                f"склейка AWG: чанков {len(parts)}, нужно {total}, индексы {sorted(parts)}"
            )
        out = decoded_output_path(
            pngs,
            strip_stem=strip_awg_qr_stem,
            name_suffix=".awg_qr.decoded",
            output=output,
        )
        out.write_bytes(b"".join(parts[i] for i in range(total)))
        written.append(out)

    for key in sorted(raw_by_key):
        png, payload = raw_by_key[key]
        out = decoded_output_path(
            [png],
            strip_stem=strip_awg_qr_stem,
            name_suffix=".awg_qr.decoded",
            output=output,
        )
        out.write_bytes(payload)
        written.append(out)

    return written
