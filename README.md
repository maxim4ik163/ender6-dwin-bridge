# Ender 6 DWIN screen ↔ Klipper bridge

[English](#english) · [Русский](#русский)

---

## English

Brings the stock Creality Ender 6 touch screen (DWIN DMT48270C043_06WT) back to life on a Klipper printer.
A small Python service on the Klipper host talks to the screen over a USB-UART adapter and to Klipper through
Moonraker. The screen keeps its existing UI (InsanityAutomation DW7.4.6); the bridge fills in the values and
turns touches into G-code, the way Marlin used to.

> **Status: early, briefly tested.** Version 0.2.1. Tested on one printer during a single session
> (27 Oct 2026), including one complete ~16 minute print started from the screen. Many functions were tested only
> against a simulated screen and Moonraker (see the table below). Use with care and keep Mainsail/Fluidd at hand.

### How it works

```
DWIN screen ──UART 115200──► USB-UART adapter ──► dwin_bridge.py ──HTTP──► Moonraker ──► Klipper
```

- Every 0.5 s the bridge reads printer state from Moonraker and writes it into the screen variables.
- Touches arrive as "variable + value" frames; the bridge maps them to G-code or Moonraker calls.
- Pages that the stock UI expects the host to switch are switched by the bridge.
- Dependencies: Python 3 and `python3-serial` only.

### Requirements

- Klipper + Moonraker on the host (tested: Klipper v0.13.0-816, Debian 13).
- The screen flashed with the **InsanityAutomation DW7.4.6** `DWIN_SET` for Ender 6 (TM3D). Other screen
  firmwares use different addresses and will not work. Where to get it:
  - release tag [`DW_746`](https://github.com/InsanityAutomation/Marlin/releases/tag/DW_746) and all
    [InsanityAutomation releases](https://github.com/InsanityAutomation/Marlin/releases);
  - source branch [`CrealityDwin_2.0`](https://github.com/InsanityAutomation/Marlin/tree/CrealityDwin_2.0).

  The `DWIN_SET` folder is copied to a FAT32 microSD card and flashed through the slot on the screen board.
- A USB-UART adapter with **3.3 V TTL** levels (CP2102, CH340, FT232, PL2303 TTL). Do **not** use RS-232 level
  ports: ±12 V can damage the screen.

### Wiring

| Screen | Adapter |
|---|---|
| TX | RX |
| RX | TX |
| GND | GND (common ground with the 5 V source) |
| 5V | 5 V from the host USB or the printer board, **not** from the adapter (the screen draws about 250 mA) |

### Feature status

✅ tested on the printer · 🧪 tested only in the simulator · ❌ not implemented

| Area | Function | Status |
|---|---|---|
| Status | Hotend/bed temperatures, targets, fan, speed, flow, XYZ, status line | ✅ |
| Status | Fault page on Klipper shutdown/error with message, return to main page after restart | ✅ |
| Temperatures | Set hotend/bed target, turn off, presets (PLA 200/60, PETG 240/80, ABS 245/100), cool down, fan on/off | ✅ |
| Files | File list from Moonraker (newest first, folders, paging), select and start print | ✅ |
| Printing | Auto switch to print page, progress, time, file name, finish page with beep, "Finish print" | ✅ |
| Printing | Pause / resume / cancel from the screen | 🧪 |
| Printing | Adjust page: speed, flow, Z offset (babystep), fan | 🧪 |
| Move | Home, X/Y/Z arrows (10/1/0.1 mm), grouped moves, blocked until homed | ✅ |
| Move | Motors off | ✅ |
| Leveling | Mesh display (resampled to 5×5), home + Z0, Z ±0.1, measure (`BED_MESH_CALIBRATE`), mesh on/off | 🧪 |
| Leveling | Manual tramming: centre and 4 corners | 🧪 |
| Filament | Load/unload by length, cold-extrusion warning, heat-up page | 🧪 |
| Tools | Hotend/bed PID calibration (double tap) | 🧪 (v0.1 single-tap version started on the printer) |
| Tools | STORE: `Z_OFFSET_APPLY_PROBE` (if a Z offset is set) + `SAVE_CONFIG` (double tap, restarts Klipper) | 🧪 |
| Tools | BLTouch reset | ✅ (command sent) |
| Screen | Volume −/+ | ✅ |
| Screen | Sound on/off, brightness, standby dimming, 180° rotation, stored in `dwin_settings.json` | 🧪 |
| Leveling | Editing single mesh points | ❌ (value is reset) |
| Tools | E-steps page, Motion config page, manual PID values | ❌ |
| Printing | Filament runout and power-loss pages | ❌ |
| Other | G26, IDEX, language selection | ❌ |
| Other | Cyrillic text, G-code thumbnails | ❌ (screen font is ASCII-only) |

### Known limitations

- State is polled over HTTP every 0.5 s (no WebSocket yet).
- File names are cut to 20 characters; all messages on the screen are in English.
- The Z offset during a print and on the leveling page uses `SET_GCODE_OFFSET`; it is only kept after STORE.
- Preheat presets, filament heat temperature and feed rates are constants at the top of `dwin_bridge.py`.
- PID calibration blocks Klipper's G-code queue until it finishes; a cancel pressed meanwhile waits.

### Installation

1. Install the dependency:
   ```bash
   sudo apt install python3-serial git
   ```
2. Clone the repository into `~/screen_bridge`:
   ```bash
   cd ~
   git clone https://github.com/maxim4ik163/ender6-dwin-bridge.git screen_bridge
   ```
3. Find the stable path of the adapter:
   ```bash
   ls -l /dev/serial/by-id/
   ```
   Use the path only, without the ` -> ../../ttyUSB1` part.

### First run (manual)

```bash
python3 ~/screen_bridge/dwin_bridge.py --port /dev/serial/by-id/<your-adapter> -v
```

Within a few seconds the screen should show the main page with real temperatures. Every touch and every G-code
sent is printed to the console. Stop with Ctrl+C.

Options:

| Option | Default | Meaning |
|---|---|---|
| `--port` | (required) | serial port of the screen |
| `--baud` | 115200 | UART speed |
| `--moonraker` | `http://127.0.0.1:7125` | Moonraker address |
| `--settings` | `dwin_settings.json` next to the script | where screen settings are stored |
| `-v` | off | debug log |

### Run as a service

Edit the `--port` value in `dwin-bridge.service`, then:

```bash
sudo cp ~/screen_bridge/dwin-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dwin-bridge
journalctl -u dwin-bridge -f
```

Useful commands: `sudo systemctl restart dwin-bridge`, `sudo systemctl stop dwin-bridge`,
`journalctl -u dwin-bridge -n 100`. Stop the service before running the bridge manually: only one program can
own the serial port.

Optional, updates from Mainsail: add `dwin-bridge` to `~/printer_data/moonraker.asvc` and to `moonraker.conf`:

```ini
[update_manager screen_bridge]
type: git_repo
path: ~/screen_bridge
origin: https://github.com/maxim4ik163/ender6-dwin-bridge.git
primary_branch: main
managed_services: dwin-bridge
```

### Repository layout

| Path | Purpose |
|---|---|
| `dwin_bridge.py` | the bridge |
| `dwin-bridge.service` | systemd unit |
| `tools/dwin_probe.py` | link test: switches a page, writes values, prints touches |
| `tools/parse_dgus.py` | parses a compiled `DWIN_SET` (pages, touch zones, variables) |
| `tests/` | end-to-end test against a simulated screen and Moonraker: `python3 tests/run_test.py` |
| `docs/protocol.md` | screen protocol and address map |

### Credits and license

Button logic and the address map follow InsanityAutomation's Marlin DWIN implementation
([`CrealityDwin_2.0`](https://github.com/InsanityAutomation/Marlin/tree/CrealityDwin_2.0) branch,
`Marlin/src/lcd/extui/ia_creality/`, sponsored by Tiny Machines 3D). Licensed under GPL-3.0, same as Marlin.
The screen firmware (`DWIN_SET`) is not included.

---

## Русский

Возвращает к жизни штатный сенсорный экран Creality Ender 6 (DWIN DMT48270C043_06WT) на принтере с Klipper.
Небольшой сервис на Python на хосте Klipper общается с экраном через USB-UART адаптер, а с Klipper через
Moonraker. На экране остаётся его прежний интерфейс (InsanityAutomation DW7.4.6); мост заполняет значения и
превращает касания в G-code, как раньше это делал Marlin.

> **Состояние: ранняя версия, тестирование было кратковременным.** Версия 0.2.1. Проверено на одном принтере
> за одну сессию (27.10.2026), включая одну полную печать примерно на 16 минут, запущенную с экрана. Многие функции
> проверены только на эмуляции экрана и Moonraker (см. таблицу ниже). Пользуйтесь осторожно и держите Mainsail
> или Fluidd под рукой.

### Как это работает

```
Экран DWIN ──UART 115200──► USB-UART адаптер ──► dwin_bridge.py ──HTTP──► Moonraker ──► Klipper
```

- Раз в 0,5 с мост берёт состояние принтера из Moonraker и записывает его в переменные экрана.
- Касания приходят как «переменная + значение»; мост превращает их в G-code или вызовы Moonraker.
- Страницы, которые штатный интерфейс ждёт от хоста, переключает мост.
- Зависимости: только Python 3 и `python3-serial`.

### Что нужно

- Klipper и Moonraker на хосте (проверено: Klipper v0.13.0-816, Debian 13).
- Экран, прошитый `DWIN_SET` **InsanityAutomation DW7.4.6** для Ender 6 (TM3D). В других прошивках экрана
  адреса другие, с ними мост работать не будет. Где взять:
  - релиз с тегом [`DW_746`](https://github.com/InsanityAutomation/Marlin/releases/tag/DW_746) и все
    [релизы InsanityAutomation](https://github.com/InsanityAutomation/Marlin/releases);
  - исходники, ветка [`CrealityDwin_2.0`](https://github.com/InsanityAutomation/Marlin/tree/CrealityDwin_2.0).

  Папка `DWIN_SET` копируется на microSD (FAT32) и заливается через слот на плате экрана.
- USB-UART адаптер с уровнями **TTL 3,3 В** (CP2102, CH340, FT232, PL2303 TTL). Порты с уровнями RS-232
  использовать **нельзя**: ±12 В могут повредить экран.

### Подключение

| Экран | Адаптер |
|---|---|
| TX | RX |
| RX | TX |
| GND | GND (общая земля с источником 5 В) |
| 5V | 5 В от USB хоста или платы принтера, **не** от адаптера (экран потребляет около 250 мА) |

### Что реализовано

✅ проверено на принтере · 🧪 проверено только на эмуляции · ❌ не реализовано

| Раздел | Функция | Статус |
|---|---|---|
| Статус | Температуры сопла и стола, цели, вентилятор, скорость, поток, XYZ, строка статуса | ✅ |
| Статус | Страница ошибки при shutdown/error Klipper с текстом, возврат на главную после перезапуска | ✅ |
| Температуры | Цель сопла и стола, выключение, пресеты (PLA 200/60, PETG 240/80, ABS 245/100), охлаждение, вентилятор | ✅ |
| Файлы | Список из Moonraker (новые сверху, папки, листание), выбор и запуск печати | ✅ |
| Печать | Автопереход на страницу печати, прогресс, время, имя файла, страница окончания со звуком, «Finish print» | ✅ |
| Печать | Пауза, продолжение, отмена с экрана | 🧪 |
| Печать | Страница Adjust: скорость, поток, Z-offset (babystep), вентилятор | 🧪 |
| Движение | Home, стрелки X/Y/Z (10/1/0,1 мм), объединение нажатий, блокировка до Home | ✅ |
| Движение | Отключение моторов | ✅ |
| Выравнивание | Сетка (пересчёт в 5×5), Home + Z0, Z ±0,1, построение (`BED_MESH_CALIBRATE`), вкл/выкл сетки | 🧪 |
| Выравнивание | Ручное выравнивание: центр и 4 угла | 🧪 |
| Пруток | Загрузка/выгрузка на заданную длину, предупреждение о холодном сопле, страница нагрева | 🧪 |
| Tools | PID-калибровка сопла и стола (двойное нажатие) | 🧪 (версия v0.1 с одним касанием запускалась на принтере) |
| Tools | STORE: `Z_OFFSET_APPLY_PROBE` (если задан Z-offset) + `SAVE_CONFIG` (двойное нажатие, перезапускает Klipper) | 🧪 |
| Tools | Сброс BLTouch | ✅ (команда отправлена) |
| Экран | Громкость −/+ | ✅ |
| Экран | Звук вкл/выкл, яркость, затемнение в простое, поворот на 180°, сохранение в `dwin_settings.json` | 🧪 |
| Выравнивание | Правка отдельных точек сетки | ❌ (значение сбрасывается) |
| Tools | Страницы E-steps и Motion config, ручной ввод PID | ❌ |
| Печать | Страницы окончания прутка и восстановления после отключения питания | ❌ |
| Прочее | G26, IDEX, выбор языка | ❌ |
| Прочее | Кириллица, миниатюры G-code | ❌ (шрифт экрана только ASCII) |

### Известные ограничения

- Состояние опрашивается по HTTP раз в 0,5 с (WebSocket пока не используется).
- Имена файлов обрезаются до 20 символов; все сообщения на экране на английском.
- Z-offset во время печати и на странице выравнивания задаётся через `SET_GCODE_OFFSET`; сохраняется только по STORE.
- Пресеты нагрева, температура для замены прутка и скорости заданы константами в начале `dwin_bridge.py`.
- PID-калибровка занимает очередь G-code Klipper до своего окончания; нажатая в это время отмена ждёт.

### Установка

1. Установите зависимости:
   ```bash
   sudo apt install python3-serial git
   ```
2. Склонируйте репозиторий в `~/screen_bridge`:
   ```bash
   cd ~
   git clone https://github.com/maxim4ik163/ender6-dwin-bridge.git screen_bridge
   ```
3. Узнайте постоянный путь к адаптеру:
   ```bash
   ls -l /dev/serial/by-id/
   ```
   Нужен только путь, без части ` -> ../../ttyUSB1`.

### Первый запуск (вручную)

```bash
python3 ~/screen_bridge/dwin_bridge.py --port /dev/serial/by-id/<ваш-адаптер> -v
```

Через несколько секунд экран должен показать главную страницу с реальными температурами. Каждое касание и
каждая отправленная команда выводятся в консоль. Остановка: Ctrl+C.

Параметры:

| Параметр | По умолчанию | Назначение |
|---|---|---|
| `--port` | (обязателен) | последовательный порт экрана |
| `--baud` | 115200 | скорость UART |
| `--moonraker` | `http://127.0.0.1:7125` | адрес Moonraker |
| `--settings` | `dwin_settings.json` рядом со скриптом | где хранятся настройки экрана |
| `-v` | выкл. | подробный лог |

### Запуск как сервис

Исправьте значение `--port` в `dwin-bridge.service`, затем:

```bash
sudo cp ~/screen_bridge/dwin-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dwin-bridge
journalctl -u dwin-bridge -f
```

Полезные команды: `sudo systemctl restart dwin-bridge`, `sudo systemctl stop dwin-bridge`,
`journalctl -u dwin-bridge -n 100`. Перед ручным запуском остановите сервис: порт может занимать только одна
программа.

Дополнительно, обновления из Mainsail: добавьте `dwin-bridge` в `~/printer_data/moonraker.asvc` и в `moonraker.conf`:

```ini
[update_manager screen_bridge]
type: git_repo
path: ~/screen_bridge
origin: https://github.com/maxim4ik163/ender6-dwin-bridge.git
primary_branch: main
managed_services: dwin-bridge
```

### Структура репозитория

| Путь | Назначение |
|---|---|
| `dwin_bridge.py` | мост |
| `dwin-bridge.service` | юнит systemd |
| `tools/dwin_probe.py` | проверка связи: переключает страницу, пишет значения, выводит касания |
| `tools/parse_dgus.py` | разбор скомпилированного `DWIN_SET` (страницы, зоны касаний, переменные) |
| `tests/` | сквозной тест на эмуляции экрана и Moonraker: `python3 tests/run_test.py` |
| `docs/protocol.md` | протокол экрана и карта адресов |

### Благодарности и лицензия

Логика кнопок и карта адресов повторяют реализацию DWIN в Marlin от InsanityAutomation (ветка
[`CrealityDwin_2.0`](https://github.com/InsanityAutomation/Marlin/tree/CrealityDwin_2.0),
`Marlin/src/lcd/extui/ia_creality/`, спонсор Tiny Machines 3D). Лицензия GPL-3.0, как у Marlin. Прошивка экрана (`DWIN_SET`) в репозиторий не входит.
