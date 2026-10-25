"""3D printing: repair, real size, cutting into closed pieces, STL and 3MF files."""

import io
import struct
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import numpy as np

import fixtures  # noqa: F401 (puts the project on the import path)

from kn5garage import importer, kn5write, printing
from test_app import AppTestCase

CUBE_V = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]], float)
CUBE_F = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
                   [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]])


def volume(v, f):
    p = v[f]
    return np.einsum("ij,ij->i", p[:, 0], np.cross(p[:, 1], p[:, 2])).sum() / 6


def cube_model(size=1.0, open_top=False, flip=0):
    f = CUBE_F.copy()
    if open_top:
        f = np.delete(f, [2, 3], axis=0)
    if flip:
        f[flip] = f[flip][::-1]
    v = CUBE_V * size
    verts = np.hstack([v, np.zeros((8, 8))]).astype(np.float32)
    model = importer.empty_model("cube")
    model["materials"].append(kn5write.new_material("m"))
    model["root"]["children"] = [kn5write.new_mesh("cube", verts, f.reshape(-1).astype(np.uint16), 0)]
    return model


class PrintingTests(unittest.TestCase):
    def test_closed_cube_is_watertight_and_has_volume(self):
        tris, _ = printing.to_print_space(printing.model_triangles(cube_model()), height_mm=20)
        v, f, fixes = printing.repair(tris)
        s = printing.stats(v, f)
        self.assertTrue(s["watertight"])
        self.assertEqual(s["size"], [20, 20, 20])
        self.assertAlmostEqual(volume(v, f), 8000, places=3)  # outward facing: positive

    def test_hole_closed_and_flipped_triangle_fixed(self):
        tris, _ = printing.to_print_space(printing.model_triangles(cube_model(open_top=True, flip=4)), height_mm=10)
        v, f, fixes = printing.repair(tris)
        self.assertEqual(fixes["holesClosed"], 1)
        self.assertEqual(fixes["holesLeft"], 0)
        self.assertAlmostEqual(volume(v, f), 1000, places=3)

    def test_concave_hole_is_filled_without_overlaps(self):
        # an L-shaped outline: a fan from one corner would spill outside it
        loop = np.array([[0, 0, 0], [2, 0, 0], [2, 1, 0], [1, 1, 0], [1, 2, 0], [0, 2, 0]], float)
        tris = printing.triangulate_loop(loop, list(range(6)))
        area = sum(np.linalg.norm(np.cross(loop[b] - loop[a], loop[c] - loop[a])) / 2 for a, b, c in tris)
        self.assertEqual(len(tris), 4)
        self.assertAlmostEqual(area, 3.0)

    def test_cut_gives_closed_pieces(self):
        tris, _ = printing.to_print_space(printing.model_triangles(cube_model()), height_mm=30)
        v, f, _ = printing.repair(tris)
        pieces = printing.cut_pieces(v, f, [10, 20])
        self.assertEqual(len(pieces), 3)
        for pv, pf in pieces:
            s = printing.stats(pv, pf)
            self.assertTrue(s["watertight"], s)
            self.assertAlmostEqual(volume(pv, pf), 30 * 30 * 10, delta=1e-3)

    def test_y_up_becomes_z_up_on_the_bed(self):
        model = cube_model()
        model["root"]["children"][0]["verts"][:, 1] *= 3  # tall in Y (up in the app)
        tris, _ = printing.to_print_space(printing.model_triangles(model))
        lo, hi = tris.reshape(-1, 3).min(0), tris.reshape(-1, 3).max(0)
        np.testing.assert_allclose(hi - lo, [1000, 1000, 3000])  # metres -> mm, tall in Z
        self.assertAlmostEqual(lo[2], 0)
        np.testing.assert_allclose((lo + hi)[:2] / 2, [0, 0], atol=1e-9)

    def test_flat_surface_is_reported(self):
        model = cube_model()
        model["root"]["children"][0]["verts"][:, 1] = 0  # squashed flat
        tris, _ = printing.to_print_space(printing.model_triangles(model), scale=1000)
        v, f, _ = printing.repair(tris)
        s = printing.stats(v, f)
        self.assertTrue(s["flat"])
        self.assertFalse(s["watertight"])

    def test_thin_surface_gets_a_wall(self):
        tris, _ = printing.to_print_space(printing.model_triangles(cube_model(open_top=True)), height_mm=20)
        v, f = printing.weld(tris, 1e-3)
        f = printing.orient(v, printing.clean(v, f))
        v2, f2 = printing.thicken(v, f, 2.0)
        s = printing.stats(v2, f2)
        self.assertTrue(s["watertight"])
        self.assertFalse(s["flat"])
        self.assertGreater(s["volume"], 0.5)  # a 2 mm wall of a 20 mm box (cm3)
        self.assertLess(s["volume"], 8.0)     # but not filled in

    def test_files(self):
        with tempfile.TemporaryDirectory() as d:
            r = printing.prepare(cube_model(), d, "cube", height_mm=40, cuts=[20])
            self.assertEqual([f.name for f in r["files"]], ["cube_part1.stl", "cube_part2.stl"])
            data = r["files"][0].read_bytes()
            n = struct.unpack_from("<I", data, 80)[0]
            self.assertEqual(len(data), 84 + 50 * n)
            r = printing.prepare(cube_model(), d, "cube", height_mm=40, cuts=[20], fmt="3mf")
            with zipfile.ZipFile(r["files"][0]) as z:
                root = ET.fromstring(z.read("3D/3dmodel.model"))
                self.assertIn("[Content_Types].xml", z.namelist())
            ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
            self.assertEqual(root.get("unit"), "millimeter")
            self.assertEqual(len(root.findall(".//m:object", ns)), 2)
            self.assertEqual(r["report"]["size"], [40, 40, 40])
        with self.assertRaises(ValueError):
            printing.prepare(importer.empty_model("x"), "unused", "x")


class PrintApiTests(AppTestCase):
    def test_print_a_library_item(self):
        (self.cars / "figures").mkdir()
        (self.cars / "figures" / "box.obj").write_text(
            "\n".join("v %g %g %g" % tuple(p) for p in CUBE_V) + "\n" +
            "\n".join("f %d %d %d" % tuple(t + 1) for t in CUBE_F) + "\n")
        self.scan()
        r = self.json("/api/print", {"id": "box", "heightMm": 50, "cuts": [25], "format": "stl"})
        self.assertEqual(len(r["files"]), 2)
        self.assertTrue(r["report"]["watertight"])
        self.assertTrue((self.out / "print" / "box" / "box_part1.stl").is_file())
        r = self.json("/api/print", {"id": "a_normal", "heightMm": 30, "format": "3mf"})
        self.assertEqual(r["files"], ["a_normal.3mf"])
        for bad in ({"id": "box", "heightMm": -1}, {"id": "box", "heightMm": 5000}, {"id": "box", "cuts": ["x"]},
                    {"id": "box", "format": "exe"}, {"id": "box", "cuts": list(range(20))}):
            with self.subTest(bad=bad):
                self.json("/api/print", bad, expect=400)


if __name__ == "__main__":
    unittest.main()
