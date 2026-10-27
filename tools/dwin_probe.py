#!/usr/bin/env python3
"""
dwin_probe.py — этап 1: проверка канала хост <-> экран DWIN (Ender 6, DWIN_SET от InsanityAutomation DW7.4.6)

Что делает:
  1. переключает экран на главную страницу (45);
  2. пишет тестовые значения температур и строку статуса;
  3. подаёт короткий сигнал динамиком;
  4. дальше слушает касания и печатает их в понятном виде (VP, значение, что это за кнопка).

Запуск (Klipper можно не останавливать, экран к нему не подключён):
  pip install pyserial          # или: sudo apt install python3-serial
  python3 dwin_probe.py /dev/ttyUSB1
  python3 dwin_probe.py /dev/ttyUSB1 --page 63   # открыть любую страницу
Выход: Ctrl+C
"""
import argparse, struct, sys, time
import serial

# --- адреса из Marlin InsanityAutomation (ia_creality/creality_extui.h), сверены с 13/14*.bin ---
VP_PAGE        = 0x0084   # системный регистр переключения страниц: 5A 01 00 NN
VP_SOUND       = 0x00A0   # системный регистр звука
VP_NOZZLE_SET  = 0x1034
VP_NOZZLE_ACT  = 0x1036
VP_BED_SET     = 0x103A
VP_BED_ACT     = 0x103C
VP_STATUS_TEXT = 0x2064   # строка статуса (40 байт)

KEYS = {  # индекс в Addrbuf -> case в RTS_HandleData (Marlin ia_creality/creality_extui.cpp)
    0x1002: "Printfile: 1=Print(файлы) 2=возврат после печати 3=Temp 4=Settings",
    0x1004: "Adjust (подстройка во время печати)",
    0x1006: "Feedrate (скорость печати)",
    0x1008: "PrintChoice: Stop (240=нет)",
    0x100A: "PrintChoice: Pause",
    0x100C: "PrintChoice: Resume",
    0x1026: "Zoffset (ввод)",
    0x1030: "TempControl: 0..3 навигация, 5=PLA, 6=ABS, 0xF1=подтв.",
    0x1032: "ManualSetTemp / охлаждение",
    0x1034: "ввод цели сопла",
    0x103A: "ввод цели стола",
    0x103E: "Setting: 0=главная 1=уровень стола 2=пруток 3=движение 4=язык 5=инфо 6=моторы off",
    0x1040: "ReturnBack",
    0x1044: "Bedlevel",
    0x1046: "Autohome",
    0x1048: "XYZEaxis",
    0x104A: "XYZEaxis",
    0x104C: "XYZEaxis",
    0x1054: "Filament",
    0x1056: "Filament",
    0x1058: "Filament",
    0x105C: "LanguageChoice / доп. меню",
    0x105E: "No_Filament",
    0x105F: "PwrOffNoF",
    0x1088: "Volume",
    0x20D2: "Filename (выбор файла)",
    0x20D3: "Filename: 0=на главную 2=PgDn 3=PgUp",
}


def frame_write(vp, payload: bytes) -> bytes:
    body = bytes([0x82]) + struct.pack(">H", vp) + payload
    return bytes([0x5A, 0xA5, len(body)]) + body


def write_words(ser, vp, *words):
    ser.write(frame_write(vp, b"".join(struct.pack(">H", w & 0xFFFF) for w in words)))
    ser.flush(); time.sleep(0.02)


def write_text(ser, vp, text, maxlen=40):
    data = text.encode("ascii", "replace")[:maxlen]
    if len(data) < maxlen:
        data += b"\xff\xff"            # конец строки для DGUS
    ser.write(frame_write(vp, data)); ser.flush(); time.sleep(0.02)


def set_page(ser, page):
    write_words(ser, VP_PAGE, 0x5A01, page)


def beep(ser):
    # 0x00A0: [номер звука][кол-во][громкость][0] — как в Marlin StartSoundSet, но короткий клик
    ser.write(frame_write(VP_SOUND, bytes([0x05, 0x01, 0x40, 0x00]))); ser.flush()


def read_frames(ser):
    """Генератор кадров 5A A5 LEN ... от экрана."""
    buf = bytearray()
    while True:
        chunk = ser.read(64)
        if chunk:
            buf += chunk
        while len(buf) >= 3:
            i = buf.find(b"\x5a\xa5")
            if i < 0:
                buf.clear(); break
            if i > 0:
                del buf[:i]
            if len(buf) < 3:
                break
            n = buf[2]
            if len(buf) < 3 + n:
                break
            yield bytes(buf[:3 + n]); del buf[:3 + n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--page", type=int, default=45, help="страница после старта (45 = главная)")
    a = ap.parse_args()

    ser = serial.Serial(a.port, a.baud, timeout=0.05)
    print(f"Открыт {a.port} @ {a.baud}")

    set_page(ser, a.page)
    write_words(ser, VP_NOZZLE_ACT, 25)
    write_words(ser, VP_NOZZLE_SET, 215)
    write_words(ser, VP_BED_ACT, 24)
    write_words(ser, VP_BED_SET, 60)
    write_text(ser, VP_STATUS_TEXT, "Hello from Klipper host")
    beep(ser)
    print(f"Отправлено: страница {a.page}, температуры 25/215 и 24/60, строка статуса, сигнал.")
    print("Нажимайте кнопки на экране (Ctrl+C — выход)...\n")

    try:
        for f in read_frames(ser):
            hexs = f.hex(" ")
            if len(f) >= 7 and f[3] == 0x83:
                vp = struct.unpack(">H", f[4:6])[0]
                cnt = f[6]
                vals = [struct.unpack(">H", f[7 + 2 * k:9 + 2 * k])[0] for k in range(cnt) if 9 + 2 * k <= len(f)]
                name = KEYS.get(vp, "?")
                print(f"TOUCH vp=0x{vp:04X} val={vals}  {name}   [{hexs}]")
            elif f[3:5] == b"\x82\x4f":
                print(f"ACK  [{hexs}]")
            else:
                print(f"RX   [{hexs}]")
    except KeyboardInterrupt:
        pass
    finally:
        ser.close()


if __name__ == "__main__":
    main()
