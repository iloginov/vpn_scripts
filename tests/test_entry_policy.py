"""Policy yaml: два файрвола и сборка пулов."""

from __future__ import annotations

from pathlib import Path

import pytest

from vpn_awg.entry_policy import (
    PolicyError,
    combine_prefixes,
    fetch_pools,
    load_policy,
    parse_prefixes,
    render_files,
    write_render,
)

_ROOT = Path(__file__).resolve().parents[1]


def test_example_without_ru_exit_has_no_second_iface():
    policy = load_policy(_ROOT / "policy" / "entry.example.yaml")
    files = render_files(policy, [])
    nft = files["nftables.conf"]

    assert policy.ru_exit is None
    assert 'oifname "awg1" masquerade' in nft
    assert "awg2" not in nft
    assert "awg_ru_split" not in nft
    assert "rules.nft" not in files
    assert "prio 2000" not in files["hooks/awg0.hooks"]
    assert "prio 1000" in files["hooks/awg0.hooks"]
    assert "dev awg1 table clientroute" in files["hooks/awg1.hooks"]


def test_example_ru_policy_names_both_pools():
    policy = load_policy(_ROOT / "policy" / "entry.ru.example.yaml")
    assert policy.ru_exit is not None
    assert policy.ru_exit.combine == "union"
    assert [source.name for source in policy.ru_exit.sources] == ["ipdeny", "ipverse"]
    assert policy.ru_exit.fwmark == 0x100
    assert policy.ru_exit.priority == 1100


def test_ru_render_marks_and_guards(tmp_path: Path):
    policy = _policy_with_pools(tmp_path, "union", "8.8.8.0/24\n", "8.8.9.0/24\n# comment\n")
    pools = fetch_pools(policy)
    files = render_files(policy, pools)

    assert "8.8.8.0/23" in files["rules.nft"]
    assert "meta mark set meta mark | 0x100" in files["rules.nft"]
    assert 'oifname != "awg2" counter drop' in files["rules.nft"]
    assert 'include "/etc/awg-ru-split/rules.nft"' in files["nftables.conf"]
    assert 'oifname "awg2" masquerade' in files["nftables.conf"]
    assert "fwmark 0x100/0x100 iif awg0 lookup ruroute prio 1100" in files["hooks/awg0.hooks"]
    assert "dev awg2 table ruroute" in files["hooks/awg2.hooks"]
    assert files["pools/combined.txt"] == "8.8.8.0/23\n"


def test_intersection_keeps_overlap_only():
    left = parse_prefixes("8.8.8.0/23\n", "left")
    right = parse_prefixes("8.8.9.0/24\n", "right")
    assert [str(net) for net in combine_prefixes([left, right], "intersection")] == ["8.8.9.0/24"]
    assert [str(net) for net in combine_prefixes([left, right], "first")] == ["8.8.8.0/23"]


def test_private_prefix_rejects_the_list():
    with pytest.raises(PolicyError, match="непубличная"):
        parse_prefixes("8.8.8.0/24\n10.1.0.0/16\n", "ipdeny")


def test_priority_order_is_required(tmp_path: Path):
    path = tmp_path / "policy.yaml"
    path.write_text(
        (_ROOT / "policy" / "entry.ru.example.yaml").read_text(encoding="utf-8").replace(
            "priority: 1100",
            "priority: 3000",
        ),
        encoding="utf-8",
    )
    with pytest.raises(PolicyError, match="приоритеты"):
        load_policy(path)


def test_write_render_drops_split_files_when_policy_has_no_ru(tmp_path: Path):
    ru = _policy_with_pools(tmp_path, "first", "8.8.8.0/24\n", "8.8.9.0/24\n")
    write_render(tmp_path / "out", render_files(ru, fetch_pools(ru)))
    plain = load_policy(_ROOT / "policy" / "entry.example.yaml")
    dest = tmp_path / "out"
    write_render(dest, render_files(plain, []))

    assert not (dest / "rules.nft").exists()
    assert not (dest / "hooks" / "awg2.hooks").exists()
    assert not (dest / "pools").exists()
    assert (dest / "nftables.conf").is_file()


def _policy_with_pools(tmp_path: Path, combine: str, left: str, right: str):
    left_path = tmp_path / "left.zone"
    right_path = tmp_path / "right.zone"
    left_path.write_text(left, encoding="utf-8")
    right_path.write_text(right, encoding="utf-8")
    path = tmp_path / "policy.yaml"
    path.write_text(
        f"""
client:
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
  rules_path: /etc/awg-ru-split/rules.nft
  pools:
    combine: {combine}
    sources:
      - name: ipdeny
        url: {left_path.as_uri()}
      - name: ipverse
        url: {right_path.as_uri()}
""",
        encoding="utf-8",
    )
    return load_policy(path)
