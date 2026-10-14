#!/usr/bin/env bash
# docs/deploy_middle.md целиком. На exit заход только через ssh -J middle.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/middle.sh"

load_env
require_vars MIDDLE_SSH MIDDLE_SUBNET MIDDLE_NAME EXIT_SSH ENTRY_SSH ENTRY_SUBNET
validate_ssh "$MIDDLE_SSH"
validate_ssh "$EXIT_SSH"
validate_ssh "$ENTRY_SSH"
validate_subnet "$MIDDLE_SUBNET"
validate_subnet "$ENTRY_SUBNET"

require_awg0 "$ENTRY_SSH"
refuse_fresh "$MIDDLE_SSH"
base_setup "$MIDDLE_SSH"

(
  SSH_JUMP=$MIDDLE_SSH
  dir=$(find_awg_dir "$EXIT_SSH" exit)
  if peer_conf_exists "$EXIT_SSH" "$dir" middle; then
    log "$EXIT_SSH: пир middle уже есть"
  else
    add_peer "$EXIT_SSH" "$dir" middle
  fi
  sync_awg0 "$EXIT_SSH" "$dir"
  fetch_client "$EXIT_SSH" "$dir" middle
)

setup_middle_awg

log "$ENTRY_SSH: меняю только awg1"
remote_bash "$ENTRY_SSH" root <<'EOF'
set -euo pipefail
cp -a /etc/amnezia/amneziawg/awg1.conf /etc/amnezia/amneziawg/awg1.conf.bak
EOF
stage_client "$ENTRY_SSH" "$SSH_DIR/artifacts/client_entry.conf" client_entry.conf awg1.conf
prepare_client_iface "$ENTRY_SSH" etc awg1.conf awg1 clientroute 1
awg_down "$ENTRY_SSH" awg1
awg_up "$ENTRY_SSH" awg1
wait_handshake "$ENTRY_SSH" awg1
assert_clientroute "$ENTRY_SSH" "$ENTRY_SUBNET" awg1

log "дальше exit только так: ssh -J $MIDDLE_SSH $EXIT_SSH"
