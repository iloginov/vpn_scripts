"""AmneziaWG 3.1 obfuscation toolkit (порт Any-Tech-ARCHITECT).

docs/awg2_protocol_params.md, docs/awg3_protocol_params.md.
"""

from vpn_awg.gen_config import GenConfig, INTENSITIES, JUNK_LEVELS, MIMIC_PROFILES
from vpn_awg.obfs import Awg2ServerObfs, generate_awg2_server_obfs, obfs_summary

__all__ = [
    "GenConfig",
    "MIMIC_PROFILES",
    "INTENSITIES",
    "JUNK_LEVELS",
    "Awg2ServerObfs",
    "generate_awg2_server_obfs",
    "obfs_summary",
]
