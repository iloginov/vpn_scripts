"""Параметры генерации obfs (профили мимикрии, intensity, junk, AWG 3.1).

Список MIMIC_PROFILES и GenConfig — как в Any-Tech-ARCHITECT.
См. docs/awg2_protocol_params.md и docs/awg3_protocol_params.md.
"""

from __future__ import annotations

from dataclasses import dataclass

MIMIC_PROFILES = (
    "quic_initial",
    "quic_0rtt",
    "tls_client_hello",
    "wireguard_noise",
    "dtls",
    "http3",
    "sip",
    "tls_to_quic",
    "quic_burst",
    "dns_query",
    "random",
)

INTENSITIES = ("low", "medium", "high")
JUNK_LEVELS = (0, 3, 5, 7, 10)

PROFILE_LABELS: dict[str, str] = {
    "quic_initial": "QUIC Initial",
    "quic_0rtt": "QUIC 0-RTT",
    "tls_client_hello": "TLS 1.3",
    "wireguard_noise": "Noise_IK",
    "dtls": "DTLS 1.3",
    "http3": "HTTP/3",
    "sip": "SIP",
    "tls_to_quic": "TLS → QUIC",
    "quic_burst": "QUIC Burst",
    "dns_query": "DNS Query",
    "random": "Random",
}


@dataclass(frozen=True)
class GenConfig:
    mimic: str
    intensity: str
    junk_level: int
    mtu: int
    mimic_host: str = ""
    mimic_all: bool = False
    router_mode: bool = False
    use_extreme_max: bool = False
    iter_count: int = 0
    use_tag_c: bool = False
    use_tag_t: bool = True
    use_tag_r: bool = True
    use_tag_rc: bool = True
    use_tag_rd: bool = True
    use_browser_fp: bool = False
    browser_profile: str = ""
    # AWG 3.0 — по умолчанию как в Architect createDefaults()
    use_header_protection: bool = True
    use_content_padding: bool = True
    use_random_timings: bool = True
    # AWG 3.1 — как self-hosted installer AmneziaVPN (оба on). Architect
    # для DisableCookies осторожнее (NAT keepalive), но свежий клиент
    # считает конфиг устаревшим без protocol_version 3.1.
    use_random_trailers: bool = True
    use_disable_cookies: bool = True
    # Обход бага amneziawg-go 3.1: широкие H1–H4 (до 100M) дорого сканируются.
    use_narrow_h: bool = True

    def __post_init__(self) -> None:
        if self.mimic not in MIMIC_PROFILES:
            raise ValueError(f"Неизвестный mimic: {self.mimic}")
        if self.intensity not in INTENSITIES:
            raise ValueError(f"Неизвестная intensity: {self.intensity}")
        if self.junk_level not in JUNK_LEVELS:
            raise ValueError(f"junk_level должен быть из {JUNK_LEVELS}")
        if not 576 <= self.mtu <= 9000:
            raise ValueError("mtu должен быть в диапазоне 576–9000")
