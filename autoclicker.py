# -*- coding: utf-8 -*-
"""اتو کلیکر: رابط کاربری. هسته در engine.py است؛ خواندن عدد (اختیاری) در digits.py."""
import sys
import json
import copy
import shutil
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
except ImportError:
    tk = None

import engine as E
from engine import *
import profiles

try:
    import digits
    HAS_DIGITS = True
except ImportError:
    digits, HAS_DIGITS = None, False

FONT = ("Tahoma", 10)
SMALL = ("Tahoma", 9)
BOLD = ("Tahoma", 11, "bold")
GRAY = "#666666"
MORE_BY = [("min", "کمترین عدد"), ("max", "بیشترین عدد"), ("list", "ترتیب فهرست اعداد")] if HAS_DIGITS else []


# ------------------------------------------------------------------ red halo and on-screen boxes
class Overlay:
    KEY = "#010203"

    def __init__(self, root):
        self.root, self.win, self.job, self.on = root, None, None, False
        self.bwin = None

    def _make(self, geo):
        w = tk.Toplevel(self.root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.geometry(geo)
        w.configure(bg=self.KEY)
        w.attributes("-transparentcolor", self.KEY)
        cv = tk.Canvas(w, bg=self.KEY, highlightthickness=0)
        cv.pack(fill="both", expand=True)
        w.update_idletasks()
        if IS_WIN:  # click-through
            hwnd = user32.GetParent(w.winfo_id()) or w.winfo_id()
            ex = user32.GetWindowLongW(hwnd, -20)
            user32.SetWindowLongW(hwnd, -20, ex | 0x80000 | 0x20 | 0x80)
        return w, cv

    def show(self, text, steady=False):
        self.hide()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.win, cv = self._make("%dx%d+0+0" % (sw, sh))
        self.cv = cv
        self.rect = cv.create_rectangle(8, 8, sw - 8, sh - 8, outline="#e5262b", width=16)
        cv.create_rectangle(sw / 2 - 260, 16, sw / 2 + 260, 62, fill="#e5262b", outline="")
        cv.create_text(sw / 2, 39, text=text, fill="white", font=("Tahoma", 16, "bold"))
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
        for n in ("win", "bwin"):
            w = getattr(self, n)
            if w:
                w.destroy()
                setattr(self, n, None)

    def boxes(self, rects, mon, secs=2.5):
        """Draws green rectangles (monitor coordinates) for a few seconds."""
        if self.bwin:
            self.bwin.destroy()
        geo = "%dx%d+%d+%d" % (mon["width"], mon["height"], mon["left"], mon["top"])
        self.bwin, cv = self._make(geo)
        for x0, y0, x1, y1 in rects:
            cv.create_rectangle(x0, y0, x1, y1, outline="#00c853", width=4)
        w = self.bwin
        self.root.after(int(secs * 1000), lambda: w.destroy() if w is self.bwin and self.bwin else None)


# ------------------------------------------------------------------ generic small form dialog
class Form(tk.Toplevel):
    """Modal form. fields: dicts with k (key), l (label), t (type), h (short hint), o (options), req.
    Types: str, num, bool, choice [(value, label)], img / imgs (one / several library images), rule / rules, rel (rectangle relative to an
    anchor image), pt (screen point), area (screen rectangle). After closing, .res is a dict or None."""

    def __init__(self, app, title, fields, values=None, note="", check=None):
        super().__init__(app.top())
        self.app, self.fields, self.check, self.res = app, fields, check, None
        self.fd = {f["k"]: f for f in fields}
        self.vals, self.vars, self.maps, self.combos, self.multi = dict(values or {}), {}, {}, {}, {}
        self.title(title)
        self.transient(app.root)
        self.resizable(False, False)
        body = tk.Frame(self)
        body.pack(fill="both", expand=True, padx=12, pady=8)
        if note:
            tk.Label(body, text=note, font=SMALL, fg=GRAY, wraplength=420, justify="right", anchor="e").pack(fill="x", pady=(0, 6))
        for f in fields:
            self.add_row(body, f)
        bar = tk.Frame(self)
        bar.pack(fill="x", padx=12, pady=(0, 10))
        tk.Button(bar, text="تأیید", font=FONT, width=10, command=self.ok).pack(side="right")
        tk.Button(bar, text="لغو", font=FONT, width=10, command=self.destroy).pack(side="right", padx=6)
        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()
        self.geometry("+%d+%d" % (app.root.winfo_rootx() + 40, app.root.winfo_rooty() + 60))
        self.grab_set()
        self.wait_window(self)

    def options(self, f):
        o = f.get("o")
        if f["t"] in ("img", "imgs"):
            return [(m["id"], m["name"]) for m in self.app.cfg["images"]]
        if f["t"] in ("rule", "rules"):
            return [(r["id"], r.get("name") or "بدون نام") for r in self.app.cfg["rules"] if r["id"] != f.get("exclude")]
        return o() if callable(o) else (o or [])

    def add_row(self, body, f):
        k, t, cur = f["k"], f["t"], self.vals.get(f["k"])
        box = tk.Frame(body)
        box.pack(fill="x", pady=2)
        row = tk.Frame(box)
        row.pack(fill="x")
        tk.Label(row, text=f["l"], font=FONT, anchor="e", justify="right", wraplength=210).pack(side="right", padx=(8, 0))
        if t in ("str", "num"):
            v = tk.StringVar(value="" if cur is None else str(cur))
            tk.Entry(row, textvariable=v, font=FONT, width=26, justify="center").pack(side="left", fill="x", expand=True)
        elif t == "bool":
            v = tk.BooleanVar(value=bool(cur))
            tk.Checkbutton(row, variable=v).pack(side="left")
        elif t in ("choice", "img", "rule"):
            opts = self.options(f)
            self.maps[k] = {lb: val for val, lb in opts}
            v = tk.StringVar(value=dict(opts).get(cur, ""))
            cb = ttk.Combobox(row, textvariable=v, values=[lb for _, lb in opts], state="readonly", font=FONT, width=24)
            cb.pack(side="left", fill="x", expand=True)
            self.combos[k] = cb
            if t == "img":
                tk.Button(row, text="+ جدید", font=SMALL, command=lambda: self.new_image(k)).pack(side="left", padx=3)
        elif t in ("imgs", "rules"):
            opts = self.options(f)
            self.multi[k] = [val for val, _ in opts]
            lbx = tk.Listbox(row, selectmode="multiple", height=min(5, max(2, len(opts))), exportselection=False, font=FONT, width=26)
            for val, name in opts:
                lbx.insert("end", name)
                if val in (cur or []):
                    lbx.selection_set(lbx.size() - 1)
            lbx.pack(side="left", fill="x", expand=True)
            self.combos[k] = lbx
            v = None
            if t == "imgs":
                tk.Button(row, text="+ جدید", font=SMALL, command=lambda: self.new_image(k)).pack(side="left", padx=3)
        else:
            v = None
            lb = tk.Label(row, font=SMALL, fg=GRAY, text=self.summ(t, cur))
            tk.Button(row, text="انتخاب نقطه" if t == "pt" else "کشیدن کادر", font=SMALL,
                      command=lambda: self.pick(f, lb)).pack(side="left")
            if t == "area":
                tk.Button(row, text="پاک", font=SMALL, command=lambda: self.clear(k, lb, t)).pack(side="left", padx=3)
            lb.pack(side="left", padx=4)
        self.vars[k] = v
        if f.get("h"):
            tk.Label(box, text=f["h"], font=SMALL, fg=GRAY, wraplength=420, justify="right", anchor="e").pack(fill="x")

    def summ(self, t, cur):
        if not cur:
            return "ثبت نشده"
        return "(%d، %d)" % tuple(cur[:2]) if t == "pt" else "ثبت شد (%d×%d)" % (abs(cur[2] - (cur[0] if t == "area" else 0)), abs(cur[3] - (cur[1] if t == "area" else 0)))

    def clear(self, k, lb, t):
        self.vals[k] = None
        lb.configure(text=self.summ(t, None))

    def new_image(self, k):
        meta = self.app.capture_image()
        if meta and k in self.multi:
            self.multi[k].append(meta["id"])
            self.combos[k].insert("end", meta["name"])
            self.combos[k].selection_set("end")
        elif meta:
            self.maps[k][meta["name"]] = meta["id"]
            self.combos[k]["values"] = list(self.maps[k].keys())
            self.vars[k].set(meta["name"])

    def pick(self, f, lb):
        t = f["t"]
        if t == "rel":
            iid = f["anchor"](self)
            if not iid:
                messagebox.showinfo(APP_TITLE, "اول تصویر لنگر (تصویری که باید دیده شود) را انتخاب کنید.", parent=self)
                return
            r = self.app.pick_rel(iid)
        else:
            got = self.app.grab_rects(["روی نقطه‌ی کلیک یک کادر کوچک بکشید" if t == "pt" else f["l"]])
            r = None
            if got:
                x0, y0, x1, y1 = got[0][0]
                r = [int((x0 + x1) / 2), int((y0 + y1) / 2)] if t == "pt" else [x0, y0, x1, y1]
        if r:
            self.vals[f["k"]] = r
            lb.configure(text=self.summ(t, r))

    def get(self, k):
        f, v = self.fd[k], self.vars.get(k)
        if k in self.multi:
            return [self.multi[k][i] for i in self.combos[k].curselection()]
        if f["t"] in ("choice", "img", "rule"):
            return self.maps[k].get(v.get(), "")
        if f["t"] in ("rel", "pt", "area"):
            return self.vals.get(k)
        return v.get()

    def ok(self):
        res = {}
        for f in self.fields:
            val = self.get(f["k"])
            if f["t"] == "num":
                s = str(val).strip()
                val = 0.0 if not s else num(s, None)
                if val is None:
                    messagebox.showerror(APP_TITLE, "«%s» باید عدد باشد." % f["l"], parent=self)
                    return
            if f.get("req") and (val in ("", None) or val == []):
                messagebox.showerror(APP_TITLE, "«%s» را پر یا انتخاب کنید." % f["l"], parent=self)
                return
            res[f["k"]] = val
        err = self.check(res) if self.check else None
        if err:
            messagebox.showerror(APP_TITLE, err, parent=self)
            return
        self.res = res
        self.destroy()


# ------------------------------------------------------------------ three-step rule editor
class RuleEditor(tk.Toplevel):
    HEADS = ["مرحله ۱ از ۳ — چه ببینم؟ (شرط‌ها)", "مرحله ۲ از ۳ — چه کار کنم؟ (کارها، به ترتیب)", "مرحله ۳ از ۳ — تمام: نام و گزینه‌ها"]

    def __init__(self, app, rule, is_new):
        super().__init__(app.top())
        self.app, self.r, self.res, self.lists, self.step = app, copy.deepcopy(rule), None, {}, 0
        self.title("قانون جدید" if is_new else "ویرایش قانون")
        self.transient(app.root)
        self.geometry("540x600+%d+%d" % (app.root.winfo_rootx() + 20, app.root.winfo_rooty() + 30))
        self.head = tk.Label(self, font=BOLD, bg="#eeeeee", pady=6)
        self.head.pack(fill="x", padx=10, pady=8)
        nav = tk.Frame(self)
        nav.pack(side="bottom", fill="x", padx=10, pady=8)
        tk.Button(nav, text="لغو", font=FONT, width=8, command=self.destroy).pack(side="right")
        tk.Button(nav, text="ذخیره", font=FONT, width=8, bg="#17803d", fg="white", command=self.save).pack(side="right", padx=6)
        self.b_next = tk.Button(nav, text="بعدی ◀", font=FONT, width=8, command=lambda: self.show(self.step + 1))
        self.b_next.pack(side="left")
        self.b_prev = tk.Button(nav, text="▶ قبلی", font=FONT, width=8, command=lambda: self.show(self.step - 1))
        self.b_prev.pack(side="left", padx=6)
        self.body = tk.Frame(self)
        self.body.pack(fill="both", expand=True, padx=10)
        self.pages = [self.page_see(), self.page_do(), self.page_end()]
        self.refresh("conds")
        self.refresh("acts")
        self.show(0)
        self.grab_set()
        self.wait_window(self)

    def show(self, i):
        self.step = min(2, max(0, i))
        for j, p in enumerate(self.pages):
            p.pack_forget()
            if j == self.step:
                p.pack(fill="both", expand=True)
        self.head.configure(text=self.HEADS[self.step])
        self.b_prev.configure(state="normal" if self.step else "disabled")
        self.b_next.configure(state="normal" if self.step < 2 else "disabled")

    # --- list pages
    def list_page(self, key, intro, adders):
        fr = tk.Frame(self.body)
        tk.Label(fr, text=intro, font=SMALL, fg=GRAY, wraplength=490, justify="right", anchor="e").pack(fill="x", pady=(0, 4))
        lb = tk.Listbox(fr, height=10, font=FONT, activestyle="none", exportselection=False)
        lb.pack(fill="both", expand=True)
        lb.bind("<Double-Button-1>", lambda e: self.edit(key))
        bar = tk.Frame(fr)
        bar.pack(fill="x", pady=4)
        mb = tk.Menubutton(bar, text="+ افزودن ▾", font=FONT, relief="raised")
        menu = tk.Menu(mb, tearoff=0)
        mb["menu"] = menu
        for label, fn in adders:
            menu.add_command(label=label, command=fn)
        mb.pack(side="right", padx=2)
        for txt, fn in [("ویرایش", lambda: self.edit(key)), ("حذف", lambda: self.delete(key)),
                        ("▲", lambda: self.move(key, -1)), ("▼", lambda: self.move(key, 1))]:
            tk.Button(bar, text=txt, font=SMALL, command=fn).pack(side="right", padx=2)
        self.lists[key] = lb
        return fr

    def page_see(self):
        fr = self.list_page("conds", "شرط‌ها تعیین می‌کنند قانون کِی اجرا شود. اگر هیچ شرطی نگذارید، قانون بلافاصله بعد از شروع "
                            "اجرا می‌شود. برای ویرایش روی یک شرط دو بار کلیک کنید.",
                            [(lb, lambda k=k: self.add_cond(k)) for k, lb in COND_KINDS])
        jf = tk.Frame(fr)
        jf.pack(fill="x")
        self.v_join = tk.StringVar(value=self.r.get("join", "and"))
        tk.Radiobutton(jf, text="همه‌ی شرط‌ها (و)", variable=self.v_join, value="and", font=FONT).pack(side="right")
        tk.Radiobutton(jf, text="یکی از آن‌ها (یا)", variable=self.v_join, value="or", font=FONT).pack(side="right", padx=10)
        return fr

    def page_do(self):
        ads = [(lb, lambda k=k: self.add_act(k)) for k, lb in ACT_KINDS]
        ads.insert(0, ("کلیک کنار تصویر (کشیدن دو کادر: تصویر و هدف)", self.quick_two))
        return self.list_page("acts", "کارها از بالا به پایین اجرا می‌شوند. با ▲ و ▼ ترتیب را عوض کنید.", ads)

    def refresh(self, key):
        lb = self.lists[key]
        lb.delete(0, "end")
        fn = describe_cond if key == "conds" else describe_act
        for i, it in enumerate(self.r[key], 1):
            lb.insert("end", "%d.  %s" % (i, fn(self.app.cfg, it)))

    def sel(self, key):
        s = self.lists[key].curselection()
        return s[0] if s else None

    def delete(self, key):
        i = self.sel(key)
        if i is not None:
            del self.r[key][i]
            self.refresh(key)

    def move(self, key, d):
        i = self.sel(key)
        if i is not None and 0 <= i + d < len(self.r[key]):
            L = self.r[key]
            L[i], L[i + d] = L[i + d], L[i]
            self.refresh(key)
            self.lists[key].selection_set(i + d)

    def edit(self, key):
        i = self.sel(key)
        if i is None:
            return
        it = self.r[key][i]
        d = self.cond_form(it["t"], it) if key == "conds" else self.act_form(it["t"], it)
        if d is not None:
            self.r[key][i] = d
            self.refresh(key)
            self.lists[key].selection_set(i)

    def add_cond(self, t):
        d = self.cond_form(t, {})
        if d is not None:
            self.r["conds"].append(d)
            self.refresh("conds")

    def add_act(self, t):
        d = {"t": t} if t in ("shot", "bright", "sleep", "stop") else self.act_form(t, {})
        if d is not None:
            self.r["acts"].append(d)
            self.refresh("acts")

    def anchor(self, form=None):
        return next((c["img"] for c in self.r["conds"] if c["t"] == "seen" and c.get("img")), None)

    def quick_two(self):
        name = simpledialog.askstring(APP_TITLE, "یک نام برای تصویر لنگر بنویسید (مثلاً «آیکون»):", parent=self)
        if not name or not name.strip():
            return
        got = self.app.grab_rects(["دور تصویر لنگر (مثلاً آیکون) یک کادر بکشید", "حالا دور هدف (جای کلیک یا ناحیه‌ی عدد) یک کادر بکشید"])
        if not got or len(got[0]) < 2:
            return
        (a, t), img = got[0][:2], got[1]
        meta = self.app.store_crop(name.strip(), img, a)
        if meta:
            self.r["conds"].append({"t": "seen", "img": meta["id"]})
            self.r["acts"].append({"t": "click", "mode": "rect", "rel": [t[0] - a[0], t[1] - a[1], t[2] - t[0], t[3] - t[1]]})
            self.refresh("conds")
            self.refresh("acts")

    # --- condition / action forms
    def cond_form(self, t, c):
        ask = self.app.ask
        imgf = {"k": "imgs", "l": "تصویر (چندتا هم می‌شود)", "t": "imgs", "req": True,
                "h": "روی هر مورد کلیک کنید تا انتخاب یا لغو شود. اگر چند آیکون مختلف انتخاب کنید، دیدن هرکدام کافی است. «+ جدید»: ثبت از روی صفحه."}
        if t in ("seen", "unseen"):
            f, v = [imgf], {"imgs": seen_ids(c), "thr": c.get("thr", 0) or ""}
            nl = near_list(c)
            complex_near = len(nl) > 1 or any("imgs" in n for n in nl)  # e.g. the Black Friday profile: several images
            if t == "seen" and not complex_near:
                n = nl[0] if nl else {}
                f += [{"k": "near_on", "l": "فقط اگر کنار تصویر دیگری باشد / نباشد", "t": "bool", "h": "مثلاً آیکون فقط کنار برچسب «۵۰٪»، یا نه کنار «ناموجود»."},
                      {"k": "near_img", "l": "آن تصویر دیگر", "t": "img"},
                      {"k": "near_want", "l": "شرط", "t": "choice", "o": [("1", "کنارش باشد"), ("0", "کنارش نباشد")]},
                      {"k": "near_r", "l": "حداکثر فاصله (پیکسل)", "t": "num", "h": "با تغییر اندازه‌ی تصویر خودکار متناسب می‌شود."}]
                v.update(near_on=bool(n.get("img")), near_img=n.get("img"), near_want="1" if n.get("want", True) else "0", near_r=n.get("radius", 300))
            if t == "seen" and HAS_DIGITS:
                nm = c.get("num") or {}
                f += [{"k": "num_on", "l": "عدد کنار تصویر خوانده شود", "t": "bool", "h": "اول در «آموزش ارقام» نمونه‌ها را ثبت کنید."},
                      {"k": "num_rel", "l": "ناحیه‌ی عدد", "t": "rel", "anchor": lambda form: (form.get("imgs") or [None])[0]},
                      {"k": "num_list", "l": "اعداد مجاز", "t": "str", "h": "فهرست و بازه، مثلاً: 1000000-4000000, 4500000"},
                      {"k": "num_fail", "l": "اگر عدد خوانده نشد", "t": "choice", "o": [("skip", "رد کن"), ("click", "کلیک کن")]}]
                v.update(num_on=bool(nm), num_rel=nm.get("rel"), num_list=nm.get("list", ""), num_fail=nm.get("fail", "skip"))
            f.append({"k": "thr", "l": "حداقل شباهت (۰ = پیش‌فرض)", "t": "num", "h": "اگر تصویر اشتباه تشخیص داده می‌شود بالاتر ببرید (مثلاً ۰٫۸۸)."})
            note = "این شرط چند تصویر «کنار / نه کنار» دارد؛ آن‌ها اینجا نشان داده نمی‌شوند و بدون تغییر می‌مانند." if complex_near else ""
            d = ask("شرط: تصویر " + ("دیده شود" if t == "seen" else "دیده نشود"), f, v, note=note)
            if d is None:
                return None
            out = {"t": t, "img": d["imgs"][0]}
            if len(d["imgs"]) > 1:
                out["imgs"] = d["imgs"]
            if d["thr"] > 0:
                out["thr"] = min(0.99, d["thr"])
            if complex_near:
                out["near"] = c["near"]
            elif t == "seen" and d["near_on"] and d["near_img"]:
                out["near"] = {"img": d["near_img"], "radius": d["near_r"] or 300, "want": d["near_want"] == "1"}
            if t == "seen" and HAS_DIGITS and d.get("num_on") and d.get("num_rel"):
                out["num"] = {"rel": d["num_rel"], "list": d["num_list"], "fail": d["num_fail"]}
            return out
        if t == "rule":
            f = [{"k": "rule", "l": "قانون", "t": "rule", "req": True, "exclude": self.r["id"]},
                 {"k": "state", "l": "وضعیت", "t": "choice", "o": [("done", "انجام شده باشد"), ("notdone", "انجام نشده باشد"),
                                                                    ("finished", "تمام شده باشد (انجام یا رد)")]}]
            d = ask("شرط: قانون دیگر", f, {"rule": c.get("rule"), "state": c.get("state", "done")})
            return None if d is None else {"t": t, "rule": d["rule"], "state": d["state"]}
        if t == "after":
            f = [{"k": "secs", "l": "چند ثانیه", "t": "num"},
                 {"k": "from", "l": "از چه زمانی", "t": "choice", "o": lambda: [("start", "شروع اجرا")] + [
                     (r["id"], "اجرای «%s»" % (r.get("name") or "؟")) for r in self.app.cfg["rules"] if r["id"] != self.r["id"]]}]
            d = ask("شرط: گذشتن زمان", f, {"secs": c.get("secs", 5), "from": c.get("from", "start")})
            return None if d is None else {"t": t, "secs": d["secs"], "from": d["from"]}
        f = [{"k": "at", "l": "ساعت", "t": "str", "req": True, "h": "مثلاً 20:00؛ از این ساعت به بعد شرط برقرار است."}]
        d = ask("شرط: ساعت مشخص", f, {"at": c.get("at", "")}, check=lambda r: None if parse_hhmm(r["at"]) else "ساعت باید به شکل 20:00 باشد.")
        return None if d is None else {"t": t, "at": d["at"].strip()}

    def act_form(self, t, a):
        ask = self.app.ask
        if t == "click":
            f = [{"k": "mode", "l": "کجا کلیک شود", "t": "choice", "o": [("match", "وسط تصویر دیده‌شده"), ("rect", "داخل کادر کنار تصویر"), ("xy", "نقطه‌ی ثابت روی صفحه")]},
                 {"k": "rel", "l": "کادر کنار تصویر", "t": "rel", "anchor": self.anchor, "h": "فقط برای حالت «داخل کادر». برنامه فاصله و اندازه را نسبت به تصویر ذخیره می‌کند."},
                 {"k": "xy", "l": "نقطه‌ی ثابت", "t": "pt", "h": "فقط برای حالت «نقطه‌ی ثابت»."},
                 {"k": "button", "l": "دکمه", "t": "choice", "o": [("left", "چپ"), ("right", "راست"), ("middle", "وسط")]},
                 {"k": "double", "l": "کلیک دوبل", "t": "bool"},
                 {"k": "rand", "l": "نقطه‌ی تصادفی داخل کادر", "t": "bool"}]
            v = {"mode": a.get("mode", "match"), "rel": a.get("rel"), "xy": a.get("xy"), "button": a.get("button", "left"),
                 "double": a.get("double", False), "rand": a.get("rand", False)}

            def chk(r):
                if r["mode"] == "rect" and not r["rel"]:
                    return "کادر کنار تصویر را بکشید."
                if r["mode"] == "xy" and not r["xy"]:
                    return "نقطه‌ی ثابت را انتخاب کنید."
                return None
            d = ask("کار: کلیک", f, v, check=chk)
            return None if d is None else {"t": t, **{k: d[k] for k in ("mode", "button", "double", "rand")}, "rel": d["rel"], "xy": d["xy"]}
        specs = {"type": ([{"k": "text", "l": "متن یا عدد", "t": "str", "h": "فارسی هم پشتیبانی می‌شود. {num} = عدد خوانده‌شده."}], "text"),
                 "key": ([{"k": "keys", "l": "کلید", "t": "str", "req": True, "h": "مثلاً enter یا tab یا ctrl+a یا ctrl+shift+s یا f5"}], "keys"),
                 "scroll": ([{"k": "amount", "l": "مقدار", "t": "num", "h": "مثبت = بالا، منفی = پایین (هر ۱ = یک گام چرخ ماوس)."}], "amount"),
                 "wait": ([{"k": "secs", "l": "ثانیه", "t": "num"}], "secs"),
                 "halo": ([{"k": "text", "l": "متن هاله", "t": "str"}], "text"),
                 "run": ([{"k": "rule", "l": "قانون", "t": "rule", "req": True, "exclude": self.r["id"]}], "rule"),
                 "refresh": ([{"k": "wait", "l": "صبر بعد از رفرش (ثانیه)", "t": "num", "h": "زمان بارگذاری صفحه، قبل از کار بعدی."}], None),
                 "scan": ([{"k": "speed", "l": "سرعت (گام چرخ ماوس در ثانیه)", "t": "num",
                            "h": "هر گام حدود ۱۰۰ پیکسل. ۳ = آرام، ۸ = سریع. اگر آیکون را وسط پیمایش از دست می‌دهد، کمتر کنید."},
                           {"k": "dir", "l": "جهت", "t": "choice", "o": [("down", "به پایین"), ("up", "به بالا")]},
                           {"k": "end", "l": "پایان پیمایش", "t": "choice",
                            "o": [("bottom", "وقتی صفحه دیگر پایین‌تر نرفت"), ("time", "فقط با حداکثر زمان")]},
                           {"k": "max_secs", "l": "حداکثر زمان پیمایش (ثانیه)", "t": "num", "h": "جلوی پیمایش بی‌پایان را می‌گیرد."},
                           {"k": "watch", "l": "با آماده شدن این قانون‌ها بایست و اجرا کن", "t": "rules", "exclude": self.r["id"],
                            "h": "مثلاً قانون «حراج‌جمعه»: پیمایش می‌ایستد، اطمینان می‌گیرد، کلیک می‌کند و بقیه‌ی کارهای این قانون انجام نمی‌شود."},
                           {"k": "settle", "l": "مکث بعد از ایستادن (ثانیه)", "t": "num", "h": "تا صفحه کاملاً بایستد و تصویر دقیق دیده شود."},
                           {"k": "still_secs", "l": "چند ثانیه ثابت ماندن یعنی انتهای صفحه", "t": "num",
                            "h": "اگر صفحه جاهای خالی طولانی دارد، بیشتر کنید."},
                           {"k": "region", "l": "ناحیه‌ی بررسی تغییر صفحه", "t": "area",
                            "h": "اختیاری. اگر بخشی از صفحه همیشه متحرک است (ویدیو)، فقط ناحیه‌ی ثابت را بکشید."},
                           {"k": "center", "l": "ماوس وسط صفحه برود", "t": "bool", "h": "چرخ ماوس روی پنجره‌ای کار می‌کند که ماوس روی آن است."}], None),
                 "sound": ([{"k": "kind", "l": "نوع بوق", "t": "choice", "o": [("hit", "دیدن"), ("done", "پایان"), ("fail", "مشکل"), ("none", "بدون بوق")]},
                            {"k": "text", "l": "جمله‌ی انگلیسی", "t": "str", "h": "خالی = فقط بوق. صدای فارسی روی ویندوز معمولاً نصب نیست."}], None)}
        f, key = specs[t]
        d = ask("کار: " + dict(ACT_KINDS)[t], f, {"kind": "hit", "amount": -3, "secs": 1, "speed": 3, "dir": "down", "end": "bottom", "max_secs": 180,
                                                         "settle": 0.3, "still_secs": 1.0, "center": True, "wait": 2, **a},
                check=(lambda r: self.bad_keys(r["keys"])) if t == "key" else ((lambda r: None if r["speed"] > 0 else "سرعت باید بیشتر از صفر باشد") if t == "scan" else None))
        if d is None:
            return None
        if t == "key":
            d["keys"] = d["keys"].strip()
        return {"t": t, **d}

    def bad_keys(self, spec):
        try:
            parse_keys(spec)
        except ValueError:
            return "نام کلید درست نیست. نمونه: enter ، tab ، ctrl+a ، f5"
        return None

    # --- step 3
    def page_end(self):
        fr, r = tk.Frame(self.body), self.r
        self.vv = {k: tk.StringVar(value=str(r.get(k, d))) for k, d in
                   (("name", ""), ("times", 1), ("gap", 0), ("prio", 0), ("timeout", 0), ("confirm", 1))}
        self.v_on = tk.BooleanVar(value=r.get("on", True))
        self.v_opt = tk.BooleanVar(value=bool(r.get("opt", False)))
        by = BY_LABELS + MORE_BY
        self.by_map = {lb: k for k, lb in by}
        self.v_by = tk.StringVar(value=dict((k, lb) for k, lb in by).get(r.get("by", "score"), by[0][1]))
        self.v_tie = tk.StringVar(value=dict(BY_LABELS).get(r.get("tie", "top"), "بالاترین موقعیت"))
        self.area = r.get("area")

        def row(parent, label, widget_fn, hint=""):
            b = tk.Frame(parent)
            b.pack(fill="x", pady=3)
            tk.Label(b, text=label, font=FONT, anchor="e", wraplength=250, justify="right").pack(side="right", padx=(8, 0))
            widget_fn(b).pack(side="left", fill="x", expand=True)
            if hint:
                tk.Label(parent, text=hint, font=SMALL, fg=GRAY, anchor="e", justify="right", wraplength=480).pack(fill="x")
        row(fr, "نام قانون", lambda p: tk.Entry(p, textvariable=self.vv["name"], font=FONT, justify="center"))
        row(fr, "تعداد دفعات اجرا", lambda p: tk.Entry(p, textvariable=self.vv["times"], font=FONT, width=8, justify="center"),
            "۱ = یک بار. ۰ = تا وقتی دیده می‌شود دوباره و دوباره.")
        tk.Checkbutton(fr, text="قانون روشن باشد", variable=self.v_on, font=FONT).pack(anchor="e", pady=4)
        self.v_adv = tk.BooleanVar(value=False)
        tk.Checkbutton(fr, text="گزینه‌های پیشرفته", variable=self.v_adv, font=FONT, command=self.toggle_adv).pack(anchor="e")
        self.adv = tk.LabelFrame(fr, text=" پیشرفته ", font=SMALL, labelanchor="ne")
        row(self.adv, "فاصله‌ی بین اجراها (ثانیه)", lambda p: tk.Entry(p, textvariable=self.vv["gap"], font=FONT, width=8, justify="center"))
        row(self.adv, "مهلت رد شدن (ثانیه، ۰ = بدون مهلت)", lambda p: tk.Entry(p, textvariable=self.vv["timeout"], font=FONT, width=8, justify="center"),
            "اگر شرط‌های «قانون/زمان» برقرار شده ولی تصویر تا این مدت دیده نشد، قانون رد می‌شود و بقیه ادامه می‌دهند.")
        tk.Checkbutton(self.adv, text="اختیاری: برای تمام شدن اجرا لازم نیست", variable=self.v_opt, font=FONT).pack(anchor="e")
        row(self.adv, "تعداد دیدن پشت‌سرهم برای تأیید", lambda p: tk.Entry(p, textvariable=self.vv["confirm"], font=FONT, width=8, justify="center"))
        row(self.adv, "اولویت این قانون (عدد بزرگ‌تر زودتر)", lambda p: tk.Entry(p, textvariable=self.vv["prio"], font=FONT, width=8, justify="center"))
        row(self.adv, "اگر چند تصویر هم‌زمان دیده شد", lambda p: ttk.Combobox(p, textvariable=self.v_by, values=[lb for _, lb in by], state="readonly", font=FONT))
        row(self.adv, "در حالت تساوی", lambda p: ttk.Combobox(p, textvariable=self.v_tie, values=[lb for _, lb in BY_LABELS], state="readonly", font=FONT))
        ab = tk.Frame(self.adv)
        ab.pack(fill="x", pady=3)
        self.area_lb = tk.Label(ab, font=SMALL, fg=GRAY)
        tk.Label(ab, text="ناحیه‌ی جست‌وجو", font=FONT).pack(side="right", padx=(8, 0))
        tk.Button(ab, text="کشیدن ناحیه", font=SMALL, command=self.pick_area).pack(side="left")
        tk.Button(ab, text="کل صفحه", font=SMALL, command=lambda: self.set_area(None)).pack(side="left", padx=3)
        self.area_lb.pack(side="left", padx=4)
        self.set_area(self.area)
        return fr

    def toggle_adv(self):
        if self.v_adv.get():
            self.adv.pack(fill="x", pady=4)
        else:
            self.adv.pack_forget()

    def set_area(self, a):
        self.area = a
        self.area_lb.configure(text="کل صفحه" if not a else "ناحیه‌ی %d×%d" % (a[2] - a[0], a[3] - a[1]))

    def pick_area(self):
        got = self.app.grab_rects(["ناحیه‌ای را که باید جست‌وجو شود بکشید"])
        if got:
            self.set_area(list(got[0][0]))

    def save(self):
        r, v = self.r, self.vv
        r["name"] = v["name"].get().strip() or "قانون بدون نام"
        r["on"], r["join"], r["area"], r["opt"] = bool(self.v_on.get()), self.v_join.get(), self.area, bool(self.v_opt.get())
        r["times"], r["gap"], r["timeout"] = int(num(v["times"].get(), 1)), num(v["gap"].get()), num(v["timeout"].get())
        r["confirm"], r["prio"] = max(1, int(num(v["confirm"].get(), 1))), int(num(v["prio"].get()))
        r["by"] = self.by_map.get(self.v_by.get(), "score")
        r["tie"] = {lb: k for k, lb in BY_LABELS}.get(self.v_tie.get(), "top")
        if not r["acts"] and not messagebox.askyesno(APP_TITLE, "این قانون هیچ کاری ندارد. باز هم ذخیره شود؟", parent=self):
            return
        self.res = r
        self.destroy()
# ===== END OF PART 01 (lines: 588) =====


# ------------------------------------------------------------------ screen capture and image library
class CaptureMixin:
    def top(self):
        return self.root.grab_current() or self.root

    def ask(self, title, fields, values=None, note="", check=None):
        return Form(self, title, fields, values, note, check).res

    def grab_rects(self, labels):
        """Shows a frozen screenshot full-screen; the user draws one rectangle per label.
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
        cv = tk.Canvas(top, cursor="crosshair", highlightthickness=0)
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
            kx, ky = img.width / cw, img.height / ch
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

    def store_crop(self, name, img, rect):
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
        got = self.grab_rects(["دور «%s» یک کادر بکشید" % name.strip()])
        return self.store_crop(name.strip(), got[1], got[0][0]) if got else None

    def import_image_file(self, name=None, meta=None):
        path = filedialog.askopenfilename(parent=self.top(), title="انتخاب فایل تصویر",
                                          filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.webp"), ("All files", "*.*")])
        if not path:
            return None
        arr = imread(path)
        if arr is None or min(arr.shape[:2]) < 8:
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
        self.lib.bind("<<ListboxSelect>>", lambda e: self.show_preview())
        pv = tk.Frame(tab, height=190, relief="groove", bd=1)
        pv.pack(fill="x", padx=12, pady=4)
        pv.pack_propagate(False)
        self.prev = tk.Label(pv, text="(برای دیدن تصویر، یک مورد را از فهرست انتخاب کنید)", font=FONT)
        self.prev.pack(expand=True)
        bar = tk.Frame(tab)
        bar.pack(fill="x", padx=12, pady=4)
        for txt, fn in [("+ ثبت از روی صفحه", lambda: self.capture_image()), ("از فایل", lambda: self.import_image_file()),
                        ("تعویض تصویر", self.lib_replace), ("تغییر نام", self.lib_rename), ("حذف", self.lib_delete)]:
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
            self.lib.insert("end", "%d.  %s    (در %d قانون)" % (i, m["name"], self.usage(m["id"])))
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
            from PIL import Image, ImageTk
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
        name = m and simpledialog.askstring(APP_TITLE, "نام جدید:", initialvalue=m["name"], parent=self.root)
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
        wrap.pack(fill="both", expand=True, padx=8)
        self.rc = tk.Canvas(wrap, highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.rc.yview)
        self.rf = tk.Frame(self.rc)
        self.rf.bind("<Configure>", lambda e: self.rc.configure(scrollregion=self.rc.bbox("all")))
        self.rc_win = self.rc.create_window((0, 0), window=self.rf, anchor="nw")
        self.rc.bind("<Configure>", lambda e: self.rc.itemconfigure(self.rc_win, width=e.width))
        self.rc.configure(yscrollcommand=sb.set)
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
            messagebox.showinfo(APP_TITLE, "اول قانون هدف (مثلاً حراج‌جمعه) را بسازید: قالب‌ها ← «حراج‌جمعه کامل» یا «کلیک روی یک تصویر».")
            return
        first = next((r for r in rules if "حراج" in (r.get("name") or "")), rules[0])
        f = [{"k": "watch", "l": "قانون هدف (با دیدن آن بایست و کلیک کن)", "t": "rule", "req": True},
             {"k": "speed", "l": "سرعت پیمایش (گام چرخ ماوس در ثانیه)", "t": "num", "h": "هر گام حدود ۱۰۰ پیکسل. ۳ = آرام، ۸ = سریع."},
             {"k": "end", "l": "پیمایش تا کجا", "t": "choice", "o": [("bottom", "تا انتهای صفحه"), ("time", "فقط تا حداکثر زمان")]},
             {"k": "max_secs", "l": "حداکثر زمان هر پیمایش (ثانیه)", "t": "num"},
             {"k": "refresh", "l": "در انتهای صفحه رفرش کن و دوباره پیمایش کن", "t": "bool"},
             {"k": "wait", "l": "صبر بعد از رفرش (ثانیه)", "t": "num", "h": "زمان بارگذاری صفحه."},
             {"k": "cycles", "l": "تعداد دورها (۰ = تا وقتی هدف پیدا شود)", "t": "num"}]
        d = self.ask("گشتن صفحه برای آیکون", f, {"watch": first["id"], "speed": 3, "end": "bottom", "max_secs": 180, "refresh": True, "wait": 3, "cycles": 0},
                     note="قانون جدید: صفحه با این سرعت پایین می‌رود. اگر قانون هدف دیده شد می‌ایستد، مطمئن می‌شود، کلیک می‌کند و بقیه‌ی مراحل ادامه پیدا می‌کنند.",
                     check=lambda r: None if r["speed"] > 0 else "سرعت باید بیشتر از صفر باشد")
        if not d:
            return
        r = new_rule("گشتن صفحه برای «%s»" % rule_name(self.cfg, d["watch"]))
        r["conds"] = [{"t": "rule", "rule": d["watch"], "state": "notdone"}]
        r["acts"] = [{"t": "scan", "speed": d["speed"], "dir": "down", "end": d["end"], "max_secs": d["max_secs"] or 180, "watch": [d["watch"]],
                      "settle": 0.3, "still_secs": 1.0, "center": True, "region": None}]
        if d["refresh"]:
            r["acts"].append({"t": "refresh", "wait": d["wait"]})
        r["times"], r["gap"], r["opt"] = int(d["cycles"]), 0.5, True
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
        for i, r in enumerate(self.cfg["rules"]):
            text = describe_rule(self.cfg, r)
            if q and q not in text and q not in r.get("name", ""):
                continue
            shown += 1
            fr = tk.Frame(self.rf, relief="groove", bd=2, padx=6, pady=4)
            fr.pack(fill="x", padx=2, pady=3)
            top = tk.Frame(fr)
            top.pack(fill="x")
            var = tk.BooleanVar(value=r.get("on", True))
            tk.Checkbutton(top, text="روشن", variable=var, font=SMALL, command=lambda r=r, v=var: self.toggle_rule(r, v)).pack(side="right")
            tk.Label(top, text="%d. %s" % (i + 1, r.get("name") or "بدون نام"), font=BOLD, anchor="e",
                     fg="#222" if r.get("on", True) else "#999").pack(side="right", padx=6)
            tk.Label(fr, text=text, font=SMALL, wraplength=430, justify="right", anchor="e",
                     fg="#333" if r.get("on", True) else "#999").pack(fill="x")
            bar = tk.Frame(fr)
            bar.pack(fill="x", pady=(4, 0))
            for txt, fn in [("ویرایش", self.edit_rule), ("آزمایش", self.test_rule), ("کپی", self.copy_rule),
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
        self.on_log("قانون «%s» اضافه شد" % r["name"], "ok")

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
        if messagebox.askyesno(APP_TITLE, "قانون «%s» حذف شود؟" % r.get("name", "")):
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
                self.post(self.on_probe, lines, boxes, mon)
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
            r = new_rule("کلیک کنار " + meta["name"])
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
                          "اگر هر دو پر باشند، هرکدام زودتر برسد. ۳۰ ثانیه قبل از خواب یک بوق هشدار می‌زند و می‌توانید لغو کنید. "
                          "کار «خواب کامپیوتر» داخل قانون‌ها هم همین شمارش معکوس را دارد.",
                 font=SMALL, justify="right", wraplength=470, anchor="e").pack(fill="x", padx=8, pady=6)
        self.make_rows(sl, [("sleep_at", "رفتن به خواب در ساعت (مثلاً 02:30)", "hhmm"), ("sleep_hours", "یا چند ساعت دیگر (مثلاً 1٫5)", "float")])
        self.sleep_btn = tk.Button(sl, text="فعال‌سازی زمان‌بندی خواب", font=BOLD, command=self.toggle_sleep, pady=6)
        self.sleep_btn.pack(fill="x", padx=8, pady=(8, 4))
        self._sleep_btn_style = (self.sleep_btn.cget("bg"), self.sleep_btn.cget("fg"))
        self.sleep_info = tk.Label(sl, text="زمان‌بندی خواب غیرفعال است", font=FONT, anchor="e")
        self.sleep_info.pack(fill="x", padx=8, pady=(0, 4))
        tk.Button(sl, text="همین حالا: توقف همه‌چیز و رفتن به خواب", font=FONT, command=self.sleep_now).pack(fill="x", padx=8, pady=(0, 8))

    def toggle_sleep(self):
        if self.sleep_deadline:
            self.cancel_sleep("زمان‌بندی خواب لغو شد")
            return
        if not self.read_settings():
            return
        st, now, cands = self.cfg["settings"], time.time(), []
        hm = parse_hhmm(st["sleep_at"]) if st["sleep_at"] else None
        if hm:
            lt = time.localtime(now)
            t = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hm[0], hm[1], 0, 0, 0, -1))
            if t <= now:
                t = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + 1, hm[0], hm[1], 0, 0, 0, -1))
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


# ------------------------------------------------------------------ profiles (a folder per profile)
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
            messagebox.showerror(APP_TITLE, "این نام قابل استفاده نیست.")
            return
        d = base_dir() / "profiles" / name
        if d.exists():
            if not messagebox.askyesno(APP_TITLE, "پروفایل «%s» قبلاً هست. جایگزین شود؟" % name):
                return
            shutil.rmtree(d, ignore_errors=True)
        (d / "images").mkdir(parents=True, exist_ok=True)
        (d / "profile.json").write_text(json.dumps(self.cfg, ensure_ascii=False, indent=1), encoding="utf-8")
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
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".json", initialfile="profile.json",
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
        self.on_log("پروفایل «%s» بارگذاری شد" % name, "ok")

    def delete_profile(self):
        name = self.prof_var.get().strip()
        if name and messagebox.askyesno(APP_TITLE, "پروفایل «%s» برای همیشه حذف شود؟" % name):
            shutil.rmtree(base_dir() / "profiles" / name, ignore_errors=True)
            self.refresh_profiles()
            self.on_log("پروفایل «%s» حذف شد" % name, "ok")


# ------------------------------------------------------------------ main window
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
         ("announce_end", "اعلام پایان وقتی همه‌ی قانون‌ها تمام شد"), ("always_on_top", "پنجره همیشه روی بقیه")]
HK_KEYS = [("hk_start", "شروع"), ("hk_stop", "توقف"), ("hk_pause", "مکث / ادامه")]
HK_CHOICES = ["F%d" % i for i in range(1, 13)] + ["ندارد"]
HK_DEFAULT = {"hk_start": "F8", "hk_stop": "F8", "hk_pause": "F9"}
LIMITS = {"threshold": (0.5, 0.95), "scale": (0.0, 5.0), "monitor": (1, 16), "tick": (0.005, 1.0), "beep_seconds": (1, 30)}


class App(CaptureMixin, RulesMixin, SleepMixin, ProfileMixin):
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
        """First start after the upgrade: the old config.json of the Black Friday bot becomes rules."""
        old_p = base_dir() / "config.json"
        if (base_dir() / CONFIG_NAME).exists() or not old_p.exists():
            return None
        try:
            old = json.loads(old_p.read_text(encoding="utf-8"))
            if not profiles.is_legacy(old):
                return None
            self.cfg = profiles.convert_legacy(old, base_dir() / "templates_user")
            save_config(self.cfg)
            return "تنظیمات و مراحل قبلی حراج‌جمعه به %d قانون تبدیل شد (config.json دست‌نخورده ماند)" % len(self.cfg["rules"])
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
        for tag, col in (("hit", "#c0151a"), ("ok", "#17803d"), ("warn", "#b36b00"), ("error", "#c0151a")):
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
        self.prof_box = ttk.Combobox(prow, textvariable=self.prof_var, state="readonly", font=FONT, width=16)
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
            self.sv[key] = tk.BooleanVar(value=bool(st.get(key)))
            cmd = (lambda: self.root.attributes("-topmost", bool(self.sv["always_on_top"].get()))) if key == "always_on_top" else None
            tk.Checkbutton(box, text=label, variable=self.sv[key], font=FONT, command=cmd).pack(anchor="e", padx=8)
        hkf = tk.LabelFrame(tab, text=" کلیدهای میان‌بر (در همه‌ی برنامه‌ها کار می‌کنند) ", font=SMALL, labelanchor="ne")
        hkf.pack(fill="x", padx=10, pady=4)
        for key, label in HK_KEYS:
            hr = tk.Frame(hkf)
            hr.pack(side="right", padx=8, pady=3)
            tk.Label(hr, text=label, font=SMALL).pack(side="right", padx=3)
            self.sv[key] = tk.StringVar(value=self.hk(key))
            ttk.Combobox(hr, textvariable=self.sv[key], values=HK_CHOICES, state="readonly", width=7, font=SMALL).pack(side="right")
        self.adv_on = tk.BooleanVar(value=False)
        tk.Checkbutton(tab, text="گزینه‌های پیشرفته", variable=self.adv_on, font=FONT, command=self.toggle_adv).pack(anchor="e", padx=14)
        self.adv_box = tk.LabelFrame(tab, text=" پیشرفته ", font=FONT, labelanchor="ne")
        self.make_rows(self.adv_box, SPECS_ADV)
        tk.Button(self.adv_box, text="تشخیص مقیاس از روی صفحه‌ی فعلی (لیست محصولات)", font=SMALL, command=self.do_calibrate).pack(pady=4)

    def do_calibrate(self):
        if self.bot:
            return
        if "anchor" not in EMBEDDED:
            messagebox.showinfo(APP_TITLE, "تصویر راهنمای مقیاس (assets.py) پیدا نشد.")
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
            self.adv_box.pack(fill="x", padx=10, pady=4)
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
                    messagebox.showerror(APP_TITLE, "«%s» باید به شکل 20:00 باشد (یا خالی بماند)." % label, parent=self.top())
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

    def on_status(self, text, kind="idle"):
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
        self.btn.configure(text="توقف  (%s)" % (self.hk("hk_stop") if self.hk("hk_stop") != "ندارد" else self.hk("hk_start")), bg="#c0151a")
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
