"""STL (ASCII and binary), PLY (ASCII and binary) and 3DS models."""

import re
import struct
from pathlib import Path

import numpy as np

from ..importer import ImportError3D, MAX_VERTICES, _mesh_nodes, empty_model, read_sibling, simple_material

# ---------------------------------------------------------------------------
# STL
# ---------------------------------------------------------------------------


def load_stl(path):
    path = Path(path)
    data = path.read_bytes()
    tris = None
    if len(data) >= 84:
        n = struct.unpack_from("<I", data, 80)[0]
        if 84 + 50 * n == len(data):
            rec = np.frombuffer(data, np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]), n, 84)
            tris = rec["v"].astype(np.float32)
    if tris is None:
        text = data.decode("latin-1")
        if not text.lstrip().lower().startswith("solid"):
            raise ImportError3D("not an STL file")
        nums = re.findall(r"vertex\s+(\S+)\s+(\S+)\s+(\S+)", text)
        tris = np.array(nums, np.float32).reshape(-1, 3, 3)
    if len(tris) * 3 > MAX_VERTICES:
        raise ImportError3D("the model has more than %d vertices" % MAX_VERTICES)
    # STL has no shared corners: join equal ones so normals are smooth where the surface is
    pos, inv = np.unique(tris.reshape(-1, 3), axis=0, return_inverse=True)
    model = empty_model(path.stem)
    mat = simple_material(model, "stl", (0.75, 0.77, 0.8, 1))
    model["root"]["children"] = _mesh_nodes(path.stem[:80] or "mesh", pos, None, None, inv.reshape(-1), mat)
    model["units_hint"] = "mm"  # STL has no units; most files made for printing are in millimetres
    return model


# ---------------------------------------------------------------------------
# PLY
# ---------------------------------------------------------------------------

PLY_TYPES = {"char": "i1", "int8": "i1", "uchar": "u1", "uint8": "u1", "short": "i2", "int16": "i2",
             "ushort": "u2", "uint16": "u2", "int": "i4", "int32": "i4", "uint": "u4", "uint32": "u4",
             "float": "f4", "float32": "f4", "double": "f8", "float64": "f8"}


def load_ply(path):
    path = Path(path)
    data = path.read_bytes()
    end = data.find(b"end_header")
    if not data.startswith(b"ply") or end < 0:
        raise ImportError3D("not a PLY file")
    header = data[:end].decode("latin-1").splitlines()
    body = data[data.index(b"\n", end) + 1:]
    fmt = None
    elements = []  # [name, count, [(prop, type) or (prop, ("list", count_type, item_type))]]
    for line in header:
        p = line.split()
        if not p:
            continue
        if p[0] == "format":
            fmt = p[1]
        elif p[0] == "element":
            elements.append([p[1], int(p[2]), []])
        elif p[0] == "property" and elements:
            if p[1] == "list":
                elements[-1][2].append((p[4], ("list", PLY_TYPES[p[2]], PLY_TYPES[p[3]])))
            else:
                elements[-1][2].append((p[2], PLY_TYPES[p[1]]))
    if fmt not in ("ascii", "binary_little_endian", "binary_big_endian"):
        raise ImportError3D("unknown PLY format")
    if sum(e[1] for e in elements if e[0] == "vertex") > MAX_VERTICES:
        raise ImportError3D("the model has more than %d vertices" % MAX_VERTICES)
    values = {}
    if fmt == "ascii":
        tokens = body.decode("latin-1").split()
        at = 0
        for name, count, props in elements:
            rows = []
            for _ in range(count):
                row = {}
                for prop, t in props:
                    if isinstance(t, tuple):
                        k = int(tokens[at])
                        row[prop] = [float(x) for x in tokens[at + 1:at + 1 + k]]
                        at += 1 + k
                    else:
                        row[prop] = float(tokens[at])
                        at += 1
                rows.append(row)
            values[name] = rows
        verts = values.get("vertex", [])
        get = lambda k: np.array([r[k] for r in verts], np.float64) if verts and k in verts[0] else None  # noqa: E731
        faces = [r.get("vertex_indices") or r.get("vertex_index") or [] for r in values.get("face", [])]
    else:
        bo = "<" if fmt == "binary_little_endian" else ">"
        at = 0
        faces, columns = [], {}
        for name, count, props in elements:
            if all(not isinstance(t, tuple) for _, t in props):
                dt = np.dtype([(prop, bo + t) for prop, t in props])
                arr = np.frombuffer(body, dt, count, at)
                at += dt.itemsize * count
                if name == "vertex":
                    columns = {k: arr[k].astype(np.float64) for k in arr.dtype.names}
            else:  # rows with lists: read one by one (faces)
                for _ in range(count):
                    for prop, t in props:
                        if isinstance(t, tuple):
                            cdt, idt = np.dtype(bo + t[1]), np.dtype(bo + t[2])
                            k = int(np.frombuffer(body, cdt, 1, at)[0])
                            at += cdt.itemsize
                            items = np.frombuffer(body, idt, k, at)
                            at += idt.itemsize * k
                            if name == "face" and prop in ("vertex_indices", "vertex_index"):
                                faces.append(items.tolist())
                        else:
                            at += np.dtype(bo + t).itemsize
        get = columns.get
    x, y, z = get("x"), get("y"), get("z")
    if x is None:
        raise ImportError3D("the PLY file has no vertices")
    pos = np.stack([x, y, z], 1).astype(np.float32)
    nrm = np.stack([get("nx"), get("ny"), get("nz")], 1).astype(np.float32) if get("nx") is not None else None
    u = get("u") if get("u") is not None else get("s")
    v = get("v") if get("v") is not None else get("t")
    uv = np.stack([u, 1.0 - v], 1).astype(np.float32) if u is not None and v is not None else None
    tri = [(f[0], f[k], f[k + 1]) for f in faces for k in range(1, len(f) - 1)]
    model = empty_model(path.stem)
    image = None
    for line in header:  # "comment TextureFile name.png" (MeshLab)
        m = re.match(r"comment\s+TextureFile\s+(.+)", line.strip(), re.I)
        if m:
            image = read_sibling(path.parent, m.group(1).strip())
    colour = (0.8, 0.8, 0.8, 1)
    r = get("red")
    if r is not None and image is None:  # per-vertex colours: shown as the average colour
        colour = (float(np.mean(r)) / 255, float(np.mean(get("green"))) / 255, float(np.mean(get("blue"))) / 255, 1)
    mat = simple_material(model, path.stem or "ply", colour, image=image)
    if not tri:
        raise ImportError3D("the PLY file has no faces (point clouds can't be shown as a model)")
    tri = np.array(tri, np.int64)
    if tri.max() >= len(pos) or tri.min() < 0:
        raise ImportError3D("damaged face indices")
    model["root"]["children"] = _mesh_nodes(path.stem[:80] or "mesh", pos, nrm, uv, tri.reshape(-1), mat)
    return model


# ---------------------------------------------------------------------------
# 3DS (3D Studio)
# ---------------------------------------------------------------------------

def _chunks(data, start, end):
    at = start
    while at + 6 <= end:
        cid, length = struct.unpack_from("<HI", data, at)
        if length < 6 or at + length > end:
            break
        yield cid, at + 6, at + length
        at += length


def _cstr(data, at):
    stop = data.index(b"\0", at)
    return data[at:stop].decode("latin-1"), stop + 1


def _colour(data, start, end):
    for cid, s, _ in _chunks(data, start, end):
        if cid == 0x0010:
            return struct.unpack_from("<3f", data, s)
        if cid in (0x0011, 0x0012):
            return tuple(c / 255 for c in data[s:s + 3])
    return (0.8, 0.8, 0.8)


def load_3ds(path):
    path = Path(path)
    data = path.read_bytes()
    if len(data) < 6 or struct.unpack_from("<H", data, 0)[0] != 0x4D4D:
        raise ImportError3D("not a 3DS file")
    model = empty_model(path.stem)
    materials, objects = {}, []

    def walk(start, end, depth=0):
        if depth > 12:
            return
        for cid, s, e in _chunks(data, start, end):
            if cid in (0x4D4D, 0x3D3D):
                walk(s, e, depth + 1)
            elif cid == 0xAFFF:  # material
                name, colour, tex = "material", (0.8, 0.8, 0.8), None
                for c2, s2, e2 in _chunks(data, s, e):
                    if c2 == 0xA000:
                        name = _cstr(data, s2)[0]
                    elif c2 == 0xA020:
                        colour = _colour(data, s2, e2)
                    elif c2 == 0xA200:
                        for c3, s3, _ in _chunks(data, s2, e2):
                            if c3 == 0xA300:
                                tex = _cstr(data, s3)[0]
                materials[name] = (colour, tex)
            elif cid == 0x4000:  # object
                name, at = _cstr(data, s)
                for c2, s2, e2 in _chunks(data, at, e):
                    if c2 == 0x4100:
                        objects.append((name, _trimesh(data, s2, e2)))

    walk(0, len(data))
    mat_index = {}

    def material(name):
        if name not in mat_index:
            colour, tex = materials.get(name, ((0.8, 0.8, 0.8), None))
            image = read_sibling(path.parent, tex) if tex else None
            mat_index[name] = simple_material(model, name or "material", tuple(colour) + (1,), image=image, image_name=tex)
        return mat_index[name]

    total = 0
    for name, mesh in objects:
        pos, faces, uv, face_mats = mesh
        if pos is None or faces is None or not len(faces):
            continue
        total += len(pos)
        if total > MAX_VERTICES:
            raise ImportError3D("the model has more than %d vertices" % MAX_VERTICES)
        if faces.max() >= len(pos):
            raise ImportError3D("damaged face indices in %s" % name)
        pos = pos[:, [0, 2, 1]] * [1, 1, -1]  # 3DS is Z up; AC / glTF are Y up
        tri = faces[:, ::-1]                   # the axis swap mirrors: turn the triangles round
        groups = face_mats or [(None, np.arange(len(faces)))]
        covered = np.zeros(len(faces), bool)
        for mname, idx in groups:
            covered[idx] = True
        if not covered.all():
            groups = list(groups) + [(None, np.flatnonzero(~covered))]
        for k, (mname, idx) in enumerate(groups):
            if not len(idx):
                continue
            part = tri[idx]
            used, remap = np.unique(part, return_inverse=True)
            model["root"]["children"] += _mesh_nodes(
                ("%s_%d" % (name, k) if len(groups) > 1 else name)[:80] or "mesh", pos[used],
                None, uv[used] if uv is not None else None, remap.reshape(-1), material(mname))
    if not model["root"]["children"]:
        raise ImportError3D("no meshes found in the 3DS file")
    return model


def _trimesh(data, start, end):
    pos = faces = uv = None
    face_mats = []
    for cid, s, e in _chunks(data, start, end):
        if cid == 0x4110:
            n = struct.unpack_from("<H", data, s)[0]
            pos = np.frombuffer(data, "<f4", n * 3, s + 2).reshape(n, 3).astype(np.float32)
        elif cid == 0x4140:
            n = struct.unpack_from("<H", data, s)[0]
            uv = np.frombuffer(data, "<f4", n * 2, s + 2).reshape(n, 2).astype(np.float32).copy()
            uv[:, 1] = 1 - uv[:, 1]
        elif cid == 0x4120:
            n = struct.unpack_from("<H", data, s)[0]
            faces = np.frombuffer(data, "<u2", n * 4, s + 2).reshape(n, 4)[:, :3].astype(np.int64)
            for c2, s2, _ in _chunks(data, s + 2 + n * 8, e):
                if c2 == 0x4130:
                    name, at = _cstr(data, s2)
                    k = struct.unpack_from("<H", data, at)[0]
                    face_mats.append((name, np.frombuffer(data, "<u2", k, at + 2).astype(np.int64)))
    return pos, faces, uv, face_mats

