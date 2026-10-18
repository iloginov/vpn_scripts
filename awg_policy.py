#!/usr/bin/env python3
"""Собрать nft и хуки entry из policy yaml.

Без ru_exit — файрвол пары. С ru_exit — тот же файрвол плюс второй канал
и таблица awg_ru_split. В живой ruleset скрипт ничего не загружает.

Список российских сетей берётся из кеша: сначала fetch (скачивает базу и AS
из split-overrides.yaml рядом с policy yaml), затем render (без сети).

    python awg_policy.py fetch entry.yaml --cache /tmp/awg-policy-cache
    python awg_policy.py render entry.yaml --dest /tmp/awg-policy --cache /tmp/awg-policy-cache
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_PKG_ROOT = _ROOT / "packages"
if _PKG_ROOT.is_dir():
    sys.path.insert(0, str(_PKG_ROOT))
else:
    print(
        "Ошибка: не найден каталог packages/ рядом с awg_policy.py",
        file=sys.stderr,
    )
    sys.exit(1)

from vpn_awg.entry_policy import (  # noqa: E402
    OVERRIDES_FILE,
    PolicyError,
    build_list,
    fetch_to_cache,
    load_overrides,
    load_policy,
    read_applied_nets,
    render_files,
    summary_lines,
    write_render,
)


def _cmd_fetch(args: argparse.Namespace) -> int:
    policy = load_policy(args.policy)
    if policy.ru_exit is None:
        raise PolicyError("в policy нет ru_exit: качать нечего")
    overrides = load_overrides(args.policy.parent / OVERRIDES_FILE)
    fetch_to_cache(overrides, args.cache)
    print(f"{args.cache}: скачано, источник {overrides.base.label()}")
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    policy = load_policy(args.policy)
    if policy.ru_exit is None:
        write_render(args.dest, render_files(policy, None))
        print(f"{args.dest}: файрвол без сплита, списки не нужны")
        print("в ruleset не загружал")
        return 0
    if args.cache is None:
        raise PolicyError("render со сплитом требует --cache")
    overrides = load_overrides(args.policy.parent / OVERRIDES_FILE)
    built = build_list(overrides, args.cache)
    previous = read_applied_nets(args.previous) if args.previous else []
    write_render(args.dest, render_files(policy, built.nets))
    print(
        f"{args.dest}: файрвол со сплитом, источник {overrides.base.label()}, "
        f"сетей {len(built.nets)}"
    )
    for line in summary_lines(built, overrides, previous):
        print(line)
    print("в ruleset не загружал")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Собрать nft и хуки entry из policy yaml")
    sub = parser.add_subparsers(dest="cmd", required=True)
    fetch = sub.add_parser("fetch", help="скачать базу и AS правок в кеш")
    fetch.add_argument("policy", type=Path)
    fetch.add_argument("--cache", required=True, type=Path)
    render = sub.add_parser("render", help="собрать файлы из кеша, без сети")
    render.add_argument("policy", type=Path)
    render.add_argument("--dest", required=True, type=Path)
    render.add_argument("--cache", type=Path)
    render.add_argument("--previous", type=Path, help="применённый rules.split.nft для сводки")
    args = parser.parse_args(argv)

    try:
        return _cmd_fetch(args) if args.cmd == "fetch" else _cmd_render(args)
    except PolicyError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
