"""Генерация параметров AmneziaWG 3.x (порт Architect generator/awg3.ts).

Источник: amneziawg-go v3.0.1 / v3.1, amneziawg-tools —
HeaderProtectionKey, ContentPaddingAddition, рандомизированные таймеры,
RandomTrailers, DisableCookies.
При HPK: S1–S4 ≥ MIN_S_WITH_HEADER_PROTECTION (12).
"""

from __future__ import annotations

import base64
import secrets
from dataclasses import dataclass, field, fields

HEADER_PROTECTION_KEY_BYTES = 32
MIN_S_WITH_HEADER_PROTECTION = 12

AWG3_CONF_KEYS = (
    "HeaderProtectionKey",
    "ContentPaddingAddition",
    "RekeyAfterTime",
    "RekeyTimeout",
    "RejectAfterTime",
    "KeepaliveTimeout",
    "MaxHandshakeAttempts",
    "RandomTrailers",
    "DisableCookies",
)

AWG31_FLAG_KEYS = ("RandomTrailers", "DisableCookies")
_AWG_BOOL_TRUTHY = frozenset({"true", "yes", "on", "1"})

AWG3_TIMER_KEYS = (
    "RekeyAfterTime",
    "RekeyTimeout",
    "RejectAfterTime",
    "KeepaliveTimeout",
    "MaxHandshakeAttempts",
)


def _rnd(a: int, b: int) -> int:
    return a + secrets.randbelow(b - a + 1)


def _range(lo: int, hi: int) -> str:
    a = max(0, int(lo))
    b = max(a, int(hi))
    return f"{a}" if a == b else f"{a}-{b}"


def parse_awg_bool(value: str) -> bool:
    """Как parse_bool в amneziawg-tools / Architect parse.ts (true|yes|on|1)."""
    return value.strip().lower() in _AWG_BOOL_TRUTHY


def format_awg_bool(value: bool) -> str:
    """1/0 — единственная форма, которую принимают и tools, и go."""
    return "1" if value else "0"


def awg31_flag_items(
    random_trailers: bool, disable_cookies: bool
) -> list[tuple[str, str]]:
    """Пара ключей 3.1. Tools требуют обе строки, если хотя бы одна включена."""
    if not random_trailers and not disable_cookies:
        return []
    return [
        ("RandomTrailers", format_awg_bool(random_trailers)),
        ("DisableCookies", format_awg_bool(disable_cookies)),
    ]


@dataclass(frozen=True)
class Awg3Timers:
    rekey_after_time: str = ""
    rekey_timeout: str = ""
    reject_after_time: str = ""
    keepalive_timeout: str = ""
    max_handshake_attempts: str = ""

    def active(self) -> bool:
        return any(getattr(self, f.name) for f in fields(self))

    def to_dict(self) -> dict[str, str]:
        mapping = {
            "rekey_after_time": "RekeyAfterTime",
            "rekey_timeout": "RekeyTimeout",
            "reject_after_time": "RejectAfterTime",
            "keepalive_timeout": "KeepaliveTimeout",
            "max_handshake_attempts": "MaxHandshakeAttempts",
        }
        return {mapping[f.name]: getattr(self, f.name) for f in fields(self) if getattr(self, f.name)}

    @classmethod
    def from_iface(cls, iface: dict) -> Awg3Timers:
        return cls(
            rekey_after_time=iface.get("RekeyAfterTime", "").strip(),
            rekey_timeout=iface.get("RekeyTimeout", "").strip(),
            reject_after_time=iface.get("RejectAfterTime", "").strip(),
            keepalive_timeout=iface.get("KeepaliveTimeout", "").strip(),
            max_handshake_attempts=iface.get("MaxHandshakeAttempts", "").strip(),
        )


@dataclass(frozen=True)
class Awg3Params:
    """Полный блок 3.x (как в Architect). Scope разносится при записи в conf."""

    header_protection_key: str = ""
    content_padding_addition: str = ""
    timers: Awg3Timers = field(default_factory=Awg3Timers)
    random_trailers: bool = False
    disable_cookies: bool = False

    def active(self) -> bool:
        return bool(
            self.header_protection_key
            or self.content_padding_addition
            or self.timers.active()
            or self.random_trailers
            or self.disable_cookies
        )

    def has_feature_flags(self) -> bool:
        return self.random_trailers or self.disable_cookies


def gen_header_protection_key() -> str:
    raw = secrets.token_bytes(HEADER_PROTECTION_KEY_BYTES)
    return base64.b64encode(raw).decode("ascii")


def gen_content_padding(intensity: str, *, router_mode: bool = False) -> str:
    if router_mode:
        return _range(4, 32)
    spans = {"low": (8, 64), "medium": (16, 128), "high": (24, 200)}
    lo, hi = spans[intensity]
    minimum = _rnd(lo, (lo + hi) // 2)
    return _range(minimum, _rnd(minimum + 8, hi))


def gen_timings(intensity: str) -> Awg3Timers:
    spread = {"low": 10, "medium": 25, "high": 45}[intensity]
    rekey_timeout_lo = _rnd(4, 6)
    rekey_timeout_hi = rekey_timeout_lo + _rnd(1, 4)
    keepalive_lo = _rnd(8, 14)
    keepalive_hi = keepalive_lo + _rnd(2, 8)
    rekey_after_lo = _rnd(100, 120)
    rekey_after_hi = rekey_after_lo + _rnd(10, spread)
    reject_floor = rekey_after_hi + keepalive_hi + rekey_timeout_hi + 15
    reject_lo = max(170, reject_floor)
    reject_hi = reject_lo + _rnd(10, spread)
    attempts_lo = _rnd(12, 18)
    attempts_hi = attempts_lo + _rnd(2, 10)
    return Awg3Timers(
        rekey_after_time=_range(rekey_after_lo, rekey_after_hi),
        rekey_timeout=_range(rekey_timeout_lo, rekey_timeout_hi),
        reject_after_time=_range(reject_lo, reject_hi),
        keepalive_timeout=_range(keepalive_lo, keepalive_hi),
        max_handshake_attempts=_range(attempts_lo, attempts_hi),
    )


def gen_awg3(
    intensity: str,
    *,
    router_mode: bool = False,
    use_header_protection: bool = True,
    use_content_padding: bool = True,
    use_random_timings: bool = True,
    use_random_trailers: bool = True,
    use_disable_cookies: bool = True,
) -> Awg3Params:
    return Awg3Params(
        header_protection_key=gen_header_protection_key() if use_header_protection else "",
        content_padding_addition=(
            gen_content_padding(intensity, router_mode=router_mode) if use_content_padding else ""
        ),
        timers=gen_timings(intensity) if use_random_timings else Awg3Timers(),
        random_trailers=use_random_trailers,
        disable_cookies=use_disable_cookies,
    )


def lift_above_floor(value: int, floor: int, high: int) -> int:
    """Перерисовка из [floor, high], не clamp (иначе все S=12 — отпечаток)."""
    if value >= floor:
        return value
    if high < floor:
        return floor
    return _rnd(floor, high)


def render_awg3_lines(
    *,
    header_protection_key: str = "",
    content_padding_addition: str = "",
    timers: Awg3Timers | None = None,
    random_trailers: bool = False,
    disable_cookies: bool = False,
    commented: bool = False,
) -> list[str]:
    prefix = "# " if commented else ""
    lines: list[str] = []
    if header_protection_key:
        if not commented:
            lines.append("# AmneziaWG 3.0 header protection (shared)")
        lines.append(f"{prefix}HeaderProtectionKey = {header_protection_key}")
        lines.append("")
    if content_padding_addition:
        if commented:
            lines.append("# Recommended client ContentPaddingAddition:")
        else:
            lines.append("# AmneziaWG 3.0 content padding (sender)")
        lines.append(f"{prefix}ContentPaddingAddition = {content_padding_addition}")
        lines.append("")
    timers = timers or Awg3Timers()
    if timers.active():
        if commented:
            lines.append("# Recommended client timers:")
        else:
            lines.append("# AmneziaWG 3.0 randomised timers (local)")
        for key, value in timers.to_dict().items():
            lines.append(f"{prefix}{key} = {value}")
        lines.append("")
    flag_items = awg31_flag_items(random_trailers, disable_cookies)
    if flag_items:
        if not commented:
            lines.append("# AmneziaWG 3.1 feature flags (local)")
        for key, value in flag_items:
            lines.append(f"{prefix}{key} = {value}")
        lines.append("")
    return lines
