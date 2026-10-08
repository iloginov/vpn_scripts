"""MTU в .conf: чтение и опциональная строка для [Interface]."""

from __future__ import annotations

import configparser
from pathlib import Path


def mtu_from_conf_parser(cfg: configparser.ConfigParser) -> int | None:
    if "Interface" not in cfg:
        return None
    raw = cfg["Interface"].get("MTU", "").strip()
    if not raw:
        return None
    return int(raw)


def mtu_from_conf_text(text: str) -> int | None:
    cfg = configparser.ConfigParser()
    cfg.optionxform = str
    cfg.read_string(text)
    return mtu_from_conf_parser(cfg)


def mtu_from_conf_path(path: Path) -> int | None:
    return mtu_from_conf_text(path.read_text(encoding="utf-8"))
