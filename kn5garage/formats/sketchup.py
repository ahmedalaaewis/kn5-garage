"""SketchUp models (.skp, and .skb backups) from SketchUp 2021 and newer.

Those files are a short header followed by a ZIP: materials/*/material.xml (names and colours)
and model.dat, a tree of tagged records (2-byte tag, 4-byte length, data). What is read:

    5500 component definition: 5502 name, 5000 its entities
    5000 entities: 5001 vertices, 5002 edges, 5003 faces, 5004 groups, 5005 components
    2500 vertex: 1500/1502 id, 2501 position (3 doubles, inches)
    3000 edge: 3001 / 3002 vertex ids
    3500 face: 2000/2001 material id, 3503 back material id, 3501 plane, 3502 loops
             (4500 loop -> 4501 -> 4000 edge use -> 4001 edge id)
    6500 group / component instance: 2001 material, 6502 transform (3 axes, origin, w),
             6503 definition id
    01f6 the model's own entities, 01f7 materials (13000: 1502 id, 13004 name)

Faces become triangles in the colour of their material (inherited from the group or component
when they have none). Textures whose pictures aren't inside the file show in their average
colour. Files from SketchUp 2020 and older use a different, older layout and aren't read."""

import io
import re
import struct
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

from .. import kn5write
from ..importer import ImportError3D, MAX_VERTICES, _mesh_nodes, empty_model, simple_material

INCH = 0.0254
MAX_MODEL_DAT = 512 << 20
MAX_DEPTH = 64
Z_UP = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]], np.float64)
DEFAULT_FRONT = (0.92, 0.92, 0.9, 1.0)


def flat(m):
    return tuple(float(v) for v in np.asarray(m).T.reshape(-1))


class Records:
    """The tagged records of model.dat."""

    def __init__(self, data):
        self.d = data

    def kids(self, rec):
        s, e = rec[1], rec[2]
        out, at = [], s
        while at < e:
            if at + 6 > e:
                return []
            t, ln = struct.unpack_from("<HI", self.d, at)
            if at + 6 + ln > e:
                return []
            out.append((t, at + 6, at + 6 + ln))
            at += 6 + ln
        return out

    def child(self, rec, tag):
        if rec is None:
            return None
        return next((k for k in self.kids(rec) if k[0] == tag), None)

    def path(self, rec, *tags):
        for t in tags:
            rec = self.child(rec, t)
        return rec

    def raw(self, rec):
        return self.d[rec[1]:rec[2]] if rec else b""

    def num(self, rec):
        b = self.raw(rec)
        return int.from_bytes(b[:8], "little") if b else None

    def entity_id(self, rec):
        return self.num(self.path(rec, 1500, 1502))


def version_of(data):
    head = data[:200].decode("utf-16-le", "ignore")
    m = re.search(r"\{(\d+)\.(\d+)\.?(\d*)\}", head)
    return tuple(int(x) for x in m.groups() if x) if m else None


def open_skp(path):
    data = Path(path).read_bytes()
    if not data[:40].decode("utf-16-le", "ignore").count("SketchUp Model"):
        raise ImportError3D("not a SketchUp file")
    version = version_of(data)
    at = data.find(b"PK\x03\x04", 0, 4096)
    if at < 0:
        made = "SketchUp %d" % (2000 + version[0]) if version and version[0] < 100 else "an older SketchUp"
        raise ImportError3D("this file was saved by %s; only files from SketchUp 2021 and newer can be read. "
                            "Open it in a newer SketchUp and save it again, or export it as .dae" % made)
    try:
        z = zipfile.ZipFile(io.BytesIO(data[at:]))
        info = z.getinfo("model.dat")
    except (zipfile.BadZipFile, KeyError):
        raise ImportError3D("damaged SketchUp file")
    if info.file_size > MAX_MODEL_DAT:
        raise ImportError3D("the SketchUp model is too large")
    return z, version


def read_materials(z, rec, top):
    """{material id: (name, rgba)} from the material list and materials/<name>/material.xml."""
    colours = {}
    for n in z.namelist():
        if n.startswith("materials/") and n.endswith("/material.xml"):
            try:
                el = ET.fromstring(z.read(n)).find("{*}material")
            except ET.ParseError:
                continue
            if el is None:
                continue
            rgb = [int(el.get(k, 200)) / 255 for k in ("colorRed", "colorGreen", "colorBlue")]
            tex = el.find("{*}texture")
            if tex is not None and tex.get("avgColor"):  # a picture that isn't in the file: its average colour
                v = int(tex.get("avgColor")) & 0xFFFFFFFF
                rgb = [((v >> s) & 255) / 255 for s in (16, 8, 0)]
            alpha = 1.0 - float(el.get("trans", 0)) if el.get("useTrans") == "1" else 1.0
            colours[n.split("/")[1]] = (el.get("name") or n.split("/")[1], tuple(rgb) + (alpha,))
    out = {}
    for lst in rec.kids(rec.child(top, 12500) or (0, 0, 0)):
        if lst[0] != 12501:
            continue
        for mt in rec.kids(lst):
            mid = rec.entity_id(mt)
            name = rec.raw(rec.child(mt, 13004)).decode("utf-8", "replace")
            out[mid] = colours.get(name, (name, (0.8, 0.8, 0.8, 1.0)))
    return out


# ---------------------------------------------------------------------------
# faces -> triangles
# ---------------------------------------------------------------------------

def _area2(poly):
    x, y = poly[:, 0], poly[:, 1]
    return float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def _bridge(outer, hole):
    """Join a hole to the outline (both lists of 2D points + index pairs) with a two-way cut."""
    hi = max(range(len(hole)), key=lambda k: hole[k][0][0])
    hx, hy = hole[hi][0]
    best, best_x = None, None
    for k in range(len(outer)):
        (ax, ay), (bx, by) = outer[k][0], outer[(k + 1) % len(outer)][0]
        if (ay > hy) == (by > hy) or ay == by:
            continue
        x = ax + (hy - ay) * (bx - ax) / (by - ay)
        if x >= hx and (best_x is None or x < best_x):
            best_x = x
            best = k if ax > bx else (k + 1) % len(outer)
    if best is None:
        best = min(range(len(outer)), key=lambda k: (outer[k][0][0] - hx) ** 2 + (outer[k][0][1] - hy) ** 2)
    ring = hole[hi:] + hole[:hi + 1]
    return outer[:best + 1] + ring + outer[best:]


def triangulate(loops2d):
    """loops2d: [outer, holes...] each a list of (point2d, index). Returns index triples."""
    outer = loops2d[0]
    if _area2(np.array([p for p, _ in outer])) < 0:
        outer = outer[::-1]
    holes = []
    for h in loops2d[1:]:
        if len(h) >= 3:
            holes.append(h[::-1] if _area2(np.array([p for p, _ in h])) > 0 else h)
    for h in sorted(holes, key=lambda h: -max(p[0] for p, _ in h)):
        outer = _bridge(outer, h)
    pts = [p for p, _ in outer]
    ids = [i for _, i in outer]
    n = len(pts)
    if n < 3:
        return []
    if n == 3:
        return [tuple(ids)]
    idx = list(range(n))
    out = []

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    guard = 0
    while len(idx) > 3 and guard < 4 * n * n:
        guard += 1
        found = False
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = pts[i0], pts[i1], pts[i2]
            if cross(a, b, c) <= 1e-12:
                continue
            bad = False
            for j in idx:
                if j in (i0, i1, i2) or pts[j] in (a, b, c):
                    continue
                q = pts[j]
                if cross(a, b, q) >= 0 and cross(b, c, q) >= 0 and cross(c, a, q) >= 0:
                    bad = True
                    break
            if bad:
                continue
            out.append((ids[i0], ids[i1], ids[i2]))
            idx.pop(k)
            found = True
            break
        if not found:
            break
    for k in range(1, len(idx) - 1):  # what's left (nearly flat corners): a fan
        out.append((ids[idx[0]], ids[idx[k]], ids[idx[k + 1]]))
    return out


def _loop_vertices(edge_ids, edges):
    """Vertex ids around a loop, from its edges (each edge: (v1, v2))."""
    es = [edges[e] for e in edge_ids if e in edges]
    if len(es) < 3:
        return []
    out = []
    for k in range(len(es)):
        prev, cur = es[k - 1], es[k]
        shared = set(prev) & set(cur)
        if not shared:
            return []
        out.append(shared.pop())
    return out


class Definition:
    """One set of entities (a component definition, a group, or the model itself)."""

    def __init__(self, rec, ents, name):
        self.rec, self.ents, self.name = rec, ents, name
        self.faces = None
        self.instances = None

    def load(self):
        if self.faces is not None:
            return
        r, ents = self.rec, self.ents
        verts, edges = {}, {}
        for v in r.kids(r.child(ents, 5001) or (0, 0, 0)):
            p = r.raw(r.child(v, 2501))
            if len(p) == 24:
                verts[r.entity_id(v)] = struct.unpack("<3d", p)
        for e in r.kids(r.child(ents, 5002) or (0, 0, 0)):
            a, b = r.num(r.child(e, 3001)), r.num(r.child(e, 3002))
            edges[r.num(r.path(e, 2000, 1500, 1502))] = (a, b)
        self.faces = []
        for f in r.kids(r.child(ents, 5003) or (0, 0, 0)):
            plane = r.raw(r.child(f, 3501))
            normal = np.array(struct.unpack("<3d", plane[:24])) if len(plane) >= 24 else None
            loops = []
            for lp in r.kids(r.child(f, 3502) or (0, 0, 0)):
                uses = r.kids(r.child(lp, 4501) or lp)  # loop -> 4501 -> edge uses
                ids = [r.num(r.child(u, 4001)) for u in uses if u[0] == 4000]
                vs = [v for v in _loop_vertices(ids, edges) if v in verts]
                if len(vs) >= 3:
                    loops.append(vs)
            if loops:
                self.faces.append((r.num(r.path(f, 2000, 2001)), r.num(r.child(f, 3503)), normal, loops))
        self.verts = verts
        self.instances = []
        for kind in (5004, 5005):  # groups, components
            for it in r.kids(r.child(ents, kind) or (0, 0, 0)):
                core = it if it[0] == 6500 else r.child(it, 6500)
                if core is None:
                    continue
                t = r.raw(r.child(core, 6502))
                if len(t) < 104:
                    continue
                v = struct.unpack("<13d", t[:104])
                m = np.eye(4)
                m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = v[0:3], v[3:6], v[6:9], v[9:12]
                if abs(v[12]) > 1e-12 and v[12] != 1.0:
                    m[:3, :] /= v[12]
                if not np.all(np.isfinite(m)):
                    continue
                self.instances.append((r.num(r.child(core, 6503)), m, r.num(r.path(core, 2000, 2001))))

    def triangles(self, inherited):
        """{material id: (positions n x 3, normals n x 3)} with both sides of every face."""
        self.load()
        out = {}
        for front, back, normal, loops in self.faces:
            pts = {v: np.array(self.verts[v], np.float64) for lp in loops for v in lp}
            n = normal
            if n is None or not np.all(np.isfinite(n)) or np.linalg.norm(n) < 1e-12:
                a, b, c = (pts[v] for v in loops[0][:3])
                n = np.cross(b - a, c - a)
            n = n / max(np.linalg.norm(n), 1e-12)
            ax = np.cross(n, [0, 0, 1] if abs(n[2]) < 0.9 else [1, 0, 0])
            ax /= np.linalg.norm(ax)
            ay = np.cross(n, ax)
            loops2d = [[((float(pts[v] @ ax), float(pts[v] @ ay)), v) for v in lp] for lp in loops]
            tris = triangulate(loops2d)
            if not tris:
                continue
            tri = np.array([[pts[v] for v in t] for t in tris])
            # keep the winding that matches the face's normal
            fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
            flip = fn @ n < 0
            tri[flip] = tri[flip][:, ::-1]
            for mat, side, sign in ((front if front is not None else inherited, tri, 1.0),
                                    (back if back is not None else (front if front is not None else inherited),
                                     tri[:, ::-1], -1.0)):
                p, nn = out.setdefault(mat, ([], []))
                p.append(side.reshape(-1, 3))
                nn.append(np.tile(n * sign, (len(side) * 3, 1)))
        return {k: (np.concatenate(p), np.concatenate(nn)) for k, (p, nn) in out.items()}


def load_skp(path):
    path = Path(path)
    z, version = open_skp(path)
    r = Records(z.read("model.dat"))
    top_recs = r.kids((0, 0, len(r.d)))
    if not top_recs:
        raise ImportError3D("damaged SketchUp model data")
    top = top_recs[0]
    sections = {t: (t, s, e) for t, s, e in r.kids(top)}
    materials = read_materials(z, r, sections.get(0x01F7))
    defs = {}
    lib = r.path(sections.get(0x01F9), 6000, 6001)
    for dfn in r.kids(lib or (0, 0, 0)):
        if dfn[0] != 5500:
            continue
        ents = r.child(dfn, 5000)
        did = r.num(r.path(ents, 2000, 1500, 1502))
        name = r.raw(r.child(dfn, 5502)).decode("utf-8", "replace") or "component"
        defs[did] = Definition(r, ents, name)
    root_ents = r.child(sections.get(0x01F6), 5000)
    if root_ents is None:
        raise ImportError3D("the SketchUp file has no model in it")

    model = empty_model(path.stem)
    mat_index = {}

    def material(mid):
        if mid not in mat_index:
            name, rgba = materials.get(mid, ("default", DEFAULT_FRONT))
            mat_index[mid] = simple_material(model, re.sub(r"[<>]", "", name)[:60] or "material", rgba,
                                             blend=rgba[3] < 0.99, shininess=20)
        return mat_index[mid]

    total = [0]
    cache = {}

    def meshes(d, inherited, name):
        key = (id(d), inherited)
        if key not in cache:
            cache[key] = d.triangles(inherited)
        out = []
        for mid, (pos, nrm) in cache[key].items():
            total[0] += len(pos)
            if total[0] > MAX_VERTICES:
                raise ImportError3D("the model has more than %d vertices" % MAX_VERTICES)
            tri = np.arange(len(pos))
            out += _mesh_nodes(name[:80], pos.astype(np.float32), nrm.astype(np.float32), None, tri, material(mid))
        return out

    def build(d, name, inherited, depth, matrix=np.eye(4)):
        if depth > MAX_DEPTH:
            raise ImportError3D("the SketchUp model is nested too deeply")
        node = kn5write.new_dummy(name[:80], flat(matrix))
        node["children"] += meshes(d, inherited, name)
        for ref, m, mat in d.instances:
            sub = defs.get(ref)
            if sub is not None:
                node["children"].append(build(sub, sub.name, mat if mat is not None else inherited, depth + 1, m))
        return node

    root = Definition(r, root_ents, path.stem)
    scene = build(root, path.stem, None, 0)
    scene["matrix"] = flat(Z_UP @ np.diag([INCH, INCH, INCH, 1.0]))
    model["root"]["children"] = [scene]
    if not total[0]:
        raise ImportError3D("the SketchUp model has no faces (only lines?)")
    model["sketchup_version"] = ".".join(map(str, version)) if version else None
    return model
