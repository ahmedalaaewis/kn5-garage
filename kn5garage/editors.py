"""Image and design programs (Photoshop, Illustrator, GIMP, Krita, Paint.NET, Inkscape, Affinity…)
for editing liveries and textures.

Only programs found in their usual install folders, or picked by the user, are ever started,
always with one file from the app's own work folder and no shell."""

import os
import re
import subprocess
import sys
from pathlib import Path

# name, kind, paths below Program Files (glob patterns; newest version first)
KNOWN = [
    ("Photoshop", "image", ["Adobe/Adobe Photoshop */Photoshop.exe"]),
    ("Illustrator", "vector", ["Adobe/Adobe Illustrator */Support Files/Contents/Windows/Illustrator.exe"]),
    ("GIMP", "image", ["GIMP 3/bin/gimp-3*.exe", "GIMP 2/bin/gimp-2.*.exe"]),
    ("Krita", "image", ["Krita (x64)/bin/krita.exe", "Krita/bin/krita.exe"]),
    ("Paint.NET", "image", ["paint.net/paintdotnet.exe"]),
    ("Affinity Photo", "image", ["Affinity/Photo 2/Photo.exe", "Affinity/Photo/Photo.exe"]),
    ("Affinity Designer", "vector", ["Affinity/Designer 2/Designer.exe", "Affinity/Designer/Designer.exe"]),
    ("Inkscape", "vector", ["Inkscape/bin/inkscape.exe"]),
]
NOT_MAIN = re.compile(r"console|debug|uninst", re.I)  # e.g. gimp-console-3.0.exe


def _version_key(p):
    return [int(n) for n in re.findall(r"\d+", str(p))] or [0]


def detect():
    """Installed programs we know: [{name, kind, path}]."""
    if sys.platform != "win32":
        return []
    roots = {os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramW6432", r"C:\Program Files"),
             os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
             str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs")}
    found = []
    for name, kind, patterns in KNOWN:
        hits = []
        for root in roots:
            base = Path(root)
            if base.is_dir():
                for pat in patterns:
                    hits += [p for p in base.glob(pat) if p.is_file() and not NOT_MAIN.search(p.name)]
        if hits:
            best = sorted(hits, key=_version_key, reverse=True)[0]
            found.append({"name": name, "kind": kind, "path": str(best)})
    return found


def valid_program(path):
    p = Path(path)
    return p.is_file() and (p.suffix.lower() == ".exe" if sys.platform == "win32" else os.access(p, os.X_OK))


def all_editors(saved):
    """Detected programs plus the ones the user added (settings 'editors': [{name, path}])."""
    out, seen = [], set()
    for e in detect() + [dict(x, kind=x.get("kind", "custom")) for x in (saved or []) if isinstance(x, dict)]:
        key = str(e.get("path", "")).lower()
        if key and key not in seen and valid_program(e["path"]):
            seen.add(key)
            out.append({"name": str(e.get("name") or Path(e["path"]).stem)[:40], "kind": e.get("kind", "custom"),
                        "path": str(e["path"])})
    return out


def launch(program, file):
    """Start a program with one file. The caller checks both are allowed."""
    return subprocess.Popen([str(program), str(file)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_default(file):
    """Open with the program Windows uses for this kind of file."""
    if sys.platform == "win32":
        os.startfile(str(file))  # noqa: S606 - a file in our own work folder
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(file)])
