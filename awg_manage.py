#!/usr/bin/env python3
"""
awg_manage.py

Утилита для управления конфигами AmneziaWG 3.1:

1) init    - первичная генерация server_awg0.conf (без клиентов)
2) add     - создание клиентского .conf, запись в <server>.conf и <server>.json
3) remove  - удаление клиента из server.conf по IP или по пути client_*.conf

Деплой: скопировать awg_manage.py и каталог packages/ рядом.

Obfs/мимикрия: docs/awg3_protocol_params.md; генерация — порт
Any-Tech-ARCHITECT / packages/vpn_awg.
"""

from __future__ import annotations

import argparse
import ipaddress
import pathlib
import subprocess
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_PKG_ROOT = _ROOT / "packages"
if _PKG_ROOT.is_dir():
    sys.path.insert(0, str(_PKG_ROOT))
else:
    print(
        "Ошибка: не найден каталог packages/ рядом с awg_manage.py",
        file=sys.stderr,
    )
    sys.exit(1)

from amnezia_export.server import read_wg_conf, resolve_server_conf
from vpn_awg.clients_registry import (
    add_client_record,
    ensure_registry,
    registry_json_path,
    remove_client_by_ip,
)
from vpn_awg.conf import generate_client_config, generate_server_config
from vpn_awg.conf_parse import (
    client_template_complete,
    parse_server_conf,
)
from vpn_awg.gen_config import INTENSITIES, JUNK_LEVELS, MIMIC_PROFILES, GenConfig
from vpn_awg.obfs import (
    Awg2ClientObfs,
    Awg2ServerObfs,
    generate_awg2_client_obfs,
    generate_awg2_server_obfs,
    generate_awg2_server_shared_only,
    obfs_summary,
)

AWG_BIN = "awg"

_CLIENT_OBFS_ERR = (
    "Нет client-only obfs в server_awg0.conf (Jc/Jmin/Jmax и I1–I5). "
    "Укажите --mimic / --intensity / --junk-level при init (шаблон в server) или при add."
)
_DEFAULT_CLIENT_DNS = "1.1.1.1,1.0.0.1"
_CLIENT_SERVER_HOST_ERR = (
    "В server_awg0.conf нет ServerHost для клиентов. Укажите --server-host при add "
    "или задайте --server-host при init."
)
_DEFAULT_SERVER_MTU = 1280


def run_cmd(cmd, input_data=None) -> str:
    result = subprocess.run(
        cmd,
        input=input_data,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def check_awg() -> None:
    try:
        run_cmd([AWG_BIN, "help"])
    except Exception:
        print(f"Ошибка: не найден {AWG_BIN} (пакет amneziawg).", file=sys.stderr)
        sys.exit(1)


def generate_keypair():
    priv = run_cmd([AWG_BIN, "genkey"])
    pub = run_cmd([AWG_BIN, "pubkey"], input_data=priv + "\n")
    return priv, pub


def generate_psk():
    return run_cmd([AWG_BIN, "genpsk"])


def parse_client_ip(ip_str: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    ip_str = ip_str.strip()
    if "/" in ip_str:
        return ipaddress.ip_interface(ip_str).ip
    return ipaddress.ip_address(ip_str)


def peer_allowed_ips(peer: dict) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    ips = []
    for part in peer["allowed"].split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "/" in part:
                ips.append(ipaddress.ip_interface(part).ip)
            else:
                ips.append(ipaddress.ip_address(part))
        except ValueError:
            continue
    return ips


def find_peer_by_ip(
    peers: list[dict], client_ip: ipaddress.IPv4Address | ipaddress.IPv6Address
) -> dict | None:
    for peer in peers:
        if client_ip in peer_allowed_ips(peer):
            return peer
    return None


def peers_for_server_config(peers: list[dict]) -> list[dict]:
    return [
        {
            "public_key": p["public_key"],
            "psk": p.get("psk", ""),
            "ip": p["allowed"],
            "keepalive": int(p["keepalive"]) if p.get("keepalive") else 25,
        }
        for p in peers
    ]


def write_server_conf(path: pathlib.Path, text: str, *, backup: bool = True) -> None:
    if backup and path.exists():
        backup_path = path.with_suffix(".conf.bak")
        backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"  резервная копия: {backup_path}")
    path.write_text(text, encoding="utf-8")


def client_obfs_cli_specified(args) -> bool:
    return (
        getattr(args, "mimic", None) is not None
        or getattr(args, "intensity", None) is not None
        or getattr(args, "junk_level", None) is not None
    )


def validate_mtu(mtu: int) -> None:
    if not 576 <= mtu <= 9000:
        print(
            f"[error] MTU {mtu} вне диапазона 576–9000.",
            file=sys.stderr,
        )
        sys.exit(1)


def gen_mtu_from_args(args) -> int:
    """MTU для генерации obfs (внутренний default, если в CLI не задан)."""
    if getattr(args, "mtu", None) is not None:
        validate_mtu(args.mtu)
        return args.mtu
    return _DEFAULT_SERVER_MTU


def gen_config_from_args(args, *, mtu: int) -> GenConfig:
    return GenConfig(
        mimic=args.mimic if args.mimic is not None else "quic_initial",
        intensity=args.intensity if args.intensity is not None else "medium",
        junk_level=args.junk_level if args.junk_level is not None else 5,
        mtu=mtu,
        mimic_host=getattr(args, "mimic_host", "") or "",
        mimic_all=getattr(args, "mimic_all", False),
        router_mode=getattr(args, "router_mode", False),
    )


def build_server_obfs(args) -> Awg2ServerObfs:
    mtu = gen_mtu_from_args(args)
    if client_obfs_cli_specified(args):
        return generate_awg2_server_obfs(gen_config_from_args(args, mtu=mtu))
    return generate_awg2_server_shared_only(mtu=mtu)


def resolve_client_mtu(
    server: Awg2ServerObfs, args, *, iface_has_mtu: bool
) -> int | None:
    """MTU для строки в client .conf: CLI → server conf → None."""
    if args.mtu is not None:
        validate_mtu(args.mtu)
        return args.mtu
    if iface_has_mtu:
        return server.shared.mtu
    return None


def mtu_for_obfs_generation(
    server: Awg2ServerObfs, args, *, iface_has_mtu: bool
) -> int:
    """MTU только для расчёта obfs (padding), не обязательно попадает в client .conf."""
    client_mtu = resolve_client_mtu(server, args, iface_has_mtu=iface_has_mtu)
    if client_mtu is not None:
        return client_mtu
    return gen_mtu_from_args(args)


def server_obfs_with_client_mtu(
    server: Awg2ServerObfs, client_mtu: int
) -> Awg2ServerObfs:
    return replace(server, shared=replace(server.shared, mtu=client_mtu))


def resolve_server_host(server: Awg2ServerObfs, args) -> str:
    if getattr(args, "server_host", None):
        host = args.server_host.strip()
        if host:
            return host
    if server.client_server_host:
        return server.client_server_host.strip()
    print(f"[error] {_CLIENT_SERVER_HOST_ERR}", file=sys.stderr)
    sys.exit(1)


def resolve_client_dns(server: Awg2ServerObfs, args) -> str:
    if getattr(args, "dns", None):
        dns = args.dns.strip()
        if dns:
            return dns
    if server.client_dns:
        return server.client_dns.strip()
    return _DEFAULT_CLIENT_DNS


def resolve_client_obfs(
    server: Awg2ServerObfs, args, *, iface_has_mtu: bool
) -> Awg2ClientObfs:
    obfs_mtu = mtu_for_obfs_generation(server, args, iface_has_mtu=iface_has_mtu)
    if client_obfs_cli_specified(args):
        cfg = gen_config_from_args(args, mtu=obfs_mtu)
        return generate_awg2_client_obfs(cfg)
    if not client_template_complete(server.client_template):
        print(f"[error] {_CLIENT_OBFS_ERR}", file=sys.stderr)
        sys.exit(1)
    from vpn_awg.obfs import client_obfs_from_server

    return client_obfs_from_server(server)


def add_server_obfs_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--mimic",
        default=None,
        choices=MIMIC_PROFILES,
        help="Шаблон client-only obfs в server (комментарии # Jc / # I1 …).",
    )
    parser.add_argument(
        "--intensity",
        default=None,
        choices=INTENSITIES,
        help="Энтропия для шаблона в server.",
    )
    parser.add_argument(
        "--junk-level",
        default=None,
        type=int,
        choices=JUNK_LEVELS,
        help="Junk-train (Jc) для шаблона в server.",
    )
    parser.add_argument(
        "--mimic-host",
        default="",
        help="Хост мимикрии для шаблона в server.",
    )
    parser.add_argument(
        "--mimic-all",
        action="store_true",
        help="Профиль мимикрии для I2–I5 в шаблоне server.",
    )
    parser.add_argument(
        "--router-mode",
        action="store_true",
        help="Режим роутера для шаблона в server.",
    )


def add_client_obfs_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--mtu",
        default=None,
        type=int,
        help="MTU в client.conf (иначе из server, если там есть; иначе без MTU в conf).",
    )
    parser.add_argument(
        "--mimic",
        default=None,
        choices=MIMIC_PROFILES,
        help="Профиль мимикрии (генерация client-only; иначе шаблон из server).",
    )
    parser.add_argument(
        "--intensity",
        default=None,
        choices=INTENSITIES,
        help="Энтропия (Jmin/Jmax, I2–I5).",
    )
    parser.add_argument(
        "--junk-level",
        default=None,
        type=int,
        choices=JUNK_LEVELS,
        help="Junk-train (Jc).",
    )
    parser.add_argument(
        "--mimic-host",
        default="",
        help="Свой хост для мимикрии.",
    )
    parser.add_argument(
        "--mimic-all",
        action="store_true",
        help="Профиль мимикрии для I2–I5.",
    )
    parser.add_argument(
        "--router-mode",
        action="store_true",
        help="Режим роутера: меньше Jc/Jmax, без I2–I5.",
    )


def cmd_init(args):
    check_awg()

    try:
        net = ipaddress.ip_network(args.subnet, strict=True)
    except ValueError as e:
        print(f"Некорректная подсеть: {e}", file=sys.stderr)
        sys.exit(1)

    hosts = list(net.hosts())
    if not hosts:
        print("В подсети нет адресов для сервера.", file=sys.stderr)
        sys.exit(1)

    server_ip = f"{hosts[0]}/{net.prefixlen}"

    if args.output_dir:
        out_dir = pathlib.Path(args.output_dir)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = pathlib.Path(f"amneziawg_3_{ts}")
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[init] Использую каталог: {out_dir}")

    server_obfs = build_server_obfs(args)
    if getattr(args, "server_host", None):
        server_obfs = replace(
            server_obfs, client_server_host=args.server_host.strip()
        )
    if getattr(args, "dns", None):
        server_obfs = replace(server_obfs, client_dns=args.dns.strip())
    if getattr(args, "server_name", None):
        server_obfs = replace(
            server_obfs, client_server_name=args.server_name.strip()
        )
    print("[init] Параметры обфускации AmneziaWG 3.1 (сервер):")
    for line in obfs_summary(server_obfs):
        print(line)
    if getattr(args, "server_host", None):
        print(f"[init] ServerHost по умолчанию для клиентов: {args.server_host.strip()}")
    if args.mtu is not None:
        print(f"[init] MTU в server: {args.mtu}")
    if getattr(args, "dns", None):
        print(f"[init] DNS для клиентов (# DNS = …): {args.dns.strip()}")
    if getattr(args, "server_name", None):
        print(
            f"[init] Имя по умолчанию для клиентов (# ServerName = …): "
            f"{args.server_name.strip()}"
        )
    if not client_obfs_cli_specified(args):
        print(
            "[init] Client-only obfs в server не задан — при add укажите "
            "--mimic/--intensity/--junk-level или добавьте шаблон через init с этими флагами.",
        )

    print("[init] Генерирую ключи сервера...")
    server_priv, _server_pub = generate_keypair()

    server_conf_text = generate_server_config(
        server_priv=server_priv,
        server_ip=server_ip,
        listen_port=args.listen_port,
        server_obfs=server_obfs,
        peers=[],
        include_mtu=args.mtu is not None,
    )
    server_conf_path = out_dir / "server_awg0.conf"
    server_conf_path.write_text(server_conf_text, encoding="utf-8")
    print(f"[init] Серверный конфиг: {server_conf_path}")

    registry_path = registry_json_path(server_conf_path)
    ensure_registry(registry_path)
    print(f"[init] Реестр клиентов: {registry_path}")

    print("\n[init] На входной ноде:")
    print("  sudo mkdir -p /etc/amnezia/amneziawg")
    print(f"  sudo cp {server_conf_path} /etc/amnezia/amneziawg/awg0.conf")
    print("  sudo awg-quick up awg0")
    print("  sudo systemctl enable awg-quick@awg0")
    print("\n[init] Клиентов создавайте через: awg_manage.py add ...")


def cmd_add(args):
    server_path = pathlib.Path(args.server_conf)
    if not server_path.exists():
        print(f"[add] Нет файла {server_path}", file=sys.stderr)
        sys.exit(1)

    check_awg()

    server_priv, address, listen_port, server_obfs, peers, iface_has_mtu, interface_hooks, _ = (
        parse_server_conf(server_path)
    )

    try:
        server_ip_iface = ipaddress.ip_interface(address)
    except ValueError as e:
        print(f"[add] Некорректный Address в серверном конфиге: {e}", file=sys.stderr)
        sys.exit(1)

    net = server_ip_iface.network
    used_ips = {server_ip_iface.ip}

    for p in peers:
        try:
            first_allowed = p["allowed"].split(",")[0].strip()
            ip_iface = ipaddress.ip_interface(first_allowed)
            used_ips.add(ip_iface.ip)
        except ValueError:
            continue

    free_ip = None
    for host in net.hosts():
        if host not in used_ips:
            free_ip = host
            break

    if free_ip is None:
        print(
            "[add] Нет свободных адресов в подсети для нового клиента.", file=sys.stderr
        )
        sys.exit(1)

    client_ip = f"{free_ip}/32"
    print(f"[add] Новый клиент '{args.name}' получит IP: {client_ip}")

    server_pub = run_cmd([AWG_BIN, "pubkey"], input_data=server_priv + "\n")

    c_priv, c_pub = generate_keypair()
    psk = generate_psk()

    try:
        client_mtu = resolve_client_mtu(
            server_obfs, args, iface_has_mtu=iface_has_mtu
        )
        obfs_mtu = mtu_for_obfs_generation(
            server_obfs, args, iface_has_mtu=iface_has_mtu
        )
        client_obfs = resolve_client_obfs(
            server_obfs, args, iface_has_mtu=iface_has_mtu
        )
    except SystemExit:
        raise
    except ValueError as e:
        print(f"[add] {e}", file=sys.stderr)
        sys.exit(1)

    client_dns = resolve_client_dns(server_obfs, args)
    server_host = resolve_server_host(server_obfs, args)

    client_conf_text = generate_client_config(
        server_obfs_with_client_mtu(server_obfs, obfs_mtu),
        client_priv=c_priv,
        client_ip=client_ip,
        server_pub=server_pub,
        endpoint=f"{server_host}:{listen_port}",
        dns=client_dns,
        psk=psk,
        client_obfs=client_obfs,
        keepalive=25,
        client_mtu=client_mtu,
    )
    out_dir = pathlib.Path(args.output_dir) if args.output_dir else server_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    client_filename = f"client_{args.name}.conf"
    client_conf_path = out_dir / client_filename
    client_conf_path.write_text(client_conf_text, encoding="utf-8")
    print(f"[add] client config: {client_conf_path}")

    registry_path = registry_json_path(server_path)
    try:
        registry_label = (args.description or "").strip()
        add_client_record(
            registry_path,
            name=args.name,
            filename=client_filename,
            ip=client_ip,
            description=registry_label,
        )
    except ValueError as e:
        print(f"[add] {e}", file=sys.stderr)
        client_conf_path.unlink(missing_ok=True)
        sys.exit(1)
    print(f"[add] реестр обновлён: {registry_path}")

    peers.append(
        {
            "public_key": c_pub,
            "psk": psk,
            "allowed": client_ip,
            "keepalive": "25",
        }
    )
    new_server_text = generate_server_config(
        server_priv=server_priv,
        server_ip=address,
        listen_port=listen_port,
        server_obfs=server_obfs,
        peers=peers_for_server_config(peers),
        include_mtu=iface_has_mtu,
        interface_hooks=interface_hooks,
    )
    print("[add] Серверный конфиг обновлён.")
    write_server_conf(server_path, new_server_text)
    print("[add] Перезапусти awg0 на входной ноде:")
    print("  sudo awg-quick down awg0 && sudo awg-quick up awg0")


def client_address_from_conf(client_path: pathlib.Path) -> str:
    """Address из [Interface] client .conf (как в AllowedIPs на сервере)."""
    try:
        cfg = read_wg_conf(client_path)
    except Exception as e:
        print(f"[remove] Не удалось прочитать {client_path}: {e}", file=sys.stderr)
        sys.exit(1)
    if "Interface" not in cfg:
        print(f"[remove] В {client_path} нет секции [Interface].", file=sys.stderr)
        sys.exit(1)
    address = cfg["Interface"].get("Address", "").strip()
    if not address:
        print(f"[remove] В {client_path} нет Address в [Interface].", file=sys.stderr)
        sys.exit(1)
    return address


def resolve_remove_targets(args) -> tuple[pathlib.Path, str, pathlib.Path | None]:
    """(server_conf, ip для remove/registry, client_conf или None)."""
    client_conf = getattr(args, "client_conf", None)
    if client_conf:
        client_path = pathlib.Path(client_conf)
        if not client_path.is_file():
            print(f"[remove] Нет файла {client_path}", file=sys.stderr)
            sys.exit(1)
        explicit = pathlib.Path(args.server_conf) if args.server_conf else None
        try:
            server_path = resolve_server_conf(client_path, explicit)
        except FileNotFoundError as e:
            print(f"[remove] {e}", file=sys.stderr)
            sys.exit(1)
        except RuntimeError as e:
            print(f"[remove] {e}", file=sys.stderr)
            sys.exit(1)
        ip_str = client_address_from_conf(client_path)
        print(f"[remove] Клиентский конфиг: {client_path}")
        print(f"[remove] Серверный конфиг: {server_path}")
        print(f"[remove] IP из Address: {ip_str}")
        return server_path, ip_str, client_path

    if not args.server_conf or not args.ip:
        print(
            "[remove] Укажите client_*.conf или пару --server-conf и --ip.",
            file=sys.stderr,
        )
        sys.exit(1)
    server_path = pathlib.Path(args.server_conf)
    if not server_path.is_file():
        print(f"[remove] Нет файла {server_path}", file=sys.stderr)
        sys.exit(1)
    return server_path, args.ip, None


def backup_client_conf(client_path: pathlib.Path) -> pathlib.Path:
    bak_path = client_path.with_suffix(client_path.suffix + ".bak")
    if bak_path.exists():
        bak_path.unlink()
    client_path.rename(bak_path)
    return bak_path


def cmd_remove(args):
    server_path, ip_str, client_path = resolve_remove_targets(args)

    try:
        client_ip = parse_client_ip(ip_str)
    except ValueError as e:
        print(f"[remove] Некорректный IP: {e}", file=sys.stderr)
        sys.exit(1)

    server_priv, address, listen_port, server_obfs, peers, iface_has_mtu, interface_hooks, _ = (
        parse_server_conf(server_path)
    )

    peer = find_peer_by_ip(peers, client_ip)
    if peer is None:
        print(
            f"[remove] Клиент с IP {client_ip} не найден в {server_path}.",
            file=sys.stderr,
        )
        sys.exit(1)

    remaining = [p for p in peers if p is not peer]
    print(f"[remove] Удаляю peer {client_ip} (PublicKey = {peer['public_key'][:8]}…)")

    new_server_text = generate_server_config(
        server_priv=server_priv,
        server_ip=address,
        listen_port=listen_port,
        server_obfs=server_obfs,
        peers=peers_for_server_config(remaining),
        include_mtu=iface_has_mtu,
        interface_hooks=interface_hooks,
    )
    print("[remove] Серверный конфиг обновлён.")
    write_server_conf(server_path, new_server_text)

    registry_path = registry_json_path(server_path)
    if remove_client_by_ip(registry_path, ip_str):
        print(f"[remove] Запись удалена из {registry_path}")
    else:
        print(
            f"[remove] Предупреждение: IP {client_ip} не найден в {registry_path.name}",
            file=sys.stderr,
        )

    if client_path is not None:
        bak_path = backup_client_conf(client_path)
        print(f"[remove] Клиентский конфиг переименован: {bak_path}")

    print("[remove] Перезапусти awg0 на входной ноде:")
    print("  sudo awg-quick down awg0 && sudo awg-quick up awg0")


def main():
    parser = argparse.ArgumentParser(
        description="Управление конфигами AmneziaWG 3.1 (init / add / remove)."
    )
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    p_init = subparsers.add_parser(
        "init", help="Создать server .conf и пустой <server>.json (без клиентов)."
    )
    p_init.add_argument(
        "--listen-port",
        type=int,
        default=443,
        help="Порт сервера UDP (рекомендуется 443 для AWG 2.0).",
    )
    p_init.add_argument(
        "--subnet", default="10.10.1.0/24", help="Подсеть WG, напр. 10.10.1.0/24."
    )
    p_init.add_argument(
        "--server-host",
        default=None,
        help="Хост входной ноды (# ServerHost = … в server.conf; Endpoint при add без --server-host).",
    )
    p_init.add_argument(
        "--dns",
        default=None,
        help="DNS для клиентов (# DNS = … в server.conf или 1.1.1.1,1.0.0.1; при add без --dns).",
    )
    p_init.add_argument(
        "--server-name",
        default=None,
        help="Имя по умолчанию в AmneziaVPN (# ServerName = … в server.conf).",
    )
    p_init.add_argument(
        "--mtu",
        default=None,
        type=int,
        help="MTU, например 1280 (опционально; без флага строка MTU не пишется в server.conf).",
    )
    add_server_obfs_args(p_init)
    p_init.add_argument(
        "--output-dir",
        default=None,
        help="Каталог вывода (по умолчанию amneziawg_3_<ts>).",
    )
    p_init.set_defaults(func=cmd_init)

    p_add = subparsers.add_parser(
        "add", help="Добавить нового клиента к существующему server.conf."
    )
    p_add.add_argument(
        "--server-conf",
        required=True,
        help="Путь к server.conf (на локальной машине).",
    )
    p_add.add_argument(
        "--server-host",
        default=None,
        help="Хост для Endpoint (если не задан — из server # ServerHost, иначе ошибка).",
    )
    p_add.add_argument(
        "--name",
        required=True,
        help="Имя клиента (файл client_<name>.conf).",
    )
    p_add.add_argument(
        "--description",
        default=None,
        help="Метка в <server>.json (только dashboard). По умолчанию пусто.",
    )
    p_add.add_argument(
        "--dns",
        default=None,
        help=(
            f"DNS в клиентском конфиге (--dns на add, иначе # DNS в server, "
            f"иначе {_DEFAULT_CLIENT_DNS})."
        ),
    )
    add_client_obfs_args(p_add)
    p_add.add_argument(
        "--output-dir",
        default=None,
        help="Каталог для client_<name>.conf (по умолчанию рядом с server.conf).",
    )
    p_add.set_defaults(func=cmd_add)

    p_remove = subparsers.add_parser(
        "remove",
        help="Удалить клиента: client_*.conf или --server-conf + --ip.",
    )
    p_remove.add_argument(
        "client_conf",
        nargs="?",
        default=None,
        metavar="client.conf",
        help=(
            "Клиентский .conf: IP из Address, server_*.conf рядом "
            "(если один; иначе добавьте --server-conf)."
        ),
    )
    p_remove.add_argument(
        "--server-conf",
        default=None,
        help="Путь к server_*.conf (с --ip; или уточнение при нескольких server_*.conf).",
    )
    p_remove.add_argument(
        "--ip",
        default=None,
        help="IP клиента (как в AllowedIPs), напр. 10.10.1.5 или 10.10.1.5/32.",
    )
    p_remove.set_defaults(func=cmd_remove)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
