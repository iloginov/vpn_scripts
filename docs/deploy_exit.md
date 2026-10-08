---
date: 2026-05-21
status: draft
aliases: ["deploy_exit", "Настройка выходной ноды"]
---
# Настройка выходной ноды (exit)

Ручной деплой. Автоматизация: [deploy_ansible.md](deploy_ansible.md). Транзит: [deploy_transit.md](deploy_transit.md). Middle (тройка): [deploy_middle.md](deploy_middle.md).

Сначала exit, пока до него есть SSH. **Пара:** пир `transit` → `client_transit.conf` для awg1 на entry. **Новая тройка, exit ещё открыт:** пир `middle` → `client_middle.conf`. Если пара уже стоит и из страны exit заблокирован — не этот файл, а [deploy_middle.md](deploy_middle.md): заход на exit только через `ssh -J middle`.

## Добавляем ключ SSH

```sh
ssh-keygen -t ed25519 -C "keyname"
ssh-copy-id user@server
```

## Настраиваем SSH и ставим Fail2ban

```sh
sudo apt update
sudo apt install fail2ban
```

```sh
sudo vim /etc/fail2ban/jail.d/ssh.conf
```

```
[sshd]
enabled = true
port = ssh
maxretry = 5
findtime = 10m
bantime = 1h
```

```sh
sudo systemctl enable fail2ban
sudo systemctl restart fail2ban
```

Проверяем, что всё работает:

```sh
sudo fail2ban-client status sshd
```

```sh
sudo vim /etc/ssh/sshd_config
```

```
PasswordAuthentication yes
PubkeyAuthentication yes
PermitRootLogin no
MaxAuthTries 3
LoginGraceTime 2m
MaxStartups 10:30:60
PerSourceMaxStartups 3
```

```sh
sudo sshd -t
sudo systemctl restart ssh
```

## Вырубаем unattended updates

Эта штука по таймеру может что-то обновить и передёрнуть `systemd`, который потом передёрнет сеть и маршрутизация может сломаться.

Если вот так, то оно включено:

```sh
cat /etc/apt/apt.conf.d/20auto-upgrades
```

```
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
```

1. Меняем тут на нули
2. Максируем таймеры, чтобы при обновлении какого-то пакета снова не включились
```sh
   sudo systemctl mask apt-daily.timer apt-daily-upgrade.timer
   sudo systemctl stop apt-daily.timer apt-daily-upgrade.timer
```
3. Проверяем, что они стали `masked`:
```sh
   systemctl status apt-daily.timer apt-daily-upgrade.timer
```
4. Маскируем сервис:
```sh
   sudo systemctl mask unattended-upgrades.service
   sudo systemctl stop unattended-upgrades.service
```
5. Проверяем, что сервис тоже стал `masked`:
```sh
   systemctl status unattended-upgrades.service
```

## Устанавливаем AmneziaWG

```sh
sudo add-apt-repository ppa:amnezia/ppa -y
sudo apt update
sudo apt install amneziawg amneziawg-dkms amneziawg-tools

sudo reboot
```

## Создаём конфиг для AmneziaWG

```sh
sudo apt install python-is-python3 python3-venv
mkdir awg
```

```sh
scp -r awg_manage.py awg_gen.py requirements.txt packages/ user@server:awg
```

```sh
cd awg
find . -path "./.venv" -prune -o -name "__pycache__" -print | xargs rm -rf
```

```sh
cd awg
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
sudo apt install zbar-tools

python awg_manage.py init \
   --server-host 203.0.113.10 \
   --listen-port 443 \
   --subnet 10.10.1.0/24 \
   --mtu 1280 \
   --mimic quic_initial \
   --intensity medium \
   --junk-level 5 \
   --dns 1.1.1.1,1.0.0.1 \
   --server-name "Exit 3.1"
   
python awg_manage.py add \
   --server-conf amneziawg_3_<date_time>/server_awg0.conf \
   --name transit \
   --mimic quic_initial \
   --intensity medium \
   --junk-level 5
```

Для тройки вместо `--name transit` — `--name middle` (файл `client_middle.conf`).

## Настраиваем сеть и файрвол

```sh
sudo vim /etc/sysctl.d/99-forwarding.conf
```

```
net.ipv4.ip_forward=1
# net.ipv6.conf.all.forwarding=1 # если нужно IPv6
```

```sh
sudo sysctl --system
```

Проверяем, что всё подхватилось:

```sh
sysctl net.ipv4.ip_forward
```

```sh
sudo vim /etc/nftables.conf
```

```
#!/usr/sbin/nft -f

flush ruleset

# =============================================
# FILTER TABLE — close unused ports
# =============================================
table inet filter {
    chain input {
        type filter hook input priority filter; policy drop;

        iif lo accept
        ct state established,related accept

        # SSH
        tcp dport 22 accept

        # AmneziaWG
        udp dport 443 accept

        # ICMP
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

# =============================================
# NAT TABLE — MASQUERADE
# =============================================
table ip nat {
    chain postrouting {
        type nat hook postrouting priority srcnat; policy accept;

        ip saddr 10.10.1.0/24 masquerade
    }
}

# =============================================
# MANGLE TABLE — TCP MSS Clamping
# =============================================
table ip mangle {
    chain forward {
        type filter hook forward priority mangle; policy accept;

        oifname "eth0" tcp flags syn tcp option maxseg size set rt mtu
        iifname "eth0" tcp flags syn tcp option maxseg size set rt mtu

        oifname "awg0" tcp flags syn tcp option maxseg size set rt mtu
        iifname "awg0" tcp flags syn tcp option maxseg size set rt mtu
    }
}
```

```sh
sudo nft -f /etc/nftables.conf
sudo systemctl enable --now nftables
```

Проверим, что всё ок:

```sh
sudo nft list ruleset
```

## Включаем AmneziaWG

```sh
cd awg
sudo cp amneziawg_3_<date_time>/server_awg0.conf /etc/amnezia/amneziawg/awg0.conf
sudo awg-quick up awg0
sudo systemctl enable awg-quick@awg0
```
## Backlinks
 - [[inbox/2025-11-27-a|Настройка цепочки виртуалок для VPN]]
