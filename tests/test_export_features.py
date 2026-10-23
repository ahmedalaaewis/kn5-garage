"""Export presets, paint, txMaps, TRS nodes, .ksanim animations, STL and the export report."""

import struct
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import numpy as np

import fixtures as fx
import glb_check
import kn5_to_glb as k


class FeatureTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.out = self.dir / "out"

    def tearDown(self):
        self.tmp.cleanup()

    def car(self, **kw):
        return fx.make_car(self.dir, **kw)

    def convert(self, car, **kw):
        r = k.convert_car(car, self.out, log=lambda m: None, **kw)
        if r["path"].suffix == ".glb":
            g = glb_check.load(r["path"])
            self.assertEqual(g.problems, [])
            return g, r
        return None, r

    def run_kn5(self, data, **kw):
        p = self.dir / "m.kn5"
        p.write_bytes(data)
        conv = k.Converter(k.read_kn5(p), {}, log=lambda m: None, **kw)
        report = conv.run(self.dir / "m.glb")
        g = glb_check.load(self.dir / "m.glb")
        self.assertEqual(g.problems, [])
        return g, report


def texts(report):
    return [i["text"] for i in report]


class PresetTests(FeatureTestCase):
    def test_presets_listed(self):
        self.assertEqual(set(k.PRESETS), {"render", "game", "print", "obj", "fbx", "preview"})
        for p in k.PRESETS.values():
            self.assertIn("label", p)

    def test_unknown_preset_and_paint_mode_refused(self):
        car = self.car()
        with self.assertRaisesRegex(k.Kn5Error, "unknown preset"):
            k.convert_car(car, self.out, log=lambda m: None, preset="nope")
        with self.assertRaisesRegex(k.Kn5Error, "paint mode"):
            k.convert_car(car, self.out, log=lambda m: None, paint_mode="glitter")

    def test_render_preset_is_full_model_with_plain_name(self):
        g, r = self.convert(self.car(lod=True))
        self.assertEqual(r["preset"], "render")
        self.assertEqual(r["path"].name, "testcar__00_red.glb")
        self.assertEqual(len(g.json["meshes"]), 8)

    def test_game_preset_uses_lod_model(self):
        g, r = self.convert(self.car(lod=True), preset="game")
        self.assertEqual(r["path"].name, "testcar__00_red__game.glb")
        self.assertEqual(r["kn5"], "testcar_lod_b.kn5")
        self.assertEqual([m["name"] for m in g.json["meshes"]], ["lod_body"])

    def test_game_preset_without_lod_falls_back_and_says_so(self):
        g, r = self.convert(self.car(), preset="game")  # the fixture's lod file is a tiny stub
        self.assertEqual(r["kn5"], "testcar.kn5")
        self.assertIn("No lower-detail LOD model in this car; used the full model", texts(r["report"]))

    def test_lods_ini_picks_lod_1(self):
        car = self.car(lod=True)
        (car / "data").mkdir()
        (car / "data" / "lods.ini").write_text("[LOD_0]\nFILE=testcar.kn5\n[LOD_1]\nFILE=testcar_lod_b.kn5\n")
        self.assertEqual(k.find_lod_kn5(car, 1).name, "testcar_lod_b.kn5")
        self.assertEqual(k.find_main_kn5(car).name, "testcar.kn5")

    def test_max_texture_shrinks_images(self):
        big = fx.dds_solid(size=64)
        data = fx.kn5_bytes(fx.dummy("r", [fx.mesh("m", 0)]), {"t.dds": big},
                            [fx.material("m", maps=[("txDiffuse", "t.dds")])])
        g, _ = self.run_kn5(data, max_texture=16)
        self.assertEqual(g.image_named("t.dds").size, (16, 16))

    def test_max_texture_zero_writes_no_images(self):
        g, _ = self.run_kn5(fx.car_kn5(), max_texture=0)
        self.assertEqual(g.json.get("images", []), [])

    def test_parallel_textures_give_the_same_file(self):
        p = self.dir / "m.kn5"
        p.write_bytes(fx.car_kn5())
        model = k.read_kn5(p)
        for workers in (1, 4):
            k.Converter(model, {}, log=lambda m: None, workers=workers).run(self.dir / ("w%d.glb" % workers))
        self.assertEqual((self.dir / "w1.glb").read_bytes(), (self.dir / "w4.glb").read_bytes())

    def test_no_partial_file_left_on_failure(self):
        car = self.car()
        with unittest.mock.patch.object(k.Converter, "add_animations", side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                k.convert_car(car, self.out, log=lambda m: None)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_chosen_paint_materials_override_the_guess(self):
        g, r = self.convert(self.car(), paint_names=["Tyre"], paint="#00ff00")
        mats = {m["name"]: m for m in g.json["materials"]}
        self.assertTrue(mats["Tyre"]["extras"]["kn5_paint"])
        self.assertFalse(mats["EXT_Carpaint"]["extras"]["kn5_paint"])
        self.assertEqual(mats["Tyre"]["pbrMetallicRoughness"]["baseColorFactor"], [0.0, 1.0, 0.0, 1.0])
        self.assertIn("Car paint found: Tyre", texts(r["report"]))
        _, r = self.convert(self.car(), paint_names=["NotThere"])
        self.assertIn("None of the chosen paint materials are in this model", texts(r["report"]))

    def test_model_materials_lists_names(self):
        from kn5garage.convert import model_materials
        mats = model_materials(self.car())
        self.assertEqual([m["name"] for m in mats], ["EXT_Carpaint", "Tyre", "Glass"])
        self.assertEqual([m["paint"] for m in mats], [True, False, False])

    def test_preview_preset_with_custom_name(self):
        g, r = self.convert(self.car(animations={"door": fx.ksanim_bytes({"STEER_HR": fx.door_anim()})}),
                            preset="preview", out_name="p.glb")
        self.assertEqual(r["path"].name, "p.glb")
        self.assertNotIn("animations", g.json)  # previews skip animations


class StlTests(FeatureTestCase):
    def read_stl(self, path):
        raw = path.read_bytes()
        n = struct.unpack_from("<I", raw, 80)[0]
        self.assertEqual(len(raw), 84 + 50 * n)
        rec = np.frombuffer(raw, dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")], count=n, offset=84)
        return rec

    def test_print_preset_writes_binary_stl_in_mm_z_up(self):
        _, r = self.convert(self.car(), preset="print")
        self.assertEqual(r["path"].name, "testcar__00_red__print.stl")
        rec = self.read_stl(r["path"])
        self.assertEqual(len(rec), 96)  # every part, world space, one mesh
        v = rec["v"].reshape(-1, 3)
        # tallest point: steering wheel top at 0.9 + 0.18 m (glTF Y) -> STL Z in mm
        self.assertAlmostEqual(float(v[:, 2].max()), 1080, delta=0.5)
        # front of the body at glTF +Z 2.2 m -> STL -Y
        self.assertAlmostEqual(float(v[:, 1].min()), -2200, delta=0.5)
        self.assertTrue(np.allclose(np.linalg.norm(rec["n"], axis=1), 1, atol=1e-3))

    def test_stl_report_skips_paint_and_animation_lines(self):
        _, r = self.convert(self.car(animations={"door": fx.ksanim_bytes({"STEER_HR": fx.door_anim()})}),
                            preset="print")
        joined = " ".join(texts(r["report"]))
        self.assertNotIn("paint", joined.lower())
        self.assertNotIn("Animations", joined)


class PaintTests(FeatureTestCase):
    def paint_material(self, g):
        return next(m for m in g.json["materials"] if m["extras"]["kn5_paint"])

    def test_paint_material_detected_with_clearcoat(self):
        g, r = self.convert(self.car())
        m = self.paint_material(g)
        self.assertEqual(m["name"], "EXT_Carpaint")
        self.assertIn("KHR_materials_clearcoat", m["extensions"])
        self.assertIn("KHR_materials_clearcoat", g.json["extensionsUsed"])
        self.assertIn("Car paint found: EXT_Carpaint", texts(r["report"]))
        tyre = next(m for m in g.json["materials"] if m["name"] == "Tyre")
        self.assertNotIn("extensions", tyre)

    def test_is_paint_material_names(self):
        for name, want in (("EXT_Carpaint", True), ("car_paint", True), ("body", True), ("Skin_00", True),
                           ("Tyre", False), ("glass", False), ("int_body_leather", False)):
            with self.subTest(name=name):
                self.assertEqual(k.is_paint_material({"name": name, "maps": {}}), want)

    def test_tint_keeps_livery_texture(self):
        g, _ = self.convert(self.car(), paint="#ff0000", paint_mode="tint")
        pbr = self.paint_material(g)["pbrMetallicRoughness"]
        self.assertIn("baseColorTexture", pbr)
        np.testing.assert_allclose(pbr["baseColorFactor"], [1, 0, 0, 1])

    def test_solid_replaces_livery(self):
        g, _ = self.convert(self.car(), paint="#808080", paint_mode="solid")
        pbr = self.paint_material(g)["pbrMetallicRoughness"]
        self.assertNotIn("baseColorTexture", pbr)
        # sRGB 128 -> linear ~0.2158
        np.testing.assert_allclose(pbr["baseColorFactor"][:3], [0.2158] * 3, atol=1e-3)

    def test_paint_does_not_touch_other_materials(self):
        g, _ = self.convert(self.car(), paint="#00ff00")
        tyre = next(m for m in g.json["materials"] if m["name"] == "Tyre")
        self.assertNotIn("baseColorFactor", tyre["pbrMetallicRoughness"])

    def test_hex_rgb(self):
        self.assertEqual(k.hex_rgb("#ff8000"), [1.0, 128 / 255, 0.0])
        self.assertEqual(k.hex_rgb("FF8000"), [1.0, 128 / 255, 0.0])
        for bad in ("red", "#fff", "#gg0000", ""):
            with self.subTest(bad=bad), self.assertRaises(k.Kn5Error):
                k.hex_rgb(bad)

    def test_no_paint_material_is_reported(self):
        data = fx.kn5_bytes(fx.dummy("r", [fx.mesh("m", 0)]), {}, [fx.material("Tyre")])
        _, report = self.run_kn5(data)
        self.assertTrue(any("No paint material" in t for t in texts(report)))


class TxMapsTests(FeatureTestCase):
    def test_txmaps_becomes_metallic_roughness_texture(self):
        maps_png = fx.png_bytes((0, 255, 255), 4)  # full gloss, full reflection
        mat = fx.material("chrome", maps=[("txDiffuse", "d.png"), ("txMaps", "maps.png")],
                          props=[("ksSpecularEXP", 198, (0, 0, 0)), ("fresnelMaxLevel", 0.5, (0, 0, 0))])
        data = fx.kn5_bytes(fx.dummy("r", [fx.mesh("m", 0)]), {"d.png": fx.png_bytes(), "maps.png": maps_png}, [mat])
        g, _ = self.run_kn5(data)
        pbr = g.json["materials"][0]["pbrMetallicRoughness"]
        self.assertIn("metallicRoughnessTexture", pbr)
        self.assertEqual((pbr["metallicFactor"], pbr["roughnessFactor"]), (1.0, 1.0))
        img = g.image_named("maps_metal_rough").convert("RGB")
        _, rough, metal = img.getpixel((0, 0))
        self.assertAlmostEqual(rough / 255, 0.1, delta=0.01)  # sqrt(2 / (198 * 1 + 2))
        self.assertAlmostEqual(metal / 255, 0.5, delta=0.01)  # reflection * fresnelMaxLevel

    def test_missing_txmaps_falls_back_to_factors(self):
        mat = fx.material("x", maps=[("txMaps", "gone.png")], props=[("ksSpecularEXP", 198, (0, 0, 0))])
        g, _ = self.run_kn5(fx.kn5_bytes(fx.dummy("r", [fx.mesh("m", 0)]), {}, [mat]))
        pbr = g.json["materials"][0]["pbrMetallicRoughness"]
        self.assertNotIn("metallicRoughnessTexture", pbr)
        self.assertAlmostEqual(pbr["roughnessFactor"], 0.1, places=3)


class NodeTests(FeatureTestCase):
    def test_translated_dummy_gets_translation_not_matrix(self):
        g, _ = self.run_kn5(fx.kn5_bytes(fx.dummy("r", [fx.dummy("moved", translation=(1, 2, 3))])))
        node = g.world_nodes()["moved"][1]
        self.assertEqual(node["translation"], [1, 2, 3])
        self.assertNotIn("matrix", node)
        self.assertNotIn("rotation", node)

    def test_rotation_and_scale_decomposed(self):
        a = np.radians(30)
        m = np.eye(4, dtype=np.float32)
        m[:3, :3] = np.array([[np.cos(a), 0, -np.sin(a)], [0, 1, 0], [np.sin(a), 0, np.cos(a)]]) * 2
        g, _ = self.run_kn5(fx.kn5_bytes(fx.dummy("r", [fx.dummy("rot", matrix=m)])))
        w, node = g.world_nodes()["rot"]
        np.testing.assert_allclose(node["scale"], [2, 2, 2], atol=1e-5)
        self.assertAlmostEqual(np.linalg.norm(node["rotation"]), 1, places=5)
        np.testing.assert_allclose(w[:3, :3], m[:3, :3].T, atol=1e-5)

    def test_sheared_matrix_kept_as_matrix(self):
        m = np.eye(4, dtype=np.float32)
        m[1, 0] = 0.5  # shear
        g, _ = self.run_kn5(fx.kn5_bytes(fx.dummy("r", [fx.dummy("shear", matrix=m)])))
        node = g.world_nodes()["shear"][1]
        self.assertIn("matrix", node)
        self.assertNotIn("translation", node)

    def test_decompose_round_trip(self):
        t, r, s = k.decompose(np.eye(4).reshape(-1))
        np.testing.assert_allclose(t, 0)
        np.testing.assert_allclose(r, [0, 0, 0, 1])
        np.testing.assert_allclose(s, 1)
        self.assertIsNone(k.decompose(np.zeros(16)))


class AnimationTests(FeatureTestCase):
    def test_read_ksanim_v2(self):
        p = self.dir / "a.ksanim"
        p.write_bytes(fx.ksanim_bytes({"STEER_HR": fx.door_anim(4)}))
        tr = k.read_ksanim(p)["STEER_HR"]
        self.assertEqual(tr["t"].shape, (4, 3))
        self.assertEqual(tr["r"].shape, (4, 4))
        np.testing.assert_allclose(tr["t"][-1], [0.4, 1.0, 0.3], atol=1e-6)

    def test_read_ksanim_v1_matrices(self):
        m = np.eye(4, dtype=np.float32)
        m[3, :3] = (1, 2, 3)
        p = self.dir / "a.ksanim"
        p.write_bytes(fx.ksanim_bytes({"node": [m.reshape(-1), m.reshape(-1)]}, version=1))
        tr = k.read_ksanim(p)["node"]
        np.testing.assert_allclose(tr["t"], [[1, 2, 3], [1, 2, 3]])
        np.testing.assert_allclose(tr["r"][0], [0, 0, 0, 1])

    def test_bad_ksanim_skipped_with_log(self):
        car = self.car(animations={"good": fx.ksanim_bytes({"STEER_HR": fx.door_anim()}),
                                   "bad": struct.pack("<i", 9)})
        logs = []
        anims = k.load_animations(car, logs.append)
        self.assertEqual(list(anims), ["good"])
        self.assertTrue(any("bad.ksanim" in line for line in logs))

    def test_animations_written_to_glb(self):
        car = self.car(animations={"steer": fx.ksanim_bytes({"STEER_HR": fx.door_anim(5), "NOT_THERE": fx.door_anim(2)})})
        g, r = self.convert(car)
        anims = g.json["animations"]
        self.assertEqual([a["name"] for a in anims], ["steer"])
        target = {ch["target"]["node"] for ch in anims[0]["channels"]}
        self.assertEqual({g.json["nodes"][i]["name"] for i in target}, {"STEER_HR"})  # unknown node ignored
        self.assertEqual({ch["target"]["path"] for ch in anims[0]["channels"]}, {"translation", "rotation", "scale"})
        inp = g.json["accessors"][anims[0]["samplers"][0]["input"]]
        self.assertEqual(inp["count"], 5)
        self.assertAlmostEqual(inp["max"][0], 4 / 30, places=5)
        self.assertIn("Animations: steer", texts(r["report"]))


class ReportTests(FeatureTestCase):
    def test_clean_car_report(self):
        _, r = self.convert(self.car())
        t = texts(r["report"])
        self.assertEqual(t[0], "No problems found")
        self.assertIn("96 triangles in 8 parts", t)
        self.assertTrue(all(i["level"] in ("ok", "info", "warn") for i in r["report"]))

    def test_missing_and_broken_textures_are_warnings(self):
        mats = [fx.material("a", maps=[("txDiffuse", "gone.dds")]), fx.material("b", maps=[("txDiffuse", "bad.dds")])]
        data = fx.kn5_bytes(fx.dummy("r", [fx.mesh("m0", 0), fx.mesh("m1", 1), fx.mesh("m2", 9)]),
                            {"bad.dds": b"DDS junk"}, mats)
        _, report = self.run_kn5(data)
        warn = [i["text"] for i in report if i["level"] == "warn"]
        self.assertTrue(any("not found: gone.dds" in w for w in warn))
        self.assertTrue(any("couldn't be decoded: bad.dds" in w for w in warn))
        self.assertTrue(any("no material" in w for w in warn))
        self.assertNotIn("No problems found", texts(report))

    def test_protected_car_warned(self):
        _, r = self.convert(self.car(protected=True, skins=()))
        self.assertTrue(any(i["level"] == "warn" and "Protected" in i["text"] for i in r["report"]))


if __name__ == "__main__":
    unittest.main()
