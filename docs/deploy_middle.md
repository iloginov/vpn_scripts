---
date: 2026-09-24
status: draft
aliases: ["deploy_middle", "Вставка middle когда exit из страны уже не открывается"]
---
# Вставка middle, когда из страны до exit уже не достучаться

Пара уже стоит (ansible: `~/awg/amneziawg_3_exit` и `~/awg/amneziawg_3_entry`). Туннель entry→exit умер, SSH на exit из России тоже.

Из страны открыты только entry и **новый** зарубежный middle. До exit заходим прыжком через middle: middle за границей, до exit у него SSH есть. Туннель AWG для этого прыжка не нужен.

Дальше все заходы на exit — только так:

```sh
ssh -J vps@MIDDLE_IP vps@EXIT_IP
```

Подставьте свои IP и пользователя. Ниже везде `vps`, `MIDDLE_IP`, `EXIT_IP`, `ENTRY_IP`.

Автоматизация того же порядка: [deploy_ansible.md](deploy_ansible.md), playbook `insert_middle.yml`, или SSH [../ssh/README.md](../ssh/README.md) (`ssh/insert-middle.sh`).

## 1. Ключ на middle

С ноутбука. На exit ключ уже лежит с прошлого деплоя, `ssh-copy-id` туда из страны не пройдёт.

```sh
ssh-copy-id vps@MIDDLE_IP
ssh vps@MIDDLE_IP
```

## 2. На middle: SSH и Fail2ban

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

## 3. На middle: вырубаем unattended updates

```sh
cat /etc/apt/apt.conf.d/20auto-upgrades
```

Если там единицы:

```
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
```

поменять на `"0"`.

```sh
sudo systemctl mask apt-daily.timer apt-daily-upgrade.timer
sudo systemctl stop apt-daily.timer apt-daily-upgrade.timer
systemctl status apt-daily.timer apt-daily-upgrade.timer

sudo systemctl mask unattended-upgrades.service
sudo systemctl stop unattended-upgrades.service
systemctl status unattended-upgrades.service
```

Оба таймера и сервис должны быть `masked`.

## 4. На middle: AmneziaWG

```sh
sudo add-apt-repository ppa:amnezia/ppa -y
sudo apt update
sudo apt install amneziawg amneziawg-dkms amneziawg-tools
sudo reboot
```

После ребута снова `ssh vps@MIDDLE_IP`.

```sh
sudo apt install python-is-python3 python3-venv
mkdir -p awg
```

С ноутбука, из корня репозитория:

```sh
scp -r awg_manage.py awg_gen.py requirements.txt packages/ vps@MIDDLE_IP:awg
```

На middle:

```sh
cd awg
find . -path "./.venv" -prune -o -name "__pycache__" -print | xargs rm -rf
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
sudo apt install zbar-tools
```

## 5. Пир middle на exit — только через прыжок

С ноутбука. Прямой `ssh vps@EXIT_IP` из страны не используем.

```sh
ssh -J vps@MIDDLE_IP vps@EXIT_IP
```

На exit пара уже развёрнута ansible, каталог фиксированный:

```sh
cd ~/awg
source .venv/bin/activate
python awg_manage.py add \
   --server-conf amneziawg_3_exit/server_awg0.conf \
   --name middle \
   --mimic quic_initial \
   --intensity medium \
   --junk-level 5

sudo cp amneziawg_3_exit/server_awg0.conf /etc/amnezia/amneziawg/awg0.conf
sudo awg syncconf awg0 <(awg-quick strip awg0)
sudo awg show awg0
```

В `awg show` должен появиться пир middle. Старый пир `transit` можно не удалять.

Забрать клиентский конфиг на ноутбук (опять прыжок, не прямой scp на exit):

```sh
exit
scp -o ProxyJump=vps@MIDDLE_IP vps@EXIT_IP:awg/amneziawg_3_exit/client_middle.conf .
```

С этого момента exit с ноутбука открывается только командой `ssh -J vps@MIDDLE_IP vps@EXIT_IP`.

## 6. На middle: сервер для entry и awg1 на exit

```sh
ssh vps@MIDDLE_IP
cd ~/awg
source .venv/bin/activate

python awg_manage.py init \
   --server-host MIDDLE_IP \
   --listen-port 443 \
   --subnet 10.10.3.0/24 \
   --mtu 1280 \
   --mimic quic_initial \
   --intensity medium \
   --junk-level 5 \
   --dns 1.1.1.1,1.0.0.1 \
   --server-name "Middle 3.1"
```

`init` создаст `amneziawg_3_<дата>/`. Дальше подставьте этот каталог вместо `amneziawg_3_<date_time>`.

В конец `server_awg0.conf`:

```sh
vim amneziawg_3_<date_time>/server_awg0.conf
```

```
PostUp = ip rule add from 10.10.3.0/24 table clientroute 2>/dev/null || true
PostUp = ip rule add iif awg0 table clientroute 2>/dev/null || true
PostUp = ip rule add to 10.10.3.0/24 table main prio 1000 2>/dev/null || true

PostDown = ip rule del from 10.10.3.0/24 table clientroute 2>/dev/null || true
PostDown = ip rule del iif awg0 table clientroute 2>/dev/null || true
PostDown = ip rule del to 10.10.3.0/24 table main prio 1000 2>/dev/null || true
```

Пир entry (это конфиг, который потом станет awg1 на российской ноде):

```sh
python awg_manage.py add \
   --server-conf amneziawg_3_<date_time>/server_awg0.conf \
   --name entry \
   --mimic quic_initial \
   --intensity medium \
   --junk-level 5
```

awg1 — клиент к exit. Файл `client_middle.conf` лежит на ноутбуке с шага 5.

```sh
# с ноутбука
scp client_middle.conf vps@MIDDLE_IP:awg/client_middle.conf
```

На middle:

```sh
sudo mkdir -p /etc/amnezia/amneziawg
sudo cp ~/awg/client_middle.conf /etc/amnezia/amneziawg/awg1.conf
sudo vim /etc/amnezia/amneziawg/awg1.conf
```

Закомментировать строку `DNS`. Перед `[Peer]` вписать:

```
Table = off
PostUp = ip route add default dev awg1 table clientroute 2>/dev/null || true
PostDown = ip route del default dev awg1 table clientroute 2>/dev/null || true
```

## 7. На middle: сеть и файрвол

```sh
sudo vim /etc/sysctl.d/99-forwarding.conf
```

```
net.ipv4.ip_forward=1
```

```sh
sudo sysctl --system
sysctl net.ipv4.ip_forward
```

```sh
sudo vim /etc/nftables.conf
```

```
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

        ip saddr 10.10.3.0/24 oifname "awg1" masquerade
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
```

```sh
sudo nft -f /etc/nftables.conf
sudo systemctl enable --now nftables
sudo nft list ruleset
```

```sh
echo "100 clientroute" | sudo tee -a /etc/iproute2/rt_tables
```

## 8. На middle: поднять awg0 и awg1

```sh
sudo cp ~/awg/amneziawg_3_<date_time>/server_awg0.conf /etc/amnezia/amneziawg/awg0.conf

sudo awg-quick up awg0
sudo systemctl enable awg-quick@awg0

sudo awg-quick up awg1
sudo systemctl enable awg-quick@awg1
```

Проверка на middle:

```sh
sudo awg show awg0
sudo awg show awg1
ip route show table clientroute
```

`awg1` — handshake с exit. В таблице `clientroute` default через `awg1`. Клиентов на middle больше не добавляем.

`client_entry.conf` забрать на ноутбук:

```sh
# с ноутбука, из каталога где удобно
scp vps@MIDDLE_IP:awg/amneziawg_3_<date_time>/client_entry.conf .
```

## 9. На entry: поменять только awg1

Entry из страны открывается напрямую. `awg0` и клиентские конфиги не трогаем.

```sh
ssh vps@ENTRY_IP
sudo cp /etc/amnezia/amneziawg/awg1.conf /etc/amnezia/amneziawg/awg1.conf.bak
```

```sh
# с ноутбука
scp client_entry.conf vps@ENTRY_IP:awg/client_entry.conf
```

На entry:

```sh
sudo cp ~/awg/client_entry.conf /etc/amnezia/amneziawg/awg1.conf
sudo vim /etc/amnezia/amneziawg/awg1.conf
```

Так же, как на middle: `DNS` закомментировать, перед `[Peer]`:

```
Table = off
PostUp = ip route add default dev awg1 table clientroute 2>/dev/null || true
PostDown = ip route del default dev awg1 table clientroute 2>/dev/null || true
```

Только awg1:

```sh
sudo awg-quick down awg1
sudo awg-quick up awg1
sudo awg show awg1
ip route show table clientroute
```

`awg0` не перезапускать. Handshake awg1 должен быть с middle, default в `clientroute` — через awg1.

Клиентский трафик: entry `awg0` → `awg1` → middle → exit. Из страны до exit напрямую по-прежнему ничего нет, и это нормально.
