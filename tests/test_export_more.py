"""Driver rig, lights, glass / chrome / carbon, OBJ, FBX (via Blender) and texture options."""

import tempfile
import unittest
from pathlib import Path

import numpy as np

import fixtures as fx
import glb_check
import kn5_to_glb as k


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.out = self.dir / "out"

    def tearDown(self):
        self.tmp.cleanup()

    def run_kn5(self, data, **kw):
        p = self.dir / "m.kn5"
        p.write_bytes(data)
        conv = k.Converter(k.read_kn5(p), {}, log=lambda m: None, **kw)
        report = conv.run(self.dir / "m.glb")
        g = glb_check.load(self.dir / "m.glb")
        self.assertEqual(g.problems, [])
        return g, [i["text"] for i in report]

    def materials(self, mats, **kw):
        meshes = [fx.mesh("m%d" % i, i) for i in range(len(mats))]
        g, report = self.run_kn5(fx.kn5_bytes(fx.dummy("r", meshes), {"d.png": fx.png_bytes()}, mats), **kw)
        return {m["name"]: m for m in g.json["materials"]}, g, report


class RigTests(Case):
    def test_driver_becomes_a_glTF_skin(self):
        g, report = self.run_kn5(fx.rigged_kn5())
        skins = g.json["skins"]
        self.assertEqual(len(skins), 1)
        names = [g.json["nodes"][j]["name"] for j in skins[0]["joints"]]
        self.assertEqual(names, ["DRIVER_ROOT", "DRIVER_HEAD"])
        node = g.world_nodes()["driver_body"][1]
        self.assertEqual(node["skin"], 0)
        prim = g.json["meshes"][node["mesh"]]["primitives"][0]
        np.testing.assert_allclose(g.accessor(prim["attributes"]["WEIGHTS_0"])[0], [0.5, 0.5, 0, 0])
        self.assertEqual(g.json["accessors"][prim["attributes"]["JOINTS_0"]]["componentType"], 5123)
        self.assertIn("Rigged: driver_body (2 bones)", report)

    def test_bind_pose_mismatch_falls_back_to_static(self):
        g, report = self.run_kn5(fx.rigged_kn5(break_bind=True))
        self.assertNotIn("skins", g.json)
        prim = g.json["meshes"][0]["primitives"][0]
        self.assertNotIn("JOINTS_0", prim["attributes"])
        self.assertTrue(any("exported as static meshes" in t for t in report))

    def test_missing_bone_falls_back_to_static(self):
        g, _ = self.run_kn5(fx.rigged_kn5(missing_bone=True))
        self.assertNotIn("skins", g.json)

    def test_rig_can_be_turned_off(self):
        g, _ = self.run_kn5(fx.rigged_kn5(), rig=False)
        self.assertNotIn("skins", g.json)

    def test_car_fixture_driver_without_bones_stays_static(self):
        g, report = self.run_kn5(fx.car_kn5())
        self.assertNotIn("skins", g.json)
        self.assertTrue(any("1 skinned part(s)" in t for t in report))


class MaterialKindTests(Case):
    def test_kinds_guessed_from_names(self):
        def kind(name, blend=0, shader="ksPerPixel"):
            return k.material_kind({"name": name, "blend": blend, "shader": shader})
        self.assertEqual(kind("Headlight_glass"), "light")
        self.assertEqual(kind("TAILLIGHT"), "light")
        self.assertEqual(kind("window_glass", blend=1), "glass")
        self.assertEqual(kind("anything", blend=1, shader="ksWindscreen"), "glass")
        self.assertEqual(kind("window_glass", blend=0), None)  # opaque: not see-through glass
        self.assertEqual(kind("Chrome_trim"), "chrome")
        self.assertEqual(kind("Carbon_hood"), "carbon")
        self.assertEqual(kind("Tyre"), None)

    def test_glass_chrome_carbon_materials(self):
        m, g, report = self.materials([fx.material("Glass_side", blend=1), fx.material("Chrome_trim"),
                                       fx.material("Carbon_splitter")])
        self.assertIn("KHR_materials_transmission", m["Glass_side"]["extensions"])
        self.assertEqual(m["Glass_side"]["alphaMode"], "BLEND")
        self.assertEqual(m["Chrome_trim"]["pbrMetallicRoughness"]["metallicFactor"], 1.0)
        self.assertIn("KHR_materials_clearcoat", m["Carbon_splitter"]["extensions"])
        for ext in ("KHR_materials_transmission", "KHR_materials_ior", "KHR_materials_clearcoat"):
            self.assertIn(ext, g.json["extensionsUsed"])
        self.assertIn("Glass: Glass_side", report)
        self.assertIn("Chrome: Chrome_trim", report)

    def test_lights_on_makes_light_materials_glow(self):
        mats = [fx.material("Headlight", maps=[("txDiffuse", "d.png")]), fx.material("Body")]
        off, _, report = self.materials(mats)
        self.assertNotIn("emissiveFactor", off["Headlight"])
        self.assertEqual(off["Headlight"]["extras"]["kn5_kind"], "light")
        self.assertIn("Lights: Headlight", report)
        on, _, _ = self.materials(mats, lights_on=True)
        h = on["Headlight"]
        self.assertEqual(h["emissiveFactor"], [1.0, 1.0, 1.0])
        self.assertEqual(h["emissiveTexture"], h["pbrMetallicRoughness"]["baseColorTexture"])
        self.assertGreaterEqual(h["extensions"]["KHR_materials_emissive_strength"]["emissiveStrength"], 4.0)
        self.assertNotIn("emissiveFactor", on["Body"])


class ObjTests(Case):
    def test_obj_mtl_and_textures(self):
        car = fx.make_car(self.dir)
        r = k.convert_car(car, self.out, log=lambda m: None, preset="obj", paint="#ff0000")
        self.assertEqual(r["path"].name, "testcar__00_red__obj.obj")
        obj = r["path"].read_text(encoding="utf-8").splitlines()
        self.assertEqual(obj[1], "mtllib testcar__00_red__obj.mtl")
        faces = [l for l in obj if l.startswith("f ")]
        self.assertEqual(len(faces), 96)
        nv = sum(1 for l in obj if l.startswith("v "))
        self.assertLessEqual(max(int(x.split("/")[0]) for f in faces for x in f.split()[1:]), nv)
        mtl = (self.out / "testcar__00_red__obj.mtl").read_text(encoding="utf-8")
        self.assertIn("newmtl EXT_Carpaint", mtl)
        self.assertIn("Kd 1.0000 0.0000 0.0000", mtl)  # the paint colour
        tex = [l.split()[1] for l in mtl.splitlines() if l.startswith("map_Kd")]
        self.assertTrue(tex)
        for t in tex:
            self.assertTrue((self.out / t).is_file(), t)
        # world space: the front wheel sits at x = 0.8 - 0.12 .. 0.8 + 0.12
        xs = [float(l.split()[1]) for l in obj if l.startswith("v ")]
        self.assertAlmostEqual(max(xs), 0.92, places=4)
        self.assertEqual(list(self.out.glob("*.part")), [])


class FbxTests(Case):
    def test_fbx_needs_blender(self):
        with self.assertRaisesRegex(k.Kn5Error, "needs Blender"):
            k.convert_car(fx.make_car(self.dir), self.out, log=lambda m: None, preset="fbx")

    def test_fbx_made_from_a_glb_by_the_converter(self):
        seen = []

        def fake_blender(glb, fbx):
            seen.append(glb_check.load(glb))
            Path(fbx).write_bytes(b"Kaydara FBX Binary  \x00")

        r = k.convert_car(fx.make_car(self.dir), self.out, log=lambda m: None, preset="fbx",
                          fbx_converter=fake_blender)
        self.assertEqual(r["path"].name, "testcar__00_red__fbx.fbx")
        self.assertTrue(r["path"].read_bytes().startswith(b"Kaydara"))
        self.assertEqual(seen[0].problems, [])
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["testcar__00_red__fbx.fbx"])

    def test_blender_failure_leaves_nothing(self):
        def broken(glb, fbx):
            raise k.Kn5Error("Blender crashed")
        with self.assertRaisesRegex(k.Kn5Error, "crashed"):
            k.convert_car(fx.make_car(self.dir), self.out, log=lambda m: None, preset="fbx", fbx_converter=broken)
        self.assertEqual(list(self.out.iterdir()), [])


class TextureOptionTests(Case):
    def big_car(self):
        return fx.make_car(self.dir, skins=(("00", {"body.dds": fx.dds_solid((10, 200, 30, 255), size=64)}),))

    def test_texture_size_override(self):
        r = k.convert_car(self.big_car(), self.out, log=lambda m: None, texture_size=256)
        g = glb_check.load(r["path"])
        self.assertEqual(g.image_named("body.dds").size, (64, 64))  # never scaled up
        r = k.convert_car(self.big_car(), self.out, log=lambda m: None, preset="game", texture_size=256)
        self.assertTrue(r["path"].is_file())
        with self.assertRaisesRegex(k.Kn5Error, "texture size"):
            k.convert_car(self.big_car(), self.out, log=lambda m: None, texture_size=300)

    def test_small_texture_size_shrinks(self):
        p = self.dir / "m.kn5"
        p.write_bytes(fx.kn5_bytes(fx.dummy("r", [fx.mesh("m", 0)]), {"t.dds": fx.dds_solid(size=64)},
                                   [fx.material("m", maps=[("txDiffuse", "t.dds")])]))
        k.Converter(k.read_kn5(p), {}, max_texture=32).run(self.dir / "o.glb")
        self.assertEqual(glb_check.load(self.dir / "o.glb").image_named("t.dds").size, (32, 32))

    def test_jpeg_for_colour_png_for_normals_and_alpha(self):
        mats = [fx.material("c", maps=[("txDiffuse", "c.dds"), ("txNormal", "n.dds")]),
                fx.material("a", maps=[("txDiffuse", "a.dds")])]
        tex = {"c.dds": fx.dds_solid((200, 10, 10, 255), size=16), "n.dds": fx.dds_solid((128, 128, 255, 255), size=16),
               "a.dds": fx.dds_uncompressed(fx.gradient_pixels())}
        g, _ = self.run_kn5(fx.kn5_bytes(fx.dummy("r", [fx.mesh("m0", 0), fx.mesh("m1", 1)]), tex, mats),
                            jpeg_quality=85)
        mime = {im["name"]: im["mimeType"] for im in g.json["images"]}
        self.assertEqual(mime, {"c.dds": "image/jpeg", "n.dds": "image/png", "a.dds": "image/png"})
        r, gg, b = g.image_named("c.dds").convert("RGB").getpixel((0, 0))
        self.assertLess(abs(r - 200) + abs(gg - 10) + abs(b - 10), 12)

    def test_bad_jpeg_quality(self):
        with self.assertRaisesRegex(k.Kn5Error, "JPEG quality"):
            k.convert_car(fx.make_car(self.dir), self.out, log=lambda m: None, jpeg_quality=20)


if __name__ == "__main__":
    unittest.main()
