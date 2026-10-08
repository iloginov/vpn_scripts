"""Тесты vpn-to-wg (.vpn → .conf)."""

from __future__ import annotations

import base64
import json
import tempfile
import zlib
from pathlib import Path

from amnezia_export.vpn_payload import read_vpn_payload
from amnezia_export.vpn_to_wg import make_wireguard_config_from_amnezia_json, vpn_to_wg_conf

_WG_TEMPLATE = """\
[Interface]
Address = $CLIENT_IP/32
DNS = $PRIMARY_DNS, $SECONDARY_DNS
PrivateKey = privkey=
Jc = 6

[Peer]
PublicKey = pubkey=
AllowedIPs = 0.0.0.0/0
Endpoint = 1.2.3.4:51820
"""

_LAST_CONFIG = {
    "config": _WG_TEMPLATE,
    "mtu": "1280",
    "port": 51820,
    "clientIp": "10.8.1.2",
    "clientPrivKey": "privkey=",
    "serverPubKey": "pubkey=",
}

_AMNEZIA_JSON = {
    "dns1": "1.1.1.1",
    "dns2": "1.0.0.1",
    "containers": [
        {
            "container": "amnezia-awg",
            "awg": {"last_config": json.dumps(_LAST_CONFIG)},
        }
    ],
}


def _write_vpn(path: Path, payload: dict) -> None:
    raw = json.dumps(payload).encode()
    compressed = len(raw).to_bytes(4, "big") + zlib.compress(raw, level=8)
    enc = base64.urlsafe_b64encode(compressed).decode().rstrip("=")
    path.write_text(f"vpn://{enc}\n", encoding="utf-8")


def test_make_wireguard_config():
    out = make_wireguard_config_from_amnezia_json(json.dumps(_AMNEZIA_JSON))
    assert "1.1.1.1" in out
    assert "MTU = 1280" in out
    assert "ListenPort = 51820" in out
    assert "$PRIMARY_DNS" not in out


def test_vpn_to_wg_conf_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        vpn = Path(tmp) / "client.vpn"
        _write_vpn(vpn, _AMNEZIA_JSON)
        conf = vpn_to_wg_conf(vpn)
        assert conf == vpn.with_suffix(".conf")
        assert "ListenPort = 51820" in conf.read_text(encoding="utf-8")
        assert read_vpn_payload(vpn).decode() == json.dumps(_AMNEZIA_JSON)
