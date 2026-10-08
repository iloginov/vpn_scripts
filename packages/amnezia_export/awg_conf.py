"""Нормализация клиентского .conf под AmneziaWG iOS (wg-quick)."""

from __future__ import annotations

import configparser
import re

# Ключи, которые принимает TunnelConfiguration+WgQuickConfig в amneziawg-apple (iOS).
IOS_INTERFACE_KEYS = frozenset(
    {
        "privatekey",
        "listenport",
        "address",
        "dns",
        "mtu",
        "jc",
        "jmin",
        "jmax",
        "s1",
        "s2",
        "s3",
        "s4",
        "h1",
        "h2",
        "h3",
        "h4",
        "i1",
        "i2",
        "i3",
        "i4",
        "i5",
        "headerprotectionkey",
        "contentpaddingaddition",
        "rekeyaftertime",
        "rekeytimeout",
        "rejectaftertime",
        "keepalivetimeout",
        "maxhandshakeattempts",
        "randomtrailers",
        "disablecookies",
    }
)
IOS_PEER_KEYS = frozenset(
    {
        "publickey",
        "presharedkey",
        "allowedips",
        "endpoint",
        "persistentkeepalive",
    }
)


def _collect_section_keys(conf_text: str) -> tuple[set[str], set[str]]:
    cfg = configparser.ConfigParser()
    cfg.optionxform = str
    cfg.read_string(conf_text.replace("\r", ""))
    iface_keys: set[str] = set()
    peer_keys: set[str] = set()
    if "Interface" in cfg:
        iface_keys = {k.lower() for k in cfg["Interface"]}
    for sec in cfg.sections():
        if sec != "Interface" and sec.lower().startswith("peer"):
            peer_keys = {k.lower() for k in cfg[sec]}
            break
    return iface_keys, peer_keys


def validate_ios_wg_quick(conf_text: str) -> None:
    """Проверка: только ключи из whitelist парсера AmneziaWG iOS."""
    iface, peer = _collect_section_keys(conf_text)
    extra_iface = iface - IOS_INTERFACE_KEYS
    extra_peer = peer - IOS_PEER_KEYS
    if extra_iface or extra_peer:
        parts = []
        if extra_iface:
            parts.append(f"[Interface]: {', '.join(sorted(extra_iface))}")
        if extra_peer:
            parts.append(f"[Peer]: {', '.join(sorted(extra_peer))}")
        raise ValueError("лишние поля для AmneziaWG iOS: " + "; ".join(parts))


def normalize_client_conf(conf_text: str) -> str:
    text = conf_text.replace("\r", "")
    cfg = configparser.ConfigParser()
    cfg.optionxform = str
    cfg.read_string(text)
    if "Interface" not in cfg:
        raise ValueError("Нет секции [Interface]")
    peer = None
    for sec in cfg.sections():
        if sec != "Interface" and sec.lower().startswith("peer"):
            peer = cfg[sec]
            break
    if peer is None:
        raise ValueError("Нет секции [Peer]")

    iface = {k: cfg["Interface"].get(k, "").strip() for k in cfg["Interface"]}
    peer_d = {k: peer.get(k, "").strip() for k in peer}

    addr = iface.get("Address", "")
    client_ip = addr.split("/", 1)[0] if "/" in addr else addr

    dns = iface.get("DNS", "1.1.1.1, 1.0.0.1")
    dns_parts = [p.strip() for p in re.split(r"[, ]+", dns) if p.strip()]
    if len(dns_parts) >= 2:
        dns_line = f"{dns_parts[0]}, {dns_parts[1]}"
    elif dns_parts:
        dns_line = dns_parts[0]
    else:
        dns_line = "1.1.1.1, 1.0.0.1"

    lines = [
        "[Interface]",
        f"Address = {client_ip}/32",
        f"DNS = {dns_line}",
    ]
    mtu = iface.get("MTU", "").strip()
    if mtu:
        lines.append(f"MTU = {mtu}")
    lines.append(f"PrivateKey = {iface.get('PrivateKey', '')}")
    for key in ("Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4", "H1", "H2", "H3", "H4"):
        if key in iface:
            lines.append(f"{key} = {iface[key]}")
    for n in range(1, 6):
        if f"I{n}" in iface:
            lines.append(f"I{n} = {iface[f'I{n}']}")
    for key in (
        "HeaderProtectionKey",
        "ContentPaddingAddition",
        "RekeyAfterTime",
        "RekeyTimeout",
        "RejectAfterTime",
        "KeepaliveTimeout",
        "MaxHandshakeAttempts",
        "RandomTrailers",
        "DisableCookies",
    ):
        if key in iface and iface[key].strip():
            lines.append(f"{key} = {iface[key]}")

    lines += ["", "[Peer]"]
    if peer_d.get("PublicKey"):
        lines.append(f"PublicKey = {peer_d['PublicKey']}")
    if peer_d.get("PresharedKey"):
        lines.append(f"PresharedKey = {peer_d['PresharedKey']}")
    lines.append(f"AllowedIPs = {peer_d.get('AllowedIPs', '0.0.0.0/0, ::/0')}")
    if peer_d.get("Endpoint"):
        lines.append(f"Endpoint = {peer_d['Endpoint']}")
    lines.append(f"PersistentKeepalive = {peer_d.get('PersistentKeepalive', '25')}")
    out = "\n".join(lines) + "\n"
    validate_ios_wg_quick(out)
    return out
