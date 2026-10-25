"""Characters: BVH motion-capture skeletons and MikuMikuDance PMX models.

BVH has only bones and motion, so each bone gets a thin visible shape that moves with it.
PMX characters come in rigged (bones and weights), in metres and with Y up."""

import re
import struct
from pathlib import Path

import numpy as np

from .. import kn5write
from ..importer import ImportError3D, MAX_VERTICES, _mesh_nodes, empty_model, read_sibling, simple_material, top4

FPS = 30.0


def flat(m):
    return tuple(float(v) for v in np.asarray(m).T.reshape(-1))


def translation(t):
    m = np.eye(4)
    m[:3, 3] = t
    return m


def axis_rotation(axis, deg):
    a = np.radians(deg)
    c, s = np.cos(a), np.sin(a)
    m = np.eye(4)
    if axis == "X":
        m[1:3, 1:3] = [[c, -s], [s, c]]
    elif axis == "Y":
        m[0, 0], m[0, 2], m[2, 0], m[2, 2] = c, s, -s, c
    else:
        m[0:2, 0:2] = [[c, -s], [s, c]]
    return m


def quat(m):
    from .fbx import quat as q
    return q(m)


def bone_shape(length_vec, radius):
    """A thin double pyramid from (0,0,0) to length_vec: how a bone is usually drawn."""
    v = np.asarray(length_vec, np.float64)
    length = np.linalg.norm(v)
    if length < 1e-9:
        return None
    d = v / length
    side = np.cross(d, [0, 1, 0] if abs(d[1]) < 0.9 else [1, 0, 0])
    side /= np.linalg.norm(side)
    up = np.cross(d, side)
    mid = v * 0.2
    r = min(radius, length * 0.15)
    pos = np.array([[0, 0, 0], mid + side * r, mid + up * r, mid - side * r, mid - up * r, v], np.float32)
    tri = np.array([[0, 2, 1], [0, 3, 2], [0, 4, 3], [0, 1, 4], [5, 1, 2], [5, 2, 3], [5, 3, 4], [5, 4, 1]], np.int64)
    return pos, tri


# ---------------------------------------------------------------------------
# BVH
# ---------------------------------------------------------------------------

def load_bvh(path):
    path = Path(path)
    tokens = path.read_text(encoding="utf-8", errors="replace").split()
    if not tokens or tokens[0].upper() != "HIERARCHY":
        raise ImportError3D("not a BVH file")
    joints = []  # {name, parent, offset, channels, end}
    at = 1
    stack = []
    channel_count = 0
    try:
        while at < len(tokens) and tokens[at].upper() != "MOTION":
            t = tokens[at].upper()
            if t in ("ROOT", "JOINT"):
                joints.append({"name": tokens[at + 1], "parent": stack[-1] if stack else None, "offset": np.zeros(3),
                               "channels": [], "end": None})
                at += 2
            elif t == "END":  # End Site
                joints.append({"name": None, "parent": stack[-1] if stack else None, "offset": np.zeros(3),
                               "channels": [], "end": True})
                at += 2
            elif t == "{":
                stack.append(len(joints) - 1)
                at += 1
            elif t == "}":
                stack.pop()
                at += 1
            elif t == "OFFSET":
                joints[stack[-1]]["offset"] = np.array([float(x) for x in tokens[at + 1:at + 4]])
                at += 4
            elif t == "CHANNELS":
                n = int(tokens[at + 1])
                joints[stack[-1]]["channels"] = [c.lower() for c in tokens[at + 2:at + 2 + n]]
                joints[stack[-1]]["first"] = channel_count
                channel_count += n
                at += 2 + n
            else:
                raise ImportError3D("unexpected word in BVH: %s" % tokens[at][:30])
            if len(joints) > 1000:
                raise ImportError3D("too many joints")
        at += 1
        frames = int(tokens[at + 1])          # Frames: N
        frame_time = float(tokens[at + 4])    # Frame Time: x
        values = np.array(tokens[at + 5:at + 5 + frames * channel_count], np.float64)
    except (IndexError, ValueError):
        raise ImportError3D("damaged BVH file")
    if not joints or frames < 1 or len(values) < frames * channel_count:
        raise ImportError3D("damaged BVH file")
    motion = values.reshape(frames, channel_count)
    real = [i for i, j in enumerate(joints) if not j["end"]]
    # size: most BVH files are in centimetres; a skeleton taller than 10 units is taken as cm
    reach = max(np.linalg.norm(j["offset"]) for j in joints)
    height = sum(np.linalg.norm(j["offset"]) for j in joints)
    scale = 0.01 if height > 10 or reach > 3 else 1.0

    model = empty_model(path.stem)
    mat = simple_material(model, "bones", (0.85, 0.75, 0.55, 1))
    nodes = {}
    seen = {}
    for i in real:
        j = joints[i]
        base = re.sub(r"[^\w.\-]", "_", j["name"])[:76] or "joint"
        seen[base] = seen.get(base, -1) + 1
        j["unique"] = base if not seen[base] else "%s_%d" % (base, seen[base])
        nodes[i] = kn5write.new_dummy(j["unique"], flat(translation(j["offset"])))
    radius = 0.03 / scale
    for i, j in enumerate(joints):
        p = j["parent"]
        if p is None:
            continue
        if not j["end"]:
            nodes[p]["children"].append(nodes[i])
        shape = bone_shape(j["offset"], radius)
        if shape is not None:  # the bone from the parent joint to this one, moving with the parent
            pos, tri = shape
            nodes[p]["children"] += _mesh_nodes(("%s_bone%d" % (joints[p]["unique"], i))[:80], pos, None, None,
                                                tri.reshape(-1), mat)
    roots = [nodes[i] for i in real if joints[i]["parent"] is None]
    model["root"]["children"] = [kn5write.new_dummy("skeleton", flat(np.diag([scale] * 3 + [1.0])), roots)]

    # motion: sampled at 30 fps
    times = np.arange(frames) * frame_time
    length = times[-1] if frames > 1 else 0
    samples = np.arange(0, length + 1e-9, 1 / FPS)[:6000] if frame_time > 0 else np.zeros(1)
    tracks = {}
    for i in real:
        j = joints[i]
        if not j["channels"]:
            continue
        cols = motion[:, j["first"]:j["first"] + len(j["channels"])]
        cols = np.stack([np.interp(samples, times, cols[:, k]) for k in range(cols.shape[1])], 1) if frames > 1 else cols[:1]
        ts, rs = [], []
        for row in cols:
            t = j["offset"].copy()
            m = np.eye(4)
            for ch, v in zip(j["channels"], row):
                if ch.endswith("position"):
                    t["xyz".index(ch[0])] += v
                else:
                    m = m @ axis_rotation(ch[0].upper(), v)
            ts.append(t)
            rs.append(quat(m))
        tracks[j["unique"]] = {"t": np.array(ts, np.float32), "r": np.array(rs, np.float32),
                               "s": np.ones((len(ts), 3), np.float32)}
    model["animations"] = {path.stem[:60] or "motion": tracks}
    return model


# ---------------------------------------------------------------------------
# PMX (MikuMikuDance)
# ---------------------------------------------------------------------------

MMD_SCALE = 0.08  # one MMD unit is about 8 cm


class _R:
    def __init__(self, data):
        self.d, self.at = data, 0

    def take(self, fmt):
        v = struct.unpack_from(fmt, self.d, self.at)
        self.at += struct.calcsize(fmt)
        return v

    def one(self, fmt):
        return self.take(fmt)[0]

    def array(self, dtype, n):
        dt = np.dtype(dtype)
        if self.at + dt.itemsize * n > len(self.d):
            raise ImportError3D("damaged PMX file")
        a = np.frombuffer(self.d, dt, n, self.at)
        self.at += dt.itemsize * n
        return a


def load_pmx(path):
    path = Path(path)
    data = path.read_bytes()
    if data[:4] != b"PMX ":
        if data[:3] == b"Pmd":
            raise ImportError3D("this is an old PMD model: open it in PMX Editor and save it as PMX")
        raise ImportError3D("not a PMX file")
    r = _R(data)
    try:
        return _pmx(path, r)
    except struct.error:
        raise ImportError3D("damaged PMX file")


def _pmx(path, r):
    r.at = 4
    r.one("<f")
    n_globals = r.one("<B")
    g = r.take("<%dB" % n_globals)
    enc = "utf-16-le" if g[0] == 0 else "utf-8"
    add_uv, vsize, tsize, msize, bsize = g[1], g[2], g[3], g[4], g[5]
    signed = {1: "<b", 2: "<h", 4: "<i"}

    def text():
        n = r.one("<i")
        if n < 0 or r.at + n > len(r.d):
            raise ImportError3D("damaged PMX text")
        s = r.d[r.at:r.at + n].decode(enc, "replace")
        r.at += n
        return s

    def bone_idx():
        return r.one(signed[bsize])

    def tex_idx():
        return r.one(signed[tsize])

    name = text()
    text()
    text()
    text()
    nv = r.one("<i")
    if not 0 <= nv <= MAX_VERTICES:
        raise ImportError3D("the model has too many vertices")
    pos = np.zeros((nv, 3))
    nrm = np.zeros((nv, 3))
    uv = np.zeros((nv, 2))
    joints = np.zeros((nv, 4))
    weights = np.zeros((nv, 4))
    for i in range(nv):
        v = r.take("<8f")
        pos[i], nrm[i], uv[i] = v[0:3], v[3:6], v[6:8]
        r.at += 16 * add_uv
        kind = r.one("<B")
        if kind == 0:  # BDEF1
            joints[i, 0], weights[i, 0] = bone_idx(), 1
        elif kind == 1:  # BDEF2
            a, b = bone_idx(), bone_idx()
            w = r.one("<f")
            joints[i, :2], weights[i, :2] = (a, b), (w, 1 - w)
        elif kind in (2, 4):  # BDEF4, QDEF
            joints[i] = [bone_idx() for _ in range(4)]
            weights[i] = r.take("<4f")
        elif kind == 3:  # SDEF: used as BDEF2
            a, b = bone_idx(), bone_idx()
            w = r.one("<f")
            r.at += 36
            joints[i, :2], weights[i, :2] = (a, b), (w, 1 - w)
        else:
            raise ImportError3D("damaged PMX vertex")
        r.at += 4  # edge scale
    nf = r.one("<i")
    faces = r.array({1: "u1", 2: "<u2", 4: "<i4"}[vsize], nf).astype(np.int64)
    if len(faces) and (faces.max() >= nv or faces.min() < 0):
        raise ImportError3D("damaged PMX faces")
    textures = [text() for _ in range(r.one("<i"))]
    materials = []
    for _ in range(r.one("<i")):
        mname = text()
        text()
        diffuse = r.take("<4f")
        r.at += 12 + 4 + 12  # specular, strength, ambient
        r.one("<B")
        r.at += 16 + 4  # edge colour and size
        tex = tex_idx()
        tex_idx()
        r.one("<B")
        toon_shared = r.one("<B")
        if toon_shared == 0:
            tex_idx()
        else:
            r.one("<B")
        text()
        count = r.one("<i")
        materials.append((mname, diffuse, tex, count))
    bones = []
    for _ in range(r.one("<i")):
        bname = text()
        text()
        bpos = r.take("<3f")
        parent = bone_idx()
        r.one("<i")
        flags = r.one("<H")
        if flags & 0x0001:
            bone_idx()
        else:
            r.at += 12
        if flags & (0x0100 | 0x0200):
            bone_idx()
            r.at += 4
        if flags & 0x0400:
            r.at += 12
        if flags & 0x0800:
            r.at += 24
        if flags & 0x2000:
            r.at += 4
        if flags & 0x0020:
            bone_idx()
            r.at += 8
            for _ in range(r.one("<i")):
                bone_idx()
                if r.one("<B"):
                    r.at += 24
        bones.append((bname, np.array(bpos, np.float64), parent))
        if len(bones) > 2000:
            raise ImportError3D("too many bones")

    # to metres, right-handed (MMD is left-handed: flip Z and turn the triangles round)
    flip = np.array([1, 1, -1]) * MMD_SCALE
    pos = pos * flip
    nrm = nrm * np.array([1, 1, -1])
    model = empty_model(path.stem)
    model["root"]["name"] = (name or path.stem)[:80] or path.stem
    bone_world = [b[1] * flip for b in bones]
    seen, names, dummies = {}, [], []
    for i, (bname, _, parent) in enumerate(bones):
        base = (bname or "bone%d" % i)[:76]
        seen[base] = seen.get(base, -1) + 1
        names.append(base if not seen[base] else "%s_%d" % (base, seen[base]))
    top = []
    for i, (_, _, parent) in enumerate(bones):
        attached = 0 <= parent < len(bones) and parent != i and _ancestor_ok(bones, i)
        p = bone_world[parent] if attached else np.zeros(3)
        dummies.append(kn5write.new_dummy(names[i], flat(translation(bone_world[i] - p))))
    for i, (_, _, parent) in enumerate(bones):
        if 0 <= parent < len(bones) and parent != i and _ancestor_ok(bones, i):
            dummies[parent]["children"].append(dummies[i])
        else:
            top.append(dummies[i])
    skin = None
    if bones:
        ibm = [(names[i], flat(translation(-bone_world[i]))) for i in range(len(bones))]
        bad = (joints < 0) | (joints >= len(bones))
        weights[bad] = 0
        joints[bad] = 0
        j4, w4 = top4(joints, weights)
        skin = (ibm, j4, w4)
    meshes = []
    at = 0
    for k, (mname, diffuse, tex, count) in enumerate(materials):
        tri = faces[at:at + count].reshape(-1, 3)[:, ::-1]
        at += count
        if not len(tri):
            continue
        image, image_name = None, None
        if 0 <= tex < len(textures):
            image_name = textures[tex]
            image = read_sibling(path.parent, image_name)
        mat = simple_material(model, mname or "material%d" % k, tuple(diffuse[:3]) + (1.0,), image=image,
                              image_name=Path(image_name.replace("\\", "/")).name if image_name else None,
                              blend=diffuse[3] < 0.99)
        used, remap = np.unique(tri, return_inverse=True)
        sk = (skin[0], skin[1][used], skin[2][used]) if skin else None
        meshes += _mesh_nodes((mname or "part%d" % k)[:80], pos[used].astype(np.float32), nrm[used].astype(np.float32),
                              uv[used].astype(np.float32), remap.reshape(-1), mat, sk)
    if not meshes:
        raise ImportError3D("the PMX model has no surfaces")
    model["root"]["children"] = top + meshes
    return model


def _ancestor_ok(bones, i):
    """The parent chain ends (no loops)."""
    seen = set()
    while 0 <= bones[i][2] < len(bones):
        if i in seen:
            return False
        seen.add(i)
        i = bones[i][2]
    return True
