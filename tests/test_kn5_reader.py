import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np

import fixtures as fx
import kn5_to_glb as k


class Kn5TestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, data, name="m.kn5"):
        p = self.dir / name
        p.write_bytes(data)
        return p


class ReadKn5Tests(Kn5TestCase):
    def test_reads_textures_materials_and_node_tree(self):
        d = k.read_kn5(self.write(fx.car_kn5()))
        self.assertEqual(d["version"], 6)
        self.assertEqual(set(d["textures"]), {"body.dds", "body_nm.dds", "wheel.dds"})
        self.assertEqual([m["name"] for m in d["materials"]], ["EXT_Carpaint", "Tyre", "Glass"])
        paint = d["materials"][0]
        self.assertEqual(paint["shader"], "ksPerPixelMultiMap")
        self.assertEqual(paint["maps"], {"txDiffuse": "body.dds", "txNormal": "body_nm.dds"})
        self.assertAlmostEqual(paint["props"]["ksSpecularEXP"][0], 80)
        self.assertEqual(d["materials"][2]["blend"], 1)
        root = d["root"]
        self.assertEqual(root["name"], "car.kn5")
        self.assertEqual([c["name"] for c in root["children"]],
                         ["BODY", "STEER_HR", "driver_skinned", "WHEEL_LF", "WHEEL_RF", "WHEEL_LR", "WHEEL_RR"])
        body_mesh = root["children"][0]["children"][0]
        self.assertEqual(body_mesh["verts"].shape, (24, 11))
        self.assertEqual(len(body_mesh["indices"]), 36)
        self.assertFalse(d["protected"])

    def test_dummy_matrix_is_read_row_major(self):
        d = k.read_kn5(self.write(fx.car_kn5()))
        wheel = d["root"]["children"][3]
        self.assertEqual(wheel["name"], "WHEEL_LF")
        self.assertEqual(tuple(round(v, 3) for v in wheel["matrix"][12:15]), (0.8, 0.33, 1.3))

    def test_version_5_has_no_extra_header_or_depth_mode(self):
        d = k.read_kn5(self.write(fx.car_kn5(version=5)))
        self.assertEqual(d["version"], 5)
        self.assertEqual(len(d["materials"]), 3)
        self.assertEqual(d["materials"][1]["maps"], {"txDiffuse": "wheel.dds"})

    def test_version_4_material_without_depth_mode(self):
        root = fx.dummy("r", [fx.mesh("m", 0)])
        data = fx.kn5_bytes(root, {}, [fx.material("a", version=4)], version=4)
        d = k.read_kn5(self.write(data))
        self.assertEqual(d["materials"][0]["name"], "a")

    def test_skinned_mesh_keeps_first_11_vertex_floats(self):
        verts, idx = fx.box_geometry()
        d = k.read_kn5(self.write(fx.kn5_bytes(fx.dummy("r", [fx.mesh("s", 0, verts, idx, skinned=True)]))))
        node = d["root"]["children"][0]
        self.assertEqual(node["cls"], 3)
        np.testing.assert_array_equal(node["verts"], verts)

    def test_mesh_children_are_read(self):
        child = fx.mesh("child", 0)
        d = k.read_kn5(self.write(fx.kn5_bytes(fx.dummy("r", [fx.mesh("parent", 0, children=[child])]))))
        self.assertEqual(d["root"]["children"][0]["children"][0]["name"], "child")

    def test_inactive_and_hidden_flags(self):
        root = fx.dummy("r", [fx.dummy("off", active=False), fx.mesh("hid", 0, visible=False)])
        d = k.read_kn5(self.write(fx.kn5_bytes(root)))
        self.assertFalse(d["root"]["children"][0]["active"])
        self.assertFalse(d["root"]["children"][1]["visible"])


class BadFileTests(Kn5TestCase):
    def test_wrong_magic(self):
        with self.assertRaisesRegex(k.Kn5Error, "not a kn5"):
            k.read_kn5(self.write(b"PK\x03\x04" + b"\0" * 100))

    def test_truncated_file(self):
        data = fx.car_kn5()
        for cut in (8, 30, len(data) // 2, len(data) - 1):
            with self.subTest(cut=cut), self.assertRaises(k.Kn5Error):
                k.read_kn5(self.write(data[:cut]))

    def test_absurd_string_length(self):
        data = b"sc6969" + struct.pack("<ii", 6, 0) + struct.pack("<i", 1) + struct.pack("<ii", 1, 10 ** 8)
        with self.assertRaisesRegex(k.Kn5Error, "string length"):
            k.read_kn5(self.write(data + b"\0" * 32))

    def test_absurd_counts_rejected_before_allocating(self):
        # 2 billion textures / vertices in a tiny file must fail fast, not try to read them
        data = b"sc6969" + struct.pack("<ii", 6, 0) + struct.pack("<i", 2_000_000_000)
        with self.assertRaisesRegex(k.Kn5Error, "bad count"):
            k.read_kn5(self.write(data + b"\0" * 64))
        verts = struct.pack("<i", 2) + fx.s("m") + struct.pack("<i", 0) + b"\x01" + b"\x01\x01\x00"
        verts += struct.pack("<i", 50_000_000)
        with self.assertRaisesRegex(k.Kn5Error, "bad count"):
            k.read_kn5(self.write(fx.kn5_bytes(verts + b"\0" * 64)))

    def test_deep_nesting_rejected(self):
        node = struct.pack("<i", 1) + fx.s("n") + struct.pack("<i", 0) + b"\x01" + b"\0" * 64
        for _ in range(400):  # each dummy holds the next one
            node = struct.pack("<i", 1) + fx.s("n") + struct.pack("<i", 1) + b"\x01" + b"\0" * 64 + node
        with self.assertRaisesRegex(k.Kn5Error, "too deep"):
            k.read_kn5(self.write(fx.kn5_bytes(node)))

    def test_random_garbage_only_raises_kn5error(self):
        import random
        rnd = random.Random(1234)
        good = fx.car_kn5()
        for i in range(300):
            data = bytearray(good)
            for _ in range(rnd.randint(1, 20)):  # flip random bytes after the magic
                data[rnd.randrange(6, len(data))] = rnd.randrange(256)
            with self.subTest(i=i):
                try:
                    k.read_kn5(self.write(bytes(data)))
                except k.Kn5Error:
                    pass

    def test_unknown_node_class(self):
        root = struct.pack("<i", 9) + fx.s("x") + struct.pack("<i", 0) + b"\x01"
        with self.assertRaisesRegex(k.Kn5Error, "unknown node type 9"):
            k.read_kn5(self.write(fx.kn5_bytes(root)))

    def test_empty_file(self):
        with self.assertRaises(k.Kn5Error):
            k.read_kn5(self.write(b""))


class ProtectionTests(Kn5TestCase):
    def test_protected_file_detected(self):
        self.assertTrue(k.read_kn5(self.write(fx.car_kn5(protected=True)))["protected"])

    def test_trailer_alone_marks_protected(self):
        data = fx.kn5_bytes(fx.dummy("r"), {"a.dds": fx.dds_solid(size=16)}, trailer=fx.s("acd.checksum") + b"x" * 50)
        self.assertTrue(k.read_kn5(self.write(data))["protected"])

    def test_no_textures_is_not_protected(self):
        self.assertFalse(k.read_kn5(self.write(fx.kn5_bytes(fx.dummy("r"))))["protected"])

    def test_probe_matches_full_read(self):
        for protected in (False, True):
            with self.subTest(protected=protected):
                p = self.write(fx.car_kn5(protected=protected), "p%d.kn5" % protected)
                probe = k.probe_kn5(p)
                self.assertTrue(probe["readable"])
                self.assertEqual(probe["protected"], protected)
                self.assertEqual(probe["textures"], 3)
                self.assertEqual(probe["version"], 6)

    def test_probe_encrypted_and_damaged(self):
        self.assertFalse(k.probe_kn5(self.write(b"\x13\x37" * 50, "enc.kn5"))["readable"])
        damaged = b"sc6969" + struct.pack("<iii", 6, 0, 5) + struct.pack("<ii", 1, -4)
        info = k.probe_kn5(self.write(damaged, "dmg.kn5"))
        self.assertFalse(info["readable"])
        self.assertIn("damaged", info["reason"])
        self.assertFalse(k.probe_kn5(self.dir / "missing.kn5")["readable"])


if __name__ == "__main__":
    unittest.main()
