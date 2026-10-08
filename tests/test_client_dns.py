"""DNS клиента: # DNS в server, CLI, дефолт."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import awg_manage as m
from vpn_awg.conf_parse import parse_server_conf
from vpn_awg.obfs import generate_awg2_server_shared_only, render_server_obfs_lines


def test_parse_commented_dns(tmp_path: Path):
    server = replace(
        generate_awg2_server_shared_only(1280),
        client_dns="1.1.1.1,1.0.0.1",
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
    assert parsed.client_dns == "1.1.1.1,1.0.0.1"


def test_resolve_client_dns_default():
    server = generate_awg2_server_shared_only(1280)
    args = type("A", (), {"dns": None})()
    assert m.resolve_client_dns(server, args) == m._DEFAULT_CLIENT_DNS


def test_resolve_client_dns_from_server_comment():
    server = replace(
        generate_awg2_server_shared_only(1280),
        client_dns="1.0.0.1",
    )
    args = type("A", (), {"dns": None})()
    assert m.resolve_client_dns(server, args) == "1.0.0.1"


def test_resolve_client_dns_cli_overrides():
    server = replace(
        generate_awg2_server_shared_only(1280),
        client_dns="1.1.1.1",
    )
    args = type("A", (), {"dns": "9.9.9.9"})()
    assert m.resolve_client_dns(server, args) == "9.9.9.9"
