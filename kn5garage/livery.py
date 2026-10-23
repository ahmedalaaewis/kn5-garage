"""Livery studio: take a car's paint textures out as PNG files to edit in any image program,
watch them for changes, and install the result as a new skin.

Work files live in <output>/work/livery/<car>/<session>/; car folders are only written
when a livery is installed (through an edits.Target)."""

import io
import json
import re
import secrets
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from . import carfiles
from .convert import is_paint_material, walk
from .edits import EditError
from .kn5 import read_kn5
from .textures import decode_image, dds_bytes

SKIN_NAME = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
SESSION = re.compile(r"^[0-9a-f]{12}$")
MAX_SIDE = 8192


def _session_dir(work_root, car_id, sid):
    if not SESSION.match(str(sid)):
        raise EditError("unknown livery session")
    safe = re.sub(r"[^A-Za-z0-9_\-]", "_", car_id)[:64]
    return Path(work_root) / "livery" / safe / sid


def start(car_dir, car_id, skin, work_root, paint_names=None):
    """Export the paint textures of a skin (and a UV template for each) to a new work folder."""
    car_dir = Path(car_dir)
    main = carfiles.find_main_kn5(car_dir)
    if main is None:
        raise EditError("this car has no model")
    model = read_kn5(main)
    if model["protected"]:
        raise EditError("protected cars can't get new liveries: their textures are encrypted")
    wanted = {n.lower() for n in paint_names} if paint_names else None
    mats = [i for i, m in enumerate(model["materials"])
            if (m["name"].lower() in wanted if wanted is not None else is_paint_material(m)) and m["maps"].get("txDiffuse")]
    if not mats:
        raise EditError("no paint found in this car; choose the paint parts in the 3D viewer first")
    skin_name, skin_files = carfiles.load_skin(car_dir, skin or None)
    sid = secrets.token_hex(6)
    folder = _session_dir(work_root, car_id, sid)
    folder.mkdir(parents=True)
    textures, seen = [], set()
    for mi in mats:
        tex = model["materials"][mi]["maps"]["txDiffuse"]
        if tex.lower() in seen:
            continue
        seen.add(tex.lower())
        data = skin_files.get(tex.lower()) or skin_files.get(Path(tex).stem.lower()) or model["textures"].get(tex)
        if not data:
            continue
        img = decode_image(data).convert("RGBA")
        stem = re.sub(r"[^A-Za-z0-9_\-]", "_", Path(tex).stem)[:60] or "paint"
        file = stem + ".png"
        img.save(folder / file)
        uv_template(model, {i for i in mats if model["materials"][i]["maps"]["txDiffuse"].lower() == tex.lower()},
                    img.size).save(folder / (stem + "_uv.png"))
        textures.append({"texture": tex, "file": file, "uv": stem + "_uv.png", "width": img.size[0], "height": img.size[1]})
    if not textures:
        raise EditError("the paint textures of this skin couldn't be found")
    info = {"car": str(car_dir), "carId": car_id, "skin": skin_name, "textures": textures, "created": time.time()}
    (folder / "session.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    return dict(info, session=sid, folder=str(folder))


def uv_template(model, material_ids, size):
    """The paint meshes' UV layout drawn as thin lines on a transparent image (a guide layer)."""
    w, h = size
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for n in walk(model["root"]):
        if n["cls"] == 1 or n.get("material") not in material_ids:
            continue
        uv = np.asarray(n["verts"])[:, 6:8]
        idx = np.asarray(n["indices"])[: len(n["indices"]) // 3 * 3].reshape(-1, 3)
        if len(idx) > 200000:
            idx = idx[:200000]
        for tri in idx:
            pts = uv[tri]
            if np.any(pts < -0.01) or np.any(pts > 1.01):
                continue  # tiled UVs: not part of the main layout
            xy = [(float(u) * w, float(v) * h) for u, v in pts]
            d.line(xy + [xy[0]], fill=(255, 64, 32, 200), width=1)
    return img


def load(work_root, car_id, sid):
    folder = _session_dir(work_root, car_id, sid)
    try:
        info = json.loads((folder / "session.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise EditError("unknown livery session")
    return folder, info


def work_file(work_root, car_id, sid, name):
    folder, info = load(work_root, car_id, sid)
    names = {t["file"] for t in info["textures"]} | {t["uv"] for t in info["textures"]}
    if name not in names:
        raise EditError("unknown file")
    return folder / name


def status(work_root, car_id, sid):
    folder, info = load(work_root, car_id, sid)
    out = []
    for t in info["textures"]:
        p = folder / t["file"]
        st = p.stat() if p.exists() else None
        out.append({"file": t["file"], "changed": st.st_mtime_ns if st else 0})
    return out


def use_image(work_root, car_id, sid, texture_file, image_path):
    """Put a picture (any size) in place of a paint texture, fitted to the texture's size."""
    p = work_file(work_root, car_id, sid, texture_file)
    folder, info = load(work_root, car_id, sid)
    t = next(x for x in info["textures"] if x["file"] == texture_file)
    try:
        with Image.open(image_path) as img:
            img.load()
            img = img.convert("RGBA").resize((t["width"], t["height"]), Image.LANCZOS)
    except (OSError, ValueError, Image.DecompressionBombError) as e:
        raise EditError("that picture can't be read: %s" % e)
    img.save(p)


def livery_icon(img):
    """AC's small skin icon: the two main colours of the livery."""
    small = img.convert("RGB").resize((64, 64), Image.BILINEAR)
    pal = small.quantize(colors=2, method=Image.Quantize.MEDIANCUT).getpalette()[:6]
    icon = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(icon)
    d.pieslice([2, 2, 62, 62], 90, 270, fill=tuple(pal[:3]) + (255,))
    d.pieslice([2, 2, 62, 62], 270, 90, fill=tuple(pal[3:6]) + (255,))
    return icon


def encode_like(name, img):
    """Bytes in the format the texture name asks for (.dds, .png or .jpg)."""
    ext = Path(name).suffix.lower()
    buf = io.BytesIO()
    if ext in (".png",):
        img.save(buf, "PNG")
        return buf.getvalue()
    if ext in (".jpg", ".jpeg"):
        img.convert("RGB").save(buf, "JPEG", quality=95)
        return buf.getvalue()
    return dds_bytes(img)


def install(work_root, car_id, sid, target, skin_name, info=None, preview_png=None):
    """Write the new skin into target (an edits.Target): the base skin's files, the edited paint
    textures, ui_skin.json, preview.jpg and livery.png."""
    if not SKIN_NAME.match(str(skin_name)):
        raise EditError("the skin's folder name may only use letters, numbers, - and _ (up to 64)")
    folder, sess = load(work_root, car_id, sid)
    dest = "skins/%s" % skin_name
    if (target.dir / dest).exists():
        raise EditError("a skin named %s already exists" % skin_name)
    base = target.dir / "skins" / sess["skin"] if sess.get("skin") else None
    replaced = {t["texture"].lower() for t in sess["textures"]} | {Path(t["texture"]).stem.lower() for t in sess["textures"]}
    keep_out = {"ui_skin.json", "preview.jpg", "livery.png"}
    if base is not None and base.is_dir():
        for f in sorted(base.iterdir()):
            if f.is_file() and f.name.lower() not in keep_out and f.name.lower() not in replaced \
                    and f.stem.lower() not in replaced:
                target.write("%s/%s" % (dest, f.name), f.read_bytes())
    main_img = None
    for t in sess["textures"]:
        with Image.open(folder / t["file"]) as img:
            img.load()
            if max(img.size) > MAX_SIDE:
                raise EditError("%s is larger than %d pixels" % (t["file"], MAX_SIDE))
            img = img.convert("RGBA")
        main_img = main_img or img
        target.write("%s/%s" % (dest, t["texture"]), encode_like(t["texture"], img))
    info = info or {}
    ui = {"skinname": str(info.get("name") or skin_name)[:60], "drivername": str(info.get("driver") or "")[:60],
          "team": str(info.get("team") or "")[:60], "number": str(info.get("number") or "")[:8], "country": "",
          "priority": 1}
    target.write("%s/ui_skin.json" % dest, json.dumps(ui, indent=2, ensure_ascii=False))
    if preview_png:
        try:
            with Image.open(io.BytesIO(preview_png)) as p:
                p.load()
                buf = io.BytesIO()
                p.convert("RGB").resize((1022, 575), Image.LANCZOS).save(buf, "JPEG", quality=90)
                target.write("%s/preview.jpg" % dest, buf.getvalue())
        except (OSError, ValueError):
            pass
    if main_img is not None:
        buf = io.BytesIO()
        livery_icon(main_img).save(buf, "PNG")
        target.write("%s/livery.png" % dest, buf.getvalue())
    return dest
