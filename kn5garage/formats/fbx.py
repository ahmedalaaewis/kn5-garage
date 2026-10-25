"""FBX (binary, version 7.x) without Blender: meshes, materials, embedded and side-by-side
textures, units and up axis, rigged characters (skin clusters) and animations.

ASCII FBX and versions before 7.0 aren't read; Blender can still convert those."""

import struct
import zlib
from pathlib import Path

import numpy as np

from .. import kn5write
from ..importer import ImportError3D, MAX_VERTICES, _mesh_nodes, empty_model, read_sibling, simple_material, top4

MAGIC = b"Kaydara FBX Binary  \x00"
FBX_SECOND = 46186158000
MAX_ARRAY = 200_000_000  # bytes, after unpacking
FPS = 30.0


class Node:
    __slots__ = ("name", "props", "children")

    def __init__(self, name, props, children):
        self.name, self.props, self.children = name, props, children

    def find(self, name):
        return next((c for c in self.children if c.name == name), None)

    def findall(self, name):
        return [c for c in self.children if c.name == name]

    def value(self, name, default=None):
        c = self.find(name)
        return c.props[0] if c is not None and c.props else default


# ---------------------------------------------------------------------------
# reading the node tree
# ---------------------------------------------------------------------------

ARRAY = {b"f": "<f4", b"d": "<f8", b"l": "<i8", b"i": "<i4", b"b": "u1"}


def parse(data):
    if not data.startswith(MAGIC):
        if data[:200].lstrip().startswith(b";") or b"FBXHeaderExtension:" in data[:4000]:
            raise ImportError3D("this is a text (ASCII) FBX: save it as binary FBX, or open it through Blender")
        raise ImportError3D("not an FBX file")
    version = struct.unpack_from("<I", data, 23)[0]
    if version < 7000:
        raise ImportError3D("FBX version %d is too old; save it as FBX 2011 or newer" % version)
    wide = version >= 7500
    head = struct.Struct("<QQQB" if wide else "<IIIB")

    def prop(at):
        t = data[at:at + 1]
        at += 1
        if t == b"Y":
            return struct.unpack_from("<h", data, at)[0], at + 2
        if t == b"C":
            return bool(data[at]), at + 1
        if t == b"I":
            return struct.unpack_from("<i", data, at)[0], at + 4
        if t == b"F":
            return struct.unpack_from("<f", data, at)[0], at + 4
        if t == b"D":
            return struct.unpack_from("<d", data, at)[0], at + 8
        if t == b"L":
            return struct.unpack_from("<q", data, at)[0], at + 8
        if t in ARRAY:
            n, enc, clen = struct.unpack_from("<III", data, at)
            at += 12
            raw = data[at:at + clen]
            dt = np.dtype(ARRAY[t])
            if n * dt.itemsize > MAX_ARRAY:
                raise ImportError3D("an FBX array is too large")
            if enc == 1:
                d = zlib.decompressobj()
                raw = d.decompress(raw, n * dt.itemsize + 1)
            if len(raw) < n * dt.itemsize:
                raise ImportError3D("damaged FBX array")
            return np.frombuffer(raw, dt, n).copy(), at + clen
        if t in (b"S", b"R"):
            n = struct.unpack_from("<I", data, at)[0]
            raw = bytes(data[at + 4:at + 4 + n])
            return (raw if t == b"R" else raw.decode("utf-8", "replace")), at + 4 + n
        raise ImportError3D("damaged FBX file (property type %r)" % t)

    def node(at, depth):
        if depth > 64:
            raise ImportError3D("the FBX file is nested too deeply")
        end, nprops, plen, nlen = head.unpack_from(data, at)
        if end == 0:
            return None, at + head.size
        if end > len(data) or end <= at:
            raise ImportError3D("damaged FBX file")
        at += head.size
        name = data[at:at + nlen].decode("ascii", "replace")
        at += nlen
        props = []
        stop = at + plen
        for _ in range(nprops):
            v, at = prop(at)
            props.append(v)
        at = stop
        children = []
        while at < end:
            child, at = node(at, depth + 1)
            if child is None:
                break
            children.append(child)
        return Node(name, props, children), end

    at, top = 27, []
    while at < len(data) - head.size:
        n, at = node(at, 0)
        if n is None:
            break
        top.append(n)
    return Node("", [], top)


def props70(node):
    out = {}
    p = node.find("Properties70") if node is not None else None
    for c in (p.children if p is not None else []):
        if c.props:
            out[c.props[0]] = c.props[4:] if len(c.props) > 4 else []
    return out


# ---------------------------------------------------------------------------
# transforms
# ---------------------------------------------------------------------------

def euler(deg, order=0):
    """FBX Euler angles (degrees) -> 4x4; order 0 = XYZ (X applied first)."""
    rx, ry, rz = np.radians(np.asarray(deg, np.float64)[:3])
    X = np.array([[1, 0, 0], [0, np.cos(rx), -np.sin(rx)], [0, np.sin(rx), np.cos(rx)]])
    Y = np.array([[np.cos(ry), 0, np.sin(ry)], [0, 1, 0], [-np.sin(ry), 0, np.cos(ry)]])
    Z = np.array([[np.cos(rz), -np.sin(rz), 0], [np.sin(rz), np.cos(rz), 0], [0, 0, 1]])
    seq = {0: (Z, Y, X), 1: (Y, Z, X), 2: (X, Z, Y), 3: (Z, X, Y), 4: (Y, X, Z), 5: (X, Y, Z)}.get(int(order), (Z, Y, X))
    m = np.eye(4)
    m[:3, :3] = seq[0] @ seq[1] @ seq[2]
    return m


def trs(t=(0, 0, 0), r=None, s=(1, 1, 1)):
    m = np.eye(4) if r is None else r.copy()
    m[:3, :3] = m[:3, :3] * np.asarray(s, np.float64)[:3]
    m[:3, 3] = np.asarray(t, np.float64)[:3]
    return m


def local_matrix(p, t=None, r=None, s=None):
    """Lcl Translation / Rotation / Scaling with pre / post rotation and pivots."""
    get = lambda k, d: np.asarray(p.get(k) or d, np.float64)[:3]  # noqa: E731
    order = (p.get("RotationOrder") or [0])[0]
    t = get("Lcl Translation", (0, 0, 0)) if t is None else t
    r = get("Lcl Rotation", (0, 0, 0)) if r is None else r
    s = get("Lcl Scaling", (1, 1, 1)) if s is None else s
    pre, post = euler(get("PreRotation", (0, 0, 0))), euler(get("PostRotation", (0, 0, 0)))
    rp, sp = get("RotationPivot", (0, 0, 0)), get("ScalingPivot", (0, 0, 0))
    roff, soff = get("RotationOffset", (0, 0, 0)), get("ScalingOffset", (0, 0, 0))
    T = lambda v: trs(v)  # noqa: E731
    return (T(t) @ T(roff) @ T(rp) @ pre @ euler(r, order) @ np.linalg.inv(post) @ T(-rp) @ T(soff) @ T(sp)
            @ np.diag(list(s) + [1.0]) @ T(-sp))


def flat(m):
    return tuple(float(v) for v in np.asarray(m).T.reshape(-1))


def quat(m):
    """Rotation part of a 4x4 (scale removed) -> (x, y, z, w)."""
    r = m[:3, :3] / np.maximum(np.linalg.norm(m[:3, :3], axis=0), 1e-12)
    tr = np.trace(r)
    if tr > 0:
        s = np.sqrt(tr + 1) * 2
        q = [(r[2, 1] - r[1, 2]) / s, (r[0, 2] - r[2, 0]) / s, (r[1, 0] - r[0, 1]) / s, s / 4]
    elif r[0, 0] > r[1, 1] and r[0, 0] > r[2, 2]:
        s = np.sqrt(1 + r[0, 0] - r[1, 1] - r[2, 2]) * 2
        q = [s / 4, (r[0, 1] + r[1, 0]) / s, (r[0, 2] + r[2, 0]) / s, (r[2, 1] - r[1, 2]) / s]
    elif r[1, 1] > r[2, 2]:
        s = np.sqrt(1 + r[1, 1] - r[0, 0] - r[2, 2]) * 2
        q = [(r[0, 1] + r[1, 0]) / s, s / 4, (r[1, 2] + r[2, 1]) / s, (r[0, 2] - r[2, 0]) / s]
    else:
        s = np.sqrt(1 + r[2, 2] - r[0, 0] - r[1, 1]) * 2
        q = [(r[0, 2] + r[2, 0]) / s, (r[1, 2] + r[2, 1]) / s, s / 4, (r[1, 0] - r[0, 1]) / s]
    return np.array(q, np.float64)


# ---------------------------------------------------------------------------
# the scene
# ---------------------------------------------------------------------------

def _layer(geom, layer, data_key, index_key, n_polyvert, poly_of_corner, vert_of_corner):
    """A layer element (normals, UVs) as one value per polygon corner."""
    el = geom.find(layer)
    if el is None or el.find(data_key) is None:
        return None
    vals = np.asarray(el.value(data_key), np.float64)
    width = 3 if layer == "LayerElementNormal" else 2
    vals = vals[: len(vals) // width * width].reshape(-1, width)
    mapping = el.value("MappingInformationType", "ByPolygonVertex")
    reference = el.value("ReferenceInformationType", "Direct")
    if mapping in ("ByVertice", "ByVertex"):
        key = vert_of_corner
    elif mapping == "ByPolygon":
        key = poly_of_corner
    elif mapping == "AllSame":
        key = np.zeros(n_polyvert, np.int64)
    else:
        key = np.arange(n_polyvert)
    if reference == "IndexToDirect" and el.find(index_key) is not None:
        idx = np.asarray(el.value(index_key), np.int64)
        if key.max(initial=0) >= len(idx):
            return None
        key = idx[key]
    if len(key) and (key.max() >= len(vals) or key.min() < 0):
        return None
    return vals[key]


def load_fbx(path):
    path = Path(path)
    root = parse(path.read_bytes())
    objects = root.find("Objects")
    if objects is None:
        raise ImportError3D("the FBX file has no objects")
    obj = {}
    for o in objects.children:
        if o.props and isinstance(o.props[0], int):
            obj[o.props[0]] = o
    children, parents, prop_links = {}, {}, {}
    conns = root.find("Connections")
    for c in (conns.findall("C") if conns is not None else []):
        if len(c.props) >= 3:
            kind, child, parent = c.props[0], c.props[1], c.props[2]
            children.setdefault(parent, []).append(child)
            parents.setdefault(child, []).append(parent)
            if kind == "OP" and len(c.props) > 3:
                prop_links.setdefault(parent, []).append((child, c.props[3]))

    def name_of(o):
        return str(o.props[1]).split("\x00")[0] if len(o.props) > 1 else "node"

    def kind_of(o):
        return o.props[2] if len(o.props) > 2 else ""

    # units and axes
    gs = props70(root.find("GlobalSettings"))
    unit = float((gs.get("UnitScaleFactor") or [1.0])[0])
    up = int((gs.get("UpAxis") or [1])[0])
    up_sign = int((gs.get("UpAxisSign") or [1])[0])
    top = np.diag([unit * 0.01] * 3 + [1.0])  # FBX units are centimetres times the factor
    if up == 2:
        top = np.array([[1, 0, 0, 0], [0, 0, up_sign, 0], [0, -up_sign, 0, 0], [0, 0, 0, 1]], np.float64) @ top
    elif up == 0:
        top = np.array([[0, up_sign, 0, 0], [-up_sign, 0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], np.float64) @ top

    model = empty_model(path.stem)
    mat_cache = {}

    def texture_of(mat_id, channel="DiffuseColor"):
        for child, prop in prop_links.get(mat_id, []):
            if prop not in (channel, "Diffuse", "DiffuseFactor") or child not in obj or obj[child].name != "Texture":
                continue
            tex = obj[child]
            for vid in children.get(child, []):  # embedded picture
                video = obj.get(vid)
                if video is not None and video.name == "Video":
                    content = video.find("Content")
                    if content is not None and content.props and isinstance(content.props[0], bytes) and content.props[0]:
                        return content.props[0], name_of(video)
            for key in ("RelativeFilename", "FileName"):
                f = tex.value(key)
                if f:
                    data = read_sibling(path.parent, f)
                    if data:
                        return data, Path(str(f).replace("\\", "/")).name
        return None, None

    def material(mat_id):
        if mat_id in mat_cache:
            return mat_cache[mat_id]
        m = obj.get(mat_id)
        p = props70(m)
        colour = list((p.get("DiffuseColor") or p.get("Diffuse") or [0.8, 0.8, 0.8])[:3]) + [1.0]
        opacity = float((p.get("Opacity") or [1.0])[0])
        image, image_name = texture_of(mat_id)
        normal, _ = texture_of(mat_id, "NormalMap")
        mat_cache[mat_id] = simple_material(model, name_of(m) if m is not None else "material", colour, image=image,
                                            image_name=image_name, normal=normal, blend=opacity < 0.99)
        return mat_cache[mat_id]

    models = {i: o for i, o in obj.items() if o.name == "Model"}
    model_props = {i: props70(o) for i, o in models.items()}
    model_parent = {}
    for i in models:
        ps = [p for p in parents.get(i, []) if p in models]
        model_parent[i] = ps[0] if ps else 0

    local = {i: local_matrix(model_props[i]) for i in models}
    world_cache = {0: np.eye(4)}  # in the file's own units and axes (the "scene" node adds `top`)

    def world(i, depth=0):
        if i not in world_cache:
            if depth > 500:
                raise ImportError3D("the FBX node tree loops")
            world_cache[i] = world(model_parent[i], depth + 1) @ local[i]
        return world_cache[i]

    nodes = {i: kn5write.new_dummy(name_of(o)[:80] or "node", flat(local[i])) for i, o in models.items()}
    # unique names: animations and bones are found by name
    seen = {}
    for i in sorted(nodes):
        n = nodes[i]["name"]
        if n in seen:
            seen[n] += 1
            nodes[i]["name"] = ("%s_%d" % (n, seen[n]))[:80]
        else:
            seen[n] = 0

    total = 0
    for gid, geom in obj.items():
        if geom.name != "Geometry" or kind_of(geom) != "Mesh":
            continue
        owners = [p for p in parents.get(gid, []) if p in models]
        if not owners:
            continue
        owner = owners[0]
        v = np.asarray(geom.value("Vertices", np.zeros(0)), np.float64)
        pv = np.asarray(geom.value("PolygonVertexIndex", np.zeros(0, np.int64)), np.int64)
        if not len(v) or not len(pv):
            continue
        pos_all = v[: len(v) // 3 * 3].reshape(-1, 3)
        ends = pv < 0
        vert_of_corner = np.where(ends, ~pv, pv)
        if vert_of_corner.max() >= len(pos_all) or vert_of_corner.min() < 0:
            raise ImportError3D("damaged FBX polygon indices")
        poly_of_corner = np.concatenate([[0], np.cumsum(ends)[:-1]]).astype(np.int64)
        starts = np.concatenate([[0], np.flatnonzero(ends)[:-1] + 1])
        counts = np.flatnonzero(ends) - starts + 1
        corners = np.concatenate([np.stack([np.full(c - 2, s), s + np.arange(1, c - 1), s + np.arange(2, c)], 1)
                                  for s, c in zip(starts, counts) if c >= 3] or [np.zeros((0, 3), np.int64)])
        nrm = _layer(geom, "LayerElementNormal", "Normals", "NormalsIndex", len(pv), poly_of_corner, vert_of_corner)
        uv = _layer(geom, "LayerElementUV", "UV", "UVIndex", len(pv), poly_of_corner, vert_of_corner)
        mats_el = geom.find("LayerElementMaterial")
        poly_mat = np.zeros(int(ends.sum()), np.int64)
        if mats_el is not None and mats_el.find("Materials") is not None:
            mm = np.asarray(mats_el.value("Materials"), np.int64)
            if mats_el.value("MappingInformationType", "") == "ByPolygon" and len(mm) == len(poly_mat):
                poly_mat = mm
            elif len(mm):
                poly_mat[:] = mm[0]
        owner_mats = [c for c in children.get(owner, []) if c in obj and obj[c].name == "Material"]
        # geometric transform (applies to this mesh only)
        gp = model_props[owner]
        geo = trs(gp.get("GeometricTranslation") or (0, 0, 0), euler(gp.get("GeometricRotation") or (0, 0, 0)),
                  gp.get("GeometricScaling") or (1, 1, 1))
        skin = _skin(gid, obj, children, models, nodes, world, owner, len(pos_all), top)
        total += len(pos_all)
        if total > MAX_VERTICES:
            raise ImportError3D("the model has more than %d vertices" % MAX_VERTICES)
        for mi in sorted(set(poly_mat.tolist())):
            sel = poly_mat[poly_of_corner[corners[:, 0]]] == mi if len(corners) else np.zeros(0, bool)
            cs = corners[sel]
            if not len(cs):
                continue
            # one vertex per distinct (position, normal, uv) corner
            keys = [vert_of_corner[cs.reshape(-1)][:, None]]
            if nrm is not None:
                keys.append(np.round(nrm[cs.reshape(-1)], 5))
            if uv is not None:
                keys.append(np.round(uv[cs.reshape(-1)], 6))
            key = np.hstack(keys)
            uniq, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
            corner_ids = cs.reshape(-1)[first]
            vi = vert_of_corner[corner_ids]
            place = geo if skin is None else skin[3] @ geo  # rigged: world space at bind time
            p = (np.c_[pos_all[vi], np.ones(len(vi))] @ place.T)[:, :3]
            n = None if nrm is None else nrm[corner_ids] @ np.linalg.inv(place[:3, :3])
            u = None
            if uv is not None:
                u = uv[corner_ids].copy()
                u[:, 1] = 1 - u[:, 1]
            mat = material(owner_mats[mi]) if mi < len(owner_mats) else simple_material(model, "default")
            mesh_name = "%s%s" % (nodes[owner]["name"], "_%d" % mi if mi else "")
            sk = None
            if skin is not None:
                bones, joints, weights, _ = skin
                sk = (bones, joints[vi], weights[vi])
            made = _mesh_nodes(mesh_name[:80], p.astype(np.float32), None if n is None else n.astype(np.float32),
                               None if u is None else u.astype(np.float32), inv.reshape(-1), mat, sk)
            if sk is not None:
                model["root"]["children"] += made  # rigged meshes sit at the top, moved by their bones
            else:
                nodes[owner]["children"] += made

    for i in sorted(models):
        parent = model_parent[i]
        (nodes[parent]["children"] if parent in nodes else model["root"]["children"]).append(nodes[i])
    # rigged meshes sit at the root (in world space); everything else under the units / axes node
    rigged = [n for n in model["root"]["children"] if n["cls"] == 3]
    others = [n for n in model["root"]["children"] if n["cls"] != 3]
    model["root"]["children"] = [kn5write.new_dummy("scene", flat(top), others)] + rigged
    if not any(n["cls"] != 1 for n in _walk(model["root"])):
        raise ImportError3D("no meshes found in the FBX file")
    model["animations"] = _animations(obj, children, parents, prop_links, models, model_props, nodes)
    return model


def _skin(gid, obj, children, models, nodes, world, owner, nverts, top):
    """(bones [(name, inverse bind matrix)], joints, weights, mesh at bind in file space) or None.

    Rigged vertices are stored in world space as they were at bind time, so a bone's inverse
    bind matrix is simply the inverse of its world matrix at bind time (TransformLink)."""
    skins = [c for c in children.get(gid, []) if c in obj and obj[c].name == "Deformer" and kind_of_(obj[c]) == "Skin"]
    if not skins:
        return None
    bones, lists, mesh_bind = [], [[] for _ in range(nverts)], None
    for cid in children.get(skins[0], []):
        cl = obj.get(cid)
        if cl is None or cl.name != "Deformer" or kind_of_(cl) != "Cluster":
            continue
        limb = [c for c in children.get(cid, []) if c in models]
        if not limb:
            continue
        idx = np.asarray(cl.value("Indexes", np.zeros(0)), np.int64)
        w = np.asarray(cl.value("Weights", np.zeros(0)), np.float64)
        tl, tm = cl.value("TransformLink"), cl.value("Transform")
        bone_bind = np.asarray(tl, np.float64).reshape(4, 4).T if tl is not None and len(tl) == 16 else world(limb[0])
        if mesh_bind is None:
            mesh_bind = np.asarray(tm, np.float64).reshape(4, 4).T if tm is not None and len(tm) == 16 else world(owner)
        j = len(bones)
        bones.append((nodes[limb[0]]["name"], flat(np.linalg.inv(top @ bone_bind))))
        for k, wt in zip(idx, w):
            if 0 <= k < nverts and wt > 0:
                lists[k].append((j, wt))
    if not bones:
        return None
    width = max(4, max((len(x) for x in lists), default=0))
    joints, weights = np.zeros((nverts, width)), np.zeros((nverts, width))
    for k, x in enumerate(lists):
        for c, (j, wt) in enumerate(x):
            joints[k, c], weights[k, c] = j, wt
    joints, weights = top4(joints, weights)
    return bones, joints, weights, top @ mesh_bind


def kind_of_(o):
    return o.props[2] if len(o.props) > 2 else ""


def _animations(obj, children, parents, prop_links, models, model_props, nodes):
    """AnimationStack -> {name: {node name: {"t", "r", "s"}}} sampled at 30 fps."""
    out = {}
    for sid, stack in obj.items():
        if stack.name != "AnimationStack":
            continue
        layers = [c for c in children.get(sid, []) if c in obj and obj[c].name == "AnimationLayer"]
        curves_for = {}  # model id -> {"T"/"R"/"S": {axis: (times, values)}}
        length = 0.0
        for lid in layers:
            for cnid in children.get(lid, []):
                cn = obj.get(cnid)
                if cn is None or cn.name != "AnimationCurveNode":
                    continue
                target = [(p, pr) for p in parents.get(cnid, []) for c, pr in prop_links.get(p, []) if c == cnid]
                target = [(p, pr) for p, pr in target if p in models]
                if not target:
                    continue
                mid, prop = target[0]
                key = {"Lcl Translation": "T", "Lcl Rotation": "R", "Lcl Scaling": "S"}.get(prop)
                if key is None:
                    continue
                for curve_id, axis in prop_links.get(cnid, []):
                    curve = obj.get(curve_id)
                    if curve is None or curve.name != "AnimationCurve":
                        continue
                    times = np.asarray(curve.value("KeyTime", np.zeros(0)), np.float64) / FBX_SECOND
                    vals = np.asarray(curve.value("KeyValueFloat", np.zeros(0)), np.float64)
                    if len(times) and len(times) == len(vals):
                        curves_for.setdefault(mid, {}).setdefault(key, {})[axis[-1].upper()] = (times, vals)
                        length = max(length, float(times.max()))
        if not curves_for:
            continue
        frames = np.arange(0, length + 1e-6, 1 / FPS)[:6000]
        tracks = {}
        for mid, chans in curves_for.items():
            p = model_props[mid]
            base = {"T": p.get("Lcl Translation") or (0, 0, 0), "R": p.get("Lcl Rotation") or (0, 0, 0),
                    "S": p.get("Lcl Scaling") or (1, 1, 1)}

            def sample(key):
                cols = []
                for k, axis in enumerate("XYZ"):
                    if axis in chans.get(key, {}):
                        tt, vv = chans[key][axis]
                        cols.append(np.interp(frames, tt, vv))
                    else:
                        cols.append(np.full(len(frames), float(base[key][k])))
                return np.stack(cols, 1)
            T, R, S = sample("T"), sample("R"), sample("S")
            mats = [local_matrix(p, T[f], R[f], S[f]) for f in range(len(frames))]
            tracks[nodes[mid]["name"]] = {
                "t": np.array([m[:3, 3] for m in mats], np.float32),
                "r": np.array([quat(m) for m in mats], np.float32),
                "s": np.array([np.linalg.norm(m[:3, :3], axis=0) for m in mats], np.float32)}
        name = str(stack.props[1]).split("\x00")[0] if len(stack.props) > 1 else "animation"
        out[(name.split("|")[-1] or "animation")[:60]] = tracks
    return out


def _walk(n):
    yield n
    for c in n["children"]:
        yield from _walk(c)
