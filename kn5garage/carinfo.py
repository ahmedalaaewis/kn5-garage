"""
Read car details (name, brand, author, specs, skins, protection) from an
Assetto Corsa car folder. Everything here only reads files.
"""

import configparser
import html
import json
import re
from pathlib import Path

from . import carfiles
from .kn5 import probe_kn5

SPEC_KEYS = ("bhp", "torque", "weight", "topspeed", "acceleration", "pwratio")
IMAGE_TYPES = (".png", ".jpg", ".jpeg")


def read_json_lenient(path):
    """AC json files are often slightly broken (BOM, trailing commas, raw newlines)."""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return {}
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    for candidate in (text, re.sub(r",\s*([}\]])", r"\1", text)):
        try:
            data = json.loads(candidate, strict=False)
            return data if isinstance(data, dict) else {}
        except ValueError:
            pass
    # Last resort: pick out simple "key": "value" pairs.
    return {m.group(1): m.group(2).replace('\\"', '"')
            for m in re.finditer(r'"(\w+)"\s*:\s*"((?:[^"\\]|\\.)*)"', text)}


def clean_text(value):
    """Turn AC's html-ish description text into plain text."""
    if value is None:
        return None
    text = re.sub(r"<br\s*/?>", "\n", str(value), flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip() or None


def clean_curve(points):
    out = []
    if isinstance(points, list):
        for p in points:
            try:
                out.append([float(p[0]), float(p[1])])
            except (TypeError, ValueError, IndexError):
                pass
    return out


def text_or_none(value):
    if value is None or isinstance(value, (dict, list)):
        return None
    return clean_text(value)


def protection_info(status, author):
    who = author or "The author"
    if status == "protected":
        return ("%s has protected this model. Its textures are encrypted and its surface data "
                "(normals, UVs) is scrambled, so only the shape can be exported. For a full "
                "export, ask %s for an unprotected version." % (who, author or "the author"))
    if status == "encrypted":
        return "The model file is encrypted or damaged and can't be read."
    if status == "no-model":
        return "No .kn5 model file was found in this folder."
    return None


def model_status(kn5_path):
    if kn5_path is None:
        return "no-model", {}
    probe = probe_kn5(kn5_path)
    if not probe["readable"]:
        return "encrypted", probe
    return ("protected" if probe["protected"] else "ok"), probe


def skin_info(skin_dir):
    ui = read_json_lenient(skin_dir / "ui_skin.json")
    preview = skin_dir / "preview.jpg"
    livery = skin_dir / "livery.png"
    return {
        "id": skin_dir.name,
        "name": text_or_none(ui.get("skinname")) or skin_dir.name.replace("_", " "),
        "driver": text_or_none(ui.get("drivername")),
        "team": text_or_none(ui.get("team")),
        "number": text_or_none(ui.get("number")),
        "country": text_or_none(ui.get("country")),
        "preview": "skins/%s/preview.jpg" % skin_dir.name if preview.is_file() else None,
        "livery": "skins/%s/livery.png" % skin_dir.name if livery.is_file() else None,
    }


def car_summary(car_dir):
    car_dir = Path(car_dir)
    ui = read_json_lenient(car_dir / "ui" / "ui_car.json")
    main = carfiles.find_main_kn5(car_dir)
    status, probe = model_status(main)
    skins = carfiles.list_skins(car_dir)
    first = skin_info(skins[0]) if skins else None
    specs = ui.get("specs") if isinstance(ui.get("specs"), dict) else {}
    tags = ui.get("tags") if isinstance(ui.get("tags"), list) else []
    author = text_or_none(ui.get("author"))
    has_ui = (car_dir / "ui" / "ui_car.json").is_file()
    issues = []
    if status in ("encrypted", "no-model"):
        issues.append("model can't be read" if status == "encrypted" else "no .kn5 model")
    if not has_ui:
        issues.append("no ui_car.json (name, specs)")
    if not skins:
        issues.append("no skins folder")
    elif not (first and first["preview"]):
        issues.append("no preview image")
    return {
        "id": car_dir.name,
        "name": text_or_none(ui.get("name")) or car_dir.name,
        "brand": text_or_none(ui.get("brand")),
        "author": author,
        "version": text_or_none(ui.get("version")),
        "url": text_or_none(ui.get("url")),
        "year": text_or_none(ui.get("year")),
        "country": text_or_none(ui.get("country")),
        "carClass": text_or_none(ui.get("class")),
        "tags": [t for t in (text_or_none(t) for t in tags[:12]) if t],
        "specs": {k: text_or_none(specs.get(k)) for k in SPEC_KEYS},
        "status": status,
        "statusText": protection_info(status, author),
        "kn5": main.name if main else None,
        "kn5Size": main.stat().st_size if main else 0,
        "textureBytes": probe.get("texture_bytes", 0),
        "skinCount": len(skins),
        "preview": first["preview"] if first else None,
        "badge": "ui/badge.png" if (car_dir / "ui" / "badge.png").is_file() else None,
        "hasUi": has_ui,
        "issues": issues,
        "hasAnimations": (car_dir / "animations").is_dir() and any((car_dir / "animations").glob("*.ksanim")),
        "hasLod": carfiles.find_lod_kn5(car_dir, 1) is not None,
        "hasExtension": (car_dir / "extension").is_dir(),
        # Content Manager: "parent" marks a variant / upgrade of another car
        "parent": text_or_none(ui.get("parent")),
    }


def file_role(path):
    stem = path.stem.lower()
    if "collider" in stem:
        return "collider"
    if "_lod" in stem:
        return "lod"
    if "driver" in stem:
        return "driver"
    return "model"


def car_details(car_dir):
    car_dir = Path(car_dir)
    info = car_summary(car_dir)
    ui = read_json_lenient(car_dir / "ui" / "ui_car.json")
    main = info["kn5"]
    files = []
    for f in sorted(car_dir.glob("*.kn5")):
        files.append({"name": f.name, "size": f.stat().st_size,
                      "role": "main model" if f.name == main else file_role(f)})
    folder_size = 0
    for f in car_dir.rglob("*"):
        try:
            if f.is_file():
                folder_size += f.stat().st_size
        except OSError:
            pass
    info.update({
        "description": clean_text(ui.get("description")),
        "torqueCurve": clean_curve(ui.get("torqueCurve")),
        "powerCurve": clean_curve(ui.get("powerCurve")),
        "skins": [skin_info(d) for d in carfiles.list_skins(car_dir)],
        "files": files,
        "folderSize": folder_size,
        "hasDataAcd": (car_dir / "data.acd").is_file(),
        "hasDataFolder": (car_dir / "data").is_dir(),
        "hasExtension": (car_dir / "extension").is_dir(),
        "animations": len(list((car_dir / "animations").glob("*.ksanim")))
        if (car_dir / "animations").is_dir() else 0,
        "path": str(car_dir),
        "csp": csp_features(car_dir),
        "carData": car_data(car_dir),
        "upgradeIcon": "ui/upgrade.png" if (car_dir / "ui" / "upgrade.png").is_file() else None,
    })
    return info


def read_ini(path):
    """AC / CSP ini files: ';' and '//' comments, duplicate keys, no interpolation."""
    cfg = configparser.ConfigParser(strict=False, interpolation=None, inline_comment_prefixes=(";", "//"),
                                    comment_prefixes=(";", "//", "#"))
    try:
        cfg.read_string(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, configparser.Error):
        return None
    return cfg


CSP_FEATURES = [  # Custom Shaders Patch ext_config.ini sections -> what they add
    (r"^LIGHT_|^LIGHTS?$", "Extra lights"),
    (r"^EMISSIVE_", "Glowing parts"),
    (r"^SHADER_REPLACEMENT", "Shader tweaks"),
    (r"^(RAIN_FX|WIPERS?|WIPER_)", "Rain and wipers"),
    (r"MIRROR", "Real mirrors"),
    (r"^(INSTRUMENTS|DIGITAL_|DISPLAY|SCREEN)", "Digital displays"),
    (r"^(TYRES?_FX|GROOVES)", "Tyre effects"),
    (r"^(ANIMATED|ANIMATION|EXTRA_ANIMATION|DOOR|HINGE|WING_ANIMATION)", "Extra animations"),
    (r"^(SOUND|AUDIO)", "Sounds"),
    (r"^(DRIVER|SEAT|STEERING_WHEEL)", "Driver and cockpit"),
    (r"^(PARTICLES|EXHAUST|FLAMES)", "Exhaust effects"),
    (r"^(SHAKING|SUSPENSION_ANIM|WOBBLE)", "Moving body parts"),
]


def csp_features(car_dir):
    """What the car's Custom Shaders Patch config (extension/ext_config.ini) adds."""
    cfg = read_ini(Path(car_dir) / "extension" / "ext_config.ini")
    if cfg is None:
        return []
    found = []
    for section in cfg.sections():
        for pattern, label in CSP_FEATURES:
            if re.search(pattern, section, re.I) and label not in found:
                found.append(label)
    return found


def ini_value(cfg, section, key):
    if cfg is None or not cfg.has_option(section, key):
        return None
    return cfg.get(section, key).strip().strip('"') or None


def car_data(car_dir):
    """Real numbers from an unpacked data folder (packed data.acd files are left alone)."""
    data = Path(car_dir) / "data"
    if not data.is_dir():
        return {}
    car = read_ini(data / "car.ini")
    drive = read_ini(data / "drivetrain.ini")
    engine = read_ini(data / "engine.ini")
    out = {
        "mass": ini_value(car, "BASIC", "TOTALMASS"),
        "fuel": ini_value(car, "FUEL", "MAX_FUEL"),
        "drive": ini_value(drive, "TRACTION", "TYPE"),
        "gears": ini_value(drive, "GEARS", "COUNT"),
        "limiter": ini_value(engine, "ENGINE_DATA", "LIMITER"),
        "turbo": bool(engine and any(s.upper().startswith("TURBO_") for s in engine.sections())),
    }
    return {k: v for k, v in out.items() if v}


def resolve_car_file(car_dir, rel):
    """Return the path of an image inside car_dir, or None if rel points elsewhere."""
    car_dir = Path(car_dir).resolve()
    try:
        path = (car_dir / rel).resolve()
    except (OSError, ValueError):
        return None
    if car_dir not in path.parents or path.suffix.lower() not in IMAGE_TYPES or not path.is_file():
        return None
    return path
