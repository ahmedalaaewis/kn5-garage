"""The user's own data about their cars and exports: favourites, tags and export history.
Saved as small JSON files on this PC (never inside car folders)."""

import json
import threading
import time
from pathlib import Path

MAX_TAGS = 20
MAX_TAG_LEN = 40
MAX_HISTORY = 200


def _load(path, default):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, type(default)) else default
    except (OSError, ValueError):
        return default


def _save(path, data):
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def folder_key(folder):
    """Cars are remembered by their folder, so favourites survive renames of the display name."""
    return str(Path(folder).resolve()).lower()


def clean_tags(tags):
    out = []
    for t in tags:
        t = " ".join(str(t).split())[:MAX_TAG_LEN]
        if t and t.lower() not in (x.lower() for x in out):
            out.append(t)
    return out[:MAX_TAGS]


class Library:
    """{"favourites": [folder key], "tags": {folder key: [tag]}}"""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        data = _load(self.path, {})
        fav = data.get("favourites")
        tags = data.get("tags")
        self.favourites = set(f for f in fav if isinstance(f, str)) if isinstance(fav, list) else set()
        self.tags = {k: clean_tags(v) for k, v in tags.items()
                     if isinstance(k, str) and isinstance(v, list)} if isinstance(tags, dict) else {}

    def get(self, folder):
        key = folder_key(folder)
        with self.lock:
            return key in self.favourites, list(self.tags.get(key, []))

    def set(self, folder, favourite=None, tags=None):
        key = folder_key(folder)
        with self.lock:
            if favourite is not None:
                (self.favourites.add if favourite else self.favourites.discard)(key)
            if tags is not None:
                tags = clean_tags(tags)
                if tags:
                    self.tags[key] = tags
                else:
                    self.tags.pop(key, None)
            data = {"favourites": sorted(self.favourites), "tags": self.tags}
        _save(self.path, data)
        return self.get(folder)

    def all_tags(self):
        with self.lock:
            return sorted({t for tags in self.tags.values() for t in tags}, key=str.lower)


class History:
    """The last exports, newest first."""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.items = [i for i in _load(self.path, []) if isinstance(i, dict)][:MAX_HISTORY]

    def add(self, entry):
        entry = dict(entry, time=time.time())
        with self.lock:
            self.items.insert(0, entry)
            del self.items[MAX_HISTORY:]
            data = list(self.items)
        _save(self.path, data)

    def list(self):
        with self.lock:
            return json.loads(json.dumps(self.items))

    def clear(self):
        with self.lock:
            self.items = []
        _save(self.path, [])
