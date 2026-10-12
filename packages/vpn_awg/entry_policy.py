"""Сборка nft и хуков entry из policy yaml.

Без секции ru_exit — файрвол пары: NAT и MSS только на основной канал.
С секцией — тот же файл плюс awg2 и таблица awg_ru_split.
Скрипт пишет файлы и не вызывает nft.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, summarize_address_range
from pathlib import Path

import yaml

_TOP_KEYS = frozenset({"client", "uplink", "rules", "ru_exit"})
_CLIENT_KEYS = frozenset({"iface", "subnet", "listen_port"})
_CHANNEL_KEYS = frozenset({"iface", "table", "table_id"})
_RULE_KEYS = frozenset({"to_client_priority", "iif_priority", "from_priority"})
_RU_KEYS = frozenset(
    {"iface", "table", "table_id", "fwmark", "priority", "rules_path", "pools"}
)
_POOL_KEYS = frozenset({"combine", "sources"})
_SOURCE_KEYS = frozenset({"name", "url"})
_COMBINE = frozenset({"union", "intersection", "first"})
_IFACE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,14}$")
_TABLE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
_USER_AGENT = "awg-policy"


class PolicyError(Exception):
    pass


@dataclass(frozen=True)
class Channel:
    iface: str
    table: str
    table_id: int


@dataclass(frozen=True)
class PoolSource:
    name: str
    url: str


@dataclass(frozen=True)
class RuExit:
    iface: str
    table: str
    table_id: int
    fwmark: int
    priority: int
    rules_path: str
    combine: str
    sources: tuple[PoolSource, ...]


@dataclass(frozen=True)
class Policy:
    client_iface: str
    client_subnet: IPv4Network
    listen_port: int
    uplink: Channel
    to_client_priority: int
    iif_priority: int
    from_priority: int
    ru_exit: RuExit | None


def load_policy(path: Path) -> Policy:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PolicyError(f"{path}: yaml: {exc}") from exc
    if not isinstance(raw, dict):
        raise PolicyError(f"{path}: в корне нужен словарь")
    return policy_from_dict(raw, origin=str(path))


def policy_from_dict(raw: dict, origin: str = "policy") -> Policy:
    unknown = set(raw) - _TOP_KEYS
    if unknown:
        raise PolicyError(f"{origin}: неизвестные ключи: {', '.join(sorted(unknown))}")
    client = _mapping(raw.get("client"), "client")
    uplink_raw = _mapping(raw.get("uplink"), "uplink")
    _reject_unknown(client, _CLIENT_KEYS, "client")
    _reject_unknown(uplink_raw, _CHANNEL_KEYS, "uplink")

    subnet = _network(_required(client, "subnet", "client"), "client.subnet")
    listen_port = _port(client.get("listen_port", 443), "client.listen_port")
    uplink = Channel(
        iface=_iface(_required(uplink_raw, "iface", "uplink"), "uplink.iface"),
        table=_table(_required(uplink_raw, "table", "uplink"), "uplink.table"),
        table_id=_table_id(_required(uplink_raw, "table_id", "uplink"), "uplink.table_id"),
    )
    client_iface = _iface(_required(client, "iface", "client"), "client.iface")
    if client_iface == uplink.iface:
        raise PolicyError("client.iface и uplink.iface совпадают")

    rules = raw.get("rules") or {}
    if not isinstance(rules, dict):
        raise PolicyError("rules: нужен словарь")
    _reject_unknown(rules, _RULE_KEYS, "rules")
    to_client = _priority(rules.get("to_client_priority", 1000), "rules.to_client_priority")
    iif_prio = _priority(rules.get("iif_priority", 2000), "rules.iif_priority")
    from_prio = _priority(rules.get("from_priority", 2010), "rules.from_priority")

    ru = _parse_ru(raw.get("ru_exit"), uplink.iface, client_iface)
    if ru is not None:
        if uplink.table_id == ru.table_id or uplink.table == ru.table:
            raise PolicyError("таблица uplink и ru_exit совпадают")
        if not (to_client < ru.priority < iif_prio < from_prio):
            raise PolicyError(
                "приоритеты должны идти так: "
                "to_client < ru_exit < iif < from "
                f"({to_client} < {ru.priority} < {iif_prio} < {from_prio})"
            )

    return Policy(
        client_iface=client_iface,
        client_subnet=subnet,
        listen_port=listen_port,
        uplink=uplink,
        to_client_priority=to_client,
        iif_priority=iif_prio,
        from_priority=from_prio,
        ru_exit=ru,
    )


def parse_prefixes(text: str, source: str) -> list[IPv4Network]:
    nets: list[IPv4Network] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            net = IPv4Network(line, strict=True)
        except ValueError as exc:
            raise PolicyError(f"{source}:{lineno}: не сеть IPv4 ({line})") from exc
        if net.prefixlen == 0 or not net.is_global:
            raise PolicyError(
                f"{source}:{lineno}: {net} — default route или непубличная сеть"
            )
        nets.append(net)
    if not nets:
        raise PolicyError(f"{source}: список сетей пуст")
    return nets


def combine_prefixes(
    groups: list[list[IPv4Network]],
    mode: str,
) -> list[IPv4Network]:
    if mode not in _COMBINE:
        raise PolicyError(f"pools.combine: {mode}")
    if not groups:
        raise PolicyError("пулы пусты")
    ranges = [_merge_ranges(_ranges(group)) for group in groups]
    if mode == "first":
        chosen = ranges[0]
    elif mode == "union":
        chosen = _merge_ranges([span for group in ranges for span in group])
    else:
        chosen = ranges[0]
        for other in ranges[1:]:
            chosen = _intersect_ranges(chosen, other)
    if not chosen:
        raise PolicyError("после combine список сетей пуст")
    nets: list[IPv4Network] = []
    for start, end in chosen:
        nets.extend(summarize_address_range(IPv4Address(start), IPv4Address(end)))
    return nets


def render_nftables(policy: Policy) -> str:
    nat = [
        f'        ip saddr {policy.client_subnet} oifname "{policy.uplink.iface}" masquerade'
    ]
    mss_ifaces = [policy.uplink.iface, policy.client_iface]
    include = ""
    if policy.ru_exit is not None:
        nat.append(
            f'        ip saddr {policy.client_subnet} oifname "{policy.ru_exit.iface}" masquerade'
        )
        mss_ifaces.append(policy.ru_exit.iface)
        include = f'\ninclude "{policy.ru_exit.rules_path}"\n'
    mss = "\n\n".join(_mss_block(iface) for iface in mss_ifaces)
    return f"""#!/usr/sbin/nft -f

flush ruleset

table inet filter {{
    chain input {{
        type filter hook input priority filter; policy drop;

        iif lo accept
        ct state established,related accept

        tcp dport 22 accept
        udp dport {policy.listen_port} accept

        ip protocol icmp accept
        meta l4proto ipv6-icmp accept
    }}

    chain forward {{
        type filter hook forward priority filter; policy accept;
        ct state established,related accept
    }}

    chain output {{
        type filter hook output priority filter; policy accept;
    }}
}}

table ip nat {{
    chain postrouting {{
        type nat hook postrouting priority srcnat; policy accept;
{chr(10).join(nat)}
    }}
}}

table ip mangle {{
    chain forward {{
        type filter hook forward priority mangle; policy accept;

{mss}
    }}
}}
{include}"""


def render_ru_table(policy: Policy, nets: list[IPv4Network]) -> str:
    ru = policy.ru_exit
    if ru is None:
        raise PolicyError("таблица сплита есть только при ru_exit")
    elements = ",\n            ".join(str(net) for net in nets)
    mark = hex(ru.fwmark)
    return f"""table ip awg_ru_split {{
    set ru_nets {{
        type ipv4_addr
        flags interval
        auto-merge
        elements = {{
            {elements}
        }}
    }}

    chain prerouting {{
        type filter hook prerouting priority mangle; policy accept;
        iifname "{policy.client_iface}" ip saddr {policy.client_subnet} ip daddr @ru_nets counter meta mark set meta mark | {mark}
    }}

    chain forward_guard {{
        type filter hook forward priority filter - 10; policy accept;
        iifname "{policy.client_iface}" ip saddr {policy.client_subnet} ip daddr @ru_nets oifname != "{ru.iface}" counter drop
    }}
}}
"""


def render_awg0_hooks(policy: Policy) -> str:
    subnet = policy.client_subnet
    table = policy.uplink.table
    iface = policy.client_iface
    to_prio = policy.to_client_priority
    if policy.ru_exit is None:
        return (
            f"PostUp = ip rule add from {subnet} table {table} 2>/dev/null || true\n"
            f"PostUp = ip rule add iif {iface} table {table} 2>/dev/null || true\n"
            f"PostUp = ip rule add to {subnet} table main prio {to_prio} 2>/dev/null || true\n"
            "\n"
            f"PostDown = ip rule del from {subnet} table {table} 2>/dev/null || true\n"
            f"PostDown = ip rule del iif {iface} table {table} 2>/dev/null || true\n"
            f"PostDown = ip rule del to {subnet} table main prio {to_prio} 2>/dev/null || true\n"
        )
    ru = policy.ru_exit
    mark = hex(ru.fwmark)
    return (
        f"PostUp = ip rule add from {subnet} table {table} prio {policy.from_priority} 2>/dev/null || true\n"
        f"PostUp = ip rule add iif {iface} table {table} prio {policy.iif_priority} 2>/dev/null || true\n"
        f"PostUp = ip rule add to {subnet} table main prio {to_prio} 2>/dev/null || true\n"
        f"PostUp = ip rule add fwmark {mark}/{mark} iif {iface} lookup {ru.table} prio {ru.priority} 2>/dev/null || true\n"
        "\n"
        f"PostDown = ip rule del from {subnet} table {table} prio {policy.from_priority} 2>/dev/null || true\n"
        f"PostDown = ip rule del iif {iface} table {table} prio {policy.iif_priority} 2>/dev/null || true\n"
        f"PostDown = ip rule del to {subnet} table main prio {to_prio} 2>/dev/null || true\n"
        f"PostDown = ip rule del fwmark {mark}/{mark} iif {iface} lookup {ru.table} prio {ru.priority} 2>/dev/null || true\n"
    )


def render_route_hooks(iface: str, table: str) -> str:
    return (
        f"PostUp = ip route add default dev {iface} table {table} 2>/dev/null || true\n"
        f"PostDown = ip route del default dev {iface} table {table} 2>/dev/null || true\n"
    )


def fetch_text(url: str, timeout: float = 60) -> str:
    if url.startswith("file:"):
        return _read_url(url, timeout)
    last: Exception | None = None
    for attempt in range(3):
        try:
            return _read_url(url, timeout)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            if attempt == 2:
                break
    raise PolicyError(f"не скачать {url}: {last}")


def fetch_pools(policy: Policy, fetch=fetch_text) -> list[tuple[str, list[IPv4Network]]]:
    ru = policy.ru_exit
    if ru is None:
        return []
    loaded: list[tuple[str, list[IPv4Network]]] = []
    for source in ru.sources:
        text = fetch(source.url)
        loaded.append((source.name, parse_prefixes(text, source.name)))
    return loaded


def render_files(
    policy: Policy,
    pools: list[tuple[str, list[IPv4Network]]],
) -> dict[str, str]:
    files = {
        "nftables.conf": render_nftables(policy),
        "hooks/awg0.hooks": render_awg0_hooks(policy),
        "hooks/awg1.hooks": render_route_hooks(policy.uplink.iface, policy.uplink.table),
    }
    ru = policy.ru_exit
    if ru is None:
        return files
    groups = [nets for _name, nets in pools]
    combined = combine_prefixes(groups, ru.combine)
    files["rules.nft"] = render_ru_table(policy, combined)
    files["hooks/awg2.hooks"] = render_route_hooks(ru.iface, ru.table)
    files["pools/combined.txt"] = _prefix_text(combined)
    for name, nets in pools:
        files[f"pools/{_pool_filename(name)}.txt"] = _prefix_text(combine_prefixes([nets], "first"))
    return files


def write_render(dest: Path, files: dict[str, str]) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    written = set(files)
    for rel, text in files.items():
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for rel in (
        "nftables.conf",
        "rules.nft",
        "hooks/awg0.hooks",
        "hooks/awg1.hooks",
        "hooks/awg2.hooks",
    ):
        path = dest / rel
        if rel not in written and path.is_file():
            path.unlink()
    pools_dir = dest / "pools"
    if pools_dir.is_dir():
        for child in pools_dir.iterdir():
            if child.is_file() and f"pools/{child.name}" not in written:
                child.unlink()
        if not any(pools_dir.iterdir()):
            pools_dir.rmdir()


def _parse_ru(raw: object, uplink_iface: str, client_iface: str) -> RuExit | None:
    if raw is None:
        return None
    data = _mapping(raw, "ru_exit")
    _reject_unknown(data, _RU_KEYS, "ru_exit")
    iface = _iface(_required(data, "iface", "ru_exit"), "ru_exit.iface")
    if iface in {uplink_iface, client_iface}:
        raise PolicyError("ru_exit.iface совпадает с client или uplink")
    pools = _mapping(_required(data, "pools", "ru_exit"), "ru_exit.pools")
    _reject_unknown(pools, _POOL_KEYS, "ru_exit.pools")
    combine = pools.get("combine", "union")
    if combine not in _COMBINE:
        raise PolicyError(f"ru_exit.pools.combine: {combine}")
    sources_raw = pools.get("sources")
    if not isinstance(sources_raw, list) or not sources_raw:
        raise PolicyError("ru_exit.pools.sources: нужен непустой список")
    sources: list[PoolSource] = []
    names: set[str] = set()
    for index, item in enumerate(sources_raw):
        source = _mapping(item, f"ru_exit.pools.sources[{index}]")
        _reject_unknown(source, _SOURCE_KEYS, f"ru_exit.pools.sources[{index}]")
        name = str(_required(source, "name", f"sources[{index}]")).strip()
        url = str(_required(source, "url", f"sources[{index}]")).strip()
        if not name or name in names:
            raise PolicyError(f"sources[{index}].name: пустое или повтор")
        if not url:
            raise PolicyError(f"sources[{index}].url: пусто")
        names.add(name)
        sources.append(PoolSource(name=name, url=url))
    rules_path = str(data.get("rules_path", "/etc/awg-ru-split/rules.nft"))
    if not rules_path.startswith("/") or '"' in rules_path or "\n" in rules_path:
        raise PolicyError("ru_exit.rules_path: нужен абсолютный путь без кавычек")
    return RuExit(
        iface=iface,
        table=_table(_required(data, "table", "ru_exit"), "ru_exit.table"),
        table_id=_table_id(_required(data, "table_id", "ru_exit"), "ru_exit.table_id"),
        fwmark=_fwmark(_required(data, "fwmark", "ru_exit"), "ru_exit.fwmark"),
        priority=_priority(_required(data, "priority", "ru_exit"), "ru_exit.priority"),
        rules_path=rules_path,
        combine=str(combine),
        sources=tuple(sources),
    )


def _mss_block(iface: str) -> str:
    return (
        f'        oifname "{iface}" tcp flags syn tcp option maxseg size set rt mtu\n'
        f'        iifname "{iface}" tcp flags syn tcp option maxseg size set rt mtu'
    )


def _prefix_text(nets: list[IPv4Network]) -> str:
    return "".join(f"{net}\n" for net in nets)


def _pool_filename(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)
    if not cleaned:
        raise PolicyError("пустое имя пула")
    return cleaned


def _ranges(nets: list[IPv4Network]) -> list[tuple[int, int]]:
    return [(int(net.network_address), int(net.broadcast_address)) for net in nets]


def _merge_ranges(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if not merged or start > merged[-1][1] + 1:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def _intersect_ranges(
    left: list[tuple[int, int]],
    right: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    i = 0
    j = 0
    while i < len(left) and j < len(right):
        start = max(left[i][0], right[j][0])
        end = min(left[i][1], right[j][1])
        if start <= end:
            out.append((start, end))
        if left[i][1] < right[j][1]:
            i += 1
        else:
            j += 1
    return out


def _read_url(url: str, timeout: float) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def _mapping(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise PolicyError(f"{label}: нужен словарь")
    return value


def _required(data: dict, key: str, label: str) -> object:
    if key not in data:
        raise PolicyError(f"{label}: нет {key}")
    return data[key]


def _reject_unknown(data: dict, allowed: frozenset[str], label: str) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise PolicyError(f"{label}: неизвестные ключи: {', '.join(sorted(unknown))}")


def _iface(value: object, label: str) -> str:
    text = str(value)
    if _IFACE.fullmatch(text) is None:
        raise PolicyError(f"{label}: недопустимое имя интерфейса")
    return text


def _table(value: object, label: str) -> str:
    text = str(value)
    if text in {"local", "main", "default", "unspec"} or _TABLE.fullmatch(text) is None:
        raise PolicyError(f"{label}: недопустимое имя таблицы")
    return text


def _table_id(value: object, label: str) -> int:
    number = _int(value, label)
    if not 1 <= number <= 252:
        raise PolicyError(f"{label}: id таблицы 1..252")
    return number


def _priority(value: object, label: str) -> int:
    number = _int(value, label)
    if not 1 <= number <= 32765:
        raise PolicyError(f"{label}: приоритет 1..32765")
    return number


def _port(value: object, label: str) -> int:
    number = _int(value, label)
    if not 1 <= number <= 65535:
        raise PolicyError(f"{label}: порт 1..65535")
    return number


def _fwmark(value: object, label: str) -> int:
    if isinstance(value, str):
        try:
            number = int(value, 0)
        except ValueError as exc:
            raise PolicyError(f"{label}: не метка") from exc
    else:
        number = _int(value, label)
    if not 1 <= number <= 0xFFFFFFFF:
        raise PolicyError(f"{label}: метка вне диапазона")
    return number


def _network(value: object, label: str) -> IPv4Network:
    try:
        net = IPv4Network(str(value), strict=True)
    except ValueError as exc:
        raise PolicyError(f"{label}: не сеть IPv4") from exc
    if net.prefixlen == 0:
        raise PolicyError(f"{label}: default route нельзя")
    return net


def _int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PolicyError(f"{label}: нужно целое число")
    return value
