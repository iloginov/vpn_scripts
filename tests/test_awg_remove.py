"""remove: по client_*.conf (позиционный аргумент) и по --server-conf + --ip."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import pytest

from vpn_awg.clients_registry import add_client_record, load_registry
from vpn_awg.conf_parse import parse_server_conf
from vpn_awg.obfs import generate_awg2_server_shared_only, render_server_obfs_lines

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import awg_manage as cli  # noqa: E402

_CLIENT_IP = "10.10.1.2/32"
_PEER_PUB = "cGhlci1wdWJsaWMta2V5MTIzNDU2Nzg5MA=="
_CLIENT_CONF = f"""\
[Interface]
Address = {_CLIENT_IP}
DNS = 1.1.1.1
PrivateKey = Y2xpZW50LXByaXZhdGUta2V5MTIzNDU2Nzg5MA==

[Peer]
PublicKey = c2VydmVyLXB1YmxpYy1rZXkxMjM0NTY3ODkw
Endpoint = 203.0.113.1:443
PersistentKeepalive = 25
"""


def _write_server_conf(path: Path, *, with_peer: bool = True) -> None:
    server = generate_awg2_server_shared_only(1280)
    lines = [
        "[Interface]",
        "PrivateKey = c2VydmVyLXByaXZhdGUta2V5MTIzNDU2Nzg5MA==",
        "Address = 10.10.1.1/24",
        "ListenPort = 443",
        "",
    ]
    lines.extend(render_server_obfs_lines(server))
    if with_peer:
        lines.extend(
            [
                "",
                "[Peer]",
                f"PublicKey = {_PEER_PUB}",
                "PresharedKey = cHNrMTIzNDU2Nzg5MDEyMzQ1Njc4OTAxMjM0NTY3ODkw",
                f"AllowedIPs = {_CLIENT_IP}",
                "PersistentKeepalive = 25",
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_resolve_remove_targets_client_conf():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        server = root / "server_awg0.conf"
        _write_server_conf(server)
        client = root / "client_alice.conf"
        client.write_text(_CLIENT_CONF, encoding="utf-8")

        server_path, ip_str, client_path = cli.resolve_remove_targets(
            argparse.Namespace(
                client_conf=str(client),
                server_conf=None,
                ip=None,
            )
        )
        assert server_path.resolve() == server.resolve()
        assert ip_str == _CLIENT_IP
        assert client_path.resolve() == client.resolve()


def test_resolve_remove_targets_multiple_servers_error():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_server_conf(root / "server_awg0.conf")
        _write_server_conf(root / "server_awg1.conf")
        client = root / "client_alice.conf"
        client.write_text(_CLIENT_CONF, encoding="utf-8")

        with pytest.raises(SystemExit):
            cli.resolve_remove_targets(
                argparse.Namespace(
                    client_conf=str(client),
                    server_conf=None,
                    ip=None,
                )
            )


def test_cmd_remove_by_client_conf(tmp_path: Path, capsys):
    server = tmp_path / "server_awg0.conf"
    _write_server_conf(server)
    client = tmp_path / "client_alice.conf"
    client.write_text(_CLIENT_CONF, encoding="utf-8")
    registry = tmp_path / "server_awg0.json"
    add_client_record(
        registry,
        name="alice",
        filename="client_alice.conf",
        ip=_CLIENT_IP,
    )

    cli.cmd_remove(
        argparse.Namespace(
            client_conf=str(client),
            server_conf=None,
            ip=None,
        )
    )

    _, _, _, _, peers, _, _, _ = parse_server_conf(server)
    assert peers == []
    assert not client.exists()
    assert client.with_suffix(".conf.bak").is_file()
    assert load_registry(registry)["clients"] == []

    out = capsys.readouterr().out
    assert "переименован" in out
