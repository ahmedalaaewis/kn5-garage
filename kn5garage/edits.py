"""Saving edits to cars: either as a new car (a copy of the folder) or into the original car
folder with a backup of every file first, so it can be undone.

All writes stay inside the target car folder; backups go to <output>/backups/<car>/<time>/.
"""

import json
import re
import shutil
import time
from pathlib import Path

from .kn5 import Kn5Error

CAR_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
MANIFEST = "kn5garage-backup.json"


class EditError(Kn5Error):
    pass


def copy_blockers(car_dir):
    """Why this car can't be saved as a new car (empty when it can)."""
    car_dir = Path(car_dir)
    if (car_dir / "data.acd").is_file() and not (car_dir / "data").is_dir():
        return ["Its physics are packed in data.acd, which only works under the original folder name. "
                "Unpack the data folder (for example with Content Manager) to save copies."]
    return []


def check_new_id(car_dir, new_id):
    car_dir = Path(car_dir)
    if not isinstance(new_id, str) or not CAR_ID.match(new_id):
        raise EditError("the new car's folder name may only use letters, numbers, - and _ (up to 64)")
    dest = car_dir.parent / new_id
    if dest.exists():
        raise EditError("a folder named %s already exists next to this car" % new_id)
    return dest


def copy_car(car_dir, new_id, display_name=None):
    """Copy a car folder to a new folder next to it, ready to be edited. Returns the new folder."""
    car_dir = Path(car_dir)
    blockers = copy_blockers(car_dir)
    if blockers:
        raise EditError(blockers[0])
    dest = check_new_id(car_dir, new_id)
    shutil.copytree(car_dir, dest, symlinks=True,
                    ignore=lambda d, names: [n for n in names if (Path(d) / n).is_symlink()])
    old_id = car_dir.name
    if (dest / "data").is_dir():
        acd = dest / "data.acd"
        if acd.exists():
            acd.unlink()  # keyed to the old folder name; the unpacked data folder is used instead
    guids = dest / "sfx" / "GUIDs.txt"
    if guids.is_file():  # engine sounds are found by car folder name
        text = guids.read_text(encoding="utf-8", errors="replace")
        guids.write_text(re.sub(r"(/cars/)%s(/)" % re.escape(old_id), r"\g<1>%s\g<2>" % new_id, text,
                                flags=re.I), encoding="utf-8")
    ui_path = dest / "ui" / "ui_car.json"
    if ui_path.is_file():
        from .carinfo import read_json_lenient
        ui = read_json_lenient(ui_path)
        ui["name"] = display_name or "%s (custom)" % (ui.get("name") or old_id)
        ui["parent"] = old_id  # Content Manager shows it as a variant of the original
        ui_path.write_text(json.dumps(ui, indent=2, ensure_ascii=False), encoding="utf-8")
    return dest


class Target:
    """Where an edit is written. Use .write(rel, data) / .remove(rel); call .finish() at the end.

    mode "new": the car was copied to a new folder; files are written there directly.
    mode "original": every file is backed up before it is replaced, created or removed."""

    def __init__(self, car_dir, mode, backups_dir=None, new_id=None, display_name=None):
        self.source = Path(car_dir)
        self.mode = mode
        self.changed, self.created, self.removed = [], [], []
        if mode == "new":
            self.dir = copy_car(self.source, new_id, display_name)
            self.backup = None
        elif mode == "original":
            if backups_dir is None:
                raise EditError("no backup folder")
            self.dir = self.source
            self.backup = Path(backups_dir) / self.source.name / time.strftime("%Y%m%d-%H%M%S")
            n = 2
            while self.backup.exists():
                self.backup = self.backup.with_name(self.backup.name.split("_")[0] + "_%d" % n)
                n += 1
        else:
            raise EditError("save mode must be 'new' or 'original'")

    def path(self, rel):
        rel = str(rel).replace("\\", "/")
        p = (self.dir / rel).resolve()
        if self.dir.resolve() not in p.parents:
            raise EditError("refusing to write outside the car folder: %s" % rel)
        return p, rel

    def _save_backup(self, p, rel):
        if self.backup is None or rel in self.changed or rel in self.created or rel in self.removed:
            return
        if p.exists():
            dst = self.backup / "files" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if p.is_dir():
                shutil.copytree(p, dst)
            else:
                shutil.copy2(p, dst)
            return True
        return False

    def write(self, rel, data):
        p, rel = self.path(rel)
        existed = self._save_backup(p, rel)
        if existed is True:
            self.changed.append(rel)
        elif existed is False:
            self.created.append(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".kn5garage-tmp")
        tmp.write_bytes(data if isinstance(data, bytes) else str(data).encode("utf-8"))
        tmp.replace(p)

    def remove(self, rel):
        p, rel = self.path(rel)
        if not p.exists():
            return
        if self._save_backup(p, rel):
            self.removed.append(rel)
        shutil.rmtree(p) if p.is_dir() else p.unlink()

    def rename(self, rel, new_rel):
        p, rel = self.path(rel)
        q, new_rel = self.path(new_rel)
        if not p.exists():
            return
        self.write(new_rel, p.read_bytes())
        self.remove(rel)

    def finish(self, what):
        if self.backup is not None and (self.changed or self.created or self.removed):
            self.backup.mkdir(parents=True, exist_ok=True)
            (self.backup / MANIFEST).write_text(json.dumps({
                "car": str(self.source), "what": what, "time": time.time(),
                "changed": self.changed, "created": self.created, "removed": self.removed}, indent=1), encoding="utf-8")
        return {"mode": self.mode, "folder": str(self.dir), "carId": self.dir.name,
                "backup": str(self.backup) if self.backup and self.backup.exists() else None,
                "changed": self.changed, "created": self.created, "removed": self.removed}


def list_backups(backups_dir, car_dir=None):
    """Saved edits that can be undone, newest first."""
    root = Path(backups_dir)
    items = []
    for m in root.glob("*/*/" + MANIFEST) if root.is_dir() else []:
        try:
            info = json.loads(m.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if car_dir is not None and Path(info.get("car", "")).resolve() != Path(car_dir).resolve():
            continue
        items.append({"id": "%s/%s" % (m.parent.parent.name, m.parent.name), "car": info.get("car"),
                      "what": info.get("what"), "time": info.get("time"),
                      "files": len(info.get("changed", [])) + len(info.get("created", [])) + len(info.get("removed", []))})
    return sorted(items, key=lambda i: i["time"] or 0, reverse=True)


def restore_backup(backups_dir, backup_id):
    """Undo a saved edit: put changed and removed files back, delete files it created."""
    if not isinstance(backup_id, str) or not re.match(r"^[^/\\]+/[0-9_\-]+$", backup_id):
        raise EditError("unknown backup")
    folder = (Path(backups_dir) / backup_id).resolve()
    if Path(backups_dir).resolve() not in folder.parents:
        raise EditError("unknown backup")
    try:
        info = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise EditError("unknown backup")
    car = Path(info["car"]).resolve()
    if not car.is_dir():
        raise EditError("the car folder isn't there any more")

    def inside(rel):
        p = (car / rel).resolve()
        if car not in p.parents:
            raise EditError("backup points outside the car folder")
        return p

    for rel in info.get("created", []):
        p = inside(rel)
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
        parent = p.parent  # remove folders the edit created and that are now empty (a new skin's folder)
        while parent != car and car in parent.parents and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    for rel in info.get("changed", []) + info.get("removed", []):
        src, dst = folder / "files" / rel, inside(rel)
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        elif src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    shutil.rmtree(folder)
    return {"restored": backup_id, "car": str(car)}


# ---------------------------------------------------------------------------
# Small text formats used by AC's data folder
# ---------------------------------------------------------------------------

def ini_set(text, section, key, value):
    """Set KEY=value in [SECTION] keeping every other line (and comments) as it was."""
    lines = text.splitlines()
    out, in_sec, done, sec_seen = [], False, False, False
    sec_re = re.compile(r"^\s*\[([^\]]+)\]")
    key_re = re.compile(r"^(\s*)%s(\s*)=(\s*)([^;/\r\n]*?)(\s*)((?:;|//).*)?$" % re.escape(key), re.I)
    for line in lines:
        m = sec_re.match(line)
        if m:
            if in_sec and not done:
                out.append("%s=%s" % (key, value))
                done = True
            in_sec = m.group(1).strip().upper() == section.upper()
            sec_seen = sec_seen or in_sec
        elif in_sec and not done:
            k = key_re.match(line)
            if k:
                line = "%s%s%s=%s%s%s%s" % (k.group(1), key, k.group(2), k.group(3), value,
                                             k.group(5) if k.group(6) else "", k.group(6) or "")
                done = True
        out.append(line)
    if not done:
        if not sec_seen:
            out += ["", "[%s]" % section]
        out.append("%s=%s" % (key, value))
    return "\n".join(out) + "\n"


def read_lut(text):
    """AC .lut curves: 'x|y' per line, ; or // comments."""
    points = []
    for line in text.splitlines():
        line = re.split(r";|//", line)[0].strip()
        if "|" in line:
            a, b = line.split("|", 1)
            try:
                points.append([float(a), float(b)])
            except ValueError:
                pass
    return points


def write_lut(points):
    def num(v):
        return ("%.4f" % v).rstrip("0").rstrip(".") if v != int(v) else str(int(v))
    return "\n".join("%s|%s" % (num(x), num(y)) for x, y in points) + "\n"
