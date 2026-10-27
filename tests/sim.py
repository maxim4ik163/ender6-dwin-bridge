"""Simulated Moonraker + DWIN screen for dwin_bridge.py tests."""
import json, os, pty, select, struct, subprocess, sys, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {
    "info": {"state": "ready", "state_message": "", "software_version": "v0.13.0-816-g1234"},
    "status": {
        "extruder": {"temperature": 24.6, "target": 0},
        "heater_bed": {"temperature": 23.1, "target": 0},
        "fan": {"speed": 0.0},
        "print_stats": {"state": "standby", "filename": "", "print_duration": 0, "message": ""},
        "display_status": {"progress": 0, "message": None},
        "virtual_sdcard": {"progress": 0, "is_active": False},
        "toolhead": {"homed_axes": "", "axis_minimum": [0, 0, -2], "axis_maximum": [260, 260, 400], "position": [0, 0, 0, 0]},
        "gcode_move": {"speed_factor": 1.0, "extrude_factor": 1.0, "homing_origin": [0, 0, 0, 0], "gcode_position": [12.3, 45.6, 7.8, 0]},
        "bed_mesh": {"profile_name": "default", "probed_matrix": [[round(0.01 * (x - y), 3) for x in range(7)] for y in range(7)]},
        "configfile": {"save_config_pending": False},
        "idle_timeout": {"state": "Idle"},
    },
}
GCODES, CALLS = [], []
FILES = {"gcodes": {"dirs": [{"dirname": "parts"}, {"dirname": ".thumbs"}],
                    "files": [{"filename": f"model_{i}_very_long_name_PLA_0.2mm.gcode", "modified": 1000 + i} for i in range(6)]
                    + [{"filename": "notes.txt", "modified": 1}]},
         "gcodes/parts": {"dirs": [], "files": [{"filename": "bracket.gcode", "modified": 5}]}}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _ok(self, res, code=200):
        b = json.dumps({"result": res} if code == 200 else {"error": {"message": res}}).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(u.query, keep_blank_values=True)
        if u.path == "/printer/info":
            return self._ok(STATE["info"])
        if u.path == "/printer/objects/query":
            return self._ok({"eventtime": time.time(), "status": {k: v for k, v in STATE["status"].items() if k in q}})
        if u.path == "/server/files/directory":
            return self._ok(FILES[q["path"][0]])
        self._ok("not found", 404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path); n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if u.path == "/printer/gcode/script":
            GCODES.append(body["script"])
            if "BAD" in body["script"]:
                return self._ok("Unknown command:\"BAD\"", 400)
            return self._ok("ok")
        CALLS.append(u.path + ("?" + u.query if u.query else ""))
        self._ok("ok")


def start_moonraker(port=17125):
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class Screen:
    """Master side of a pty: decodes host writes, answers PIC_NOW reads, injects touches."""
    def __init__(self):
        self.m, s = pty.openpty()
        import tty; tty.setraw(s)
        self.name = os.ttyname(s)
        self.vars, self.pages, self.page = {}, [], 0
        self.buf = bytearray()
        threading.Thread(target=self._rx, daemon=True).start()

    def _rx(self):
        while True:
            if select.select([self.m], [], [], 0.1)[0]:
                self.buf += os.read(self.m, 4096)
                while len(self.buf) >= 3:
                    i = self.buf.find(b"\x5a\xa5")
                    if i < 0: self.buf.clear(); break
                    del self.buf[:i]
                    if len(self.buf) < 3 + self.buf[2]: break
                    f = bytes(self.buf[:3 + self.buf[2]]); del self.buf[:3 + self.buf[2]]
                    self._frame(f)

    def _frame(self, f):
        cmd, vp = f[3], struct.unpack(">H", f[4:6])[0]
        data = f[6:]
        if cmd == 0x82:
            if vp == 0x0084:
                self.page = data[3]; self.pages.append(self.page)
            # store as words at consecutive addresses (text too)
            for k in range(0, len(data) - 1, 2):
                self.vars[vp + k // 2] = data[k:k + 2]
            self.vars[("raw", vp)] = data
        elif cmd == 0x83 and vp == 0x0014:
            os.write(self.m, bytes([0x5A, 0xA5, 0x06, 0x83, 0x00, 0x14, 0x01, 0x00, self.page]))

    def touch(self, vp, val, goto=None):
        if goto is not None: self.page = goto
        os.write(self.m, bytes([0x5A, 0xA5, 0x06, 0x83]) + struct.pack(">H", vp) + b"\x01" + struct.pack(">H", val & 0xFFFF))

    def word(self, vp):
        b = self.vars.get(vp); return None if b is None else struct.unpack(">h", b)[0]

    def text(self, vp):
        d = self.vars.get(("raw", vp), b"")
        return d.split(b"\xff\xff")[0].decode("ascii", "replace").rstrip()
