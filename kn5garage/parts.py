"""Editing a car's parts: hide, remove, move / rotate / scale, recolour, and swap in parts from
other cars or 3D files. Changes are kept as a list of steps over the original model (so undo
and redo are exact) and only written to a car when the user saves.

Parts are addressed by their index in a depth-first walk of the model, which is also the
node index in the preview .glb. Removed parts stay in the working model (marked) and added
parts are appended at the end of the root, so the indices never move during a session."""

import copy
import io
import re
import secrets
import time
from pathlib import Path

import numpy as np
from PIL import Image

from . import carfiles, importer, kn5write
from .convert import PRESETS, Converter
from .edits import EditError
from .kn5 import read_kn5
from .textures import decode_image, dds_bytes

MAX_SESSIONS = 4
MAX_STEPS = 500
MAX_SOURCES = 8
MAX_TINT_SIDE = 2048
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def walk(node):
    yield node
    for c in node["children"]:
        yield from walk(c)


def local_matrix(n):
    if n["cls"] != 1:
        return np.eye(4)
    m = np.array(n.get("matrix", kn5write.IDENTITY), np.float64).reshape(4, 4).T
    return m if np.all(np.isfinite(m)) else np.eye(4)


def parent_worlds(root):
    """id(node) -> world matrix of the node's parent."""
    out = {}

    def visit(n, parent):
        out[id(n)] = parent
        world = parent @ local_matrix(n)
        for c in n["children"]:
            visit(c, world)
    visit(root, np.eye(4))
    return out


def as_matrix(values):
    """16 numbers in kn5 / glTF / three.js order -> 4x4 (column vectors)."""
    if not isinstance(values, (list, tuple)) or len(values) != 16:
        raise EditError("a transform needs 16 numbers")
    try:
        m = np.array([float(v) for v in values], np.float64).reshape(4, 4).T
    except (TypeError, ValueError):
        raise EditError("a transform needs 16 numbers")
    if not np.all(np.isfinite(m)) or np.abs(m).max() > 1e5 or abs(np.linalg.det(m[:3, :3])) < 1e-9:
        raise EditError("that transform can't be used")
    return m


def flat(m):
    return tuple(float(v) for v in np.asarray(m).T.reshape(-1))


def bake(node, m):
    """Move a mesh's vertices by m (meshes have no transform of their own in a kn5)."""
    v = np.array(node["verts"], np.float32, copy=True)
    if not len(v):
        return
    lin = m[:3, :3]
    pos = np.c_[v[:, :3].astype(np.float64), np.ones(len(v))] @ m.T
    nm = np.linalg.inv(lin).T
    nrm = v[:, 3:6].astype(np.float64) @ nm.T
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    tan = v[:, 8:11].astype(np.float64) @ lin.T
    tan /= np.maximum(np.linalg.norm(tan, axis=1, keepdims=True), 1e-12)
    v[:, :3], v[:, 3:6], v[:, 8:11] = pos[:, :3], nrm, tan
    if np.linalg.det(lin) < 0:  # mirrored: turn the triangles round so they still face out
        node["indices"] = np.asarray(node["indices"]).reshape(-1, 3)[:, ::-1].reshape(-1).copy()
    node["verts"] = v
    node["bsphere_raw"] = None


class Session:
    def __init__(self, car_dir, car_id, skin=None):
        self.id = secrets.token_hex(6)
        self.car_id = car_id
        self.car_dir = Path(car_dir)
        path = carfiles.find_main_kn5(self.car_dir)
        if path is None:
            raise EditError("this car has no model")
        self.kn5_rel = path.relative_to(self.car_dir).as_posix()
        self.base = read_kn5(path)
        if self.base["protected"]:
            raise EditError("this model is protected by its author, so its parts can't be edited")
        self.skin, self.skin_files = carfiles.load_skin(self.car_dir, skin)
        self.steps, self.redo = [], []
        self.sources = {}  # key -> {"name", "model"}
        self.used = time.time()
        self.version = 0
        self._model = None
        self._moves = {}  # part index -> its last move (meshes get it baked into their vertices)

    # --- the working model ----------------------------------------------

    def model(self):
        if self._model is None:
            self._model = self.build()
        return self._model

    def build(self):
        model = copy.deepcopy(self.base)
        nodes = list(walk(model["root"]))
        moves = {}
        for s in self.steps:
            op = s["op"]
            if op == "add":
                nodes += self._add(model, nodes, s)
                continue
            n = nodes[s["node"]]
            if op == "show":
                n["active"] = bool(s["on"])
            elif op == "remove":
                n["removed"] = True
            elif op == "move":
                moves[s["node"]] = as_matrix(s["matrix"])
            elif op == "colour":
                self._colour(model, n, s)
            elif op == "material":
                n["material"] = s["material"]
        self._moves = {i: list(flat(m)) for i, m in moves.items()}
        for i, m in moves.items():
            n = nodes[i]
            if n["cls"] == 1:
                n["matrix"] = flat(m)
            else:
                bake(n, m)
        return model

    def final_model(self):
        """The model to write into the car: removed parts dropped."""
        model = copy.deepcopy(self.model())

        def prune(n):
            n["children"] = [prune(c) for c in n["children"] if not c.get("removed")]
            return n
        prune(model["root"])
        return model

    def node(self, i):
        nodes = list(walk(self.model()["root"]))
        if not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < len(nodes):
            raise EditError("unknown part")
        return nodes[i]

    # --- steps -------------------------------------------------------------

    def apply(self, step):
        """Check one step, add it, and rebuild. Returns whether the preview must be reloaded."""
        if not isinstance(step, dict):
            raise EditError("a step must be an object")
        op = step.get("op")
        if op == "add":
            clean = self._check_add(step)
        else:
            n = self.node(step.get("node"))
            clean = {"op": op, "node": step["node"]}
            if op == "show":
                clean["on"] = bool(step.get("on"))
            elif op == "remove":
                if step["node"] == 0:
                    raise EditError("the whole car can't be removed")
            elif op == "move":
                if n["cls"] == 3:
                    raise EditError("rigged parts (like the driver) move with their bones and can't be moved here")
                if step["node"] == 0:
                    raise EditError("move the parts, not the whole car")
                as_matrix(step.get("matrix"))
                clean["matrix"] = [float(v) for v in step["matrix"]]
            elif op == "colour":
                if n["cls"] == 1:
                    raise EditError("pick a part with a surface (not a group) to recolour")
                if not isinstance(step.get("colour"), str) or not HEX.match(step["colour"]):
                    raise EditError("the colour must look like #rrggbb")
                clean.update(colour=step["colour"].lower(), mode="solid" if step.get("mode") == "solid" else "tint",
                             scope="material" if step.get("scope") == "material" else "part")
            elif op == "material":
                if n["cls"] == 1:
                    raise EditError("pick a part with a surface (not a group)")
                mid = step.get("material")
                if not isinstance(mid, int) or isinstance(mid, bool) or not 0 <= mid < len(self.model()["materials"]):
                    raise EditError("unknown material")
                clean["material"] = mid
            else:
                raise EditError("unknown step")
        if len(self.steps) >= MAX_STEPS:
            raise EditError("too many changes in one go: save first")
        self.steps.append(clean)
        try:
            reload = self._rebuilt(reload=op in ("add", "colour", "material"))
        except Exception:
            self.steps.pop()
            self._model = None
            raise
        self.redo = []
        return reload

    def undo(self):
        if not self.steps:
            raise EditError("nothing to undo")
        self.redo.append(self.steps.pop())
        return self._rebuilt(reload=True)

    def redo_step(self):
        if not self.redo:
            raise EditError("nothing to redo")
        self.steps.append(self.redo.pop())
        return self._rebuilt(reload=True)

    def _rebuilt(self, reload):
        self._model = None
        self.model()
        self.version += 1
        return reload

    # --- recolouring -------------------------------------------------------

    def texture_bytes(self, model, name):
        return (self.skin_files.get(name.lower()) or self.skin_files.get(Path(name).stem.lower())
                or model["textures"].get(name))

    def _colour(self, model, n, s):
        mats = model["materials"]
        if not 0 <= n.get("material", -1) < len(mats):
            raise EditError("this part has no material")
        mat = mats[n["material"]]
        rgb = tuple(int(s["colour"][k:k + 2], 16) for k in (1, 3, 5))
        img = None
        if s["mode"] == "tint" and "txDiffuse" in mat["maps"]:
            data = self.texture_bytes(model, mat["maps"]["txDiffuse"])
            try:
                img = decode_image(data).convert("RGBA") if data else None
            except Exception:
                img = None
        if img is not None:
            if max(img.size) > MAX_TINT_SIDE:
                img.thumbnail((MAX_TINT_SIDE, MAX_TINT_SIDE), Image.LANCZOS)
            a = np.asarray(img, np.float32)
            a[..., :3] *= np.array(rgb, np.float32) / 255.0
            img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
        else:
            img = Image.new("RGBA", (4, 4), rgb + (255,))
        stem = re.sub(r"[^A-Za-z0-9_]", "_", mat["name"])[:40] or "part"
        tex = "kn5g_%s_%s.dds" % (stem, s["colour"][1:])
        k = 2
        while tex in model["textures"]:
            tex = "kn5g_%s_%s_%d.dds" % (stem, s["colour"][1:], k)
            k += 1
        kn5write.add_texture(model, tex, dds_bytes(img))
        if s["scope"] == "part":
            mat = copy.deepcopy(mat)
            mat["name"] = "%s_%s" % (mat["name"], s["colour"][1:])
            mat.get("raw", {}).pop("name", None)
            mats.append(mat)
            n["material"] = len(mats) - 1
        set_map(mat, "txDiffuse", tex)

    # --- parts from elsewhere -----------------------------------------------

    def add_source(self, key, name, model):
        if model.get("protected"):
            raise EditError("that model is protected by its author, so its parts can't be used")
        if key not in self.sources and len(self.sources) >= MAX_SOURCES:
            oldest = next(iter(self.sources))
            if any(s.get("source") == oldest for s in self.steps):
                raise EditError("too many part sources open: save first")
            del self.sources[oldest]
        self.sources[key] = {"name": name, "model": model}
        return key

    def _check_add(self, step):
        src = self.sources.get(step.get("source"))
        if src is None:
            raise EditError("open the car or file with the part first")
        nodes = list(walk(src["model"]["root"]))
        i = step.get("node")
        if not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < len(nodes):
            raise EditError("unknown part in %s" % src["name"])
        clean = {"op": "add", "source": step["source"], "node": i, "replace": None, "fit": bool(step.get("fit"))}
        if step.get("replace") is not None:
            self.node(step["replace"])
            if step["replace"] == 0:
                raise EditError("the whole car can't be replaced")
            clean["replace"] = step["replace"]
        return clean

    def _add(self, model, nodes, s):
        src_model = self.sources[s["source"]]["model"]
        src_nodes = list(walk(src_model["root"]))
        part = copy.deepcopy(src_nodes[s["node"]])
        src_parent = parent_worlds(src_model["root"])[id(src_nodes[s["node"]])]
        dropped = [n for n in walk(part) if n["cls"] == 3]
        if dropped:  # rigged meshes belong to the other model's bones
            def strip(n):
                n["children"] = [strip(c) for c in n["children"] if c["cls"] != 3]
                return n
            strip(part)
        # materials and textures come along (renamed if a different one has the same name)
        tex_names = {}
        used = sorted({n["material"] for n in walk(part) if n["cls"] != 1})
        remap = {}
        for mid in used:
            if not 0 <= mid < len(src_model["materials"]):
                continue
            mat = copy.deepcopy(src_model["materials"][mid])
            for sampler, tex in list(mat["maps"].items()):
                if tex not in tex_names:
                    tex_names[tex] = self._copy_texture(model, src_model, tex)
                if tex_names[tex] != tex:
                    set_map(mat, sampler, tex_names[tex])
            model["materials"].append(mat)
            remap[mid] = len(model["materials"]) - 1
        for n in walk(part):
            if n["cls"] != 1:
                n["material"] = remap.get(n["material"], 0)
            n.pop("removed", None)
        root = model["root"]
        root_world = local_matrix(root)
        place = src_parent
        if s.get("fit_nodes"):  # made by builder.py: fit into the space of several parts
            worlds = parent_worlds(root)
            boxes = [importer.bounds(nodes[i], worlds[id(nodes[i])]) for i in s["fit_nodes"]]
            dst = (np.min([b[0] for b in boxes], 0), np.max([b[1] for b in boxes], 0))
            place = as_matrix(importer.fit_matrix(importer.bounds(part, src_parent), dst)) @ src_parent
        elif s["replace"] is not None:
            old = nodes[s["replace"]]
            old["removed"] = True
            if s["fit"]:
                worlds = parent_worlds(root)
                dst = importer.bounds(old, worlds[id(old)])
                srcb = importer.bounds(part, src_parent)
                place = as_matrix(importer.fit_matrix(srcb, dst)) @ src_parent
        names = {n["name"] for n in nodes}
        name = "%s_added" % part["name"] if part["name"] in names else part["name"]
        wrapper = kn5write.new_dummy(name[:80], flat(np.linalg.inv(root_world) @ place), [part])
        root["children"].append(wrapper)
        return list(walk(wrapper))

    def _copy_texture(self, model, src_model, tex):
        data = src_model["textures"].get(tex)
        if data is None:
            return tex
        if model["textures"].get(tex) == data:
            return tex
        name, k = tex, 2
        while name in model["textures"]:
            name = "%s_%d%s" % (Path(tex).stem, k, Path(tex).suffix)
            k += 1
        kn5write.add_texture(model, name, data)
        return name

    # --- what the page shows ----------------------------------------------

    def tree(self, model=None):
        own = model is None
        model = model or self.model()
        mats = model["materials"]
        out = []

        def visit(n, depth, removed):
            removed = removed or bool(n.get("removed"))
            item = {"i": len(out), "name": n["name"], "depth": depth, "group": n["cls"] == 1,
                    "active": bool(n.get("active", True)), "removed": removed, "rigged": n["cls"] == 3}
            if n["cls"] != 1:
                m = n.get("material", -1)
                item["material"] = mats[m]["name"] if 0 <= m < len(mats) else ""
                item["verts"] = len(n["verts"])
                if own:  # the move already baked into the preview's shape
                    item["baked"] = self._moves.get(len(out), list(kn5write.IDENTITY))
            else:
                item["matrix"] = list(n.get("matrix", kn5write.IDENTITY))
            out.append(item)
            for c in n["children"]:
                visit(c, depth + 1, removed)
        visit(model["root"], 0, False)
        return out

    def state(self):
        model = self.model()
        return {"session": self.id, "version": self.version, "parts": self.tree(),
                "materials": [{"i": i, "name": m["name"]} for i, m in enumerate(model["materials"])],
                "steps": len(self.steps), "canUndo": bool(self.steps), "canRedo": bool(self.redo),
                "sources": [{"key": k, "name": v["name"]} for k, v in self.sources.items()]}

    def preview(self, out_path):
        """A light .glb of the working model; removed and hidden parts have no shape in it."""
        model = copy.deepcopy(self.model())
        for n in walk(model["root"]):
            if n["cls"] != 1 and (n.get("removed") or not n.get("active", True)):
                n["verts"] = np.zeros((0, 11), np.float32)
                n["indices"] = np.zeros(0, np.uint16)
        opts = PRESETS["preview"]
        Converter(model, self.skin_files, log=lambda *a: None, max_texture=opts["max_texture"]).run(out_path)


def set_map(mat, sampler, tex):
    mat["maps"][sampler] = tex
    for mp in mat.get("map_list", []):
        if mp["sampler"] == sampler:
            mp["texture"] = tex
            break
    else:
        mat.setdefault("map_list", []).append({"sampler": sampler, "slot": len(mat.get("map_list", [])), "texture": tex})


class Sessions:
    """The open parts sessions (kept in memory; the oldest is dropped when there are too many)."""

    def __init__(self):
        self.items = {}

    def start(self, car_dir, car_id, skin=None):
        s = Session(car_dir, car_id, skin)
        if len(self.items) >= MAX_SESSIONS:
            del self.items[min(self.items, key=lambda k: self.items[k].used)]
        self.items[s.id] = s
        return s

    def get(self, sid, car_id):
        s = self.items.get(sid)
        if s is None or s.car_id != car_id:
            raise EditError("this editing session has ended; open the parts editor again")
        s.used = time.time()
        return s

    def close(self, sid):
        self.items.pop(sid, None)
