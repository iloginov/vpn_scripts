#!/usr/bin/env python3
"""
Собрать packages/vpn_awg/data/host_pools.json из Architect api/v1/index.json.

Репозиторий-источник: https://github.com/Vadim-Khristenko/AmneziaWG-Architect
(ветка main, api/v1/index.json). Контекст мимикрии — docs/awg2_protocol_params.md.

Без локального клона Architect: по умолчанию скачивание JSON с GitHub;
либо --index /path/to/index.json для офлайн.

Пул dns — tools/dns_hosts.json (в API Architect нет профиля dns_query).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _TOOLS_DIR.parent
_DNS_HOSTS_PATH = _TOOLS_DIR / "dns_hosts.json"
_OUT = _REPO_ROOT / "packages" / "vpn_awg" / "data" / "host_pools.json"

ARCHITECT_INDEX_URL = (
    "https://raw.githubusercontent.com/Vadim-Khristenko/"
    "AmneziaWG-Architect/main/api/v1/index.json"
)

PROFILE_KEYS = (
    "quic_initial",
    "quic_0rtt",
    "tls_client_hello",
    "dtls",
    "sip",
)


def load_dns_hosts() -> list[str]:
    if not _DNS_HOSTS_PATH.is_file():
        raise SystemExit(f"Нет {_DNS_HOSTS_PATH}")
    data = json.loads(_DNS_HOSTS_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise SystemExit(f"Некорректный формат {_DNS_HOSTS_PATH}: ожидается JSON-массив строк")
    return [str(h) for h in data]


def fetch_index(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "vpn_scripts/build_host_pools"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_index(path: Path | None, url: str) -> dict:
    if path is not None:
        if not path.is_file():
            raise FileNotFoundError(path)
        return json.loads(path.read_text(encoding="utf-8"))
    try:
        print(f"Fetching {url} …")
        return fetch_index(url)
    except (urllib.error.URLError, TimeoutError) as e:
        raise SystemExit(
            f"Не удалось скачать index.json: {e}\n"
            "Укажите локальный файл: --index /path/to/index.json"
        ) from e


def build_pools(data: dict) -> dict[str, list[str]]:
    profiles = data.get("profiles", {})
    out: dict[str, list[str]] = {}

    for key in PROFILE_KEYS:
        block = profiles.get(key, {})
        hosts = block.get("hosts", [])
        out[key] = [h["host"] if isinstance(h, dict) else str(h) for h in hosts]

    out["dns"] = load_dns_hosts()
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--index",
        type=Path,
        default=None,
        help="Локальный api/v1/index.json (без сети).",
    )
    parser.add_argument(
        "--url",
        default=ARCHITECT_INDEX_URL,
        help=f"URL index.json (по умолчанию: {ARCHITECT_INDEX_URL}).",
    )
    args = parser.parse_args()

    data = load_index(args.index, args.url)
    out = build_pools(data)

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {_OUT} ({sum(len(v) for v in out.values())} hosts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
