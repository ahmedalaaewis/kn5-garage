"""Installing downloaded mods: cars and skin packs from .zip (built in), .7z and .rar (through
7-Zip, when it is installed).

Archives from the internet are untrusted. Before anything is unpacked the list of files is
checked: no absolute paths, no "..", no links, a cap on the number of files and their total
size. Programs and scripts (.exe, .bat, …) are never unpacked. Everything is unpacked into
the app's own work folder first; only car and skin folders found there are copied to the
place the user chooses, and a folder that is replaced is moved to the backups first."""

import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath

from . import carfiles, carinfo
from .edits import CAR_ID, EditError

MAX_FILES = 50_000
MAX_TOTAL = 8 << 30          # 8 GB unpacked
MAX_RATIO = 200              # unpacked / packed size (zip bombs)
SKIP_TYPES = {".exe", ".dll", ".bat", ".cmd", ".com", ".scr", ".ps1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh",
              ".msi", ".msp", ".reg", ".lnk", ".url", ".hta", ".cpl", ".jar", ".sys", ".pif", ".appx", ".msix"}
SEVEN_ZIP = ["7-Zip/7z.exe", "7-Zip-Zstandard/7z.exe", "NanaZip/7z.exe"]
SKIN_NAME = re.compile(r"^[A-Za-z0-9_\-. ()]{1,80}$")


def find_7zip():
    if sys.platform != "win32":
        return shutil.which("7z") or shutil.which("7zz")
    for root in {os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramW6432", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")}:
        for rel in SEVEN_ZIP:
            p = Path(root) / rel
            if p.is_file():
                return str(p)
    return None


def safe_member(name):
    """The archive path as a relative path, or None when it must not be unpacked."""
    name = str(name).replace("\\", "/")
    p = PurePosixPath(name)
    if not name or name.startswith("/") or ".." in p.parts or any(ch in name for ch in ':*?"<>|\0'):
        return None  # absolute, C:..., ../ or a name Windows can't hold
    return p


def check_listing(entries, packed_size):
    """entries: [(name, size, is_dir, is_link)]. Returns the names to unpack and the skipped ones."""
    if len(entries) > MAX_FILES:
        raise EditError("the archive has more than %d files" % MAX_FILES)
    total = sum(max(0, int(e[1] or 0)) for e in entries)
    if total > MAX_TOTAL:
        raise EditError("the archive unpacks to more than 8 GB")
    if packed_size and total > MAX_RATIO * max(packed_size, 1 << 20):
        raise EditError("the archive unpacks to far more than its size (it may be a zip bomb)")
    keep, skipped = [], []
    for name, size, is_dir, is_link in entries:
        p = safe_member(name)
        if p is None or is_link:
            raise EditError("the archive has an unsafe path: %s" % str(name)[:120])
        if is_dir:
            continue
        if p.suffix.lower() in SKIP_TYPES:
            skipped.append(str(p))
        else:
            keep.append(str(p))
    return keep, skipped


# ---------------------------------------------------------------------------
# unpacking
# ---------------------------------------------------------------------------

def _zip_entries(zf):
    out = []
    for i in zf.infolist():
        mode = (i.external_attr >> 16) & 0xFFFF
        out.append((i.filename, i.file_size, i.is_dir(), stat.S_ISLNK(mode)))
    return out


def _unpack_zip(path, dest):
    with zipfile.ZipFile(path) as zf:
        keep, skipped = check_listing(_zip_entries(zf), path.stat().st_size)
        wanted = set(keep)
        written = 0
        for info in zf.infolist():
            name = str(safe_member(info.filename)) if safe_member(info.filename) else None
            if info.is_dir() or name not in wanted:
                continue
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                while True:
                    chunk = src.read(1 << 20)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_TOTAL:
                        raise EditError("the archive unpacks to more than 8 GB")
                    out.write(chunk)
    return skipped


def _7z_entries(exe, path):
    p = subprocess.run([exe, "l", "-slt", "-ba", "--", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    if p.returncode != 0:
        raise EditError("7-Zip couldn't read the archive (it may be damaged or need a password)")
    entries, cur = [], {}
    for line in p.stdout.splitlines() + [""]:
        if not line.strip():
            if "Path" in cur:
                attrs = cur.get("Attributes", "")
                unix = attrs.split()[-1] if attrs.split() else ""  # e.g. "A -rw-r--r--"
                is_dir = cur.get("Folder") == "+" or attrs.startswith("D")
                is_link = bool(cur.get("Symbolic Link")) or unix.startswith("l")
                entries.append((cur["Path"], int(cur.get("Size") or 0), is_dir, is_link))
            cur = {}
            continue
        k, _, v = line.partition(" = ")
        cur[k.strip()] = v
    return entries


def _unpack_7z(exe, path, dest):
    keep, skipped = check_listing(_7z_entries(exe, path), path.stat().st_size)
    listfile = dest.parent / (dest.name + ".files.txt")
    listfile.write_text("\n".join(keep), encoding="utf-8")
    try:
        p = subprocess.run([exe, "x", "-y", "-bd", "-snl-", "-o" + str(dest), "-i@" + str(listfile), "-scsUTF-8",
                            "--", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=3600)
    finally:
        listfile.unlink(missing_ok=True)
    if p.returncode not in (0, 1):  # 1 = warnings
        raise EditError("7-Zip couldn't unpack the archive")
    return skipped


def _clean_tree(root):
    """After unpacking: no links or junctions, nothing outside the folder, no programs."""
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        d = Path(dirpath)
        for n in list(dirnames):
            p = d / n
            if p.is_symlink() or _is_junction(p):
                os.unlink(p) if p.is_symlink() else os.rmdir(p)
                dirnames.remove(n)
        for n in filenames:
            p = d / n
            if p.is_symlink() or p.suffix.lower() in SKIP_TYPES:
                p.unlink()
            elif root not in p.resolve().parents:
                p.unlink()


def _is_junction(p):
    try:
        return bool(os.lstat(p).st_file_attributes & 0x400)
    except (AttributeError, OSError):
        return False


def unpack(archive, work_root):
    """Unpack into a new folder in work_root/import; returns (folder, skipped files)."""
    archive = Path(archive)
    if not archive.is_file():
        raise EditError("file not found")
    dest = Path(work_root) / "import" / secrets.token_hex(6)
    dest.mkdir(parents=True)
    try:
        ext = archive.suffix.lower()
        if ext == ".zip" or zipfile.is_zipfile(archive):
            try:
                skipped = _unpack_zip(archive, dest)
            except zipfile.BadZipFile:
                raise EditError("the .zip file is damaged")
            except RuntimeError as e:  # encrypted
                raise EditError("the archive needs a password: %s" % e)
        elif ext in (".7z", ".rar"):
            exe = find_7zip()
            if not exe:
                raise EditError("%s files are unpacked with 7-Zip; install it from 7-zip.org (free) and try again" % ext)
            skipped = _unpack_7z(exe, archive, dest)
        else:
            raise EditError("choose a .zip, .7z or .rar file")
        _clean_tree(dest)
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return dest, skipped


# ---------------------------------------------------------------------------
# what's inside
# ---------------------------------------------------------------------------

def _is_skin_dir(d):
    try:
        files = [f for f in d.iterdir() if f.is_file()]
    except OSError:
        return False
    names = {f.name.lower() for f in files}
    return "ui_skin.json" in names or (any(f.suffix.lower() in carfiles.SKIN_IMAGE_TYPES for f in files)
                                      and ("preview.jpg" in names or "livery.png" in names))


def contents(folder):
    """Cars and skins found in an unpacked mod."""
    folder = Path(folder)
    cars = []
    car_dirs = carfiles.find_cars(folder)
    for d in car_dirs:
        try:
            info = carinfo.car_summary(d)
        except Exception:
            info = {"name": d.name, "status": "unknown"}
        cars.append({"id": d.name, "name": info.get("name") or d.name, "status": info.get("status"),
                     "path": d.relative_to(folder).as_posix(), "validId": bool(CAR_ID.match(d.name))})
    inside = [d.resolve() for d in car_dirs]
    skins = []
    for dirpath, dirnames, _ in os.walk(folder):
        d = Path(dirpath)
        if any(c == d.resolve() or c in d.resolve().parents for c in inside):
            dirnames[:] = []
            continue
        if _is_skin_dir(d) and d != folder:
            parent = d.parent
            car = parent.parent.name if parent.name.lower() == "skins" and parent.parent != folder else None
            skins.append({"name": d.name, "car": car, "path": d.relative_to(folder).as_posix()})
            dirnames[:] = []
    notes = []
    if any(p.name.lower() == "tracks" for p in folder.rglob("tracks") if p.is_dir()):
        notes.append("This mod also has tracks; only cars and skins are installed here.")
    return {"cars": cars, "skins": skins, "notes": notes}


# ---------------------------------------------------------------------------
# installing
# ---------------------------------------------------------------------------

def _free_name(parent, name):
    n = 2
    while (parent / ("%s_%d" % (name, n))).exists():
        n += 1
    return "%s_%d" % (name, n)


def _move_to_backup(path, backups_dir, what):
    dest = Path(backups_dir) / "replaced" / time.strftime("%Y%m%d-%H%M%S") / path.name
    n = 2
    while dest.exists():
        dest = dest.with_name("%s_%d" % (path.name, n))
        n += 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(dest))
    (dest.parent / (dest.name + ".json")).write_text(json.dumps({"from": str(path), "what": what, "time": time.time()}),
                                                     encoding="utf-8")
    return dest


def _copy(src, dest, conflict, backups_dir, what):
    """Copy a folder; conflict: skip / rename / replace. Returns (installed path or None, note)."""
    if dest.exists():
        if conflict == "skip":
            return None, "%s is already there: skipped" % dest.name
        if conflict == "rename":
            dest = dest.with_name(_free_name(dest.parent, dest.name))
        elif conflict == "replace":
            moved = _move_to_backup(dest, backups_dir, what)
            note = "the old %s was moved to %s" % (dest.name, moved)
            shutil.copytree(src, dest)
            return dest, note
        else:
            raise EditError("unknown choice for folders that already exist")
    shutil.copytree(src, dest)
    return dest, None


def install(folder, cars_dir, choices, conflict, backups_dir, skin_cars=None):
    """Copy chosen cars (into cars_dir) and skins (into cars_dir/<car>/skins).

    choices: {"cars": [paths], "skins": [paths]} from contents(); skin_cars: {skin path: car id}
    for skins whose car the user picked."""
    folder, cars_dir = Path(folder), Path(cars_dir)
    if not cars_dir.is_dir():
        raise EditError("choose a folder for the cars (assettocorsa\\content\\cars)")
    found = contents(folder)
    by_path = {c["path"]: c for c in found["cars"]}
    skins_by_path = {s["path"]: s for s in found["skins"]}
    done, notes = [], []
    for rel in choices.get("cars", []):
        c = by_path.get(rel)
        if c is None:
            raise EditError("unknown car in the mod: %s" % rel)
        if not c["validId"]:
            raise EditError("the car folder name %s can't be used" % c["id"])
        dest, note = _copy(folder / rel, cars_dir / c["id"], conflict, backups_dir, "car")
        notes += [note] if note else []
        if dest:
            done.append({"kind": "car", "id": dest.name, "path": str(dest)})
    for rel in choices.get("skins", []):
        s = skins_by_path.get(rel)
        if s is None:
            raise EditError("unknown skin in the mod: %s" % rel)
        car = (skin_cars or {}).get(rel) or s["car"]
        if not car or not CAR_ID.match(car) or not (cars_dir / car).is_dir():
            notes.append("No car folder %s for the skin %s: choose its car" % (car or "?", s["name"]))
            continue
        if not SKIN_NAME.match(s["name"]):
            raise EditError("the skin folder name %s can't be used" % s["name"])
        dest, note = _copy(folder / rel, cars_dir / car / "skins" / s["name"], conflict, backups_dir, "skin")
        notes += [note] if note else []
        if dest:
            done.append({"kind": "skin", "car": car, "id": dest.name, "path": str(dest)})
    return {"installed": done, "notes": notes}


def cleanup(work_root, max_age=24 * 3600):
    """Remove unpacked mods older than a day."""
    base = Path(work_root) / "import"
    if not base.is_dir():
        return
    for d in base.iterdir():
        try:
            if d.is_dir() and time.time() - d.stat().st_mtime > max_age:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass
