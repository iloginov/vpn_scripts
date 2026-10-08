"""Тесты AmneziaWG 3.0/3.1 (HPK, CPA, timers, flags, export)."""

from __future__ import annotations

import base64
import json
import tempfile
from pathlib import Path

from amnezia_export.awg_conf import validate_ios_wg_quick
from amnezia_export.vpn import build_vpn_json
from vpn_awg.awg3 import (
    MIN_S_WITH_HEADER_PROTECTION,
    format_awg_bool,
    gen_awg3,
    gen_header_protection_key,
    parse_awg_bool,
    render_awg3_lines,
)
from vpn_awg.conf import generate_client_config
from vpn_awg.gen_config import GenConfig
from vpn_awg.obfs import generate_awg2_server_obfs, render_client_native_obfs_lines


_TEST_PRIV = "cFzGNvY8yV1q2m3n4o5p6q7r8s9t0u1v2w3x4y5z6a7b8c9d0e1f2g3="
_TEST_PUB = "5Rj4R73JyvKiAlj1WWTeqP+lP3DB8En71WAWpwWSujE="


def test_hpk_is_32_byte_base64():
    key = gen_header_protection_key()
    raw = base64.b64decode(key)
    assert len(raw) == 32
    assert key.endswith("=")


def test_parse_awg_bool():
    assert parse_awg_bool("1")
    assert parse_awg_bool("on")
    assert parse_awg_bool("true")
    assert parse_awg_bool("YES")
    assert not parse_awg_bool("0")
    assert not parse_awg_bool("off")
    assert not parse_awg_bool("")
    assert format_awg_bool(True) == "1"
    assert format_awg_bool(False) == "0"


def test_gen_awg3_defaults():
    p = gen_awg3("medium")
    assert p.header_protection_key
    assert "-" in p.content_padding_addition or p.content_padding_addition.isdigit()
    assert p.timers.active()
    assert p.random_trailers
    assert p.disable_cookies


def test_awg31_flag_lines_pair_or_nothing():
    both = "\n".join(
        render_awg3_lines(random_trailers=True, disable_cookies=True)
    )
    assert "RandomTrailers = 1" in both
    assert "DisableCookies = 1" in both

    one = "\n".join(
        render_awg3_lines(random_trailers=True, disable_cookies=False)
    )
    assert "RandomTrailers = 1" in one
    assert "DisableCookies = 0" in one

    off = "\n".join(
        render_awg3_lines(random_trailers=False, disable_cookies=False)
    )
    assert "RandomTrailers" not in off
    assert "DisableCookies" not in off


def test_server_obfs_includes_awg3():
    cfg = GenConfig(mimic="quic_initial", intensity="medium", junk_level=5, mtu=1280)
    server = generate_awg2_server_obfs(cfg)
    assert server.shared.header_protection_key
    assert min(server.shared.s1, server.shared.s2, server.shared.s3, server.shared.s4) >= (
        MIN_S_WITH_HEADER_PROTECTION
    )
    assert server.client_template is not None
    assert server.client_template.content_padding_addition
    assert server.timers.active()
    assert server.client_template.timers.active()
    assert server.random_trailers
    assert server.disable_cookies
    assert server.client_template.random_trailers
    assert server.client_template.disable_cookies
    native = "\n".join(
        render_client_native_obfs_lines(server.shared, server.client_template)
    )
    assert "HeaderProtectionKey =" in native
    assert "ContentPaddingAddition =" in native
    assert "RekeyAfterTime =" in native
    assert "RandomTrailers = 1" in native
    assert "DisableCookies = 1" in native
    validate_ios_wg_quick(
        "[Interface]\n"
        "Address = 10.8.1.2/32\n"
        "DNS = 1.1.1.1, 1.0.0.1\n"
        f"PrivateKey = {_TEST_PRIV}\n"
        + native
        + "\n[Peer]\n"
        f"PublicKey = {_TEST_PUB}\n"
        "AllowedIPs = 0.0.0.0/0, ::/0\n"
        "Endpoint = 1.2.3.4:443\n"
        "PersistentKeepalive = 25\n"
    )


def test_vpn_export_protocol_version_31(monkeypatch):
    cfg = GenConfig(mimic="quic_initial", intensity="low", junk_level=5, mtu=1280)
    server = generate_awg2_server_obfs(cfg)
    conf = generate_client_config(
        server,
        _TEST_PRIV,
        "10.10.2.2/32",
        _TEST_PUB,
        "203.0.113.9:443",
        client_obfs=server.client_template,
        client_mtu=1280,
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        client = root / "client_x.conf"
        client.write_text(conf, encoding="utf-8")
        server_conf = root / "server_awg0.conf"
        server_conf.write_text(
            "[Interface]\n"
            "PrivateKey = x\n"
            "Address = 10.10.2.1/24\n"
            f"HeaderProtectionKey = {server.shared.header_protection_key}\n",
            encoding="utf-8",
        )

        def _fake_pubkey(priv: str) -> str:
            return _TEST_PUB

        monkeypatch.setattr("amnezia_export.vpn._run_wg_pubkey", _fake_pubkey)
        payload = build_vpn_json(client, server_conf=server_conf)
        data = json.loads(payload)
        awg = data["containers"][0]["awg"]
        assert data["containers"][0]["container"] == "amnezia-awg2"
        assert data["defaultContainer"] == "amnezia-awg2"
        assert awg["protocol_version"] == "3.1"
        assert awg["HeaderProtectionKey"] == server.shared.header_protection_key
        assert "ContentPaddingAddition" in awg
        assert awg["RandomTrailers"] == "1"
        assert awg["DisableCookies"] == "1"
        last = json.loads(awg["last_config"])
        assert last["HeaderProtectionKey"] == server.shared.header_protection_key
        assert "HeaderProtectionKey =" in last["config"]
        assert "RandomTrailers = 1" in last["config"]
        assert "DisableCookies = 1" in last["config"]


def test_vpn_export_hpk_only_still_31(monkeypatch):
    """Старый 3.0 без флагов: клиент всё равно ждёт protocol_version=3.1, не 3."""
    cfg = GenConfig(
        mimic="quic_initial",
        intensity="low",
        junk_level=5,
        mtu=1280,
        use_random_trailers=False,
        use_disable_cookies=False,
    )
    server = generate_awg2_server_obfs(cfg)
    conf = generate_client_config(
        server,
        _TEST_PRIV,
        "10.10.2.2/32",
        _TEST_PUB,
        "203.0.113.9:443",
        client_obfs=server.client_template,
        client_mtu=1280,
    )
    assert "RandomTrailers" not in conf
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        client = root / "client_x.conf"
        client.write_text(conf, encoding="utf-8")
        server_conf = root / "server_awg0.conf"
        server_conf.write_text(
            "[Interface]\n"
            "PrivateKey = x\n"
            "Address = 10.10.2.1/24\n"
            f"HeaderProtectionKey = {server.shared.header_protection_key}\n",
            encoding="utf-8",
        )

        monkeypatch.setattr("amnezia_export.vpn._run_wg_pubkey", lambda priv: _TEST_PUB)
        payload = build_vpn_json(client, server_conf=server_conf)
        data = json.loads(payload)
        assert data["containers"][0]["awg"]["protocol_version"] == "3.1"
        assert data["containers"][0]["container"] == "amnezia-awg2"
