# -*- coding: utf-8 -*-
"""Ready-made profile (Black Friday), conversion of the old 'Black Friday bot' settings, profile import/export."""
import json
import base64
from pathlib import Path

from engine import EMBEDDED, new_id, new_rule, fix_cfg, img_dir, imread, imwrite

PROFILE_FORMAT = "autoclicker-profile"
LEGACY_FILES = {"badge": "sale_badge.png", "add": "add_to_cart.png", "cart": "cart_icon.png"}
LEGACY_NAMES = {"badge": "آیکون حراج‌جمعه", "add": "دکمه افزودن به سبد خرید", "cart": "آیکون سبد خرید"}
BF_DEFAULTS = {"threshold": 0.80, "btn_threshold": 0.80, "confirm": 2, "step_timeout": 8.0, "exclude_radius": 300,
               "require_radius": 300, "require_on": True, "shot_on_hit": True, "brightness_on_hit": True,
               "exclude_images": [], "require_images": [], "extra_steps": []}


def has_embedded():
    return all(k in EMBEDDED for k in LEGACY_FILES)


def is_legacy(d):
    """True for the config.json / profile.json of the old single-purpose bot."""
    keys = ("interval", "step_timeout", "extra_steps", "exclude_images", "require_images", "btn_threshold")
    return isinstance(d, dict) and "rules" not in d and any(k in d for k in keys)


def lib_image(images, name, src_dir=None, file=None, emb=None):
    """Adds a library image from a user file (if it exists) or from the embedded image `emb`. Returns its id or None."""
    meta = {"id": new_id("i"), "name": name, "file": ""}
    if file and src_dir and (Path(src_dir) / file).exists():
        arr = imread(Path(src_dir) / file)
        if arr is None or min(arr.shape[:2]) < 8:
            return None
        meta["file"] = meta["id"] + ".png"
        imwrite(img_dir() / meta["file"], arr)
    elif emb and emb in EMBEDDED:
        meta["emb"] = emb
    else:
        return None
    images.append(meta)
    return meta["id"]


def black_friday(opts=None, src_dir=None):
    """The old bot as rules: (images, rules). Own pictures found in src_dir replace the built-in ones.
    Flow: sale badge -> (add to cart, cart icon, extra steps in any order, each skipped after a timeout)."""
    o = dict(BF_DEFAULTS)
    o.update(opts or {})
    images = []
    ids = {}
    for k, fn in LEGACY_FILES.items():
        ids[k] = lib_image(images, LEGACY_NAMES[k], src_dir, fn, k)
        if ids[k] is None:
            raise ValueError("تصاویر آماده‌ی حراج‌جمعه پیدا نشد (فایل assets.py کنار برنامه نیست یا ناقص است)")
    excl = [lib_image(images, x.get("name", "مستثنی"), src_dir, x.get("file")) for x in o["exclude_images"]]
    need = [lib_image(images, x.get("name", "شرط دوم"), src_dir, x.get("file")) for x in o["require_images"]]
    extra = [(x.get("name", "مرحله"), lib_image(images, x.get("name", "مرحله"), src_dir, x.get("file"))) for x in o["extra_steps"]]
    near = [{"img": i, "radius": o["exclude_radius"], "want": False} for i in excl if i]
    if o["require_on"] and any(need):
        near.append({"imgs": [i for i in need if i], "radius": o["require_radius"], "want": True})
    seen = {"t": "seen", "img": ids["badge"], "thr": o["threshold"]}
    if near:
        seen["near"] = near
    acts = [{"t": "sound", "kind": "hit", "text": "Big discount spotted."}, {"t": "halo", "text": "حراج‌جمعه دیده شد"}]
    if o["shot_on_hit"]:
        acts.append({"t": "shot"})
    acts.append({"t": "click"})
    if o["brightness_on_hit"]:
        acts.append({"t": "bright"})
    r_badge = new_rule("حراج‌جمعه")
    r_badge.update(conds=[seen], acts=acts, times=1, confirm=int(o["confirm"]))
    r_add, r_cart = new_rule("افزودن به سبد خرید"), new_rule("آیکون سبد خرید")
    after = {"t": "rule", "rule": r_badge["id"], "state": "done"}
    r_add.update(conds=[{"t": "seen", "img": ids["add"], "thr": o["btn_threshold"]}, dict(after)],
                 acts=[{"t": "click"}], times=1, timeout=o["step_timeout"])
    r_cart.update(conds=[{"t": "seen", "img": ids["cart"], "thr": o["btn_threshold"]}, dict(after)],
                  acts=[{"t": "click"}], times=1, timeout=o["step_timeout"])
    r_retry = new_rule("تلاش دوباره‌ی افزودن")  # the first click may not register: click again if still visible
    r_retry.update(conds=[{"t": "seen", "img": ids["add"], "thr": o["btn_threshold"]}, {"t": "rule", "rule": r_add["id"], "state": "done"},
                          {"t": "rule", "rule": r_cart["id"], "state": "notdone"}, {"t": "after", "secs": 2.5, "from": r_add["id"]}],
                   acts=[{"t": "click"}], times=2, gap=2.5, opt=True)
    rules = [r_badge, r_add, r_cart, r_retry]
    for name, iid in extra:
        if iid:
            r = new_rule(name)
            r.update(conds=[{"t": "seen", "img": iid, "thr": o["btn_threshold"]}, dict(after)], acts=[{"t": "click"}],
                     times=1, timeout=o["step_timeout"])
            rules.append(r)
    return images, rules


def convert_legacy(old, src_dir):
    """Old config.json / profile.json -> new config (same behaviour, as rules). src_dir holds the old image files."""
    cfg = fix_cfg({})
    st = cfg["settings"]
    for k in ("threshold", "monitor", "max_minutes", "start_at", "beep", "beep_seconds", "speak", "halo", "always_on_top",
              "sleep_at", "sleep_hours", "interval_after_hours", "interval_after", "scale"):
        if k in old:
            st[k] = old[k]
    st["refresh"], st["settle"] = float(old.get("interval", 2.5) or 0), 0.5
    cfg["images"], cfg["rules"] = black_friday(old, src_dir)
    return cfg


# ------------------------------------------------------------------ one-file export / import
def export_profile(cfg, path):
    """Writes settings, rules and all pictures into a single JSON file. Returns (rules, images)."""
    out = {"format": PROFILE_FORMAT, "version": 1, "settings": cfg["settings"], "rules": cfg["rules"], "images": []}
    for m in cfg["images"]:
        d = dict(m)
        p = img_dir() / m["file"] if m.get("file") else None
        if p is not None and p.exists():
            d["data"] = base64.b64encode(p.read_bytes()).decode("ascii")
        out["images"].append(d)
    Path(path).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return len(out["rules"]), len(out["images"])


def import_profile(path):
    """Reads a file written by export_profile; pictures are restored into the images folder. Returns a new config."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("format") != PROFILE_FORMAT:
        raise ValueError("این فایل، پروفایل اتو کلیکر نیست")
    images = []
    for m in data.get("images", []):
        m, raw = dict(m), None
        raw = m.pop("data", None)
        if m.get("file"):
            m["file"] = Path(str(m["file"])).name  # never write outside the images folder
        if raw and m.get("file"):
            (img_dir()).mkdir(parents=True, exist_ok=True)
            (img_dir() / m["file"]).write_bytes(base64.b64decode(raw))
        images.append(m)
    return fix_cfg({"settings": data.get("settings"), "images": images, "rules": data.get("rules")})
# ===== END OF FILE (total lines: 135) =====
