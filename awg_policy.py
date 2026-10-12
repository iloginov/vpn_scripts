#!/usr/bin/env python3
"""Собрать nft и хуки entry из policy yaml.

Без ru_exit — файрвол пары. С ru_exit — тот же файрвол плюс второй канал
и таблица awg_ru_split. В живой ruleset скрипт ничего не загружает.

    python awg_policy.py render entry.yaml --dest /tmp/awg-policy
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
    PolicyError,
    fetch_pools,
    load_policy,
    render_files,
    write_render,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Собрать nft и хуки entry из policy yaml")
    sub = parser.add_subparsers(dest="cmd", required=True)
    render = sub.add_parser("render", help="скачать пулы, если есть ru_exit, и записать файлы")
    render.add_argument("policy", type=Path)
    render.add_argument("--dest", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        policy = load_policy(args.policy)
        pools = fetch_pools(policy)
        files = render_files(policy, pools)
        write_render(args.dest, files)
    except PolicyError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    if policy.ru_exit is None:
        print(f"{args.dest}: файрвол без сплита, пулы не качались")
    else:
        combined = (args.dest / "pools" / "combined.txt").read_text(encoding="utf-8").splitlines()
        names = ", ".join(source.name for source in policy.ru_exit.sources)
        print(
            f"{args.dest}: файрвол со сплитом, {policy.ru_exit.combine} ({names}), "
            f"сетей {len(combined)}"
        )
    print("в ruleset не загружал")
    return 0


if __name__ == "__main__":
    sys.exit(main())
