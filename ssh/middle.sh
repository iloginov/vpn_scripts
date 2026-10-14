#!/usr/bin/env bash
# docs/deploy_middle.md, разделы 6–8. Хуки до первого up, отдельные ip rule не добавляем.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

setup_middle_awg() {
  local conf="$SSH_DIR/artifacts/client_middle.conf"
  local host dir
  [[ -f $conf ]] || die "нет $conf"
  validate_ssh "$MIDDLE_SSH"
  validate_subnet "$MIDDLE_SUBNET"
  host=$(host_of "$MIDDLE_SSH")
  dir=$(init_awg "$MIDDLE_SSH" "$MIDDLE_NAME" "$MIDDLE_SUBNET" "$host")
  append_server_hooks "$MIDDLE_SSH" "$dir" "$MIDDLE_SUBNET"
  add_peer "$MIDDLE_SSH" "$dir" entry
  fetch_client "$MIDDLE_SSH" "$dir" entry
  save_dir middle "$dir"

  stage_client "$MIDDLE_SSH" "$conf" client_middle.conf awg1.conf
  prepare_client_iface "$MIDDLE_SSH" etc awg1.conf awg1 clientroute 1
  apply_sysctl "$MIDDLE_SSH"
  install_nft "$MIDDLE_SSH" "$(forwarder_nft "$MIDDLE_SUBNET")"
  ensure_rt "$MIDDLE_SSH" 100 clientroute
  copy_server_conf "$MIDDLE_SSH" "$dir"
  awg_up "$MIDDLE_SSH" awg0
  awg_up "$MIDDLE_SSH" awg1
  wait_handshake "$MIDDLE_SSH" awg1
  assert_clientroute "$MIDDLE_SSH" "$MIDDLE_SUBNET" awg1
  log "middle готов: ~/awg/$dir, клиент для entry — ssh/artifacts/client_entry.conf"
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  load_env
  require_vars MIDDLE_SSH MIDDLE_SUBNET MIDDLE_NAME
  refuse_fresh "$MIDDLE_SSH"
  base_setup "$MIDDLE_SSH"
  setup_middle_awg
fi
