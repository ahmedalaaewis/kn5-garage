"""Export presets and the converter: kn5 model -> glTF binary (.glb), OBJ, STL (or FBX via Blender)."""

import os
import re
import shutil
import struct
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from . import carfiles
from .glb import GlbBuilder
from .kn5 import Kn5Error, decompose, node_matrix, read_kn5
from .textures import image_for_gltf, image_size, metal_rough_png

PAINT_NAME = re.compile(r"paint|skin|livery|carbody|body_?main|ext_?body|^body$|^ext_?main", re.I)
LIGHT_NAME = re.compile(r"light|lamp|headl|taill|brake_?l|indicator|blinker|signal|emissive|\bled\b|_led", re.I)
GLASS_NAME = re.compile(r"glass|window|windscreen|windshield|lexan|lens", re.I)
CHROME_NAME = re.compile(r"chrome|chrom_|polish|mirror_?glass|^mirror", re.I)
CARBON_NAME = re.compile(r"carbon|^cf_|_cf$|_cf_", re.I)

# Export presets: which model (LOD level), max texture size (None = full, 0 = none), format, animations
PRESETS = {
    "render": {"label": "Blender render (full quality)", "lod": 0, "max_texture": None, "format": "glb",
               "animations": True},
    "game": {"label": "Game-ready (low-poly LOD, 1K textures)", "lod": 1, "max_texture": 1024, "format": "glb",
             "animations": True},
    "print": {"label": "3D printing (one mesh, STL in mm)", "lod": 0, "max_texture": 0, "format": "stl",
              "animations": False},
    "obj": {"label": "OBJ + textures (older 3D tools)", "lod": 0, "max_texture": None, "format": "obj",
            "animations": False},
    "fbx": {"label": "FBX via Blender (Unity, Unreal)", "lod": 0, "max_texture": None, "format": "fbx",
            "animations": True},
    "preview": {"label": "Quick preview", "lod": 0, "max_texture": 512, "format": "glb", "animations": False},
}
FORMAT_EXT = {"glb": ".glb", "stl": ".stl", "obj": ".obj", "fbx": ".fbx"}
TEXTURE_SIZES = (256, 512, 1024, 2048, 4096)

TEXTURE_WORKERS = max(1, min(8, (os.cpu_count() or 2)))


def is_paint_material(mat):
    """Best guess at the car's paint: AC modders usually name it after paint / skin / body."""
    return bool(PAINT_NAME.search(mat["name"]) or PAINT_NAME.search(mat["maps"].get("txDiffuse", "")))


def material_kind(mat):
    """'light', 'glass', 'chrome', 'carbon' or None, guessed from the material and shader names."""
    name, shader = mat["name"], mat.get("shader", "")
    if LIGHT_NAME.search(name):
        return "light"
    if mat.get("blend") == 1 and (GLASS_NAME.search(name) or re.search(r"windscreen|glass", shader, re.I)):
        return "glass"
    if CHROME_NAME.search(name):
        return "chrome"
    if CARBON_NAME.search(name):
        return "carbon"
    return None


def fix_winding(pos, nrm, tri):
    """Make triangle winding agree with the stored normals (glTF front = CCW)."""
    p = pos.astype(np.float64)
    face = np.cross(p[tri[:, 1]] - p[tri[:, 0]], p[tri[:, 2]] - p[tri[:, 0]])
    vn = nrm[tri[:, 0]] + nrm[tri[:, 1]] + nrm[tri[:, 2]]
    d = np.einsum("ij,ij->i", face, vn)
    if (d < 0).sum() > (d > 0).sum():
        tri = tri[:, [0, 2, 1]]
    return tri


def walk(node):
    yield node
    for c in node["children"]:
        yield from walk(c)


def count_meshes(node):
    return sum(1 for n in walk(node) if n["cls"] != 1)


def hex_rgb(h):
    h = str(h).lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        raise Kn5Error("paint colour must look like #c8102e")
    return [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]


def srgb_to_linear(c):
    return [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in c]


def material_shading(m, paint):
    """(spec exponent, fresnel, roughness factor, metal scale) for an AC material."""
    props = m["props"]
    spec_exp = props.get("ksSpecularEXP", (0.0, None))[0]
    fresnel = props.get("fresnelMaxLevel", (0.0, None))[0]
    roughness = float(np.clip(np.sqrt(2.0 / (max(spec_exp, 0.0) + 2.0)), 0.05, 1.0))
    metal_scale = 0.3 if paint else float(np.clip(fresnel, 0.0, 1.0))
    return spec_exp, fresnel, roughness, metal_scale


def safe_file_part(name):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "texture"


class Converter:
    def __init__(self, kn5, skin_textures, flip_normal_green=True, log=print, progress=None,
                 max_texture=None, paint=None, paint_mode="tint", animations=None, stl=False,
                 paint_names=None, workers=TEXTURE_WORKERS, jpeg_quality=None, lights_on=False,
                 fmt=None, rig=True):
        self.kn5 = kn5
        self.skin = skin_textures  # lowercase name (and name without extension) -> bytes
        self.flip_green = flip_normal_green
        self.log = log
        self.progress = progress  # progress(fraction, message) or None
        self.max_texture = max_texture  # None = full size, 0 = no textures at all
        self.paint_srgb = hex_rgb(paint) if paint else None  # OBJ / MTL colours are sRGB
        self.paint = srgb_to_linear(self.paint_srgb) if paint else None  # glTF factors are linear
        self.paint_mode = paint_mode  # "tint" multiplies the livery, "solid" replaces it
        # material names chosen as paint by the user; None = guess from the names
        self.paint_names = {n.lower() for n in paint_names} if paint_names else None
        self.animations = animations or {}  # {name: tracks} from read_ksanim
        self.format = fmt or ("stl" if stl else "glb")
        self.stl = self.format == "stl"
        self.obj = self.format == "obj"
        self.workers = workers
        self.jpeg_quality = jpeg_quality  # colour textures without alpha as JPEG (smaller files)
        self.lights_on = lights_on
        self.rig = rig
        self.b = GlbBuilder()
        self.tex_cache = {}
        self.encoded = {}  # texture key -> (bytes, mime) | Exception, filled by prefetch()
        self.mat_cache = {}
        self.meshes_total = max(count_meshes(kn5["root"]), 1)
        self.meshes_done = 0
        self.node_index = {}   # dummy name -> glTF node index
        self.node_world = {}   # dummy name -> world matrix (bind pose)
        self.skinned = []      # (glTF node index, kn5 node, mesh world matrix, primitive)
        self.triangles = []    # world-space triangles, for STL
        self.parts = []        # world-space parts, for OBJ
        self.obj_textures = {}  # texture key -> file name, for OBJ
        self.report = {"triangles": 0, "meshes": 0, "missing_textures": set(), "failed_textures": set(),
                       "flipped_meshes": 0, "degenerate": 0, "no_material": 0, "big_textures": set(),
                       "paint_materials": [], "animations": [], "skipped_meshes": 0, "kinds": {},
                       "rigged": [], "static_skinned": 0}

    @property
    def geometry_only(self):
        return self.stl or self.obj

    # --- textures --------------------------------------------------------

    def is_paint(self, m):
        if self.paint_names is not None:
            return m["name"].lower() in self.paint_names
        return is_paint_material(m)

    def texture_bytes(self, name):
        return (self.skin.get(name.lower()) or self.skin.get(Path(name).stem.lower())
                or self.kn5["textures"].get(name))

    def encode_texture(self, data, normal_map):
        jpeg = None if normal_map else self.jpeg_quality
        return image_for_gltf(data, flip_green=normal_map and self.flip_green, max_size=self.max_texture,
                              jpeg_quality=jpeg)

    def texture_jobs(self):
        """Every texture the used materials will ask for, as (key, encode function)."""
        if self.stl or self.max_texture == 0:
            return []
        used = {n["material"] for n in walk(self.kn5["root"]) if n["cls"] != 1}
        mats = self.kn5["materials"]
        jobs = {}
        for mid in sorted(i for i in used if isinstance(i, int) and 0 <= i < len(mats)):
            m = mats[mid]
            maps = m["maps"]
            spec_exp, _, _, metal_scale = material_shading(m, self.is_paint(m))
            for slot, normal in (("txDiffuse", False), ("txNormal", True)):
                if slot in maps:
                    key = (maps[slot].lower(), normal)
                    data = self.texture_bytes(maps[slot])
                    if data and key not in jobs:
                        jobs[key] = lambda d=data, n=normal: self.encode_texture(d, n)
            if "txMaps" in maps and not self.obj:
                key = ("maps", maps["txMaps"].lower(), round(max(spec_exp, 1.0), 2), round(metal_scale, 3))
                data = self.texture_bytes(maps["txMaps"])
                if data and key not in jobs:
                    jobs[key] = lambda d=data, e=max(spec_exp, 1.0), s=metal_scale: (
                        metal_rough_png(d, e, s, self.max_texture), "image/png")
        return list(jobs.items())

    def prefetch(self):
        """Decode and re-encode all textures in parallel; they're the slow part of a big car."""
        jobs = self.texture_jobs()
        if not jobs:
            return

        def run(fn):
            try:
                return fn()
            except Exception as e:  # reported when the texture is used
                return e

        if self.workers > 1 and len(jobs) > 1:
            with ThreadPoolExecutor(self.workers) as pool:
                results = list(pool.map(run, [fn for _, fn in jobs]))
        else:
            results = [run(fn) for _, fn in jobs]
        self.encoded = {key: r for (key, _), r in zip(jobs, results)}

    def encode(self, key, fn):
        if key in self.encoded:
            r = self.encoded.pop(key)
            if isinstance(r, Exception):
                raise r
            return r
        return fn()

    def texture(self, name, normal_map=False):
        """glTF texture index (or, for OBJ, the texture's file name), None if unavailable."""
        key = (name.lower(), normal_map)
        if key in self.tex_cache:
            return self.tex_cache[key]
        idx = None
        if self.max_texture == 0:
            self.tex_cache[key] = None
            return None
        data = self.texture_bytes(name)
        if data:
            try:
                img, mime = self.encode(key, lambda: self.encode_texture(data, normal_map))
                if max(image_size(img)) > 4096:
                    self.report["big_textures"].add(name)
                if self.obj:
                    idx = "%s%s%s" % (safe_file_part(Path(name).stem), "_nm" if normal_map else "",
                                      ".jpg" if mime == "image/jpeg" else ".png")
                    self.obj_textures[idx] = img
                else:
                    idx = self.b.image(name, img, mime)
            except Exception as e:
                self.report["failed_textures"].add(name)
                self.log("    ! could not convert texture %s: %s" % (name, e))
        else:
            self.report["missing_textures"].add(name)
        self.tex_cache[key] = idx
        return idx

    def maps_texture(self, name, spec_exp, metal_scale):
        """AC's txMaps (R specular, G gloss, B reflection) -> glTF metallic-roughness
        (G roughness, B metallic)."""
        key = ("maps", name.lower(), round(spec_exp, 2), round(metal_scale, 3))
        if key in self.tex_cache:
            return self.tex_cache[key]
        idx = None
        data = self.texture_bytes(name) if self.max_texture != 0 else None
        if data:
            try:
                png, mime = self.encode(key, lambda: (metal_rough_png(data, spec_exp, metal_scale,
                                                                      self.max_texture), "image/png"))
                idx = self.b.image(Path(name).stem + "_metal_rough", png, mime)
            except Exception as e:
                self.report["failed_textures"].add(name)
                self.log("    ! could not convert maps texture %s: %s" % (name, e))
        self.tex_cache[key] = idx
        return idx

    # --- materials -------------------------------------------------------

    def material(self, mat_id):
        if mat_id in self.mat_cache:
            return self.mat_cache[mat_id]
        mats = self.kn5["materials"]
        if not 0 <= mat_id < len(mats):
            self.mat_cache[mat_id] = None
            return None
        m = mats[mat_id]
        if self.obj:
            idx = self.mat_cache[mat_id] = self.obj_material(m)
            return idx
        maps = m["maps"]
        paint = self.is_paint(m)
        kind = None if paint else material_kind(m)
        spec_exp, fresnel, roughness, metal_scale = material_shading(m, paint)
        pbr = {"metallicFactor": 0.0, "roughnessFactor": roughness}
        out = {"name": m["name"], "pbrMetallicRoughness": pbr,
               "extras": {"kn5_shader": m["shader"], "kn5_paint": paint, "kn5_kind": kind}}
        ext = {}

        if "txDiffuse" in maps:
            t = self.texture(maps["txDiffuse"])
            if t is not None:
                pbr["baseColorTexture"] = {"index": t}
        if "txNormal" in maps:
            t = self.texture(maps["txNormal"], normal_map=True)
            if t is not None:
                out["normalTexture"] = {"index": t}
        if "txMaps" in maps:
            t = self.maps_texture(maps["txMaps"], max(spec_exp, 1.0), metal_scale)
            if t is not None:
                pbr["metallicRoughnessTexture"] = {"index": t}
                pbr["metallicFactor"] = 1.0
                pbr["roughnessFactor"] = 1.0
        if paint:
            # car paint: a glossy clearcoat over the base colour
            ext["KHR_materials_clearcoat"] = {
                "clearcoatFactor": float(np.clip(fresnel if fresnel > 0 else 1.0, 0.3, 1.0)),
                "clearcoatRoughnessFactor": 0.05}
            self.report["paint_materials"].append(m["name"])
            if self.paint:
                if self.paint_mode == "solid":
                    pbr.pop("baseColorTexture", None)
                pbr["baseColorFactor"] = self.paint + [1.0]
        elif kind == "glass":
            pbr["roughnessFactor"] = min(pbr["roughnessFactor"], 0.05)
            pbr["metallicFactor"] = 0.0
            ext["KHR_materials_transmission"] = {"transmissionFactor": 0.85}
            ext["KHR_materials_ior"] = {"ior": 1.5}
        elif kind == "chrome" and "metallicRoughnessTexture" not in pbr:
            pbr["metallicFactor"] = 1.0
            pbr["roughnessFactor"] = 0.06
        elif kind == "carbon":
            ext["KHR_materials_clearcoat"] = {"clearcoatFactor": 1.0, "clearcoatRoughnessFactor": 0.03}
        if kind:
            self.report["kinds"].setdefault(kind, []).append(m["name"])

        emissive = m["props"].get("ksEmissive", (0.0, (0, 0, 0)))[1]
        peak = max([float(v) for v in emissive] + [0.0])
        if peak > 0 and np.all(np.isfinite(emissive)):
            out["emissiveFactor"] = [float(np.clip(v / max(peak, 1.0), 0, 1)) for v in emissive]
            if peak > 1:  # AC lights are often brighter than 1: keep the glow
                ext["KHR_materials_emissive_strength"] = {"emissiveStrength": float(min(peak, 100.0))}
        if kind == "light" and self.lights_on:
            out["emissiveFactor"] = out.get("emissiveFactor") if peak > 0 else [1.0, 1.0, 1.0]
            if "baseColorTexture" in pbr:
                out["emissiveTexture"] = dict(pbr["baseColorTexture"])
            ext["KHR_materials_emissive_strength"] = {"emissiveStrength": max(4.0, peak)}

        if m["blend"] == 1:
            out["alphaMode"] = "BLEND"
        elif m["alpha_tested"]:
            out["alphaMode"] = "MASK"
            out["alphaCutoff"] = 0.5

        if ext:
            out["extensions"] = ext
            for name in ext:
                self.b.use_extension(name)
        self.b.gltf["materials"].append(out)
        idx = len(self.b.gltf["materials"]) - 1
        self.mat_cache[mat_id] = idx
        return idx

    def obj_material(self, m):
        """A Wavefront .mtl entry for an AC material."""
        paint = self.is_paint(m)
        maps = m["maps"]
        mat = {"name": safe_file_part(m["name"]).replace(" ", "_"), "kd": [1.0, 1.0, 1.0], "d": 1.0}
        if "txDiffuse" in maps:
            mat["map_kd"] = self.texture(maps["txDiffuse"])
        if "txNormal" in maps:
            mat["bump"] = self.texture(maps["txNormal"], normal_map=True)
        if paint:
            self.report["paint_materials"].append(m["name"])
            if self.paint_srgb:
                mat["kd"] = [round(v, 4) for v in self.paint_srgb]
                if self.paint_mode == "solid":
                    mat.pop("map_kd", None)
        if m["blend"] == 1:
            mat["d"] = 0.35
        return mat

    # --- meshes and nodes ------------------------------------------------

    def mesh(self, node, world=None):
        v = node["verts"]
        idx = node["indices"]
        idx = idx[: len(idx) - len(idx) % 3]
        if len(v) == 0 or len(idx) == 0 or idx.max() >= len(v) or not np.all(np.isfinite(v[:, :8])):
            self.report["skipped_meshes"] += 1
            return None
        pos = v[:, 0:3].copy()
        nrm = v[:, 3:6].astype(np.float64)
        uv = v[:, 6:8].copy()
        length = np.linalg.norm(nrm, axis=1)
        bad = length < 1e-8
        nrm[bad] = (0, 1, 0)
        length[bad] = 1
        nrm = (nrm / length[:, None]).astype(np.float32)

        tri0 = idx.reshape(-1, 3).astype(np.int64)
        tri = fix_winding(pos, nrm, tri0)
        if tri is not tri0:
            self.report["flipped_meshes"] += 1
        p = pos.astype(np.float64)
        area = np.linalg.norm(np.cross(p[tri[:, 1]] - p[tri[:, 0]], p[tri[:, 2]] - p[tri[:, 0]]), axis=1)
        self.report["degenerate"] += int((area < 1e-12).sum())
        self.report["triangles"] += len(tri)
        self.report["meshes"] += 1
        if self.stl:
            wp = np.c_[p, np.ones(len(p))] @ world.T
            self.triangles.append(wp[:, :3][tri])
            return None
        if self.obj:
            wp = (np.c_[p, np.ones(len(p))] @ world.T)[:, :3]
            lin = world[:3, :3]
            try:
                nm = np.linalg.inv(lin).T
            except np.linalg.LinAlgError:
                nm = lin
            wn = nrm.astype(np.float64) @ nm.T
            wn /= np.maximum(np.linalg.norm(wn, axis=1, keepdims=True), 1e-12)
            self.parts.append({"name": node["name"], "pos": wp, "nrm": wn, "uv": uv, "tri": tri,
                               "material": self.material(node["material"])})
            return None

        prim = {
            "attributes": {
                "POSITION": self.b.accessor(pos, "VEC3", 34962, minmax=True),
                "NORMAL": self.b.accessor(nrm, "VEC3", 34962),
                "TEXCOORD_0": self.b.accessor(uv, "VEC2", 34962),
            },
            "indices": self.b.accessor(tri.reshape(-1).astype(np.uint16), "SCALAR", 34963),
        }
        mat = self.material(node["material"])
        if mat is not None:
            prim["material"] = mat
        else:
            self.report["no_material"] += 1
        self.b.gltf["meshes"].append({"name": node["name"], "primitives": [prim]})
        return len(self.b.gltf["meshes"]) - 1

    def node(self, n, parent=np.eye(4)):
        out = {"name": n["name"], "extras": {"kn5_active": n["active"]}}
        world = parent
        if n["cls"] == 1:
            local = np.array(n["matrix"], dtype=np.float64).reshape(4, 4).T
            if not np.all(np.isfinite(local)):
                local = np.eye(4)
            world = parent @ local
            if not np.allclose(local, np.eye(4)):
                trs = decompose(n["matrix"])
                if trs is None:  # sheared: keep the matrix (can't be animated)
                    out["matrix"] = node_matrix(n["matrix"]).tolist()
                else:  # translation / rotation / scale: what Blender shows as the part's pivot
                    t, r, s = trs
                    if np.any(np.abs(t) > 1e-9):
                        out["translation"] = t.tolist()
                    if not np.allclose(r, [0, 0, 0, 1]):
                        out["rotation"] = r.tolist()
                    if not np.allclose(s, 1):
                        out["scale"] = s.tolist()
        else:
            out["extras"]["kn5_visible"] = n["visible"]
            mesh = self.mesh(n, world)
            if mesh is not None:
                out["mesh"] = mesh
            self.meshes_done += 1
            if self.progress:
                self.progress(self.meshes_done / self.meshes_total, "building %s" % n["name"])
        nodes = self.b.gltf["nodes"]
        nodes.append(out)
        my_index = len(nodes) - 1
        if n["cls"] == 1:
            self.node_index.setdefault(n["name"], my_index)
            self.node_world.setdefault(n["name"], world)
        elif n["cls"] == 3 and "mesh" in out:
            self.skinned.append((my_index, n, world))
        children = [self.node(c, world) for c in n["children"]]
        if children:
            out["children"] = children
        return my_index

    # --- driver rig ------------------------------------------------------

    def add_skins(self):
        """Skinned meshes (the driver) become a real glTF skin when their bones are nodes of this
        model and the bind pose matches; otherwise they stay static meshes (always correct)."""
        g = self.b.gltf
        for node_idx, n, world in self.skinned:
            names = [b[0] for b in n["bones"]]
            ok = self.rig and names and all(b in self.node_index for b in names)
            if ok:
                ibms = [np.array(m, dtype=np.float64).reshape(4, 4).T for _, m in n["bones"]]
                ok = all(np.all(np.isfinite(ibm)) and np.allclose(self.node_world[b] @ ibm, world, atol=1e-3)
                         for b, ibm in zip(names, ibms))
            if not ok:
                self.report["static_skinned"] += 1
                continue
            joints = np.nan_to_num(np.asarray(n["joints"], np.float64)).round().astype(np.int64)
            weights = np.clip(np.nan_to_num(np.asarray(n["weights"], np.float64)), 0, None)
            weights[(joints < 0) | (joints >= len(names))] = 0
            joints = np.clip(joints, 0, len(names) - 1)
            total = weights.sum(axis=1, keepdims=True)
            empty = total[:, 0] <= 1e-9
            weights[empty] = [1, 0, 0, 0]
            joints[empty] = 0
            weights /= np.maximum(weights.sum(axis=1, keepdims=True), 1e-9)
            prim = g["meshes"][g["nodes"][node_idx]["mesh"]]["primitives"][0]
            prim["attributes"]["JOINTS_0"] = self.b.accessor(joints.astype(np.uint16), "VEC4", 34962)
            prim["attributes"]["WEIGHTS_0"] = self.b.accessor(weights.astype(np.float32), "VEC4", 34962)
            ibm = np.array([node_matrix(m) for _, m in n["bones"]], np.float32)
            g.setdefault("skins", []).append({
                "name": n["name"], "joints": [self.node_index[b] for b in names],
                "inverseBindMatrices": self.b.accessor(ibm, "MAT4", None)})
            g["nodes"][node_idx]["skin"] = len(g["skins"]) - 1
            self.report["rigged"].append("%s (%d bones)" % (n["name"], len(names)))

    def add_animations(self, fps=30.0):
        g = self.b.gltf
        for anim_name, tracks in sorted(self.animations.items()):
            channels, samplers = [], []
            for node_name, tr in tracks.items():
                target = self.node_index.get(node_name)
                if target is None or len(tr["t"]) == 0:
                    continue
                node = g["nodes"][target]
                if "matrix" in node:
                    continue
                times = (np.arange(len(tr["t"])) / fps).astype(np.float32)
                t_acc = self.b.accessor(times, "SCALAR", None, minmax=True)
                g["accessors"][t_acc]["min"] = [float(times.min())]
                g["accessors"][t_acc]["max"] = [float(times.max())]
                rot = np.asarray(tr["r"], np.float64)
                rot = (rot / np.maximum(np.linalg.norm(rot, axis=1, keepdims=True), 1e-12)).astype(np.float32)
                for path, values, kind in (("translation", tr["t"], "VEC3"), ("rotation", rot, "VEC4"),
                                           ("scale", tr["s"], "VEC3")):
                    samplers.append({"input": t_acc, "interpolation": "LINEAR",
                                     "output": self.b.accessor(np.asarray(values, np.float32), kind, None)})
                    channels.append({"sampler": len(samplers) - 1, "target": {"node": target, "path": path}})
            if channels:
                g.setdefault("animations", []).append({"name": anim_name, "channels": channels, "samplers": samplers})
                self.report["animations"].append(anim_name)

    # --- writing ---------------------------------------------------------

    def run(self, out_path, final_path=None):
        """Write to out_path; final_path is the name it will get (OBJ names its .mtl after it)."""
        self.prefetch()
        self.b.gltf["scenes"][0]["nodes"].append(self.node(self.kn5["root"]))
        if self.stl:
            write_stl(out_path, np.concatenate(self.triangles) if self.triangles else np.zeros((0, 3, 3)))
        elif self.obj:
            write_obj(out_path, Path(final_path or out_path), self.parts, self.obj_textures)
        else:
            self.add_skins()
            self.add_animations()
            self.b.write(out_path)
        return self.checker_report()

    def checker_report(self):
        r = self.report
        items = []

        def add(level, text):
            items.append({"level": level, "text": text})

        add("ok", "%s triangles in %d parts" % ("{:,}".format(r["triangles"]), r["meshes"]))
        if self.kn5.get("protected"):
            add("warn", "Protected model: textures, shading and UVs come out wrong (shape only)")
        if r["missing_textures"]:
            add("warn", "%d texture(s) not found: %s" % (len(r["missing_textures"]),
                                                         ", ".join(sorted(r["missing_textures"])[:5])))
        if r["failed_textures"]:
            add("warn", "%d texture(s) couldn't be decoded: %s" % (len(r["failed_textures"]),
                                                                   ", ".join(sorted(r["failed_textures"])[:5])))
        if r["flipped_meshes"]:
            add("info", "Fixed inside-out faces on %d part(s)" % r["flipped_meshes"])
        if r["degenerate"]:
            add("info", "%d zero-area triangle(s) (harmless, Blender's Merge by Distance removes them)"
                % r["degenerate"])
        if r["no_material"]:
            add("warn", "%d part(s) have no material" % r["no_material"])
        if r["skipped_meshes"]:
            add("warn", "%d empty or broken part(s) skipped" % r["skipped_meshes"])
        if r["big_textures"]:
            add("info", "%d texture(s) larger than 4K; the Game-ready preset shrinks them" % len(r["big_textures"]))
        if not self.stl:
            if r["paint_materials"]:
                add("ok", "Car paint found: %s" % ", ".join(r["paint_materials"][:4]))
            elif self.paint_names is not None:
                add("warn", "None of the chosen paint materials are in this model")
            else:
                add("info", "No paint material recognised; pick one in the 3D viewer to recolour it")
            labels = {"light": "Lights", "glass": "Glass", "chrome": "Chrome", "carbon": "Carbon"}
            for kind, names in sorted(r["kinds"].items()):
                add("ok", "%s: %s" % (labels[kind], ", ".join(names[:4]) + ("…" if len(names) > 4 else "")))
            if r["animations"]:
                add("ok", "Animations: %s" % ", ".join(r["animations"]))
            if r["rigged"]:
                add("ok", "Rigged: %s" % ", ".join(r["rigged"]))
            if r["static_skinned"]:
                add("info", "%d skinned part(s) (the driver) exported as static meshes; their bones aren't in "
                            "this model" % r["static_skinned"])
        if r["triangles"] > 400_000:
            add("info", "High polygon count; use the Game-ready preset for a lighter model")
        if not any(i["level"] == "warn" for i in items):
            items.insert(0, {"level": "ok", "text": "No problems found"})
        return items


def write_stl(path, tris):
    """Binary STL in millimetres, as slicers expect. glTF Y-up becomes STL Z-up."""
    t = np.asarray(tris, np.float64) * 1000.0
    t = t[:, :, [0, 2, 1]] * [1, -1, 1]  # (x, y, z) Y-up -> (x, -z, y) Z-up
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    rec = np.zeros(len(t), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["n"], rec["v"] = n, t
    with open(path, "wb") as f:
        f.write(b"KN5 Garage STL export".ljust(80, b" "))
        f.write(struct.pack("<I", len(t)))
        f.write(rec.tobytes())


def write_obj(path, final_path, parts, textures):
    """Wavefront OBJ (metres, Y up) + .mtl + a <name>_textures folder next to it."""
    final_path = Path(final_path)
    mtl_name = final_path.with_suffix(".mtl").name
    tex_dir_name = final_path.stem + "_textures"
    tex_dir = final_path.parent / tex_dir_name
    if tex_dir.exists():
        shutil.rmtree(tex_dir)
    if textures:
        tex_dir.mkdir(parents=True)
        for name, data in textures.items():
            (tex_dir / name).write_bytes(data)
    materials = {}
    for part in parts:
        m = part["material"]
        if m:
            materials.setdefault(m["name"], m)
    lines = ["# KN5 Garage OBJ export", "mtllib %s" % mtl_name]
    base = 1
    for part in parts:
        lines.append("o %s" % safe_file_part(part["name"]).replace(" ", "_"))
        lines += ["v %.6f %.6f %.6f" % tuple(p) for p in part["pos"]]
        lines += ["vn %.5f %.5f %.5f" % tuple(n) for n in part["nrm"]]
        lines += ["vt %.6f %.6f" % (u, 1.0 - v) for u, v in part["uv"]]
        if part["material"]:
            lines.append("usemtl %s" % part["material"]["name"])
        for a, b, c in part["tri"] + base:
            lines.append("f %d/%d/%d %d/%d/%d %d/%d/%d" % (a, a, a, b, b, b, c, c, c))
        base += len(part["pos"])
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    mtl = []
    for m in materials.values():
        mtl += ["newmtl %s" % m["name"], "Kd %.4f %.4f %.4f" % tuple(m["kd"]), "d %.3f" % m["d"]]
        if m.get("map_kd"):
            mtl.append("map_Kd %s/%s" % (tex_dir_name, m["map_kd"]))
        if m.get("bump"):
            mtl.append("map_Bump %s/%s" % (tex_dir_name, m["bump"]))
        mtl.append("")
    final_path.with_suffix(".mtl").write_text("\n".join(mtl), encoding="utf-8")


def model_materials(car_dir):
    """Names of the materials in a car's main model, with the guessed paint ones marked."""
    path = carfiles.find_main_kn5(Path(car_dir))
    if path is None:
        raise Kn5Error("no .kn5 file found")
    kn5 = read_kn5(path)
    return [{"name": m["name"], "paint": is_paint_material(m), "shader": m["shader"], "kind": material_kind(m)}
            for m in kn5["materials"]]


def convert_car(car_dir, out_dir, skin_name=None, flip_green=True, log=print, progress=None,
                preset="render", paint=None, paint_mode="tint", out_name=None, paint_names=None,
                texture_size=None, jpeg_quality=None, lights_on=False, fbx_converter=None):
    """Convert one car folder. Reads only from car_dir; writes the export into out_dir.

    texture_size: largest texture side (overrides the preset; ignored for STL).
    jpeg_quality: 50-100 to store colour textures without alpha as JPEG.
    lights_on: make light materials glow (headlights / tail lights on).
    fbx_converter(glb_path, fbx_path): needed for the FBX preset (Blender does the conversion).
    """
    car_dir, out_dir = Path(car_dir), Path(out_dir)
    if preset not in PRESETS:
        raise Kn5Error("unknown preset '%s'" % preset)
    opts = PRESETS[preset]
    if paint_mode not in ("tint", "solid"):
        raise Kn5Error("paint mode must be tint or solid")
    if texture_size is not None and texture_size not in TEXTURE_SIZES:
        raise Kn5Error("texture size must be one of %s" % ", ".join(map(str, TEXTURE_SIZES)))
    if jpeg_quality is not None and not 50 <= int(jpeg_quality) <= 100:
        raise Kn5Error("JPEG quality must be 50-100")
    if opts["format"] == "fbx" and fbx_converter is None:
        raise Kn5Error("FBX export needs Blender: locate blender.exe first")
    if out_dir.resolve() == car_dir.resolve() or car_dir.resolve() in out_dir.resolve().parents:
        raise Kn5Error("the output folder must not be inside the car folder")
    kn5_path = carfiles.find_main_kn5(car_dir)
    if kn5_path is None:
        raise Kn5Error("no .kn5 file found")
    notes = []
    if opts["lod"]:
        lod = carfiles.find_lod_kn5(car_dir, opts["lod"])
        if lod:
            kn5_path = lod
        else:
            notes.append({"level": "info", "text": "No lower-detail LOD model in this car; used the full model"})
    skin, skin_files = carfiles.load_skin(car_dir, skin_name)
    log("  model: %s" % kn5_path.name)
    log("  skin:  %s" % (skin or "(textures inside the kn5)"))
    if progress:
        progress(0.0, "reading %s" % kn5_path.name)
    kn5 = read_kn5(kn5_path)
    if kn5["protected"]:
        log("  ! this model is protected by its author: the shape converts, but textures,\n"
            "    shading and UVs will be wrong. Unprotected cars (e.g. Kunos cars) convert fully.")
    anims = carfiles.load_animations(car_dir, log) if opts["animations"] else {}
    out_dir.mkdir(parents=True, exist_ok=True)
    fmt = opts["format"]
    out_path = out_dir / (out_name or carfiles.output_name(car_dir, skin, preset, FORMAT_EXT[fmt]))
    max_texture = opts["max_texture"] if texture_size is None or opts["max_texture"] == 0 else texture_size
    if progress:
        progress(0.0, "converting textures")
    conv = Converter(kn5, skin_files, flip_normal_green=flip_green, log=log, progress=progress,
                     max_texture=max_texture, paint=paint, paint_mode=paint_mode, animations=anims,
                     fmt="glb" if fmt == "fbx" else fmt, paint_names=paint_names,
                     jpeg_quality=int(jpeg_quality) if jpeg_quality else None, lights_on=lights_on)
    tmp_path = out_path.with_name(out_path.name + ".part")
    glb_tmp = out_path.with_name(out_path.stem + ".fbxsource.glb") if fmt == "fbx" else None
    try:
        if fmt == "fbx":
            report = conv.run(glb_tmp) + notes
            if progress:
                progress(0.95, "converting to FBX in Blender")
            fbx_converter(glb_tmp, tmp_path)
            if not tmp_path.is_file():
                raise Kn5Error("Blender didn't write the FBX file")
        else:
            report = conv.run(tmp_path, final_path=out_path) + notes
        os.replace(tmp_path, out_path)  # never leave a half-written file under the real name
    finally:
        for p in (tmp_path, glb_tmp):
            if p is not None and p.exists():
                p.unlink()
    for item in report:
        if item["level"] == "warn":
            log("  ! %s" % item["text"])
    if progress:
        progress(1.0, "done")
    return {"path": out_path, "skin": skin, "kn5": kn5_path.name, "protected": kn5["protected"],
            "report": report, "preset": preset}
