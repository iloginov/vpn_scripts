#!/usr/bin/env bash
# Добавить клиента на этой ноде: add + .vpn/QR + reload awg0.
#
#   ./awg_add.sh ilya_pixel
#   ./awg_add.sh ilya_pixel --description "Pixel 8"
#
set -euo pipefail

cd "$(dirname "$0")"

usage() {
  echo "Usage: $0 NAME [extra args for awg_manage.py add ...]" >&2
  exit 2
}

[[ $# -ge 1 ]] || usage
[[ "$1" != "-h" && "$1" != "--help" ]] || usage

NAME=$1
shift

if [[ "$NAME" == */* || "$NAME" == *..* ]]; then
  echo "Имя клиента не должно содержать '/' или '..'" >&2
  exit 1
fi

if [[ -f amneziawg_3_entry/server_awg0.conf ]]; then
  AWG_DIR=amneziawg_3_entry
elif [[ -f amneziawg_3_exit/server_awg0.conf ]]; then
  AWG_DIR=amneziawg_3_exit
else
  echo "Нет server_awg0.conf в amneziawg_3_entry/ или amneziawg_3_exit/" >&2
  exit 1
fi

if [[ ! -f .venv/bin/activate ]]; then
  echo "Нет .venv в $(pwd)" >&2
  exit 1
fi

# shellcheck source=/dev/null
source .venv/bin/activate

python awg_manage.py add \
  --server-conf "$AWG_DIR/server_awg0.conf" \
  --name "$NAME" \
  --mimic quic_initial \
  --intensity medium \
  --junk-level 5 \
  "$@"

CLIENT="$AWG_DIR/client_${NAME}.conf"
python awg_gen.py "$CLIENT"

sudo cp "$AWG_DIR/server_awg0.conf" /etc/amnezia/amneziawg/awg0.conf
sudo awg-quick down awg0 && sudo awg-quick up awg0

echo
echo "Готово: $CLIENT"