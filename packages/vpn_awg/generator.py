"""
Порт логики AmneziaWG Architect: gen_cfg и протокольные mk*.

Источник: tmp/Any-Tech-ARCHITECT (AmneziaWG 3.1 по умолчанию).
Коллизии S — offsets из messageSizes.ts (INIT_TO_RESPONSE=56, INIT_TO_COOKIE=84,
RESPONSE_TO_COOKIE=28). При HeaderProtectionKey: S1–S4 ≥ 12.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from vpn_awg.awg3 import (
    Awg3Params,
    MIN_S_WITH_HEADER_PROTECTION,
    gen_awg3,
    lift_above_floor,
)
from vpn_awg.gen_config import GenConfig

# WG message size diffs — padded lengths collide when S differ by these.
INIT_TO_RESPONSE = 56  # 148 - 92
INIT_TO_COOKIE = 84  # 148 - 64
RESPONSE_TO_COOKIE = 28  # 92 - 64

_DATA_DIR = Path(__file__).resolve().parent / "data"
_HOST_POOLS_PATH = _DATA_DIR / "host_pools.json"

CHROMIUM_PROFILES = frozenset({"chrome", "edge", "yandex_desktop", "yandex_mobile"})

BFP: dict[str, dict[str, tuple[int, int]]] = {
    "chrome": {
        "qi": (1250, 1250),
        "q0": (1250, 1350),
        "h3": (1250, 1350),
        "tls": (512, 800),
        "nx": (1200, 1250),
        "dtls": (1100, 1200),
    },
    "edge": {
        "qi": (1250, 1250),
        "q0": (1250, 1350),
        "h3": (1250, 1350),
        "tls": (512, 800),
        "nx": (1200, 1250),
        "dtls": (1100, 1200),
    },
    "firefox": {
        "qi": (1200, 1252),
        "q0": (1200, 1300),
        "h3": (1200, 1350),
        "tls": (512, 700),
        "nx": (1200, 1250),
        "dtls": (1050, 1200),
    },
    "safari": {
        "qi": (1250, 1252),
        "q0": (1250, 1300),
        "h3": (1250, 1350),
        "tls": (512, 750),
        "nx": (1200, 1250),
        "dtls": (1100, 1200),
    },
    "yandex_desktop": {
        "qi": (1250, 1250),
        "q0": (1250, 1350),
        "h3": (1350, 1350),
        "tls": (512, 800),
        "nx": (1200, 1250),
        "dtls": (1100, 1200),
    },
    "yandex_mobile": {
        "qi": (1232, 1232),
        "q0": (1250, 1350),
        "h3": (1350, 1350),
        "tls": (512, 800),
        "nx": (1200, 1250),
        "dtls": (1100, 1200),
    },
}

_host_pools_cache: dict[str, list[str]] | None = None


@dataclass
class AwgCfgResult:
    h1: str
    h2: str
    h3: str
    h4: str
    s1: int
    s2: int
    s3: int
    s4: int
    jc: int
    jmin: int
    jmax: int
    i1: str
    i2: str
    i3: str
    i4: str
    i5: str
    mimic: str
    awg3: Awg3Params | None = None


def rnd(a: int, b: int) -> int:
    return a + secrets.randbelow(b - a + 1)


def rh(n: int) -> str:
    n = max(0, int(n))
    return secrets.token_hex(n)


def hex_pad(value: int, byte_len: int) -> str:
    hex_s = f"{int(value):x}"
    while len(hex_s) < byte_len * 2:
        hex_s = "0" + hex_s
    return hex_s[-(byte_len * 2) :]


def assert_even_hex(hex_s: str, label: str = "?") -> str:
    if len(hex_s) % 2 != 0:
        return hex_s + "0"
    return hex_s


def r_range(base: int, spread: int = 500_000, *, max_width: int = 50_000) -> str:
    s = base + rnd(0, spread)
    width_hi = max(1000, int(max_width))
    return f"{s}-{s + rnd(1000, width_hi)}"


def split_pad(n: int, tag: str = "r") -> str:
    n = max(0, int(n))
    if n == 0:
        return ""
    out: list[str] = []
    while n > 1000:
        out.append(f"<{tag} 1000>")
        n -= 1000
    out.append(f"<{tag} {n}>")
    return "".join(out)


def tag_overhead(use_c: bool, use_t: bool) -> int:
    return (4 if use_c else 0) + (4 if use_t else 0)


def calc_padding(
    header_b: int,
    extra_b: int,
    range_: tuple[int, int] | None,
    iv: int,
    mtu: int,
) -> int:
    max_pad = max(0, mtu - header_b - extra_b)
    if range_ is None:
        return min(rnd(20, 80) * iv, 500, max_pad)

    occupied = header_b + extra_b
    lo, hi = range_
    clamped_min = min(lo, mtu)
    clamped_max = min(hi, mtu)
    needed = max(0, clamped_min - occupied)
    jitter = max(
        0,
        min(clamped_max - clamped_min, clamped_max - occupied - needed, 20),
    )
    pad = needed + (rnd(0, jitter) if jitter > 0 else 0)
    return min(pad, max_pad)


def align_to128(n: int) -> int:
    return ((n + 127) // 128) * 128


def load_host_pools() -> dict[str, list[str]]:
    global _host_pools_cache
    if _host_pools_cache is not None:
        return _host_pools_cache
    if not _HOST_POOLS_PATH.is_file():
        raise FileNotFoundError(
            f"Нет {_HOST_POOLS_PATH}. Соберите: python3 tools/build_host_pools.py"
        )
    _host_pools_cache = json.loads(_HOST_POOLS_PATH.read_text(encoding="utf-8"))
    return _host_pools_cache


def get_host(cfg: GenConfig, pool_key: str) -> str:
    if cfg.mimic_host.strip():
        return cfg.mimic_host.strip()
    actual = "dns" if pool_key == "dns_query" else pool_key
    pools = load_host_pools()
    pool = pools.get(actual) or pools.get("tls_client_hello") or []
    if not pool:
        return "yandex.net"
    return pool[rnd(0, len(pool) - 1)]


def get_fp_range(cfg: GenConfig, slot: str) -> tuple[int, int] | None:
    if not cfg.use_browser_fp or not cfg.browser_profile:
        return None
    table = BFP.get(cfg.browser_profile)
    return table.get(slot) if table else None


def _mk_quic_initial(cfg: GenConfig, iv: int) -> str:
    host = get_host(cfg, "quic_initial")
    dcid = rnd(8, 20)
    scid = rnd(0, 20)
    token_len = 0 if rnd(0, 1) == 0 else rnd(8, 32)
    sni_rc = min(len(host) + rnd(0, 6), 64)

    hex_body = assert_even_hex(
        hex_pad(0xC0 | rnd(0, 3), 1)
        + "00000001"
        + hex_pad(dcid, 1)
        + rh(dcid)
        + hex_pad(scid, 1)
        + rh(scid)
        + hex_pad(token_len, 1)
        + rh(token_len)
        + rh(4),
        "mkQUICi",
    )
    header_b = len(hex_body) // 2
    extra_b = (sni_rc if cfg.use_tag_rc else 0) + tag_overhead(
        cfg.use_tag_c, cfg.use_tag_t
    )
    pad = calc_padding(header_b, extra_b, get_fp_range(cfg, "qi"), iv, cfg.mtu)
    return (
        f"<b 0x{hex_body}>"
        + (f"<rc {sni_rc}>" if cfg.use_tag_rc else "")
        + ("<c>" if cfg.use_tag_c else "")
        + ("<t>" if cfg.use_tag_t else "")
        + (split_pad(pad) if cfg.use_tag_r else "")
    )


def _mk_quic_0rtt(cfg: GenConfig, iv: int) -> str:
    host = get_host(cfg, "quic_0rtt")
    dcid = rnd(8, 20)
    scid = rnd(0, 20)
    ticket_hint = min(len(host) + rnd(4, 16), 48)

    hex_body = assert_even_hex(
        hex_pad(0xD0 | rnd(0, 3), 1)
        + "00000001"
        + hex_pad(dcid, 1)
        + rh(dcid)
        + hex_pad(scid, 1)
        + rh(scid)
        + rh(4),
        "mkQUIC0",
    )
    header_b = len(hex_body) // 2
    extra_b = (ticket_hint if cfg.use_tag_rc else 0) + tag_overhead(
        cfg.use_tag_c, cfg.use_tag_t
    )
    pad = calc_padding(header_b, extra_b, get_fp_range(cfg, "q0"), iv, cfg.mtu)
    return (
        f"<b 0x{hex_body}>"
        + ("<t>" if cfg.use_tag_t else "")
        + (split_pad(pad) if cfg.use_tag_r else "")
        + (f"<rc {ticket_hint}>" if cfg.use_tag_rc else "")
        + ("<c>" if cfg.use_tag_c else "")
    )


def _mk_tls(cfg: GenConfig, iv: int) -> str:
    host = get_host(cfg, "tls_client_hello")
    sni_ext = 2 + 2 + 2 + 1 + 2 + len(host)
    sni_rc = min(sni_ext, 64)

    fp = get_fp_range(cfg, "tls")
    base_len = rnd(fp[0], fp[1]) if fp else rnd(300, 550)
    rec_len = align_to128(base_len) if cfg.browser_profile in CHROMIUM_PROFILES else base_len
    hs_len = rec_len - rnd(4, 9)

    r_len = min(
        rnd(20, 60) * iv,
        300,
        max(0, cfg.mtu - 44 - sni_rc - tag_overhead(cfg.use_tag_c, cfg.use_tag_t)),
    )

    hex_body = assert_even_hex(
        "160301"
        + hex_pad(rec_len, 2)
        + "01"
        + hex_pad(hs_len, 3)
        + "0303"
        + rh(32),
        "mkTLS",
    )
    return (
        f"<b 0x{hex_body}>"
        + (f"<rc {sni_rc}>" if cfg.use_tag_rc else "")
        + (split_pad(r_len) if cfg.use_tag_r else "")
        + ("<c>" if cfg.use_tag_c else "")
        + ("<t>" if cfg.use_tag_t else "")
    )


def _mk_noise(cfg: GenConfig, iv: int) -> str:
    rc_len = rnd(4, 12)
    header_b = 148
    extra_b = (rc_len if cfg.use_tag_rc else 0) + tag_overhead(
        cfg.use_tag_c, cfg.use_tag_t
    )
    fp = get_fp_range(cfg, "nx")
    if fp:
        pad = calc_padding(header_b, extra_b, fp, iv, cfg.mtu)
    else:
        pad = min(rnd(10, 40) * iv, 200, max(0, cfg.mtu - header_b - extra_b))
    return (
        f"<b 0x01000000{rh(4)}>"
        f"<b 0x{rh(32)}>"
        f"<b 0x{rh(48)}>"
        f"<b 0x{rh(28)}>"
        f"<b 0x{rh(32)}>"
        + (split_pad(pad) if cfg.use_tag_r else "")
        + ("<t>" if cfg.use_tag_t else "")
        + (f"<rc {rc_len}>" if cfg.use_tag_rc else "")
    )


def _mk_dtls(cfg: GenConfig, iv: int) -> str:
    host = get_host(cfg, "dtls")
    frag_len = rnd(100, 300)
    sni_rc = min(len(host) + rnd(2, 8), 60)
    epoch = rnd(0, 255)

    hex_body = assert_even_hex(
        "16"
        + "fefd"
        + hex_pad(epoch, 2)
        + rh(6)
        + hex_pad(frag_len, 2)
        + "01"
        + rh(6)
        + "fefd0000"
        + rh(4)
        + rh(32),
        "mkDTLS",
    )
    header_b = len(hex_body) // 2
    extra_b = (sni_rc if cfg.use_tag_rc else 0) + tag_overhead(
        cfg.use_tag_c, cfg.use_tag_t
    )
    pad = calc_padding(header_b, extra_b, get_fp_range(cfg, "dtls"), iv, cfg.mtu)
    return (
        f"<b 0x{hex_body}>"
        + (f"<rc {sni_rc}>" if cfg.use_tag_rc else "")
        + ("<c>" if cfg.use_tag_c else "")
        + ("<t>" if cfg.use_tag_t else "")
        + (split_pad(pad) if cfg.use_tag_r else "")
    )


def _mk_http3(cfg: GenConfig, iv: int) -> str:
    host = get_host(cfg, "quic_initial")
    ptypes = [0xC0, 0xC1, 0xC2, 0xC3, 0xE0, 0xE1, 0xE2]
    dcid = rnd(8, 20)
    scid = rnd(0, 20)
    sni_len = min(len(host) + 9 + rnd(0, 6), 64)

    hex_body = assert_even_hex(
        hex_pad(ptypes[rnd(0, len(ptypes) - 1)], 1)
        + "00000001"
        + hex_pad(dcid, 1)
        + rh(dcid)
        + hex_pad(scid, 1)
        + rh(scid)
        + rh(4),
        "mkHTTP3",
    )
    header_b = len(hex_body) // 2
    extra_b = (sni_len if cfg.use_tag_rc else 0) + tag_overhead(
        cfg.use_tag_c, cfg.use_tag_t
    )
    pad = calc_padding(header_b, extra_b, get_fp_range(cfg, "h3"), iv, cfg.mtu)
    return (
        f"<b 0x{hex_body}>"
        + (f"<rc {sni_len}>" if cfg.use_tag_rc else "")
        + (split_pad(pad) if cfg.use_tag_r else "")
        + ("<c>" if cfg.use_tag_c else "")
        + ("<t>" if cfg.use_tag_t else "")
    )


def _mk_sip(cfg: GenConfig, iv: int) -> str:
    host = get_host(cfg, "sip")
    host_hex = "".join(f"{ord(c):02x}" for c in host)
    hex_body = assert_even_hex(
        "5245474953544552207369703a" + host_hex + "20" + rh(4),
        "mkSIP",
    )
    header_b = len(hex_body) // 2
    rc_val = min(len(host) + rnd(8, 24) * iv, 150)
    r_len = min(
        rnd(5, 30) * iv,
        120,
        max(0, cfg.mtu - header_b - rc_val - tag_overhead(cfg.use_tag_c, cfg.use_tag_t)),
    )
    return (
        f"<b 0x{hex_body}>"
        + (f"<rc {rc_val}>" if cfg.use_tag_rc else "")
        + ("<c>" if cfg.use_tag_c else "")
        + ("<t>" if cfg.use_tag_t else "")
        + (split_pad(r_len) if cfg.use_tag_r else "")
    )


def _mk_dns(cfg: GenConfig, iv: int) -> str:
    host = get_host(cfg, "dns_query")
    query_name_hex = ""
    for label in host.split("."):
        query_name_hex += f"{len(label):02x}" + "".join(f"{ord(c):02x}" for c in label)
    query_name_hex += "00"

    qtype = "0001" if iv % 2 == 0 else "001c"
    dns_query_hex = (
        rh(2)
        + "0100"
        + "0001"
        + "0000"
        + "0000"
        + "0000"
        + query_name_hex
        + qtype
        + "0001"
    )
    hex_body = assert_even_hex(dns_query_hex, "mkDNS")
    header_b = len(hex_body) // 2
    target = rnd(64, min(512, cfg.mtu - 20))
    r_len = max(0, target - header_b)
    return (
        f"<b 0x{hex_body}>"
        + (split_pad(min(r_len, 200)) if cfg.use_tag_r and r_len > 0 else "")
        + ("<t>" if cfg.use_tag_t else "")
        + ("<c>" if cfg.use_tag_c else "")
    )


def _mk_entropy(cfg: GenConfig, idx: int, iv: int) -> str:
    is_big = rnd(1, 10) > 6
    base_len = rnd(200, 500) if is_big else rnd(4, 20)
    cap = 500 if is_big else 60
    r_len = min(
        base_len * iv,
        cap,
        max(0, cfg.mtu - 20 - tag_overhead(cfg.use_tag_c, cfg.use_tag_t)),
    )

    rc_len = rnd(4, 12)
    rd_len = rnd(4, 8)
    c = "<c>" if cfg.use_tag_c else ""
    t = "<t>" if cfg.use_tag_t else ""
    r = split_pad(r_len) if cfg.use_tag_r else ""
    rc = f"<rc {rc_len}>" if cfg.use_tag_rc else ""
    rd = f"<rd {rd_len}>" if cfg.use_tag_rd else ""
    b = f"<b 0x{rh(rnd(4, 8 * iv))}>" if iv >= 2 else ""
    b2 = f"<b 0x{rh(rnd(2, 4))}>" if iv >= 3 else ""

    patterns = [
        b + r + t + rc + c + rd,
        c + t + b + r + rc + rd,
        rc + b + r + c + t + rd,
        t + r + c + rc + b + rd,
        r + rc + b + t + c + rd,
        b2 + t + r + b + rc + c + rd,
        rd + b + rc + r + t + c + b2,
        c + b + b2 + t + rc + r + rd,
    ]
    result = patterns[(idx + rnd(0, len(patterns) - 1)) % len(patterns)]
    return result or "<r 10>"


_DISPATCH: dict[str, Callable[[GenConfig, int], str]] = {
    "quic_initial": _mk_quic_initial,
    "quic_0rtt": _mk_quic_0rtt,
    "tls_client_hello": _mk_tls,
    "wireguard_noise": _mk_noise,
    "dtls": _mk_dtls,
    "http3": _mk_http3,
    "sip": _mk_sip,
    "dns_query": _mk_dns,
    "tls_to_quic": _mk_tls,
    "quic_burst": _mk_quic_initial,
}


def gen_i1(cfg: GenConfig, profile: str, iv: int) -> str:
    if profile == "random":
        keys = [k for k in _DISPATCH if k != "random"]
        profile = keys[rnd(0, len(keys) - 1)]
    fn = _DISPATCH.get(profile, _mk_quic_initial)
    return fn(cfg, iv)


def gen_cfg(cfg: GenConfig) -> AwgCfgResult:
    imap = {"low": 1, "medium": 2, "high": 3}
    iv = imap[cfg.intensity] + (1 if cfg.iter_count > 3 else 0)
    profile = cfg.mimic

    h1_spread = 10_000_000 if cfg.use_extreme_max else 100_000_000
    h2_spread = 10_000_000 if cfg.use_extreme_max else 100_000_000
    h3_spread = 10_000_000 if cfg.use_extreme_max else 100_000_000
    h4_spread = 15_000_000 if cfg.use_extreme_max else 150_000_000
    # 3.1 bug workaround: Architect headerZones(narrowH) — ширина окна ~20k/30k.
    h_width = 20_000 if cfg.use_narrow_h else 50_000
    h4_width = 30_000 if cfg.use_narrow_h else 50_000

    h1 = r_range(rnd(100_000_000, 900_000_000), h1_spread, max_width=h_width)
    h2 = r_range(rnd(1_200_000_000, 2_000_000_000), h2_spread, max_width=h_width)
    h3 = r_range(rnd(2_400_000_000, 3_200_000_000), h3_spread, max_width=h_width)
    h4 = r_range(rnd(3_600_000_000, 4_000_000_000), h4_spread, max_width=h4_width)

    s1_high = 20 if cfg.router_mode else 150
    s2_high = 20 if cfg.router_mode else 150
    s1 = rnd(1, s1_high)
    s2 = rnd(1, s2_high)
    for _ in range(20):
        if s2 != s1 + INIT_TO_RESPONSE:
            break
        s2 = rnd(1, s2_high)

    s3_high = 64
    s4_high = 32
    s3 = rnd(1, s3_high)
    for _ in range(20):
        if s3 != s1 + INIT_TO_COOKIE and s3 != s2 + RESPONSE_TO_COOKIE:
            break
        s3 = rnd(1, s3_high)
    s4 = rnd(1, s4_high)

    if cfg.use_extreme_max:
        s3_high = 256
        s4_high = 128
        s3 = rnd(65, s3_high)
        s4 = rnd(33, s4_high)
        for _ in range(20):
            if s3 != s1 + INIT_TO_COOKIE and s3 != s2 + RESPONSE_TO_COOKIE:
                break
            s3 = rnd(65, s3_high)

    needs_s_floor = cfg.use_header_protection
    if needs_s_floor:
        floor = MIN_S_WITH_HEADER_PROTECTION
        s1 = lift_above_floor(s1, floor, s1_high)
        s2 = lift_above_floor(s2, floor, s2_high)
        for _ in range(20):
            if s2 != s1 + INIT_TO_RESPONSE:
                break
            s2 = lift_above_floor(rnd(1, s2_high), floor, s2_high)
        s3 = lift_above_floor(s3, floor, s3_high)
        for _ in range(20):
            if s3 != s1 + INIT_TO_COOKIE and s3 != s2 + RESPONSE_TO_COOKIE:
                break
            s3 = lift_above_floor(rnd(1, s3_high), floor, s3_high)
        s4 = lift_above_floor(s4, floor, s4_high)

    min_jc = 3
    max_jc = 128 if cfg.use_extreme_max else 15
    jcv = cfg.junk_level
    if jcv > 0:
        jcv = max(1, min(max_jc, jcv + rnd(-1, 1)))
    if cfg.use_extreme_max and cfg.junk_level == 0:
        jcv = rnd(1, 8)

    jmin_ranges = {"low": (64, 256), "medium": (128, 512), "high": (256, 768)}
    jmax_ranges = {"low": (256, 512), "medium": (512, 1024), "high": (768, 1280)}
    jmin = rnd(*jmin_ranges[cfg.intensity])
    jmax = rnd(*jmax_ranges[cfg.intensity])
    min_jmax = jmin + 64
    if jmax < min_jmax:
        jmax = min_jmax + rnd(64, 256)

    if cfg.router_mode:
        jcv = max(min_jc, min(jcv, 2))
        jmin = min(jmin, 40)
        jmax = min(jmax, 128)

    i1 = i2 = i3 = i4 = i5 = ""
    is_composite_tls_quic = profile == "tls_to_quic"
    is_composite_burst = profile == "quic_burst"
    is_dns = profile == "dns_query"

    if is_composite_tls_quic:
        i1 = _mk_tls(cfg, iv)
        i2 = _mk_quic_initial(cfg, iv)
        i3 = _mk_entropy(cfg, 2, iv)
        i4 = _mk_entropy(cfg, 3, iv)
        i5 = _mk_entropy(cfg, 4, iv)
    elif is_composite_burst:
        i1 = _mk_quic_initial(cfg, iv)
        i2 = _mk_quic_0rtt(cfg, iv)
        i3 = _mk_http3(cfg, iv)
        i4 = _mk_entropy(cfg, 3, iv)
        i5 = _mk_entropy(cfg, 4, iv)
    elif is_dns:
        i1 = _mk_dns(cfg, iv)
        i2 = _mk_dns(cfg, iv + 1) if cfg.mimic_all else _mk_entropy(cfg, 1, iv)
        i3 = _mk_dns(cfg, iv + 2) if cfg.mimic_all else _mk_entropy(cfg, 2, iv)
        i4 = _mk_dns(cfg, iv + 3) if cfg.mimic_all else _mk_entropy(cfg, 3, iv)
        i5 = _mk_dns(cfg, iv + 4) if cfg.mimic_all else _mk_entropy(cfg, 4, iv)
    else:
        i1 = gen_i1(cfg, profile, iv)
        i2 = gen_i1(cfg, profile, iv) if cfg.mimic_all else _mk_entropy(cfg, 1, iv)
        i3 = gen_i1(cfg, profile, iv) if cfg.mimic_all else _mk_entropy(cfg, 2, iv)
        i4 = gen_i1(cfg, profile, iv) if cfg.mimic_all else _mk_entropy(cfg, 3, iv)
        i5 = gen_i1(cfg, profile, iv) if cfg.mimic_all else _mk_entropy(cfg, 4, iv)

    if cfg.router_mode:
        i2 = i3 = i4 = i5 = ""

    resolved = profile
    if profile == "random":
        resolved = "random"

    awg3 = None
    if (
        cfg.use_header_protection
        or cfg.use_content_padding
        or cfg.use_random_timings
        or cfg.use_random_trailers
        or cfg.use_disable_cookies
    ):
        awg3 = gen_awg3(
            cfg.intensity,
            router_mode=cfg.router_mode,
            use_header_protection=cfg.use_header_protection,
            use_content_padding=cfg.use_content_padding,
            use_random_timings=cfg.use_random_timings,
            use_random_trailers=cfg.use_random_trailers,
            use_disable_cookies=cfg.use_disable_cookies,
        )

    return AwgCfgResult(
        h1=h1,
        h2=h2,
        h3=h3,
        h4=h4,
        s1=s1,
        s2=s2,
        s3=s3,
        s4=s4,
        jc=jcv,
        jmin=jmin,
        jmax=jmax,
        i1=i1,
        i2=i2,
        i3=i3,
        i4=i4,
        i5=i5,
        mimic=resolved,
        awg3=awg3,
    )
