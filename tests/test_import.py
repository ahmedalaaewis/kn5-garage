"""Installing mods and skin packs from archives, opening 3D files, and making a car from a model."""

import io
import stat
import sys
import tempfile
import unittest
import unittest.mock
import zipfile
from pathlib import Path

import numpy as np

import fixtures as fx
from kn5garage import builder, edits, importer, installer, kn5, parts
from test_app import AppTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import make_demo_garage as demo  # noqa: E402


def zip_bytes(files, links=()):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
        for name in links:
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, "/etc/passwd")
    return buf.getvalue()


def car_files(prefix, name="modcar"):
    """The files of a small car, as an archive would hold them."""
    tmp = tempfile.TemporaryDirectory()
    car = fx.make_car(tmp.name, name)
    out = {}
    for f in car.rglob("*"):
        if f.is_file():
            out[prefix + f.relative_to(car.parent).as_posix()] = f.read_bytes()
    tmp.cleanup()
    return out


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.work = self.dir / "work"
        self.cars = self.dir / "cars"
        self.cars.mkdir()
        self.backups = self.dir / "backups"

    def tearDown(self):
        self.tmp.cleanup()

    def archive(self, files, name="mod.zip", links=()):
        p = self.dir / name
        p.write_bytes(zip_bytes(files, links))
        return p

    def test_car_and_skin_pack_found_and_installed(self):
        files = car_files("content/cars/")
        files["content/cars/othercar/skins/gold/body.dds"] = fx.dds_solid((200, 160, 0, 255))
        files["content/cars/othercar/skins/gold/ui_skin.json"] = b"{}"
        files["readme.txt"] = b"hi"
        files["content/cars/modcar/setup.exe"] = b"MZ"
        folder, skipped = installer.unpack(self.archive(files), self.work)
        self.assertEqual(skipped, ["content/cars/modcar/setup.exe"])
        self.assertFalse((folder / "content/cars/modcar/setup.exe").exists())
        found = installer.contents(folder)
        self.assertEqual([c["id"] for c in found["cars"]], ["modcar"])
        self.assertEqual([(s["name"], s["car"]) for s in found["skins"]], [("gold", "othercar")])
        (self.cars / "othercar").mkdir()
        r = installer.install(folder, self.cars, {"cars": [found["cars"][0]["path"]], "skins": [found["skins"][0]["path"]]},
                              "skip", self.backups)
        self.assertEqual([x["kind"] for x in r["installed"]], ["car", "skin"])
        self.assertTrue((self.cars / "modcar" / "modcar.kn5").is_file())
        self.assertTrue((self.cars / "othercar" / "skins" / "gold" / "body.dds").is_file())

    def test_conflicts_skip_rename_replace(self):
        folder, _ = installer.unpack(self.archive(car_files("")), self.work)
        path = installer.contents(folder)["cars"][0]["path"]
        (self.cars / "modcar").mkdir()
        (self.cars / "modcar" / "mine.txt").write_text("keep me")
        r = installer.install(folder, self.cars, {"cars": [path]}, "skip", self.backups)
        self.assertEqual(r["installed"], [])
        r = installer.install(folder, self.cars, {"cars": [path]}, "rename", self.backups)
        self.assertEqual(r["installed"][0]["id"], "modcar_2")
        r = installer.install(folder, self.cars, {"cars": [path]}, "replace", self.backups)
        self.assertFalse((self.cars / "modcar" / "mine.txt").exists())
        moved = list((self.backups / "replaced").rglob("mine.txt"))
        self.assertEqual(len(moved), 1)
        with self.assertRaises(edits.EditError):
            installer.install(folder, self.cars, {"cars": ["../x"]}, "skip", self.backups)

    def test_skin_pack_on_its_own(self):
        folder, _ = installer.unpack(self.archive({"MyPack/blue/body.png": fx.png_bytes(), "MyPack/blue/preview.jpg": fx.jpeg_bytes()}),
                                     self.work)
        found = installer.contents(folder)
        self.assertEqual([(s["name"], s["car"]) for s in found["skins"]], [("blue", None)])
        (self.cars / "a_car").mkdir()
        r = installer.install(folder, self.cars, {"skins": [found["skins"][0]["path"]]}, "skip", self.backups)
        self.assertEqual(r["installed"], [])  # no car known: the user has to choose one
        r = installer.install(folder, self.cars, {"skins": [found["skins"][0]["path"]]}, "skip", self.backups,
                              {found["skins"][0]["path"]: "a_car"})
        self.assertTrue((self.cars / "a_car" / "skins" / "blue" / "body.png").is_file())

    def test_hostile_archives_are_refused(self):
        cases = {
            "dotdot": {"../evil.txt": b"x"},
            "deep dotdot": {"a/b/../../../evil.txt": b"x"},
            "absolute": {"/abs.txt": b"x"},
            "drive": {"C:/Windows/evil.txt": b"x"},
            "colon stream": {"a.txt:hidden": b"x"},
        }
        for name, files in cases.items():
            with self.subTest(name):
                with self.assertRaises(edits.EditError):
                    installer.unpack(self.archive(files), self.work)
        with self.assertRaises(edits.EditError):
            installer.unpack(self.archive({"ok.txt": b"x"}, links=["link"]), self.work)
        self.assertFalse((self.dir / "evil.txt").exists())
        self.assertEqual(list((self.work / "import").iterdir()), [])  # half-unpacked folders are removed

    def test_zip_bomb_and_too_many_files(self):
        with self.assertRaises(edits.EditError):
            installer.check_listing([("a", installer.MAX_TOTAL + 1, False, False)], 1000)
        with self.assertRaises(edits.EditError):
            installer.check_listing([("a", 500 << 20, False, False)], 1000)
        with self.assertRaises(edits.EditError):
            installer.check_listing([("f%d" % i, 1, False, False) for i in range(installer.MAX_FILES + 1)], 10 ** 9)

    def test_7zip_listing_is_read_and_checked(self):
        listing = ("Path = cars/a/a.kn5\nFolder = -\nSize = 100\nAttributes = A\n\n"
                   "Path = cars/a\nFolder = +\nSize = 0\nAttributes = D\n\n"
                   "Path = cars/a/link\nFolder = -\nSize = 5\nAttributes = A -lrwxrwxrwx\nSymbolic Link = /etc\n\n")
        done = type("P", (), {"returncode": 0, "stdout": listing})
        with unittest.mock.patch.object(installer.subprocess, "run", return_value=done):
            entries = installer._7z_entries("7z.exe", Path("x.7z"))
        self.assertEqual(entries, [("cars/a/a.kn5", 100, False, False), ("cars/a", 0, True, False),
                                   ("cars/a/link", 5, False, True)])
        with self.assertRaises(edits.EditError):
            installer.check_listing(entries, 1000)
        p = self.dir / "x.7z"
        p.write_bytes(b"7z")
        with unittest.mock.patch.object(installer, "find_7zip", return_value=None), self.assertRaises(edits.EditError) as e:
            installer.unpack(p, self.work)
        self.assertIn("7-Zip", str(e.exception))

    def test_not_an_archive(self):
        p = self.dir / "x.txt"
        p.write_text("x")
        with self.assertRaises(edits.EditError):
            installer.unpack(p, self.work)
        bad = self.dir / "bad.zip"
        bad.write_bytes(b"PK\x03\x04garbage")
        with self.assertRaises(edits.EditError):
            installer.unpack(bad, self.work)


class BuilderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.cars = self.dir / "cars"
        self.donor = demo.build_car(demo.CARS[0], self.cars)

    def tearDown(self):
        self.tmp.cleanup()

    def test_body_replaced_wheels_kept_and_lods_written(self):
        (self.dir / "box.obj").write_text("v 0 0 0\nv 0 0 10\nv 4 0 0\nv 4 2 10\nf 1 2 3\nf 2 4 3\n")
        model = importer.load_model(self.dir / "box.obj")
        s = parts.Session(self.donor, self.donor.name)
        body = builder.body_parts(s.model())
        names = [p["name"] for p in s.tree()]
        self.assertTrue(body)
        self.assertFalse(any(names[i].upper().startswith("WHEEL") for i in body))
        removed = builder.build(s, model, "box.obj", rotate=90)
        self.assertEqual(removed, len(body))
        final = s.final_model()
        final_names = [n["name"] for n in importer.walk(final["root"])]
        self.assertIn("box", final_names)
        self.assertTrue(any(n.upper().startswith("WHEEL_LF") for n in final_names))
        # sized like the donor's body
        old = parts.Session(self.donor, self.donor.name).model()
        worlds = parts.parent_worlds(old["root"])
        nodes = list(importer.walk(old["root"]))
        boxes = [importer.bounds(nodes[i], worlds[id(nodes[i])]) for i in body]
        length = max(b[1][2] for b in boxes) - min(b[0][2] for b in boxes)
        lo, hi = importer.bounds(final["root"]["children"][-1], parts.local_matrix(final["root"]))
        self.assertAlmostEqual(float(np.max(hi - lo)), float(length), places=3)
        target = edits.Target(self.donor, "new", new_id="box_car")
        builder.write(s, target)
        lod = next((self.cars / "box_car").glob("*_lod_b.kn5"))
        self.assertEqual(lod.read_bytes(), (self.cars / "box_car" / s.kn5_rel).read_bytes())
        self.assertIn("box", [n["name"] for n in importer.walk(kn5.read_kn5(lod)["root"])])

    def test_bad_rotation(self):
        with self.assertRaises(edits.EditError):
            builder.orient(importer.empty_model("x"), rotate=45)


class ImportApiTests(AppTestCase):
    def test_install_mod_through_the_api(self):
        self.scan()
        archive = self.dir / "mod.zip"
        archive.write_bytes(zip_bytes(car_files("content/cars/")))
        self.app.pick_archive = lambda: str(archive)
        r = self.json("/api/import/archive", {})
        self.assertEqual([c["id"] for c in r["cars"]], ["modcar"])
        self.json("/api/import/install", {"import": r["import"], "cars": ["../../x"], "dest": "pick"}, expect=400)
        self.json("/api/import/install", {"import": r["import"], "cars": [], "dest": "C:/Windows"}, expect=400)
        done = self.json("/api/import/install", {"import": r["import"], "cars": [r["cars"][0]["path"]], "dest": "pick"})
        self.assertEqual(done["installed"][0]["id"], "modcar")
        self.assertTrue((self.cars / "modcar" / "modcar.kn5").is_file())
        self.json("/api/import/install", {"import": "nope", "cars": []}, expect=400)

    def test_empty_archive(self):
        archive = self.dir / "empty.zip"
        archive.write_bytes(zip_bytes({"readme.txt": b"x"}))
        self.app.pick_archive = lambda: str(archive)
        self.json("/api/import/archive", {}, expect=400)
        self.app.pick_archive = lambda: None
        self.assertIsNone(self.json("/api/import/archive", {})["import"])

    def test_open_3d_file_as_glb_and_obj(self):
        obj = self.dir / "thing.obj"
        obj.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
        self.app.pick_model = lambda: str(obj)
        r = self.json("/api/import/model", {"format": "glb"})
        self.assertEqual(r["file"], "thing_imported.glb")
        self.assertTrue((self.out / "thing_imported.glb").is_file())
        r = self.json("/api/import/model", {"format": "obj"})
        self.assertTrue((self.out / "thing_imported.obj").is_file())
        self.json("/api/import/model", {"format": "exe"}, expect=400)

    def test_build_car_opens_a_parts_session(self):
        self.scan()
        obj = self.dir / "shell.obj"
        obj.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nv 0 0 1\nf 1 2 3\nf 1 3 4\n")
        self.app.pick_model = lambda: str(obj)
        r = self.json("/api/build/start", {"donor": "a_normal", "rotate": 0})
        self.assertTrue(r["url"])
        self.assertTrue(r["canUndo"])
        self.json("/api/build/start", {"donor": "a_normal", "rotate": 33}, expect=400)
        self.json("/api/build/start", {"donor": "b_protected"}, expect=400)
        saved = self.json("/api/parts/save", {"id": "a_normal", "session": r["session"],
                                              "target": {"mode": "new", "newId": "shell_car"}})
        self.assertEqual(saved["carId"], "shell_car")


if __name__ == "__main__":
    unittest.main()
