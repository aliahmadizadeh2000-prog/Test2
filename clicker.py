# -*- coding: utf-8 -*-
"""
ربات حراج

صفحه را مرتب رفرش می‌کند، با پردازش تصویر دنبال آیکون «حراج‌جمعه!» می‌گردد و
به محض دیدنش این مسیر را می‌رود:
    کلیک روی محصول ← افزودن به سبد خرید ← آیکون سبد ← مراحل اضافه‌ی خودتان ← توقف
در طول مسیر یک هاله قرمز دور صفحه می‌آید و صدای هشدار پخش می‌شود.

توقف اضطراری: کلید F8، یا بردن ماوس به گوشه بالا-چپ صفحه.
فقط برای ویندوز. نیازمندی‌ها: requirements.txt
"""
import sys
import os
import json
import math
import shutil
import subprocess
import base64
import time
import queue
import threading
import traceback
from pathlib import Path

import numpy as np
import cv2

try:
    import tkinter as tk
    from tkinter import messagebox, simpledialog, filedialog, ttk
except ImportError:  # فقط برای تست منطق تشخیص روی سیستم‌های بدون tkinter
    tk = None

IS_WIN = sys.platform == "win32"
if IS_WIN:
    import ctypes
    from ctypes import wintypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # مختصات واقعی پیکسل‌ها
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    user32 = ctypes.windll.user32

APP_TITLE = "ربات حراج"
REF_WIDTH = 1280  # عرض عکس‌هایی که قالب‌ها از آن‌ها بریده شده

DEFAULTS = {
    "interval": 2.5,        # فاصله رفرش‌ها (ثانیه)
    "threshold": 0.80,      # حداقل شباهت برای آیکون حراج‌جمعه
    "btn_threshold": 0.80,  # حداقل شباهت برای دکمه‌ها
    "confirm": 2,           # تعداد فریم پشت‌سرهم برای تأیید دیدن آیکون
    "step_timeout": 8.0,    # حداکثر انتظار برای باز شدن هر صفحه (ثانیه)
    "scale": 0.0,           # 0 یعنی خودکار
    "beep": True,
    "halo": True,
    "monitor": 1,           # شماره صفحه‌نمایش (۱ = اصلی)
    "max_minutes": 0,       # توقف خودکار بعد از این‌قدر دقیقه (۰ = بدون محدودیت)
    "start_at": "",         # شروع خودکار در ساعت HH:MM (خالی = همین حالا)
    "shot_on_hit": True,    # ذخیره عکس صفحه لحظه دیدن آیکون در پوشه debug
    "always_on_top": False, # پنجره ربات همیشه روی بقیه
    "speak": True,          # اعلام صوتی یک‌جمله‌ای به انگلیسی
    "beep_seconds": 3,      # مدت صدای هشدار (ثانیه)
    "exclude_radius": 300,  # آیکونی که این‌قدر (پیکسل) به تصویر مستثنی نزدیک باشد نادیده گرفته می‌شود
    "brightness_on_hit": True,  # با دیدن آیکون حراج‌جمعه روشنایی صفحه تا آخر روی ۱۰۰٪ برود
    "sleep_at": "",         # ساعت رفتن کامپیوتر به خواب (مثلاً 02:30)؛ خالی = غیرفعال
    "sleep_hours": 0,       # یا چند ساعت دیگر (۰ = غیرفعال)
    "interval_after_hours": 0,  # بعد از این‌قدر ساعت رصد، فاصله رفرش عوض شود (۰ = غیرفعال)
    "interval_after": 5.0,      # فاصله رفرش جدید (ثانیه)
}

TEMPLATES = {
    "badge": "sale_badge.png",
    "add": "add_to_cart.png",
    "cart": "cart_icon.png",
    "anchor": "anchor_filters.png",
}
EXCL_HINT = "(برای دیدن تصویر، یک مورد را از فهرست انتخاب کنید)"
CAPTURE_LABELS = {
    "badge": "آیکون حراج‌جمعه",
    "add": "دکمه افزودن به سبد خرید",
    "cart": "آیکون سبد خرید",
}


from assets import EMBEDDED  # target images (base64), kept in assets.py

# ------------------------------------------------------------------ مسیرها و تنظیمات
def base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def res_dir():
    return Path(getattr(sys, "_MEIPASS", base_dir()))


def load_config():
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads((base_dir() / "config.json").read_text(encoding="utf-8")))
    except Exception:
        pass
    return cfg


def save_config(cfg):
    try:
        (base_dir() / "config.json").write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def write_log(line):
    try:
        with open(base_dir() / "bot.log", "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + line + "\n")
    except Exception:
        pass


def imread(path):
    data = np.fromfile(str(path), dtype=np.uint8)  # سازگار با مسیرهای فارسی
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def imwrite(path, img):
    ok, buf = cv2.imencode(".png", img)
    if ok:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        buf.tofile(str(path))


def parse_hhmm(text):
    """«20:00» ← (20, 0)؛ اگر معتبر نبود None."""
    try:
        h, m = str(text).strip().split(":")
        h, m = int(h), int(m)
        return (h, m) if 0 <= h < 24 and 0 <= m < 60 else None
    except Exception:
        return None


def mon_index(sct, idx):
    """شماره صفحه‌نمایش معتبر برمی‌گرداند؛ در غیر این صورت صفحه اصلی."""
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        return 1
    return idx if 1 <= idx < len(sct.monitors) else 1


# ------------------------------------------------------------------ پردازش تصویر
class Templates:
    """قالب‌ها را می‌خواند. تصویر ثبت‌شده‌ی خود کاربر (templates_user) اولویت دارد و مقیاس نمی‌خورد."""

    def __init__(self):
        self.raw = {}
        self.cache = {}

    def load_raw(self, key):
        if key not in self.raw:
            name = TEMPLATES[key]
            user = base_dir() / "templates_user" / name
            if key != "anchor" and user.exists():
                self.raw[key] = (imread(user), True)
            else:
                data = np.frombuffer(base64.b64decode(EMBEDDED[key]), dtype=np.uint8)
                self.raw[key] = (cv2.imdecode(data, cv2.IMREAD_COLOR), False)
        return self.raw[key]

    def forget(self):
        self.raw.clear()
        self.cache.clear()

    def scaled(self, key, scale):
        img, custom = self.load_raw(key)
        s = 1.0 if custom else scale
        k = (key, round(s, 3))
        if k not in self.cache:
            if abs(s - 1.0) < 0.01:
                self.cache[k] = img
            else:
                interp = cv2.INTER_CUBIC if s > 1 else cv2.INTER_AREA
                self.cache[k] = cv2.resize(img, None, fx=s, fy=s, interpolation=interp)
        return self.cache[k]


def best_match(frame, tpl):
    th, tw = tpl.shape[:2]
    if frame.shape[0] < th or frame.shape[1] < tw:
        return 0.0, (0, 0)
    res = cv2.matchTemplate(frame, tpl, cv2.TM_CCOEFF_NORMED)
    res = np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0)
    _, mx, _, loc = cv2.minMaxLoc(res)
    return float(mx), loc


def all_matches(frame, tpl, thr, limit=12):
    """همه‌ی جاهایی که تصویر با شباهت ≥ thr دیده می‌شود: [(امتیاز، (x, y)), ...]"""
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


EXCL_MULTS = [1.0, 0.92, 1.08, 0.85, 1.17, 0.78, 1.28]  # اندازه‌هایی که برای تصویر مستثنی امتحان می‌شود
MULTS = [1.0, 0.92, 1.08, 0.85, 1.17, 0.78, 1.28, 0.7, 1.4, 0.6, 1.6]  # ضریب اندازه‌هایی که امتحان می‌شود


class Finder:
    """یک تصویر هدف را در چند اندازه جست‌وجو می‌کند.
    اندازه‌ی آخرین موفقیت اول امتحان می‌شود؛ در هر بار فراخوانی یک اندازه‌ی دیگر هم نوبتی بررسی می‌شود،
    پس سرعت حلقه زیاد کم نمی‌شود و بعد از چند بار همه‌ی اندازه‌ها پوشش داده شده‌اند."""

    def __init__(self, raw, base):
        self.raw, self.base, self.hint = raw, float(base), float(base)
        self.cache = {}
        self.i = 0

    def tpl(self, s):
        k = round(s, 3)
        if k not in self.cache:
            if abs(s - 1.0) < 0.01:
                self.cache[k] = self.raw
            else:
                interp = cv2.INTER_CUBIC if s > 1 else cv2.INTER_AREA
                self.cache[k] = cv2.resize(self.raw, None, fx=s, fy=s, interpolation=interp)
        return self.cache[k]

    def match(self, frame, thr):
        """(امتیاز، موقعیت، تصویر هم‌اندازه‌ی پیداشده) را برمی‌گرداند."""
        t = self.tpl(self.hint)
        sc, loc = best_match(frame, t)
        best = (sc, loc, t)
        if sc >= thr:
            return best
        cs = self.base * MULTS[self.i % len(MULTS)]
        self.i += 1
        if abs(cs - self.hint) > 0.005:
            t2 = self.tpl(cs)
            if min(t2.shape[:2]) >= 8:
                sc2, loc2 = best_match(frame, t2)
                if sc2 > sc:
                    best = (sc2, loc2, t2)
                if sc2 >= thr:
                    self.hint = cs
        return best


def calibrate(frame, templates):
    """مقیاس نمایش را با پیدا کردن پنل «فیلترها» در صفحه لیست محصولات حدس می‌زند."""
    anchor, _ = templates.load_raw("anchor")
    g_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    g_anchor = cv2.cvtColor(anchor, cv2.COLOR_BGR2GRAY)

    def score(s):
        t = cv2.resize(g_anchor, None, fx=s, fy=s,
                       interpolation=cv2.INTER_CUBIC if s > 1 else cv2.INTER_AREA)
        sc, loc = best_match(g_frame, t)
        return sc, loc, t.shape

    best = (0.0, 1.0, (0, 0), (0, 0))
    for s in np.arange(0.5, 2.51, 0.05):
        sc, loc, shp = score(float(s))
        if sc > best[0]:
            best = (sc, float(s), loc, shp)
    for s in np.arange(best[1] - 0.05, best[1] + 0.0501, 0.01):
        if s <= 0.3:
            continue
        sc, loc, shp = score(float(s))
        if sc > best[0]:
            best = (sc, float(s), loc, shp)
    return best  # (امتیاز، مقیاس، موقعیت، اندازه)


# ------------------------------------------------------------------ ورودی سیستم (ویندوز)
if IS_WIN:
    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    def cursor_pos():
        p = _POINT()
        user32.GetCursorPos(ctypes.byref(p))
        return p.x, p.y

    def click(x, y):
        user32.SetCursorPos(int(x), int(y))
        time.sleep(0.02)
        user32.mouse_event(0x0002, 0, 0, 0, 0)  # دکمه چپ پایین
        time.sleep(0.03)
        user32.mouse_event(0x0004, 0, 0, 0, 0)  # دکمه چپ بالا

    def press_key(vk):
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, 2, 0)

    def foreground_is_me():
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(user32.GetForegroundWindow(), buf, 256)
        return APP_TITLE in buf.value
else:
    def cursor_pos():
        return (1, 1)

    def click(x, y):
        raise RuntimeError("فقط ویندوز")

    def press_key(vk):
        raise RuntimeError("فقط ویندوز")

    def foreground_is_me():
        return False


_beep_gen = [0]
_speaker = [None]
BEEP_SEQS = {
    "hit": [(880, 250), (0, 80), (880, 250), (0, 80), (1175, 500), (0, 300)],
    "done": [(784, 400), (0, 80), (988, 400), (0, 80), (1175, 700), (0, 300)],
    "fail": [(330, 500), (0, 80), (220, 700), (0, 300)],
}
SPEAK_SCRIPT = (
    "Add-Type -AssemblyName System.Speech;"
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
    "try { $s.SelectVoiceByHints('NotSet','NotSet',0,[System.Globalization.CultureInfo]'en-US') } catch {};"
    "$s.Volume = 100;"
    "while ($true) { $l = [Console]::In.ReadLine(); if ($l -eq $null) { break }; [void]$s.Speak($l) }"
)


def prewarm_speech():
    """یک PowerShell پنهان را از قبل راه می‌اندازد تا جمله‌ها بدون تأخیر خوانده شوند."""
    if not IS_WIN:
        return
    p = _speaker[0]
    if p is not None and p.poll() is None:
        return
    try:
        _speaker[0] = subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", SPEAK_SCRIPT],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=0x08000000)  # CREATE_NO_WINDOW
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
        p.stdin.write((text + "\n").encode("ascii", "ignore"))
        p.stdin.flush()
    except Exception:
        _speaker[0] = None


def announce(kind, text, cfg):
    """اعلام موفقیت/مشکل: یک جمله‌ی انگلیسی (اگر روشن باشد) و بعد بوق (اگر روشن باشد)."""
    if not IS_WIN:
        return
    import winsound
    speak_on = bool(cfg.get("speak", True)) and bool(text)
    _beep_gen[0] += 1
    gen = _beep_gen[0]

    def _run():
        if speak_on:
            _speak(text)
        if not cfg.get("beep", True):
            return
        if speak_on:  # صبر کن تا جمله تمام شود تا صدای بوق روی آن نیفتد
            end_wait = time.time() + min(4.0, 0.4 + 0.07 * len(text))
            while time.time() < end_wait and _beep_gen[0] == gen:
                time.sleep(0.05)
        end = time.time() + max(0.3, float(cfg.get("beep_seconds", 3)))
        while time.time() < end and _beep_gen[0] == gen:
            for f, d in BEEP_SEQS[kind]:
                if time.time() >= end or _beep_gen[0] != gen:
                    return
                if f:
                    winsound.Beep(f, d)
                else:
                    time.sleep(d / 1000)
    threading.Thread(target=_run, daemon=True).start()


def max_brightness():
    """روشنایی صفحه را روی ۱۰۰٪ می‌برد: لپ‌تاپ با WMI و مانیتور خارجی با DDC/CI (در پس‌زمینه)."""
    if not IS_WIN:
        return

    def _run():
        try:  # صفحه داخلی لپ‌تاپ
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command",
                 "(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1,100)"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=0x08000000, timeout=10)
        except Exception:
            pass
        try:  # مانیتور خارجی (اگر DDC/CI را پشتیبانی کند)
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
    """کامپیوتر را به حالت Sleep می‌برد (نه Hibernate)."""
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


# ------------------------------------------------------------------ ربات
class Bot(threading.Thread):
    def __init__(self, app, cfg):
        super().__init__(daemon=True)
        self.app = app
        self.cfg = dict(cfg)
        self.stop_ev = threading.Event()
        self.T = Templates()
        self.last_best = 0.0
        self.finders = {}

    # --- کمکی‌ها
    def say(self, msg, level="info"):
        write_log(msg)
        self.app.post(self.app.on_log, msg, level)

    def stopped(self):
        if self.stop_ev.is_set():
            return True
        if IS_WIN and cursor_pos() == (0, 0):  # توقف اضطراری با گوشه صفحه
            self.stop_ev.set()
            self.say("توقف اضطراری: ماوس به گوشه صفحه رفت", "warn")
            return True
        return False

    def sleep(self, t):
        end = time.perf_counter() + t
        while time.perf_counter() < end:
            if self.stopped():
                return True
            time.sleep(min(0.02, max(0.0, end - time.perf_counter())))
        return False

    def grab(self):
        return np.ascontiguousarray(np.asarray(self.sct.grab(self.mon))[:, :, :3])

    def center(self, loc, tpl):
        h, w = tpl.shape[:2]
        return (self.mon["left"] + loc[0] + w / 2, self.mon["top"] + loc[1] + h / 2)

    def eligible_badge(self, frame, fb, badge):
        """آیکون‌هایی را که کنار تصویر مستثنی نیستند پیدا می‌کند و بهترینشان را برمی‌گرداند (یا None)."""
        cfg = self.cfg
        cands = all_matches(frame, badge, cfg["threshold"])
        if not cands:
            return None
        ratio = fb.hint / fb.base
        boxes = []
        for i, raw in enumerate(self.excl):
            for m in EXCL_MULTS:
                sc_e = ratio * m
                k = (i, round(sc_e, 3))
                if k not in self.excl_cache:
                    self.excl_cache[k] = raw if abs(sc_e - 1.0) < 0.01 else cv2.resize(
                        raw, None, fx=sc_e, fy=sc_e, interpolation=cv2.INTER_CUBIC if sc_e > 1 else cv2.INTER_AREA)
                t = self.excl_cache[k]
                if min(t.shape[:2]) < 8:
                    continue
                for _, l in all_matches(frame, t, cfg["btn_threshold"]):
                    boxes.append((l[0], l[1], l[0] + t.shape[1], l[1] + t.shape[0]))
        radius = cfg["exclude_radius"] * fb.hint
        bh, bw = badge.shape[:2]
        for sc, loc in sorted(cands, reverse=True):
            cx, cy = loc[0] + bw / 2, loc[1] + bh / 2
            blocked = False
            for x0, y0, x1, y1 in boxes:
                inside = x0 <= cx <= x1 and y0 <= cy <= y1
                near = math.hypot(cx - (x0 + x1) / 2, cy - (y0 + y1) / 2) <= radius
                if inside or near:
                    blocked = True
                    break
            if not blocked:
                return sc, loc
        return None

    def finder(self, key):
        if key not in self.finders:
            raw, custom = self.T.load_raw(key)
            self.finders[key] = Finder(raw, 1.0 if custom else self.s)
        return self.finders[key]

    def find_now(self, key):
        thr = self.cfg["btn_threshold"]
        sc, loc, tpl = self.finder(key).match(self.grab(), thr)
        return (self.center(loc, tpl), sc) if sc >= thr else (None, sc)

    def wait_for(self, key, timeout):
        end = time.perf_counter() + timeout
        best = 0.0
        while time.perf_counter() < end and not self.stopped():
            pt, sc = self.find_now(key)
            best = max(best, sc)
            if pt:
                return pt, sc
            time.sleep(0.02)
        self.last_best = best
        return None, best

    def wait_for_tpl(self, fd, timeout):
        """مثل wait_for ولی برای تصویر دلخواه کاربر (در چند اندازه)."""
        end = time.perf_counter() + timeout
        best = 0.0
        thr = self.cfg["btn_threshold"]
        while time.perf_counter() < end and not self.stopped():
            sc, loc, tpl = fd.match(self.grab(), thr)
            best = max(best, sc)
            if sc >= thr:
                return self.center(loc, tpl), sc
            time.sleep(0.02)
        self.last_best = best
        return None, best

    def save_debug(self, name):
        try:
            imwrite(base_dir() / "debug" / name, self.grab())
        except Exception:
            pass

    def run(self):
        try:
            self._main()
        except Exception:
            self.say("خطای برنامه:\n" + traceback.format_exc(), "error")
            self.app.post(self.app.on_finish, "error")

    # --- حلقه اصلی
    def _main(self):
        import mss
        cfg = self.cfg
        with mss.mss() as sct:
            self.sct = sct
            mi = mon_index(sct, cfg.get("monitor", 1))
            self.mon = sct.monitors[mi]
            if mi > 1:
                self.say(f"رصد روی صفحه‌نمایش شماره {mi}")
            t_sched = parse_hhmm(cfg.get("start_at", ""))
            if t_sched:
                lt = time.localtime()
                target = time.mktime(lt[:3] + (t_sched[0], t_sched[1], 0, 0, 0, -1))
                remain = target - time.time()
                if remain <= 0:
                    self.say("ساعت شروع امروز گذشته؛ همین حالا شروع می‌شود", "warn")
                else:
                    self.say(f"شروع خودکار ساعت {t_sched[0]:02d}:{t_sched[1]:02d}. مرورگر را آماده و فعال نگه دارید")
                    while remain > 0:
                        if self.stopped():
                            self.app.post(self.app.on_finish, "stop")
                            return
                        h, rem = divmod(int(remain), 3600)
                        m, sec = divmod(rem, 60)
                        self.app.post(self.app.on_status, f"شروع تا {h:02d}:{m:02d}:{sec:02d} دیگر", "wait")
                        time.sleep(min(0.5, max(0.0, remain)))
                        remain = target - time.time()
            self.app.post(self.app.on_status, "۳ ثانیه فرصت دارید پنجره مرورگر را فعال کنید", "wait")
            if self.sleep(3):
                self.app.post(self.app.on_finish, "stop")
                return

            # مقیاس
            s = float(cfg["scale"])
            if s <= 0:
                sc, sv, _, _ = calibrate(self.grab(), self.T)
                if sc >= 0.55:
                    s = sv
                    self.say(f"مقیاس خودکار: {s:.2f} (امتیاز {sc:.2f})")
                else:
                    s = self.mon["width"] / REF_WIDTH
                    self.say(f"مقیاس خودکار پیدا نشد، حدس بر اساس عرض صفحه: {s:.2f}", "warn")
            else:
                self.say(f"مقیاس ثابت: {s:.2f}")
            self.s = s

            fb = self.finder("badge")
            self.excl, self.excl_cache = [], {}
            for x in cfg.get("exclude_images", []):
                pth = base_dir() / "templates_user" / x["file"]
                img = imread(pth) if pth.exists() else None
                if img is not None:
                    self.excl.append(img)
            if self.excl:
                self.say(f"{len(self.excl)} تصویر مستثنی فعال است؛ آیکونِ کنار آن‌ها نادیده گرفته می‌شود")
            self.app.post(self.app.on_status, "در حال رصد صفحه", "run")
            last_refresh = -1e9
            warned = -1e9
            hits = 0
            refreshes = 0
            t_start = time.perf_counter()
            last_stat = 0.0
            sc = 0.0
            last_skip = skip_until = 0.0
            switched = False
            while not self.stopped():
                now = time.perf_counter()
                if cfg.get("max_minutes", 0) > 0 and now - t_start > cfg["max_minutes"] * 60:
                    self.say(f"زمان تعیین‌شده ({cfg['max_minutes']} دقیقه) تمام شد؛ رصد متوقف شد", "warn")
                    announce("fail", "Time limit reached.", cfg)
                    break
                interval = cfg["interval"]
                ah = float(cfg.get("interval_after_hours", 0) or 0)
                if ah > 0 and now - t_start >= ah * 3600:
                    interval = float(cfg.get("interval_after", interval))
                    if not switched:
                        switched = True
                        self.say(f"بعد از {ah:g} ساعت، فاصله رفرش از {cfg['interval']:g} به {interval:g} ثانیه تغییر کرد", "ok")
                if now - last_refresh >= interval:
                    if foreground_is_me():
                        if now - warned > 5:
                            self.say("پنجره مرورگر فعال نیست؛ رفرش انجام نشد", "warn")
                            warned = now
                    else:
                        press_key(0x74)  # F5
                        refreshes += 1
                    last_refresh = now
                frame = self.grab()
                sc, loc, badge = fb.match(frame, cfg["threshold"])
                ok = sc >= cfg["threshold"]
                if ok and self.excl:
                    if now < skip_until:
                        ok = False
                    else:
                        pick = self.eligible_badge(frame, fb, badge)
                        if pick:
                            sc, loc = pick
                        else:
                            ok = False
                            skip_until = now + 0.15
                            if now - last_skip > 3:
                                self.say("آیکون حراج‌جمعه کنار تصویر مستثنی بود؛ نادیده گرفته شد", "warn")
                                last_skip = now
                hits = hits + 1 if ok else 0
                if now - last_stat > 0.25:
                    self.app.post(self.app.on_stats, refreshes, now - t_start, sc)
                    last_stat = now
                if hits >= cfg["confirm"]:
                    self.sequence(loc, badge, sc)
                    return
                time.sleep(0.005)
            self.say(f"پایان رصد: {refreshes} رفرش در {int(time.perf_counter() - t_start)} ثانیه")
            self.app.post(self.app.on_finish, "stop")

    # --- مسیر کلیک‌ها
    def sequence(self, loc, badge, sc):
        cfg, s = self.cfg, self.s
        self.app.post(self.app.on_hit)
        announce("hit", "Big discount spotted.", cfg)
        t0 = time.perf_counter()
        frame = self.grab() if cfg.get("shot_on_hit", True) else None
        cx, cy = self.center(loc, badge)
        self.say(f"آیکون حراج‌جمعه دیده شد (شباهت {sc:.2f}). کلیک روی محصول", "hit")
        click(cx, cy)
        if cfg.get("brightness_on_hit", True):
            max_brightness()  # روشنایی صفحه تا آخر زیاد بماند
        if frame is not None:
            self.save_hit_shot(frame, loc, badge, cx, cy)

        # --- بقیه مراحل: ترتیب مهم نیست. هر مرحله هر وقت دیده شد کلیک می‌شود؛
        # مرحله‌ای که دیده نشود بعد از مهلت رد می‌شود و بقیه ادامه پیدا می‌کنند.
        thr, limit = cfg["btn_threshold"], cfg["step_timeout"]
        pending = [("افزودن به سبد خرید", self.finder("add"), "add"),
                   ("آیکون سبد خرید", self.finder("cart"), "cart")]
        for i, st in enumerate(cfg.get("extra_steps", []), 1):
            name = st.get("name", f"مرحله {i}")
            path = base_dir() / "templates_user" / st["file"]
            tpl = imread(path) if path.exists() else None
            if tpl is None:
                self.say(f"تصویر مرحله «{name}» پیدا نشد؛ این مرحله رد شد", "warn")
                continue
            pending.append((name, Finder(tpl, 1.0), None))
        add_fd = pending[0][1]
        done, skipped, done_keys = [], [], set()
        t_start = t_prog = time.perf_counter()
        t_add, tries = None, 0
        while pending:
            if self.stopped():
                self.app.post(self.app.on_finish, "stop")
                return
            frame = self.grab()
            now = time.perf_counter()
            clicked = False
            for item in pending:  # اگر چند تصویر هم‌زمان دیده شوند، ترتیب فهرست حکم می‌کند
                name, fd, key = item
                sc3, loc3, t3 = fd.match(frame, thr)
                if sc3 >= thr:
                    if key == "add":
                        self.say(f"صفحه محصول باز شد ({now - t0:.2f} ثانیه). کلیک روی «{name}»", "ok")
                        t_add = now
                    else:
                        self.say(f"کلیک روی «{name}»", "ok")
                    click(*self.center(loc3, t3))
                    pending.remove(item)
                    done.append(name)
                    done_keys.add(key)
                    t_prog = now
                    clicked = True
                    break
            if clicked:
                if self.sleep(0.5):
                    self.app.post(self.app.on_finish, "stop")
                    return
                continue
            # کلیک اول «افزودن» احتمالاً ثبت نشده: اگر هنوز دیده می‌شود دوباره بزن
            if t_add and tries < 2 and "cart" not in done_keys and now - t_add > 2.5:
                sc4, loc4, t4 = add_fd.match(frame, thr)
                if sc4 >= thr:
                    tries += 1
                    self.say("دکمه افزودن هنوز هست؛ دوباره کلیک می‌کنم", "warn")
                    click(*self.center(loc4, t4))
                    t_add = t_prog = time.perf_counter()
                    continue
            if not done and now - t_start > limit * 2:  # صفحه محصول احتمالاً باز نشده
                self.save_debug("no_steps_found.png")
                return self.fail("بعد از کلیک روی محصول هیچ‌کدام از مراحل دیده نشد")
            if now - t_prog > limit:  # قدیمی‌ترین مرحله‌ی منتظر را رد کن و ادامه بده
                name, _fd, _key = pending.pop(0)
                skipped.append(name)
                self.say(f"«{name}» دیده نشد؛ رد شد", "warn")
                self.save_debug(f"skipped_{len(skipped)}.png")
                t_prog = time.perf_counter()
            time.sleep(0.02)
        total = time.perf_counter() - t0
        if skipped:
            self.say(f"{len(done)} مرحله انجام شد ({total:.2f} ثانیه). رد شده: " + "، ".join(skipped), "warn")
            announce("done", "Done. Some steps were skipped.", cfg)
        else:
            self.say(f"همه مراحل انجام شد ({total:.2f} ثانیه از دیدن آیکون). بقیه با شماست", "hit")
            announce("done", "All steps completed successfully.", cfg)
        self.app.post(self.app.on_finish, "done")

    def save_hit_shot(self, frame, loc, badge, cx, cy):
        """عکس صفحه با کادر دور آیکون و نقطه کلیک را در پوشه debug ذخیره می‌کند."""
        try:
            h, w = badge.shape[:2]
            x, y = int(loc[0]), int(loc[1])
            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 0, 255), 3)
            cv2.circle(frame, (int(cx - self.mon["left"]), int(cy - self.mon["top"])), 14, (0, 200, 0), 3)
            path = base_dir() / "debug" / time.strftime("hit_%Y%m%d_%H%M%S.png")
            threading.Thread(target=imwrite, args=(path, frame), daemon=True).start()
            self.say(f"عکس صفحه ذخیره شد: debug/{path.name}")
        except Exception:
            pass

    def fail(self, msg):
        self.say(msg + ". کنترل با شماست", "error")
        announce("fail", "A step failed. Please check the screen.", self.cfg)
        self.app.post(self.app.on_finish, "fail")


# ------------------------------------------------------------------ هاله قرمز
class Overlay:
    KEY = "#010203"

    def __init__(self, root):
        self.root = root
        self.win = None
        self.job = None
        self.on = False

    def show(self, text, steady=False):
        self.hide()
        w = tk.Toplevel(self.root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w.geometry(f"{sw}x{sh}+0+0")
        w.configure(bg=self.KEY)
        w.attributes("-transparentcolor", self.KEY)
        cv = tk.Canvas(w, bg=self.KEY, highlightthickness=0)
        cv.pack(fill="both", expand=True)
        self.rect = cv.create_rectangle(8, 8, sw - 8, sh - 8, outline="#e5262b", width=16)
        cv.create_rectangle(sw / 2 - 260, 16, sw / 2 + 260, 62, fill="#e5262b", outline="")
        cv.create_text(sw / 2, 39, text=text, fill="white", font=("Tahoma", 16, "bold"))
        self.cv, self.win = cv, w
        w.update_idletasks()
        if IS_WIN:  # شفاف برای کلیک: ماوس از روی هاله رد می‌شود
            hwnd = user32.GetParent(w.winfo_id()) or w.winfo_id()
            ex = user32.GetWindowLongW(hwnd, -20)
            user32.SetWindowLongW(hwnd, -20, ex | 0x80000 | 0x20 | 0x80)
        if not steady:
            self._pulse()

    def _pulse(self):
        if not self.win:
            return
        self.on = not self.on
        self.cv.itemconfigure(self.rect, width=22 if self.on else 12)
        self.job = self.root.after(350, self._pulse)

    def hide(self):
        if self.job:
            self.root.after_cancel(self.job)
            self.job = None
        if self.win:
            self.win.destroy()
            self.win = None


# ------------------------------------------------------------------ رابط کاربری
class App:
    def __init__(self):
        if tk is None or not IS_WIN:
            raise SystemExit("این برنامه فقط روی ویندوز اجرا می‌شود.")
        self.cfg = load_config()
        self.cfg["exclude_images"] = [
            {"name": str(x.get("name") or x["file"]), "file": str(x["file"])}
            for x in self.cfg.get("exclude_images", []) if isinstance(x, dict) and x.get("file")]
        self.cfg["extra_steps"] = [
            {"name": str(x.get("name") or x["file"]), "file": str(x["file"])}
            for x in self.cfg.get("extra_steps", []) if isinstance(x, dict) and x.get("file")]
        self.q = queue.Queue()
        self.bot = None
        self._f8 = False
        self.sleep_deadline = None
        self._sleep_job = None
        self._sleep_warned = False
        r = self.root = tk.Tk()
        r.title(APP_TITLE)
        r.geometry("480x700")
        r.minsize(440, 620)
        self.overlay = Overlay(r)
        self.f = ("Tahoma", 10)
        self.build()
        r.attributes("-topmost", bool(self.cfg["always_on_top"]))
        r.after(30, self.pump)
        r.after(40, self.poll_hotkey)
        r.protocol("WM_DELETE_WINDOW", self.close)

    # --- ساخت پنجره
    def build(self):
        r, f = self.root, self.f
        pad = {"padx": 12, "pady": 4}
        self.status = tk.Label(r, text="آماده", font=("Tahoma", 14, "bold"), bg="#eeeeee",
                               fg="#222", pady=10)
        self.status.pack(fill="x", padx=12, pady=(12, 6))

        row = tk.Frame(r)
        row.pack(fill="x", **pad)
        self.btn = tk.Button(row, text="شروع  (F8)", font=("Tahoma", 12, "bold"), bg="#8e1a9a",
                             fg="white", relief="flat", command=self.toggle, pady=6)
        self.btn.pack(side="right", expand=True, fill="x", padx=(6, 0))
        tk.Button(row, text="تشخیص مقیاس", font=f, command=self.do_calibrate,
                  pady=6).pack(side="left", expand=True, fill="x")

        # پایین پنجره (اول pack می‌شود تا همیشه دیده شود)
        self.log = tk.Text(r, height=5, font=("Tahoma", 9), state="disabled", wrap="word")
        self.log.pack(side="bottom", fill="x", padx=12, pady=(4, 12))
        self.log.tag_configure("hit", foreground="#c0151a")
        self.log.tag_configure("ok", foreground="#17803d")
        self.log.tag_configure("warn", foreground="#b36b00")
        self.log.tag_configure("error", foreground="#c0151a")
        tk.Button(r, text="خاموش کردن هاله قرمز", font=f, command=self.overlay.hide).pack(
            side="bottom", fill="x", padx=12, pady=4)
        self.stats = tk.Label(r, text="رفرش: ۰   زمان: ۰   شباهت: -", font=f, anchor="e")
        self.stats.pack(side="bottom", fill="x", padx=12)

        nb = ttk.Notebook(r)
        nb.pack(fill="both", expand=True, **pad)
        t_set, t_img, t_exc, t_sleep, t_ref = (tk.Frame(nb) for _ in range(5))
        nb.add(t_set, text="  تنظیمات  ")
        nb.add(t_img, text="  تصاویر  ")
        nb.add(t_exc, text="  مستثنی  ")
        nb.add(t_sleep, text="  خواب  ")
        nb.add(t_ref, text="  رفرش  ")

        # ---------------- تب تنظیمات
        prow = tk.Frame(t_set)
        prow.pack(fill="x", **pad)
        tk.Label(prow, text="پروفایل:", font=f).pack(side="right")
        self.prof_var = tk.StringVar()
        self.prof_box = ttk.Combobox(prow, textvariable=self.prof_var, state="readonly", font=f, width=16)
        self.prof_box.pack(side="right", padx=6)
        tk.Button(prow, text="ذخیره", font=f, command=self.save_profile).pack(side="left")
        tk.Button(prow, text="حذف", font=f, command=self.delete_profile).pack(side="left", padx=(0, 4))
        tk.Button(prow, text="بارگذاری", font=f, command=self.load_profile).pack(side="left", padx=(0, 4))
        self.refresh_profiles()

        box = tk.LabelFrame(t_set, text=" تنظیمات ", font=f, labelanchor="ne")
        box.pack(fill="x", **pad)
        self.v_interval = tk.DoubleVar(value=self.cfg["interval"])
        self.v_thr = tk.DoubleVar(value=self.cfg["threshold"])
        self.v_beep = tk.BooleanVar(value=self.cfg["beep"])
        self.v_halo = tk.BooleanVar(value=self.cfg["halo"])
        self.v_speak = tk.BooleanVar(value=self.cfg["speak"])
        self.v_monitor = tk.IntVar(value=self.cfg["monitor"])
        self.v_maxmin = tk.IntVar(value=self.cfg["max_minutes"])
        self.v_exr = tk.IntVar(value=self.cfg["exclude_radius"])
        self.v_beepsec = tk.IntVar(value=self.cfg["beep_seconds"])
        self.v_start = tk.StringVar(value=self.cfg["start_at"])
        self.v_shot = tk.BooleanVar(value=self.cfg["shot_on_hit"])
        self.v_top = tk.BooleanVar(value=self.cfg["always_on_top"])
        self.v_sleep_at = tk.StringVar(value=self.cfg.get("sleep_at", ""))
        self.v_sleep_hours = tk.StringVar(value=self.cfg.get("sleep_hours", 0))
        self.v_int_after_h = tk.StringVar(value=self.cfg.get("interval_after_hours", 0))
        self.v_int_after = tk.DoubleVar(value=self.cfg.get("interval_after", 5.0))
        rows = [
            ("فاصله رفرش (ثانیه)", tk.Spinbox(box, from_=1.0, to=15.0, increment=0.5, width=7,
                                             textvariable=self.v_interval, font=f)),
            ("حداقل شباهت آیکون (۰٫۶ تا ۰٫۹۵)", tk.Spinbox(box, from_=0.6, to=0.95, increment=0.01,
                                                           width=7, textvariable=self.v_thr, font=f)),
            ("شماره صفحه‌نمایش (۱ = اصلی)", tk.Spinbox(box, from_=1, to=6, increment=1, width=7,
                                                     textvariable=self.v_monitor, font=f)),
            ("توقف خودکار بعد از (دقیقه، ۰ = نامحدود)", tk.Spinbox(box, from_=0, to=600, increment=5,
                                                                  width=7, textvariable=self.v_maxmin, font=f)),
            ("فاصله‌ی تصویر مستثنی از آیکون (پیکسل)", tk.Spinbox(box, from_=0, to=2000, increment=25, width=7,
                                                              textvariable=self.v_exr, font=f)),
            ("شروع خودکار در ساعت (مثلاً 20:00)", tk.Entry(box, width=9, textvariable=self.v_start,
                                                          font=f, justify="center")),
        ]
        for i, (txt, w) in enumerate(rows):
            tk.Label(box, text=txt, font=f, anchor="e").grid(row=i, column=1, sticky="e", padx=8, pady=3)
            w.grid(row=i, column=0, sticky="w", padx=8)
        box.columnconfigure(1, weight=1)
        hf = tk.Frame(box)
        hf.grid(row=6, column=1, sticky="e", padx=8)
        tk.Checkbutton(hf, text="هاله قرمز", variable=self.v_halo, font=f).pack(side="right")
        tk.Checkbutton(hf, text="اعلام صوتی (انگلیسی)", variable=self.v_speak, font=f).pack(side="right", padx=(0, 10))
        bf = tk.Frame(box)
        bf.grid(row=7, column=1, sticky="e", padx=8)
        tk.Checkbutton(bf, text="صدای هشدار", variable=self.v_beep, font=f).pack(side="right")
        tk.Label(bf, text="ثانیه", font=f).pack(side="left")
        tk.Spinbox(bf, from_=1, to=30, increment=1, width=4, textvariable=self.v_beepsec,
                   font=f).pack(side="left", padx=4)
        tk.Label(bf, text="مدت:", font=f).pack(side="left")
        tk.Checkbutton(box, text="ذخیره عکس صفحه لحظه دیدن آیکون", variable=self.v_shot, font=f).grid(
            row=8, column=1, sticky="e", padx=8)
        tk.Checkbutton(box, text="پنجره ربات همیشه روی بقیه", variable=self.v_top, font=f,
                       command=lambda: self.root.attributes("-topmost", bool(self.v_top.get()))).grid(
            row=9, column=1, sticky="e", padx=8, pady=(0, 4))

        # ---------------- تب تصاویر و مراحل
        adv = tk.LabelFrame(t_img, text=" تصویر هدف خودتان (اختیاری) ", font=f, labelanchor="ne")
        adv.pack(fill="x", **pad)
        for key, label in CAPTURE_LABELS.items():
            tk.Button(adv, text="ثبت: " + label, font=f,
                      command=lambda k=key: self.capture(k)).pack(fill="x", padx=8, pady=2)
        tk.Button(adv, text="حذف تصاویر ثبت‌شده و برگشت به تصاویر پیش‌فرض", font=f,
                  command=self.clear_custom).pack(fill="x", padx=8, pady=(2, 6))

        stp = tk.LabelFrame(t_img, text=" مراحل اضافه (بعد از آیکون سبد، به ترتیب) ", font=f, labelanchor="ne")
        stp.pack(fill="x", **pad)
        self.steps_list = tk.Listbox(stp, height=4, font=f, activestyle="none", exportselection=False)
        self.steps_list.pack(fill="x", padx=8, pady=(4, 2))
        brow = tk.Frame(stp)
        brow.pack(fill="x", padx=8, pady=(0, 6))
        tk.Button(brow, text="+ مرحله", font=f, command=self.add_step).pack(
            side="right", expand=True, fill="x", padx=(4, 0))
        tk.Button(brow, text="از فایل", font=f, command=self.add_step_file).pack(side="right", padx=(4, 0))
        tk.Button(brow, text="تعویض تصویر", font=f, command=self.replace_step).pack(side="right", padx=(4, 0))
        tk.Button(brow, text="حذف", font=f, command=self.delete_step).pack(side="right", padx=(4, 0))
        tk.Button(brow, text="▲", font=f, width=3, command=lambda: self.move_step(-1)).pack(side="left")
        tk.Button(brow, text="▼", font=f, width=3, command=lambda: self.move_step(1)).pack(side="left")
        self.refresh_steps()

        # ---------------- تب مستثنی
        exf = tk.LabelFrame(t_exc, text=" تصاویر مستثنی (آیکونِ کنار این‌ها نادیده گرفته شود) ", font=f,
                            labelanchor="ne")
        exf.pack(fill="both", expand=True, **pad)
        tk.Label(exf, text="تصویری را (مثلاً برچسب «ناموجود») ثبت کنید؛ هر آیکون حراج‌جمعه که نزدیک آن باشد "
                           "نادیده گرفته می‌شود. برای تعویض، آن را از فهرست انتخاب کنید و «تعویض تصویر» را بزنید.",
                 font=f, justify="right", wraplength=400, anchor="e").pack(fill="x", padx=8, pady=(6, 2))
        self.excl_list = tk.Listbox(exf, height=5, font=f, activestyle="none", exportselection=False)
        self.excl_list.pack(fill="x", padx=8, pady=(4, 2))
        self.excl_list.bind("<<ListboxSelect>>", lambda e: self.show_excl_preview())
        pv = tk.Frame(exf, height=170, relief="groove", bd=1)
        pv.pack(fill="x", padx=8, pady=4)
        pv.pack_propagate(False)
        self.excl_prev = tk.Label(pv, text=EXCL_HINT, font=f)
        self.excl_prev.pack(expand=True)
        erow = tk.Frame(exf)
        erow.pack(fill="x", padx=8, pady=(0, 8))
        tk.Button(erow, text="+ ثبت از روی صفحه", font=f, command=self.add_excl).pack(
            side="right", expand=True, fill="x", padx=(4, 0))
        tk.Button(erow, text="از فایل", font=f, command=self.add_excl_file).pack(side="right", padx=(4, 0))
        tk.Button(erow, text="تعویض تصویر", font=f, command=self.replace_excl).pack(side="right", padx=(4, 0))
        tk.Button(erow, text="حذف", font=f, command=self.delete_excl).pack(side="right", padx=(4, 0))
        self.refresh_excl()

        # ---------------- تب خواب
        sl = tk.LabelFrame(t_sleep, text=" خواب کامپیوتر ", font=f, labelanchor="ne")
        sl.pack(fill="x", **pad)
        tk.Label(sl, text="در ساعت تعیین‌شده یا چند ساعت دیگر، ربات و همه‌ی کارها متوقف می‌شوند و کامپیوتر "
                          "به حالت Sleep می‌رود. اگر هر دو پر باشند، هرکدام زودتر برسد. "
                          "۳۰ ثانیه قبل از خواب یک بوق هشدار می‌زند.",
                 font=f, justify="right", wraplength=400, anchor="e").pack(fill="x", padx=8, pady=6)
        g = tk.Frame(sl)
        g.pack(fill="x", padx=8)
        tk.Label(g, text="رفتن به خواب در ساعت (مثلاً 02:30)", font=f, anchor="e").grid(
            row=0, column=1, sticky="e", padx=8, pady=3)
        tk.Entry(g, width=9, textvariable=self.v_sleep_at, font=f, justify="center").grid(
            row=0, column=0, sticky="w", padx=8)
        tk.Label(g, text="یا چند ساعت دیگر (مثلاً 1٫5)", font=f, anchor="e").grid(
            row=1, column=1, sticky="e", padx=8, pady=3)
        tk.Spinbox(g, from_=0, to=48, increment=0.5, width=7, textvariable=self.v_sleep_hours,
                   font=f).grid(row=1, column=0, sticky="w", padx=8)
        g.columnconfigure(1, weight=1)
        self.sleep_btn = tk.Button(sl, text="فعال‌سازی زمان‌بندی خواب", font=("Tahoma", 11, "bold"),
                                   command=self.toggle_sleep, pady=6)
        self.sleep_btn.pack(fill="x", padx=8, pady=(8, 4))
        self._sleep_btn_style = (self.sleep_btn.cget("bg"), self.sleep_btn.cget("fg"))
        self.sleep_info = tk.Label(sl, text="زمان‌بندی خواب غیرفعال است", font=f, anchor="e")
        self.sleep_info.pack(fill="x", padx=8, pady=(0, 4))
        tk.Button(sl, text="همین حالا: توقف همه‌چیز و رفتن به خواب", font=f,
                  command=self.sleep_now).pack(fill="x", padx=8, pady=(0, 8))

        # ---------------- تب رفرش
        rf = tk.LabelFrame(t_ref, text=" تغییر فاصله رفرش بعد از چند ساعت ", font=f, labelanchor="ne")
        rf.pack(fill="x", **pad)
        tk.Label(rf, text="مثلاً ۲ ساعت بعد از شروع رصد، فاصله رفرش از مقدار تب «تنظیمات» به مقدار جدید تغییر کند "
                          "(رفرش‌ها کمتر یا بیشتر شوند). زمان از لحظه‌ی شروع رصد حساب می‌شود. ۰ = غیرفعال.",
                 font=f, justify="right", wraplength=400, anchor="e").pack(fill="x", padx=8, pady=6)
        g2 = tk.Frame(rf)
        g2.pack(fill="x", padx=8, pady=(0, 8))
        tk.Label(g2, text="بعد از چند ساعت (مثلاً 2 یا 1٫5)", font=f, anchor="e").grid(
            row=0, column=1, sticky="e", padx=8, pady=3)
        tk.Spinbox(g2, from_=0, to=48, increment=0.5, width=7, textvariable=self.v_int_after_h,
                   font=f).grid(row=0, column=0, sticky="w", padx=8)
        tk.Label(g2, text="فاصله رفرش جدید (ثانیه)", font=f, anchor="e").grid(
            row=1, column=1, sticky="e", padx=8, pady=3)
        tk.Spinbox(g2, from_=1.0, to=60.0, increment=0.5, width=7, textvariable=self.v_int_after,
                   font=f).grid(row=1, column=0, sticky="w", padx=8)
        g2.columnconfigure(1, weight=1)

    # --- ارتباط با ربات
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

    def on_status(self, text, kind="idle"):
        colors = {"idle": ("#eeeeee", "#222"), "wait": ("#fff4cf", "#7a5600"),
                  "run": ("#dff3e6", "#17803d"), "hit": ("#fdeaea", "#c0151a")}
        bg, fg = colors.get(kind, colors["idle"])
        self.status.configure(text=text, bg=bg, fg=fg)

    def on_stats(self, refreshes, elapsed, score):
        self.stats.configure(text=f"رفرش: {refreshes}   زمان: {elapsed:.0f} ثانیه   شباهت: {score:.2f}")

    def on_hit(self):
        self.on_status("حراج‌جمعه پیدا شد! در حال انجام مراحل", "hit")
        if self.cfg["halo"]:
            self.overlay.show("حراج‌جمعه پیدا شد!")

    def on_finish(self, kind):
        self.bot = None
        self.btn.configure(text="شروع  (F8)", bg="#8e1a9a")
        if kind == "done":
            self.on_status("به سبد خرید رسید. بقیه با شماست", "hit")
            if self.cfg["halo"]:
                self.overlay.show("ربات متوقف شد. ادامه با شماست", steady=True)
        elif kind == "fail":
            self.on_status("مشکل پیش آمد. کنترل با شماست", "hit")
            if self.cfg["halo"]:
                self.overlay.show("مشکل پیش آمد. ادامه با شماست", steady=True)
        elif kind == "error":
            self.on_status("خطا. جزئیات در گزارش پایین", "hit")
        else:
            self.on_status("متوقف شد", "idle")
            self.overlay.hide()
        if kind == "error":  # برای موفقیت یا مشکل مرحله‌ها فقط بوق می‌زند؛ پنجره جلو نمی‌آید
            self.root.deiconify()

    # --- کنترل
    def read_settings(self):
        try:
            self.cfg["interval"] = max(1.0, float(self.v_interval.get()))
            self.cfg["threshold"] = min(0.95, max(0.5, float(self.v_thr.get())))
            self.cfg["monitor"] = max(1, int(self.v_monitor.get()))
            self.cfg["max_minutes"] = max(0, int(self.v_maxmin.get()))
            self.cfg["exclude_radius"] = max(0, int(self.v_exr.get()))
            self.cfg["beep_seconds"] = min(30, max(1, int(self.v_beepsec.get())))
            self.cfg["interval_after"] = max(1.0, float(self.v_int_after.get()))
        except (tk.TclError, ValueError):
            messagebox.showerror(APP_TITLE, "یکی از تنظیمات عدد معتبر نیست.")
            return False
        self.cfg["beep"] = bool(self.v_beep.get())
        self.cfg["halo"] = bool(self.v_halo.get())
        self.cfg["speak"] = bool(self.v_speak.get())
        self.cfg["shot_on_hit"] = bool(self.v_shot.get())
        self.cfg["always_on_top"] = bool(self.v_top.get())
        st = self.v_start.get().strip()
        if st and not parse_hhmm(st):
            messagebox.showerror(APP_TITLE, "ساعت شروع باید به شکل 20:00 باشد (یا خالی بماند).")
            return False
        self.cfg["start_at"] = st
        sa = self.v_sleep_at.get().strip()
        if sa and not parse_hhmm(sa):
            messagebox.showerror(APP_TITLE, "ساعت خواب باید به شکل 02:30 باشد (یا خالی بماند).")
            return False
        try:
            sh = float(str(self.v_sleep_hours.get()).strip().replace("٫", ".").replace(",", ".") or 0)
            if sh < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror(APP_TITLE, "تعداد ساعتِ خواب عدد معتبر نیست.")
            return False
        self.cfg["sleep_at"] = sa
        self.cfg["sleep_hours"] = sh
        try:
            ih = float(str(self.v_int_after_h.get()).strip().replace("٫", ".").replace(",", ".") or 0)
            if ih < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror(APP_TITLE, "تعداد ساعتِ تغییر رفرش عدد معتبر نیست.")
            return False
        self.cfg["interval_after_hours"] = ih
        save_config(self.cfg)
        return True

    def toggle(self):
        if self.bot:
            self.bot.stop_ev.set()
            return
        if not self.read_settings():
            return
        self.overlay.hide()
        if self.cfg.get("speak", True):
            prewarm_speech()
        self.bot = Bot(self, self.cfg)
        self.btn.configure(text="توقف  (F8)", bg="#c0151a")
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.root.iconify()  # تا کلید F5 به مرورگر برسد، نه به این پنجره
        self.bot.start()

    def poll_hotkey(self):
        down = bool(user32.GetAsyncKeyState(0x77) & 0x8000)  # F8
        if down and not self._f8:
            self.toggle()
        self._f8 = down
        self.root.after(40, self.poll_hotkey)

    def do_calibrate(self):
        import mss
        self.root.iconify()
        self.root.update()
        time.sleep(1.2)
        with mss.mss() as s:
            frame = np.ascontiguousarray(np.asarray(s.grab(s.monitors[mon_index(s, self.v_monitor.get())]))[:, :, :3])
        sc, sv, loc, shp = calibrate(frame, Templates())
        self.root.deiconify()
        if sc >= 0.55:
            self.cfg["scale"] = round(sv, 2)
            save_config(self.cfg)
            dbg = frame.copy()
            cv2.rectangle(dbg, loc, (loc[0] + shp[1], loc[1] + shp[0]), (0, 0, 255), 3)
            imwrite(base_dir() / "debug" / "calibration.png", dbg)
            self.on_log(f"مقیاس {sv:.2f} ذخیره شد (امتیاز {sc:.2f})", "ok")
            messagebox.showinfo(APP_TITLE, f"مقیاس شناسایی شد: {sv:.2f}\nامتیاز: {sc:.2f}")
        else:
            self.on_log(f"تشخیص مقیاس ناموفق بود (بهترین امتیاز {sc:.2f})", "warn")
            messagebox.showwarning(
                APP_TITLE,
                "پنل «فیلترها» در صفحه پیدا نشد.\nصفحه لیست محصولات را تمام‌صفحه باز بگذارید "
                "و دوباره امتحان کنید. تا آن موقع مقیاس خودکار حدس زده می‌شود.")

    def capture(self, key, label=None, filename=None, on_saved=None):
        """یک ناحیه از صفحه را با ماوس انتخاب می‌کند و به‌عنوان تصویر هدف ذخیره می‌کند."""
        import mss
        from PIL import Image, ImageTk
        label = label or CAPTURE_LABELS[key]
        filename = filename or TEMPLATES[key]
        self.root.withdraw()
        self.root.update()
        time.sleep(0.5)
        with mss.mss() as s:
            try:
                mi = int(self.v_monitor.get())
            except (tk.TclError, ValueError):
                mi = self.cfg.get("monitor", 1)
            mon = s.monitors[mon_index(s, mi)]
            shot = s.grab(mon)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        top = tk.Toplevel(self.root)
        top.geometry(f"+{mon['left']}+{mon['top']}")  # روی همان صفحه‌نمایشی که ربات رصد می‌کند
        top.attributes("-fullscreen", True)
        top.attributes("-topmost", True)
        cv = tk.Canvas(top, cursor="crosshair", highlightthickness=0)
        cv.pack(fill="both", expand=True)
        top.update()
        cw, ch = cv.winfo_width(), cv.winfo_height()
        photo = ImageTk.PhotoImage(img.resize((cw, ch)) if (cw, ch) != img.size else img)
        cv.create_image(0, 0, image=photo, anchor="nw")
        cv.photo = photo
        cv.create_rectangle(cw / 2 - 300, 10, cw / 2 + 300, 50, fill="black")
        cv.create_text(cw / 2, 30, fill="white", font=("Tahoma", 13),
                       text=f"دور «{label}» یک کادر بکشید. (Esc = لغو)")
        st = {}

        def down(e):
            st["p"] = (e.x, e.y)
            st["r"] = cv.create_rectangle(e.x, e.y, e.x, e.y, outline="#ff2020", width=2)

        def move(e):
            if "r" in st:
                cv.coords(st["r"], st["p"][0], st["p"][1], e.x, e.y)

        def up(e):
            if "p" not in st:
                return
            x0, y0 = st["p"]
            kx, ky = img.width / cw, img.height / ch
            box = (int(min(x0, e.x) * kx), int(min(y0, e.y) * ky),
                   int(max(x0, e.x) * kx), int(max(y0, e.y) * ky))
            top.destroy()
            self.root.deiconify()
            if box[2] - box[0] < 8 or box[3] - box[1] < 8:
                self.on_log("ناحیه خیلی کوچک بود؛ ذخیره نشد", "warn")
                return
            arr = cv2.cvtColor(np.asarray(img.crop(box)), cv2.COLOR_RGB2BGR)
            imwrite(base_dir() / "templates_user" / filename, arr)
            self.on_log(f"تصویر «{label}» ذخیره شد ({arr.shape[1]}×{arr.shape[0]})", "ok")
            if on_saved:
                on_saved()

        def cancel(_e=None):
            top.destroy()
            self.root.deiconify()

        cv.bind("<ButtonPress-1>", down)
        cv.bind("<B1-Motion>", move)
        cv.bind("<ButtonRelease-1>", up)
        top.bind("<Escape>", cancel)
        top.focus_force()

    # --- پروفایل‌ها: ذخیره و بارگذاری کل تنظیمات + مراحل + تصاویر
    def profile_names(self):
        d = base_dir() / "profiles"
        if not d.exists():
            return []
        return sorted(x.name for x in d.iterdir() if (x / "profile.json").exists())

    def refresh_profiles(self, select=None):
        names = self.profile_names()
        self.prof_box["values"] = names
        if select in names:
            self.prof_var.set(select)
        elif self.prof_var.get() not in names:
            self.prof_var.set("")

    def _clean_lists(self):
        for k in ("exclude_images", "extra_steps"):
            self.cfg[k] = [{"name": str(x.get("name") or x["file"]), "file": str(x["file"])}
                           for x in self.cfg.get(k, []) if isinstance(x, dict) and x.get("file")]

    def apply_cfg_to_ui(self):
        c = self.cfg
        pairs = [(self.v_interval, "interval"), (self.v_thr, "threshold"), (self.v_beep, "beep"),
                 (self.v_halo, "halo"), (self.v_speak, "speak"), (self.v_monitor, "monitor"), (self.v_maxmin, "max_minutes"),
                 (self.v_exr, "exclude_radius"), (self.v_beepsec, "beep_seconds"), (self.v_start, "start_at"),
                 (self.v_shot, "shot_on_hit"), (self.v_top, "always_on_top"),
                 (self.v_sleep_at, "sleep_at"), (self.v_sleep_hours, "sleep_hours"),
                 (self.v_int_after_h, "interval_after_hours"), (self.v_int_after, "interval_after")]
        for var, key in pairs:
            try:
                var.set(c[key])
            except (tk.TclError, ValueError):
                pass
        self.root.attributes("-topmost", bool(c["always_on_top"]))
        self.refresh_steps()
        self.refresh_excl()

    def save_profile(self):
        if not self.read_settings():
            return
        name = simpledialog.askstring(APP_TITLE, "یک نام برای این پروفایل بنویسید:",
                                      initialvalue=self.prof_var.get(), parent=self.root)
        if not name:
            return
        bad = set('/:*?"<>|' + chr(92))
        name = "".join(ch for ch in name if ch not in bad).strip().strip(".")
        if not name:
            messagebox.showerror(APP_TITLE, "این نام قابل استفاده نیست.")
            return
        d = base_dir() / "profiles" / name
        if d.exists():
            if not messagebox.askyesno(APP_TITLE, f"پروفایل «{name}» قبلاً هست. جایگزین شود؟"):
                return
            shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
        (d / "profile.json").write_text(json.dumps(self.cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        files = set(TEMPLATES.values())
        files |= {x["file"] for x in self.cfg["extra_steps"]} | {x["file"] for x in self.cfg["exclude_images"]}
        n = 0
        for fn in files:
            src = base_dir() / "templates_user" / fn
            if src.exists():
                shutil.copy2(src, d / fn)
                n += 1
        self.refresh_profiles(name)
        self.on_log(f"پروفایل «{name}» ذخیره شد ({n} تصویر)", "ok")

    def load_profile(self):
        if self.bot:
            messagebox.showinfo(APP_TITLE, "اول ربات را متوقف کنید، بعد پروفایل را بارگذاری کنید.")
            return
        name = self.prof_var.get().strip()
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
        if not messagebox.askyesno(APP_TITLE, f"تنظیمات و مراحل فعلی با پروفایل «{name}» جایگزین می‌شود. ادامه؟"):
            return
        ud = base_dir() / "templates_user"
        ud.mkdir(parents=True, exist_ok=True)
        for fn in TEMPLATES.values():  # تصاویر اصلی ثبت‌شده‌ی فعلی؛ پروفایل خودش را می‌آورد
            try:
                (ud / fn).unlink()
            except OSError:
                pass
        for img in d.glob("*.png"):
            shutil.copy2(img, ud / img.name)
        new = dict(DEFAULTS)
        new.update(data if isinstance(data, dict) else {})
        self.cfg.clear()
        self.cfg.update(new)
        self._clean_lists()
        self.apply_cfg_to_ui()
        save_config(self.cfg)
        self.on_log(f"پروفایل «{name}» بارگذاری شد", "ok")

    def delete_profile(self):
        name = self.prof_var.get().strip()
        if not name:
            return
        if not messagebox.askyesno(APP_TITLE, f"پروفایل «{name}» برای همیشه حذف شود؟"):
            return
        shutil.rmtree(base_dir() / "profiles" / name, ignore_errors=True)
        self.refresh_profiles()
        self.on_log(f"پروفایل «{name}» حذف شد", "ok")

    # --- وارد کردن تصویر از فایل و تعویض تصویر
    def _import_image_file(self, filename):
        path = filedialog.askopenfilename(
            parent=self.root, title="انتخاب فایل تصویر",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.webp"), ("All files", "*.*")])
        if not path:
            return False
        img = imread(path)
        if img is None or min(img.shape[:2]) < 8:
            messagebox.showerror(APP_TITLE, "این فایل تصویر معتبری نیست یا خیلی کوچک است.")
            return False
        imwrite(base_dir() / "templates_user" / filename, img)
        self.on_log(f"تصویر از فایل وارد شد ({img.shape[1]}×{img.shape[0]})", "ok")
        return True

    def _replace_image(self, name, filename, done):
        ans = messagebox.askyesnocancel(
            APP_TITLE, f"تصویر جدید «{name}» از کجا بیاید؟\n\nبله = کادر کشیدن روی صفحه\nخیر = انتخاب فایل تصویر",
            parent=self.root)
        if ans is None:
            return
        if ans:
            self.capture(None, label=name, filename=filename, on_saved=done)
        elif self._import_image_file(filename):
            done()

    def add_step_file(self):
        name = simpledialog.askstring(APP_TITLE, "یک نام برای این مرحله بنویسید:", parent=self.root)
        if not name or not name.strip():
            return
        fname = f"step_{int(time.time())}.png"
        if self._import_image_file(fname):
            self.cfg["extra_steps"].append({"name": name.strip(), "file": fname})
            save_config(self.cfg)
            self.refresh_steps(len(self.cfg["extra_steps"]) - 1)

    def replace_step(self):
        sel = self.steps_list.curselection()
        if not sel:
            messagebox.showinfo(APP_TITLE, "اول یک مرحله را از فهرست انتخاب کنید.")
            return
        st = self.cfg["extra_steps"][sel[0]]
        self._replace_image(st["name"], st["file"],
                            lambda: self.on_log(f"تصویر مرحله «{st['name']}» تعویض شد", "ok"))

    def add_excl_file(self):
        name = simpledialog.askstring(APP_TITLE, "یک نام برای این تصویر مستثنی بنویسید:", parent=self.root)
        if not name or not name.strip():
            return
        fname = f"excl_{int(time.time())}.png"
        if self._import_image_file(fname):
            self.cfg["exclude_images"].append({"name": name.strip(), "file": fname})
            save_config(self.cfg)
            self.refresh_excl(len(self.cfg["exclude_images"]) - 1)

    def replace_excl(self):
        sel = self.excl_list.curselection()
        if not sel:
            messagebox.showinfo(APP_TITLE, "اول یک تصویر مستثنی را از فهرست انتخاب کنید.")
            return
        x = self.cfg["exclude_images"][sel[0]]
        self._replace_image(x["name"], x["file"], lambda: self._excl_replaced(x["name"]))

    def _excl_replaced(self, name):
        self.on_log(f"تصویر مستثنی «{name}» تعویض شد", "ok")
        self.show_excl_preview()

    def refresh_excl(self, select=None):
        self.excl_list.delete(0, "end")
        for i, x in enumerate(self.cfg["exclude_images"], 1):
            self.excl_list.insert("end", f"{i}.  {x['name']}")
        if select is not None and 0 <= select < self.excl_list.size():
            self.excl_list.selection_set(select)
            self.excl_list.see(select)
        self.show_excl_preview()

    def show_excl_preview(self):
        self.excl_prev.configure(image="", text=EXCL_HINT)
        self.excl_prev.image = None
        sel = self.excl_list.curselection()
        if not sel:
            return
        try:
            from PIL import Image, ImageTk
            x = self.cfg["exclude_images"][sel[0]]
            img = imread(base_dir() / "templates_user" / x["file"])
            if img is None:
                raise ValueError("no image")
            im = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
            im.thumbnail((400, 160))
            ph = ImageTk.PhotoImage(im)
            self.excl_prev.configure(image=ph, text="")
            self.excl_prev.image = ph
        except Exception:
            self.excl_prev.configure(image="", text="تصویر پیدا نشد؛ دوباره ثبت یا تعویض کنید")

    def add_excl(self):
        name = simpledialog.askstring(APP_TITLE, "یک نام برای این تصویر مستثنی بنویسید (مثلاً «برچسب ناموجود»):",
                                      parent=self.root)
        if not name or not name.strip():
            return
        name = name.strip()
        fname = f"excl_{int(time.time())}.png"

        def saved():
            self.cfg["exclude_images"].append({"name": name, "file": fname})
            save_config(self.cfg)
            self.refresh_excl(len(self.cfg["exclude_images"]) - 1)

        self.capture(None, label=name, filename=fname, on_saved=saved)

    def delete_excl(self):
        sel = self.excl_list.curselection()
        if not sel:
            return
        x = self.cfg["exclude_images"].pop(sel[0])
        try:
            (base_dir() / "templates_user" / x["file"]).unlink()
        except OSError:
            pass
        save_config(self.cfg)
        self.refresh_excl()
        self.on_log(f"تصویر مستثنی «{x['name']}» حذف شد", "ok")

    def refresh_steps(self, select=None):
        self.steps_list.delete(0, "end")
        for i, st in enumerate(self.cfg["extra_steps"], 1):
            self.steps_list.insert("end", f"{i}.  {st['name']}")
        if select is not None and 0 <= select < len(self.cfg["extra_steps"]):
            self.steps_list.selection_set(select)

    def add_step(self):
        name = simpledialog.askstring(APP_TITLE, "یک نام برای این مرحله بنویسید (مثلاً «ادامه خرید»):",
                                      parent=self.root)
        if not name or not name.strip():
            return
        name = name.strip()
        fname = f"step_{int(time.time())}.png"

        def saved():
            self.cfg["extra_steps"].append({"name": name, "file": fname})
            save_config(self.cfg)
            self.refresh_steps(len(self.cfg["extra_steps"]) - 1)

        self.capture(None, label=name, filename=fname, on_saved=saved)

    def delete_step(self):
        sel = self.steps_list.curselection()
        if not sel:
            return
        st = self.cfg["extra_steps"].pop(sel[0])
        try:
            (base_dir() / "templates_user" / st["file"]).unlink()
        except OSError:
            pass
        save_config(self.cfg)
        self.refresh_steps()
        self.on_log(f"مرحله «{st['name']}» حذف شد", "ok")

    def move_step(self, d):
        sel = self.steps_list.curselection()
        if not sel:
            return
        i, j = sel[0], sel[0] + d
        steps = self.cfg["extra_steps"]
        if 0 <= j < len(steps):
            steps[i], steps[j] = steps[j], steps[i]
            save_config(self.cfg)
            self.refresh_steps(j)

    def clear_custom(self):
        d = base_dir() / "templates_user"
        n = 0
        if d.exists():
            for p in d.glob("*.png"):
                if p.name.startswith(("step_", "excl_")):
                    continue
                p.unlink()
                n += 1
        self.on_log(f"{n} تصویر ثبت‌شده حذف شد", "ok")

    # --- خواب کامپیوتر
    def toggle_sleep(self):
        if self.sleep_deadline:
            self.cancel_sleep("زمان‌بندی خواب لغو شد")
            return
        if not self.read_settings():
            return
        now = time.time()
        cands = []
        hm = parse_hhmm(self.cfg["sleep_at"]) if self.cfg["sleep_at"] else None
        if hm:
            lt = time.localtime(now)
            t = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hm[0], hm[1], 0, 0, 0, -1))
            if t <= now:
                t = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + 1, hm[0], hm[1], 0, 0, 0, -1))
            cands.append(t)
        if self.cfg["sleep_hours"] > 0:
            cands.append(now + self.cfg["sleep_hours"] * 3600)
        if not cands:
            messagebox.showinfo(APP_TITLE, "یک ساعت (مثلاً 02:30) یا تعداد ساعت وارد کنید.")
            return
        self.sleep_deadline = min(cands)
        self._sleep_warned = False
        self.sleep_btn.configure(text="لغو زمان‌بندی خواب", bg="#c0151a", fg="white")
        self.on_log("زمان‌بندی خواب فعال شد: " + time.strftime("%H:%M:%S", time.localtime(self.sleep_deadline)), "ok")
        self.tick_sleep()

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
        self.sleep_info.configure(text=f"خواب ساعت {at}  —  {h} ساعت و {m} دقیقه و {sec} ثانیه مانده")
        if left <= 30:
            if not self._sleep_warned:
                self._sleep_warned = True
                user32.MessageBeep(0x30)
                self.on_log("۳۰ ثانیه تا خواب؛ برای لغو، در تب «خواب» دکمه‌ی لغو را بزنید", "warn")
            self.on_status(f"رفتن به خواب تا {int(left)} ثانیه دیگر", "wait")
        self._sleep_job = self.root.after(1000, self.tick_sleep)

    def cancel_sleep(self, msg=None):
        self.sleep_deadline = None
        self._sleep_warned = False
        if self._sleep_job:
            self.root.after_cancel(self._sleep_job)
            self._sleep_job = None
        self.sleep_btn.configure(text="فعال‌سازی زمان‌بندی خواب", bg=self._sleep_btn_style[0],
                                 fg=self._sleep_btn_style[1])
        self.sleep_info.configure(text="زمان‌بندی خواب غیرفعال است")
        if msg:
            self.on_log(msg, "ok")
            if not self.bot:
                self.on_status("آماده", "idle")

    def sleep_now(self, confirm=True):
        if confirm and not messagebox.askyesno(
                APP_TITLE, "همه‌ی کارها متوقف شود و کامپیوتر به حالت Sleep برود؟", parent=self.root):
            return
        self.cancel_sleep()
        if self.bot:
            self.bot.stop_ev.set()
        _beep_gen[0] += 1  # بوق‌های در حال پخش قطع شوند
        self.overlay.hide()
        stop_speech()
        self.on_status("همه‌ی کارها متوقف شد؛ در حال رفتن به خواب", "idle")
        self.on_log("همه‌ی کارها متوقف شد؛ کامپیوتر به حالت Sleep می‌رود", "warn")
        self.root.after(1500, sleep_computer)

    def close(self):
        if self.sleep_deadline and not messagebox.askyesno(
                APP_TITLE, "زمان‌بندی خواب فعال است و با بستن برنامه لغو می‌شود. برنامه بسته شود؟",
                parent=self.root):
            return
        if self.bot:
            self.bot.stop_ev.set()
        self.overlay.hide()
        stop_speech()
        self.root.destroy()

    def run(self):
        self.on_log("آماده. صفحه لیست محصولات را در مرورگر باز کنید و شروع را بزنید.")
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
