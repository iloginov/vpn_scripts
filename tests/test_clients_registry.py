"""Реестр <server>.json рядом с серверным .conf."""

from pathlib import Path

import pytest

from vpn_awg.clients_registry import (
    add_client_record,
    load_registry,
    registry_json_path,
    remove_client_by_ip,
)


def test_registry_path_from_server_conf():
    server = Path("/opt/vpn/server_awg0.conf")
    assert registry_json_path(server) == Path("/opt/vpn/server_awg0.json")


def test_add_and_remove_client(tmp_path: Path):
    server_conf = tmp_path / "server_awg0.conf"
    server_conf.write_text("[Interface]\n", encoding="utf-8")
    path = registry_json_path(server_conf)
    add_client_record(
        path,
        name="alice",
        filename="client_alice.conf",
        ip="10.10.1.2/32",
        description="test device",
    )
    data = load_registry(path)
    assert len(data["clients"]) == 1
    assert data["clients"][0]["description"] == "test device"
    assert data["clients"][0]["filename"] == "client_alice.conf"

    assert remove_client_by_ip(path, "10.10.1.2")
    assert load_registry(path)["clients"] == []


def test_empty_description(tmp_path: Path):
    server_conf = tmp_path / "awg0.conf"
    server_conf.touch()
    path = registry_json_path(server_conf)
    assert path.name == "awg0.json"
    add_client_record(
        path,
        name="n",
        filename="client_n.conf",
        ip="10.0.0.2/32",
    )
    assert load_registry(path)["clients"][0]["description"] == ""


def test_duplicate_name(tmp_path: Path):
    server_conf = tmp_path / "server.conf"
    server_conf.touch()
    path = registry_json_path(server_conf)
    add_client_record(
        path,
        name="x",
        filename="client_x.conf",
        ip="10.0.0.2/32",
        description="a",
    )
    with pytest.raises(ValueError, match="уже есть"):
        add_client_record(
            path,
            name="x",
            filename="client_x2.conf",
            ip="10.0.0.3/32",
            description="b",
        )
