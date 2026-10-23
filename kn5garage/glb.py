"""A minimal binary glTF 2.0 (.glb) writer."""

import json
import struct

import numpy as np


class GlbBuilder:
    def __init__(self, generator="KN5 Garage"):
        self.bin = bytearray()
        self.gltf = {
            "asset": {"version": "2.0", "generator": generator},
            "scene": 0,
            "scenes": [{"nodes": []}],
            "nodes": [], "meshes": [], "materials": [],
            "accessors": [], "bufferViews": [], "buffers": [],
            "images": [], "textures": [],
            "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}],
        }

    def view(self, data, target=None):
        while len(self.bin) % 4:
            self.bin.append(0)
        v = {"buffer": 0, "byteOffset": len(self.bin), "byteLength": len(data)}
        if target:
            v["target"] = target
        self.bin += data
        self.gltf["bufferViews"].append(v)
        return len(self.gltf["bufferViews"]) - 1

    def accessor(self, arr, kind, target, minmax=False):
        ctype = {np.dtype("float32"): 5126, np.dtype("uint16"): 5123, np.dtype("uint32"): 5125}[arr.dtype]
        acc = {
            "bufferView": self.view(np.ascontiguousarray(arr).tobytes(), target),
            "componentType": ctype,
            "count": len(arr),
            "type": kind,
        }
        if minmax:
            acc["min"] = arr.min(axis=0).tolist()
            acc["max"] = arr.max(axis=0).tolist()
        self.gltf["accessors"].append(acc)
        return len(self.gltf["accessors"]) - 1

    def image(self, name, data, mime):
        self.gltf["images"].append({"name": name, "bufferView": self.view(data), "mimeType": mime})
        self.gltf["textures"].append({"sampler": 0, "source": len(self.gltf["images"]) - 1})
        return len(self.gltf["textures"]) - 1

    def use_extension(self, name):
        used = self.gltf.setdefault("extensionsUsed", [])
        if name not in used:
            used.append(name)

    def write(self, path):
        g = self.gltf
        for key in ("images", "textures", "materials", "meshes", "accessors", "bufferViews"):
            if not g[key]:
                del g[key]
        if "textures" not in g:
            del g["samplers"]
        while len(self.bin) % 4:
            self.bin.append(0)
        if self.bin:
            g["buffers"] = [{"byteLength": len(self.bin)}]
        else:
            del g["buffers"]
        js = json.dumps(g, separators=(",", ":")).encode("utf-8")
        js += b" " * (-len(js) % 4)
        total = 12 + 8 + len(js) + (8 + len(self.bin) if self.bin else 0)
        with open(path, "wb") as f:
            f.write(struct.pack("<4sII", b"glTF", 2, total))
            f.write(struct.pack("<I4s", len(js), b"JSON"))
            f.write(js)
            if self.bin:
                f.write(struct.pack("<I4s", len(self.bin), b"BIN\0"))
                f.write(self.bin)
