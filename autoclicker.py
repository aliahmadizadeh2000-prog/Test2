# -*- coding: utf-8 -*-
"""Auto Clicker engine: image matching, rules, actions, Windows input (Windows only for real input)."""
import sys
import os
import json
import math
import shutil
import subprocess
import base64
import time
import random
import threading
import traceback
import itertools
from pathlib import Path

import numpy as np
import cv2

IS_WIN = sys.platform == "win32"
if IS_WIN:
    import ctypes
    from ctypes import wintypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # real pixel coordinates
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    user32 = ctypes.windll.user32

try:
    from assets import EMBEDDED  # base64 images of the Black Friday profile
except Exception:
    EMBEDDED = {}

APP_TITLE = "اتو کلیکر"
REF_WIDTH = 1280  # width of screenshots the embedded images were cut from
CONFIG_NAME = "autoclicker.json"

SETTINGS = {
    "refresh": 0.0,            # F5 every N seconds (0 = off)
    "refresh_after_hours": 0,  # after N hours of watching, use refresh_after
    "refresh_after": 5.0,
    "threshold": 0.80,         # default image similarity
    "monitor": 1,
    "max_minutes": 0,          # auto stop (0 = never)
    "start_at": "",            # HH:MM scheduled start
    "countdown": 3,            # seconds to switch to the target window
    "tick": 0.02,              # loop period
    "settle": 0.4,             # pause after a rule fires
    "beep": True,
    "beep_seconds": 3,
    "speak": True,
    "halo": True,
    "always_on_top": False,
    "test_mode": False,
    "announce_end": True,
    "sleep_at": "",
    "sleep_hours": 0,
}

MULTS = [1.0, 0.92, 1.08, 0.85, 1.17, 0.78, 1.28, 0.7, 1.4, 0.6, 1.6]  # ~60%..160%
NEAR_MULTS = [1.0, 0.92, 1.08, 0.85, 1.17, 0.78, 1.28]
_cnt = itertools.count(1)
_TR = str.maketrans("۰۱۲۳۴۵۶۷۸۹٫,", "0123456789..", "٬، ")


def new_id(prefix):
    return prefix + format(int(time.time() * 1000), "x")[-7:] + str(next(_cnt))


def num(text, default=0.0):
    """Float from text with Persian digits / decimal separator."""
    try:
        return float(str(text).translate(_TR))
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------ paths, config, files
def base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def img_dir():
    return base_dir() / "images"


def new_rule(name=""):
    return {"id": new_id("r"), "name": name, "on": True, "join": "and", "conds": [], "acts": [],
            "times": 1, "gap": 0.0, "prio": 0, "by": "score", "tie": "top", "area": None,
            "timeout": 0.0, "confirm": 1}


def fix_cfg(cfg):
    """Make any loaded dict a valid config (missing keys get defaults)."""
    if not isinstance(cfg, dict):
        cfg = {}
    st = dict(SETTINGS)
    st.update(cfg.get("settings") or {})
    cfg["settings"] = st
    cfg["v"] = 2
    cfg["images"] = [{**m, "id": str(m["id"]), "name": str(m.get("name") or m["id"]),
                      "file": str(m.get("file") or "")} for m in cfg.get("images", [])
                     if isinstance(m, dict) and m.get("id")]
    rules = []
    for r in cfg.get("rules", []):
        if isinstance(r, dict) and r.get("id"):
            d = new_rule()
            d.update(r)
            d["conds"] = [c for c in d.get("conds") or [] if isinstance(c, dict) and c.get("t")]
            d["acts"] = [a for a in d.get("acts") or [] if isinstance(a, dict) and a.get("t")]
            rules.append(d)
    cfg["rules"] = rules
    return cfg


def load_config():
    try:
        data = json.loads((base_dir() / CONFIG_NAME).read_text(encoding="utf-8"))
    except Exception:
        data = {}
    return fix_cfg(data)


def save_config(cfg):
    try:
        (base_dir() / CONFIG_NAME).write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def write_log(line):
    try:
        with open(base_dir() / "autoclicker.log", "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + line + "\n")
    except Exception:
        pass


def imread(path):
    data = np.fromfile(str(path), dtype=np.uint8)  # works with Persian paths
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def imwrite(path, img):
    ok, buf = cv2.imencode(".png", img)
    if ok:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        buf.tofile(str(path))


def parse_hhmm(text):
    try:
        h, m = str(text).strip().split(":")
        h, m = int(h), int(m)
        return (h, m) if 0 <= h < 24 and 0 <= m < 60 else None
    except Exception:
        return None


def mon_index(sct, idx):
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        return 1
    return idx if 1 <= idx < len(sct.monitors) else 1


class ImageStore:
    """Loads library images (user files, or embedded base64 for built-in ones)."""

    def __init__(self):
        self.raw = {}

    def load(self, meta):
        k = meta["id"]
        if k not in self.raw:
            img = None
            try:
                if meta.get("emb") and meta["emb"] in EMBEDDED:
                    data = np.frombuffer(base64.b64decode(EMBEDDED[meta["emb"]]), dtype=np.uint8)
                    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
                elif (img_dir() / meta["file"]).exists():
                    img = imread(img_dir() / meta["file"])
            except Exception:
                img = None
            self.raw[k] = img
        return self.raw[k]


# ------------------------------------------------------------------ image matching
def best_match(frame, tpl):
    th, tw = tpl.shape[:2]
    if frame.shape[0] < th or frame.shape[1] < tw:
        return 0.0, (0, 0)
    res = cv2.matchTemplate(frame, tpl, cv2.TM_CCOEFF_NORMED)
    res = np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0)
    _, mx, _, loc = cv2.minMaxLoc(res)
    return float(mx), loc


def all_matches(frame, tpl, thr, limit=12):
    """Every place the template is seen with similarity >= thr: [(score, (x, y)), ...] best first."""
    th, tw = tpl.shape[:2]
    if frame.shape[0] < th or frame.shape[1] < tw:
        return []
    res = cv2.matchTemplate(frame, tpl, cv2.TM_CCOEFF_NORMED)
    res = np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0)
    out = []
    for _ in range(limit):
        _, mx, _, loc = cv2.minMaxLoc(res)
        if mx < thr:
            break
        out.append((float(mx), loc))
        res[max(0, loc[1] - th // 2):loc[1] + th // 2 + 1, max(0, loc[0] - tw // 2):loc[0] + tw // 2 + 1] = -1.0
    return out


def scaled(raw, s):
    if abs(s - 1.0) < 0.01:
        return raw
    return cv2.resize(raw, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC if s > 1 else cv2.INTER_AREA)


class Finder:
    """Searches one image at several sizes. The last successful size is tried first and one other size
    is probed per call, so the loop stays fast but every size is covered after a few calls."""

    def __init__(self, raw, base=1.0):
        self.raw, self.base, self.hint = raw, float(base), float(base)
        self.cache = {}
        self.i = 0

    def tpl(self, s):
        k = round(s, 3)
        if k not in self.cache:
            self.cache[k] = scaled(self.raw, s)
        return self.cache[k]

    def probe_scale(self):
        cs = self.base * MULTS[self.i % len(MULTS)]
        self.i += 1
        return cs

    def match(self, frame, thr):
        """Best single match: (score, loc, template used)."""
        t = self.tpl(self.hint)
        sc, loc = best_match(frame, t)
        best = (sc, loc, t)
        if sc >= thr:
            return best
        cs = self.probe_scale()
        if abs(cs - self.hint) > 0.005:
            t2 = self.tpl(cs)
            if min(t2.shape[:2]) >= 8:
                sc2, loc2 = best_match(frame, t2)
                if sc2 > sc:
                    best = (sc2, loc2, t2)
                if sc2 >= thr:
                    self.hint = cs
        return best

    def find_all(self, frame, thr, limit=12):
        """All matches at the current size (or a newly found one): [(score, loc, template), ...]."""
        t = self.tpl(self.hint)
        hits = all_matches(frame, t, thr, limit)
        if not hits:
            cs = self.probe_scale()
            if abs(cs - self.hint) > 0.005:
                t2 = self.tpl(cs)
                if min(t2.shape[:2]) >= 8:
                    h2 = all_matches(frame, t2, thr, limit)
                    if h2:
                        self.hint, t, hits = cs, t2, h2
        return [(sc, loc, t) for sc, loc in hits]


# ------------------------------------------------------------------ input backends
VK = {"enter": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "space": 0x20, "backspace": 0x08,
      "delete": 0x2E, "del": 0x2E, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
      "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "insert": 0x2D,
      "ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B}
for _i in range(1, 13):
    VK["f%d" % _i] = 0x6F + _i
EXT_KEYS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E}
MOUSE_FLAGS = {"left": (0x2, 0x4), "right": (0x8, 0x10), "middle": (0x20, 0x40)}


def parse_keys(spec):
    """'ctrl+shift+s' -> [vk, ...]; raises ValueError for unknown names."""
    out = []
    for p in str(spec).lower().split("+"):
        p = p.strip()
        if p in VK:
            out.append(VK[p])
        elif len(p) == 1 and p.isalnum() and p.isascii():
            out.append(ord(p.upper()))
        else:
            raise ValueError(p)
    if not out:
        raise ValueError(spec)
    return out


class NullIO:
    """Records calls instead of touching the system (tests, non-Windows)."""

    def __init__(self):
        self.calls = []
        self.pos = (1, 1)

    def cursor_pos(self):
        return self.pos

    def click(self, x, y, button="left", double=False):
        self.calls.append(("click", int(x), int(y), button, bool(double)))

    def type_text(self, text):
        self.calls.append(("type", text))

    def key(self, spec):
        parse_keys(spec)
        self.calls.append(("key", spec))

    def scroll(self, amount):
        self.calls.append(("scroll", amount))

    def move(self, x, y):
        self.calls.append(("move", int(x), int(y)))

    def foreground_is_me(self):
        return False


if IS_WIN:
    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class _KI(ctypes.Structure):
        _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_ulong),
                    ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]

    class _MI(ctypes.Structure):
        _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong),
                    ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]

    class _IU(ctypes.Union):
        _fields_ = [("ki", _KI), ("mi", _MI)]

    class _INPUT(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ulong), ("u", _IU)]

    class WinIO(NullIO):
        def cursor_pos(self):
            p = _POINT()
            user32.GetCursorPos(ctypes.byref(p))
            return p.x, p.y

        def click(self, x, y, button="left", double=False):
            user32.SetCursorPos(int(x), int(y))
            time.sleep(0.02)
            dn, up = MOUSE_FLAGS.get(button, (2, 4))
            for _ in range(2 if double else 1):
                user32.mouse_event(dn, 0, 0, 0, 0)
                time.sleep(0.03)
                user32.mouse_event(up, 0, 0, 0, 0)
                time.sleep(0.05 if double else 0)

        def _send(self, vk, scan, flags):
            inp = _INPUT(1, _IU(ki=_KI(vk, scan, flags, 0, 0)))
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))

        def type_text(self, text):
            """Unicode typing (Persian included) one UTF-16 unit at a time."""
            for ch in str(text):
                if ch == "\n":
                    self.key("enter")
                    continue
                data = ch.encode("utf-16-le")
                for i in range(0, len(data), 2):
                    unit = data[i] | (data[i + 1] << 8)
                    self._send(0, unit, 0x4)
                    self._send(0, unit, 0x6)
                time.sleep(0.006)

        def key(self, spec):
            vks = parse_keys(spec)
            for vk in vks:
                user32.keybd_event(vk, 0, 1 if vk in EXT_KEYS else 0, 0)
            time.sleep(0.02)
            for vk in reversed(vks):
                user32.keybd_event(vk, 0, (1 if vk in EXT_KEYS else 0) | 2, 0)

        def scroll(self, amount):
            user32.mouse_event(0x0800, 0, 0, int(round(float(amount) * 120)), 0)

        def move(self, x, y):
            user32.SetCursorPos(int(x), int(y))

        def foreground_is_me(self):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(user32.GetForegroundWindow(), buf, 256)
            return APP_TITLE in buf.value
else:
    WinIO = NullIO


# ------------------------------------------------------------------ sound, speech, brightness, sleep
_beep_gen = [0]
_speaker = [None]
BEEP_SEQS = {
    "hit": [(880, 250), (0, 80), (880, 250), (0, 80), (1175, 500), (0, 300)],
    "done": [(784, 400), (0, 80), (988, 400), (0, 80), (1175, 700), (0, 300)],
    "fail": [(330, 500), (0, 80), (220, 700), (0, 300)],
}
SPEAK_SCRIPT = (
    "Add-Type -AssemblyName System.Speech;"
    "[Console]::InputEncoding = [System.Text.Encoding]::UTF8;"
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
    "try { $s.SelectVoiceByHints('NotSet','NotSet',0,[System.Globalization.CultureInfo]'en-US') } catch {};"
    "$s.Volume = 100;"
    "while ($true) { $l = [Console]::In.ReadLine(); if ($l -eq $null) { break }; [void]$s.Speak($l) }"
)


def prewarm_speech():
    """Starts a hidden PowerShell ahead of time so sentences are spoken without delay."""
    if not IS_WIN:
        return
    p = _speaker[0]
    if p is not None and p.poll() is None:
        return
    try:
        _speaker[0] = subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", SPEAK_SCRIPT],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=0x08000000)
    except Exception:
        _speaker[0] = None


def stop_speech():
    p = _speaker[0]
    _speaker[0] = None
    if p is not None:
        try:
            p.kill()
        except Exception:
            pass


def _speak(text):
    prewarm_speech()
    p = _speaker[0]
    if p is None or p.stdin is None:
        return
    try:
        p.stdin.write((text + "\n").encode("utf-8", "ignore"))
        p.stdin.flush()
    except Exception:
        _speaker[0] = None


def announce(kind, text, st):
    """Optional spoken sentence, then optional beeps (kind: hit / done / fail)."""
    if not IS_WIN:
        return
    import winsound
    speak_on = bool(st.get("speak", True)) and bool(text)
    _beep_gen[0] += 1
    gen = _beep_gen[0]
    seq = BEEP_SEQS.get(kind)

    def _run():
        if speak_on:
            _speak(text)
        if not st.get("beep", True) or not seq:
            return
        if speak_on:  # let the sentence finish before the beeps
            end_wait = time.time() + min(4.0, 0.4 + 0.07 * len(text))
            while time.time() < end_wait and _beep_gen[0] == gen:
                time.sleep(0.05)
        end = time.time() + max(0.3, float(st.get("beep_seconds", 3)))
        while time.time() < end and _beep_gen[0] == gen:
            for f, d in seq:
                if time.time() >= end or _beep_gen[0] != gen:
                    return
                if f:
                    winsound.Beep(f, d)
                else:
                    time.sleep(d / 1000)
    threading.Thread(target=_run, daemon=True).start()


def max_brightness():
    """Screen brightness to 100%: laptop panel via WMI, external monitor via DDC/CI (background thread)."""
    if not IS_WIN:
        return

    def _run():
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command",
                 "(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1,100)"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000, timeout=10)
        except Exception:
            pass
        try:
            dxva2 = ctypes.windll.dxva2

            class _PHYS(ctypes.Structure):
                _fields_ = [("h", wintypes.HANDLE), ("desc", ctypes.c_wchar * 128)]

            dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            dxva2.GetPhysicalMonitorsFromHMONITOR.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(_PHYS)]
            dxva2.GetMonitorBrightness.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
                                                   ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
            dxva2.SetMonitorBrightness.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            dxva2.DestroyPhysicalMonitors.argtypes = [wintypes.DWORD, ctypes.POINTER(_PHYS)]
            enum_proc = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HANDLE, wintypes.HANDLE,
                                           ctypes.c_void_p, wintypes.LPARAM)

            def _cb(hmon, hdc, rect, lp):
                try:
                    n = wintypes.DWORD(0)
                    if dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hmon, ctypes.byref(n)) and n.value:
                        arr = (_PHYS * n.value)()
                        if dxva2.GetPhysicalMonitorsFromHMONITOR(hmon, n, arr):
                            for pm in arr:
                                lo, cur, hi = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
                                if dxva2.GetMonitorBrightness(pm.h, ctypes.byref(lo), ctypes.byref(cur), ctypes.byref(hi)):
                                    dxva2.SetMonitorBrightness(pm.h, hi.value)
                            dxva2.DestroyPhysicalMonitors(n, arr)
                except Exception:
                    pass
                return 1

            cb = enum_proc(_cb)
            user32.EnumDisplayMonitors.argtypes = [ctypes.c_void_p, ctypes.c_void_p, enum_proc, wintypes.LPARAM]
            user32.EnumDisplayMonitors(None, None, cb, 0)
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()


def sleep_computer():
    """Puts the computer to Sleep (not Hibernate)."""
    if not IS_WIN:
        return

    def _run():
        try:
            ctypes.windll.powrprof.SetSuspendState(ctypes.c_ubyte(0), ctypes.c_ubyte(0), ctypes.c_ubyte(0))
        except Exception:
            try:
                subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
                               creationflags=0x08000000, timeout=30)
            except Exception:
                pass
    threading.Thread(target=_run, daemon=True).start()
# ===== END OF PART 01 (lines: 566) =====


# ------------------------------------------------------------------ screen capture and image library
class CaptureMixin:
    def top(self):
        return self.root.grab_current() or self.root

    def ask(self, title, fields, values=None, note="", check=None):
        return Form(self, title, fields, values, note, check).res

    def grab_rects(self, labels):
        """Shows a frozen screenshot full-screen; the user draws one 
rectangle per label.
        Returns ([(x0, y0, x1, y1), ...] in monitor pixels, PIL image) or None if cancelled."""
        import mss
        from PIL import Image, ImageTk
        g = self.root.grab_current()
        if g:
            g.grab_release()
        self.overlay.hide()
        hidden = [w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel) and w.winfo_viewable()]
        for w in hidden:
            w.withdraw()
        self.root.withdraw()
        self.root.update()
        time.sleep(0.5)
        with mss.mss() as s:
            mon = s.monitors[mon_index(s, self.cfg["settings"].get("monitor", 1))]
            shot = s.grab(mon)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        top = tk.Toplevel(self.root)
        top.geometry("+%d+%d" % (mon["left"], mon["top"]))
        top.attributes("-fullscreen", True)
        top.attributes("-topmost", True)
        cv = tk.Canvas(top, 
cursor="crosshair", highlightthickness=0)
        cv.pack(fill="both", expand=True)
        top.update()
        cw, ch = cv.winfo_width(), cv.winfo_height()
        photo = ImageTk.PhotoImage(img.resize((cw, ch)) if (cw, ch) != img.size else img)
        cv.create_image(0, 0, image=photo, anchor="nw")
        cv.photo = photo
        cv.create_rectangle(cw / 2 - 330, 10, cw / 2 + 330, 50, fill="black")
        msg = cv.create_text(cw / 2, 30, fill="white", font=("Tahoma", 13), text=labels[0] + "  (Esc = لغو)")
        st, out = {}, []

        def down(e):
            st["p"] = (e.x, e.y)
            st["r"] = cv.create_rectangle(e.x, e.y, e.x, e.y, outline="#ff2020", width=2)

        def move(e):
            if "r" in st:
                cv.coords(st["r"], st["p"][0], st["p"][1], e.x, e.y)

        def up(e):
            if "p" not in st:
                return
            x0, y0 = st.pop("p")
            kx, ky = img.width / cw, 
img.height / ch
            box = (int(min(x0, e.x) * kx), int(min(y0, e.y) * ky), int(max(x0, e.x) * kx), int(max(y0, e.y) * ky))
            if box[2] - box[0] < 6 or box[3] - box[1] < 6:
                cv.delete(st.pop("r"))
                return
            st.pop("r")
            cv.create_rectangle(x0, y0, e.x, e.y, outline="#20c020", width=2)
            out.append(box)
            if len(out) >= len(labels):
                top.destroy()
            else:
                cv.itemconfigure(msg, text=labels[len(out)] + "  (Esc = لغو)")

        cv.bind("<ButtonPress-1>", down)
        cv.bind("<B1-Motion>", move)
        cv.bind("<ButtonRelease-1>", up)
        top.bind("<Escape>", lambda e: top.destroy())
        top.focus_force()
        top.wait_window()
        self.root.deiconify()
        for w in hidden:
            w.deiconify()
        if g:
            g.grab_set()
        return (out, img) if len(out) == len(labels) else None

    def store_crop(self, name, img, 
rect):
        box = tuple(int(v) for v in rect)
        if box[2] - box[0] < 8 or box[3] - box[1] < 8:
            self.on_log("ناحیه خیلی کوچک بود؛ ذخیره نشد", "warn")
            return None
        arr = cv2.cvtColor(np.asarray(img.crop(box)), cv2.COLOR_RGB2BGR)
        meta = {"id": new_id("i"), "name": name, "file": ""}
        meta["file"] = meta["id"] + ".png"
        imwrite(img_dir() / meta["file"], arr)
        self.snap()
        
self.cfg["images"].append(meta)
        self.save()
        self.refresh_library()
        self.on_log("تصویر «%s» ثبت شد (%d×%d)" % (name, arr.shape[1], arr.shape[0]), "ok")
        return meta

    def capture_image(self, name=None):
        name = name or simpledialog.askstring(APP_TITLE, "یک نام برای این تصویر بنویسید (مثلاً «دکمه خرید»):", parent=self.top())
        if not name or not name.strip():
            return None
        got = self.grab_rects(["دور «%s» 
یک کادر بکشید" % name.strip()])
        return self.store_crop(name.strip(), got[1], got[0][0]) if got else None

    def import_image_file(self, name=None, meta=None):
        path = filedialog.askopenfilename(parent=self.top(), title="انتخاب فایل تصویر",
                                          filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.webp"), ("All files", "*.*")])
        if not path:
            return None
        arr = imread(path)
        if arr is None or 
min(arr.shape[:2]) < 8:
            messagebox.showerror(APP_TITLE, "این فایل تصویر معتبری نیست یا خیلی کوچک است.", parent=self.top())
            return None
        self.snap()
        if meta is None:
            meta = {"id": new_id("i"), "name": name or Path(path).stem, "file": ""}
            meta["file"] = meta["id"] + ".png"
            self.cfg["images"].append(meta)
        meta.pop("emb", None)
        imwrite(img_dir() / meta["file"], arr)
        self.save()
        self.refresh_library()
        self.on_log("تصویر «%s» از فایل وارد شد (%d×%d)" % (meta["name"], arr.shape[1], arr.shape[0]), "ok")
        return meta

    def pick_rel(self, iid):
        """User draws the target rectangle; returns [dx, dy, w, h] relative to the anchor image (raw pixels)."""
        meta = next((m for m in self.cfg["images"] if m["id"] == iid), None)
        raw = ImageStore().load(meta) if meta else None
        if raw is None:
            messagebox.showerror(APP_TITLE, "تصویر لنگر پیدا نشد.", parent=self.top())
            return None
        got = self.grab_rects(["فاصله و اندازه را نسبت به تصویر «%s» تعیین کنید: دور هدف یک کادر بکشید" % meta["name"]])
        if not got:
            return None
        (x0, y0, x1, y1), frame = got[0][0], cv2.cvtColor(np.asarray(got[1]), cv2.COLOR_RGB2BGR)
        base = (frame.shape[1] / REF_WIDTH) if meta.get("emb") else 1.0
        best = (0.0, (0, 0), 1.0)
        for m in MULTS:
            t = scaled(raw, base * m)
            if min(t.shape[:2]) >= 8:
                sc, loc = best_match(frame, t)
                if sc > best[0]:
                    best = (sc, loc, t.shape[1] / raw.shape[1])
        if best[0] < 0.6:
            messagebox.showwarning(APP_TITLE, "تصویر لنگر «%s» روی صفحه پیدا نشد (شباهت %.2f).\nآن را روی صفحه نشان دهید و دوباره "
                                   "امتحان کنید." % (meta["name"], best[0]), parent=self.top())
            return None
        (lx, ly), k = best[1], best[2]
        return [(x0 - lx) / k, (y0 - ly) / k, (x1 - x0) / k, (y1 - y0) / k]

    # --- library tab
    def build_library(self, tab):
        tk.Label(tab, text="هر تصویر یک بار اینجا ثبت می‌شود و در چند قانون قابل استفاده است.", font=SMALL, fg=GRAY,
                 anchor="e", wraplength=470, justify="right").pack(fill="x", padx=12, pady=(8, 2))
        self.lib = tk.Listbox(tab, height=7, font=FONT, activestyle="none", exportselection=False)
        self.lib.pack(fill="x", padx=12, pady=4)
        self.lib.bind("<<ListboxSelect>>", 
lambda e: self.show_preview())
        pv = tk.Frame(tab, height=190, relief="groove", bd=1)
        pv.pack(fill="x", padx=12, pady=4)
        pv.pack_propagate(False)
        self.prev = tk.Label(pv, text="(برای دیدن تصویر، یک مورد را از فهرست انتخاب کنید)", font=FONT)
        self.prev.pack(expand=True)
        bar = tk.Frame(tab)
        bar.pack(fill="x", padx=12, pady=4)
        for txt, fn in [("+ ثبت از روی صفحه", lambda: self.capture_image()), ("از فایل", lambda: self.import_image_file()),
                        ("تعویض تصویر", 
self.lib_replace), ("تغییر نام", self.lib_rename), ("حذف", self.lib_delete)]:
            tk.Button(bar, text=txt, font=SMALL, command=fn).pack(side="right", padx=2)
        self.refresh_library()

    def usage(self, iid):
        n = 0
        for r in self.cfg["rules"]:
            if any(iid in cond_imgs(c) for c in r["conds"]):
                n += 1
        return n

    def lib_sel(self):
        s = self.lib.curselection()
        if not s:
            messagebox.showinfo(APP_TITLE, "اول یک تصویر را از فهرست انتخاب کنید.")
            return None
        return self.cfg["images"][s[0]]

    def refresh_library(self, select=None):
        if not hasattr(self, "lib"):
            return
        keep = self.lib.curselection()
        self.lib.delete(0, "end")
        for i, m in enumerate(self.cfg["images"], 1):
            self.lib.insert("end", "%d.  %s    (در %d قانون)" % (i, m["name"], 
self.usage(m["id"])))
        sel = select if select is not None else (keep[0] if keep else None)
        if sel is not None and 0 <= sel < self.lib.size():
            self.lib.selection_set(sel)
        self.show_preview()

    def show_preview(self):
        self.prev.configure(image="", text="(برای دیدن تصویر، یک مورد را از فهرست انتخاب کنید)")
        self.prev.image = None
        s = self.lib.curselection()
        if not s:
            return
        try:
            from PIL import Image, 
ImageTk
            raw = ImageStore().load(self.cfg["images"][s[0]])
            if raw is None:
                raise ValueError("missing")
            im = Image.fromarray(cv2.cvtColor(raw, cv2.COLOR_BGR2RGB))
            im.thumbnail((420, 180))
            ph = ImageTk.PhotoImage(im)
            self.prev.configure(image=ph, text="")
            self.prev.image = ph
        except Exception:
            self.prev.configure(image="", text="تصویر پیدا نشد؛ آن را تعویض کنید")

    def lib_replace(self):
        m = self.lib_sel()
        if not m:
            return
        ans = messagebox.askyesnocancel(APP_TITLE, "تصویر جدید «%s» از کجا بیاید؟\n\nبله = کادر کشیدن روی صفحه\nخیر = انتخاب فایل" % m["name"])
        if ans is None:
            return
        if ans:
            got = self.grab_rects(["دور «%s» یک کادر بکشید" % m["name"]])
            if not got:
                return
            self.snap()
            arr = cv2.cvtColor(np.asarray(got[1].crop(tuple(int(v) for v in got[0][0]))), cv2.COLOR_RGB2BGR)
            m.pop("emb", None)
            imwrite(img_dir() / m["file"], arr)
            self.save()
            self.refresh_library()
        else:
            self.import_image_file(meta=m)

    def lib_rename(self):
        m = self.lib_sel()
        name = m and simpledialog.askstring(APP_TITLE, 
"نام جدید:", initialvalue=m["name"], parent=self.root)
        if name and name.strip():
            self.snap()
            m["name"] = name.strip()
            self.changed()

    def lib_delete(self):
        m = self.lib_sel()
        if not m:
            return
        n = self.usage(m["id"])
        if not messagebox.askyesno(APP_TITLE, "تصویر «%s» حذف شود؟%s" % (m["name"], "\nدر %d قانون استفاده شده و آن قانون‌ها کار نمی‌کنند." % n if n else "")):
            return
        self.snap()
        self.cfg["images"].remove(m)
        self.changed()


# ------------------------------------------------------------------ rules tab: cards, actions, templates, test
class RulesMixin:
    def build_rules(self, tab):
        bar = tk.Frame(tab)
        bar.pack(fill="x", padx=8, pady=6)
        tk.Button(bar, text="+ قانون جدید", font=FONT, bg="#8e1a9a", fg="white", command=self.new_rule_ui).pack(side="right")
        mb = tk.Menubutton(bar, text="قالب‌های آماده ▾", font=FONT, relief="raised")
        menu = tk.Menu(mb, tearoff=0)
        mb["menu"] = menu
        for lb, fn in [("کلیک روی یک تصویر", self.tpl_click), ("کلیک کنار تصویر (دو کادر)", self.tpl_beside),
                       ("کلیک روی تصویر و تایپ متن", self.tpl_type)] + self.extra_templates():
            menu.add_command(label=lb, command=fn)
        mb.pack(side="right", padx=4)
        tk.Button(bar, text="↶ برگردان", font=FONT, command=self.do_undo).pack(side="right")
        if HAS_DIGITS:
            tk.Button(bar, text="آموزش ارقام", font=FONT, command=lambda: digits.open_trainer(self)).pack(side="right", padx=4)
        self.q_var = tk.StringVar()
        self.q_var.trace_add("write", lambda *a: self.refresh_rules())
        tk.Entry(bar, textvariable=self.q_var, font=FONT, width=9, justify="center").pack(side="left")
        tk.Label(bar, text="جست‌وجو:", font=SMALL).pack(side="left", padx=3)
        wrap = tk.Frame(tab)
        wrap.pack(fill="both", 
expand=True, padx=8)
        self.rc = tk.Canvas(wrap, highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.rc.yview)
        self.rf = tk.Frame(self.rc)
        self.rf.bind("<Configure>", lambda e: self.rc.configure(scrollregion=self.rc.bbox("all")))
        self.rc_win = self.rc.create_window((0, 0), window=self.rf, anchor="nw")
        self.rc.bind("<Configure>", lambda e: self.rc.itemconfigure(self.rc_win, width=e.width))
        self.rc.configure(yscrollcomma
nd=sb.set)
        self.rc.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.root.bind_all("<MouseWheel>", lambda e: self.rc.yview_scroll(int(-e.delta / 120), "units") if self.nb.index("current") == 0 else None)
        self.refresh_rules()

    def extra_templates(self):
        out = [("حراج‌جمعه کامل (پروفایل آماده)", self.tpl_blackfriday),
               ("گشتن صفحه برای آیکون (پیمایش + رفرش)", self.tpl_scan)]
        if HAS_DIGITS:
            out.append(("کلیک و تایپ بر اساس عدد کنار تصویر", lambda: digits.template_number(self)))
        return out

    def tpl_scan(self):
        """Template: scroll the page; stop when the target rule becomes possible (it confirms and clicks);
        at the bottom refresh and scroll again."""
        rules = self.cfg["rules"]
        if not rules:
            messagebox.showinfo(APP_TITLE, "اول قانون هدف (مثلاً حراج‌جمعه) را بسازید: قالب‌ها ← «حراج‌جمعه کامل» یا «کلیک روی 
یک تصویر».")
            return
        first = next((r for r in rules if "حراج" in (r.get("name") or "")), rules[0])
        f = [{"k": "watch", "l": "قانون هدف (با دیدن آن بایست و کلیک کن)", "t": "rule", "req": True},
             {"k": "speed", "l": "سرعت پیمایش (گام چرخ ماوس در ثانیه)", "t": "num", "h": "هر گام حدود ۱۰۰ پیکسل. ۳ = آرام، ۸ = سریع."},
             {"k": "end", "l": "پیمایش تا کجا", "t": "choice", "o": [("bottom", "تا انتهای صفحه"), ("time", "فقط تا حداکثر زمان")]},
             {"k": "max_secs", "l": "حداکثر زمان هر پیمایش (ثانیه)", "t": "num"},
             {"k": "refresh", "l": "در انتهای 
صفحه رفرش کن و دوباره پیمایش کن", "t": "bool"},
             {"k": "wait", "l": "صبر بعد از رفرش (ثانیه)", "t": "num", "h": "زمان بارگذاری صفحه."},
             {"k": "cycles", "l": "تعداد دورها (۰ = تا وقتی هدف پیدا شود)", "t": "num"}]
        d = self.ask("گشتن صفحه برای آیکون", f, {"watch": first["id"], "speed": 3, "end": "bottom", "max_secs": 180, "refresh": True, "wait": 3, "cycles": 0},
                     note="قانون جدید: صفحه با این سرعت پایین می‌رود. اگر قانون هدف دیده شد می‌ایستد، مطمئن می‌شود، کلیک می‌کند و بقیه‌ی مراحل ادامه پیدا می‌کنند.",
                     check=lambda r: None if r["speed"] > 0 else "سرعت باید بیشتر 
از صفر باشد")
        if not d:
            return
        r = new_rule("گشتن صفحه برای «%s»" % rule_name(self.cfg, d["watch"]))
        r["conds"] = [{"t": "rule", "rule": d["watch"], "state": "notdone"}]
        r["acts"] = [{"t": "scan", "speed": d["speed"], "dir": "down", "end": d["end"], "max_secs": d["max_secs"] or 180, "watch": [d["watch"]],
                      "settle": 0.3, "still_secs": 1.0, "center": True, "region": None}]
        if d["refresh"]:
            r["acts"].append({"t": "refresh", "wait": d["wait"]})
        r["times"], r["gap"], r["opt"] = 
int(d["cycles"]), 0.5, True
        self.add_rule(r)

    def tpl_blackfriday(self):
        if not profiles.has_embedded():
            messagebox.showerror(APP_TITLE, "تصاویر آماده‌ی حراج‌جمعه پیدا نشد؛ فایل assets.py باید کنار برنامه باشد.")
            return
        if not messagebox.askyesno(APP_TITLE, "۴ قانون و ۳ تصویر آماده‌ی حراج‌جمعه اضافه می‌شود: دیدن آیکون ← کلیک ← افزودن به سبد ← "
                                   "سبد خرید (هر مرحله اگر دیده نشد رد می‌شود). ادامه؟"):
            return
        images, rules = profiles.black_friday()
        self.snap()
        self.cfg["images"] += images
        self.cfg["rules"] += rules
        self.changed()
        self.on_log("پروفایل آماده‌ی حراج‌جمعه اضافه شد. موقع شروع، صفحه‌ی لیست محصولات را باز کنید تا مقیاس خودکار پیدا شود", "ok")

    def refresh_rules(self):
        for w in self.rf.winfo_children():
            w.destroy()
        q = self.q_var.get().strip()
        shown = 0
        for i, r in 
enumerate(self.cfg["rules"]):
            text = describe_rule(self.cfg, r)
            if q and q not in text and q not in r.get("name", ""):
                continue
            shown += 1
            fr = tk.Frame(self.rf, relief="groove", bd=2, padx=6, pady=4)
            fr.pack(fill="x", padx=2, pady=3)
            top = tk.Frame(fr)
            top.pack(fill="x")
            var = tk.BooleanVar(value=r.get("on", True))
            tk.Checkbutton(top, 
text="روشن", variable=var, font=SMALL, command=lambda r=r, v=var: self.toggle_rule(r, v)).pack(side="right")
            tk.Label(top, text="%d. %s" % (i + 1, r.get("name") or "بدون نام"), font=BOLD, anchor="e",
                     fg="#222" if r.get("on", True) else "#999").pack(side="right", padx=6)
            tk.Label(fr, text=text, font=SMALL, wraplength=430, justify="right", anchor="e",
                     fg="#333" if r.get("on", True) else "#999").pack(fill="x")
            bar = tk.Frame(fr)
            bar.pack(fill="x", pady=(4, 0))
            for txt, fn in [("ویرایش", 
self.edit_rule), ("آزمایش", self.test_rule), ("کپی", self.copy_rule),
                            ("▲", lambda i: self.move_rule(i, -1)), ("▼", lambda i: self.move_rule(i, 1)), ("حذف", self.delete_rule)]:
                tk.Button(bar, text=txt, font=SMALL, command=lambda fn=fn, i=i: fn(i)).pack(side="right", padx=2)
        if not shown:
            tk.Label(self.rf, text="هنوز قانونی نیست. «+ قانون جدید» یا «قالب‌های آماده» را بزنید." if not q else "موردی پیدا نشد.",
                     font=FONT, fg=GRAY, pady=30).pack()

    # --- save / undo
    def save(self):
        save_config(self.cfg)

    def snap(self):
        self.undo.append(json.dumps({"images": self.cfg["images"], "rules": self.cfg["rules"]}, ensure_ascii=False))
        del self.undo[:-50]

    def changed(self):
        self.save()
        self.refresh_rules()
        self.refresh_library()

    def do_undo(self):
        if not self.undo:
            self.on_log("چیزی برای برگرداندن نیست", "warn")
            return
        d = json.loads(self.undo.pop())
        self.cfg["images"], self.cfg["rules"] = d["images"], d["rules"]
        self.changed()
        self.on_log("آخرین تغییر برگردانده شد", "ok")

    # --- rule operations
    def add_rule(self, r):
        self.snap()
        self.cfg["rules"].append(r)
        self.changed()
        self.on_log("قانون «%s» اضافه شد" 
% r["name"], "ok")

    def new_rule_ui(self):
        d = RuleEditor(self, new_rule("قانون %d" % (len(self.cfg["rules"]) + 1)), True).res
        if d:
            self.add_rule(d)

    def edit_rule(self, i):
        d = RuleEditor(self, self.cfg["rules"][i], False).res
        if d:
            self.snap()
            self.cfg["rules"][i] = d
            self.changed()

    def copy_rule(self, i):
        c = copy.deepcopy(self.cfg["rules"][i])
        c["id"], c["name"] = new_id("r"), (c.get("name") or "") + " (کپی)"
        self.snap()
        self.cfg["rules"].insert(i + 1, c)
        self.changed()

    def move_rule(self, i, d):
        L = self.cfg["rules"]
        if 0 <= i + d < len(L):
            self.snap()
            L[i], L[i + d] = L[i + d], L[i]
            self.changed()

    def delete_rule(self, i):
        r = self.cfg["rules"][i]
        if 
messagebox.askyesno(APP_TITLE, "قانون «%s» حذف شود؟" % r.get("name", "")):
            self.snap()
            del self.cfg["rules"][i]
            self.changed()

    def toggle_rule(self, r, var):
        r["on"] = bool(var.get())
        self.save()
        self.refresh_rules()

    # --- single-rule test (no clicks)
    def test_rule(self, i):
        if self.bot:
            messagebox.showinfo(APP_TITLE, "اول اجرای فعلی را متوقف کنید.")
            return
        if not self.read_settings():
            return
        r = copy.deepcopy(self.cfg["rules"][i])
        runner = Runner(self, self.cfg, dry=True)
        self.on_status("آزمایش: ۲ ثانیه دیگر صفحه بررسی می‌شود؛ پنجره‌ی هدف را نشان دهید", "wait")
        self.root.iconify()

        def work():
            time.sleep(2.0)
            try:
                lines, boxes, mon = runner.probe(r)
                self.post(self.on_probe, 
lines, boxes, mon)
            except Exception:
                self.post(self.on_log, "خطا در آزمایش:\n" + traceback.format_exc(), "error")
                self.post(self.root.deiconify)
        threading.Thread(target=work, daemon=True).start()

    def on_probe(self, lines, boxes, mon):
        for ln in lines:
            self.on_log(ln, "ok" if "دیده شد" in ln else "info")
        self.on_status("آزمایش تمام شد", "idle")
        if boxes:
            self.overlay.boxes(boxes, mon, 3.0)
        self.root.after(3200 if boxes else 200, self.root.deiconify)

    # --- ready-made templates
    def tpl_click(self):
        meta = self.capture_image()
        if meta:
            r = new_rule("کلیک روی " + meta["name"])
            r["conds"], r["acts"], r["times"], r["gap"] = [{"t": "seen", "img": meta["id"]}], [{"t": "click"}], 0, 1.0
            self.add_rule(r)

    def tpl_beside(self):
        name = simpledialog.askstring(APP_TITLE, "یک نام برای تصویر لنگر بنویسید (مثلاً «آیکون»):", parent=self.root)
        if not name or not name.strip():
            return
        got = self.grab_rects(["دور تصویر لنگر (مثلاً آیکون) یک کادر بکشید", "حالا دور هدف (جای کلیک) یک کادر بکشید"])
        if not got:
            return
        (a, t), meta = got[0][:2], None
        meta = self.store_crop(name.strip(), got[1], a)
        if meta:
            r = new_rule("کلیک کنار " + 
meta["name"])
            r["conds"], r["times"], r["gap"] = [{"t": "seen", "img": meta["id"]}], 0, 1.0
            r["acts"] = [{"t": "click", "mode": "rect", "rel": [t[0] - a[0], t[1] - a[1], t[2] - t[0], t[3] - t[1]]}]
            self.add_rule(r)

    def tpl_type(self):
        meta = self.capture_image()
        if not meta:
            return
        text = simpledialog.askstring(APP_TITLE, "چه متن یا عددی تایپ شود؟", parent=self.root)
        if text is None:
            return
        r = new_rule("تایپ در " + meta["name"])
        r["conds"], r["acts"] = [{"t": "seen", "img": meta["id"]}], [{"t": "click"}, {"t": "type", "text": text}]
        if messagebox.askyesno(APP_TITLE, "بعد از تایپ، کلید Enter هم زده شود؟"):
            r["acts"].append({"t": "key", "keys": "enter"})
        self.add_rule(r)
# ===== END OF PART 02 (lines: 502) =====


# ------------------------------------------------------------------ computer sleep with countdown
class SleepMixin:
    def build_sleep(self, tab):
        sl = tk.LabelFrame(tab, text=" خواب کامپیوتر ", font=FONT, labelanchor="ne")
        sl.pack(fill="x", padx=10, pady=8)
        tk.Label(sl, text="در ساعت تعیین‌شده یا چند ساعت دیگر، اجرا و همه‌ی کارها متوقف می‌شوند و کامپیوتر به حالت Sleep می‌رود. "
                          "اگر هر دو پر باشند، هرکدام زودتر برسد. ۳۰ ثانیه قبل از خواب یک بوق 
هشدار می‌زند و می‌توانید لغو کنید. "
                          "کار «خواب کامپیوتر» داخل قانون‌ها هم همین شمارش معکوس را دارد.",
                 font=SMALL, justify="right", wraplength=470, anchor="e").pack(fill="x", padx=8, pady=6)
        self.make_rows(sl, [("sleep_at", "رفتن به خواب در ساعت (مثلاً 02:30)", "hhmm"), ("sleep_hours", "یا چند ساعت دیگر (مثلاً 1٫5)", "float")])
        self.sleep_btn = tk.Button(sl, text="فعال‌سازی زمان‌بندی خواب", font=BOLD, command=self.toggle_sleep, pady=6)
        self.sleep_btn.pack(fill="x", 
padx=8, pady=(8, 4))
        self._sleep_btn_style = (self.sleep_btn.cget("bg"), self.sleep_btn.cget("fg"))
        self.sleep_info = tk.Label(sl, text="زمان‌بندی خواب غیرفعال است", font=FONT, anchor="e")
        self.sleep_info.pack(fill="x", padx=8, pady=(0, 4))
        tk.Button(sl, text="همین حالا: توقف همه‌چیز و رفتن به خواب", font=FONT, command=self.sleep_now).pack(fill="x", padx=8, pady=(0, 8))

    def toggle_sleep(self):
        if self.sleep_deadline:
            self.cancel_sleep("زمان‌بندی 
خواب لغو شد")
            return
        if not self.read_settings():
            return
        st, now, cands = self.cfg["settings"], time.time(), []
        hm = parse_hhmm(st["sleep_at"]) if st["sleep_at"] else None
        if hm:
            lt = time.localtime(now)
            t = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hm[0], hm[1], 0, 0, 0, -1))
            if t <= now:
                t = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + 1, hm[0], 
hm[1], 0, 0, 0, -1))
            cands.append(t)
        if st["sleep_hours"] > 0:
            cands.append(now + st["sleep_hours"] * 3600)
        if not cands:
            messagebox.showinfo(APP_TITLE, "یک ساعت (مثلاً 02:30) یا تعداد ساعت وارد کنید.")
            return
        self.start_sleep_timer(min(cands))

    def start_sleep_timer(self, deadline):
        self.sleep_deadline, self._sleep_warned = deadline, False
        self.sleep_btn.configure(text="لغو زمان‌بندی خواب", bg="#c0151a", fg="white")
        self.on_log("زمان‌بندی خواب فعال شد: " + time.strftime("%H:%M:%S", time.localtime(deadline)), "ok")
        self.tick_sleep()

    def request_sleep(self):
        """Called by the 'sleep computer' action: 30 s countdown that the user can cancel."""
        self.start_sleep_timer(time.time() + 30)

    def tick_sleep(self):
        self._sleep_job = None
        if not self.sleep_deadline:
            return
        left = self.sleep_deadline - time.time()
        if left <= 0:
            self.sleep_now(confirm=False)
            return
        h, rem = divmod(int(left), 3600)
        m, sec = divmod(rem, 60)
        at = time.strftime("%H:%M", time.localtime(self.sleep_deadline))
        self.sleep_info.configure(text="خواب ساعت %s  —  %d ساعت و %d دقیقه و %d ثانیه مانده" % (at, h, m, sec))
        if left <= 30:
            if not self._sleep_warned:
                self._sleep_warned = True
                user32.MessageBeep(0x30)
                self.on_log("۳۰ ثانیه تا خواب؛ برای لغو، در تب «خواب» دکمه‌ی لغو را بزنید", "warn")
            self.on_status("رفتن به خواب تا %d ثانیه دیگر" % int(left), "wait")
        self._sleep_job = self.root.after(1000, self.tick_sleep)

    def cancel_sleep(self, msg=None):
        self.sleep_deadline, self._sleep_warned = None, False
        if self._sleep_job:
            self.root.after_cancel(self._sleep_job)
            self._sleep_job = None
        self.sleep_btn.configure(text="فعال‌سازی زمان‌بندی خواب", bg=self._sleep_btn_style[0], fg=self._sleep_btn_style[1])
        self.sleep_info.configure(text="زمان‌بندی خواب غیرفعال است")
        if msg:
            self.on_log(msg, "ok")
            if not self.bot:
                self.on_status("آماده", "idle")

    def sleep_now(self, confirm=True):
        if confirm and not messagebox.askyesno(APP_TITLE, "همه‌ی کارها متوقف شود و کامپیوتر به حالت Sleep برود؟", parent=self.root):
            return
        self.cancel_sleep()
        if self.bot:
            self.bot.stop_ev.set()
        E._beep_gen[0] += 1  # stop any beeps
        self.overlay.hide()
        stop_speech()
        self.on_status("همه‌ی کارها متوقف شد؛ در حال رفتن به خواب", "idle")
        self.on_log("همه‌ی کارها متوقف شد؛ کامپیوتر به حالت Sleep می‌رود", "warn")
        self.root.after(1500, sleep_computer)


# ------------------------------------------------------------------ profiles (a folder per 
profile)
class ProfileMixin:
    def profile_names(self):
        d = base_dir() / "profiles"
        return sorted(x.name for x in d.iterdir() if (x / "profile.json").exists()) if d.exists() else []

    def refresh_profiles(self, select=None):
        names = self.profile_names()
        self.prof_box["values"] = names
        if select in names:
            self.prof_var.set(select)
        elif self.prof_var.get() not in names:
            self.prof_var.set("")

    def save_profile(self):
        if not self.read_settings():
            return
        name = simpledialog.askstring(APP_TITLE, "یک نام برای این پروفایل بنویسید:", initialvalue=self.prof_var.get(), parent=self.root)
        if not name:
            return
        bad = set('/:*?"<>|' + chr(92))
        name = "".join(ch for ch in name if ch not in bad).strip().strip(".")
        if not name:
            messagebox.showerror(APP_TITLE, 
"این نام قابل استفاده نیست.")
            return
        d = base_dir() / "profiles" / name
        if d.exists():
            if not messagebox.askyesno(APP_TITLE, "پروفایل «%s» قبلاً هست. جایگزین شود؟" % name):
                return
            shutil.rmtree(d, ignore_errors=True)
        (d / "images").mkdir(parents=True, exist_ok=True)
        (d / "profile.json").write_text(json.dumps(self.cfg, ensure_ascii=False, indent=1), 
encoding="utf-8")
        n = 0
        for m in self.cfg["images"]:
            src = img_dir() / m["file"]
            if src.exists():
                shutil.copy2(src, d / "images" / m["file"])
                n += 1
        self.refresh_profiles(name)
        self.on_log("پروفایل «%s» ذخیره شد (%d قانون، %d تصویر)" % (name, len(self.cfg["rules"]), n), "ok")

    def export_file(self):
        if not self.read_settings():
            return
        path = filedialog.asksaveasfilename(parent=self.root, 
defaultextension=".json", initialfile="profile.json",
                                            filetypes=[("پروفایل اتو کلیکر", "*.json")])
        if path:
            try:
                r, n = profiles.export_profile(self.cfg, path)
                self.on_log("پروفایل در یک فایل ذخیره شد (%d قانون، %d تصویر): %s" % (r, n, path), "ok")
            except Exception as e:
                messagebox.showerror(APP_TITLE, "ذخیره نشد: %s" % e)

    def import_file(self):
        if self.bot:
            messagebox.showinfo(APP_TITLE, "اول اجرا را متوقف کنید.")
            return
        path = filedialog.askopenfilename(parent=self.root, filetypes=[("پروفایل اتو کلیکر", "*.json"), ("All files", "*.*")])
        if not path or not messagebox.askyesno(APP_TITLE, "قانون‌ها، تصاویر و تنظیمات فعلی با این فایل جایگزین می‌شود. ادامه؟"):
            return
        try:
            new = profiles.import_profile(path)
        except Exception as e:
            messagebox.showerror(APP_TITLE, "این فایل خوانده نشد: %s" % e)
            return
        self.snap()
        self.cfg.clear()
        self.cfg.update(new)
        self.init_defaults()
        self.apply_cfg_to_ui()
        self.changed()
        self.on_log("پروفایل از فایل وارد شد (%d قانون)" % len(new["rules"]), "ok")

    def load_profile(self):
        if self.bot:
            
messagebox.showinfo(APP_TITLE, "اول اجرا را متوقف کنید، بعد پروفایل را بارگذاری کنید.")
            return
        name, d = self.prof_var.get().strip(), None
        if not name:
            messagebox.showinfo(APP_TITLE, "اول یک پروفایل را از فهرست انتخاب کنید.")
            return
        d = base_dir() / "profiles" / name
        try:
            data = json.loads((d / "profile.json").read_text(encoding="utf-8"))
        except Exception:
            
messagebox.showerror(APP_TITLE, "این پروفایل پیدا نشد یا خراب است.")
            self.refresh_profiles()
            return
        if not messagebox.askyesno(APP_TITLE, "قانون‌ها، تصاویر و تنظیمات فعلی با پروفایل «%s» جایگزین می‌شود. ادامه؟" % name):
            return
        self.snap()
        if profiles.is_legacy(data):  # saved by the old Black Friday bot: convert to rules
            try:
                data = profiles.convert_legacy(data, d)
            except ValueError as e:
                
messagebox.showerror(APP_TITLE, str(e))
                return
            self.on_log("این پروفایل قدیمی بود و به قانون‌ها تبدیل شد", "ok")
        for f in (d / "images").glob("*.png"):
            (img_dir()).mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, img_dir() / f.name)
        self.cfg.clear()
        self.cfg.update(fix_cfg(data))
        self.init_defaults()
        self.apply_cfg_to_ui()
        self.changed()
        self.on_log("پروفایل «%s» بارگذاری 
شد" % name, "ok")

    def delete_profile(self):
        name = self.prof_var.get().strip()
        if name and messagebox.askyesno(APP_TITLE, "پروفایل «%s» برای همیشه حذف شود؟" % name):
            shutil.rmtree(base_dir() / "profiles" / name, ignore_errors=True)
            self.refresh_profiles()
            self.on_log("پروفایل «%s» حذف شد" % name, "ok")


# -------------------------------------
----------------------------- main window
SPECS_BASIC = [("monitor", "شماره صفحه‌نمایش (۱ = اصلی)", "int"),
               ("threshold", "حداقل شباهت پیش‌فرض تصویر (۰٫۵ تا ۰٫۹۵)", "float"),
               ("refresh", "رفرش خودکار با F5 هر N ثانیه (۰ = خاموش)", "float"),
               ("max_minutes", "توقف خودکار بعد از (دقیقه، ۰ = نامحدود)", "float"),
               ("start_at", "شروع زمان‌بندی‌شده در ساعت (مثلاً 20:00)", "hhmm"),
               ("beep_seconds", "مدت صدای هشدار (ثانیه)", "float")]
SPECS_ADV = [("countdown", "مهلت رفتن به پنجره‌ی هدف قبل از شروع (ثانیه)", "float"),
             ("tick", "فاصله‌ی بررسی صفحه (ثانیه)", "float"),
             ("settle", "مکث بعد از اجرای هر قانون (ثانیه)", "float"),
             ("refresh_after_hours", "بعد از چند ساعت فاصله‌ی رفرش عوض شود (۰ = خاموش)", "float"),
             ("refresh_after", "فاصله‌ی رفرش جدید (ثانیه)", "float"),
             ("scale", "مقیاس تصاویر آماده‌ی حراج‌جمعه (۰ = خودکار)", "float")]
SPECS_SLEEP = [("sleep_at", "ساعت خواب", "hhmm"), ("sleep_hours", "ساعت تا خواب", "float")]
BOOLS = [("beep", "صدای هشدار"), ("speak", "اعلام صوتی (انگلیسی)"), ("halo", "هاله‌ی قرمز دور صفحه"),
         ("announce_end", "اعلام 
پایان وقتی همه‌ی قانون‌ها تمام شد"), ("always_on_top", "پنجره همیشه روی بقیه")]
HK_KEYS = [("hk_start", "شروع"), ("hk_stop", "توقف"), ("hk_pause", "مکث / ادامه")]
HK_CHOICES = ["F%d" % i for i in range(1, 13)] + ["ندارد"]
HK_DEFAULT = {"hk_start": "F8", "hk_stop": "F8", "hk_pause": "F9"}
LIMITS = {"threshold": (0.5, 0.95), "scale": (0.0, 5.0), "monitor": (1, 16), "tick": (0.005, 1.0), "beep_seconds": (1, 30)}


class App(CaptureMixin, RulesMixin, SleepMixin, 
ProfileMixin):
    def __init__(self):
        if tk is None or not IS_WIN:
            raise SystemExit("این برنامه فقط روی ویندوز اجرا می‌شود.")
        self.cfg = load_config()
        self.migrated = self.migrate_old()
        self.init_defaults()
        self.q, self.bot, self.undo, self.sv, self._keys = queue.Queue(), None, [], {}, {}
        self.sleep_deadline, self._sleep_job, self._sleep_warned = None, None, False
        r = self.root = tk.Tk()
        r.title(APP_TITLE)
        r.geometry("540x790")
        r.minsize(500, 680)
        self.overlay = Overlay(r)
        self.build()
        r.attributes("-topmost", bool(self.cfg["settings"]["always_on_top"]))
        r.after(30, self.pump)
        r.after(40, self.poll_hotkey)
        r.protocol("WM_DELETE_WINDOW", self.close)
        r.bind("<Control-z>", lambda e: None if isinstance(e.widget, (tk.Entry, ttk.Entry, ttk.Combobox)) else self.do_undo())

    def migrate_old(self):
        """First start after the upgrade: 
the old config.json of the Black Friday bot becomes rules."""
        old_p = base_dir() / "config.json"
        if (base_dir() / CONFIG_NAME).exists() or not old_p.exists():
            return None
        try:
            old = json.loads(old_p.read_text(encoding="utf-8"))
            if not profiles.is_legacy(old):
                return None
            self.cfg = profiles.convert_legacy(old, base_dir() / "templates_user")
            save_config(self.cfg)
            return "تنظیمات و مراحل 
قبلی حراج‌جمعه به %d قانون تبدیل شد (config.json دست‌نخورده ماند)" % len(self.cfg["rules"])
        except Exception:
            return None

    def init_defaults(self):
        for k, v in HK_DEFAULT.items():
            self.cfg["settings"].setdefault(k, v)

    def hk(self, key):
        return self.cfg["settings"].get(key) or "ندارد"

    def make_rows(self, parent, specs):
        st = self.cfg["settings"]
        for key, label, kind in specs:
            row = tk.Frame(parent)
            row.pack(fill="x", padx=8, pady=2)
            tk.Label(row, text=label, font=FONT, anchor="e", wraplength=330, justify="right").pack(side="right")
            self.sv[key] = tk.StringVar(value=str(st.get(key, "")))
            tk.Entry(row, textvariable=self.sv[key], font=FONT, width=9, justify="center").pack(side="left")

    def build(self):
        r, st = self.root, self.cfg["settings"]
        self.status = tk.Label(r, text="آماده", font=("Tahoma", 14, "bold"), bg="#eeeeee", fg="#222", pady=8)
        self.status.pack(fill="x", padx=12, pady=(10, 4))
        row = tk.Frame(r)
        row.pack(fill="x", padx=12, pady=4)
        self.btn = tk.Button(row, text="شروع  (%s)" % self.hk("hk_start"), font=("Tahoma", 12, "bold"), bg="#8e1a9a", fg="white",
                             relief="flat", command=self.toggle, pady=6)
        self.btn.pack(side="right", expand=True, fill="x", padx=(6, 0))
        self.sv["test_mode"] = tk.BooleanVar(value=bool(st["test_mode"]))
        tk.Checkbutton(row, text="حالت آزمایشی\n(فقط نشان می‌دهد، کلیک نمی‌زند)", font=SMALL, variable=self.sv["test_mode"],
                       command=self.quick_save, justify="right").pack(side="left")
        self.log = tk.Text(r, height=6, font=("Tahoma", 9), state="disabled", wrap="word")
        self.log.pack(side="bottom", fill="x", padx=12, pady=(4, 10))
        for tag, col in (("hit", "#c0151a"), ("ok", "#17803d"), 
("warn", "#b36b00"), ("error", "#c0151a")):
            self.log.tag_configure(tag, foreground=col)
        tk.Button(r, text="خاموش کردن هاله‌ی قرمز", font=FONT, command=self.overlay.hide).pack(side="bottom", fill="x", padx=12, pady=2)
        self.stats = tk.Label(r, text="قانون‌های انجام‌شده: ۰   رفرش: ۰   زمان: ۰", font=SMALL, anchor="e")
        self.stats.pack(side="bottom", fill="x", padx=12)
        nb = self.nb = ttk.Notebook(r)
        nb.pack(fill="both", expand=True, padx=8, pady=4)
        tabs = [tk.Frame(nb) for _ in range(4)]
        for t, name in zip(tabs, ("  قانون‌ها  ", "  کتابخانه‌ی تصاویر  ", "  تنظیمات  ", "  خواب  ")):
            nb.add(t, text=name)
        self.build_rules(tabs[0])
        self.build_library(tabs[1])
        self.build_settings(tabs[2])
        self.build_sleep(tabs[3])

    def build_settings(self, tab):
        prow = tk.Frame(tab)
        prow.pack(fill="x", padx=10, pady=6)
        tk.Label(prow, text="پروفایل:", font=FONT).pack(side="right")
        self.prof_var = tk.StringVar()
        self.prof_box = ttk.Combobox(prow, 
textvariable=self.prof_var, state="readonly", font=FONT, width=16)
        self.prof_box.pack(side="right", padx=6)
        for txt, fn in (("ذخیره", self.save_profile), ("بارگذاری", self.load_profile), ("حذف", self.delete_profile)):
            tk.Button(prow, text=txt, font=FONT, command=fn).pack(side="left", padx=2)
        frow = tk.Frame(tab)
        frow.pack(fill="x", padx=10)
        tk.Label(frow, text="پروفایل در یک فایل:", font=SMALL).pack(side="right")
        tk.Button(frow, text="ورود از فایل…", font=SMALL, command=self.import_file).pack(side="right", padx=4)
        tk.Button(frow, text="خروجی به فایل…", font=SMALL, command=self.export_file).pack(side="right")
        self.refresh_profiles()
        box = tk.LabelFrame(tab, text=" تنظیمات ", font=FONT, labelanchor="ne")
        box.pack(fill="x", padx=10, pady=4)
        self.make_rows(box, SPECS_BASIC)
        st = self.cfg["settings"]
        for key, label in BOOLS:
            self.sv[key] = tk.BooleanVar(
value=bool(st.get(key)))
            cmd = (lambda: self.root.attributes("-topmost", bool(self.sv["always_on_top"].get()))) if key == "always_on_top" else None
            tk.Checkbutton(box, text=label, variable=self.sv[key], font=FONT, command=cmd).pack(anchor="e", padx=8)
        hkf = tk.LabelFrame(tab, text=" کلیدهای میان‌بر (در همه‌ی برنامه‌ها کار می‌کنند) ", font=SMALL, labelanchor="ne")
        hkf.pack(fill="x", padx=10, pady=4)
        for key, label in HK_KEYS:
            hr = tk.Frame(hkf)
            hr.pack(side="right", padx=8, 
pady=3)
            tk.Label(hr, text=label, font=SMALL).pack(side="right", padx=3)
            self.sv[key] = tk.StringVar(value=self.hk(key))
            ttk.Combobox(hr, textvariable=self.sv[key], values=HK_CHOICES, state="readonly", width=7, font=SMALL).pack(side="right")
        self.adv_on = tk.BooleanVar(value=False)
        tk.Checkbutton(tab, text="گزینه‌های پیشرفته", variable=self.adv_on, font=FONT, command=self.toggle_adv).pack(anchor="e", 
padx=14)
        self.adv_box = tk.LabelFrame(tab, text=" پیشرفته ", font=FONT, labelanchor="ne")
        self.make_rows(self.adv_box, SPECS_ADV)
        tk.Button(self.adv_box, text="تشخیص مقیاس از روی صفحه‌ی فعلی (لیست محصولات)", font=SMALL, command=self.do_calibrate).pack(pady=4)

    def do_calibrate(self):
        if self.bot:
            return
        if "anchor" not in EMBEDDED:
            messagebox.showinfo(APP_TITLE, 
"تصویر راهنمای مقیاس (assets.py) پیدا نشد.")
            return
        if not self.read_settings():
            return
        self.on_status("۳ ثانیه دیگر صفحه‌ی لیست محصولات (با پنل «فیلترها») بررسی می‌شود", "wait")
        self.root.iconify()

        def work():
            time.sleep(3.0)
            try:
                import mss
                with mss.mss() as s:
                    mon = s.monitors[mon_index(s, self.cfg["settings"]["monitor"])]
                    frame = np.ascontiguousarray(np.asarray(s.grab(mon))[:, :, :3])
                anchor = ImageStore().load({"id": "anchor", "emb": "anchor", "file": ""})
                sc, sv = E.calibrate(frame, anchor)[:2]
                self.post(self.on_calibrated, sc, sv)
            except Exception:
                self.post(self.on_log, "خطا در تشخیص مقیاس:\n" + traceback.format_exc(), "error")
                self.post(self.root.deiconify)
        threading.Thread(target=work, daemon=True).start()

    def on_calibrated(self, sc, sv):
        self.root.deiconify()
        self.on_status("آماده", "idle")
        if sc >= 0.55:
            self.sv["scale"].set("%.2f" % sv)
            self.on_log("مقیاس پیدا شد: %.2f (امتیاز %.2f)؛ در تنظیمات پیشرفته ذخیره می‌شود" % (sv, sc), "ok")
        else:
            self.on_log("مقیاس پیدا نشد (امتیاز %.2f). صفحه‌ی لیست محصولات با پنل «فیلترها» باید دیده شود" % sc, "warn")

    def toggle_adv(self):
        if self.adv_on.get():
            self.adv_box.pack(fill="x", 
padx=10, pady=4)
        else:
            self.adv_box.pack_forget()

    def apply_cfg_to_ui(self):
        st = self.cfg["settings"]
        for key, var in self.sv.items():
            v = st.get(key, "")
            try:
                var.set(bool(v) if isinstance(var, tk.BooleanVar) else str(v))
            except (tk.TclError, ValueError):
                pass
        self.root.attributes("-topmost", bool(st["always_on_top"]))
        self.refresh_rules()
        self.refresh_library()

    def quick_save(self):
        self.cfg["settings"]["test_mode"] = bool(self.sv["test_mode"].get())
        self.save()

    def read_settings(self):
        st = self.cfg["settings"]
        for key, label, kind in SPECS_BASIC + SPECS_ADV + SPECS_SLEEP:
            raw = self.sv[key].get().strip()
            if kind == "hhmm":
                if raw and not parse_hhmm(raw):
                    messagebox.showerror(APP_TITLE, 
"«%s» باید به شکل 20:00 باشد (یا خالی بماند)." % label, parent=self.top())
                    return False
                st[key] = raw
                continue
            v = 0.0 if not raw else num(raw, None)
            if v is None or v < 0:
                messagebox.showerror(APP_TITLE, "«%s» باید یک عدد معتبر باشد." % label, parent=self.top())
                return False
            lo, hi = LIMITS.get(key, (0, 1e9))
            v = min(hi, max(lo, v))
            st[key] = int(v) if kind == "int" else v
        for key, _ in BOOLS:
            st[key] = bool(self.sv[key].get())
        for key, _ in HK_KEYS:
            st[key] = self.sv[key].get() or "ندارد"
        st["test_mode"] = bool(self.sv["test_mode"].get())
        save_config(self.cfg)
        return True

    # --- messages from the runner thread
    def post(self, fn, *a):
        self.q.put((fn, a))

    def pump(self):
        try:
            while True:
                fn, a = self.q.get_nowait()
                fn(*a)
        except queue.Empty:
            pass
        self.root.after(30, self.pump)

    def on_log(self, msg, level="info"):
        self.log.configure(state="normal")
        self.log.insert("end", time.strftime("%H:%M:%S  ") + msg + "\n", level)
        self.log.see("end")
        self.log.configure(state="disabled")

    def on_status(self, text, 
kind="idle"):
        colors = {"idle": ("#eeeeee", "#222"), "wait": ("#fff4cf", "#7a5600"), "run": ("#dff3e6", "#17803d"), "hit": ("#fdeaea", "#c0151a")}
        bg, fg = colors.get(kind, colors["idle"])
        self.status.configure(text=text, bg=bg, fg=fg)

    def on_stats(self, refreshes, elapsed, done, total):
        self.stats.configure(text="قانون‌های انجام‌شده: %d از %d   رفرش: %d   زمان: %d ثانیه" % (done, total, refreshes, elapsed))

    def on_halo(self, text):
        if self.cfg["settings"]["halo"]:
            self.overlay.show(text)

    def on_boxes(self, boxes, mon):
        self.overlay.boxes(boxes, mon, 2.0)

    def on_finish(self, kind):
        self.bot = None
        self.btn.configure(text="شروع  (%s)" % self.hk("hk_start"), bg="#8e1a9a")
        halo = self.cfg["settings"]["halo"] and not self.cfg["settings"]["test_mode"]
        if kind == "done":
            self.on_status("همه‌ی کارها انجام شد. بقیه با شماست", "hit")
            if halo:
                self.overlay.show("اجرا تمام شد. ادامه با شماست", steady=True)
        elif kind == "error":
            self.on_status("خطا. جزئیات در گزارش پایین", "hit")
            self.root.deiconify()
        else:
            if not self.sleep_deadline:
                self.on_status("متوقف شد", "idle")
            self.overlay.hide()

    # --- run control
    def toggle(self):
        if self.bot:
            self.bot.stop_ev.set()
            return
        if not self.read_settings():
            return
        st = self.cfg["settings"]
        if not any(r.get("on", True) for r in self.cfg["rules"]):
            messagebox.showinfo(APP_TITLE, "هیچ قانون روشنی نیست. اول از تب «قانون‌ها» یک قانون بسازید یا روشن کنید.")
            return
        self.overlay.hide()
        if st["speak"] and not st["test_mode"]:
            prewarm_speech()
        self.bot = Runner(self, self.cfg)
        self.btn.configure(text="توقف  
(%s)" % (self.hk("hk_stop") if self.hk("hk_stop") != "ندارد" else self.hk("hk_start")), bg="#c0151a")
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.root.iconify()  # so keys reach the target window, not this one
        self.bot.start()

    def poll_hotkey(self):
        """Global hotkeys (work while another window is active). Start and stop on the same key = one toggle key."""
        down = {}
        for key, _ in HK_KEYS:
            name = self.hk(key)
            down[key] = name != "ندارد" and bool(user32.GetAsyncKeyState(0x6F + int(name[1:])) & 0x8000)
        new = {k for k, v in down.items() if v and not self._keys.get(k)}
        if "hk_start" in new and "hk_stop" in new:
            self.toggle()
        elif "hk_start" in new and not self.bot:
            self.toggle()
        elif "hk_stop" in new and self.bot:
            self.toggle()
        if "hk_pause" in new and self.bot:
            self.on_log("مکث" if self.bot.toggle_pause() else "ادامه", "warn")
        self._keys = down
        self.root.after(40, self.poll_hotkey)

    def close(self):
        if self.sleep_deadline and not messagebox.askyesno(
                APP_TITLE, "زمان‌بندی خواب فعال است و با بستن برنامه لغو می‌شود. برنامه بسته شود؟", parent=self.root):
            return
        if self.bot:
            self.bot.stop_ev.set()
        self.overlay.hide()
        stop_speech()
        self.root.destroy()

    def run(self):
        if self.migrated:
            self.on_log(self.migrated, "ok")
        self.on_log("آماده. از تب «قانون‌ها» یک قانون بسازید و شروع را بزنید. برای امتحان بدون کلیک، «حالت آزمایشی» را روشن کنید.")
        self.root.mainloop()


def main():
    App().run()


if __name__ == "__main__":
    main()
# ===== END OF FILE (total lines: 1638) =====
