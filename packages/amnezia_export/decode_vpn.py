"""Файл .vpn → <name>.vpn.decoded."""

from __future__ import annotations

from pathlib import Path

from amnezia_export.decode_output import decoded_output_path
from amnezia_export.vpn_payload import read_vpn_payload


def decode_vpn_file(vpn: Path, *, output: Path | None = None) -> Path:
    plain = read_vpn_payload(vpn)
    out = decoded_output_path(
        [vpn],
        strip_stem=lambda stem: stem,
        name_suffix=".vpn.decoded",
        output=output,
    )
    out.write_bytes(plain)
    return out
