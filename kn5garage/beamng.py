"""BeamNG.drive vehicles: a folder (or a .zip mod) with vehicles/<name>/ holding .jbeam parts,
.dae meshes, info.json, configurations (.pc) and *.materials.json.

The 3D model shown is the vehicle's default configuration: the parts it uses (jbeam slots)
decide which meshes of the .dae files appear, and materials.json gives them their textures.
Wheels and other parts shared from BeamNG's own "common" folder aren't in the mod, so they
don't appear."""

import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path, PurePosixPath

from . import kn5write
from .importer import ImportError3D, _texture, walk

MAX_ZIP_FILES = 20000
MAX_ZIP_BYTES = 4 << 30
TEXTURE_KEYS = ("baseColorMap", "colorMap", "diffuseMap", "baseTex")


# ---------------------------------------------------------------------------
# BeamNG's relaxed JSON (comments, missing and trailing commas)
# ---------------------------------------------------------------------------

def relaxed_json(text):
    """Parse BeamNG's JSON: comments, missing commas between values, trailing commas."""
    text = text.lstrip("\ufeff")
    tokens, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            tokens.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c in "{}[]:,":
            tokens.append(c)
            i += 1
        else:  # a number or true / false / null
            j = i
            while j < n and text[j] not in ' \t\r\n{}[]:,"/':
                j += 1
            j = max(j, i + 1)
            tokens.append(text[i:j])
            i = j
    out = []
    for t in tokens:
        prev = out[-1] if out else ""
        if t not in ("}", "]", ":", ",") and prev and prev not in ("{", "[", ":", ","):
            out.append(",")  # a missing comma
        if t in ("}", "]") and prev == ",":
            out.pop()        # a trailing comma
        out.append(t)
    return json.loads("".join(out), strict=False)


def read_relaxed(path):
    try:
        data = relaxed_json(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError, RecursionError):
        return {}
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# finding vehicles
# ---------------------------------------------------------------------------

def is_vehicle_dir(entries):
    names = [e.name.lower() for e in entries]
    return any(n.endswith(".jbeam") for n in names) and (
        any(n.endswith(".dae") for n in names) or "info.json" in names)


def zip_vehicles(path):
    """Vehicle folders inside a BeamNG mod zip: ["vehicles/<name>", ...]."""
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
    except (OSError, zipfile.BadZipFile, RuntimeError):
        return []
    if len(names) > MAX_ZIP_FILES:
        return []
    found = set()
    for n in names:
        p = PurePosixPath(n)
        if len(p.parts) >= 3 and p.parts[0].lower() == "vehicles" and p.suffix.lower() == ".jbeam":
            found.add("/".join(p.parts[:2]))
    return sorted(found)


def unpack_vehicle(zip_path, inner, cache_root):
    """The vehicle folder of a zip mod, unpacked once into the cache (programs left out)."""
    from .installer import SKIP_TYPES, safe_member
    zip_path = Path(zip_path)
    st = zip_path.stat()
    key = hashlib.sha1(("%s|%s|%d|%d" % (zip_path.resolve(), inner, st.st_size, int(st.st_mtime))).encode()).hexdigest()[:16]
    dest = Path(cache_root) / key
    done = dest / ".complete"
    if done.is_file():
        return dest
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    prefix = inner.rstrip("/") + "/"
    total = 0
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir() or not info.filename.startswith(prefix):
                continue
            rel = safe_member(info.filename[len(prefix):])
            if rel is None or rel.suffix.lower() in SKIP_TYPES:
                continue
            total += info.file_size
            if total > MAX_ZIP_BYTES:
                raise ImportError3D("the vehicle in this zip is too large")
            target = dest / str(rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
    done.write_text("ok")
    return dest


def read_zip_json(zip_path, inner, name):
    try:
        with zipfile.ZipFile(zip_path) as zf:
            raw = zf.read("%s/%s" % (inner, name))
    except (KeyError, OSError, zipfile.BadZipFile, RuntimeError):
        return {}
    try:
        data = relaxed_json(raw.decode("utf-8", "replace"))
    except (ValueError, RecursionError):
        return {}
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# summary (library card)
# ---------------------------------------------------------------------------

def _text(v):
    if v is None or isinstance(v, (dict, list)):
        return None
    return re.sub(r"<[^>]*>", "", str(v)).strip()[:300] or None


def summary_from(info, config_info, name, size):
    years = info.get("Years") if isinstance(info.get("Years"), dict) else {}
    year = years.get("min")
    specs = {}
    ci = config_info or {}
    if isinstance(ci.get("Power"), (int, float)):
        specs["bhp"] = "%d hp" % round(ci["Power"])
    if isinstance(ci.get("Torque"), (int, float)):
        specs["torque"] = "%d Nm" % round(ci["Torque"])
    if isinstance(ci.get("Weight"), (int, float)):
        specs["weight"] = "%d kg" % round(ci["Weight"])
    if isinstance(ci.get("Top Speed"), (int, float)):
        specs["topspeed"] = "%d km/h" % round(ci["Top Speed"] * 3.6)
    if isinstance(ci.get("0-100 km/h"), (int, float)):
        specs["acceleration"] = "%.1f s" % ci["0-100 km/h"]
    return {
        "name": _text(info.get("Name")) or name, "brand": _text(info.get("Brand")),
        "author": _text(info.get("Author")), "year": str(year) if isinstance(year, (int, float)) else None,
        "country": _text(info.get("Country")), "carClass": _text(info.get("Body Style") or info.get("Type")),
        "description": _text(info.get("Description")), "specs": specs, "size": size,
    }


def vehicle_summary(src, inner=None):
    """src: a vehicle folder, or a zip with inner = "vehicles/<name>"."""
    src = Path(src)
    if inner is None:
        info = read_relaxed(src / "info.json")
        pc = default_config_name(info, [p.stem for p in src.glob("*.pc")])
        cinfo = read_relaxed(src / ("info_%s.json" % pc)) if pc else {}
        size = sum(f.stat().st_size for f in src.rglob("*") if f.is_file())
        return summary_from(info, cinfo, src.name, size)
    info = read_zip_json(src, inner, "info.json")
    try:
        with zipfile.ZipFile(src) as zf:
            pcs = [PurePosixPath(n).stem for n in zf.namelist() if n.startswith(inner + "/") and n.endswith(".pc")]
    except (OSError, zipfile.BadZipFile):
        pcs = []
    pc = default_config_name(info, pcs)
    cinfo = read_zip_json(src, inner, "info_%s.json" % pc) if pc else {}
    return summary_from(info, cinfo, inner.split("/")[-1], src.stat().st_size)


def default_config_name(info, pcs):
    d = info.get("default_pc") if isinstance(info, dict) else None
    if isinstance(d, str) and d in pcs:
        return d
    return sorted(pcs)[0] if pcs else None


# ---------------------------------------------------------------------------
# the 3D model
# ---------------------------------------------------------------------------

def jbeam_parts(folder):
    parts = {}
    for f in sorted(Path(folder).rglob("*.jbeam")):
        data = read_relaxed(f)
        for name, part in data.items():
            if isinstance(part, dict):
                parts[name] = part
    return parts


def _table_rows(table):
    """jbeam tables: a header row of names, then rows (lists) or option dicts."""
    if not isinstance(table, list) or not table or not isinstance(table[0], list):
        return []
    head = table[0]
    rows = []
    for row in table[1:]:
        if isinstance(row, list):
            rows.append(dict(zip(head, row)))
    return rows


def used_meshes(folder, config=None):
    """Mesh names the chosen configuration shows (None when they can't be worked out)."""
    folder = Path(folder)
    parts = jbeam_parts(folder)
    if not parts:
        return None
    info = read_relaxed(folder / "info.json")
    pcs = {p.stem: p for p in folder.glob("*.pc")}
    chosen = {}
    name = config if config in pcs else default_config_name(info, list(pcs))
    if name:
        pc = read_relaxed(pcs[name])
        chosen = pc.get("parts") if isinstance(pc.get("parts"), dict) else {}
    main = [n for n, p in parts.items() if p.get("slotType") == "main"]
    if not main:
        return None
    meshes, seen = set(), set()

    def visit(part_name, depth=0):
        if part_name in seen or depth > 60 or part_name not in parts:
            return
        seen.add(part_name)
        part = parts[part_name]
        for key in ("flexbodies", "props"):
            for row in _table_rows(part.get(key)):
                mesh = row.get("mesh")
                if isinstance(mesh, str) and mesh:
                    meshes.add(mesh.lower())
        for row in _table_rows(part.get("slots")) + _table_rows(part.get("slots2")):
            slot = row.get("type") or row.get("name")
            default = row.get("default")
            pick = chosen.get(slot, default) if isinstance(slot, str) else default
            if isinstance(pick, str) and pick:
                visit(pick, depth + 1)
    visit(main[0])
    return meshes or None


def materials(folder):
    """{dae material name: {"base": {...}, "skins": {skin: {...}}}} from *.materials.json."""
    out = {}
    for f in sorted(Path(folder).rglob("*.materials.json")):
        for key, m in read_relaxed(f).items():
            if not isinstance(m, dict):
                continue
            target = str(m.get("mapTo") or m.get("name") or key)
            stages = m.get("Stages") if isinstance(m.get("Stages"), list) else []
            stage = next((s for s in stages if isinstance(s, dict)), {})
            tex = next((stage.get(k) for k in TEXTURE_KEYS if isinstance(stage.get(k), str)), None)
            colour = stage.get("diffuseColor") or stage.get("baseColorFactor")
            entry = {"texture": tex, "colour": colour if isinstance(colour, list) and len(colour) >= 3 else None}
            if ".skin." in target:
                base, skin = target.split(".skin.", 1)
                out.setdefault(base, {"base": None, "skins": {}})["skins"][skin] = entry
            else:
                out.setdefault(target, {"base": None, "skins": {}})["base"] = entry
    return out


def skins(folder):
    return sorted({s for m in materials(folder).values() for s in m["skins"]})


def _find_texture(folder, game_path):
    """A texture named in materials.json ("/vehicles/x/x_d.dds"), found in the vehicle folder."""
    name = PurePosixPath(str(game_path).replace("\\", "/")).name
    if not name:
        return None
    hits = [p for p in Path(folder).rglob("*") if p.name.lower() == name.lower() and p.is_file()]
    if not hits:  # newer BeamNG writes .color.png but ships .dds, or the other way round
        stem = name.rsplit(".", 1)[0].lower()
        hits = [p for p in Path(folder).rglob("*") if p.is_file() and p.name.lower().rsplit(".", 1)[0] == stem]
    return hits[0].read_bytes() if hits else None


def load_vehicle(folder, skin=None, config=None):
    """The vehicle as a kn5-like model (Y up), with the parts of its default configuration."""
    from .formats.collada import load_dae
    folder = Path(folder)
    daes = sorted(folder.rglob("*.dae"))
    if not daes:
        raise ImportError3D("this BeamNG vehicle has no .dae model")
    keep = used_meshes(folder, config)
    model = None
    for f in daes:
        try:
            part = load_dae(f)
        except ImportError3D:
            continue
        if model is None:
            model = part
            continue
        offset = len(model["materials"])
        for n in walk(part["root"]):
            if n["cls"] != 1:
                n["material"] += offset
        model["materials"] += part["materials"]
        for t in part.get("texture_list", []):
            kn5write.add_texture(model, t["name"], t["data"])
        model["root"]["children"] += part["root"]["children"]
    if model is None:
        raise ImportError3D("the vehicle's .dae files couldn't be read")
    model["root"]["name"] = folder.name
    if keep:
        _filter(model["root"], keep)
    mats = materials(folder)
    for i, m in enumerate(model["materials"]):
        entry = mats.get(m["name"])
        if not entry:
            continue
        use = (entry["skins"].get(skin) if skin else None) or entry["base"]
        if not use:
            continue
        data = _find_texture(folder, use["texture"]) if use.get("texture") else None
        tex = None
        if data:
            try:
                tex = _texture(model, PurePosixPath(str(use["texture"])).name, data)
            except (OSError, ValueError):
                tex = None
        if tex is None and use.get("colour"):
            from .importer import _solid_texture
            tex = _solid_texture(model, [float(c) for c in use["colour"][:3]] + [1.0], m["name"])
        if tex:
            from .parts import set_map
            set_map(m, "txDiffuse", tex)
    if not any(n["cls"] != 1 and len(n["verts"]) for n in walk(model["root"])):
        raise ImportError3D("no parts of this vehicle could be shown")
    return model


def _strip_suffix(name):
    return re.sub(r"_\d+$", "", name.lower())


def _filter(node, keep):
    """Keep meshes named in `keep` (or under a node that is); drop the other optional parts.
    If nothing matches (the names differ from the jbeam), everything stays."""
    import copy

    def visit(n, inside):
        here = inside or n["name"].lower() in keep or _strip_suffix(n["name"]) in keep
        kids = []
        for c in n["children"]:
            if c["cls"] == 1:
                visit(c, here)
                kids.append(c)
            elif here or c["name"].lower() in keep or _strip_suffix(c["name"]) in keep:
                kids.append(c)
        n["children"] = kids
    trial = copy.deepcopy(node)
    visit(trial, False)
    if any(n["cls"] != 1 for n in walk(trial)):
        node["children"] = trial["children"]
