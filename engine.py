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


# ------------------------------------------------------------------ rule model and Persian descriptions
COND_KINDS = [("seen", "تصویر دیده شد"), ("unseen", "تصویر دیده نشد"), ("rule", "قانون دیگر انجام شد / نشد"),
              ("after", "N ثانیه گذشته"), ("clock", "ساعت مشخص رسید")]
ACT_KINDS = [("click", "کلیک"), ("type", "تایپ متن یا عدد"), ("key", "کلید یا کلید ترکیبی"),
             ("scroll", "اسکرول"), ("wait", "صبر"), ("sound", "بوق یا جمله‌ی صوتی"),
             ("shot", "اسکرین‌شات"), ("halo", "هاله‌ی قرمز"), ("bright", "روشنایی ۱۰۰٪"),
             ("scan", "پیمایش صفحه (اسکرول خودکار با سرعت دلخواه)"), ("refresh", "رفرش صفحه (F5)"),
             ("run", "اجرای قانون دیگر"), ("sleep", "خواب کامپیوتر"), ("stop", "توقف")]
BY_LABELS = [("score", "بالاترین شباهت"), ("top", "بالاترین موقعیت"), ("left", "چپ‌ترین"),
             ("bottom", "پایین‌ترین"), ("right", "راست‌ترین")]
KEYS = {"score": lambda m: -m["sc"], "top": lambda m: m["y"], "left": lambda m: m["x"],
        "bottom": lambda m: -m["y"], "right": lambda m: -m["x"],
        "min": lambda m: m.get("num", 1e18), "max": lambda m: -m.get("num", -1e18),
        "list": lambda m: m.get("rank", 1e9)}


def pick(cands, by, tie="score"):
    """Chooses one candidate: by `by`, ties broken by `tie`, then by similarity."""
    kb, kt = KEYS.get(by, KEYS["score"]), KEYS.get(tie, KEYS["score"])
    return min(cands, key=lambda m: (kb(m), kt(m), -m["sc"]))


def img_name(cfg, iid):
    for m in cfg["images"]:
        if m["id"] == iid:
            return m["name"]
    return "؟"


def rule_name(cfg, rid):
    for r in cfg["rules"]:
        if r["id"] == rid:
            return r.get("name") or "بدون نام"
    return "؟"


def near_list(c):
    """The 'next to' requirements of a condition as a list of dicts: {img | imgs, radius, want}."""
    n = c.get("near")
    return [x for x in (n if isinstance(n, list) else [n]) if isinstance(x, dict) and (x.get("img") or x.get("imgs"))]


def near_ids(n):
    return list(n.get("imgs") or ([n["img"]] if n.get("img") else []))


def seen_ids(c):
    """Images of a 'seen' / 'unseen' condition: one image, or several (any of them is enough)."""
    return [i for i in (c.get("imgs") or [c.get("img")]) if i]


def cond_imgs(c):
    """Library image ids a condition uses (its own images and the neighbours)."""
    ids = list(seen_ids(c))
    for n in near_list(c):
        ids += near_ids(n)
    return ids


def calibrate(frame, anchor):
    """Guesses the page scale by finding the 'filters' panel image: (score, scale, location, shape)."""
    g_frame, g_anchor = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), cv2.cvtColor(anchor, cv2.COLOR_BGR2GRAY)

    def score(s):
        t = cv2.resize(g_anchor, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC if s > 1 else cv2.INTER_AREA)
        sc, loc = best_match(g_frame, t)
        return sc, loc, t.shape

    best = (0.0, 1.0, (0, 0), (0, 0))
    for s in np.arange(0.5, 2.51, 0.05):
        sc, loc, shp = score(float(s))
        if sc > best[0]:
            best = (sc, float(s), loc, shp)
    for s in np.arange(best[1] - 0.05, best[1] + 0.0501, 0.01):
        if s > 0.3:
            sc, loc, shp = score(float(s))
            if sc > best[0]:
                best = (sc, float(s), loc, shp)
    return best


def describe_cond(cfg, c):
    t = c.get("t")
    if t == "seen":
        s = "«%s» دیده شود" % "» یا «".join(img_name(cfg, i) for i in seen_ids(c))
        for n in near_list(c):
            names = "، ".join("«%s»" % img_name(cfg, i) for i in near_ids(n))
            s += " %s %s" % ("کنار" if n.get("want", True) else "نه کنار", ("یکی از " + names) if len(near_ids(n)) > 1 else names)
        if c.get("num"):
            s += " و عدد کنارش در فهرست/بازه‌ی «%s» باشد" % c["num"].get("list", "")
        return s
    if t == "unseen":
        return "«%s» دیده نشود" % "» و «".join(img_name(cfg, i) for i in seen_ids(c))
    if t == "rule":
        st = {"done": "انجام شده باشد", "notdone": "انجام نشده باشد", "finished": "تمام شده (انجام یا رد) باشد"}
        return "قانون «%s» %s" % (rule_name(cfg, c.get("rule")), st.get(c.get("state", "done"), ""))
    if t == "after":
        f = "شروع" if c.get("from", "start") == "start" else "قانون «%s»" % rule_name(cfg, c.get("from"))
        return "%g ثانیه از %s گذشته باشد" % (num(c.get("secs", 0)), f)
    if t == "clock":
        return "ساعت %s رسیده باشد" % c.get("at", "")
    return "؟"


def describe_act(cfg, a):
    t = a.get("t")
    if t == "click":
        m = a.get("mode", "match")
        w = {"left": "کلیک", "right": "کلیک راست", "middle": "کلیک وسط"}.get(a.get("button", "left"), "کلیک")
        if a.get("double"):
            w = "دابل‌" + w
        if m == "rect":
            return "%s داخل کادر کنار تصویر" % w
        if m == "xy":
            return "%s روی نقطه‌ی (%d، %d)" % (w, a.get("xy", [0, 0])[0], a.get("xy", [0, 0])[1])
        return "%s روی وسط تصویر" % w
    if t == "type":
        return "تایپ «%s»" % a.get("text", "")
    if t == "key":
        return "کلید %s" % a.get("keys", "")
    if t == "scroll":
        return "اسکرول %g" % num(a.get("amount", 0))
    if t == "wait":
        return "صبر %g ثانیه" % num(a.get("secs", 0))
    if t == "sound":
        return "صدا" + (" و جمله‌ی «%s»" % a["text"] if a.get("text") else "")
    if t == "run":
        return "اجرای «%s»" % rule_name(cfg, a.get("rule"))
    if t == "scan":
        w = [rule_name(cfg, i) for i in a.get("watch") or []]
        return "پیمایش %s با سرعت %g گام بر ثانیه%s%s" % (
            "به بالا" if a.get("dir") == "up" else "به پایین", num(a.get("speed", 3)),
            " تا انتهای صفحه" if a.get("end", "bottom") == "bottom" else "",
            ("؛ با دیدن «%s» می‌ایستد و اجرا می‌کند" % "، ".join(w)) if w else "")
    if t == "refresh":
        return "رفرش صفحه (F5) و صبر %g ثانیه" % num(a.get("wait", 2))
    return dict(ACT_KINDS).get(t, "؟")


def describe_rule(cfg, r):
    j = " و " if r.get("join", "and") == "and" else " یا "
    head = "اگر " + j.join(describe_cond(cfg, c) for c in r.get("conds", [])) if r.get("conds") else "بلافاصله پس از شروع"
    s = head + "، آن‌گاه: " + ("؛ ".join(describe_act(cfg, a) for a in r.get("acts", [])) or "هیچ کاری")
    ex = []
    t = int(num(r.get("times", 1)))
    ex.append("نامحدود" if t <= 0 else "%d بار" % t)
    if r.get("area"):
        ex.append("فقط در ناحیه‌ی انتخابی")
    if num(r.get("timeout", 0)) > 0:
        ex.append("مهلت %g ثانیه" % num(r["timeout"]))
    return s + "  [" + "، ".join(ex) + "]"


# ------------------------------------------------------------------ the runner
def new_state():
    return {"count": 0, "last": -1e9, "t_last": None, "streak": 0, "armed": None, "skipped": False}


class Runner(threading.Thread):
    """Runs the rules in a loop: grab screen -> evaluate rules in priority order -> run actions of the first
    rule that fires. `app` only needs post(fn, *args) and the on_* callbacks (see UI)."""

    def __init__(self, app, cfg, io=None, grab=None, mon=None, dry=None):
        super().__init__(daemon=True)
        self.app = app
        self.cfg = json.loads(json.dumps(cfg))
        self.st = self.cfg["settings"]
        self.rules = self.cfg["rules"]
        self.imgs = {m["id"]: m for m in self.cfg["images"]}
        self.dry = bool(self.st.get("test_mode")) if dry is None else bool(dry)
        self.io = io or WinIO()
        self.grab_fn, self.mon = grab, mon
        self.stop_ev = threading.Event()
        self.store = ImageStore()
        self.finders, self.state, self.cache, self.skip = {}, {r["id"]: new_state() for r in self.rules}, {}, {}
        self.warned, self.reads = {}, []
        self.t0 = self.last_act = time.perf_counter()
        self.last_refresh, self.refreshes = -1e9, 0
        self.final, self._fin, self.depth, self.frame = None, False, 0, None
        self.pause_ev, self._paused, self.scale = threading.Event(), False, None
        self.busy, self.skip_rest = set(), False

    # --- helpers
    def say(self, msg, level="info"):
        write_log(msg)
        self.app.post(self.app.on_log, msg, level)

    def warn_once(self, key, msg, level="warn", every=1e9):
        now = time.perf_counter()
        if now - self.warned.get(key, -1e9) > every:
            self.warned[key] = now
            self.say(msg, level)

    def stopped(self):
        if self.stop_ev.is_set():
            return True
        if IS_WIN and self.io.cursor_pos() == (0, 0):  # emergency stop: mouse in the top-left corner
            self.stop_ev.set()
            self.say("توقف اضطراری: ماوس به گوشه‌ی صفحه رفت", "warn")
            return True
        return False

    def sleep(self, t):
        end = time.perf_counter() + t
        while time.perf_counter() < end:
            if self.stopped():
                return True
            time.sleep(min(0.02, max(0.0, end - time.perf_counter())))
        return False

    def finish(self, kind):
        if not self._fin:
            self._fin = True
            self.app.post(self.app.on_finish, kind)

    def rs(self, r):
        return self.state.setdefault(r["id"], new_state())

    def thr(self, c=None):
        v = (c or {}).get("thr")
        return min(0.99, max(0.4, num(v, 0))) if v else float(self.st.get("threshold", 0.8))

    def finder(self, iid):
        if iid not in self.finders:
            meta, fd = self.imgs.get(iid), None
            raw = self.store.load(meta) if meta else None
            if raw is None:
                self.warn_once("img" + str(iid), "تصویر «%s» پیدا نشد؛ آن را دوباره ثبت کنید" % (meta or {}).get("name", iid))
            else:
                fd = Finder(raw, (self.scale or num(self.st.get("scale", 0)) or self.mon["width"] / REF_WIDTH) if meta.get("emb") else 1.0)
            self.finders[iid] = fd
        return self.finders[iid]

    def cands(self, frame, iid, area, thr):
        """All places image `iid` is seen (inside `area` if given), as dicts in monitor coordinates."""
        key = (iid, tuple(area) if area else None, thr)
        if key in self.cache:
            return self.cache[key]
        out, fd = [], self.finder(iid)
        if fd is not None:
            ox = oy = 0
            sub = frame
            if area:
                x0, y0, x1, y1 = [max(0, int(v)) for v in area]
                sub, ox, oy = frame[y0:y1, x0:x1], x0, y0
            for sc, loc, t in fd.find_all(sub, thr):
                h, w = t.shape[:2]
                x, y = loc[0] + ox, loc[1] + oy
                out.append({"img": iid, "sc": sc, "x": x, "y": y, "w": w, "h": h, "cx": x + w / 2, "cy": y + h / 2,
                            "k": w / fd.raw.shape[1], "rel": fd.hint / fd.base})
        self.cache[key] = out
        return out

    def near_ok(self, frame, m, near, thr):
        """near: one requirement or a list (all must hold). Requirement: img or imgs (any of them),
        radius in pixels (0 = anywhere on screen), want (True = must be next to, False = must not be)."""
        return all(self.near_one(frame, m, n, thr) for n in (near if isinstance(near, list) else [near]))

    def near_one(self, frame, m, n, thr):
        r0, found = num(n.get("radius", 300)), False
        radius = r0 * m["k"]
        for iid in near_ids(n):
            fd = self.finder(iid)
            if fd is None:
                continue
            for mult in NEAR_MULTS:
                t = scaled(fd.raw, m["rel"] * mult)
                if min(t.shape[:2]) < 8:
                    continue
                for _, (lx, ly) in all_matches(frame, t, thr):
                    bw, bh = t.shape[1], t.shape[0]
                    inside = lx <= m["cx"] <= lx + bw and ly <= m["cy"] <= ly + bh
                    if r0 <= 0 or inside or math.hypot(m["cx"] - (lx + bw / 2), m["cy"] - (ly + bh / 2)) <= radius:
                        found = True
                        break
                if found:
                    break
            if found:
                break
        return found == bool(n.get("want", True))

    # --- conditions
    def eval_seen(self, c, r, frame, now, ci):
        thr = self.thr(c)
        cs = []
        for iid in seen_ids(c):
            cs += self.cands(frame, iid, r.get("area"), thr)
        cs.sort(key=lambda m: -m["sc"])
        near = near_list(c)
        if cs and near:
            key = (r["id"], ci)
            if now < self.skip.get(key, 0):
                return []
            cs = [m for m in cs if self.near_ok(frame, m, near, thr)]
            if not cs:
                self.skip[key] = now + 0.15
                self.warn_once(("near", key), "«%s» دیده شد ولی شرط «کنار/نه کنار» برقرار نبود؛ نادیده گرفته شد"
                               % img_name(self.cfg, c.get("img")), "warn", 3.0)
        if cs and c.get("num"):
            try:
                import digits
            except ImportError:
                self.warn_once("nodigits", "فایل digits.py کنار برنامه نیست؛ شرط عدد رد شد", "warn")
                return []
            cs = digits.filter_matches(self, c, cs, frame)
        return cs

    def eval_plain(self, c, now):
        t = c["t"]
        if t == "rule":
            s = self.state.get(c.get("rule"))
            if s is None:
                return False
            done, stt = s["count"] > 0, c.get("state", "done")
            return done if stt == "done" else (not done if stt == "notdone" else (done or s["skipped"]))
        if t == "after":
            src = c.get("from", "start")
            base = self.t0 if src == "start" else (self.state.get(src) or {}).get("t_last")
            return base is not None and now - base >= num(c.get("secs", 0))
        if t == "clock":
            hm = parse_hhmm(c.get("at", ""))
            lt = time.localtime()
            return bool(hm) and (lt.tm_hour, lt.tm_min) >= hm
        return False

    def eval_rule(self, r, frame, now, assume=False):
        """(ok, ctx): ctx = chosen match of the first satisfied 'seen' condition (or None)."""
        conds = r.get("conds", [])
        if not conds:
            return True, None
        res, ctx = [], None
        for ci, c in enumerate(conds):
            t = c["t"]
            if t == "seen":
                cs = self.eval_seen(c, r, frame, now, ci)
                if cs and ctx is None:
                    ctx = pick(cs, r.get("by", "score"), r.get("tie", "top"))
                res.append(bool(cs))
            elif t == "unseen":
                res.append(not any(self.cands(frame, i, r.get("area"), self.thr(c)) for i in seen_ids(c)))
            else:
                res.append(True if assume else self.eval_plain(c, now))
        ok = all(res) if r.get("join", "and") == "and" else any(res)
        return ok, ctx

    def armed(self, r, now):
        cs = [c for c in r.get("conds", []) if c["t"] in ("rule", "after", "clock")]
        if not cs:
            return True
        res = [self.eval_plain(c, now) for c in cs]
        return all(res) if r.get("join", "and") == "and" else any(res)

    # --- one scan of all rules
    def step(self, frame, now, only=None):
        """One pass over the rules (only those in `only` if given); runs the first one that fires."""
        order = sorted(range(len(self.rules)), key=lambda i: (-int(num(self.rules[i].get("prio", 0))), i))
        for i in order:
            r = self.rules[i]
            if not r.get("on", True) or r["id"] in self.busy or (only is not None and r["id"] not in only):
                continue
            s, times = self.rs(r), int(num(r.get("times", 1)))
            if s["skipped"] or (times > 0 and s["count"] >= times):
                continue
            to = num(r.get("timeout", 0))
            if to > 0 and s["count"] == 0:
                if self.armed(r, now):
                    s["armed"] = now if s["armed"] is None else s["armed"]
                    if now - max(s["armed"], self.last_act) > to:
                        s["skipped"] = True
                        self.say("«%s» دیده نشد؛ رد شد" % r.get("name", ""), "warn")
                        continue
                else:
                    s["armed"] = None
            gap = max(num(r.get("gap", 0)), 1.0 if self.dry else 0.0)
            if now - s["last"] < gap:
                continue
            ok, ctx = self.eval_rule(r, frame, now)
            s["streak"] = s["streak"] + 1 if ok else 0
            if ok and s["streak"] >= max(1, int(num(r.get("confirm", 1)))):
                self.fire(r, ctx)
                return True
        return False

    # --- actions
    def click_target(self, a, ctx):
        """(x, y, box) in monitor coordinates, or None when the target cannot be found."""
        mode = a.get("mode", "match")
        if mode == "xy":
            x, y = a.get("xy", [0, 0])
            return x, y, (x - 8, y - 8, x + 8, y + 8)
        if ctx is None:
            return None
        if mode == "match":
            return ctx["cx"], ctx["cy"], (ctx["x"], ctx["y"], ctx["x"] + ctx["w"], ctx["y"] + ctx["h"])
        dx, dy, w, h = a.get("rel", [0, 0, 10, 10])
        k = ctx["k"]
        x0, y0 = ctx["x"] + dx * k, ctx["y"] + dy * k
        x1, y1 = x0 + w * k, y0 + h * k
        if a.get("rand"):
            return (random.uniform(x0 + (x1 - x0) * 0.2, x1 - (x1 - x0) * 0.2),
                    random.uniform(y0 + (y1 - y0) * 0.2, y1 - (y1 - y0) * 0.2), (x0, y0, x1, y1))
        return (x0 + x1) / 2, (y0 + y1) / 2, (x0, y0, x1, y1)

    def watch_ok(self, rid, frame, now):
        r = next((x for x in self.rules if x["id"] == rid), None)
        if r is None or not r.get("on", True) or rid in self.busy:
            return False
        s, times = self.rs(r), int(num(r.get("times", 1)))
        if s["skipped"] or (times > 0 and s["count"] >= times):
            return False
        return self.eval_rule(r, frame, now)[0]

    def confirm_hit(self, watch, settle):
        """The page has stopped moving: look again on still frames; True when a watched rule fired."""
        if self.sleep(settle):
            return False
        end = time.perf_counter() + 2.5
        while time.perf_counter() < end and not self.stopped():
            self.frame = frame = self.grab_fn()
            self.cache.clear()
            if self.step(frame, time.perf_counter(), only=watch):
                return True
            time.sleep(0.03)
        return False

    def do_scan(self, a):
        """Scrolls the page at a fixed speed until: a watched rule can run (-> 'hit': stop, confirm, act), the page
        cannot go further ('bottom'), or the time limit ('time')."""
        speed, sign = max(0.05, num(a.get("speed", 3))), (1 if a.get("dir") == "up" else -1)
        max_s, still_s = max(1.0, num(a.get("max_secs", 180))), max(0.3, num(a.get("still_secs", 1.0)))
        watch, settle, area = list(a.get("watch") or []), max(0.05, num(a.get("settle", 0.3))), a.get("region")
        by_bottom = a.get("end", "bottom") == "bottom"
        if a.get("center", True):
            self.io.move(self.mon["left"] + self.mon["width"] / 2, self.mon["top"] + self.mon["height"] / 2)
        self.say("پیمایش شروع شد (%g گام بر ثانیه)" % speed)
        t_start = last = time.perf_counter()
        prev = still_since = None
        cool = 0.0
        while not self.stopped():
            now = time.perf_counter()
            if self.pause_ev.is_set():
                time.sleep(0.05)
                last = now
                continue
            if now - t_start > max_s:
                self.say("پیمایش: حداکثر زمان تمام شد", "warn")
                return "time"
            self.frame = frame = self.grab_fn()
            self.cache.clear()
            if watch and now >= cool and any(self.watch_ok(w, frame, now) for w in watch):
                if self.confirm_hit(watch, settle):
                    self.say("هدف دیده شد؛ پیمایش متوقف شد", "hit")
                    return "hit"
                cool = time.perf_counter() + 0.7  # false alarm: keep scrolling
                last = time.perf_counter()
                continue
            if by_bottom:
                crop = frame if not area else frame[max(0, int(area[1])):int(area[3]), max(0, int(area[0])):int(area[2])]
                small = cv2.resize(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), (96, 54), interpolation=cv2.INTER_AREA)
                if prev is not None:
                    if float(np.mean(cv2.absdiff(small, prev))) < 0.4:
                        still_since = still_since or now
                        if now - still_since >= still_s and now - t_start > 1.0:
                            self.say("پیمایش: به انتهای صفحه رسید", "ok")
                            return "bottom"
                    else:
                        still_since = None
                prev = small
            self.io.scroll(sign * speed * (now - last))
            last = now
            time.sleep(0.02)
        return "stopped"

    def save_shot(self, ctx):
        try:
            fr = self.frame.copy()
            if ctx:
                cv2.rectangle(fr, (int(ctx["x"]), int(ctx["y"])), (int(ctx["x"] + ctx["w"]), int(ctx["y"] + ctx["h"])),
                              (0, 0, 255), 3)
            path = base_dir() / "debug" / time.strftime("shot_%Y%m%d_%H%M%S.png")
            threading.Thread(target=imwrite, args=(path, fr), daemon=True).start()
            self.say("عکس صفحه ذخیره شد: debug/" + path.name)
        except Exception:
            pass

    def do_action(self, a, ctx, boxes, rule=None):
        t, cfg, st = a.get("t"), self.cfg, self.st
        txt = describe_act(cfg, a)
        phys = t in ("click", "type", "key", "scroll", "sleep", "bright", "wait", "sound", "halo", "scan", "refresh")
        if self.dry and phys:
            self.say("آزمایشی (اجرا نشد): " + txt, "info")
            if t == "click":
                tg = self.click_target(a, ctx)
                if tg:
                    boxes.append(tg[2])
            return
        if t == "click":
            tg = self.click_target(a, ctx)
            if tg is None:
                self.say("هدف کلیک پیدا نشد (برای این کار باید تصویری دیده شود)؛ رد شد", "warn")
                return
            self.io.click(self.mon["left"] + tg[0], self.mon["top"] + tg[1], a.get("button", "left"), a.get("double"))
            self.say(txt, "ok")
        elif t == "type":
            self.io.type_text(str(a.get("text", "")).replace("{num}", str((ctx or {}).get("numtxt", ""))))
            self.say(txt, "ok")
        elif t == "key":
            self.io.key(a.get("keys", "enter"))
            self.say(txt, "ok")
        elif t == "scroll":
            self.io.scroll(num(a.get("amount", -3)))
            self.say(txt, "ok")
        elif t == "wait":
            self.sleep(num(a.get("secs", 1)))
        elif t == "refresh":
            self.io.key("f5")
            self.refreshes += 1
            self.say(txt, "ok")
            self.sleep(num(a.get("wait", 2)))
        elif t == "scan":
            res = self.do_scan(a)
            if res == "hit":
                self.skip_rest = True  # the watched rule already acted: skip the rest of this rule (e.g. the refresh)
        elif t == "sound":
            announce(a.get("kind", "hit"), a.get("text", ""), st)
        elif t == "shot":
            self.save_shot(ctx)
        elif t == "halo":
            self.app.post(self.app.on_halo, a.get("text") or "اتو کلیکر")
        elif t == "bright":
            max_brightness()
        elif t == "sleep":
            self.say("خواب کامپیوتر: ۳۰ ثانیه دیگر", "warn")
            self.app.post(self.app.request_sleep)
            self.final = "stop"
        elif t == "stop":
            self.final = "done"
        elif t == "run":
            tgt = [r for r in self.rules if r["id"] == a.get("rule")]
            if tgt and self.depth < 4:
                self.depth += 1
                try:
                    self.fire(tgt[0], ctx)
                finally:
                    self.depth -= 1
            elif not tgt:
                self.say("قانونِ مقصد پیدا نشد؛ رد شد", "warn")

    def fire(self, r, ctx):
        s = self.rs(r)
        extra = " (شباهت %.2f)" % ctx["sc"] if ctx else ""
        self.say("قانون «%s» اجرا شد%s" % (r.get("name", ""), extra), "hit" if not self.dry else "info")
        s["streak"], boxes = 0, []
        if self.dry and ctx:
            boxes.append((ctx["x"], ctx["y"], ctx["x"] + ctx["w"], ctx["y"] + ctx["h"]))
        self.busy.add(r["id"])
        try:
            for a in r.get("acts", []):
                if self.stopped():
                    break
                try:
                    self.do_action(a, ctx, boxes, r)
                except Exception as e:
                    self.say("خطا در کار «%s»: %s" % (describe_act(self.cfg, a), e), "error")
                if self.final or self.skip_rest:
                    self.skip_rest = False
                    break
        finally:
            self.busy.discard(r["id"])
        s["count"] += 1
        s["last"] = s["t_last"] = self.last_act = time.perf_counter()
        if boxes:
            self.app.post(self.app.on_boxes, boxes, dict(self.mon))

    # --- main loop
    def _open_source(self):
        if self.grab_fn is not None:
            return None
        import mss
        sct = mss.mss()
        self.mon = mon = sct.monitors[mon_index(sct, self.st.get("monitor", 1))]
        self.grab_fn = lambda: np.ascontiguousarray(np.asarray(sct.grab(mon))[:, :, :3])
        return sct

    def run(self):
        try:
            self._main()
        except Exception:
            self.say("خطای برنامه:\n" + traceback.format_exc(), "error")
            self.finish("error")

    def _schedule(self):
        hm = parse_hhmm(self.st.get("start_at", ""))
        if not hm:
            return True
        lt = time.localtime()
        target = time.mktime(lt[:3] + (hm[0], hm[1], 0, 0, 0, -1))
        remain = target - time.time()
        if remain <= 0:
            self.say("ساعت شروع امروز گذشته؛ همین حالا شروع می‌شود", "warn")
            return True
        self.say("شروع خودکار ساعت %02d:%02d. پنجره‌ی هدف را آماده نگه دارید" % hm)
        while remain > 0:
            if self.stopped():
                return False
            h, rem = divmod(int(remain), 3600)
            m, sec = divmod(rem, 60)
            self.app.post(self.app.on_status, "شروع تا %02d:%02d:%02d دیگر" % (h, m, sec), "wait")
            time.sleep(min(0.5, max(0.0, remain)))
            remain = target - time.time()
        return True

    def refresh_tick(self, now):
        st = self.st
        iv, ah = num(st.get("refresh", 0)), num(st.get("refresh_after_hours", 0))
        if ah > 0 and now - self.t0 >= ah * 3600:
            iv = num(st.get("refresh_after", iv)) or iv
        if iv <= 0 or now - self.last_refresh < iv:
            return
        self.last_refresh = now
        if self.io.foreground_is_me():
            self.warn_once("fg", "پنجره‌ی هدف فعال نیست؛ رفرش انجام نشد", "warn", 5.0)
        elif not self.dry:
            self.io.key("f5")
            self.refreshes += 1

    def all_finished(self):
        act = [r for r in self.rules if r.get("on", True) and not r.get("opt")]  # 'opt' rules never hold the run open
        for r in act:
            s, times = self.rs(r), int(num(r.get("times", 1)))
            if times <= 0 or not (s["count"] >= times or s["skipped"]):
                return False
        return bool(act)

    def _main(self):
        st = self.st
        src = self._open_source()
        try:
            if not self._schedule():
                return self.finish("stop")
            self.app.post(self.app.on_status, "چند ثانیه فرصت دارید پنجره‌ی هدف را فعال کنید", "wait")
            if self.sleep(num(st.get("countdown", 3))):
                return self.finish("stop")
            self.find_scale()
            n = len([r for r in self.rules if r.get("on", True)])
            self.say("شروع اجرا (%s)؛ %d قانون فعال" % ("آزمایشی، بدون کلیک" if self.dry else "واقعی", n))
            self.app.post(self.app.on_status, "در حال اجرا" + (" (آزمایشی)" if self.dry else ""), "run")
            self.loop()
        finally:
            if src is not None:
                src.close()

    def find_scale(self):
        """Scale of the built-in (embedded) images: fixed setting, else found with the 'filters' panel, else by width."""
        if not any(m.get("emb") for m in self.imgs.values()):
            return
        s = num(self.st.get("scale", 0))
        if s > 0:
            self.say("مقیاس ثابت: %.2f" % s)
        else:
            anchor = self.store.load({"id": "anchor", "emb": "anchor", "file": ""})
            sc, sv = (calibrate(self.grab_fn(), anchor)[:2]) if anchor is not None else (0.0, 1.0)
            if sc >= 0.55:
                s = sv
                self.say("مقیاس خودکار: %.2f (امتیاز %.2f)" % (s, sc), "ok")
            else:
                s = self.mon["width"] / REF_WIDTH
                self.say("مقیاس خودکار پیدا نشد، حدس بر اساس عرض صفحه: %.2f" % s, "warn")
        self.scale = s
        self.finders.clear()

    def toggle_pause(self):
        if self.pause_ev.is_set():
            self.pause_ev.clear()
        else:
            self.pause_ev.set()
        return self.pause_ev.is_set()

    def loop(self):
        st = self.st
        self.t0 = self.last_act = t0 = time.perf_counter()
        last_stat, maxm = 0.0, num(st.get("max_minutes", 0))
        while not self.stopped():
            now = time.perf_counter()
            if self.pause_ev.is_set():
                if not self._paused:
                    self._paused = True
                    self.say("مکث شد؛ با کلید مکث ادامه می‌دهد", "warn")
                    self.app.post(self.app.on_status, "مکث", "wait")
                time.sleep(0.05)
                continue
            if self._paused:
                self._paused = False
                self.say("ادامه‌ی اجرا", "ok")
                self.app.post(self.app.on_status, "در حال اجرا" + (" (آزمایشی)" if self.dry else ""), "run")
            if maxm > 0 and now - t0 > maxm * 60:
                self.say("زمان تعیین‌شده (%g دقیقه) تمام شد؛ اجرا متوقف شد" % maxm, "warn")
                announce("fail", "Time limit reached.", st)
                return self.finish("stop")
            self.refresh_tick(now)
            self.frame = frame = self.grab_fn()
            self.cache.clear()
            self.reads = []
            fired = self.step(frame, now)
            if self.final:
                return self.finish(self.final)
            if now - last_stat > 0.25:
                dn = len([1 for s in self.state.values() if s["count"] > 0])
                self.app.post(self.app.on_stats, self.refreshes, now - t0, dn, len(self.rules))
                last_stat = now
            if self.all_finished():
                sk = [r.get("name", "") for r in self.rules if self.rs(r)["skipped"]]
                self.say("همه‌ی قانون‌ها تمام شد" + (". رد شده: " + "، ".join(sk) if sk else ""), "warn" if sk else "hit")
                if st.get("announce_end", True) and not self.dry:
                    announce("done", "Done. Some steps were skipped." if sk else "All steps completed successfully.", st)
                return self.finish("done")
            if fired:
                if self.sleep(num(st.get("settle", 0.4))):
                    break
            else:
                time.sleep(max(0.005, num(st.get("tick", 0.02))))
        self.say("پایان اجرا: %d رفرش در %d ثانیه" % (self.refreshes, int(time.perf_counter() - t0)))
        self.finish("stop")

    # --- one-shot test of a single rule (no clicks): returns (text lines, boxes, monitor)
    def probe(self, r):
        src = self._open_source()
        try:
            frame = self.grab_fn()
            self.frame, self.reads = frame, []
            now, lines, boxes = time.perf_counter(), [], []
            ok, ctx = self.eval_rule(r, frame, now, assume=True)
            for c in r.get("conds", []):
                if c["t"] not in ("seen", "unseen"):
                    lines.append("شرط «%s» در آزمایش برقرار فرض شد" % describe_cond(self.cfg, c))
                    continue
                for iid in seen_ids(c):
                    cs = self.cands(frame, iid, r.get("area"), self.thr(c))
                    nm, fd = img_name(self.cfg, iid), self.finder(iid)
                    if cs:
                        lines.append("«%s»: دیده شد (%d مورد، بهترین شباهت %.2f)" % (nm, len(cs), cs[0]["sc"]))
                        boxes += [(m["x"], m["y"], m["x"] + m["w"], m["y"] + m["h"]) for m in cs]
                    elif fd is not None:
                        sc = fd.match(frame, 2.0)[0]
                        lines.append("«%s»: دیده نشد (بهترین شباهت %.2f، حداقل لازم %.2f)" % (nm, sc, self.thr(c)))
            lines += self.reads
            lines.append("نتیجه: شرط‌ها " + ("برقرار است؛ در اجرای واقعی کارها انجام می‌شود" if ok else "برقرار نیست"))
            if ok and ctx:
                for a in r.get("acts", []):
                    tg = self.click_target(a, ctx) if a.get("t") == "click" else None
                    if tg:
                        boxes.append(tg[2])
            return lines, boxes, dict(self.mon)
        finally:
            if src is not None:
                src.close()
                self.grab_fn = None
# ===== END OF FILE (total lines: 1326) =====
