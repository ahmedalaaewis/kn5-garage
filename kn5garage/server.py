"""
The KN5 Garage web app: a small HTTP server on 127.0.0.1 and its JSON API.

Security model (see SECURITY.md):
  * it listens on 127.0.0.1 only, so nothing outside this PC can reach it;
  * requests must name this server in the Host header (blocks DNS-rebinding attacks,
    where a web page tricks the browser into treating 127.0.0.1 as its own site);
  * every API call needs a random per-run token that only the app's own page knows,
    sent in the X-Token header (a ?t= query token only works for loading files);
  * cross-site requests are refused (Origin / Sec-Fetch-Site), and the page has a strict
    Content-Security-Policy and can't be framed;
  * car folders are only read; files are only written to the output folder.
"""

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import traceback
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__, blender, carfiles, carinfo, convert, dialogs, paths
from .cache import PreviewCache, ScanCache
from .jobs import Jobs
from .kn5 import Kn5Error
from .settings import Settings
from .userdata import History, Library

PROJECT_DIR = paths.data_dir()        # settings and the default output folder
WEB_DIR = paths.resource_dir() / "web"

MAX_JSON_BODY = 1 << 20          # 1 MB for ordinary API calls
MAX_RENDER_BODY = 130 << 20      # turntable frames and videos
MAX_ROOTS = 64
MAX_IDS = 2000
MAX_SAVED_PRESETS = 50
# export options a saved preset may hold (the names used by the page)
PRESET_OPTIONS = {"preset": str, "paint": (str, type(None)), "paintMode": str, "textureSize": (int, type(None)),
                  "jpegQuality": (int, type(None)), "lightsOn": bool, "blenderScript": bool}

CONTENT_TYPES = {  # fixed, so a Windows registry setting can't break module scripts
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".json": "application/json", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml", ".woff2": "font/woff2", ".glb": "model/gltf-binary", ".txt": "text/plain; charset=utf-8",
}

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), usb=(), serial=(), payment=()",
}


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def re_safe(text):
    return re.sub(r"[^A-Za-z0-9_\- ().]", "_", text).strip(" .")


def safe_summary(car_dir):
    try:
        return carinfo.car_summary(car_dir)
    except Exception as e:
        return {"id": car_dir.name, "name": car_dir.name, "status": "encrypted",
                "statusText": "Couldn't read this car: %s" % e, "specs": {}, "tags": [], "issues": ["unreadable"],
                "kn5": None, "kn5Size": 0}


def mark_duplicates(cars):
    """Cars that look like the same car: same display name, or same folder name in two places."""
    def keys(c):
        return {("name", re.sub(r"[^a-z0-9]", "", (c.get("name") or "").lower())),
                ("folder", re.sub(r"[^a-z0-9]", "", re.sub(r" \(\d+\)$", "", c["id"]).lower()))}
    groups = {}
    for c in cars:
        for k in keys(c):
            if k[1]:
                groups.setdefault(k, []).append(c["id"])
    for c in cars:
        dup = set()
        for k in keys(c):
            if len(groups.get(k, [])) > 1:
                dup.update(groups[k])
        dup.discard(c["id"])
        c["duplicates"] = sorted(dup)


def mark_identical(cars, dirs, cache):
    """Cars whose main model file is the same (size and fingerprint), whatever they're called.
    Only models with the same size are fingerprinted, so this stays fast."""
    by_size = {}
    for c, d in zip(cars, dirs):
        c["identical"] = []
        if c.get("kn5") and c.get("kn5Size"):
            by_size.setdefault(c["kn5Size"], []).append((c, d))
    for group in by_size.values():
        if len(group) < 2:
            continue
        by_fp = {}
        for c, d in group:
            fp = cache.fingerprint(d, c["kn5"])
            if fp:
                by_fp.setdefault(fp, []).append(c)
        for same in by_fp.values():
            for c in same:
                c["identical"] = sorted(x["id"] for x in same if x is not c)
                c["duplicates"] = sorted(set(c["duplicates"]) | set(c["identical"]))


def open_in_explorer(path):
    if sys.platform == "win32":
        os.startfile(str(path))  # a folder: opens it in Explorer
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


def str_list(value, what, max_items, max_len=1024):
    if not isinstance(value, list) or len(value) > max_items or not all(
            isinstance(v, str) and len(v) <= max_len for v in value):
        raise ApiError(400, "%s must be a list of up to %d names" % (what, max_items))
    return value


class App:
    def __init__(self, settings_path=None, default_out=None, pick_folder=None, open_path=None,
                 pick_file=None, launch_blender=None):
        self.token = secrets.token_urlsafe(32)
        settings_path = Path(settings_path or PROJECT_DIR / "settings.json")
        self.settings = Settings(settings_path, default_out or PROJECT_DIR / "output")
        self.library = Library(settings_path.with_name("library.json"))
        self.jobs = Jobs(self.run_car, on_result=self.record_history)
        self._history = None
        self.cars = {}  # id -> Path, from the last scan
        self.cars_lock = threading.Lock()
        self.pick_folder = pick_folder or dialogs.pick_folder
        self.pick_file = pick_file or dialogs.pick_blender_exe
        self.open_path = open_path or open_in_explorer
        self.launch_blender = launch_blender or blender.launch
        self.pick_lock = threading.Lock()
        self.preview_lock = threading.Lock()
        self._scan_cache = None

    # --- folders ---------------------------------------------------------

    def out_dir(self):
        return Path(self.settings.get("out"))

    def preview_cache(self):
        return PreviewCache(self.out_dir() / ".preview")

    def scan_cache(self):
        path = self.out_dir() / ".cache" / "scan.json"
        if self._scan_cache is None or self._scan_cache.path != path:
            self._scan_cache = ScanCache(path)
        return self._scan_cache

    def history(self):
        path = self.out_dir() / ".cache" / "history.json"
        if self._history is None or self._history.path != path:
            self._history = History(path)
        return self._history

    def car_dir(self, car_id):
        with self.cars_lock:
            if car_id not in self.cars:
                raise ApiError(404, "unknown car (scan the folder first)")
            return self.cars[car_id]

    # --- export jobs -----------------------------------------------------

    def fbx_converter(self):
        exe = blender.find_blender(self.settings.get("blender"))
        if not exe:
            return None
        return lambda glb, fbx: blender.glb_to_fbx(exe, glb, fbx)

    def run_car(self, car_id, car_dir, opts, log, progress):
        preset = opts.get("preset", "render")
        r = convert.convert_car(car_dir, opts["out"], opts.get("skin"), opts.get("flip_green", True), log=log,
                                progress=progress, preset=preset, paint=opts.get("paint"),
                                paint_mode=opts.get("paint_mode", "tint"), paint_names=opts.get("paint_names"),
                                texture_size=opts.get("texture_size"), jpeg_quality=opts.get("jpeg_quality"),
                                lights_on=opts.get("lights_on", False),
                                fbx_converter=self.fbx_converter() if preset == "fbx" else None)
        result = {"file": r["path"].name, "size": r["path"].stat().st_size, "protected": r["protected"],
                  "skin": r["skin"], "report": r["report"], "preset": r["preset"]}
        if opts.get("blender_script") and r["path"].suffix == ".glb":
            result["script"] = blender.write_setup_script(r["path"], car_id).name
        log("  -> %s" % r["path"].name)
        return result

    def record_history(self, car_id, car_dir, opts, result):
        self.history().add({
            "id": car_id, "folder": str(car_dir), "ok": result.get("ok", False), "file": result.get("file"),
            "error": result.get("error"), "seconds": result.get("seconds"), "report": result.get("report", []),
            "options": opts.get("request", {})})

    # --- API -------------------------------------------------------------

    def api_settings(self, body):
        return {"roots": self.settings.get("roots"), "out": self.settings.get("out"), "version": __version__,
                "presets": {k: v["label"] for k, v in convert.PRESETS.items() if k != "preview"},
                "textureSizes": list(convert.TEXTURE_SIZES), "savedPresets": self.settings.get("savedPresets") or [],
                "tags": self.library.all_tags(), "language": self.settings.get("language"),
                "home": str(Path.home())}  # the page shows paths as ~\... (no user name on screen)

    def api_save_settings(self, body):
        out = body.get("out")
        if out is not None and (not isinstance(out, str) or not out.strip()):
            raise ApiError(400, "output folder can't be empty")
        roots = body.get("roots")
        if roots is not None:
            if not isinstance(roots, list) or not all(isinstance(r, str) for r in roots):
                raise ApiError(400, "roots must be a list of folders")
            roots = list(dict.fromkeys(r.strip() for r in roots if r.strip()))
            str_list(roots, "roots", MAX_ROOTS)
        language = body.get("language")
        if language is not None and (not isinstance(language, str) or not re.fullmatch(r"[a-z]{2}", language)):
            raise ApiError(400, "language must be a two-letter code")
        self.settings.update(roots=roots, out=out.strip() if out else None, language=language)
        return self.api_settings(body)

    def api_pick(self, body):
        what = body.get("what")
        titles = {"root": "Add a car folder, or an assettocorsa\\content\\cars folder",
                  "out": "Choose where the exported files go"}
        if what not in titles:
            raise ApiError(400, "what must be 'root' or 'out'")
        with self.pick_lock:
            folder = self.pick_folder(titles[what])
        if folder:
            if what == "root":
                roots = self.settings.get("roots")
                if folder not in roots:
                    self.settings.update(roots=roots + [folder])
            else:
                self.settings.update(out=folder)
        return {"path": folder, "roots": self.settings.get("roots")}

    def api_scan(self, body):
        roots = body.get("roots")
        if roots is None:
            roots = [body["root"]] if body.get("root") else self.settings.get("roots")
        if not isinstance(roots, list) or not roots:
            raise ApiError(400, "add at least one folder to scan")
        roots = list(dict.fromkeys(str(r).strip() for r in roots if str(r).strip()))
        str_list(roots, "roots", MAX_ROOTS)
        missing = [r for r in roots if not Path(r).is_dir()]
        if missing:
            raise ApiError(400, "folder not found: %s" % "; ".join(missing))
        self.settings.update(roots=roots)
        dirs, origin, seen = [], [], set()
        for root in roots:
            for d in carfiles.find_cars(Path(root)):
                key = d.resolve()
                if key not in seen:
                    seen.add(key)
                    dirs.append(d)
                    origin.append(root)
        cache = self.scan_cache()
        with ThreadPoolExecutor(8) as pool:
            cars = list(pool.map(lambda d: cache.summary(d, safe_summary), dirs))
        ids, used = [], {}
        for c in cars:  # the same folder name in two places gets "(2)", "(3)"...
            n = used.get(c["id"].lower(), 0) + 1
            used[c["id"].lower()] = n
            ids.append(c["id"] if n == 1 else "%s (%d)" % (c["id"], n))
        for c, i, root, d in zip(cars, ids, origin, dirs):
            c["id"], c["root"], c["folder"] = i, root, str(d)
            c["favourite"], c["userTags"] = self.library.get(d)
        mark_duplicates(cars)
        mark_identical(cars, dirs, cache)
        cache.save(keep=dirs)
        with self.cars_lock:
            self.cars = {c["id"]: d for c, d in zip(cars, dirs)}
        return {"roots": roots, "cars": cars}

    def api_car(self, query):
        return carinfo.car_details(self.car_dir(query.get("id", "")))

    def api_materials(self, query):
        try:
            return {"materials": convert.model_materials(self.car_dir(query.get("id", "")))}
        except Kn5Error as e:
            raise ApiError(400, str(e))

    def api_convert(self, body):
        ids = body.get("ids") or []
        if not isinstance(ids, list) or not ids:
            raise ApiError(400, "no cars selected")
        str_list(ids, "ids", MAX_IDS)
        cars = [(i, self.car_dir(i)) for i in ids]
        skin = body.get("skin") if len(ids) == 1 else None
        if skin is not None and not isinstance(skin, str):
            raise ApiError(400, "skin must be a name")
        preset = body.get("preset") or "render"
        if preset not in convert.PRESETS or preset == "preview":
            raise ApiError(400, "unknown preset")
        paint = body.get("paint") or None
        if paint:
            try:
                convert.hex_rgb(paint)
            except Kn5Error as e:
                raise ApiError(400, str(e))
        mode = body.get("paintMode") or "tint"
        if mode not in ("tint", "solid"):
            raise ApiError(400, "paintMode must be tint or solid")
        paint_names = body.get("paintMaterials")
        if paint_names is not None:
            str_list(paint_names, "paintMaterials", 200, 256)
            paint_names = paint_names if len(ids) == 1 else None  # material names belong to one model
        size = body.get("textureSize")
        if size is not None and size not in convert.TEXTURE_SIZES:
            raise ApiError(400, "textureSize must be one of %s" % list(convert.TEXTURE_SIZES))
        quality = body.get("jpegQuality")
        if quality is not None and (not isinstance(quality, int) or isinstance(quality, bool) or not 50 <= quality <= 100):
            raise ApiError(400, "jpegQuality must be 50-100")
        if preset == "fbx" and not blender.find_blender(self.settings.get("blender")):
            raise ApiError(400, "FBX export needs Blender: locate blender.exe first")
        request = {k: body.get(k) for k in ("skin", "preset", "paint", "paintMode", "paintMaterials", "textureSize",
                                            "jpegQuality", "lightsOn", "blenderScript") if body.get(k) is not None}
        opts = {"out": self.out_dir(), "skin": skin, "flip_green": bool(body.get("flipGreen", True)),
                "preset": preset, "paint": paint, "paint_mode": mode, "paint_names": paint_names,
                "blender_script": bool(body.get("blenderScript", True)), "texture_size": size,
                "jpeg_quality": quality, "lights_on": bool(body.get("lightsOn", False)), "request": request}
        return {"job": self.jobs.submit(cars, opts)}

    # --- library extras --------------------------------------------------

    def api_carmeta(self, body):
        """Favourite star and the user's own tags for a car (remembered by its folder)."""
        car_dir = self.car_dir(str(body.get("id", "")))
        fav = body.get("favourite")
        if fav is not None and not isinstance(fav, bool):
            raise ApiError(400, "favourite must be true or false")
        tags = body.get("tags")
        if tags is not None:
            str_list(tags, "tags", 50, 200)
        favourite, tags = self.library.set(car_dir, favourite=fav, tags=tags)
        return {"favourite": favourite, "tags": tags, "allTags": self.library.all_tags()}

    def scanned(self):
        with self.cars_lock:
            return dict(self.cars)

    def api_space(self, query):
        """Disk use of every scanned car (model, skins, the rest) and what identical copies waste."""
        def measure(item):
            car_id, d = item
            sizes = {"model": 0, "skins": 0, "other": 0}
            for f in d.rglob("*"):
                try:
                    if not f.is_file():
                        continue
                    size = f.stat().st_size
                except OSError:
                    continue
                rel = f.relative_to(d).parts
                kind = "skins" if rel[0].lower() == "skins" else "model" if f.suffix.lower() == ".kn5" else "other"
                sizes[kind] += size
            return dict(id=car_id, total=sum(sizes.values()), **sizes)

        with ThreadPoolExecutor(8) as pool:
            cars = list(pool.map(measure, self.scanned().items()))
        cars.sort(key=lambda c: c["total"], reverse=True)
        return {"cars": cars, "total": sum(c["total"] for c in cars)}

    def api_skins(self, query):
        """Every skin of every scanned car, for the skin gallery."""
        def skins(item):
            car_id, d = item
            return [dict(carinfo.skin_info(s), car=car_id) for s in carfiles.list_skins(d)]

        with ThreadPoolExecutor(8) as pool:
            return {"skins": [s for group in pool.map(skins, self.scanned().items()) for s in group]}

    def api_history(self, query):
        return {"items": self.history().list()}

    def api_history_clear(self, body):
        self.history().clear()
        return {"items": []}

    def api_preset_save(self, body):
        name = " ".join(str(body.get("name") or "").split())[:60]
        options = body.get("options")
        if not name or not isinstance(options, dict):
            raise ApiError(400, "a saved preset needs a name and options")
        clean = {}
        for key, value in options.items():
            if key in PRESET_OPTIONS and isinstance(value, PRESET_OPTIONS[key]) and not (
                    PRESET_OPTIONS[key] is not bool and isinstance(value, bool)):
                clean[key] = value
        if clean.get("preset") not in {k for k in convert.PRESETS if k != "preview"}:
            raise ApiError(400, "unknown preset")
        saved = [p for p in (self.settings.get("savedPresets") or []) if p["name"].lower() != name.lower()]
        if len(saved) >= MAX_SAVED_PRESETS:
            raise ApiError(400, "too many saved presets")
        self.settings.update(savedPresets=saved + [{"name": name, "options": clean}])
        return {"savedPresets": self.settings.get("savedPresets")}

    def api_preset_delete(self, body):
        name = str(body.get("name") or "").lower()
        self.settings.update(savedPresets=[p for p in (self.settings.get("savedPresets") or [])
                                           if p["name"].lower() != name])
        return {"savedPresets": self.settings.get("savedPresets")}

    def api_job(self, query):
        job = self.jobs.get(query.get("id", ""))
        if not job:
            raise ApiError(404, "unknown job")
        return job

    def api_job_cancel(self, body):
        if not self.jobs.cancel(str(body.get("id", ""))):
            raise ApiError(404, "unknown job")
        return {"cancelled": True}

    def api_preview(self, query):
        """A light .glb (512 px textures) of a car with one skin, for the 3D skin preview."""
        car_id = query.get("id", "")
        car_dir = self.car_dir(car_id)
        skin = query.get("skin") or None
        main = carfiles.find_main_kn5(car_dir)
        if main is None:
            raise ApiError(400, "this car has no model")
        stamp = "%d_%d_%d" % (int(main.stat().st_mtime), main.stat().st_size, carfiles.skin_stamp(car_dir, skin))
        prefix = re_safe("%s__%s__" % (car_id, skin or "default"))
        name = prefix + re_safe(stamp) + ".glb"
        previews = self.preview_cache()
        path = previews.path(name)
        with self.preview_lock:
            if not path.is_file():
                try:
                    convert.convert_car(car_dir, previews.folder, skin, log=lambda m: None, preset="preview",
                                        out_name=name)
                except Kn5Error as e:
                    raise ApiError(400, str(e))
                previews.prune(keep=name, prefix=prefix)
            else:
                os.utime(path)  # recently used: keep it when pruning
        return {"url": "/preview/" + name}

    def api_render(self, body):
        """Save one turntable frame (PNG / JPEG) or a video (WebM) sent by the viewer."""
        name = re_safe(str(body.get("name") or "render"))[:80] or "render"
        data = body.get("data")
        if not isinstance(data, str) or not data.startswith("data:") or len(data) > MAX_RENDER_BODY:
            raise ApiError(400, "data must be a data: URL")
        head, _, b64 = data.partition(",")
        ext = {"image/png": ".png", "image/jpeg": ".jpg", "video/webm": ".webm"}.get(head[5:].split(";")[0])
        if not ext or ";base64" not in head:
            raise ApiError(400, "only base64 PNG, JPEG or WebM")
        try:
            raw = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            raise ApiError(400, "bad base64 data")
        folder = self.out_dir() / "renders" / name
        folder.mkdir(parents=True, exist_ok=True)
        if ext == ".webm":
            path = folder / "turntable.webm"
        else:
            index = body.get("index", 0)
            if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < 10000:
                raise ApiError(400, "index must be 0-9999")
            stem = "snapshot" if body.get("snapshot") else "frame"
            path = folder / ("%s_%04d%s" % (stem, index, ext))
        path.write_bytes(raw)
        return {"file": str(path.relative_to(self.out_dir())), "folder": str(folder)}

    def api_blender(self, query):
        exe = blender.find_blender(self.settings.get("blender"))
        return {"found": bool(exe), "path": str(exe) if exe else None}

    def api_blender_locate(self, body):
        with self.pick_lock:
            path = self.pick_file()
        if path:
            if not Path(path).is_file():
                raise ApiError(400, "that file doesn't exist")
            if not blender.looks_like_blender(path):
                raise ApiError(400, "that isn't blender.exe")
            self.settings.update(blender=str(path))
        return self.api_blender({})

    def api_blender_open(self, body):
        name = str(body.get("file") or "")
        f = self.out_dir() / name
        if Path(name).name != name or not name.endswith(".glb") or not f.is_file():
            raise ApiError(404, "exported file not found")
        script = blender.write_setup_script(f, Path(name).stem)
        exe = blender.find_blender(self.settings.get("blender"))
        if not exe:
            return {"launched": False, "script": script.name,
                    "message": "Blender wasn't found. Use 'Locate Blender', or open the script in Blender "
                               "(Scripting > Open > Run Script)."}
        self.launch_blender(exe, script)
        return {"launched": True, "script": script.name, "blender": str(exe)}

    def api_outputs(self, query):
        out = self.out_dir()
        files = [f for ext in ("*.glb", "*.stl", "*.obj", "*.fbx") for f in out.glob(ext)] if out.is_dir() else []
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        return {"out": str(out), "files": [{"name": f.name, "size": f.stat().st_size, "modified": f.stat().st_mtime,
                                            "viewable": f.suffix == ".glb"} for f in files]}

    def api_open(self, body):
        what = body.get("what")
        if what == "out":
            path = self.out_dir()
            path.mkdir(parents=True, exist_ok=True)
        elif what == "renders":
            path = self.out_dir() / "renders"
            sub = body.get("name")
            if sub:
                path = path / (re_safe(str(sub))[:80] or "render")
            path.mkdir(parents=True, exist_ok=True)
        elif what == "car":
            path = self.car_dir(body.get("id", ""))
        else:
            raise ApiError(400, "what must be 'out', 'renders' or 'car'")
        self.open_path(path)
        return {"opened": str(path)}


GET_ROUTES = {"/api/settings": "api_settings", "/api/car": "api_car", "/api/job": "api_job",
              "/api/outputs": "api_outputs", "/api/preview": "api_preview", "/api/blender": "api_blender",
              "/api/materials": "api_materials", "/api/space": "api_space", "/api/skins": "api_skins",
              "/api/history": "api_history"}
POST_ROUTES = {"/api/settings": "api_save_settings", "/api/pick": "api_pick", "/api/scan": "api_scan",
               "/api/convert": "api_convert", "/api/open": "api_open", "/api/render": "api_render",
               "/api/job/cancel": "api_job_cancel", "/api/carmeta": "api_carmeta",
               "/api/history/clear": "api_history_clear", "/api/presets/save": "api_preset_save",
               "/api/presets/delete": "api_preset_delete",
               "/api/blender/locate": "api_blender_locate", "/api/blender/open": "api_blender_open"}


def page_csp(html):
    """Content-Security-Policy for the page: only our own scripts, plus the inline import
    map by its hash. No other site can be contacted from the page."""
    hashes = ["'sha256-%s'" % base64.b64encode(hashlib.sha256(m.group(1).encode("utf-8")).digest()).decode()
              for m in re.finditer(r'<script type="importmap">(.*?)</script>', html, re.S)]
    return ("default-src 'self'; script-src 'self' %s; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; connect-src 'self' data: blob:; media-src 'self' blob:; "
            "font-src 'self'; worker-src 'self' blob:; object-src 'none'; base-uri 'none'; "
            "form-action 'none'; frame-ancestors 'none'" % " ".join(hashes)).replace("  ", " ")


class Handler(BaseHTTPRequestHandler):
    server_version = "KN5Garage"
    sys_version = ""

    @property
    def app(self):
        return self.server.app

    def log_message(self, fmt, *args):
        pass  # nothing is logged (URLs may carry the token)

    # --- responses -------------------------------------------------------

    def start(self, status, ctype, length, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        headers = dict(SECURITY_HEADERS)
        headers["Cache-Control"] = "no-store"
        headers.update(extra or {})
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()

    def send(self, status, body, ctype="application/json", extra_headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.start(status, ctype, len(body), extra_headers)
        self.wfile.write(body)

    def send_file(self, path, cache=False):
        path = Path(path)
        ctype = CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
        extra = {"Cache-Control": "max-age=86400"} if cache else None
        with open(path, "rb") as f:
            self.start(200, ctype, os.fstat(f.fileno()).st_size, extra)
            try:
                shutil.copyfileobj(f, self.wfile, 1 << 20)
            except (ConnectionError, OSError):
                pass  # the browser stopped loading

    # --- request checks --------------------------------------------------

    def host_ok(self):
        port = self.server.server_address[1]
        return (self.headers.get("Host") or "").lower() in ("127.0.0.1:%d" % port, "localhost:%d" % port)

    def cross_site(self):
        port = self.server.server_address[1]
        origin = self.headers.get("Origin")
        if origin is not None and origin not in ("http://127.0.0.1:%d" % port, "http://localhost:%d" % port):
            return True
        return self.headers.get("Sec-Fetch-Site") in ("cross-site", "same-site")

    def authorized(self, query, allow_query_token=False):
        token = self.headers.get("X-Token")
        if token is None and allow_query_token:
            token = query.get("t")
        if not isinstance(token, str):
            return False
        return secrets.compare_digest(token.encode("utf-8", "replace"), self.app.token.encode("utf-8"))

    def read_json(self, limit):
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            raise ApiError(415, "expected application/json")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(400, "bad Content-Length")
        if length < 0:
            raise ApiError(400, "bad Content-Length")
        if length > limit:
            if length <= 16 << 20:  # drain small bodies so the client sees the error cleanly
                self.rfile.read(length)
            self.close_connection = True
            raise ApiError(413, "request too large")
        try:
            arg = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            raise ApiError(400, "invalid JSON")
        if not isinstance(arg, dict):
            raise ApiError(400, "expected a JSON object")
        return arg

    # --- routing ---------------------------------------------------------

    def route(self, method):
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        path = url.path
        try:
            if not self.host_ok():
                return self.send(421, {"error": "wrong host"})
            if method == "GET" and path in ("/", "/index.html"):
                return self.send(200, self.server.page.replace("__TOKEN__", self.app.token), CONTENT_TYPES[".html"],
                                 {"Content-Security-Policy": self.server.csp})
            if method == "GET" and path.startswith("/web/"):
                f = (WEB_DIR / unquote(path[5:])).resolve()
                if WEB_DIR.resolve() not in f.parents or not f.is_file():
                    return self.send(404, {"error": "not found"})
                return self.send_file(f, cache="/web/vendor/" in path)
            if self.cross_site():
                return self.send(403, {"error": "cross-site request refused"})

            file_route = method == "GET" and (path == "/api/file" or path.startswith(("/out/", "/preview/")))
            if not self.authorized(query, allow_query_token=file_route):
                return self.send(403, {"error": "missing or wrong token"})

            if file_route and path == "/api/file":
                f = carinfo.resolve_car_file(self.app.car_dir(query.get("car", "")), query.get("path", ""))
                return self.send_file(f) if f else self.send(404, {"error": "not found"})
            if file_route:
                preview = path.startswith("/preview/")
                name = unquote(path[9:] if preview else path[5:])
                folder = self.app.preview_cache().folder if preview else self.app.out_dir()
                f = folder / name
                if Path(name).name != name or not name.endswith(".glb") or not f.is_file():
                    return self.send(404, {"error": "not found"})
                return self.send_file(f)

            routes = GET_ROUTES if method == "GET" else POST_ROUTES
            if path not in routes:
                return self.send(404, {"error": "not found"})
            arg = query if method == "GET" else self.read_json(MAX_RENDER_BODY if path == "/api/render" else MAX_JSON_BODY)
            return self.send(200, getattr(self.app, routes[path])(arg))
        except ApiError as e:
            return self.send(e.status, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            return self.send(500, {"error": str(e) or e.__class__.__name__})

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PUT(self):
        self.send(405, {"error": "method not allowed"})

    do_DELETE = do_PATCH = do_OPTIONS = do_PUT


def make_server(app, port=0):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.app = app
    server.page = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    server.csp = page_csp(server.page)
    return server


def main(argv=None):
    ap = argparse.ArgumentParser(description="KN5 Garage - convert Assetto Corsa cars to .glb")
    ap.add_argument("--port", type=int, default=8642)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--settings", help="settings file (default: settings.json in the program folder)")
    ap.add_argument("--out", help="default output folder")
    args = ap.parse_args(argv)
    app = App(settings_path=args.settings, default_out=args.out)
    if args.out:  # an explicit --out wins over the saved setting
        app.settings.update(out=str(Path(args.out).resolve()))
    try:
        server = make_server(app, args.port)
    except OSError:
        server = make_server(app, 0)  # preferred port busy: take any free one
    url = "http://127.0.0.1:%d/" % server.server_address[1]
    print("KN5 Garage %s is running at %s" % (__version__, url), flush=True)
    print("Keep this window open while you use the app. Press Ctrl+C to quit.")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
