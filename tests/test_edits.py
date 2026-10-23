"""Saving edits: new-car copies, edits of the original with backups and undo, ini / lut files,
and the specs editor (physics and menu specs)."""

import json
import tempfile
import unittest
from pathlib import Path

import fixtures as fx
from kn5garage import edits, physics


def make_physics(car, packed=False):
    d = car / "data"
    d.mkdir()
    (d / "car.ini").write_text("[HEADER]\nVERSION=2\n\n[BASIC]\nTOTALMASS=1250 ; kg with driver\n[FUEL]\nMAX_FUEL=80\n",
                               encoding="utf-8")
    (d / "engine.ini").write_text("[HEADER]\nPOWER_CURVE=power.lut\n[ENGINE_DATA]\nLIMITER=8000\n[TURBO_0]\nMAX_BOOST=0.5\n",
                                  encoding="utf-8")
    (d / "drivetrain.ini").write_text("[TRACTION]\nTYPE=RWD\n[GEARS]\nCOUNT=6\nFINAL=3.9\n", encoding="utf-8")
    (d / "power.lut").write_text("; rpm | Nm\n1000|200\n4000|400\n8000|300\n", encoding="utf-8")
    if packed:
        (car / "data.acd").write_bytes(b"packed")
    (car / "sfx").mkdir()
    (car / "sfx" / "GUIDs.txt").write_text("{abc} event:/cars/%s/engine_ext\n" % car.name, encoding="utf-8")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.car = fx.make_car(self.dir / "cars", "racer")
        self.backups = self.dir / "out" / "backups"

    def tearDown(self):
        self.tmp.cleanup()


class TextFormatTests(unittest.TestCase):
    def test_ini_set_keeps_everything_else(self):
        text = "; comment\n[BASIC]\nGRAPHICS_OFFSET=0,0,0\nTOTALMASS = 1250 ; with driver\n[FUEL]\nMAX_FUEL=80\n"
        out = edits.ini_set(text, "BASIC", "TOTALMASS", "1100")
        self.assertIn("TOTALMASS = 1100 ; with driver", out)
        self.assertIn("; comment", out)
        self.assertIn("GRAPHICS_OFFSET=0,0,0", out)
        out = edits.ini_set(out, "FUEL", "FUEL", "30")  # new key in an existing, last section
        self.assertTrue(out.rstrip().endswith("FUEL=30"))
        out = edits.ini_set(out, "BASIC", "NEW_KEY", "1")  # new key in a section that isn't last
        self.assertLess(out.index("NEW_KEY=1"), out.index("[FUEL]"))
        out = edits.ini_set(out, "TURBO_0", "MAX_BOOST", "1")  # new section
        self.assertIn("[TURBO_0]\nMAX_BOOST=1", out)

    def test_lut(self):
        pts = edits.read_lut("; header\n0|0\n1000 | 150.5 // c\nbad line\n2000|200\n")
        self.assertEqual(pts, [[0, 0], [1000, 150.5], [2000, 200]])
        self.assertEqual(edits.write_lut(pts), "0|0\n1000|150.5\n2000|200\n")


class NewCarTests(Base):
    def test_copy_is_independent_and_renamed(self):
        make_physics(self.car)
        dest = edits.copy_car(self.car, "racer_custom")
        self.assertTrue((dest / "racer.kn5").is_file())
        ui = json.loads((dest / "ui" / "ui_car.json").read_text(encoding="utf-8"))
        self.assertEqual(ui["name"], "Test Racer GT (custom)")
        self.assertEqual(ui["parent"], "racer")
        self.assertIn("/cars/racer_custom/", (dest / "sfx" / "GUIDs.txt").read_text(encoding="utf-8"))
        self.assertIn("/cars/racer/", (self.car / "sfx" / "GUIDs.txt").read_text(encoding="utf-8"))

    def test_packed_only_car_cannot_be_copied(self):
        (self.car / "data.acd").write_bytes(b"x")
        self.assertTrue(edits.copy_blockers(self.car))
        with self.assertRaisesRegex(edits.EditError, "data.acd"):
            edits.copy_car(self.car, "racer2")

    def test_copy_drops_acd_when_data_folder_exists(self):
        make_physics(self.car, packed=True)
        dest = edits.copy_car(self.car, "racer2")
        self.assertFalse((dest / "data.acd").exists())
        self.assertTrue((self.car / "data.acd").exists())

    def test_bad_names(self):
        for name in ("", "../escape", "a/b", "with space", "x" * 65, "racer"):
            with self.subTest(name=name), self.assertRaises(edits.EditError):
                edits.copy_car(self.car, name)


class OriginalWithBackupTests(Base):
    def test_backup_and_undo(self):
        make_physics(self.car)
        before = {p.relative_to(self.car).as_posix(): p.read_bytes() for p in self.car.rglob("*") if p.is_file()}
        t = edits.Target(self.car, "original", self.backups)
        t.write("data/car.ini", "changed")
        t.write("skins/new_skin/body.dds", b"new")
        t.remove("data/power.lut")
        res = t.finish("test edit")
        self.assertEqual(res["changed"], ["data/car.ini"])
        self.assertEqual(res["created"], ["skins/new_skin/body.dds"])
        self.assertEqual(res["removed"], ["data/power.lut"])
        items = edits.list_backups(self.backups, self.car)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["files"], 3)
        edits.restore_backup(self.backups, items[0]["id"])
        after = {p.relative_to(self.car).as_posix(): p.read_bytes() for p in self.car.rglob("*") if p.is_file()}
        self.assertEqual(after, before)
        self.assertEqual(edits.list_backups(self.backups), [])

    def test_writes_cannot_leave_the_car(self):
        t = edits.Target(self.car, "original", self.backups)
        for rel in ("../outside.txt", "..\\outside.txt", "C:/Windows/x.txt", "/etc/passwd"):
            with self.subTest(rel=rel), self.assertRaises(edits.EditError):
                t.write(rel, "x")
        self.assertFalse((self.dir / "cars" / "outside.txt").exists())

    def test_restore_rejects_tricks(self):
        for bid in ("../../x", "..", "a/../../b", "racer/../../x"):
            with self.subTest(bid=bid), self.assertRaises(edits.EditError):
                edits.restore_backup(self.backups, bid)


class SpecsTests(Base):
    def test_read_physics(self):
        self.assertEqual(physics.read_physics(self.car)["reason"], "missing")
        make_physics(self.car)
        p = physics.read_physics(self.car)
        self.assertEqual((p["mass"], p["fuel"], p["limiter"], p["final"], p["drive"]), (1250, 80, 8000, 3.9, "RWD"))
        self.assertEqual(p["turbos"], [{"section": "TURBO_0", "boost": 0.5}])
        self.assertEqual(p["curve"], [[1000, 200], [4000, 400], [8000, 300]])

    def test_edit_physics_and_menu_specs_as_new_car(self):
        make_physics(self.car)
        t = edits.Target(self.car, "new", new_id="racer_fast")
        physics.apply_specs(t, physics={"mass": 1100, "limiter": 8500, "scale": 1.5,
                                        "turbos": [{"section": "TURBO_0", "boost": 0.8}]},
                            ui={"specs": {"bhp": "700 bhp"}, "curvesFromPhysics": True})
        t.finish("specs")
        new = self.car.parent / "racer_fast"
        p = physics.read_physics(new)
        self.assertEqual((p["mass"], p["limiter"]), (1100, 8500))
        self.assertEqual(p["curve"][1], [4000, 600])
        self.assertEqual(p["turbos"][0]["boost"], 0.8)
        self.assertIn("TOTALMASS=1100 ; kg with driver", (new / "data" / "car.ini").read_text(encoding="utf-8"))
        ui = json.loads((new / "ui" / "ui_car.json").read_text(encoding="utf-8"))
        self.assertEqual(ui["specs"]["bhp"], "700 bhp")
        self.assertEqual(ui["torqueCurve"][1], [4000, 1080.0])  # 600 Nm x (1 + 0.8 boost)
        self.assertEqual(physics.read_physics(self.car)["mass"], 1250)  # the original is untouched

    def test_original_with_packed_acd_moves_it_to_the_backup(self):
        make_physics(self.car, packed=True)
        t = edits.Target(self.car, "original", self.backups)
        notes = physics.apply_specs(t, physics={"mass": 999})
        t.finish("specs")
        self.assertTrue(notes)
        self.assertFalse((self.car / "data.acd").exists())
        edits.restore_backup(self.backups, edits.list_backups(self.backups)[0]["id"])
        self.assertTrue((self.car / "data.acd").exists())
        self.assertEqual(physics.read_physics(self.car)["mass"], 1250)

    def test_validation(self):
        make_physics(self.car)
        bad = [{"mass": 5}, {"mass": "heavy"}, {"limiter": 99999}, {"scale": 50},
               {"curve": [[1000, 100]]}, {"curve": [[2000, 100], [1000, 200]]}, {"curve": [[0, -5], [100, 1]]},
               {"turbos": [{"section": "TURBO_9", "boost": 1}]}]
        for p in bad:
            with self.subTest(p=p), self.assertRaises(edits.EditError):
                physics.apply_specs(edits.Target(self.car, "original", self.backups), physics=p)
        with self.assertRaises(edits.EditError):
            physics.apply_specs(edits.Target(self.car, "original", self.backups), ui={"specs": {"evil": "x"}})

    def test_packed_physics_not_editable(self):
        (self.car / "data.acd").write_bytes(b"x")
        with self.assertRaisesRegex(edits.EditError, "data.acd"):
            physics.apply_specs(edits.Target(self.car, "original", self.backups), physics={"mass": 1000})

    def test_suggested_specs(self):
        make_physics(self.car)
        s = physics.suggested_specs(physics.read_physics(self.car))
        self.assertEqual(s["torque"], "600 Nm")  # 400 x 1.5 boost
        self.assertEqual(s["weight"], "1250 kg")
        self.assertTrue(s["bhp"].endswith("bhp"))


if __name__ == "__main__":
    unittest.main()
