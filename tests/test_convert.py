import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

import fixtures as fx
import glb_check
import kn5_to_glb as k


class ConvertTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.out = self.dir / "out"

    def tearDown(self):
        self.tmp.cleanup()

    def convert_kn5(self, data, skin=None, **kw):
        p = self.dir / "m.kn5"
        p.write_bytes(data)
        out = self.dir / "m.glb"
        logs = []
        k.Converter(k.read_kn5(p), skin or {}, log=logs.append, **kw).run(out)
        g = glb_check.load(out)
        self.assertEqual(g.problems, [])
        return g, logs

    def convert_car(self, **kw):
        car = fx.make_car(self.dir, **{k_: v for k_, v in kw.items() if k_ in ("protected", "skins", "lods_ini")})
        opts = {k_: v for k_, v in kw.items() if k_ in ("skin_name", "flip_green")}
        r = k.convert_car(car, self.out, log=lambda m: None, **opts)
        g = glb_check.load(r["path"])
        self.assertEqual(g.problems, [])
        return g, r

    def materials(self, mats, textures=None):
        meshes = [fx.mesh("m%d" % n, n) for n in range(len(mats))]
        g, logs = self.convert_kn5(fx.kn5_bytes(fx.dummy("r", meshes), textures or {}, mats))
        return {m["name"]: m for m in g.json["materials"]}, g, logs


class GeometryTests(ConvertTestCase):
    def test_glb_is_valid_and_keeps_every_node(self):
        g, _ = self.convert_kn5(fx.car_kn5())
        names = set(g.world_nodes())
        for n in ("car.kn5", "BODY", "body", "glass", "STEER_HR", "steer_mesh", "driver_skinned",
                  "WHEEL_LF", "tyre_LF", "WHEEL_RR", "tyre_RR"):
            self.assertIn(n, names)
        self.assertEqual(len(g.json["meshes"]), 8)

    def test_kn5_axes_are_kept_as_gltf_axes(self):
        # kn5 cars already use glTF's axes: +X is the car's left (WHEEL_LF), +Z the front
        g, _ = self.convert_kn5(fx.car_kn5())
        nodes = g.world_nodes()
        for name, x in (("WHEEL_LF", 0.8), ("WHEEL_RF", -0.8), ("STEER_HR", 0.4)):
            w, _ = nodes[name]
            self.assertAlmostEqual(w[0, 3], x, places=5, msg=name)
        w, node = nodes["body"]
        pos, _, _, _ = g.mesh_data(node)
        self.assertAlmostEqual(w[1, 3], 0.6, places=5)
        self.assertAlmostEqual(float(pos[:, 2].max()), 2.2, places=5)
        self.assertAlmostEqual(nodes["WHEEL_LF"][0][2, 3], 1.3, places=5)  # front wheels at +Z

    def test_row_vector_matrix_becomes_gltf_matrix(self):
        a = np.radians(30)
        rot = np.eye(4, dtype=np.float32)
        rot[:3, :3] = [[np.cos(a), 0, -np.sin(a)], [0, 1, 0], [np.sin(a), 0, np.cos(a)]]
        g, _ = self.convert_kn5(fx.kn5_bytes(fx.dummy("r", [fx.dummy("rot", matrix=rot, translation=(1, 2, 3))])))
        w, _ = g.world_nodes()["rot"]
        np.testing.assert_allclose(w[:3, :3], rot[:3, :3].T, atol=1e-6)  # row-vector -> column-vector
        np.testing.assert_allclose(w[:3, 3], (1, 2, 3), atol=1e-6)

    def test_winding_matches_normals_for_both_input_windings(self):
        for winding in ("cw", "ccw"):
            with self.subTest(winding=winding):
                v, i = fx.box_geometry(winding=winding)
                g, _ = self.convert_kn5(fx.kn5_bytes(fx.dummy("r", [fx.mesh("box", 0, v, i)])))
                self.assertEqual(g.ccw_share(g.world_nodes()["box"][1]), 1.0)

    def test_normals_are_normalised_and_zero_normals_replaced(self):
        v, i = fx.box_geometry()
        v[:, 3:6] *= 3.0
        v[0, 3:6] = 0
        g, _ = self.convert_kn5(fx.kn5_bytes(fx.dummy("r", [fx.mesh("box", 0, v, i)])))
        _, nrm, _, _ = g.mesh_data(g.world_nodes()["box"][1])
        np.testing.assert_allclose(np.linalg.norm(nrm, axis=1), 1, atol=1e-6)
        np.testing.assert_allclose(nrm[0], (0, 1, 0))

    def test_uvs_are_kept_unchanged(self):
        v, i = fx.box_geometry()
        g, _ = self.convert_kn5(fx.kn5_bytes(fx.dummy("r", [fx.mesh("box", 0, v, i)])))
        prim = g.json["meshes"][0]["primitives"][0]
        np.testing.assert_allclose(g.accessor(prim["attributes"]["TEXCOORD_0"]), v[:, 6:8])

    def test_empty_and_broken_meshes_are_skipped_but_nodes_kept(self):
        v, i = fx.box_geometry()
        bad_idx = i.copy()
        bad_idx[0] = 500
        root = fx.dummy("r", [
            fx.mesh("empty", 0, np.zeros((0, 11), np.float32), np.zeros(0, np.uint16)),
            fx.mesh("bad_index", 0, v, bad_idx),
            fx.mesh("partial", 0, v, i[:35]),
        ])
        g, _ = self.convert_kn5(fx.kn5_bytes(root, materials=[fx.material("m")]))
        nodes = g.world_nodes()
        self.assertNotIn("mesh", nodes["empty"][1])
        self.assertNotIn("mesh", nodes["bad_index"][1])
        _, _, tri, _ = g.mesh_data(nodes["partial"][1])
        self.assertEqual(len(tri), 11)  # leftover indices dropped

    def test_node_flags_go_to_extras(self):
        root = fx.dummy("r", [fx.dummy("off", active=False), fx.mesh("hid", 0, visible=False)])
        g, _ = self.convert_kn5(fx.kn5_bytes(root, materials=[fx.material("m")]))
        nodes = g.world_nodes()
        self.assertFalse(nodes["off"][1]["extras"]["kn5_active"])
        self.assertFalse(nodes["hid"][1]["extras"]["kn5_visible"])

    def test_progress_reports_every_mesh(self):
        calls = []
        self.convert_kn5(fx.car_kn5(), progress=lambda f, m: calls.append(f))
        self.assertEqual(len(calls), 8)
        self.assertAlmostEqual(calls[-1], 1.0)
        self.assertEqual(calls, sorted(calls))


class MaterialTests(ConvertTestCase):
    def test_alpha_modes(self):
        m, _, _ = self.materials([fx.material("opaque"), fx.material("blend", blend=1),
                                  fx.material("cutout", alpha_tested=True)])
        self.assertNotIn("alphaMode", m["opaque"])
        self.assertEqual(m["blend"]["alphaMode"], "BLEND")
        self.assertEqual(m["cutout"]["alphaMode"], "MASK")

    def test_roughness_from_specular_exponent(self):
        m, _, _ = self.materials([fx.material("glossy", props=[("ksSpecularEXP", 198, (0, 0, 0))]),
                                  fx.material("matte", props=[("ksSpecularEXP", 0, (0, 0, 0))])])
        self.assertAlmostEqual(m["glossy"]["pbrMetallicRoughness"]["roughnessFactor"], 0.1, places=3)
        self.assertEqual(m["matte"]["pbrMetallicRoughness"]["roughnessFactor"], 1.0)

    def test_bright_emissive_keeps_its_colour_and_strength(self):
        m, _, _ = self.materials([fx.material("lamp", props=[("ksEmissive", 0, (20, 10, 0))]),
                                  fx.material("dim", props=[("ksEmissive", 0, (0.5, 0.25, 0))])])
        self.assertEqual(m["lamp"]["emissiveFactor"], [1.0, 0.5, 0.0])
        self.assertEqual(m["lamp"]["extensions"]["KHR_materials_emissive_strength"]["emissiveStrength"], 20.0)
        self.assertEqual(m["dim"]["emissiveFactor"], [0.5, 0.25, 0.0])
        self.assertNotIn("extensions", m["dim"])

    def test_shader_name_kept_in_extras(self):
        m, _, _ = self.materials([fx.material("x", shader="ksTyres")])
        self.assertEqual(m["x"]["extras"]["kn5_shader"], "ksTyres")

    def test_shared_material_and_texture_written_once(self):
        root = fx.dummy("r", [fx.mesh("a", 0), fx.mesh("b", 0), fx.mesh("c", 1)])
        mats = [fx.material("m0", maps=[("txDiffuse", "t.png")]), fx.material("m1", maps=[("txDiffuse", "t.png")])]
        g, _ = self.convert_kn5(fx.kn5_bytes(root, {"t.png": fx.png_bytes()}, mats))
        self.assertEqual(len(g.json["materials"]), 2)
        self.assertEqual(len(g.json["images"]), 1)

    def test_missing_material_index_gives_no_material(self):
        g, _ = self.convert_kn5(fx.kn5_bytes(fx.dummy("r", [fx.mesh("m", 7)])))
        self.assertNotIn("material", g.json["meshes"][0]["primitives"][0])


class TextureTests(ConvertTestCase):
    def test_dds_becomes_png_and_png_jpeg_pass_through(self):
        png, jpg = fx.png_bytes(), fx.jpeg_bytes()
        mats = [fx.material("a", maps=[("txDiffuse", "a.dds")]), fx.material("b", maps=[("txDiffuse", "b.png")]),
                fx.material("c", maps=[("txDiffuse", "c.jpg")])]
        _, g, _ = self.materials(mats, {"a.dds": fx.dds_solid((10, 20, 30, 255)), "b.png": png, "c.jpg": jpg})
        by_name = {im["name"]: (i, im["mimeType"]) for i, im in enumerate(g.json["images"])}
        self.assertEqual(by_name["a.dds"][1], "image/png")
        self.assertEqual(g.image_named("a.dds").convert("RGB").getpixel((0, 0)), (10, 20, 30))
        self.assertEqual(g.image_bytes(by_name["b.png"][0]), png)
        self.assertEqual(by_name["c.jpg"][1], "image/jpeg")
        self.assertEqual(g.image_bytes(by_name["c.jpg"][0]), jpg)

    def test_normal_map_green_channel_flipped_by_default(self):
        mats = [fx.material("n", maps=[("txNormal", "n.dds")])]
        tex = {"n.dds": fx.dds_solid((128, 100, 255, 255))}
        _, g, _ = self.materials(mats, tex)
        self.assertEqual(g.image_named("n.dds").getpixel((0, 0))[:3], (128, 155, 255))

    def test_normal_map_green_kept_when_asked(self):
        p = self.dir / "m.kn5"
        p.write_bytes(fx.kn5_bytes(fx.dummy("r", [fx.mesh("m", 0)]), {"n.dds": fx.dds_solid((128, 100, 255, 255))},
                                   [fx.material("n", maps=[("txNormal", "n.dds")])]))
        k.Converter(k.read_kn5(p), {}, flip_normal_green=False).run(self.dir / "o.glb")
        g = glb_check.load(self.dir / "o.glb")
        self.assertEqual(g.image_named("n.dds").getpixel((0, 0))[:3], (128, 100, 255))

    def test_fully_opaque_alpha_is_dropped(self):
        _, g, _ = self.materials([fx.material("a", maps=[("txDiffuse", "a.dds")])], {"a.dds": fx.dds_solid()})
        self.assertEqual(g.image_named("a.dds").mode, "RGB")

    def test_real_alpha_is_kept(self):
        _, g, _ = self.materials([fx.material("a", maps=[("txDiffuse", "a.dds")])],
                                 {"a.dds": fx.dds_uncompressed(fx.gradient_pixels())})
        self.assertEqual(g.image_named("a.dds").mode, "RGBA")

    def test_undecodable_texture_is_logged_not_fatal(self):
        m, g, logs = self.materials([fx.material("a", maps=[("txDiffuse", "bad.dds")])], {"bad.dds": b"DDS garbage"})
        self.assertNotIn("baseColorTexture", m["a"]["pbrMetallicRoughness"])
        self.assertTrue(any("bad.dds" in line for line in logs))

    def test_missing_texture_is_skipped(self):
        m, _, _ = self.materials([fx.material("a", maps=[("txDiffuse", "nowhere.dds")])])
        self.assertNotIn("baseColorTexture", m["a"]["pbrMetallicRoughness"])


class FastDdsTests(unittest.TestCase):
    def test_matches_pillow_for_32_and_24_bit(self):
        px = fx.gradient_pixels(16, 8)
        for bits in (32, 24):
            with self.subTest(bits=bits):
                data = fx.dds_uncompressed(px, bits)
                fast = k.decode_dds_uncompressed(data)
                slow = Image.open(io.BytesIO(data))
                slow.load()
                self.assertEqual(fast.size, (16, 8))
                np.testing.assert_array_equal(np.asarray(fast), np.asarray(slow.convert(fast.mode)))

    def test_declines_formats_it_does_not_handle(self):
        img = Image.new("RGBA", (8, 8), (1, 2, 3, 4))
        buf = io.BytesIO()
        img.save(buf, "DDS", pixel_format="DXT5")
        self.assertIsNone(k.decode_dds_uncompressed(buf.getvalue()))
        self.assertIsNone(k.decode_dds_uncompressed(fx.png_bytes()))
        self.assertIsNone(k.decode_dds_uncompressed(fx.dds_solid()[:140]))  # truncated

    def test_handles_non_8_bit_masks(self):
        # 16-bit-per-pixel style masks inside a 32-bit pixel: R=5 bits, G=6 bits, B=5 bits
        data = bytearray(fx.dds_solid(size=2))
        import struct
        struct.pack_into("<IIII", data, 92, 0xF800, 0x07E0, 0x001F, 0)
        struct.pack_into("<I", data, 80, 0x40)
        for i in range(4):
            struct.pack_into("<I", data, 128 + i * 4, 0xFFFF)
        img = k.decode_dds_uncompressed(bytes(data))
        self.assertEqual(img.getpixel((0, 0)), (255, 255, 255))


class CarFolderTests(ConvertTestCase):
    def test_default_skin_is_first_and_overrides_texture(self):
        g, r = self.convert_car()
        self.assertEqual(r["skin"], "00_red")
        self.assertEqual(r["path"].name, "testcar__00_red.glb")
        self.assertEqual(g.image_named("body.dds").convert("RGB").getpixel((0, 0)), (220, 20, 20))

    def test_skin_file_matched_by_name_without_extension(self):
        g, r = self.convert_car(skin_name="01_BLUE")
        self.assertEqual(r["skin"], "01_blue")
        self.assertEqual(g.image_named("body.dds").convert("RGB").getpixel((0, 0)), (20, 40, 200))

    def test_skin_none_uses_model_textures(self):
        g, r = self.convert_car(skin_name="none")
        self.assertIsNone(r["skin"])
        self.assertEqual(r["path"].name, "testcar.glb")
        self.assertEqual(g.image_named("body.dds").convert("RGB").getpixel((0, 0)), (200, 200, 200))

    def test_unknown_skin_raises(self):
        car = fx.make_car(self.dir)
        with self.assertRaisesRegex(k.Kn5Error, "skin 'nope' not found"):
            k.convert_car(car, self.out, "nope", log=lambda m: None)

    def test_protected_car_flagged_and_still_exports_shape(self):
        g, r = self.convert_car(protected=True, skins=())
        self.assertTrue(r["protected"])
        self.assertEqual(len(g.json["meshes"]), 8)

    def test_output_inside_car_folder_refused(self):
        car = fx.make_car(self.dir)
        for out in (car, car / "export"):
            with self.subTest(out=out), self.assertRaisesRegex(k.Kn5Error, "must not be inside"):
                k.convert_car(car, out, log=lambda m: None)

    def test_folder_without_model(self):
        (self.dir / "empty").mkdir()
        with self.assertRaisesRegex(k.Kn5Error, "no .kn5"):
            k.convert_car(self.dir / "empty", self.out, log=lambda m: None)

    def test_output_name_sanitised(self):
        self.assertEqual(k.output_name(Path("car"), 'a:b?'), "car__a_b_.glb")


class CliTests(ConvertTestCase):
    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = k.main([str(a) for a in args])
        return code, out.getvalue()

    def test_cli_converts_a_cars_folder_and_reports_failures(self):
        garage = fx.make_garage(self.dir / "cars")
        code, text = self.run_cli(garage, "-o", self.out)
        self.assertEqual(code, 1)  # the encrypted car fails
        made = sorted(p.name for p in self.out.glob("*.glb"))
        self.assertEqual(made, ["a_normal__00_red.glb", "b_protected__00_red.glb", "d_broken_json.glb"])
        self.assertIn("Done: 3 of 4 converted", text)
        self.assertIn("protected by its author", text)

    def test_cli_single_car_success(self):
        car = fx.make_car(self.dir)
        code, _ = self.run_cli(car, "-o", self.out, "--skin", "none")
        self.assertEqual(code, 0)
        self.assertTrue((self.out / "testcar.glb").is_file())

    def test_cli_missing_folder(self):
        code, text = self.run_cli(self.dir / "nope", "-o", self.out)
        self.assertEqual(code, 1)
        self.assertIn("Folder not found", text)


if __name__ == "__main__":
    unittest.main()
