"""Opening 3D files: glb / gltf / obj come in as the same model structure as a kn5."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

import fixtures as fx
from kn5garage import convert, importer, kn5, kn5write

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import make_demo_garage as demo  # noqa: E402


def meshes(model):
    return [n for n in importer.walk(model["root"]) if n["cls"] != 1]


class ImporterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def demo_glb(self):
        car = demo.build_car(demo.CARS[0], self.dir / "cars")
        r = convert.convert_car(car, self.dir / "out", log=lambda *a: None)
        return car, r["path"]

    def test_glb_from_a_car_comes_back_with_the_same_shape(self):
        car, glb = self.demo_glb()
        original = kn5.read_kn5(car / (demo.CARS[0]["id"] + ".kn5"))
        model = importer.load_model(glb)
        tris = sum(len(n["indices"]) // 3 for n in meshes(model))
        self.assertEqual(tris, sum(len(n["indices"]) // 3 for n in meshes(original) if n.get("visible", True)) or tris)
        self.assertGreater(len(model["materials"]), 0)
        self.assertTrue(all(m["maps"].get("txDiffuse") in model["textures"] for m in model["materials"]))
        lo, hi = importer.bounds(model["root"])
        olo, ohi = importer.bounds(original["root"])
        np.testing.assert_allclose(hi - lo, ohi - olo, atol=1e-3)

    def test_imported_model_writes_a_valid_kn5_and_converts_again(self):
        _, glb = self.demo_glb()
        model = importer.load_model(glb)
        data = kn5write.write_kn5(model)
        p = self.dir / "imported.kn5"
        p.write_bytes(data)
        back = kn5.read_kn5(p)
        self.assertEqual(len(meshes(back)), len(meshes(model)))
        conv = convert.Converter(back, {}, log=lambda *a: None)
        conv.run(self.dir / "again.glb")
        self.assertTrue((self.dir / "again.glb").stat().st_size > 1000)

    def test_gltf_with_files_next_to_it(self):
        # a separate .bin and .png next to the .gltf
        pos = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float32)
        idx = np.array([0, 1, 2], np.uint16)
        (self.dir / "tri.bin").write_bytes(pos.tobytes() + idx.tobytes() + b"\0\0")
        (self.dir / "red.png").write_bytes(fx.png_bytes())
        doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
               "nodes": [{"name": "tri", "mesh": 0, "translation": [0, 2, 0]}],
               "meshes": [{"name": "tri", "primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 0}]}],
               "materials": [{"name": "red", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}],
               "textures": [{"source": 0}], "images": [{"uri": "red.png"}],
               "buffers": [{"uri": "tri.bin", "byteLength": 44}],
               "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 36}, {"buffer": 0, "byteOffset": 36, "byteLength": 6}],
               "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
                             {"bufferView": 1, "componentType": 5123, "count": 3, "type": "SCALAR"}]}
        (self.dir / "tri.gltf").write_text(json.dumps(doc))
        model = importer.load_model(self.dir / "tri.gltf")
        m = meshes(model)[0]
        self.assertEqual(len(m["verts"]), 3)
        np.testing.assert_allclose(m["verts"][:, 3:6], [[0, 0, 1]] * 3, atol=1e-6)  # normals made
        self.assertEqual(model["materials"][0]["maps"]["txDiffuse"], "red.dds")
        lo, hi = importer.bounds(model["root"])
        np.testing.assert_allclose(lo, [0, 2, 0])

    def test_files_outside_the_model_folder_are_refused(self):
        for uri in ("../secret.bin", "C:/Windows/win.ini", "/etc/passwd", "sub/../../x.bin"):
            doc = {"asset": {"version": "2.0"}, "buffers": [{"uri": uri, "byteLength": 4}]}
            (self.dir / "bad.gltf").write_text(json.dumps(doc))
            with self.subTest(uri), self.assertRaises(importer.ImportError3D):
                importer.load_model(self.dir / "bad.gltf")

    def test_obj_with_mtl_quads_and_negative_indices(self):
        (self.dir / "box.mtl").write_text("newmtl blue\nKd 0 0 1\nnewmtl tex\nmap_Kd red.png\n")
        (self.dir / "red.png").write_bytes(fx.png_bytes())
        (self.dir / "box.obj").write_text(
            "mtllib box.mtl\no quad\nv 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nvt 0 0\nvt 1 0\nvt 1 1\nvt 0 1\n"
            "usemtl blue\nf 1/1 2/2 3/3 4/4\nusemtl tex\nf -4/-4 -2/-2 -1/-1\n")
        model = importer.load_model(self.dir / "box.obj")
        ms = meshes(model)
        self.assertEqual(sum(len(n["indices"]) for n in ms), 9)
        self.assertEqual({m["name"] for m in model["materials"]}, {"blue", "tex"})
        self.assertEqual(model["materials"][1]["maps"]["txDiffuse"], "red.dds")
        uv = ms[0]["verts"][:, 6:8]
        self.assertAlmostEqual(float(uv[:, 1].max()), 1.0)  # v flipped for DirectX

    def test_big_mesh_is_split_into_kn5_sized_parts(self):
        n = 70000
        pos = np.random.default_rng(1).random((n, 3)).astype(np.float32)
        tri = np.arange(n - n % 3)
        parts = importer._mesh_nodes("big", pos, None, None, tri, 0)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p["verts"]) <= 65535 for p in parts))
        self.assertEqual(sum(len(p["indices"]) for p in parts), len(tri))
        kn5write.write_kn5({**importer.empty_model("x"), "root": kn5write.new_dummy("x", children=parts),
                            "materials": [kn5write.new_material("m")]})

    def test_damaged_files(self):
        cases = {"a.glb": b"glTF\x02\0\0\0\x10\0\0\0", "b.gltf": b"not json", "c.gltf": b'{"asset":{"version":"1.0"}}',
                 "d.obj": b"v 1 2\nf 1 2 3\n", "e.xyz": b""}
        for name, data in cases.items():
            (self.dir / name).write_bytes(data)
            with self.subTest(name), self.assertRaises(importer.ImportError3D):
                importer.load_model(self.dir / name)

    def test_fbx_needs_blender(self):
        (self.dir / "a.fbx").write_bytes(b"x")
        with self.assertRaises(importer.ImportError3D):
            importer.load_model(self.dir / "a.fbx")

    def test_fit_matrix(self):
        m = np.array(importer.fit_matrix((np.zeros(3), np.ones(3)), (np.array([2, 2, 2]), np.array([4, 4, 4])))).reshape(4, 4).T
        np.testing.assert_allclose(m @ [1, 1, 1, 1], [4, 4, 4, 1])
        np.testing.assert_allclose(m @ [0, 0, 0, 1], [2, 2, 2, 1])


if __name__ == "__main__":
    unittest.main()
