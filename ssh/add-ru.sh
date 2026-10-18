#!/usr/bin/env bash
# docs/deploy_entry_ru_exit.md, раздел «Уже работающий entry».
# awg0 на время down/up гаснет: так снимаются старые правила без приоритета.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

load_env
require_vars ENTRY_SSH ENTRY_SUBNET RU_EXIT_SSH RU_EXIT_SUBNET
RU_PEER=${RU_PEER:-entry}
validate_ssh "$ENTRY_SSH"
validate_ssh "$RU_EXIT_SSH"
validate_subnet "$ENTRY_SUBNET"
validate_subnet "$RU_EXIT_SUBNET"
validate_peer "$RU_PEER"

py=python3
[[ -x $REPO/.venv/bin/python ]] && py=$REPO/.venv/bin/python
"$py" - "$ENTRY_SUBNET" "$RU_EXIT_SUBNET" <<'PY'
import ipaddress
import sys
a = ipaddress.ip_network(sys.argv[1], strict=False)
b = ipaddress.ip_network(sys.argv[2], strict=False)
if a.overlaps(b):
    sys.stderr.write("подсети entry и ru_exit пересекаются\n")
    sys.exit(1)
PY

require_awg0 "$ENTRY_SSH"
require_awg0 "$RU_EXIT_SSH"

remote_bash "$ENTRY_SSH" root <<'EOF'
set -euo pipefail
rules=$(ip -4 rule show)
if printf '%s\n' "$rules" | grep -q 'fwmark 0x100/0x100' \
   && printf '%s\n' "$rules" | grep -q '^2000:'; then
  echo "правила fwmark и 2000 уже стоят, править нечего" >&2
  exit 1
fi
if [[ -f /etc/amnezia/amneziawg/awg2.conf ]] \
   && ! grep -q 'table ruroute' /etc/amnezia/amneziawg/awg2.conf; then
  echo "awg2.conf есть, но без table ruroute" >&2
  exit 1
fi
EOF

log "$ENTRY_SSH: rp_filter и старая метка"
remote_bash "$ENTRY_SSH" root <<'EOF'
set -euo pipefail
all=$(sysctl -n net.ipv4.conf.all.rp_filter)
def=$(sysctl -n net.ipv4.conf.default.rp_filter)
echo "rp_filter all=$all default=$def"
if [[ $all != 2 || $def != 2 ]]; then
  printf 'net.ipv4.conf.all.rp_filter=2\nnet.ipv4.conf.default.rp_filter=2\n' \
    > /etc/sysctl.d/99-rp-filter.conf
  sysctl --system >/dev/null
fi
if systemctl list-unit-files --type=service | grep -q '^awg-marked-rule.service'; then
  systemctl disable --now awg-marked-rule.service || true
fi
while ip -4 rule del priority 1100 fwmark 0x100/0x100 iif awg0 lookup ruroute 2>/dev/null; do
  :
done
while ip -4 rule del priority 1100 fwmark 0x100/0x100 iif awg0 lookup main 2>/dev/null; do
  :
done
EOF

ru_dir=$(find_awg_dir "$RU_EXIT_SSH" ru-exit)
if peer_conf_exists "$RU_EXIT_SSH" "$ru_dir" "$RU_PEER"; then
  log "$RU_EXIT_SSH: пир $RU_PEER уже есть"
else
  add_peer "$RU_EXIT_SSH" "$ru_dir" "$RU_PEER"
fi
sync_awg0 "$RU_EXIT_SSH" "$ru_dir"
scp_from "$RU_EXIT_SSH" "awg/${ru_dir}/client_${RU_PEER}.conf" "$SSH_DIR/artifacts/client_ru.conf"
log "файл $SSH_DIR/artifacts/client_ru.conf"

ensure_rt "$ENTRY_SSH" 101 ruroute
scp_to "$SSH_DIR/artifacts/client_ru.conf" "$ENTRY_SSH" awg/client_ru.conf
prepare_client_iface "$ENTRY_SSH" home client_ru.conf awg2 ruroute 1
promote_home_conf "$ENTRY_SSH" client_ru.conf awg2.conf
awg_up "$ENTRY_SSH" awg2
wait_handshake "$ENTRY_SSH" awg2
remote_bash "$ENTRY_SSH" root <<'EOF'
set -euo pipefail
ip -4 route show table ruroute
ip -4 route show table ruroute | grep -F -q 'default dev awg2'
EOF

yaml=$(mktemp)
ru_entry_yaml "$ENTRY_SUBNET" > "$yaml"
scp_to "$yaml" "$ENTRY_SSH" awg/entry.yaml
rm -f "$yaml"
scp_to "$REPO/awg_policy.py" "$ENTRY_SSH" awg/awg_policy.py
scp_args=("${SSH_OPTS[@]}")
scp -r "${scp_args[@]}" "$REPO/packages" "$ENTRY_SSH:awg/"

log "$ENTRY_SSH: awg_policy fetch и render"
remote_bash "$ENTRY_SSH" user <<'EOF'
set -euo pipefail
cd "$HOME/awg"
find . -path "./.venv" -prune -o -name "__pycache__" -print | xargs rm -rf
. .venv/bin/activate
pip install -r requirements.txt
python awg_policy.py fetch entry.yaml --cache /tmp/awg-policy-cache
python awg_policy.py render entry.yaml --dest /tmp/awg-policy --cache /tmp/awg-policy-cache
EOF

entry_dir=$(find_awg_dir "$ENTRY_SSH" entry)
replace_server_hooks "$ENTRY_SSH" "awg/${entry_dir}/server_awg0.conf" /tmp/awg-policy/hooks/awg0.hooks

log "$ENTRY_SSH: nft -c, рабочий ruleset пока старый"
remote_bash "$ENTRY_SSH" root <<'EOF'
set -euo pipefail
mkdir -p /etc/awg-ru-split
cp /tmp/awg-policy/rules.nft /etc/awg-ru-split/rules.nft
nft -c -f /tmp/awg-policy/nftables.conf
EOF

log "$ENTRY_SSH: опускаю awg0, клиенты на это время отвалятся"
awg_down "$ENTRY_SSH" awg0
remote_bash "$ENTRY_SSH" root <<'EOF'
set -euo pipefail
bad=0
while IFS= read -r line; do
  prio=${line%%:*}
  case $prio in
    0|32766|32767) ;;
    *) echo "осталось правило: $line" >&2; bad=1 ;;
  esac
done < <(ip -4 rule show)
if [[ $bad != 0 ]]; then
  echo "awg0 опущен, новые правила не ставлю" >&2
  exit 1
fi
EOF

copy_server_conf "$ENTRY_SSH" "$entry_dir"
remote_bash "$ENTRY_SSH" root <<'EOF'
set -euo pipefail
cp /tmp/awg-policy/nftables.conf /etc/nftables.conf
nft -f /etc/nftables.conf
systemctl enable nftables
EOF
awg_up "$ENTRY_SSH" awg0

remote_bash "$ENTRY_SSH" root <<'EOF'
set -euo pipefail
mkdir -p /etc/systemd/system/awg-quick@awg0.service.d
cat > /etc/systemd/system/awg-quick@awg0.service.d/10-routing.conf <<'UNIT'
[Unit]
After=nftables.service awg-quick@awg1.service awg-quick@awg2.service
Wants=awg-quick@awg1.service awg-quick@awg2.service
UNIT
systemctl daemon-reload
EOF

remote_bash "$ENTRY_SSH" root <<EOF
set -euo pipefail
rules=\$(ip -4 rule show)
printf '%s\n' "\$rules"
printf '%s\n' "\$rules" | grep -q '^1000:'
printf '%s\n' "\$rules" | grep -q '^1100:'
printf '%s\n' "\$rules" | grep -q 'fwmark 0x100/0x100'
printf '%s\n' "\$rules" | grep -q '^2000:'
printf '%s\n' "\$rules" | grep -q '^2010:'
ip -4 route show table clientroute | grep -F -q 'default dev awg1'
ip -4 route show table ruroute | grep -F -q 'default dev awg2'
probe=\$(python3 -c 'import ipaddress,sys; n=ipaddress.ip_network(sys.argv[1]); hs=list(n.hosts()); print(hs[1] if len(hs)>1 else hs[0])' $(q "$ENTRY_SUBNET"))
plain=\$(ip -4 route get 1.1.1.1 from "\$probe" iif awg0)
marked=\$(ip -4 route get 1.1.1.1 from "\$probe" iif awg0 mark 0x100)
printf '%s\n' "\$plain"
printf '%s\n' "\$marked"
printf '%s\n' "\$plain" | grep -q 'table clientroute'
printf '%s\n' "\$marked" | grep -q 'table ruroute'
EOF

log "российский выход подключён, awg0 снова поднят"
