"""3D printing: turn any model into a printable mesh.

- one triangle soup in millimetres, Z up, standing on the bed (z = 0), centred;
- repair: join matching corners, drop broken and doubled triangles, make all triangles face the
  same way (outwards), and close holes;
- scale to a real height;
- cut into pieces at heights (each cut is closed flat, so every piece is printable);
- STL (binary) and 3MF (zip + XML, millimetres) files.
"""

import io
import zipfile
from pathlib import Path

import numpy as np

from .kn5write import IDENTITY

MAX_TRIANGLES = 5_000_000
MAX_CUTS = 10


# ---------------------------------------------------------------------------
# model -> triangles
# ---------------------------------------------------------------------------

def model_triangles(model, include_hidden=False):
    """World-space triangles (n x 3 x 3, metres, Y up) of a kn5-like model."""
    out = []

    def visit(n, world):
        if n["cls"] == 1:
            m = np.array(n.get("matrix", IDENTITY), np.float64).reshape(4, 4).T
            world = world @ (m if np.all(np.isfinite(m)) else np.eye(4))
        elif include_hidden or (n.get("active", True) and n.get("visible", True) and not n.get("removed")):
            v = np.asarray(n["verts"], np.float64)
            idx = np.asarray(n["indices"], np.int64)
            idx = idx[: len(idx) // 3 * 3].reshape(-1, 3)
            if len(v) and len(idx) and idx.max() < len(v):
                p = (np.c_[v[:, :3], np.ones(len(v))] @ world.T)[:, :3]
                out.append(p[idx])
        if n["cls"] == 1 or include_hidden or n.get("active", True):
            for c in n["children"]:
                visit(c, world)
    visit(model["root"], np.eye(4))
    tris = np.concatenate(out) if out else np.zeros((0, 3, 3))
    if len(tris) > MAX_TRIANGLES:
        raise ValueError("the model has more than %d triangles" % MAX_TRIANGLES)
    return tris[np.all(np.isfinite(tris), axis=(1, 2))]


def to_print_space(tris, height_mm=None, scale=1000.0, up="y"):
    """Metres, Y up -> millimetres, Z up, on the bed and centred. height_mm overrides scale."""
    t = tris[..., [0, 2, 1]] * [1, -1, 1] if up == "y" else tris.copy()  # (x, y, z) -> (x, -z, y)
    if not len(t):
        return t, 1.0
    lo, hi = t.reshape(-1, 3).min(0), t.reshape(-1, 3).max(0)
    if height_mm:
        size = hi[2] - lo[2]
        if size <= 0:
            raise ValueError("the model is flat: it has no height to scale")
        scale = float(height_mm) / size
    t = (t - [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]]) * scale
    return t, scale


# ---------------------------------------------------------------------------
# repair
# ---------------------------------------------------------------------------

def weld(tris, tol=1e-4):
    """Triangle soup -> (vertices, faces) with corners closer than tol joined."""
    pts = tris.reshape(-1, 3)
    keys = np.round(pts / tol).astype(np.int64)
    _, first, inv = np.unique(keys, axis=0, return_index=True, return_inverse=True)
    return pts[first], inv.reshape(-1, 3)


def clean(verts, faces):
    """Drop triangles with a repeated corner or no area, and exact duplicates."""
    f = faces[(faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])]
    a = np.linalg.norm(np.cross(verts[f[:, 1]] - verts[f[:, 0]], verts[f[:, 2]] - verts[f[:, 0]]), axis=1)
    f = f[a > 1e-12]
    _, keep = np.unique(np.sort(f, axis=1), axis=0, return_index=True)
    return f[np.sort(keep)]


def orient(verts, faces):
    """Make neighbouring triangles agree on which side is out, then point every closed-ish
    piece outwards (positive volume)."""
    faces = faces.copy()
    n = len(faces)
    if not n:
        return faces
    edges = {}
    for i, (a, b, c) in enumerate(faces):
        for u, v in ((a, b), (b, c), (c, a)):
            edges.setdefault((min(u, v), max(u, v)), []).append(i)
    seen = np.zeros(n, bool)
    for start in range(n):
        if seen[start]:
            continue
        seen[start] = True
        stack, part = [start], [start]
        while stack:
            i = stack.pop()
            a, b, c = faces[i]
            for u, v in ((a, b), (b, c), (c, a)):
                for j in edges[(min(u, v), max(u, v))]:
                    if seen[j]:
                        continue
                    x, y, z = faces[j]
                    # a neighbour that agrees walks the shared edge the other way (v -> u)
                    if (u, v) in ((x, y), (y, z), (z, x)):
                        faces[j] = faces[j][::-1]
                    seen[j] = True
                    stack.append(j)
                    part.append(j)
        p = faces[part]
        vol = np.einsum("ij,ij->i", verts[p[:, 0]], np.cross(verts[p[:, 1]], verts[p[:, 2]])).sum()
        if vol < 0:
            faces[part] = faces[part][:, ::-1]
    return faces


def boundary_loops(faces):
    """Edges used by only one triangle, chained into loops (lists of vertex indices)."""
    count = {}
    for a, b, c in faces:
        for u, v in ((a, b), (b, c), (c, a)):
            k = (min(u, v), max(u, v))
            count[k] = count.get(k, 0) + 1
    nxt = {}
    for a, b, c in faces:
        for u, v in ((a, b), (b, c), (c, a)):
            if count[(min(u, v), max(u, v))] == 1:
                nxt.setdefault(v, []).append(u)  # the hole runs against the triangles
    loops, used = [], set()
    for s in list(nxt):
        for first in nxt[s]:
            if (s, first) in used:
                continue
            loop, cur, prev = [s], first, s
            used.add((s, first))
            while cur != s and len(loop) < 100000:
                loop.append(cur)
                options = [x for x in nxt.get(cur, []) if (cur, x) not in used]
                if not options:
                    break
                used.add((cur, options[0]))
                prev, cur = cur, options[0]
            if cur == s and len(loop) >= 3:
                loops.append(loop)
    return loops


def triangulate_loop(verts, loop):
    """Ear clipping on the loop's best-fit plane; returns triangles (index triples)."""
    pts = verts[loop]
    centre = pts.mean(0)
    _, _, vt = np.linalg.svd(pts - centre)
    normal = vt[2]  # the direction the loop varies least in
    ax = vt[0] - normal * np.dot(vt[0], normal)
    ax /= max(np.linalg.norm(ax), 1e-12)
    ay = np.cross(normal, ax)
    p2 = np.stack([(pts - centre) @ ax, (pts - centre) @ ay], 1)
    idx = list(range(len(loop)))
    out = []

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    sign = 1.0 if sum(cross(p2[0] * 0, p2[i], p2[(i + 1) % len(p2)]) for i in range(len(p2))) >= 0 else -1.0
    guard = 0
    while len(idx) > 3 and guard < len(loop) * len(loop):
        guard += 1
        found = False
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            if sign * cross(p2[i0], p2[i1], p2[i2]) <= 1e-12:
                continue  # not convex here
            inside = False
            for j in idx:
                if j in (i0, i1, i2):
                    continue
                q = p2[j]
                if (sign * cross(p2[i0], p2[i1], q) >= 0 and sign * cross(p2[i1], p2[i2], q) >= 0
                        and sign * cross(p2[i2], p2[i0], q) >= 0):
                    inside = True
                    break
            if inside:
                continue
            out.append((loop[i0], loop[i1], loop[i2]))
            idx.pop(k)
            found = True
            break
        if not found:  # self-touching outline: finish with a fan
            break
    for k in range(1, len(idx) - 1):
        out.append((loop[idx[0]], loop[idx[k]], loop[idx[k + 1]]))
    return out


def close_holes(verts, faces, max_loop=5000):
    tris = []
    for loop in boundary_loops(faces):
        if len(loop) <= max_loop:
            tris += triangulate_loop(verts, loop)
    if not tris:
        return faces, 0
    return np.vstack([faces, np.array(tris, np.int64)]), len(boundary_loops(faces))


def repair(tris, tol=1e-3):
    """Triangle soup -> repaired (verts, faces) and what was done."""
    verts, faces = weld(tris, tol)
    before = len(faces)
    faces = clean(verts, faces)
    dropped = before - len(faces)
    faces = orient(verts, faces)
    holes = len(boundary_loops(faces))
    faces, _ = close_holes(verts, faces)
    faces = orient(verts, faces)
    left = len(boundary_loops(faces))
    return verts, faces, {"dropped": int(dropped), "holesClosed": max(holes - left, 0), "holesLeft": left}


def vertex_normals(verts, faces):
    n = np.zeros_like(verts)
    fn = np.cross(verts[faces[:, 1]] - verts[faces[:, 0]], verts[faces[:, 2]] - verts[faces[:, 0]])
    for k in range(3):
        np.add.at(n, faces[:, k], fn)
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)


def thicken(verts, faces, thickness):
    """Give surfaces a wall: an inner copy moved back along the normals, joined to the outer
    one along every open edge. Closed parts are left as they are."""
    open_loops = boundary_loops(faces)
    if not open_loops:
        return verts, faces
    n = len(verts)
    inner = verts - vertex_normals(verts, faces) * float(thickness)
    walls = []
    for loop in open_loops:
        for k in range(len(loop)):
            a, b = loop[k], loop[(k + 1) % len(loop)]
            # the loop runs against the outer triangles, so (a, b) plus the inner pair faces outwards
            walls += [(a, b, b + n), (a, b + n, a + n)]
    faces = np.vstack([faces, faces[:, ::-1] + n, np.array(walls, np.int64)])
    return np.vstack([verts, inner]), faces


def stats(verts, faces):
    if not len(faces):
        return {"triangles": 0, "size": [0, 0, 0], "volume": 0.0, "watertight": False, "flat": True}
    p = verts[faces]
    lo, hi = p.reshape(-1, 3).min(0), p.reshape(-1, 3).max(0)
    vol = np.einsum("ij,ij->i", p[:, 0], np.cross(p[:, 1], p[:, 2])).sum() / 6
    size = hi - lo
    # no thickness (a single surface, like most game car bodies seen from inside): nothing to fill
    flat = float(size.min()) < 0.05 or abs(float(vol)) < 1e-6 * max(float(np.prod(np.maximum(size, 1e-9))), 1e-9)
    return {"triangles": int(len(faces)), "size": [round(float(x), 2) for x in size],
            "volume": round(abs(float(vol)) / 1000, 2), "watertight": not boundary_loops(faces) and not flat,
            "flat": flat}


# ---------------------------------------------------------------------------
# cutting
# ---------------------------------------------------------------------------

def cut(verts, faces, z):
    """Split at height z: (below, above), each as (verts, faces) closed with a flat cap."""
    p = verts[faces]
    d = p[..., 2] - z
    below, above = [], []
    for tri, dist in zip(p, d):
        neg, pos = dist < 0, dist >= 0
        if neg.all():
            below.append(tri)
            continue
        if pos.all():
            above.append(tri)
            continue
        # rotate so the lonely corner comes first
        lonely = int(np.flatnonzero(neg if neg.sum() == 1 else pos)[0])
        order = [lonely, (lonely + 1) % 3, (lonely + 2) % 3]
        a, b, c = tri[order]
        da, db, dc = dist[order]
        ab = a + (b - a) * (da / (da - db))
        ac = a + (c - a) * (da / (da - dc))
        lone_side, pair_side = (below, above) if neg[lonely] else (above, below)
        lone_side.append(np.array([a, ab, ac]))
        pair_side.append(np.array([ab, b, c]))
        pair_side.append(np.array([ab, c, ac]))
    out = []
    for side in (below, above):
        if not side:
            out.append((np.zeros((0, 3)), np.zeros((0, 3), np.int64)))
            continue
        v, f = weld(np.array(side), 1e-4)
        f = clean(v, f)
        f, _ = close_holes(v, f)
        out.append((v, orient(v, f)))
    return out


def cut_pieces(verts, faces, heights):
    heights = sorted(float(h) for h in heights)[:MAX_CUTS]
    pieces, rest = [], (verts, faces)
    for z in heights:
        lo, hi = cut(rest[0], rest[1], z)
        if len(lo[1]):
            pieces.append(lo)
        rest = hi
    if len(rest[1]):
        pieces.append(rest)
    return pieces


# ---------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------

def stl_bytes(verts, faces):
    p = verts[faces].astype(np.float32)
    n = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    rec = np.zeros(len(p), np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]))
    rec["n"], rec["v"] = n, p
    return b"KN5 Garage print, millimetres".ljust(80, b" ") + np.uint32(len(p)).tobytes() + rec.tobytes()


def threemf_bytes(pieces, name="model"):
    """A 3MF file (millimetres) with every piece as its own object, side by side on the bed."""
    objects, items, offset = [], [], 0.0
    for k, (v, f) in enumerate(pieces, 1):
        lo, hi = v.min(0), v.max(0)
        shift = np.array([offset - lo[0], -(lo[1] + hi[1]) / 2, -lo[2]])
        offset += (hi[0] - lo[0]) + 10
        vs = "".join('<vertex x="%.4f" y="%.4f" z="%.4f"/>' % tuple(x) for x in v + shift)
        ts = "".join('<triangle v1="%d" v2="%d" v3="%d"/>' % tuple(t) for t in f)
        objects.append('<object id="%d" type="model" name="%s part %d"><mesh><vertices>%s</vertices>'
                       '<triangles>%s</triangles></mesh></object>' % (k, _xml(name), k, vs, ts))
        items.append('<item objectid="%d"/>' % k)
    model = ('<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" xml:lang="en-US" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"><resources>%s</resources>'
             '<build>%s</build></model>' % ("".join(objects), "".join(items)))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
                   'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
        z.writestr("3D/3dmodel.model", model)
    return buf.getvalue()


def _xml(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def prepare(model, out_dir, name, height_mm=None, repair_mesh=True, cuts=(), fmt="stl", thickness=None):
    """The whole job: returns {"files": [...], "report": {...}}. thickness (mm): give open
    surfaces walls of this thickness instead of closing their holes."""
    tris, scale = to_print_space(model_triangles(model), height_mm)
    if not len(tris):
        raise ValueError("the model has no visible surfaces to print")
    if thickness:
        verts, faces = weld(tris, 1e-3)
        faces = orient(verts, clean(verts, faces))
        verts, faces = thicken(verts, faces, thickness)
        faces = orient(verts, faces)  # walls and inner side all facing out
        fixes = {"thickened": round(float(thickness), 2)}
    elif repair_mesh:
        verts, faces, fixes = repair(tris)
    else:
        verts, faces = weld(tris, 1e-6)
        fixes = {}
    pieces = cut_pieces(verts, faces, cuts) if cuts else [(verts, faces)]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    if fmt == "3mf":
        f = out_dir / ("%s.3mf" % name)
        f.write_bytes(threemf_bytes(pieces, name))
        files.append(f)
    else:
        for k, (v, fc) in enumerate(pieces, 1):
            f = out_dir / ("%s%s.stl" % (name, "_part%d" % k if len(pieces) > 1 else ""))
            f.write_bytes(stl_bytes(v, fc))
            files.append(f)
    report = dict(stats(verts, faces), scale=round(scale, 4), pieces=[stats(v, f) for v, f in pieces], **fixes)
    return {"files": files, "report": report}
