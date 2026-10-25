"""Every 3D format the app reads: each test writes a small file in that format and checks
the model that comes back (shape, size, materials, rigs, animations)."""

import io
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

import numpy as np

import fixtures as fx
from kn5garage import convert, formats, importer, kn5, kn5write
from kn5garage.importer import ImportError3D


def meshes(model):
    return [n for n in importer.walk(model["root"]) if n["cls"] != 1]


def world_bounds(model):
    return importer.bounds(model["root"])


def tris(model):
    return sum(len(n["indices"]) // 3 for n in meshes(model))


def glb_of(model, path):
    convert.Converter(model, {}, log=lambda *a: None).run(path)
    import glb_check
    return glb_check.load(path).json


# ---------------------------------------------------------------------------
# small writers for binary formats
# ---------------------------------------------------------------------------

def fbx_bytes(nodes, version=7400):
    """nodes: [(name, [props], [children])]; props: int (L), float (D), str (S), bytes (R),
    ('i'|'d'|'l'|'f', array) arrays (stored zlib-compressed to test that too)."""
    wide = version >= 7500
    head = struct.Struct("<QQQB" if wide else "<IIIB")

    def prop(p):
        if isinstance(p, bool):
            return b"C" + bytes([p])
        if isinstance(p, int):
            return b"L" + struct.pack("<q", p)
        if isinstance(p, float):
            return b"D" + struct.pack("<d", p)
        if isinstance(p, str):
            raw = p.encode()
            return b"S" + struct.pack("<I", len(raw)) + raw
        if isinstance(p, bytes):
            return b"R" + struct.pack("<I", len(p)) + p
        kind, arr = p
        raw = np.asarray(arr, {"i": "<i4", "d": "<f8", "l": "<i8", "f": "<f4"}[kind]).tobytes()
        packed = zlib.compress(raw)
        return kind.encode() + struct.pack("<III", len(arr), 1, len(packed)) + packed

    def node(n, offset):
        name, props, children = n
        pb = b"".join(prop(p) for p in props)
        body_start = offset + head.size + len(name) + len(pb)
        kids, at = b"", body_start
        for c in children:
            k = node(c, at)
            kids += k
            at += len(k)
        if children:
            kids += b"\0" * head.size
        end = body_start + len(kids)
        return head.pack(end, len(props), len(pb), len(name)) + name.encode() + pb + kids

    out = b"Kaydara FBX Binary  \x00\x1a\x00" + struct.pack("<I", version)
    at = len(out)
    for n in nodes:
        b = node(n, at)
        out += b
        at += len(b)
    return out + b"\0" * head.size + b"\0" * 16


def p70(*items):
    return ("Properties70", [], [("P", list(i), []) for i in items])


def pmx_bytes(textures=("tex.png",), bone_parent=(-1, 0)):
    def text(s):
        raw = s.encode("utf-16-le")
        return struct.pack("<i", len(raw)) + raw
    out = b"PMX " + struct.pack("<f", 2.0) + bytes([8, 0, 0, 2, 1, 1, 2, 1, 1])
    out += text("Figure") + text("Figure") + text("") + text("")
    verts = [((0, 0, 0), 0), ((1, 0, 0), 0), ((0, 10, 0), 1), ((1, 10, 0), 1)]
    out += struct.pack("<i", len(verts))
    for p, bone in verts:
        out += struct.pack("<8f", *p, 0, 0, -1, 0, 0) + bytes([0]) + struct.pack("<h", bone) + struct.pack("<f", 1)
    faces = [0, 1, 2, 1, 3, 2]
    out += struct.pack("<i", len(faces)) + struct.pack("<%dH" % len(faces), *faces)
    out += struct.pack("<i", len(textures)) + b"".join(text(t) for t in textures)
    out += struct.pack("<i", 1)
    out += text("skin") + text("skin") + struct.pack("<4f", 1, 0.8, 0.7, 1) + struct.pack("<7f", 0, 0, 0, 5, .5, .5, .5)
    out += bytes([0]) + struct.pack("<5f", 0, 0, 0, 1, 1) + struct.pack("<b", 0) + struct.pack("<b", -1)
    out += bytes([0, 1, 0]) + text("") + struct.pack("<i", len(faces))
    out += struct.pack("<i", 2)
    for name, pos, parent in (("root", (0, 0, 0), bone_parent[0]), ("head", (0, 10, 0), bone_parent[1])):
        out += text(name) + text(name) + struct.pack("<3f", *pos) + struct.pack("<h", parent) + struct.pack("<i", 0)
        out += struct.pack("<H", 0) + struct.pack("<3f", 0, 1, 0)
    return out


class FormatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, data):
        p = self.dir / name
        p.write_bytes(data if isinstance(data, bytes) else data.encode())
        return p

    def load(self, name, data):
        return formats.load(self.write(name, data))

    # --- simple formats ---------------------------------------------------

    def test_stl_binary_and_ascii(self):
        tri = np.array([[[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[1, 0, 0], [1, 1, 0], [0, 1, 0]]], np.float32)
        binary = b"\0" * 80 + struct.pack("<I", 2) + b"".join(
            struct.pack("<3f", 0, 0, 1) + t.tobytes() + b"\0\0" for t in tri)
        ascii_ = "solid x\n" + "".join("facet normal 0 0 1\nouter loop\n" + "".join(
            "vertex %g %g %g\n" % tuple(v) for v in t) + "endloop\nendfacet\n" for t in tri) + "endsolid x\n"
        for name, data in (("b.stl", binary), ("a.stl", ascii_)):
            with self.subTest(name):
                m = self.load(name, data)
                self.assertEqual(tris(m), 2)
                self.assertEqual(len(meshes(m)[0]["verts"]), 4)  # shared corners joined

    def test_ply_ascii_and_binary(self):
        header = "ply\nformat %s 1.0\nelement vertex 4\nproperty float x\nproperty float y\nproperty float z\n" \
                 "property float u\nproperty float v\nelement face 1\nproperty list uchar int vertex_indices\nend_header\n"
        rows = [(0, 0, 0, 0, 0), (1, 0, 0, 1, 0), (1, 1, 0, 1, 1), (0, 1, 0, 0, 1)]
        ascii_ = header % "ascii" + "".join("%g %g %g %g %g\n" % r for r in rows) + "4 0 1 2 3\n"
        binary = (header % "binary_little_endian").encode() + b"".join(struct.pack("<5f", *r) for r in rows) + \
            bytes([4]) + struct.pack("<4i", 0, 1, 2, 3)
        for name, data in (("a.ply", ascii_), ("b.ply", binary)):
            with self.subTest(name):
                m = self.load(name, data)
                self.assertEqual(tris(m), 2)
                np.testing.assert_allclose(meshes(m)[0]["verts"][:, 7], [1, 1, 0, 0])  # v flipped

    def test_3ds_with_material_and_z_up(self):
        def chunk(cid, body):
            return struct.pack("<HI", cid, 6 + len(body)) + body
        verts = struct.pack("<H", 3) + struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 0, 2)
        faces = struct.pack("<H", 1) + struct.pack("<4H", 0, 1, 2, 0) + chunk(0x4130, b"red\0" + struct.pack("<HH", 1, 0))
        mesh = chunk(0x4000, b"tri\0" + chunk(0x4100, chunk(0x4110, verts) + chunk(0x4120, faces)))
        mat = chunk(0xAFFF, chunk(0xA000, b"red\0") + chunk(0xA020, chunk(0x0011, bytes([255, 0, 0]))))
        m = self.load("x.3ds", chunk(0x4D4D, chunk(0x3D3D, mat + mesh)))
        lo, hi = world_bounds(m)
        np.testing.assert_allclose(hi - lo, [1, 2, 0], atol=1e-6)  # Z up (height 2) became Y up
        self.assertEqual(m["materials"][0]["name"], "red")

    # --- COLLADA ------------------------------------------------------------

    def test_dae_with_texture_units_and_z_up(self):
        (self.dir / "paint.png").write_bytes(fx.png_bytes((10, 200, 30)))
        dae = """<?xml version="1.0"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
 <asset><unit meter="0.01"/><up_axis>Z_UP</up_axis></asset>
 <library_images><image id="img"><init_from>paint.png</init_from></image></library_images>
 <library_effects><effect id="fx"><profile_COMMON>
  <newparam sid="surf"><surface type="2D"><init_from>img</init_from></surface></newparam>
  <newparam sid="samp"><sampler2D><source>surf</source></sampler2D></newparam>
  <technique sid="c"><phong><diffuse><texture texture="samp" texcoord="UV"/></diffuse></phong></technique>
 </profile_COMMON></effect></library_effects>
 <library_materials><material id="mat" name="Body"><instance_effect url="#fx"/></material></library_materials>
 <library_geometries><geometry id="g" name="body"><mesh>
  <source id="p"><float_array id="pa" count="12">0 0 0 100 0 0 100 0 200 0 0 200</float_array>
   <technique_common><accessor source="#pa" count="4" stride="3"/></technique_common></source>
  <vertices id="v"><input semantic="POSITION" source="#p"/></vertices>
  <polylist material="m1" count="1"><input semantic="VERTEX" source="#v" offset="0"/>
   <vcount>4</vcount><p>0 1 2 3</p></polylist>
 </mesh></geometry></library_geometries>
 <library_visual_scenes><visual_scene id="s"><node name="car"><translate>0 0 50</translate>
  <instance_geometry url="#g"><bind_material><technique_common>
   <instance_material symbol="m1" target="#mat"/></technique_common></bind_material></instance_geometry>
 </node></visual_scene></library_visual_scenes>
 <scene><instance_visual_scene url="#s"/></scene>
</COLLADA>"""
        m = self.load("car.dae", dae)
        self.assertEqual(tris(m), 2)
        lo, hi = world_bounds(m)
        np.testing.assert_allclose(lo, [0, 0.5, 0], atol=1e-6)     # cm -> m, Z up -> Y up, moved up 50 cm
        np.testing.assert_allclose(hi, [1, 2.5, 0], atol=1e-6)
        mat = m["materials"][0]
        self.assertEqual(mat["name"], "Body")
        self.assertEqual(mat["maps"]["txDiffuse"], "paint.dds")
        with self.assertRaises(ImportError3D):
            self.load("bomb.dae", '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><COLLADA/>')

    # --- FBX ----------------------------------------------------------------

    def fbx_scene(self, version=7400, unit=100.0):
        verts = ("d", [0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0])
        poly = ("i", [0, 1, 2, ~3])
        geometry = ("Geometry", [10, "Box\x00\x01Geometry", "Mesh"], [
            ("Vertices", [verts], []), ("PolygonVertexIndex", [poly], []),
            ("LayerElementNormal", [0], [("MappingInformationType", ["ByPolygonVertex"], []),
                                         ("ReferenceInformationType", ["Direct"], []),
                                         ("Normals", [("d", [0, 0, 1] * 4)], [])]),
            ("LayerElementUV", [0], [("MappingInformationType", ["ByPolygonVertex"], []),
                                     ("ReferenceInformationType", ["IndexToDirect"], []),
                                     ("UV", [("d", [0, 0, 1, 0, 1, 1, 0, 1])], []), ("UVIndex", [("i", [0, 1, 2, 3])], [])])])
        model = ("Model", [20, "Box\x00\x01Model", "Mesh"], [p70(
            ("Lcl Translation", "Lcl Translation", "", "A", 0.0, 2.0, 0.0),
            ("Lcl Rotation", "Lcl Rotation", "", "A", 0.0, 0.0, 90.0))])
        material = ("Material", [30, "Red\x00\x01Material", ""], [p70(("DiffuseColor", "Color", "", "A", 1.0, 0.0, 0.0))])
        video = ("Video", [50, "pic\x00\x01Video", "Clip"], [("Content", [fx.png_bytes((0, 0, 255))], [])])
        texture = ("Texture", [40, "pic\x00\x01Texture", ""], [("RelativeFilename", ["pic.png"], [])])
        curve_node = ("AnimationCurveNode", [60, "T\x00\x01AnimCurveNode", ""], [])
        curve = ("AnimationCurve", [61, "\x00\x01AnimCurve", ""], [
            ("KeyTime", [("l", [0, 46186158000])], []), ("KeyValueFloat", [("f", [2.0, 5.0])], [])])
        layer = ("AnimationLayer", [62, "Base\x00\x01AnimLayer", ""], [])
        stack = ("AnimationStack", [63, "Take 001\x00\x01AnimStack", ""], [])
        conns = [("C", ["OO", 20, 0], []), ("C", ["OO", 10, 20], []), ("C", ["OO", 30, 20], []),
                 ("C", ["OP", 40, 30, "DiffuseColor"], []), ("C", ["OO", 50, 40], []),
                 ("C", ["OO", 62, 63], []), ("C", ["OO", 60, 62], []), ("C", ["OP", 60, 20, "Lcl Translation"], []),
                 ("C", ["OP", 61, 60, "d|Y"], [])]
        return fbx_bytes([
            ("GlobalSettings", [], [p70(("UpAxis", "int", "Integer", "", 1), ("UnitScaleFactor", "double", "Number", "", unit))]),
            ("Objects", [], [geometry, model, material, texture, video, curve_node, curve, layer, stack]),
            ("Connections", [], conns)], version)

    def test_fbx_mesh_material_embedded_texture_units_animation(self):
        for version in (7400, 7500):
            with self.subTest(version=version):
                m = self.load("box.fbx", self.fbx_scene(version))
                self.assertEqual(tris(m), 2)
                lo, hi = world_bounds(m)
                np.testing.assert_allclose(hi - lo, [1, 1, 0], atol=1e-6)  # metres (unit factor 100)
                np.testing.assert_allclose(lo[1], 2.0, atol=1e-6)          # moved up
                self.assertEqual(m["materials"][0]["name"], "Red")
                img = importer.Image.open(io.BytesIO(fx.png_bytes()))  # noqa: F841 (Pillow is there)
                tex = m["textures"][m["materials"][0]["maps"]["txDiffuse"]]
                from kn5garage.textures import decode_image
                self.assertEqual(decode_image(tex).getpixel((0, 0))[:3], (0, 0, 255))
                track = m["animations"]["Take 001"]["Box"]
                self.assertAlmostEqual(float(track["t"][0][1]), 2.0, places=4)
                self.assertAlmostEqual(float(track["t"][-1][1]), 5.0, places=4)
                doc = glb_of(m, self.dir / "box.glb")
                self.assertEqual(doc["animations"][0]["name"], "Take 001")

    def test_fbx_rigged_character(self):
        verts = ("d", [0, 0, 0, 10, 0, 0, 0, 100, 0, 10, 100, 0])
        geometry = ("Geometry", [10, "Body\x00\x01Geometry", "Mesh"], [
            ("Vertices", [verts], []), ("PolygonVertexIndex", [("i", [0, 1, 3, ~2])], [])])
        mesh_model = ("Model", [20, "Body\x00\x01Model", "Mesh"], [])
        hips = ("Model", [21, "Hips\x00\x01Model", "LimbNode"], [])
        head = ("Model", [22, "Head\x00\x01Model", "LimbNode"], [p70(("Lcl Translation", "", "", "A", 0.0, 100.0, 0.0))])
        eye = list(np.eye(4).reshape(-1))
        head_link = list(np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 100, 0, 1]], float).reshape(-1))
        skin = ("Deformer", [30, "\x00\x01Deformer", "Skin"], [])
        c1 = ("Deformer", [31, "\x00\x01SubDeformer", "Cluster"], [
            ("Indexes", [("i", [0, 1])], []), ("Weights", [("d", [1.0, 1.0])], []),
            ("Transform", [("d", eye)], []), ("TransformLink", [("d", eye)], [])])
        c2 = ("Deformer", [32, "\x00\x01SubDeformer", "Cluster"], [
            ("Indexes", [("i", [2, 3])], []), ("Weights", [("d", [1.0, 1.0])], []),
            ("Transform", [("d", eye)], []), ("TransformLink", [("d", head_link)], [])])
        data = fbx_bytes([
            ("GlobalSettings", [], [p70(("UnitScaleFactor", "double", "Number", "", 1.0))]),
            ("Objects", [], [geometry, mesh_model, hips, head, skin, c1, c2]),
            ("Connections", [], [("C", ["OO", 20, 0], []), ("C", ["OO", 21, 0], []), ("C", ["OO", 22, 21], []),
                                 ("C", ["OO", 10, 20], []), ("C", ["OO", 30, 10], []), ("C", ["OO", 31, 30], []),
                                 ("C", ["OO", 32, 30], []), ("C", ["OO", 21, 31], []), ("C", ["OO", 22, 32], [])])])
        m = self.load("man.fbx", data)
        rigged = [n for n in meshes(m) if n["cls"] == 3]
        self.assertEqual(len(rigged), 1)
        self.assertEqual([b for b, _ in rigged[0]["bones"]], ["Hips", "Head"])
        np.testing.assert_allclose(rigged[0]["verts"][:, 1].max(), 1.0, atol=1e-6)  # 100 cm
        doc = glb_of(m, self.dir / "man.glb")
        self.assertEqual(len(doc.get("skins", [])), 1)       # a real rig in the export
        self.assertEqual(len(doc["skins"][0]["joints"]), 2)
        back = kn5.read_kn5(self.write("man.kn5", kn5write.write_kn5(m)))  # and in a .kn5
        self.assertEqual(len([n for n in meshes(back) if n["cls"] == 3]), 1)

    def test_fbx_text_and_damaged(self):
        with self.assertRaises(ImportError3D) as e:
            self.load("t.fbx", "; FBX 7.4.0 project file\nFBXHeaderExtension:  {\n}")
        self.assertIn("ASCII", str(e.exception))
        good = self.fbx_scene()
        for cut in (30, len(good) // 2, len(good) - 40):
            with self.subTest(cut=cut), self.assertRaises(ImportError3D):
                self.load("cut.fbx", good[:cut])

    # --- characters ---------------------------------------------------------

    def test_bvh_skeleton_and_motion(self):
        bvh = """HIERARCHY
ROOT Hips
{
  OFFSET 0 90 0
  CHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation
  JOINT Spine
  {
    OFFSET 0 20 0
    CHANNELS 3 Zrotation Xrotation Yrotation
    End Site
    {
      OFFSET 0 30 0
    }
  }
}
MOTION
Frames: 3
Frame Time: 0.5
0 0 0 0 0 0 0 0 0
0 0 10 0 0 0 0 90 0
0 0 20 0 0 0 0 0 0
"""
        m = self.load("walk.bvh", bvh)
        names = [n["name"] for n in importer.walk(m["root"])]
        self.assertIn("Hips", names)
        self.assertIn("Spine", names)
        self.assertEqual(tris(m), 16)  # two bones drawn as double pyramids
        track = m["animations"]["walk"]
        self.assertEqual(len(track["Hips"]["t"]), 31)  # 1 second at 30 fps
        np.testing.assert_allclose(track["Hips"]["t"][-1], [0, 90, 20], atol=1e-4)
        q = track["Spine"]["r"][15]  # half way: 90 degrees about X
        np.testing.assert_allclose(np.abs(q), [np.sin(np.pi / 4), 0, 0, np.cos(np.pi / 4)], atol=1e-4)
        lo, hi = world_bounds(m)
        self.assertAlmostEqual(float(hi[1]), 1.4, places=4)  # cm -> m
        doc = glb_of(m, self.dir / "walk.glb")
        self.assertEqual(len(doc["animations"]), 1)

    def test_pmx_character(self):
        (self.dir / "tex.png").write_bytes(fx.png_bytes((250, 200, 180)))
        m = self.load("miku.pmx", pmx_bytes())
        self.assertEqual(m["root"]["name"], "Figure")
        rigged = [n for n in meshes(m) if n["cls"] == 3]
        self.assertEqual(len(rigged), 1)
        self.assertEqual([b for b, _ in rigged[0]["bones"]], ["root", "head"])
        self.assertAlmostEqual(float(rigged[0]["verts"][:, 1].max()), 0.8, places=5)  # 10 MMD units = 0.8 m
        self.assertEqual(m["materials"][0]["maps"]["txDiffuse"], "tex.dds")
        doc = glb_of(m, self.dir / "miku.glb")
        self.assertEqual(len(doc.get("skins", [])), 1)
        m = self.load("loop.pmx", pmx_bytes(bone_parent=(1, 0)))  # bones that are each other's parent
        self.assertEqual(len([n for n in meshes(m) if n["cls"] == 3]), 1)
        with self.assertRaises(ImportError3D):
            self.load("bad.pmx", pmx_bytes()[:120])

    def test_gltf_rig_and_animation_survive_import(self):
        car = self.write("driver.kn5", fx.rigged_kn5())
        glb = self.dir / "driver.glb"
        convert.Converter(kn5.read_kn5(car), {}, log=lambda *a: None).run(glb)
        m = formats.load(glb)
        rigged = [n for n in meshes(m) if n["cls"] == 3]
        self.assertEqual(len(rigged), 1)
        self.assertEqual([b for b, _ in rigged[0]["bones"]], ["DRIVER_ROOT", "DRIVER_HEAD"])
        doc = glb_of(m, self.dir / "again.glb")
        self.assertEqual(len(doc.get("skins", [])), 1)
        vrm = self.write("avatar.vrm", glb.read_bytes())  # a VRM avatar is a .glb
        self.assertEqual(len([n for n in meshes(formats.load(vrm)) if n["cls"] == 3]), 1)

    # --- the list -----------------------------------------------------------

    def test_unsupported_formats_say_what_to_do(self):
        for ext, word in ((".skb", "SketchUp"), (".skp", "SketchUp"), (".blend", "Blender"), (".modelbin", "encrypted")):
            with self.subTest(ext), self.assertRaises(ImportError3D) as e:
                self.load("x" + ext, b"data")
            self.assertIn(word, str(e.exception))
        with self.assertRaises(ImportError3D):
            self.load("x.xyz", b"")

    def test_every_listed_reader_exists(self):
        from importlib import import_module
        for ext, (label, module, fn) in formats.FORMATS.items():
            if module:
                self.assertTrue(callable(getattr(import_module(module), fn)), ext)

    def test_garbage_in_every_format_only_raises_import_errors(self):
        rng = np.random.default_rng(3)
        for ext in formats.EXTENSIONS:
            for size in (0, 7, 100, 3000):
                data = rng.integers(0, 256, size, dtype=np.uint8).tobytes()
                for prefix in (b"", b"PMX ", b"ply\nformat ascii 1.0\nend_header\n", b"Kaydara FBX Binary  \x00\x1a\x00",
                               b"HIERARCHY\n", b"glTF", b"<COLLADA>", b"\x4d\x4d"):
                    with self.subTest(ext=ext, size=size, prefix=prefix[:6]):
                        try:
                            formats.load(self.write("g" + ext, prefix + data))
                        except Exception as e:  # noqa: BLE001
                            self.assertIsInstance(e, kn5.Kn5Error, "%s: %r" % (ext, e))


if __name__ == "__main__":
    unittest.main()
