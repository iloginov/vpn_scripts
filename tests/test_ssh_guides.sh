#!/usr/bin/env bash
# Тексты nft и хуков из гайдов и правка клиентского конфига. Сеть не нужна.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
# shellcheck disable=SC1091
source "$root/ssh/lib.sh"

nft=$(exit_nft 10.10.1.0/24 eth0)
printf '%s\n' "$nft" | grep -q 'ip saddr 10.10.1.0/24 masquerade'
if printf '%s\n' "$nft" | grep 'masquerade' | grep -q 'oifname'; then
  echo "exit: masquerade без oifname" >&2
  exit 1
fi
printf '%s\n' "$nft" | grep -q 'oifname "eth0"'
printf '%s\n' "$nft" | grep -q 'udp dport 443 accept'

fwd=$(forwarder_nft 10.10.2.0/24)
printf '%s\n' "$fwd" | grep -q 'ip saddr 10.10.2.0/24 oifname "awg1" masquerade'

hooks=$(clientroute_hooks 10.10.2.0/24)
printf '%s\n' "$hooks" | grep -q 'prio 1000'
if printf '%s\n' "$hooks" | grep -E -q 'prio 2010|fwmark'; then
  echo "парные хуки без 2010 и fwmark" >&2
  exit 1
fi

printf '%s\n' "$(sshd_text root)" | grep -q 'PermitRootLogin prohibit-password'
printf '%s\n' "$(sshd_text ubuntu)" | grep -q 'PermitRootLogin no'
printf '%s\n' "$(ru_entry_yaml 10.10.2.0/24)" | grep -q 'table: ruroute'
printf '%s\n' "$(ru_entry_yaml 10.10.2.0/24)" | grep -q 'fwmark: 0x100'

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

(
  remote_bash() {
    cat > "$tmp/init.sh"
    printf '%s\n' '[init] Использую каталог: amneziawg_3_20260101_120000'
  }
  got=$(init_awg host "Exit 3.1" 10.10.1.0/24 203.0.113.10)
  [[ $got == amneziawg_3_20260101_120000 ]]
  grep -F -q -- "--server-name Exit\\ 3.1" "$tmp/init.sh"
  grep -q -- "--subnet 10.10.1.0/24" "$tmp/init.sh"
  if grep -q -- '--output-dir' "$tmp/init.sh"; then
    echo "init не должен получать --output-dir" >&2
    exit 1
  fi
)
cat > "$tmp/client.conf" <<'EOF'
[Interface]
PrivateKey = sPriv
Address = 10.10.4.2/32
DNS = 1.1.1.1
MTU = 1280

[Peer]
PublicKey = aGVsbG8=
AllowedIPs = 0.0.0.0/0
Endpoint = 203.0.113.40:443
EOF

remote_bash() { cat > "$tmp/remote.sh"; }
prepare_client_iface host home client.conf awg2 ruroute 1
sed -n "/<<'PY'/,/^PY$/p" "$tmp/remote.sh" | sed '1d;$d' > "$tmp/edit.py"
"$root/.venv/bin/python" - "$tmp/client.conf" awg2 ruroute 1 < "$tmp/edit.py"
grep -q '^# DNS = 1.1.1.1$' "$tmp/client.conf"
grep -q '^Table = off$' "$tmp/client.conf"
grep -q 'default dev awg2 table ruroute' "$tmp/client.conf"
awk 'BEGIN{p=0} /^\[Peer\]/{p=1} p&&/Table = off/{exit 1}' "$tmp/client.conf"

"$root/.venv/bin/python" - "$tmp/client.conf" awg2 ruroute 1 < "$tmp/edit.py"
test "$(grep -c '^Table = off$' "$tmp/client.conf")" -eq 1
test "$(grep -c 'default dev awg2 table ruroute' "$tmp/client.conf")" -eq 2

echo ok
