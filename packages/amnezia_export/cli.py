"""CLI: подкоманды для генерации и декодирования."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from amnezia_export.awg_qr import write_awg_qr_pngs
from amnezia_export.decode_awg_qr import decode_awg_qr_pngs
from amnezia_export.decode_qr import decode_vpn_qr_pngs
from amnezia_export.decode_vpn import decode_vpn_file
from amnezia_export.vpn import write_vpn_file
from amnezia_export.vpn_qr import write_vpn_qr_pngs
from amnezia_export.vpn_to_wg import vpn_to_wg_conf

SUBCOMMANDS = frozenset(
    {
        "all",
        "vpn",
        "vpn-qr",
        "awg-qr",
        "vpn-to-wg",
        "decode-vpn",
        "decode-qr",
        "decode-awg-qr",
    }
)


def _cmd_all(args: argparse.Namespace) -> int:
    vpn_path = write_vpn_file(
        args.client_conf,
        server_name=args.server_name,
        server_conf=args.server_conf,
    )
    print(f"✔ {vpn_path.name}")
    for p in write_awg_qr_pngs(args.client_conf, method=args.method):
        print(f"✔ {p.name}")
    for p in write_vpn_qr_pngs(vpn_path):
        print(f"✔ {p.name}")
    return 0


def _cmd_vpn(args: argparse.Namespace) -> int:
    out = write_vpn_file(
        args.client_conf,
        server_name=args.server_name,
        server_conf=args.server_conf,
    )
    print(f"✔ {out.name}")
    return 0


def _cmd_vpn_qr(args: argparse.Namespace) -> int:
    err = 0
    for vpn in args.vpn_files:
        try:
            for p in write_vpn_qr_pngs(vpn):
                print(f"✔ {p.name}")
        except Exception as exc:
            print(f"✖ {vpn.name}: {exc}", file=sys.stderr)
            err = 1
    return err


def _cmd_awg_qr(args: argparse.Namespace) -> int:
    err = 0
    for conf in args.client_confs:
        try:
            for p in write_awg_qr_pngs(
                conf,
                method=args.method,
                output_png=args.output,
            ):
                print(f"✔ {p.name}")
        except Exception as exc:
            print(f"✖ {conf.name}: {exc}", file=sys.stderr)
            err = 1
    return err


def _cmd_vpn_to_wg(args: argparse.Namespace) -> int:
    if args.output is not None and len(args.vpn_files) != 1:
        print("✖ --output допустим только с одним .vpn файлом", file=sys.stderr)
        return 1
    err = 0
    for vpn in args.vpn_files:
        try:
            out = vpn_to_wg_conf(vpn, output=args.output)
            print(f"✔ {vpn.name} → {out.name}")
        except Exception as exc:
            print(f"✖ {vpn.name}: {exc}", file=sys.stderr)
            err = 1
    return err


def _cmd_decode_vpn(args: argparse.Namespace) -> int:
    if args.output is not None and len(args.vpn_files) != 1:
        print("✖ --output допустим только с одним .vpn файлом", file=sys.stderr)
        return 1
    err = 0
    for vpn in args.vpn_files:
        try:
            out = decode_vpn_file(vpn, output=args.output)
            print(f"✔ {vpn.name} → {out.name}")
        except Exception as exc:
            print(f"✖ {vpn.name}: {exc}", file=sys.stderr)
            err = 1
    return err


def _cmd_decode_qr(args: argparse.Namespace) -> int:
    try:
        for out in decode_vpn_qr_pngs(args.images, output=args.output):
            print(f"✔ → {out.name}")
        return 0
    except Exception as exc:
        print(f"✖ {exc}", file=sys.stderr)
        return 1


def _cmd_decode_awg_qr(args: argparse.Namespace) -> int:
    try:
        for out in decode_awg_qr_pngs(args.images, output=args.output):
            print(f"✔ → {out.name}")
        return 0
    except Exception as exc:
        print(f"✖ {exc}", file=sys.stderr)
        return 1


def _add_server_conf(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--server-conf",
        type=Path,
        default=None,
        help="server_*.conf (если в папке не ровно один — обязателен)",
    )


def _add_qr_method(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--method",
        choices=("chunked", "raw"),
        default="raw",
        type=str,
        help="raw: .conf в QR (AmneziaWG iOS); chunked: magic 1984, до 850 байт/чанк (AmneziaVPN)",
    )


def _add_decode_output(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="имя результирующего файла (иначе из общего префикса имён входных)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AmneziaVPN / AmneziaWG: генерация и декодирование",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_common_vpn(p: argparse.ArgumentParser) -> None:
        p.add_argument("client_conf", type=Path)
        p.add_argument(
            "--server-name",
            default=None,
            help=(
                "Имя в AmneziaVPN (vpn://). "
                "Иначе # ServerName в server.conf, иначе Russia 2.0."
            ),
        )
        _add_server_conf(p)

    all_p = sub.add_parser("all", help=".vpn + awg_qr + vpn_qr (как awg_gen.sh)")
    add_common_vpn(all_p)
    _add_qr_method(all_p)
    all_p.set_defaults(func=_cmd_all)

    vpn_p = sub.add_parser("vpn", help="client.conf → .vpn")
    add_common_vpn(vpn_p)
    vpn_p.set_defaults(func=_cmd_vpn)

    vqr_p = sub.add_parser("vpn-qr", help=".vpn → *_qr.png [*_qr2.png …]")
    vqr_p.add_argument("vpn_files", nargs="+", type=Path)
    vqr_p.set_defaults(func=_cmd_vpn_qr)

    aqr_p = sub.add_parser("awg-qr", help="client.conf → *_awg_qr.png [*_awg_qr2.png …]")
    aqr_p.add_argument("client_confs", nargs="+", type=Path)
    aqr_p.add_argument("-o", "--output", type=Path, default=None)
    _add_qr_method(aqr_p)
    aqr_p.set_defaults(func=_cmd_awg_qr)

    vtw_p = sub.add_parser("vpn-to-wg", help=".vpn → .conf (AmneziaWG)")
    vtw_p.add_argument("vpn_files", nargs="+", type=Path)
    vtw_p.add_argument("-o", "--output", type=Path, default=None)
    vtw_p.set_defaults(func=_cmd_vpn_to_wg)

    dv_p = sub.add_parser("decode-vpn", help=".vpn → <name>.vpn.decoded")
    dv_p.add_argument("vpn_files", nargs="+", type=Path)
    _add_decode_output(dv_p)
    dv_p.set_defaults(func=_cmd_decode_vpn)

    dqr_p = sub.add_parser("decode-qr", help="VPN QR PNG → <name>.qr.decoded")
    dqr_p.add_argument("images", nargs="+", type=Path)
    _add_decode_output(dqr_p)
    dqr_p.set_defaults(func=_cmd_decode_qr)

    da_p = sub.add_parser(
        "decode-awg-qr",
        help="AWG QR PNG → <name>.awg_qr.decoded (chunked/raw, несколько PNG)",
    )
    da_p.add_argument("images", nargs="+", type=Path)
    _add_decode_output(da_p)
    da_p.set_defaults(func=_cmd_decode_awg_qr)

    return parser


def main(argv: list[str] | None = None) -> int:
    raw = list(argv) if argv is not None else sys.argv[1:]
    if raw and raw[0] not in SUBCOMMANDS and not raw[0].startswith("-"):
        raw = ["all", *raw]
    args = build_parser().parse_args(raw)
    return int(args.func(args))
