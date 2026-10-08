"""Тесты выбора wg / awg для pubkey."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from amnezia_export import wg_tools


@pytest.fixture(autouse=True)
def _clear_pubkey_bin_cache():
    wg_tools.wireguard_pubkey_bin.cache_clear()
    yield
    wg_tools.wireguard_pubkey_bin.cache_clear()


def test_prefers_awg_when_both_in_path():
    with patch("amnezia_export.wg_tools.shutil.which") as which:
        which.side_effect = lambda name: f"/usr/bin/{name}" if name in ("awg", "wg") else None
        assert wg_tools.wireguard_pubkey_bin() == "/usr/bin/awg"


def test_falls_back_to_wg():
    with patch("amnezia_export.wg_tools.shutil.which") as which:
        which.side_effect = lambda name: "/usr/bin/wg" if name == "wg" else None
        assert wg_tools.wireguard_pubkey_bin() == "/usr/bin/wg"


def test_raises_when_missing():
    with patch("amnezia_export.wg_tools.shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="awg.*wg"):
            wg_tools.wireguard_pubkey_bin()


def test_awg_pubkey_bin_env_override(monkeypatch, tmp_path):
    exe = tmp_path / "custom-wg"
    exe.write_text("#!/bin/sh\necho ok\n")
    exe.chmod(0o755)
    monkeypatch.setenv("AWG_PUBKEY_BIN", str(exe))
    with patch("amnezia_export.wg_tools.shutil.which", return_value=None):
        assert wg_tools.wireguard_pubkey_bin() == str(exe)


def test_awg_pubkey_bin_env_name_lookup(monkeypatch):
    monkeypatch.setenv("AWG_PUBKEY_BIN", "wg")
    with patch("amnezia_export.wg_tools.shutil.which", return_value="/usr/bin/wg"):
        assert wg_tools.wireguard_pubkey_bin() == "/usr/bin/wg"
