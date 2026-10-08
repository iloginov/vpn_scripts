---
date: 2026-09-04
status: draft
aliases: ["Настройки параметров AmneziaWG 3.0", "Настройки параметров AmneziaWG 3.1"]
---
# Настройки параметров AmneziaWG 3.x

Поверх AmneziaWG 2.0 (см. [[awg2_protocol_params]]) добавляются блоки 3.0 и 3.1.
Параметры выверены по исходникам:

- [amnezia-vpn/amneziawg-go](https://github.com/amnezia-vpn/amneziawg-go) ≥ `v3.1`
- [amnezia-vpn/amneziawg-tools](https://github.com/amnezia-vpn/amneziawg-tools) ≥ `v3.1`
- [amnezia-vpn/amneziawg-linux-kernel-module](https://github.com/amnezia-vpn/amneziawg-linux-kernel-module) ≥ `v3.1`
- [amnezia-vpn/amnezia-client](https://github.com/amnezia-vpn/amnezia-client) ≥ `5.0.1.5` (`awgV3 = "3.1"`)

Логика генерации — порт [Vadim-Khristenko/Any-Tech-ARCHITECT](https://github.com/Vadim-Khristenko/Any-Tech-ARCHITECT).

Рекомендуемый профиль для сервера: **QUIC Initial** на UDP **443**, MTU **1280**.

## 3.0 vs 3.1

| Версия | Что появилось |
|--------|----------------|
| **3.0** | `HeaderProtectionKey` (ChaCha20 на заголовок), `ContentPaddingAddition` (случайный паддинг транспорта), рандомизированные таймеры |
| **3.1** | `RandomTrailers` — добивка пакета случайным хвостом до MTU; `DisableCookies` — не отвечать `cookiereply` на WG-порту |

Оба ключа 3.1 — **local**: у сервера и клиента свои значения. Устройство с 3.0 **откажется парсить** конфиг, если в нём есть эти ключи — поэтому при выключенных обоих флажках строки не пишутся вовсе. Если включён хотя бы один, `amneziawg-tools` требуют **пару** строк `1`/`0` (не `true`/`on`: tools не принимают `true`).

AmneziaVPN при self-hosted установке включает оба (`on`). Architect по умолчанию держит `DisableCookies` выключенным (ломает NAT keepalive под нагрузкой). Мы следуем клиенту: оба **включены**.

В `amneziawg-go` 3.1 широкие диапазоны H1–H4 (до 100M) дорого сканируются при header protection. Генератор сужает ширину окна (~20k / ~30k для H4).

## Scope параметров

| Группа | Параметры | Совпадение сервер ↔ клиент |
|--------|-----------|----------------------------|
| **shared** | H1–H4, S1–S4, `HeaderProtectionKey` | Обязаны совпадать |
| **sender** | Jc/Jmin/Jmax, I1–I5, `ContentPaddingAddition` | Могут отличаться на каждом устройстве |
| **local** | таймеры 3.0, `RandomTrailers`, `DisableCookies` | У каждой стороны свои |

## Новые ключи 3.0

| Ключ | Scope | Формат |
|------|-------|--------|
| `HeaderProtectionKey` | shared | 32 байта base64 (как PrivateKey) |
| `ContentPaddingAddition` | sender | диапазон `lo-hi` |
| `RekeyAfterTime` и др. таймеры | local | диапазон `lo-hi` |

При включённом `HeaderProtectionKey`: **S1–S4 ≥ 12** (nonce ChaCha20 из первых 12 байт S-паддинга). Иначе интерфейс не поднимется.

## Новые ключи 3.1

| Ключ | Scope | Формат |
|------|-------|--------|
| `RandomTrailers` | local | `1` / `0` |
| `DisableCookies` | local | `1` / `0` |

## Коллизии длин (S)

Правильные offsets (из размеров WG 148/92/64):

- `S2 ≠ S1 + 56`
- `S3 ≠ S1 + 84`
- `S3 ≠ S2 + 28`
- `S4 ≤ 32`

## Экспорт клиентам (`vpn://` и QR)

- Контейнер в `vpn://` остаётся **`amnezia-awg2`**. Отдельного `amnezia-awg3` нет: в AmneziaVPN 5.x `Awg` = `amnezia-awg` (1.x), `Awg2` = `amnezia-awg2` (2.0/3.x), оба отображаются как «AmneziaWG».
- `protocol_version`: **`"3.1"`** при любом блоке 3.x (HPK / CPA / таймеры / флаги 3.1), иначе `"2"`. Свежий клиент сравнивает это поле с константой `awgV3 = "3.1"` и помечает всё остальное (включая старое `"3"`) предупреждением «AmneziaWG 2.0 is outdated».
- Алгоритм `vpn://` (qCompress + base64url) и QR-чанки (magic 1984) **не менялись**.
- Поля 3.x пишутся в `awg`, в `last_config` и во вложенный `config`.

## Установка на сервер

Нужны `amneziawg-tools` и DKMS-модуль с поддержкой 3.1. На Ubuntu 24.04
достаточно `ppa:amnezia/ppa` (`amneziawg` / `amneziawg-dkms` / `amneziawg-tools`).
Playbook после установки проверяет, что `awg` знает `HeaderProtectionKey`,
`RandomTrailers` и `DisableCookies` (см. `ansible/tasks/shared/03_install_amneziawg.yml`).

## Backlinks

- [[awg2_protocol_params]]
