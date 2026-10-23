"""Livery studio and image programs: DDS writing, paint texture export with a UV template,
pictures in place of the paint, installing a new skin, and the API around it."""

import base64
import io
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

import fixtures as fx
from kn5garage import edits, editors, livery, textures
from test_app import AppTestCase


def png_data_url(color=(10, 20, 30), size=(64, 36)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


class DdsTests(unittest.TestCase):
    def test_uncompressed_with_mipmaps_reads_back(self):
        img = Image.fromarray(fx.gradient_pixels(16, 8), "RGBA")
        data = textures.dds_bytes(img)
        h, w, pitch, depth, mips = struct.unpack_from("<IIIII", data, 12)
        self.assertEqual((w, h, pitch, mips), (16, 8, 64, 5))  # 16x8, 8x4, 4x2, 2x1, 1x1
        self.assertEqual(len(data), 128 + 4 * (16 * 8 + 8 * 4 + 4 * 2 + 2 * 1 + 1))
        back = textures.decode_dds_uncompressed(data)
        np.testing.assert_array_equal(np.asarray(back), np.asarray(img))

    def test_without_mipmaps(self):
        data = textures.dds_bytes(Image.new("RGB", (4, 4), (1, 2, 3)), mipmaps=False)
        self.assertEqual(len(data), 128 + 64)
        self.assertEqual(textures.decode_dds_uncompressed(data).getpixel((0, 0)), (1, 2, 3, 255))


class LiveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.car = fx.make_car(self.dir / "cars", "racer")
        (self.car / "skins" / "00_red" / "extra_decal.dds").write_bytes(fx.dds_solid((1, 1, 1, 255)))
        self.work = self.dir / "work"

    def tearDown(self):
        self.tmp.cleanup()

    def test_start_exports_paint_and_uv_template(self):
        s = livery.start(self.car, "racer", "00_red", self.work)
        self.assertEqual(s["skin"], "00_red")
        self.assertEqual([t["texture"] for t in s["textures"]], ["body.dds"])
        folder = Path(s["folder"])
        with Image.open(folder / "body.png") as img:
            self.assertEqual(img.convert("RGB").getpixel((0, 0)), (220, 20, 20))  # the skin's paint, not the kn5's
        with Image.open(folder / "body_uv.png") as uv:
            self.assertGreater(np.asarray(uv)[..., 3].max(), 0)  # lines were drawn

    def test_chosen_paint_parts_and_errors(self):
        s = livery.start(self.car, "racer", None, self.work, paint_names=["Tyre"])
        self.assertEqual([t["texture"] for t in s["textures"]], ["wheel.dds"])
        with self.assertRaisesRegex(edits.EditError, "no paint"):
            livery.start(self.car, "racer", None, self.work, paint_names=["Nothing"])
        locked = fx.make_car(self.dir / "cars", "locked", protected=True, skins=())
        with self.assertRaisesRegex(edits.EditError, "protected"):
            livery.start(locked, "locked", None, self.work)

    def test_picture_in_place_of_the_paint(self):
        s = livery.start(self.car, "racer", "00_red", self.work)
        pic = self.dir / "logo.jpg"
        Image.new("RGB", (300, 100), (0, 200, 0)).save(pic)
        livery.use_image(self.work, "racer", s["session"], "body.png", pic)
        with Image.open(Path(s["folder"]) / "body.png") as img:
            self.assertEqual(img.size, (8, 8))  # fitted to the texture's size
        bad = self.dir / "bad.png"
        bad.write_bytes(b"not a picture")
        with self.assertRaises(edits.EditError):
            livery.use_image(self.work, "racer", s["session"], "body.png", bad)

    def test_install_new_skin_into_the_original_with_undo(self):
        s = livery.start(self.car, "racer", "00_red", self.work)
        Image.new("RGBA", (8, 8), (0, 0, 255, 255)).save(Path(s["folder"]) / "body.png")
        backups = self.dir / "backups"
        t = edits.Target(self.car, "original", backups)
        preview = base64.b64decode(png_data_url().split(",")[1])
        livery.install(self.work, "racer", s["session"], t, "blue_42",
                       {"name": "Blue 42", "driver": "Me", "number": "42"}, preview)
        res = t.finish("livery")
        skin = self.car / "skins" / "blue_42"
        self.assertEqual(sorted(p.name for p in skin.iterdir()),
                         ["body.dds", "extra_decal.dds", "livery.png", "preview.jpg", "ui_skin.json"])
        body = textures.decode_dds_uncompressed((skin / "body.dds").read_bytes())
        self.assertEqual(body.getpixel((0, 0)), (0, 0, 255, 255))
        ui = json.loads((skin / "ui_skin.json").read_text(encoding="utf-8"))
        self.assertEqual((ui["skinname"], ui["drivername"], ui["number"]), ("Blue 42", "Me", "42"))
        with Image.open(skin / "preview.jpg") as p:
            self.assertEqual(p.size, (1022, 575))
        self.assertEqual(len(res["created"]), 5)
        edits.restore_backup(backups, edits.list_backups(backups)[0]["id"])
        self.assertFalse(skin.exists())  # the skin's folder is gone too
        self.assertTrue((self.car / "skins" / "00_red").is_dir())

    def test_install_checks(self):
        s = livery.start(self.car, "racer", "00_red", self.work)
        t = edits.Target(self.car, "original", self.dir / "backups")
        for name in ("00_red", "../x", "with space", ""):
            with self.subTest(name=name), self.assertRaises(edits.EditError):
                livery.install(self.work, "racer", s["session"], t, name)
        for sid in ("../../etc", "zz", "0123456789ab"):
            with self.subTest(sid=sid), self.assertRaises(edits.EditError):
                livery.load(self.work, "racer", sid)
        with self.assertRaises(edits.EditError):
            livery.work_file(self.work, "racer", s["session"], "session.json")


class EditorsTests(unittest.TestCase):
    def test_detects_known_install_folders(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            ps = root / "Adobe" / "Adobe Photoshop 2025" / "Photoshop.exe"
            ps.parent.mkdir(parents=True)
            ps.write_bytes(b"MZ")
            old = root / "Adobe" / "Adobe Photoshop 2023" / "Photoshop.exe"
            old.parent.mkdir(parents=True)
            old.write_bytes(b"MZ")
            gimp = root / "GIMP 3" / "bin"
            gimp.mkdir(parents=True)
            (gimp / "gimp-3.0.exe").write_bytes(b"MZ")
            (gimp / "gimp-console-3.0.exe").write_bytes(b"MZ")
            env = {"ProgramFiles": t, "ProgramW6432": t, "ProgramFiles(x86)": t, "LOCALAPPDATA": t}
            with mock.patch.dict("os.environ", env), mock.patch.object(editors.sys, "platform", "win32"):
                found = {e["name"]: Path(e["path"]) for e in editors.detect()}
        self.assertEqual(found["Photoshop"], ps)  # the newest version
        self.assertEqual(found["GIMP"].name, "gimp-3.0.exe")  # not the console one
        self.assertNotIn("Illustrator", found)


class LiveryApiTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.launched = []
        self.prog = self.dir / "MyPainter.exe"
        self.prog.write_bytes(b"MZ")
        self.prog.chmod(0o755)  # a program on Linux / macOS too
        self.app.pick_program = lambda: str(self.prog)
        self.app.launch_program = lambda exe, f: self.launched.append((Path(exe).name, Path(f).name))
        self.pic = self.dir / "pic.png"
        Image.new("RGB", (40, 40), (255, 255, 0)).save(self.pic)
        self.app.pick_image = lambda: str(self.pic)
        patcher = mock.patch.object(editors, "detect", lambda: [])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_full_livery_flow(self):
        self.scan()
        self.assertEqual(self.json("/api/editors")["editors"], [])
        progs = self.json("/api/editors/add", {"name": "My painter"})["editors"]
        self.assertEqual([p["name"] for p in progs], ["My painter"])
        s = self.json("/api/livery/start", {"id": "a_normal", "skin": "00_red"})
        sid = s["session"]
        q = "/api/livery/file?id=a_normal&session=%s&file=body.png&t=%s" % (sid, self.app.token)
        status, data, headers = self.request(q, token=False)
        self.assertEqual((status, headers["Content-Type"]), (200, "image/png"))
        self.json("/api/livery/open", {"id": "a_normal", "session": sid, "file": "body.png", "program": str(self.prog)})
        self.assertEqual(self.launched, [("MyPainter.exe", "body.png")])
        before = self.json("/api/livery/status?id=a_normal&session=" + sid)["files"][0]["changed"]
        self.json("/api/livery/image", {"id": "a_normal", "session": sid, "file": "body.png"})
        after = self.json("/api/livery/status?id=a_normal&session=" + sid)["files"][0]["changed"]
        self.assertNotEqual(before, after)
        r = self.json("/api/livery/install", {"id": "a_normal", "session": sid, "skinName": "yellow",
                                              "info": {"name": "Yellow"}, "preview": png_data_url(),
                                              "target": {"mode": "new", "newId": "a_normal_yellow"}})
        self.assertEqual((r["mode"], r["carId"], r["skin"]), ("new", "a_normal_yellow", "yellow"))
        skin = self.cars / "a_normal_yellow" / "skins" / "yellow"
        self.assertTrue((skin / "body.dds").is_file())
        self.assertFalse((self.cars / "a_normal" / "skins" / "yellow").exists())

    def test_refuses_unknown_programs_and_files(self):
        self.scan()
        sid = self.json("/api/livery/start", {"id": "a_normal", "skin": "00_red"})["session"]
        other = self.dir / "calc.exe"
        other.write_bytes(b"MZ")
        self.json("/api/livery/open", {"id": "a_normal", "session": sid, "file": "body.png", "program": str(other)},
                  expect=400)
        for f in ("session.json", "../../settings.json", "..\\x.png"):
            with self.subTest(f=f):
                self.json("/api/livery/open", {"id": "a_normal", "session": sid, "file": f}, expect=400)
                q = "/api/livery/file?id=a_normal&session=%s&file=%s&t=%s" % (sid, f, self.app.token)
                self.assertEqual(self.request(q, token=False)[0], 400)
        self.assertEqual(self.launched, [])
        self.json("/api/livery/start", {"id": "b_protected"}, expect=400)

    def test_protected_cars_cannot_be_edited(self):
        self.scan()
        self.json("/api/edit/specs", {"id": "b_protected", "ui": {"specs": {"bhp": "1"}},
                                      "target": {"mode": "original"}}, expect=400)


if __name__ == "__main__":
    unittest.main()
