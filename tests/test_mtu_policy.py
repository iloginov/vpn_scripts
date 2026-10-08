"""Политика MTU: server/client conf, AWG QR, vpn://."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from amnezia_export.awg_conf import normalize_client_conf
from amnezia_export.vpn import VPN_JSON_DEFAULT_MTU, _extract, build_vpn_json
from amnezia_export.server import read_wg_conf
from vpn_awg.conf import generate_client_config, generate_server_config
from vpn_awg.obfs import (
    Awg2ClientObfs,
    Awg2ServerObfs,
    generate_awg2_server_shared_only,
    render_server_obfs_lines,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import awg_manage as cli  # noqa: E402

_TEST_PRIV = "cFzGNvY8yV1q2m3n4o5p6q7r8s9t0u1v2w3x4y5z6a7b8c9d0e1f2g3="
_TEST_PUB = "5Rj4R73JyvKiAlj1WWTeqP+lP3DB8En71WAWpwWSujE="


def _minimal_client_conf(*, mtu_line: str | None = None) -> str:
    lines = [
        "[Interface]",
        "Address = 10.8.1.2/32",
        "DNS = 1.1.1.1, 1.0.0.1",
    ]
    if mtu_line is not None:
        lines.append(mtu_line)
    lines += [
        f"PrivateKey = {_TEST_PRIV}",
        "Jc = 5",
        "Jmin = 10",
        "Jmax = 50",
        "S1 = 37",
        "S2 = 116",
        "S3 = 35",
        "S4 = 14",
        "H1 = 376345202-376355028",
        "H2 = 1347085878-1347121167",
        "H3 = 2673328934-2673374377",
        "H4 = 3757569531-3757583681",
        "I1 = ",
        "I2 = ",
        "I3 = ",
        "I4 = ",
        "I5 = ",
        "",
        "[Peer]",
        "PublicKey = ooTZcPF6VsgNZzFi1XFXjIRN0a8QaxBoRCaSC/nsxnI=",
        "AllowedIPs = 0.0.0.0/0, ::/0",
        "Endpoint = 10.0.0.1:443",
        "PersistentKeepalive = 25",
        "",
    ]
    return "\n".join(lines)


def test_server_config_mtu_only_when_requested():
    server = generate_awg2_server_shared_only(1420)
    without = generate_server_config(
        "srvpriv", "10.0.0.1/24", 443, server, [], include_mtu=False
    )
    assert "MTU" not in without
    with_mtu = generate_server_config(
        "srvpriv", "10.0.0.1/24", 443, server, [], include_mtu=True
    )
    assert "MTU = 1420" in with_mtu


def test_resolve_client_mtu_none_without_server_mtu(tmp_path: Path):
    conf = tmp_path / "no_mtu.conf"
    conf.write_text(
        "\n".join(
            [
                "[Interface]",
                "PrivateKey = x",
                "Address = 10.0.0.1/24",
                "ListenPort = 443",
                "S1 = 10",
                "S2 = 20",
                "S3 = 8",
                "S4 = 8",
                "H1 = 1-2",
                "H2 = 3-4",
                "H3 = 5-6",
                "H4 = 7-8",
            ]
        )
        + "\n"
    )
    _, _, _, parsed, _, has_mtu, _, _ = cli.parse_server_conf(conf)
    assert not has_mtu

    class Args:
        mtu = None

    assert cli.resolve_client_mtu(parsed, Args(), iface_has_mtu=has_mtu) is None


def test_resolve_client_mtu_from_server(tmp_path: Path):
    server = generate_awg2_server_shared_only(1420)
    lines = [
        "[Interface]",
        "PrivateKey = x",
        "Address = 10.0.0.1/24",
        "ListenPort = 443",
        "",
    ]
    lines.extend(render_server_obfs_lines(server, include_mtu=True))
    conf = tmp_path / "s.conf"
    conf.write_text("\n".join(lines) + "\n")
    _, _, _, parsed, _, has_mtu, _, _ = cli.parse_server_conf(conf)
    assert has_mtu

    class Args:
        mtu = None

    assert cli.resolve_client_mtu(parsed, Args(), iface_has_mtu=has_mtu) == 1420


def test_generate_client_config_mtu_optional():
    server = generate_awg2_server_shared_only(1280)
    client_obfs = Awg2ClientObfs(jc=5, jmin=10, jmax=50, i_packets=[""] * 5)
    without = generate_client_config(
        server,
        _TEST_PRIV,
        "10.8.1.2/32",
        "srvpub",
        "1.2.3.4:443",
        client_obfs=client_obfs,
        client_mtu=None,
    )
    assert "MTU" not in without
    with_mtu = generate_client_config(
        server,
        _TEST_PRIV,
        "10.8.1.2/32",
        "srvpub",
        "1.2.3.4:443",
        client_obfs=client_obfs,
        client_mtu=1500,
    )
    assert "MTU = 1500" in with_mtu


def test_normalize_awg_qr_mtu_passthrough():
    with_mtu = normalize_client_conf(_minimal_client_conf(mtu_line="MTU = 1420"))
    assert "MTU = 1420" in with_mtu
    without = normalize_client_conf(_minimal_client_conf())
    assert "MTU" not in without


@patch("amnezia_export.vpn._run_wg_pubkey", return_value=_TEST_PUB)
def test_vpn_json_mtu_with_conf_line(_mock_pubkey):
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "client.conf"
        server = Path(td) / "server_awg0.conf"
        path.write_text(_minimal_client_conf(mtu_line="MTU = 1500"))
        server.write_text(
            "[Interface]\nPrivateKey = x\nAddress = 10.8.0.1/24\nListenPort = 443\n"
        )
        raw = build_vpn_json(path, server_name="t", server_conf=server)
    data = json.loads(raw)
    last = json.loads(data["containers"][0]["awg"]["last_config"])
    assert last["mtu"] == "1500"
    assert "MTU = 1500" in last["config"]


@patch("amnezia_export.vpn._run_wg_pubkey", return_value=_TEST_PUB)
def test_vpn_json_mtu_default_1376_without_conf(_mock_pubkey):
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "client.conf"
        server = Path(td) / "server_awg0.conf"
        path.write_text(_minimal_client_conf())
        server.write_text(
            "[Interface]\nPrivateKey = x\nAddress = 10.8.0.1/24\nListenPort = 443\n"
        )
        raw = build_vpn_json(path, server_name="t", server_conf=server)
    last = json.loads(json.loads(raw)["containers"][0]["awg"]["last_config"])
    assert last["mtu"] == VPN_JSON_DEFAULT_MTU
    assert "MTU" not in last["config"]


def test_extract_mtu_in_conf(tmp_path: Path):
    p1 = tmp_path / "c1.conf"
    p1.write_text(_minimal_client_conf(mtu_line="MTU = 1280"))
    assert _extract(read_wg_conf(p1))["mtu_in_conf"] == "1280"

    p2 = tmp_path / "c2.conf"
    p2.write_text(_minimal_client_conf())
    assert _extract(read_wg_conf(p2))["mtu_in_conf"] is None
