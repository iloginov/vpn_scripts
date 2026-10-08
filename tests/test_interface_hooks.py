"""PreUp/PostUp/PreDown/PostDown сохраняются при пересборке server .conf."""

from __future__ import annotations

from pathlib import Path

from vpn_awg.conf import generate_server_config
from vpn_awg.conf_parse import InterfaceHooks, parse_server_conf
from vpn_awg.obfs import generate_awg2_server_shared_only, render_server_obfs_lines


def _minimal_server_lines(*extra_iface: str) -> list[str]:
    server = generate_awg2_server_shared_only(1280)
    lines = [
        "[Interface]",
        "PrivateKey = c2VydmVyLXByaXZhdGUta2V5MTIzNDU2Nzg5MA==",
        "Address = 10.10.1.1/24",
        "ListenPort = 443",
        "",
    ]
    lines.extend(render_server_obfs_lines(server))
    lines.extend(extra_iface)
    return lines


def test_parse_multiple_hooks_in_order(tmp_path: Path):
    lines = _minimal_server_lines(
        "PostUp = first postup",
        "PostUp = second postup",
        "PreDown = one predown",
        "PostDown = one postdown",
        "PreUp = rare preup",
        "",
    )
    conf = tmp_path / "server.conf"
    conf.write_text("\n".join(lines) + "\n", encoding="utf-8")

    *_, hooks, _ = parse_server_conf(conf)
    assert hooks.entries == (
        ("PostUp", "first postup"),
        ("PostUp", "second postup"),
        ("PreDown", "one predown"),
        ("PostDown", "one postdown"),
        ("PreUp", "rare preup"),
    )


def test_roundtrip_preserves_hooks(tmp_path: Path):
    lines = _minimal_server_lines(
        "PostUp = ip rule add from 10.10.1.0/24 table clientroute 2>/dev/null || true",
        "PostUp = ip rule add iif awg0 table clientroute 2>/dev/null || true",
        "PostDown = ip rule del from 10.10.1.0/24 table clientroute 2>/dev/null || true",
        "",
        "[Peer]",
        "PublicKey = cGhlci1wdWJsaWMta2V5MTIzNDU2Nzg5MA==",
        "AllowedIPs = 10.10.1.2/32",
        "",
    )
    conf = tmp_path / "server.conf"
    conf.write_text("\n".join(lines) + "\n", encoding="utf-8")

    priv, address, port, server_obfs, peers, has_mtu, hooks, _ = parse_server_conf(conf)
    rebuilt = generate_server_config(
        server_priv=priv,
        server_ip=address,
        listen_port=port,
        server_obfs=server_obfs,
        peers=[
            {
                "public_key": peers[0]["public_key"],
                "ip": peers[0]["allowed"],
                "keepalive": 25,
            }
        ],
        include_mtu=has_mtu,
        interface_hooks=hooks,
    )
    conf.write_text(rebuilt, encoding="utf-8")
    _, _, _, _, _, _, hooks2, _ = parse_server_conf(conf)
    assert hooks2.entries == hooks.entries


def test_generate_without_hooks(tmp_path: Path):
    server = generate_awg2_server_shared_only(1280)
    text = generate_server_config(
        server_priv="x" * 43 + "=",
        server_ip="10.10.1.1/24",
        listen_port=443,
        server_obfs=server,
        peers=[],
        interface_hooks=InterfaceHooks.empty(),
    )
    assert "PostUp" not in text
    assert "PreDown" not in text
