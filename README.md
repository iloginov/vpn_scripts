# Скрипты для настройки VPN

## Документация

| Документ | Описание |
|----------|----------|
| [docs/deploy_exit.md](docs/deploy_exit.md) | Ручная настройка выходной ноды (NL) |
| [docs/deploy_transit.md](docs/deploy_transit.md) | Ручная настройка транзитной ноды (RU, клиенты + awg1) |
| [docs/deploy_entry_ru_exit.md](docs/deploy_entry_ru_exit.md) | Ветка entry: российские сети через второй сервер (`awg2`) |
| [docs/deploy_middle.md](docs/deploy_middle.md) | Ручная настройка middle (тройка: entry → middle → exit) |
| [docs/deploy_awg2_pair_middle.md](docs/deploy_awg2_pair_middle.md) | Пара 2.0: ansible поднимает middle и заново exit на 3.1, entry остаётся 2.0 |
| [docs/deploy_ansible.md](docs/deploy_ansible.md) | Ansible: пара, тройка, insert_middle, российский выход |
| [ssh/README.md](ssh/README.md) | То же по SSH, шаги из ручных гайдов |
| [docs/awg2_protocol_params.md](docs/awg2_protocol_params.md) | Параметры AmneziaWG 2.0 (H/S/I, Jc/Jmin/Jmax), мимикрия |
| [docs/awg3_protocol_params.md](docs/awg3_protocol_params.md) | Параметры AmneziaWG 3.0/3.1 (HPK, padding, RandomTrailers) |
| [docs/xray_plan.md](docs/xray_plan.md) | План AWG + Xray/VLESS+Reality на одной паре (не внедрение) |

## AWG 3.1 (`awg_manage.py`)

Управление конфигами: `awg_manage.py` + каталог `packages/vpn_awg/`. Первичный деплой — [docs/deploy_ansible.md](docs/deploy_ansible.md), SSH-скрипты [ssh/README.md](ssh/README.md) или ручные [docs/deploy_exit.md](docs/deploy_exit.md) / [docs/deploy_transit.md](docs/deploy_transit.md) / [docs/deploy_middle.md](docs/deploy_middle.md).

Параметры obfs и профили мимикрии — [docs/awg2_protocol_params.md](docs/awg2_protocol_params.md), [docs/awg3_protocol_params.md](docs/awg3_protocol_params.md). Генерация типов мимикрии в коде портирована с [AmneziaWG-Architect](https://github.com/Vadim-Khristenko/AmneziaWG-Architect) (`generator.ts`, пулы хостов).

## Amnezia export (`awg_gen.py`)

`pip install -r requirements.txt` (на VPS venv уже ставит Ansible). Для `.vpn` нужен в PATH **`wg`** (wireguard-tools) или **`awg`** (amneziawg-tools) — скрипт выбирает сам. Для декодирования QR — `zbarimg` (`brew install zbar` / `apt install zbar-tools`).

```shell
source .venv/bin/activate
python awg_gen.py client.conf                    # .vpn + *_awg_qr.png + *_qr.png
python awg_gen.py client.conf --server-conf server_awg0.conf
python awg_gen.py awg-qr client.conf                    # raw QR для AmneziaWG iOS
python awg_gen.py awg-qr client.conf --method chunked   # AmneziaVPN; *_awg_qr2.png при >850 байт
python awg_gen.py decode-awg-qr client_awg_qr.png client_awg_qr2.png
python awg_gen.py decode-vpn test.vpn -o out.bin
python awg_gen.py vpn-to-wg client.vpn -o client.conf
```

Логика в `packages/amnezia_export/`.

## Файрвол entry (`awg_policy.py`)

Политика — yaml. Без секции `ru_exit` скрипт пишет файрвол пары. С секцией — тот же файрвол плюс второй канал и таблицу российских сетей из ipdeny и ipverse. В ruleset ничего не загружает. Примеры: [policy/entry.example.yaml](policy/entry.example.yaml), [policy/entry.ru.example.yaml](policy/entry.ru.example.yaml). Ручной порядок: [docs/deploy_entry_ru_exit.md](docs/deploy_entry_ru_exit.md).

```shell
python awg_policy.py render policy/entry.ru.example.yaml --dest /tmp/awg-policy
```

## Ссылки

 - [AmneziaWG-Architect](https://github.com/Vadim-Khristenko/AmneziaWG-Architect) — генератор obfs и профилей мимикрии
 - [AmmeziaVPN Client](https://github.com/amnezia-vpn/amnezia-client) - клиент AmneziaVPN
 - [AmneziaWG Apple Client](https://github.com/amnezia-vpn/amneziawg-apple) - iOS клиент AmneziaWG
