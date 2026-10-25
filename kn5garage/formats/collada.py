"""COLLADA (.dae): the XML format used by BeamNG.drive, SketchUp exports and many 3D tools.

Meshes, node transforms, materials with colours and textures, units and up axis. Rigged
(skinned) meshes are shown in their rest pose."""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

from .. import kn5write
from ..importer import ImportError3D, MAX_VERTICES, _mesh_nodes, empty_model, read_sibling, simple_material

Z_UP = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]], np.float64)


def _strip(root):
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _floats(text):
    return np.array(text.split(), np.float64) if text and text.strip() else np.zeros(0)


def flat(m):
    return tuple(float(v) for v in np.asarray(m).T.reshape(-1))


def _rotation(axis_angle):
    x, y, z, deg = axis_angle
    n = np.linalg.norm([x, y, z])
    if n < 1e-12:
        return np.eye(4)
    x, y, z = x / n, y / n, z / n
    a = np.radians(deg)
    c, s, t = np.cos(a), np.sin(a), 1 - np.cos(a)
    m = np.eye(4)
    m[:3, :3] = [[t * x * x + c, t * x * y - s * z, t * x * z + s * y],
                 [t * x * y + s * z, t * y * y + c, t * y * z - s * x],
                 [t * x * z - s * y, t * y * z + s * x, t * z * z + c]]
    return m


def node_transform(node):
    m = np.eye(4)
    for el in node:
        if el.tag == "matrix":
            v = _floats(el.text)
            if len(v) == 16:
                m = m @ v.reshape(4, 4)
        elif el.tag == "translate":
            v = _floats(el.text)
            t = np.eye(4)
            t[:3, 3] = v[:3]
            m = m @ t
        elif el.tag == "rotate":
            v = _floats(el.text)
            if len(v) == 4:
                m = m @ _rotation(v)
        elif el.tag == "scale":
            v = _floats(el.text)
            m = m @ np.diag(list(v[:3]) + [1.0])
    return m if np.all(np.isfinite(m)) else np.eye(4)


def load_dae(path):
    path = Path(path)
    raw = path.read_bytes()
    if b"<!ENTITY" in raw[:4096] or b"<!DOCTYPE" in raw[:4096]:
        raise ImportError3D("the .dae file declares XML entities, which aren't allowed")
    try:
        root = _strip(ET.fromstring(raw))
    except ET.ParseError as e:
        raise ImportError3D("damaged .dae file: %s" % e)
    if root.tag != "COLLADA":
        raise ImportError3D("not a COLLADA file")
    ids = {el.get("id"): el for el in root.iter() if el.get("id")}

    def ref(url):
        return ids.get(str(url or "").lstrip("#"))

    model = empty_model(path.stem)
    unit = root.find("asset/unit")
    scale = float(unit.get("meter", 1)) if unit is not None else 1.0
    up = (root.findtext("asset/up_axis") or "Y_UP").strip().upper()

    # images and materials
    images = {}
    for img in root.iter("image"):
        src = img.findtext("init_from") or img.findtext("init_from/ref")
        if src:
            src = re.sub(r"^file:/+", "", src.strip())
            images[img.get("id")] = src.replace("%20", " ")
    mat_cache = {}

    def effect_of(material_id):
        mat = ref(material_id)
        if mat is None or mat.tag != "material":
            return None, material_id or "material"
        inst = mat.find("instance_effect")
        return (ref(inst.get("url")) if inst is not None else None), mat.get("name") or mat.get("id")

    def material(material_id):
        if material_id in mat_cache:
            return mat_cache[material_id]
        eff, name = effect_of(material_id)
        colour, image, image_name, blend = (0.8, 0.8, 0.8, 1.0), None, None, False
        if eff is not None:
            params = {p.get("sid"): p for p in eff.iter("newparam")}
            diffuse = next(eff.iter("diffuse"), None)
            if diffuse is not None:
                c = diffuse.find("color")
                if c is not None:
                    v = _floats(c.text)
                    if len(v) >= 3:
                        colour = tuple(v[:4]) if len(v) >= 4 else tuple(v[:3]) + (1.0,)
                t = diffuse.find("texture")
                if t is not None:
                    image_id = t.get("texture")
                    sampler = params.get(image_id)  # COLLADA 1.4: sampler -> surface -> image
                    if sampler is not None:
                        surf = params.get(sampler.findtext("sampler2D/source") or "")
                        inst = sampler.find("sampler2D/instance_image")
                        if surf is not None:
                            image_id = surf.findtext("surface/init_from") or image_id
                        elif inst is not None:
                            image_id = inst.get("url", "").lstrip("#")
                    file = images.get(image_id) or images.get(str(image_id).lstrip("#"))
                    if file:
                        image, image_name = read_sibling(path.parent, file), Path(file).name
            tr = next(eff.iter("transparency"), None)
            if tr is not None and tr.findtext("float") and float(tr.findtext("float")) < 0.99:
                blend = True
        mat_cache[material_id] = simple_material(model, name, colour, image=image, image_name=image_name, blend=blend)
        return mat_cache[material_id]

    def source(src_id):
        s = ref(src_id)
        if s is None:
            raise ImportError3D("missing data source %s" % src_id)
        arr = _floats(s.findtext("float_array"))
        acc = s.find("technique_common/accessor")
        stride = int(acc.get("stride", 1)) if acc is not None else 1
        return arr[: len(arr) // stride * stride].reshape(-1, stride)

    total = [0]

    def geometry(geom, bind, name, bind_shape=None):
        mesh = geom.find("mesh")
        if mesh is None:
            return []
        out = []
        vert_inputs = {}
        verts_el = mesh.find("vertices")
        if verts_el is not None:
            for inp in verts_el.findall("input"):
                vert_inputs[inp.get("semantic")] = inp.get("source")
        for prim_i, prim in enumerate(el for el in mesh if el.tag in ("triangles", "polylist", "polygons")):
            inputs = prim.findall("input")
            if not inputs:
                continue
            stride = max(int(i.get("offset", 0)) for i in inputs) + 1
            if prim.tag == "polygons":
                polys = [_floats(p.text).astype(np.int64) for p in prim.findall("p")]
                counts = [len(p) // stride for p in polys]
                idx = np.concatenate(polys) if polys else np.zeros(0, np.int64)
            else:
                idx = _floats(prim.findtext("p")).astype(np.int64)
                counts = _floats(prim.findtext("vcount")).astype(np.int64) if prim.tag == "polylist" else \
                    np.full(len(idx) // (3 * stride), 3)
            idx = idx[: len(idx) // stride * stride].reshape(-1, stride)
            cols = {}
            for inp in inputs:
                sem, off = inp.get("semantic"), int(inp.get("offset", 0))
                if sem == "VERTEX":
                    for vsem, vsrc in vert_inputs.items():
                        cols.setdefault(vsem, (vsrc, off))
                elif sem in ("NORMAL", "TEXCOORD") and sem not in cols:
                    cols[sem] = (inp.get("source"), off)
            if "POSITION" not in cols:
                continue
            # fan-triangulate polygons (corner rows in idx)
            corners, at = [], 0
            for c in np.asarray(counts, np.int64):
                corners += [(at, at + k, at + k + 1) for k in range(1, int(c) - 1)]
                at += int(c)
            if not corners:
                continue
            corners = np.array(corners, np.int64)
            if corners.max() >= len(idx):
                raise ImportError3D("damaged polygon list in %s" % name)
            keys = idx[corners.reshape(-1)]
            uniq, inv = np.unique(keys, axis=0, return_inverse=True)
            pos_src = source(cols["POSITION"][0])
            pi = uniq[:, cols["POSITION"][1]]
            if pi.max() >= len(pos_src):
                raise ImportError3D("damaged vertex indices in %s" % name)
            pos = pos_src[pi][:, :3]
            if bind_shape is not None:
                pos = (np.c_[pos, np.ones(len(pos))] @ bind_shape.T)[:, :3]
            nrm = uv = None
            if "NORMAL" in cols:
                ns = source(cols["NORMAL"][0])
                ni = uniq[:, cols["NORMAL"][1]]
                if ni.max() < len(ns):
                    nrm = ns[ni][:, :3]
                    if bind_shape is not None:
                        nrm = nrm @ np.linalg.inv(bind_shape[:3, :3])
            if "TEXCOORD" in cols:
                ts = source(cols["TEXCOORD"][0])
                ti = uniq[:, cols["TEXCOORD"][1]]
                if ti.max() < len(ts):
                    uv = ts[ti][:, :2].copy()
                    uv[:, 1] = 1 - uv[:, 1]
            total[0] += len(pos)
            if total[0] > MAX_VERTICES:
                raise ImportError3D("the model has more than %d vertices" % MAX_VERTICES)
            symbol = prim.get("material")
            mat = material(bind.get(symbol, symbol))
            out += _mesh_nodes(("%s_%d" % (name, prim_i) if prim_i else name)[:80],
                               pos.astype(np.float32), None if nrm is None else nrm.astype(np.float32),
                               None if uv is None else uv.astype(np.float32), inv.reshape(-1), mat)
        return out

    def bindings(inst):
        return {im.get("symbol"): im.get("target", "").lstrip("#") for im in inst.iter("instance_material")}

    def build(node, depth=0):
        if depth > 100:
            raise ImportError3D("the model is nested too deeply")
        out = kn5write.new_dummy((node.get("name") or node.get("id") or "node")[:80], flat(node_transform(node)))
        for el in node:
            if el.tag == "instance_geometry":
                g = ref(el.get("url"))
                if g is not None:
                    out["children"] += geometry(g, bindings(el), g.get("name") or out["name"])
            elif el.tag == "instance_controller":
                c = ref(el.get("url"))
                skin = c.find("skin") if c is not None else None
                if skin is not None:
                    g = ref(skin.get("source"))
                    bsm = _floats(skin.findtext("bind_shape_matrix"))
                    if g is not None:
                        out["children"] += geometry(g, bindings(el), g.get("name") or out["name"],
                                                    bsm.reshape(4, 4) if len(bsm) == 16 else None)
            elif el.tag == "node":
                out["children"].append(build(el, depth + 1))
            elif el.tag == "instance_node":
                n = ref(el.get("url"))
                if n is not None and n.tag == "node":
                    out["children"].append(build(n, depth + 1))
        return out

    scene_url = root.find("scene/instance_visual_scene")
    scene = ref(scene_url.get("url")) if scene_url is not None else next(root.iter("visual_scene"), None)
    if scene is None:
        raise ImportError3D("the .dae file has no scene")
    top = np.diag([scale, scale, scale, 1.0])
    if up == "Z_UP":
        top = Z_UP @ top
    elif up == "X_UP":
        top = np.array([[0, -1, 0, 0], [1, 0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], np.float64) @ top
    model["root"]["children"] = [kn5write.new_dummy("scene", flat(top), [build(n) for n in scene.findall("node")])]
    if not any(n["cls"] != 1 for n in _walk(model["root"])):
        raise ImportError3D("no meshes found in the .dae file")
    return model


def _walk(n):
    yield n
    for c in n["children"]:
        yield from _walk(c)
