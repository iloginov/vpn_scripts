#!/usr/bin/env python3
"""
AmneziaVPN / AmneziaWG: .vpn, QR, декодирование.

    python awg_gen.py client.conf
    python awg_gen.py client.conf --server-name "Russia 2.0"
    python awg_gen.py client.conf --server-conf server_awg0.conf

Подкоманды:
    python awg_gen.py vpn client.conf
    python awg_gen.py awg-qr client.conf
    python awg_gen.py vpn-to-wg client.vpn
    python awg_gen.py vpn-qr client.vpn
    python awg_gen.py decode-vpn test.vpn
    python awg_gen.py decode-qr test_qr1.png test_qr2.png
    python awg_gen.py awg-qr client.conf --method chunked
    python awg_gen.py decode-awg-qr client_awg_qr.png client_awg_qr2.png
"""

from __future__ import annotations

import sys
from pathlib import Path

_PKG = Path(__file__).resolve().parent / "packages"
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

from amnezia_export.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
