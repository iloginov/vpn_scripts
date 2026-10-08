"""Парсинг server conf с закомментированным client template."""

from __future__ import annotations

import textwrap
from pathlib import Path

from vpn_awg.conf_parse import client_template_complete, parse_server_conf
from vpn_awg.obfs import (
    Awg2ClientObfs,
    Awg2ServerObfs,
    generate_awg2_server_obfs,
    render_server_obfs_lines,
)
from vpn_awg.gen_config import GenConfig


def test_parse_commented_client_template(tmp_path: Path):
    cfg = GenConfig(
        mimic="quic_initial", intensity="medium", junk_level=5, mtu=1280
    )
    server = generate_awg2_server_obfs(cfg)
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

    _, _, _, parsed, _, has_mtu, _, _ = parse_server_conf(conf)
    assert has_mtu
    assert client_template_complete(parsed.client_template)
    assert parsed.client_template is not None
    assert parsed.client_template.jc == server.client_template.jc
    assert parsed.random_trailers == server.random_trailers
    assert parsed.disable_cookies == server.disable_cookies
    assert parsed.client_template.random_trailers == server.client_template.random_trailers


def test_shared_only_server_has_no_template(tmp_path: Path):
    from vpn_awg.obfs import generate_awg2_server_shared_only

    server = generate_awg2_server_shared_only(1280)
    lines = [
        "[Interface]",
        "PrivateKey = x",
        "Address = 10.0.0.1/24",
        "ListenPort = 443",
        "",
    ]
    lines.extend(render_server_obfs_lines(server))
    conf = tmp_path / "s.conf"
    conf.write_text("\n".join(lines) + "\n")
    _, _, _, parsed, _, has_mtu, _, _ = parse_server_conf(conf)
    assert has_mtu
    assert not client_template_complete(parsed.client_template)
