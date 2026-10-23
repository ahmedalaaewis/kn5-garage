import json
import tempfile
import unittest
from pathlib import Path

import fixtures as fx
import kn5_to_glb as k
from kn5garage import carinfo


class TmpDirCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()


class LenientJsonTests(TmpDirCase):
    def read(self, raw):
        p = self.dir / "x.json"
        p.write_bytes(raw if isinstance(raw, bytes) else raw.encode("utf-8"))
        return carinfo.read_json_lenient(p)

    def test_plain(self):
        self.assertEqual(self.read('{"a": 1}'), {"a": 1})

    def test_bom_and_trailing_commas(self):
        self.assertEqual(self.read('﻿{"a": [1, 2,], "b": "x",}'), {"a": [1, 2], "b": "x"})

    def test_raw_newlines_inside_strings(self):
        self.assertEqual(self.read('{"d": "line1\nline2\tx"}')["d"], "line1\nline2\tx")

    def test_cp1252_text(self):
        self.assertEqual(self.read('{"name": "Citro\xebn"}'.encode("cp1252"))["name"], "Citro\xebn")

    def test_badly_broken_falls_back_to_pairs(self):
        self.assertEqual(self.read('{"name": "Car", "author": "Me" "oops": }'), {"name": "Car", "author": "Me"})

    def test_missing_or_non_object(self):
        self.assertEqual(carinfo.read_json_lenient(self.dir / "missing.json"), {})
        self.assertEqual(self.read("[1, 2]"), {})


class TextTests(unittest.TestCase):
    def test_clean_text(self):
        self.assertEqual(carinfo.clean_text("A<br>B<br/>C &amp; <b>D</b> N&#233;on"), "A\nB\nC & D N\xe9on")
        self.assertIsNone(carinfo.clean_text(None))
        self.assertIsNone(carinfo.clean_text("  "))
        self.assertEqual(carinfo.clean_text(1997), "1997")

    def test_clean_curve_drops_bad_points(self):
        self.assertEqual(carinfo.clean_curve([[0, 1], ["x", 2], [3], [4, "5"]]), [[0.0, 1.0], [4.0, 5.0]])
        self.assertEqual(carinfo.clean_curve("nope"), [])


class FindFilesTests(TmpDirCase):
    def test_main_kn5_from_lods_ini(self):
        car = fx.make_car(self.dir, lods_ini=True)
        (car / "zzz_huge.kn5").write_bytes(b"x" * 10 ** 6)
        self.assertEqual(k.find_main_kn5(car).name, "testcar.kn5")

    def test_main_kn5_largest_non_lod(self):
        car = self.dir / "c"
        car.mkdir()
        (car / "car.kn5").write_bytes(b"x" * 100)
        (car / "car_lod_a.kn5").write_bytes(b"x" * 1000)
        (car / "collider.kn5").write_bytes(b"x" * 1000)
        (car / "driver_test.kn5").write_bytes(b"x" * 1000)
        self.assertEqual(k.find_main_kn5(car).name, "car.kn5")

    def test_lods_ini_pointing_to_missing_file_falls_back(self):
        car = fx.make_car(self.dir, lods_ini=True)
        (car / "data" / "lods.ini").write_text("[LOD_0]\nFILE=gone.kn5\n")
        self.assertEqual(k.find_main_kn5(car).name, "testcar.kn5")

    def test_no_kn5(self):
        self.assertIsNone(k.find_main_kn5(self.dir))

    def test_find_cars_single_and_folder(self):
        garage = fx.make_garage(self.dir / "cars")
        self.assertEqual([c.name for c in k.find_cars(garage)],
                         ["a_normal", "b_protected", "c_encrypted", "d_broken_json"])
        self.assertEqual(k.find_cars(garage / "a_normal"), [garage / "a_normal"])


class SummaryTests(TmpDirCase):
    def setUp(self):
        super().setUp()
        self.garage = fx.make_garage(self.dir / "cars")

    def test_normal_car_summary(self):
        s = carinfo.car_summary(self.garage / "a_normal")
        self.assertEqual(s["name"], "Test Racer GT")
        self.assertEqual(s["brand"], "Testa")
        self.assertEqual(s["author"], "Jane Modder")
        self.assertEqual(s["year"], "2024")
        self.assertEqual(s["specs"]["bhp"], "500 bhp")
        self.assertEqual(s["status"], "ok")
        self.assertIsNone(s["statusText"])
        self.assertEqual(s["skinCount"], 2)
        self.assertEqual(s["preview"], "skins/00_red/preview.jpg")
        self.assertEqual(s["badge"], "ui/badge.png")
        json.dumps(s)  # must be serialisable for the UI

    def test_statuses(self):
        expect = {"b_protected": "protected", "c_encrypted": "encrypted", "d_broken_json": "ok"}
        for name, status in expect.items():
            with self.subTest(car=name):
                self.assertEqual(carinfo.car_summary(self.garage / name)["status"], status)
        self.assertEqual(carinfo.car_summary(self.garage / "e_not_a_car")["status"], "no-model")

    def test_protected_message_names_the_author(self):
        s = carinfo.car_summary(self.garage / "b_protected")
        self.assertIn("Secret Crew has protected this model", s["statusText"])
        self.assertIn("ask Secret Crew", s["statusText"])

    def test_broken_json_still_gives_name(self):
        s = carinfo.car_summary(self.garage / "d_broken_json")
        self.assertEqual(s["name"], "Broken, json")
        self.assertEqual(s["author"], "X")

    def test_missing_ui_uses_folder_name(self):
        s = carinfo.car_summary(self.garage / "c_encrypted")
        self.assertEqual(s["name"], "c_encrypted")
        self.assertIsNone(s["author"])

    def test_details(self):
        d = carinfo.car_details(self.garage / "a_normal")
        self.assertEqual(d["description"], "Fast\ncar & friends")
        self.assertEqual(d["powerCurve"][-1], [8000.0, 500.0])
        self.assertEqual([s["id"] for s in d["skins"]], ["00_red", "01_blue"])
        self.assertEqual(d["skins"][0]["name"], "00_RED")
        self.assertEqual(d["skins"][0]["driver"], "Driver 00_red")
        roles = {f["name"]: f["role"] for f in d["files"]}
        self.assertEqual(roles, {"a_normal.kn5": "main model", "a_normal_lod_b.kn5": "lod", "collider.kn5": "collider"})
        self.assertGreater(d["folderSize"], 0)
        json.dumps(d)


class ResolveFileTests(TmpDirCase):
    def test_only_images_inside_the_car(self):
        car = fx.make_car(self.dir)
        (self.dir / "secret.png").write_bytes(fx.png_bytes())
        self.assertIsNotNone(carinfo.resolve_car_file(car, "ui/badge.png"))
        self.assertIsNotNone(carinfo.resolve_car_file(car, "skins/00_red/preview.jpg"))
        for bad in ("../secret.png", "..\\secret.png", "testcar.kn5", "ui/ui_car.json", "ui/missing.png",
                    str(self.dir / "secret.png"), "", "skins/../../secret.png"):
            with self.subTest(path=bad):
                self.assertIsNone(carinfo.resolve_car_file(car, bad))


if __name__ == "__main__":
    unittest.main()
