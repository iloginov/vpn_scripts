"""Тесты packages/amnezia_export."""

from __future__ import annotations

import base64
import struct
import tempfile
from pathlib import Path

from amnezia_export.awg_conf import normalize_client_conf
from amnezia_export.awg_qr import awg_qr_path, write_awg_qr_pngs
from amnezia_export.decode_awg_qr import decode_awg_qr_pngs
from amnezia_export.qr_chunk import (
    CHUNK_SIZE,
    QR_MAGIC,
    build_chunk_strings,
    chunk_b64,
    extract_chunk,
    is_chunked_qr_text,
)
from amnezia_export.qr_io import decode_qr_png, save_qr_png
from amnezia_export.server import (
    enumerate_peer_sections,
    read_wg_conf,
    resolve_server_conf,
    subnet_address_from_server,
)

SAMPLE_CLIENT_CONF = """\
[Interface]
Address = 10.8.1.19/32
DNS = 1.1.1.1, 1.0.0.1
PrivateKey = AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
Jc = 6
Jmin = 10
Jmax = 50
S1 = 144
S2 = 118

[Peer]
PublicKey = BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=
PresharedKey = CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = 203.0.113.10:51820
PersistentKeepalive = 25
"""

MINIMAL_CLIENT_CONF = """\
[Interface]
Address = 10.8.1.2/32
PrivateKey = aGVsbG8=

[Peer]
PublicKey = YmFyYmFy=
Endpoint = 1.2.3.4:51820
"""


def test_chunk_magic_1984():
    s = chunk_b64(b"hello", 0, 1)
    raw = base64.urlsafe_b64decode(s + "==")
    assert struct.unpack(">h", raw[0:2])[0] == QR_MAGIC


def test_read_wg_conf_multiple_postup():
    with tempfile.TemporaryDirectory() as tmp:
        server = Path(tmp) / "server_awg0.conf"
        server.write_text(
            "[Interface]\n"
            "PrivateKey = x\n"
            "Address = 10.10.1.1/24\n"
            "PostUp = first\n"
            "PostUp = second\n"
            "PostDown = down\n"
            "\n"
            "[Peer]\n"
            "PublicKey = aaa\n"
            "AllowedIPs = 10.10.1.2/32\n",
            encoding="utf-8",
        )
        cfg = read_wg_conf(server)
        assert cfg["Interface"]["Address"] == "10.10.1.1/24"
        assert cfg["Interface"]["PostUp"] == "first"
        assert cfg["Interface"]["PostUp_2"] == "second"
        assert cfg["Interface"]["PostDown"] == "down"
        assert subnet_address_from_server(server) == "10.10.1.0"


def test_enumerate_peer_sections():
    with tempfile.TemporaryDirectory() as tmp:
        server = Path(tmp) / "server_awg0.conf"
        server.write_text(
            "[Interface]\n"
            "Address = 10.10.1.1/24\n"
            "\n"
            "[Peer]\n"
            "PublicKey = aaa\n"
            "AllowedIPs = 10.10.1.2/32\n"
            "\n"
            "[Peer]\n"
            "PublicKey = bbb\n"
            "AllowedIPs = 10.10.1.3/32\n",
            encoding="utf-8",
        )
        enumerated = enumerate_peer_sections(server.read_text(encoding="utf-8"))
        assert "[Peer:1]" in enumerated
        assert "[Peer:2]" in enumerated
        assert "\n[Peer]\n" not in enumerated
        cfg = read_wg_conf(server)
        assert cfg["Interface"]["Address"] == "10.10.1.1/24"
        peer_secs = [s for s in cfg.sections() if s.lower().startswith("peer")]
        assert peer_secs == ["Peer:1", "Peer:2"]
        assert subnet_address_from_server(server) == "10.10.1.0"


def test_resolve_server_conf_single():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "server_awg0.conf").write_text("[Interface]\n", encoding="utf-8")
        client = root / "client_1.conf"
        client.write_text(SAMPLE_CLIENT_CONF, encoding="utf-8")
        assert resolve_server_conf(client).name == "server_awg0.conf"


def test_awg_chunk_single():
    body = normalize_client_conf(SAMPLE_CLIENT_CONF).encode()
    chunks = build_chunk_strings(body)
    assert len(chunks) == 1
    raw = base64.urlsafe_b64decode(chunks[0] + "==")
    assert struct.unpack(">h", raw[0:2])[0] == QR_MAGIC


def test_awg_chunk_multi_paths():
    data = b"x" * (CHUNK_SIZE + 1)
    assert len(build_chunk_strings(data)) == 2
    base = Path("client.conf")
    assert awg_qr_path(base, 0, 2).name == "client_awg_qr.png"
    assert awg_qr_path(base, 1, 2).name == "client_awg_qr2.png"


def test_is_chunked_detect():
    assert is_chunked_qr_text(chunk_b64(b"ab", 0, 1))
    assert not is_chunked_qr_text("[Interface]\n")


def test_awg_roundtrip_chunked():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        client = root / "client.conf"
        client.write_text(SAMPLE_CLIENT_CONF, encoding="utf-8")
        expected = normalize_client_conf(SAMPLE_CLIENT_CONF)
        pngs = write_awg_qr_pngs(client, method="chunked")
        assert [p.name for p in pngs] == ["client_awg_qr.png"]
        decoded = decode_awg_qr_pngs(pngs)
        assert len(decoded) == 1
        assert decoded[0].name == "client.awg_qr.decoded"
        assert decoded[0].read_text(encoding="utf-8") == expected


def test_awg_roundtrip_raw():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        client = root / "client.conf"
        client.write_text(MINIMAL_CLIENT_CONF, encoding="utf-8")
        expected = normalize_client_conf(MINIMAL_CLIENT_CONF)
        pngs = write_awg_qr_pngs(client, method="raw")
        assert len(pngs) == 1
        text = decode_qr_png(pngs[0])
        assert not is_chunked_qr_text(text)
        decoded = decode_awg_qr_pngs(pngs)
        assert normalize_client_conf(decoded[0].read_text(encoding="utf-8")) == expected


def test_awg_chunked_multi_decode():
    payload = b"[Interface]\n" + b"z" * CHUNK_SIZE
    chunks = build_chunk_strings(payload)
    assert len(chunks) == 2
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        base = root / "cfg.conf"
        pngs = [awg_qr_path(base, i, len(chunks)) for i in range(len(chunks))]
        for path, ch in zip(pngs, chunks, strict=True):
            save_qr_png(ch, path)
        out = decode_awg_qr_pngs(pngs)
        assert len(out) == 1
        assert out[0].name == "cfg.awg_qr.decoded"
        assert out[0].read_bytes() == payload


def test_awg_chunked_multi_roundtrip():
    """Конфиг >850 байт после normalize → два QR и склейка."""
    long_i1 = "x" * 700
    conf = SAMPLE_CLIENT_CONF.replace(
        "S2 = 118",
        f"S2 = 118\nI1 = {long_i1}",
    )
    normalized = normalize_client_conf(conf)
    assert len(normalized.encode()) > CHUNK_SIZE
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        client = root / "client.conf"
        client.write_text(conf, encoding="utf-8")
        pngs = write_awg_qr_pngs(client, method="chunked")
        assert len(pngs) == 2
        assert pngs[0].name == "client_awg_qr.png"
        assert pngs[1].name == "client_awg_qr2.png"
        decoded = decode_awg_qr_pngs(pngs)
        assert len(decoded) == 1
        assert decoded[0].read_text(encoding="utf-8") == normalized


def test_extract_chunk_roundtrip():
    data = b"payload-bytes"
    text = chunk_b64(data, 0, 1)
    total, index, chunk = extract_chunk(text)
    assert total == 1 and index == 0 and chunk == data
