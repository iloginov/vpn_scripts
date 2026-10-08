"""Клиентский .conf → .vpn (vpn:// + qCompress)."""

from __future__ import annotations

import base64
import json
import subprocess
import zlib
from pathlib import Path
from typing import Any

from amnezia_export.server import mtu_from_conf, read_wg_conf, resolve_server_conf, subnet_address_from_server
from amnezia_export.wg_tools import wireguard_pubkey_bin
from vpn_awg.conf_parse import parse_server_name_comment, resolve_vpn_display_name

VPN_JSON_DEFAULT_MTU = "1376"
CONTAINER_AWG2 = "amnezia-awg2"
_AWG3_KEYS = (
    "HeaderProtectionKey",
    "ContentPaddingAddition",
    "RekeyAfterTime",
    "RekeyTimeout",
    "RejectAfterTime",
    "KeepaliveTimeout",
    "MaxHandshakeAttempts",
    "RandomTrailers",
    "DisableCookies",
)
_JSON_COMPACT = {"ensure_ascii": False, "separators": (",", ":")}
_JSON_INDENT = {"ensure_ascii": False, "indent": 4}


def _run_wg_pubkey(priv_key: str) -> str:
    res = subprocess.run(
        [wireguard_pubkey_bin(), "pubkey"],
        input=priv_key + "\n",
        text=True,
        capture_output=True,
        check=True,
    )
    return res.stdout.strip()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _qcompress(data: bytes) -> bytes:
    return len(data).to_bytes(4, "big") + zlib.compress(data, level=8)


def _parse_special_junk(iface) -> dict[str, str]:
    return {f"I{n}": iface.get(f"I{n}", "").strip() for n in range(1, 6)}


def _extract(cfg) -> dict[str, Any]:
    if "Interface" not in cfg:
        raise RuntimeError("В конфиге нет секции [Interface].")
    iface = cfg["Interface"]
    if not (iface.get("S3", "").strip() and iface.get("S4", "").strip()):
        raise RuntimeError("Ожидается AmneziaWG 2.0+: в [Interface] нужны S3 и S4.")

    addr = iface.get("Address", "").strip()
    client_ip = addr.split("/", 1)[0] if "/" in addr else addr

    dns_line = iface.get("DNS", "").strip()
    dns1, dns2 = "1.1.1.1", "1.0.0.1"
    if dns_line:
        parts = [p.strip() for p in dns_line.replace(" ", "").split(",") if p.strip()]
        if parts:
            dns1 = parts[0]
        if len(parts) >= 2:
            dns2 = parts[1]

    mtu_in_conf = mtu_from_conf(cfg)

    peer = None
    for sec in cfg.sections():
        if sec != "Interface" and sec.lower().startswith("peer"):
            peer = cfg[sec]
            break
    if peer is None:
        raise RuntimeError("В конфиге нет секции [Peer].")

    endpoint = peer.get("Endpoint", "").strip()
    hostName, port = "", 51820
    if endpoint and ":" in endpoint:
        hostName, port_str = endpoint.rsplit(":", 1)
        hostName = hostName.strip()
        try:
            port = int(port_str)
        except ValueError:
            port = 51820

    allowed_line = peer.get("AllowedIPs", "").strip()
    allowed_ips = (
        [p.strip() for p in allowed_line.split(",") if p.strip()]
        if allowed_line
        else ["0.0.0.0/0", "::/0"]
    )

    required = ("Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4", "H1", "H2", "H3", "H4", "PrivateKey")
    missing = [k for k in required if not iface.get(k, "").strip()]
    if missing:
        raise RuntimeError(f"В [Interface] не хватает: {', '.join(missing)}")

    awg3 = {k: iface.get(k, "").strip() for k in _AWG3_KEYS if iface.get(k, "").strip()}

    return {
        "client_ip": client_ip,
        "dns1": dns1,
        "dns2": dns2,
        "mtu_in_conf": mtu_in_conf,
        "Jc": iface.get("Jc", "").strip(),
        "Jmin": iface.get("Jmin", "").strip(),
        "Jmax": iface.get("Jmax", "").strip(),
        "S1": iface.get("S1", "").strip(),
        "S2": iface.get("S2", "").strip(),
        "S3": iface.get("S3", "").strip(),
        "S4": iface.get("S4", "").strip(),
        "H1": iface.get("H1", "").strip(),
        "H2": iface.get("H2", "").strip(),
        "H3": iface.get("H3", "").strip(),
        "H4": iface.get("H4", "").strip(),
        "awg3": awg3,
        "special_junk": _parse_special_junk(iface),
        "client_priv_key": iface.get("PrivateKey", "").strip(),
        "server_pub_key": peer.get("PublicKey", "").strip(),
        "psk_key": peer.get("PresharedKey", "").strip(),
        "allowed_ips": allowed_ips,
        "hostName": hostName,
        "port": port,
        "persistent_keep_alive": peer.get("PersistentKeepalive", "25").strip(),
    }


def _json_mtu(p: dict[str, Any]) -> str:
    return p["mtu_in_conf"] if p["mtu_in_conf"] else VPN_JSON_DEFAULT_MTU


def _protocol_version(awg3: dict[str, str]) -> str:
    # Свежий AmneziaVPN сравнивает protocol_version с константой awgV3 = "3.1"
    # и помечает всё остальное (включая наше старое "3") как устаревший AWG 2.0.
    # Контейнер при этом по-прежнему amnezia-awg2 — отдельного amnezia-awg3 нет.
    if any(awg3.get(k) for k in _AWG3_KEYS):
        return "3.1"
    return "2"


def _config_text(p: dict[str, Any]) -> str:
    lines = [
        "[Interface]",
        f"Address = {p['client_ip']}/32",
        "DNS = $PRIMARY_DNS, $SECONDARY_DNS",
    ]
    if p["mtu_in_conf"]:
        lines.append(f"MTU = {p['mtu_in_conf']}")
    lines.append(f"PrivateKey = {p['client_priv_key']}")
    for key in ("Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4", "H1", "H2", "H3", "H4"):
        lines.append(f"{key} = {p[key]}")
    for n in range(1, 6):
        lines.append(f"I{n} = {p['special_junk'][f'I{n}']}")
    for key in _AWG3_KEYS:
        if key in p["awg3"]:
            lines.append(f"{key} = {p['awg3'][key]}")
    lines += ["", "[Peer]", f"PublicKey = {p['server_pub_key']}"]
    if p["psk_key"]:
        lines.append(f"PresharedKey = {p['psk_key']}")
    lines.append("AllowedIPs = " + ", ".join(p["allowed_ips"]))
    lines.append(f"Endpoint = {p['hostName']}:{p['port']}")
    lines.append(f"PersistentKeepalive = {p['persistent_keep_alive']}")
    lines.append("")
    return "\n".join(lines)


def build_vpn_json(
    client_conf: Path,
    *,
    server_name: str | None = None,
    server_conf: Path | None = None,
) -> str:
    cfg = read_wg_conf(client_conf)
    p = _extract(cfg)
    server_path = resolve_server_conf(client_conf, server_conf)
    resolved_description = resolve_vpn_display_name(
        server_name_cli=server_name,
        server_conf_path=server_path,
    )
    subnet = subnet_address_from_server(server_path)
    client_pub = _run_wg_pubkey(p["client_priv_key"])
    config_text = _config_text(p)

    protocol_version = _protocol_version(p["awg3"])

    awg: dict[str, Any] = {
        "port": str(p["port"]),
        "transport_proto": "udp",
        "protocol_version": protocol_version,
        "Jc": p["Jc"],
        "Jmin": p["Jmin"],
        "Jmax": p["Jmax"],
        "S1": p["S1"],
        "S2": p["S2"],
        "S3": p["S3"],
        "S4": p["S4"],
        "H1": p["H1"],
        "H2": p["H2"],
        "H3": p["H3"],
        "H4": p["H4"],
    }
    for n in range(1, 6):
        awg[f"I{n}"] = p["special_junk"][f"I{n}"]
    awg.update(p["awg3"])
    if subnet:
        awg["subnet_address"] = subnet

    last_config = {
        "config": config_text,
        "hostName": p["hostName"],
        "port": p["port"],
        "client_ip": p["client_ip"],
        "client_priv_key": p["client_priv_key"],
        "client_pub_key": client_pub,
        "server_pub_key": p["server_pub_key"],
        "psk_key": p["psk_key"],
        "clientId": client_pub,
        "allowed_ips": p["allowed_ips"],
        "persistent_keep_alive": p["persistent_keep_alive"],
        "mtu": _json_mtu(p),
        "Jc": p["Jc"],
        "Jmin": p["Jmin"],
        "Jmax": p["Jmax"],
        "S1": p["S1"],
        "S2": p["S2"],
        "S3": p["S3"],
        "S4": p["S4"],
        "H1": p["H1"],
        "H2": p["H2"],
        "H3": p["H3"],
        "H4": p["H4"],
        "I1": p["special_junk"]["I1"],
        "I2": p["special_junk"]["I2"],
        "I3": p["special_junk"]["I3"],
        "I4": p["special_junk"]["I4"],
        "I5": p["special_junk"]["I5"],
    }
    last_config.update(p["awg3"])
    awg["last_config"] = json.dumps(last_config, **_JSON_COMPACT)

    root = {
        "description": resolved_description,
        "hostName": p["hostName"],
        "nameOverriddenByUser": False,
        "containers": [{"container": CONTAINER_AWG2, "awg": awg}],
        "defaultContainer": CONTAINER_AWG2,
        "dns1": p["dns1"],
        "dns2": p["dns2"],
    }
    return json.dumps(root, **_JSON_INDENT)


def write_vpn_file(
    client_conf: Path,
    *,
    server_name: str | None = None,
    server_conf: Path | None = None,
) -> Path:
    text = "vpn://" + _b64url(
        _qcompress(
            build_vpn_json(
                client_conf,
                server_name=server_name,
                server_conf=server_conf,
            ).encode()
        )
    )
    out = client_conf.with_suffix(".vpn")
    out.write_text(text, encoding="utf-8")
    return out
