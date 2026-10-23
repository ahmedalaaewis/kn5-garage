"""Server features: several car folders, scan flags, export options, previews, renders, Blender."""

import base64
import json
import time
import unittest
from pathlib import Path
from unittest import mock

import fixtures as fx
import glb_check
import app as garage
from kn5garage import blender
from kn5garage import server as garage_server
from test_app import AppTestCase

PNG = "data:image/png;base64," + base64.b64encode(fx.png_bytes()).decode()
WEBM = "data:video/webm;codecs=vp9;base64," + base64.b64encode(b"\x1aE\xdf\xa3 fake webm").decode()


class RootsTests(AppTestCase):
    def second_folder(self):
        other = self.dir / "more"
        fx.make_car(other, "a_normal")  # same folder name as in the first garage
        fx.make_car(other, "z_twin", ui=dict(fx.UI_CAR, name="Locked Car"))  # same display name as b_protected
        return other

    def test_settings_lists_roots_and_presets(self):
        s = self.json("/api/settings")
        self.assertEqual(s["roots"], [])
        self.assertEqual(set(s["presets"]), {"render", "game", "print", "obj", "fbx"})  # preview is internal

    def test_save_roots_dedupes_and_validates(self):
        s = self.json("/api/settings", {"roots": [" C:/a ", "C:/a", "", "C:/b"]})
        self.assertEqual(s["roots"], ["C:/a", "C:/b"])
        self.assertEqual(json.loads((self.dir / "settings.json").read_text())["roots"], ["C:/a", "C:/b"])
        self.json("/api/settings", {"roots": "C:/a"}, expect=400)
        self.json("/api/settings", {"roots": [1, 2]}, expect=400)

    def test_old_single_root_setting_is_migrated(self):
        p = self.dir / "old.json"
        p.write_text(json.dumps({"root": "D:/cars", "out": str(self.out)}))
        s = garage.Settings(p, self.out)
        self.assertEqual(s.data["roots"], ["D:/cars"])
        self.assertNotIn("root", s.data)

    def test_pick_root_adds_a_folder_once(self):
        self.json("/api/pick", {"what": "root"})
        r = self.json("/api/pick", {"what": "root"})
        self.assertEqual(r["roots"], [str(self.cars)])

    def test_scan_several_folders(self):
        other = self.second_folder()
        r = self.json("/api/scan", {"roots": [str(self.cars), str(other)]})
        cars = {c["id"]: c for c in r["cars"]}
        self.assertIn("a_normal (2)", cars)
        self.assertEqual(cars["a_normal"]["root"], str(self.cars))
        self.assertEqual(cars["a_normal (2)"]["root"], str(other))
        self.assertEqual(Path(cars["a_normal (2)"]["folder"]), other / "a_normal")
        self.assertIn("a_normal (2)", cars["a_normal"]["duplicates"])  # same folder name
        self.assertIn("z_twin", cars["b_protected"]["duplicates"])  # same display name
        # the fixture cars share one model file, so they're also identical copies
        self.assertEqual(cars["a_normal"]["identical"], ["a_normal (2)", "d_broken_json", "z_twin"])
        self.assertEqual(cars["b_protected"]["identical"], [])  # its model differs
        self.assertEqual(cars["c_encrypted"]["identical"], [])
        # both copies can be opened and exported separately
        self.assertEqual(self.json("/api/car?id=a_normal%20(2)")["path"], str(other / "a_normal"))
        self.assertEqual(json.loads((self.dir / "settings.json").read_text())["roots"], [str(self.cars), str(other)])

    def test_identical_found_under_another_name_and_not_for_same_size(self):
        other = self.dir / "renamed"
        fx.make_car(other, "totally_different_name", ui=dict(fx.UI_CAR, name="Another Name"))
        same_size = bytearray((self.cars / "a_normal" / "a_normal.kn5").read_bytes())
        same_size[-1] ^= 0xFF  # same length, different content
        fx.make_car(other, "lookalike", ui=dict(fx.UI_CAR, name="Lookalike"))
        (other / "lookalike" / "lookalike.kn5").write_bytes(bytes(same_size))
        cars = {c["id"]: c for c in self.json("/api/scan", {"roots": [str(self.cars), str(other)]})["cars"]}
        self.assertIn("totally_different_name", cars["a_normal"]["identical"])
        self.assertIn("totally_different_name", cars["a_normal"]["duplicates"])
        self.assertNotIn("lookalike", cars["a_normal"]["identical"])

    def test_rescan_uses_cache_and_notices_changes(self):
        self.scan()
        self.assertTrue((self.out / ".cache" / "scan.json").is_file())
        with mock.patch.object(garage_server.carinfo, "car_summary") as summary:
            self.assertEqual(len(self.scan()["cars"]), 4)
        summary.assert_not_called()  # nothing changed: everything came from the cache
        ui = self.cars / "a_normal" / "ui" / "ui_car.json"
        ui.write_text(json.dumps(dict(fx.UI_CAR, name="Renamed Racer")), encoding="utf-8")
        import os
        st = ui.stat()
        os.utime(ui, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        cars = {c["id"]: c for c in self.scan()["cars"]}
        self.assertEqual(cars["a_normal"]["name"], "Renamed Racer")

    def test_scan_same_folder_twice_counts_once(self):
        r = self.json("/api/scan", {"roots": [str(self.cars), str(self.cars) + " "]})
        self.assertEqual(len(r["cars"]), 4)

    def test_scan_uses_saved_roots_and_rejects_bad_ones(self):
        self.json("/api/settings", {"roots": [str(self.cars)]})
        self.assertEqual(len(self.json("/api/scan", {})["cars"]), 4)
        self.json("/api/scan", {"roots": []}, expect=400)
        status, data, _ = self.request("/api/scan", {"roots": [str(self.cars), str(self.dir / "gone")]})
        self.assertEqual(status, 400)
        self.assertIn("gone", json.loads(data)["error"])

    def test_scan_flags(self):
        lod_dir = self.dir / "flags"
        fx.make_car(lod_dir, "lod_anim", lod=True, animations={"door": fx.ksanim_bytes({"STEER_HR": fx.door_anim()})})
        cars = {c["id"]: c for c in self.json("/api/scan", {"roots": [str(self.cars), str(lod_dir)]})["cars"]}
        self.assertTrue(cars["lod_anim"]["hasLod"])
        self.assertTrue(cars["lod_anim"]["hasAnimations"])
        self.assertFalse(cars["a_normal"]["hasLod"])
        self.assertEqual(cars["a_normal"]["issues"], [])
        self.assertIn("model can't be read", cars["c_encrypted"]["issues"])
        self.assertIn("no ui_car.json (name, specs)", cars["c_encrypted"]["issues"])
        self.assertIn("no skins folder", cars["d_broken_json"]["issues"])


class ExportOptionTests(AppTestCase):
    def convert(self, body, expect_ok=True):
        self.scan()
        j = self.wait_job(self.json("/api/convert", body)["job"])
        res = j["results"][0]
        if expect_ok:
            self.assertTrue(res["ok"], res)
        return res

    def test_result_has_report_preset_and_blender_script(self):
        res = self.convert({"ids": ["a_normal"]})
        self.assertEqual(res["preset"], "render")
        self.assertTrue(any(i["text"] == "No problems found" for i in res["report"]))
        self.assertEqual(res["script"], "a_normal__00_red_blender_setup.py")
        self.assertTrue((self.out / res["script"]).is_file())

    def test_blender_script_can_be_turned_off(self):
        res = self.convert({"ids": ["a_normal"], "blenderScript": False})
        self.assertNotIn("script", res)
        self.assertEqual(list(self.out.glob("*.py")), [])

    def test_print_preset_writes_stl_listed_as_not_viewable(self):
        res = self.convert({"ids": ["a_normal"], "preset": "print"})
        self.assertEqual(res["file"], "a_normal__00_red__print.stl")
        self.assertNotIn("script", res)  # no Blender script for STL
        files = {f["name"]: f for f in self.json("/api/outputs")["files"]}
        self.assertFalse(files["a_normal__00_red__print.stl"]["viewable"])
        self.assertEqual(self.request("/out/a_normal__00_red__print.stl")[0], 404)  # viewer only loads .glb

    def test_paint_options_reach_the_glb(self):
        res = self.convert({"ids": ["a_normal"], "preset": "game", "paint": "#0000ff", "paintMode": "solid"})
        self.assertEqual(res["file"], "a_normal__00_red__game.glb")
        g = glb_check.load(self.out / res["file"])
        paint = next(m for m in g.json["materials"] if m["extras"]["kn5_paint"])
        self.assertNotIn("baseColorTexture", paint["pbrMetallicRoughness"])
        self.assertEqual(paint["pbrMetallicRoughness"]["baseColorFactor"], [0.0, 0.0, 1.0, 1.0])

    def test_paint_materials_and_materials_list(self):
        self.scan()
        mats = self.json("/api/materials?id=a_normal")["materials"]
        self.assertEqual([m["name"] for m in mats], ["EXT_Carpaint", "Tyre", "Glass"])
        self.json("/api/materials?id=c_encrypted", expect=400)
        res = self.convert({"ids": ["a_normal"], "paintMaterials": ["Glass"]})
        self.assertTrue(any(i["text"] == "Car paint found: Glass" for i in res["report"]))

    def test_cancel_endpoint(self):
        self.scan()
        real = self.app.jobs.run_car

        def slow(car_id, car_dir, opts, log, progress):
            for i in range(100):
                progress(i / 100, "slow")
                time.sleep(0.01)
            return real(car_id, car_dir, opts, log, progress)

        self.app.jobs.run_car = slow
        job = self.json("/api/convert", {"ids": ["a_normal", "b_protected", "d_broken_json"]})["job"]
        self.json("/api/job/cancel", {"id": job})
        j = self.wait_job(job)
        self.assertTrue(j["cancelled"])
        self.json("/api/job/cancel", {"id": "nope"}, expect=404)

    def test_bad_options_rejected(self):
        self.scan()
        for body in ({"paintMaterials": "Glass"}, {"paintMaterials": [1]}, {"skin": ["x"]}):
            with self.subTest(body=body):
                self.json("/api/convert", dict(ids=["a_normal"], **body), expect=400)
        for body in ({"preset": "nope"}, {"preset": "preview"}, {"paint": "blue"}, {"paintMode": "glitter"}):
            with self.subTest(body=body):
                self.json("/api/convert", dict(ids=["a_normal"], **body), expect=400)


class PreviewTests(AppTestCase):
    def test_preview_is_made_once_and_served(self):
        self.scan()
        r = self.json("/api/preview?id=a_normal&skin=01_blue")
        self.assertTrue(r["url"].startswith("/preview/a_normal__01_blue__"))
        path = self.out / ".preview" / r["url"][len("/preview/"):]
        self.assertTrue(path.is_file())
        with mock.patch.object(garage_server.convert, "convert_car") as conv:
            self.assertEqual(self.json("/api/preview?id=a_normal&skin=01_blue")["url"], r["url"])
        conv.assert_not_called()  # cached, not rebuilt
        status, data, headers = self.request(r["url"])
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "model/gltf-binary")
        g = glb_check.Glb(data)
        self.assertEqual(g.problems, [])
        self.assertNotIn("animations", g.json)
        self.assertEqual(self.request(r["url"], token=False)[0], 403)

    def test_default_skin_and_errors(self):
        self.scan()
        self.assertIn("__default__", self.json("/api/preview?id=a_normal")["url"])
        self.json("/api/preview?id=ghost", expect=404)
        self.json("/api/preview?id=a_normal&skin=nope", expect=400)
        self.json("/api/preview?id=c_encrypted", expect=400)
        self.assertEqual(self.request("/preview/..%2fsettings.json")[0], 404)
        self.assertEqual(list(self.out.glob("*.glb")), [])  # previews stay out of the exports list


class RenderTests(AppTestCase):
    def test_frames_and_video_saved(self):
        r = self.json("/api/render", {"name": "car__skin", "index": 3, "data": PNG})
        self.assertEqual(Path(r["file"]), Path("renders/car__skin/frame_0003.png"))
        self.assertEqual((self.out / r["file"]).read_bytes(), fx.png_bytes())
        v = self.json("/api/render", {"name": "car__skin", "data": WEBM})
        self.assertEqual(Path(v["file"]).name, "turntable.webm")
        self.assertTrue((self.out / v["file"]).read_bytes().startswith(b"\x1aE\xdf\xa3"))

    def test_name_cannot_escape_renders_folder(self):
        r = self.json("/api/render", {"name": "../../evil", "index": 0, "data": PNG})
        target = (self.out / r["file"]).resolve()
        self.assertIn((self.out / "renders").resolve(), target.parents)

    def test_bad_render_input(self):
        for body in ({"data": "not a data url"}, {"data": "data:text/html;base64,PGI+"},
                     {"data": "data:image/png;base64,***"}, {"data": PNG, "index": -1},
                     {"data": PNG, "index": "1"}, {"data": 5}):
            with self.subTest(body=str(body)[:40]):
                self.json("/api/render", dict(name="x", **body), expect=400)

    def test_open_renders_folder(self):
        self.json("/api/open", {"what": "renders"})
        self.assertEqual(self.opened[-1], self.out / "renders")
        self.assertTrue((self.out / "renders").is_dir())


class BlenderApiTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.launched = []
        self.exe = self.dir / "blender.exe"
        self.app.launch_blender = lambda exe, script: self.launched.append((exe, script))
        self.app.pick_file = lambda: str(self.exe)
        patcher = mock.patch.object(blender, "candidates", lambda: [])  # ignore a real Blender on this PC
        patcher.start()
        self.addCleanup(patcher.stop)

    def export(self):
        self.scan()
        return self.wait_job(self.json("/api/convert", {"ids": ["a_normal"], "blenderScript": False})["job"])["results"][0]

    def test_not_found_then_located(self):
        self.assertEqual(self.json("/api/blender"), {"found": False, "path": None})
        self.json("/api/blender/locate", {}, expect=400)  # picked file doesn't exist
        self.exe.write_bytes(b"MZ")
        r = self.json("/api/blender/locate", {})
        self.assertEqual(r, {"found": True, "path": str(self.exe)})
        self.assertEqual(json.loads((self.dir / "settings.json").read_text())["blender"], str(self.exe))

    def test_locate_cancelled(self):
        self.app.pick_file = lambda: None
        self.assertFalse(self.json("/api/blender/locate", {})["found"])

    def test_open_without_blender_writes_script_only(self):
        res = self.export()
        r = self.json("/api/blender/open", {"file": res["file"]})
        self.assertFalse(r["launched"])
        self.assertIn("Locate Blender", r["message"])
        self.assertTrue((self.out / r["script"]).is_file())
        self.assertEqual(self.launched, [])

    def test_open_launches_blender_with_script(self):
        res = self.export()
        self.exe.write_bytes(b"MZ")
        self.app.settings.update(blender=str(self.exe))
        r = self.json("/api/blender/open", {"file": res["file"]})
        self.assertTrue(r["launched"])
        # compare real files: Windows may give the same temp folder a short name (RUNNER~1)
        [(exe, script)] = self.launched
        self.assertEqual(Path(exe).resolve(), self.exe.resolve())
        self.assertEqual(Path(script).resolve(), (self.out / r["script"]).resolve())

    def test_open_refuses_other_files(self):
        self.export()
        for name in ("missing.glb", "../settings.json", "a_normal__00_red_blender_setup.py", ""):
            with self.subTest(name=name):
                self.json("/api/blender/open", {"file": name}, expect=404)


class BlenderModuleTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_setup_script_is_valid_python_and_finds_the_glb_next_to_it(self):
        glb = self.dir / "my car's ü.glb"
        glb.write_bytes(b"glTF")
        script = blender.write_setup_script(glb, 'evil"name\n')
        self.assertEqual(script.name, "my car's ü_blender_setup.py")
        text = script.read_text(encoding="utf-8")
        compile(text, str(script), "exec")
        self.assertIn('GLB_NAME = "my car\'s \\u00fc.glb"', text)
        self.assertIn('bpy.data.collections.new("evil_name_")', text)
        # no absolute paths: the script can be shared without revealing folder names
        self.assertNotIn(str(self.dir), text)
        self.assertNotIn(str(Path.home()), text)

    def test_only_blender_executables_are_accepted(self):
        for name, ok in (("blender.exe", True), ("Blender", True), ("calc.exe", False), ("blender.bat", False)):
            with self.subTest(name=name):
                p = self.dir / name
                p.write_bytes(b"MZ")
                self.assertEqual(blender.looks_like_blender(p), ok)
                p.unlink()

    def test_saved_path_wins_and_missing_falls_back(self):
        exe = self.dir / "blender.exe"
        exe.write_bytes(b"MZ")
        (self.dir / "other").mkdir()
        other = self.dir / "other" / "blender.exe"
        other.write_bytes(b"MZ")
        with mock.patch.object(blender, "candidates", lambda: [other]):
            self.assertEqual(blender.find_blender(str(exe)), exe)
            self.assertEqual(blender.find_blender(str(self.dir / "gone.exe")), other)
            self.assertEqual(blender.find_blender(None), other)
        with mock.patch.object(blender, "candidates", lambda: [self.dir / "nope.exe"]):
            self.assertIsNone(blender.find_blender(None))


if __name__ == "__main__":
    unittest.main()
