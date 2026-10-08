"""MTU клиента: CLI, server conf, ошибки."""

from __future__ import annotations

from pathlib import Path

import pytest

from vpn_awg.conf_parse import parse_server_conf
from vpn_awg.obfs import generate_awg2_server_shared_only, render_server_obfs_lines

# импорт функций CLI-модуля
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import awg_manage as cli  # noqa: E402


def test_resolve_client_mtu_from_server(tmp_path: Path):
    server = generate_awg2_server_shared_only(1420)
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

    class Args:
        mtu = None

    mtu = cli.resolve_client_mtu(parsed, Args(), iface_has_mtu=has_mtu)
    assert mtu == 1420


def test_resolve_client_mtu_cli_override(tmp_path: Path):
    server = generate_awg2_server_shared_only(1280)
    class Args:
        mtu = 1500

    mtu = cli.resolve_client_mtu(server, Args(), iface_has_mtu=True)
    assert mtu == 1500


def test_resolve_client_mtu_missing_in_server(tmp_path: Path):
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
    _, _, _, parsed, _, has_mtu, _, _ = parse_server_conf(conf)
    assert not has_mtu

    class Args:
        mtu = None

    assert cli.resolve_client_mtu(parsed, Args(), iface_has_mtu=has_mtu) is None
