"""Writing .kn5 models: the reverse of kn5.read_kn5().

A model read with read_kn5() and written with write_kn5() comes back byte for byte
(tests/test_kn5write.py checks this), so edits change only what they mean to change.
Values the editor sets (names, flags, matrices, vertices, materials, textures) are written
from the parsed data; everything the editor never touches is written from the original bytes.
"""

import struct

import numpy as np

from .kn5 import Kn5Error

IDENTITY = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)


class Writer:
    def __init__(self):
        self.parts = []

    def raw(self, b):
        self.parts.append(bytes(b))

    def i32(self, v):
        self.raw(struct.pack("<i", int(v)))

    def u8(self, v):
        self.raw(struct.pack("<B", int(v) & 0xFF))

    def f32(self, *vals):
        self.raw(struct.pack("<%df" % len(vals), *[float(v) for v in vals]))

    def string(self, text, raw=None):
        """The original bytes when the text hasn't changed (keeps non-UTF-8 names intact)."""
        if raw is None or raw.decode("utf-8", errors="replace") != text:
            raw = str(text).encode("utf-8")
        self.i32(len(raw))
        self.raw(raw)

    def array(self, arr, dtype):
        self.raw(np.ascontiguousarray(arr, dtype=dtype).tobytes())

    def bytes(self):
        return b"".join(self.parts)


def write_kn5(model):
    w = Writer()
    w.raw(b"sc6969")
    version = model.get("version", 6)
    w.i32(version)
    if version > 5:
        w.i32(model.get("extra") or 0)

    textures = model.get("texture_list")
    if textures is None:  # a model built by hand: name -> bytes
        textures = [{"active": 1, "name": n, "data": d} for n, d in model.get("textures", {}).items()]
    w.i32(len(textures))
    for t in textures:
        w.i32(t.get("active", 1))
        w.string(t["name"], t.get("name_raw"))
        w.i32(len(t["data"]))
        w.raw(t["data"])

    w.i32(len(model["materials"]))
    for m in model["materials"]:
        write_material(w, m, version)
    write_node(w, model["root"])
    w.raw(model.get("trailer", b""))
    return w.bytes()


def write_material(w, m, version):
    raw = m.get("raw", {})
    w.string(m["name"], raw.get("name"))
    w.string(m.get("shader", "ksPerPixel"), raw.get("shader"))
    w.u8(m.get("blend", 0))
    w.u8(m.get("alpha_tested_raw", int(bool(m.get("alpha_tested")))))
    if version > 4:
        w.i32(m.get("depth_mode") or 0)
    props = m.get("prop_list")
    if props is None:
        props = [{"name": k, "a": a, "c": c} for k, (a, c) in m.get("props", {}).items()]
    w.i32(len(props))
    for p in props:
        w.string(p["name"], p.get("name_raw"))
        w.f32(p.get("a", 0.0))
        w.raw(p.get("b", b"\0" * 8))
        w.f32(*(list(p.get("c", (0.0, 0.0, 0.0))) + [0.0, 0.0, 0.0])[:3])
        w.raw(p.get("d", b"\0" * 16))
    maps = m.get("map_list")
    if maps is None:
        maps = [{"sampler": s, "slot": i, "texture": t} for i, (s, t) in enumerate(m.get("maps", {}).items())]
    w.i32(len(maps))
    for mp in maps:
        w.string(mp["sampler"], mp.get("sampler_raw"))
        w.i32(mp.get("slot", 0))
        w.string(mp["texture"], mp.get("texture_raw"))


def _flags(node):
    raw = node.get("flags_raw")
    flags = [bool(node.get("shadows", True)), bool(node.get("visible", True)), bool(node.get("transparent", False))]
    if raw is not None and [bool(b) for b in raw] == flags:
        return raw  # unchanged: keep the exact bytes
    return bytes(int(f) for f in flags)


def _active(node):
    raw = node.get("active_raw")
    return raw if raw is not None and bool(raw) == bool(node.get("active", True)) else int(bool(node.get("active", True)))


def write_node(w, n):
    cls = n["cls"]
    w.i32(cls)
    w.string(n["name"], n.get("name_raw"))
    w.i32(len(n["children"]))
    w.u8(_active(n))
    if cls == 1:
        w.f32(*n.get("matrix", IDENTITY))
    elif cls == 2:
        w.raw(_flags(n))
        verts = np.asarray(n["verts"], np.float32).reshape(-1, 11)
        idx = np.asarray(n["indices"])
        if len(verts) > 65536 or (len(idx) and int(idx.max()) > 65535):
            raise Kn5Error("part %s has more than 65535 vertices; split it into smaller parts" % n["name"])
        w.i32(len(verts))
        w.array(verts, "<f4")
        w.i32(len(idx))
        w.array(idx, "<u2")
        w.i32(n.get("material", 0))
        w.i32(n.get("layer", 0))
        w.raw(n.get("lod_raw", struct.pack("<2f", 0.0, 0.0)))
        w.raw(n.get("bsphere_raw") or bounding_sphere(verts))
        w.u8(n.get("renderable", 1))
    elif cls == 3:
        w.raw(_flags(n))
        bones = n.get("bones", [])
        raws = n.get("bones_raw", [None] * len(bones))
        w.i32(len(bones))
        for (name, m), raw in zip(bones, raws):
            w.string(name, raw)
            w.f32(*m)
        full = n.get("verts_full")
        if full is None or len(full) != len(n["verts"]) or not np.array_equal(full[:, :11], n["verts"]):
            full = np.hstack([np.asarray(n["verts"], np.float32), np.asarray(n["weights"], np.float32),
                              np.asarray(n["joints"], np.float32)])
        w.i32(len(full))
        w.array(full, "<f4")
        w.i32(len(n["indices"]))
        w.array(n["indices"], "<u2")
        w.i32(n.get("material", 0))
        w.i32(n.get("layer", 0))
        w.raw(n.get("tail_raw", b"\0" * 8))
    else:
        raise Kn5Error("can't write node type %d" % cls)
    for c in n["children"]:
        write_node(w, c)


def bounding_sphere(verts):
    """Centre and radius around the vertex positions (for new or edited meshes)."""
    pos = np.asarray(verts, np.float64)[:, :3]
    if not len(pos):
        return struct.pack("<4f", 0, 0, 0, 0)
    centre = (pos.min(0) + pos.max(0)) / 2
    radius = float(np.linalg.norm(pos - centre, axis=1).max())
    return struct.pack("<4f", *centre, radius)


# ---------------------------------------------------------------------------
# Building blocks for edits: new parts, materials and textures
# ---------------------------------------------------------------------------

def new_dummy(name, matrix=IDENTITY, children=None):
    return {"cls": 1, "name": name, "children": list(children or []), "active": True, "matrix": tuple(matrix)}


def new_mesh(name, verts, indices, material):
    """verts: n x 11 (position, normal, uv, tangent); indices: triangle list."""
    verts = np.asarray(verts, np.float32).reshape(-1, 11)
    return {"cls": 2, "name": name, "children": [], "active": True, "shadows": True, "visible": True,
            "transparent": False, "verts": verts, "indices": np.asarray(indices), "material": material,
            "layer": 0, "lod_raw": struct.pack("<2f", 0.0, 0.0), "bsphere_raw": None, "renderable": 1}


def new_material(name, shader="ksPerPixel", maps=None, props=None, blend=0):
    """maps: {sampler: texture name}; props: {name: value or (value, (r, g, b))}."""
    plist = []
    defaults = {"ksAmbient": 0.6, "ksDiffuse": 0.6, "ksSpecular": 0.4, "ksSpecularEXP": 30.0, "ksEmissive": (0.0, (0, 0, 0)),
                "ksAlphaRef": 0.0}
    for k, v in {**defaults, **(props or {})}.items():
        a, c = (v if isinstance(v, tuple) else (v, (0.0, 0.0, 0.0)))
        plist.append({"name": k, "a": a, "c": tuple(c)})
    return {"name": name, "shader": shader, "blend": blend, "alpha_tested": False, "depth_mode": 0,
            "prop_list": plist, "props": {p["name"]: (p["a"], p["c"]) for p in plist},
            "map_list": [{"sampler": s, "slot": i, "texture": t} for i, (s, t) in enumerate((maps or {}).items())],
            "maps": dict(maps or {})}


def add_texture(model, name, data):
    """Add (or replace) a texture by name; keeps the textures dict and list in step."""
    lst = model.setdefault("texture_list", [{"active": 1, "name": n, "data": d} for n, d in model["textures"].items()])
    for t in lst:
        if t["name"] == name:
            t["data"] = data
            break
    else:
        lst.append({"active": 1, "name": name, "data": data})
    model["textures"][name] = data
