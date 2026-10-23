"""Favourites and tags, disk space, skin gallery, CSP / car data, export history,
saved presets and the new export options over the API."""

import json
import unittest
from pathlib import Path
from unittest import mock

import fixtures as fx
from kn5garage import blender, carinfo
from test_app import AppTestCase


class FavouriteTagTests(AppTestCase):
    def test_favourite_and_tags_are_kept_by_folder(self):
        self.scan()
        r = self.json("/api/carmeta", {"id": "a_normal", "favourite": True, "tags": [" GT3 ", "gt3", "My  pick", ""]})
        self.assertEqual(r, {"favourite": True, "tags": ["GT3", "My pick"], "allTags": ["GT3", "My pick"]})
        cars = {c["id"]: c for c in self.scan()["cars"]}
        self.assertTrue(cars["a_normal"]["favourite"])
        self.assertEqual(cars["a_normal"]["userTags"], ["GT3", "My pick"])
        self.assertFalse(cars["b_protected"]["favourite"])
        saved = json.loads((self.dir / "library.json").read_text())
        self.assertEqual(len(saved["favourites"]), 1)
        self.json("/api/carmeta", {"id": "a_normal", "favourite": False, "tags": []})
        cars = {c["id"]: c for c in self.scan()["cars"]}
        self.assertFalse(cars["a_normal"]["favourite"])
        self.assertEqual(self.json("/api/settings")["tags"], [])

    def test_carmeta_validation(self):
        self.scan()
        self.json("/api/carmeta", {"id": "ghost", "favourite": True}, expect=404)
        self.json("/api/carmeta", {"id": "a_normal", "favourite": "yes"}, expect=400)
        self.json("/api/carmeta", {"id": "a_normal", "tags": "gt3"}, expect=400)
        long = self.json("/api/carmeta", {"id": "a_normal", "tags": ["x" * 100] + ["t%d" % i for i in range(30)]})
        self.assertEqual(len(long["tags"]), 20)
        self.assertEqual(len(long["tags"][0]), 40)


class SpaceAndSkinsTests(AppTestCase):
    def test_space_lists_every_car_biggest_first(self):
        self.scan()
        r = self.json("/api/space")
        self.assertEqual(len(r["cars"]), 4)
        totals = [c["total"] for c in r["cars"]]
        self.assertEqual(totals, sorted(totals, reverse=True))
        a = next(c for c in r["cars"] if c["id"] == "a_normal")
        self.assertEqual(a["total"], a["model"] + a["skins"] + a["other"])
        self.assertGreater(a["skins"], 0)
        self.assertGreater(a["model"], 0)
        self.assertEqual(r["total"], sum(totals))

    def test_skin_gallery(self):
        self.scan()
        skins = self.json("/api/skins")["skins"]
        a = [s for s in skins if s["car"] == "a_normal"]
        self.assertEqual([s["id"] for s in a], ["00_red", "01_blue"])
        self.assertEqual(a[0]["driver"], "Driver 00_red")
        self.assertEqual(a[0]["preview"], "skins/00_red/preview.jpg")

    def test_nothing_scanned(self):
        self.assertEqual(self.json("/api/space")["cars"], [])
        self.assertEqual(self.json("/api/skins")["skins"], [])


class CarDataTests(unittest.TestCase):
    def test_csp_features_car_data_and_variant(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            car = fx.make_car(t, ui=dict(fx.UI_CAR, parent="ks_base_car"))
            (car / "extension").mkdir()
            (car / "extension" / "ext_config.ini").write_text(
                "[ABOUT]\nAUTHOR=x\n[LIGHT_HEADLIGHT_0]\nCOLOR=1\n[EMISSIVE_BRAKE_0]\n[RAIN_FX]\n[REAL_MIRROR]\n"
                "[SHADER_REPLACEMENT_0]\nMATERIALS=x\n[LIGHT_TAIL_1]\n", encoding="utf-8")
            (car / "data").mkdir()
            (car / "data" / "car.ini").write_text("[BASIC]\nTOTALMASS=1250 ; with driver\n[FUEL]\nMAX_FUEL=90\n")
            (car / "data" / "drivetrain.ini").write_text("[TRACTION]\nTYPE=RWD\n[GEARS]\nCOUNT=6\n")
            (car / "data" / "engine.ini").write_text("[ENGINE_DATA]\nLIMITER=8500\n[TURBO_0]\nMAX_BOOST=1\n")
            (car / "ui" / "upgrade.png").write_bytes(fx.png_bytes())
            d = carinfo.car_details(car)
            self.assertEqual(d["csp"], ["Extra lights", "Glowing parts", "Rain and wipers", "Real mirrors",
                                        "Shader tweaks"])
            self.assertEqual(d["carData"], {"mass": "1250", "fuel": "90", "drive": "RWD", "gears": "6",
                                            "limiter": "8500", "turbo": True})
            self.assertEqual(d["parent"], "ks_base_car")
            self.assertEqual(d["upgradeIcon"], "ui/upgrade.png")
            self.assertTrue(d["hasExtension"])

    def test_missing_or_broken_ini(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            car = fx.make_car(t)
            self.assertEqual(carinfo.csp_features(car), [])
            self.assertEqual(carinfo.car_data(car), {})
            (car / "extension").mkdir()
            (car / "extension" / "ext_config.ini").write_bytes(b"\xff\xfe[[[ not ini \x00")
            self.assertEqual(carinfo.csp_features(car), [])


class HistoryPresetTests(AppTestCase):
    def test_history_records_each_export_with_its_options(self):
        self.scan()
        self.wait_job(self.json("/api/convert", {"ids": ["a_normal"], "preset": "game", "paint": "#112233",
                                                  "textureSize": 512, "lightsOn": True})["job"])
        self.wait_job(self.json("/api/convert", {"ids": ["c_encrypted"]})["job"])
        items = self.json("/api/history")["items"]
        self.assertEqual([i["id"] for i in items], ["c_encrypted", "a_normal"])  # newest first
        self.assertFalse(items[0]["ok"])
        self.assertTrue(items[1]["ok"])
        self.assertEqual(items[1]["options"], {"preset": "game", "paint": "#112233", "textureSize": 512,
                                               "lightsOn": True})
        self.assertEqual(items[1]["file"], "a_normal__00_red__game.glb")
        self.assertTrue((self.out / ".cache" / "history.json").is_file())
        self.assertEqual(self.json("/api/history/clear", {})["items"], [])

    def test_saved_presets(self):
        r = self.json("/api/presets/save", {"name": "  Red  game ", "options": {
            "preset": "game", "paint": "#ff0000", "textureSize": 1024, "lightsOn": True, "evil": "<x>",
            "jpegQuality": True}})
        self.assertEqual(r["savedPresets"], [{"name": "Red game", "options": {
            "preset": "game", "paint": "#ff0000", "textureSize": 1024, "lightsOn": True}}])
        self.json("/api/presets/save", {"name": "red GAME", "options": {"preset": "render"}})  # replaces
        self.assertEqual([p["name"] for p in self.json("/api/settings")["savedPresets"]], ["red GAME"])
        self.json("/api/presets/save", {"name": "x", "options": {"preset": "preview"}}, expect=400)
        self.json("/api/presets/save", {"name": "", "options": {"preset": "render"}}, expect=400)
        self.assertEqual(self.json("/api/presets/delete", {"name": "Red Game"})["savedPresets"], [])

    def test_new_export_options_validated(self):
        self.scan()
        for body in ({"textureSize": 300}, {"textureSize": "1024"}, {"jpegQuality": 10}, {"jpegQuality": True}):
            with self.subTest(body=body):
                self.json("/api/convert", dict(ids=["a_normal"], **body), expect=400)

    def test_fbx_needs_blender_and_uses_it(self):
        self.scan()
        with mock.patch.object(blender, "candidates", lambda: []):
            self.json("/api/convert", {"ids": ["a_normal"], "preset": "fbx"}, expect=400)
            exe = self.dir / "blender.exe"
            exe.write_bytes(b"MZ")
            self.app.settings.update(blender=str(exe))
            calls = []

            def fake_fbx(exe_path, glb, fbx):
                calls.append(Path(exe_path).name)
                Path(fbx).write_bytes(b"Kaydara FBX Binary")

            with mock.patch.object(blender, "glb_to_fbx", fake_fbx):
                j = self.wait_job(self.json("/api/convert", {"ids": ["a_normal"], "preset": "fbx"})["job"])
        self.assertTrue(j["results"][0]["ok"], j["results"][0])
        self.assertEqual(calls, ["blender.exe"])
        names = {f["name"]: f for f in self.json("/api/outputs")["files"]}
        self.assertFalse(names["a_normal__00_red__fbx.fbx"]["viewable"])

    def test_language_setting(self):
        self.assertEqual(self.json("/api/settings", {"language": "it"})["language"], "it")
        self.json("/api/settings", {"language": "italian"}, expect=400)


if __name__ == "__main__":
    unittest.main()
