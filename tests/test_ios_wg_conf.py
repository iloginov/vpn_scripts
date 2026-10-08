"""Совместимость client .conf с парсером AmneziaWG iOS."""

from __future__ import annotations

import pytest

from amnezia_export.awg_conf import normalize_client_conf, validate_ios_wg_quick
from vpn_awg.conf import generate_client_config
from vpn_awg.obfs import Awg2ClientObfs, generate_awg2_server_shared_only

_TEST_PRIV = "cFzGNvY8yV1q2m3n4o5p6q7r8s9t0u1v2w3x4y5z6a7b8c9d0e1f2g3="
_TEST_PUB = "5Rj4R73JyvKiAlj1WWTeqP+lP3DB8En71WAWpwWSujE="


def test_normalize_strips_listen_port():
    raw = """\
[Interface]
Address = 10.8.1.2/32
ListenPort = 9999
PrivateKey = aGVsbG8=
Jc = 5

[Peer]
PublicKey = YmFyYmFy=
Endpoint = 1.2.3.4:51820
"""
    out = normalize_client_conf(raw)
    assert "ListenPort" not in out
    validate_ios_wg_quick(out)


def test_normalize_strips_unknown_peer_key():
    raw = """\
[Interface]
Address = 10.8.1.2/32
PrivateKey = aGVsbG8=

[Peer]
PublicKey = YmFyYmFy=
Endpoint = 1.2.3.4:51820
TxBytes = 123
"""
    out = normalize_client_conf(raw)
    assert "TxBytes" not in out


def test_generate_client_config_ios_compatible():
    server = generate_awg2_server_shared_only(1280)
    client_obfs = Awg2ClientObfs(jc=5, jmin=10, jmax=50, i_packets=[])
    conf = generate_client_config(
        server,
        _TEST_PRIV,
        "10.8.1.2/32",
        _TEST_PUB,
        "1.2.3.4:443",
        client_obfs=client_obfs,
    )
    validate_ios_wg_quick(conf)
    assert "ListenPort" not in conf


def test_validate_rejects_extra_interface_key():
    bad = """\
[Interface]
Address = 10.8.1.2/32
FwMark = 0x1234
PrivateKey = aGVsbG8=

[Peer]
PublicKey = YmFyYmFy=
Endpoint = 1.2.3.4:51820
"""
    with pytest.raises(ValueError, match="лишние поля"):
        validate_ios_wg_quick(bad)
