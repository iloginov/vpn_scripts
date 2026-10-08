"""ServerName: init → server, vpn:// / add → server, CLI, дефолт."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import awg_manage as m
from amnezia_export.vpn import build_vpn_json
from vpn_awg.conf_parse import (
    DEFAULT_CLIENT_DESCRIPTION,
    parse_server_conf,
    resolve_vpn_display_name,
)
from vpn_awg.obfs import generate_awg2_server_shared_only, render_server_obfs_lines


def test_parse_commented_server_name(tmp_path: Path):
    server = replace(
        generate_awg2_server_shared_only(1280),
        client_server_name="Russia 2.0",
    )
    lines = [
        "[Interface]",
        "PrivateKey = sPriv",
        "Address = 10.10.1.1/24",
        "ListenPort = 443",
        "",
    ]
    lines.extend(render_server_obfs_lines(server))
    conf = tmp_path / "server_awg0.conf"
    conf.write_text("\n".join(lines) + "\n", encoding="utf-8")

    _, _, _, parsed, _, _, _, _ = parse_server_conf(conf)
    assert parsed.client_server_name == "Russia 2.0"


def test_parse_server_name_with_spaces(tmp_path: Path):
    server = replace(
        generate_awg2_server_shared_only(1280),
        client_server_name="Russia 2.0 EU",
    )
    lines = [
        "[Interface]",
        "PrivateKey = x",
        "Address = 10.0.0.1/24",
        "ListenPort = 443",
        "",
    ]
    lines.extend(render_server_obfs_lines(server))
    conf = tmp_path / "s.conf"
    conf.write_text("\n".join(lines) + "\n", encoding="utf-8")

    _, _, _, parsed, _, _, _, _ = parse_server_conf(conf)
    assert parsed.client_server_name == "Russia 2.0 EU"


def test_resolve_vpn_display_name_cli_over_server_conf():
    server = replace(
        generate_awg2_server_shared_only(1280),
        client_server_name="From Server",
    )
    assert (
        resolve_vpn_display_name(server_name_cli="From CLI", server=server)
        == "From CLI"
    )


def test_resolve_vpn_display_name_from_server_obfs():
    server = replace(
        generate_awg2_server_shared_only(1280),
        client_server_name="From Server",
    )
    assert (
        resolve_vpn_display_name(server_name_cli=None, server=server) == "From Server"
    )


def test_resolve_vpn_display_name_default():
    server = generate_awg2_server_shared_only(1280)
    assert resolve_vpn_display_name(server=server) == DEFAULT_CLIENT_DESCRIPTION
    assert DEFAULT_CLIENT_DESCRIPTION == "Russia 3.0"


def test_build_vpn_json_uses_server_name(tmp_path: Path, monkeypatch):
    server = replace(
        generate_awg2_server_shared_only(1280),
        client_server_name="My VPN Node",
    )
    server_lines = [
        "[Interface]",
        "PrivateKey = sPriv",
        "Address = 10.10.1.0/24",
        "ListenPort = 443",
        "",
    ]
    server_lines.extend(render_server_obfs_lines(server))
    server_conf = tmp_path / "server_awg0.conf"
    server_conf.write_text("\n".join(server_lines) + "\n", encoding="utf-8")

    client_conf = tmp_path / "client_a.conf"
    client_conf.write_text(
        "\n".join(
            [
                "[Interface]",
                "Address = 10.10.1.2/32",
                "DNS = 1.1.1.1",
                "PrivateKey = cPrivKeyBase64Placeholder12=",
                "Jc = 4",
                "Jmin = 40",
                "Jmax = 70",
                "S1 = 1",
                "S2 = 2",
                "S3 = 3",
                "S4 = 4",
                "H1 = 1",
                "H2 = 2",
                "H3 = 3",
                "H4 = 4",
                "",
                "[Peer]",
                "PublicKey = sPubKeyBase64Placeholder12=",
                "Endpoint = 1.2.3.4:443",
                "AllowedIPs = 0.0.0.0/0",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "amnezia_export.vpn._run_wg_pubkey",
        lambda _priv: "clientPubKeyBase64Placeholder=",
    )

    raw = build_vpn_json(
        client_conf,
        server_name="CLI override",
        server_conf=server_conf,
    )
    data = json.loads(raw)
    assert data["description"] == "CLI override"

    raw_default = build_vpn_json(client_conf, server_conf=server_conf)
    data_default = json.loads(raw_default)
    assert data_default["description"] == "My VPN Node"
