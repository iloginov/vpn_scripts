"""Поиск server_*.conf рядом с клиентским конфигом."""

from __future__ import annotations

import configparser
import ipaddress
import re
from pathlib import Path

_PEER_SECTION_RE = re.compile(r"^\[Peer\]\s*$", re.MULTILINE | re.IGNORECASE)
_INTERFACE_HOOK_RE = re.compile(
    r"^(PreUp|PostUp|PreDown|PostDown)\s*=\s*(.*)$",
    re.IGNORECASE,
)
_INTERFACE_HOOK_KEYS = frozenset({"preup", "postup", "predown", "postdown"})


def deduplicate_interface_hooks(text: str) -> str:
    """Повторяющиеся PreUp/PostUp/… → PostUp_2, PostUp_3 — иначе DuplicateOptionError."""
    lines = text.replace("\r", "").splitlines()
    out: list[str] = []
    in_interface = False
    hook_counts: dict[str, int] = {}

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            sect = stripped[1:-1].strip().lower()
            in_interface = sect == "interface"
            if not in_interface:
                hook_counts.clear()
            out.append(line)
            continue

        if in_interface:
            m = _INTERFACE_HOOK_RE.match(stripped)
            if m:
                key = m.group(1)
                key_lower = key.lower()
                if key_lower not in _INTERFACE_HOOK_KEYS:
                    out.append(line)
                    continue
                hook_counts[key_lower] = hook_counts.get(key_lower, 0) + 1
                n = hook_counts[key_lower]
                value = m.group(2)
                if n == 1:
                    out.append(line)
                else:
                    out.append(f"{key}_{n} = {value}")
                continue

        out.append(line)

    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(out) + suffix


def prepare_wg_conf_text(text: str) -> str:
    """wg-quick текст, пригодный для ConfigParser (хуки + несколько [Peer])."""
    return enumerate_peer_sections(deduplicate_interface_hooks(text))


def enumerate_peer_sections(text: str) -> str:
    """Пронумеровать повторяющиеся [Peer] → [Peer:1], [Peer:2], …

    Стандартный ConfigParser падает с DuplicateSectionError, если в wg-quick
    несколько секций с одним именем.
    """
    n = 0

    def _repl(_m: re.Match[str]) -> str:
        nonlocal n
        n += 1
        return f"[Peer:{n}]"

    return _PEER_SECTION_RE.sub(_repl, text.replace("\r", ""))


def read_wg_conf(path: Path) -> configparser.ConfigParser:
    """wg-quick .conf → ConfigParser (с нумерацией дубликатов [Peer])."""
    cfg = configparser.ConfigParser()
    cfg.optionxform = lambda optionstr: optionstr
    cfg.read_string(prepare_wg_conf_text(path.read_text(encoding="utf-8")))
    return cfg


def resolve_server_conf(client_conf: Path, explicit: Path | None = None) -> Path:
    if explicit is not None:
        if not explicit.is_file():
            raise FileNotFoundError(f"Нет файла: {explicit}")
        return explicit.resolve()

    directory = client_conf.resolve().parent
    matches = sorted(directory.glob("server_*.conf"))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise RuntimeError(
            f"В {directory} нет server_*.conf — укажите --server-conf PATH"
        )
    names = ", ".join(m.name for m in matches)
    raise RuntimeError(
        f"В {directory} несколько server_*.conf ({names}) — укажите --server-conf PATH"
    )


def mtu_from_conf(cfg: configparser.ConfigParser) -> str | None:
    if "Interface" not in cfg:
        return None
    value = cfg["Interface"].get("MTU", "").strip()
    return value or None


def mtu_from_server_conf(server_conf: Path) -> str | None:
    return mtu_from_conf(read_wg_conf(server_conf))


def subnet_address_from_server(server_conf: Path) -> str | None:
    cfg = read_wg_conf(server_conf)
    if "Interface" not in cfg:
        return None
    addr = cfg["Interface"].get("Address", "").strip()
    if not addr or "/" not in addr:
        return None
    try:
        return str(ipaddress.ip_interface(addr).network.network_address)
    except ValueError:
        return None
