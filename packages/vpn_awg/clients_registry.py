"""Реестр клиентов: <server>.conf → <server>.json рядом с серверным конфигом."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def registry_json_path(server_conf: Path) -> Path:
    """Путь к JSON-реестру: то же имя и каталог, что у server_conf, расширение .json."""
    return server_conf.with_suffix(".json")


def load_registry(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"clients": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if "clients" not in data or not isinstance(data["clients"], list):
        raise ValueError(f"Некорректный формат {path}: ожидается ключ clients[]")
    return data


def save_registry(path: Path, data: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def ensure_registry(path: Path) -> dict[str, Any]:
    data = load_registry(path)
    save_registry(path, data)
    return data


def add_client_record(
    path: Path,
    *,
    name: str,
    filename: str,
    ip: str,
    description: str = "",
    created_at: datetime | None = None,
) -> dict[str, Any]:
    data = load_registry(path) if path.is_file() else {"clients": []}
    ts = created_at or datetime.now(timezone.utc)
    created_iso = ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for entry in data["clients"]:
        if entry.get("name") == name:
            raise ValueError(f"Клиент '{name}' уже есть в {path.name}")
        if entry.get("ip") == ip:
            raise ValueError(f"IP {ip} уже занят в {path.name}")

    data["clients"].append(
        {
            "name": name,
            "filename": filename,
            "ip": ip,
            "created_at": created_iso,
            "description": description,
        }
    )
    save_registry(path, data)
    return data


def remove_client_by_ip(path: Path, ip: str) -> bool:
    if not path.is_file():
        return False
    target = ip.split("/")[0].strip()
    data = load_registry(path)
    before = len(data["clients"])
    data["clients"] = [
        c
        for c in data["clients"]
        if c.get("ip", "").split("/")[0].strip() != target
    ]
    if len(data["clients"]) == before:
        return False
    save_registry(path, data)
    return True
