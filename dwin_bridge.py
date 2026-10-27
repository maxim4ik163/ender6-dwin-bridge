#!/usr/bin/env python3
"""
dwin_bridge.py — мост между штатным экраном DWIN Ender 6 (UI InsanityAutomation DW7.4.6) и Klipper/Moonraker.

Экран остаётся «терминалом»: страницы, кнопки и поля уже есть в DWIN_SET. Мост
  * раз в полсекунды берёт состояние принтера из Moonraker (HTTP) и пишет его в переменные экрана;
  * принимает касания (VP + значение) и превращает их в G-code / команды Moonraker;
  * переключает страницы там, где штатный UI ждёт этого от хоста (как это делал Marlin).

Зависимости: только python3-serial (sudo apt install python3-serial). Moonraker — через urllib.

Запуск:   python3 dwin_bridge.py --port /dev/serial/by-id/usb-...  [--moonraker http://127.0.0.1:7125] [-v]
Логика кнопок повторяет Marlin InsanityAutomation (Marlin/src/lcd/extui/ia_creality/creality_extui.cpp).
"""
import argparse
import json
import logging
import os
import queue
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import serial

VERSION = "0.2.1"
log = logging.getLogger("dwin")

# ----------------------------------------------------------------------------- настройки
PREHEATS = {            # кнопки на странице выбора пресета (VP 0x1030 = 5/6/7)
    5: ("PLA", 200, 60),
    6: ("PETG", 240, 80),
    7: ("ABS", 245, 100),
}
FILAMENT_HEAT_TEMP = 210     # «нагреть для замены прутка»
MIN_EXTRUDE_TEMP = 170
FILAMENT_FEED = 300          # мм/мин при загрузке/выгрузке
MOVE_FEED_XY = 6000
MOVE_FEED_Z = 600
TRAM_INSET = 30              # отступ от края для угловых точек ручного выравнивания, мм
TRAM_Z_HOP = 5
PID_DEFAULTS = (210, 60)     # температуры автокалибровки PID сопла/стола по умолчанию
MACHINE_NAME = "Ender-6 Klipper"
WEBSITE = "github.com/Klipper3d"
POLL_PERIOD = 0.5
MOVE_DEBOUNCE = 0.35         # с, пауза после последнего нажатия стрелки перед отправкой движения
CONFIRM_WINDOW = 4.0         # с, окно для повторного нажатия опасных кнопок
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dwin_settings.json")
SCREEN_DEFAULTS = {"volume": 255, "brightness": 100, "standby": False,
                   "standby_brightness": 10, "standby_seconds": 60, "rotated": False}
FULL_REFRESH_PERIOD = 10.0

# ----------------------------------------------------------------------------- адреса (VP)
VP_PAGE = 0x0084
VP_PIC_NOW = 0x0014
VP_SYSCFG = 0x0080
VP_LED = 0x0082
VP_SOUND = 0x00A0

FilenameIcon, FilenameIcon1 = 0x1200, 0x1220
FeedrateDisplay = 0x1006
Stopprint, Pauseprint, Resumeprint = 0x1008, 0x100A, 0x100C
PrintscheduleIcon = 0x100E
Timehour, Timemin = 0x1010, 0x1012
Percentage = 0x1016
FanKeyIcon = 0x101E
ExtrudeMinTemp = 0x1020
HeatPercentIcon = 0x1024
ProbeOffset_Z = 0x1026
NzBdSet = 0x1032
NozzlePreheat, NozzleTemp = 0x1034, 0x1036
BedPreheat, Bedtemp = 0x103A, 0x103C
AutoLevelIcon = 0x1045
DisplayX, DisplayY, DisplayZ = 0x1048, 0x104A, 0x104C
FilamentUnit1, Exchfilement, FilamentUnit2 = 0x1054, 0x1056, 0x1058
MacVersion, SoftVersion, PrinterSize, CorpWebsite = 0x1060, 0x106A, 0x1074, 0x107E
AutolevelVal = 0x1100
VolumeKey, VolumeIcon, SoundIcon = 0x1088, 0x108A, 0x108C
VolumeDisplay, DisplayBrightness, DisplayStandbyBrightness = 0x1140, 0x1142, 0x1144
DisplayStandbyIndicator, DisplayStandbySeconds = 0x1146, 0x1148
HotendPID_AutoTmp, BedPID_AutoTmp = 0x1252, 0x1254
HotendPID_P, HotendPID_I, HotendPID_D = 0x1256, 0x125A, 0x125E
BedPID_P, BedPID_I, BedPID_D = 0x1262, 0x1266, 0x126A
Flowrate = 0x1300
FilesCurentPage, FilesMaxPage = 0x1310, 0x1312
Printfilename = 0x2000
SDFILE_ADDR = 0x200A          # 4 строки по 0x14
StatusMessage = 0x2064
FilenamePlay, FilenameChs, Choosefilename = 0x20D2, 0x20D3, 0x20D4
FilenameNature = 0x6003       # SP цвета текста строки j: 0x6003 + 16*j

# страницы
P_BOOT, P_TOOLS, P_MAIN, P_FILES = 0, 34, 45, 46
P_FINISHED, P_PRINTING, P_PAUSED, P_ADJUST = 51, 53, 54, 56
P_TEMP_FAN_ON, P_TEMP_FAN_OFF, P_PRESET_FAN_ON, P_PRESET_FAN_OFF = 57, 58, 59, 60
P_SETTINGS, P_LEVEL, P_FILAMENT, P_COLD, P_FIL_HEATING = 63, 64, 65, 66, 68
P_MOVE10, P_INFO, P_TRAM, P_FAULT = 71, 75, 84, 88
P_ESTEPS, P_PID, P_SCREENCFG, P_MOTIONCFG = 91, 92, 94, 95
PRINT_PAGES = {51, 52, 53, 54, 56, 76, 78}
TOOLS_SUBPAGES = {91, 92, 93, 94, 95}

# переходы, которые экран делает сам (из 13TouchFile.bin) — для отслеживания текущей страницы
NEXT_PAGE = {(0x1002, 4): 63, (0x1004, 1): 56, (0x1030, 0): 45, (0x1030, 2): 61, (0x103E, 1): 64,
             (0x103E, 4): 34, (0x103E, 5): 75, (0x103E, 10): 91, (0x103E, 11): 92, (0x1046, 0): 71,
             (0x1046, 1): 72, (0x1046, 2): 73, (0x20D2, 0): 45}


def s16(v):
    return v - 0x10000 if v >= 0x8000 else v


def ascii(text, n):
    return str(text).encode("ascii", "replace")[:n].decode()


# ============================================================================= экран
class Dwin:
    def __init__(self, port, baud=115200):
        self.ser = serial.Serial(port, baud, timeout=0.05)
        self.wlock = threading.Lock()
        self.cache = {}
        self.touches = queue.Queue()
        self.page = None              # текущая страница (по чтению PIC_NOW или по нашим переключениям)
        self._stop = False
        threading.Thread(target=self._reader, daemon=True, name="dwin-rx").start()

    # --- запись
    def _send(self, payload):
        with self.wlock:
            self.ser.write(bytes([0x5A, 0xA5, len(payload)]) + payload)
            self.ser.flush()
            time.sleep(0.003)

    def write_raw(self, vp, data: bytes, force=False):
        if not force and self.cache.get(vp) == data:
            return
        self.cache[vp] = data
        self._send(bytes([0x82]) + struct.pack(">H", vp) + data)

    def word(self, vp, value, force=False):
        self.write_raw(vp, struct.pack(">H", int(round(value)) & 0xFFFF), force)

    def words(self, vp, *values, force=False):
        self.write_raw(vp, b"".join(struct.pack(">H", int(v) & 0xFFFF) for v in values), force)

    def text(self, vp, s, maxlen, force=False):
        data = str(s).encode("ascii", "replace")[:maxlen]
        if len(data) < maxlen:
            data += b"\xff\xff"
        self.write_raw(vp, data, force)

    def set_page(self, page):
        log.debug("page -> %d", page)
        self.page = page
        self._send(bytes([0x82]) + struct.pack(">H", VP_PAGE) + bytes([0x5A, 0x01, 0x00, page]))

    def request_page(self):
        self._send(bytes([0x83]) + struct.pack(">H", VP_PIC_NOW) + b"\x01")

    def beep(self):
        self._send(bytes([0x82]) + struct.pack(">H", VP_SOUND) + bytes([0x05, 0x01, 0x40, 0x00]))

    def invalidate(self):
        self.cache.clear()

    # --- чтение
    def _reader(self):
        buf = bytearray()
        while not self._stop:
            try:
                chunk = self.ser.read(64)
            except serial.SerialException as e:
                log.error("serial: %s", e)
                time.sleep(1)
                continue
            if chunk:
                buf += chunk
            while len(buf) >= 3:
                i = buf.find(b"\x5a\xa5")
                if i < 0:
                    buf.clear()
                    break
                del buf[:i]
                if len(buf) < 3 or len(buf) < 3 + buf[2]:
                    break
                frame = bytes(buf[:3 + buf[2]])
                del buf[:3 + buf[2]]
                self._frame(frame)

    def _frame(self, f):
        if len(f) >= 9 and f[3] == 0x83:
            vp = struct.unpack(">H", f[4:6])[0]
            val = struct.unpack(">H", f[7:9])[0]
            if vp == VP_PIC_NOW:
                if self.page != val:
                    log.debug("screen reports page %d", val)
                self.page = val
                return
            self.touches.put((vp, val))
        else:
            log.debug("rx other %s", f.hex(" "))


# ============================================================================= Moonraker
class Moonraker:
    OBJECTS = {
        "webhooks": ["state", "state_message"],
        "extruder": ["temperature", "target"],
        "heater_bed": ["temperature", "target"],
        "fan": ["speed"],
        "print_stats": ["state", "filename", "print_duration", "message"],
        "display_status": ["progress", "message"],
        "virtual_sdcard": ["progress", "is_active"],
        "toolhead": ["homed_axes", "axis_minimum", "axis_maximum", "position"],
        "gcode_move": ["speed_factor", "extrude_factor", "homing_origin", "gcode_position"],
        "bed_mesh": ["profile_name", "probed_matrix"],
        "configfile": ["save_config_pending"],
        "idle_timeout": ["state"],
    }

    def __init__(self, base):
        self.base = base.rstrip("/")
        self.jobs = queue.Queue()
        self.on_error = lambda msg: None
        self.busy = False
        threading.Thread(target=self._worker, daemon=True, name="moonraker-jobs").start()

    def _req(self, method, path, params=None, body=None, timeout=5):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
        req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}").get("result")

    def get(self, path, params=None):
        return self._req("GET", path, params)

    def query(self):
        q = "&".join(f"{k}=" + ",".join(v) for k, v in self.OBJECTS.items())
        return self._req("GET", "/printer/objects/query?" + q)["status"]

    def info(self):
        return self.get("/printer/info")

    # --- долгие действия выполняются по очереди в отдельном потоке
    def submit(self, method, path, params=None, body=None, label=""):
        self.jobs.put((method, path, params, body, label))

    def gcode(self, script):
        log.info("gcode: %s", script.replace("\n", " | "))
        self.submit("POST", "/printer/gcode/script", body={"script": script}, label=script.split("\n")[0])

    def _worker(self):
        while True:
            method, path, params, body, label = self.jobs.get()
            self.busy = True
            try:
                self._req(method, path, params, body, timeout=3600)
            except urllib.error.HTTPError as e:
                try:
                    msg = json.loads(e.read()).get("error", {}).get("message", str(e))
                except Exception:
                    msg = str(e)
                log.warning("%s -> %s", label or path, msg)
                self.on_error(msg)
            except Exception as e:
                log.warning("%s -> %s", label or path, e)
                self.on_error(str(e))
            finally:
                self.busy = not self.jobs.empty()


# ============================================================================= файлы
class FileBrowser:
    ROWS = 4

    def __init__(self, mr: Moonraker, dw: Dwin):
        self.mr, self.dw = mr, dw
        self.path = ""
        self.entries = []      # (name, is_dir)
        self.offset = 0
        self.selected = None   # полный путь выбранного файла

    def load(self, path=None):
        if path is not None:
            self.path = path
        res = self.mr.get("/server/files/directory", {"path": "gcodes/" + self.path if self.path else "gcodes"})
        dirs = sorted((d["dirname"] for d in res.get("dirs", []) if not d["dirname"].startswith(".")), key=str.lower)
        files = sorted((f for f in res.get("files", []) if f["filename"].lower().endswith((".gcode", ".g", ".gco"))),
                       key=lambda f: -f.get("modified", 0))
        self.entries = ([("..", True)] if self.path else []) + [(d, True) for d in dirs] + \
                       [(f["filename"], False) for f in files]
        self.offset = 0
        self.selected = None
        self.render()

    def render(self):
        dw = self.dw
        for j in range(1, self.ROWS + 1):
            k = self.offset + j - 1
            vp = SDFILE_ADDR + (j - 1) * 0x14
            if k < len(self.entries):
                name, is_dir = self.entries[k]
                label = "Up Directory" if name == ".." else name
                dw.text(vp, ascii(label, 20), 20)
                dw.word(FilenameIcon + j, 4 if is_dir else 0)
                dw.word(FileNameColor(j), 0x041F if is_dir else 0xFFFF)
            else:
                dw.text(vp, "", 20)
                dw.word(FilenameIcon + j, 10)
            dw.word(FilenameIcon1 + j, 10)
        dw.text(Choosefilename, "", 20)
        pages = max(1, -(-len(self.entries) // self.ROWS))
        dw.word(FilesCurentPage, self.offset // self.ROWS + 1)
        dw.word(FilesMaxPage, pages)

    def choose(self, row):
        k = self.offset + row - 1
        if not (1 <= row <= self.ROWS) or k >= len(self.entries):
            return
        name, is_dir = self.entries[k]
        if is_dir:
            self.load(self.path.rsplit("/", 1)[0] if "/" in self.path else "") if name == ".." else \
                self.load(f"{self.path}/{name}" if self.path else name)
            return
        self.render()
        self.selected = f"{self.path}/{name}" if self.path else name
        self.dw.text(Choosefilename, ascii(name, 20), 20)
        self.dw.word(FileNameColor(row), 0x87F0)
        self.dw.word(FilenameIcon1 + row, 6)

    def page_down(self):
        if self.offset + self.ROWS < len(self.entries):
            self.offset += self.ROWS
            self.selected = None
            self.render()

    def page_up(self):
        if self.offset >= self.ROWS:
            self.offset -= self.ROWS
            self.selected = None
            self.render()


def FileNameColor(row):
    return FilenameNature + row * 16


# ============================================================================= логика
class Bridge:
    def __init__(self, dw: Dwin, mr: Moonraker):
        self.dw, self.mr = dw, mr
        self.files = FileBrowser(mr, dw)
        self.st = {}                 # последний статус Moonraker
        self.klippy = None           # ready / startup / shutdown / error / offline
        self.print_state = None
        self.flash_msg, self.flash_until = "", 0.0
        self.filament_len = [10.0, 10.0]
        self.pid_temps = list(PID_DEFAULTS)
        self.fil_heating = False
        self.last_full = 0.0
        self.pending_moves = {}      # ось -> целевая координата (копим нажатия стрелок)
        self.last_axis_touch = 0.0
        self.hold_pos_until = 0.0    # не перезаписывать координаты на экране, пока пользователь жмёт стрелки
        self.confirm_key, self.confirm_until = None, 0.0
        self.scr = dict(SCREEN_DEFAULTS)
        try:
            with open(SETTINGS_FILE) as f:
                self.scr.update(json.load(f))
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("settings: %s", e)
        mr.on_error = lambda m: self.flash(m)

    # ------------------------------------------------------------- helpers
    def flash(self, msg, secs=6):
        self.flash_msg, self.flash_until = ascii(msg, 40), time.time() + secs
        self.dw.text(StatusMessage, self.flash_msg.ljust(40), 40)

    def g(self, obj, key, default=None):
        return (self.st.get(obj) or {}).get(key, default)

    def homed(self, axes="xyz"):
        h = self.g("toolhead", "homed_axes", "") or ""
        return all(a in h for a in axes)

    def printing(self):
        return self.print_state in ("printing", "paused")

    def fan_on(self):
        return (self.g("fan", "speed", 0) or 0) > 0

    def temp_page(self):
        return P_TEMP_FAN_ON if self.fan_on() else P_TEMP_FAN_OFF

    def go(self, page):
        self.dw.set_page(page)

    def confirmed(self, key, what):
        """Опасные действия: второе нажатие в течение CONFIRM_WINDOW."""
        now = time.time()
        if self.confirm_key == key and now < self.confirm_until:
            self.confirm_key = None
            return True
        self.confirm_key, self.confirm_until = key, now + CONFIRM_WINDOW
        self.flash(f"Press again to {what}", CONFIRM_WINDOW)
        return False

    # ------------------------------------------------------------- настройки экрана
    def save_settings(self):
        try:
            tmp = SETTINGS_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.scr, f, indent=1)
            os.replace(tmp, SETTINGS_FILE)
        except Exception as e:
            log.warning("save settings: %s", e)

    def apply_screen(self, save=True):
        """Как SetTouchScreenConfiguration() в Marlin."""
        c, dw = self.scr, self.dw
        c["brightness"] = max(10, min(100, int(c["brightness"])))
        c["standby_brightness"] = max(0, min(100, int(c["standby_brightness"])))
        c["standby_seconds"] = max(10, min(655, int(c["standby_seconds"])))
        c["volume"] = max(0, min(255, int(c["volume"])))
        bits = 0x80 | 0x20 | 0x10 | (0x08 if c["volume"] > 0 else 0) | (0x04 if c["standby"] else 0) | \
               (0x02 if c["rotated"] else 0)
        dw.write_raw(VP_SYSCFG, bytes([0x5A, 0x00, 0xFF, bits]), force=True)
        t = 100 * c["standby_seconds"]
        dw.write_raw(VP_LED, bytes([c["brightness"], c["standby_brightness"], t >> 8, t & 0xFF]), force=True)
        dw.word(VP_SOUND + 1, c["volume"] << 8, force=True)
        self.show_screen_settings(force=True)
        if save:
            self.save_settings()

    def show_screen_settings(self, force=False):
        c, dw = self.scr, self.dw
        v = c["volume"]
        dw.word(VolumeIcon, max(0, (v + 1) // 32 - 1) if v else 0, force)
        dw.word(SoundIcon, 8 if v else 9, force)
        dw.word(VolumeKey, v, force)
        dw.word(VolumeDisplay, round(v * 100 / 255), force)
        dw.word(DisplayBrightness, c["brightness"], force)
        dw.word(DisplayStandbyBrightness, c["standby_brightness"], force)
        dw.word(DisplayStandbySeconds, c["standby_seconds"], force)
        dw.word(DisplayStandbyIndicator, 3 if c["standby"] else 2, force)

    # ------------------------------------------------------------- старт
    def startup(self):
        time.sleep(1.5)                       # экран грузится
        dw = self.dw
        dw.invalidate()
        dw.word(FeedrateDisplay, 100)
        dw.word(NozzlePreheat, 0)
        dw.word(BedPreheat, 0)
        for j in range(1, 5):
            dw.text(SDFILE_ADDR + (j - 1) * 0x14, "", 20)
            dw.word(FilenameIcon + j, 10)
            dw.word(FilenameIcon1 + j, 10)
        dw.text(Printfilename, "", 20)
        dw.text(Choosefilename, "", 20)
        dw.words(PrintscheduleIcon, 0, 0)
        dw.word(HotendPID_AutoTmp, self.pid_temps[0])
        dw.word(BedPID_AutoTmp, self.pid_temps[1])
        dw.word(FilamentUnit1, self.filament_len[0] * 10)
        dw.word(FilamentUnit2, self.filament_len[1] * 10)
        dw.text(MacVersion, MACHINE_NAME, 20)
        dw.text(CorpWebsite, WEBSITE, 20)
        dw.text(StatusMessage, "Connecting to Klipper...", 40)
        self.apply_screen(save=False)
        dw.beep()

    # ------------------------------------------------------------- опрос
    def poll(self):
        try:
            info = self.mr.info()
            klippy = info.get("state", "offline")
            msg = info.get("state_message", "")
            ver = info.get("software_version", "")
        except Exception as e:
            klippy, msg, ver = "offline", f"Moonraker: {e}", ""
        if klippy != "ready":
            self.on_not_ready(klippy, msg)
            return
        if self.klippy != "ready":
            self.on_ready(ver)
        try:
            self.st = self.mr.query()
        except Exception as e:
            log.warning("query: %s", e)
            return
        self.update_screen()

    def on_not_ready(self, klippy, msg):
        if self.klippy != klippy:
            log.warning("klippy %s: %s", klippy, msg)
            self.klippy = klippy
        first = (msg or klippy).strip().splitlines()[0] if (msg or klippy).strip() else klippy
        self.dw.text(MacVersion, ascii(f"{klippy.upper()}: {first}", 40), 40)
        self.dw.text(StatusMessage, ascii("Klipper " + klippy, 40), 40)
        if klippy in ("shutdown", "error") and self.dw.page != P_FAULT:
            self.go(P_FAULT)

    def on_ready(self, ver):
        log.info("klippy ready (%s)", ver)
        self.klippy = "ready"
        self.dw.invalidate()
        self.dw.text(MacVersion, MACHINE_NAME, 20)
        self.dw.text(SoftVersion, ascii(ver.split("-")[0] if ver else "Klipper", 20), 20)
        try:
            st = self.mr.query()
            mx = st.get("toolhead", {}).get("axis_maximum", [0, 0, 0])
            self.dw.text(PrinterSize, f"{mx[0]:.0f} X {mx[1]:.0f} X {mx[2]:.0f}", 20)
            self.st = st
            self.print_state = st.get("print_stats", {}).get("state")
        except Exception:
            pass
        self.go(P_PRINTING if self.print_state == "printing" else
                P_PAUSED if self.print_state == "paused" else P_MAIN)

    def update_screen(self):
        dw, g = self.dw, self.g
        force = time.time() - self.last_full > FULL_REFRESH_PERIOD
        if force:
            hold = {vp: dw.cache.get(vp) for vp in (DisplayX, DisplayY, DisplayZ)}
            dw.invalidate()
            dw.cache.update({k: v for k, v in hold.items() if v is not None})
            self.last_full = time.time()
            if self.klippy == "ready":
                dw.text(MacVersion, MACHINE_NAME, 20)
            self.show_screen_settings()

        dw.word(NozzleTemp, g("extruder", "temperature", 0))
        dw.word(NozzlePreheat, g("extruder", "target", 0))
        dw.word(Bedtemp, g("heater_bed", "temperature", 0))
        dw.word(BedPreheat, g("heater_bed", "target", 0))
        dw.word(FanKeyIcon, (g("fan", "speed", 0) or 0) * 100)
        dw.word(FeedrateDisplay, (g("gcode_move", "speed_factor", 1) or 1) * 100)
        dw.word(Flowrate, (g("gcode_move", "extrude_factor", 1) or 1) * 100)
        ho = g("gcode_move", "homing_origin", [0, 0, 0, 0]) or [0, 0, 0, 0]
        dw.word(ProbeOffset_Z, ho[2] * 100)
        pos = g("gcode_move", "gcode_position", [0, 0, 0, 0]) or [0, 0, 0, 0]
        if not self.pending_moves and time.time() > self.hold_pos_until and not self.mr.busy:
            dw.word(DisplayX, pos[0] * 10)
            dw.word(DisplayY, pos[1] * 10)
            dw.word(DisplayZ, pos[2] * 10)
        dw.word(AutoLevelIcon, 3 if g("bed_mesh", "profile_name") else 2)

        # печать
        ps = g("print_stats", "state", "standby")
        dur = g("print_stats", "print_duration", 0) or 0
        prog = g("display_status", "progress", None)
        if prog is None:
            prog = g("virtual_sdcard", "progress", 0) or 0
        pct = int(prog * 100)
        if ps in ("printing", "paused"):
            dw.word(Timehour, dur // 3600)
            dw.word(Timemin, (dur % 3600) // 60)
            dw.word(Percentage, pct)
            dw.words(PrintscheduleIcon, min(100, pct * 2), max(0, pct * 2 - 100))
            dw.text(Printfilename, ascii((g("print_stats", "filename", "") or "").split("/")[-1], 20), 20)

        # строка статуса
        if time.time() > self.flash_until:
            msg = g("display_status", "message") or ""
            if not msg:
                msg = {"printing": f"Printing {pct}%", "paused": "Paused", "complete": "Print complete",
                       "cancelled": "Print cancelled", "error": "Print error: " + (g("print_stats", "message") or "")
                       }.get(ps, f"{MACHINE_NAME} ready")
            dw.text(StatusMessage, ascii(msg, 40).ljust(40), 40)

        self.on_print_state(ps)

        # страницы, которым нужны данные периодически
        if self.dw.page == P_LEVEL:
            self.send_mesh(force)
        if self.fil_heating:
            t, tg = g("extruder", "temperature", 0), g("extruder", "target", 0)
            dw.word(HeatPercentIcon, min(100, int(t * 100 / tg)) if tg else 0)
            if t >= MIN_EXTRUDE_TEMP:
                self.fil_heating = False
                if self.dw.page == P_FIL_HEATING:
                    self.go(P_FILAMENT)

    def on_print_state(self, ps):
        prev, self.print_state = self.print_state, ps
        if ps == prev:
            return
        log.info("print state %s -> %s", prev, ps)
        page = self.dw.page
        if ps == "printing":
            if page not in (P_PRINTING, P_ADJUST):
                self.go(P_PRINTING)
        elif ps == "paused":
            if page in PRINT_PAGES or page in (None, P_MAIN):
                self.go(P_PAUSED)
        elif ps == "complete":
            if prev in ("printing", "paused"):
                self.dw.words(PrintscheduleIcon, 100, 100)
                self.dw.word(Percentage, 100)
                self.go(P_FINISHED)
                self.dw.beep()
        elif ps in ("cancelled", "error", "standby"):
            if page in PRINT_PAGES:
                self.go(P_MAIN)

    def send_mesh(self, force=False):
        m = self.g("bed_mesh", "probed_matrix") or []
        if not m or not m[0]:
            for k in range(25):
                self.dw.word(AutolevelVal + 2 * k, 0, force)
            return
        ny, nx = len(m), len(m[0])
        pick = lambda n, i: round(i * (n - 1) / 4)
        k = 0
        for row in range(5):                       # снизу вверх, змейкой — как в Marlin
            for col in range(5):
                c = 4 - col if row & 1 else col
                v = m[pick(ny, row)][pick(nx, c)]
                self.dw.word(AutolevelVal + 2 * k, v * 1000, force)
                k += 1

    # ------------------------------------------------------------- касания
    def handle(self, vp, val):
        if vp == 0x0000:              # кнопки, которые только переключают страницу
            log.debug("page-only key %d", val)
            return
        log.info("touch 0x%04X = %d (page %s)", vp, val, self.dw.page)
        nxt = NEXT_PAGE.get((vp, val))
        prev_page = self.dw.page
        if nxt is not None:
            self.dw.page = nxt
        if self.klippy != "ready":
            self.flash("Klipper is not ready")
            return
        h = self.HANDLERS.get(vp)
        if h:
            h(self, val, prev_page)
        elif AutolevelVal <= vp < AutolevelVal + 50:
            self.flash("Mesh editing: use BED_MESH_CALIBRATE")
            self.send_mesh(force=True)
        elif SDFILE_ADDR <= vp < SDFILE_ADDR + 0x60:
            pass
        else:
            log.info("no handler for 0x%04X", vp)

    # главная / печать
    def k_printfile(self, v, _):
        if v == 1:
            try:
                self.files.load("")
            except Exception as e:
                self.flash(f"Files: {e}")
                return
            self.go(P_FILES)
        elif v == 2:                                 # «Finish print»
            self.dw.words(PrintscheduleIcon, 0, 0)
            self.dw.word(Percentage, 0)
            self.dw.word(Timehour, 0)
            self.dw.word(Timemin, 0)
            self.dw.text(Printfilename, "", 20)
            self.go(P_MAIN)
        elif v == 3:
            self.go(self.temp_page())

    def k_adjust(self, v, _):
        if v == 2:
            self.go(P_PAUSED if self.print_state == "paused" else P_PRINTING)
        elif v == 3:
            self.mr.gcode("M107" if self.fan_on() else "M106 S255")

    def k_feedrate(self, v, _):
        self.mr.gcode(f"M220 S{v}")

    def k_stop(self, v, _):
        if v == 240:
            self.go(P_PAUSED if self.print_state == "paused" else P_PRINTING)
        else:
            self.mr.submit("POST", "/printer/print/cancel", label="cancel")
            self.flash("Cancelling print...")
            self.go(P_MAIN)

    def k_pause(self, v, _):
        if v == 0xF1:
            self.mr.submit("POST", "/printer/print/pause", label="pause")
            self.flash("Pausing...")
            self.go(P_PAUSED)

    def k_resume(self, v, _):
        if v in (1, 2):
            self.mr.submit("POST", "/printer/print/resume", label="resume")
            self.flash("Resuming...")
            self.go(P_PRINTING)

    def k_zoffset(self, v, _):
        z = s16(v) / 100
        self.mr.gcode(f"SET_GCODE_OFFSET Z={z:.3f} MOVE={1 if self.homed('z') else 0}")
        self.flash(f"Z offset: {z:.2f}")

    # температуры
    def k_tempcontrol(self, v, _):
        if v == 1:
            self.go(P_PRESET_FAN_ON if self.fan_on() else P_PRESET_FAN_OFF)
        elif v == 3:
            on = self.fan_on()
            self.mr.gcode("M107" if on else "M106 S255")
            self.go(P_TEMP_FAN_OFF if on else P_TEMP_FAN_ON)
        elif v in PREHEATS:
            name, he, bed = PREHEATS[v]
            self.mr.gcode(f"M104 S{he}\nM140 S{bed}")
            self.dw.word(NozzlePreheat, he)
            self.dw.word(BedPreheat, bed)
            self.flash(f"Preheat {name}: {he}/{bed}")
            self.go(self.temp_page())
        elif v == 0xF1:                               # «охладить всё»
            self.mr.gcode("M107\nTURN_OFF_HEATERS")
            self.go(P_TEMP_FAN_OFF)

    def k_nzbd(self, v, _):
        if v == 0:
            self.go(self.temp_page())
        elif v == 1:
            self.mr.gcode("M104 S0")
        elif v == 2:
            self.mr.gcode("M140 S0")

    def k_nozzle(self, v, _):
        self.mr.gcode(f"M104 S{v}")

    def k_bed(self, v, _):
        self.mr.gcode(f"M140 S{v}")

    def k_flow(self, v, _):
        self.mr.gcode(f"M221 S{v}")

    def k_fan(self, v, _):
        self.mr.gcode(f"M106 S{min(255, int(v * 2.55))}")

    # настройки
    def k_setting(self, v, _):
        if v == 1:
            self.send_mesh(force=True)
        elif v == 2:
            self.dw.word(FilamentUnit1, self.filament_len[0] * 10, force=True)
            self.dw.word(FilamentUnit2, self.filament_len[1] * 10, force=True)
            self.go(P_FILAMENT)
        elif v == 3:
            self.go(P_MOVE10)
        elif v == 5:
            self.dw.text(CorpWebsite, WEBSITE, 20, force=True)
        elif v == 6:
            if self.printing():
                self.flash("Not while printing")
            else:
                self.mr.gcode("M84")
                self.flash("Motors off")

    def k_returnback(self, v, prev_page):
        if v == 1:
            if prev_page in TOOLS_SUBPAGES:            # экран сам ушёл на страницу Tools
                self.dw.page = P_TOOLS
            else:
                self.go(P_SETTINGS)
        elif v == 2:
            self.go(P_LEVEL)

    def k_bedlevel(self, v, _):
        mr = self.mr
        home = "" if self.homed() else "G28\n"
        if self.printing() and v not in (2, 3):
            self.flash("Not while printing")
            return
        if v == 1:                                     # домой и Z0 (проверка бумагой)
            mr.gcode(f"{home or 'G28 Z'}\nG90\nG1 Z0 F{MOVE_FEED_Z}".lstrip("\n"))
            self.flash("Homing, Z to 0...")
        elif v in (2, 3):                              # Z ±0.1 (babystep через gcode offset)
            if not self.homed("z"):
                self.flash("Home Z first")
                return
            d = 0.1 if v == 2 else -0.1
            mr.gcode(f"SET_GCODE_OFFSET Z_ADJUST={d:+.2f} MOVE=1")
        elif v == 4:                                   # ручное выравнивание по углам
            if home:
                mr.gcode("G28")
            self.go(P_TRAM)
        elif v == 5:                                   # построить сетку
            mr.gcode(f"{home}BED_MESH_CALIBRATE".lstrip("\n"))
            self.flash("Probing bed mesh...", 60)
        elif 6 <= v <= 10:
            if not self.homed():
                self.flash("Home first")
                return
            mx = self.g("toolhead", "axis_maximum", [235, 235, 0])
            lo, hx, hy = TRAM_INSET, mx[0] - TRAM_INSET, mx[1] - TRAM_INSET
            pts = {6: (mx[0] / 2, mx[1] / 2), 7: (lo, lo), 8: (hx, lo), 9: (hx, hy), 10: (lo, hy)}
            x, y = pts[v]
            mr.gcode(f"SAVE_GCODE_STATE NAME=dwin\nG90\nG1 Z{TRAM_Z_HOP} F{MOVE_FEED_Z}\n"
                     f"G1 X{x:.1f} Y{y:.1f} F{MOVE_FEED_XY}\nG1 Z0 F{MOVE_FEED_Z}\nRESTORE_GCODE_STATE NAME=dwin")
        elif v == 11:                                  # вкл/выкл сетку
            if self.g("bed_mesh", "profile_name"):
                mr.gcode("BED_MESH_CLEAR")
            else:
                mr.gcode("BED_MESH_PROFILE LOAD=default")
        elif v in (17, 18):                            # звук выкл/вкл
            self.scr["volume"] = 0 if v == 17 else 255
            self.apply_screen()
        elif v in (19, 20):                            # яркость мин/макс
            self.scr["brightness"] = 10 if v == 19 else 100
            self.apply_screen()
        elif v == 21:                                  # затемнение в простое
            self.scr["standby"] = not self.scr["standby"]
            self.apply_screen()
        elif v == 22:                                  # поворот 180
            self.scr["rotated"] = not self.scr["rotated"]
            self.apply_screen()
        else:
            self.flash("Not supported in Klipper")

    def k_axis(self, v, _):                            # 0x1046
        if v == 3:
            if self.printing():
                self.flash("Not while printing")
                return
            self.mr.gcode("G28")
            self.flash("Homing...")

    def k_move(self, axis):
        def h(self, v, _):
            # экран уже сам изменил число у себя; забываем наш кэш, чтобы следующий опрос
            # обязательно вернул на экран реальную координату (если движение отклонено или ограничено)
            self.dw.cache.pop({"X": DisplayX, "Y": DisplayY, "Z": DisplayZ}[axis], None)
            if self.printing():
                self.flash("Not while printing")
                return
            if not self.homed(axis.lower()):
                self.flash(f"Home {axis} first")
                return
            i = "XYZ".index(axis)
            mn = self.g("toolhead", "axis_minimum", [0, 0, 0])[i]
            mx = self.g("toolhead", "axis_maximum", [999, 999, 999])[i]
            self.pending_moves[axis] = max(mn, min(mx, s16(v) / 10))
            self.last_axis_touch = time.time()
        return h

    def flush_moves(self):
        """Отправить накопленные перемещения одной командой, когда пользователь перестал жать стрелки."""
        if not self.pending_moves or time.time() - self.last_axis_touch < MOVE_DEBOUNCE:
            return
        moves = self.pending_moves
        self.pending_moves = {}
        lines = ["SAVE_GCODE_STATE NAME=dwin", "G90"]
        xy = " ".join(f"{a}{moves[a]:.1f}" for a in "XY" if a in moves)
        if xy:
            lines.append(f"G1 {xy} F{MOVE_FEED_XY}")
        if "Z" in moves:
            lines.append(f"G1 Z{moves['Z']:.1f} F{MOVE_FEED_Z}")
        lines.append("RESTORE_GCODE_STATE NAME=dwin")
        self.mr.gcode("\n".join(lines))
        self.hold_pos_until = time.time() + 1.5

    # пруток
    def k_filament(self, v, _):
        t = self.g("extruder", "temperature", 0)
        if v in (1, 2, 3, 4):
            if self.printing():
                self.flash("Not while printing")
                return
            if t < MIN_EXTRUDE_TEMP:
                self.dw.word(ExtrudeMinTemp, MIN_EXTRUDE_TEMP)
                self.go(P_COLD)
                return
            length = self.filament_len[0 if v in (1, 2) else 1]
            e = -length if v in (1, 3) else length
            self.mr.gcode(f"SAVE_GCODE_STATE NAME=dwin\nM83\nG1 E{e:.1f} F{FILAMENT_FEED}\nRESTORE_GCODE_STATE NAME=dwin")
        elif v == 5:                                   # «да, нагреть»
            self.mr.gcode(f"M104 S{FILAMENT_HEAT_TEMP}")
            self.dw.word(NozzlePreheat, FILAMENT_HEAT_TEMP)
            self.fil_heating = True
            self.go(P_FIL_HEATING)
        elif v in (6, 0xF1):
            self.fil_heating = False
            self.go(P_FILAMENT)

    def k_fil_len(self, idx):
        def h(self, v, _):
            self.filament_len[idx] = v / 10
        return h

    # Tools / сервис
    def k_tools(self, v, _):                           # 0x105C
        if v in (0, 6):                                # STORE
            if self.printing():
                self.flash("Not while printing")
            elif self.store_needed() and self.confirmed("store", "SAVE_CONFIG (restart)"):
                self.store()
        elif v == 1:
            self.go(P_SCREENCFG)
        elif v in (2, 5) and self.printing():
            self.flash("Not while printing")
        elif v == 2:
            if not self.confirmed("pid_e", f"start hotend PID {self.pid_temps[0]}C"):
                return
            self.mr.gcode(f"PID_CALIBRATE HEATER=extruder TARGET={self.pid_temps[0]}")
            self.flash(f"Hotend PID {self.pid_temps[0]}C started", 30)
        elif v == 5:
            if not self.confirmed("pid_b", f"start bed PID {self.pid_temps[1]}C"):
                return
            self.mr.gcode(f"PID_CALIBRATE HEATER=heater_bed TARGET={self.pid_temps[1]}")
            self.flash(f"Bed PID {self.pid_temps[1]}C started", 30)
        elif v == 4:
            self.mr.gcode("BLTOUCH_DEBUG COMMAND=reset")
        else:
            self.flash("Not supported in Klipper")

    def store_needed(self):
        ho = self.g("gcode_move", "homing_origin", [0, 0, 0, 0]) or [0, 0, 0, 0]
        if abs(ho[2]) > 0.0005 or self.g("configfile", "save_config_pending"):
            return True
        self.flash("Nothing to save")
        return False

    def store(self):
        ho = self.g("gcode_move", "homing_origin", [0, 0, 0, 0]) or [0, 0, 0, 0]
        script = ["Z_OFFSET_APPLY_PROBE"] if abs(ho[2]) > 0.0005 else []
        script.append("SAVE_CONFIG")
        self.mr.gcode("\n".join(script))
        self.flash("Saving config, Klipper restarts...")

    def k_pid_temp(self, idx):
        def h(self, v, _):
            self.pid_temps[idx] = v
        return h

    def k_volume(self, v, _):                          # 0x1088: -/+ шагами по 32
        self.scr["volume"] = min(255, v)
        self.apply_screen()

    def k_screen_value(self, key, conv=lambda v: v):
        def h(self, v, _):
            self.scr[key] = conv(v)
            self.apply_screen()
        return h

    HANDLERS = {}


Bridge.HANDLERS = {
    0x1002: Bridge.k_printfile,
    0x1004: Bridge.k_adjust,
    0x1006: Bridge.k_feedrate,
    Stopprint: Bridge.k_stop,
    Pauseprint: Bridge.k_pause,
    Resumeprint: Bridge.k_resume,
    ProbeOffset_Z: Bridge.k_zoffset,
    FanKeyIcon: Bridge.k_fan,
    0x1030: Bridge.k_tempcontrol,
    NzBdSet: Bridge.k_nzbd,
    NozzlePreheat: Bridge.k_nozzle,
    BedPreheat: Bridge.k_bed,
    0x103E: Bridge.k_setting,
    0x1040: Bridge.k_returnback,
    0x1044: Bridge.k_bedlevel,
    0x1046: Bridge.k_axis,
    DisplayX: Bridge.k_move(None, "X"),
    DisplayY: Bridge.k_move(None, "Y"),
    DisplayZ: Bridge.k_move(None, "Z"),
    FilamentUnit1: Bridge.k_fil_len(None, 0),
    FilamentUnit2: Bridge.k_fil_len(None, 1),
    Exchfilement: Bridge.k_filament,
    0x105C: Bridge.k_tools,
    VolumeKey: Bridge.k_volume,
    VolumeDisplay: Bridge.k_screen_value(None, "volume", lambda v: round(min(v, 100) * 255 / 100)),
    DisplayBrightness: Bridge.k_screen_value(None, "brightness"),
    DisplayStandbyBrightness: Bridge.k_screen_value(None, "standby_brightness"),
    DisplayStandbySeconds: Bridge.k_screen_value(None, "standby_seconds"),
    Flowrate: Bridge.k_flow,
    HotendPID_AutoTmp: Bridge.k_pid_temp(None, 0),
    BedPID_AutoTmp: Bridge.k_pid_temp(None, 1),
    FilenameChs: lambda self, v, _: self.files.choose(v),
    FilenamePlay: lambda self, v, _: self.k_fileplay(v),
}


def _k_fileplay(self, v):
    if v == 1:
        if not self.files.selected:
            self.flash("Select a file first")
            return
        name = self.files.selected
        self.dw.text(Printfilename, ascii(name.split("/")[-1], 20), 20)
        self.mr.submit("POST", "/printer/print/start", params={"filename": name}, label="start " + name)
        self.flash("Starting print...")
        self.go(P_PRINTING)
    elif v == 2:
        self.files.page_down()
    elif v == 3:
        self.files.page_up()
    elif v == 4:
        try:
            self.files.load()
        except Exception as e:
            self.flash(f"Files: {e}")


Bridge.k_fileplay = _k_fileplay


# ============================================================================= main
def main():
    ap = argparse.ArgumentParser(description="DWIN (Ender 6 stock screen) <-> Moonraker bridge")
    ap.add_argument("--port", required=True, help="последовательный порт экрана, лучше /dev/serial/by-id/...")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--moonraker", default="http://127.0.0.1:7125")
    ap.add_argument("--settings", default=SETTINGS_FILE, help="файл настроек экрана (громкость, яркость)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    globals()["SETTINGS_FILE"] = a.settings
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    log.info("dwin_bridge %s, port %s, moonraker %s", VERSION, a.port, a.moonraker)

    dw = Dwin(a.port, a.baud)
    mr = Moonraker(a.moonraker)
    br = Bridge(dw, mr)
    br.startup()

    next_poll = 0.0
    while True:
        try:
            vp, val = dw.touches.get(timeout=0.05)
            try:
                br.handle(vp, val)
            except Exception:
                log.exception("handler 0x%04X", vp)
            continue
        except queue.Empty:
            pass
        br.flush_moves()
        now = time.time()
        if now >= next_poll:
            next_poll = now + POLL_PERIOD
            try:
                dw.request_page()
                br.poll()
            except Exception:
                log.exception("poll")


if __name__ == "__main__":
    main()
