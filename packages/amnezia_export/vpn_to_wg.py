"""AmneziaVPN .vpn → AmneziaWG .conf."""

from __future__ import annotations

import configparser
import io
import json
from pathlib import Path

from amnezia_export.vpn_payload import read_vpn_payload


def make_wireguard_config_from_amnezia_json(amnezia_config_json: str) -> str:
    amnezia_config = json.loads(amnezia_config_json)

    last_config_json = amnezia_config["containers"][-1]["awg"]["last_config"]
    last_config = json.loads(last_config_json)
    wireguard_config = last_config["config"]

    wireguard_config = wireguard_config.replace("$PRIMARY_DNS", amnezia_config["dns1"])
    wireguard_config = wireguard_config.replace(
        "$SECONDARY_DNS", amnezia_config["dns2"]
    )

    config = configparser.ConfigParser()
    config.optionxform = lambda optionstr: optionstr
    config.read_string(wireguard_config)

    config["Interface"]["MTU"] = last_config["mtu"]
    config["Interface"]["ListenPort"] = f"{last_config['port']}"

    with io.StringIO() as buf:
        config.write(buf)
        return buf.getvalue()


def vpn_to_wg_conf(vpn_file: Path, *, output: Path | None = None) -> Path:
    amnezia_config_json = read_vpn_payload(vpn_file).decode()
    wireguard_config = make_wireguard_config_from_amnezia_json(amnezia_config_json)
    out = output or vpn_file.with_suffix(".conf")
    out.write_text(wireguard_config, encoding="utf-8")
    return out
