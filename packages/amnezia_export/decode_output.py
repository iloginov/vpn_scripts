"""Имена выходных файлов для decode-vpn / decode-qr / decode-awg-qr."""

from __future__ import annotations

import os
import re
from pathlib import Path

QR_STEM_SUFFIX = re.compile(r"_qr\d*$")
AWG_QR_STEM_SUFFIX = re.compile(r"_awg_qr\d*$")


def strip_qr_stem(stem: str) -> str:
    return QR_STEM_SUFFIX.sub("", stem)


def strip_awg_qr_stem(stem: str) -> str:
    return AWG_QR_STEM_SUFFIX.sub("", stem)


def common_stem_prefix(stems: list[str]) -> str:
    if not stems:
        return ""
    if len(stems) == 1:
        return stems[0]
    return os.path.commonprefix(stems)


def decoded_output_path(
    inputs: list[Path],
    *,
    strip_stem,
    name_suffix: str,
    output: Path | None = None,
    empty_basename: str = "output",
) -> Path:
    """name_suffix: ``.qr.decoded``, ``.awg_qr.decoded``, ``.vpn.decoded``."""
    if output is not None:
        return output
    if not inputs:
        raise ValueError("decoded_output_path: нет входных файлов")
    directory = inputs[0].parent
    bases = [strip_stem(p.stem) for p in inputs]
    prefix = common_stem_prefix(bases)
    basename = prefix if prefix else empty_basename
    return directory / f"{basename}{name_suffix}"
