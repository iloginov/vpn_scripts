#!/usr/bin/env bash
# docs/deploy_exit.md. Пир по умолчанию transit; для новой тройки — --peer middle.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

peer=transit
while [[ $# -gt 0 ]]; do
  case $1 in
    --peer)
      [[ $# -ge 2 ]] || die "--peer требует имя"
      peer=$2
      shift 2
      ;;
    *)
      die "неизвестный аргумент: $1"
      ;;
  esac
done
validate_peer "$peer"

load_env
require_vars EXIT_SSH EXIT_SUBNET EXIT_WAN EXIT_NAME
validate_ssh "$EXIT_SSH"
validate_subnet "$EXIT_SUBNET"
validate_wan "$EXIT_WAN"

refuse_fresh "$EXIT_SSH"
base_setup "$EXIT_SSH"

dir=$(init_awg "$EXIT_SSH" "$EXIT_NAME" "$EXIT_SUBNET" "$(host_of "$EXIT_SSH")")
add_peer "$EXIT_SSH" "$dir" "$peer"
fetch_client "$EXIT_SSH" "$dir" "$peer"
save_dir exit "$dir"

apply_sysctl "$EXIT_SSH"
install_nft "$EXIT_SSH" "$(exit_nft "$EXIT_SUBNET" "$EXIT_WAN")"
enable_awg0_from_dir "$EXIT_SSH" "$dir"

log "exit готов: ~/awg/$dir, клиент ssh/artifacts/client_${peer}.conf"
