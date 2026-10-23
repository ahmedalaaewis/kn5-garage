"""The app's saved settings (settings.json): car folders, output folder, Blender path."""

import json
import threading
from pathlib import Path


class Settings:
    def __init__(self, path, default_out):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.data = {"roots": [], "out": str(default_out), "blender": None}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            loaded = {}
        if isinstance(loaded, dict):
            old = loaded.pop("root", None)  # settings from before several folders were supported
            roots = loaded.get("roots")
            if isinstance(roots, list):
                self.data["roots"] = [r for r in roots if isinstance(r, str) and r.strip()]
            if isinstance(old, str) and old and old not in self.data["roots"]:
                self.data["roots"].insert(0, old)
            for key in ("out", "blender", "language"):
                if isinstance(loaded.get(key), str) and loaded[key].strip():
                    self.data[key] = loaded[key]
            progs = loaded.get("editors")
            if isinstance(progs, list):
                self.data["editors"] = [e for e in progs if isinstance(e, dict) and isinstance(e.get("path"), str)][:30]
            presets = loaded.get("savedPresets")
            if isinstance(presets, list):
                self.data["savedPresets"] = [p for p in presets if isinstance(p, dict) and isinstance(p.get("name"), str)
                                             and isinstance(p.get("options"), dict)]

    def get(self, key):
        with self.lock:
            value = self.data.get(key)
            return list(value) if isinstance(value, list) else value

    def update(self, **values):
        with self.lock:
            self.data.update({k: v for k, v in values.items() if v is not None})
            tmp = self.path.with_name(self.path.name + ".tmp")
            try:
                tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
                tmp.replace(self.path)
            except OSError:
                pass
