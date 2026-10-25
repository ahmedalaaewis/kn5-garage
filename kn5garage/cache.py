"""Caches that make a big library fast: car summaries between scans, quick model
fingerprints for finding identical cars, and the 3D preview files."""

import hashlib
import json
import os
import threading
from pathlib import Path

SCAN_CACHE_VERSION = 3
FINGERPRINT_CHUNK = 1 << 20  # bytes hashed from the start and from the end of a model


def _stat(path):
    try:
        st = os.stat(path)
        return [st.st_mtime_ns, st.st_size]
    except OSError:
        return None


def folder_signature(car_dir):
    """Changes whenever a file that the car summary depends on changes."""
    car_dir = Path(car_dir)
    sig = [_stat(car_dir), _stat(car_dir / "ui" / "ui_car.json"), _stat(car_dir / "ui" / "badge.png"),
           _stat(car_dir / "skins"), _stat(car_dir / "animations"), _stat(car_dir / "data" / "lods.ini"),
           _stat(car_dir / "extension")]
    try:
        kn5s = sorted(car_dir.glob("*.kn5"))
    except OSError:
        kn5s = []
    sig += [[f.name] + (_stat(f) or []) for f in kn5s]
    skins = car_dir / "skins"
    if skins.is_dir():
        try:
            first = min((d for d in skins.iterdir() if d.is_dir()), default=None)
        except OSError:
            first = None
        if first is not None:
            sig.append([first.name, _stat(first / "preview.jpg")])
    return sig


def model_fingerprint(path):
    """Size + hash of the first and last megabyte: equal for copies of the same model file,
    without reading whole 100 MB models."""
    size = os.path.getsize(path)
    h = hashlib.sha256(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(FINGERPRINT_CHUNK))
        if size > 2 * FINGERPRINT_CHUNK:
            f.seek(-FINGERPRINT_CHUNK, os.SEEK_END)
            h.update(f.read(FINGERPRINT_CHUNK))
    return h.hexdigest()[:32]


class ScanCache:
    """{car folder: {"sig", "summary", "fingerprint"}}, saved as JSON in the cache folder."""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.entries = {}
        self.dirty = False
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("version") == SCAN_CACHE_VERSION and isinstance(data.get("cars"), dict):
                self.entries = data["cars"]
        except (OSError, ValueError, AttributeError):
            pass

    def summary(self, car_dir, make, sig=None):
        """The cached summary if the folder hasn't changed, else make(car_dir) (and cache it).
        sig: what "unchanged" means for entries that aren't AC car folders."""
        key = str(Path(car_dir).resolve())
        sig = folder_signature(car_dir) if sig is None else sig
        with self.lock:
            e = self.entries.get(key)
            if e and e.get("sig") == sig:
                return json.loads(json.dumps(e["summary"]))  # a copy the caller may change
        summary = make(car_dir)
        with self.lock:
            self.entries[key] = {"sig": sig, "summary": summary, "fingerprint": None}
            self.dirty = True
        return json.loads(json.dumps(summary))

    def fingerprint(self, car_dir, kn5_name):
        key = str(Path(car_dir).resolve())
        with self.lock:
            e = self.entries.get(key)
            if e and e.get("fingerprint") and e["fingerprint"][0] == kn5_name:
                return e["fingerprint"][1]
        try:
            fp = model_fingerprint(Path(car_dir) / kn5_name)
        except OSError:
            return None
        with self.lock:
            if key in self.entries:
                self.entries[key]["fingerprint"] = [kn5_name, fp]
                self.dirty = True
        return fp

    def save(self, keep=None):
        """Write to disk; keep = car folders still in use (others are forgotten)."""
        with self.lock:
            if keep is not None:
                keep = {str(Path(k).resolve()) for k in keep}
                for k in [k for k in self.entries if k not in keep]:
                    del self.entries[k]
                    self.dirty = True
            if not self.dirty:
                return
            data = json.dumps({"version": SCAN_CACHE_VERSION, "cars": self.entries})
            self.dirty = False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(data, encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass


class PreviewCache:
    """Light preview .glb files: at most max_files and max_bytes, oldest removed first."""

    def __init__(self, folder, max_files=60, max_bytes=2 << 30):
        self.folder = Path(folder)
        self.max_files = max_files
        self.max_bytes = max_bytes

    def path(self, name):
        self.folder.mkdir(parents=True, exist_ok=True)
        return self.folder / name

    def prune(self, keep=None, prefix=None):
        """Drop old versions of the same car + skin (same prefix) and the oldest files over the limits."""
        try:
            files = [f for f in self.folder.glob("*.glb") if f.is_file()]
        except OSError:
            return
        for f in files:
            if prefix and f.name.startswith(prefix) and f.name != keep:
                f.unlink(missing_ok=True)
        files = sorted((f for f in files if f.exists()), key=lambda f: f.stat().st_mtime, reverse=True)
        total = 0
        for i, f in enumerate(files):
            total += f.stat().st_size
            if f.name != keep and (i >= self.max_files or total > self.max_bytes):
                f.unlink(missing_ok=True)
        for f in self.folder.glob("*.part"):
            f.unlink(missing_ok=True)
