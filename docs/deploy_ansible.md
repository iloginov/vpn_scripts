# Ansible: пара (exit + entry) или тройка (exit + middle + entry)

Один playbook [playbooks/bootstrap.yml](../ansible/playbooks/bootstrap.yml) вместо ручных шагов из [deploy_exit.md](deploy_exit.md), [deploy_transit.md](deploy_transit.md) и при тройке [deploy_middle.md](deploy_middle.md).

Топологию задаёт inventory: **пустая группа `middle`** — пара, **хост в `middle`** — тройка.

```
ansible/
  playbooks/bootstrap.yml         # exit → (middle) → entry
  playbooks/insert_middle.yml     # живая пара → тройка
  playbooks/bootstrap_ru_exit.yml # пустая российская выходная нода
  playbooks/add_ru_exit.yml       # живой entry → сплит через эту ноду
  tasks/
    shared/…
    exit/05_create_awg_config.yml … 07_enable_awg0.yml, add_middle_peer.yml
    middle/05_create_awg_config.yml
    entry/05_create_awg_config.yml, rewire_awg1.yml
    forwarder/06_configure_awg1.yml … 09_enable_awg.yml
  templates/
  handlers/main.yml
  inventory/
    hosts.yml.example
    group_vars/all.yml
```

Группа **`entry`** — транзит из [deploy_transit.md](deploy_transit.md) (клиенты на awg0, туннель awg1 на exit или middle). Выход российских сетей через второй сервер `bootstrap.yml` не делает. Для этого группа **`ru_exit`** и два playbook ниже; ручной порядок — [deploy_entry_ru_exit.md](deploy_entry_ru_exit.md).

## Требования

- Ansible 2.14+ на ноутбуке (`pip install ansible` / `brew install ansible`)
- Две (пара) или три (тройка) свежие Ubuntu VPS, SSH по ключу
- Репозиторий `vpn_scripts` на ноутбуке (playbook пакует `awg_manage.py`, `awg_gen.py`, `requirements.txt`, `packages/` в tar → `~/awg`)
- AmneziaWG **3.1**: `ppa:amnezia/ppa` → `amneziawg` / `amneziawg-dkms` / `amneziawg-tools`.
  На Ubuntu 24.04 (noble) это `awg` **3.1.20260812** и DKMS-модуль той же линии
  (`RandomTrailers` / `DisableCookies` есть и в `awg`, и в исходниках модуля).
  Номер apt (`1.0.20210914-…`) — упаковка; смотреть `awg --version`.

## Шаги

1. Ключ на все VPS: `ssh-copy-id user@<ip>` (заходить именно по IP, если IP указаны в `hosts.yml`, иначе SSH запишет hostname в `known_hosts`, и Ansible не зайдёт).

2. Inventory:

```bash
cd ansible
cp inventory/hosts.yml.example inventory/hosts.yml
# отредактировать: ansible_host, ansible_user, awg_subnet, endpoint_host, server_name
# тройка: заменить middle.hosts: {} на vpn_middle с IP
```

3. Проверка SSH:

```bash
ansible -i inventory/hosts.yml all -m ping
```

4. Bootstrap:

```bash
ansible-playbook -i inventory/hosts.yml playbooks/bootstrap.yml
```

или с логом:

```bash
ansible-playbook -i inventory/hosts.yml playbooks/bootstrap.yml \
  -v \
  2>&1 | tee "bootstrap-$(date +%Y%m%d-%H%M%S).log"
```

Порядок: общая настройка всех хостов → **exit** (init, пир `transit` или `middle`, nftables, awg0) → **middle** (если есть; init, пир `entry`, awg1 с exit) → **entry** (init, awg1 из артефакта предыдущего hop, маршрутизация, awg0+awg1).

## Артефакты

| Топология | Пир на exit | awg1 у middle | awg1 у entry |
|-----------|-------------|---------------|--------------|
| Пара | `transit` → `artifacts/client_transit.conf` | нет | `client_transit.conf` |
| Тройка | `middle` → `artifacts/client_middle.conf` | тот же файл | `artifacts/client_entry.conf` |

Ключи в `ansible/artifacts/` (каталог в `.gitignore`).

## Переменные inventory

| Где | Переменная | Назначение |
|-----|------------|------------|
| `exit` | `awg_subnet` | Подсеть awg0 (напр. `10.10.1.0/24`) |
| `exit` | `wan_interface` | WAN для nftables (напр. `eth0`) |
| `exit` | `server_name` | Имя для `# ServerName` |
| `middle` | `awg_subnet` | Подсеть между entry и middle (напр. `10.10.3.0/24`) |
| `middle` | `endpoint_host` | Публичный IP middle (Endpoint в `client_entry.conf`); иначе `ansible_host` |
| `middle` | `server_name` | Имя сервера в конфигах |
| `entry` | `awg_subnet` | Подсеть клиентов (напр. `10.10.2.0/24`) |
| `entry` | `endpoint_host` | Публичный IP transit для `--server-host` |
| `entry` | `server_name` | Имя сервера в конфигах |
| `entry` | `ru_peer_name` | Имя пира этого entry на `ru_exit`. Без неё — `inventory_hostname` |
| `ru_exit` | `awg_subnet` | Подсеть awg0 российского выхода (напр. `10.10.4.0/24`) |
| `ru_exit` | `wan_interface` | WAN для nftables |
| `ru_exit` | `server_name` | Имя сервера в конфигах |
| `ru_exit` | `endpoint_host` | Публичный адрес в Endpoint пиров entry; иначе `ansible_host` |

Общие параметры AWG (порт 443, MTU, mimic) и имена пиров — в `group_vars/all.yml`. Каталог скриптов — `~/awg`: у обычного пользователя `/home/<user>/awg`, у root `/root/awg`. Если root уже развёрнут в `/home/root/awg`, следующий прогон playbook переносит дерево в `/root/awg` и оставляет симлинк на старом пути.

Служебные флаги bootstrap: dot-файлы в `~/awg/.vpn-bootstrap/`. Рабочие конфиги — `amneziawg_3_exit` / `amneziawg_3_middle` / `amneziawg_3_entry`. На VPS: `cd ~/awg`, `source .venv/bin/activate`, `python awg_manage.py …`.

## После bootstrap

Клиенты только на entry:

```bash
cd ~/awg
source .venv/bin/activate
python awg_manage.py add \
  --server-conf amneziawg_3_entry/server_awg0.conf \
  --name alice \
  ...
sudo cp amneziawg_3_entry/server_awg0.conf /etc/amnezia/amneziawg/awg0.conf
sudo awg-quick down awg0 && sudo awg-quick up awg0
```

## Пара → тройка (`insert_middle.yml`)

Когда из страны до exit уже нет ни туннеля, ни SSH. Ноутбук по-прежнему ходит на entry и на свежий middle. До exit playbook заходит только прыжком `ноутбук → middle → exit` (middle за границей, ТСПУ его не режет).

1. `ssh-copy-id` на middle (по IP). На exit ключ уже есть с прошлого bootstrap.
2. Добавить хост в группу `middle` в `hosts.yml`. На `vpn_exit` ProxyJump пока не писать — его выставит сам play.
3. `ansible-playbook -i inventory/hosts.yml playbooks/insert_middle.yml`

Порядок: проверка entry → подготовка middle → пир `middle` на exit через ProxyJump (`awg syncconf`) → awg0/awg1 на middle → на entry замена **только awg1** (бэкап `awg1.conf.bak`). Клиентов не трогает. Старый пир `transit` на exit можно оставить.

После этого из страны exit доступен только через middle. На `vpn_exit` в `hosts.yml`:

```yaml
ansible_ssh_common_args: '-o ProxyJump=<user>@<middle_ip>'
```

Иначе следующий playbook снова пойдёт на exit напрямую и упрётся в блокировку.

Ручная последовательность тех же шагов, команда за командой: [deploy_middle.md](deploy_middle.md).

Повторный полный bootstrap по-прежнему запрещён, если `/etc/amnezia/amneziawg/awg0.conf` уже есть. Для пары, уже поднятой этим ansible на 3.1, insert — путь «пара → тройка» без сноса клиентов.

Пара на AmneziaWG 2.0 этим playbook не переводится: insert положит на entry клиент с полями 3.1. Для неё отдельный прогон `bootstrap.yml --limit exit:middle` после сноса exit и ручная чистка полей на middle: [deploy_awg2_pair_middle.md](deploy_awg2_pair_middle.md).

## Российский выход

Два playbook, по очереди. `bootstrap.yml` их не вызывает.

Российская нода — обычный exit: NAT своей подсети в интернет. Пира `transit` или `middle` у неё нет. Клиенты этой ноды — entry, их добавляет второй playbook. Подсеть не совпадает с подсетью клиентов entry (в примере `10.10.4.0/24` и `10.10.2.0/24`).

1. Хост в группе `ru_exit`: `ansible_host`, `ansible_user`, `awg_subnet`, `server_name`, `wan_interface`. `endpoint_host` — публичный адрес, который попадёт в Endpoint пира; без него берётся `ansible_host`.
2. `ansible-playbook -i inventory/hosts.yml playbooks/bootstrap_ru_exit.yml`
3. На каждом entry, который надо подключить, при необходимости `ru_peer_name`. Имя пира на российском выходе: эта переменная, иначе `inventory_hostname`. Буквы, цифры, `_` и `-`. Один `ru_exit` на несколько пар — имена не повторяются.
4. `ansible-playbook -i inventory/hosts.yml playbooks/add_ru_exit.yml`

`add_ru_exit.yml` не делает `down awg0` и не перезапускает `awg1`. Старые правила `clientroute` (без явного приоритета, около `32764` / `32765`) остаются. Добавляется правило `fwmark 0x100/0x100` с приоритетом `1100`: оно смотрится раньше них. `nft -f` выполняется после handshake `awg2` и после этого правила. Несколько entry в одном inventory обрабатываются по одному.

Повторный запуск пропускает уже отмеченные шаги (`~/awg/.vpn-bootstrap/`). Пир на российском выходе второй раз не создаётся, если файл `client_<имя>.conf` уже есть. Если `awg2.conf` на entry уже есть и в нём нет маршрута `ruroute`, playbook останавливается.

На ноутбуке для render нужен `.venv` репозитория (`python -m venv .venv && .venv/bin/pip install -r requirements.txt`). Скрипт качает списки RU с ipdeny и ipverse.

Ручная сборка с нуля по-прежнему ставит в `awg0` все четыре пары хуков, включая приоритеты `2000` и `2010`, и для этого опускает `awg0`. На живой entry playbook этого не делает.

## Повторный запуск bootstrap

Если уже есть `/etc/amnezia/amneziawg/awg0.conf`, playbook завершится с ошибкой. Для пересоздания — удалите конфиги в `/etc/amnezia/` и каталоги `~/awg/amneziawg_3_exit` / `~/awg/amneziawg_3_middle` / `~/awg/amneziawg_3_entry`.

## Проверка

На entry (и на middle в тройке):

```bash
sudo awg show awg0
sudo awg show awg1
sudo nft list ruleset
ip rule show
ip route show table clientroute
```

На exit:

```bash
sudo awg show awg0
sudo nft list ruleset
```
