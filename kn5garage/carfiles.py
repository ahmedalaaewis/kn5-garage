"""Finding things in an Assetto Corsa car folder: models, LODs, skins, animations."""

import configparser
import re
from pathlib import Path

from .kn5 import Kn5Error, read_ksanim

SKIN_IMAGE_TYPES = (".dds", ".png", ".jpg", ".jpeg")


def _lods_ini_file(car_dir, section):
    lods = car_dir / "data" / "lods.ini"
    if not lods.is_file():
        return None
    cfg = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        cfg.read_string(lods.read_text(errors="replace"))
        if cfg.has_option(section, "FILE"):
            f = car_dir / cfg.get(section, "FILE").strip()
            # lods.ini comes from the mod: only accept a .kn5 inside this car folder
            if f.suffix.lower() == ".kn5" and f.is_file() and car_dir.resolve() in f.resolve().parents:
                return f
    except (configparser.Error, OSError, ValueError):
        pass
    return None


def find_main_kn5(car_dir):
    car_dir = Path(car_dir)
    f = _lods_ini_file(car_dir, "LOD_0")
    if f:
        return f
    all_kn5 = list(car_dir.glob("*.kn5"))
    if not all_kn5:
        return None
    main = [f for f in all_kn5 if not is_secondary_kn5(f)]
    return max(main or all_kn5, key=lambda f: f.stat().st_size)


def is_secondary_kn5(path):
    return bool(re.search(r"_lod|collider|driver", Path(path).stem, re.I))


def find_lod_kn5(car_dir, level=1):
    """A lower-detail model: LOD_<level> from data/lods.ini, else a *_lod_b / *_lod_1 file."""
    car_dir = Path(car_dir)
    f = _lods_ini_file(car_dir, "LOD_%d" % level)
    if f:
        return f
    letter = "abcdefgh"[level]
    for pat in (r"_lod_?%s$" % letter, r"_lod_?%d$" % level):
        for f in sorted(car_dir.glob("*.kn5")):
            if re.search(pat, f.stem, re.I) and f.stat().st_size > 1000:
                return f
    return None


def load_animations(car_dir, log=print):
    """All readable .ksanim files in the car's animations folder: {file stem: tracks}."""
    out = {}
    folder = Path(car_dir) / "animations"
    if not folder.is_dir():
        return out
    for f in sorted(folder.glob("*.ksanim")):
        try:
            out[f.stem] = read_ksanim(f)
        except (Kn5Error, OSError, ValueError) as e:
            log("    ! skipped animation %s: %s" % (f.name, e))
    return out


def list_skins(car_dir):
    skins_dir = Path(car_dir) / "skins"
    if not skins_dir.is_dir():
        return []
    return sorted(d for d in skins_dir.iterdir() if d.is_dir())


def load_skin(car_dir, skin_name):
    """Return (skin name, {lowercase file name: bytes}). skin_name None = first skin."""
    if skin_name == "none":
        return None, {}
    skins = list_skins(car_dir)
    if not skins:
        return None, {}
    chosen = skins[0]
    if skin_name:
        match = [d for d in skins if d.name.lower() == str(skin_name).lower()]
        if not match:
            raise Kn5Error("skin '%s' not found" % skin_name)
        chosen = match[0]
    files = {}
    for f in sorted(chosen.iterdir()):
        if f.is_file() and f.suffix.lower() in SKIN_IMAGE_TYPES:
            data = f.read_bytes()
            files[f.name.lower()] = data
            files.setdefault(f.stem.lower(), data)  # also match body.png to body.dds
    return chosen.name, files


def skin_stamp(car_dir, skin_name):
    """Changes when any file of the skin changes (used to refresh cached previews)."""
    if not skin_name or skin_name == "none":
        return 0
    d = Path(car_dir) / "skins" / skin_name
    try:
        return max((int(f.stat().st_mtime) for f in d.iterdir() if f.is_file()), default=0)
    except OSError:
        return 0


def find_cars(folder):
    folder = Path(folder)
    if any(folder.glob("*.kn5")):
        return [folder]
    return sorted(d for d in folder.iterdir() if d.is_dir() and any(d.glob("*.kn5")))


def output_name(car_dir, skin, preset="render", ext=".glb"):
    name = Path(car_dir).name if not skin else "%s__%s" % (Path(car_dir).name, skin)
    if preset not in ("render", "preview"):
        name += "__" + preset
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name) + ext
