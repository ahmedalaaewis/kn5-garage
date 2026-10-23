"""The kn5 writer: read -> write gives back the same bytes, and edits come out as meant."""

import struct
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

import fixtures as fx
import glb_check
from kn5garage import convert, kn5, kn5write

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import make_demo_garage as demo  # noqa: E402


class RoundTripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def roundtrip(self, data):
        p = self.dir / "in.kn5"
        p.write_bytes(data)
        out = kn5write.write_kn5(kn5.read_kn5(p))
        self.assertEqual(len(out), len(data))
        self.assertEqual(out, data)

    def test_every_kind_of_model_comes_back_byte_for_byte(self):
        cases = {
            "car v6": fx.car_kn5(), "car v5": fx.car_kn5(version=5), "car v4": fx.car_kn5(version=4),
            "protected": fx.car_kn5(protected=True), "rigged driver": fx.rigged_kn5(), "lod": fx.lod_kn5(),
            "empty": fx.kn5_bytes(fx.dummy("r")),
        }
        for name, data in cases.items():
            with self.subTest(name):
                self.roundtrip(data)

    def test_demo_car_with_many_parts(self):
        car = demo.build_car(demo.CARS[0], self.dir / "cars")
        self.roundtrip((car / (demo.CARS[0]["id"] + ".kn5")).read_bytes())

    def test_odd_bytes_are_kept(self):
        # a non-UTF-8 name (old tools write Windows-1252), flag bytes other than 0/1, a NaN
        data = bytearray(fx.kn5_bytes(fx.dummy("r", [fx.mesh("caf\xe9", 0)]), materials=[fx.material("m")]))
        data = bytes(data).replace("caf\xe9".encode("utf-8"), b"caf\xe9\x20")  # same length, 1252 byte
        self.roundtrip(data)
        flags = bytearray(fx.kn5_bytes(fx.dummy("r", [fx.mesh("x", 0, visible=True)]), materials=[fx.material("m")]))
        i = flags.find(b"x") + 1 + 4 + 1  # name, child count, active -> the 3 flag bytes
        flags[i:i + 3] = b"\x02\x01\x00"
        self.roundtrip(bytes(flags))

    def test_trailer_kept(self):
        self.roundtrip(fx.kn5_bytes(fx.dummy("r"), trailer=b"\x01\x02whatever after the model"))


class EditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def edited(self, model):
        p = self.dir / "out.kn5"
        p.write_bytes(kn5write.write_kn5(model))
        back = kn5.read_kn5(p)
        conv = convert.Converter(back, {}, log=lambda m: None)
        conv.run(self.dir / "out.glb")
        g = glb_check.load(self.dir / "out.glb")
        self.assertEqual(g.problems, [])
        return back, g

    def model(self):
        p = self.dir / "car.kn5"
        p.write_bytes(fx.car_kn5())
        return kn5.read_kn5(p)

    def test_hide_rename_and_move(self):
        m = self.model()
        body = m["root"]["children"][0]
        body["name"] = "BODY_EDITED"
        body["matrix"] = list(body["matrix"])
        body["matrix"][13] += 0.5  # row-vector translation y
        body["children"][1]["visible"] = False  # glass
        back, g = self.edited(m)
        self.assertEqual(back["root"]["children"][0]["name"], "BODY_EDITED")
        self.assertFalse(back["root"]["children"][0]["children"][1]["visible"])
        self.assertAlmostEqual(g.world_nodes()["BODY_EDITED"][0][1, 3], 1.1, places=5)

    def test_add_part_material_and_texture(self):
        m = self.model()
        kn5write.add_texture(m, "decal.dds", fx.dds_solid((0, 255, 0, 255)))
        m["materials"].append(kn5write.new_material("Decal", maps={"txDiffuse": "decal.dds"}))
        verts, idx = fx.box_geometry(0.1, 0.1, 0.1)
        m["root"]["children"].append(kn5write.new_mesh("decal_box", verts, idx, len(m["materials"]) - 1))
        back, g = self.edited(m)
        self.assertIn("decal.dds", back["textures"])
        self.assertEqual(back["materials"][-1]["maps"], {"txDiffuse": "decal.dds"})
        self.assertIn("decal_box", g.world_nodes())
        sphere = struct.unpack("<4f", back["root"]["children"][-1]["bsphere_raw"])
        self.assertAlmostEqual(sphere[3], np.sqrt(3) * 0.1, places=4)

    def test_remove_part(self):
        m = self.model()
        m["root"]["children"] = [c for c in m["root"]["children"] if c["name"] != "STEER_HR"]
        back, g = self.edited(m)
        self.assertNotIn("STEER_HR", g.world_nodes())

    def test_too_many_vertices_refused(self):
        m = self.model()
        big = kn5write.new_mesh("big", np.zeros((70000, 11), np.float32), np.array([0, 1, 69999], np.uint32), 0)
        m["root"]["children"].append(big)
        with self.assertRaisesRegex(kn5.Kn5Error, "65535"):
            kn5write.write_kn5(m)


if __name__ == "__main__":
    unittest.main()
