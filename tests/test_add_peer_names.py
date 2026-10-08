"""awg_manage add: --name, --description (метка в JSON)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import awg_manage as m
from vpn_awg.clients_registry import load_registry, registry_json_path


def _init_minimal_server(tmp_path: Path) -> Path:
    out = tmp_path / "out"
    out.mkdir()
    server_conf = out / "server_awg0.conf"
    argv = [
        "init",
        "--subnet",
        "10.10.2.0/24",
        "--server-name",
        "Global VPN",
        "--server-host",
        "vpn.example.test",
        "--intensity",
        "medium",
        "--junk-level",
        "5",
        "--output-dir",
        str(out),
    ]
    old = sys.argv
    try:
        sys.argv = ["awg_manage.py", *argv]
        m.main()
    finally:
        sys.argv = old
    return server_conf


def _run_add(server_conf: Path, name: str, **extra: str) -> None:
    argv = ["add", "--server-conf", str(server_conf), "--name", name]
    for k, v in extra.items():
        if v is not None:
            argv.extend([f"--{k.replace('_', '-')}", v])
    old = sys.argv
    try:
        sys.argv = ["awg_manage.py", *argv]
        m.main()
    finally:
        sys.argv = old


@pytest.fixture
def server_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(m, "check_awg", lambda: None)
    monkeypatch.setattr(
        m,
        "run_cmd",
        lambda cmd, input_data=None: {
            "genkey": "cPrivKeyBase64Placeholder12=\n",
            "pubkey": "sPubKeyBase64Placeholder12=\n",
            "genpsk": "pskKeyBase64Placeholder12=\n",
        }[cmd[1]],
    )
    return _init_minimal_server(tmp_path)


def test_add_registry_description_empty_by_default(server_tree: Path) -> None:
    _run_add(server_tree, "peer_a")
    reg = load_registry(registry_json_path(server_tree))
    entry = next(c for c in reg["clients"] if c["name"] == "peer_a")
    assert entry["description"] == ""
    assert not (server_tree.parent / "client_peer_a.vpn").exists()


def test_add_registry_description_only(server_tree: Path) -> None:
    _run_add(server_tree, "peer_b", description="Ноутбук")
    reg = load_registry(registry_json_path(server_tree))
    entry = next(c for c in reg["clients"] if c["name"] == "peer_b")
    assert entry["description"] == "Ноутбук"
    assert (server_tree.parent / "client_peer_b.conf").is_file()
    assert not (server_tree.parent / "client_peer_b.vpn").exists()
