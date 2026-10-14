#!/usr/bin/env bash
# docs/deploy_exit.md без пира. Пир entry добавляет add-ru.sh.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

load_env
require_vars RU_EXIT_SSH RU_EXIT_SUBNET RU_EXIT_WAN RU_EXIT_NAME
validate_ssh "$RU_EXIT_SSH"
validate_subnet "$RU_EXIT_SUBNET"
validate_wan "$RU_EXIT_WAN"

refuse_fresh "$RU_EXIT_SSH"
base_setup "$RU_EXIT_SSH"

dir=$(init_awg "$RU_EXIT_SSH" "$RU_EXIT_NAME" "$RU_EXIT_SUBNET" "$(host_of "$RU_EXIT_SSH")")
save_dir ru-exit "$dir"
apply_sysctl "$RU_EXIT_SSH"
install_nft "$RU_EXIT_SSH" "$(exit_nft "$RU_EXIT_SUBNET" "$RU_EXIT_WAN")"
enable_awg0_from_dir "$RU_EXIT_SSH" "$dir"

log "российский exit готов: ~/awg/$dir, пира ещё нет"
