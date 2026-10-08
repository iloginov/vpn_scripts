"""Тесты генератора obfs (порт AmneziaWG-Architect, см. packages/vpn_awg/generator.py)."""

from __future__ import annotations

import random

import pytest

from vpn_awg.gen_config import GenConfig
from vpn_awg.generator import gen_cfg, split_pad
from vpn_awg.obfs import generate_awg2_server_obfs


def test_split_pad_500():
    assert split_pad(500) == "<r 500>"


def test_split_pad_2500():
    assert split_pad(2500) == "<r 1000><r 1000><r 500>"


def test_gen_cfg_s_uniqueness():
    random.seed(42)
    r = gen_cfg(
        GenConfig(mimic="quic_initial", intensity="medium", junk_level=5, mtu=1500)
    )
    from vpn_awg.generator import INIT_TO_RESPONSE, INIT_TO_COOKIE, RESPONSE_TO_COOKIE

    assert r.s2 != r.s1 + INIT_TO_RESPONSE
    assert r.s3 != r.s1 + INIT_TO_COOKIE
    assert r.s3 != r.s2 + RESPONSE_TO_COOKIE
    assert r.jmax >= r.jmin + 64
    assert min(r.s1, r.s2, r.s3, r.s4) >= 12
    assert r.awg3 is not None
    assert r.awg3.header_protection_key
    assert r.awg3.random_trailers
    assert r.awg3.disable_cookies
    lo, hi = (int(x) for x in r.h1.split("-", 1))
    assert 0 < hi - lo <= 20_000
    lo4, hi4 = (int(x) for x in r.h4.split("-", 1))
    assert 0 < hi4 - lo4 <= 30_000


def test_intensity_affects_junk_ranges():
    random.seed(10)
    low = gen_cfg(
        GenConfig(mimic="quic_initial", intensity="low", junk_level=5, mtu=1500)
    )
    random.seed(10)
    high = gen_cfg(
        GenConfig(mimic="quic_initial", intensity="high", junk_level=5, mtu=1500)
    )
    assert high.jmin >= low.jmin or high.jmax >= low.jmax
    assert high.i2 and high.i3


def test_mimic_host_override():
    random.seed(0)
    r = gen_cfg(
        GenConfig(
            mimic="sip",
            intensity="medium",
            junk_level=5,
            mtu=1280,
            mimic_host="sip.example.test",
        )
    )
    assert "sip.example.test" in r.i1.lower() or "736970" in r.i1  # sip hex


def test_server_obfs_smoke():
    random.seed(99)
    cfg = GenConfig(mimic="tls_client_hello", intensity="low", junk_level=3, mtu=1280)
    server = generate_awg2_server_obfs(cfg)
    assert server.shared.is_awg2
    assert server.client_template is not None
    assert server.client_template.jc >= 1
    assert len(server.client_template.i_packets) >= 1
