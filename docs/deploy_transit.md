---
date: 2026-05-21
status: draft
aliases: ["deploy_transit", "Настройка транзитной ноды"]
---
# Настройка транзитной ноды (transit / entry)

Ручной деплой. Автоматизация: [deploy_ansible.md](deploy_ansible.md) или SSH [../ssh/README.md](../ssh/README.md). Выходная нода: [deploy_exit.md](deploy_exit.md). Middle (тройка): [deploy_middle.md](deploy_middle.md).

**Пара:** после exit — awg1 из `client_transit.conf`. **Тройка:** после middle — awg1 из `client_entry.conf`.

**Развилка выхода.** Без второго сервера в РФ весь трафик клиентов идёт в `awg1`. Этот файл описывает именно так.

```text
без выхода через РФ:
клиент → awg0 → clientroute → awg1 → exit или middle

с выходом через РФ:
клиент → awg0
    ├── назначение в списке RU → ruroute → awg2 → сервер в РФ
    └── остальное → clientroute → awg1 → exit или middle
```

Ветка «с выходом через РФ» — [deploy_entry_ru_exit.md](deploy_entry_ru_exit.md). С нуля: общие шаги этого файла до копирования `awg1` включительно, дальше тот документ, раздел «С нуля». Уже поднятый entry переводится тем же документом, раздел «Уже работающий entry». Ниже по тексту, где ветки расходятся, стоит отсылка.

Файл политики для сборки файрвола скриптом: без РФ — [policy/entry.example.yaml](../policy/entry.example.yaml), с РФ — [policy/entry.ru.example.yaml](../policy/entry.ru.example.yaml). Поля разобраны в [deploy_entry_ru_exit.md](deploy_entry_ru_exit.md), раздел «Файл entry.yaml».

## Добавляем ключ SSH

```sh
ssh-keygen -t ed25519 -C "keyname"
ssh-copy-id user@server
```

## Настраиваем SSH и ставим Fail2ban

```ssh
sudo apt update
sudo apt install fail2ban
```

```
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

```
sudo systemctl enable fail2ban
sudo systemctl restart fail2ban
```

Проверяем, что всё работает:

```
sudo fail2ban-client status sshd
```

```
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

```
sudo sshd -t
sudo systemctl restart ssh
```

## Вырубаем unattended updates

Эта штука по таймеру может что-то обновить и передёрнуть `systemd`, который потом передёрнет сеть и маршрутизация может сломаться.

Если вот так, то оно включено:

```
cat /etc/apt/apt.conf.d/20auto-upgrades
```

```
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
```

1. Меняем тут на нули
2. Максируем таймеры, чтобы при обновлении какого-то пакета снова не включились
```
   sudo systemctl mask apt-daily.timer apt-daily-upgrade.timer
   sudo systemctl stop apt-daily.timer apt-daily-upgrade.timer
```
3. Проверяем, что они стали `masked`:
```
   systemctl status apt-daily.timer apt-daily-upgrade.timer
```
4. Маскируем сервис:
```
   sudo systemctl mask unattended-upgrades.service
   sudo systemctl stop unattended-upgrades.service
```
5. Проверяем, что сервис тоже стал `masked`:
```
   systemctl status unattended-upgrades.service
```

## Устанавливаем AmneziaWG

```
sudo add-apt-repository ppa:amnezia/ppa -y
sudo apt update
sudo apt install amneziawg amneziawg-dkms amneziawg-tools

sudo reboot
```

## Создаём конфиг для AmneziaWG

```
sudo apt install python-is-python3 python3-venv
mkdir awg
```

```
scp -r awg_manage.py awg_gen.py requirements.txt packages/ user@server:awg
```

```
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
   --server-host 203.0.113.20 \
   --listen-port 443 \
   --subnet 10.10.2.0/24 \
   --mtu 1280 \
   --mimic quic_initial \
   --intensity medium \
   --junk-level 5 \
   --dns 1.1.1.1,1.0.0.1 \
   --server-name "Russia 3.1"

sudo cp amneziawg_3_<date_time>/server_awg0.conf /etc/amnezia/amneziawg/awg0.conf
```

```sh
# пара: client_transit.conf с exit; тройка: client_entry.conf с middle
sudo cp client_transit.conf /etc/amnezia/amneziawg/awg1.conf
sudo vim /etc/amnezia/amneziawg/awg1.conf
# [Interface]
# ...
# #DNS = 
# ...
# Table = off
```
## Настраиваем сеть и файрвол

```
sudo vim /etc/sysctl.d/99-forwarding.conf
```

```
net.ipv4.ip_forward=1
# net.ipv6.conf.all.forwarding=1 # если нужно IPv6
```

```
sudo sysctl --system
```

Проверяем, что всё подхватилось:

```
sysctl net.ipv4.ip_forward
```

Ветка «с выходом через РФ» этот файл не использует: NAT и MSS есть ещё и на `awg2`, плюс include таблицы `awg_ru_split`. Файл и порядок включения — [deploy_entry_ru_exit.md](deploy_entry_ru_exit.md).

```
sudo vim /etc/nftables.conf
```

```
#!/usr/sbin/nft -f

flush ruleset

# =============================================
# FILTER TABLE —  close unused ports
# =============================================
table inet filter {
    chain input {
        type filter hook input priority filter; policy drop;

        iif lo accept
        ct state established,related accept

        # SSH
        tcp dport 22 accept

        # AmneziaWG (оба интерфейса слушают 443)
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

        ip saddr 10.10.2.0/24 oifname "awg1" masquerade
    }
}

# =============================================
# MANGLE TABLE — TCP MSS Clamping
# =============================================
table ip mangle {
    chain forward {
        type filter hook forward priority mangle; policy accept;

        oifname "awg1" tcp flags syn tcp option maxseg size set rt mtu
        iifname "awg1" tcp flags syn tcp option maxseg size set rt mtu

        oifname "awg0" tcp flags syn tcp option maxseg size set rt mtu
        iifname "awg0" tcp flags syn tcp option maxseg size set rt mtu
    }
}
```

```
sudo nft -f /etc/nftables.conf
sudo systemctl enable --now nftables
```

Проверим, что всё ок:

```
sudo nft list ruleset
```

## Поднимаем интерфейсы

Для ветки «с выходом через РФ» здесь поднять и включить только `awg1`. `awg0` первый раз поднимается уже с хуками из [deploy_entry_ru_exit.md](deploy_entry_ru_exit.md), после `awg2`.

```sh
sudo awg-quick up awg0
sudo systemctl enable awg-quick@awg0

sudo awg-quick up awg1
sudo systemctl enable awg-quick@awg1
```

## Настраиваем маршрутизацию

Ветка «с выходом через РФ» эти `ip rule` руками не добавляет. Там таблица `ruroute` и правила с приоритетами `1000` / `1100` / `2000` / `2010`, их ставят `PostUp`. См. [deploy_entry_ru_exit.md](deploy_entry_ru_exit.md).

```sh
echo "100 clientroute" | sudo tee -a /etc/iproute2/rt_tables

sudo ip route add default dev awg1 table clientroute
sudo ip rule add from 10.10.2.0/24 table clientroute

sudo ip rule add iif awg0 table clientroute
sudo ip rule add to 10.10.2.0/24 table main prio 1000
```

## Добавляем PostUp и PostDown правила

Ветка «с выходом через РФ» подставляет другой блок: те же три правила с явными `prio 2010` / `2000` / `1000` и четвёртое, `fwmark 0x100/0x100` → `ruroute` с `prio 1100`. Оба варианта — `PostDown`, не отдельный сервис. Блок и порядок `down`/`up` — [deploy_entry_ru_exit.md](deploy_entry_ru_exit.md).

```sh
cd awg
vim amneziawg_3_<date_time>/server_awg0.conf
```

```
PostUp = ip rule add from 10.10.2.0/24 table clientroute 2>/dev/null || true
PostUp = ip rule add iif awg0 table clientroute 2>/dev/null || true
PostUp = ip rule add to 10.10.2.0/24 table main prio 1000 2>/dev/null || true

PostDown = ip rule del from 10.10.2.0/24 table clientroute 2>/dev/null || true
PostDown = ip rule del iif awg0 table clientroute 2>/dev/null || true
PostDown = ip rule del to 10.10.2.0/24 table main prio 1000 2>/dev/null || true
```

```sh
sudo vim /etc/amnezia/amneziawg/awg1.conf
```

```
PostUp = ip route add default dev awg1 table clientroute 2>/dev/null || true PostDown = ip route del default dev awg1 table clientroute 2>/dev/null || true
```

```sh
cd awg
sudo cp amneziawg_3_<date_time>/server_awg0.conf /etc/amnezia/amneziawg/awg0.conf
```


Проверяем, что всё работает:

```sh
sudo awg-quick down awg0
sudo awg-quick down awg1

sudo awg-quick up awg0
# [#] ip link add awg0 type amneziawg
# [#] awg setconf awg0 /dev/fd/63
# [#] ip -4 address add 10.10.2.1/24 dev awg0
# [#] ip link set mtu 1280 up dev awg0
# [#] ip rule add from 10.10.2.0/24 table clientroute 2>/dev/null || true
# [#] ip rule add iif awg0 table clientroute 2>/dev/null || true
# [#] ip rule add to 10.10.2.0/24 table main prio 1000 2>/dev/null || true
ip rule show
# 0:	from all lookup local
# 1000:	from all to 10.10.2.0/24 lookup main
# 32764:	from all iif awg0 lookup clientroute
# 32765:	from 10.10.2.0/24 lookup clientroute
# 32766:	from all lookup main
# 32767:	from all lookup default
ip route show table clientroute
sudo awg-quick up awg1
# [#] ip link add awg1 type amneziawg
# [#] awg setconf awg1 /dev/fd/63
# [#] ip -4 address add 10.10.1.2/32 dev awg1
# [#] ip link set mtu 1280 up dev awg1
# [#] ip route add default dev awg1 table clientroute 2>/dev/null || true
ip rule show
# 0:	from all lookup local
# 1000:	from all to 10.10.2.0/24 lookup main
# 32764:	from all iif awg0 lookup clientroute
# 32765:	from 10.10.2.0/24 lookup clientroute
# 32766:	from all lookup main
# 32767:	from all lookup default
ip route show table clientroute
# default dev awg1 scope link
sudo awg-quick down awg0
# [#] ip link delete dev awg0
# [#] ip rule del from 10.10.2.0/24 table clientroute 2>/dev/null || true
# [#] ip rule del iif awg0 table clientroute 2>/dev/null || true
# [#] ip rule del to 10.10.2.0/24 table main prio 1000 2>/dev/null || true
ip rule show
# 0:	from all lookup local
# 32766:	from all lookup main
# 32767:	from all lookup default
ip route show table clientroute
# default dev awg1 scope link
sudo awg-quick down awg1
# [#] ip link delete dev awg1
# [#] ip route del default dev awg1 table clientroute 2>/dev/null || true
ip rule show
# 0:	from all lookup local
# 32766:	from all lookup main
# 32767:	from all lookup default
ip route show table clientroute
```

## Backlinks
 - [[inbox/2025-11-27-a|Настройка цепочки виртуалок для VPN]]
