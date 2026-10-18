"""Сборка nft и хуков entry из policy yaml.

Без секции ru_exit — файрвол пары: NAT и MSS только на основной канал.
С секцией — тот же файл плюс awg2 и таблица awg_ru_split.
Скрипт пишет файлы и не вызывает nft.

Список российских сетей: база (страна из ipverse) плюс добавления минус исключения;
правки лежат в split-overrides.yaml рядом с policy yaml. Скачивание (fetch) и
сборка (render) разделены: сборка идёт из кеша и не ходит в сеть.
"""

from __future__ import annotations

import re
import shutil
import time
import urllib.error
import urllib.request
from bisect import bisect_right
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, summarize_address_range
from pathlib import Path

import yaml

SCHEMA_VERSION = 2
OVERRIDES_FILE = "split-overrides.yaml"
MAX_OVERRIDE_ITEMS = 400
_TOP_KEYS = frozenset({"schema", "client", "uplink", "rules", "ru_exit"})
_CLIENT_KEYS = frozenset({"iface", "subnet", "listen_port"})
_CHANNEL_KEYS = frozenset({"iface", "table", "table_id"})
_RULE_KEYS = frozenset({"to_client_priority", "iif_priority", "from_priority"})
_RU_KEYS = frozenset({"iface", "table", "table_id", "fwmark", "priority", "rules_path"})
_OVERRIDE_KEYS = frozenset({"schema", "base", "include", "exclude"})
_BASE_KEYS = frozenset({"type", "code"})
_BASE_TYPES = frozenset({"country", "geo"})
_COUNTRY = re.compile(r"^[a-z]{2}$")
_AS_ITEM = re.compile(r"^AS([0-9]{1,10})$", re.IGNORECASE)
_IPVERSE = "https://raw.githubusercontent.com/ipverse"
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
class Base:
    type: str
    code: str

    def label(self) -> str:
        return f"{self.type}:{self.code}"


@dataclass(frozen=True)
class Overrides:
    base: Base
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()


DEFAULT_OVERRIDES = Overrides(base=Base("country", "ru"))


@dataclass(frozen=True)
class RuExit:
    iface: str
    table: str
    table_id: int
    fwmark: int
    priority: int
    rules_path: str


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
    schema = raw.get("schema")
    if schema != SCHEMA_VERSION:
        raise PolicyError(
            f"{origin}: schema {schema if schema is not None else 'нет'}, "
            f"рендерер ждёт {SCHEMA_VERSION} — запишите entry.yaml заново"
        )
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
    """Список сетей источника: строки на # и IPv6 пропускаются, остальное — публичный IPv4 CIDR."""
    nets: list[IPv4Network] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line or ":" in line:
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


def overrides_from_dict(raw: object, origin: str = OVERRIDES_FILE) -> Overrides:
    data = _mapping(raw, origin)
    _reject_unknown(data, _OVERRIDE_KEYS, origin)
    if data.get("schema", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise PolicyError(f"{origin}: schema {data.get('schema')}, рендерер ждёт {SCHEMA_VERSION}")
    base = DEFAULT_OVERRIDES.base
    if "base" in data:
        braw = _mapping(data["base"], f"{origin}: base")
        _reject_unknown(braw, _BASE_KEYS, f"{origin}: base")
        kind = str(braw.get("type", base.type))
        code = str(braw.get("code", base.code))
        if kind not in _BASE_TYPES:
            raise PolicyError(f"{origin}: base.type: country или geo, получено {kind!r}")
        if _COUNTRY.fullmatch(code) is None:
            raise PolicyError(f"{origin}: base.code: две строчные латинские буквы, получено {code!r}")
        base = Base(kind, code)
    include = _override_items(data.get("include"), f"{origin}: include")
    exclude = _override_items(data.get("exclude"), f"{origin}: exclude")
    if len(include) + len(exclude) > MAX_OVERRIDE_ITEMS:
        raise PolicyError(f"{origin}: не больше {MAX_OVERRIDE_ITEMS} элементов в include и exclude")
    return Overrides(base=base, include=include, exclude=exclude)


def load_overrides(path: Path) -> Overrides:
    """Правки списка; нет файла — страна по умолчанию без правок."""
    if not path.is_file():
        return DEFAULT_OVERRIDES
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PolicyError(f"{path}: yaml: {exc}") from exc
    return overrides_from_dict(raw if raw is not None else {}, origin=str(path))


def source_url(kind: str, ident: str) -> str:
    """URL списка ipverse: kind — country, geo или as (ident — код страны или номер AS)."""
    if kind == "country":
        return f"{_IPVERSE}/country-ip-blocks/master/country/{ident}/ipv4-aggregated.txt"
    if kind == "geo":
        return f"{_IPVERSE}/geo-ip-blocks/master/country/{ident}/{ident}-ipv4.txt"
    if kind == "as":
        return f"{_IPVERSE}/as-ip-blocks/master/as/{ident}/ipv4-aggregated.txt"
    raise PolicyError(f"неизвестный тип источника: {kind}")


def override_as_numbers(overrides: Overrides) -> list[str]:
    """Номера AS из include и exclude без повторов, в порядке появления."""
    found: list[str] = []
    for item in (*overrides.include, *overrides.exclude):
        match = _AS_ITEM.fullmatch(item)
        if match and match.group(1) not in found:
            found.append(match.group(1))
    return found


def fetch_to_cache(
    overrides: Overrides,
    cache_dir: Path,
    fetch=None,
    now=time.time,
) -> None:
    """Скачать базу и все AS правок в кеш; при любой ошибке прежний кеш остаётся."""
    fetch = fetch or fetch_text
    new = cache_dir.with_name(cache_dir.name + ".new")
    old = cache_dir.with_name(cache_dir.name + ".old")
    shutil.rmtree(new, ignore_errors=True)
    new.mkdir(parents=True)
    try:
        base = overrides.base
        _fetch_list(fetch, "base", source_url(base.type, base.code), new / "base.txt",
                    f"{base.type} {base.code}: нет такого списка в ipverse")
        for number in override_as_numbers(overrides):
            _fetch_list(
                fetch, f"AS{number}", source_url("as", number), new / f"as-{number}.txt",
                f"AS{number}: нет в as-ip-blocks — AS не анонсирует префиксы 90 дней или не существует",
            )
        (new / ".base").write_text(f"{base.label()}\n", encoding="utf-8")
        (new / ".fetched").write_text(f"{int(now())}\n", encoding="utf-8")
        shutil.rmtree(old, ignore_errors=True)
        if cache_dir.exists():
            cache_dir.rename(old)
        new.rename(cache_dir)
        shutil.rmtree(old, ignore_errors=True)
    except BaseException:
        shutil.rmtree(new, ignore_errors=True)
        raise


def _fetch_list(fetch, label: str, url: str, dest: Path, missing: str) -> None:
    try:
        text = fetch(url)
    except NotFoundError as exc:
        raise PolicyError(missing) from exc
    dest.write_text(_prefix_text(sorted(parse_prefixes(text, label))), encoding="utf-8")


@dataclass(frozen=True)
class BuiltList:
    nets: list[IPv4Network]
    excluded_prefixes: int


def build_list(overrides: Overrides, cache_dir: Path) -> BuiltList:
    """(база ∪ include) − exclude из кеша, без сети."""
    base = overrides.base
    marker = cache_dir / ".base"
    have = marker.read_text(encoding="utf-8").strip() if marker.is_file() else None
    if (cache_dir / "base.txt").is_file() and have != base.label():
        raise PolicyError(
            f"кеш скачан для {have or 'неизвестного источника'}, а в правках {base.label()} — "
            "сначала обновите списки"
        )
    included = _cached(cache_dir, "base.txt", f"base ({base.label()})")
    excluded: list[tuple[int, int]] = []
    for item in overrides.include:
        included += _item_ranges(item, cache_dir)
    for item in overrides.exclude:
        excluded += _item_ranges(item, cache_dir)
    union = _merge_ranges(included)
    cut = _merge_ranges(excluded)
    touched = sum(
        1 for net in _spans_to_nets(union) if _overlaps(cut, int(net.network_address), int(net.broadcast_address))
    )
    result = _subtract_ranges(union, cut)
    if not result:
        raise PolicyError("после исключений список сетей пуст")
    return BuiltList(nets=_spans_to_nets(result), excluded_prefixes=touched)


def summary_lines(
    built: BuiltList,
    overrides: Overrides,
    previous: list[IPv4Network],
) -> list[str]:
    now = _merge_ranges(_ranges(built.nets))
    was = _merge_ranges(_ranges(previous))
    added = _subtract_ranges(now, was)
    removed = _subtract_ranges(was, now)
    changed = _merge_ranges(added + removed)
    was_addrs = _address_count(was)
    now_addrs = _address_count(now)
    shrink = 0 if was_addrs == 0 else max(0, (was_addrs - now_addrs) * 100 // was_addrs)
    values = [
        ("was_prefixes", len(_spans_to_nets(was))),
        ("now_prefixes", len(built.nets)),
        ("was_addrs", was_addrs),
        ("now_addrs", now_addrs),
        ("added_prefixes", len(_spans_to_nets(added))),
        ("removed_prefixes", len(_spans_to_nets(removed))),
        ("excluded_prefixes", built.excluded_prefixes),
        ("changed_prefixes", len(_spans_to_nets(changed))),
        ("shrink_pct", shrink),
        ("base", overrides.base.label()),
        ("includes", len(overrides.include)),
        ("excludes", len(overrides.exclude)),
    ]
    return [f"summary {key}={value}" for key, value in values]


_ELEMENTS = re.compile(r"elements\s*=\s*\{([^}]*)\}", re.DOTALL)
_CIDR = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3}/\d{1,2})\b")


def read_applied_nets(path: Path) -> list[IPv4Network]:
    """Сети из elements = { … } применённого rules.split.nft; нет файла — пусто."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    nets: list[IPv4Network] = []
    for block in _ELEMENTS.findall(text):
        for token in _CIDR.findall(block):
            try:
                nets.append(IPv4Network(token, strict=True))
            except ValueError:
                continue
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


class NotFoundError(PolicyError):
    """Источник ответил 404: такого списка нет."""


def fetch_text(url: str, timeout: float = 60) -> str:
    if url.startswith("file:"):
        return _read_url(url, timeout)
    last: Exception | None = None
    for attempt in range(3):
        try:
            return _read_url(url, timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise NotFoundError(f"{url}: HTTP 404") from exc
            last = exc
            if 400 <= exc.code < 500 or attempt == 2:
                break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            if attempt == 2:
                break
    raise PolicyError(f"не скачать {url}: {last}")


def render_files(
    policy: Policy,
    nets: list[IPv4Network] | None,
) -> dict[str, str]:
    files = {
        "nftables.conf": render_nftables(policy),
        "hooks/awg0.hooks": render_awg0_hooks(policy),
        "hooks/awg1.hooks": render_route_hooks(policy.uplink.iface, policy.uplink.table),
    }
    ru = policy.ru_exit
    if ru is None:
        return files
    if not nets:
        raise PolicyError("список российских сетей пуст")
    files["rules.nft"] = render_ru_table(policy, nets)
    files["hooks/awg2.hooks"] = render_route_hooks(ru.iface, ru.table)
    files["pools/combined.txt"] = _prefix_text(nets)
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
    if "pools" in data:
        raise PolicyError(
            "ru_exit.pools: больше не поддерживается, источник задаёт split-overrides.yaml"
        )
    _reject_unknown(data, _RU_KEYS, "ru_exit")
    iface = _iface(_required(data, "iface", "ru_exit"), "ru_exit.iface")
    if iface in {uplink_iface, client_iface}:
        raise PolicyError("ru_exit.iface совпадает с client или uplink")
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
    )


def _mss_block(iface: str) -> str:
    return (
        f'        oifname "{iface}" tcp flags syn tcp option maxseg size set rt mtu\n'
        f'        iifname "{iface}" tcp flags syn tcp option maxseg size set rt mtu'
    )


def _prefix_text(nets: list[IPv4Network]) -> str:
    return "".join(f"{net}\n" for net in nets)


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


def _subtract_ranges(
    spans: list[tuple[int, int]],
    cut: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """spans − cut; оба списка объединены и отсортированы."""
    out: list[tuple[int, int]] = []
    j = 0
    for start, end in spans:
        cur = start
        while j < len(cut) and cut[j][1] < cur:
            j += 1
        k = j
        while k < len(cut) and cut[k][0] <= end:
            c_start, c_end = cut[k]
            if c_start > cur:
                out.append((cur, c_start - 1))
            cur = max(cur, c_end + 1)
            if cur > end:
                break
            k += 1
        if cur <= end:
            out.append((cur, end))
    return out


def _overlaps(spans: list[tuple[int, int]], start: int, end: int) -> bool:
    """Пересекает ли [start, end] хоть один из объединённых диапазонов."""
    i = bisect_right(spans, (start, 2**33)) - 1
    if i >= 0 and spans[i][1] >= start:
        return True
    return i + 1 < len(spans) and spans[i + 1][0] <= end


def _spans_to_nets(spans: list[tuple[int, int]]) -> list[IPv4Network]:
    nets: list[IPv4Network] = []
    for start, end in spans:
        nets.extend(summarize_address_range(IPv4Address(start), IPv4Address(end)))
    return nets


def _address_count(spans: list[tuple[int, int]]) -> int:
    return sum(end - start + 1 for start, end in spans)


def _cached(cache_dir: Path, name: str, label: str) -> list[tuple[int, int]]:
    path = cache_dir / name
    if not path.is_file():
        raise PolicyError(f"нет в кеше: {label} — сначала обновите списки")
    return _ranges(parse_prefixes(path.read_text(encoding="utf-8"), name))


def _item_ranges(item: str, cache_dir: Path) -> list[tuple[int, int]]:
    match = _AS_ITEM.fullmatch(item)
    if match:
        return _cached(cache_dir, f"as-{match.group(1)}.txt", f"AS{match.group(1)}")
    return _ranges([IPv4Network(item, strict=True)])


def _override_items(value: object, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise PolicyError(f"{label}: нужен список")
    items: list[str] = []
    for raw in value:
        text = str(raw).strip()
        match = _AS_ITEM.fullmatch(text)
        if match:
            number = int(match.group(1))
            if number > 4294967295:
                raise PolicyError(f"{label}: {text}: номер AS вне диапазона")
            item = f"AS{number}"
        else:
            try:
                net = IPv4Network(text, strict=True)
            except ValueError as exc:
                raise PolicyError(f"{label}: {text!r} — не AS и не сеть IPv4 (a.b.c.d/n)") from exc
            if not 8 <= net.prefixlen <= 32:
                raise PolicyError(f"{label}: {text}: маска от /8 до /32")
            item = str(net)
        if item not in items:
            items.append(item)
    return tuple(items)


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
