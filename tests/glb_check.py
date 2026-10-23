"""A strict reader/validator for the .glb files we write (used by unit and smoke tests)."""

import io
import json
import struct

import numpy as np
from PIL import Image

COMPONENTS = {5126: np.float32, 5125: np.uint32, 5123: np.uint16, 5121: np.uint8}
WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


class Glb:
    def __init__(self, data):
        self.problems = []
        p = self.problems.append
        if len(data) < 20:
            raise ValueError("file too small")
        magic, version, total = struct.unpack_from("<4sII", data)
        if magic != b"glTF" or version != 2:
            raise ValueError("not a glTF 2 binary")
        if total != len(data):
            p("header length %d != file size %d" % (total, len(data)))
        jlen, jtype = struct.unpack_from("<I4s", data, 12)
        if jtype != b"JSON" or jlen % 4:
            p("bad JSON chunk")
        self.json = json.loads(data[20:20 + jlen])
        self.bin = b""
        rest = 20 + jlen
        if rest < len(data):
            blen, btype = struct.unpack_from("<I4s", data, rest)
            if btype != b"BIN\0" or blen % 4:
                p("bad BIN chunk")
            self.bin = data[rest + 8:rest + 8 + blen]
        self._validate()

    def _validate(self):
        g, p = self.json, self.problems.append
        if g.get("asset", {}).get("version") != "2.0":
            p("asset.version must be 2.0")
        buffers = g.get("buffers", [])
        if buffers and buffers[0]["byteLength"] > len(self.bin):
            p("buffer longer than BIN chunk")
        for i, v in enumerate(g.get("bufferViews", [])):
            if v["byteOffset"] % 4:
                p("bufferView %d not 4-byte aligned" % i)
            if v["byteOffset"] + v["byteLength"] > len(self.bin):
                p("bufferView %d outside buffer" % i)
        for i, a in enumerate(g.get("accessors", [])):
            if a["componentType"] not in COMPONENTS or a["type"] not in WIDTH:
                p("accessor %d has bad type" % i)
                continue
            size = np.dtype(COMPONENTS[a["componentType"]]).itemsize * WIDTH[a["type"]] * a["count"]
            v = g["bufferViews"][a["bufferView"]]
            if a.get("byteOffset", 0) + size > v["byteLength"]:
                p("accessor %d overruns its bufferView" % i)
        meshes, mats = g.get("meshes", []), g.get("materials", [])
        for mi, m in enumerate(meshes):
            for prim in m["primitives"]:
                pos = self.accessor(prim["attributes"]["POSITION"])
                pa = g["accessors"][prim["attributes"]["POSITION"]]
                if "min" not in pa or not np.allclose(pa["min"], pos.min(0)) or not np.allclose(pa["max"], pos.max(0)):
                    p("mesh %d POSITION min/max wrong" % mi)
                if "NORMAL" in prim["attributes"]:
                    n = self.accessor(prim["attributes"]["NORMAL"])
                    if len(n) != len(pos) or not np.allclose(np.linalg.norm(n, axis=1), 1, atol=1e-3):
                        p("mesh %d normals not unit length" % mi)
                if "TEXCOORD_0" in prim["attributes"] and len(self.accessor(prim["attributes"]["TEXCOORD_0"])) != len(pos):
                    p("mesh %d uv count mismatch" % mi)
                if "indices" in prim:
                    idx = self.accessor(prim["indices"]).reshape(-1)
                    if len(idx) % 3 or (len(idx) and idx.max() >= len(pos)):
                        p("mesh %d indices invalid" % mi)
                if "material" in prim and not 0 <= prim["material"] < len(mats):
                    p("mesh %d material index invalid" % mi)
        textures, images = g.get("textures", []), g.get("images", [])
        for mi, m in enumerate(mats):
            refs = [m.get("pbrMetallicRoughness", {}).get("baseColorTexture"), m.get("normalTexture")]
            for r in refs:
                if r and not 0 <= r["index"] < len(textures):
                    p("material %d texture index invalid" % mi)
        for t in textures:
            if not 0 <= t["source"] < len(images):
                p("texture source invalid")
        for i in range(len(images)):
            try:
                self.image(i).load()
            except Exception as e:
                p("image %d not decodable: %s" % (i, e))
        nodes = g.get("nodes", [])
        parents = {}
        for i, n in enumerate(nodes):
            for c in n.get("children", []):
                if not 0 <= c < len(nodes):
                    p("node %d has invalid child" % i)
                elif c in parents:
                    p("node %d has two parents" % c)
                parents[c] = i
            if "mesh" in n and not 0 <= n["mesh"] < len(meshes):
                p("node %d mesh index invalid" % i)
            if "matrix" in n and len(n["matrix"]) != 16:
                p("node %d matrix not 16 values" % i)
            if "matrix" in n and any(k in n for k in ("translation", "rotation", "scale")):
                p("node %d has both matrix and TRS" % i)
            if "rotation" in n and abs(np.linalg.norm(n["rotation"]) - 1) > 1e-3:
                p("node %d rotation not a unit quaternion" % i)
        animated = set()
        for ai, a in enumerate(g.get("animations", [])):
            for ch in a["channels"]:
                s = a["samplers"][ch["sampler"]]
                node = ch["target"]["node"]
                animated.add(node)
                if not 0 <= node < len(nodes):
                    p("animation %d targets a missing node" % ai)
                elif "matrix" in nodes[node]:
                    p("animation %d targets node %d that uses a matrix" % (ai, node))
                inp = g["accessors"][s["input"]]
                out = g["accessors"][s["output"]]
                if inp["count"] != out["count"] or "min" not in inp or "max" not in inp:
                    p("animation %d sampler input/output mismatch" % ai)
        for si, skin in enumerate(g.get("skins", [])):
            if not skin["joints"] or not all(0 <= j < len(nodes) for j in skin["joints"]):
                p("skin %d has invalid joints" % si)
            if "inverseBindMatrices" in skin:
                acc = g["accessors"][skin["inverseBindMatrices"]]
                if acc["type"] != "MAT4" or acc["count"] != len(skin["joints"]):
                    p("skin %d inverse bind matrices don't match its joints" % si)
        for i, n in enumerate(nodes):
            if "skin" in n:
                if not 0 <= n["skin"] < len(g.get("skins", [])):
                    p("node %d skin index invalid" % i)
                    continue
                attrs = meshes[n["mesh"]]["primitives"][0]["attributes"] if "mesh" in n else {}
                if "JOINTS_0" not in attrs or "WEIGHTS_0" not in attrs:
                    p("skinned node %d has no JOINTS_0 / WEIGHTS_0" % i)
                    continue
                joints = self.accessor(attrs["JOINTS_0"])
                weights = self.accessor(attrs["WEIGHTS_0"])
                if joints.max() >= len(g["skins"][n["skin"]]["joints"]):
                    p("node %d joint index out of range" % i)
                if not np.allclose(weights.sum(axis=1), 1, atol=1e-3):
                    p("node %d weights don't add up to 1" % i)
        for r in g["scenes"][g.get("scene", 0)]["nodes"]:
            if r in parents:
                p("scene root %d has a parent" % r)

    def accessor(self, i):
        a = self.json["accessors"][i]
        v = self.json["bufferViews"][a["bufferView"]]
        dt = COMPONENTS[a["componentType"]]
        n = a["count"] * WIDTH[a["type"]]
        arr = np.frombuffer(self.bin, dt, n, v["byteOffset"] + a.get("byteOffset", 0))
        return arr.reshape(a["count"], WIDTH[a["type"]]) if WIDTH[a["type"]] > 1 else arr

    def image_bytes(self, i):
        v = self.json["bufferViews"][self.json["images"][i]["bufferView"]]
        return self.bin[v["byteOffset"]:v["byteOffset"] + v["byteLength"]]

    def image(self, i):
        return Image.open(io.BytesIO(self.image_bytes(i)))

    def image_named(self, name):
        for i, im in enumerate(self.json.get("images", [])):
            if im["name"].lower() == name.lower():
                return self.image(i)
        return None

    def world_nodes(self):
        """name -> (world 4x4 matrix, node dict)."""
        out = {}

        def walk(i, parent):
            n = self.json["nodes"][i]
            w = parent @ local_matrix(n)
            out[n["name"]] = (w, n)
            for c in n.get("children", []):
                walk(c, w)

        for r in self.json["scenes"][0]["nodes"]:
            walk(r, np.eye(4))
        return out

    def mesh_data(self, node):
        prim = self.json["meshes"][node["mesh"]]["primitives"][0]
        pos = self.accessor(prim["attributes"]["POSITION"])
        nrm = self.accessor(prim["attributes"]["NORMAL"])
        tri = self.accessor(prim["indices"]).reshape(-1, 3).astype(np.int64)
        return pos, nrm, tri, prim

    def ccw_share(self, node):
        """Share of triangles whose CCW face normal agrees with the vertex normals."""
        pos, nrm, tri, _ = self.mesh_data(node)
        p = pos.astype(np.float64)
        face = np.cross(p[tri[:, 1]] - p[tri[:, 0]], p[tri[:, 2]] - p[tri[:, 0]])
        return float((np.einsum("ij,ij->i", face, nrm[tri[:, 0]]) > 0).mean())


def local_matrix(n):
    """4x4 column-vector matrix of a glTF node (matrix, or translation * rotation * scale)."""
    if "matrix" in n:
        return np.array(n["matrix"], dtype=np.float64).reshape(4, 4).T
    x, y, z, w = n.get("rotation", [0, 0, 0, 1])
    r = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    m = np.eye(4)
    m[:3, :3] = r * np.array(n.get("scale", [1, 1, 1]))
    m[:3, 3] = n.get("translation", [0, 0, 0])
    return m


def load(path):
    with open(path, "rb") as f:
        return Glb(f.read())
