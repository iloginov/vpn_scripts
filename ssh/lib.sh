# Общие куски ручных гайдов: SSH, базовая подготовка VPS, тексты nft и хуков.
# Скрипты в этом каталоге подключают файл через source. Сам по себе он ничего не делает.

SSH_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=20 -o ServerAliveInterval=30)

log() { printf '==> %s\n' "$*" >&2; }
die() { printf 'ошибка: %s\n' "$*" >&2; exit 1; }

q() { printf '%q' "$1"; }

load_env() {
  SSH_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
  REPO=$(cd "$SSH_DIR/.." && pwd)
  local env_file=${SSH_ENV:-$SSH_DIR/hosts.env}
  [[ -f $env_file ]] || die "нет $env_file (скопируйте ssh/hosts.env.example)"
  # shellcheck disable=SC1090
  source "$env_file"
  mkdir -p "$SSH_DIR/artifacts"
}

host_of() { printf '%s\n' "${1#*@}"; }
user_of() { printf '%s\n' "${1%@*}"; }

validate_ssh() {
  [[ $1 =~ ^[A-Za-z_][A-Za-z0-9_-]*@[A-Za-z0-9][A-Za-z0-9.-]*$ ]] || die "ожидался user@host, получено: $1"
}

validate_subnet() {
  local s=$1 i n
  [[ $s =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})/([0-9]{1,2})$ ]] || die "подсеть: $s"
  for i in 1 2 3 4; do
    n=${BASH_REMATCH[$i]}
    [[ $n -le 255 ]] || die "подсеть: $s"
  done
  [[ ${BASH_REMATCH[5]} -le 32 ]] || die "подсеть: $s"
}

validate_wan() {
  [[ $1 =~ ^[A-Za-z][A-Za-z0-9._-]{0,14}$ ]] || die "интерфейс: $1"
}

validate_peer() {
  [[ $1 =~ ^[A-Za-z0-9_-]+$ ]] || die "имя пира: $1"
}

require_vars() {
  local name
  for name in "$@"; do
    [[ -n ${!name:-} ]] || die "в hosts.env нет $name"
  done
}

ssh_to() {
  local target=$1
  shift
  local args=("${SSH_OPTS[@]}")
  if [[ -n ${SSH_JUMP:-} ]]; then
    args+=(-J "$SSH_JUMP")
  fi
  ssh "${args[@]}" "$target" "$@"
}

ssh_n() {
  local target=$1
  shift
  local args=("${SSH_OPTS[@]}" -n)
  if [[ -n ${SSH_JUMP:-} ]]; then
    args+=(-J "$SSH_JUMP")
  fi
  ssh "${args[@]}" "$target" "$@"
}

# stdin — скрипт для bash на сервере. mode=root запускает его через sudo -n.
remote_bash() {
  local target=$1
  local mode=$2
  local user cmd
  user=$(user_of "$target")
  if [[ $mode == root && $user != root ]]; then
    cmd=(sudo -n bash -s)
  else
    cmd=(bash -s)
  fi
  ssh_to "$target" "${cmd[@]}"
}

wait_ssh() {
  local target=$1
  local tries=${2:-60}
  local i
  sleep 5
  for i in $(seq 1 "$tries"); do
    if ssh_n "$target" true 2>/dev/null; then
      return 0
    fi
    sleep 5
  done
  die "$target: SSH не отвечает"
}

reboot_and_wait() {
  local target=$1
  remote_bash "$target" root <<'EOF' || true
nohup shutdown -r now >/dev/null 2>&1 &
EOF
  wait_ssh "$target" 60
}

restart_ssh() {
  local target=$1
  remote_bash "$target" root <<'EOF' || true
nohup systemctl restart ssh >/dev/null 2>&1 &
EOF
  wait_ssh "$target" 12
}

# --- тексты из гайдов ---

sysctl_text() {
  cat <<'EOF'
net.ipv4.ip_forward=1
# net.ipv6.conf.all.forwarding=1 # если нужно IPv6
EOF
}

fail2ban_text() {
  cat <<'EOF'
[sshd]
enabled = true
port = ssh
maxretry = 5
findtime = 10m
bantime = 1h
EOF
}

# Те же директивы, что гайд вписывает в sshd_config.
# Файл 00- читается раньше cloud-init, иначе первая строка PasswordAuthentication побеждает.
sshd_text() {
  local user=$1
  local permit=no
  [[ $user == root ]] && permit=prohibit-password
  cat <<EOF
PasswordAuthentication yes
PubkeyAuthentication yes
PermitRootLogin $permit
MaxAuthTries 3
LoginGraceTime 2m
MaxStartups 10:30:60
PerSourceMaxStartups 3
EOF
}

exit_nft() {
  local subnet=$1 wan=$2
  cat <<EOF
#!/usr/sbin/nft -f

flush ruleset

table inet filter {
    chain input {
        type filter hook input priority filter; policy drop;

        iif lo accept
        ct state established,related accept

        tcp dport 22 accept
        udp dport 443 accept

        ip protocol icmp accept
        meta l4proto ipv6-icmp accept
    }

    chain forward {
        type filter hook forward priority filter; policy accept;
        ct state established,related accept
    }

    chain output {
        type filter hook output priority filter; policy accept;
    }
}

table ip nat {
    chain postrouting {
        type nat hook postrouting priority srcnat; policy accept;
        ip saddr $subnet masquerade
    }
}

table ip mangle {
    chain forward {
        type filter hook forward priority mangle; policy accept;

        oifname "$wan" tcp flags syn tcp option maxseg size set rt mtu
        iifname "$wan" tcp flags syn tcp option maxseg size set rt mtu

        oifname "awg0" tcp flags syn tcp option maxseg size set rt mtu
        iifname "awg0" tcp flags syn tcp option maxseg size set rt mtu
    }
}
EOF
}

# NAT только в awg1: deploy_transit.md и deploy_middle.md.
forwarder_nft() {
  local subnet=$1
  cat <<EOF
#!/usr/sbin/nft -f

flush ruleset

table inet filter {
    chain input {
        type filter hook input priority filter; policy drop;

        iif lo accept
        ct state established,related accept

        tcp dport 22 accept
        udp dport 443 accept

        ip protocol icmp accept
        meta l4proto ipv6-icmp accept
    }

    chain forward {
        type filter hook forward priority filter; policy accept;
        ct state established,related accept
    }

    chain output {
        type filter hook output priority filter; policy accept;
    }
}

table ip nat {
    chain postrouting {
        type nat hook postrouting priority srcnat; policy accept;
        ip saddr $subnet oifname "awg1" masquerade
    }
}

table ip mangle {
    chain forward {
        type filter hook forward priority mangle; policy accept;

        oifname "awg1" tcp flags syn tcp option maxseg size set rt mtu
        iifname "awg1" tcp flags syn tcp option maxseg size set rt mtu

        oifname "awg0" tcp flags syn tcp option maxseg size set rt mtu
        iifname "awg0" tcp flags syn tcp option maxseg size set rt mtu
    }
}
EOF
}

clientroute_hooks() {
  local subnet=$1
  cat <<EOF
PostUp = ip rule add from $subnet table clientroute 2>/dev/null || true
PostUp = ip rule add iif awg0 table clientroute 2>/dev/null || true
PostUp = ip rule add to $subnet table main prio 1000 2>/dev/null || true

PostDown = ip rule del from $subnet table clientroute 2>/dev/null || true
PostDown = ip rule del iif awg0 table clientroute 2>/dev/null || true
PostDown = ip rule del to $subnet table main prio 1000 2>/dev/null || true
EOF
}

ru_entry_yaml() {
  local subnet=$1
  cat <<EOF
schema: 2

client:
  iface: awg0
  subnet: $subnet
  listen_port: 443

uplink:
  iface: awg1
  table: clientroute
  table_id: 100

rules:
  to_client_priority: 1000
  iif_priority: 2000
  from_priority: 2010

ru_exit:
  iface: awg2
  table: ruroute
  table_id: 101
  fwmark: 0x100
  priority: 1100
  rules_path: /etc/awg-ru-split/rules.nft
EOF
}

# --- подготовка, общий блок гайдов до awg_manage init ---

remote_has_awg0() {
  local target=$1
  local answer
  answer=$(remote_bash "$target" root <<'EOF'
if [[ -f /etc/amnezia/amneziawg/awg0.conf ]]; then printf yes; else printf no; fi
EOF
)
  [[ $answer == yes ]]
}

refuse_awg0() {
  local target=$1
  if remote_has_awg0 "$target"; then
    die "$target: /etc/amnezia/amneziawg/awg0.conf уже есть, повторный init не делаю"
  fi
}

require_awg0() {
  local target=$1
  if ! remote_has_awg0 "$target"; then
    die "$target: нет /etc/amnezia/amneziawg/awg0.conf"
  fi
}

base_setup() {
  local target=$1
  local user
  user=$(user_of "$target")
  validate_ssh "$target"
  log "$target: sudo"
  remote_bash "$target" root <<'EOF'
true
EOF

  log "$target: fail2ban и sshd"
  remote_bash "$target" root <<EOF
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y fail2ban
install -d -m 0755 /etc/fail2ban/jail.d
cat > /etc/fail2ban/jail.d/ssh.conf <<'JAIL'
$(fail2ban_text)
JAIL
systemctl enable fail2ban
systemctl restart fail2ban
fail2ban-client status sshd
install -d -m 0755 /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/00-vpn-hardening.conf <<'SSHD'
$(sshd_text "$user")
SSHD
if ! sshd -t; then
  rm -f /etc/ssh/sshd_config.d/00-vpn-hardening.conf
  exit 1
fi
EOF
  restart_ssh "$target"

  log "$target: unattended-upgrades"
  remote_bash "$target" root <<'EOF'
set -euo pipefail
f=/etc/apt/apt.conf.d/20auto-upgrades
[[ -f $f ]] || { echo "нет $f" >&2; exit 1; }
sed -i -E \
  -e 's/^(APT::Periodic::Update-Package-Lists[[:space:]]+)"1";/\1"0";/' \
  -e 's/^(APT::Periodic::Unattended-Upgrade[[:space:]]+)"1";/\1"0";/' \
  "$f"
systemctl mask apt-daily.timer apt-daily-upgrade.timer
systemctl stop apt-daily.timer apt-daily-upgrade.timer
systemctl mask unattended-upgrades.service
systemctl stop unattended-upgrades.service
for unit in apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service; do
  state=$(systemctl is-enabled "$unit" || true)
  if [[ $state != masked ]]; then
    echo "$unit: $state" >&2
    exit 1
  fi
done
EOF

  log "$target: AmneziaWG"
  local reboot
  reboot=$(remote_bash "$target" root <<'EOF'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
if command -v awg >/dev/null 2>&1 && [[ -d /sys/module/amneziawg ]]; then
  echo no
  exit 0
fi
apt-get update >&2
apt-get install -y software-properties-common "linux-headers-$(uname -r)" >&2
add-apt-repository -y ppa:amnezia/ppa >&2
apt-get update >&2
apt-get install -y amneziawg amneziawg-dkms amneziawg-tools >&2
echo yes
EOF
)
  if [[ $reboot == yes ]]; then
    log "$target: перезагрузка после amneziawg-dkms"
    reboot_and_wait "$target"
  fi

  log "$target: скрипты и venv"
  remote_bash "$target" root <<'EOF'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get install -y python-is-python3 python3-venv zbar-tools
EOF
  remote_bash "$target" user <<'EOF'
set -euo pipefail
mkdir -p "$HOME/awg"
EOF
  local scp_args=("${SSH_OPTS[@]}")
  if [[ -n ${SSH_JUMP:-} ]]; then
    scp_args+=(-o "ProxyJump=${SSH_JUMP}")
  fi
  scp "${scp_args[@]}" \
    "$REPO/awg_manage.py" "$REPO/awg_gen.py" "$REPO/requirements.txt" \
    "$target:awg/"
  scp -r "${scp_args[@]}" "$REPO/packages" "$target:awg/"
  remote_bash "$target" user <<'EOF'
set -euo pipefail
cd "$HOME/awg"
find . -path "./.venv" -prune -o -name "__pycache__" -print | xargs rm -rf
if [[ ! -x .venv/bin/python ]]; then
  python -m venv .venv
fi
. .venv/bin/activate
pip install -r requirements.txt
EOF
}

apply_sysctl() {
  local target=$1
  log "$target: ip_forward"
  remote_bash "$target" root <<EOF
set -euo pipefail
cat > /etc/sysctl.d/99-forwarding.conf <<'SYS'
$(sysctl_text)
SYS
sysctl --system >/dev/null
val=\$(sysctl -n net.ipv4.ip_forward)
echo "net.ipv4.ip_forward=\$val"
[[ \$val == 1 ]] || exit 1
EOF
}

install_nft() {
  local target=$1
  local text=$2
  local tmp
  tmp=$(mktemp)
  printf '%s\n' "$text" > "$tmp"
  scp_to "$tmp" "$target" "/tmp/nftables.conf.vpn"
  rm -f "$tmp"
  remote_bash "$target" root <<'EOF'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get install -y nftables >/dev/null
nft -c -f /tmp/nftables.conf.vpn
install -m 0644 /tmp/nftables.conf.vpn /etc/nftables.conf
nft -f /etc/nftables.conf
systemctl enable --now nftables
nft list ruleset
rm -f /tmp/nftables.conf.vpn
EOF
}

init_awg() {
  local target=$1 name=$2 subnet=$3 server_host=$4
  local out dir
  log "$target: awg_manage init"
  out=$(remote_bash "$target" user <<EOF
set -euo pipefail
cd "\$HOME/awg"
. .venv/bin/activate
python awg_manage.py init \\
  --server-host $(q "$server_host") \\
  --listen-port 443 \\
  --subnet $(q "$subnet") \\
  --mtu 1280 \\
  --mimic quic_initial \\
  --intensity medium \\
  --junk-level 5 \\
  --dns $(q "1.1.1.1,1.0.0.1") \\
  --server-name $(q "$name")
EOF
)
  printf '%s\n' "$out" >&2
  dir=
  local line prefix='[init] Использую каталог: '
  while IFS= read -r line; do
    case $line in
      "$prefix"*) dir=${line#"$prefix"} ;;
    esac
  done <<< "$out"
  dir=${dir%$'\r'}
  dir=${dir%"${dir##*[![:space:]]}"}
  [[ $dir =~ ^amneziawg_3_[A-Za-z0-9_-]+$ ]] || die "$target: init не назвал каталог ($dir)"
  printf '%s\n' "$dir"
}

add_peer() {
  local target=$1 dir=$2 peer=$3
  local out
  log "$target: пир $peer"
  out=$(remote_bash "$target" user <<EOF
set -euo pipefail
cd "\$HOME/awg"
. .venv/bin/activate
python awg_manage.py add \\
  --server-conf $(q "$dir/server_awg0.conf") \\
  --name $(q "$peer") \\
  --mimic quic_initial \\
  --intensity medium \\
  --junk-level 5
EOF
)
  printf '%s\n' "$out" >&2
}

fetch_client() {
  local target=$1 dir=$2 peer=$3
  local dest="$SSH_DIR/artifacts/client_${peer}.conf"
  scp_from "$target" "awg/${dir}/client_${peer}.conf" "$dest"
  log "файл $dest"
}

save_dir() {
  printf '%s\n' "$2" > "$SSH_DIR/artifacts/$1.dir"
}

scp_from() {
  local target=$1 remote_path=$2 local_path=$3
  local args=("${SSH_OPTS[@]}")
  if [[ -n ${SSH_JUMP:-} ]]; then
    args+=(-o "ProxyJump=${SSH_JUMP}")
  fi
  scp "${args[@]}" "$target:$remote_path" "$local_path"
}

scp_to() {
  local local_path=$1 target=$2 remote_path=$3
  local args=("${SSH_OPTS[@]}")
  if [[ -n ${SSH_JUMP:-} ]]; then
    args+=(-o "ProxyJump=${SSH_JUMP}")
  fi
  scp "${args[@]}" "$local_path" "$target:$remote_path"
}

enable_awg0_from_dir() {
  local target=$1 dir=$2
  copy_server_conf "$target" "$dir"
  log "$target: awg-quick up awg0"
  awg_up "$target" awg0
}

refuse_fresh() {
  local target=$1 n
  if remote_has_awg0 "$target"; then
    die "$target: /etc/amnezia/amneziawg/awg0.conf уже есть"
  fi
  n=$(remote_bash "$target" user <<'EOF'
set -euo pipefail
if [[ ! -d $HOME/awg ]]; then
  printf 0
  exit 0
fi
cd "$HOME/awg"
n=0
for d in amneziawg_3_*; do
  [[ -d $d && -f $d/server_awg0.conf ]] || continue
  n=$((n+1))
done
printf '%s' "$n"
EOF
)
  [[ $n == 0 ]] || die "$target: в ~/awg уже есть server_awg0.conf, повторный init не делаю"
}

copy_server_conf() {
  local target=$1 dir=$2
  remote_bash "$target" root <<EOF
set -euo pipefail
home=\$(getent passwd $(q "$(user_of "$target")") | cut -d: -f6)
install -d -m 0755 /etc/amnezia/amneziawg
install -m 0600 "\$home/awg/$(q "$dir")/server_awg0.conf" /etc/amnezia/amneziawg/awg0.conf
EOF
}

awg_up() {
  local target=$1 iface=$2
  remote_bash "$target" root <<EOF
set -euo pipefail
iface=$(q "$iface")
set +e
out=\$(awg-quick up "\$iface" 2>&1)
rc=\$?
set -e
printf '%s\n' "\$out"
if [[ \$rc -ne 0 ]]; then
  case \$out in
    *"already exists"*) ;;
    *) exit 1 ;;
  esac
fi
systemctl enable "awg-quick@\$iface"
EOF
}

awg_down() {
  local target=$1 iface=$2
  remote_bash "$target" root <<EOF
set -euo pipefail
iface=$(q "$iface")
set +e
out=\$(awg-quick down "\$iface" 2>&1)
rc=\$?
set -e
printf '%s\n' "\$out"
if [[ \$rc -ne 0 ]]; then
  case \$out in
    *"is not a WireGuard interface"*|*"is not an AmneziaWG interface"*) ;;
    *) exit 1 ;;
  esac
fi
EOF
}

ensure_rt() {
  local target=$1 id=$2 name=$3
  [[ $id =~ ^[0-9]+$ && $name =~ ^[A-Za-z0-9_-]+$ ]] || die "таблица $id $name"
  remote_bash "$target" root <<EOF
set -euo pipefail
grep -q '^${id}[[:space:]]\\+${name}\$' /etc/iproute2/rt_tables \\
  || echo '${id} ${name}' | tee -a /etc/iproute2/rt_tables >/dev/null
EOF
}

append_server_hooks() {
  local target=$1 dir=$2 subnet=$3
  remote_bash "$target" user <<EOF
set -euo pipefail
f="\$HOME/awg/$(q "$dir")/server_awg0.conf"
if grep -q 'table clientroute' "\$f"; then
  echo "хуки clientroute уже в \$f" >&2
  exit 0
fi
cat >> "\$f" <<'HOOKS'
$(clientroute_hooks "$subnet")
HOOKS
EOF
}

# where=home — ~/awg/$name у пользователя SSH; where=etc — /etc/amnezia/amneziawg/$name.
# hooks=0 оставляет только Table = off, hooks=1 добавляет default в таблицу.
prepare_client_iface() {
  local target=$1 where=$2 name=$3 iface=$4 table=$5 hooks=$6
  [[ $where == home || $where == etc ]] || die "where=$where"
  [[ $name =~ ^[A-Za-z0-9._-]+$ ]] || die "имя файла: $name"
  [[ $iface =~ ^awg[0-9]+$ && $table =~ ^[A-Za-z0-9_-]+$ ]] || die "iface/table"
  [[ $hooks == 0 || $hooks == 1 ]] || die "hooks"
  remote_bash "$target" root <<EOF
set -euo pipefail
if [[ $(q "$where") == home ]]; then
  home=\$(getent passwd $(q "$(user_of "$target")") | cut -d: -f6)
  path="\$home/awg/$(q "$name")"
else
  path="/etc/amnezia/amneziawg/$(q "$name")"
fi
[[ -f \$path ]] || { echo "нет \$path" >&2; exit 1; }
python3 - "\$path" $(q "$iface") $(q "$table") $(q "$hooks") <<'PY'
import sys
path, iface, table, hooks = sys.argv[1:5]
lines = open(path, encoding="utf-8").read().splitlines()
out = []
inserted = False
for line in lines:
    s = line.strip()
    if s.startswith("DNS ") or s.startswith("DNS="):
        out.append("# " + line)
        continue
    if s.startswith("Table ") or s.startswith("Table=") or s.startswith("PostUp") or s.startswith("PostDown"):
        continue
    if s == "[Peer]" and not inserted:
        out.append("Table = off")
        if hooks == "1":
            out.append(
                "PostUp = ip route add default dev %s table %s 2>/dev/null || true" % (iface, table)
            )
            out.append(
                "PostDown = ip route del default dev %s table %s 2>/dev/null || true" % (iface, table)
            )
        out.append("")
        inserted = True
    out.append(line)
if not inserted:
    sys.stderr.write("в конфиге нет [Peer]\\n")
    sys.exit(1)
open(path, "w", encoding="utf-8").write("\\n".join(out) + "\\n")
PY
if [[ $(q "$where") == home ]]; then
  chown --reference="\$home" "\$path"
fi
chmod 600 "\$path"
EOF
}

replace_server_hooks() {
  local target=$1 conf=$2 hooks=$3
  remote_bash "$target" user <<EOF
set -euo pipefail
cd "\$HOME"
python3 - $(q "$conf") $(q "$hooks") <<'PY'
import sys
conf, hooks_path = sys.argv[1:3]
hooks = open(hooks_path, encoding="utf-8").read().strip() + "\\n"
lines = open(conf, encoding="utf-8").read().splitlines()
out = []
inserted = False
for line in lines:
    s = line.strip()
    if s.startswith("PostUp") or s.startswith("PostDown"):
        continue
    if s == "[Peer]" and not inserted:
        out.append(hooks.rstrip("\\n"))
        out.append("")
        inserted = True
    out.append(line)
if not inserted:
    if out and out[-1] != "":
        out.append("")
    out.append(hooks.rstrip("\\n"))
text = "\\n".join(out) + "\\n"
open(conf, "w", encoding="utf-8").write(text)
need = ("prio 2010", "prio 2000", "fwmark 0x100/0x100", "prio 1100")
for item in need:
    if item not in text:
        sys.stderr.write("в server conf нет %s\\n" % item)
        sys.exit(1)
PY
EOF
}

find_awg_dir() {
  local target=$1 role=$2 saved dir
  if [[ -n ${AWG_DIR:-} ]]; then
    [[ $AWG_DIR =~ ^amneziawg_3_[A-Za-z0-9_-]+$ ]] || die "AWG_DIR=$AWG_DIR"
    printf '%s\n' "$AWG_DIR"
    return
  fi
  saved="$SSH_DIR/artifacts/${role}.dir"
  if [[ -f $saved ]]; then
    dir=$(tr -d '[:space:]' < "$saved")
    [[ $dir =~ ^amneziawg_3_[A-Za-z0-9_-]+$ ]] || die "битый $saved"
    printf '%s\n' "$dir"
    return
  fi
  dir=$(remote_bash "$target" user <<'EOF'
set -euo pipefail
cd "$HOME/awg"
n=0
found=
for d in amneziawg_3_*; do
  [[ -d $d && -f $d/server_awg0.conf ]] || continue
  n=$((n+1))
  found=$d
done
if [[ $n -eq 0 ]]; then
  echo "нет каталога amneziawg_3_* с server_awg0.conf" >&2
  exit 1
fi
if [[ $n -ne 1 ]]; then
  echo "несколько каталогов amneziawg_3_*, задайте AWG_DIR" >&2
  exit 1
fi
printf '%s\n' "$found"
EOF
)
  log "$target: каталог $dir"
  printf '%s\n' "$dir"
}

sync_awg0() {
  local target=$1 dir=$2
  log "$target: обновить awg0"
  remote_bash "$target" root <<EOF
set -euo pipefail
home=\$(getent passwd $(q "$(user_of "$target")") | cut -d: -f6)
install -d -m 0755 /etc/amnezia/amneziawg
install -m 0600 "\$home/awg/$(q "$dir")/server_awg0.conf" /etc/amnezia/amneziawg/awg0.conf
if awg show awg0 >/dev/null 2>&1; then
  awg syncconf awg0 <(awg-quick strip awg0)
else
  awg-quick up awg0
  systemctl enable awg-quick@awg0
fi
awg show awg0
EOF
}

wait_handshake() {
  local target=$1 iface=$2
  remote_bash "$target" root <<EOF
set -euo pipefail
iface=$(q "$iface")
ok=0
i=1
while [[ \$i -le 15 ]]; do
  if awg show "\$iface" latest-handshakes | grep -q "$(printf '\t')[1-9]"; then
    ok=1
    break
  fi
  sleep 2
  i=\$((i+1))
done
if [[ \$ok != 1 ]]; then
  echo "нет handshake \$iface" >&2
  awg show "\$iface" >&2 || true
  exit 1
fi
awg show "\$iface"
EOF
}

stage_client() {
  local target=$1 local_path=$2 home_name=$3 etc_name=$4
  [[ $home_name =~ ^[A-Za-z0-9._-]+$ && $etc_name =~ ^[A-Za-z0-9._-]+$ ]] || die "имя файла"
  scp_to "$local_path" "$target" "awg/${home_name}"
  promote_home_conf "$target" "$home_name" "$etc_name"
}

promote_home_conf() {
  local target=$1 home_name=$2 etc_name=$3
  remote_bash "$target" root <<EOF
set -euo pipefail
home=\$(getent passwd $(q "$(user_of "$target")") | cut -d: -f6)
install -d -m 0755 /etc/amnezia/amneziawg
install -m 0600 "\$home/awg/$(q "$home_name")" "/etc/amnezia/amneziawg/$(q "$etc_name")"
EOF
}

peer_conf_exists() {
  local target=$1 dir=$2 peer=$3 answer
  answer=$(remote_bash "$target" user <<EOF
set -euo pipefail
if [[ -f "\$HOME/awg/$(q "$dir")/client_$(q "$peer").conf" ]]; then
  printf yes
else
  printf no
fi
EOF
)
  [[ $answer == yes ]]
}

add_manual_clientroute() {
  local target=$1 subnet=$2
  validate_subnet "$subnet"
  remote_bash "$target" root <<EOF
set -euo pipefail
ip route add default dev awg1 table clientroute
ip rule add from ${subnet} table clientroute
ip rule add iif awg0 table clientroute
ip rule add to ${subnet} table main prio 1000
ip -4 rule show
ip -4 route show table clientroute
EOF
}

assert_clientroute() {
  local target=$1 subnet=$2 dev=$3
  validate_subnet "$subnet"
  [[ $dev =~ ^awg[0-9]+$ ]] || die "интерфейс: $dev"
  remote_bash "$target" root <<EOF
set -euo pipefail
rules=\$(ip -4 rule show)
printf '%s\n' "\$rules"
printf '%s\n' "\$rules" | grep -F -q "to ${subnet} lookup main"
printf '%s\n' "\$rules" | grep -F -q "iif awg0"
printf '%s\n' "\$rules" | grep -F -q "from ${subnet}"
ip -4 route show table clientroute | grep -F -q "default dev ${dev}"
ip -4 route show table clientroute
EOF
}
