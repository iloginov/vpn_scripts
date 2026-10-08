"""Парсинг server_awg0.conf (active + закомментированный client template)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from vpn_awg.awg3 import Awg3Timers, parse_awg_bool
from vpn_awg.obfs import Awg2ClientObfs, Awg2ServerObfs

_CLIENT_KEYS = frozenset(
    {
        "Jc",
        "Jmin",
        "Jmax",
        "I1",
        "I2",
        "I3",
        "I4",
        "I5",
        "ContentPaddingAddition",
        "RekeyAfterTime",
        "RekeyTimeout",
        "RejectAfterTime",
        "KeepaliveTimeout",
        "MaxHandshakeAttempts",
        "RandomTrailers",
        "DisableCookies",
    }
)
_INTERFACE_HOOK_KEYS = frozenset({"PreUp", "PostUp", "PreDown", "PostDown"})


@dataclass(frozen=True)
class InterfaceHooks:
    """PreUp/PostUp/PreDown/PostDown из [Interface] в порядке появления в файле."""

    entries: tuple[tuple[str, str], ...] = ()

    @classmethod
    def empty(cls) -> InterfaceHooks:
        return cls(())

    def render_lines(self) -> list[str]:
        if not self.entries:
            return []
        lines = [f"{key} = {value}" for key, value in self.entries]
        lines.append("")
        return lines
_COMMENT_KV = re.compile(r"^#\s*([A-Za-z0-9]+)\s*=\s*(.+)$")
_GEN_META = re.compile(
    r"^#\s*awg-client-defaults:\s*mimic=(\S+)\s+intensity=(\S+)\s+junk_level=(\d+)\s*$"
)


def client_template_complete(template: Awg2ClientObfs | None) -> bool:
    if template is None:
        return False
    return len(template.i_packets) > 0


def parse_server_conf(
    path: Path,
) -> tuple[str, str, int, Awg2ServerObfs, list[dict], bool, InterfaceHooks, str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    current_section: str | None = None
    iface_data: dict[str, str] = {}
    template_data: dict[str, str] = {}
    dns_template: str | None = None
    server_host_template: str | None = None
    server_name_template: str | None = None
    peers: list[dict] = []
    current_peer: dict | None = None
    hook_entries: list[tuple[str, str]] = []

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            continue

        if stripped.startswith("[") and stripped.endswith("]"):
            section_name = stripped[1:-1].strip()
            if section_name == "Interface":
                current_section = "Interface"
                current_peer = None
            elif section_name == "Peer":
                if current_peer is not None:
                    if current_peer.get("public_key") and current_peer.get("allowed"):
                        peers.append(current_peer)
                current_section = "Peer"
                current_peer = {}
            else:
                current_section = None
                current_peer = None
            continue

        if current_section != "Interface":
            if current_section == "Peer" and current_peer is not None and "=" in stripped:
                if not stripped.startswith("#"):
                    key, value = stripped.split("=", 1)
                    key, value = key.strip(), value.strip()
                    if key == "PublicKey":
                        current_peer["public_key"] = value
                    elif key == "AllowedIPs":
                        current_peer["allowed"] = value
                    elif key == "PresharedKey":
                        current_peer["psk"] = value
                    elif key == "PersistentKeepalive":
                        current_peer["keepalive"] = value
            continue

        if stripped.startswith("#"):
            m = _COMMENT_KV.match(stripped)
            if m:
                key = m.group(1)
                if key == "DNS":
                    dns_template = m.group(2).strip()
                elif key == "ServerHost":
                    server_host_template = m.group(2).strip()
                elif key == "ServerName":
                    server_name_template = m.group(2).strip()
                elif key in _CLIENT_KEYS:
                    template_data[key] = m.group(2).strip()
            continue

        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key, value = key.strip(), value.strip()
        if key in _INTERFACE_HOOK_KEYS:
            hook_entries.append((key, value))
            continue
        iface_data[key] = value

    if current_peer is not None:
        if current_peer.get("public_key") and current_peer.get("allowed"):
            peers.append(current_peer)

    if "PrivateKey" not in iface_data:
        raise RuntimeError(
            "В конфиге нет секции [Interface] или отсутствует PrivateKey."
        )

    server_priv = iface_data.get("PrivateKey")
    address = iface_data.get("Address")
    listen_port = int(iface_data.get("ListenPort", 443))

    if not server_priv or not address:
        raise RuntimeError("В [Interface] должны быть PrivateKey и Address.")

    shared = Awg2ServerObfs.from_iface(iface_data).shared
    client_template = Awg2ClientObfs.from_iface(template_data, required=False)
    if client_template is None:
        client_template = Awg2ClientObfs.from_iface(iface_data, required=False)

    # Только # DNS = … — активный DNS в [Interface] сервера awg-quick не трогаем.
    client_dns = (dns_template or "").strip() or None
    client_server_host = (
        iface_data.get("ServerHost") or server_host_template or ""
    ).strip() or None
    client_server_name = (server_name_template or "").strip() or None
    server_obfs = Awg2ServerObfs(
        shared=shared,
        client_template=client_template,
        gen=None,
        client_dns=client_dns,
        client_server_host=client_server_host,
        client_server_name=client_server_name,
        timers=Awg3Timers.from_iface(iface_data),
        random_trailers=parse_awg_bool(iface_data.get("RandomTrailers", "")),
        disable_cookies=parse_awg_bool(iface_data.get("DisableCookies", "")),
    )

    iface_has_mtu = "MTU" in iface_data
    interface_hooks = InterfaceHooks(tuple(hook_entries))
    return (
        server_priv,
        address,
        listen_port,
        server_obfs,
        peers,
        iface_has_mtu,
        interface_hooks,
        text,
    )


DEFAULT_CLIENT_DESCRIPTION = "Russia 3.0"


def parse_server_name_comment(path: Path) -> str | None:
    """Только # ServerName = … (без полного parse_server_conf)."""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped.startswith("#"):
            continue
        m = _COMMENT_KV.match(stripped)
        if m and m.group(1) == "ServerName":
            value = m.group(2).strip()
            return value or None
    return None


def resolve_vpn_display_name(
    *,
    server_name_cli: str | None = None,
    server_conf_path: Path | None = None,
    server: Awg2ServerObfs | None = None,
) -> str:
    """Имя подключения в AmneziaVPN (поле description в vpn://)."""
    if server_name_cli is not None and server_name_cli.strip():
        return server_name_cli.strip()
    if server_conf_path is not None:
        parsed = parse_server_name_comment(server_conf_path)
        if parsed:
            return parsed
    if server is not None and server.client_server_name:
        return server.client_server_name.strip()
    return DEFAULT_CLIENT_DESCRIPTION
