"""Выбор wg (wireguard-tools) или awg (amneziawg-tools) для pubkey."""

from __future__ import annotations

import os
import shutil
from functools import lru_cache

_PUBKEY_CANDIDATES = ("awg", "wg")


@lru_cache
def wireguard_pubkey_bin() -> str:
    override = os.environ.get("AWG_PUBKEY_BIN", "").strip()
    if override:
        path = shutil.which(override) if "/" not in override else override
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
        raise RuntimeError(f"AWG_PUBKEY_BIN={override!r} не найден или не исполняемый")

    for name in _PUBKEY_CANDIDATES:
        exe = shutil.which(name)
        if exe:
            return exe

    raise RuntimeError(
        "В PATH нет awg (amneziawg-tools) и wg (wireguard-tools); "
        "нужен один из них для `pubkey`"
    )
