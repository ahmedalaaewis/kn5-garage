"""The parts editor: hide, remove, move, recolour, swap; undo / redo; saving into a car."""

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

import fixtures as fx
import glb_check
from kn5garage import edits, importer, kn5, kn5write, parts
from kn5garage.textures import decode_image
from test_app import AppTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import make_demo_garage as demo  # noqa: E402

MOVE_UP = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0.5, 0, 1]  # column-major: up 0.5


class PartsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.cars = self.dir / "cars"
        self.car = demo.build_car(demo.CARS[0], self.cars)
        self.s = parts.Session(self.car, self.car.name)

    def tearDown(self):
        self.tmp.cleanup()

    def find(self, pred, s=None):
        return next(p for p in (s or self.s).tree() if pred(p))

    def mesh_part(self):
        return self.find(lambda p: not p["group"] and p["verts"] > 3 and p["depth"] > 0)

    def test_tree_matches_preview_glb_nodes(self):
        out = self.dir / "p.glb"
        self.s.preview(out)
        doc = glb_check.load(out).json
        names = [n["name"] for n in doc["nodes"]][:len(self.s.tree())]
        self.assertEqual(names, [p["name"] for p in self.s.tree()])

    def test_hide_remove_and_undo(self):
        p = self.mesh_part()
        self.assertFalse(self.s.apply({"op": "show", "node": p["i"], "on": False}))
        self.assertFalse(self.s.tree()[p["i"]]["active"])
        self.s.apply({"op": "remove", "node": p["i"]})
        self.assertTrue(self.s.tree()[p["i"]]["removed"])
        names = [n["name"] for n in importer.walk(self.s.final_model()["root"])]
        self.assertNotIn(p["name"], names)
        self.assertEqual(len(self.s.tree()), len(list(importer.walk(self.s.base["root"]))))  # indices kept
        self.s.undo()
        self.s.undo()
        self.assertTrue(self.s.tree()[p["i"]]["active"])
        self.s.redo_step()
        self.assertFalse(self.s.tree()[p["i"]]["active"])
        with self.assertRaises(edits.EditError):
            self.s.apply({"op": "remove", "node": 0})

    def test_move_mesh_bakes_vertices_and_moving_twice_is_not_added_up(self):
        p = self.mesh_part()
        before = self.s.node(p["i"])["verts"][:, 1].copy()
        self.s.apply({"op": "move", "node": p["i"], "matrix": MOVE_UP})
        self.s.apply({"op": "move", "node": p["i"], "matrix": MOVE_UP})
        after = self.s.node(p["i"])["verts"][:, 1]
        np.testing.assert_allclose(after, before + 0.5, atol=1e-5)
        mirror = [-1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
        idx = self.s.node(p["i"])["indices"].reshape(-1, 3).copy()
        self.s.apply({"op": "move", "node": p["i"], "matrix": mirror})
        np.testing.assert_array_equal(self.s.node(p["i"])["indices"].reshape(-1, 3), idx[:, ::-1])
        for bad in ([0] * 16, [1] * 15, ["x"] * 16, [float("nan")] * 16):
            with self.subTest(bad=bad), self.assertRaises(edits.EditError):
                self.s.apply({"op": "move", "node": p["i"], "matrix": bad})

    def test_move_group_sets_its_matrix(self):
        g = self.find(lambda p: p["group"] and p["depth"] == 1)
        self.s.apply({"op": "move", "node": g["i"], "matrix": MOVE_UP})
        self.assertAlmostEqual(self.s.node(g["i"])["matrix"][13], 0.5 + 0 * 1)

    def test_recolour_part_and_material(self):
        p = self.mesh_part()
        mats = len(self.s.model()["materials"])
        old = self.s.node(p["i"])["material"]
        self.assertTrue(self.s.apply({"op": "colour", "node": p["i"], "colour": "#ff0000", "mode": "solid"}))
        m = self.s.model()
        self.assertEqual(len(m["materials"]), mats + 1)
        mat = m["materials"][self.s.node(p["i"])["material"]]
        img = decode_image(m["textures"][mat["maps"]["txDiffuse"]])
        self.assertEqual(img.getpixel((0, 0))[:3], (255, 0, 0))
        self.assertNotEqual(self.s.node(p["i"])["material"], old)
        self.s.undo()
        self.s.apply({"op": "colour", "node": p["i"], "colour": "#00ff00", "scope": "material"})
        self.assertEqual(len(self.s.model()["materials"]), mats)
        with self.assertRaises(edits.EditError):
            self.s.apply({"op": "colour", "node": p["i"], "colour": "red"})

    def test_swap_in_part_from_another_car_fitted(self):
        other = demo.build_car(demo.CARS[1], self.cars)
        src = kn5.read_kn5(next(other.glob("*.kn5")))
        key = self.s.add_source("car:" + other.name, other.name, src)
        src_tree = parts.Session(other, other.name).tree()
        piece = next(p for p in src_tree if not p["group"] and p["verts"] > 3 and p["depth"] > 0)
        target = self.mesh_part()
        count = len(self.s.tree())
        self.assertTrue(self.s.apply({"op": "add", "source": key, "node": piece["i"], "replace": target["i"], "fit": True}))
        tree = self.s.tree()
        self.assertTrue(tree[target["i"]]["removed"])
        self.assertGreater(len(tree), count)
        self.assertEqual([p["name"] for p in tree[:count]], [p["name"] for p in parts.Session(self.car, self.car.name).tree()])
        model = self.s.model()
        new_root = list(importer.walk(model["root"]))[count]
        worlds = parts.parent_worlds(model["root"])
        old = self.s.node(target["i"])
        (alo, ahi), (blo, bhi) = importer.bounds(new_root, worlds[id(new_root)]), importer.bounds(old, worlds[id(old)])
        np.testing.assert_allclose((alo + ahi) / 2, (blo + bhi) / 2, atol=1e-4)
        self.assertAlmostEqual(float(np.max(ahi - alo)), float(np.max(bhi - blo)), places=4)
        # its materials and textures came too
        added = [n for n in importer.walk(new_root) if n["cls"] != 1]
        for n in added:
            mat = model["materials"][n["material"]]
            for tex in mat["maps"].values():
                self.assertIn(tex, model["textures"])

    def test_add_from_3d_file_then_save_into_a_new_car(self):
        (self.dir / "cube.obj").write_text("v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nf 1 2 3 4\n")
        key = self.s.add_source("file:cube", "cube.obj", importer.load_model(self.dir / "cube.obj"))
        self.s.apply({"op": "add", "source": key, "node": 0})
        target = edits.Target(self.car, "new", new_id="vel_custom")
        target.write(self.s.kn5_rel, kn5write.write_kn5(self.s.final_model()))
        saved = kn5.read_kn5(target.dir / self.s.kn5_rel)
        self.assertIn("cube", [n["name"] for n in importer.walk(saved["root"])])
        out = self.dir / "p.glb"
        parts.Session(target.dir, "vel_custom").preview(out)
        self.assertTrue(out.stat().st_size > 1000)

    def test_bad_steps(self):
        for step in ({"op": "show", "node": 99999}, {"op": "nope", "node": 1}, {"op": "show", "node": True},
                     {"op": "add", "source": "x", "node": 0}, "x", {"op": "material", "node": 0, "material": 0}):
            with self.subTest(step=step), self.assertRaises(edits.EditError):
                self.s.apply(step)
        self.assertEqual(self.s.steps, [])

    def test_protected_car_refused(self):
        car = fx.make_car(self.dir, "locked", protected=True)
        with self.assertRaises(edits.EditError):
            parts.Session(car, "locked")

    def test_sessions(self):
        reg = parts.Sessions()
        ids = [reg.start(self.car, self.car.name).id for _ in range(parts.MAX_SESSIONS + 1)]
        self.assertEqual(len(reg.items), parts.MAX_SESSIONS)
        reg.get(ids[-1], self.car.name)
        with self.assertRaises(edits.EditError):
            reg.get(ids[-1], "other")


class PartsApiTests(AppTestCase):
    def test_edit_parts_and_save_with_backup_then_undo(self):
        self.scan()
        original = (self.cars / "a_normal" / "a_normal.kn5").read_bytes()
        st = self.json("/api/parts/start", {"id": "a_normal", "skin": "00_red"})
        self.assertTrue(st["url"].startswith("/preview/parts__"))
        status = self.request(st["url"] + "?t=" + self.app.token, token=False)[0]
        self.assertEqual(status, 200)
        mesh = next(p for p in st["parts"] if not p["group"])
        base = {"id": "a_normal", "session": st["session"]}
        r = self.json("/api/parts/step", dict(base, step={"op": "show", "node": mesh["i"], "on": False}))
        self.assertNotIn("url", r)
        r = self.json("/api/parts/step", dict(base, step={"op": "colour", "node": mesh["i"], "colour": "#123456"}))
        self.assertIn("url", r)
        self.assertEqual(r["steps"], 2)
        r = self.json("/api/parts/step", dict(base, undo=True))
        self.assertEqual((r["steps"], r["canRedo"]), (1, True))
        self.json("/api/parts/step", dict(base, step={"op": "nope", "node": 0}), expect=400)
        self.json("/api/parts/step", {"id": "a_normal", "session": "nope", "step": {}}, expect=400)
        saved = self.json("/api/parts/save", dict(base, target={"mode": "original"}))
        self.assertEqual(saved["changed"], ["a_normal.kn5"])
        edited = kn5.read_kn5(self.cars / "a_normal" / "a_normal.kn5")
        self.assertFalse(next(n for n in importer.walk(edited["root"]) if n["name"] == mesh["name"])["active"])
        self.json("/api/parts/step", dict(base, undo=True), expect=400)  # the session ended with the save
        backup = self.json("/api/backups?id=a_normal")["items"][0]["id"]
        self.json("/api/backups/restore", {"backup": backup})
        self.assertEqual((self.cars / "a_normal" / "a_normal.kn5").read_bytes(), original)

    def test_parts_from_another_car_and_a_3d_file(self):
        self.scan()
        obj = self.dir / "wing.obj"
        obj.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
        self.app.pick_model = lambda: str(obj)
        st = self.json("/api/parts/start", {"id": "a_normal"})
        base = {"id": "a_normal", "session": st["session"]}
        src = self.json("/api/parts/source", dict(base, car="d_broken_json"))
        self.assertTrue(src["parts"])
        f = self.json("/api/parts/source", base)
        self.assertEqual(f["name"], "wing.obj")
        r = self.json("/api/parts/step", dict(base, step={"op": "add", "source": f["source"], "node": 0}))
        self.assertEqual(r["parts"][-1]["name"], "wing")
        self.json("/api/parts/source", dict(base, car="b_protected"), expect=400)
        self.json("/api/parts/start", {"id": "b_protected"}, expect=400)
        self.json("/api/parts/save", dict(base, target={"mode": "new", "newId": "a_winged"}))
        self.assertIn("wing", [n["name"] for n in importer.walk(kn5.read_kn5(self.cars / "a_winged" / "a_normal.kn5")["root"])])


if __name__ == "__main__":
    unittest.main()
