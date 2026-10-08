"""ServerHost: init → server, add → CLI или server."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from vpn_awg.conf_parse import parse_server_conf
from vpn_awg.obfs import (
    generate_awg2_server_shared_only,
    render_server_obfs_lines,
)


def test_parse_commented_server_host(tmp_path: Path):
    server = replace(
        generate_awg2_server_shared_only(1280),
        client_server_host="203.0.113.20",
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
    assert parsed.client_server_host == "203.0.113.20"


def test_resolve_server_host_cli_overrides():
    import awg_manage as m

    server = replace(
        generate_awg2_server_shared_only(1280),
        client_server_host="1.2.3.4",
    )
    args = type("A", (), {"server_host": "vpn.example.com"})()
    assert m.resolve_server_host(server, args) == "vpn.example.com"


def test_resolve_server_host_missing_exits():
    import awg_manage as m

    server = generate_awg2_server_shared_only(1280)
    args = type("A", (), {"server_host": None})()
    with pytest.raises(SystemExit):
        m.resolve_server_host(server, args)
