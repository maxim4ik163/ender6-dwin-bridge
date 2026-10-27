"""End-to-end test: runs dwin_bridge.py against a simulated screen (pty) and a fake Moonraker.
Usage: python3 tests/run_test.py   (needs pyserial)
"""
import os, subprocess, sys, tempfile, time
sys.path.insert(0, os.path.dirname(__file__))
import sim

ok = True
def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    ok &= bool(cond)

sim.start_moonraker()
scr = sim.Screen()
HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE = os.path.join(HERE, "..", "dwin_bridge.py")
TMP = tempfile.mkdtemp(prefix="dwin_test_")
SETTINGS = os.path.join(TMP, "dwin_settings.json")
LOG = os.path.join(TMP, "bridge.log")
p = subprocess.Popen([sys.executable, "-u", BRIDGE, "--port", scr.name, "--moonraker", "http://127.0.0.1:17125",
                      "--settings", SETTINGS, "-v"], env=dict(os.environ), stdout=open(LOG, "w"), stderr=subprocess.STDOUT)
W = lambda t=0.8: time.sleep(t)
try:
    W(3.0)
    check("start: main page", scr.page == 45, scr.pages)
    check("temps", scr.word(0x1036) == 25 and scr.word(0x103C) == 23)
    check("position x10", (scr.word(0x1048), scr.word(0x104A), scr.word(0x104C)) == (123, 456, 78))
    check("version text", scr.text(0x106A) == "v0.13.0", scr.text(0x106A))
    check("printer size", scr.text(0x1074) == "260 X 260 X 400", scr.text(0x1074))
    check("status ready", "ready" in scr.text(0x2064), scr.text(0x2064))

    # --- temperatures
    scr.touch(0x1034, 215); W()
    scr.touch(0x1002, 3); W()
    check("temp page", scr.page == 58, scr.page)
    scr.touch(0x1030, 1); W()
    check("preset page (fan off)", scr.page == 60)
    scr.touch(0x1030, 6, goto=57); W()
    scr.touch(0x1030, 0xF1); W()
    check("gcodes temps", sim.GCODES[:3] == ["M104 S215", "M104 S240\nM140 S80", "M107\nTURN_OFF_HEATERS"], sim.GCODES[:3])

    # --- files
    scr.touch(0x1002, 1); W()
    check("files page", scr.page == 46)
    check("row1 is dir", scr.text(0x200A) == "parts" and scr.word(0x1201) == 4, scr.text(0x200A))
    check("row2 newest file", scr.text(0x201E).startswith("model_5_very_long_na"), scr.text(0x201E))
    check("pages 1/2", (scr.word(0x1310), scr.word(0x1312)) == (1, 2), (scr.word(0x1310), scr.word(0x1312)))
    scr.touch(0x20D2, 2); W()
    check("page down", scr.word(0x1310) == 2 and scr.text(0x200A).startswith("model_2"), scr.text(0x200A))
    scr.touch(0x20D3, 3); W()
    check("selected file shown", scr.text(0x20D4).startswith("model_0"), scr.text(0x20D4))
    check("selected color", scr.word(0x6003 + 3 * 16) == 0x87F0 - 0x10000)
    scr.touch(0x20D2, 1); W()
    check("print start", sim.CALLS[-1].startswith("/printer/print/start?filename=model_0"), sim.CALLS[-1])
    check("printing page", scr.page == 53)

    # --- simulate printing state
    sim.STATE["status"]["print_stats"].update(state="printing", filename="model_0_very_long_name_PLA_0.2mm.gcode", print_duration=3725)
    sim.STATE["status"]["display_status"]["progress"] = 0.63
    W(1.2)
    check("progress", scr.word(0x1016) == 63 and (scr.word(0x100E), scr.word(0x100F)) == (100, 26), (scr.word(0x100E), scr.word(0x100F)))
    check("time 1:02", (scr.word(0x1010), scr.word(0x1012)) == (1, 2))
    scr.touch(0x100A, 0xF1); W()
    check("pause", sim.CALLS[-1] == "/printer/print/pause" and scr.page == 54)
    sim.STATE["status"]["print_stats"]["state"] = "paused"; W()
    scr.touch(0x100C, 1); W()
    check("resume", sim.CALLS[-1] == "/printer/print/resume" and scr.page == 53)
    sim.STATE["status"]["print_stats"]["state"] = "printing"; W()
    scr.touch(0x1026, (-15) & 0xFFFF); W()
    check("babystep", sim.GCODES[-1] == "SET_GCODE_OFFSET Z=-0.150 MOVE=0", sim.GCODES[-1])
    scr.touch(0x1008, 240); W()
    check("stop: no", scr.page == 53)
    sim.STATE["status"]["print_stats"]["state"] = "complete"; W(1.2)
    check("finished page", scr.page == 51, scr.pages[-3:])
    scr.touch(0x1002, 2, goto=1); W()
    check("finish -> main", scr.page == 45)
    sim.STATE["status"]["print_stats"]["state"] = "standby"

    # --- leveling page & back
    scr.touch(0x1002, 4, goto=63); W()
    scr.touch(0x103E, 1, goto=64); W(1.2)
    check("mesh sent zigzag", scr.word(0x1100) == 0 and scr.word(0x1108) == 60 and scr.word(0x110A) == 40, (scr.word(0x1100), scr.word(0x1108), scr.word(0x110A)))
    scr.touch(0x1048, 2500); W(0.3); scr.vars[0x1048] = (2500).to_bytes(2, "big"); W(1.0)
    check("rejected move restores display", scr.word(0x1048) == 123 and "Home X" in scr.text(0x2064), (scr.word(0x1048), scr.text(0x2064)))
    scr.touch(0x1044, 2); W()
    check("z adjust needs home", "Home Z" in scr.text(0x2064), scr.text(0x2064))
    sim.STATE["status"]["toolhead"]["homed_axes"] = "xyz"; W()
    scr.touch(0x1044, 3); W()
    check("z adjust", sim.GCODES[-1] == "SET_GCODE_OFFSET Z_ADJUST=-0.10 MOVE=1", sim.GCODES[-1])
    scr.touch(0x1040, 1); W()
    check("back -> settings", scr.page == 63)
    sim.STATE["status"]["gcode_move"]["homing_origin"] = [0, 0, -0.1, 0]; W()
    n = len(sim.GCODES); scr.touch(0x105C, 6); W()
    check("store needs confirm", len(sim.GCODES) == n and "Press again" in scr.text(0x2064), scr.text(0x2064))
    scr.touch(0x105C, 6); W()
    check("store", sim.GCODES[-1] == "Z_OFFSET_APPLY_PROBE\nSAVE_CONFIG", sim.GCODES[-1])

    # --- move & filament
    scr.touch(0x103E, 3); W()
    check("move page", scr.page == 71)
    scr.touch(0x1048, 1005); W()
    check("move x", "G1 X100.5 F6000" in sim.GCODES[-1], sim.GCODES[-1])
    n = len(sim.GCODES)
    for z in range(199, 189, -1):
        scr.touch(0x104C, z); time.sleep(0.12)
    W(1.0)
    check("rapid z -> one move", len(sim.GCODES) == n + 1 and "G1 Z19.0 F600" in sim.GCODES[-1], sim.GCODES[n:])
    scr.touch(0x104C, 9999); W()
    check("move z clamp", "G1 Z400.0" in sim.GCODES[-1], sim.GCODES[-1])
    scr.touch(0x1040, 1); W()
    scr.touch(0x103E, 2); W()
    check("filament page", scr.page == 65)
    scr.touch(0x1056, 2); W()
    check("cold warning", scr.page == 66 and scr.word(0x1020) == 170)
    scr.touch(0x1056, 5); W()
    check("heating page", scr.page == 68 and sim.GCODES[-1] == "M104 S210")
    sim.STATE["status"]["extruder"].update(temperature=180, target=210); W(1.2)
    check("heated -> filament", scr.page == 65)
    scr.touch(0x1054, 500); W(0.3); scr.touch(0x1056, 1); W()
    check("unload 50mm", "G1 E-50.0 F300" in sim.GCODES[-1], sim.GCODES[-1])

    # --- error from klipper shown
    scr.touch(0x105C, 2); W(0.3); scr.touch(0x105C, 2); W()
    check("pid", sim.GCODES[-1] == "PID_CALIBRATE HEATER=extruder TARGET=210")

    # --- screen settings
    scr.touch(0x1088, 96); W()
    check("volume", scr.vars.get(("raw", 0x00A1)) == bytes([96, 0]) and scr.word(0x108A) == 2, (scr.vars.get(("raw", 0x00A1)), scr.word(0x108A)))
    scr.touch(0x1044, 19); W()
    check("brightness", scr.vars.get(("raw", 0x0082))[:2] == bytes([10, 10]), scr.vars.get(("raw", 0x0082)))
    import json as _j
    check("settings saved", _j.load(open(SETTINGS))["volume"] == 96)
    scr.touch(0x0000, 0); W(0.3)

    # --- shutdown / recovery
    sim.STATE["info"].update(state="shutdown", state_message="MCU 'mcu' shutdown: Timer too close\nmore")
    W(1.5)
    check("fault page", scr.page == 88 and "SHUTDOWN: MCU" in scr.text(0x1060), scr.text(0x1060))
    sim.STATE["info"].update(state="ready", state_message="")
    W(1.5)
    check("recovered", scr.page == 45)
finally:
    p.terminate(); p.wait()
print("ALL OK" if ok else "SOME FAILED", "| bridge log:", LOG)
sys.exit(0 if ok else 1)
