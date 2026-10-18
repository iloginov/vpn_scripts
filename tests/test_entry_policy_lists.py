"""Список российских сетей: правки, кеш, вычитание, сводка (specs/017 в vpn-admin)."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from ipaddress import IPv4Network
from pathlib import Path

import pytest

import awg_policy
from vpn_awg.entry_policy import (
    DEFAULT_OVERRIDES,
    NotFoundError,
    PolicyError,
    SCHEMA_VERSION,
    build_list,
    fetch_to_cache,
    load_overrides,
    load_policy,
    overrides_from_dict,
    parse_prefixes,
    read_applied_nets,
    render_files,
    source_url,
    summary_lines,
)

_ROOT = Path(__file__).resolve().parents[1]

BASE = "# Country: Russia (RU)\n# Address family: IPv4\n#\n8.8.8.0/24\n8.8.9.0/24\n9.9.0.0/16\n"
AS_A = "# AS64500 (EXAMPLE)\n# Example Org\n#\n8.8.8.0/24\n7.7.7.0/24\n"


def fake_fetch(table: dict[str, str]):
    def fetch(url: str) -> str:
        if url not in table:
            raise NotFoundError(f"{url}: HTTP 404")
        return table[url]

    return fetch


def nets(text: str) -> list[IPv4Network]:
    return parse_prefixes(text, "t")


# --- парсер -----------------------------------------------------------------------------------------------


def test_parser_skips_header_comments_ipv6_and_blank_lines():
    text = "# header\n\n8.8.8.0/24  # inline\n2001:db8::/32\n"
    assert [str(n) for n in nets(text)] == ["8.8.8.0/24"]


def test_parser_rejects_garbage_with_line_number():
    with pytest.raises(PolicyError, match=r"t:2: не сеть IPv4"):
        nets("8.8.8.0/24\nhello\n")


def test_parser_rejects_empty_and_private():
    with pytest.raises(PolicyError, match="пуст"):
        nets("# only a header\n2001:db8::/32\n")
    with pytest.raises(PolicyError, match="непубличная"):
        nets("10.0.0.0/8\n")


# --- правки -----------------------------------------------------------------------------------------------


def test_overrides_defaults_without_file(tmp_path: Path):
    assert load_overrides(tmp_path / "split-overrides.yaml") == DEFAULT_OVERRIDES
    assert DEFAULT_OVERRIDES.base.label() == "country:ru"


def test_overrides_normalises_and_dedups():
    o = overrides_from_dict(
        {"schema": 2, "base": {"type": "geo", "code": "ru"}, "include": ["as64500", "AS064500", "203.0.113.0/24"], "exclude": ["198.51.100.0/24"]}
    )
    assert o.base.label() == "geo:ru"
    assert o.include == ("AS64500", "203.0.113.0/24")
    assert o.exclude == ("198.51.100.0/24",)


@pytest.mark.parametrize(
    "raw, match",
    [
        ({"base": {"type": "city", "code": "ru"}}, "base.type"),
        ({"base": {"code": "RU"}}, "base.code"),
        ({"base": {"code": "rus"}}, "base.code"),
        ({"include": ["AS1; rm -rf /"]}, "не AS и не сеть"),
        ({"include": ["203.0.113.5/24"]}, "не AS и не сеть"),
        ({"include": ["2001:db8::/32"]}, "не AS и не сеть"),
        ({"include": ["0.0.0.0/0"]}, "от /8 до /32"),
        ({"include": ["AS99999999999"]}, "не AS и не сеть"),
        ({"extra": 1}, "неизвестные ключи"),
        ({"schema": 1}, "schema 1"),
        ({"include": [f"203.0.{i // 250}.{i % 250}/32" for i in range(401)]}, "не больше 400"),
    ],
)
def test_overrides_reject(raw, match):
    with pytest.raises(PolicyError, match=match):
        overrides_from_dict(raw)


def test_source_urls():
    assert source_url("country", "ru").endswith("/country-ip-blocks/master/country/ru/ipv4-aggregated.txt")
    assert source_url("geo", "ru").endswith("/geo-ip-blocks/master/country/ru/ru-ipv4.txt")
    assert source_url("as", "64500").endswith("/as-ip-blocks/master/as/64500/ipv4-aggregated.txt")


# --- fetch ------------------------------------------------------------------------------------------------


def test_fetch_writes_cache_and_marker(tmp_path: Path):
    o = overrides_from_dict({"include": ["AS64500"], "exclude": ["AS64500"]})
    cache = tmp_path / "cache"
    fetch_to_cache(o, cache, fake_fetch({source_url("country", "ru"): BASE, source_url("as", "64500"): AS_A}), now=lambda: 1_800_000_000)
    assert sorted(p.name for p in cache.iterdir()) == [".base", ".fetched", "as-64500.txt", "base.txt"]
    assert (cache / ".base").read_text().strip() == "country:ru"
    assert (cache / ".fetched").read_text().strip() == "1800000000"
    assert "#" not in (cache / "base.txt").read_text()
    assert not (tmp_path / "cache.new").exists()


def test_fetch_failure_keeps_previous_cache(tmp_path: Path):
    cache = tmp_path / "cache"
    first = overrides_from_dict({})
    fetch_to_cache(first, cache, fake_fetch({source_url("country", "ru"): BASE}), now=lambda: 1)
    before = {p.name: p.read_text() for p in cache.iterdir()}

    second = overrides_from_dict({"include": ["AS64500", "AS64502"]})
    table = {source_url("country", "ru"): "8.8.8.0/24\n", source_url("as", "64500"): AS_A}
    with pytest.raises(PolicyError, match="AS64502: нет в as-ip-blocks"):
        fetch_to_cache(second, cache, fake_fetch(table), now=lambda: 2)

    assert {p.name: p.read_text() for p in cache.iterdir()} == before
    assert not (tmp_path / "cache.new").exists()
    assert not (tmp_path / "cache.old").exists()


def test_fetch_unknown_country_and_empty_list(tmp_path: Path):
    with pytest.raises(PolicyError, match="нет такого списка"):
        fetch_to_cache(overrides_from_dict({"base": {"code": "xx"}}), tmp_path / "c", fake_fetch({}))
    with pytest.raises(PolicyError, match="пуст"):
        fetch_to_cache(overrides_from_dict({}), tmp_path / "c", fake_fetch({source_url("country", "ru"): "# nothing\n"}))
    assert not (tmp_path / "c").exists()


# --- сборка из кеша ---------------------------------------------------------------------------------------


def cached(tmp_path: Path, overrides, table) -> Path:
    cache = tmp_path / "cache"
    fetch_to_cache(overrides, cache, fake_fetch(table), now=lambda: 1)
    return cache


def test_build_union_minus_exclude(tmp_path: Path):
    o = overrides_from_dict({"include": ["AS64500", "203.0.113.0/24"], "exclude": ["8.8.8.128/25", "9.9.0.0/17"]})
    cache = cached(tmp_path, o, {source_url("country", "ru"): BASE, source_url("as", "64500"): AS_A})
    built = build_list(o, cache)
    got = {str(n) for n in built.nets}
    assert "8.8.8.0/25" in got and "8.8.8.128/25" not in got
    assert "9.9.128.0/17" in got and "9.9.0.0/17" not in got
    assert "7.7.7.0/24" in got and "203.0.113.0/24" in got and "8.8.9.0/24" in got
    assert built.excluded_prefixes == 2  # 8.8.8.0/24 и 9.9.0.0/16 задеты


def test_exclude_beats_include(tmp_path: Path):
    o = overrides_from_dict({"include": ["203.0.113.0/24"], "exclude": ["203.0.113.0/24"]})
    cache = cached(tmp_path, o, {source_url("country", "ru"): BASE})
    assert "203.0.113.0/24" not in {str(n) for n in build_list(o, cache).nets}


def test_exclude_bigger_than_block_and_touching_borders(tmp_path: Path):
    o = overrides_from_dict({"exclude": ["8.8.0.0/16", "9.9.0.0/24"]})
    cache = cached(tmp_path, o, {source_url("country", "ru"): BASE})
    got = [str(n) for n in build_list(o, cache).nets]
    assert not any(n.startswith("8.8.") for n in got)
    assert "9.9.1.0/24" in got and "9.9.0.0/24" not in got


def test_build_refuses_empty_result_and_missing_cache(tmp_path: Path):
    o = overrides_from_dict({"exclude": ["8.0.0.0/8", "9.0.0.0/8"]})
    cache = cached(tmp_path, o, {source_url("country", "ru"): BASE})
    with pytest.raises(PolicyError, match="после исключений список сетей пуст"):
        build_list(o, cache)
    with pytest.raises(PolicyError, match="нет в кеше: base"):
        build_list(overrides_from_dict({}), tmp_path / "nothing")
    o2 = overrides_from_dict({"include": ["AS64503"]})
    cache2 = tmp_path / "c2"
    cache2.mkdir()
    (cache2 / "base.txt").write_text("8.8.8.0/24\n")
    (cache2 / ".base").write_text("country:ru\n")
    with pytest.raises(PolicyError, match="нет в кеше: AS64503 — сначала обновите списки"):
        build_list(o2, cache2)


# --- сводка -----------------------------------------------------------------------------------------------


def summary(lines: list[str]) -> dict[str, str]:
    return dict(line.removeprefix("summary ").split("=", 1) for line in lines)


def test_summary_against_applied_list(tmp_path: Path):
    o = overrides_from_dict({"exclude": ["8.8.9.0/24"]})
    cache = cached(tmp_path, o, {source_url("country", "ru"): BASE})
    built = build_list(o, cache)
    applied = tmp_path / "rules.split.nft"
    applied.write_text(
        "table ip awg_ru_split {\n    set ru_nets {\n        elements = {\n            8.8.8.0/24,\n            8.8.9.0/24,\n            1.1.1.0/24\n        }\n    }\n"
        "    chain prerouting {\n        ip saddr 10.10.8.0/24 ip daddr @ru_nets meta mark set meta mark | 0x100\n    }\n}\n"
    )
    previous = read_applied_nets(applied)
    assert [str(n) for n in previous] == ["8.8.8.0/24", "8.8.9.0/24", "1.1.1.0/24"]  # подсеть клиентов не попала
    s = summary(summary_lines(built, o, previous))
    assert s["was_prefixes"] == "2"  # 8.8.8.0/23 и 1.1.1.0/24 после объединения
    assert s["now_prefixes"] == "2"  # 8.8.8.0/24, 9.9.0.0/16
    assert s["added_prefixes"] == "1" and s["removed_prefixes"] == "2"
    assert s["excluded_prefixes"] == "1"
    assert s["base"] == "country:ru" and s["includes"] == "0" and s["excludes"] == "1"
    was, now = int(s["was_addrs"]), int(s["now_addrs"])
    assert was == 256 * 3 and now == 256 + 65536
    assert s["shrink_pct"] == "0"


def test_summary_shrink_and_no_previous(tmp_path: Path):
    o = overrides_from_dict({})
    cache = cached(tmp_path, o, {source_url("country", "ru"): "8.8.8.0/24\n"})
    built = build_list(o, cache)
    big = [IPv4Network("8.8.0.0/16"), IPv4Network("9.9.0.0/16")]
    assert summary(summary_lines(built, o, big))["shrink_pct"] == "99"
    s = summary(summary_lines(built, o, []))
    assert s["was_prefixes"] == "0" and s["shrink_pct"] == "0" and s["added_prefixes"] == "1" and s["changed_prefixes"] == "1"


def test_read_applied_nets_missing_file(tmp_path: Path):
    assert read_applied_nets(tmp_path / "none") == []


# --- схема и CLI ------------------------------------------------------------------------------------------


def write_policy(path: Path, schema: str = "schema: 2\n", extra: str = "") -> Path:
    path.write_text(
        schema
        + """client:
  iface: awg0
  subnet: 10.10.2.0/24
uplink:
  iface: awg1
  table: clientroute
  table_id: 100
ru_exit:
  iface: awg2
  table: ruroute
  table_id: 101
  fwmark: 0x100
  priority: 1100
"""
        + extra,
        encoding="utf-8",
    )
    return path


def test_schema_version_constant():
    assert SCHEMA_VERSION == 2


def test_old_schema_and_pools_are_refused(tmp_path: Path):
    with pytest.raises(PolicyError, match="schema нет, рендерер ждёт 2"):
        load_policy(write_policy(tmp_path / "a.yaml", schema=""))
    with pytest.raises(PolicyError, match="schema 1, рендерер ждёт 2"):
        load_policy(write_policy(tmp_path / "b.yaml", schema="schema: 1\n"))
    with pytest.raises(PolicyError, match="ru_exit.pools: больше не поддерживается"):
        load_policy(write_policy(tmp_path / "c.yaml", extra="  pools:\n    combine: union\n"))


def run_cli(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = awg_policy.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def test_cli_render_from_cache_prints_summary_and_files(tmp_path: Path):
    policy = write_policy(tmp_path / "entry.yaml")
    (tmp_path / "split-overrides.yaml").write_text("schema: 2\nexclude:\n  - 8.8.9.0/24\n", encoding="utf-8")
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "base.txt").write_text(BASE)
    (cache / ".base").write_text("country:ru\n")
    dest = tmp_path / "out"
    code, out, err = run_cli("render", str(policy), "--dest", str(dest), "--cache", str(cache))
    assert code == 0, err
    assert "summary now_prefixes=2" in out and "summary excludes=1" in out and "в ruleset не загружал" in out
    assert (dest / "pools" / "combined.txt").read_text() == "8.8.8.0/24\n9.9.0.0/16\n"
    assert "8.8.9.0/24" not in (dest / "rules.nft").read_text()


def test_cli_render_without_cache_refuses(tmp_path: Path):
    policy = write_policy(tmp_path / "entry.yaml")
    code, _, err = run_cli("render", str(policy), "--dest", str(tmp_path / "o"), "--cache", str(tmp_path / "none"))
    assert code == 1 and "нет в кеше: base" in err and not (tmp_path / "o").exists()


def test_cli_old_render_form_is_gone(tmp_path: Path):
    policy = write_policy(tmp_path / "entry.yaml")
    code, _, err = run_cli("render", str(policy), "--dest", str(tmp_path / "o"))
    assert code == 1 and "--cache" in err


def test_cli_fetch_needs_ru_exit(tmp_path: Path):
    plain = tmp_path / "entry.yaml"
    plain.write_text((_ROOT / "policy" / "entry.example.yaml").read_text(encoding="utf-8"))
    code, _, err = run_cli("fetch", str(plain), "--cache", str(tmp_path / "c"))
    assert code == 1 and "нет ru_exit" in err


def test_rendered_nft_contains_the_computed_list(tmp_path: Path):
    policy = load_policy(write_policy(tmp_path / "entry.yaml"))
    o = overrides_from_dict({"exclude": ["8.8.8.128/25"]})
    cache = cached(tmp_path, o, {source_url("country", "ru"): BASE})
    files = render_files(policy, build_list(o, cache).nets)
    assert "8.8.8.0/25" in files["rules.nft"] and "8.8.8.128/25" not in files["rules.nft"]
    json.dumps(files)  # все значения — строки


def test_build_refuses_a_cache_fetched_for_another_source(tmp_path: Path):
    cache = cached(tmp_path, overrides_from_dict({"base": {"type": "geo", "code": "ru"}}), {source_url("geo", "ru"): BASE})
    with pytest.raises(PolicyError, match="кеш скачан для geo:ru, а в правках country:ru — сначала обновите списки"):
        build_list(overrides_from_dict({}), cache)
    # …and a cache without the marker (made by hand) is not trusted either.
    (cache / ".base").unlink()
    with pytest.raises(PolicyError, match="неизвестного источника"):
        build_list(overrides_from_dict({"base": {"type": "geo", "code": "ru"}}), cache)
