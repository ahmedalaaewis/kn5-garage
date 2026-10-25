"""Reading 3D files (.glb, .gltf, .obj; .fbx through Blender) into the same model structure
as a .kn5, so they can be viewed, converted, used as parts, or made into a car.

Files from the internet are untrusted: sizes and counts are capped, external files must sit
next to the model (no absolute paths, no ..), and only triangles are read."""

import base64
import io
import json
import math
import re
import struct
from pathlib import Path

import numpy as np
from PIL import Image

from . import kn5write
from .kn5 import Kn5Error
from .textures import dds_bytes

MAX_FILE = 1 << 30           # 1 GB
MAX_VERTICES = 10_000_000
COMPONENT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


class ImportError3D(Kn5Error):
    pass


def load_model(path, blender_exe=None):
    """Any supported 3D file -> a kn5-like model (see formats/__init__.py for the list)."""
    from .formats import load
    return load(path, blender_exe)


def empty_model(name):
    return {"version": 6, "extra": 0, "textures": {}, "texture_list": [], "materials": [], "trailer": b"",
            "protected": False, "root": kn5write.new_dummy(name)}


def _safe_sibling(base, uri):
    """A file referenced by a model: only inside the model's own folder."""
    rel = Path(str(uri).replace("\\", "/"))
    if rel.is_absolute() or ".." in rel.parts or ":" in str(uri):
        raise ImportError3D("the model points to a file outside its folder: %s" % uri)
    p = (base / rel).resolve()
    if base.resolve() not in p.parents or not p.is_file():
        raise ImportError3D("missing file next to the model: %s" % uri)
    return p


def _uri_bytes(base, uri):
    if uri.startswith("data:"):
        head, _, data = uri.partition(",")
        if ";base64" not in head:
            raise ImportError3D("unsupported data URI")
        return base64.b64decode(data)
    from urllib.parse import unquote
    return _safe_sibling(base, unquote(uri)).read_bytes()


def _texture(model, name, data, normal=False):
    """Store a picture as a DDS texture in the model; returns the texture name."""
    with Image.open(io.BytesIO(data)) as img:
        img.load()
        img = img.convert("RGBA")
    if normal:  # glTF normal maps are OpenGL style; AC (DirectX) has green flipped
        r, g, b, a = img.split()
        img = Image.merge("RGBA", (r, g.point(lambda v: 255 - v), b, a))
    stem = re.sub(r"[^A-Za-z0-9_\-]", "_", Path(name).stem)[:50] or "texture"
    tex = stem + ".dds"
    n = 2
    while tex in model["textures"]:
        tex = "%s_%d.dds" % (stem, n)
        n += 1
    kn5write.add_texture(model, tex, dds_bytes(img))
    return tex


def _solid_texture(model, rgba, name):
    img = Image.new("RGBA", (4, 4), tuple(int(max(0, min(1, c)) * 255 + 0.5) for c in rgba))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return _texture(model, name, buf.getvalue())


def _mesh_nodes(name, pos, nrm, uv, tri, material, skin=None):
    """kn5 mesh nodes for a triangle mesh, split so each has at most 65535 vertices.

    skin: (bones, joints, weights) for a rigged mesh: bones = [(bone node name, inverse bind
    matrix as 16 floats, column-major)], joints / weights = n x 4 per vertex."""
    pos = np.asarray(pos, np.float32)
    if nrm is None or len(nrm) != len(pos):
        nrm = vertex_normals(pos, tri)
    if uv is None or len(uv) != len(pos):
        uv = np.zeros((len(pos), 2), np.float32)
    verts = np.hstack([pos, nrm, uv, np.zeros((len(pos), 3), np.float32)]).astype(np.float32)
    tri = np.asarray(tri, np.int64).reshape(-1, 3)

    def make(nm, used, idx):
        node = kn5write.new_mesh(nm, verts[used], idx.astype(np.uint16), material)
        if skin is not None:
            bones, joints, weights = skin
            node.update(cls=3, bones=[(b, tuple(float(v) for v in m)) for b, m in bones],
                        joints=np.asarray(joints, np.float32)[used], weights=np.asarray(weights, np.float32)[used])
            node.pop("lod_raw", None)
        return node

    if len(verts) <= 65535:
        return [make(name, np.arange(len(verts)), tri.reshape(-1))]
    out = []
    per = 21000  # triangles per piece (at most 63000 vertices)
    for k, start in enumerate(range(0, len(tri), per)):
        chunk = tri[start:start + per]
        used, remap = np.unique(chunk, return_inverse=True)
        out.append(make("%s_%d" % (name, k), used, remap.reshape(-1)))
    return out


def top4(joints, weights):
    """Per-vertex bone lists of any length -> the 4 strongest (joints, weights), normalised."""
    joints, weights = np.asarray(joints, np.float64), np.asarray(weights, np.float64)
    if joints.shape[1] < 4:
        pad = 4 - joints.shape[1]
        joints = np.hstack([joints, np.zeros((len(joints), pad))])
        weights = np.hstack([weights, np.zeros((len(weights), pad))])
    order = np.argsort(-weights, axis=1)[:, :4]
    j = np.take_along_axis(joints, order, 1)
    w = np.take_along_axis(weights, order, 1)
    total = w.sum(1, keepdims=True)
    w = np.where(total > 1e-9, w / np.maximum(total, 1e-9), [1, 0, 0, 0])
    return j.astype(np.float32), w.astype(np.float32)


def simple_material(model, name, colour=(0.8, 0.8, 0.8, 1.0), image=None, image_name=None, normal=None,
                    blend=False, shininess=30.0):
    """A material with a colour or a picture (bytes in any format Pillow reads); returns its index."""
    maps = {}
    if image:
        try:
            maps["txDiffuse"] = _texture(model, image_name or name, image)
        except (OSError, ValueError, Image.DecompressionBombError):
            pass
    if "txDiffuse" not in maps:
        maps["txDiffuse"] = _solid_texture(model, list(colour)[:3] + [1.0], name or "colour")
    if normal:
        try:
            maps["txNormal"] = _texture(model, (image_name or name) + "_nm", normal, normal=True)
        except (OSError, ValueError, Image.DecompressionBombError):
            pass
    shader = "ksPerPixelNM" if "txNormal" in maps else "ksPerPixel"
    model["materials"].append(kn5write.new_material(str(name or "material")[:60], shader, maps,
                                                    {"ksSpecularEXP": float(shininess)}, blend=1 if blend else 0))
    return len(model["materials"]) - 1


def read_sibling(base, name):
    """A texture next to a model, also found by file name alone (exporters write odd paths)."""
    name = str(name).replace("\\", "/")
    try:
        return _safe_sibling(base, name).read_bytes()
    except ImportError3D:
        p = Path(base) / Path(name).name
        if Path(name).name and p.is_file():
            return p.read_bytes()
        for sub in ("textures", "Textures", "tex", "images"):
            q = Path(base) / sub / Path(name).name
            if Path(name).name and q.is_file():
                return q.read_bytes()
    return None


def vertex_normals(pos, tri):
    pos = np.asarray(pos, np.float64)
    tri = np.asarray(tri, np.int64).reshape(-1, 3)
    n = np.zeros_like(pos)
    face = np.cross(pos[tri[:, 1]] - pos[tri[:, 0]], pos[tri[:, 2]] - pos[tri[:, 0]])
    for k in range(3):
        np.add.at(n, tri[:, k], face)
    length = np.linalg.norm(n, axis=1, keepdims=True)
    n = np.where(length > 1e-12, n / np.maximum(length, 1e-12), [0, 1, 0])
    return n.astype(np.float32)


def _check_vertices(model):
    total = sum(len(n["verts"]) for n in walk(model["root"]) if n["cls"] != 1)
    if total > MAX_VERTICES:
        raise ImportError3D("the model has more than %d vertices" % MAX_VERTICES)


def walk(node):
    yield node
    for c in node["children"]:
        yield from walk(c)


# ---------------------------------------------------------------------------
# glTF / glb
# ---------------------------------------------------------------------------

def load_gltf(path):
    path = Path(path)
    raw = path.read_bytes()
    binchunk = b""
    if raw[:4] == b"glTF":
        if len(raw) < 20:
            raise ImportError3D("damaged .glb file")
        jlen, jtype = struct.unpack_from("<I4s", raw, 12)
        if jtype != b"JSON" or 20 + jlen > len(raw):
            raise ImportError3D("damaged .glb file")
        doc = json.loads(raw[20:20 + jlen])
        rest = 20 + jlen
        if rest + 8 <= len(raw):
            blen, btype = struct.unpack_from("<I4s", raw, rest)
            if btype == b"BIN\0":
                binchunk = raw[rest + 8:rest + 8 + blen]
    else:
        try:
            doc = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ImportError3D("not a glTF file")
    if not isinstance(doc, dict) or not str(doc.get("asset", {}).get("version", "")).startswith("2"):
        raise ImportError3D("only glTF 2.0 files are supported")
    base = path.parent
    buffers = []
    for b in doc.get("buffers", []):
        buffers.append(_uri_bytes(base, b["uri"]) if "uri" in b else binchunk)

    def view_bytes(vi):
        v = doc["bufferViews"][vi]
        buf = buffers[v["buffer"]]
        off = v.get("byteOffset", 0)
        if off + v["byteLength"] > len(buf):
            raise ImportError3D("damaged buffer view")
        return buf[off:off + v["byteLength"]], v.get("byteStride")

    def accessor(ai):
        a = doc["accessors"][ai]
        dt = np.dtype(COMPONENT[a["componentType"]]).newbyteorder("<")
        width = WIDTH[a["type"]]
        count = a["count"]
        if count > MAX_VERTICES * 3:
            raise ImportError3D("too much data")
        if "bufferView" not in a:
            return np.zeros((count, width), dt)
        data, stride = view_bytes(a["bufferView"])
        off = a.get("byteOffset", 0)
        item = dt.itemsize * width
        if stride and stride != item:
            if off + stride * (count - 1) + item > len(data):
                raise ImportError3D("damaged accessor")
            rows = np.frombuffer(data, np.uint8, stride * (count - 1) + item, off)
            out = np.lib.stride_tricks.as_strided(rows, (count, item), (stride, 1)).copy().view(dt)
        else:
            if off + item * count > len(data):
                raise ImportError3D("damaged accessor")
            out = np.frombuffer(data, dt, width * count, off).copy()
        out = out.reshape(count, width)
        if a.get("normalized") and dt.kind in "iu":
            out = out.astype(np.float32) / np.iinfo(dt).max
        return out

    model = empty_model(path.stem)
    image_tex = {}

    def image_texture(ii, normal=False):
        key = (ii, normal)
        if key in image_tex:
            return image_tex[key]
        im = doc["images"][ii]
        if "bufferView" in im:
            data = view_bytes(im["bufferView"])[0]
        else:
            data = _uri_bytes(base, im["uri"])
        name = im.get("name") or (Path(im["uri"]).name if "uri" in im and not im["uri"].startswith("data:") else "image%d" % ii)
        try:
            image_tex[key] = _texture(model, name, data, normal)
        except (OSError, ValueError, Image.DecompressionBombError):
            image_tex[key] = None
        return image_tex[key]

    mat_index = {}

    def material(mi):
        if mi in mat_index:
            return mat_index[mi]
        maps = {}
        if mi is None:
            m = {"name": "default"}
        else:
            m = doc["materials"][mi]
        pbr = m.get("pbrMetallicRoughness", {})
        if "baseColorTexture" in pbr:
            src = doc["textures"][pbr["baseColorTexture"]["index"]].get("source")
            tex = image_texture(src) if src is not None else None
            if tex:
                maps["txDiffuse"] = tex
        if "txDiffuse" not in maps:
            maps["txDiffuse"] = _solid_texture(model, pbr.get("baseColorFactor", [0.8, 0.8, 0.8, 1]), m.get("name", "colour"))
        if "normalTexture" in m:
            src = doc["textures"][m["normalTexture"]["index"]].get("source")
            tex = image_texture(src, normal=True) if src is not None else None
            if tex:
                maps["txNormal"] = tex
        rough = float(pbr.get("roughnessFactor", 0.5))
        spec_exp = max(1.0, 2.0 / max(rough, 0.05) ** 2 - 2.0)
        shader = "ksPerPixelNM" if "txNormal" in maps else "ksPerPixel"
        mat = kn5write.new_material(str(m.get("name") or "material%d" % len(model["materials"]))[:60], shader, maps,
                                    {"ksSpecularEXP": round(spec_exp, 1)}, blend=1 if m.get("alphaMode") == "BLEND" else 0)
        mat["alpha_tested"] = m.get("alphaMode") == "MASK"
        model["materials"].append(mat)
        mat_index[mi] = len(model["materials"]) - 1
        return mat_index[mi]

    # unique node names: bones and animations find their nodes by name
    names, seen = [], {}
    for i, n in enumerate(doc.get("nodes", [])):
        label = str(n.get("name") or "node%d" % i)[:76]
        seen[label] = seen.get(label, -1) + 1
        names.append(label if not seen[label] else "%s_%d" % (label, seen[label]))
    rigged = []  # skinned meshes go to the top: their bones move them

    def node(ni, depth=0):
        if depth > 200:
            raise ImportError3D("the model is nested too deeply")
        n = doc["nodes"][ni]
        out = kn5write.new_dummy(names[ni], node_matrix(n))
        if "mesh" in n:
            mesh = doc["meshes"][n["mesh"]]
            skin = doc["skins"][n["skin"]] if "skin" in n and n["skin"] < len(doc.get("skins", [])) else None
            for pi, prim in enumerate(mesh.get("primitives", [])):
                if prim.get("mode", 4) != 4 or "POSITION" not in prim.get("attributes", {}):
                    continue
                attrs = prim["attributes"]
                pos = accessor(attrs["POSITION"]).astype(np.float32)
                nrm = accessor(attrs["NORMAL"]).astype(np.float32) if "NORMAL" in attrs else None
                uv = accessor(attrs["TEXCOORD_0"]).astype(np.float32) if "TEXCOORD_0" in attrs else None
                tri = accessor(prim["indices"]).reshape(-1) if "indices" in prim else np.arange(len(pos))
                tri = tri[: len(tri) // 3 * 3].astype(np.int64)
                if len(tri) and (tri.max() >= len(pos) or tri.min() < 0):
                    raise ImportError3D("damaged mesh indices")
                name = "%s%s" % (mesh.get("name") or out["name"], "_%d" % pi if pi else "")
                sk = None
                if skin is not None and "JOINTS_0" in attrs and "WEIGHTS_0" in attrs:
                    joints = skin.get("joints", [])
                    ibm = accessor(skin["inverseBindMatrices"]).reshape(-1, 16) if "inverseBindMatrices" in skin                         else np.tile(np.eye(4).reshape(-1), (len(joints), 1))
                    bones = [(names[j], tuple(float(v) for v in m)) for j, m in zip(joints, ibm)]
                    sk = (bones, accessor(attrs["JOINTS_0"]).astype(np.float32),
                          accessor(attrs["WEIGHTS_0"]).astype(np.float32))
                made = _mesh_nodes(name[:80], pos, nrm, uv, tri, material(prim.get("material")), sk)
                (rigged if sk else out["children"]).extend(made)
        out["children"] += [node(c, depth + 1) for c in n.get("children", [])]
        return out

    scene = doc.get("scenes", [{}])[doc.get("scene", 0)] if doc.get("scenes") else {"nodes": list(range(len(doc.get("nodes", []))))}
    model["root"]["children"] = [node(i) for i in scene.get("nodes", [])] + rigged
    model["animations"] = _gltf_animations(doc, accessor, names)
    _check_vertices(model)
    return model


def _gltf_animations(doc, accessor, names, fps=30.0):
    """glTF animations sampled at 30 frames a second: {name: {node name: {"t", "r", "s"}}}."""
    out = {}
    for ai, anim in enumerate(doc.get("animations", [])):
        chans = {}
        length = 0.0
        for ch in anim.get("channels", []):
            target = ch.get("target", {})
            if target.get("node") is None or target.get("path") not in ("translation", "rotation", "scale"):
                continue
            smp = anim["samplers"][ch["sampler"]]
            times = accessor(smp["input"]).reshape(-1).astype(np.float64)
            vals = accessor(smp["output"]).astype(np.float64)
            if smp.get("interpolation") == "CUBICSPLINE":
                vals = vals.reshape(len(times), 3, -1)[:, 1]  # keep the values, drop the tangents
            vals = vals.reshape(len(times), -1)
            if len(times):
                chans.setdefault(target["node"], {})[target["path"]] = (times, vals)
                length = max(length, float(times.max()))
        if not chans:
            continue
        frames = np.arange(0, length + 1e-6, 1 / fps)[:6000]
        tracks = {}
        for ni, paths in chans.items():
            n = doc["nodes"][ni]
            rest = {"translation": n.get("translation", [0, 0, 0]), "rotation": n.get("rotation", [0, 0, 0, 1]),
                    "scale": n.get("scale", [1, 1, 1])}
            got = {}
            for path, default in rest.items():
                if path in paths:
                    tt, vv = paths[path]
                    if path == "rotation":  # keep neighbouring quaternions on the same side
                        for k in range(1, len(vv)):
                            if np.dot(vv[k], vv[k - 1]) < 0:
                                vv[k] = -vv[k]
                    col = np.stack([np.interp(frames, tt, vv[:, k]) for k in range(vv.shape[1])], 1)
                    if path == "rotation":
                        col /= np.maximum(np.linalg.norm(col, axis=1, keepdims=True), 1e-12)
                    got[path] = col
                else:
                    got[path] = np.tile(np.asarray(default, np.float64), (len(frames), 1))
            tracks[names[ni]] = {"t": got["translation"].astype(np.float32), "r": got["rotation"].astype(np.float32),
                                 "s": got["scale"].astype(np.float32)}
        out[str(anim.get("name") or "animation%d" % ai)[:60]] = tracks
    return out


def node_matrix(n):
    """A glTF node's local matrix as 16 floats in glTF's column-major order (= kn5's order)."""
    if "matrix" in n:
        return tuple(float(v) for v in n["matrix"])
    t = n.get("translation", [0, 0, 0])
    x, y, z, w = n.get("rotation", [0, 0, 0, 1])
    s = n.get("scale", [1, 1, 1])
    r = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]) * np.array(s)
    m = np.eye(4)
    m[:3, :3] = r
    m[:3, 3] = t
    return tuple(float(v) for v in m.T.reshape(-1))  # column-major


# ---------------------------------------------------------------------------
# OBJ (+ MTL)
# ---------------------------------------------------------------------------

def load_obj(path):
    path = Path(path)
    base = path.parent
    model = empty_model(path.stem)
    v, vt, vn = [], [], []
    groups = {}  # (object, material) -> list of (vi, ti, ni) triangles
    obj_name, mat_name = path.stem, None
    mtl = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if not parts or parts[0].startswith("#"):
            continue
        tag = parts[0]
        try:
            if tag == "v":
                v.append([float(parts[1]), float(parts[2]), float(parts[3])])
            elif tag == "vt":
                vt.append([float(parts[1]), 1.0 - float(parts[2]) if len(parts) > 2 else 0.0])
            elif tag == "vn":
                vn.append([float(parts[1]), float(parts[2]), float(parts[3])])
            elif tag in ("o", "g") and len(parts) > 1:
                obj_name = " ".join(parts[1:])[:80]
            elif tag == "usemtl" and len(parts) > 1:
                mat_name = " ".join(parts[1:])
            elif tag == "mtllib" and len(parts) > 1:
                try:
                    mtl.update(read_mtl(_safe_sibling(base, " ".join(parts[1:]))))
                except ImportError3D:
                    pass
            elif tag == "f" and len(parts) >= 4:
                corners = []
                for c in parts[1:]:
                    idx = (c.split("/") + ["", ""])[:3]
                    corners.append(tuple(_obj_index(i, n) for i, n in zip(idx, (len(v), len(vt), len(vn)))))
                tris = groups.setdefault((obj_name, mat_name), [])
                for k in range(1, len(corners) - 1):  # fan triangulation
                    tris.append((corners[0], corners[k], corners[k + 1]))
        except (ValueError, IndexError):
            raise ImportError3D("damaged .obj line: %s" % line[:60])
        if len(v) > MAX_VERTICES:
            raise ImportError3D("the model has more than %d vertices" % MAX_VERTICES)
    v, vt, vn = np.array(v, np.float32).reshape(-1, 3), np.array(vt, np.float32).reshape(-1, 2), np.array(vn, np.float32).reshape(-1, 3)
    mats = {}
    for (name, mname), tris in groups.items():
        if mname not in mats:
            info = mtl.get(mname, {})
            maps = {}
            if info.get("map_Kd"):
                try:
                    maps["txDiffuse"] = _texture(model, Path(info["map_Kd"]).name, _safe_sibling(base, info["map_Kd"]).read_bytes())
                except (ImportError3D, OSError, ValueError):
                    pass
            if "txDiffuse" not in maps:
                maps["txDiffuse"] = _solid_texture(model, info.get("Kd", [0.8, 0.8, 0.8]) + [1], mname or "colour")
            model["materials"].append(kn5write.new_material(str(mname or "material")[:60], "ksPerPixel", maps,
                                                            blend=1 if info.get("d", 1) < 1 else 0))
            mats[mname] = len(model["materials"]) - 1
        corners = np.array(tris, np.int64).reshape(-1, 3)  # (v, t, n) per corner, -1 = missing
        if (corners[:, 0] < 0).any() or (corners[:, 0] >= len(v)).any() or (corners[:, 1] >= len(vt)).any() \
                or (corners[:, 2] >= len(vn)).any():
            raise ImportError3D("damaged face indices in %s" % name)
        uniq, inv = np.unique(corners, axis=0, return_inverse=True)
        pos = v[uniq[:, 0]]
        uv = np.where(uniq[:, 1:2] >= 0, vt[np.clip(uniq[:, 1], 0, None)] if len(vt) else 0, 0).astype(np.float32) \
            if len(vt) else None
        nrm = vn[uniq[:, 2]] if len(vn) and (uniq[:, 2] >= 0).all() else None
        model["root"]["children"] += _mesh_nodes(str(name)[:80], pos, nrm, uv, inv.reshape(-1), mats[mname])
    _check_vertices(model)
    return model


def _obj_index(text, count):
    if not text:
        return -1
    i = int(text)
    return i - 1 if i > 0 else count + i


def read_mtl(path):
    out, cur = {}, None
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "newmtl" and len(parts) > 1:
            cur = out.setdefault(" ".join(parts[1:]), {})
        elif cur is not None and parts[0] == "Kd" and len(parts) >= 4:
            cur["Kd"] = [float(x) for x in parts[1:4]]
        elif cur is not None and parts[0] == "d" and len(parts) >= 2:
            cur["d"] = float(parts[1])
        elif cur is not None and parts[0] == "map_Kd" and len(parts) >= 2:
            cur["map_Kd"] = parts[-1]
    return out


# ---------------------------------------------------------------------------
# Helpers for using imported (or other cars') parts
# ---------------------------------------------------------------------------

def bounds(node, parent=np.eye(4)):
    """(min, max) of a node tree's vertices, in the coordinates of the node's parent."""
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)

    def visit(n, m):
        nonlocal lo, hi
        if n["cls"] == 1:
            m = m @ np.array(n.get("matrix", kn5write.IDENTITY), np.float64).reshape(4, 4).T
        elif len(n["verts"]):
            p = np.c_[np.asarray(n["verts"], np.float64)[:, :3], np.ones(len(n["verts"]))] @ m.T
            lo, hi = np.minimum(lo, p[:, :3].min(0)), np.maximum(hi, p[:, :3].max(0))
        for c in n["children"]:
            visit(c, m)
    visit(node, parent)
    return (lo, hi) if np.all(np.isfinite(lo)) else (np.zeros(3), np.zeros(3))


def fit_matrix(src_bounds, dst_bounds):
    """Uniform scale + move so the source box sits where the destination box is (column-major)."""
    (slo, shi), (dlo, dhi) = src_bounds, dst_bounds
    ssize, dsize = shi - slo, dhi - dlo
    scale = float(np.max(dsize) / np.max(ssize)) if np.max(ssize) > 1e-9 and np.max(dsize) > 1e-9 else 1.0
    m = np.eye(4)
    m[:3, :3] *= scale
    m[:3, 3] = (dlo + dhi) / 2 - scale * (slo + shi) / 2
    return tuple(float(v) for v in m.T.reshape(-1))


def rotation_y(degrees):
    a = math.radians(degrees)
    m = np.eye(4)
    m[0, 0], m[0, 2], m[2, 0], m[2, 2] = math.cos(a), math.sin(a), -math.sin(a), math.cos(a)
    return m
