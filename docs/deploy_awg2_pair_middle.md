---
date: 2026-09-24
status: draft
aliases: ["Пара AmneziaWG 2.0: middle и новый exit на 3.1"]
---
# Пара AmneziaWG 2.0: middle и новый exit на 3.1

Пара уже стоит на AmneziaWG 2.0. Из страны до exit нет ни туннеля, ни SSH. Entry и клиенты остаются на 2.0.

Новая виртуалка — middle. Ansible поднимает middle и заново поднимает exit, оба на пакетах 3.1. Entry playbook не трогает. После прогона на middle руками убираем поля 3.1 у входа для entry и на entry меняем только `awg1`.

`insert_middle.yml` и полный `bootstrap.yml` для этой пары не запускать. Insert ждёт уже живой exit 3.1 и положит на entry клиентский файл с полями 3.1. Полный bootstrap дойдёт до entry и остановится: там уже есть `awg0`.

Ниже везде `vps`, `MIDDLE_IP`, `EXIT_IP`, `ENTRY_IP`. Подставьте свои.

## 1. Ключ на middle

Новая чистая виртуалка за границей, другой хостер чем exit. С ноутбука:

```sh
ssh-copy-id vps@MIDDLE_IP
ssh vps@MIDDLE_IP
```

## 2. Заход на exit через middle

На exit ключ ноутбука уже лежит с прошлого деплоя. Middle только прокидывает TCP до `exit:22`, своим ключом на exit не логинится.

```sh
ssh -J vps@MIDDLE_IP vps@EXIT_IP
```

Если ключа на exit всё же нет, с ноутбука (пароль exit, если он ещё включён):

```sh
ssh-copy-id -o ProxyJump=vps@MIDDLE_IP vps@EXIT_IP
ssh -J vps@MIDDLE_IP vps@EXIT_IP
```

## 3. На exit: снести старый туннель

Уже с прыжка из шага 2. Каталог скриптов — `/home/vps/awg`.

```sh
sudo awg-quick down awg0 || true
sudo systemctl disable --now awg-quick@awg0 || true
sudo rm -f /etc/amnezia/amneziawg/awg0.conf
rm -rf ~/awg
```

Пока `awg0.conf` на месте, bootstrap откажется работать. Метка «amneziawg уже ставили» лежала в `~/awg`, вместе с каталогом она пропадёт, и playbook перезагрузит exit.

Там же, на exit, пакеты 2.0 надо сменить на 3.1. Playbook на шаге 5 делает это сам (`state: latest` из `ppa:amnezia/ppa`) и после установки перезагружает машину, чтобы подхватился новый модуль DKMS. Руками — те же три пакета, пока сессия с прыжка ещё открыта:

```sh
sudo apt update
sudo apt install -y software-properties-common "linux-headers-$(uname -r)"
sudo add-apt-repository -y ppa:amnezia/ppa
sudo apt update
sudo apt install -y amneziawg amneziawg-dkms amneziawg-tools
```

`amneziawg` — метапакет, `amneziawg-dkms` — модуль ядра 3.1, `amneziawg-tools` — бинарь `awg` / `awg-quick`. Заголовки ядра нужны, иначе DKMS модуль не соберёт. Перезагрузку можно не делать сейчас: её выполнит playbook. Пока она не прошла, в памяти ещё старый модуль 2.0.

После ребута проверка:

```sh
awg --version
strings "$(command -v awg)" | grep -E 'HeaderProtectionKey|RandomTrailers|DisableCookies'
```

В выводе `grep` должны быть все три имени. Playbook эту проверку тоже делает и останавливается, если строк нет.

## 4. `hosts.yml`

В группе `middle` хост, на `vpn_exit` прыжок. Без этой строки playbook пойдёт на exit напрямую и из страны не зайдёт.

```yaml
middle:
  hosts:
    vpn_middle:
      ansible_host: MIDDLE_IP
      ansible_user: vps
      endpoint_host: MIDDLE_IP
  vars:
    awg_subnet: 10.10.3.0/24
    server_name: "Middle 3.1"

exit:
  hosts:
    vpn_exit:
      ansible_host: EXIT_IP
      ansible_user: vps
      ansible_ssh_common_args: '-o ProxyJump=vps@MIDDLE_IP'
```

`endpoint_host` и `server_name` exit оставьте как были в вашем `hosts.yml`, кроме пользователя и ProxyJump.

## 5. Playbook

С ноутбука, из каталога `ansible`:

```sh
cd ansible
ansible-playbook -i inventory/hosts.yml playbooks/bootstrap.yml --limit exit:middle
```

Exit перезагрузится посреди прогона, playbook дождётся его сам. На exit к этому моменту стоят `amneziawg`, `amneziawg-dkms` и `amneziawg-tools` 3.1 из шага 3. Дальше exit на 3.1, middle на 3.1, `awg1` middle смотрит на этот exit. Entry не менялся, его `awg1` всё ещё смотрит на старый exit и с страны не поднимется, пока не сделаны шаги 6 и 7.

Клиентский файл для entry лежит на ноутбуке: `ansible/artifacts/client_entry.conf`. На middle тот же файл: `~/awg/amneziawg_3_middle/client_entry.conf`.

## 6. На middle: убрать поля 3.1 у входа для entry

Хоп middle → exit остаётся 3.1. Не трогать `awg1` middle и конфиг exit.

Править три файла, одни и те же строки. Два — сервер middle, третий — файл, который поедет на entry:

```sh
ssh vps@MIDDLE_IP
vim ~/awg/amneziawg_3_middle/server_awg0.conf
sudo vim /etc/amnezia/amneziawg/awg0.conf
vim ~/awg/amneziawg_3_middle/client_entry.conf
```

И на ноутбуке тот же список в `ansible/artifacts/client_entry.conf`, если на entry понесёте его, а не копию с middle.

Удалить строки:

```
HeaderProtectionKey
ContentPaddingAddition
RekeyAfterTime
RekeyTimeout
RejectAfterTime
KeepaliveTimeout
MaxHandshakeAttempts
RandomTrailers
DisableCookies
```

Оставить `Jc`, `Jmin`, `Jmax`, `S1`–`S4`, `H1`–`H4`, `I1`–`I5`. Это поля 2.0, старый `awg` на entry их понимает.

Перезапустить только `awg0`:

```sh
sudo awg-quick down awg0
sudo awg-quick up awg0
sudo awg show awg0
sudo awg show awg1
```

`awg1` не гасить: handshake с новым exit должен остаться.

## 7. На entry: поменять только awg1

Entry из страны открывается напрямую. `awg0` и клиентские конфиги не трогаем.

```sh
ssh vps@ENTRY_IP
sudo cp /etc/amnezia/amneziawg/awg1.conf /etc/amnezia/amneziawg/awg1.conf.bak
```

```sh
# с ноутбука, файл уже без полей из шага 6
scp ansible/artifacts/client_entry.conf vps@ENTRY_IP:awg/client_entry.conf
```

На entry:

```sh
sudo cp ~/awg/client_entry.conf /etc/amnezia/amneziawg/awg1.conf
sudo vim /etc/amnezia/amneziawg/awg1.conf
```

`DNS` закомментировать. Перед `[Peer]`:

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

`awg0` не перезапускать. Handshake `awg1` должен быть с middle, default в `clientroute` — через `awg1`.

Клиентский трафик: entry `awg0` → `awg1` → middle → exit. Из страны до exit напрямую по-прежнему ничего нет.
