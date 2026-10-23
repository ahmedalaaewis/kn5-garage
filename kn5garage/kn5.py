"""Reading Assetto Corsa .kn5 models and .ksanim animations.

Mod files come from anywhere, so every count and length read from a file is checked
against the bytes that are actually there, and node nesting is limited.
"""

import math
import struct
from pathlib import Path

import numpy as np


class Kn5Error(Exception):
    pass


# Author-protected files keep only tiny placeholder textures in the kn5.
PLACEHOLDER_TEXTURE_SIZE = 200
MAX_NODE_DEPTH = 256  # real cars nest a few dozen levels deep
MAX_STRING = 65536


class Reader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def read(self, n):
        if n < 0 or self.pos + n > len(self.data):
            raise Kn5Error("unexpected end of file (the file may be encrypted or damaged)")
        b = self.data[self.pos:self.pos + n]
        self.pos += n
        return b

    def u8(self):
        return self.read(1)[0]

    def i32(self):
        return struct.unpack("<i", self.read(4))[0]

    def f32(self):
        return struct.unpack("<f", self.read(4))[0]

    def floats(self, n):
        return struct.unpack("<%df" % n, self.read(4 * n))

    def string(self):
        return self.string_raw()[0]

    def string_raw(self):
        """(text, the exact bytes): names from old tools aren't always UTF-8."""
        n = self.i32()
        if n < 0 or n > MAX_STRING:
            raise Kn5Error("bad string length %d (the file may be encrypted or damaged)" % n)
        raw = self.read(n)
        return raw.decode("utf-8", errors="replace"), raw

    def count(self, item_size=1):
        """A non-negative item count that fits in the bytes left in the file."""
        n = self.i32()
        if n < 0 or n * item_size > len(self.data) - self.pos:
            raise Kn5Error("bad count %d (the file may be encrypted or damaged)" % n)
        return n

    def array(self, dtype, count):
        dtype = np.dtype(dtype)
        raw = self.read(dtype.itemsize * count)
        return np.frombuffer(raw, dtype=dtype, count=count)


def read_kn5(path):
    r = Reader(Path(path).read_bytes())
    if r.read(6) != b"sc6969":
        raise Kn5Error("not a kn5 file, or it is encrypted")
    version = r.i32()
    extra = r.i32() if version > 5 else None  # extra header value in newer files

    # Everything is kept, so kn5write.write_kn5() can put the file back together byte for byte.
    textures, texture_list = {}, []
    for _ in range(r.count(12)):
        active = r.i32()
        name, raw = r.string_raw()
        size = r.i32()
        data = r.read(size)
        textures[name] = data
        texture_list.append({"active": active, "name": name, "name_raw": raw, "data": data})

    materials = []
    for _ in range(r.count(10)):
        name, name_raw = r.string_raw()
        shader, shader_raw = r.string_raw()
        mat = {"name": name, "shader": shader, "raw": {"name": name_raw, "shader": shader_raw}}
        mat["blend"] = r.u8()
        mat["alpha_tested_raw"] = r.u8()
        mat["alpha_tested"] = bool(mat["alpha_tested_raw"])
        mat["depth_mode"] = r.i32() if version > 4 else None
        props, prop_list = {}, []
        for _ in range(r.count(40)):
            pname, praw = r.string_raw()
            a = r.f32()
            b = r.read(8)
            c = r.floats(3)
            d = r.read(16)
            props[pname] = (a, c)
            prop_list.append({"name": pname, "name_raw": praw, "a": a, "b": b, "c": c, "d": d})
        mat["props"] = props
        mat["prop_list"] = prop_list
        maps, map_list = {}, []
        for _ in range(r.count(12)):
            sampler, sraw = r.string_raw()
            slot = r.i32()
            tex, traw = r.string_raw()
            maps[sampler] = tex
            map_list.append({"sampler": sampler, "sampler_raw": sraw, "slot": slot, "texture": tex, "texture_raw": traw})
        mat["maps"] = maps
        mat["map_list"] = map_list
        materials.append(mat)

    root = read_node(r)
    trailer = r.data[r.pos:]
    # Protected files carry an encrypted block after the model and only
    # placeholder textures; their real data can't be read.
    protected = b"acd.checksum" in trailer[:256] or bool(
        textures and all(len(t) < PLACEHOLDER_TEXTURE_SIZE for t in textures.values()))
    return {"version": version, "extra": extra, "textures": textures, "texture_list": texture_list,
            "materials": materials, "root": root, "trailer": trailer, "protected": protected}


def read_node(r, depth=0):
    if depth > MAX_NODE_DEPTH:
        raise Kn5Error("model nesting is too deep (the file may be damaged)")
    cls = r.i32()
    name, name_raw = r.string_raw()
    node = {"cls": cls, "name": name, "name_raw": name_raw, "children": []}
    child_count = r.count(13)
    node["active_raw"] = r.u8()
    node["active"] = bool(node["active_raw"])

    if cls == 1:  # dummy / transform
        node["matrix"] = r.floats(16)
    elif cls == 2:  # static mesh
        node["flags_raw"] = r.read(3)
        node["shadows"], node["visible"], node["transparent"] = (bool(b) for b in node["flags_raw"])
        verts = r.array("<f4", r.count(44) * 11).reshape(-1, 11)
        node["indices"] = r.array("<u2", r.count(2))
        node["material"] = r.i32()
        node["layer"] = r.i32()
        node["lod_raw"] = r.read(8)  # lod in / out
        node["bsphere_raw"] = r.read(16)  # bounding sphere centre + radius
        node["renderable"] = r.u8()
        node["verts"] = verts
    elif cls == 3:  # skinned mesh: bones (name, inverse bind matrix) + per-vertex weights
        node["flags_raw"] = r.read(3)
        node["shadows"], node["visible"], node["transparent"] = (bool(b) for b in node["flags_raw"])
        node["bones"] = []
        node["bones_raw"] = []
        for _ in range(r.count(68)):
            bname, braw = r.string_raw()
            m = r.floats(16)
            node["bones"].append((bname, m))
            node["bones_raw"].append(braw)
        verts = r.array("<f4", r.count(76) * 19).reshape(-1, 19)
        node["indices"] = r.array("<u2", r.count(2))
        node["material"] = r.i32()
        node["layer"] = r.i32()
        node["tail_raw"] = r.read(8)
        node["verts_full"] = verts
        node["verts"] = verts[:, :11]
        node["weights"] = verts[:, 11:15]  # pos 3, normal 3, uv 2, tangent 3, weights 4, bone indices 4
        node["joints"] = verts[:, 15:19]
    else:
        raise Kn5Error("unknown node type %d (the file may be encrypted or damaged)" % cls)

    for _ in range(child_count):
        node["children"].append(read_node(r, depth + 1))
    return node


def probe_kn5(path):
    """Quick look at a kn5 (header and texture table only), for listing many cars."""
    info = {"readable": False, "protected": False, "version": None,
            "textures": 0, "texture_bytes": 0, "reason": None}
    try:
        size_on_disk = Path(path).stat().st_size
        with open(path, "rb") as f:
            if f.read(6) != b"sc6969":
                info["reason"] = "encrypted or not a kn5 file"
                return info
            version = struct.unpack("<i", f.read(4))[0]
            if version > 5:
                f.read(4)
            count = struct.unpack("<i", f.read(4))[0]
            if not 0 <= count < 100000:
                raise Kn5Error("bad texture count")
            sizes = []
            for _ in range(count):
                f.read(4)
                name_len = struct.unpack("<i", f.read(4))[0]
                if not 0 <= name_len < MAX_STRING:
                    raise Kn5Error("bad texture name")
                f.seek(name_len, 1)
                size = struct.unpack("<i", f.read(4))[0]
                if size < 0 or f.tell() + size > size_on_disk:
                    raise Kn5Error("bad texture size")
                sizes.append(size)
                f.seek(size, 1)
    except (OSError, struct.error, Kn5Error) as e:
        info["reason"] = "damaged or encrypted (%s)" % e
        return info
    info.update(readable=True, version=version, textures=count, texture_bytes=sum(sizes),
                protected=bool(sizes) and max(sizes) < PLACEHOLDER_TEXTURE_SIZE)
    return info


# ---------------------------------------------------------------------------
# Node transforms
# ---------------------------------------------------------------------------

def node_matrix(m):
    """kn5 node matrices are row-major with row vectors (translation in the last row).
    Read in order, that is exactly glTF's column-major layout. kn5 cars already use
    glTF's axes (+Y up, +Z front, +X the car's left: WHEEL_LF and mirrors_L sit at +X),
    so nothing is mirrored."""
    return np.array(m, dtype=np.float64).reshape(-1)


def quat_from_matrix(r):
    """Unit quaternion (x, y, z, w) of a 3x3 rotation matrix (column vectors)."""
    t = np.trace(r)
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        q = [(r[2, 1] - r[1, 2]) / s, (r[0, 2] - r[2, 0]) / s, (r[1, 0] - r[0, 1]) / s, 0.25 * s]
    elif r[0, 0] > r[1, 1] and r[0, 0] > r[2, 2]:
        s = math.sqrt(1.0 + r[0, 0] - r[1, 1] - r[2, 2]) * 2
        q = [0.25 * s, (r[0, 1] + r[1, 0]) / s, (r[0, 2] + r[2, 0]) / s, (r[2, 1] - r[1, 2]) / s]
    elif r[1, 1] > r[2, 2]:
        s = math.sqrt(1.0 + r[1, 1] - r[0, 0] - r[2, 2]) * 2
        q = [(r[0, 1] + r[1, 0]) / s, 0.25 * s, (r[1, 2] + r[2, 1]) / s, (r[0, 2] - r[2, 0]) / s]
    else:
        s = math.sqrt(1.0 + r[2, 2] - r[0, 0] - r[1, 1]) * 2
        q = [(r[0, 2] + r[2, 0]) / s, (r[1, 2] + r[2, 1]) / s, 0.25 * s, (r[1, 0] - r[0, 1]) / s]
    q = np.array(q)
    return q / np.linalg.norm(q)


def decompose(m16):
    """kn5 row-vector matrix -> (translation, rotation xyzw, scale), or None if it has shear."""
    a = np.array(m16, dtype=np.float64).reshape(4, 4).T  # column-vector form
    if not np.all(np.isfinite(a)):
        return None
    lin = a[:3, :3]
    scale = np.linalg.norm(lin, axis=0)
    if np.any(scale < 1e-9):
        return None
    if np.linalg.det(lin) < 0:
        scale[0] = -scale[0]
    rot = lin / scale
    if not np.allclose(rot.T @ rot, np.eye(3), atol=1e-4):
        return None
    return a[:3, 3], quat_from_matrix(rot), scale


def read_ksanim(path):
    """AC animation file -> {node name: {"t": (n,3), "r": (n,4) xyzw, "s": (n,3)}}."""
    r = Reader(Path(path).read_bytes())
    version = r.i32()
    if version not in (1, 2):
        raise Kn5Error("unsupported ksanim version %d" % version)
    tracks = {}
    for _ in range(r.count(8)):
        name = r.string()
        frames = r.i32()
        if not 0 <= frames < 100000:
            raise Kn5Error("bad frame count")
        if version == 2:
            data = r.array("<f4", frames * 10).reshape(frames, 10)
            tracks[name] = {"r": data[:, 0:4], "t": data[:, 4:7], "s": data[:, 7:10]}
        else:
            t, q, s = [], [], []
            for m in r.array("<f4", frames * 16).reshape(frames, 16):
                d = decompose(m)
                if d is None:
                    d = (np.array(m[12:15]), np.array([0, 0, 0, 1.0]), np.ones(3))
                t.append(d[0]); q.append(d[1]); s.append(d[2])
            tracks[name] = {"t": np.array(t).reshape(-1, 3), "r": np.array(q).reshape(-1, 4),
                            "s": np.array(s).reshape(-1, 3)}
    return tracks
