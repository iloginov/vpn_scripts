"""Policy yaml: два файрвола и сборка пулов."""

from __future__ import annotations

from pathlib import Path

import pytest

from vpn_awg.entry_policy import (
    PolicyError,
    load_policy,
    parse_prefixes,
    render_files,
    write_render,
)

_ROOT = Path(__file__).resolve().parents[1]


def test_example_without_ru_exit_has_no_second_iface():
    policy = load_policy(_ROOT / "policy" / "entry.example.yaml")
    files = render_files(policy, None)
    nft = files["nftables.conf"]

    assert policy.ru_exit is None
    assert 'oifname "awg1" masquerade' in nft
    assert "awg2" not in nft
    assert "awg_ru_split" not in nft
    assert "rules.nft" not in files
    assert "prio 2000" not in files["hooks/awg0.hooks"]
    assert "prio 1000" in files["hooks/awg0.hooks"]
    assert "dev awg1 table clientroute" in files["hooks/awg1.hooks"]


def test_example_ru_policy_has_split_and_no_pools():
    policy = load_policy(_ROOT / "policy" / "entry.ru.example.yaml")
    assert policy.ru_exit is not None
    assert policy.ru_exit.fwmark == 0x100
    assert policy.ru_exit.priority == 1100


def test_ru_render_marks_and_guards():
    policy = load_policy(_ROOT / "policy" / "entry.ru.example.yaml")
    files = render_files(policy, parse_prefixes("8.8.8.0/24\n8.8.9.0/24\n", "list"))

    assert "8.8.8.0/24" in files["rules.nft"]
    assert "meta mark set meta mark | 0x100" in files["rules.nft"]
    assert 'oifname != "awg2" counter drop' in files["rules.nft"]
    assert 'include "/etc/awg-ru-split/rules.nft"' in files["nftables.conf"]
    assert 'oifname "awg2" masquerade' in files["nftables.conf"]
    assert "fwmark 0x100/0x100 iif awg0 lookup ruroute prio 1100" in files["hooks/awg0.hooks"]
    assert "dev awg2 table ruroute" in files["hooks/awg2.hooks"]
    assert files["pools/combined.txt"] == "8.8.8.0/24\n8.8.9.0/24\n"


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
    ru = load_policy(_ROOT / "policy" / "entry.ru.example.yaml")
    write_render(tmp_path / "out", render_files(ru, parse_prefixes("8.8.8.0/24\n", "list")))
    plain = load_policy(_ROOT / "policy" / "entry.example.yaml")
    dest = tmp_path / "out"
    write_render(dest, render_files(plain, None))

    assert not (dest / "rules.nft").exists()
    assert not (dest / "hooks" / "awg2.hooks").exists()
    assert not (dest / "pools").exists()
    assert (dest / "nftables.conf").is_file()
