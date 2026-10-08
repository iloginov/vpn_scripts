"""Генерация server/client .conf AmneziaWG 3.1."""

from __future__ import annotations

from vpn_awg.conf_parse import InterfaceHooks
from vpn_awg.obfs import (
    Awg2ClientObfs,
    Awg2ServerObfs,
    client_obfs_from_server,
    render_client_native_obfs_lines,
    render_server_obfs_lines,
)

# Порядок полей [Interface] как server_scripts/awg/template.conf (AmneziaVPN export)
_INTERFACE_KEYS_AFTER_PRIVATE = (
    "Jc",
    "Jmin",
    "Jmax",
    "S1",
    "S2",
    "S3",
    "S4",
    "H1",
    "H2",
    "H3",
    "H4",
    "I1",
    "I2",
    "I3",
    "I4",
    "I5",
)

_PEER_KEYS = (
    "PublicKey",
    "PresharedKey",
    "AllowedIPs",
    "Endpoint",
    "PersistentKeepalive",
)


def _client_address_line(client_ip: str) -> str:
    return client_ip if "/" in client_ip else f"{client_ip}/32"


def generate_server_config(
    server_priv: str,
    server_ip: str,
    listen_port: int,
    server_obfs: Awg2ServerObfs,
    peers: list,
    *,
    include_mtu: bool = True,
    interface_hooks: InterfaceHooks | None = None,
) -> str:
    lines = [
        "[Interface]",
        f"PrivateKey = {server_priv}",
        f"Address = {server_ip}",
        f"ListenPort = {listen_port}",
        "",
    ]
    lines.extend(render_server_obfs_lines(server_obfs, include_mtu=include_mtu))
    hooks = interface_hooks if interface_hooks is not None else InterfaceHooks.empty()
    lines.extend(hooks.render_lines())
    for peer in peers:
        lines.append("[Peer]")
        lines.append(f"PublicKey = {peer['public_key']}")
        if peer.get("psk"):
            lines.append(f"PresharedKey = {peer['psk']}")
        lines.append(f"AllowedIPs = {peer['ip']}")
        if peer.get("keepalive"):
            lines.append(f"PersistentKeepalive = {peer['keepalive']}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def generate_client_config(
    server_obfs: Awg2ServerObfs,
    client_priv: str,
    client_ip: str,
    server_pub: str,
    endpoint: str,
    *,
    dns: str = "",
    psk: str = "",
    client_obfs: Awg2ClientObfs | None = None,
    keepalive: int = 25,
    client_mtu: int | None = None,
) -> str:
    """
    Клиентский .conf как native export AmneziaVPN:
    Address → DNS → [MTU] → PrivateKey → obfs → [Peer].
    MTU только если передан client_mtu (из CLI, client conf или server conf).
    """
    if client_obfs is None:
        client_obfs = client_obfs_from_server(server_obfs)

    if dns:
        dns_line = dns
    else:
        dns_line = "1.1.1.1, 1.0.0.1"

    lines = [
        "[Interface]",
        f"Address = {_client_address_line(client_ip)}",
        f"DNS = {dns_line}",
    ]
    if client_mtu is not None:
        lines.append(f"MTU = {client_mtu}")
    lines.append(f"PrivateKey = {client_priv}")
    lines.extend(render_client_native_obfs_lines(server_obfs.shared, client_obfs))
    lines.append("")
    lines.append("[Peer]")
    lines.append(f"PublicKey = {server_pub}")
    if psk:
        lines.append(f"PresharedKey = {psk}")
    lines.append("AllowedIPs = 0.0.0.0/0, ::/0")
    lines.append(f"Endpoint = {endpoint}")
    if keepalive:
        lines.append(f"PersistentKeepalive = {keepalive}")
    lines.append("")
    text = "\n".join(lines)
    from amnezia_export.awg_conf import validate_ios_wg_quick

    validate_ios_wg_quick(text)
    return text
