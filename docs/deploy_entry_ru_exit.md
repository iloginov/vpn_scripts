---
date: 2026-10-11
status: draft
aliases: ["deploy_entry_ru_exit", "Выход через РФ"]
---
# Выход российских сетей через второй сервер

Ручное добавление к entry, который уже работает по [deploy_transit.md](deploy_transit.md). Тот же порядок — ветка «с выходом через РФ» при настройке entry с нуля.

Ansible: [deploy_ansible.md](deploy_ansible.md). `bootstrap_ru_exit.yml` поднимает российский выход, `add_ru_exit.yml` подключает уже работающий entry. Playbook не опускает `awg0` и дописывает в него только правило метки `1100`. Раздел «Уже работающий entry» ниже — ручная замена всех хуков, она как раз опускает `awg0`. SSH-скрипт `ssh/add-ru.sh` идёт по этому ручному разделу: [../ssh/README.md](../ssh/README.md).

Клиентские конфиги не перевыпускаются. `awg1` и таблица `clientroute` остаются путём для всего, что не попало в список российских сетей.

```text
клиент Amnezia
      │
      ▼
entry, awg0 (10.10.2.0/24)
      │
      ├── назначение в списке RU, метка 0x100
      │       └── таблица ruroute → awg2 → сервер в РФ → интернет
      │
      └── остальное
              └── таблица clientroute → awg1 → exit или middle
```

На entry клиентский адрес прячется NAT-ом. Сервер в РФ видит один источник — адрес `awg2` (в примерах `10.10.4.2/32`), не адреса `10.10.2.0/24`. Так же exit видит только адрес `awg1`.

Сплит только IPv4. Список сетей — агрегат RU с ipdeny. DNS клиентов (`1.1.1.1`) в этот список не входит, запросы идут в `awg1`, а уже резолвленный российский адрес — в `awg2`.

Состояние после перезагрузки и после `down`/`up` держат те же места, что и пара без РФ:

| Что | Где |
|---|---|
| фильтр, NAT, MSS, set `ru_nets`, метка, `forward_guard` | `/etc/nftables.conf` и include, грузит `nftables.service` |
| правила `ip rule` | `PostUp` / `PostDown` в `awg0.conf` |
| default в `clientroute` | `PostUp` / `PostDown` в `awg1.conf` |
| default в `ruroute` | `PostUp` / `PostDown` в `awg2.conf` |
| имена таблиц | `/etc/iproute2/rt_tables` |

`awg-quick down` выполняет `PostDown` после удаления интерфейса. Селектор `ip rule` от того, жив ли интерфейс, не зависит. Отдельный сервис для метки не нужен: иначе у правила нет `PostDown`, и после `down awg0` оно остаётся в ядре.

Drop-in ниже задаёт только порядок старта (`awg0` после `awg1`, `awg2` и nftables). Маршрутов он не хранит и при `down` соседа `awg0` не гасит.

Метка ставится через `meta mark set meta mark | 0x100`: бит `0x100` добавляется, остальные не стираются. В [xray_plan.md](xray_plan.md) для своего outbound занята метка `255` (`0xff`); биты не пересекаются.

## Уже работающий entry

Entry уже по [deploy_transit.md](deploy_transit.md): `awg0` + `awg1`, в `ip rule` приоритет `1000` на подсеть клиентов и два правила `clientroute` без явного приоритета (около `32764` / `32765`).

Между `down awg0` и следующим `up` клиенты отключены. `awg1` не останавливать.

### 0. Проверить, что править ещё нечего

```sh
sysctl net.ipv4.conf.all.rp_filter net.ipv4.conf.default.rp_filter
ip -4 rule show
ip -4 route show table clientroute
systemctl is-enabled awg-marked-rule.service 2>/dev/null || true
```

`rp_filter` должен быть `2` (loose). Так и стоит в Ubuntu (`/etc/sysctl.d/10-network-security.conf`). Значение `1` режет ответ пакетов при policy routing. Если стоит `1`:

```sh
printf 'net.ipv4.conf.all.rp_filter=2\nnet.ipv4.conf.default.rp_filter=2\n' | sudo tee /etc/sysctl.d/99-rp-filter.conf
sudo sysctl --system
```

`clientroute` содержит `default dev awg1`. Правил с `fwmark` и таблицы `ruroute` ещё нет.

Если раньше метку уже ставили руками и есть `awg-marked-rule.service`, выключить его и снять правило. У того юнита нет `ExecStop`, `disable --now` само правило не удаляет:

```sh
sudo systemctl disable --now awg-marked-rule.service
sudo ip -4 rule del priority 1100 fwmark 0x100/0x100 iif awg0 lookup ruroute 2>/dev/null || true
sudo ip -4 rule del priority 1100 fwmark 0x100/0x100 iif awg0 lookup main 2>/dev/null || true
```

Повторять `del`, пока команда не начнёт отвечать, что правила нет.

### 1. Пир на сервере в РФ

Сервер в РФ — любой AmneziaWG, который уже выпускает своих клиентов в интернет. Если это exit по [deploy_exit.md](deploy_exit.md), его NAT на подсеть `awg0` уже подходит: новый пир получит адрес из той же подсети.

Подсеть этого сервера не должна пересекаться с `10.10.2.0/24` и с адресом `awg1` на entry. В примерах ниже у `awg2` адрес `10.10.4.2/32`. Если сервер в РФ выдаст другой — подставь его во всех командах `awg2`, сам маршрут `ruroute` от цифры адреса не зависит.

На сервере в РФ:

```sh
cd ~/awg
source .venv/bin/activate

python awg_manage.py add \
   --server-conf amneziawg_3_<date_time>/server_awg0.conf \
   --name entry \
   --mimic quic_initial \
   --intensity medium \
   --junk-level 5
```

Забрать `client_entry.conf` на entry как `~/awg/client_ru.conf`. Это не `client_entry.conf` тройки: тот файл — `awg1` на middle. Не перепутать.

Поднять обновлённый `awg0` на сервере в РФ, как после обычного `add`.

### 2. Таблица ruroute

На entry, один раз:

```sh
grep -q '^101[[:space:]]\+ruroute$' /etc/iproute2/rt_tables \
  || echo "101 ruroute" | sudo tee -a /etc/iproute2/rt_tables
```

### 3. awg2

```sh
sudo cp ~/awg/client_ru.conf /etc/amnezia/amneziawg/awg2.conf
sudo vim /etc/amnezia/amneziawg/awg2.conf
```

Закомментировать `DNS`. Перед `[Peer]`:

```
Table = off
PostUp = ip route add default dev awg2 table ruroute 2>/dev/null || true
PostDown = ip route del default dev awg2 table ruroute 2>/dev/null || true
```

Копию с этими строками оставить в `~/awg/client_ru.conf`.

Туннель можно поднять сразу: таблица `ruroute` ещё ничем не выбрана, клиенты по-прежнему идут в `awg1`.

```sh
sudo awg-quick up awg2
sudo systemctl enable awg-quick@awg2
```

В `awg show awg2` handshake — секунды, не пусто. `ip -4 route show table ruroute` — `default dev awg2`.

### Файл entry.yaml

Его кладут на entry как `~/awg/entry.yaml`. В репозитории два готовых файла: без сплита — [policy/entry.example.yaml](../policy/entry.example.yaml), со сплитом — [policy/entry.ru.example.yaml](../policy/entry.ru.example.yaml). Для этого гайда копируют второй и правят подсеть, если она не `10.10.2.0/24`.

```yaml
client:
  iface: awg0
  subnet: 10.10.2.0/24
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
  pools:
    combine: union
    sources:
      - name: ipdeny
        url: https://www.ipdeny.com/ipblocks/data/aggregated/ru-aggregated.zone
      - name: ipverse
        url: https://raw.githubusercontent.com/ipverse/country-ip-blocks/master/country/ru/ipv4-aggregated.txt
```

| Ключ | Смысл |
|---|---|
| `client.iface`, `client.subnet` | вход клиентов, обычно `awg0` и `10.10.2.0/24`. Подсеть должна совпасть с `Address` в `awg0` |
| `client.listen_port` | UDP, который файрвол открывает снаружи. Это порт `awg0`, не `awg1` и не `awg2` |
| `uplink` | основной канал. Интерфейс `awg1`, таблица `clientroute`, id `100` — строка в `/etc/iproute2/rt_tables` |
| `rules.to_client_priority` | приоритет `1000`: пакеты к адресам клиентов идут в `main`, а не в туннель |
| `rules.iif_priority`, `rules.from_priority` | приоритеты `2000` и `2010` у правил `iif awg0` и `from <подсеть>` → `clientroute`. В хуки попадают только вместе с `ru_exit`. Без секции эти два правила остаются без явного приоритета, как на текущих entry |
| `ru_exit` | второй канал. Нет секции — файрвол без `awg2` и без списков сетей |
| `ru_exit.iface`, `table`, `table_id` | `awg2`, таблица `ruroute`, id `101` |
| `ru_exit.fwmark`, `priority` | метка `0x100` и приоритет `1100`. Он обязан лежать строго между `to_client_priority` и `iif_priority` |
| `ru_exit.rules_path` | куда потом копируют `rules.nft`. Тот же путь скрипт вписывает в `include` внутри `nftables.conf` |
| `pools.combine` | `union` — адрес российский, если он есть хотя бы в одном списке. `intersection` — только пересечение адресного пространства. `first` — только первый URL |
| `pools.sources` | откуда качать списки. Имена `ipdeny` и `ipverse` попадают в имена файлов в `pools/` |

`awg1` в этом файле — имя интерфейса и таблица маршрутов. Адрес туннеля (`10.10.1.2/32` или другой) сюда не пишется: он уже лежит в клиентском конфиге, из которого собран `awg1.conf`. То же для адреса `awg2`.

### 4. Собрать файрвол из policy

На entry нужны `awg_policy.py`, каталог `packages/` и venv с `requirements.txt` (`pyyaml`). Дальше `render` читает `~/awg/entry.yaml`.

```sh
cd ~/awg
source .venv/bin/activate
pip install -r requirements.txt
python awg_policy.py render entry.yaml --dest /tmp/awg-policy
```

Скрипт качает ipdeny и ipverse, проверяет, что в списках только публичные IPv4-сети, и пишет файлы. Если скачивание или проверка оборвались, каталог `--dest` не меняется. В рабочий ruleset он ничего не грузит.

```text
/tmp/awg-policy/nftables.conf
/tmp/awg-policy/rules.nft
/tmp/awg-policy/hooks/awg0.hooks
/tmp/awg-policy/hooks/awg1.hooks
/tmp/awg-policy/hooks/awg2.hooks
/tmp/awg-policy/pools/
```

`pools.combine: union` считает адрес российским, если он есть хотя бы в одном списке. Повторять `render` — это и есть обновление пулов.

`forward_guard` стоит раньше обычного filter forward. Российский пакет с `awg0`, который собрался уйти не в `awg2`, отбрасывается. Пока `awg2` поднят и правило `1100` на месте, правило не совпадает. Оно закрывает провал: `down awg2` снимает default в `ruroute`, пустая таблица не выбирает маршрут, ядро идёт в `clientroute` (`awg1`). Guard этот переход режет.

NAT на `eth0` в эту таблицу не входит. Выход в РФ — `awg2`, не адрес самого entry.

Пока не подменять рабочий `/etc/nftables.conf` и не делать `nft -f`. Сам файл `rules.nft` таблицу не включает. Guard при старых правилах `clientroute` отрежет текущий российский трафик: он ещё выходит в `awg1`.

### 5. Хуки awg0

Править файл, из которого `awg_manage.py` пересобирает сервер (`amneziawg_3_<date_time>/server_awg0.conf`), а не живой `/etc/amnezia/amneziawg/awg0.conf`. Следующий `add` сохраняет хуки только из своего файла. В `/etc` его копируют после `down`.

Блок для подсети `10.10.2.0/24` скрипт пишет в `hooks/awg0.hooks`. Заменить им шесть старых строк `PostUp` / `PostDown`:

```
PostUp = ip rule add from 10.10.2.0/24 table clientroute prio 2010 2>/dev/null || true
PostUp = ip rule add iif awg0 table clientroute prio 2000 2>/dev/null || true
PostUp = ip rule add to 10.10.2.0/24 table main prio 1000 2>/dev/null || true
PostUp = ip rule add fwmark 0x100/0x100 iif awg0 lookup ruroute prio 1100 2>/dev/null || true

PostDown = ip rule del from 10.10.2.0/24 table clientroute prio 2010 2>/dev/null || true
PostDown = ip rule del iif awg0 table clientroute prio 2000 2>/dev/null || true
PostDown = ip rule del to 10.10.2.0/24 table main prio 1000 2>/dev/null || true
PostDown = ip rule del fwmark 0x100/0x100 iif awg0 lookup ruroute prio 1100 2>/dev/null || true
```

`1100` раньше `2000`, поэтому помеченный пакет не доходит до общего `iif awg0 → clientroute`. Явный приоритет нужен, чтобы `PostDown` снял то же правило, которое добавил `PostUp`. `|| true` оставляет повторный `up` рабочим: второй `add` на занятый приоритет не роняет интерфейс.

Приоритет `1000` (`to 10.10.2.0/24 → main`) без изменений. Ответы клиентам не уходят в туннель.

### 6. Положить файлы и проверить синтаксис

`nftables.conf` из `--dest` уже содержит NAT и MSS на `awg2` и `include` на путь из yaml (`/etc/awg-ru-split/rules.nft`). UDP `443` — это `awg0`. Файл начинается с `flush ruleset`. Без `include` перезапуск `nftables` сотрёт `awg_ru_split`.

`nft -c` только проверяет файл из `--dest` и не подменяет рабочий `/etc/nftables.conf`. Include внутри указывает на `/etc/awg-ru-split/rules.nft`, поэтому сам список туда уже кладём: пока старый `nftables.conf` его не включает, таблица не активна. `nft -f` здесь не запускать.

```sh
sudo mkdir -p /etc/awg-ru-split
sudo cp /tmp/awg-policy/rules.nft /etc/awg-ru-split/rules.nft
sudo nft -c -f /tmp/awg-policy/nftables.conf
```

### 7. Переключение

`awg1` и `awg2` в этот момент подняты. `ruroute` уже с default через `awg2`.

Опустить `awg0`, пока в `/etc/amnezia/amneziawg/awg0.conf` ещё старые хуки без `prio 2000` / `2010` / `1100`. Иначе `PostDown` не совпадёт со старыми правилами, они останутся рядом с новыми.

```sh
sudo awg-quick down awg0
ip -4 rule show
```

Ожидается только `local`, `main`, `default`. Если `32764` / `32765` или `fwmark` ещё есть — остановиться и снять их вручную, до `up` не идти.

Дальше поставить новый конфиг и ruleset, потом поднять `awg0`:

```sh
sudo cp ~/awg/amneziawg_3_<date_time>/server_awg0.conf /etc/amnezia/amneziawg/awg0.conf
sudo cp /tmp/awg-policy/nftables.conf /etc/nftables.conf
sudo nft -f /etc/nftables.conf
sudo awg-quick up awg0
```

Строка маршрута `awg2` в конфиге интерфейса — `hooks/awg2.hooks`.

Порядок старта после перезагрузки:

```sh
sudo mkdir -p /etc/systemd/system/awg-quick@awg0.service.d
sudo tee /etc/systemd/system/awg-quick@awg0.service.d/10-routing.conf >/dev/null <<'EOF'
[Unit]
After=nftables.service awg-quick@awg1.service awg-quick@awg2.service
Wants=awg-quick@awg1.service awg-quick@awg2.service
EOF
sudo systemctl daemon-reload
```

### 8. Проверка

```sh
ip -4 rule show
ip -4 route show table clientroute
ip -4 route show table ruroute
```

```text
1000:  to 10.10.2.0/24 lookup main
1100:  fwmark 0x100/0x100 iif awg0 lookup ruroute
2000:  iif awg0 lookup clientroute
2010:  from 10.10.2.0/24 lookup clientroute
```

`clientroute` — `default dev awg1`. `ruroute` — `default dev awg2`.

`ip route get` метку nftables сам не ставит. Одна и та же цель проверяет выбор таблицы:

```sh
ip -4 route get 1.1.1.1 from 10.10.2.2 iif awg0
ip -4 route get 1.1.1.1 from 10.10.2.2 iif awg0 mark 0x100
```

Без метки — `dev awg1 table clientroute`. С меткой — `dev awg2 table ruroute`. Подставь вместо `10.10.2.2` адрес живого пира.

С клиента открыть российский ресурс, затем:

```sh
sudo nft list chain ip awg_ru_split prerouting
sudo nft list chain ip awg_ru_split forward_guard
```

У `prerouting` растут `packets` и `bytes`. У `forward_guard` счётчик остаётся нулевым, пока `awg2` поднят и правило `1100` на месте.

### Обновление пулов

Когда сплит уже работает (`awg2` поднят, правило `1100` на месте), то же `render` заново забирает списки. В ruleset попадает только успешная сборка:

```sh
python awg_policy.py render entry.yaml --dest /tmp/awg-policy \
  && sudo cp /tmp/awg-policy/rules.nft /etc/awg-ru-split/rules.nft \
  && sudo cp /tmp/awg-policy/nftables.conf /etc/nftables.conf \
  && sudo nft -c -f /etc/nftables.conf \
  && sudo nft -f /etc/nftables.conf
```

### Как отрабатывает down

`down` одного интерфейса не тянет остальные. Drop-in этого не делает.

**down awg0.** `PostDown` снимает `1000`, `1100`, `2000`, `2010`. `awg1` и `awg2` остаются, их default тоже. Пока интерфейса нет, селектор `iif awg0` не совпадает.

```sh
sudo awg-quick down awg0
ip -4 rule show
sudo awg-quick up awg0
```

**down awg2.** Снимается только `default dev awg2 table ruroute`. Правило `1100` остаётся, таблица пустая, выбор не удаётся, следующее правило — `clientroute` и `awg1`. Реальный пакет в российскую сеть до `awg1` не доходит: его режет `forward_guard`.

```sh
sudo awg-quick down awg2
ip -4 route show table ruroute
ip -4 route get 1.1.1.1 from 10.10.2.2 iif awg0 mark 0x100
sudo awg-quick up awg2
```

Пустая `ruroute` на пробе `1.1.1.1` с меткой покажет `dev awg1 table clientroute`. Это провал маршрута, не дырка guard: `1.1.1.1` не в set `ru_nets`, guard на него не смотрит.

**down awg1.** Снимается `default dev awg1 table clientroute`. Российский путь не меняется: `1100` выбирает `ruroute` раньше. Непомеченный трафик ищет пустую `clientroute`, не находит маршрут и доходит до `main` (`eth0` entry). NAT на `eth0` нет. Так же ведёт себя пара без РФ.

Пир замолчал, а `awg-quick down` не делали — хуки не бегут, default остаётся, пакеты уходят в интерфейс и там пропадают. Так же устроен `awg1` без этой ветки.

## С нуля

Общие шаги — [deploy_transit.md](deploy_transit.md), до копирования `awg1` включительно: SSH, fail2ban, unattended, AmneziaWG, `init` для `awg0`, клиентский конфиг в `awg1.conf` с закомментированным `DNS`, `Table = off` и маршрутом `default dev awg1 table clientroute`.

Дальше из `deploy_transit.md` не брать:

- `nftables.conf` без `awg2` и без include;
- `ip rule` / `ip route` руками до хуков;
- шесть строк `PostUp` / `PostDown` без приоритетов `2000` / `2010` / `1100`;
- проверку `down`/`up` в конце того файла.

Вместо них — разделы 2–8 выше. Отличия от «уже работающего entry»:

- старых правил `32764` / `32765` нет, шаг «сначала `down` со старыми хуками» не нужен;
- `awg0` первый раз поднимать уже с хуками из раздела 5, после `nft -c` и `awg-quick up awg2`;
- порядок первого старта: `nftables` → `awg1` → `awg2` → `awg0`.

`awg1` поднять и включить в автозапуск так же, как в `deploy_transit.md`, до `awg0`.

## Откат к entry без РФ

`awg1` не трогать.

```sh
sudo awg-quick down awg0
```

Вернуть в `server_awg0.conf` и в `/etc/amnezia/amneziawg/awg0.conf` шесть строк без `prio 2000` / `2010` и без `fwmark` — блок из [deploy_transit.md](deploy_transit.md).

Из `/etc/nftables.conf` убрать masquerade и MSS для `awg2` и строку `include`. Вернуть файл к виду из `deploy_transit.md`.

```sh
sudo nft -f /etc/nftables.conf
sudo awg-quick down awg2
sudo systemctl disable awg-quick@awg2
sudo rm -f /etc/systemd/system/awg-quick@awg0.service.d/10-routing.conf
sudo systemctl daemon-reload
sudo awg-quick up awg0
```

`ip -4 rule show` снова как у пары без РФ: `1000`, затем `clientroute` около `32764` / `32765`. Строку `101 ruroute` в `rt_tables` можно оставить.

## Что делает ansible

Два отдельных playbook, не `bootstrap.yml`. Подробности и команды — [deploy_ansible.md](deploy_ansible.md).

`bootstrap_ru_exit.yml` поднимает пустую российскую ноду: init без пира, тот же файрвол, что у обычного exit, `awg0` в автозапуске. Пиры entry появляются позже.

`add_ru_exit.yml` подключает уже работающий entry и `awg0` не опускает:

- на российском выходе — пир с именем `ru_peer_name` или `inventory_hostname`, затем `awg syncconf`;
- на entry — `awg2` с default в `ruroute`, и дальше только после handshake;
- в оба `awg0.conf` дописываются `PostUp` / `PostDown` метки `1100`, то же правило ставится сразу `ip rule add`;
- старые правила `clientroute` без явного приоритета не переписываются. Файл `hooks/awg0.hooks` на живой entry не ставится: в нём приоритеты `2000` / `2010`, их ставят только при сборке с нуля, и для этого нужен `down`;
- `awg_policy.py render` на ноутбуке пишет `nftables.conf` и `rules.nft`. `nft -f` — после правила `1100` и handshake, иначе `forward_guard` уронит текущий российский трафик, который ещё идёт в `awg1`.
