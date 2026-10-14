# Настройка по SSH

Те же машины, что в ручных гайдах. Скрипты — shell: гайд и есть последовательность команд, по SSH уходит она же. Ключ на машинах уже лежит, `sudo` без пароля.

Адреса лежат в `ssh/hosts.env` (скопировать из `ssh/hosts.env.example`). Это не inventory Ansible и не `group_vars`.

| Команда | Гайд |
|---|---|
| `ssh/exit.sh` | [deploy_exit.md](../docs/deploy_exit.md), пир `transit` |
| `ssh/exit.sh --peer middle` | тот же гайд, пир `middle`, пока до exit есть прямой SSH |
| `ssh/entry.sh` | [deploy_transit.md](../docs/deploy_transit.md), awg1 из `ssh/artifacts/client_transit.conf` |
| `ssh/entry.sh --uplink client_entry.conf` | тот же гайд, awg1 из middle |
| `ssh/middle.sh` | [deploy_middle.md](../docs/deploy_middle.md), разделы 6–8 |
| `ssh/insert-middle.sh` | [deploy_middle.md](../docs/deploy_middle.md) целиком, на exit только `ssh -J` |
| `ssh/ru-exit.sh` | [deploy_exit.md](../docs/deploy_exit.md) без пира |
| `ssh/add-ru.sh` | [deploy_entry_ru_exit.md](../docs/deploy_entry_ru_exit.md), «Уже работающий entry» |

Пара: `exit.sh`, затем `entry.sh`. Новая тройка, пока exit открыт: `exit.sh --peer middle`, `middle.sh`, `entry.sh --uplink client_entry.conf`. Exit из страны уже закрыт: `insert-middle.sh`. Российский выход: `ru-exit.sh`, затем `add-ru.sh`.

`awg_manage.py init` сам называет каталог `amneziawg_3_<дата>`. Имя пишется в `ssh/artifacts/*.dir`, следующий скрипт подставляет его. Если каталогов несколько и записи нет, скрипт останавливается: задайте `AWG_DIR=amneziawg_3_<дата>`. Клиентские конфиги туннелей тоже забираются в `ssh/artifacts/` (каталог в git не входит).

`add-ru.sh` на время `down`/`up` отключает клиентов entry и заменяет все хуки `awg0`, включая приоритеты `2000` / `2010` / `1100`. После `entry.sh` и `middle.sh` интерфейсы остаются поднятыми.

Повторный `exit.sh` / `entry.sh` / `middle.sh` / `ru-exit.sh` останавливается, если `awg0.conf` уже лежит или в `~/awg` уже есть `server_awg0.conf`. `insert-middle.sh` так же останавливается, если middle уже с `awg0`. `add-ru.sh` останавливается, если правила `fwmark` и приоритет `2000` уже стоят.

Директивы SSH из гайда пишутся в `/etc/ssh/sshd_config.d/00-vpn-hardening.conf`: этот файл читается раньше cloud-init, иначе первая строка `PasswordAuthentication` побеждает. При входе под root в этой строке `PermitRootLogin prohibit-password`, чтобы ключ пережил перезапуск `ssh`. Перед DKMS ставятся заголовки ядра. Перезагрузка только если модуль `amneziawg` ещё не загружен.
