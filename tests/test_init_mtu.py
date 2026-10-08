"""MTU на init: опционально, без флага не пишется в server."""

from __future__ import annotations

from pathlib import Path

from vpn_awg.conf import generate_server_config
from vpn_awg.conf_parse import parse_server_conf
from vpn_awg.obfs import generate_awg2_server_shared_only


def test_init_without_mtu_omits_line():
    server = generate_awg2_server_shared_only(1280)
    text = generate_server_config(
        server_priv="k",
        server_ip="10.0.0.1/24",
        listen_port=443,
        server_obfs=server,
        peers=[],
        include_mtu=False,
    )
    assert "MTU" not in text


def test_init_with_mtu_writes_line(tmp_path: Path):
    server = generate_awg2_server_shared_only(1420)
    text = generate_server_config(
        server_priv="k",
        server_ip="10.0.0.1/24",
        listen_port=443,
        server_obfs=server,
        peers=[],
        include_mtu=True,
    )
    conf = tmp_path / "s.conf"
    conf.write_text(text, encoding="utf-8")
    _, _, _, _, _, has_mtu, _, _ = parse_server_conf(conf)
    assert has_mtu
    assert "MTU = 1420" in text
