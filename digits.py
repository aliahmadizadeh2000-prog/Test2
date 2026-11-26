# -*- coding: utf-8 -*-
"""Number reader for Auto Clicker: Persian digits without any OCR engine (numpy + opencv only).
Pipeline: crop -> adaptive binarize -> connected components -> merge dots -> normalize -> match samples."""
import re
import json
import math
import time
import traceback
from pathlib import Path

import numpy as np
import cv2

try:
    import tkinter as tk
    from tkinter import messagebox, simpledialog, ttk
except ImportError:
    tk = None

from engine import base_dir, imread, imwrite, new_id, mon_index, new_rule, APP_TITLE

FONT = ("Tahoma", 10)
SMALL = ("Tahoma", 9)
BOLD = ("Tahoma", 11, "bold")
GRAY = "#666666"
DIGIT_LABELS = list("0123456789")
LABELS = DIGIT_LABELS + ["comma", "toman"]
IGNORED = ("comma", "toman")  # recognized, but not part of the value
PERSIAN = "۰۱۲۳۴۵۶۷۸۹"
_ASCII = str.maketrans(PERSIAN + "٠١٢٣٤٥٦٧٨٩", "0123456789" * 2)
ADAPT_C = 7
MIN_MARGIN = 0.04  # best class must beat the second class by this much (unless almost perfect)
VEC_W, VEC_H = 16, 24


def label_text(label):
    if label == "comma":
        return "ویرگول ،"
    if label == "toman":
        return "کلمه‌ی تومان"
    return PERSIAN[int(label)]


def to_ascii_digits(s):
    return str(s).translate(_ASCII)


# ------------------------------------------------------------------ allowed numbers: list and ranges
def parse_list(text):
    """'1000000-4000000, 4500000' -> [(1000000, 4000000), (4500000, 4500000)]. Thousand separators are ignored.
    Entries are separated by space, ؛ ، ; or a comma followed by a space."""
    s = to_ascii_digits(text or "").replace("٬", "")
    s = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", s)
    s = re.sub(r"\s*(?:-|–|—|~|تا)\s*", "-", s)
    out = []
    for tok in re.split(r"[,،؛;\s]+", s):
        nums = [int(d) for d in (re.sub(r"\D", "", p) for p in tok.split("-")) if d]
        if len(nums) == 1:
            out.append((nums[0], nums[0]))
        elif len(nums) >= 2:
            out.append((min(nums[:2]), max(nums[:2])))
    return out


def match_list(value, entries):
    """Index of the first matching entry (used for 'list order' priority); 0 if the list is empty; None if no match."""
    if not entries:
        return 0
    for i, (lo, hi) in enumerate(entries):
        if lo <= value <= hi:
            return i
    return None


# ------------------------------------------------------------------ binarize and segment
def binarize(gray):
    """Adaptive threshold; the polarity (dark or light text) is detected from the gray-level distribution."""
    p5, med, p95 = np.percentile(gray, [5, 50, 95])
    bs = int(max(15, min(51, (min(gray.shape) // 2) * 2 + 1)))
    if med - p5 >= p95 - med:
        return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, bs, ADAPT_C)
    return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, bs, -ADAPT_C)


def merge_glyphs(gs):
    x0, y0 = min(g["box"][0] for g in gs), min(g["box"][1] for g in gs)
    x1, y1 = max(g["box"][2] for g in gs), max(g["box"][3] for g in gs)
    mask = np.zeros((y1 - y0, x1 - x0), bool)
    for g in gs:
        a, b, c, d = g["box"]
        mask[b - y0:d - y0, a - x0:c - x0] |= g["mask"]
    return {"box": (x0, y0, x1, y1), "mask": mask}


def segment(img):
    """-> (upscaled image, glyphs left to right, scale). A glyph = {'box': (x0, y0, x1, y1), 'mask': bool array}."""
    h0 = img.shape[0]
    f = 1.0 if h0 >= 40 else min(4.0, 40.0 / max(1, h0))
    up = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC) if f > 1.01 else img
    gray = cv2.GaussianBlur(cv2.cvtColor(up, cv2.COLOR_BGR2GRAY), (3, 3), 0)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(binarize(gray), connectivity=8)
    comps = [i for i in range(1, n) if stats[i][4] >= 6]
    if not comps:
        return up, [], f
    hmax = max(stats[i][3] for i in comps)
    comps = [i for i in comps if stats[i][3] >= 0.08 * hmax or stats[i][2] >= 0.08 * hmax]  # drop specks
    boxes = [[int(stats[i][0]), int(stats[i][1]), int(stats[i][0] + stats[i][2]), int(stats[i][1] + stats[i][3])] for i in comps]
    ids = [[i] for i in comps]
    changed = True
    while changed:  # dots above / below a letter belong to it
        changed = False
        for a in range(len(boxes)):
            for b in range(a + 1, len(boxes)):
                A, B = boxes[a], boxes[b]
                ov = min(A[2], B[2]) - max(A[0], B[0])
                if ov > 0 and ov >= 0.5 * min(A[2] - A[0], B[2] - B[0]) and max(A[1], B[1]) - min(A[3], B[3]) < 0.5 * hmax:
                    boxes[a] = [min(A[0], B[0]), min(A[1], B[1]), max(A[2], B[2]), max(A[3], B[3])]
                    ids[a] += ids[b]
                    del boxes[b], ids[b]
                    changed = True
                    break
            if changed:
                break
    glyphs, hh, ww = [], up.shape[0], up.shape[1]
    for bx, idl in zip(boxes, ids):
        if (bx[0] <= 0 or bx[1] <= 0 or bx[2] >= ww or bx[3] >= hh) and (bx[2] - bx[0] < 0.12 * hmax or bx[3] - bx[1] < 0.12 * hmax):
            continue  # a thin sliver cut by the crop border (e.g. the edge of a neighbouring element)
        sub = lab[bx[1]:bx[3], bx[0]:bx[2]]
        glyphs.append({"box": tuple(bx), "mask": np.isin(sub, idl)})
    glyphs.sort(key=lambda g: g["box"][0])
    return up, glyphs, f


def line_geom(glyphs):
    """Digit height H and top of the line, from the tall glyphs (commas and small zeros are ignored)."""
    hs = [g["box"][3] - g["box"][1] for g in glyphs]
    tall = [g for g in glyphs if g["box"][3] - g["box"][1] >= 0.6 * max(hs)]
    H = float(np.median([g["box"][3] - g["box"][1] for g in tall]))
    return max(H, 1.0), float(np.median([g["box"][1] for g in tall]))


def ref_geom(glyphs, lib=None, extra=()):
    """Reference size/position for the line. Starts from the tall glyphs, then (if samples of the digits 1-9 exist)
    re-measures using only glyphs that look like those digits, so the word 'toman' does not disturb it.
    `extra` = glyphs already known to be digits (used while training)."""
    H, top = line_geom(glyphs)
    hs = [g["box"][3] - g["box"][1] for g in glyphs]
    refs = list(extra)
    if lib is not None and any(i["label"] in DIGIT_LABELS[1:] for i in lib.items):
        for g in glyphs:
            if g["box"][3] - g["box"][1] >= 0.6 * max(hs):
                vg = vec(g["mask"])
                if max((float(np.dot(vg, i["vec"])) for i in lib.items if i["label"] in DIGIT_LABELS[1:]), default=0.0) >= 0.8:
                    refs.append(g)
    if refs:
        H = max(1.0, float(np.median([g["box"][3] - g["box"][1] for g in refs])))
        top = float(np.median([g["box"][1] for g in refs]))
    return H, top


def feats(box, H, top):
    """Size and position relative to the digit height: (width/H, height/H, vertical center/H)."""
    x0, y0, x1, y1 = box
    return ((x1 - x0) / H, (y1 - y0) / H, ((y0 + y1) / 2.0 - top) / H)


def vec(mask):
    v = cv2.resize(mask.astype(np.float32), (VEC_W, VEC_H), interpolation=cv2.INTER_AREA)
    v = cv2.GaussianBlur(v, (3, 3), 0.8).ravel()
    v = v - v.mean()
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-6 else v


def score(vg, geo, it):
    ncc = max(0.0, float(np.dot(vg, it["vec"])))
    d = ((geo[0] - it["geo"][0]) / 0.22) ** 2 + ((geo[1] - it["geo"][1]) / 0.18) ** 2 + ((geo[2] - it["geo"][2]) / 0.18) ** 2
    ws = 0.3 if (geo[1] < 0.5 and it["geo"][1] < 0.5) else 0.75  # tiny glyphs (zero, comma): shape is unreliable
    return ws * ncc + (1.0 - ws) * math.exp(-0.5 * d)


# ------------------------------------------------------------------ sample library (folder 'digits')
class Lib:
    def __init__(self):
        self.conf, self.items, self.stamp = 0.80, [], None

    def path(self):
        return base_dir() / "digits"

    def load(self):
        p = self.path() / "samples.json"
        self.items = []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        self.conf = min(0.99, max(0.5, float(data.get("conf", 0.80))))
        for s in data.get("samples", []):
            try:
                img = imread(self.path() / (s["id"] + ".png"))
                mask = img[:, :, 0] > 127
                self.items.append({"id": s["id"], "label": s["label"], "mask": mask, "vec": vec(mask), "geo": tuple(s["geo"])})
            except Exception:
                continue
        self.stamp = p.stat().st_mtime if p.exists() else None

    def save(self):
        self.path().mkdir(parents=True, exist_ok=True)
        data = {"conf": self.conf, "samples": [{"id": i["id"], "label": i["label"], "geo": list(i["geo"])} for i in self.items]}
        (self.path() / "samples.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        self.stamp = (self.path() / "samples.json").stat().st_mtime

    def add(self, label, mask, geo):
        sid = new_id("d")
        imwrite(self.path() / (sid + ".png"), (mask.astype(np.uint8) * 255))
        self.items.append({"id": sid, "label": label, "mask": mask, "vec": vec(mask), "geo": tuple(geo)})
        self.save()
        return sid

    def remove(self, sid):
        self.items = [i for i in self.items if i["id"] != sid]
        try:
            (self.path() / (sid + ".png")).unlink()
        except OSError:
            pass
        self.save()

    def counts(self):
        c = {k: 0 for k in LABELS}
        for i in self.items:
            c[i["label"]] = c.get(i["label"], 0) + 1
        return c


_LIB = Lib()
_CACHE = {}
_last_check = [0.0]


def get_lib():
    """Shared library; reloaded when samples.json changes (checked at most once per second)."""
    now = time.time()
    if _last_check[0] == 0.0 or now - _last_check[0] > 1.0:
        first = _last_check[0] == 0.0
        _last_check[0] = now
        p = _LIB.path() / "samples.json"
        st = p.stat().st_mtime if p.exists() else None
        if first or st != _LIB.stamp:
            _LIB.load()
            _CACHE.clear()
    return _LIB


def classify(vg, geo, lib, only=None):
    """-> (best label, its score, margin over the runner-up class)."""
    best = {}
    for it in lib.items:
        if only and it["label"] not in only:
            continue
        s = score(vg, geo, it)
        if s > best.get(it["label"], -1.0):
            best[it["label"]] = s
    if not best:
        return None, 0.0, 0.0
    rk = sorted(best.items(), key=lambda kv: -kv[1])
    return rk[0][0], rk[0][1], rk[0][1] - (rk[1][1] if len(rk) > 1 else 0.0)


def read_number(img, lib=None, conf=None):
    """Reads the number in a BGR crop. -> dict(ok, value, text, conf, items[(label, score, box)], why, scale).
    ok=False means 'not read' (never a guess): no samples, no digits, or a glyph below the confidence threshold."""
    lib = lib or get_lib()
    conf = conf or lib.conf
    res = {"ok": False, "value": None, "text": "", "conf": 0.0, "items": [], "why": "", "scale": 1.0}
    if not lib.items:
        res["why"] = "هیچ نمونه‌ای ثبت نشده؛ از «آموزش ارقام» نمونه بگیرید"
        return res
    up, glyphs, res["scale"] = segment(img)
    if not glyphs:
        res["why"] = "در این ناحیه چیزی دیده نشد"
        return res
    H, top = ref_geom(glyphs, lib)
    has_word = any(i["label"] == "toman" for i in lib.items)
    i, items = 0, []
    while i < len(glyphs):
        if has_word:  # the word 'toman' may be split into several pieces: try merging neighbours
            best = None
            for j in range(i, min(len(glyphs), i + 4)):
                g = merge_glyphs(glyphs[i:j + 1])
                lb, sc, _ = classify(vec(g["mask"]), feats(g["box"], H, top), lib, only=("toman",))
                if lb and sc >= conf and (best is None or sc > best[0]):
                    best = (sc, j, g["box"])
            if best:
                items.append(("toman", best[0], best[2], 1.0))
                i = best[1] + 1
                continue
        g = glyphs[i]
        lb, sc, mg = classify(vec(g["mask"]), feats(g["box"], H, top), lib)
        items.append((lb, sc, g["box"], mg))
        i += 1
    res["items"] = [(lb, sc, bx) for lb, sc, bx, _ in items]
    res["conf"] = min([sc for _, sc, _, _ in items] or [0.0])
    for lb, sc, bx, mg in items:
        if sc < conf or (mg < MIN_MARGIN and sc < 0.92):
            res["why"] = "نویسه‌ای با اطمینان کم (%.2f) خوانده شد؛ نمونه‌های بیشتری ثبت کنید" % sc
            return res
    digits = "".join(lb for lb, _, _, _ in items if lb in DIGIT_LABELS)
    if not digits:
        res["why"] = "عددی پیدا نشد"
        return res
    res.update(ok=True, text=digits, value=int(digits))
    return res


def region_of(m, rel, shape):
    """Number rectangle (x0, y0, x1, y1) inside the frame for candidate m, or None."""
    dx, dy, w, h = rel
    k = m["k"]
    x0, y0 = int(round(m["x"] + dx * k)), int(round(m["y"] + dy * k))
    x1, y1 = int(round(x0 + w * k)), int(round(y0 + h * k))
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(shape[1], x1), min(shape[0], y1)
    return (x0, y0, x1, y1) if x1 - x0 >= 8 and y1 - y0 >= 6 else None


def read_cached(crop, lib):
    key = (hash(crop.tobytes()), crop.shape)
    if key not in _CACHE:
        if len(_CACHE) > 300:
            _CACHE.clear()
        _CACHE[key] = read_number(crop, lib)
    return _CACHE[key]


def filter_matches(runner, c, cs, frame):
    """Called by the engine for a 'seen' condition with a number: keeps the candidates whose number is allowed.
    Adds m['num'] (value), m['numtxt'] (digits as text) and m['rank'] (index in the allowed list)."""
    cond, lib = c["num"], get_lib()
    entries, rel, out = parse_list(cond.get("list", "")), cond.get("rel"), []
    seen = getattr(runner, "_dg_last", None)
    if seen is None:
        seen = runner._dg_last = {}
    for m in cs:
        box = region_of(m, rel, frame.shape) if rel else None
        if box is None:
            continue
        r = read_cached(frame[box[1]:box[3], box[0]:box[2]], lib)
        name = "%d,%d" % (m["x"] // 20, m["y"] // 20)
        txt = ("%d" % r["value"]) if r["ok"] else "؟"
        if seen.get(name) != txt:
            seen[name] = txt
            runner.say("عدد خوانده‌شده: %s" % (("%s (اطمینان %.2f)" % (format(r["value"], ","), r["conf"])) if r["ok"] else
                                                "خوانده نشد، " + r["why"]), "info")
        runner.reads.append("عدد کنار تصویر: " + (format(r["value"], ",") if r["ok"] else "خوانده نشد (%s)" % r["why"]))
        if r["ok"]:
            rank = match_list(r["value"], entries)
            if rank is not None:
                m["num"], m["numtxt"], m["rank"] = r["value"], r["text"], rank
                out.append(m)
        elif cond.get("fail") == "click":
            m["numtxt"], m["rank"] = "", 1e9
            out.append(m)
    return out
# ===== END OF PART 01 (lines: 363) =====


# ------------------------------------------------------------------ training window and number-based rule template
class Trainer(tk.Toplevel if tk else object):
    """'آموزش ارقام': draw a box around a printed number on screen, pick glyphs, give each a label."""
    HELP = ("۱) «ثبت از صفحه» را بزنید و دور یک قیمت (یک خط) کادر بکشید.  ۲) روی عکس: کلیک روی یک شکل = انتخاب؛ کشیدن کادر = انتخاب چند شکل "
            "(کلمه‌ی «تومان» اگر چند تکه است، همه‌ی تکه‌ها را با هم انتخاب کنید).  ۳) برچسب درست را بزنید.\n"
            "ترتیب پیشنهادی: اول ارقام ۱ تا ۹، بعد ۰، ویرگول و تومان. از هر مورد ۳ تا ۵ نمونه و با اندازه‌های مختلف بگیرید.")

    def __init__(self, app):
        super().__init__(app.root)
        self.app, self.lib = app, get_lib()
        self.cur, self.sel, self.done, self.rid, self.p0, self.d, self.photo = None, set(), {}, None, (0, 0), 1.0, None
        self.title("آموزش ارقام")
        self.geometry("620x760+%d+%d" % (app.root.winfo_rootx() + 10, app.root.winfo_rooty() + 10))
        tk.Label(self, text=self.HELP, font=SMALL, fg=GRAY, wraplength=590, justify="right", anchor="e").pack(fill="x", padx=10, pady=6)
        bar = tk.Frame(self)
        bar.pack(fill="x", padx=10)
        tk.Button(bar, text="ثبت از صفحه…", font=FONT, bg="#8e1a9a", fg="white", command=self.capture).pack(side="right")
        tk.Button(bar, text="آزمایش خواندن عدد…", font=FONT, command=self.test_read).pack(side="right", padx=6)
        self.v_conf = tk.StringVar(value="%.2f" % self.lib.conf)
        tk.Button(bar, text="اعمال", font=SMALL, command=self.set_conf).pack(side="left")
        tk.Entry(bar, textvariable=self.v_conf, font=FONT, width=5, justify="center").pack(side="left", padx=3)
        tk.Label(bar, text="آستانه‌ی اطمینان (۰٫۵ تا ۰٫۹۹):", font=SMALL).pack(side="left")
        self.cv = tk.Canvas(self, height=110, bg="#f4f4f4", highlightthickness=1, cursor="crosshair")
        self.cv.pack(fill="x", padx=10, pady=6)
        self.cv.bind("<ButtonPress-1>", self.on_press)
        self.cv.bind("<B1-Motion>", self.on_move)
        self.cv.bind("<ButtonRelease-1>", self.on_release)
        self.info = tk.Label(self, text="هنوز عکسی ثبت نشده", font=SMALL, anchor="e")
        self.info.pack(fill="x", padx=10)
        grid = tk.LabelFrame(self, text=" برچسب (تعداد نمونه‌ها) ", font=SMALL, labelanchor="ne")
        grid.pack(fill="x", padx=10, pady=6)
        self.btns = {}
        order = DIGIT_LABELS[1:] + ["0", "comma", "toman"]
        for n, lb in enumerate(order):
            b = tk.Button(grid, font=FONT, width=16, command=lambda lb=lb: self.add_sample(lb))
            b.grid(row=n // 3, column=2 - n % 3, padx=3, pady=3)
            self.btns[lb] = b
        mid = tk.Frame(self)
        mid.pack(fill="both", expand=True, padx=10)
        self.lst = tk.Listbox(mid, height=6, font=FONT, activestyle="none", exportselection=False)
        self.lst.pack(side="right", fill="both", expand=True)
        self.lst.bind("<<ListboxSelect>>", lambda e: self.preview())
        side = tk.Frame(mid)
        side.pack(side="left", padx=8)
        self.pv = tk.Label(side, text="پیش‌نمایش", font=SMALL, width=14, height=5, relief="groove")
        self.pv.pack()
        tk.Button(side, text="حذف نمونه‌ی انتخاب‌شده", font=SMALL, command=self.delete_sample).pack(pady=4)
        self.res = tk.Label(self, text="", font=BOLD, anchor="e", justify="right", wraplength=590)
        self.res.pack(fill="x", padx=10, pady=8)
        self.refresh()

    # --- capture and selection
    def crop_from(self, label):
        got = self.app.grab_rects([label])
        if not got:
            return None
        x0, y0, x1, y1 = got[0][0]
        return (x0, y0, x1, y1), cv2.cvtColor(np.asarray(got[1].crop((x0, y0, x1, y1))), cv2.COLOR_RGB2BGR)

    def capture(self):
        r = self.crop_from("دور یک قیمت (فقط یک خط) کادر بکشید")
        if not r:
            return
        up, glyphs, f = segment(r[1])
        if not glyphs:
            messagebox.showinfo(APP_TITLE, "در این ناحیه نوشته‌ای پیدا نشد. کادر را دقیق‌تر دور عدد بکشید.", parent=self)
            return
        self.cur, self.sel, self.done = {"up": up, "glyphs": glyphs}, set(), {}
        self.draw()

    def draw(self):
        from PIL import Image, ImageTk
        up, gl = self.cur["up"], self.cur["glyphs"]
        self.d = min(3.0, 590.0 / up.shape[1])
        im = Image.fromarray(cv2.cvtColor(up, cv2.COLOR_BGR2RGB)).resize((int(up.shape[1] * self.d), int(up.shape[0] * self.d)))
        self.photo = ImageTk.PhotoImage(im)
        self.cv.configure(height=im.height + 8)
        self.cv.delete("all")
        self.cv.create_image(4, 4, image=self.photo, anchor="nw")
        for i, g in enumerate(gl):
            x0, y0, x1, y1 = [v * self.d + 4 for v in g["box"]]
            col = "#e5262b" if i in self.sel else ("#17803d" if i in self.done else "#2a6fdb")
            self.cv.create_rectangle(x0, y0, x1, y1, outline=col, width=3 if i in self.sel else 2)
            if i in self.done:
                self.cv.create_text((x0 + x1) / 2, y0 - 2, text=label_text(self.done[i])[:1], fill=col, anchor="s", font=SMALL)
        self.info.configure(text="%d شکل پیدا شد؛ انتخاب‌شده: %d" % (len(gl), len(self.sel)))

    def on_press(self, e):
        self.p0 = (e.x, e.y)
        self.rid = self.cv.create_rectangle(e.x, e.y, e.x, e.y, outline="#ff8800", dash=(3, 3))

    def on_move(self, e):
        if self.rid:
            self.cv.coords(self.rid, self.p0[0], self.p0[1], e.x, e.y)

    def on_release(self, e):
        if self.rid:
            self.cv.delete(self.rid)
            self.rid = None
        if not self.cur:
            return
        gl, d = self.cur["glyphs"], self.d
        x0, y0, x1, y1 = self.p0[0], self.p0[1], e.x, e.y
        if abs(x1 - x0) < 4 and abs(y1 - y0) < 4:
            for i, g in enumerate(gl):
                b = [v * d + 4 for v in g["box"]]
                if b[0] - 4 <= x1 <= b[2] + 4 and b[1] - 4 <= y1 <= b[3] + 4:
                    self.sel ^= {i}
                    break
        else:
            lx, hx, ly, hy = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
            self.sel = {i for i, g in enumerate(gl) if lx <= (g["box"][0] + g["box"][2]) / 2 * d + 4 <= hx
                        and ly <= (g["box"][1] + g["box"][3]) / 2 * d + 4 <= hy}
        self.draw()

    # --- samples
    def add_sample(self, label):
        if not self.cur or not self.sel:
            messagebox.showinfo(APP_TITLE, "اول «ثبت از صفحه» را بزنید و روی عکس یک یا چند شکل را انتخاب کنید.", parent=self)
            return
        gl = self.cur["glyphs"]
        pick = [gl[i] for i in sorted(self.sel)]
        g = merge_glyphs(pick) if len(pick) > 1 else pick[0]
        known = pick if label in DIGIT_LABELS[1:] else []
        H, top = ref_geom(gl, self.lib, known)
        self.lib.add(label, g["mask"], feats(g["box"], H, top))
        for i in self.sel:
            self.done[i] = label
        self.sel = set()
        self.draw()
        self.refresh()

    def refresh(self):
        c = self.lib.counts()
        for lb, b in self.btns.items():
            b.configure(text="%s   (%d)" % (label_text(lb), c.get(lb, 0)))
        self.lst.delete(0, "end")
        self.order = sorted(self.lib.items, key=lambda i: LABELS.index(i["label"]) if i["label"] in LABELS else 99)
        seen = {}
        for it in self.order:
            seen[it["label"]] = seen.get(it["label"], 0) + 1
            self.lst.insert("end", "%s — نمونه %d  (%d×%d)" % (label_text(it["label"]), seen[it["label"]], it["mask"].shape[1], it["mask"].shape[0]))
        self.preview()

    def preview(self):
        s = self.lst.curselection()
        self.pv.image = None
        if not s:
            self.pv.configure(image="", text="پیش‌نمایش")
            return
        from PIL import Image, ImageTk
        m = self.order[s[0]]["mask"]
        im = Image.fromarray((~m).astype(np.uint8) * 255)
        k = max(1, min(80 // im.height, 100 // im.width)) if im.height < 80 and im.width < 100 else 1
        ph = ImageTk.PhotoImage(im.resize((im.width * k, im.height * k), Image.NEAREST))
        self.pv.configure(image=ph, text="", width=104, height=84)
        self.pv.image = ph

    def delete_sample(self):
        s = self.lst.curselection()
        if s and messagebox.askyesno(APP_TITLE, "این نمونه حذف شود؟", parent=self):
            self.lib.remove(self.order[s[0]]["id"])
            _CACHE.clear()
            self.refresh()

    def set_conf(self):
        v = to_float(self.v_conf.get(), self.lib.conf)
        self.lib.conf = min(0.99, max(0.5, v))
        self.v_conf.set("%.2f" % self.lib.conf)
        self.lib.save()
        _CACHE.clear()

    # --- test: read a number on screen without clicking
    def test_read(self):
        r = self.crop_from("دور عددی که می‌خواهید خوانده شود کادر بکشید")
        if not r:
            return
        rect, crop = r
        res = read_number(crop, self.lib)
        if res["ok"]:
            self.res.configure(text="عدد خوانده‌شده: %s   (اطمینان %.2f)" % (format(res["value"], ","), res["conf"]), fg="#17803d")
        else:
            self.res.configure(text="خوانده نشد: " + res["why"], fg="#c0151a")
        import mss
        with mss.mss() as s:
            mon = s.monitors[mon_index(s, self.app.cfg["settings"].get("monitor", 1))]
        f = res["scale"]
        boxes = [rect] + [(rect[0] + b[0] / f, rect[1] + b[1] / f, rect[0] + b[2] / f, rect[1] + b[3] / f) for _, _, b in res["items"]]
        self.app.overlay.boxes(boxes, mon, 4.0)


def to_float(text, default):
    try:
        return float(to_ascii_digits(text).replace("٫", ".").replace(",", "."))
    except ValueError:
        return default


def open_trainer(app):
    w = getattr(app, "_trainer", None)
    if w is not None and w.winfo_exists():
        w.lift()
        return
    app._trainer = Trainer(app)


def template_number(app):
    """Template: if the anchor icon is seen and the number beside it is allowed -> click a box, type text, Enter."""
    name = simpledialog.askstring(APP_TITLE, "یک نام برای تصویر لنگر بنویسید (مثلاً «آیکون سبد»):", parent=app.root)
    if not name or not name.strip():
        return
    got = app.grab_rects(["۱) دور تصویر لنگر (مثلاً آیکون) کادر بکشید", "۲) دور ناحیه‌ی عدد کادر بکشید",
                          "۳) دور جای کلیک (مثلاً کادر تعداد) کادر بکشید"])
    if not got:
        return
    (a, n, t), meta = got[0][:3], None
    meta = app.store_crop(name.strip(), got[1], a)
    if not meta:
        return
    lst = simpledialog.askstring(APP_TITLE, "اعداد مجاز (فهرست و بازه؛ خالی = هر عددی):\nمثلاً 1000000-4000000 یا 500000 800000",
                                 initialvalue="", parent=app.root)
    if lst is None:
        return
    text = simpledialog.askstring(APP_TITLE, "چه متن یا عددی تایپ شود؟ (خالی = چیزی تایپ نشود)", initialvalue="2", parent=app.root)
    r = new_rule("عدد کنار " + meta["name"])
    r["conds"] = [{"t": "seen", "img": meta["id"], "num": {"rel": [n[0] - a[0], n[1] - a[1], n[2] - n[0], n[3] - n[1]], "list": lst, "fail": "skip"}}]
    r["acts"] = [{"t": "click", "mode": "rect", "rel": [t[0] - a[0], t[1] - a[1], t[2] - t[0], t[3] - t[1]]}]
    if text:
        r["acts"].append({"t": "type", "text": text})
        if messagebox.askyesno(APP_TITLE, "بعد از تایپ، کلید Enter هم زده شود؟"):
            r["acts"].append({"t": "key", "keys": "enter"})
    app.add_rule(r)
# ===== END OF FILE (total lines: 598) =====
