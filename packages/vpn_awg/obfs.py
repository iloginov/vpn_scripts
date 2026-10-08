"""Dataclasses, render/parse и генерация obfs через gen_cfg.

gen_cfg и профили мимикрии — порт Any-Tech-ARCHITECT.
Документация: docs/awg2_protocol_params.md, docs/awg3_protocol_params.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from vpn_awg.awg3 import (
    Awg3Timers,
    awg31_flag_items,
    gen_timings,
    parse_awg_bool,
    render_awg3_lines,
)
from vpn_awg.gen_config import GenConfig, PROFILE_LABELS
from vpn_awg.generator import AwgCfgResult, gen_cfg


@dataclass(frozen=True)
class Awg2SharedObfs:
    s1: int
    s2: int
    s3: int
    s4: int
    h1: str
    h2: str
    h3: str
    h4: str
    mtu: int = 1280
    header_protection_key: str = ""

    @property
    def is_awg2(self) -> bool:
        return self.s3 != 0 or self.s4 != 0

    @property
    def is_awg3(self) -> bool:
        return bool(self.header_protection_key)

    def to_dict(self) -> dict:
        d = {
            "mtu": self.mtu,
            "S1": self.s1,
            "S2": self.s2,
            "H1": self.h1,
            "H2": self.h2,
            "H3": self.h3,
            "H4": self.h4,
        }
        if self.is_awg2:
            d["S3"] = self.s3
            d["S4"] = self.s4
        if self.header_protection_key:
            d["HeaderProtectionKey"] = self.header_protection_key
        return d

    @classmethod
    def from_iface(cls, iface: dict) -> Awg2SharedObfs:
        for key in ("S1", "S2", "H1", "H2", "H3", "H4"):
            if key not in iface:
                raise RuntimeError(
                    f"В [Interface] нет параметра {key} (это AmneziaWG-конфиг?)."
                )
        mtu = int(iface.get("MTU", 1280))
        s3 = int(iface["S3"]) if "S3" in iface else 0
        s4 = int(iface["S4"]) if "S4" in iface else 0
        return cls(
            s1=int(iface["S1"]),
            s2=int(iface["S2"]),
            s3=s3,
            s4=s4,
            h1=iface["H1"],
            h2=iface["H2"],
            h3=iface["H3"],
            h4=iface["H4"],
            mtu=mtu,
            header_protection_key=iface.get("HeaderProtectionKey", "").strip(),
        )


@dataclass(frozen=True)
class Awg2ClientObfs:
    jc: int
    jmin: int
    jmax: int
    i_packets: list[str] = field(default_factory=list)
    content_padding_addition: str = ""
    timers: Awg3Timers = field(default_factory=Awg3Timers)
    random_trailers: bool = False
    disable_cookies: bool = False

    def to_dict(self) -> dict[str, int | str]:
        d: dict[str, int | str] = {"Jc": self.jc, "Jmin": self.jmin, "Jmax": self.jmax}
        for i, packet in enumerate(self.i_packets, start=1):
            if packet:
                d[f"I{i}"] = packet
        if self.content_padding_addition:
            d["ContentPaddingAddition"] = self.content_padding_addition
        d.update(self.timers.to_dict())
        for key, value in awg31_flag_items(self.random_trailers, self.disable_cookies):
            d[key] = value
        return d

    @classmethod
    def from_iface(cls, iface: dict, *, required: bool = True) -> Awg2ClientObfs | None:
        if not all(k in iface for k in ("Jc", "Jmin", "Jmax")):
            if required:
                raise RuntimeError(
                    "В [Interface] нет Jc/Jmin/Jmax (клиентский AWG-конфиг?)."
                )
            return None
        i_packets = []
        for n in range(1, 6):
            key = f"I{n}"
            if key in iface and iface[key].strip():
                i_packets.append(iface[key])
        return cls(
            jc=int(iface["Jc"]),
            jmin=int(iface["Jmin"]),
            jmax=int(iface["Jmax"]),
            i_packets=i_packets,
            content_padding_addition=iface.get("ContentPaddingAddition", "").strip(),
            timers=Awg3Timers.from_iface(iface),
            random_trailers=parse_awg_bool(iface.get("RandomTrailers", "")),
            disable_cookies=parse_awg_bool(iface.get("DisableCookies", "")),
        )


@dataclass(frozen=True)
class Awg2ServerObfs:
    shared: Awg2SharedObfs
    client_template: Awg2ClientObfs | None = None
    gen: GenConfig | None = None
    client_dns: str | None = None
    client_server_host: str | None = None
    client_server_name: str | None = None
    timers: Awg3Timers = field(default_factory=Awg3Timers)
    random_trailers: bool = False
    disable_cookies: bool = False

    @property
    def is_awg2(self) -> bool:
        return self.shared.is_awg2 or bool(
            self.client_template and self.client_template.i_packets
        )

    @property
    def is_awg3(self) -> bool:
        client = self.client_template
        return (
            self.shared.is_awg3
            or self.timers.active()
            or self.random_trailers
            or self.disable_cookies
            or bool(
                client
                and (
                    client.content_padding_addition
                    or client.timers.active()
                    or client.random_trailers
                    or client.disable_cookies
                )
            )
        )

    @classmethod
    def from_iface(cls, iface: dict) -> Awg2ServerObfs:
        shared = Awg2SharedObfs.from_iface(iface)
        client_template = Awg2ClientObfs.from_iface(iface, required=False)
        return cls(
            shared=shared,
            client_template=client_template,
            timers=Awg3Timers.from_iface(iface),
            random_trailers=parse_awg_bool(iface.get("RandomTrailers", "")),
            disable_cookies=parse_awg_bool(iface.get("DisableCookies", "")),
        )


@dataclass(frozen=True)
class Awg2Obfs:
    shared: Awg2SharedObfs
    client: Awg2ClientObfs

    @property
    def mtu(self) -> int:
        return self.shared.mtu

    @property
    def is_awg2(self) -> bool:
        return self.shared.is_awg2 or bool(self.client.i_packets)

    def to_dict(self) -> dict:
        return {**self.shared.to_dict(), **self.client.to_dict()}

    @classmethod
    def from_iface(cls, iface: dict) -> Awg2Obfs:
        shared = Awg2SharedObfs.from_iface(iface)
        client = Awg2ClientObfs.from_iface(iface, required=True)
        assert client is not None
        return cls(shared=shared, client=client)


def _cfg_result_to_obfs(
    result: AwgCfgResult, cfg: GenConfig
) -> tuple[Awg2SharedObfs, Awg2ClientObfs, Awg3Timers, bool, bool]:
    awg3 = result.awg3
    hpk = awg3.header_protection_key if awg3 else ""
    cpa = awg3.content_padding_addition if awg3 else ""
    client_timers = awg3.timers if awg3 else Awg3Timers()
    random_trailers = bool(awg3.random_trailers) if awg3 else False
    disable_cookies = bool(awg3.disable_cookies) if awg3 else False
    # Серверные таймеры — отдельная генерация (local scope).
    server_timers = (
        gen_timings(cfg.intensity) if cfg.use_random_timings else Awg3Timers()
    )
    shared = Awg2SharedObfs(
        s1=result.s1,
        s2=result.s2,
        s3=result.s3,
        s4=result.s4,
        h1=result.h1,
        h2=result.h2,
        h3=result.h3,
        h4=result.h4,
        mtu=cfg.mtu,
        header_protection_key=hpk,
    )
    packets = [p for p in (result.i1, result.i2, result.i3, result.i4, result.i5) if p]
    client = Awg2ClientObfs(
        jc=result.jc,
        jmin=result.jmin,
        jmax=result.jmax,
        i_packets=packets,
        content_padding_addition=cpa,
        timers=client_timers,
        random_trailers=random_trailers,
        disable_cookies=disable_cookies,
    )
    return shared, client, server_timers, random_trailers, disable_cookies


def generate_awg2_shared_obfs(cfg: GenConfig) -> Awg2SharedObfs:
    result = gen_cfg(cfg)
    shared, _, _, _, _ = _cfg_result_to_obfs(result, cfg)
    return shared


def generate_awg2_client_obfs(cfg: GenConfig) -> Awg2ClientObfs:
    """Client-only: Jc/I*/CPA/timers/3.1 flags. HPK не генерируется (берётся из shared сервера)."""
    result = gen_cfg(replace(cfg, use_header_protection=False))
    _, client, _, _, _ = _cfg_result_to_obfs(
        result, replace(cfg, use_header_protection=False)
    )
    return client


def generate_awg2_server_obfs(cfg: GenConfig) -> Awg2ServerObfs:
    result = gen_cfg(cfg)
    shared, client, server_timers, random_trailers, disable_cookies = _cfg_result_to_obfs(
        result, cfg
    )
    return Awg2ServerObfs(
        shared=shared,
        client_template=client,
        gen=cfg,
        timers=server_timers,
        random_trailers=random_trailers,
        disable_cookies=disable_cookies,
    )


def generate_awg2_server_shared_only(mtu: int = 1280) -> Awg2ServerObfs:
    """Shared H/S/HPK + server timers/3.1 flags — без client template в .conf."""
    cfg = GenConfig(
        mimic="quic_initial",
        intensity="medium",
        junk_level=5,
        mtu=mtu,
        use_content_padding=False,
    )
    result = gen_cfg(cfg)
    shared, _, server_timers, random_trailers, disable_cookies = _cfg_result_to_obfs(
        result, cfg
    )
    return Awg2ServerObfs(
        shared=shared,
        client_template=None,
        gen=None,
        timers=server_timers,
        random_trailers=random_trailers,
        disable_cookies=disable_cookies,
    )


def client_obfs_from_server(server: Awg2ServerObfs) -> Awg2ClientObfs:
    if server.client_template is not None:
        return server.client_template
    if server.gen is not None:
        return generate_awg2_client_obfs(server.gen)
    raise ValueError(
        "В серверном конфиге нет client-only obfs (Jc/Jmin/Jmax, I1–I5). "
        "Задайте --mimic/--intensity/--junk-level при создании клиента "
        "или добавьте шаблон в server_awg0.conf через init с этими флагами."
    )


def render_shared_obfs_lines(
    shared: Awg2SharedObfs, *, include_mtu: bool = True
) -> list[str]:
    lines: list[str] = []
    if include_mtu and shared.mtu:
        lines.append(f"MTU = {shared.mtu}")
        lines.append("")
    if shared.is_awg3:
        lines.append("# AmneziaWG 3.0 obfuscation (shared)")
    elif shared.is_awg2:
        lines.append("# AmneziaWG 2.0 obfuscation (shared)")
    for key in ("S1", "S2"):
        lines.append(f"{key} = {getattr(shared, key.lower())}")
    if shared.is_awg2:
        lines.append(f"S3 = {shared.s3}")
        lines.append(f"S4 = {shared.s4}")
    lines.append("")
    for key in ("H1", "H2", "H3", "H4"):
        lines.append(f"{key} = {getattr(shared, key.lower())}")
    lines.append("")
    if shared.header_protection_key:
        lines.extend(
            render_awg3_lines(header_protection_key=shared.header_protection_key)
        )
    return lines


def render_client_only_obfs_lines(
    client: Awg2ClientObfs, *, commented: bool = False
) -> list[str]:
    prefix = "# " if commented else ""
    lines: list[str] = []
    if commented:
        lines.append("# Recommended client obfuscation template:")
        lines.append("")
    for key in ("Jc", "Jmin", "Jmax"):
        lines.append(f"{prefix}{key} = {getattr(client, key.lower())}")
    lines.append("")
    if client.i_packets:
        header = "CPS mimicry (client-only)" if commented else "CPS mimicry"
        lines.append(f"# {header}")
        for idx, packet in enumerate(client.i_packets, start=1):
            lines.append(f"{prefix}I{idx} = {packet}")
        lines.append("")
    lines.extend(
        render_awg3_lines(
            content_padding_addition=client.content_padding_addition,
            timers=client.timers,
            random_trailers=client.random_trailers,
            disable_cookies=client.disable_cookies,
            commented=commented,
        )
    )
    return lines


def render_server_obfs_lines(
    server: Awg2ServerObfs, *, include_mtu: bool = True, include_template: bool = True
) -> list[str]:
    lines = render_shared_obfs_lines(server.shared, include_mtu=include_mtu)
    if (
        server.timers.active()
        or server.random_trailers
        or server.disable_cookies
    ):
        lines.extend(
            render_awg3_lines(
                timers=server.timers,
                random_trailers=server.random_trailers,
                disable_cookies=server.disable_cookies,
            )
        )
    if server.client_server_host:
        lines.append(f"# ServerHost = {server.client_server_host}")
        lines.append("")
    if server.client_server_name:
        lines.append(f"# ServerName = {server.client_server_name}")
        lines.append("")
    if server.client_dns:
        lines.append(f"# DNS = {server.client_dns}")
        lines.append("")
    if include_template and server.client_template is not None:
        if server.gen is not None:
            g = server.gen
            lines.append(
                f"# awg-client-defaults: mimic={g.mimic} "
                f"intensity={g.intensity} junk_level={g.junk_level}"
            )
            lines.append("")
        lines.extend(
            render_client_only_obfs_lines(server.client_template, commented=True)
        )
    return lines


def render_client_obfs_lines(
    shared: Awg2SharedObfs,
    client: Awg2ClientObfs,
    *,
    include_mtu: bool = True,
) -> list[str]:
    lines = render_shared_obfs_lines(shared, include_mtu=include_mtu)
    if shared.is_awg3:
        for i, line in enumerate(lines):
            if line == "# AmneziaWG 3.0 obfuscation (shared)":
                lines[i] = "# AmneziaWG 3.0 obfuscation"
                break
    elif shared.is_awg2:
        for i, line in enumerate(lines):
            if line == "# AmneziaWG 2.0 obfuscation (shared)":
                lines[i] = "# AmneziaWG 2.0 obfuscation"
                break
    lines.extend(render_client_only_obfs_lines(client, commented=False))
    return lines


def render_client_native_obfs_lines(
    shared: Awg2SharedObfs, client: Awg2ClientObfs
) -> list[str]:
    """
    Секция [Interface] после PrivateKey — порядок как amneziawg-apple:
    Jc..H, I1–I5, затем AWG 3.x (HPK, CPA, timers, 3.1 flags).
    """
    lines: list[str] = []
    for key in ("Jc", "Jmin", "Jmax"):
        lines.append(f"{key} = {getattr(client, key.lower())}")
    for key in ("S1", "S2", "S3", "S4"):
        if key in ("S3", "S4") and not shared.is_awg2:
            continue
        lines.append(f"{key} = {getattr(shared, key.lower())}")
    for key in ("H1", "H2", "H3", "H4"):
        lines.append(f"{key} = {getattr(shared, key.lower())}")
    for n in range(1, 6):
        if n <= len(client.i_packets):
            lines.append(f"I{n} = {client.i_packets[n - 1]}")
        else:
            lines.append(f"I{n} = ")
    if shared.header_protection_key:
        lines.append(f"HeaderProtectionKey = {shared.header_protection_key}")
    if client.content_padding_addition:
        lines.append(f"ContentPaddingAddition = {client.content_padding_addition}")
    for key, value in client.timers.to_dict().items():
        lines.append(f"{key} = {value}")
    for key, value in awg31_flag_items(client.random_trailers, client.disable_cookies):
        lines.append(f"{key} = {value}")
    return lines


def obfs_summary(obfs: Awg2ServerObfs) -> list[str]:
    lines: list[str] = []
    if obfs.gen:
        g = obfs.gen
        label = PROFILE_LABELS.get(g.mimic, g.mimic)
        lines.append(f"  mimic = {g.mimic} ({label})")
        lines.append(f"  intensity = {g.intensity}")
        lines.append(f"  junk_level = {g.junk_level}")
        if g.mimic_host:
            lines.append(f"  mimic_host = {g.mimic_host}")
        lines.append(f"  header_protection = {g.use_header_protection}")
        lines.append(f"  content_padding = {g.use_content_padding}")
        lines.append(f"  random_timings = {g.use_random_timings}")
        lines.append(f"  random_trailers = {g.use_random_trailers}")
        lines.append(f"  disable_cookies = {g.use_disable_cookies}")
    d = obfs.shared.to_dict()
    if obfs.client_template:
        d.update({f"template_{k}": v for k, v in obfs.client_template.to_dict().items()})
    if obfs.timers.active():
        d.update({f"server_{k}": v for k, v in obfs.timers.to_dict().items()})
    for key, value in awg31_flag_items(obfs.random_trailers, obfs.disable_cookies):
        d[f"server_{key}"] = value
    if d.get("mtu"):
        lines.append(f"  mtu = {d['mtu']}")
    for key in (
        "Jc",
        "Jmin",
        "Jmax",
        "S1",
        "S2",
        "S3",
        "S4",
        "H1",
        "H2",
        "H3",
        "H4",
        "HeaderProtectionKey",
        "ContentPaddingAddition",
        "RekeyAfterTime",
        "RekeyTimeout",
        "RejectAfterTime",
        "KeepaliveTimeout",
        "MaxHandshakeAttempts",
        "RandomTrailers",
        "DisableCookies",
    ):
        for prefix in ("", "template_", "server_"):
            k = f"{prefix}{key}"
            if k in d:
                label = key if not prefix else f"{prefix.rstrip('_')}:{key}"
                val = str(d[k])
                if key == "HeaderProtectionKey" and len(val) > 20:
                    val = val[:12] + "…"
                lines.append(f"  {label} = {val}")
                break
    for key in sorted(k for k in d if k.startswith("I") or k.startswith("template_I")):
        short = key.replace("template_", "")
        val = str(d[key])
        if len(val) > 72:
            val = val[:72] + "…"
        lines.append(f"  {short} = {val}")
    return lines
