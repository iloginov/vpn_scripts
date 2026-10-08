"""zbarimg и сохранение PNG QR."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import qrcode
from qrcode.constants import ERROR_CORRECT_L

from amnezia_export.qr_chunk import is_chunked_qr_text


def _strip_zbar_prefix(line: str) -> str:
    if line.startswith("QR-Code:"):
        return line[len("QR-Code:") :].lstrip()
    return line


def decode_qr_png(png: Path) -> str:
    exe = shutil.which("zbarimg")
    if not exe:
        raise RuntimeError("zbarimg не найден в PATH (brew install zbar)")
    proc = subprocess.run(
        [exe, "-q", "--raw", str(png)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"zbarimg exit {proc.returncode}")
    lines = [_strip_zbar_prefix(ln.strip()) for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines:
        raise RuntimeError("zbarimg не вернул данных")
    if len(lines) == 1:
        return lines[0]
    for line in sorted(lines, key=len, reverse=True):
        if is_chunked_qr_text(line):
            return line
    return "\n".join(lines)


def save_qr_png(data: str, out_path: Path) -> None:
    qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_L)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    with out_path.open("wb") as stream:
        img.save(stream)
