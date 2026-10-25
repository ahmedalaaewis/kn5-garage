"""What the library shows: Assetto Corsa cars, BeamNG.drive vehicles (folders and zip mods)
and loose 3D files (characters, props, scans, anything the app can open).

Every entry has a "kind" and the same summary fields as an AC car, so the cards, table,
search, compare and export work for all of them."""

import os
import re
from pathlib import Path

from . import beamng, carfiles, formats
from .carinfo import car_summary

MAX_ITEMS = 20000
MAX_DEPTH = 8
MAX_FOLDERS = 20000
KIND_LABELS = {"ac": "Assetto Corsa", "beamng": "BeamNG.drive", "model": "3D file"}


class Item:
    """One library entry: kind, path (a folder or a file) and, for zip mods, the vehicle inside."""
    __slots__ = ("kind", "path", "inner")

    def __init__(self, kind, path, inner=None):
        self.kind, self.path, self.inner = kind, Path(path), inner

    def key(self):
        return "%s|%s|%s" % (self.kind, self.path.resolve(), self.inner or "")

    def ident(self):
        """A path that names this entry (favourites, tags and the scan cache are kept by it)."""
        return self.path if not self.inner else Path("%s#%s" % (self.path, self.inner.replace("/", "_")))

    def __repr__(self):
        return "Item(%s, %s%s)" % (self.kind, self.path, ", " + self.inner if self.inner else "")


def _skip(entry):
    try:
        if entry.name.startswith(".") or entry.is_symlink():
            return True
        return bool(getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400)
    except OSError:
        return True


def find_items(folder, max_depth=MAX_DEPTH, max_folders=MAX_FOLDERS, max_items=MAX_ITEMS, exclude=()):
    """Everything below folder, at any depth. Car and vehicle folders aren't searched further;
    exclude: folders to leave out (the app's own output folder, with exports and backups)."""
    folder = Path(folder)
    skip = {os.path.normcase(str(Path(e).resolve())) for e in exclude}
    found, visited = [], 0
    stack = [(folder, 0)]
    while stack and visited < max_folders and len(found) < max_items:
        path, depth = stack.pop()
        visited += 1
        if skip and os.path.normcase(str(Path(path).resolve())) in skip:
            continue
        try:
            with os.scandir(path) as it:
                entries = [e for e in it if not _skip(e)]
        except OSError:
            continue
        files = [e for e in entries if e.is_file(follow_symlinks=False)]
        if carfiles._has_kn5(files):
            found.append(Item("ac", path))
            continue
        if beamng.is_vehicle_dir(files):
            found.append(Item("beamng", path))
            continue
        for e in sorted(files, key=lambda e: e.name.lower()):
            ext = os.path.splitext(e.name)[1].lower()
            if ext == ".zip":
                found += [Item("beamng", e.path, inner) for inner in beamng.zip_vehicles(e.path)]
            elif ext in formats.FORMATS and ext != ".kn5" and not e.name.lower().endswith(".fbxsource.glb"):
                found.append(Item("model", e.path))
        if depth >= max_depth:
            continue
        subdirs = sorted((Path(e.path) for e in entries if e.is_dir(follow_symlinks=False)), reverse=True)
        stack.extend((d, depth + 1) for d in subdirs)
    found = found[:max_items]
    return sorted(found, key=lambda i: (str(i.path).lower(), i.inner or ""))


# ---------------------------------------------------------------------------
# summaries
# ---------------------------------------------------------------------------

def _base(item, name):
    return {
        "id": name, "name": name, "brand": None, "author": None, "version": None, "url": None, "year": None,
        "country": None, "carClass": None, "tags": [], "specs": {}, "status": "ok", "statusText": None,
        "kn5": None, "kn5Size": 0, "textureBytes": 0, "skinCount": 0, "preview": None, "badge": None,
        "hasUi": False, "issues": [], "hasAnimations": False, "hasLod": False, "hasExtension": False,
        "parent": None, "kind": item.kind, "kindLabel": KIND_LABELS[item.kind],
    }


def summary(item):
    if item.kind == "ac":
        s = car_summary(item.path)
        s.update(kind="ac", kindLabel=KIND_LABELS["ac"])
        return s
    if item.kind == "beamng":
        name = item.inner.split("/")[-1] if item.inner else item.path.name
        s = _base(item, name)
        try:
            info = beamng.vehicle_summary(item.path, item.inner)
        except (OSError, ValueError) as e:
            s.update(status="encrypted", statusText="Couldn't read this vehicle: %s" % e, issues=["unreadable"])
            return s
        s.update({k: info[k] for k in ("name", "brand", "author", "year", "country", "carClass")})
        s["specs"] = info["specs"]
        s["kn5Size"] = info["size"]
        s["format"] = "BeamNG vehicle" + (" (zip)" if item.inner else "")
        s["tags"] = ["BeamNG"]
        return s
    # a loose 3D file
    p = item.path
    s = _base(item, p.stem)
    s["name"] = p.stem.replace("_", " ").strip() or p.name
    s["format"] = formats.describe(p) or p.suffix
    s["kn5"] = p.name
    try:
        s["kn5Size"] = p.stat().st_size
    except OSError:
        s.update(status="encrypted", issues=["unreadable"])
    s["tags"] = [p.suffix.lstrip(".").upper()]
    if p.suffix.lower() in (".bvh", ".pmx", ".vrm"):
        s["tags"].append("Character")
    return s


def signature(item):
    """Changes when anything the summary depends on changes (for the scan cache)."""
    from .cache import _stat, folder_signature
    if item.kind == "ac":
        return folder_signature(item.path)
    if item.kind == "beamng" and not item.inner:
        try:
            files = sorted(f for f in item.path.iterdir() if f.is_file())
        except OSError:
            files = []
        return [[f.name] + (_stat(f) or []) for f in files]
    return [item.inner, _stat(item.path)]


# ---------------------------------------------------------------------------
# 3D model and export for BeamNG vehicles and loose files
# ---------------------------------------------------------------------------

def load_model(item, skin=None, cache_root=None, blender_exe=None):
    if skin == "default":
        skin = None
    if item.kind == "beamng":
        folder = item.path
        if item.inner:
            folder = beamng.unpack_vehicle(item.path, item.inner, Path(cache_root) / "beamng")
        return beamng.load_vehicle(folder, skin=skin)
    return formats.load(item.path, blender_exe)


def output_stem(item, skin=None):
    name = item.inner.split("/")[-1] if item.inner else item.path.stem
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name + ("__" + skin if skin else ""))


def convert_item(item, out_dir, skin=None, preset="render", paint=None, paint_mode="tint", paint_names=None,
                 texture_size=None, jpeg_quality=None, lights_on=False, fbx_converter=None, cache_root=None,
                 blender_exe=None, log=print, progress=None, out_name=None):
    """Export a BeamNG vehicle or a 3D file the way convert_car exports an AC car."""
    from .convert import FORMAT_EXT, PRESETS, Converter
    from .kn5 import Kn5Error
    opts = PRESETS[preset]
    out_dir = Path(out_dir)
    skin = None if skin == "default" else skin
    if opts["format"] == "fbx" and fbx_converter is None:
        raise Kn5Error("FBX export needs Blender: locate blender.exe first")
    if progress:
        progress(0.0, "reading %s" % item.path.name)
    model = load_model(item, skin, cache_root, blender_exe)
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = {"render": "", "preview": ""}.get(preset, "__" + preset)
    out_path = out_dir / (out_name or output_stem(item, skin) + suffix + FORMAT_EXT[opts["format"]])
    max_texture = opts["max_texture"] if texture_size is None or opts["max_texture"] == 0 else texture_size
    fmt = opts["format"]
    conv = Converter(model, {}, log=log, progress=progress, max_texture=max_texture, paint=paint,
                     paint_mode=paint_mode, fmt="glb" if fmt == "fbx" else fmt, paint_names=paint_names,
                     jpeg_quality=int(jpeg_quality) if jpeg_quality else None, lights_on=lights_on)
    tmp = out_path.with_name(out_path.name + ".part")
    glb_tmp = out_path.with_name(out_path.stem + ".fbxsource.glb") if fmt == "fbx" else None
    try:
        if fmt == "fbx":
            report = conv.run(glb_tmp)
            fbx_converter(glb_tmp, tmp)
        else:
            report = conv.run(tmp, final_path=out_path)
        os.replace(tmp, out_path)
    finally:
        for p in (tmp, glb_tmp):
            if p is not None and p.exists():
                p.unlink()
    if progress:
        progress(1.0, "done")
    return {"path": out_path, "skin": skin, "kn5": item.path.name, "protected": False, "report": report,
            "preset": preset}


def details(item, cache_root=None):
    s = summary(item)
    files, size = [], 0
    if item.kind == "beamng" and not item.inner:
        for f in sorted(item.path.rglob("*")):
            if f.is_file():
                size += f.stat().st_size
                if f.suffix.lower() in (".dae", ".jbeam", ".pc"):
                    files.append({"name": f.relative_to(item.path).as_posix(), "size": f.stat().st_size,
                                  "role": {".dae": "3D model", ".jbeam": "parts and physics", ".pc": "configuration"}[f.suffix.lower()]})
        skin_names = beamng.skins(item.path)
    elif item.kind == "beamng":
        folder = beamng.unpack_vehicle(item.path, item.inner, Path(cache_root) / "beamng") if cache_root else None
        skin_names = beamng.skins(folder) if folder else []
        size = item.path.stat().st_size
        files = [{"name": item.path.name + " → " + item.inner, "size": size, "role": "BeamNG mod (zip)"}]
    else:
        size = item.path.stat().st_size if item.path.is_file() else 0
        files = [{"name": item.path.name + (" → " + item.inner if item.inner else ""), "size": size,
                  "role": s.get("format") or "3D file"}]
        skin_names = []
    if skin_names:  # the vehicle's own paint comes first (BeamNG skins are extras)
        skin_names = ["default"] + skin_names
    s.update({
        "description": None, "torqueCurve": [], "powerCurve": [],
        "skins": [{"id": n, "name": n.replace("_", " "), "preview": None, "livery": None, "driver": None,
                   "team": None, "number": None} for n in skin_names],
        "files": files, "folderSize": size, "hasDataAcd": False, "hasDataFolder": False, "animations": 0,
        "path": str(item.path), "csp": [], "carData": None, "upgradeIcon": None,
    })
    if item.kind == "beamng":
        info = beamng.vehicle_summary(item.path, item.inner)
        s["description"] = info.get("description")
    return s
