#!/usr/bin/env bash
# docs/deploy_transit.md. Интерфейсы в конце остаются поднятыми:
# финальный down в гайде только показывает PostDown.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

uplink=client_transit.conf
while [[ $# -gt 0 ]]; do
  case $1 in
    --uplink)
      [[ $# -ge 2 ]] || die "--uplink требует файл"
      uplink=$2
      shift 2
      ;;
    *)
      die "неизвестный аргумент: $1"
      ;;
  esac
done

if [[ $uplink == */* ]]; then
  conf=$uplink
  home_name=$(basename "$uplink")
else
  home_name=$uplink
  conf=""
fi
[[ $home_name =~ ^[A-Za-z0-9._-]+$ ]] || die "имя клиентского конфига: $home_name"

load_env
require_vars ENTRY_SSH ENTRY_SUBNET ENTRY_NAME
validate_ssh "$ENTRY_SSH"
validate_subnet "$ENTRY_SUBNET"
[[ -n $conf ]] || conf="$SSH_DIR/artifacts/$home_name"
[[ -f $conf ]] || die "нет $conf"

refuse_fresh "$ENTRY_SSH"
base_setup "$ENTRY_SSH"

dir=$(init_awg "$ENTRY_SSH" "$ENTRY_NAME" "$ENTRY_SUBNET" "$(host_of "$ENTRY_SSH")")
copy_server_conf "$ENTRY_SSH" "$dir"
stage_client "$ENTRY_SSH" "$conf" "$home_name" awg1.conf
prepare_client_iface "$ENTRY_SSH" etc awg1.conf awg1 clientroute 0

apply_sysctl "$ENTRY_SSH"
install_nft "$ENTRY_SSH" "$(forwarder_nft "$ENTRY_SUBNET")"

awg_up "$ENTRY_SSH" awg0
awg_up "$ENTRY_SSH" awg1
ensure_rt "$ENTRY_SSH" 100 clientroute
add_manual_clientroute "$ENTRY_SSH" "$ENTRY_SUBNET"

append_server_hooks "$ENTRY_SSH" "$dir" "$ENTRY_SUBNET"
prepare_client_iface "$ENTRY_SSH" etc awg1.conf awg1 clientroute 1
copy_server_conf "$ENTRY_SSH" "$dir"

awg_down "$ENTRY_SSH" awg0
awg_down "$ENTRY_SSH" awg1
awg_up "$ENTRY_SSH" awg0
awg_up "$ENTRY_SSH" awg1
wait_handshake "$ENTRY_SSH" awg1
assert_clientroute "$ENTRY_SSH" "$ENTRY_SUBNET" awg1
save_dir entry "$dir"

log "entry готов: ~/awg/$dir, awg0 и awg1 подняты"
